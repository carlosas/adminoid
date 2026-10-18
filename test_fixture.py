"""Test fixture only: a fake installation (config, translations, mods) and fake systemd/journal/console.

Used by test_browser.py and by `python test_fixture.py` to preview the panel locally without a game server.
"""
import contextlib
import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import settings as S
import operations as O
from common import atomic, write_json, record

INI = '''# Players can hurt and kill other players
PVP=false
# Pause the game when no players are online
PauseEmpty=true
# Maximum players. Min: 1 Max: 100 Default: 32
MaxPlayers=16
# Message shown when joining
ServerWelcomeMessage=Welcome to the server
# Server password
Password=old-secret
# Public listing
Public=false
# Minutes between automatic world saves. Min: 0 Max: 1440 Default: 0
SaveWorldEveryMinutes=15
ResetID=99
Mods=\\CommonSense;\\truemusic;\\BetterCarPhysics;\\MiModLocal;\\GhostMod
WorkshopItems=2875848298;2613146550;2850935956
Map=Muldraugh, KY
'''

LUA = '''SandboxVars = {
    VERSION = 6,
    -- 1 = Insane
    -- 2 = Very High
    -- 3 = High
    -- 4 = Normal
    -- 5 = Low
    -- 6 = None
    -- Min: 1 Max: 6 Default: 4
    Zombies = 4,
    -- 1 = 15 minutes
    -- 2 = 30 minutes
    -- 3 = 1 hour
    -- 4 = 2 hours
    -- 5 = 3 hours
    -- 6 = 4 hours
    -- 7 = 5 hours
    -- 8 = 6 hours
    -- Min: 1 Max: 8 Default: 3
    DayLength = 3,
    -- 1 = Instant
    -- 2 = 0-30 Days
    -- 3 = 0-2 Months
    -- Min: 1 Max: 3 Default: 2
    WaterShut = 2,
    -- Min: 0.00 Max: 60.00 Default: 1.00
    MinutesPerPage = 1.0,
    ZombieConfig = {
        -- Min: 0.00 Max: 4.00 Default: 1.00
        PopulationMultiplier = 1.0,
        -- Min: 0.00 Max: 8760.00 Default: 72.00
        RespawnHours = 72.0,
    },
    ZombieLore = {
        -- 1 = Sprinters
        -- 2 = Fast Shamblers
        -- 3 = Shamblers
        -- 4 = Random
        -- Min: 1 Max: 4 Default: 3
        Speed = 3,
        ThumpOnConstruction = true,
    },
}
'''

REGIONS = '''function SpawnRegions()
    return {
        { name = "Muldraugh, KY", file = "media/maps/Muldraugh, KY/spawnpoints.lua" },
        { name = "Riverside, KY", file = "media/maps/Riverside, KY/spawnpoints.lua" },
    }
end
'''

SCREEN = '''SettingsTable = {
    {
        name = "Details",
        settings = {
            { name = "ServerWelcomeMessage" },
            { name = "Public" },
            { name = "Password" },
            { name = "MaxPlayers" },
        },
    },
    {
        name = "PVP",
        settings = {
            { name = "PVP" },
        },
    },
    {
        name = "Saving",
        settings = {
            { name = "SaveWorldEveryMinutes" },
            { name = "PauseEmpty" },
        },
    },
    {
        name = "Sandbox",
        settings = {
            {
                name = "Zombie",
                settings = {
                    { name = "Zombies" },
                    { name = "ZombieConfig.PopulationMultiplier" },
                    { name = "ZombieConfig.RespawnHours" },
                    { name = "ZombieLore.Speed" },
                    { name = "ZombieLore.ThumpOnConstruction" },
                },
            },
            {
                name = "World",
                settings = {
                    { name = "DayLength" },
                    { name = "WaterShut" },
                    { name = "MinutesPerPage" },
                },
            },
        },
    },
}
'''

TRANSLATIONS = {
    'EN': {
        'UI': {'UI_ServerSettingGroup_Details': 'Details', 'UI_ServerSettingGroup_PVP': 'PvP', 'UI_ServerSettingGroup_Saving': 'Saving',
               'UI_ServerOption_PVP': 'PvP', 'UI_ServerOption_PauseEmpty': 'Pause when empty', 'UI_ServerOption_MaxPlayers': 'Maximum players',
               'UI_ServerOption_ServerWelcomeMessage': 'Welcome message', 'UI_ServerOption_Password': 'Password', 'UI_ServerOption_Public': 'Public',
               'UI_ServerOption_SaveWorldEveryMinutes': 'Save every N minutes'},
        'Sandbox': {'Sandbox_Zombie': 'Zombies', 'Sandbox_World': 'World', 'Sandbox_ZombieCount': 'Zombie population',
                    'Sandbox_ZombieCount_option1': 'Insane', 'Sandbox_ZombieCount_option2': 'Very high', 'Sandbox_ZombieCount_option3': 'High',
                    'Sandbox_ZombieCount_option4': 'Normal', 'Sandbox_ZombieCount_option5': 'Low', 'Sandbox_ZombieCount_option6': 'None',
                    'Sandbox_ZombieCount_tooltip': 'How many zombies there are when the world starts.',
                    'Sandbox_PopulationMultiplier': 'Population multiplier', 'Sandbox_RespawnHours': 'Respawn hours',
                    'Sandbox_RespawnHours_tooltip': 'In-game hours until an empty area repopulates. 0 disables it.',
                    'Sandbox_Speed': 'Speed', 'Sandbox_ThumpOnConstruction': 'Thump on construction',
                    'Sandbox_DayLength': 'Day length', 'Sandbox_WaterShut': 'Water shutoff', 'Sandbox_MinutesPerPage': 'Minutes per page'},
    },
    'ES': {
        'UI': {'UI_ServerSettingGroup_Details': 'Detalles', 'UI_ServerSettingGroup_PVP': 'PvP', 'UI_ServerSettingGroup_Saving': 'Guardado',
               'UI_ServerOption_PauseEmpty': 'Pausar si está vacío', 'UI_ServerOption_MaxPlayers': 'Máximo de jugadores',
               'UI_ServerOption_ServerWelcomeMessage': 'Mensaje de bienvenida', 'UI_ServerOption_Password': 'Contraseña', 'UI_ServerOption_Public': 'Público',
               'UI_ServerOption_SaveWorldEveryMinutes': 'Guardar cada N minutos'},
        'Sandbox': {'Sandbox_Zombie': 'Zombis', 'Sandbox_World': 'Mundo', 'Sandbox_ZombieCount': 'Población de zombis',
                    'Sandbox_ZombieCount_option1': 'Insana', 'Sandbox_ZombieCount_option2': 'Muy alta', 'Sandbox_ZombieCount_option3': 'Alta',
                    'Sandbox_ZombieCount_option4': 'Normal', 'Sandbox_ZombieCount_option5': 'Baja', 'Sandbox_ZombieCount_option6': 'Ninguna',
                    'Sandbox_ZombieCount_tooltip': 'Cuántos zombis hay en el mundo al empezar.',
                    'Sandbox_PopulationMultiplier': 'Multiplicador de población', 'Sandbox_RespawnHours': 'Horas para reaparecer',
                    'Sandbox_Speed': 'Velocidad', 'Sandbox_ThumpOnConstruction': 'Golpean construcciones',
                    'Sandbox_DayLength': 'Duración del día', 'Sandbox_WaterShut': 'Corte del agua', 'Sandbox_MinutesPerPage': 'Minutos por página'},
    },
}

MODS = {
    'local': {'MiModLocal': 'name=Mod local de pruebas\nid=MiModLocal\n'},
    '2875848298': {'CommonSense': 'name=Common Sense\nid=CommonSense\nversionMin=42.0\n'},
    '2613146550': {'truemusic': 'name=True Music\nid=truemusic\nversionMin=42.0\n',
                   'tsarslib': 'name=tsarslib\nid=tsarslib\nversionMin=42.0\n'},
    '2850935956': {'BetterCarPhysics': 'name=Better Car Physics\nid=BetterCarPhysics\nrequire=\\tsarslib\nversionMin=42.0\nversionMax=42.99\n'},
    '2200148440': {'Brita': 'name=Brita\'s Weapon Pack\nid=Brita\nversionMax=41.78\n'},
}

LOG = ['LOG  : General      f:0, t:{ms}> Saving world...', 'LOG  : General      f:0, t:{ms}> World saved (1.8 s)',
       'LOG  : Network      f:0, t:{ms}> ConnectionManager: [fully-connected] "" connection: guid=1 username="Spiffo"',
       'WARN : General      f:0, t:{ms}> Slow chunk load at 34,21 (412 ms)', 'LOG  : Network      f:0, t:{ms}> Player "Kate" ping 38 ms',
       'ERROR: Lua          f:0, t:{ms}> Exception thrown in BetterCarPhysics/client/main.lua:88',
       'LOG  : Workshop     f:0, t:{ms}> All items up to date']

def build(root):
    """Create a complete fake installation under root and point settings at it."""
    S.STATE = root / 'state'; S.DATA = root / 'Zomboid'; S.BACKUPS = root / 'backups'; S.GAME = root / 'game'
    S.STEAM = root / 'steamcmd/steamcmd.sh'; S.SOCKET = S.STATE / 'console.sock'
    for d in (S.STATE, S.DATA / 'Server', S.BACKUPS, S.GAME): d.mkdir(parents=True, exist_ok=True)
    atomic(S.DATA / 'Server' / S.FILES['server'], INI); atomic(S.DATA / 'Server' / S.FILES['sandbox'], LUA)
    atomic(S.DATA / 'Server' / S.FILES['regions'], REGIONS)
    atomic(S.GAME / 'media/lua/client/OptionScreens/ServerSettingsScreen.lua', SCREEN)
    for lang, files in TRANSLATIONS.items():
        for name, values in files.items(): write_json(S.GAME / 'media/lua/shared/Translate' / lang / (name + '.json'), values)
    for workshop, mods in MODS.items():
        for folder, info in mods.items():
            base = S.DATA / 'mods' / folder if workshop == 'local' else S.GAME / 'steamapps/workshop/content/108600' / workshop / 'mods' / folder / '42'
            atomic(base / 'mod.info', info)
    # Configuration applied at the last start: one sandbox value differs, so a restart is pending.
    applied = {'server': INI, 'sandbox': LUA.replace('Zombies = 4', 'Zombies = 3'), 'regions': REGIONS}
    write_json(S.STATE / 'applied-values.json', applied)
    write_json(S.STATE / 'applied.json', {k: 'x' for k in applied})
    now = time.time()
    for i, (label, age) in enumerate([('daily', 2500), ('pre-update', 12000), ('manual', 55000), ('daily', 90000), ('daily', 180000), ('daily', 270000), ('daily', 360000)]):
        stamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime(now - age))
        p = S.BACKUPS / f'zomboid-{label}-{stamp}{i:06d}Z.tar.gz'
        p.write_bytes(b'x' * (1000 + i)); import os; os.utime(p, (now - age, now - age))
    record('started', 'info', seconds=48)
    record('config', 'warn', count=3, files=['sandbox'])
    record('backup', 'ok', automatic=True, message='Verified backup: zomboid-daily.tar.gz')
    write_json(S.STATE / 'sessions.json', {'Kate': now - 6000, 'Baldspot': now - 3100})
    write_json(S.STATE / 'metrics.json', [{'time': now - 3600 + i * 120, 'memory': int((3.0 + i * 0.015) * 1024**3)} for i in range(30)])

@contextlib.contextmanager
def fake_services(operation_delay=0.4):
    """Replace systemd, journald and the game console with in-memory fakes."""
    state = {'active': True, 'players': ['Kate', 'Baldspot', 'Spiffo'], 'messages': [], 'kicked': [], 'log': []}
    started = max(1.0, time.monotonic() - 11520)
    lock = threading.Lock()

    def systemctl(action):
        if action == 'show':
            return '\n'.join([f"ActiveState={'active' if state['active'] else 'inactive'}", 'MemoryCurrent=3661627392', 'MainPID=48211',
                              f'ActiveEnterTimestampMonotonic={int(started * 1e6)}'])
        if action == 'is-active': return 'active' if state['active'] else 'inactive'
        state['active'] = action == 'start'
        return ''

    def console(cmd, **args):
        if not state['active']: raise ValueError('Local console unavailable; player status unknown.')
        if cmd == 'players': return {'count': len(state['players']), 'names': list(state['players'])}
        if cmd == 'message': state['messages'].append(args['text']); return {'sent': True}
        if cmd == 'kick':
            state['players'].remove(args['user']); state['kicked'].append(args['user']); return {'sent': True}
        return {'saved': True}

    counter = [0]
    def journal_lines(n):
        with lock:
            out = []
            for _ in range(n):
                counter[0] += 1
                text = LOG[counter[0] % len(LOG)].replace('{ms}', str(int(time.time() * 1000)))
                out.append(json.dumps({'MESSAGE': text, '__REALTIME_TIMESTAMP': str(int((time.time() - 60 + counter[0] * 0.01) * 1e6)), '__CURSOR': f's=1;i={counter[0]}'}))
            return out

    from subprocess import CompletedProcess
    def command(args, timeout=20):
        if args[0] == 'journalctl':
            if '-g' in args: return CompletedProcess(args, 0, 'version=42.12.3 demo', '')
            if '-o' in args and args[args.index('-o') + 1] == 'json':
                n = int(args[args.index('-n') + 1])
                return CompletedProcess(args, 0, '\n'.join(journal_lines(min(n, 40) if '--after-cursor' not in args else 1)), '')
            return CompletedProcess(args, 0, 'Oct 18 13:30:58 host start-server.sh[1]: World saved\n', '')
        return CompletedProcess(args, 1, '', 'not available in tests')

    def execute(action, payload, progress):
        with O.locked():
            for step in O.STEPS[action]:
                message = next(m for m, s in O.STEP_OF.items() if s == step)
                progress(message); time.sleep(operation_delay)
            state['active'] = action != 'stop'
            if state['active']:
                # The runner records the configuration it started with once the game is up.
                from configuration import digest, read_all
                configs = read_all()
                write_json(S.STATE / 'applied.json', {k: digest(v) for k,v in configs.items()})
                write_json(S.STATE / 'applied-values.json', configs)
            if action == 'backup': return 'Verified backup: ' + O.backup('manual')
            return {'start': 'Server started.', 'stop': 'Server stopped; saved configuration preserved.', 'restart': 'Server restarted.',
                    'restore': 'Restore completed.', 'update': 'Update completed.'}[action]

    with patch.object(O, 'systemctl', side_effect=systemctl), patch.object(O, 'console', side_effect=console), \
         patch.object(O, 'command', side_effect=command), patch.object(O, 'execute', side_effect=execute):
        yield state

if __name__ == '__main__':
    # Local preview: python test_fixture.py [port] → http://127.0.0.1:8099 (user preview / preview-password)
    import sys
    from waitress import serve
    from werkzeug.security import generate_password_hash
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    with tempfile.TemporaryDirectory(prefix='pz-preview-') as temp:
        build(Path(temp))
        write_json(S.STATE / 'account.json', {'username': 'preview', 'hash': generate_password_hash('preview-password'), 'generation': 'preview'})
        with fake_services(operation_delay=1.5):
            from app import create_app
            print(f'Preview on http://127.0.0.1:{port} · preview / preview-password', flush=True)
            serve(create_app(), host='127.0.0.1', port=port, threads=8)
