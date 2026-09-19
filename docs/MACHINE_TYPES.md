# Machine Types

The `--machine` flag specifies the environment type, enabling setup commands to
adapt configuration based on platform capabilities. The default is `auto`, so
the remote setup process makes a best-effort guess from the target runtime.

## Officially Supported Configurations

basaltwater officially supports Debian in these configurations:

- **Bare metal**: a physical Debian host, detected as `hardware`.
- **Virtual machine**: a Debian VM on Proxmox or a hosted VPS such as
  DigitalOcean, detected as `vm`.
- **Proxmox LXC**: an unprivileged Debian LXC container, detected as
  `unprivileged`.

The setup preflight also accepts Ubuntu and Linux Mint as Debian-compatible
best-effort distributions. Distribution-specific behavior outside the shared
APT and systemd interfaces is not part of the official support guarantee.

The detection is deliberately conservative. Use an explicit `--machine` value
when the runtime cannot identify itself reliably or when an existing setup has
a special requirement. OCI containers and privileged LXCs remain recognized
compatibility labels, but they are not part of the official support target.

## Types

| Type | Description | Example |
|------|-------------|---------|
| `auto` | Detect the target runtime and select a safe supported profile | Default |
| `unprivileged` | Unprivileged LXC container for lightweight compatibility use | Proxmox LXC |
| `vm` | Virtual machine | Proxmox VM or DigitalOcean VPS |
| `hardware` | Bare metal | Physical server |
| `privileged` | Privileged LXC container (compatibility label) | Proxmox LXC with passthrough |
| `oci` | OCI container | Docker, Podman |

## Capability Matrix

| Capability | unprivileged | vm | privileged | hardware | oci |
|------------|--------------|-----|------------|----------|-----|
| GPU/DRI device access | Explicit passthrough only | Device-dependent | Explicit passthrough only | Device-dependent | Explicit passthrough only |
| Kernel parameters | ❌ | ✅ | ✅ | ✅ | ❌ |
| Firewall (UFW) | ❌ | ✅ | ✅ | ✅ | ❌ |
| Swap configuration | ❌ | ✅ | ✅ | ✅ | ❌ |
| Time sync (chrony) | ❌ | ✅ | ✅ | ✅ | ❌ |
| System restart | ✅ | ✅ | ✅ | ✅ | ❌ |

## Behavior

### Unprivileged/OCI Containers
- **Skipped**: Swap, kernel hardening (sysctl), fail2ban
- **Host-managed**: Time synchronization; installed guest NTP daemons are
  disabled while the configured timezone is retained
- **Attempted with graceful failure**: Firewall (UFW), auto-updates
- **XRDP**: Managed glamor when a supported accessible render node exists;
  software fallback otherwise
- **Flatpak**: Warns and falls back to apt

### VM/Privileged/Hardware
- Kernel, firewall, swap, and time-sync controls are enabled where the runtime
  exposes them
- XRDP probes for a supported accessible DRM render node and enables glamor
  when available; an emulated VM display is not treated as acceleration by
  itself
- VM and hardware setups receive host-level AppArmor, auditd, and security
  monitoring; the AppArmor setup preserves package-declared profile modes and
  verifies Debian's restricted-user-namespace browser sandbox support.
  Privileged containers may inherit those controls from the host

## Default Resolution

- Direct setup and patch flows default to `auto` and resolve the machine type
  on the target before setup steps run.
- Step planning and execution use that current resolved type immediately, even
  though durable state is not finalized until setup succeeds. Runtime services
  outside setup continue to use the last successfully saved type.
- `auto` resolves to `hardware`, `vm`, or `unprivileged` for the officially
  supported configurations, and to `oci` when an OCI runtime is detected.
- Proxmox provisioning defaults to a VM because the guest does not exist
  yet; use `--machine unprivileged` to provision an LXC instead.
- `server_proxmox` also uses `auto` and normally resolves to `hardware` on the
  Proxmox host itself.

## Existing Saved Configurations

Saved configurations retain their explicit machine type. New commands use
`auto`, while an existing saved `vm`, `hardware`, or `unprivileged` selection
is preserved when patching or deploying. This avoids changing the behavior of
existing hosts unexpectedly.

For saved configurations, prefer `basaltw deploy <name-or-host>` or
`basaltw patch <host>` over retyping old commands. `basaltw cmd
<name-or-host>` prints an explicit override when the saved configuration is not
using the new `auto` default.

Proxmox host setup normally resolves to `hardware`, with automatic restarts
and forced restart deadlines disabled by default. It continues to report a
pending restart; set `--auto-restart` or a nonzero
`--auto-restart-force-days` only after planning guest downtime.

## Usage

```bash
# Auto-detect the target (the normal direct-setup path)
basaltw setup workstation_dev 192.168.1.10

# Force an officially supported Proxmox LXC profile
basaltw setup workstation_dev 192.168.1.10 --machine unprivileged

# Explicit machine types when needed
basaltw setup workstation_dev 192.168.1.10 --machine privileged
basaltw setup server_web 192.168.1.20 --machine hardware

# OCI container (limited features)
basaltw setup server_lite 192.168.1.30 --machine oci
```

## Provisioning a Proxmox VM

The regular setup flow defaults to a VM when `--provision-on` is present, so it
can create the VM through `qm` + cloud-init without `--machine vm`:

```bash
basaltw setup server_web 10.0.0.50 \
    --provision-on proxmox.lan \
    --memory 4G --cores 2 \
    --storage root local-lvm 32G
```

Use `--machine unprivileged` to stay on the LXC path.

Provisioned VMs automatically use a matching key from the registered Proxmox
host or the local `~/.ssh/id_ed25519`, `id_ecdsa`, or `id_rsa` identity. Use
`--key PATH` when the guest should use a different identity. The matching
public key is installed by cloud-init; `--provision-key` controls only SSH
access to the Proxmox node. The target is the guest IPv4 address: bare IPv4
uses `/24`, while `IPv4/PREFIX` selects another prefix.

For raw Proxmox addresses, storage shorthand falls back to `auto`. For
registered hosts, shorthand uses the saved/probed storage defaults first.

By default, the curated Debian cloud-image catalog
(`lib/cloud_images.py`) supplies the qcow2. Override with `--image`:

```bash
# Direct URL
... --image https://cloud.debian.org/.../debian-13-genericcloud-amd64.qcow2

# Pre-uploaded image on the Proxmox node
... --image local:import/my-debian.qcow2

# Downloaded image staged on a specific Proxmox storage
... --image-storage local
```

Refresh the catalog with `python3 scripts/update_cloud_images.py`. Add
`--pin-snapshot` to refresh entries to a specific upstream snapshot directory
and record its SHA-512. The checked-in catalog is pinned and verified by
default.

## State Persistence

Machine type is saved through `/opt/basaltwater/state/machine.json` only after
every requested setup mutation succeeds. The runtime path is a compatibility
link to durable root-owned storage under `/var/lib/basaltwater`, so refreshing
the uploaded basaltwater source does not discard release metadata, bundle
registrations, or maintenance state. Legacy state is migrated automatically.
An unfinished marker from the old non-persistent layout is retained once as
`setup-operation.pre-persistence.json` for diagnosis without blocking the
explicit migration rerun; subsequent operation markers remain durable and
retain their normal recovery guard.
The recalled setup is finalized at the same time in `setup.json`.

A real setup first creates `/opt/basaltwater/state/setup-operation.json`. It
records the current step and blocks another setup if execution is interrupted.
Ordinary failures mark it `recovery_required`. Rerunning the same system type,
machine type, and setup user resumes that operation and safely reapplies the
idempotent setup plan. A marker from a different setup identity, a corrupt
marker, or an `in_progress` marker still blocks setup for inspection; do not
remove it until the recorded operation is known to have stopped. Dry runs do
not create or change these state files.
