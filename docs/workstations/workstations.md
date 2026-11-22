# Workstations and desktop applications

For an already installed CachyOS KDE desktop, use the separate
[`agent_cachyos` local tooling profile](cachyos.md). The Debian desktop/RDP
setup does not apply to that profile; application flag support is compared below.

The workstation system types build a Debian desktop from the same
machine-aware setup pipeline used for servers. Choose a profile first, then
override the desktop, browser, or application choices with flags.

To use a Debian desktop as the local control plane, combine the workstation
profile with `--control-plane` and select the agent tools you need (GitHub CLI
and Codex CLI in this example). [Install the launcher](../getting-started/installation.md) first:

```bash
sudo basaltw setup agent_workstation localhost "$USER" --codex \
  --control-plane --desktop xfce --rdp --rdp-existing-password
```

This keeps the graphical workstation setup while adding the SSH, rsync,
diagnostic, terminal, and package-management tools used to administer other
VMs and containers. Run it from SSH or a text console with desktop work saved.
Setup logs out an existing managed desktop automatically; legacy graphical
sessions require manual logout. It converts access to one shared XRDP desktop;
console graphical logins are disabled and the existing account password is reused.
Select only the agents needed with `--codex`, `--claude`, or `--opencode`;
combine flags for multiple providers. Language runtimes remain separate explicit options.

Agent installation, authentication, and non-secret configuration are separate
choices. See [Agent authentication](../agents/agent-authentication.md) and
[Git access](../agents/git-access.md) before copying active-user credentials to a
workstation or VM, especially when multiple VMs need independent access.
For agent-controlled page previews and interaction, see
[Agent browser automation](../agents/browser-automation.md); it is independent of the
human-operated browser described in this guide.

Basaltwater manages only T3 Code's server-side `--web-interface t3code` path.
Install desktop and mobile clients separately using upstream's supported
distribution. The server, pairing, and update model is documented in
[t3-code.md](../agents/t3-code.md).

## Choose a topic

<a id="profiles"></a>
<a id="desktop-software-support-and-flags"></a>
<a id="browser-choices"></a>
<a id="flatpak-and-containers"></a>

- [Workstation profiles and software](workstation-profiles-and-software.md) Choose a profile, desktop software, browser, and container options.

<a id="common-setups"></a>
<a id="headless-agentic-coding-host"></a>
<a id="high-capability-graphical-agent-vm"></a>
<a id="desktop-agentic-coding-workstation-with-rdp"></a>

- [Workstation setup examples](workstation-setup-examples.md) Find setup examples for headless, graphical VM, and RDP workstations.

<a id="desktop-and-rdp-choices"></a>
<a id="verification"></a>

- [Workstation desktop and RDP setup](workstation-desktop-and-rdp.md) Choose desktop and RDP options and verify the installation.
