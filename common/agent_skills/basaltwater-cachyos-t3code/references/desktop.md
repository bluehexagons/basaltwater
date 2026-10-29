# T3 desktop setup and updates

`basaltw setup agent_cachyos localhost --t3code-desktop` installs missing
`t3code-bin` through current CachyOS's default Shelly CLI
(`shelly install aur t3code-bin`). For installed packages, setup/refresh checks
`shelly list-updates aur --json` and runs `shelly update aur t3code-bin` only
when an update is available; it does not rebuild current packages. Only when
Shelly is absent does setup try paru, then yay. Preserve package-review prompts
and Shelly's configured AUR policy; do not switch helpers to bypass a failure.
Every desktop setup requires a helper. Failed queries, failed updates, and
updates leaving the installed version unchanged stop setup before startup
changes. `cachy-update` may skip AUR checks with only Shelly installed; use
`shelly list-updates aur` for routine checks. Install selected skills before
refreshing T3. Only after all steps, saved state and notifications finish does
desktop setup/refresh queue a detached user worker. This keeps local and remote
T3 terminals available for package-review and sudo prompts until completion.
The worker disables the managed web service, then gracefully sends SIGTERM to a running same-user `t3code-bin`,
waits for exit, and relaunches the packaged app with its original environment
and arguments. It never force-kills. If the app was stopped, the worker starts
it using the systemd user manager's graphical environment when KDE's graphical
session is active; otherwise its journal explicitly defers launch until KDE login.
KDE login autostart remains enabled. The worker reloads provider configuration
and skills while preserving `~/.t3` data and credentials. Queue acceptance does
not prove readiness; on failure inspect the printed unit with
`journalctl --user -u UNIT`, then verify an authenticated provider turn and
terminal. Saved threads continue on the next message; automatic continuation
of interrupted turns follows T3's existing **Continue threads after server
update** setting, and queued turns still need user Resume.
The completion worker disables the Basaltwater web service;
an inactive service is expected in this mode. Setup retains all application data.
The `desktop-mode` marker under `~/.local/share/basaltwater/cachyos-t3` records
the selection. Do not launch another backend to repair a working desktop.

Desktop setup enables KDE login startup through the managed
`~/.config/autostart/basaltwater-cachyos-t3.desktop` entry (or absolute
`$XDG_CONFIG_HOME`). It uses `/usr/bin/t3code` and skips an absent executable.
Normal quit leaves it stopped for this session. KDE's Autostart settings can
disable it; desktop setup/refresh re-enables it. Custom entries and symlinks at
the managed destination are refused before changes. Doctor checks startup
configuration and executable presence without launching Electron; verify a
provider thread and terminal after setup's worker runs or at a later login.

Use the app's provider settings, local provider login, and absolute provider
binary paths if KDE's PATH differs from the shell. Verify a thread and terminal
in the app. Use desktop Settings → Connections for pairing or T3 Connect when
supported by that version. The [web CLI commands](web-service.md) configure a
separate environment from the desktop's default `~/.t3` data.
