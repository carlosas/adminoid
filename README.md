# adminoid — Project Zomboid administration panel

Web panel to administer a Project Zomboid (Build 42) dedicated server on Linux: settings, spawn points, mods, restarts, updates and backups.
Built for the local network: plain HTTP without TLS, so it **must not be exposed to the Internet** nor have its port forwarded on the router.

The interface is available in English (default) and Spanish.

## Requirements

- Linux with systemd (user units) and Python 3.
- Project Zomboid Build 42 dedicated server installed with SteamCMD.

## Installation

```bash
cd ~/zomboid-panel
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # adjust paths, server name and IP
chmod 600 .env
.venv/bin/python app.py create-admin
.venv/bin/python deploy.py
```

`create-admin` asks for the **panel** username and password, independent from the game administrator. The same command changes the credentials and invalidates previous sessions. There is no default password.

`deploy.py` installs the units in `units/` into `~/.config/systemd/user/` (keeping the previous ones in `state/previous-units`), creates a backup with the server stopped and enables the game, the panel and the daily backup. The units assume the project lives in `~/zomboid-panel`.

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

- Settings: search by key, label or description; edit and review the differences before saving. Saving does not restart the server. World identifiers are protected.
- The INI and Sandbox files are the source of truth. Unknown fields are preserved. Lua with custom code is read-only and never executed.
- Pending changes: saved files are compared with the ones read at the last start. The comparison does not detect changes made live from the game.
- Spawn: the forms at the top add or remove regions and points per profession. Changing the structure regenerates the file; the original is kept in config-history. Do not enable regions that point to missing maps.
- Mods and maps: one identifier per line; line order defines the saved order. WorkshopItems holds numeric IDs; Mods holds internal IDs. They are not interchangeable. Dependencies are not resolved; check the installed metadata and the author's instructions. The server handles Workshop downloads at startup.
- Restart, update, backup and restore show connected players and require confirmation. If the state cannot be checked, it is shown as unknown. A shutdown is never forced when the local console is missing.
- Update uses SteamCMD, requires confirmed success and checks Build 42 before opening the world. The packaged version is read from Core.class; if its format changes, the update is explicitly blocked and the check must be adapted. JVM limit: 5 GiB.
- Backups stop the game to get consistent files. The automatic backup is attempted every 15 minutes from 04:00 UTC until one succeeds that day, without interrupting players. Seven automatic backups are kept. Manual, pre-update and pre-restore backups are not rotated: keep an eye on disk space.
- Restore validates content, space and paths and creates a rescue backup before replacing data. Do not put untrusted files in the backup directory.
- Zombie, fuel and loot changes may only affect new content; the panel does not regenerate zones or reset the world.

## Services

```bash
systemctl --user status zomboid-panel.service zomboid.service zomboid-backup.timer
journalctl --user-unit zomboid-panel.service -n 50 --no-pager
```

The game service runs through runner.py: console over a private Unix socket, limited to players/save/quit. systemd sends SIGTERM to the wrapper, which requests quit and waits; TimeoutStopSec=360 is systemd's last resort, not a guarantee that the world is saved if the process hangs.

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

Tests use temporary directories; they never restore the real world. Browser tests require requirements-test.txt and Playwright's Chromium (plus its system libraries: `playwright install --with-deps chromium`).

## Deployment recovery

Previous units are kept in state/previous-units. The migration creates a full manual backup with the server stopped. Do not restore those units while the game is running: stop it gracefully first.
