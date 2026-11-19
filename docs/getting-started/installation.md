# Install Basaltwater

Use the installer on the machine that will manage your hosts. It keeps a local
Git worktree, installs the managed `basaltw` launcher, and can configure
the same machine immediately.

For a guided first experiment, use [Try Basaltwater on a Debian
VM](getting-started.md). This page also covers advanced installation choices;
choose one path rather than running every example.

This guide describes the `v2.0.0` release-candidate series. See the
[release qualification record](../release-qualification.md) for the current candidate.
The `dev` channel follows `main` and `stable` selects the latest final release.
Select a preview by its exact tag.

Debian is the only officially supported distribution. Ubuntu and Linux Mint are
recognized as best-effort Debian-compatible hosts.

For an existing Windows 11 coding account, use the experimental
[native Windows setup](../workstations/windows.md). Its PowerShell installer
prepares Git, provider CLIs, optional runtimes, shared skills, and T3 Code desktop.
It does not install the Linux controller. The separate
[Ubuntu WSL setup](../workstations/windows-wsl.md) installs Linux tools in Ubuntu.

For an existing CachyOS KDE workstation, use the experimental
[`agent_cachyos` local setup](../workstations/cachyos.md). It uses the current desktop account,
installs missing tools through pacman/upstream installers, and leaves OS
updates and desktop configuration to you. The Debian examples below configure
a broader host stack and do not apply to CachyOS.
For Codex with T3 Code's native desktop package, follow the
[complete CachyOS desktop installer example](../workstations/cachyos.md#install-with-t3-code-desktop).
It uses `--local-setup agent_cachyos --t3code-desktop` with `--codex` as the desktop user,
without running the installer under `sudo`.

## Choose a topic

<a id="prerequisites"></a>
<a id="choose-an-installation-path"></a>
<a id="set-up-a-native-windows-coding-workstation"></a>
<a id="install-the-launcher-only"></a>
<a id="set-up-a-minimal-debian-control-plane"></a>
<a id="set-up-a-debian-gnome-desktop-control-plane"></a>
<a id="verify-the-installation"></a>
<a id="install-and-configure-a-remote-host"></a>
- [Install the controller or first remote host](installation-paths.md) Prerequisites, installation choices, and verification.

<a id="channels-and-upgrades"></a>
<a id="unsupported-orchestration-hosts"></a>
<a id="debian-package-sources"></a>
- [Switch channels and maintain the installation](installation-upgrades.md) Upgrades, legacy hosts, and Debian package sources.

<a id="optional-local-tools"></a>
<a id="workspace-and-credentials"></a>
<a id="related-guides"></a>
- [Local tools, workspaces, and credentials](installation-local-tools.md) Optional tools, account state, and related guides.
