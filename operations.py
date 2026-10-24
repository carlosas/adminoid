import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
from collections import deque
from pathlib import Path, PurePosixPath
from secrets import randbelow

import settings as S
from common import atomic, write_json, locked, record
from configuration import read_all, digest, SECRET, Document

def command(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)

def systemctl(action):
    if action not in ('start', 'stop', 'is-active', 'show'): raise ValueError('Invalid action.')
    args = ['systemctl', '--user', action, S.SERVICE]
    if action == 'show': args += ['-p', 'ActiveState', '-p', 'MemoryCurrent', '-p', 'MainPID', '-p', 'ActiveEnterTimestampMonotonic']
    result = command(args, timeout=360)
    if action in ('start', 'stop') and result.returncode: raise ValueError('systemd could not ' + action + ' the service.')
    return result.stdout.strip()

def console(cmd, **args):
    if cmd not in ('players', 'save', 'quit', 'message', 'kick'): raise ValueError('Invalid command.')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(100); sock.connect(str(S.SOCKET))
            sock.sendall((json.dumps({'command': cmd, 'args': args}) + '\n').encode())
            data = b''
            while b'\n' not in data:
                block = sock.recv(4096)
                if not block or len(data) > 100000: raise ValueError('Incomplete console response.')
                data += block
        result = json.loads(data)
    except (OSError, ValueError) as e: raise ValueError('Local console unavailable; player status unknown.') from e
    if result.get('error'): raise ValueError(result['error'])
    return result

_first_seen = {}

def players():
    if systemctl('is-active') not in ('active', 'activating'):
        _first_seen.clear(); return {'count': 0, 'names': [], 'since': {}}
    try: result = console('players')
    except ValueError as e: return {'count': None, 'names': [], 'since': {}, 'error': str(e)}
    try: joined = json.loads((S.STATE / 'sessions.json').read_text())
    except (OSError, ValueError): joined = {}
    now = time.time()
    for name in list(_first_seen):
        if name not in result['names']: del _first_seen[name]
    # Join time from the game log when the runner saw it; otherwise the first time the panel saw them.
    result['since'] = {name: joined.get(name) or _first_seen.setdefault(name, now) for name in result['names']}
    return result

def broadcast(text):
    return console('message', text=text)

def kick(name, reason=''):
    if name not in players()['names']: raise ValueError('That player is not connected.')
    return console('kick', user=name, reason=reason or None)

def secrets():
    values = []
    for content in read_all().values():
        for line in content.splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                if SECRET.search(key) and value.strip(): values.append(value.strip())
    return values

def redact(text, hidden=None):
    for value in (secrets() if hidden is None else hidden): text = text.replace(value, '[hidden]')
    return re.sub(r'(?i)((?:password|secret|token)\s*[=:]\s*)\S+', r'\1[oculto]', text)

LOG_UNITS = {'game': S.SERVICE, 'panel': 'adminoid.service', 'backup': 'zomboid-backup.service'}
LOG_LINE = re.compile(r'^(?P<level>LOG|INFO|WARN|WARNING|ERROR|SEVERE|DEBUG|TRACE)\s*:\s*(?P<category>[A-Za-z][\w.-]*)'
                      r'[\s,]*(?:f:\d+,\s*)?(?:t:)?(?:\d{10,}>)?\s*(?:[\d,]+>)?\s*(?P<text>.*)$')
LEVELS = {'WARN': 'warn', 'WARNING': 'warn', 'ERROR': 'error', 'SEVERE': 'error'}
CONNECTION = re.compile(r'connect|login|logged in|kicked|ConnectionManager|RakNet', re.I)

def parse_log_line(text):
    m = LOG_LINE.match(text)
    if m:
        level, category, body = LEVELS.get(m['level'], 'info'), m['category'], m['text']
    else:
        level, category, body = 'info', '', text
        if re.search(r'\b(?:Exception|Error)\b|^\s+at \S+\(', text): level = 'error'
        elif re.search(r'\bwarn(?:ing)?\b', text, re.I): level = 'warn'
    connection = category in ('Network', 'Multiplayer') or bool(CONNECTION.search(body))
    return {'level': level, 'category': category, 'text': body, 'connection': connection}

def logs(source='game', cursor=None, until=None, lines=1000):
    """Structured journal entries. cursor follows new lines; until pages to older ones."""
    if source not in LOG_UNITS: raise ValueError('Invalid log source.')
    lines = max(1, min(int(lines), 5000))
    args = ['journalctl', '--user-unit', LOG_UNITS[source], '-o', 'json', '--no-pager', '-n', str(lines)]
    if cursor:
        if not isinstance(cursor, str) or len(cursor) > 512 or not re.fullmatch(r'[\w;=+-]+', cursor): raise ValueError('Invalid log cursor.')
        args += ['--after-cursor', cursor]
    elif until is not None: args += ['--until', '@%.6f' % float(until)]
    output = command(args, timeout=30).stdout
    entries, last = [], cursor
    hidden = secrets()
    for raw in output.splitlines():
        try: item = json.loads(raw)
        except ValueError: continue
        text = item.get('MESSAGE', '')
        if isinstance(text, list): text = bytes(text).decode('utf-8', 'replace')
        if not isinstance(text, str): continue
        last = item.get('__CURSOR', last)
        entry = parse_log_line(redact(text.rstrip(), hidden))
        entry['time'] = int(item.get('__REALTIME_TIMESTAMP', 0)) / 1e6
        entries.append(entry)
    return {'entries': entries, 'cursor': last}

def log_text(source='game'):
    if source not in LOG_UNITS: raise ValueError('Invalid log source.')
    result = command(['journalctl', '--user-unit', LOG_UNITS[source], '-n', '5000', '--no-pager', '-o', 'short-iso'], timeout=30)
    return redact(result.stdout)

def changed_keys(scope, before, after):
    """Keys whose saved value differs from the one read at the last start.
    Keys only on one side are left out: the game adds defaults (new options, mod settings) after starting,
    and the panel never adds or removes keys."""
    before, after = Document(scope, before).fields, Document(scope, after).fields
    return [k for k in after if k in before and before[k].value != after[k].value]

def same_configuration(scope, before, after):
    """Ignore comments/formatting rewritten by the game; compare effective values."""
    try: return not changed_keys(scope, before, after)
    except ValueError: return before == after

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
    uptime = None
    entered = props.get('ActiveEnterTimestampMonotonic', '')
    if props.get('ActiveState') == 'active' and entered.isdigit() and int(entered):
        # systemd and time.monotonic() share CLOCK_MONOTONIC on Linux.
        uptime = max(0, time.monotonic() - int(entered) / 1e6)
    try: max_players = Document('server', current_text['server']).fields['MaxPlayers'].value
    except (KeyError, ValueError): max_players = None
    backups = backup_list()
    return {'state': props.get('ActiveState'), 'memory': props.get('MemoryCurrent'),
            'version': version[1] if version else 'Desconocida', 'console': S.SOCKET.exists(),
            'pending': pending, 'pending_changes': pending_changes(applied_values, current_text, pending),
            'applied_known': bool(applied), 'name': S.NAME, 'uptime': uptime, 'max_players': max_players,
            'last_backup': backups[0] if backups else None}

def pending_changes(applied_values, current_text, pending):
    """Saved values that differ from the ones read at the last start, per file."""
    result = {}
    for scope in pending:
        try: result[scope] = len(changed_keys(scope, applied_values[scope], current_text[scope]))
        except (KeyError, ValueError): result[scope] = None
    return result

_samples = deque(maxlen=240)
_samples_lock = threading.Lock()

def sample():
    """Record service memory for the overview chart; called every minute by the panel."""
    props = dict(x.split('=', 1) for x in systemctl('show').splitlines() if '=' in x)
    memory = props.get('MemoryCurrent', '')
    value = int(memory) if memory.isdigit() and props.get('ActiveState') == 'active' else None
    with _samples_lock:
        _load_samples()
        _samples.append({'time': time.time(), 'memory': value})
        write_json(S.STATE / 'metrics.json', list(_samples))

def _load_samples():
    # Restore the saved history after a panel restart; caller holds _samples_lock.
    if _samples: return
    try: _samples.extend(json.loads((S.STATE / 'metrics.json').read_text()))
    except (OSError, ValueError): pass

def history():
    with _samples_lock:
        _load_samples()
        cutoff = time.time() - 4 * 3600
        return [x for x in _samples if x['time'] >= cutoff]

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
    if label not in ('daily', 'manual', 'pre-update', 'pre-restore', 'pre-wipe'): raise ValueError('Invalid backup type.')
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

BACKUP_KINDS = {'daily': 'automatic', 'manual': 'manual', 'pre-update': 'rescue', 'pre-restore': 'rescue', 'pre-wipe': 'rescue'}

def backup_list():
    result = []
    for p in S.BACKUPS.glob('zomboid-*.tar.gz'):
        if not p.is_file() or p.is_symlink(): continue
        label = re.match(r'zomboid-(daily|manual|pre-update|pre-restore|pre-wipe)-', p.name)
        info = p.stat()
        result.append({'name': p.name, 'size': info.st_size, 'mtime': info.st_mtime,
                       'kind': BACKUP_KINDS[label[1]] if label else 'other', 'label': label[1] if label else None})
    return sorted(result, key=lambda b: b['mtime'], reverse=True)

def disk_usage():
    total = sum(b['size'] for b in backup_list())
    try: free = shutil.disk_usage(S.BACKUPS).free
    except OSError: free = None
    return {'used': total, 'free': free}

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

def wipe():
    """Delete the world and everything tied to its characters; configuration, accounts (db/), mods and logs stay.
    The game generates a new world on the next start."""
    removed = []
    # The world save (map, players.db, vehicles.db, mod data) and the game's own zipped copies of it.
    for p in (S.DATA / 'Saves' / 'Multiplayer' / S.NAME, S.DATA / 'backups' / 'startup', S.DATA / 'backups' / 'version'):
        if p.is_symlink(): raise ValueError('Data contains links; review before wiping.')
        if p.is_dir(): shutil.rmtree(p); removed.append(str(p.relative_to(S.DATA)))
    # Files mods write on the server (per-player counters and similar).
    lua = S.DATA / 'Lua'
    if lua.is_dir() and not lua.is_symlink():
        for p in lua.iterdir():
            if p.is_file() and not p.is_symlink(): p.unlink(); removed.append(str(p.relative_to(S.DATA)))
    # A new ResetID makes clients drop the characters they cached for the old world.
    ini = S.DATA / 'Server' / S.FILES['server']
    text = ini.read_text()
    old = re.search(r'(?m)^ResetID=(.*)$', text)
    if old:
        current = value = old[1].strip()
        while value == current: value = str(randbelow(10**9))
        atomic(ini, text[:old.start(1)] + value + text[old.end(1):])
    return removed

# Steps shown in the panel while an operation runs, keyed by the progress messages below.
STEPS = {'start': ['start'], 'stop': ['warn', 'stop'], 'restart': ['warn', 'stop', 'start'],
         'backup': ['warn', 'stop', 'backup', 'start'], 'restore': ['warn', 'stop', 'safety', 'restore', 'start'],
         'update': ['warn', 'stop', 'safety', 'steamcmd', 'start', 'version'], 'wipe': ['warn', 'stop', 'safety', 'wipe', 'start']}
STEP_OF = {'Warning connected players': 'warn', 'Saving and shutting down gracefully': 'stop',
           'Starting': 'start', 'Starting and checking': 'start', 'Creating and verifying backup': 'backup',
           'Creating a safety backup': 'safety', 'Restoring validated backup': 'restore',
           'Updating with SteamCMD; this may take several minutes': 'steamcmd', 'Checking version': 'version', 'Wiping world and player data': 'wipe'}
WARNING_SECONDS = 10

def execute(action, payload, progress):
    if action not in ('start', 'stop', 'restart', 'backup', 'restore', 'update', 'wipe'): raise ValueError('Invalid operation.')
    with locked():
        if action == 'start':
            if systemctl('is-active') == 'active': return 'The server was already running.'
            progress('Starting'); start(); return 'Server started.'
        state = players()
        if not payload.get('confirmed'): raise ValueError('Confirm the operation and possible interruption of connected players.')
        if payload.get('automatic') and state['count'] != 0: return 'Deferred: players are connected or their status is unknown.'
        target = resolve_backup(payload.get('backup')) if action == 'restore' else None
        if target: validate_archive(target)
        if state.get('count') and payload.get('notice'):
            progress('Warning connected players')
            try:
                broadcast(payload['notice']); time.sleep(WARNING_SECONDS)
            except ValueError: pass
        progress('Saving and shutting down gracefully')
        was_active = preserve_stop()
        if action == 'stop': return 'Server stopped; saved configuration preserved.'
        if action == 'restart': progress('Starting and checking'); start(); return 'Server restarted.'
        if action == 'backup':
            try:
                progress('Creating and verifying backup')
                result = backup('daily' if payload.get('automatic') else 'manual')
            finally:
                if was_active: progress('Starting and checking'); start()
            return 'Verified backup: ' + result
        progress('Creating a safety backup')
        backup({'restore': 'pre-restore', 'wipe': 'pre-wipe'}.get(action, 'pre-update'))
        if action == 'restore':
            progress('Restoring validated backup'); restore(target)
        elif action == 'wipe':
            progress('Wiping world and player data'); wipe()
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
            if action == 'update': progress('Checking version')
            if action == 'update' and not status()['version'].startswith('42.'):
                preserve_stop(); raise ValueError('The version is not Build 42. Server stopped; safety backup available.')
        return {'restore': 'Restore completed.', 'wipe': 'World wiped. A new world is generated on the next start.'}.get(action, 'Update completed.')

def mod_ids(text):
    """Mods line and mod.info 'require' entries: separated by ; or , and optionally prefixed by a backslash (Build 42)."""
    return [x.strip().lstrip('\\').strip() for x in re.split(r'[;,]', text or '') if x.strip().lstrip('\\').strip()]

def installed_mods(dirs=False):
    """Mods found on disk; with dirs, each also carries the folder holding its mod.info."""
    roots = [(S.DATA / 'mods', False)] + [(root, True) for root in workshop_roots()]
    found = {}
    for root, workshop in roots:
        if not root.exists(): continue
        for p in sorted(root.rglob('mod.info')):
            if p.is_symlink() or p.stat().st_size > 100000: continue
            vals = {}
            for line in p.read_text(errors='replace').splitlines():
                if '=' in line:
                    key,value = line.split('=',1)
                    key = key.strip()
                    if key in ('id','name','require','versionMin','versionMax'): vals[key] = value.strip()
            relative = p.relative_to(root)
            vals['path'] = str(relative)
            vals['workshop'] = relative.parts[0] if workshop and relative.parts[0].isdigit() else None
            vals['requires'] = mod_ids(vals.pop('require', ''))
            ident = vals.get('id') or relative.parent.name
            vals['id'] = ident
            vals['dir'] = p.parent
            # Build 42 mods ship mod.info in several folders (common, 42, ...); prefer the 42 one.
            if ident not in found or '42' in relative.parts: found[ident] = vals
    mods = sorted(found.values(), key=lambda m: (m.get('name') or m['id']).lower())
    return mods if dirs else [{k: v for k, v in m.items() if k != 'dir'} for m in mods]

def mods_report():
    """Installed mods cross-checked against the saved Mods line of the server INI."""
    try: fields = Document('server', read_all()['server']).fields
    except (KeyError, ValueError): fields = {}
    active_raw = [x for x in (fields['Mods'].value if 'Mods' in fields else '').split(';') if x.strip()]
    active = [x.strip().lstrip('\\') for x in active_raw]
    workshop = [x for x in (fields['WorkshopItems'].value if 'WorkshopItems' in fields else '').split(';') if x.strip()]
    installed = installed_mods()
    known = {m['id'] for m in installed}
    issues = []
    for position, ident in enumerate(active):
        mod = next((m for m in installed if m['id'] == ident), None)
        if not mod:
            issues.append({'kind': 'not-installed', 'mod': ident}); continue
        for dep in mod['requires']:
            if dep not in active: issues.append({'kind': 'missing', 'mod': ident, 'name': mod.get('name') or ident, 'requires': dep, 'installed': dep in known})
            elif active.index(dep) > position: issues.append({'kind': 'order', 'mod': ident, 'name': mod.get('name') or ident, 'requires': dep})
    return {'installed': installed, 'active': active_raw, 'workshop': workshop, 'issues': issues,
            'editable': 'Mods' in fields}

def workshop_roots():
    return [S.GAME / 'steamapps/workshop/content/108600', S.STEAM.parent / 'steamapps/workshop/content/108600']

def delete_workshop(ident):
    """Delete the downloaded files of a Workshop item the server no longer uses (not in the saved WorkshopItems or Mods lines)."""
    if not isinstance(ident, str) or not re.fullmatch(r'\d{1,20}', ident): raise ValueError('Invalid Workshop ID.')
    report = mods_report()
    if ident in report['workshop']: raise ValueError('The item is still in WorkshopItems. Remove it and save first.')
    active = {x.strip().lstrip('\\') for x in report['active']}
    mods = [m for m in report['installed'] if m['workshop'] == ident]
    if any(m['id'] in active for m in mods): raise ValueError('One of its mods is still in the Mods line. Remove it and save first.')
    targets = [p for root in workshop_roots() for p in (root / ident, root.parent.parent / 'downloads/108600' / ident)]
    targets = [p for p in targets if p.is_dir() and not p.is_symlink()]
    if not targets: raise ValueError('No downloaded files were found for this item.')
    with locked():
        for p in targets: shutil.rmtree(p)
    return [m['id'] for m in mods]

# Steam Workshop lookups: title and Mod IDs from the item description, required items from its page.
WORKSHOP_API = 'https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/'
WORKSHOP_PAGE = 'https://steamcommunity.com/sharedfiles/filedetails/?id='
workshop_cache, workshop_lock = {}, threading.Lock()

def fetch(url, data=None):
    import urllib.request, urllib.parse
    body = urllib.parse.urlencode(data).encode() if data else None
    request = urllib.request.Request(url, data=body, headers={'User-Agent': 'adminoid'})
    with urllib.request.urlopen(request, timeout=8) as r: return r.read(2_000_000).decode('utf-8', 'replace')

def description_mod_ids(text):
    """'Mod ID: a' lines (the convention on Project Zomboid Workshop pages), BBCode and HTML removed."""
    found = []
    for line in re.sub(r'\[/?[a-z0-9*]+(=[^\]]*)?\]|<[^>]+>', '', text or '', flags=re.I).splitlines():
        match = re.match(r'\s*mod\s*ids?\s*[:=]\s*(.+)', line, re.I)
        if not match: continue
        for ident in mod_ids(match.group(1)):
            if re.fullmatch(r'[^\s"<>\[\]]{1,100}', ident) and ident not in found: found.append(ident)
    return found

def required_items(page):
    return list(dict.fromkeys(re.findall(r'<a [^>]*href="[^"]*filedetails/\?id=(\d+)"[^>]*>\s*<div class="requiredItem">', page)))

def steam_items(ids):
    """Steam data per Workshop ID, cached for an hour; items Steam could not answer are left out."""
    now = time.time(); out = {}
    with workshop_lock: missing = [i for i in ids if not (i in workshop_cache and now - workshop_cache[i]['at'] < 3600)]
    if missing:
        data = {'itemcount': len(missing), **{f'publishedfileids[{n}]': i for n, i in enumerate(missing)}}
        try: details = json.loads(fetch(WORKSHOP_API, data))['response']['publishedfiledetails']
        except Exception: details = []
        def page(i):
            try: return required_items(fetch(WORKSHOP_PAGE + i))
            except Exception: return []
        details = [d for d in details if d.get('result') == 1 and str(d.get('publishedfileid', '')).isdigit()]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as pool: pages = list(pool.map(page, [d['publishedfileid'] for d in details]))
        for d, requires in zip(details, pages):
            with workshop_lock:
                workshop_cache[d['publishedfileid']] = {'at': now, 'title': d.get('title') or '', 'modIds': description_mod_ids(d.get('description')),
                                                        'requires': [r for r in requires if r != d['publishedfileid']]}
    with workshop_lock:
        for i in ids:
            if i in workshop_cache: out[i] = {k: v for k, v in workshop_cache[i].items() if k != 'at'}
    return out

def workshop_info(ids, depth=3):
    """Workshop items with their Mod IDs plus the items they require, required ones first.
    Mod IDs of downloaded items come from their mod.info files; the rest from the Workshop description."""
    ids = [i for i in dict.fromkeys(ids) if re.fullmatch(r'\d{1,20}', i)][:20]
    local = {}
    for m in installed_mods():
        if m['workshop']: local.setdefault(m['workshop'], []).append(m)
    items, order, pending = {}, [], list(ids)
    for _ in range(depth):
        if not pending: break
        steam = steam_items(pending)
        for i in pending:
            info = steam.get(i, {})
            mods = local.get(i, [])
            items[i] = {'id': i, 'title': info.get('title') or (mods[0].get('name') if len(mods) == 1 else ''),
                        'modIds': [m['id'] for m in mods] or info.get('modIds', []), 'downloaded': bool(mods),
                        'requires': info.get('requires', []), 'found': bool(info) or bool(mods)}
        pending = [r for i in pending for r in items[i]['requires'] if r not in items][:20]
    def visit(i, seen):
        if i in seen or i not in items: return
        seen.add(i)
        for r in items[i]['requires']: visit(r, seen)
        if i not in order: order.append(i)
    for i in ids: visit(i, set())
    return {'items': [items[i] for i in order]}

# Game updates: the installed build from the Steam app manifest, the latest one from the public branch on Steam.
GAME_APP = '380870'
game_cache, game_lock = {}, threading.Lock()

def installed_build():
    try: text = (S.GAME / 'steamapps' / f'appmanifest_{GAME_APP}.acf').read_text(errors='replace')
    except OSError: return None
    match = re.search(r'"buildid"\s+"(\d+)"', text)
    return match[1] if match else None

def public_branch(text):
    """buildid and build date of the public branch in SteamCMD's app_info_print output."""
    start = text.find('"branches"')
    match = re.compile(r'"public"\s*\{([^{}]*)\}').search(text, start) if start >= 0 else None
    if not match: return None
    values = dict(re.findall(r'"(\w+)"\s+"([^"]*)"', match[1]))
    if not values.get('buildid', '').isdigit(): return None
    published = values.get('timebuildupdated') or values.get('timeupdated') or ''
    return {'build': values['buildid'], 'published': int(published) if published.isdigit() else None}

def game_update(refresh=False):
    """Compare the installed build with Steam's; Steam is asked at most once an hour unless refresh is set."""
    with game_lock:
        latest = game_cache.get('latest')
        stale = not latest or time.time() - latest['checked'] > 3600
        if refresh or (stale and time.time() - game_cache.get('failed', 0) > 600):
            try:
                # Holding the operation lock keeps this SteamCMD run away from an update's.
                with locked():
                    result = command([str(S.STEAM), '+@sSteamCmdForcePlatformType', 'linux', '+login', 'anonymous',
                                      '+app_info_update', '1', '+app_info_print', GAME_APP, '+quit'], timeout=180)
                found = public_branch(result.stdout)
                if not found: raise ValueError('No public branch in the SteamCMD output.')
                latest = game_cache['latest'] = {**found, 'checked': time.time()}
            except (ValueError, OSError, subprocess.SubprocessError):
                game_cache['failed'] = time.time()
                if refresh: latest = None
    installed = installed_build()
    available = None if not (installed and latest) else latest['build'] != installed
    return {'installed': installed, 'latest': latest['build'] if latest else None,
            'published': latest['published'] if latest else None, 'checked': latest['checked'] if latest else None,
            'available': available}

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
        if result.startswith('Verified backup:'):
            write_json(marker, {'date':today.date().isoformat()})
            record('backup', 'ok', automatic=True, message=result)
    except Exception as e:
        record('backup', 'error', automatic=True, message=str(e) if isinstance(e, ValueError) else 'Operation error; check the panel service log.')
        print(str(e), file=sys.stderr); raise SystemExit(1)

if __name__ == '__main__': daily()
