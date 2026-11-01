# Feature availability and testing by platform

**Snapshot: 2026-11-01; includes the owner's stack setup rerun report and retained
evidence through 2026-11-01.** The rerun indicates basic functionality only;
not all selected features were tested.
The table shows what the documented profiles offer, not whether a feature
passed on that platform. Follow each link for scoped status, evidence and
next checks. The [status legend](README.md#status-legend) explains the labels.

| Capability | Debian / Proxmox | CachyOS | Native Windows | Ubuntu WSL |
| --- | --- | --- | --- | --- |
| Server setup, services and deployment | [Available](debian.md#debian-guests-and-services) | Outside workstation profile | Outside native profile | Separate build/agent profile; not general server qualification |
| Coding agents and tools | [Available](debian.md#debian-guests-and-services) | [Experimental workstation](cachyos.md) | [Experimental workstation](windows.md#native-windows-11) | [Separate Linux environment](windows.md#ubuntu-wsl) |
| Desktop workflows | [Debian XFCE/RDP](debian.md#debian-guests-and-services) | [KDE native desktop](cachyos.md) | [Native desktop](windows.md#native-windows-11) | Linux GUI results require separate evidence |
| Builds and CI | [Builder and runner](debian.md#debian-guests-and-services) | Project-owned development tools | [Runner available; managed build host planned](windows.md#native-windows-11) | [Selected build/agent tooling](windows.md#ubuntu-wsl) |
| Proxmox host and guest operations | [Available on Proxmox](debian.md#proxmox-host-and-guest-operations) | Outside workstation profile | Outside native profile | Outside WSL profile |

**Do not transfer a result across columns.** A Debian guest, Proxmox host,
native Windows account and Ubuntu WSL distribution are separate targets; a
test of a client also does not qualify its server or vice versa. “Available”
describes a documented capability and does not mean all options are qualified.
Unlisted combinations have no availability claim here.

| Platform record | Status at a glance | Evidence and next checks |
| --- | --- | --- |
| [Debian and Proxmox](debian.md) | 🟢 Selected workflows labbed; 🟡 owner-reported basic setup reruns; 🔴 LXC suspend failed | Guest services, host operations, exact gaps and recovery checks |
| [CachyOS](cachyos.md) | 🟡 Owner-reported partial refresh and earlier native checks; full profile unqualified | KDE workstation, T3, graphics and desktop boundaries |
| [Windows and WSL](windows.md) | 🟡 Owner-reported basic non-WSL setup with Godot/T3 Code and earlier native checks; WSL end-to-end unqualified | Installer, task, streaming, runner and WSL boundaries |

These are historical observations, not a percentage of features tested or a
statement about a later commit. The [release matrix](../release-qualification.md)
owns exact-candidate qualification.
See the [dated evidence summaries](evidence.md) for the owner-reported rerun,
Proxmox repair, storage activation and installed-versus-local Shares & Data boundaries.
