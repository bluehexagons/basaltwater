# Desktop and XRDP maintenance

Status: the managed session, listener policy, certificate checks, lifecycle
limits, and human handoff are documented in the [XRDP guide](../workstations/xrdp.md).
Dynamic resizing can interrupt and reconnect the RDP stream. Several lifecycle
and client combinations still need a disposable Debian qualification run.

## Current boundary

- XRDP uses an explicitly managed TLS certificate/key pair and a recurring
  health check. Network sources and redirection channels are configurable;
  agent pause is cooperative, not a lock or security boundary.
- Session limits and logout behavior use XRDP's supported controls. Basaltwater
  does not use a username-wide process-killing cleanup hook.
- Desktop RDP sessions use the configured software-rendered X.Org path.
  Proxmox desktop VMs retain a separate graphical recovery console.
- Live evidence supports startup, editor input/save, screenshots, pause/handoff,
  logout and reconnect. Resize can produce a brief black screen and reconnect;
  the cause of the FreeRDP cached-pointer failure remains unknown.

## Remaining work

| Area | Current gap |
| --- | --- |
| Release qualification | Fresh setup, concurrent starts, supported clients, keyboard layouts, restart/crash recovery, interruption, and admin rollback need disposable Debian coverage. See the [release matrix](../release-qualification.md). |
| RDP access changes | A private-network preset, trusted certificate enrollment/rotation, connectivity preview, and timed firewall rollback need an explicit migration and recovery contract. |
| Configuration upgrades | Managed full-file XRDP/X.Org configuration needs supported-version checks, validation, and restoration if activation fails. |
| Restart safety | Host restart policy does not prove arbitrary agents, builds, or terminal-multiplexer work has drained. Coordinate with the [agent host maintenance brief](agent-host-maintenance.md). |
| Hardware acceleration | Physical GPU passthrough and xorgxrdp acceleration are not supported by the software-rendered profile; add only as a separate opt-in contract if a concrete need arises. |

Mocked tests do not establish live desktop/client compatibility. Keep current
unsupported combinations and repair steps in the XRDP operator guide.
