# Native Windows coding workstation

The experimental `agent_windows` installer profile prepares the current
x86-64 Windows 11 account for local coding with T3 Code. It installs missing
PowerShell 7, Git with Git Bash, GitHub CLI and a native `basaltw` command, with
T3 Code desktop enabled by default. Choose at least one coding provider on the
first run, for example `-AgentTool codex`. Provider logins, Git identity, and T3
settings are preserved. Run as the intended Windows account. Package installers
may ask for UAC. Allow at least 5 GiB of free space, plus space for your projects
and their dependencies.

Windows follows the existing-account workstation approach for development,
compatibility testing, CI and native packaging. Choose platforms according to
the project's requirements; project-owned build and test commands stay authoritative.

This is a tools-and-skills setup. Basaltwater's Linux controller,
managed web services, privilege broker, and remote setup profiles
are not installed on Windows. WSL is a separate opt-in path in the
[Ubuntu WSL guide](windows-wsl.md). Native Windows build-host services remain
[planned work](../plans/windows-support.md).

## Choose a topic

<a id="install"></a>

- [Install or refresh the Windows profile](windows-install.md) Installer command, account scope, tool updates, T3 restart, and reboot behavior.

<a id="review-selections-from-a-checkout"></a>

- [Preview and change tool selections](windows-tool-selection.md) Checkout-led plans, saved options, and individual native tool bundles.

<a id="pinned-source-and-recovery"></a>
<a id="native-basaltw-commands"></a>
<a id="low-space-recovery"></a>

- [Maintain and recover the Windows profile](windows-maintenance.md) Pinned source, native launcher commands, and bounded disk cleanup.

<a id="verify-this-account"></a>
<a id="validation-boundary"></a>

- [Verify the Windows account](windows-first-checks.md) Provider, T3, skills, and project checks with validation limits.
