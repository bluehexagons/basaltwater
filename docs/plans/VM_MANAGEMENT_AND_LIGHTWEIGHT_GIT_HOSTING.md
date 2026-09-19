# Generic VM Management, Agent Interfaces, and Lightweight Git Hosting

Status: active. The provisioning-only portion of Lane A3, the IPv4 scope of
Lane B1, the first provider-neutral VM observation, resource statistics,
power-state lifecycle, and boot-ordering surface, confirmed destruction path,
explicit T3 Code desktop/headless setup, and generic path-backup composition
are implemented on `main`; broader VM mutation, HTTPS proxying, recovery, and
additional provider support remain subject to the gates below.
Reviewed against `main` and upstream T3 Code, Nginx, Samba, and Git LFS
documentation on 2026-08-21.

This project sharpens Basaltwater around the environments it is intended to
serve: small businesses running Debian systems on their own Proxmox hardware
or on manually created VPS instances. Proxmox is the only infrastructure
provider Basaltwater will provision or manage in the first release. A VPS from
DigitalOcean or another host remains an ordinary SSH setup target created and
destroyed outside Basaltwater.

The project has three related tracks:

1. move virtual-machine operations into a provider-neutral command surface,
   with a Proxmox implementation and additional practical Proxmox lifecycle
   operations;
2. make the existing minimal Gogs service a deliberate Git and Git LFS server
   for low-end systems, including explicit storage, health, and recovery
   contracts; and
3. let an agent VM install explicitly selected desktop or web interfaces,
   beginning with T3 Code, behind a small and secure interface, service,
   proxy, and access-policy contract.

The tracks share the same product constraints: prefer straightforward
open-source components, keep saved commands as the reusable declaration, and
avoid abstraction whose only purpose is a hypothetical provider or service.

## Current contract and historical scope

The August baseline and lane descriptions below retain earlier design decisions;
they are not the current CLI or T3 installation contract. The managed desktop
AppImage path has been retired. T3 now uses its native user-service lifecycle,
and managed HTTPS exposure is implemented. Use [T3 Code](../T3_CODE.md)
and the [CLI reference](../COMMAND_LINE.md) for supported commands. Storage and
recovery qualification remains open; the [release checklist](../BASALTWATER_RELEASE.md)
records the rename-specific remaining work. Product names and command prefixes
below use Basaltwater even where the described design has been superseded.

## Delivery status

| Lane | State | Next boundary |
| --- | --- | --- |
| A1: VM terminology and observation commands | Observation, stats, power lifecycle, autostart, and destroy slice implemented | `vm list/show/health/stats/status/snapshot list/backup list` emit versioned provider-neutral output; single-VM commands resolve saved local names; legacy Proxmox guest paths remain during migration |
| A2: existing VM mutations | Single-step power lifecycle, typed autostart, and destroy implemented; broader work dependency-gated | Complete durable operation markers and the staged mutation contract before restore, rollback, or multi-step mutation paths |
| A3: declarative VM data disks and guest mounts | Provisioning slice implemented | Live Proxmox validation, read-only mount status, then coordinated grow-only resize; existing-disk adoption, detach, and populated-path migration remain rejected |
| A4: clone and restore | Dependency-gated | Shared transaction and recovery contracts |
| B1: explicit and safe Gogs LFS | Implemented (IPv4) | Verified releases, local LFS layout, required mounts, safe hostless exposure, reusable health/status, and agent Git LFS setup are complete; IPv6 exposure remains explicitly deferred |
| B2: Gogs recovery | Dependency-gated | Shared recovery mechanism and authenticated restore smoke test |
| B3: Samba storage roles | Planned | Path-role enforcement and consistent archive publication |
| C1: T3 Code interfaces | Desktop, headless, and enrollment slices implemented | Desktop AppImage, Node-backed system service, loopback/LAN CIDR exposure, SSH pairing helper, protected enrollment portal, provider separation, and client-side project adding are implemented; managed remote lifecycle commands and optional pre-registration remain |
| C2: controlled web exposure | Planned | HTTPS/WebSocket reverse proxy for the T3 endpoint plus trusted-proxy, hostname, and source-policy integration |

## Product decisions

- A QEMU/KVM VM is the normal isolation boundary for agentic development and
  other disposable workloads. Docker and Dev Container orchestration are not
  part of this project.
- Proxmox remains the only managed virtualization provider. The command model
  should permit another provider later, but no generic cloud framework or
  DigitalOcean API integration will be built now.
- Existing, manually provisioned VPS instances continue through the normal
  `setup`, `patch`, host registry, SSH, and saved-command workflows. They do
  not appear as managed VMs unless a future provider explicitly owns them.
- Provider-neutral VM commands own guest lifecycle. Proxmox-specific commands
  own nodes, clusters, storage, placement, maintenance, and notifications.
- Gogs remains the current Git service because its single binary, SQLite
  support, and low resource needs fit the target hardware. Forgejo, Tangled,
  and other Git services are deferred until there is a concrete second
  implementation to inform a shared service contract.
- Gogs's built-in local Git LFS server is the supported LFS backend. This
  project does not introduce a separate LFS daemon or object-storage service.
- Gogs LFS object transfer is supported, but LFS file locking is not. LFS uses
  HTTP/HTTPS authentication even when ordinary Git uses an SSH remote; setup
  and documentation must not imply otherwise.
- The `v2.0.0` release intentionally breaks compatibility with unreleased
  development commands, configuration shapes, and internal APIs. A redesign
  must update saved-command rendering, documentation, completions, callers,
  and tests in the same change, then remove the superseded path instead of
  carrying aliases, adapters, deprecated fields, or dual dispatch.
- Agent capabilities are explicit and have separate categories. `--agent-tool`
  selects provider CLIs such as `gh`, `codex`, or `opencode`;
  `--desktop-interface t3code` selects the T3 Code desktop application; and
  `--web-interface t3code` selects its headless web service. None of these
  selections implies the others, a general agent suite, or unselected
  provider CLIs.
- Remove `t3code` from `--agent-tool`, but retain the current AppImage path as
  a dedicated desktop-interface adapter. T3 Code is an interface that drives
  selected provider CLIs, not a provider agent itself. Desktop installation
  must be explicit, require a suitable desktop session, and never create a
  web listener or background service as a side effect. The desktop and web
  adapters may share a verified release-artifact registry without sharing
  launchers, service state, or exposure policy.
- Samba is an optional storage and operator-access integration, not a Git
  transport, Git authorization layer, or general-purpose application data
  backend. Git and Git LFS continue to use their normal HTTPS Git service
  endpoints. Live Gogs data and active agent worktrees remain local in the
  first storage slice; Samba is used for explicitly scoped assets, import and
  export, and consistent backup archives.
- Proxmox storage-pool allocation and guest filesystem mounting are separate
  concerns. A provisioned QEMU VM may receive additional virtual disks backed
  by a selected Proxmox storage pool, then format and mount them inside the
  guest for Gogs, Git LFS, agent workspaces, `/home`, or another approved
  application path. This is guest-local block storage, not an in-guest CIFS
  workaround.
- The first storage API should use repeatable logical declarations such as
  `--storage git-data ts1-storage 128G` and
  `--storage-mount git-data /srv/gogs ext4`. Disk names, filesystem identity,
  mount paths, and ownership are persisted as one versioned declaration. A
  missing required mount must stop dependent setup rather than allowing an
  empty directory on the root filesystem to receive data.
- Common paths are supported by policy, not by silently mounting over them.
  Tool-owned empty paths such as `/srv/gogs` and
  `/srv/agent-workspace` are supported, as is an empty `/home` during new VM
  provisioning. Provisioning defers setup-user creation until `/home` is
  mounted. A populated path such as `/home` still requires an explicit
  migration policy, backup or snapshot checks, and a verified copy before
  cutover. Broad system paths such as `/`, `/etc`, `/usr`, and `/boot` are
  outside the storage contract.
- `--backup SOURCE DESTINATION INTERVAL` is a generic path-mirror declaration,
  not a Samba feature. It composes with the existing root-owned rsync and
  par2 storage-operations service; a destination may be a local path, a
  separately managed mount, or a Proxmox-provisioned non-root disk. It does
  not claim application-consistent snapshots or replace an off-host copy.
- The first implementation is provisioning-only: it allocates blank data
  disks for a newly created QEMU VM and mounts them at empty, tool-owned
  paths. Adoption of an existing disk, detach, coordinated provider/guest
  resize, and populated-path migration remain later mutation workflows. This
  keeps the initial formatting decision tied to a disk allocated in the same
  provisioning operation and avoids implying that `/home` migration is safe
  before the transactional recovery dependency is available.
- Nginx HTTP Basic Auth protects the separate device-enrollment portal with a
  root-owned password file. It never replaces T3 Code's native pairing/session
  authentication and never stores a raw password in a command, saved
  configuration, process environment, or unit file. Plain HTTP is accepted
  only for an explicitly allowlisted trusted private network and is documented
  as observable; other networks require a separately managed HTTPS endpoint.
- T3 Code is an acceptable first interface because its source is available
  under the MIT license. Each later interface still needs its own license,
  release, runtime, and update-path review before being added.
- Prefer small Basaltwater modules for deterministic parsing, validation,
  rendering, state inspection, and health checks. Continue to use mature
  open-source components for security-sensitive or protocol-heavy work such
  as TLS, WebSocket proxying, process supervision, Git, and SQLite.

### Breaking-release boundary

Unreleased development interfaces are not migration inputs. The release
should document the new command equivalents, but it must not accept the old
Proxmox guest paths, old raw reconfiguration arguments, retired agent-suite
flags, duplicate shell commands, or stale setup fields. Internal
`container_*` APIs should be renamed or removed in the same changes that
update their callers.

Persisted records need an explicit schema version. If a development build
wrote an incompatible host or setup record, the new release should refuse to
mutate it and print the short re-registration or command-regeneration steps.
It must not silently reinterpret old fields, destroy the record, or retain a
permanent migration branch. Saved setup commands should be regenerated under
the new parser before upgrade.

Tests should assert that removed parsers, fields, functions, and shell
commands are absent. Migration notes are documentation, not a compatibility
layer.

## Current implementation baseline (2026-08-20)

### Setup and VPS targets

`basaltw setup` already treats an existing Debian host as an SSH target.
That is the complete intended integration for a manually created VPS: the
operator creates the instance, networking, and provider firewall, then runs
the same setup command used for a physical host or an existing VM.

The setup flow can also create a Proxmox QEMU VM or LXC guest through
`--provision-on`. QEMU provisioning resolves and verifies a cloud image,
imports it into the selected storage, configures cloud-init, installs the SSH
key and QEMU guest agent, starts the guest, and hands the resulting address to
the normal remote setup engine. This composition should be retained rather
than creating a second target-configuration engine under the management CLI.

### Proxmox management

The current `basaltw proxmox` tree combines two different concerns:

- host and cluster operations such as registration, discovery, audit,
  rolling update, placement, rebalance, storage cleanup, and notification
  configuration; and
- guest operations such as list, status, power control, health, configuration,
  resource modification, disk resize, snapshots, backups, migration, unlock,
  and destruction.

Most basic guest mutations already exist and should be moved rather than
rewritten. Important remaining gaps include a stable machine-readable guest
inventory, graceful reboot, clone, backup restore, clearer task waiting and
timeouts, and a consistent capability/error contract when an operation is not
available for a guest type or storage backend.

The implementation currently calls both QEMU VMs and LXC containers
"containers" in several internal names. That terminology should be corrected
as part of the CLI split. Generic VM operations must not silently claim that
all Proxmox LXC behavior is portable to a future VM provider.

### VM disks and guest filesystems

At the initial review, provisioning had only a root-disk contract. Lane A3 now
accepts named non-root QEMU disk declarations and matching empty-path mount
declarations for a newly provisioned VM. Provider setup resolves an
image-capable Proxmox pool, preflights aggregate reported free capacity,
attaches each disk on a stable VirtIO-SCSI slot and serial, verifies the
identity before boot, and preserves the resolved declaration in saved setup
state. `--image-storage` remains only the Proxmox-side staging location for a
downloaded image; it is not guest data storage.

The target-side storage step identifies the declared serial and capacity,
formats only a confirmed blank device, creates one GPT partition and an ext4
or XFS filesystem, mounts it through a required UUID-based systemd unit, and
stores versioned state plus a health marker on the mounted filesystem. Gogs
and agent repository setup verify that marker and active UUID before writing.
`--agent-workspace` can place clones on the mounted disk. Existing-disk
adoption, LXC data disks, attach-only disks, detach, resize, and populated-path
migration remain deferred and rejected by validation. An empty `/home` is
supported only during new VM provisioning; cloud-init defers setup-user
creation until that mount is active.

### Planned VM storage contract

The redesign should distinguish three layers and report them separately:

1. **Provider storage** is the Proxmox pool and virtual disk allocation. It
   answers where the disk is allocated and how large it is.
2. **Guest storage** is the device identity, partition, filesystem, UUID, and
   mount unit inside the VM. It answers whether the expected disk is present
   and mounted.
3. **Application placement** is the path and owner used by Gogs, Git LFS, or
   an agent workspace. It must only run after guest storage verification.

The proposed QEMU setup shape is intentionally small and declarative:

```text
--storage root ts1-storage 32G
--storage git-data ts1-storage 128G
--storage-mount git-data /srv/gogs ext4
--storage agent-data ts1-storage 128G
--storage-mount agent-data /srv/agent-workspace ext4
--agent-workspace /srv/agent-workspace
```

`root` is reserved for the boot disk. Every other logical disk name is
unique, maps to one selected Proxmox storage pool and size, and has one
explicit guest mount declaration in the first slice. `ext4` and `xfs` are the
initial filesystem choices. Mount declarations also carry a small policy such
as `empty` for a tool-owned directory or `migrate` for a populated directory;
the full shape is `--storage-mount NAME PATH FILESYSTEM POLICY`, with
`empty` as the only policy accepted by the first implementation. The syntax
reserves a later policy position, but `migrate` must remain rejected until the
separate populated-path workflow is implemented. Mount declarations must not
become an unvalidated collection of arbitrary `mkfs`, mount, or `fstab`
options. Attach-only disks can be added later for an operator-managed
filesystem, but the initial automated path should not present an attached
unmounted disk as a successfully prepared application volume.

The persisted storage record should include a schema version, logical name,
Proxmox pool, requested size, bus slot, generated serial, guest device or
partition, filesystem type, UUID, mount path, and policy. Application
ownership stays with the consuming setup step: generic mounts remain
root-owned, the agent workspace applies the setup user's ownership after
mount verification, and Gogs applies its service user's ownership after that
account exists. The record contains no credentials. It is the basis for
idempotent reconciliation and for reporting whether a clone, backup, restore,
or resize preserved the complete storage contract.

The implementation should attach additional disks as stable VirtIO-SCSI
slots (`scsi1`, `scsi2`, and so on) with a generated logical serial. Inside
the guest, the storage step must verify the expected serial and capacity,
create one GPT partition on a confirmed blank disk, use `/dev/disk/by-id` or
the resulting filesystem UUID rather than `/dev/sdb`, and persist a native
systemd mount unit or an equivalent UUID-based mount. A
required mount must not use `nofail`: if the disk is absent or the mount fails,
dependent Gogs, agent, and repository setup must stop before writing data.
The health marker used to prove a mount is active must live on the mounted
filesystem, so a root-directory fallback cannot pass the check.

Formatting is allowed only after the target device is positively identified
and confirmed blank: no partition table, filesystem signature, mount, or
unexpected contents. A nonblank or unexpected device must fail without
formatting; a `wipefs` probe may identify signatures, but automated setup must
not blindly wipe them. Re-running setup with the expected UUID is idempotent
and reuses the filesystem; resizing is grow-only and must coordinate Proxmox
disk resize, guest partition growth, and filesystem growth. Shrinking,
implicit reformatting, in-guest LVM/RAID, and automatic disk removal are out
of scope.

For `/srv/gogs`, `/srv/agent-workspace`, and similar tool-owned paths, the
storage step creates and verifies the mount point before the consuming setup
step applies ownership. For `/home`, the later explicit `migrate` policy must
stage the filesystem, copy existing
contents while preserving ownership, permissions, and extended attributes,
verify the copy, arrange the cutover during a maintenance window, and only
then create credentials and repositories. Existing VMs require a current
backup or snapshot and explicit operator confirmation. A failed migration must
leave the original path usable and must never hide populated data beneath an
unverified mount.

On a manually provisioned VPS, Basaltwater does not attach provider volumes.
The operator or control system must attach the volume, after which a future
generic guest-storage path may apply the same identity and mount checks. The
first implementation should focus on Proxmox attachment plus guest setup and
should not pretend that a VPS provider volume was managed by Basaltwater.

Cloud-init's disk and filesystem modules may be used for first-boot mechanics,
but they must not become a second source of truth. The persisted Basaltwater
declaration owns the logical disk, expected identity, mount policy, and health
result; the target-side step must still verify the result after SSH readiness
and before application setup. Native systemd mount units are preferred for
reconciliation because their device dependencies and status are directly
observable, while generated fstab entries remain an acceptable implementation
detail if they use the same UUID and fail-closed requirements.

### Gogs and Git LFS

`--gogs` currently installs a native Gogs release, Git, Git LFS, OpenSSH,
SQLite, a restricted `git` user, a hardened systemd unit, optional nginx/TLS
integration, an initial administrator, and a managed weekly release updater.
The selected data directory contains the database and repositories.

Gogs serves Git LFS through its own HTTP endpoints and supports a local LFS
object path. The generated `app.ini` now selects local LFS storage explicitly,
places completed objects and temporary uploads below the selected Gogs data
root, creates those directories, and rejects symlinked managed data paths.
When that root is a declared VM data mount, setup verifies the mounted marker
and UUID before creating Gogs data. Reusable capacity, update-age, SQLite,
filesystem, and local endpoint-configuration reporting is implemented. The
complete recovery workflow and authenticated upload/download smoke coverage
remain open.

### Agent web interfaces

The T3 Code implementation keeps the desktop AppImage and launcher as an
explicit desktop-interface path. Its separate web-interface adapter installs
and supervises the headless CLI used by `t3 serve`, and its optional enrollment
adapter exposes only credential issuance through Nginx. Neither interface path
is an alias for the other, and the enrollment listener is not a reverse proxy
for T3's HTTP/WebSocket endpoint.

T3 Code's supported remote model already provides a headless server, one-time
owner pairing credentials, authenticated sessions, session revocation, and a
Linux systemd user service. Its server also serves the matching web client and
uses WebSockets. Basaltwater should integrate those upstream contracts rather
than creating another T3 login database or maintaining a parallel T3 process
launcher.

## Goals

- Give operators one clear VM command tree whose common vocabulary does not
  expose Proxmox implementation names.
- Preserve one setup engine: provider provisioning creates the target, then
  normal SSH setup configures it.
- Keep Proxmox node and cluster administration explicit and make that command
  tree easier to navigate.
- Define the smallest provider contract supported by working Proxmox code,
  with explicit capability reporting instead of speculative methods.
- Add the missing Proxmox lifecycle operations most useful to a small
  operator: reboot, clone, backup restore, richer inventory, and dependable
  wait/verification behavior.
- Make destructive VM operations previewable, confirmable, and compatible
  with the transactional and recovery work already on the roadmap.
- Make Gogs's Git LFS storage layout explicit and suitable for a dedicated
  low-cost data disk.
- Let QEMU provisioning attach one or more additional Proxmox-backed disks
  and make their guest filesystems and mount points explicit before Git, LFS,
  Gogs, or agent-workspace setup runs.
- Keep data-disk setup idempotent and fail-closed: identify devices by stable
  metadata, format only a confirmed blank device, mount by UUID or another
  stable identity, and never silently fall back to the root filesystem.
- Allow an explicit agent-workspace root so repository clones can use a
  dedicated data disk without requiring a `/home` migration.
- Include repositories, SQLite state, LFS objects, configuration secrets, and
  required metadata in one documented Gogs recovery contract.
- Ensure agent workspaces can explicitly install Git LFS before cloning normal
  repository declarations that use it.
- Keep desktop T3 Code available for desktop/RDP use cases without making it a
  prerequisite for, or side effect of, the headless web service.
- Define how existing Samba server and client support can provide scoped agent
  assets, Git/Gogs backup storage, and import/export without placing live Git
  metadata or SQLite on an unsafe shared filesystem.
- Let operators select one or more explicit agent web interfaces while keeping
  each backend bound to loopback and each public exposure explicit.
- Make a loopback service plus an SSH tunnel the safe zero-configuration web
  interface, with optional nginx hostname/TLS and CIDR filtering.
- Keep the implemented Basic Auth enrollment gate separate from T3's native
  browser/WebSocket session endpoint; evaluate any later edge gate only with
  TLS and verified desktop/mobile client compatibility.
- Preserve each tool's native authentication and access-revocation model,
  while ensuring startup output and normal logs do not expose pairing secrets.
- Ensure web services see the same explicit agent binaries, credentials, and
  workspace paths as the configured login user without copying credentials
  into unit files.
- Keep text output concise while providing stable JSON for inventory, audit,
  and external control systems.

## Non-goals

- Provisioning, resizing, snapshotting, destroying, or billing DigitalOcean
  instances.
- Supporting AWS, Azure, Google Cloud, Kubernetes, Terraform, Pulumi, or a
  general cloud-provider plugin system.
- Managing arbitrary QEMU/libvirt hosts outside Proxmox in the first release.
- Replacing VM isolation with Docker, Podman, or Dev Containers.
- Treating a saved profile file as the source of truth; saved setup commands
  remain the reusable declaration.
- Adding Forgejo, Tangled, Gitea, GitLab, or a generic Git-service framework in
  this project.
- Adding an external Git LFS server, S3-compatible storage abstraction, or
  distributed object store.
- Making Samba a Git protocol, a replacement for Gogs's HTTPS LFS endpoint, or
  a live writable home for Gogs's SQLite database, repositories, or LFS
  objects in the first release.
- Emulating Git LFS file locking that Gogs does not implement.
- Making every Proxmox API or `qm` option available through a raw pass-through
  interface.
- Backing up disposable agent VMs as a substitute for committing or exporting
  unfinished Git work.
- Treating an agent web interface as a general-purpose application hosting
  platform, remote desktop, or automatically exposed development-server
  preview.
- Providing arbitrary in-guest repartitioning or silently migrating populated
  system directories. `/home` migration is an explicit, separately verified
  storage policy; `/`, `/etc`, `/usr`, and `/boot` are not generic mount
  targets.
- Adding a generic username/password database, OAuth provider, identity proxy,
  Caddy, Traefik, or a container-based web-interface stack in the first slice.
- Installing a desktop T3 Code application on a headless target, or making
  desktop installation imply provider credentials, RDP, browser automation,
  or a web listener.

## Track A: provider-neutral VM commands

### Resource model

A managed VM reference consists of:

- a registered provider host, such as `ts1`;
- an opaque provider resource ID, such as Proxmox VMID `101`;
- the provider recorded by that host registration; and
- observed fields such as name, state, address, CPU, memory, disks, lock, and
  provider-specific kind.

The CLI accepts `HOST ID` as separate arguments. For a Basaltwater-provisioned
QEMU VM, single-resource observation, power-state lifecycle, autostart, and
`destroy` commands also accept the exact saved local friendly name or saved IP
address. That shorthand resolves the provider host from the saved setup and
requires the observed Proxmox name and configured IPv4 address to match before
use; tags are deliberately not VM identifiers. The provider adapter validates
the ID; the common parser must not assume every future provider uses an
integer. Provider hosts must declare their provider explicitly when persisted.
Incompatible records written by development builds should be rejected with a
command to re-register the Proxmox host under `provider = proxmox`; they should
not be interpreted through a permanent legacy branch.

Observed VM results should use typed records and a versioned JSON schema. The
common fields must remain small. Proxmox-only data may be included under a
clearly named provider section instead of expanding the common model whenever
`qm` exposes another field.

### Proposed VM command tree

Use positional `HOST` consistently and group subordinate resources under one
noun. Do not retain flat aliases for the development-era command names:

```text
basaltw vm list HOST [--json]
basaltw vm show TARGET [ID] [--json]
basaltw vm health TARGET [ID] [--json]
basaltw vm stats TARGET [ID] [--json]
basaltw vm status TARGET [ID] [--json]
basaltw vm start TARGET [ID] [--json]
basaltw vm shutdown TARGET [ID] [--timeout SECONDS] [--json]
basaltw vm stop TARGET [ID] [--json]
basaltw vm reboot TARGET [ID] [--timeout SECONDS] [--json]
basaltw vm pause TARGET [ID] [--json]
basaltw vm resume TARGET [ID] [--json]
basaltw vm autostart TARGET [ID] [--enable|--disable] [--order N] [--start-delay SECONDS] [--shutdown-timeout SECONDS] [--json]
basaltw vm modify HOST ID [--cores N] [--memory SIZE] [--balloon-min SIZE] [--dry-run]
basaltw vm disk list HOST ID [--json]
basaltw vm disk attach HOST ID NAME --storage POOL --size SIZE [--dry-run]
basaltw vm disk resize HOST ID NAME SIZE [--dry-run]
basaltw vm disk detach HOST ID NAME [--yes] [--dry-run]
basaltw vm mount status HOST ID [--json]
basaltw vm snapshot list TARGET [ID] [--json]
basaltw vm snapshot create HOST ID NAME [--description TEXT] [--dry-run]
basaltw vm snapshot rollback HOST ID NAME [--dry-run]
basaltw vm snapshot delete HOST ID NAME [--dry-run]
basaltw vm backup list TARGET [ID] [--json]
basaltw vm backup create HOST ID [options]
basaltw vm backup restore HOST BACKUP --id ID [options]
basaltw vm clone HOST ID --name NAME [options]
basaltw vm migrate HOST ID --to DESTINATION [options]
basaltw vm unlock HOST ID [--dry-run]
basaltw vm destroy TARGET [ID] [--force] [--yes]
```

Here `TARGET` is either a registered provider `HOST` followed by `ID`, or an
exact saved local VM name with `ID` omitted. The shorthand is intentionally
limited to setup-backed QEMU VMs; explicit `HOST ID` remains the route for an
otherwise unmanaged QEMU VM.

The `vm disk` commands manage provider-side virtual hardware and use the
logical disk name recorded in the VM declaration; they must report the
Proxmox volume, bus slot, serial, size, and whether a guest mount is known.
Guest formatting and mounting belong to the setup engine, not an opaque
provider command. `vm mount status` observes the guest through the QEMU agent
or SSH and reports the expected UUID, mount path, and fail-closed health state.
Detaching a disk is destructive and must refuse to remove a disk that is
declared as required application storage unless the operator explicitly
removes or overrides that declaration.

Do not add `vm create` in the first release. Provisioning continues to compose
through `basaltw setup ... --provision-on HOST`, which calls the provider
and then the one normal OS configuration engine. A later standalone creation
command would have to call those same provider functions and must not grow a
second setup path.

Every mutating command should offer `--dry-run` when a meaningful concrete
provider command can be rendered. Read-only commands should support `--json`
through one stable result format that includes `schema_version`. Unsupported
operations must fail before mutation and identify the missing provider,
guest-kind, storage, or guest-agent capability. Text and JSON output should
share one typed result; machine output must include a stable error code and,
for asynchronous Proxmox work, the provider task identifier and final task
state.

### Proxmox command boundary

After guest operations move, `basaltw proxmox` remains the explicit
administrative namespace for the provider itself:

```text
basaltw proxmox hosts list
basaltw proxmox hosts add ...
basaltw proxmox hosts remove ...
basaltw proxmox hosts probe ...
basaltw proxmox cluster discover ...
basaltw proxmox cluster audit ...
basaltw proxmox cluster update ...
basaltw proxmox cluster top ...
basaltw proxmox plan place ...
basaltw proxmox plan rebalance ...
basaltw proxmox storage list ...
basaltw proxmox storage clean ...
basaltw proxmox notifications ...
```

The exact grouping can be adjusted while implementing parser tests, but node
and cluster commands must not be duplicated under `vm`. Remove the interactive
Proxmox shell; saved commands and the normal parser are already the reusable
interface, and a second command language adds no required capability.

### QEMU and LXC boundary

QEMU VMs define the portable VM contract. Existing LXC provisioning and
lifecycle remain supported as intentional Proxmox features under an explicit
`proxmox lxc` subtree; they do not appear under generic `vm` commands. This
keeps future VM-provider semantics honest and matches the QEMU-first product
direction. Shared implementation helpers can still use a neutral internal
`Guest` name where `qm` and `pct` genuinely have the same contract.

### Provider implementation boundary

Do not begin with a comprehensive abstract base class. Extract typed provider
operations from existing Proxmox functions one command family at a time. The
initial registry needs only:

- provider identity and host resolution;
- read-only inventory and capability discovery;
- the lifecycle operations exposed by the common CLI; and
- a shared result/error vocabulary.

A future provider may implement only part of the command set. Capability
discovery and a clear unsupported result are preferable to no-op behavior or
provider-specific flags accepted by the common parser and ignored.

Provider credentials belong to the control system and must never be copied to
a managed guest. Proxmox continues to use its registered SSH identity. An
ordinary VPS setup target has no provider credential and cannot receive VM
lifecycle commands.

### Additional Proxmox capabilities

The following additions are in scope after the CLI split reuses all existing
behavior:

1. **Inventory and observation**: stable JSON, filters by name/status/type,
   guest-agent address discovery, current task/lock visibility, and consistent
   exit codes.
2. **Graceful lifecycle**: reboot, configurable shutdown/start waits, explicit
   immediate stop, and post-operation state verification.
3. **Clone**: full clone first, with validated target VMID, name, storage,
   address/cloud-init changes, and collision checks. Linked clones remain
   deferred until their template and storage dependencies are designed.
4. **Restore**: select one listed backup, validate target storage and VMID,
   restore without overwriting an existing guest, then optionally boot into an
   isolated network for verification.
5. **Snapshot safety**: report unsupported storage before creation, show the
   current snapshot state, and make rollback/delete confirmation proportional
   to data loss.
6. **Configuration**: retain typed CPU, memory, balloon, boot, and disk options.
   Remove the current arbitrary `--set KEY=VALUE` surface rather than
   presenting raw `qm`/`pct` arguments as provider-neutral.
7. **Declarative data disks**: let QEMU provisioning allocate named
   Proxmox-backed disks, attach them on stable VirtIO-SCSI slots, and pass a
   versioned guest mount declaration to the normal setup engine. Preflight
   pool capacity and content type before allocation; verify serial, size,
   filesystem, UUID, mount activation, and ownership inside the guest before
   dependent services or repository clones run. Keep `/home` migration
   explicitly gated and refuse unsafe or ambiguous devices.
8. **Disk lifecycle**: expose observation, grow-only resize, backup inclusion,
   clone/restore metadata, and explicit detach behavior for every declared
   data disk. A missing required disk or mount must be a visible failed state,
   not a successful VM with writes redirected to root storage.
9. **Recovery and unfinished work**: before a managed agent VM is destroyed or
   rebuilt, optionally inspect declared repositories for dirty work and local
   commits not present on a remote. Archiving patches or Git bundles is a later
   agent-workspace slice, not a reason to back up the entire VM.

An enabled workspace pre-destroy check is fail-closed: an unreachable guest,
unknown repository state, dirty files, or unpushed commits block destruction
unless the operator explicitly overrides the check. Confirmation output names
the provider host, VM ID, observed VM name, and any failed preflight; `--yes`
suppresses the prompt for automation but does not bypass capability or
workspace checks. `--force` is the explicit bypass and must be recorded in the
operation result.

Restore, rollback, clone, and destructive configuration changes must reuse the
roadmap's transaction markers and recovery conventions. They should not create
a VM-specific transaction framework.

## Track B: minimal Gogs and Git LFS hosting

### Configuration decisions

Keep the public service selection explicit as `--gogs` for now. Renaming it to
`--git-service gogs` before a second service exists would add a generic layer
without reducing implementation complexity. When Forgejo or Tangled becomes
real work, the common behavior should be extracted from two tested services
rather than predicted here.

The generated Gogs configuration should explicitly include:

```ini
[lfs]
STORAGE = local
OBJECTS_PATH = /selected/data/path/data/lfs-objects
OBJECTS_TEMP_PATH = /selected/data/path/data/tmp/lfs-objects
```

The object and temporary paths must be absolute after rendering, owned by the
`git` account, protected from symlink traversal, and created before Gogs
starts. The default remains inside the selected Gogs data path so one mounted
data disk contains repositories, SQLite, attachments, and LFS objects.

The intended dedicated-disk composition is:

```text
--storage root ts1-storage 32G
--storage git-data ts1-storage 128G
--storage-mount git-data /srv/gogs ext4
--gogs :3000 /srv/gogs
```

The Gogs setup step must verify that `/srv/gogs` is the declared mounted
filesystem before it creates the database, repositories, or LFS paths. A
configured path that resolves to the root filesystem, a missing mount, or a
writable CIFS mount is a setup error. Capacity and backup observations must
identify the filesystem containing the data root rather than reporting only
the VM's root-disk capacity.

Do not add a separate LFS path in the first release. A later need to place
large objects on another mounted filesystem would require an explicit absolute
path and a backup/recovery inventory that treats it as a second data root.

### Exposure and release safety

Do not preserve the current hostless behavior that can bind Gogs to
`0.0.0.0` while UFW is absent. A hostname continues to mean nginx plus the
selected TLS or Cloudflare flow and requires `--ssl` or `--cloudflare`; Git and
web credentials must not cross a public plaintext connection. Without a
hostname, Gogs binds to loopback and setup prints an SSH tunnel command unless
one or more private sources are explicitly declared:

```text
--gogs :3000 /srv/gogs-data
--gogs-source 192.168.0.0/24
--gogs-source 10.0.0.0/8
```

`--gogs-source` is repeatable and valid only for hostless mode. Before Gogs
binds a non-loopback address, Basaltwater must install and verify matching UFW
rules; if it cannot enforce them, setup fails without exposing the service.
The first implementation accepts only non-global IPv4 sources because Gogs is
rendered with an explicit IPv4 listener. IPv6 exposure remains deferred until
the listener and firewall behavior can be tested together. Reconciliation
installs replacement allow rules before removing old ones. A source list is
access control, not transport encryption; use
hostless HTTP only on a trusted or encrypted private network and prefer the SSH
tunnel for web login. Hostname mode remains intentionally public unless
another existing firewall policy restricts it.

A loopback-only Gogs service has no generally reachable HTTP endpoint for LFS
clients. Setup must label remote LFS transfer unavailable in that mode unless
the client uses a persistent SSH tunnel and matching per-repository LFS URL.
For routine LFS use, require either the HTTPS hostname mode or a validated
private source listener; successful Git-over-SSH alone is not an LFS readiness
check.

Release installation must verify the selected Gogs asset with an upstream
digest or signature before activation, then run the binary as the restricted
`git` user. Downloading over HTTPS and validating the release URL are not by
themselves artifact verification. A previously verified release is restored
when activation, startup, or health checks fail; if there is no verified
rollback target, the service remains stopped. The active release path and
saved digest must agree before an idempotent update is accepted.

### Capacity and low-end operation

The default service remains one Gogs process with SQLite and local files. Do
not add PostgreSQL, Redis, an object store, or container orchestration to the
baseline.

Add read-only checks for:

- service and SQLite health;
- repository and LFS directory ownership and writability;
- free bytes and inodes on every data filesystem;
- total repository, LFS object, attachment, and log usage;
- failed or stale Gogs update jobs; and
- nginx upload limits that would reject the documented LFS workload.

Capacity warnings should report facts and configurable thresholds; they must
not automatically delete repositories or LFS objects. Gogs remains the owner
of object reachability, so a generic filesystem cleanup must never prune the
LFS directory. When Cloudflare or another operator-managed edge sits in front
of Gogs, report that its account-specific body-size and timeout limits are
outside Basaltwater's control; a small health upload does not prove that the
largest intended LFS object will pass that edge.

### Backup and restore contract

A recoverable Gogs instance includes:

- `custom/conf/app.ini` and the persisted secret needed to reproduce it;
- the SQLite database;
- all Git repositories and server-managed hooks;
- all completed Git LFS objects;
- attachments, avatars, and other selected data-root contents; and
- enough Basaltwater state to identify the installed release and data paths.

For the low-end SQLite baseline, the first consistent backup workflow may use
a short planned service stop while the complete data roots are snapshotted or
archived. A live copy of the database, repositories, and LFS objects at
different points in time must not be presented as a verified backup. Incomplete
temporary LFS uploads should be discarded during restore rather than preserved
as durable data.

Restore is complete only after Gogs starts and an integration test can:

1. authenticate to a temporary or designated test repository;
2. clone normal Git content;
3. download an existing LFS object;
4. upload and retrieve a new test LFS object; and
5. remove the temporary repository or test branch without touching operator
   repositories.

The backup implementation and retention policy belong to the roadmap's shared
recovery project. This plan defines the Gogs data inventory and verification
contract that project must consume.

### Agent Git LFS clients

Keep the existing host-neutral `--repo` declaration and select the Git LFS
client once for the target user:

```text
--git-lfs
--repo https://git.example.com/team/assets.git
```

Repository placement should be independently selectable from Git transport.
When a dedicated agent disk is declared, use an explicit workspace root:

```text
--storage agent-data ts1-storage 128G
--storage-mount agent-data /srv/agent-workspace ext4
--agent-workspace /srv/agent-workspace
--repo https://git.example.com/team/project.git
```

`--agent-workspace` changes the target-side clone root but does not change the
repository URL, Git credentials, or Git LFS endpoint. It must be validated as
an existing required local mount before the first clone. The default can
remain the setup user's `~/repos`, which makes a separate `/home` disk useful
without making `/home` migration a prerequisite.

Do not add a second `--repo-lfs` URL list. Git LFS installation and user
initialization are VM-level concerns, while `.gitattributes` and repository
content determine whether each clone uses LFS. This avoids parallel repository
APIs and lets public or authenticated repositories on any Git host retain the
same preparation path. A server using Gogs LFS does not imply that every agent
VM or repository needs the client, so the option remains explicit.

When selected, setup installs the Git LFS client before all repository clones,
runs non-repository user initialization, and uses the normal target-side HTTPS
clone. A clone fails clearly when required LFS objects cannot be downloaded.
An existing repository remains protected from reset or overwrite;
diagnostics may report missing LFS objects without fetching them implicitly.
For an SSH Git remote, diagnostics must still verify the separate HTTPS LFS
credential path and explain that successful SSH authentication alone is not
enough for object transfer.

### Samba storage boundary

The repository already has separate Samba server and CIFS client capabilities:
`--samba` and `--share` configure an authenticated server share, while
`--smbclient` and `--mount-smb` install and mount a remote share. They should be
used as storage roles around Git, not as another Git protocol or credential
system. Samba credentials grant filesystem access to a path; they do not scope
repositories, Git pushes, or Git LFS objects.

The first storage design uses the following boundaries:

| Data or operation | First-release location | Samba role |
| --- | --- | --- |
| Gogs SQLite database, `app.ini`, secrets, and generated hooks | Local Gogs data filesystem | Never a live writable share |
| Gogs bare repositories | Local Gogs data filesystem, owned by `git` | Offline backup/export only |
| Gogs LFS objects and temporary uploads | Local Gogs LFS paths | Offline backup/export only; do not set `OBJECTS_PATH` to CIFS |
| Agent `.git` directories and active worktrees | Local VM filesystem | Import/export or backup only |
| Large agent assets that are not active Git worktrees | Explicit mounted share | Optional `--mount-smb` asset path |
| Consistent Gogs or workspace archives | Separate restricted backup share | Local backup job or dedicated backup account writes; ordinary agents have no access or read-only access |

Do not expose the live Gogs data root as a writeable Samba share. Direct SMB
writes bypass Gogs authentication, hooks, repository policy, and LFS
reachability checks; concurrent access also makes SQLite and repository
operations unsafe. A share may use the same physical disk through a separate
directory, but the exported path must not contain Gogs's database, secrets,
hooks, or live repositories. The share configuration should use explicit
`valid users`, least-privilege read/write mode, and normal Unix permissions;
`force user` or symlink-following exceptions are not a substitute for a data
ownership design.

Backups to a Samba destination must cross the same consistency boundary as any
other Gogs backup. Stop Gogs for the short archive window (or use the future
recovery mechanism that provides an equivalent consistent snapshot), create
the archive locally, verify its size and checksum, copy it to a restricted
backup share under a temporary name, and atomically rename it after the copy
completes. Restore must copy the archive back locally and pass the normal Git
and Git LFS smoke test before re-enabling service access. Do not treat a live
copy of SQLite, repositories, and LFS objects at unrelated times as a
recoverable backup.

Agent VMs should keep active `--repo` worktrees on local ext4 or xfs storage.
Git can operate on a mounted filesystem, but a CIFS worktree adds differences
in locking, case behavior, symlinks, executable bits, file notification,
latency, and concurrent-writer semantics. A direct `file://` Git remote on a
share also does not provide Gogs authentication, server hooks, or a safe
multi-writer protocol. The setup flow should therefore reject or clearly mark
repository paths on CIFS in the first release. Use normal HTTPS Git and the
Git LFS endpoint for repository transport, and use Samba for assets, staging,
or archives. A later share-backed-worktree mode needs its own explicit option
and live compatibility tests; it must not be inferred from a path under
`/mnt`.

Git LFS remains independent of the share. `git lfs install` initializes the
target user, Git stores pointer files in the local worktree, and the LFS client
transfers the large content to the Git host's HTTPS LFS endpoint. A mounted
asset share can hold source material before it is copied into a local LFS
worktree, but it does not become the LFS server or bypass the LFS credential.
The Gogs LFS object directory can be included in an offline archive placed on
Samba, but a live CIFS `OBJECTS_PATH` is out of scope until Gogs's locking,
latency, failure, and recovery behavior has been tested on the supported
hardware.

This keeps the initial CLI simple: compose the existing Samba storage options
with `--git-lfs` and normal `--repo` declarations, without adding a
Git-specific share syntax or a second repository URL. If the implementation
needs more automation later, add role-aware storage declarations for `asset`
and `backup` first; do not add a generic “put application data on Samba” flag.

## Track C: agent web interfaces

### Desktop T3 Code interface

Desktop installation remains available for a VM that has a deliberate
desktop/RDP workload. Its selection is separate from both provider CLIs and
the headless web service:

```text
--desktop-interface t3code
```

This adapter installs the verified T3 Code desktop artifact for the target
user, creates its launcher and desktop entry, and reports the required
desktop-session and architecture prerequisites. It does not install RDP,
provider CLIs, browser automation, or credentials implicitly; those remain
separate explicit setup choices. It does not create a systemd web service,
nginx site, firewall rule, or remote pairing endpoint. Desktop T3 Code may
still start its own local server as part of normal upstream desktop behavior,
but Basaltwater does not advertise or expose that server as a managed web
interface.

The current Linux artifact is an upstream AppImage. The installer must verify
the selected release asset before activation, keep the download outside the
user's credential directories, install it as the setup user, and create a
launcher that points at the real artifact. Prefer a verified upstream Debian
package or another supported native artifact when the release registry makes
one available, but do not add a package manager abstraction solely for this
adapter. Desktop update and version-skew behavior must remain visible to the
operator; it must not silently update a running desktop session or its data.

`--desktop-interface t3code` is valid on a desktop-capable target and is
invalid as a synonym for `--web-interface t3code`. A target may select both,
but they are independent installations with independent health, update,
exposure, and removal state. The desktop selection is also not a reason to
accept `--agent-tool t3code`; that provider-tool name remains removed.

### Declaration and command shape

Web interfaces belong to normal target setup so the same feature works on a
new Proxmox VM, an existing VM, or a manually created VPS. The initial setup
shape is:

```text
--web-interface t3code
--web-interface-host 0.0.0.0
--web-interface-port 3773
--web-interface-source 192.168.0.0/24
--web-interface-source 10.0.0.0/8
```

The implemented first slice accepts repeatable interface declarations, but
T3 Code currently has one service instance per target user. Its companion
options are global to the selected T3 interface: `--web-interface-host` is
the T3 bind address, `--web-interface-port` is the T3 port, and
`--web-interface-source` is a private CIDR allowlist. A source list without an
explicit host promotes the default bind to `0.0.0.0`. The saved setup command
remains the declaration; the systemd unit, firewall rules, and pairing helper
are derived data.

The following exposure modes keep a bare declaration useful and safe:

1. With only `--web-interface t3code`, run the service on loopback and print a
   reusable SSH tunnel command. Do not install or open nginx for that
   interface.
2. With one or more `--web-interface-source` values, bind the T3 service to
   `0.0.0.0` unless a different bind is explicit and add matching UFW rules.
   This is for a deliberately trusted LAN or private routed network; CIDR
   filtering does not encrypt pairing credentials.
3. The implemented device-enrollment portal uses a separate Nginx listener
   with Basic Auth. It does not proxy the T3 API/WebSocket endpoint, so native
   desktop/mobile clients keep using T3's normal bearer sessions. HTTPS
   reverse proxying for the T3 endpoint itself remains later work.
4. Reject a non-loopback listener without a CIDR restriction. The first slice
   does not expose a public listener or bypass T3's native pairing.

Source values accept IPv4 or IPv6 addresses and CIDRs through the existing
network validators. UFW exposes the listener only to the same sources. A
tunnel or CDN changes the observed peer address, so source filtering must not
claim to preserve the browser's original IP
unless a separately validated trusted-proxy configuration exists. Reject
source filtering with a Cloudflare tunnel in the first release unless its
client-IP restoration and trusted-proxy chain are explicitly supported and
tested.

### Authentication and secret handling

The first implementation uses native tool authentication when it exists. T3
Code's pairing token is exchanged for a session, and `t3 auth` can issue new
pairing credentials, inspect sessions, and revoke access. A source allowlist
is optional defense in depth for T3 Code and mandatory for any future
interface that has no suitable native authentication. A loopback-only SSH
tunnel is also sufficient network authentication for such a tool.

Current implementation boundary: direct T3 pairing, loopback/LAN source
filtering, pairing-token redaction, the control-plane
`basaltw agent web pair HOST USER` helper, and a protected on-demand
enrollment portal are implemented. The portal broker is provider-backed rather
than T3-specific internally, with T3 Code as the first fixed adapter.

Do not add raw passwords to setup arguments, saved commands, process
environments, or systemd unit files. The implemented setup shape is:

```text
--device-pairing t3code
--device-pairing-port 3774
--device-pairing-auth-file /run/secrets/t3code-pairing.htpasswd
```

Nginx Basic Auth gates only the ability to invoke a provider adapter and mint a
short-lived pairing link. It is intentionally not placed in front of T3's
normal HTTP/WebSocket endpoint because separate desktop/mobile clients cannot
be assumed to implement that extra challenge. T3's pairing exchange, session
checks, and revocation remain authoritative for actual access.

The portal inherits the web-interface bind and source allowlist, uses a
separate port, and proxies to a broker on a local Unix socket. It uses a
single-use same-site form nonce, per-source issuance limiting, no access log,
and a fixed root-managed provider command. T3 issuance uses the supported
`t3 auth pairing create --ttl 10m --base-url ... --json` interface. The broker
validates that the returned URL belongs to the expected public T3 origin.

The auth file is an Nginx-compatible `name:hash` file. The operator may supply
an existing regular file or, in interactive setup, enter a username and hidden
password so Basaltwater can generate one with the controller's existing OpenSSL
`passwd` support. Do not add `apache2-utils` only to obtain `htpasswd`, and do
not implement password hashing in Basaltwater. Store one root-owned,
appropriately group-readable file for the portal, replace it
atomically during rotation, and reload Nginx only after a complete
configuration test. A supplied file is a secret input and is never copied to
saved setup state or printed in a plan.

Basic Auth is an issuance gate, not a session or revocation system: changing
the file prevents later portal use but does not terminate a T3 session that
was already paired. There is no MFA or user-level T3 audit identity implied by
the Basic Auth username. T3 session revocation remains required.

The first LAN transport is HTTP and is allowed only with the existing private
or otherwise non-global source CIDR requirement. Documentation explicitly
warns that Basic Auth and the returned pairing link are observable on an
untrusted LAN. Loopback plus SSH forwarding or a separately managed HTTPS
endpoint is required outside a trusted network. The implementation does not
claim hosted `app.t3.codes` compatibility for a plaintext endpoint.

Pairing URLs and tokens must be treated as credentials. Normal setup output,
saved commands, dry runs, generated nginx files, and service logs must not
contain them. Before using the upstream background service, verify whether its
startup path writes an initial token to its private log. If it does,
Basaltwater must use the upstream on-demand `t3 auth` flow or a redacting
launcher so ordinary journald and setup logs never capture the token. An
explicit access-issuance command may return a new token once to an interactive
operator, but JSON status and diagnostics must always redact it.

The web interface is effectively a remote shell with access to agent and Git
credentials owned by the setup user. Documentation and setup output should
describe it at that privilege level, not as a harmless dashboard. Session
revocation, pairing expiration, and interface removal must be documented next
to the initial connection flow.

The planned remote agent-management shape for lifecycle and access operations
remains:

```text
basaltw agent web status HOST USER [--tool t3code] [--json]
basaltw agent web pair HOST USER
basaltw agent web sessions HOST USER --tool t3code [--json]
basaltw agent web revoke HOST USER --tool t3code ACCESS_ID
```

`pair` invokes the tool's supported access-issuance command on the target and
prints the credential once to the operator; it has no JSON mode. `status` and
`sessions` expose only non-secret identifiers and metadata. `revoke` accepts a
listed pairing or session identifier and verifies that it is no longer usable.
These commands reuse the normal managed SSH identity and redaction path. The
first implemented command is `agent web pair`; status/session/revocation
commands remain future work. Browser-based issuance is also implemented
through the protected portal, and `--no-device-pairing` reconciles removal
without revoking existing provider sessions.

### T3 Code runtime and service integration

`--web-interface t3code` installs the Node runtime, native build prerequisites,
and a persistent target-user T3 runtime under
`~/.local/share/basaltwater/t3code`, then invokes its supported headless CLI.
The web selection does not require a
separate `--node` flag, install the desktop AppImage, or install GitHub CLI or
provider CLIs that were not explicitly selected with `--agent-tool`. The
current implementation requires at least one of Codex, Claude Code, or
OpenCode so the resulting interface is useful immediately.

The desktop adapter and web adapter share only the artifact verification and
version policy. The web adapter owns the headless service, systemd system unit
running as the target user, loopback/LAN bind, and source firewall rules; the
desktop adapter owns its per-user launcher and desktop entry. Neither adapter
copies credentials merely because T3 Code was selected.

The current service is a Basaltwater-managed system unit with explicit HOME,
working directory, bind, port, restart policy, and target-user ownership. Its
wrapper loads the target user's nvm environment and invokes the persistent T3
binary directly; service stdout is discarded so pairing material is not
captured in journald. T3's Linux `node-pty` dependency is compiled during
setup with the required Debian build packages, and service restarts do not
depend on npm's temporary npx cache or network access. `t3code-pair` invokes
the same installed binary for an operator. A later update/revocation command
should use T3's supported service/auth lifecycle rather than adding a second
credential store.

Setup reruns are reconciliatory: completed APT package work, Go release-feed
lookups, uv self-updates, T3 npm installs, Playwright downloads, and xRDP Sid
package transactions are skipped when their installed-state checks pass.
`--refresh-packages` is a transient escape hatch for deliberately refreshing
those checks; it is not written into saved setup commands. Remote setup also
reports per-step elapsed time so a slow target-specific operation is visible
instead of being mistaken for general setup latency.

Setup ordering currently installs Node as a derived T3 dependency, installs
selected provider CLIs and their staged credentials/configuration, then creates
the T3 service and its pairing helper. Repository cloning remains the normal
`--repo` path. After pairing, the T3 client can add a prepared repository with
its normal Add Project flow; Basaltwater does not currently invent a second
project-registration API.
Firewall exposure is reconciled before service start and only for validated
private sources. A credential rotation should restart T3 only when the
affected provider requires it and should verify the provider again after
restart.

T3 Code's remote clients can add projects after pairing through the normal
Command Palette/Add Project flow. Setup must not silently register an
unvalidated path, change an existing repository, or make repository discovery
depend on the service working directory. A future pre-registration feature
must use a documented upstream interface or an explicit target-side command;
until then, `--repo` prepares the checkout and the client owns project
registration.

The service environment needs deliberate binary discovery because a systemd
user service is not a login shell. Include system and user-local binary paths
and verify every selected provider CLI from the running service context, not
only from an interactive SSH shell. The selected workspace root must also be
explicit so project discovery does not depend on systemd's default working
directory.

nginx integration must preserve the original host and scheme, forward
WebSocket upgrade headers, use bounded but agent-appropriate request and idle
timeouts and upload limits, and avoid response buffering where it breaks
streaming. It must use the repository's shared managed-site ownership,
staging, full-config validation, activation, and rollback primitives rather
than adding another nginx writer. Generated sites and units are activated only
after validation and are restored if the service or proxy health check fails.

### Reconciliation, health, and low-end operation

Each web-interface definition owns one service instance, nginx site when
present, firewall rules, and a small observed-state record. Each
desktop-interface definition owns only its verified artifact, launcher,
desktop entry, and desktop installation state. Re-running the same command is
idempotent. Removing an interface from a saved command should show the stale
managed resources and require the normal reconciliation/removal confirmation
rather than leaving an unknown listener or deleting tool data silently.

Observed state has an explicit schema version and records ownership, interface
kind, tool, Unix user, installed version, fixed backend port when applicable,
generated artifact paths, exposure mode when applicable, and last health
result. It contains no credential or copied configuration data.
Reconciliation removes only artifacts carrying the same Basaltwater ownership
marker; a same-named unmanaged unit, launcher, desktop entry, or nginx site is
a hard error. Dry run lists every resource that would be created, replaced,
retained, or removed.

T3 Code conversations, sessions, and settings are user data even on a
disposable VM. Interface removal should stop exposure while retaining that
state by default and report its path and sensitivity. Full VM recreation may
discard it; an operator who chooses to preserve it needs an encrypted backup
that is consistent with the installed T3 version. This plan adds the data to
the recovery inventory but leaves archive transport and retention to the
shared recovery project.

Health output should report, without secrets:

- tool, target Unix user, installed version, and service state;
- loopback endpoint and externally advertised URL, if any;
- nginx configuration and certificate state;
- active access mode: SSH-only, native authentication, and/or source CIDRs;
- provider CLI discovery from the service environment;
- declared-project registration and reachability;
- HTTP readiness and a WebSocket/authentication-aware probe; and
- restart count, recent update result, memory use, and disk use for retained
  interface state.

Every interface must have independent resource accounting. Conservative
systemd memory and process limits should fit low-end VMs but remain
configurable; an out-of-memory restart must be visible instead of becoming an
unexplained reconnect loop. Updates must not interrupt active agent work
silently. T3 Code's version-skew and service update workflow should remain the
owner of its staged update and rollback behavior.

### Browser automation and application previews

An agent web interface is separate from the existing optional Playwright
browser-automation capability. The former lets a person reach an agent UI;
the latter lets an agent launch a local browser and inspect a site. Selecting
T3 Code should not silently install browser binaries, and selecting browser
automation should not expose T3 Code.

It is also separate from remotely previewing a development server started by
an agent. T3 Code's served web client does not currently proxy an arbitrary
project-local preview port. Exposing every discovered development port would
bypass the interface access boundary and create SSRF, cookie, origin,
WebSocket, and cleanup problems. A later preview-gateway project can proxy one
explicit loopback target under the same authenticated origin, or setup can
print an explicit SSH tunnel for a selected preview port. It is not part of
the first web-interface slice.

### Dependency policy

Use this decision test whenever implementation proposes another package:

1. Keep mature software when it owns a security-sensitive protocol, complex
   lifecycle, or compatibility surface. nginx remains the TLS/WebSocket proxy
   and Basic Auth gate, Samba remains the SMB server/client, systemd remains
   the supervisor, and T3 Code remains the owner of its authentication,
   database migration, and version rollback.
2. Implement small deterministic behavior in Basaltwater when Python's
   standard library and existing modules are sufficient. This includes
   web-option parsing, tool/host/port/CIDR validation, nginx rendering, state
   observation, health polling, redaction, and saved-command serialization.
3. Do not add a general proxy, supervisor, template engine, secrets daemon,
   container runtime, or `htpasswd` package for one narrow feature. Nginx's
   password-file format can use the existing OpenSSL `passwd` command; do not
   maintain a bespoke password hash or add Apache utilities solely for it.
4. Record why a new runtime dependency is necessary, install it only when its
   owning explicit feature is selected, and pin or verify downloaded artifacts
   using the repository's existing supply-chain rules.

## Delivery sequencing and dependency gates

The three tracks are delivery lanes, not one six-step critical path. T3 Code
and Gogs work does not wait for Proxmox clone/restore, and read-only VM CLI
work does not wait for backup transport. Each slice must leave one coherent
public command surface; no release should contain both old and new aliases.

The shared roadmap imposes two gates:

1. Parser, validation, pure rendering, read-only observation, and removal of
   dead development APIs may proceed immediately. Single-guest power-state and
   typed, reversible boot-policy operations may use the synchronous provider
   command with exact setup/guest identity checks and a resulting-state
   observation. The existing Proxmox destroy operation additionally requires
   explicit confirmation and an absence check afterward; it remains
   intentionally non-recoverable. Restore, rollback, destructive
   reconfiguration, and multi-file service/firewall reconciliation must use
   the durable operation markers and staged activation contract from
   [transactional execution](TRANSACTIONAL_EXECUTION.md).
2. VM restore and claims of recoverable Gogs or T3 state depend on the shared
   recovery project choosing an archive/storage mechanism. Data inventory,
   consistency rules, and restore smoke tests can land before that backend.

### Lane A1: VM terminology and observation commands

Implementation status: newly written Proxmox host records carry an explicit
schema version and provider identity, and incompatible development records
fail with re-registration guidance. The first neutral VM observation/result
types, setup-backed local-name resolution, resource statistics, typed autostart
ordering, power-state lifecycle, and confirmed QEMU destroy command are
implemented.

- Add provider and schema-version fields to registered infrastructure hosts;
  reject incompatible development records without mutating them and document
  the short Proxmox re-registration flow.
- Extend the neutral VM models with capability and durable-operation types.
- Move the remaining advanced guest mutations and mount status to the nested
  `vm` command shape.
- Rename internal `container_*` symbols where they apply to both or only to
  QEMU VMs; leave true LXC concepts explicit under `proxmox lxc`.
- Reorganize Proxmox host/cluster commands, remove the interactive shell and
  old guest paths, and update docs, completions, callers, and tests together.

### Lane A2: existing VM mutations

- Move typed modification, disk resize, snapshots, migration, unlock, and
  backup creation through the provider boundary. Power-state lifecycle, typed
  autostart, and confirmed QEMU destruction are implemented with
  local-name/identity resolution and post-operation state observation.
- Add generalized bounded polling, capability preflights, provider task IDs,
  durable operation results, and stronger post-operation verification.
- Remove arbitrary `--set KEY=VALUE` reconfiguration and expose only validated
  typed options.
- Preserve dry runs and confirmations, and require the shared staged-operation
  contract before destructive commands land.

### Lane A3: declarative VM data disks and guest mounts

Implementation status: the provisioning-only blank-disk/empty-path slice and
generic `--backup` composition with the existing rsync/par2 storage service
are complete in code and unit tests. Live Proxmox/reboot validation and the
later observation, resize, adoption, detach, restore, and migration workflows
remain.

- Replace the development-era storage shape with named QEMU disk
  declarations and explicit guest mount declarations; retain a separate LXC
  template path.
- Preflight Proxmox storage-pool content, free capacity, disk names, and bus
  slots before creating any additional disk. Attach disks with stable serials
  and include them in the saved provisioning declaration.
- Add a target-side storage step after SSH/QEMU-agent readiness and before
  Gogs, Git LFS initialization, agent credentials, repository clones, or T3
  project registration.
- Implement blank-device-only formatting, UUID-based native mount units,
  required-mount fail-closed behavior, ownership after mounting, idempotent
  re-runs, and mount health markers stored on the mounted filesystem.
- Add `--agent-workspace` so normal `--repo` clones can use a dedicated local
  data disk without changing transport or credential behavior.
- Explicitly reject `/home`, `migrate`, existing-disk adoption, detach, and
  resize in this provisioning slice. Follow with a separately reviewed
  `/home` migration workflow that has backup/snapshot, maintenance-window,
  preservation, verification, and rollback checks.
- Follow with read-only mount status and coordinated grow-only
  disk/partition/filesystem resize; do not add shrink or implicit reformat
  operations.

### Lane A4: Proxmox clone and restore

- Implement full clone with explicit storage, cloud-init/network handling, and
  collision checks; keep linked clones deferred.
- Implement backup restore without overwriting an existing guest and with an
  optional isolated verification boot. Restore and clone must preserve the
  complete additional-disk declaration and verify required mounts before
  reporting an application-ready guest.
- Reuse shared durable operation and recovery records, then add live tests for
  directory/qcow2 and block-backed storage where behavior differs.

### Lane B1: explicit and safe Gogs LFS operation

Implementation status: explicit local LFS paths, directory creation, symlink
rejection, declared-mount verification, CIFS rejection, loopback/source-rule
exposure, setup-time storage and SQLite health, agent Git LFS initialization,
release-metadata-digest-verified activation, and reusable threshold-aware
health reporting are complete for the declared IPv4 scope. Exposure-mode
changes remove stale managed direct rules, direct HTTP redirects to HTTPS,
nginx validation fails setup, and weekly update checks have a persisted
failure/staleness signal.

- Depend on Lane A3 for the dedicated Gogs data-disk and mount contract when a
  data disk is declared.
- Add tested IPv6 source exposure only when Gogs's listener and UFW policy can
  be enforced as one fail-closed operation.

### Lane B2: Gogs recovery contract

- Document complete data placement and add repositories, SQLite, attachments,
  secrets, LFS objects, and the backing filesystem/disk identity to the
  recovery inventory.
- Implement the consistency boundary selected by the shared recovery project.
- Add an authenticated end-to-end backup/restore smoke test for ordinary Git
  and Git LFS data, including a restore where the Gogs data disk is a separate
  attached volume.

### Lane B3: Samba storage roles

- Audit the existing `--samba`, `--share`, `--smbclient`, and `--mount-smb`
  flows against the Git/Gogs storage boundary in this plan.
- Add path and mount checks that prevent live Gogs SQLite, repositories,
  secrets, hooks, or LFS object paths from being configured as writable CIFS
  application data in the first release.
- Support a restricted backup/archive share and an optional agent asset share
  without changing Git HTTPS or Git LFS authentication behavior.
- Test SMB3 permissions, credential-file ownership, mount failure and retry,
  atomic archive publication, and restore from a share-backed archive. Do not
  call a mounted repository or a copied set of live files a valid Git backup
  without the consistency and Git/LFS verification steps.

### Lane C1: loopback T3 Code service

Implementation status: explicit desktop and headless T3 Code selection,
provider separation, Node dependency setup, a target-user system service,
loopback/LAN bind validation, private-source UFW rules, pairing helper, and
service-log redaction are implemented. Project registration and remote
pairing/session management remain open.

- Keep repeatable interface declarations and add scoped per-interface options
  only when a second web interface requires them; current T3 options are
  target-global because there is one T3 service instance per user.
- Remove T3 Code from the agent-tool registry, add the explicit desktop and
  web interface categories, and retain the verified AppImage installer as the
  desktop adapter.
- Add the verified headless CLI/runtime path with only its required
  dependencies; selecting it must not install the desktop artifact. (Done for
  the direct headless slice.)
- Integrate the supported service with explicit HOME, PATH, workspace, fixed
  loopback/LAN port, provider discovery, and credential ordering. (Done with a
  target-user system unit; T3's own background-service lifecycle is still to
  be evaluated for a later replacement.)
- Run the service only after its declared workspace mount is healthy; a
  missing agent data disk must prevent project registration and repository
  writes rather than falling back to root storage.
- Keep prepared `--repo` paths available to the client's normal Add Project
  flow and verify them through a live remote-client test. Optional
  pre-registration remains open; the normal clone path is implemented
  independently.
- Add schema-versioned observed state, SSH-tunnel output, secret-free health,
  and `agent web` pairing/session/revocation commands.
- Do not complete this slice until startup and service logs pass pairing-token
  redaction tests.

### Lane C2: controlled network exposure

- Reuse shared Nginx ownership and staged reconciliation for hostname/TLS,
  trusted forwarded headers, synchronized UFW rules, and rollback.
- Proxy the T3 HTTP/WebSocket endpoint only after hostname, origin, upgrade,
  upload, timeout, native-authentication, and no-direct-bypass checks pass.
- Reuse the implemented enrollment password-file flow where appropriate, but
  do not put Basic Auth in front of T3's endpoint until desktop/mobile and
  same-origin browser behavior is verified.
- Add update/removal reconciliation, retained-state reporting, resource limits,
  and low-end VM measurements.
- Validate T3 Code through an SSH tunnel, direct private CIDR access, and nginx
  HTTPS, including native session revocation and continued separation of the
  Basic Auth enrollment gate.

### Release integration

- Publish breaking-release notes and regenerated command examples.
- Document manually provisioned VPS setup, QEMU versus LXC boundaries, Gogs
  recovery, T3 exposure/pairing/revocation, and external DNS/firewall actions.
- Run the complete live Proxmox, low-end Gogs/LFS, and disposable T3 Code
  scenarios without affecting unrelated guests, repositories, or interfaces.
- Update the planning index and roadmap as each independently useful lane
  lands.

## Validation requirements

- Parser, rendering, and dispatch tests cover the positional-host and nested
  disk/snapshot/backup command shapes and prove old paths are absent.
- Setup parser tests cover unique named disks, root-disk requirements, valid
  Proxmox pool and size declarations, mount-path normalization, ext4/xfs
  selection, empty versus migrate policy, duplicate declarations, unsafe
  system paths, and `--agent-workspace` placement.
- Provider tests use mocked SSH/system commands and verify capability failures
  occur before mutation.
- Proxmox storage tests verify content-type and capacity preflights, stable
  `scsiN` slot/serial assignment, additional-disk attach, partial-failure
  cleanup, grow-only resize, and reporting of every disk in inventory,
  snapshots, backups, clone, and restore operations.
- Guest-storage tests mock `lsblk`, `blkid`, `findmnt`, filesystem creation,
  and systemd calls. They prove only a confirmed blank device is formatted,
  UUID mounts are persisted, re-runs are idempotent, nonblank devices and
  unexpected serials are rejected, required mounts fail closed, ownership is
  applied after mounting, and `/home` migration preserves metadata and keeps
  the original path usable when verification fails.
- JSON fixtures prove stable field names and explicit provider data.
- Destructive-command tests cover dry run, confirmation refusal, timeout,
  partial provider failure, and post-operation verification failure.
- Restore and clone tests cover VMID/name collisions, unsupported storage,
  address conflicts, failed task completion, and cleanup of partial guests.
- Gogs tests use temporary directories and mock system calls; they verify LFS
  path rendering, permissions, symlink refusal, release-digest failure, nginx
  limits, safe hostless defaults, source-rule replacement, and backup
  inventory. They also reject writable CIFS paths for live Gogs data.
- Git LFS client tests prove installation and user initialization precede
  every normal repository clone without changing `--repo` URL handling, and
  reject active repository paths on CIFS unless a future explicit mode opts in.
- Samba tests use mocked system calls and temporary paths to cover share roles,
  least-privilege users, root-only mount credentials, failed mounts, and
  atomic backup publication without exposing a live Gogs data root.
- Web-interface parser tests cover repeated distinct tools, duplicate-tool
  rejection, scoped `TOOL VALUE` options, normalized IPv4/IPv6 sources,
  hostname and port collisions, undeclared tools, TLS requirements, fixed
  backend-port conflicts, unsupported Cloudflare/source combinations, and
  stable saved-command ordering. Desktop-interface tests cover explicit T3
  selection, desktop prerequisite failures, duplicate declarations, and the
  fact that desktop selection creates no web exposure.
- Parser and registry tests prove `--agent-tool t3code` is absent, while
  `--desktop-interface t3code` selects only the verified desktop adapter and
  `--web-interface t3code` selects only the headless path.
- Current unit tests render the T3 and enrollment systemd/Nginx configuration
  in temporary directories, mock service/firewall calls, and prove that the
  Basic Auth secret is absent from provider and saved configuration. Lane C2
  must add direct-proxy WebSocket, hostname/origin, no-bypass, and failed
  activation rollback coverage before HTTPS exposure ships.
- Redaction tests seed recognizable pairing URLs, tokens, and agent credentials
  and prove none appear in setup plans, dry runs, generated configuration,
  status JSON, or normal service logs.
- Pairing-command tests require a TTY, omit JSON mode, display one issued
  credential once, and prove status/session/revocation output remains redacted.
- A live T3 Code test installs a selected provider CLI, discovers it from the
  running user service, opens a declared project, pairs a browser, reconnects
  with the session, revokes it, verifies rejection, and confirms an unrelated
  nginx site and systemd service remain active.
- A live desktop-target test verifies `--desktop-interface t3code` launches
  with a supported desktop session, while proving it does not install the web
  service or open an nginx/firewall listener. A separate web test proves the
  inverse.
- A live Gogs test on modest Debian hardware pushes, clones, and restores both
  ordinary Git data and LFS objects over HTTPS, then proves an SSH Git remote
  still uses the configured HTTPS credential for LFS transfer.
- A live Proxmox storage test provisions a VM with separate root and
  `git-data` disks, verifies the data mount by UUID before Gogs setup, reboots
  without writing to root storage, grows the data disk, and proves Git/LFS
  data remains available. A separate agent-workspace test proves repository
  clones use `--agent-workspace`; `/home` migration is tested only in a
  disposable guest with an explicit backup/snapshot.
- A live Samba recovery test publishes a consistent Gogs archive to a
  restricted share, restores it locally, and runs the ordinary Git/LFS smoke
  test; an agent asset-share test proves the active `.git` worktree remains
  local.
- A live Proxmox test covers QEMU lifecycle, clone, snapshot rollback, backup,
  restore, and destruction without affecting an unrelated guest.
- No test talks to DigitalOcean or another cloud provider.

## Acceptance criteria

- An operator can manage a Proxmox QEMU VM entirely through `basaltw vm`
  without using Proxmox-specific command names for common lifecycle actions.
- Proxmox node, cluster, storage, placement, maintenance, and notification
  commands remain available under a clearly organized provider namespace.
- The host registry determines the provider; an ordinary VPS target is not
  mistaken for a managed VM.
- Existing one-command Proxmox provisioning plus setup still uses the normal
  setup engine and emits a reusable saved command.
- A QEMU provisioning declaration can attach one or more named non-root disks
  from selected Proxmox pools, format only blank devices, mount them by stable
  identity, and verify every required mount before Gogs, Git LFS, agent
  credentials, repository clones, or T3 projects use the path.
- A missing, wrong, or failed required mount blocks dependent setup and cannot
  redirect writes into an empty root-directory fallback. `vm mount status`
  identifies the disk, filesystem, mount path, and failure reason.
- A dedicated local agent workspace can be selected without changing Git URLs
  or credential behavior. A populated `/home` can be migrated only through an
  explicit, verified policy with preserved metadata and a recoverable failure
  path.
- VM list/show/health/snapshot/backup output has stable JSON and useful text.
- Reboot, full clone, and backup restore work with capability preflights,
  collision protection, waits, and result verification.
- Gogs runs on the SQLite/local-files baseline from a verified release artifact
  with an explicit local LFS object path.
- A configured Gogs service can accept and return LFS objects through its
  normal HTTPS URL without a separate LFS server.
- Documentation states that Gogs does not support LFS file locking and that
  SSH Git remotes still require HTTPS credentials for LFS object transfer.
- Gogs capacity output distinguishes repository, LFS, attachment, and log
  usage and never deletes reachable data.
- The documented backup inventory and restore smoke test cover Git repositories
  and LFS objects together.
- Hostless Gogs is loopback-only by default and cannot bind externally unless
  validated source firewall rules are active.
- Gogs health distinguishes local service readiness from a configured remote
  LFS endpoint, labels loopback-only SSH Git as not remotely LFS-ready, and
  does not claim to have probed client routing, DNS, or authentication.
- Agent workspace setup can install Git LFS once and prepare normal `--repo`
  declarations without adding a parallel repository API or general
  development-tool suite.
- Samba can provide a deliberately scoped agent asset or backup share while
  active Git worktrees, Gogs data, and live LFS objects remain local and Git
  and Git LFS continue to use HTTPS transport and credentials.
- A bare `--web-interface t3code` creates a boot-persistent loopback service
  and supports a documented SSH tunnel without opening a firewall port.
- `--web-interface-source` promotes the default bind only for explicit private
  sources, reconciles matching UFW rules, and refuses non-loopback exposure
  without those sources.
- `--desktop-interface t3code` installs a verified desktop T3 Code artifact
  only when a desktop-capable target explicitly selects it; it does not create
  the web service or a public listener. `t3code` is absent from
  `--agent-tool`.
- Interface declarations are repeatable in the parser and saved command;
  duplicate T3 declarations collapse to one service instance for the target
  user. The pairing helper can be invoked from the control system with
  `basaltw agent web pair`. The optional generic device-enrollment broker
  exposes a separate Basic-Auth Nginx portal, mints short-lived native T3
  credentials, and leaves HTTPS/WebSocket proxying for the T3 endpoint itself
  as later C2 work.
- T3 Code discovers only the explicitly installed provider CLIs from its
  service context and uses the setup user's existing protected credentials.
- An operator can issue one pairing credential with the control-plane
  `basaltw agent web pair HOST USER` command or the target-side
  `t3code-pair` helper. An authenticated browser can also issue its own
  credential through the protected portal without live terminal access;
  managed session listing/revocation remains open.
- No pairing credential, session secret, or agent credential appears in saved
  commands, generated unit/proxy files, dry runs, status JSON, or ordinary
  logs.
- A generic `--backup` declaration uses the existing storage-ops rsync path,
  can target a separately mounted non-root disk, composes with `--scrub`, and
  is documented as a file mirror rather than an application-consistent
  snapshot.
- No DigitalOcean provisioning or management dependency is introduced.

## Cross-cutting gaps to close during implementation

The following are implementation gates rather than reasons to broaden the
first release:

1. **Exact T3 artifact and service contract**: the current AppImage path is the
   desktop adapter, not the headless CLI. Pin and verify both the official
   desktop artifact and CLI/runtime path, test the headless background-service
   and client Add Project behavior, and confirm desktop version-skew plus
   service update and database rollback before enabling either managed
   installer.
2. **Secret-bearing startup output**: prove whether the upstream service emits
   its owner pairing token to a private file or journal. Do not ship automatic
   startup until setup, status, and service logs pass the redaction tests.
3. **Service environment**: verify provider binary paths, credential homes,
   Git configuration, workspace ownership, and non-login `PATH` from inside
   the running service. An interactive-shell check is insufficient.
4. **Public-edge prerequisites**: DNS, certificate issuance, router/NAT rules,
   and VPS-provider firewalls may live outside Basaltwater. Preflight what can
   be observed and print the exact remaining operator action without claiming
   the endpoint is ready prematurely.
5. **HTTP trust boundary**: test allowed host and origin behavior, forwarded
   scheme/host values, WebSocket authentication, request limits, and
   connection throttling. Test Nginx Basic Auth on the initial upgrade,
   reconnect, and same-origin browser paths, including password-file rotation.
   Never trust client-supplied forwarding headers from an untrusted proxy.
6. **Resource and update policy**: measure idle and active T3 Code operation on
   the intended 4 GB and smaller VMs, set conservative restart/resource
   defaults, and make updates wait for or explicitly interrupt active work.
7. **State and removal**: inventory T3 conversations, settings, sessions,
   logs, and installed versions; distinguish retained user data from generated
   service/proxy state and include the former in encrypted recovery guidance.
8. **Preview expectations**: keep agent UI access, Playwright automation, and
   development-server preview as three explicit capabilities. Do not expose
   arbitrary agent-started ports as a shortcut.
9. **Open-source and supply-chain review**: T3 Code itself is MIT-licensed;
   record transitive runtime/package licenses, release source, digest or
   signature behavior, download paths, and update ownership for T3 Code and
   Gogs before enabling unattended installation.

10. **Provider versus guest storage**: keep Proxmox pool allocation,
    virtual-disk attachment, guest filesystem creation, mount activation, and
    application placement as separately observable operations. Preflight pool
    content type and capacity before provider mutation, then verify the guest
    disk before formatting or starting a dependent service.

11. **Disk identity and fail-closed mounts**: test stable serial, by-id, UUID,
    and mount-unit behavior across reboot, disk order changes, missing disks,
    and a root directory that exists before the mount. A disconnected data
    disk must produce a health failure and block writes, never look like a
    fresh empty application directory.

12. **Populated-directory migration**: define the backup, maintenance,
    metadata-copy, verification, cutover, and rollback contract for `/home`
    before exposing it as a normal option. An empty tool-owned mount and a
    migration of a populated system directory are different workflows.

13. **Samba and filesystem semantics**: verify which target filesystems are
    local versus CIFS before configuring Git, Gogs, or LFS. Confirm that the
    backup mechanism produces one consistent archive before copying it to a
    share, that share credentials cannot read application secrets, and that
    failed mounts or disconnected shares do not turn a missing data disk into
    an empty directory that agents can overwrite.

The shared recovery project still needs to choose the first backup archive and
storage mechanism. This plan defines the Gogs and T3 data consistency,
sensitivity, and verification contracts that mechanism must consume.

## Related implementation and plans

- `lib/proxmox_cli.py`
- `lib/proxmox_manage.py`
- `lib/proxmox_vm.py`
- `lib/vm_storage.py`
- `lib/proxmox_backup.py`
- `lib/proxmox_hosts.py`
- `lib/proxmox_guest.py`
- `lib/arg_parser.py`
- `lib/config.py`
- `lib/setup_common.py`
- `lib/agent_cli.py`
- `lib/validation.py`
- `web/gogs_steps.py`
- `web/service_tools/deploy_admin.py`
- `common/agent_steps.py`
- `common/storage_steps.py`
- `plugins/common.py`
- `lib/nginx_config.py`
- `docs/GOGS.md`
- [Agent VM workspaces and credentials](AGENT_VM_WORKSPACES.md)
- [Proxmox setup and maintenance audit](PROXMOX_MAINTENANCE_AUDIT_2026-08-09.md)
- [Transactional execution and reconciliation](TRANSACTIONAL_EXECUTION.md)
- [Project roadmap](ROADMAP.md)
- [T3 Code remote access](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md)
- [T3 Code background service](https://github.com/pingdotgg/t3code/blob/main/docs/user/background-service.md)
- [T3 Code installation and provider discovery](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md)
- [T3 Code update lifecycle](https://github.com/pingdotgg/t3code/blob/main/docs/user/updating.md)
- [T3 Code MIT license](https://github.com/pingdotgg/t3code/blob/main/LICENSE)
- [T3 Code remote preview gateway gap](https://github.com/pingdotgg/t3code/issues/5101)
- [Nginx HTTP Basic Auth module](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html)
- [Nginx WebSocket proxying](https://nginx.org/en/docs/http/websocket.html)
- [Samba `smb.conf` reference](https://www.samba.org/samba/docs/current/man-html/smb.conf.5.html)
- [Git LFS command and storage model](https://github.com/git-lfs/git-lfs/blob/main/docs/man/git-lfs.adoc)
- [Git LFS API and authentication](https://github.com/git-lfs/git-lfs/blob/main/docs/api/README.md)
- [Gogs Git LFS documentation](https://gogs.io/advancing/git-lfs)
- [Samba shares and client mounts](../SAMBA_SHARES.md)
- [Proxmox storage model](https://pve.proxmox.com/pve-docs/pvesm.1.html)
- [Proxmox VE Administration Guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf)
- [Cloud-init disk, filesystem, and mount modules](https://docs.cloud-init.io/en/latest/reference/modules.html)
- [Debian `fstab(5)`](https://manpages.debian.org/unstable/mount/fstab.5.en.html)
- [systemd `mount(5)`](https://manpages.debian.org/unstable/systemd/systemd.mount.5.en.html)
