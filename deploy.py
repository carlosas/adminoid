"""One-time migration from the previous launcher, with verified graceful shutdown."""
import json
import os
import signal
import subprocess
import time
import zipfile
from pathlib import Path
import settings as S
import operations as O
from common import atomic, initialize, locked
from configuration import read_all
from classinfo import packaged_version

def deploy(leave_stopped=False):
    initialize()
    if not packaged_version(S.GAME/'java/projectzomboid.jar').startswith('42.'):
        raise ValueError('The installation is not Build 42.')
    with locked():
        units=Path.home()/'.config/systemd/user'
        saved=read_all();active=O.systemctl('is-active')=='active'
        if active:
            if S.SOCKET.exists(): O.preserve_stop()
            else:
                # The existing service has no stdin channel. Its installed shutdown hook
                # queues ServerMap.QueuedQuit on SIGTERM, which saves before exiting.
                with zipfile.ZipFile(S.GAME/'java/projectzomboid.jar') as jar:
                    hook=jar.read('zombie/network/GameServer$1.class')
                    if not all(s in hook for s in (b'QueuedQuit',b'Shutdown handling finished')):
                        raise ValueError('Unable to verify the shutdown of the previous service.')
                props=dict(x.split('=',1) for x in O.systemctl('show').splitlines() if '=' in x)
                parent=int(props['MainPID'])
                ids=Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
                ids=[int(p) for p in ids if Path(f'/proc/{p}/exe').resolve()==S.GAME/'ProjectZomboid64']
                if len(ids)!=1: raise ValueError('Previous server process is ambiguous; no signal will be sent.')
                stamp=time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime())
                print('Requesting shutdown through the verified save hook...',flush=True)
                os.kill(ids[0],signal.SIGTERM)
                deadline=time.monotonic()+300
                while O.systemctl('is-active') in ('active','deactivating') and time.monotonic()<deadline:time.sleep(2)
                if O.systemctl('is-active') in ('active','deactivating'):raise ValueError('The server did not stop; it will not be forced.')
                logs=O.command(['journalctl','--user-unit',S.SERVICE,'--since',stamp,'--no-pager','-o','cat']).stdout
                if 'Shutdown handling finished' not in logs:raise ValueError('Shutdown was not confirmed; review before continuing.')
                for scope,text in saved.items():atomic(S.DATA/'Server'/S.FILES[scope],text)
        print('Creating migration backup with the server stopped...',flush=True)
        print(O.backup('manual'),flush=True)
        old=S.STATE/'previous-units';old.mkdir(exist_ok=True,mode=0o700)
        for name in ('zomboid.service','zomboid-panel.service','zomboid-backup.service','zomboid-backup.timer'):
            if (units/name).exists() and not (old/name).exists():atomic(old/name,(units/name).read_text())
            atomic(units/name,(S.BASE/'units'/name).read_text())
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)
        subprocess.run(['systemctl','--user','enable','zomboid.service','zomboid-panel.service','zomboid-backup.timer'],check=True)
        subprocess.run(['systemctl','--user','restart','zomboid-backup.timer'],check=True)
        if active and not leave_stopped:
            print('Starting with local console and checking SERVER STARTED...',flush=True)
            O.start()
        else:
            print('Integration installed. The game remains stopped.',flush=True)
    print('Migration completed.',flush=True)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--leave-stopped',action='store_true')
    deploy(parser.parse_args().leave_stopped)
