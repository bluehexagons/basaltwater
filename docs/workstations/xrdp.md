# XRDP configuration and troubleshooting

`basaltw` configures one shared XRDP desktop owned by the setup account, with
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

## Choose a topic

<a id="set-up-xrdp"></a>

- [Set up XRDP](xrdp-setup.md) Debian profile selection, access policy, and installation flags.

<a id="start-and-share-the-desktop"></a>
<a id="native-application-productivity"></a>

- [Use and share an XRDP desktop](xrdp-session.md) Session lifecycle, client behavior, and native application workflows.

<a id="migration-and-recovery"></a>

- [Migrate or recover an XRDP setup](xrdp-recovery.md) Desktop and service recovery after an installation change.

<a id="current-behavior-and-limits"></a>
<a id="managed-configuration"></a>
<a id="tls-certificate-health"></a>
<a id="proxmox-and-containers"></a>

- [XRDP configuration and platform limits](xrdp-config.md) Backend, configuration files, TLS health, Proxmox, and containers.

<a id="troubleshooting"></a>
<a id="check-the-service-and-configuration"></a>
<a id="inspect-logs"></a>
<a id="black-screen-or-immediate-disconnect"></a>
<a id="check-gpu-acceleration"></a>
<a id="freeze-while-resizing"></a>
<a id="verify-a-live-session"></a>

- [Troubleshoot an XRDP session](xrdp-troubleshooting.md) Service checks, logs, black screens, GPU acceleration, and resize failures.
