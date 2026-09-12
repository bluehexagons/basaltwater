# infra-tools

Automated setup and operations for Debian control planes, servers, workstations,
and Proxmox guests. infra-tools applies repeatable, machine-aware configuration
over SSH, stores redacted setup state in a workspace, and provides targeted
operations for hosts that are already configured.

The documentation in this checkout describes the upcoming stable `v2.0.0`
release. Until that tag is published, the installer’s `dev` channel tracks
`main`; use `stable` when you need the latest published release.

## Start here

New to Linux or infra-tools? Follow [Try infra-tools on a Debian
VM](docs/GETTING_STARTED.md) for a guided first setup, small feature experiments,
and checks that show whether each step worked.

Install the launcher on the machine that will manage your hosts:

```bash
wget --timeout=20 --tries=2 -O "$HOME/.infra_tools-install.sh" https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh
sh "$HOME/.infra_tools-install.sh"
rm -f "$HOME/.infra_tools-install.sh"
```

Run each line in order and continue only if the previous command succeeds.
If `wget` is missing, use the [download prerequisites](docs/INSTALLATION.md#prerequisites).
Installing the launcher does not configure a target. Use `infra-tools setup ...` for
remote hosts and `infra-tools upgrade` to update the selected channel. The
[installation guide](docs/INSTALLATION.md) covers prerequisites, verification,
alternate download commands, local control-plane and desktop/RDP profiles,
channels, credentials, and recovery. The [documentation index](docs/README.md)
organizes detailed feature and operations guides.

Contributors should also read the
[contributor and coding-agent guide](docs/agents/contributing/README.md)
before changing the project.

## Supported targets

infra-tools officially supports Debian on:

- bare-metal systems;
- virtual machines, including Proxmox-provisioned VMs; and
- unprivileged Debian LXC containers on Proxmox.

The installer and setup preflight also recognize Ubuntu and Linux Mint as
best-effort Debian-compatible environments. Debian remains the only officially
supported distribution.

An experimental, limited [CachyOS + KDE coding profile](docs/CACHYOS.md) adds
agent tooling to an existing bare-metal workstation through local self-setup.
It preserves the human account and desktop, leaves OS updates to CachyOS/the
user, and does not support CachyOS VM/container provisioning or server setup.

The normal direct setup path uses `--machine auto`. Hosted Proxmox setup
defaults to a VM; select `--machine unprivileged` for the supported LXC path.
See [Machine types](docs/MACHINE_TYPES.md) for capability and compatibility
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
to accommodate infra-tools and useful work. For Proxmox hosts, add the resources
required by every guest and storage workload to the host's own requirements;
the table describes individual guests, not an entire virtualization host.

Start with 64 GB for a desktop when unsure; choose 128 GB for the performance
tier or substantial local work. The [beginner walkthrough](docs/GETTING_STARTED.md)
and [cleanup guide](docs/MAINTENANCE.md#cleanup-and-state-safety) explain how to
try features and keep a small VM usable.

## Capabilities

| Area | Summary | Detailed guide |
| --- | --- | --- |
| Setup and CLI | Unified `setup`, `patch`, `shares`, saved-host operations, and utility commands | [Command-line reference](docs/COMMAND_LINE.md) |
| Installation | User/system bootstrap, orchestration host prerequisites, and completion | [Installation](docs/INSTALLATION.md) |
| Firmware | Local fwupd inventory, dependency installation, and deliberate guarded updates | [Firmware](docs/FIRMWARE.md) |
| Control planes | Local VM/container administration tools, SSH/rsync, diagnostics, and optional coding agents | [Installation](docs/INSTALLATION.md), [Agent systems](docs/agents/README.md), [Quick reference](docs/QUICK_REFERENCE.md) |
| Servers | Security hardening, Nginx/SSL, Cloudflare tunnels, language runtimes, deployments, Gogs, and Antistatic | [CLI reference](docs/COMMAND_LINE.md), [Gogs](docs/GOGS.md), [Cloudflare tunnels](docs/CLOUDFLARE.md), [Antistatic](docs/ANTISTATIC.md) |
| Workstations | XFCE, i3, LXQt, RDP, browsers, and desktop tooling | [Workstations](docs/WORKSTATIONS.md), [XRDP](docs/XRDP.md), [CLI reference](docs/COMMAND_LINE.md) |
| CachyOS coding | Local agent tooling for an existing KDE workstation; optional localhost T3 | [CachyOS](docs/CACHYOS.md) |
| Storage | Authenticated Samba shares, private Syncthing exchange, SMB mounts, rsync sync, par2 verification, and recurring operations | [Samba shares](docs/SAMBA_SHARES.md), [Managed Syncthing](docs/SYNCTHING.md), [Storage operations](docs/STORAGE_OPERATIONS.md) |
| Deployments | Single-service deployments and `infra.json` multi-component manifests | [Deployments](docs/DEPLOYMENTS.md), [Deployment safety](docs/DEPLOYMENT_SAFETY.md), [CI/CD](docs/CICD.md) |
| Proxmox | Host discovery, VM/LXC provisioning, lifecycle, resource stats, boot ordering, snapshots, and rolling updates | [Proxmox workflows](docs/PROXMOX.md) |
| Networking | Static addressing, internal HTTPS site/preview hosting, inventory, and read-only Proxmox firewall planning | [Internal web](docs/INTERNAL_WEB.md), [Networking](docs/NETWORKING.md) |
| Sysadmin | SSH, transfers, health, services, logs, upgrades, and reachability | [Sysadmin shortcuts](docs/SYSADMIN.md) |

## Operating policies

### Safety and state

Setup arguments are saved without passwords, reconstructed commands redact
secrets, and workspace credential files use restrictive permissions. Deployments
keep persistent application state outside release directories and create
verified backups where required. Read [Deployment safety](docs/DEPLOYMENT_SAFETY.md)
for rollback and recovery behavior.

### Machine awareness

Steps detect the target machine type and skip capabilities that cannot safely
run in containers. Kernel, firewall, and desktop behavior is therefore
capability-aware rather than assumed. See [Machine types](docs/MACHINE_TYPES.md).

### Security defaults

The security profile hardens SSH, the firewall, package updates, journald,
fail2ban, and service boundaries. Samba uses authenticated SMB3+, signing and
encryption, TCP 445 only, and validated configuration reloads. See the
[Samba guide](docs/SAMBA_SHARES.md) and [maintenance guide](docs/MAINTENANCE.md).

### Conservative maintenance

Automatic APT updates remain enabled, while language ecosystem upgrades and
release selection use conservative freshness and opt-in policies. Cleanup uses
bounded cache, log, journal, and temporary-artifact policies and purges packages
APT marks unused, including superseded kernels. It expires recognized crash
reports and returns unused filesystem blocks to supported physical, virtual,
and Proxmox storage, then checks block and inode pressure across local mounts.
See the
[recurring maintenance guide](docs/MAINTENANCE.md).

### Targeted updates

Use `patch` for general saved-configuration changes. Use a feature-specific
fast path when available—for example, `infra-tools shares HOST` updates Samba
users, access, paths, and share declarations without running unrelated setup
work. See [Saved configuration operations](docs/OPERATIONS.md).

## Common commands

```bash
# Explore the command help and preview a local profile without applying it
infra-tools --help
infra-tools setup server_dev localhost "$USER" --node --dry-run

# List configurations saved by this account (empty before the first live setup)
infra-tools list
```

Use the [beginner walkthrough](docs/GETTING_STARTED.md) to apply your first
setup, the [quick reference](docs/QUICK_REFERENCE.md) for saved-host commands,
and the [documentation index](docs/README.md) for the feature map.

## Development checks

Run the default checks from a checkout. This includes building a wheel in a
temporary directory, installing it into an isolated virtual environment, and
smoke-testing both installed launchers outside the source tree:

```bash
make check
./run_tests.py --suite smoke
```

Routine continuous integration runs the suite in Debian Trixie on Python 3.13,
the interpreter shipped by that release. Version-tagged releases additionally
run the suite on the minimum supported Python version (3.10) and the latest
Python 3 version in the release policy (3.14). Expensive live tests remain
opt-in.

See [Saved configuration operations](docs/OPERATIONS.md) for test selectors and
[Proxmox workflows](docs/PROXMOX.md) for live-host notes.

## License

Apache License 2.0
