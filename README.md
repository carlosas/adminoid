# Adminoid — Project Zomboid administration panel

<img width="919" height="609" alt="Adminoid Overview" src="https://github.com/user-attachments/assets/7f4dbacb-ef0b-4e84-9d17-1332437afdb8" />

Web panel to administer a Project Zomboid (Build 42) dedicated server on Linux: settings, spawn points, mods, restarts, updates and backups.
Built for the local network: HTTP without TLS, so it **must not be exposed to the Internet** nor forward its port on the router.

The interface is available in English and Spanish.

## Requirements

- Linux with systemd (user units) and Python 3.
- Project Zomboid Build 42 dedicated server installed with SteamCMD.

## Installation

```bash
git clone https://github.com/carlosas/adminoid.git
cd ~/adminoid
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # adjust paths, server name and IP
chmod 600 .env
.venv/bin/python app.py create-admin
.venv/bin/python deploy.py
```

`create-admin` asks for the **panel** username and password, independent from the game administrator. The same command changes the credentials and invalidates previous sessions. There is no default password.

`deploy.py` installs the units in `units/` into `~/.config/systemd/user/` (keeping the previous ones in `state/previous-units`), creates a backup with the server stopped and enables the game, the panel and the daily backup. The units assume the project lives in `~/adminoid`.

## Configuration (`.env`)

| Variable | Description |
|---|---|
| `PZ_DATA_DIR` | Game data directory (`Server/`, `db/`, `Saves/`) |
| `PZ_GAME_DIR` | Dedicated server install (`start-server.sh`) |
| `PZ_STEAMCMD` | SteamCMD launcher used for updates |
| `PZ_BACKUP_DIR` | Backup destination |
| `PZ_SERVER_NAME` | Name passed to `-servername` |
| `PANEL_BIND` | IP the panel listens on (the machine's LAN IP) |
| `PANEL_PORT` | Panel port |
| `PANEL_ALLOWED_NETWORK` | Allowed client network (CIDR); empty = localhost only |

Environment variables take precedence over `.env`. If the LAN IP changes, update `PANEL_BIND` (and `PANEL_ALLOWED_NETWORK` if the subnet changes) and restart only the panel. Reserving the IP in the router's DHCP settings is recommended.

## Usage

The panel has five sections, reachable from the sidebar (bottom tabs on a phone):

- **Overview**: live server state, players online against `MaxPlayers`, memory with a chart of the last hours, last backup, and a banner when saved settings are not applied yet. Connected players can be messaged (in-game server message) or kicked. Recent activity lists starts, saves, backups, operations, kicks and messages; a live tail of the game log sits below.
- **Settings**: grouped by the game's own categories (Server, Sandbox, Other) with typed controls: option buttons or lists, switches, sliders with numeric input, steppers. Each edited setting shows its previous value and can be reset; the right-hand panel lists every unsaved change (before → after) and saves them together. Filters show only modified settings or settings pending a restart. Press `/` to search. Saving does not restart the server. World identifiers are protected.
- **Maintenance**: start, restart, stop, Build 42 update and backups. Operations show step-by-step progress (warn players, save and stop, backup, start…) and stay locked until they finish. Confirmations are in-panel dialogs that list connected players; restoring a backup requires typing the server name.
- **Mods**: explains the two IDs every mod has (the Workshop ID in `WorkshopItems` downloads it, the Mod ID in `Mods` activates it). Workshop items are listed with the Mod IDs they contain and whether they are downloaded yet. Paste a Workshop link or ID to add a mod: the panel looks it up on Steam and offers its Mod IDs and the Workshop items it requires, required ones first. Installed mods (Workshop and local) are checked against the `Mods` line: missing or misordered dependencies, mods listed but not installed, and game-version ranges. Mods can be activated, deactivated or reordered (drag or arrows); changes go through the same review as any other setting.
- **Logs**: live journal of the game, the panel or the automatic backups, with level filters (errors, warnings, connections), search with highlighting, pause, older lines on demand and download.

Notes:

- The INI and Sandbox files are the source of truth. Unknown fields are preserved. Lua with custom code is read-only and never executed.
- Workshop lookups need outbound HTTPS from the server to api.steampowered.com and steamcommunity.com; without it, Mod IDs can still be typed by hand.
- Pending changes: saved files are compared with the ones read at the last start. The comparison does not detect changes made live from the game.
- Spawn: the Spawn category adds or removes regions and points per profession. Changing the structure regenerates the file; the original is kept in config-history. Do not enable regions that point to missing maps.
- Mods and maps: WorkshopItems holds numeric IDs; Mods holds internal IDs. They are not interchangeable. The server handles Workshop downloads at startup.
- Operations that stop the server warn connected players in game and wait 10 seconds first. If the player state cannot be checked, it is shown as unknown. A shutdown is never forced when the local console is missing.
- Player messages: the game console can only broadcast, so the panel only offers a message to everyone.
- Update uses SteamCMD, requires confirmed success and checks Build 42 before opening the world. The packaged version is read from Core.class; if its format changes, the update is explicitly blocked and the check must be adapted. JVM limit: 5 GiB.
- Backups stop the game to get consistent files. The automatic backup is attempted every 15 minutes from 04:00 UTC until one succeeds that day, without interrupting players. Seven automatic backups are kept. Manual, pre-update and pre-restore backups are not rotated: keep an eye on disk space.
- Restore validates content, space and paths and creates a rescue backup before replacing data. Do not put untrusted files in the backup directory.
- Zombie, fuel and loot changes may only affect new content; the panel does not regenerate zones or reset the world.
- Memory history is sampled every minute by the panel and kept for four hours; player join times come from the game log (`fully-connected` lines) and fall back to when the panel first saw the player.

## Services

```bash
systemctl --user status adminoid.service zomboid.service zomboid-backup.timer
journalctl --user-unit adminoid.service -n 50 --no-pager
```

The game service runs through runner.py: console over a private Unix socket, limited to `players`, `save`, `quit`, `servermsg` and `kickuser` (arguments are quoted and validated; nothing else reaches the console). The runner also records server starts, unexpected exits and player join times in `state/`. A runner started before this version only accepts players/save/quit: messages and kicks work after the next game restart. systemd sends SIGTERM to the wrapper, which requests quit and waits; TimeoutStopSec=360 is systemd's last resort, not a guarantee that the world is saved if the process hangs.

The panel runs with the permissions of the user who installs it; it does not need sudo.

If the host loses power during a shutdown, state/shutdown-config.json keeps the configuration saved before quit. If the file exists, the next start restores it before launching the world.

## Security

- `state/` is private (mode 700) and must never be published: it holds the session key (`session-key`), the admin password hash (`account.json`) and copies of the game configuration, including its passwords.
- `.env` contains details of your installation; do not commit it.
- Game passwords are never displayed or returned by the API; you can only set a new one.

## Tests

```bash
.venv/bin/python -m unittest -v test_panel
.venv/bin/python test_browser.py
```

Tests use temporary directories and fake systemd, journal and console (`test_fixture.py`); they never touch the real world or services. Browser tests require requirements-test.txt and Playwright's Chromium (plus its system libraries: `playwright install --with-deps chromium`); set `PLAYWRIGHT_CHROMIUM` to use another Chromium executable.

To look at the interface without a game server, run `.venv/bin/python test_fixture.py` and open http://127.0.0.1:8099 (user `preview`, password `preview-password`). It serves fake data from a temporary directory.

## Deployment recovery

Previous units are kept in state/previous-units. The migration creates a full manual backup with the server stopped. Do not restore those units while the game is running: stop it gracefully first.
