# Basaltwater

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/wordmark-dark.svg">
  <img src="docs/brand/wordmark-light.svg" alt="Basaltwater" width="264" height="48">
</picture>

Infrastructure management, from one machine to your whole network.

The command is `basaltw` and the Python entry point is `basaltwater.py`.
Only the Basaltwater namespace is supported. The infra-tools migration is
complete, and its migration command has been retired. Remaining old
installations must first use the documented
[intermediate version](docs/migration-guide.md).

The [identity guide](docs/brand/README.md) includes editable assets and visual specimens.

Automated setup and operations for Debian control planes, servers, workstations,
and Proxmox guests. Basaltwater applies repeatable, machine-aware configuration
over SSH, stores redacted setup state in a workspace, and provides targeted
operations for hosts that are already configured.

This checkout is preparing the `v2.0.0` release-candidate series. Its current
package version is `2.0.0rc1`; preview tags use `v2.0.0-rc.N` and publish as
GitHub prereleases. Until a preview tag is published, the installer’s `dev`
channel tracks `main`. Preview tags are opt-in; `stable` continues to select the
latest final release until `v2.0.0` passes qualification and is published.

Selected-builder deployment is available for the bounded Debian static/Go
flow. Webhook CI/CD and GitHub Actions runners are separate systems; combining
all three on one host is the next build-system deliverable. See
[current build options](docs/services/build-systems.md) and the
[project roadmap](docs/plans/roadmap.md).

## Start here

New to Linux or Basaltwater? Follow [Try Basaltwater on a Debian
VM](docs/getting-started/getting-started.md) for a guided first setup, small feature experiments,
and checks that show whether each step worked.

On Linux, install the launcher on the machine that will manage your hosts:

```sh
sh -c '
  installer=$(mktemp) || exit
  cleanup() { rm -f -- "$installer"; }
  trap cleanup EXIT
  curl -fsSL --max-time 120 https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh -o "$installer" &&
  sh "$installer" "$@"
' sh
```

Copy the whole block; it works in fish, Bash, and Zsh. The explicit `sh`
invocation handles the temporary download and removes it on exit.
If `curl` is missing, use the [download prerequisites](docs/getting-started/installation.md#prerequisites).
Installing the launcher does not configure a target. Use `basaltw setup ...` for
remote hosts and `basaltw upgrade` to update the selected channel. The
[`basaltw refresh` command](docs/reference/command-line.md#refresh-this-machine) upgrades
Basaltwater and repeats this machine's last successful setup: use sudo on
Debian and the desktop account without sudo on CachyOS. The
[installation guide](docs/getting-started/installation.md) covers prerequisites, verification,
alternate download commands, local control-plane and desktop/RDP profiles,
channels, credentials, and recovery. The [documentation index](docs/README.md)
organizes detailed feature and operations guides.

Contributors should also read the
[contributor and coding-agent guide](docs/agents/contributing/README.md)
before changing the project.

### Windows coding workstation

Windows workstations are a lower-priority companion to CachyOS, primarily for
compatibility testing, CI, and native packaging. The experimental setup prepares
the current Windows 11 x64 account with Git, GitHub CLI, Codex, T3 Code desktop,
Node.js LTS, Python/uv, and shared agent skills.

Once the native profile is published to `main`, open PowerShell as the account
that will use these tools and paste the complete block:

```powershell
& {
    $installer = Join-Path $env:TEMP ("basaltwater-" + [guid]::NewGuid() + ".ps1")
    try {
        Invoke-WebRequest 'https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1' -UseBasicParsing -OutFile $installer -TimeoutSec 120 -ErrorAction Stop
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Profile agent_windows -Node -Python
        if ($LASTEXITCODE -ne 0) { throw "Windows workstation setup failed (exit code $LASTEXITCODE)." }
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
}
```

Add `-Plan` after `-Python` to preview selections. See the
[Windows workstation guide](docs/workstations/windows.md) for the current-checkout
command, provider login, verification, and recovery. CI runner registration is
a separate operation.

## Supported targets

Basaltwater officially supports Debian on:

- bare-metal systems;
- virtual machines, including Proxmox-provisioned VMs; and
- unprivileged Debian LXC containers on Proxmox.

The installer and setup preflight also recognize Ubuntu and Linux Mint as
best-effort Debian-compatible environments. Debian remains the only officially
supported distribution.

An experimental, limited [CachyOS + KDE coding profile](docs/workstations/cachyos.md) adds
agent tooling to an existing bare-metal workstation through local self-setup.
It preserves the human account and desktop, leaves OS updates to CachyOS/the
user, and does not support CachyOS VM/container provisioning or server setup.

For your first Codex setup with the native T3 Code desktop app, use the
[complete CachyOS installer example](docs/workstations/cachyos.md#install-with-t3-code-desktop).
The guide uses fish, CachyOS's default shell, and walks through selecting tools,
installing the launcher, provider login, and checking the first coding task.

The normal direct setup path uses `--machine auto`. Hosted Proxmox setup
defaults to a VM; select `--machine unprivileged` for the supported LXC path.
See [Machine types](docs/reference/machine-types.md) for capability and compatibility
details.

## Hardware sizing

Choose the row for the workload you will actually run. Each cell lists
**CPU cores / RAM / total OS disk**; for a VM, cores means virtual CPUs.
These are practical planning tiers for Debian, not installer-enforced limits
or benchmarks of every feature combination. Minimum means one user doing
limited work, recommended suits ordinary small deployments, and performance
adds room for concurrency, larger builds, and retained caches.

| Use case | Minimum | Recommended | Performance |
| --- | --- | --- | --- |
| Controller or light SSH/server utilities | 1 / 1 GB / 8 GB | 2 / 2 GB / 16 GB | 4 / 4 GB / 32 GB |
| Small web server or file-sharing service | 1 / 1 GB / 16 GB | 2 / 2 GB / 32 GB | 4 / 8 GB / 64 GB |
| Headless development with one language runtime or coding agent | 2 / 2 GB / 16 GB | 2 / 4 GB / 32 GB | 4 / 8 GB / 64 GB |
| Full XFCE/RDP desktop, T3, and occasional browser testing | **2 / 4 GB / 32 GB** | **4 / 8 GB / 64 GB** | **8 / 16 GB / 128 GB** |
| Godot development, frequent builds, or several concurrent agents | 2 / 4 GB / 32 GB¹ | 4 / 8 GB / 64 GB | 8+ / 16–32 GB / 128–256 GB |

¹ The minimum covers small 2D projects or one build/agent at a time; concurrent
agents and large projects need more memory and storage. CPU speed, SSD latency,
and graphics support also affect performance. Extra disk capacity alone does
not make a machine faster.

**A 32 GB full desktop is a valid limited-use target.** Keep only a few small
projects locally, limit browser tabs and parallel builds, and leave recurring
cleanup enabled. Budget roughly 4–6 GB of available space for temporary downloads
and updates; cleanup can discard rebuildable caches but cannot shrink your
projects or remove files used by active tools. Our maintained 32 GB test VM runs
the desktop and agent/browser tooling at about 23 GiB used after managed cleanup
(September 2026); this is an observed footprint, not a fresh-install size guarantee.

The disks above include Debian, selected tools, modest local work, and room for
updates. Add space for shared files, databases, container images, media, and
backups separately. Minimum-memory setups assume swap is available; allow about
2 GB for light servers and 4 GB for the desktop within the disk budget. Swap
helps with brief memory spikes but does not replace RAM for sustained work.
Coding-agent rows assume remote model providers; local model inference needs
its own RAM, accelerator, and model-storage budget.
These sizes exceed [Debian's base installation requirements](https://www.debian.org/releases/trixie/amd64/ch03s04.en.html)
to accommodate Basaltwater and useful work. For Proxmox hosts, add the resources
required by every guest and storage workload to the host's own requirements;
the table describes individual guests, not an entire virtualization host.

Start with 64 GB for a desktop when unsure; choose 128 GB for the performance
tier or substantial local work. The [beginner walkthrough](docs/getting-started/getting-started.md)
and [cleanup guide](docs/operations/maintenance.md#cleanup-and-state-safety) explain how to
try features and keep a small VM usable.

## Capabilities

| Area | Summary | Detailed guide |
| --- | --- | --- |
| Setup and CLI | Unified `setup`, `patch`, `shares`, saved-host operations, and utility commands | [Command-line reference](docs/reference/command-line.md) |
| Installation | User/system bootstrap, orchestration host prerequisites, and completion | [Installation](docs/getting-started/installation.md) |
| Firmware | Local fwupd inventory, dependency installation, and deliberate guarded updates | [Firmware](docs/operations/firmware.md) |
| Control planes | Local VM/container administration tools, SSH/rsync, diagnostics, and optional coding agents | [Installation](docs/getting-started/installation.md), [Agent systems](docs/agents/README.md), [Quick reference](docs/reference/quick-reference.md) |
| Servers | Security hardening, Nginx/SSL, Cloudflare tunnels, language runtimes, deployments, Gogs, and Antistatic | [CLI reference](docs/reference/command-line.md), [Gogs](docs/services/gogs.md), [Cloudflare tunnels](docs/services/cloudflare.md), [Antistatic](docs/services/antistatic.md) |
| Workstations | XFCE, i3, LXQt, RDP, browsers, and desktop tooling | [Workstations](docs/workstations/workstations.md), [XRDP](docs/workstations/xrdp.md), [CLI reference](docs/reference/command-line.md) |
| CachyOS coding | Codex on an existing KDE workstation; T3 desktop or a local/LAN web service, plus setup-time cache cleanup | [CachyOS](docs/workstations/cachyos.md) |
| Storage | Authenticated Samba shares, private Syncthing exchange, SMB mounts, rsync sync, par2 verification, and recurring operations | [Samba shares](docs/storage/samba-shares.md), [Managed Syncthing](docs/storage/syncthing.md), [Storage operations](docs/storage/storage-operations.md) |
| Deployments | Single-service deployments and `basaltwater.json` multi-component manifests | [Deployments](docs/services/deployments.md), [Deployment safety](docs/services/deployment-safety.md), [CI/CD](docs/services/cicd.md) |
| Build systems | Managed on-demand builders, verified artifact delivery, and separate webhook/Actions runner paths | [Build systems and examples](docs/services/build-systems.md) |
| Proxmox | Host discovery, VM/LXC provisioning, lifecycle, resource stats, boot ordering, snapshots, and rolling updates | [Proxmox workflows](docs/operations/proxmox.md) |
| Networking | Static addressing, internal HTTPS site/preview hosting, inventory, and read-only Proxmox firewall planning | [Internal web](docs/services/internal-web.md), [Networking](docs/operations/networking.md) |
| Sysadmin | SSH, transfers, health, services, logs, upgrades, and reachability | [Sysadmin shortcuts](docs/operations/sysadmin.md) |

## Operating policies

### Safety and state

Setup arguments are saved without passwords, reconstructed commands redact
secrets, and workspace credential files use restrictive permissions. Deployments
keep persistent application state outside release directories and create
verified backups where required. Read [Deployment safety](docs/services/deployment-safety.md)
for rollback and recovery behavior.

### Machine awareness

Steps detect the target machine type and skip capabilities that cannot safely
run in containers. Kernel, firewall, and desktop behavior is therefore
capability-aware rather than assumed. See [Machine types](docs/reference/machine-types.md).

### Security defaults

The security profile hardens SSH, the firewall, package updates, journald,
fail2ban, and service boundaries. Samba uses authenticated SMB3+, signing and
encryption, TCP 445 only, and validated configuration reloads. See the
[Samba guide](docs/storage/samba-shares.md) and [maintenance guide](docs/operations/maintenance.md).

### Conservative maintenance

On Debian profiles, automatic APT updates remain enabled, while language
ecosystem upgrades and release selection use conservative freshness and opt-in
policies. Cleanup uses bounded cache, log, journal, and temporary-artifact
policies. On APT hosts other than Proxmox, it purges packages APT marks unused,
including superseded kernels; Proxmox uses guarded kernel-only cleanup. It
expires recognized crash reports and returns unused filesystem blocks to
supported physical, virtual, and Proxmox storage, then checks block and inode
pressure across local mounts. See the
[recurring maintenance guide](docs/operations/maintenance.md).
CachyOS uses [bounded cleanup during setup](docs/workstations/cachyos.md#cleanup-during-setup)
and keeps system updates under the desktop user's control.

### Targeted updates

Use `patch` for general saved-configuration changes. Use a feature-specific
fast path when available—for example, `basaltw shares HOST` updates Samba
users, access, paths, and share declarations without running unrelated setup
work. See [Saved configuration operations](docs/operations/operations.md).

## Common commands

```bash
# Explore the command help and preview a local profile without applying it
basaltw --help
basaltw setup server_dev localhost "$USER" --node --dry-run

# List configurations saved by this account (empty before the first live setup)
basaltw list
```

Use the [beginner walkthrough](docs/getting-started/getting-started.md) to apply your first
setup, the [quick reference](docs/reference/quick-reference.md) for saved-host commands,
and the [documentation index](docs/README.md) for the feature map.

## Development checks

Run the default checks from a checkout. This includes building a wheel in a
temporary directory, installing it into an isolated virtual environment, and
smoke-testing all installed launchers outside the source tree:

```bash
make check
```

For the faster smoke-suite edit loop without the wheel build, use
`make check-fast`.

Routine continuous integration runs the suite in Debian Trixie on Python 3.13,
the interpreter shipped by that release. Version-tagged releases additionally
run the suite on the minimum supported Python version (3.11) and the latest
Python 3 version in the release policy (3.15). Expensive live tests remain
opt-in.

See [Saved configuration operations](docs/operations/operations.md) for test selectors and
[Proxmox workflows](docs/operations/proxmox.md) for live-host notes.

## License

Apache License 2.0
