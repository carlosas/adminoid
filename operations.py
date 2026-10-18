import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

import settings as S
from common import atomic, write_json, locked
from configuration import read_all, digest, SECRET, Document

def command(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)

def systemctl(action):
    if action not in ('start', 'stop', 'is-active', 'show'): raise ValueError('Invalid action.')
    args = ['systemctl', '--user', action, S.SERVICE]
    if action == 'show': args += ['-p', 'ActiveState', '-p', 'MemoryCurrent', '-p', 'MainPID']
    result = command(args, timeout=360)
    if action in ('start', 'stop') and result.returncode: raise ValueError('systemd could not ' + action + ' the service.')
    return result.stdout.strip()

def console(cmd):
    if cmd not in ('players', 'save', 'quit'): raise ValueError('Invalid command.')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(100); sock.connect(str(S.SOCKET))
            sock.sendall((json.dumps({'command': cmd}) + '\n').encode())
            data = b''
            while b'\n' not in data:
                block = sock.recv(4096)
                if not block or len(data) > 100000: raise ValueError('Incomplete console response.')
                data += block
        result = json.loads(data)
    except (OSError, ValueError) as e: raise ValueError('Local console unavailable; player status unknown.') from e
    if result.get('error'): raise ValueError(result['error'])
    return result

def players():
    if systemctl('is-active') not in ('active', 'activating'): return {'count': 0, 'names': []}
    try: return console('players')
    except ValueError as e: return {'count': None, 'names': [], 'error': str(e)}

def redact(text):
    for content in read_all().values():
        for line in content.splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                if SECRET.search(key) and value.strip(): text = text.replace(value.strip(), '[hidden]')
    return re.sub(r'(?i)((?:password|secret|token)\s*[=:]\s*)\S+', r'\1[oculto]', text)

def logs():
    result = command(['journalctl', '--user-unit', S.SERVICE, '-n', '160', '--no-pager', '-o', 'cat'])
    return redact(result.stdout)

def same_configuration(scope, before, after):
    """Ignore comments/formatting rewritten by the game; compare effective values."""
    try:
        left = {key: field.value for key, field in Document(scope, before).fields.items()}
        right = {key: field.value for key, field in Document(scope, after).fields.items()}
        return left == right
    except ValueError:
        return before == after

def status():
    props = dict(x.split('=', 1) for x in systemctl('show').splitlines() if '=' in x)
    applied_path = S.STATE / 'applied.json'
    applied = json.loads(applied_path.read_text()) if applied_path.exists() else {}
    current_text = read_all()
    applied_values_path = S.STATE / 'applied-values.json'
    applied_values = json.loads(applied_values_path.read_text()) if applied_values_path.exists() else {}
    pending = []
    for scope, text in current_text.items():
        if scope in applied_values:
            if not same_configuration(scope, applied_values[scope], text): pending.append(scope)
        elif applied.get(scope) != digest(text):
            pending.append(scope)
    version_text = command(['journalctl', '--user-unit', S.SERVICE, '-g', 'version=', '-n', '1', '--no-pager', '-o', 'cat']).stdout
    version = re.search(r'\bversion=([\w.]+)', version_text)
    return {'state': props.get('ActiveState'), 'memory': props.get('MemoryCurrent'),
            'version': version[1] if version else 'Desconocida', 'console': S.SOCKET.exists(),
            'pending': pending, 'applied_known': bool(applied)}

def validate_archive(path):
    size, names = 0, set()
    with tarfile.open(path, 'r:gz') as archive:
        for item in archive:
            p = PurePosixPath(item.name)
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] != 'Zomboid' or not (item.isdir() or item.isfile()):
                raise ValueError('Backup contains forbidden paths or file types.')
            if item.name in names: raise ValueError('Backup contains duplicate entries.')
            names.add(item.name); size += item.size
            if size > 80 * 1024**3 or len(names) > 1500000: raise ValueError('Backup is too large.')
            if item.isfile():
                with archive.extractfile(item) as src:
                    while src.read(1024*1024): pass
    required = {'Zomboid/Server/' + S.NAME + '.ini', 'Zomboid/Server/' + S.NAME + '_SandboxVars.lua'}
    if not required <= names: raise ValueError('The backup does not contain this instance configuration.')
    return size

def backup(label):
    if label not in ('daily', 'manual', 'pre-update', 'pre-restore'): raise ValueError('Invalid backup type.')
    S.BACKUPS.mkdir(mode=0o700, exist_ok=True); os.chmod(S.BACKUPS, 0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = S.BACKUPS / f'zomboid-{label}-{stamp}.tar.gz'
    tmp = path.with_suffix('.partial')
    try:
        with tarfile.open(tmp, 'w:gz') as archive:
            def safe(item):
                if item.issym() or item.islnk(): raise ValueError('Data contains links; review before backing up.')
                return item
            archive.add(S.DATA, arcname='Zomboid', filter=safe)
        validate_archive(tmp)
        os.chmod(tmp, 0o600); os.replace(tmp, path)
    finally: tmp.unlink(missing_ok=True)
    if label == 'daily':
        for old in sorted(S.BACKUPS.glob('zomboid-daily-*.tar.gz'), reverse=True)[7:]: old.unlink()
    return path.name

def backup_list():
    return [{'name': p.name, 'size': p.stat().st_size, 'mtime': p.stat().st_mtime} for p in sorted(S.BACKUPS.glob('zomboid-*.tar.gz'), reverse=True) if p.is_file() and not p.is_symlink()]

def resolve_backup(name):
    if not isinstance(name, str) or not re.fullmatch(r'zomboid-[\w-]+\.tar\.gz', name): raise ValueError('Invalid backup name.')
    path = S.BACKUPS / name
    if not path.is_file() or path.is_symlink(): raise ValueError('Backup not found.')
    return path

def preserve_stop():
    if systemctl('is-active') not in ('active', 'activating'): return False
    if not S.SOCKET.exists(): raise ValueError('The new console is not active yet; shutdown will not be forced.')
    # Persist the saved configuration even when GameServer writes its running values on quit.
    saved = read_all()
    stop_started=time.time()
    recovery = S.STATE / 'shutdown-config.json'
    write_json(recovery, saved)
    systemctl('stop')
    if systemctl('is-active') in ('active', 'activating', 'deactivating'): raise ValueError('The server has not stopped.')
    for scope,text in saved.items(): atomic(S.DATA / 'Server' / S.FILES[scope], text)
    recovery.unlink(missing_ok=True)
    exit_file=S.STATE/'game-exit.json'
    exit_info=json.loads(exit_file.read_text()) if exit_file.exists() else {}
    if not exit_info.get('graceful') or exit_info.get('time',0)<stop_started:
        raise ValueError('Clean shutdown was not confirmed. The server is stopped; investigate before backing up or restoring.')
    return True

def start():
    from classinfo import packaged_version
    if not packaged_version(S.GAME / 'java/projectzomboid.jar').startswith('42.'):
        raise ValueError('The installed build is not Build 42. It will not be started on this world.')
    p = S.GAME / 'ProjectZomboid64.json'
    config = json.loads(p.read_text())
    config['vmArgs'] = [v for v in config['vmArgs'] if not v.startswith(('-Xmx', '-Xms'))] + ['-Xmx5g']
    atomic(p, json.dumps(config, indent=2) + '\n')
    before = (S.STATE / 'applied.json').stat().st_mtime_ns if (S.STATE / 'applied.json').exists() else 0
    systemctl('start')
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        state = systemctl('is-active')
        if state == 'failed': raise ValueError('Startup failed. Check the logs.')
        applied = S.STATE / 'applied.json'
        if applied.exists() and applied.stat().st_mtime_ns > before: return
        time.sleep(2)
    raise ValueError('SERVER STARTED was not confirmed within five minutes; check the logs.')

def restore(path):
    required = validate_archive(path)
    if shutil.disk_usage(S.DATA.parent).free < required + 1024**3: raise ValueError('Not enough free space for a safe restore.')
    with tempfile.TemporaryDirectory(prefix='.pz-restore-', dir=S.DATA.parent) as temp:
        with tarfile.open(path, 'r:gz') as archive: archive.extractall(temp, filter='data')
        incoming = Path(temp) / 'Zomboid'
        for scope,name in S.FILES.items():
            p = incoming / 'Server' / name
            if p.exists() and scope in ('server', 'sandbox'): Document(scope, p.read_text())
        old = Path(temp) / 'previous'
        S.DATA.rename(old)
        try: incoming.rename(S.DATA)
        except Exception:
            old.rename(S.DATA); raise
        # pre-restore backup is verified before this function; previous is recoverable there.

def execute(action, payload, progress):
    if action not in ('start', 'stop', 'restart', 'backup', 'restore', 'update'): raise ValueError('Invalid operation.')
    with locked():
        if action == 'start':
            if systemctl('is-active') == 'active': return 'The server was already running.'
            progress('Starting'); start(); return 'Server started.'
        state = players()
        if not payload.get('confirmed'): raise ValueError('Confirm the operation and possible interruption of connected players.')
        if payload.get('automatic') and state['count'] != 0: return 'Deferred: players are connected or their status is unknown.'
        target = resolve_backup(payload.get('backup')) if action == 'restore' else None
        if target: validate_archive(target)
        progress('Saving and shutting down gracefully')
        was_active = preserve_stop()
        if action == 'stop': return 'Server stopped; saved configuration preserved.'
        if action == 'restart': start(); return 'Server restarted.'
        if action == 'backup':
            try:
                progress('Creating and verifying backup')
                result = backup('daily' if payload.get('automatic') else 'manual')
            finally:
                if was_active: start()
            return 'Verified backup: ' + result
        progress('Creating a safety backup')
        backup('pre-restore' if action == 'restore' else 'pre-update')
        if action == 'restore':
            progress('Restoring validated backup'); restore(target)
        else:
            progress('Updating with SteamCMD; this may take several minutes')
            result = command([str(S.STEAM), '+@sSteamCmdForcePlatformType', 'linux', '+force_install_dir', str(S.GAME), '+login', 'anonymous', '+app_update', '380870', 'validate', '+quit'], timeout=3600)
            if result.returncode or "Success! App '380870' fully installed" not in result.stdout:
                raise ValueError('SteamCMD did not confirm the update. Server stopped; safety backup available.')
            # Inspect packaged version metadata before starting an unexpected build.
            from classinfo import packaged_version
            if not packaged_version(S.GAME / 'java/projectzomboid.jar').startswith('42.'):
                raise ValueError('A version other than Build 42 was detected; review before starting.')
        if was_active:
            progress('Starting and checking'); start()
            if action == 'update' and not status()['version'].startswith('42.'):
                preserve_stop(); raise ValueError('The version is not Build 42. Server stopped; safety backup available.')
        return 'Restore completed.' if action == 'restore' else 'Update completed.'

def installed_mods():
    roots = [S.DATA / 'mods', S.GAME / 'steamapps/workshop/content/108600', S.STEAM.parent / 'steamapps/workshop/content/108600']
    found = []
    for root in roots:
        if not root.exists(): continue
        for p in root.rglob('mod.info'):
            if p.is_symlink() or p.stat().st_size > 100000: continue
            vals = {}
            for line in p.read_text(errors='replace').splitlines():
                if '=' in line:
                    key,value = line.split('=',1)
                    if key in ('id','name','require','versionMin','versionMax'): vals[key] = value
            vals['path'] = str(p.relative_to(root)); found.append(vals)
    return found

def daily():
    import sys
    from common import initialize
    initialize()
    today = dt.datetime.now(dt.timezone.utc)
    marker = S.STATE / 'daily.json'
    if today.hour < 4: return
    if marker.exists() and json.loads(marker.read_text()).get('date') == today.date().isoformat(): return
    try:
        result = execute('backup', {'confirmed':True, 'automatic':True}, lambda msg: print(msg, flush=True))
        print(result)
        if result.startswith('Verified backup:'): write_json(marker, {'date':today.date().isoformat()})
    except Exception as e:
        print(str(e), file=sys.stderr); raise SystemExit(1)

if __name__ == '__main__': daily()
