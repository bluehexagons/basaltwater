# Run T3 Code on CachyOS

[Documentation index](cachyos.md)

## T3 Code desktop

The desktop installer above selects the
[upstream-listed Arch package](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md).
If you installed only the launcher, apply your first desktop selection with:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --codex
```

The flag is currently supported only by `agent_cachyos`, not Debian profiles or
generic Arch installations. If `t3code-bin` is missing, setup runs
`shelly install aur t3code-bin` as the desktop user, with interactive package
review, build, and sudo prompts. Shelly is included in current CachyOS; installing
`paru` or `yay` first is unnecessary. Only when Shelly is absent does setup look
for `paru`, then `yay`. These are optional alternatives, not an older-CachyOS
support target. If all three are absent, preflight explains how to restore Shelly
with `sudo pacman -S --needed shelly` on an already updated system.
Preflight also verifies that the selected helper executable belongs to an
installed pacman package. Remove or rename an unowned executable shadowing
Shelly, paru, or yay in your PATH before retrying.

For an installed package, setup checks `shelly list-updates aur --json` and runs
`shelly update aur t3code-bin` only when T3 has a pending update. Paru/yay use
their native AUR query and install commands instead. Every desktop setup needs
an installed helper, including when T3 is already installed. Setup preserves
the helper's configured source, ignore policy, and review prompts; it does not
rebuild current packages or upgrade unrelated AUR packages.

A failed query, failed installation, or update that leaves the package version
unchanged stops setup before disabling the working web service; setup does not
retry through another helper. Setup checks package metadata and executable
ownership without launching Electron. Quit/reopen an already-running T3 when
updating its package directly. Setup/refresh instead queues a graceful reload
after all steps, saved state and notifications finish. `cachy-update` may skip AUR
checks when only Shelly is installed; use `shelly list-updates aur` for routine
checks. See [desktop updates](cachyos-maintenance.md#t3-desktop-updates) and
[Shelly's CLI reference](https://www.seafoam-labs.org/shelly-alpm/docs/cli-reference/).

Before invoking Shelly, setup creates its missing AUR cache directories as your
desktop user. This avoids a Shelly 3.1.6 first-install path that creates the cache
after elevation, then runs Git as the unprivileged user. Preflight reports an
existing inaccessible or incorrectly owned cache before package installation.
Both `~/.cache/Shelly` and an absolute `$XDG_CACHE_HOME/Shelly` are checked,
because privilege elevation may discard the XDG override. Existing cache contents
and permissions are preserved; setup does not recursively repair ownership.

Basaltwater installs the selected provider CLIs, workspace, and T3 agent skill.
It preserves T3 settings, history, login credentials, and desktop launchers.
Desktop setup protects the default `~/.t3` state directories with private
permissions, retaining their contents. Unsafe symlinks or ownership stop setup.
Setup prints provider executable paths; if a KDE-launched T3 cannot find a
provider, use its **Binary path** setting. Verify a thread and terminal in the
app; a terminal CLI check does not establish GUI provider discovery. Desktop
mode does not require the separate Node/npm runtime used by the web service.

Desktop setup enables T3 Code at KDE login using a managed
[freedesktop autostart entry](https://specifications.freedesktop.org/autostart/latest/)
at `~/.config/autostart/basaltwater-cachyos-t3.desktop` (or under an absolute
`$XDG_CONFIG_HOME`). It launches the native `/usr/bin/t3code` only after login
and skips startup if that executable has been uninstalled. After all setup steps,
saved state and notifications finish, an independent worker gracefully reloads a
running app or starts a stopped app using KDE's user-manager environment. Without
an active graphical session it reports deferred launch until KDE login. A normal
quit leaves T3 stopped until the next login or explicit setup/refresh.
Manage startup in KDE's **System Settings → Autostart**; a desktop setup or
refresh re-enables the managed entry. Custom files or symlinks at its destination
are preserved and reported before package changes. Switching successfully to
web mode removes this entry; a failed web activation preserves it. See
[login-startup maintenance](cachyos-maintenance.md#t3-desktop-login-startup).

Use the app's **Settings → Connections** for desktop pairing or T3 Connect,
when supported by the installed version. The managed web-service commands below
target a separate environment and should not be used to configure the desktop.

For helper errors, see [AUR download failures](cachyos-maintenance.md#aur-download-failures).
For an established installation, see [switching modes](cachyos-maintenance.md#switch-modes-on-a-later-setup).

## T3 Code web service: host locally or on a trusted LAN

T3 is an optional, user-owned systemd service. It runs as the logged-in desktop
user with provider credentials from that account. By default it listens only on
`127.0.0.1:3773`; selecting a specific private IPv4 address enables clients on
that LAN. Public, wildcard, and IPv6 bind addresses are rejected by this
profile.
Private LAN binds must be in RFC 1918 space (`10/8`, `172.16/12`, or
`192.168/16`); reserved/documentation and link-local addresses are rejected.
`localhost` is normalized to `127.0.0.1`. A custom web port requires the web
interface flag; desktop listeners are configured in the desktop app.

### Install the local service

Add `--web-interface t3code` to the [quick-start installer](cachyos-install.md#quick-start),
or configure the default loopback service with the installed launcher:

```fish
basaltw setup agent_cachyos localhost --web-interface t3code --codex
systemctl --user status basaltwater-cachyos-t3.service
```

The unit is `${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/basaltwater-cachyos-t3.service`,
matching systemd's user-unit lookup. A custom `XDG_CONFIG_HOME` must be absolute,
without parent traversal. Setup rejects symlinked paths and config/unit directories
or unit files owned by another user. If a T3 unit remains in the default
`~/.config/systemd/user` directory after changing `XDG_CONFIG_HOME`, setup stops
before changing services. Resolve that installation using its original configuration
before retrying; setup does not migrate it automatically.

The runtime is under `~/.local/share/basaltwater/cachyos-t3/releases`. The stable
`~/.local/share/basaltwater/cachyos-t3/bin/t3` link selects the current release
for pairing and Connect commands. T3 0.1.2's npm package downloads a standalone
archive on first use. Setup confines that download to the candidate release,
checks its native terminal addon with the archive's embedded Node, then points
the release's CLI directly at that executable. The relative link survives
activation and keeps later CLI calls independent of the desktop runtime cache.
Older npm layouts retain the disposable native PTY shell check. Setup validates
the generated unit without stopping the working service. After all steps, saved
state and notifications finish, an independent worker activates the staged
runtime and checks HTTP UI reachability. Queue acceptance alone does not prove
activation or readiness; inspect the printed worker unit and journal. This
keeps local or remote T3 terminals usable through package prompts; the final
reload can disconnect the terminal after setup is complete. Startup allows up to
30 minutes for database migration and optimization, with progress every 30
seconds. Readiness requires three consecutive HTTP 200 checks from the same
systemd invocation; a restart resets the count, and a failed service stops the
wait early. A startup failure restores the previous runtime and unit, while
application data stays intact. Provider login and a real coding thread still
need verification. Service output passes through a
credential filter before systemd records it in the journal, so headless startup
tokens, pairing URLs, and QR rows are not persisted. HTTP 200 alone is not
backend health: an unknown route such as `/api/health` can return the frontend HTML.
If port 3773 is busy, rerun with another port from 1024 through 65535.
Workspace paths retain literal spaces, quotes, backslashes, and percent signs.
Older units that escaped spaces as `\x20` need the updated web setup to regenerate
their working directory; unit syntax validation alone does not prove that path exists.

### T3 Connect

T3 Connect is the cloud access option. It is separate from direct LAN pairing:
the setup installs the T3 CLI and service, while you authorize the workstation
with your T3 account once from the desktop session. Keep the default loopback
bind when using T3 Connect; the managed relay expects the server's loopback
origin. Use `connect link` instead of `connect`: the latter may offer to install
a second upstream `t3code.service`, while Basaltwater already owns this unit.
Run:

```fish
"$HOME/.local/share/basaltwater/cachyos-t3/bin/t3" connect link --base-dir "$HOME/.local/share/basaltwater/cachyos-t3/data"
systemctl --user restart basaltwater-cachyos-t3.service
"$HOME/.local/share/basaltwater/cachyos-t3/bin/t3" connect status --base-dir "$HOME/.local/share/basaltwater/cachyos-t3/data"
```

Follow the browser sign-in flow printed by `connect link`. Then sign in to the
same T3 Connect account on the other desktop, web, or mobile client and choose
this environment. Run `connect unlink` to disable cloud exposure while keeping
the login, or `connect logout` to remove the login. T3 Connect does not require
LAN firewall rules or router forwarding, but the host must remain powered on.
The profile does not automatically enable systemd lingering; if the service
must stay available after logout, enable it deliberately as the desktop user:

```fish
sudo loginctl enable-linger "$USER"
```

Use direct LAN pairing below when clients should connect to the workstation's
private address. Treat that as a separate access mode from T3 Connect unless
the installed T3 release documents support for combining the two binds.

### Pair this workstation or another device

Generate a fresh native T3 pairing link with the managed runtime:

```fish
"$HOME/.local/share/basaltwater/cachyos-t3/bin/t3" pair --base-dir "$HOME/.local/share/basaltwater/cachyos-t3/data"
```

The command prints a QR code, a `Pairing URL`, and a token. Treat the URL and
token as credentials and use the link once. This explicit pairing command
returns the secret to the local caller; avoid saving its output in shared logs.
Paste the complete URL into the T3 desktop app at
**Settings → Connections → Add environment**, or open it in a browser. The bare
`http://127.0.0.1:3773` address redirects to T3's pairing page; it is not the
pairing link itself.

To pair a browser, desktop app, or phone on another system, bind T3 to the
workstation's private LAN address. Find that address with `ip -4 addr`, then
choose that address in your first web-service setup:

```fish
basaltw setup agent_cachyos localhost --web-interface t3code --codex \
  --web-interface-host 192.168.1.50 --web-interface-port 3773
systemctl --user status basaltwater-cachyos-t3.service
"$HOME/.local/share/basaltwater/cachyos-t3/bin/t3" pair --base-dir "$HOME/.local/share/basaltwater/cachyos-t3/data"
```

Open the generated URL on the other device, or paste it into its T3 desktop
app. The URL will contain `192.168.1.50`; a loopback URL only works on the
workstation itself. Add `--lan-access` or `--access-source` to manage restricted
UFW access, and use a static or reserved address. Basaltwater does not provide
the VM pairing broker or configure a gateway.

### Optional workstation firewall

If you need LAN access, include its policy in your initial selection:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --node --python --git-lfs --lan-access --codex
```

This explicitly opts into UFW management. It installs UFW if missing, enables
its service now and at boot, enables
IPv4/IPv6 filtering, denies incoming and routed traffic by default, and allows
outgoing traffic so normal browsing, downloads, provider access, and game clients
continue working. Existing unrelated rules are retained; this is not a firewall
reset. Active firewalld/nftables services or disabled UFW IPv6 support stop
preflight with remediation instead of combining incompatible policies.

`--lan-access` trusts the single private IPv4 subnet on the default-route
interface. Combining it with `--access-source` allows both the discovered LAN
and the explicit sources. For ambiguous/VPN/multiple-network cases, use
`--no-lan-access --access-source PRIVATE_IP_OR_CIDR` to select explicit sources
without discovery. Explicit sources must be RFC1918 IPv4 addresses or subnets;
public sources, IPv6 sources, and all-network wildcards are refused. When SSH
is enabled without explicit access flags, private IPv4 LAN access is selected
automatically. Discovery runs on each setup and refresh, while the saved record
stores the resolved SSH boolean; explicit `--access-source` values persist.
Use `--no-lan-access` or `--no-access-source` to turn off automatic discovery.

Only selected T3 TCP and Sunshine streaming/pairing ports are allowed from those
sources. Sunshine's administration port 47990, legacy RDP 3389, and T3 UDP remain
blocked remotely. The default T3 port and standard Sunshine ports receive deny
guards or replace equivalent broad allow rules, including IPv6 rules. Rule
precedence is verified; an earlier overlapping unmanaged allow stops setup for
manual review. Previous custom port guards are retained.
New guards are installed before old managed allows are
removed, so a failed update can leave access closed; rerun the same selection to
recover. Existing established connections and custom UFW before-rules are not
revoked. Unrelated ports (including old development-server rules) require review.

`--no-lan-access` with no explicit sources closes these managed ports remotely.
Omitting access flags with SSH disabled leaves the firewall unchanged. When
SSH is enabled, automatic LAN access manages selected T3, Sunshine, and SSH
ports from the discovered private source. Read [SSH access](cachyos-install.md#ssh-access) for
the preflight requirement that the current SSH client source match a selected
private IPv4 source before firewall handover.
This does not change T3's bind address, start Sunshine, enable RDP, or open KDE
Connect/Steam hosting ports. Desktop T3 uses port 3773 here; nonstandard
desktop ports need manual firewall configuration. Sunshine with custom ports
also needs manual rules.
Managed T3 ports cannot overlap the protected RDP/Sunshine TCP ports.
Inspect `sudo ufw status verbose` and verify access from the intended client.

The service follows the desktop user's session. Setup validates its CLI,
native terminal dependency and generated unit; its completion worker verifies
the HTTP UI. Check that worker's result, then a real thread and terminal after pairing. For service errors,
runtime updates, and activation recovery, use the
[maintenance reference](cachyos-maintenance.md#web-service-updates-and-recovery).
