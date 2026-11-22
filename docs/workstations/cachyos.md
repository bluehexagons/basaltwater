# Set up a CachyOS coding workstation

`agent_cachyos` adds a small coding stack to an **already installed x86-64
CachyOS KDE Plasma workstation**. It uses the existing desktop account and is
intended for bare metal machines, including workstations with dedicated GPUs.
It does not provision CachyOS VMs or containers and does not extend the
general server profiles. KDE capture/input is available through explicitly
started, user-approved [native desktop automation](cachyos-desktop.md).
Managed Playwright remains deferred.

Support targets the **latest CachyOS rolling release**. Setup and refresh run
a full repository upgrade before installing the coding tools. The current KDE
baseline uses Plasma Wayland and Plasma Login Manager. Shelly supplies the default package-manager
GUI and CLI. Basaltwater uses that CLI for explicitly selected AUR software and
leaves the desktop, login manager, DNS, drivers, and distro update services under
CachyOS management. See the upstream [January](https://blog.cachyos.org/blog/2601-january-release/),
[June](https://cachyos.org/blog/2606-june-release/), and
[August 2026 release notes](https://blog.cachyos.org/blog/2608-august-release/)
for these defaults; do not infer current packages from an older installation ISO.

## Choose a topic

<a id="quick-start"></a>
<a id="install-with-t3-code-desktop"></a>
<a id="pick-the-options-you-need"></a>
<a id="ssh-access"></a>
<a id="install-the-administrator-ssh-helper"></a>
<a id="configure-ssh-access"></a>
<a id="optional-webhook-notifications"></a>
<a id="codex-only-agent-setup"></a>
- [Install or refresh the coding profile](cachyos-install.md) Quick start, setup options, SSH, notifications, and Codex-only mode.

<a id="t3-code-desktop"></a>
<a id="t3-code-web-service-host-locally-or-on-a-trusted-lan"></a>
<a id="install-the-local-service"></a>
<a id="t3-connect"></a>
<a id="pair-this-workstation-or-another-device"></a>
<a id="optional-workstation-firewall"></a>
- [Run T3 Code on CachyOS](cachyos-t3-code.md) Native desktop installation, local web service, and trusted-LAN pairing.

<a id="package-and-repository-behavior"></a>
<a id="cleanup-during-setup"></a>
<a id="skills-diagnostics-and-boundaries"></a>
<a id="later-maintenance"></a>
<a id="upgrade-and-repeat-your-last-setup"></a>
<a id="aur-download-failures"></a>
<a id="switch-modes-on-a-later-setup"></a>
- [Inspect and maintain the profile](cachyos-profile-reference.md) Package ownership, cleanup, installed skills, and readiness.
