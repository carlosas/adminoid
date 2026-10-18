"""Private, fixed-command console bridge; runs as the game systemd service."""
import collections
import json
import os
import re
import signal
import socketserver
import subprocess
import threading
import time

import settings as S
from common import initialize, write_json
from configuration import read_all, digest

def main():
    initialize()
    recovery=S.STATE/'shutdown-config.json'
    if recovery.exists():
        from common import atomic
        saved=json.loads(recovery.read_text())
        for scope,text in saved.items():
            if scope in S.FILES:atomic(S.DATA/'Server'/S.FILES[scope],text)
        recovery.unlink()
    from classinfo import packaged_version
    if not packaged_version(S.GAME/'java/projectzomboid.jar').startswith('42.'):
        raise SystemExit('The installed build is not Build 42.')
    try: S.SOCKET.unlink()
    except FileNotFoundError: pass
    cv = threading.Condition(); serial = threading.Lock(); lines = collections.deque(maxlen=1000)
    ready = threading.Event()
    requested = threading.Event()
    clean = threading.Event()
    proc = subprocess.Popen([str(S.GAME / 'start-server.sh'), '-servername', S.NAME], cwd=S.GAME,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, start_new_session=True)
    start_configs = read_all()

    def output():
        for line in proc.stdout:
            print(line, end='', flush=True)
            with cv:
                lines.append((time.monotonic(), line.rstrip())); cv.notify_all()
            if '*** SERVER STARTED' in line:
                write_json(S.STATE / 'applied.json', {k: digest(v) for k,v in start_configs.items()})
                write_json(S.STATE / 'applied-values.json', start_configs)
                ready.set()
            if 'Shutdown handling finished' in line: clean.set()
        ready.clear()

    threading.Thread(target=output, daemon=True).start()

    def send(command):
        if command not in ('players', 'save', 'quit'): raise ValueError('Command not allowed.')
        if not ready.is_set(): raise ValueError('The server has not finished starting.')
        with serial:
            start = time.monotonic()
            if command == 'quit': requested.set()
            proc.stdin.write(command + '\n'); proc.stdin.flush()
            if command == 'quit': return {'sent': True}
            deadline = start + (30 if command == 'players' else 90)
            while time.monotonic() < deadline:
                with cv:
                    cv.wait(timeout=0.3)
                    recent = [line for t,line in lines if t >= start]
                if command == 'players':
                    for idx, line in enumerate(recent):
                        m = re.search(r'Players connected\s*\((\d+)\)', line, re.I)
                        if m:
                            count = int(m[1])
                            names = [re.sub(r'^.*?>\s*', '', x).strip().lstrip('-').strip() for x in recent[idx+1:] if re.search(r'(?:^|>\s*)-\w', x)]
                            if count == 0 or len(names) >= count: return {'count': count, 'names': names[:count]}
                            # Count itself is authoritative even if player-name formatting changes.
                            if time.monotonic() - start > 3: return {'count': count, 'names': names[:count]}
                elif any('Saving finished' in x or 'SaveAll took' in x for x in recent): return {'saved': True}
            raise ValueError('The console response could not be confirmed.')

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(100)
            try:
                msg = json.loads(self.rfile.readline(4096))
                result = send(msg.get('command'))
            except Exception as e: result = {'error': str(e)}
            self.wfile.write((json.dumps(result) + '\n').encode())

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    server = Server(str(S.SOCKET), Handler)
    os.chmod(S.SOCKET, 0o600)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stopping = threading.Event()
    def shutdown(*_):
        if stopping.is_set(): return
        stopping.set()
        def request_quit():
            # During startup wait until it is safe to save; systemd has a generous deadline.
            if ready.wait(240):
                try: send('quit')
                except (ValueError, BrokenPipeError): pass
        threading.Thread(target=request_quit, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    result = proc.wait()
    # Let the reader drain the final shutdown messages.
    clean.wait(2)
    write_json(S.STATE/'game-exit.json',{'graceful':clean.is_set() and requested.is_set() and result==0,'time':time.time()})
    server.shutdown(); server.server_close()
    S.SOCKET.unlink(missing_ok=True)
    raise SystemExit(0 if clean.is_set() and requested.is_set() and result==0 else 1)

if __name__ == '__main__': main()
