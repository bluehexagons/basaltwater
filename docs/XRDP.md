# XRDP configuration and troubleshooting

`infra-tools` configures one shared XRDP desktop owned by the setup account, with
dynamic resolution. The supported path uses Xorg with `xorgxrdp`; it does not
use Xvnc or a Proxmox emulated display as the RDP display.

## Defaults

| Setting | Default |
| --- | --- |
| Backend | Xorg with the `xrdpdev` driver |
| Rendering | Glamor acceleration when a supported, accessible DRM render node is detected; software fallback otherwise |
| Desktop | XFCE is the recommended RDP desktop |
| Listener | Loopback TCP 3389; `--rdp` selects remote access, defaulting to all IPv4 addresses |
| Firewall | Globally rate-limited access unless a generic or RDP-specific source is supplied |
| Sessions | One session; only the configured non-root account is admitted |
| Disconnected sessions | Retained indefinitely |
| Idle sessions | Not disconnected automatically |
| Clipboard | Enabled |
| Screen locking in the default XFCE session | Unsupported; Light Locker disabled, no managed automatic lock timer |
| Drive, printer, device, audio, RemoteApp, and video redirection | Disabled |

The session keeps `drdynvc` for dynamic resizing and `cliprdr` for clipboard
support. Use the CLI flags below to change the managed channel policy.

## Set up XRDP

On Debian workstations, setup uses the official Debian Sid builds of `xrdp`
and `xorgxrdp` because the Trixie `xorgxrdp` build has a known Xorg crash in
the RDP capture path. Sid is added with a low-priority pin. Only `xrdp` and
`xorgxrdp` explicitly select Sid; ordinary X11 utilities retain normal package
selection. Package installation disables recommendations,
so the optional PipeWire XRDP module and its large codec/runtime dependency
tree are not installed. Setup simulates the transaction first and refuses it
if it would remove packages or upgrade core packages such as `libc6`,
`systemd`, or `xserver-xorg-core`. The install keeps modified configuration
files without prompting, then reapplies the managed versions after the
package transaction completes.

Generic APT repair preserves this exact managed source only with its matching
low-priority pin. It restores sources commented out by older infra-tools repair
runs; desktop setup also reconciles the source when packages are already
installed. Package upgrades still require `--refresh-packages` for this pinned
XRDP path; restoring the source alone does not upgrade the running server.

For a remote target, provide the Unix account password through a secret source:

```bash
infra-tools setup workstation_dev 10.0.0.25 agent \
  --desktop xfce --rdp --password "$RDP_PASSWORD" \
  --lan-access
```

For local setup of an existing non-root desktop account, reuse its password
without placing it in process arguments:

```bash
sudo "$(command -v infra-tools)" setup workstation_dev localhost "$USER" \
  --control-plane --desktop xfce --rdp --rdp-existing-password
```

`--rdp-existing-password` is local-only. It cannot be combined with
`--password`, does not create or change the account password, and is rejected
when the account does not already exist. When a terminal prompt is available,
an empty password for a local setup is treated as this reuse mode; provisioned
guests must receive a new password.

Restrict all managed services to several explicit networks with one flag:

```bash
--access-source 10.0.0.0/24 100.64.0.0/10
```

Add `--rdp-source IP_OR_CIDR` when a source should reach RDP without being
added to the generic policy; RDP uses the union of both lists. Without either
kind of source, XRDP remains reachable through the globally rate-limited rule.
Use `--rdp-bind-address IP` to bind the listener to one local address. The
firewall reconciles only rules tagged `infra_tools RDP` and does not remove
unrelated UFW rules.
This reconciliation also runs for `server_lite --rdp`, before starting XRDP.

Client idle-disconnect and channel controls:

```bash
--rdp-idle-timeout 3600
--no-rdp-clipboard
--rdp-drive-redirection
--rdp-audio
```

Session-count and disconnect-cleanup flags are removed. Old saved values are
migrated to the fixed policy, with warnings for nondefault behavior changes.
Idle timeout only disconnects a viewer. Logout ends desktop applications;
keep independent long-running work in SSH, tmux, or supervised agent services.

## Start and share the desktop

As the configured account, without sudo:

```bash
infra-tools desktop status
infra-tools desktop start
infra-tools desktop exec -- thunar
infra-tools desktop screenshot --output /tmp/desktop-1.png
infra-tools desktop control pause
infra-tools desktop control resume
infra-tools desktop logout
```

All commands return JSON. `status` observes without starting a session; `start`
reuses one already running. `exec` and screenshots require a running session.
`exec` launches argument vectors with the desktop's X authority and D-Bus
environment and the invoking terminal's working directory, so relative project
paths work as expected. It does not execute a shell or change unrelated user services.
The session declares its desktop identity before starting D-Bus, including the
XFCE menu prefix, so desktop-specific autostart entries and toolkit integration
can recognize it.

`start` waits for the window manager and display geometry to become usable,
including when a human login wins a simultaneous start. A startup request gets
30 seconds, followed by up to 30 seconds of readiness polling; concurrent agent
starts serialize and reuse the result. While initializing, `status` reports
`starting`; unavailable geometry is `null`, with a diagnostic `detail` when
the geometry query fails. Wait with `start` before launching applications or
sending input. A startup error never automatically logs out or retries login.

The local agent startup uses XRDP 0.10's `xrdp-sesrun` Unix peer authentication;
it needs no stored password. This is for local Unix accounts; it does not
unlock password-protected keyrings or provide AD/Kerberos login.
See the [upstream explanation](https://github.com/neutrinolabs/xrdp/discussions/3467).

A human can connect first using an RDP client and the same account's Unix
password, or connect after the agent starts the desktop. Both see the same
applications. Only one human viewer is supported; another connection takes
over that display. The active RDP client controls resolution. Agent startup
uses 1280×720 until a client resizes it. No VNC server or second GUI is started.

With remote ingress disabled, connect through an SSH tunnel from the client:

```bash
ssh -N -L 13389:127.0.0.1:3389 agent@VM
```

Point the RDP client at `localhost:13389`. RDP authentication and certificate
verification still apply. A passwordless account can start the desktop locally
but needs a Unix password set by its administrator for human RDP login.

Disconnecting either participant retains the session. Logout or VM shutdown
ends it. No desktop autostarts at boot or restarts on failure. A new authenticated
RDP login counts as a new start after logout, including clients that automatically
reconnect: disable client auto-reconnect when intentionally logging out.
`logout` requests the desktop's normal logout, so unsaved-work dialogs can cancel
it. Observe `status` afterward; the request alone is not proof of completion.

Screenshots return the session `generation` and full-display pixel `geometry`.
Use `desktop windows` to discover current window IDs and titles. Capture one
application's client area with `desktop screenshot --window ID --output PATH.png`,
or use `--active-window` for the active application. Neither option changes focus;
hidden or closed windows produce an error instead of a full-desktop fallback.
Inspect captures for overlapping content before sharing. The `image_geometry`
field describes the PNG, while top-level `geometry` still describes the desktop.
Window metadata includes its desktop `origin`; recapture the desktop before
coordinate-based input because a window can move between commands.

Agents can include inspected PNGs through their client's image/attachment support
or local Markdown image links. Keep response artifacts at stable paths outside
Git, retain linked images, and avoid exposing unrelated windows or credentials.
The desktop skill documents capture selection and response attachment guidance.

Use those exact values for bounded input, then inspect the result:

```bash
infra-tools desktop input --generation GENERATION --geometry 1280 720 click --x 100 --y 200
infra-tools desktop input --generation GENERATION --geometry 1280 720 key --key ctrl+s
infra-tools desktop input --generation GENERATION --geometry 1280 720 text --text 'Example'
```

`move` takes coordinates; click buttons 4–7 scroll. Each mutation acquires a
short exclusive lease. Human takeover with `control pause` revokes it and blocks
agent mutations while leaving screenshots and RDP input available. `resume`
is explicit. This is cooperative same-account coordination, not a sandbox.
Human mouse movement does not automatically pause agents. Recapture after any
resize, reconnect or stale-input error. Screenshots are private PNGs at new
paths, never overwritten; delete temporary evidence after use.

For almost all browser testing, use available T3 Code collaborative preview or
managed Playwright. Desktop input is for native applications, desktop-specific
integration, or a justified fallback. These browsers have separate profiles
and authentication. The managed `infra-tools-desktop` skill provides routing
and command guidance for Codex/OpenCode.

## Native application productivity

Setup installs `wmctrl` and `python3-tk` from the normal package repositories
and adds **Shared Desktop Control** to the application menu. Rerun setup to
install these additions; setup handles managed desktop logout. The control
window shows agent status and offers pause/resume; closing it leaves pause in
effect. Agents can open it with `infra-tools desktop handoff` before handing over.

```bash
infra-tools desktop doctor
infra-tools desktop open /home/agent/document.txt
infra-tools desktop open /home/agent/document.txt --reveal
infra-tools desktop exec --wait-window Mousepad --timeout 15 -- mousepad
infra-tools desktop windows
infra-tools desktop window focus --window WINDOW --identity IDENTITY --generation GENERATION
infra-tools desktop window resize --window WINDOW --identity IDENTITY --generation GENERATION --width 800 --height 600
infra-tools desktop wait --window WINDOW --condition active --generation GENERATION
infra-tools desktop screenshot --active-window
```

Use the configured account's actual document paths. `open` accepts existing local
paths and uses `xdg-open`; application packages and file associations must exist.
`--reveal` opens the parent directory. `exec` flags precede `-- APPLICATION`.
Launch results include a PID and a `launch` token; inspect it using
`desktop launch-status LAUNCH --generation GENERATION`. Records are session-local
and limited to the latest 128 launches. Launchers can exit successfully after
delegating to an existing process. `--wait-window` uses a literal title substring,
reports matching windows and which IDs existed before launch, and never promises
that a match belongs to the new process or that a document has finished loading.
Inspect PID/class and the document; do not retry a timed-out launch blindly.

Window inventory adds PID/class where available, an identity fingerprint, and
the active window ID. Mutations require current generation and identity; title
changes invalidate the fingerprint. It reduces stale targeting but cannot prove
an X window ID was never reused. Available operations are focus, move, resize,
maximize, minimize, restore, and close. Desktop/panel windows are rejected.
`close` requests normal window-manager closure, allowing unsaved-work prompts;
it never destroys the X client. Verify completion with a fresh observation.

`wait` supports present, visible, active, and absent conditions. Window ID, title,
and PID selectors combine with AND. Absence requires a complete inventory.
Polling takes place outside the supervisor without holding a control lease;
human pause remains available. Timeout defaults to 15 seconds (maximum 120),
plus any in-flight bounded request. A timeout does not kill or relaunch the app.

Screenshots without `--output` receive unique persistent paths under private
`~/Pictures/infra-tools/` and return a capture timestamp. These files are never
automatically removed; retain linked response artifacts and clean disposable ones.

`sequence PATH --generation GENERATION` accepts a JSON list of 1–20 `input`,
`window`, `screenshot`, or `windows` requests with their normal payload fields.
Do not embed leases or generations. A single revocable 30-second lease prevents
other agents from interleaving mutations; human input remains independent.
Steps are not transactional: a failure reports partial results and releases
control without rollback or retries. Long waits belong outside a sequence.
For example, after observing current desktop geometry, a short save-and-capture
file can contain:

```json
[
  {"action": "input", "geometry": [1280, 720], "kind": "key", "key": "ctrl+s"},
  {"action": "screenshot", "active_window": true, "output": "/tmp/save-check-1.png"}
]
```

`doctor` reports session state, executable availability, handoff dependencies,
and XRDP service activity. It returns nonzero for detected problems and suggests
next steps without starting/restarting services or capturing content. Human RDP
reconnect/resize, clipboard, and application responsiveness remain explicitly
unverified by this check.

## Migration and recovery

All desktop profiles and `local desktop` use this stack. An explicit `--desktop`
or `--rdp` enables it on server/agent profiles; `agent_vm` remains headless by
default. `--no-rdp` retains a desktop profile's loopback listener. Omitting
desktop capability on a later headless setup is not a desktop uninstaller.

Run setup from SSH or a text console when desktop work is saved. Before package
upgrades, setup automatically requests normal logout of the configured account's
managed desktop, including a paused desktop, and waits up to 60 seconds plus
in-flight request time. It then allows up to 10 seconds for logind/sesman cleanup.
If a session disappears between listing it and reading its properties, setup
confirms its absence with a fresh session list and continues quietly. Older
versions could retain `Failed to get path for session …: No session … known`
in the final run notes even after logout and setup succeeded; that teardown
race is harmless. A failed session list or an unreadable session that still
exists continues to block desktop setup, as does a remaining XRDP process.
No separate logout command or confirmation is required. A canceled logout stops
setup and leaves agent input paused; no applications are force-killed. Disable
RDP automatic reconnect during setup. A replacement generation aborts the logout
wait, and desktop steps recheck idle state before cutover. Unmanaged/legacy
X11/Wayland or XRDP sessions still require manual logout. Setup leaves the desktop
stopped until the next explicit start or authenticated RDP login.
The setup masks GDM, LightDM, SDDM, LXDM and XDM plus the systemd display
manager alias, so graphical console login cannot create a second desktop.
The VM/hardware console becomes a text/recovery surface; direct physical-console
GUI attachment is not provided. Existing application packages and home data stay.
Unknown display-manager aliases require operator migration. OCI and
`--harden-user` configurations are rejected because persistent user services
are required. XFCE is the primary target; other environments and LXC need the
live qualification below before relying on them.

The dedicated `infra-desktop` group contains only the configured owner. SSH's
`remoteusers` group remains separate. User window-manager overrides and alternate
RDP shells are disabled. Legacy `~/startwm.sh` files are no longer executed.
The startup wrapper owns a private D-Bus session; applications launched by the
CLI join its desktop process group. Teardown never kills the entire user manager.
Applications explicitly launched as independent user services retain that
service's lifecycle.

Setup installs `/etc/systemd/system/xrdp-sesman.service` from the packaged unit,
removing its `BindsTo=xrdp.service` dependency and disabling `StopWhenUnneeded`.
This requires a full unit override: systemd cannot remove dependencies through
drop-ins. Setup refreshes the copy on reruns, preserves other packaged settings,
and rejects existing custom units or effective dependencies that still tie
sesman to the frontend. Rerun desktop setup after XRDP package upgrades to
incorporate vendor unit changes. The obsolete `shared-desktop.conf` drop-in is
removed during migration.

Managed XRDP files keep first-install `.bak` copies. Desktop configuration is
versioned at `/etc/infra-tools/desktop.json`, with a `.json.bak` on replacement.
The former display-manager symlink is retained as
`/etc/systemd/system/display-manager.service.infra-tools-backup`.
Setup failure stops the setup operation; inspect its error and rerun during a
session-free window. There is no automatic rollback that logs users out.
For administrator rollback, first log out, stop XRDP, restore the backed-up
XRDP files and desired display-manager alias, remove the managed
`/etc/systemd/system/xrdp-sesman.service` override and run `systemctl daemon-reload`,
unmask only the former display
manager and `display-manager.service`, and deliberately start that service.
Rollback restores configuration, not unsaved applications. Keep SSH available.

## Live qualification

The [desktop setup audit](plans/DESKTOP_SETUP_AUDIT.md) records the configuration
comparison, cleanup, remaining issues, and a server-side stale-pointer-cache
path requiring instrumented reproduction. The operator also observed a
reconnect with a 24-bpp client profile; seamless resize remains unqualified.

On 2026-09-12, agent-2 (Debian, XRDP 0.10.6.1-2, xorgxrdp 0.10.5-2,
XFCE session 4.20.2-2, scrot 2.0.0-1) passed local peer-authenticated startup,
native Mousepad launch, text input/save, desktop and application screenshots,
screenshots during pause, and normal logout followed by a new session generation.
T3 Code remained active. Application capture produced a 640×480 client-area PNG
within the 1280×720 desktop. Frontend restart still requires live qualification.

The operator subsequently tested Remmina: text clipboard worked in both
directions and disconnect/reconnect retained the session. Resizing completed
but caused a short black screen and reconnect. Xorg recorded the size change
before a connection drop; the desktop PID and generation remained unchanged,
and agent screenshots matched the final 1356×912 size. This is usable with a
resize interruption, not qualified seamless resizing. Subsequent client logs
identified Remmina 1.4.43 running FreeRDP 3.31.1 on CachyOS. Both native Wayland
and an XWayland reproduction failed with `Fastpath update Cached Pointer [a]
failed` after the resize, then automatically reconnected. The server completed
the first recorded resize in 286 ms before the client error; server connection
reset errors followed. This identifies the failing protocol update, not which
peer caused the cache inconsistency. FreeRDP's cache recreation during
reactivation is a candidate interaction, not a confirmed client regression.
The operator's Pause button blocked an agent launch while screenshot observation
remained available. The operator confirmed closing and reopening the control
window preserved pause, then clicked Resume. Agent status reported unpaused and
a harmless `/usr/bin/true` launch exited successfully, with unchanged desktop
PID and generation. The human handoff cycle passed.

The default XFCE Lock action did nothing: Light Locker is intentionally disabled
for the display-manager-free XRDP session, and no replacement locker is installed.
Lock/unlock is outside the default support contract. Do not treat disconnect or
agent pause as a screen lock. No automatic lock timer is configured by infra-tools;
an operator may deliberately add a locker and timers. An optional manual locker
requires its own authentication, reconnect and recovery qualification before
being offered as supported. Existing lock UI entries may remain visible.

After setup installed wmctrl/python3-tk, the same VM passed window focus, move,
resize, maximize, minimize/restore, normal close with an unsaved-work prompt,
canceled-close timeout, and save/capture sequences. Default document opening
selected Geany; parent-directory reveal opened the file manager. The handoff
window's Pause button blocked agent mutations while application screenshots
remained available; CLI resume restored control. Subsequent human UI handoff
qualification is recorded above.

Startup inspection found XFCE's notification autostart attempting a display-less
user service before falling back, plus PulseAudio receiving an empty X authority
filename. Setup now launches notifyd directly in the XFCE session, and the
supervisor declares Xlib's default ~/.Xauthority path when XAUTHORITY is unset or
empty, preserving an explicit value. A fresh session on this VM loaded the X11
audio integration and notification daemon without those startup errors. This
does not enable or qualify RDP audio redirection. GUI variables remain private
to the desktop, never imported into the global user manager.

The setup logout helper subsequently logged out that running managed desktop
successfully using the existing supervisor protocol; T3 Code remained active.

The implementation has mocked lifecycle/setup tests. End-to-end qualification
is still required on a disposable Debian VM with standard emulated graphics;
the development workspace cannot perform root setup. Record package versions,
client name/version, application PID, display, and generation for each check:

1. Fresh setup: start as agent, launch a native editor, connect as human and
   verify the same visible document and PID. Repeat with the human starting first.
2. Race multiple starts, disconnect every viewer and initiating agent, then
   reconnect. Confirm one desktop and unchanged applications.
3. Resize repeatedly, test clipboard, keyboard layouts, screenshot coordinates,
   pause/resume and competing agents. Confirm stale generations are rejected.
4. Cancel logout with unsaved work, then complete logout. Verify unrelated SSH,
   tmux and T3 services survive and a later start has a new generation.
5. Restart only the XRDP frontend, test network loss and a desktop crash, then
   reboot. Verify the documented persistence and explicit-start boundaries.
6. Test managed-session automatic logout, active legacy sessions (must defer), idle conversion, rerun,
   interrupted setup and administrator rollback. Repeat for each claimed desktop.

Do not treat mocked success as evidence of live resize, keyring, lock/unlock,
audio or multi-monitor compatibility.

## Managed configuration

| File | Purpose |
| --- | --- |
| `/etc/xrdp/sesman.ini` | Session manager and Xorg backend |
| `/etc/xrdp/xrdp.ini` | RDP protocol and channel settings |
| `/etc/X11/xrdp/xorg.conf` | `xrdpdev` display with automatic glamor/software selection |
| `/etc/X11/Xwrapper.config` | X server permissions |
| `/etc/apt/sources.list.d/infra-tools-sid.sources` | Official Sid source for newer XRDP packages on Debian |
| `/etc/apt/preferences.d/infra-tools-sid.pref` | Keeps Sid packages low priority outside the XRDP transaction |
| `/etc/apparmor.d/local/Xorg` | Allows xorgxrdp sockets, capture buffers, and the selected render node |
| `~/.local/share/xorg/Xorg.<display>.log` | Per-session Xorg diagnostics |
| `/etc/xrdp/infra-tools-startwm.sh` | Root-owned shared desktop supervisor entry point |
| `/etc/infra-tools/desktop.json` | Versioned owner and environment declaration |
| `/run/user/UID/infra-tools-desktop/control.sock` | Private same-UID control channel |

The Xwrapper configuration requires:

```ini
allowed_users=anybody
needs_root_rights=no
```

Because the X server runs without root privileges, `sesman.ini` selects its
configuration as `xrdp/xorg.conf`. Xorg resolves that trusted relative path to
`/etc/X11/xrdp/xorg.conf`; an absolute `-config` path is rejected for a
non-root session and produces the generic "X server could not be started"
login failure.

Per-session Xorg logs use `~/.local/share/xorg/Xorg.<display>.log`. This path
is accepted by Debian's enforced Xorg AppArmor profile; the traditional
`~/.xorgxrdp.<display>.log` path is denied before rootless Xorg can initialize.
Setup creates the log directory with mode `0700` and desktop-user ownership.

The AppArmor local override also grants confined Xorg access to the
desktop-user's entries under `/dev/shm`. xorgxrdp uses POSIX shared memory for
RDP frame capture; without this rule, `g_alloc_shm_map_fd` fails and the
capture path can terminate Xorg as soon as a client connects. The rule is
limited to user-owned shared-memory entries and does not disable AppArmor.

At setup time, infra-tools probes `/dev/dri/renderD*` and the corresponding
kernel driver. It enables the xorgxrdp glamor path only when the driver is on
xorgxrdp's supported allowlist (`amdgpu`, `i915`, `xe`, `msm`, or `radeon`)
and the desktop user can read and write the render node. In that case the
user is added to the `render` group, the exact node is allowed by AppArmor,
and `xorg.conf` receives `DRMDevice`, `DRI3`, and `DRMAllowList` settings. If
the supported node remains inaccessible after group setup, XRDP keeps the
software path; the `render` membership is retained so a corrected device ACL
can be picked up on the next setup run. This is a setup-time choice; rerun
setup after changing the guest's GPU device or render-node permissions.

This accelerates Xorg drawing and compositing. The Debian xrdp 0.10 packages
still use the CPU x264 encoder for H.264 RDP output, so this is not hardware
video encoding.

The startup script sets `XRDP_SESSION=1` and starts the selected desktop through
D-Bus. Xorg blanking timers and XFCE display power management are disabled by
their configurations. Setup retires only exact known legacy display-profile
and xfsettingsd overrides; custom display profiles and keyboard autostarts are
preserved. If a custom profile forces an unwanted resolution, adjust it in
XFCE Display settings. `xfsettingsd` remains enabled for normal desktop settings.

Power and GTK appearance changes merge the explicitly managed properties into
existing XML, preserving unrelated preferences. Malformed files stop the edit
instead of being silently reset. The obsolete nested Xinerama/window-theme
properties and exact known global `pm-is-supported` stubs are retired. Setup
does not force a HiDPI window-manager theme or install a warning-suppression
stub. These migrations run during normal setup, after managed desktop logout.

Existing managed configuration files are saved with a `.bak` suffix before the
managed versions are written. Use the normal setup or patch flow to reapply
them.

## TLS certificate health

XRDP uses `/etc/xrdp/cert.pem` and `/etc/xrdp/key.pem`. Setup verifies that:

- both paths are regular files;
- the private key is readable only by its owner and permitted group;
- OpenSSL can parse the certificate and key;
- the certificate and key match;
- the certificate is not expired; and
- the `xrdp` daemon can read both files.

An invalid pair stops setup and keeps XRDP fail-closed. Expiry within 30 days
generates a warning. The security monitor repeats these checks every 15 minutes
and reports errors, recovery, expiry warnings, or certificate fingerprint
changes once per state change. It never records private-key contents.

Inspect the certificate and daemon access without printing the private key:

```bash
sudo openssl x509 -in /etc/xrdp/cert.pem \
  -noout -subject -issuer -dates -fingerprint -sha256
sudo runuser -u xrdp -- test -r /etc/xrdp/cert.pem
sudo runuser -u xrdp -- test -r /etc/xrdp/key.pem
sudo systemctl status security-monitor.service --no-pager
```

The default certificate is normally self-signed. Certificate validity does
not make it trusted by RDP clients; use an operator-managed certificate when
client trust is required.

## Proxmox and containers

A hosted desktop VM receives a VirtIO-GPU recovery/noVNC console and a serial
socket. XRDP starts a separate `xrdpdev` Xorg display. Setup probes for an
accessible supported DRM render node and enables glamor only when one is
actually present; the normal Proxmox emulated VirtIO-GPU recovery display is
not treated as XRDP acceleration. In the usual VM case the session therefore
uses the software fallback and no GPU group is added.

Software mode sets an empty `DRMDevice` to disable xorgxrdp's default render-node
probe. Older templates used `UseGlamor=false` and `SWCursor=true`, neither of
which xorgxrdp 0.10.5 reads. Removing these ineffective options does not disable
RDP cursor caching or fix the observed resize disconnect.

Unprivileged Proxmox LXC guests support basic XRDP access, but host limits may
affect desktop polish. A VM is the better choice for a reproducible graphical
workstation. See [Machine types](MACHINE_TYPES.md) and
[Proxmox workflows](PROXMOX.md) for capability and provisioning details.

## Troubleshooting

### Check the service and configuration

```bash
sudo systemctl status xrdp xrdp-sesman --no-pager
sudo xrdp --version
sudo grep -A 5 '\[Xorg\]' /etc/xrdp/sesman.ini
sudo cat /etc/X11/Xwrapper.config
```

The Xwrapper output should contain `allowed_users=anybody` and
`needs_root_rights=no`. Confirm that the Xorg backend uses `xrdpdev`, not
Xvnc.

### Inspect logs

```bash
sudo journalctl -u xrdp -u xrdp-sesman -n 100 --no-pager
sudo tail -100 /var/log/xrdp.log
sudo tail -100 /var/log/xrdp-sesman.log
tail -100 ~/.xsession-errors
tail -100 ~/.local/share/xorg/Xorg.*.log
```

### Black screen or immediate disconnect

Check the Xwrapper settings, then confirm that the desktop and D-Bus startup
commands are installed. Look for permission errors in `xrdp-sesman.log` and
startup failures in `~/.xsession-errors`. A stale desktop autostart entry or
fixed XFCE display profile can also prevent a session from starting; rerun
`patch` to restore the managed desktop settings.

If the connection log reports that `ip` is not needed, reapply the current
configuration. Older managed `xrdp.ini` files included an obsolete `ip` field
in the local Xorg session entry. The warning itself is harmless, but the same
older configuration may also contain an absolute Xorg `-config` path that
prevents a non-root X server from starting.

If `xrdp-sesman.log` waits ten seconds and then reports `Unable to open display`
without creating an Xorg log, check whether AppArmor enforces the `Xorg`
profile. Reapply the current configuration to move per-session logs from the
denied home-directory path into `~/.local/share/xorg`.

### Check GPU acceleration

When glamor is selected, the Xorg log contains `rdpPreInit` lines showing the
render node, a DRM driver name, and `glamor init ok`. Check the decision and
the effective permissions with:

```bash
grep -E 'rdpPreInit:|glamor init|unsupported render node' \
  ~/.local/share/xorg/Xorg.*.log
ls -l /dev/dri/renderD* 2>/dev/null
id
```

`unsupported render node`, `open failed`, or an absent `/dev/dri/renderD*`
means that the managed software fallback is in use. For Proxmox, a
VirtIO-GPU recovery console alone is expected to produce this result; a
guest-configured VirGL/3D render node must also expose a supported driver and
usable permissions before glamor is enabled.

If Xorg logs `rdpClientConInit: g_tcp_local_bind failed` and then repeats
`g_sck_accept failed`, inspect the per-user socket directory under
`/run/xrdp/sockdir`. Reapply setup to configure
`SessionSockdirGroup=xrdp`; xorgxrdp must create `xrdp_display_<display>` in
the user-specific directory (for example, `/run/xrdp/sockdir/1000`).

### Freeze while resizing

Distribution-packaged Remmina clients are the compatibility target; an older
client comparison is diagnostic, not a deployment requirement. Record the
running FreeRDP version as well as Remmina's version.

XRDP 0.10.6.1 does not recognize `enable_gfx=false`. Older infra-tools templates
included that ineffective setting while claiming to force classic RDP. The
template now documents actual negotiation: `max_bpp=32` permits GFX/H.264.
Removing the ignored setting does not change behavior or fix the cached-pointer
disconnect. `use_fastpath=both` and `new_cursors=true` match upstream defaults.

For the cached-pointer failure recorded above, compare the same installed
client with a 24-bit color-depth profile, reconnect, and repeat dynamic resizing.
XRDP rejects GFX negotiation below 32 bpp, so this tests the classic path without
changing client packages or disabling the dynamic virtual channel needed for
resizing. Verify the negotiated pipeline in the server log. A passing comparison
would justify further server-side compatibility work; it would not establish
that all clients should lose GFX by default. Relevant upstream sources are the
[XRDP negotiation code](https://github.com/neutrinolabs/xrdp/blob/v0.10.6.1/libxrdp/xrdp_sec.c)
and [FreeRDP cache recreation change](https://github.com/FreeRDP/FreeRDP/pull/13196).

Try a fixed client resolution first. If that works, resize more slowly and
test another RDP client. Confirm that the session uses `xrdpdev`, that
`xfsettingsd` is running, and that no stale XFCE display profile is overriding
RANDR. If the freeze persists, capture the XRDP, Xorg, and session logs while
reproducing it.

Use `infra-tools desktop exec -- xterm` in a running session to isolate an
application problem. Do not bypass the managed startup wrapper or restart
sesman while applications are running.

### Verify a live session

```bash
ps aux | grep -E 'xrdp|Xorg|xfce4-session|dbus-daemon'
xrandr
```

An active session should include an Xorg process using `xrdpdev`,
`xrdp-sesman`, and the selected desktop session. When reporting a problem,
include the XRDP and xorgxrdp versions, client and desktop versions, the
relevant log excerpts, and whether reconnecting changes the result.
