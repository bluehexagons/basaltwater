# Guacamole browser desktop

`--guacamole` installs an embedded browser client for the configured account's
shared XRDP desktop. This is optional on non-root Debian server and workstation
profiles, including `agent_vm`, on Debian 13 (Trixie), amd64 or arm64. It requires
an HTTPS web panel. Proxmox hosts, CachyOS, WSL and custom-step profiles are
unsupported.

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

Open **Desktop** in the sidebar, or **Desktop (Guacamole)** under the panel's
**Services**. JavaScript is required on this page. Other panel pages retain
their existing behavior.
Enter the Unix password and choose **Connect to desktop**. Connecting explicitly
starts or reuses the managed desktop. Merely opening the page does not start it.
The Unix password is used in memory for the RDP handshake and is not saved,
returned in responses, or written to logs.

**Check desktop status** reads the supervisor's state, geometry and agent pause
state without starting a desktop or creating a browser session. Opening the
page itself remains read-only.

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

**Command line access (SSH)** provides a copyable command for an existing SSH
client. It needs no desktop connection and uses the panel hostname with SSH's
default port 22; adjust the destination or add `-p PORT` before `--` when SSH
uses another endpoint. SSH retains its existing authentication policy and
requires your SSH credentials. This is a client handoff, not a terminal running
inside the page. An embedded terminal would require a separate authenticated
backend; the pinned RDP-only gateway does not provide one.

## Observation and human control

New browser connections are view-only. The gateway enforces this even if a
client modifies the JavaScript or sends keyboard/pointer instructions directly.

- **Take control** pauses cooperative agent desktop actions, then enables
  browser keyboard and pointer input. It is the prominent primary control and
  changes to **You have control** while active. Click the display to focus keyboard input.
- **Observe only** disables browser input and retains the agent's pause state.
- **Resume agent** disables browser input and explicitly resumes agent actions.
- **Disconnect** revokes the current browser connection,
  scoped to this page's attachment. A disconnected page can revoke the active
  connection from another tab. A stale page cannot revoke a replacement
  connection with its old attachment. Disconnect releases this browser's control
  and restores the pause state that existed before takeover. It preserves any
  separate pause set before or during the connection. The desktop and applications
  keep running. Closing the tab or losing the stream performs the same cleanup.
- **Log out desktop** asks for confirmation, takes control, and requests a
  normal desktop logout. This ends the desktop and its applications. The browser
  remains usable for unsaved-work dialogs; canceling logout retains control until
  you observe, resume, or disconnect. Successful logout clears the browser session
  automatically, so the next connection can start a new desktop directly.
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
pause state and browser ownership before accepting keyboard or pointer input.
An explicit local pause or resume replaces browser ownership and disables its
input. Browser ownership has a 45-second lease, refreshed by the gateway while
the connection is alive. If the gateway crashes or cleanup fails, the supervisor
expires that lease and restores the previous pause state. Cleanup is conditional
on the original desktop generation and ownership token; it cannot resume a
replacement desktop or undo a later administrator pause.

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
and releases any browser-owned control, while preserving separate agent pauses.

The page displays the remaining session and inactivity limits. Select
**Observe only** to deliberately continue observing. Status polling does not
renew inactivity or extend the one-hour limit, and an expired session cannot
be revived with a late control action.

Changing the panel password prevents new connections but does not revoke an
already upgraded WebSocket. Use **Disconnect**, or restart
`basaltwater-guacamole.service`, to revoke it immediately. Removing Guacamole
stops active tunnels. No passwords or screen contents are retained in audit
logs. Screen contents are visible to anyone with the panel credential and a
valid desktop login.

## Installation and service isolation

Setup downloads a versioned `basaltwater-guacamole` Debian package from
[the dedicated build repository](https://github.com/bluehexagons/basaltwater-guacamole).
That repository pins Apache Guacamole 1.6.0 source archives and builds an RDP-only
`guacd` with its JavaScript client in CI. It maintains build configuration rather
than an Apache source fork. amd64 and arm64 packages are built natively and
checked in fresh Debian containers without development packages.

The installer verifies the package SHA-256 pinned in Basaltwater's source and
checks its name, version and architecture before asking APT to install it.
APT installs the package's declared runtime libraries; Python aiohttp also comes
from Debian APT. Compilers and development headers stay in build CI. No Java,
Tomcat, database, Docker daemon or VNC server is installed. The gateway uses
Debian's FreeRDP 3 packages; upstream labels FreeRDP 3 support experimental.

The initial binary release lives at `/opt/basaltwater-guacamole/1.6.0-bw1`.
Unchanged reruns validate and reuse the installed package without downloading
or rebuilding Guacamole. Unsupported distributions or architectures fail
explicitly; there is no automatic source-build fallback. Binary release updates
require a reviewed Basaltwater version/hash change; there is no separate
automatic upstream release updater. Debian runtime libraries continue under
the normal system update policy. An incomplete, unmanaged, writable or unsafe
linked release stops setup, including on reruns.

For the single testing server that used the earlier source installer, rerun the
updated setup or patch with `--guacamole` directly. A preliminary
`--no-guacamole` run is unnecessary. Setup installs the new package and restarts
the services; browser connections must reconnect. After checking the new service,
the old `/opt/basaltwater-guacamole/1.6.0` directory can be removed manually.
The old build packages are also left for explicit cleanup, since other software
may use them; new installations do not install them.

The [initial binary release](https://github.com/bluehexagons/basaltwater-guacamole/releases/tag/v1.6.0-bw1)
passed native builds and clean runtime installation/RDP negotiation on both
architectures. The Basaltwater installer additionally passed a real public
download, checksum and package validation, APT installation, release permission
checks, and an unchanged rerun without another download in a disposable amd64
container. An existing old source-release directory was preserved during that
check; this does not qualify a live service upgrade on the testing VM.

Both services run as the configured non-root account with systemd hardening.
`basaltwater-guacamole.service` runs `guacd` and the browser tunnel in a private
network namespace. The namespace contains only local gateway and RDP relay
ports. It has no route to the host's network or arbitrary remote machines.
`basaltwater-guacamole-rdp.service` provides an account-private Unix socket
which forwards only to the host's `127.0.0.1:3389` XRDP listener. Nginx reaches
the HTTP application through a separate Unix socket restricted to the account
and its web-service group. No raw Guacamole port is exposed on the host.
Orderly service stops revoke sessions and release held browser keys/buttons
and browser-owned pauses before terminating native children. Forced termination
cannot guarantee input release, but the supervisor expires the browser pause
lease. Disconnect before maintenance when possible.

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

For a shareable JSON report, run
`python3 scripts/collect_guacamole_diagnostics.py` from a Basaltwater checkout as
the desktop owner. Run `sudo -v` first to allow the collector's noninteractive
log reads. Redirect the output to a private file (for example, set `umask 077`
before redirecting to `guacamole-diagnostics.json`). The collector records
bounded service/XRDP logs, desktop state, package versions and source hashes.
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
separate agent pauses are preserved. A successful rerun
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

The browser follow-up corrected mouse/touch event registration against
Guacamole 1.6's `onEach` API and selected its existing Image/data-URI rendering
path. Pointer coordinates also account for display centering and scrolling in
fullscreen, while clamping drag releases at the display edges. The ImageDecoder
path in that release leaves VideoFrames and decoders
open and can block frame acknowledgements when image decoding rejects.
The fallback uses load/error handlers that release the display queue. A
closed or failed WebSocket tunnel now clears the client and controls directly,
so a stream disconnect is reported as a closed connection rather than being
discovered by a later status poll as an expired attachment. A
synthetic stream test with the real pinned JavaScript client exercised an
invalid first image, subsequent changing PNG frames, mouse/keyboard input after
takeover, and continued view-only enforcement. This covers browser streaming
and input; qualify the complete desktop connection again on the testing VM.

The control-lifetime follow-up adds supervisor regression coverage for ownership,
stale cleanup, explicit local pauses, crash recovery, scoped browser logout and
application dialog handling. Gateway checks cover desktop logout/replacement
cleanup, reconnection, and preservation of separate pauses. Updating this runtime
requires the current desktop to log out; normal setup already requests that
logout before installing it. Save work before rerunning setup, then reload the
desktop page.

The 2026-10-15 mobile follow-up passed synthetic browser checks at 1280 × 800,
390 × 844 and 844 × 390: connection and takeover, Unicode and composed text,
modifier release, direct touch and trackpad input, zoom, panning without remote
input, and SSH command copying. Maintenance hold/release also passed through
the narrow layout with isolated account state. These checks use the pinned
JavaScript client and a synthetic stream, not a live XRDP desktop. Native phone
keyboards and fullscreen transitions still need live-device qualification;
the collaborative preview did not complete the fullscreen transition.
