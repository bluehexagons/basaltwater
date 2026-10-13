# Guacamole browser desktop

`--guacamole` installs an embedded browser client for the configured account's
shared XRDP desktop. This is optional on non-root Debian server and workstation
profiles, including `agent_vm`. It requires an HTTPS web panel. Proxmox hosts,
CachyOS, WSL and custom-step profiles are unsupported.

## Install and open

```bash
basaltw setup agent_vm 192.168.1.50 agent \
  --web-panel --ssl --web-panel-password 'panel-password' \
  --guacamole --password 'unix-account-password'
```

Use your normal credential provisioning workflow for the Unix password; neither
password should be copied into scripts or shell history. An existing account
needs a valid Unix password for the browser RDP login. Guacamole does not reuse
the panel's Basic Auth password and cannot use local Unix peer authentication
for its RDP connection. `--harden-user`, which locks password login, is rejected.

For an existing HTTPS panel, saved panel port and TLS settings are retained:

```bash
basaltw patch 192.168.1.50 agent --guacamole
```

| Flag | Effect |
| --- | --- |
| `--guacamole` | Install the browser desktop and shared desktop prerequisites; requires `--web-panel` and `--ssl` on a new setup |
| `--no-guacamole` | Remove browser access and its two services; retain the desktop, applications and pinned release |
| `--no-web-panel` | Remove the panel and Guacamole access together |

Without `--rdp`, XRDP remains bound to loopback and no RDP firewall ingress is
added. Explicit `--rdp` retains its normal native-client access policy; the
listener must accept local IPv4 connections. Choosing a different environment
with `--desktop` follows the existing shared-desktop setup and logout policy.
Adding desktop support can require an existing managed desktop to log out;
save work before setup. See [XRDP](XRDP.md).

Open **Desktop (Guacamole)** under the panel's **Services**. JavaScript is
required on this page. Other panel pages retain their existing behavior.
Enter the Unix password and choose **Connect to desktop**. Connecting explicitly
starts or reuses the managed desktop. Merely opening the page does not start it.
The Unix password is used in memory for the RDP handshake and is not saved,
returned in responses, or written to logs.

## Observation and human control

New browser connections are view-only. The gateway enforces this even if a
client modifies the JavaScript or sends keyboard/pointer instructions directly.

- **Take control** pauses cooperative agent desktop actions, then enables
  browser keyboard and pointer input. Click the display to focus keyboard input.
- **Observe only** disables browser input and retains the agent's pause state.
- **Resume agent** disables browser input and explicitly resumes agent actions.
- **Disconnect browser session** revokes the current browser connection,
  scoped to this page's attachment. A disconnected page can revoke the active
  connection from another tab. A stale page cannot revoke a replacement
  connection with its old attachment. Disconnect does not resume the agent,
  log out the desktop, or close applications.
- **Fullscreen** changes the browser view. The client scales the display locally;
  it does not request live XRDP resolution changes.

One browser connection is supported per VM account. XRDP also supports only one
human RDP connection in this configuration; a native RDP client can take over
the same display and disconnect the browser. Desktop restart or geometry changes
require reconnecting. Pause is cooperative same-account coordination, not a
sandbox. Keep sensitive administrator sign-ins and privilege approvals on your
own device, outside the shared desktop.

The gateway binds pause/resume to the observed desktop generation and geometry;
the desktop supervisor checks these before changing state. Control is available
only after the native tunnel supplies its first display sync. Canceling a login
revokes its pending handshake immediately. The page serializes control requests
and refreshes authoritative status every three seconds, so an external resume
also clears the browser's control indicator. The gateway independently checks
pause state before accepting keyboard or pointer input.

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
included in a URL. Refreshing requires disconnecting an existing
browser session and signing into XRDP again.

Connections expire after one hour, or ten minutes without accepted control
input or a control action. Network pings and display updates do not extend
user inactivity. An observation-only connection therefore expires after ten
minutes unless a control action occurs. Expiry leaves desktop applications and
the agent pause state intact.

The page displays the remaining session and inactivity limits. Select
**Observe only** to deliberately continue observing. Status polling does not
renew inactivity or extend the one-hour limit, and an expired session cannot
be revived with a late control action.

Changing the panel password prevents new connections but does not revoke an
already upgraded WebSocket. Use **Disconnect browser session**, or restart
`basaltwater-guacamole.service`, to revoke it immediately. Removing Guacamole
stops active tunnels. No passwords or screen contents are retained in audit
logs. Screen contents are visible to anyone with the panel credential and a
valid desktop login.

## Installation and service isolation

Setup downloads the Apache Guacamole 1.6.0 server and client source archives,
checks the SHA-256 values pinned in Basaltwater's source, and builds a native
RDP-only `guacd`. JavaScript modules are assembled directly from the verified
client source. No Java, Tomcat, database, Docker daemon or VNC server is installed.
Build dependencies and Python aiohttp come from Debian APT. The gateway uses
Debian's FreeRDP 3 packages; upstream labels FreeRDP 3 support experimental.

The completed release is published atomically at
`/opt/basaltwater-guacamole/1.6.0`. Unchanged reruns reuse it. Guacamole release
updates require a reviewed Basaltwater version/hash change; there is no separate
automatic upstream release updater. Debian packages continue under the normal
system update policy. An incomplete, unmanaged, writable or unsafe linked
release stops setup, including on reruns.

Both services run as the configured non-root account with systemd hardening.
`basaltwater-guacamole.service` runs `guacd` and the browser tunnel in a private
network namespace. The namespace contains only local gateway and RDP relay
ports. It has no route to the host's network or arbitrary remote machines.
`basaltwater-guacamole-rdp.service` provides an account-private Unix socket
which forwards only to the host's `127.0.0.1:3389` XRDP listener. Nginx reaches
the HTTP application through a separate Unix socket restricted to the account
and its web-service group. No raw Guacamole port is exposed on the host.
Orderly service stops revoke sessions and release held browser keys/buttons
before terminating native children. Forced termination cannot guarantee input
release; disconnect or switch to observation before maintenance when possible.

Both services report readiness to systemd only after their listeners are
established. The gateway additionally waits for `guacd` to listen. Setup waits
for this notification inside the existing grouped unit transaction, so startup
failure restores prior service definitions and activation state. On activation
failure, setup also restores the prior non-secret gateway configuration and
reloads an active restored gateway. Interrupted or failed rollback still needs
the normal [transaction recovery](TRANSACTION_RECOVERY.md) workflow.

The gateway pins the configured XRDP certificate's SHA-256 fingerprint.
Certificate verification is not disabled. After changing the XRDP certificate,
rerun the saved setup or patch with `--guacamole` to update the pin.

## Diagnose and remove

```bash
sudo systemctl status basaltwater-guacamole.service basaltwater-guacamole-rdp.service
sudo journalctl -u basaltwater-guacamole.service -n 50 --no-pager
sudo nginx -t
basaltw desktop doctor
```

Run desktop commands as the desktop owner. A login failure can mean an incorrect
Unix password, a changed XRDP certificate, or an unhealthy shared desktop.
Missing/private sockets, failed namespace setup, and missing native libraries
are service readiness failures. Native protocol logs are suppressed to keep
credentials and desktop contents private; service errors provide bounded generic
readiness messages. Existing XRDP diagnostics remain available.

```bash
basaltw patch 192.168.1.50 agent --no-guacamole
```

Removal preserves the native release and desktop; it does not remove build
packages, reset Unix passwords, or resume paused agents. A successful rerun
reconciles services and browser ingress. Setup refuses unmanaged service files.
It checks managed configuration before changing browser ingress during removal.

Validation covers configuration round trips, source integrity and extraction,
native builds, origin/identity/CSRF checks, attachment replay and expiry,
protocol framing, view-only enforcement and cooperative takeover. Browser
rendering can be tested with a synthetic desktop; that alone does not establish
real XRDP reconnect, keyboard-layout, GPU-editor or native-client coexistence
behavior. Qualify those paths on a disposable desktop VM before relying on them
for interactive work.

On 2026-10-13, a disposable Debian Trixie container passed the native build,
Guacamole-to-XRDP framebuffer transport through the private namespace and Unix
relay, cooperative takeover/observe/resume, disconnect, and reconnect to the
same managed i3 desktop. A mismatched certificate pin was rejected. Real Nginx
HTTPS, Basic Auth, WebSocket upgrades and privacy-preserving authentication
failure logging also passed. The tested packages were xrdp 0.10.6.1-3,
xorgxrdp 0.10.5-2, FreeRDP 3.15.0 with Debian security updates, and i3 4.24.
A collaborative browser preview separately passed
synthetic desktop rendering and controls at desktop and narrow viewports.
These checks do not qualify every desktop environment, keyboard layout,
GPU editor, or coexistence with a native RDP client.

The follow-up audit on the same date added regression checks for scoped
handoffs, pending-login cancellation, stale disconnects, expiry enforcement,
external resume, graceful shutdown, release permissions and links, bounded
source handling, readiness notification, and configuration rollback. A
synthetic collaborative preview passed failed-login recovery, reconnect,
take/observe/resume/disconnect, external-resume feedback, remaining-time
display, and 1280 px and 390 px viewport layouts. Systemd startup/rollback and
signal handling have isolated mocked tests; the complete hardened systemd
profile still needs qualification on a disposable VM.
