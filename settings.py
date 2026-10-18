import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE = BASE / 'state'
HOME = Path.home()

def _load_env(path):
    """Minimal KEY=value reader; real environment variables take precedence."""
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            key, value = line.split('=', 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'': value = value[1:-1]
            values[key.strip()] = value
    values.update(os.environ)
    return values

ENV = _load_env(BASE / '.env')

def _path(key, default): return Path(ENV.get(key) or default).expanduser()

DATA = _path('PZ_DATA_DIR', HOME / 'Zomboid')
GAME = _path('PZ_GAME_DIR', HOME / 'zomboid-server/server')
STEAM = _path('PZ_STEAMCMD', HOME / 'zomboid-server/steamcmd/steamcmd.sh')
BACKUPS = _path('PZ_BACKUP_DIR', HOME / 'zomboid-backups')
NAME = ENV.get('PZ_SERVER_NAME') or 'servertest'
SERVICE = 'zomboid.service'
BIND = ENV.get('PANEL_BIND') or '127.0.0.1'
PORT = int(ENV.get('PANEL_PORT') or 8080)
# Empty: only loopback clients may reach the panel.
ALLOWED_NETWORK = ENV.get('PANEL_ALLOWED_NETWORK', '').strip()
SOCKET = STATE / 'console.sock'
FILES = {'server': NAME + '.ini', 'sandbox': NAME + '_SandboxVars.lua',
         'regions': NAME + '_spawnregions.lua', 'points': NAME + '_spawnpoints.lua'}
