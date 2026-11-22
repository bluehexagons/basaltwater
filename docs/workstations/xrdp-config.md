# XRDP configuration and platform limits

[Topic index](xrdp.md)

## Current behavior and limits

The supported path provides local peer-authenticated startup, native
application launch/input/capture, persistent human override and explicit state
control, clipboard in both
directions, normal logout, and reconnect to a retained session.

With Remmina 1.4.43 and FreeRDP 3.31.1, dynamic resizing can briefly blank the
screen and trigger an automatic reconnect while the desktop process and
session generation remain alive.
The server log reports `Fastpath update Cached Pointer [a] failed`; the peer
or protocol interaction that causes the cache inconsistency is unknown. A
24-bpp client profile also reconnects. Treat resizing as interrupted rather
than seamless; the cause is not isolated to XRDP or FreeRDP.

Screen locking and automatic locking are outside the default contract. Audio
redirection, keyring unlock, multi-monitor behavior, and other desktop
environments are not supported guarantees. Human override is not a screen lock.

## Managed configuration

| File | Purpose |
| --- | --- |
| `/etc/xrdp/sesman.ini` | Session manager and Xorg backend |
| `/etc/xrdp/xrdp.ini` | RDP protocol and channel settings |
| `/etc/X11/xrdp/xorg.conf` | `xrdpdev` display with automatic glamor/software selection |
| `/etc/X11/Xwrapper.config` | X server permissions |
| `/etc/apt/sources.list.d/basaltwater-sid.sources` | Official Sid source for newer XRDP packages on Debian |
| `/etc/apt/preferences.d/basaltwater-sid.pref` | Keeps Sid packages low priority outside the XRDP transaction |
| `/etc/apparmor.d/local/Xorg` | Allows xorgxrdp sockets, capture buffers, and the selected render node |
| `~/.local/share/xorg/Xorg.<display>.log` | Per-session Xorg diagnostics |
| `/etc/xrdp/basaltwater-startwm.sh` | Root-owned shared desktop supervisor entry point |
| `/etc/basaltwater/desktop.json` | Versioned owner and environment declaration |
| `/run/user/UID/basaltwater-desktop/control.sock` | Private same-UID control channel |

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

At setup time, Basaltwater probes `/dev/dri/renderD*` and the corresponding
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
probe. RDP cursor caching and resize behavior are independent of this setting.
See [resize troubleshooting](xrdp-troubleshooting.md#freeze-while-resizing).

Unprivileged Proxmox LXC guests support basic XRDP access, but host limits may
affect desktop polish. A VM is the better choice for a reproducible graphical
workstation. See [Machine types](../reference/machine-types.md) and
[Proxmox workflows](../operations/proxmox.md) for capability and provisioning details.
