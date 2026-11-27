# Workstation setup examples

[Topic index](workstations.md)

## Common setups

### Headless agentic coding host

For a Debian VM or server that should run coding agents from SSH or a terminal,
without a desktop or RDP, use `agent_vm`. Add only the language runtimes your
projects need:

```bash
basaltw setup agent_vm 10.0.0.24 agent --codex \
  --node --python --go --agent-config active \
  --git-access read --repo https://github.com/user/project.git
```

This installs GitHub CLI, Codex, and the selected runtimes without the broader
control-plane administrator package set. Remove `--agent-config` or `--repo`
when those inputs are not needed. Over SSH, use
[Vim](workstation-profiles-and-software.md#terminal-vim) for editing and `tmux`
for long-running agent sessions. To add another provider, append `--claude` or `--opencode`.
Use `--no-agent-tool gh` to remove a profile default.
Codex and OpenCode receive base workflow skills under `~/.agents/skills`;
Claude Code receives them under `~/.claude/skills` when selected. The complete
bundles and ownership rules are described in
[Managed agent workflow skills](../agents/agent-skills.md); they do not require T3 Code.

### High-capability graphical agent VM

Use `agent_code_vm` for the recurring combination of a graphical coding
desktop, Firefox ESR, Geany, T3 Code's headless web service, GitHub CLI, Codex,
RDP, protected pairing, and read-write Git/auth defaults. Provisioning
capacity, project language runtimes, browser fallback, and network access
remain operator choices:

```bash
basaltw setup agent_code_vm 10.0.0.25 agent --codex \
  --provision-on pve1 --name agent-1 \
  --image-storage local \
  --memory 4G --cores 4 \
  --storage root local-lvm 64G \
  --disk-ssd root --disk-discard root --disk-backup root \
  --lan-access \
  --opencode
```

The image staging pool must be file-based and enable Proxmox `import` or `iso`
content; the usual `local-lvm` thin pool is appropriate for the guest root disk
but not for `--image-storage`.

The profile does not supply implicit web or RDP source ranges. The example
opts into the target-adjacent private subnet with `--lan-access`; use
`--access-source` or service-specific flags for routed, VLAN, VPN, or other
explicit sources. Without an access source, T3 Code remains loopback-only and
RDP uses its global rate-limited fallback.
Omitted account and pairing passwords are requested with hidden prompts;
an empty pairing password reuses the account password. Use
`--git-access none --git-auth none --agent-auth none`, `--no-rdp`, `--no-web-interface`, or
`--no-device-pairing` to disable individual defaults. Node.js is installed
automatically because T3 Code requires it. Go and other project runtimes remain
optional and can be selected with their runtime flags. The shared CLI baseline
includes Vim, ripgrep (`rg`), `jq`, SQLite, `file`, `tree`, `make`, and `patch`.
Add `--data-analysis` only when the VM needs the larger NumPy, pandas, SciPy,
Matplotlib, JupyterLab, and csvkit stack; the flag also enables Python tooling.
Add `--av-tools` when agents need local media inspection or conversion; it
installs ImageMagick, FFmpeg with ffprobe, and ExifTool without selecting any
language runtime.
Add `--gl-tools` for a minimal OpenGL inspection and debugging bundle containing
Mesa utilities such as `glxinfo` plus apitrace. It does not install a compiler,
graphics SDK, Vulkan tools, or GPU drivers.
The guided `basaltw shell` workstation-development flow offers the
data-analysis bundle as a separate, default-off choice and uses the saved
choice as the default when starting from a template.

In T3 sessions exposing preview tools, follow the
[preview-first session policy](../agents/browser-automation.md#choosing-playwright-or-collaborative-preview).
Outside those sessions, choose the browser surface for the task: prefer Playwright for
repeatable VM-origin checks and when user collaboration is unnecessary;
prefer T3 Code's preview for a shared browser or client-origin behavior. Add
`--browser-automation playwright` when Codex or OpenCode should provide both,
including SSH-only sessions and checks that must continue while the T3 Code
application is closed:

```bash
basaltw setup agent_code_vm 10.0.0.25 agent --codex \
  --browser-automation playwright
```

The capability remains saved and restored on rerun. It is intentionally opt-in
because its matching browser downloads can consume more than a gigabyte. Setup
installs browser workflow guidance tailored to the resulting Playwright-only,
T3-only, or combined capability set.

### Desktop agentic coding workstation with RDP

For a graphical Debian workstation, keep the default desktop profile, select
XFCE for the RDP session, and restrict RDP to the management network:

```bash
basaltw setup agent_workstation 10.0.0.25 agent --codex \
  --control-plane --desktop xfce --rdp \
  --password "$RDP_PASSWORD" --rdp-source 10.0.0.0/24 \
  --agent-config active \
  --browser-automation playwright \
  --repo https://github.com/user/project.git
```

This adds the selected agent tools, Playwright browser automation, Firefox ESR,
administrator tools, and the selected repository. The CLI bundle includes
[terminal Vim](workstation-profiles-and-software.md#terminal-vim).
Add `--editor geany` or `--editor vscode` when the VM needs a
graphical development editor.
Combine `--codex`, `--claude`, and `--opencode` for the providers you need;
`--agent-tool gh` selects GitHub CLI on profiles without that default. Install a graphical T3 Code client
separately when needed. The RDP password is
the target Unix
account's password; provide it through a secret-sourced environment variable,
not a literal value in shell history. For a local Debian GNOME machine, use
the [installer handoff](../getting-started/installation.md#set-up-a-debian-gnome-desktop-control-plane), which
converts graphical access to the shared XRDP session after console logout.

Agent updates run during an explicit setup rerun or deliberate agent command;
there is no background agent-update timer. Host APT, security, cleanup, and
restart maintenance still runs as described in
[`maintenance.md`](../operations/maintenance.md). The default restart policy may force a
restart after seven days of active-session deferrals, so long-running hosts
should use `--no-auto-restart --auto-restart-force-days 0` and manage pending
reboots explicitly.

Minimal developer workstation with RDP:

```bash
basaltw setup workstation_dev 10.0.0.25 alice \
  --desktop xfce --rdp --password "$RDP_PASSWORD" \
  --rdp-source 10.0.0.0/24
```

Add a lightweight graphical IDE using Debian's package:

```bash
basaltw setup workstation_dev 10.0.0.25 alice --editor geany
```

Or explicitly select Visual Studio Code:

```bash
basaltw setup workstation_dev 10.0.0.25 alice --editor vscode
```

The VS Code choice verifies Microsoft's published signing-key fingerprint and
uses a Basaltwater-owned keyring plus a source restricted to
`https://packages.microsoft.com/repos/code`. It does not enable Extrepo's
global `non-free` policy. Because the editor was explicitly requested, a key,
repository, or package failure stops setup instead of reporting a completed
editor step.

PC with office and SMB tools:

```bash
basaltw setup pc_dev 10.0.0.26 alice \
  --desktop cinnamon --office --browser firefox
```

Install several browsers and use a dark theme:

```bash
basaltw setup workstation_desktop 10.0.0.27 alice \
  --browser librewolf --browser firefox --dark
```

Use `--apt-install PACKAGE` or repeat `--flatpak-install PACKAGE` for
additional packages. For example, Discord is an explicit choice with
`--flatpak-install com.discordapp.Discord`. `--flatpak` selects Flatpak for
supported built-in applications such as LibreOffice; explicit editor selection
remains APT-managed so its behavior is independent of Flatpak.
