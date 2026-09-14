# CachyOS coding workstation

`agent_cachyos` adds coding tools to an **already installed x86-64 CachyOS KDE
Plasma workstation**, using the existing desktop account. This is limited,
experimental support for bare metal. It does not provision CachyOS VMs or
containers or extend CachyOS support to the general server profiles.

The initial implementation is covered by mocked setup tests and installer
fixtures on Debian. Real CachyOS hardware, graphics, and end-to-end provider
sessions still need the acceptance checks below. KDE automation and managed
Playwright are deferred.

## Install locally

First finish the normal CachyOS installation, full-system updates, KDE setup,
and GPU driver setup. Open a terminal in your normal desktop session. Run the
following block **as yourself, without sudo**; the installer requests sudo only
when missing system packages need installation:

```bash
curl --fail --location --connect-timeout 15 --max-time 120 \
  --output "$HOME/.infra_tools-install.sh" \
  https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh &&
sh "$HOME/.infra_tools-install.sh" --channel dev --local-setup agent_cachyos
```

The `dev` channel contains this new profile on `main`; older release tags do
not. The source defaults to `~/.local/share/infra_tools` (or the configured XDG
data directory), and the launcher is `~/.local/bin/infra-tools`. The installer
adds PATH support for Bash, Zsh, or Fish without replacing shell preferences or
installing Python aliases. Open a new terminal after installation. Other shells
need `~/.local/bin` and `~/.opencode/bin` added to PATH manually.

The default tools are Git, ripgrep, native build prerequisites, GitHub CLI, and
Codex. Agent CLIs use their upstream user installers. Existing user-managed
executables are refreshed on rerun; system-managed executables are retained.
No account is created; passwords, group membership,
sudo policy, provider settings, credentials, and Git identity remain yours.
Run the selected provider's local login when needed. For example:

```bash
codex login
gh auth login
```

Authenticate before adding private repositories. Your existing Git credential
helpers and SSH-agent environment are retained. Setup accepts HTTPS repository
URLs and does not copy credentials from another host.

## Select tools

After installation, preview or apply the profile directly:

```bash
infra-tools setup agent_cachyos localhost --node --python --git-lfs --dry-run
infra-tools setup agent_cachyos localhost --node --python --git-lfs
```

`--dry-run` prints the step plan without installing software or changing files.
It can also run on a development/CI host; actual platform, account, and hardware
checks run before apply. Passing `--dry-run` to the **shell installer** still
installs the launcher and prerequisites; only the subsequent setup is a preview.

| Option | CachyOS behavior |
| --- | --- |
| `--agent-tool gh,codex,claude,opencode` | Add selected agents; repeatable; defaults include `gh,codex` |
| `--no-agent-tool codex` | Exclude a default from this run; does not uninstall an existing tool |
| `--node` | Install missing Node.js, npm, and pnpm commands using distro packages |
| `--python` | Install missing Python and uv commands; no global pip packages or interpreter aliases |
| `--go` | Install Go when absent |
| `--git-lfs` | Install Git LFS when absent; initialize it within projects as needed |
| `--godot` | Install the distro's Godot when absent; no export-template bundle or managed updater |
| `--av-tools` | Install missing FFmpeg and ImageMagick commands |
| `--gl-tools` | Install missing Mesa diagnostic and Vulkan diagnostic commands; no driver installation |
| `--repo HTTPS_URL` | Clone a missing repository; repeatable; existing origins must match |
| `--agent-workspace /absolute/path` | Clone destination, defaulting to `~/repos`; must be writable by you |
| `--web-interface t3code` | Install the optional localhost user service; implies Node tooling |
| `--web-interface-port PORT` | T3 HTTP port, default 3773; must be 1024–65535 |
| `--machine hardware` | Optional declaration; apply still verifies actual bare metal |

For example, add game and media tools and another agent:

```bash
infra-tools setup agent_cachyos localhost \
  --agent-tool opencode --node --python --git-lfs --godot --av-tools --gl-tools
```

An existing version-manager installation is retained when its commands are on
the invoking shell's PATH. Setup checks executable availability and selected
tool versions; it does not promise that an existing runtime satisfies every
project. Use project-specific environments and lockfiles. T3's Node version
requirement is checked explicitly.

## Updates and reruns

System packages are queried with `pacman -Q`. Missing packages are installed
using `pacman -S --needed`, without refreshing repository databases or running
an OS upgrade. Dependency resolution can still install or change dependencies.
If repositories are stale or dependencies conflict, setup stops; resolve the
error through CachyOS's normal update workflow and rerun.

Do not use `pacman -Sy` as a repair step: Arch-based systems do not support
partial upgrades. See [CachyOS updates](https://wiki.cachyos.org/configuration/post_install_setup/)
and [Arch system maintenance](https://wiki.archlinux.org/title/System_maintenance).

infra-tools installs no OS, language-runtime, agent, or Godot update timers on
this profile. A setup rerun refreshes selected user-managed agent CLIs and the
managed T3 runtime, then runs a bounded user-cache cleanup for this account;
system-managed executables remain under the package manager's control. Upstream
tools may have their own update behavior. Update infra-tools with
`infra-tools upgrade` or by rerunning the download block. Existing repositories
are never pulled, reset, or recursively chowned by setup.

Rerun `setup agent_cachyos localhost` with the options you want checked. This
profile does not save a controller-side host configuration and does not use
`patch`, `deploy`, or `recall`. Omitting an option does not uninstall software.
After a failed step, earlier successful installations remain; fix the reported
problem and rerun. There is no automatic rollback of installed packages.

An existing clone destination must be the root of a Git repository with the
requested origin. An ordinary directory inside another repository does not
qualify. If a destination conflicts or a clone was interrupted, inspect and move
that directory aside yourself, or choose another `--agent-workspace`; setup
does not delete its contents.

## Optional T3 Code

```bash
infra-tools setup agent_cachyos localhost --web-interface t3code
systemctl --user status infra-tools-cachyos-t3.service
```

Open `http://127.0.0.1:3773` on the workstation. The dedicated runtime lives in
`~/.local/share/infra-tools/cachyos-t3`, and the unit is
`~/.config/systemd/user/infra-tools-cachyos-t3.service`. It runs as you with your
provider credentials and the setup terminal's PATH. Setup checks HTTP readiness;
provider authentication and a real coding thread must still be verified.
Readiness probes bypass HTTP proxies and require consecutive successful
responses from the local URL. If startup fails, inspect the service journal and
check whether another application already uses port 3773; select another port
with `--web-interface-port` if needed.

The service starts with your user session. Setup does not enable lingering,
change suspend policy, configure a gateway, enroll other devices, or expose
network listeners beyond IPv4 loopback. An existing upstream `t3code.service`
causes setup to stop rather than adopt or replace it. A separately installed T3
desktop client is outside this profile's ownership.

Reruns retain the runtime. Changes to the unit may restart the service, so finish
active work before changing its port, workspace, or PATH. To stop it persistently:

```bash
systemctl --user disable --now infra-tools-cachyos-t3.service
```

For a deliberate runtime update outside setup, finish active threads, stop the
service, update its dedicated npm prefix as yourself, and restart it:

```bash
systemctl --user stop infra-tools-cachyos-t3.service
npm install --global --prefix "$HOME/.local/share/infra-tools/cachyos-t3" \
  --allow-scripts=node-pty,msgpackr-extract t3@latest
systemctl --user start infra-tools-cachyos-t3.service
```

Run each line only after the previous one succeeds. Check the service and open
the local UI afterwards. Use `journalctl --user -u infra-tools-cachyos-t3.service`
for startup errors. The generic VM T3 update/pairing commands do not manage this
unit. Upstream requirements and CLI behavior are documented in
[T3 installation](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md).

## Agent skills

Codex and OpenCode receive `infra-tools-cachyos-workstation` and
`infra-tools-cachyos-workspace` under `~/.agents/skills`. Selecting T3 adds
`infra-tools-cachyos-t3code`; the skill is retained on reruns while the managed
unit exists. The standard VM, XRDP, gateway, Godot-web, and browser-automation
skills are not installed. Known obsolete skills bearing infra-tools' managed
marker are removed; personal skills are preserved. Claude-only configurations
do not receive this shared Codex/OpenCode catalog.

Managed Git worktrees work locally through `infra-tools agent workspace`.
Other VM-oriented agent diagnostics, authentication rotation, maintenance
commands, desktop control, and gateway helpers are outside this profile.

## Hardware acceptance checks

Before treating a workstation as validated:

1. Record CachyOS, Plasma, kernel, GPU/driver, and installed tool versions.
2. Run the copy/paste install and repeat it; confirm desktop login, groups,
   shell customization, graphics settings, and existing repositories survive.
3. Authenticate an agent, complete a small edit/test workflow, and create and
   remove a managed worktree using a disposable test repository.
4. If selected, open T3, run a provider thread and terminal command, log out/in,
   and verify the user service. Check that only loopback is listening.
5. If selected, open a Godot test project, run it on the intended GPU, and check
   media conversion and Vulkan/OpenGL diagnostics. CLI versions alone do not
   validate rendering, audio, fullscreen behavior, or GPU selection.
6. Repeat relevant checks after a normal CachyOS update. Record AMD and NVIDIA
   results separately; passing one does not validate the other.

## Contributor boundaries

`plugins/cachyos.py` owns the composition, `lib/cachyos.py` owns the local support
boundary, and `common/cachyos_steps.py` owns tooling operations. The CLI routes
directly to the dedicated target-side runner in `remote_setup.py`; it does not
stage SSH payloads or invoke the general host setup lifecycle. Unsupported
options are rejected before normalization can enable additional capabilities.

Keep additions explicitly allowed and independently testable. Do not enable
generic workstation flags to obtain tools indirectly. Tests must mock pacman,
sudo, downloads, service operations, and hardware probes, using temporary
directories for user files. Native KDE automation and Playwright require their
own future compatibility work and skill selection.
