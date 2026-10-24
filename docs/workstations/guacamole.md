# Guacamole browser desktop and console

`--guacamole` installs an embedded browser client for the configured account's
shared XRDP desktop. This is optional on non-root Debian server and workstation
profiles, including `agent_vm`, on Debian 13 (Trixie), amd64. It requires
an HTTPS web panel. Proxmox hosts, CachyOS, WSL and custom-step profiles are
unsupported.

## Install and open

```bash
basaltw setup agent_vm 192.168.1.50 agent \
  --web-panel --ssl --web-panel-password "$PANEL_PASSWORD" \
  --guacamole --password "$RDP_PASSWORD"
```

Provide both values from a protected secret source. Variables keep literal
passwords out of shell history, but expanded arguments may be visible to local
process inspectors; run setup on a trusted controller and do not store the
values in scripts. An existing account needs a valid Unix password for browser
RDP login. Guacamole does not reuse the panel's Basic Auth password and cannot
use local Unix peer authentication for its RDP connection. `--harden-user`,
which locks password login, is rejected.

For an existing HTTPS panel, saved panel port and TLS settings are retained:

```bash
basaltw patch 192.168.1.50 agent --guacamole
```

| Flag | Effect |
| --- | --- |
| `--guacamole` | Install the browser desktop, console and shared desktop prerequisites; requires `--web-panel` and `--ssl` on a new setup |
| `--no-guacamole` | Remove browser desktop/console access and their managed units; retain the desktop, applications and pinned release |
| `--no-web-panel` | Remove the panel and Guacamole access together |

Without `--rdp`, XRDP remains bound to loopback and no RDP firewall ingress is
added. Explicit `--rdp` retains its normal native-client access policy; the
listener must accept local IPv4 connections. Choosing a different environment
with `--desktop` follows the existing shared-desktop setup and logout policy.
Adding desktop support can require an existing managed desktop to log out;
save work before setup. See [XRDP](xrdp.md).

Open **Desktop** in the sidebar, or **Desktop (Guacamole)** under the panel's
**Services**. Desktop and Console retain the panel's sidebar links for installed
features. JavaScript is required on these access pages.
Enter the Unix password and choose **Connect to desktop**. Connecting explicitly
starts or reuses the managed desktop. Merely opening the page does not start it.
The Unix password is used in memory for the RDP handshake and is not saved,
returned in responses, or written to logs.

**Check desktop status** reads the supervisor's state, geometry and saved agent permission
state without starting a desktop or creating a browser session. Opening the
page itself remains read-only. If the supervisor is stopped, saved permission
is unavailable to the confined gateway until you connect; the owner's native
control window and CLI can still inspect it.

## Mobile display and shell access

The desktop toolbar wraps on narrow screens and remains available in
fullscreen. Expand **Display & touch settings** to choose **Fit screen** or
100%, 150% or 200% local scaling. Fit uses the available width and, in
fullscreen, the available height. Zoom changes only the browser view, not the
shared desktop's resolution. **Pan display** lets you scroll a zoomed display
without sending clicks or scroll-wheel input to the desktop.

**Trackpad** is the default touch mode: move with one finger, tap to click,
tap with two fingers for a right click, and swipe with two fingers to scroll.
**Direct touch** clicks at the tapped location. After taking control, use
**Keyboard** to focus a text field and open a phone's keyboard. The accompanying
toolbar supplies Ctrl, Alt, Tab, Esc, Backspace, Enter and arrow keys. Ctrl and
Alt toggle until released; leaving the desktop controls, switching away from
the browser, observing or disconnecting releases held modifiers. The text field
supports Unicode and input composition. Fullscreen support depends on the
browser; rotation and zoom controls remain available when it is unsupported.

In both Desktop and Console, press **Ctrl + Alt + Shift** together to release
held keys and move focus from the remote display or text field to **Keyboard**.
Then use Tab or Shift+Tab to navigate the page controls. Without this shortcut,
Tab and Escape continue to reach the remote application.

**Console** in the sidebar, or **Open browser console** under Desktop's
**Command line access (SSH)**, opens an embedded Guacamole SSH terminal.
Enter the Unix account password and select **Connect to console**. Opening the
page is read-only; connecting starts a separate shell without starting,
connecting to, pausing or logging out the desktop. One console and one desktop
connection may coexist. The console has the account's normal shell, home,
network and sudo permissions; it is not the restricted gateway service's shell.
Agent tasks continue running independently.

The console resizes with the window, rotation and fullscreen view, including
when opening or closing its mobile keyboard controls. Choose
**Text size** before connecting. **Keyboard** exposes phone text input and
Ctrl, Alt, Tab, Esc and arrow keys; two-finger swipes scroll the terminal.
Moving the pointer outside the terminal or switching focus keeps the shell
open. Leaving the focused controls releases held keys. Pointer events are
clamped to the terminal bounds, including mouse-button releases and scaled
touch input.
Sessions end after one hour or ten minutes without keyboard/pointer input.
Polling, resizing and terminal output do not renew inactivity. Disconnecting,
closing the tab or expiring the session closes its shell. Use `tmux` or `screen`
when work should survive disconnects. Clipboard, SFTP, file transfer, recording
and arbitrary destination/command selection are disabled.

This first console integration ships with `--guacamole` and its shared desktop
prerequisites. It does not require the desktop to be running, but is not a
separate headless-only installation option.

The Desktop page also provides a copyable command for an existing SSH
client. It needs no desktop connection and uses the panel hostname with SSH's
default port 22; adjust the destination or add `-p PORT` before `--` when SSH
uses another endpoint. SSH retains its existing authentication policy and
requires your SSH credentials. The embedded console instead uses its own
private Unix login endpoint, described below.

## Observation and human control

New browser desktop connections are view-only. The gateway enforces this even if a
client modifies the JavaScript or sends keyboard/pointer instructions directly.

- **Take control** saves a human override, then enables browser keyboard and
  pointer input. On agent workstations, choose 15 minutes (the default), 5 minutes,
  1 hour, or **Until I enable agents**. The button changes to **You have control**
  while active. Click the display to focus keyboard input.
- **Observe only** disables browser input and keeps the saved permission.
- **Enable agents** ends the override or disabled state and allows authorized
  agent actions. **Disable agents** blocks them until an explicit human change.
  These choices appear on desktops with agent features. Ordinary desktops show
  **Release control** and use indefinite takeover.
- **Disconnect** revokes the current browser connection,
  scoped to this page's attachment. A disconnected page can revoke the active
  connection from another tab. A stale page cannot revoke a replacement
  connection with its old attachment. Disconnect releases this browser's control
  and keeps saved agent permission and any override deadline. The desktop and
  applications keep running. Closing the tab or losing the stream performs the
  same cleanup.
- **Log out desktop** requires you to take control first, asks for confirmation, and requests a
  normal desktop logout. This ends the desktop and its applications. The browser
  remains usable for unsaved-work dialogs; canceling logout retains control until
  you observe, enable agents, or disconnect. Successful logout clears the browser session
  automatically, so the next connection can start a new desktop directly.
- **Fullscreen** changes the browser view. The client scales the display locally;
  it does not request live XRDP resolution changes.

One browser desktop connection is supported per VM account, alongside one console.
XRDP also supports only one
human RDP connection in this configuration; a native RDP client can take over
the same display and disconnect the browser. Desktop restart or geometry changes
require reconnecting. Permission is cooperative same-account coordination;
see [persistent desktop control](desktop-control.md) for approval boundaries,
migration and storage. Keep sensitive administrator sign-ins and privilege approvals on your
own device, outside the shared desktop.

The gateway binds permission actions to the observed desktop generation and geometry;
the desktop supervisor checks these before changing state. Control is available
only after the native tunnel supplies its first display sync. Canceling a login
revokes its pending handshake immediately. The page serializes control requests
and refreshes authoritative status every three seconds. The gateway independently
checks permission, its revision and browser ownership before accepting keyboard
or pointer input. A human permission change or override expiry invalidates
browser input. Browser ownership expires after 45 seconds without a gateway
heartbeat. Heartbeats and cleanup keep the saved permission and its original
deadline. Timed overrides enable agents at the chosen deadline, including after
a disconnect or restart; indefinite overrides remain until a human changes them.
Cleanup is conditional on the original desktop generation and ownership token.

Clipboard, drive/file transfer, printing, audio, SFTP and recording are disabled.
Browser instructions cannot select hosts, ports, protocols, other users, existing
Guacamole connection IDs, or enable these channels.

## Access and lifetime

Nginx authenticates both HTTP requests and the WebSocket upgrade using the
panel's existing Basic Auth. The backend requires the proxy's authenticated
identity, HTTPS transport, a configured host, matching Origin on actions and
WebSocket upgrades, and CSRF on state-changing requests. Desktop responses are
not cached and cannot be framed by another site. JavaScript is served locally;
desktop pages have their own CSP without relaxing other panel pages.

The connect action creates one random attachment capability, valid for 30
seconds and claimable once. The Unix password is discarded after the native
handshake or when the unused attachment expires. The capability appears only
in the initial WebSocket URL; access logs retain only the existing
privacy-preserving authentication-failure records, and no Unix password is
included in a URL. Refreshing releases this page's connection; sign into XRDP
again to reconnect. An active connection in another tab must still be disconnected
explicitly. Logged-out, replaced and expired sessions do not block a new login.

Connections expire after one hour, or ten minutes without accepted control
input or a control action. Network pings and display updates do not extend
user inactivity. An observation-only connection therefore expires after ten
minutes unless a control action occurs. Expiry leaves desktop applications running
and releases browser input while keeping saved agent permission.

The page displays the remaining session and inactivity limits. Select
**Observe only** to deliberately continue observing. Status polling does not
renew inactivity or extend the one-hour limit, and an expired session cannot
be revived with a late control action.

Changing the panel password prevents new connections but does not revoke an
already upgraded WebSocket. Use **Disconnect**, or restart
`basaltwater-guacamole.service`, to revoke it immediately. Removing Guacamole
stops active tunnels. No passwords or screen contents are retained in audit
logs. Screen contents are visible to anyone with the panel credential and a
valid Unix account login.

## Installation and service isolation

Setup downloads a versioned `basaltwater-guacamole` Debian package from
[the dedicated build repository](https://github.com/bluehexagons/basaltwater-guacamole).
That repository pins Apache Guacamole 1.6.0 source archives and builds an RDP/SSH
`guacd` with its JavaScript client in CI. It maintains build configuration rather
than an Apache source fork. amd64 packages are built natively and
checked in fresh Debian containers without development packages.
Arm64 is unsupported and no longer built; older release assets are historical.

Review the amd64 package and its SHA-256 before updating the version and pin in
`lib/guacamole_config.py`.

The installer verifies the package SHA-256 pinned in Basaltwater's source and
checks its name, version and architecture before asking APT to install it.
APT installs the package's declared runtime libraries; Python aiohttp also comes
from Debian APT. Compilers and development headers stay in build CI. No Java,
Tomcat, database, Docker daemon or VNC server is installed. The gateway uses
Debian's FreeRDP 3 packages; upstream labels FreeRDP 3 support experimental.

The current binary release lives at `/opt/basaltwater-guacamole/1.6.0-bw2`.
Unchanged reruns validate and reuse the installed package without downloading
or rebuilding Guacamole. Unsupported distributions or architectures fail
explicitly; there is no automatic source-build fallback. Binary release updates
require a reviewed Basaltwater version/hash change; there is no separate
automatic upstream release updater. Debian runtime libraries continue under
the normal system update policy. An incomplete, unmanaged, writable or unsafe
linked release stops setup, including on reruns.

The gateway and RDP relay run as the configured non-root account with systemd hardening.
`basaltwater-guacamole.service` runs `guacd` and the browser tunnel in a private
network namespace. The namespace contains only local gateway, RDP and SSH relay
ports. It has no route to the host's network or arbitrary remote machines.
`basaltwater-guacamole-rdp.service` provides an account-private Unix socket
which forwards only to the host's `127.0.0.1:3389` XRDP listener. Nginx reaches
the HTTP application through a separate Unix socket restricted to the account
and its web-service group. No raw Guacamole port is exposed on the host.

The private namespace also relays port 2222 to
`/run/basaltwater-guacamole-ssh/ssh.sock`. The
`basaltwater-guacamole-ssh.socket` unit accepts at most two connections on that
account-only Unix socket; it opens no host TCP listener. Each connection starts
OpenSSH in inetd mode through `basaltwater-guacamole-ssh@.service`.
OpenSSH performs the normal PAM account/password/session checks, drops to the
configured user and creates its PTY. This login service begins as root, like
the public SSH daemon, because a normal Unix login must establish the user's
groups and permissions. It does not inherit the gateway's read-only home or
privilege restrictions. It permits only the configured non-root account,
requires a nonempty Unix password, and disables SSH forwarding and user RCs.
Its one-hour service limit also bounds a shell if the gateway fails.

Completed login instances are unloaded even when OpenSSH exits unsuccessfully,
as it does when a client abandons login. This prevents per-connection failures
from accumulating in `systemctl --failed`. Their exit results remain in the
system journal; the persistent socket and gateway retain normal failed-state
reporting.

The endpoint uses `/etc/basaltwater/guacamole-sshd.conf`, independent of
`/etc/ssh/sshd_config`; public key-only SSH policy is unchanged. Setup creates
a dedicated root-private Ed25519 host key under
`/etc/basaltwater/guacamole-ssh/` and pins its public key in the gateway
configuration. Passwords stay in memory for the SSH handshake. The gateway
cannot select another host, port, account, private key or startup command.
Removing Guacamole stops the private socket and active login instances and
removes its configuration; the host key is retained for a later reinstall.
Orderly service stops revoke sessions and release held browser keys/buttons
before terminating native children. Forced termination cannot guarantee input
release, but the supervisor expires browser ownership. Saved agent permission
survives either shutdown path. Disconnect before maintenance when possible.

The gateway and RDP relay report readiness to systemd only after their listeners are
established. The gateway additionally waits for `guacd` to listen. Setup waits
for this notification inside the existing grouped unit transaction, so startup
failure restores prior service definitions and activation state. On activation
failure, setup also restores the prior non-secret gateway configuration and
reloads an active restored gateway. Interrupted or failed rollback still needs
the normal [transaction recovery](../operations/transaction-recovery.md) workflow.

An upgrade can temporarily leave the new runtime rejecting the previous gateway
configuration. Systemd reports that failed service as `activating` with substate
`auto-restart` while waiting to retry. Setup accepts this state and installs the
new configuration and units; it does not require manually stopping the gateway.
Actual startup, shutdown or reload transitions still block a transaction, and
the error includes the observed load, active and substate values.

The gateway pins the configured XRDP certificate's SHA-256 fingerprint.
Certificate verification is not disabled. After changing the XRDP certificate,
rerun the saved setup or patch with `--guacamole` to update the pin.

## Diagnose and remove

```bash
sudo systemctl status basaltwater-guacamole.service basaltwater-guacamole-rdp.service
sudo systemctl status basaltwater-guacamole-ssh.socket
sudo journalctl -u basaltwater-guacamole.service -n 50 --no-pager
sudo nginx -t
basaltw desktop doctor
```

Run desktop commands as the desktop owner. A login failure can mean an incorrect
Unix password, a changed XRDP certificate, or an unhealthy shared desktop.
Console login failures can also mean a locked/expired Unix account, PAM policy,
or an unhealthy private SSH socket. Inspect the socket and login units rather
than enabling password authentication on the public SSH listener.
Missing/private sockets, failed namespace setup, and missing native libraries
are service readiness failures. Native protocol logs are suppressed to keep
credentials and desktop contents private; service errors provide bounded generic
readiness messages. Existing XRDP diagnostics remain available.

For a shareable JSON report, run
`python3 scripts/collect_guacamole_diagnostics.py` from a Basaltwater checkout as
the desktop owner. Run `sudo -v` first to allow the collector's noninteractive
log reads. Redirect the output to a private file (for example, set `umask 077`
before redirecting to `guacamole-diagnostics.json`). The collector records
bounded gateway, private SSH and XRDP logs, desktop state, package versions
(including the native SSH backend) and desktop/console source hashes.
If run as root, it reads the managed desktop owner from root-owned configuration
and queries status as that account. When the orchestration CLI is absent, it
uses the installed desktop runtime directly.
It does not restart services or collect screenshots, password files, environment
dumps or secret configuration. Review the output before sharing; credential
redaction is best effort, and log text may contain private account or host names.

```bash
basaltw patch 192.168.1.50 agent --no-guacamole
```

Removal preserves the native package and desktop; it does not remove runtime
libraries or reset Unix passwords. Stopping browser access releases its control;
saved agent permission is preserved. A successful rerun
reconciles services and browser ingress. Setup refuses unmanaged service files.
It checks managed configuration before changing browser ingress during removal.

Validation covers configuration round trips, pinned binary integrity and package
selection, native builds in release CI, origin/identity/CSRF checks, attachment
replay and expiry,
protocol framing, view-only enforcement and cooperative takeover. Browser
rendering can be tested with a synthetic desktop; that alone does not establish
real XRDP reconnect, keyboard-layout, GPU-editor or native-client coexistence
behavior. Qualify those paths on a disposable desktop VM before relying on them
for interactive work.


## Qualification status

A clean Debian 13 amd64 container has passed package build and installation,
private RDP/SSH relay, XRDP framebuffer transport, certificate pin rejection,
Nginx HTTPS/Basic Auth/WebSocket access, cooperative desktop control, and
reconnect to the same desktop. A systemd-booted Debian container has also passed
private SSH socket activation, PAM login, shell permissions, bounded lifetime,
and grouped removal.

Synthetic browser checks cover view-only behavior, takeover, reconnect,
external permission changes, Unicode input, touch/trackpad controls, zoom, panning,
terminal input and desktop/mobile layouts. These checks do not qualify a live
XRDP desktop, a native phone keyboard or fullscreen transition, keyboard-layout
compatibility, native RDP-client coexistence, GPU applications, or every
supported desktop environment. Keep those as live qualification requirements
before relying on the affected workflow.
