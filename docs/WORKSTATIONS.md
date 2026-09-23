# Workstations and desktop applications

For an already installed CachyOS KDE desktop, use the separate
[`agent_cachyos` local tooling profile](CACHYOS.md). The Debian desktop/RDP
setup and flags below do not apply to that profile.

The workstation system types build a Debian desktop from the same
machine-aware setup pipeline used for servers. Choose a profile first, then
override the desktop, browser, or application choices with flags.

To use a Debian desktop as the local control plane, combine the workstation
profile with `--control-plane` and select the agent tools you need (GitHub CLI
and Codex CLI in this example):

```bash
wget --timeout=20 --tries=2 -O "$HOME/.basaltwater-install.sh" https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh
sudo sh "$HOME/.basaltwater-install.sh" --user "$USER" --local-setup agent_workstation \
  --control-plane --desktop xfce --rdp --rdp-existing-password
rm -f "$HOME/.basaltwater-install.sh"
```

This keeps the graphical workstation setup while adding the SSH, rsync,
diagnostic, terminal, and package-management tools used to administer other
VMs and containers. Run it from SSH or a text console with desktop work saved.
Setup logs out an existing managed desktop automatically; legacy graphical
sessions require manual logout. It converts access to one shared XRDP desktop;
console graphical logins are disabled and the existing account password is reused.
Select only the agents needed with repeatable
`--agent-tool` flags; language runtimes remain separate explicit options.

Agent installation, authentication, and non-secret configuration are separate
choices. See [Agent authentication](AGENT_AUTHENTICATION.md) and
[Git access](GIT_ACCESS.md) before copying active-user credentials to a
workstation or VM, especially when multiple VMs need independent access.
For agent-controlled page previews and interaction, see
[Agent browser automation](BROWSER_AUTOMATION.md); it is independent of the
human-operated browser described in this guide.

Basaltwater manages only T3 Code's server-side `--web-interface t3code` path.
Install desktop and mobile clients separately using upstream's supported
distribution. The server, pairing, and update model is documented in
[T3_CODE.md](T3_CODE.md).

## Profiles

| Profile | Adds by default |
| --- | --- |
| `workstation_desktop` | Desktop, Firefox ESR, and CLI tools |
| `pc_dev` | Desktop, Firefox ESR, LibreOffice, SMB client packages, Remmina, and CLI tools |
| `workstation_dev` | Desktop, Firefox ESR, and CLI tools including Neovim |
| `agent_workstation` | Developer workstation defaults plus GitHub CLI and Codex |
| `agent_code_vm` | Agent workstation plus Geany, T3 Code web, RDP, pairing, and read-write Git/auth defaults |

The default desktop is XFCE. All workstation profiles use Debian's Firefox ESR
by default. A browser selected with `--browser` becomes the default for the
setup user; repeat the flag to install more than one browser. Graphical editors
are explicit on the general profiles: select Geany or Visual Studio Code with
`--editor geany` or `--editor vscode`. The opinionated `agent_code_vm` profile
defaults to Geany; `--editor vscode` replaces that editor default.

## Common setups

### Headless agentic coding host

For a Debian VM or server that should run coding agents from SSH or a terminal,
without a desktop or RDP, use `agent_vm`. Add only the language runtimes your
projects need:

```bash
basaltw setup agent_vm 10.0.0.24 agent \
  --node --python --go --agent-config active \
  --git-access read --repo https://github.com/user/project.git
```

This installs GitHub CLI, Codex, and the selected runtimes without the broader
control-plane administrator package set. Remove `--agent-config` or `--repo`
when those inputs are not needed. Use `tmux` for long-running agent sessions
over SSH. To add another provider, use `--agent-tool opencode` or a
comma-separated list. Use `--no-agent-tool gh` to remove a profile default.
Codex and OpenCode also receive the shared base workflow skills described in
[Managed agent workflow skills](AGENT_SKILLS.md); they do not require T3 Code.

### High-capability graphical agent VM

Use `agent_code_vm` for the recurring combination of a graphical coding
desktop, Firefox ESR, Geany, T3 Code's headless web service, GitHub CLI, Codex,
RDP, protected pairing, and read-write Git/auth defaults. Provisioning
capacity, project language runtimes, browser fallback, and network access
remain operator choices:

```bash
basaltw setup agent_code_vm 10.0.0.25 agent \
  --provision-on pve1 --name agent-1 \
  --image-storage local \
  --memory 4G --cores 4 \
  --storage root local-lvm 64G \
  --disk-ssd root --disk-discard root --disk-backup root \
  --lan-access \
  --agent-tool opencode
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
includes `ripgrep` (`rg`), `jq`, SQLite, `file`, `tree`, `make`, and `patch`.
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

Choose the browser surface for the task. Prefer managed Playwright for
repeatable VM-origin checks and when user collaboration is unnecessary;
prefer T3 Code's preview for a shared browser or client-origin behavior. Add
`--browser-automation playwright` when Codex or OpenCode should provide both,
including SSH-only sessions and checks that must continue while the T3 Code
application is closed:

```bash
basaltw setup agent_code_vm 10.0.0.25 agent \
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
basaltw setup agent_workstation 10.0.0.25 agent \
  --control-plane --desktop xfce --rdp \
  --password "$RDP_PASSWORD" --rdp-source 10.0.0.0/24 \
  --agent-config active \
  --browser-automation playwright \
  --repo https://github.com/user/project.git
```

This adds the selected agent tools, Playwright browser automation, Firefox ESR,
administrator tools, and the selected repository. Neovim is part of the CLI
bundle; add `--editor geany` or `--editor vscode` only when the VM needs a
graphical development editor.
Repeat `--agent-tool` for any combination
of `gh`, `codex`, `claude`, and `opencode`. Install a graphical T3 Code client
separately when needed. The RDP password is
the target Unix
account's password; provide it through a secret-sourced environment variable,
not a literal value in shell history. For a local Debian GNOME machine, use
the [installer handoff](INSTALLATION.md#set-up-a-debian-gnome-desktop-control-plane), which
converts graphical access to the shared XRDP session after console logout.

Agent updates run during an explicit setup rerun or deliberate agent command;
there is no background agent-update timer. Host APT, security, cleanup, and
restart maintenance still runs as described in
[`MAINTENANCE.md`](./MAINTENANCE.md). The default restart policy may force a
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
  --desktop cinnamon --office --browser brave
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

## Browser choices

This section covers the browser used by a person at the desktop. It does not
configure an MCP server for coding agents; use `--browser-automation
playwright` for that independent capability.

Supported `--browser` values are `brave`, `firefox`, `librewolf`, `helium`,
`browsh`, and `lynx`:

- Brave and LibreWolf use Debian repositories unless `--flatpak` is selected.
- Firefox uses `firefox-esr` through apt, or the Flathub build with Flatpak.
- Helium is downloaded from its upstream GitHub release for amd64 or arm64.
- Browsh installs Firefox as a dependency, resolves the newest stable release
  with a Debian package for the target architecture, and provides a terminal
  browser.
- Lynx installs the Debian terminal browser and does not become a graphical
  default.

For an extrepo-based browser install, setup first refreshes APT normally. If
that refresh fails, it makes one bounded attempt to refresh existing
extrepo-managed definitions for Brave, LibreWolf, and legacy VS Code installs,
then retries APT. The extrepo repair has a 30-second per-source timeout and does not
run on successful reruns. It does not remove or rewrite manually managed files under
`/etc/apt/sources.list` or `/etc/apt/sources.list.d`. If APT still reports an
unrelated third-party source error, repair or disable that source separately
before rerunning setup.

Native Helium and Browsh packages are staged in private, randomly named
temporary directories and removed after installation. Setup does not leave
uninstalled browser-extension packages in the shared `/tmp` directory.

The first browser in a repeated list is written to the setup user's
`~/.config/mimeapps.list` as the HTTP/HTTPS default. Text-only browsers do not
write a desktop default. XFCE's per-user WebBrowser helper is configured at
the same time, so its launcher does not fall back to the optional
`www-browser` command. A per-user `X-XFCE-Helper` desktop entry is also
created, so XFCE's Preferred Applications dialog and `exo-open --launch
WebBrowser` resolve the selected browser rather than falling back to an
unregistered command. These files are managed defaults: workstation setup
recreates them rather than merging older basaltwater state. The `v2.0.0` setup
is intended for clean workstation builds; rebuild a workstation created by an
older basaltwater release instead of attempting an in-place migration.

On VM and hardware targets, AppArmor is enabled and its distro service reloads
the installed policy without blanket-enforcing package profiles. This preserves
each profile's declared `enforce`, `complain`, or `unconfined` mode. LibreWolf's
own browser sandbox remains responsible for browser confinement. Setup replaces
the package profile with its managed unconfined compatibility profile and
reloads it. The `v2.0.0` setup is intended for clean workstation builds rather
than in-place migration of older basaltwater state.

Debian 13's AppArmor policy also supplies the `unprivileged_userns` transition
used by otherwise-unconfined applications that create a user namespace. Setup
loads and verifies this capability-stripping compatibility profile whenever
the kernel's AppArmor user-namespace restriction is active. This supports
Chromium and Firefox builds downloaded into per-user caches by Playwright and
other browser-automation tools without disabling their sandboxes or granting
an unconfined AppArmor attachment to every executable in a user-writable cache.
If that required profile cannot load, workstation setup stops with a clear
error instead of leaving browser automation silently broken.

## Flatpak and containers

Flatpak requires a desktop-capable host and is unreliable in unprivileged
containers. On a container target, basaltwater warns and falls back to apt for
browsers; other Flatpak bundles may also fall back to apt when Flatpak cannot
be installed. Prefer `--machine vm` when a reproducible Flatpak desktop is
required.

## Desktop and RDP choices

`--desktop` accepts `xfce`, `i3`, `cinnamon`, or `lxqt`. All desktop setups use
one XRDP session, reachable on loopback without `--rdp`. An explicit `--desktop`
also enables it on a headless profile. `--rdp` enables remote ingress;
the detailed session, TLS, and dynamic-resolution behavior is in
[`XRDP.md`](./XRDP.md). `--dark` configures XFCE, LXQt, or Cinnamon themes;
i3 receives an informational message because its theme is normally configured
in the user's i3 setup.

When a workstation is provisioned as a hosted Proxmox VM, it receives a
VirtIO-GPU recovery/noVNC console plus a serial socket. XRDP uses its own
xorgxrdp display; setup enables glamor only when the guest exposes a supported,
accessible DRM render node and otherwise uses the software fallback. Changing
the recovery console device alone does not accelerate the remote session. The
complete host-side settings and CPU/migration tradeoffs are in
[`PROXMOX.md`](./PROXMOX.md).

RDP logins use the setup user's Unix password. Remote setups and setups that
create a user require a non-root `--password`; prefer a secret-sourced
environment variable such as `--password "$RDP_PASSWORD"`. For local setup of
an existing desktop account, `--rdp-existing-password` reuses the password
already configured for that account without exposing it in process arguments.
It cannot be combined with `--password`, cannot be used for a remote target,
and does not set or change the account password. Passwords are not written to
saved setup state. State containing this field is sanitized when it is loaded.

Use repeatable `--rdp-source IP_OR_CIDR` flags to restrict UFW ingress to the
trusted LAN, management network, or VPN clients that should connect. Without
one, RDP remains globally rate-limited. XRDP binds all IPv4 interfaces by
default; `--rdp-bind-address IP` narrows the listener to one target-side
address. Clipboard remains enabled for coding workflows, while
drive/device, printer, audio, RemoteApp, and video redirection are disabled.
Use `--no-rdp-clipboard`, `--rdp-drive-redirection`, or `--rdp-audio` to change
the explicitly managed channel policy.

XRDP permits one session owned by the setup account. Human RDP login or
`basaltw desktop start` creates it; disconnect retains it until logout.
`--rdp-idle-timeout` disconnects an idle client without ending applications.
Session-count and disconnect-cleanup flags have been removed. Use
`basaltw desktop exec -- APPLICATION` to launch into the same session.
For browser testing, prefer T3 Code or managed Playwright over desktop input.

The `pc_dev` profile includes Remmina with RDP and VNC plugins. Other profiles
can install it through the explicit custom step `install_remmina` when using
`--steps`.

## Verification

After setup, inspect installed applications as the target user:

```bash
command -v firefox librewolf brave-browser nvim geany code remmina
flatpak list --app
xdg-mime query default x-scheme-handler/https
xdg-settings get default-web-browser
```

For remote sessions, test XRDP separately and use the log checks in
[`XRDP.md`](./XRDP.md). Machine capability differences, including Flatpak
fallbacks and software rendering in containers, are documented in
[`MACHINE_TYPES.md`](./MACHINE_TYPES.md).

For agent-operated native controls, see [Desktop automation](DESKTOP_AUTOMATION.md).
It covers bounded AT-SPI inspection, named actions, semantic waits, and a small
Geany edit/save check using the default `agent_code_vm` editor.
