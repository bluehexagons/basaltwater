# Placement, backups, and migration

[Documentation index](proxmox.md)

## Placement, backups, and migration

The placement planner ranks registered nodes without changing them:

```bash
basaltw proxmox plan place \
  --cores 4 --memory 8192 --disk 40 \
  --prefer-tag production --exclude pve3
basaltw proxmox plan rebalance --limit 3
```

`plan rebalance` reports overloaded nodes and candidate destinations. It only
migrates when `--apply VMID` is supplied; use `--dry-run` to preview the
migration command and `--yes` to skip its confirmation prompt. `--to HOST`
overrides the top-ranked destination. Online migration and local-disk transfer
have the same storage and cluster prerequisites as the direct `migrate` command.
`--target-storage POOL`, `--timeout SECONDS`, and `--shutdown-timeout SECONDS`
are also available with `--apply`. It uses the same verified lifecycle and
saved-setup updates as direct migration. Recover an unfinished rebalance move
through the direct `migrate ... --resume` command with the original options.

List and create immediate `vzdump` backups:

```bash
basaltw proxmox backups pve1 101
basaltw proxmox backup pve1 101 \
  --storage backup --mode snapshot --compress zstd --dry-run
```

The guest must belong to the selected host's local node (QEMU or LXC). Before
running `vzdump`, Basaltwater checks local guest ownership and inventories the
selected active backup storage. Success requires both a zero exit status and a
new, nonempty archive for that VMID in a fresh JSON storage inventory. This also
detects a guest moving away after the ownership check, when native `vzdump` can
exit successfully without creating a backup. An existing archive alone never
establishes success. Avoid concurrent backups for the same guest and storage:
the inventory comparison cannot attribute a concurrent archive to this command.

`--timeout SECONDS` bounds the backup SSH wait in scripts and interactive
sessions. It defaults to 86400 seconds (24 hours), accepts 1 through 604800
seconds (seven days), and can be increased for large backups. A timeout reports
uncertain remote completion: inspect the Proxmox task log and target storage
before retrying. Basaltwater does not automatically replay the backup or remove
archives, locks, or tasks. A failed completion inventory also requires inspection
before retrying. The Proxmox shell's `backup` command accepts the same storage,
mode, compression, timeout, and dry-run options. Dry runs perform read-only
preflight queries and print the command without starting a backup.

The backup command defaults to the first backup-capable storage pool, snapshot
mode, and zstd compression. `suspend` and `stop` modes trade availability for
stronger consistency where the guest workload requires it. Always verify that
the selected storage has enough capacity and a retention policy outside
Basaltwater. These commands create and list immediate backups; they do not
schedule backups, manage retention or off-host copies, audit scheduled-backup
freshness or archive integrity, or prove an isolated restore.

Backup listing reports failed or malformed storage inventories as errors,
including a failure in one pool after another pool was read successfully.
Storage queries select enabled pools and reject duplicate pool identities or
invalid active/enabled flags. Disabled or inaccessible pools are not selected.
Archive size and optional creation time must be nonnegative JSON integers;
booleans, fractional values, and duplicate volume identities are rejected.
These checks also apply to the fresh inventory used to verify backup completion.
An empty result means the queried active pools contained no matching backups;
it does not establish that offline pools contain none.

Migrate a guest between registered cluster nodes by VMID or exact Proxmox name:

```bash
basaltw proxmox migrate pve1 agent-dev-01 pve2 --target-storage local --dry-run
basaltw proxmox migrate pve1 agent-dev-01 pve2 --target-storage local --timeout 14400
basaltw proxmox migrate pve1 101 pve2 \
  --online --with-local-disks --target-storage local-lvm
```

`--online` keeps a VM running and requires suitable shared or migrated storage.
`--with-local-disks` enables live transfer of QEMU local disks; stopped VMs can
transfer local disks without it. `--target-storage POOL` selects one destination
pool for the migrated local disks instead of retaining their source pool names.
The pool must be active and enabled on the destination and accept `images` for
QEMU VMs or `rootdir` for LXC. In particular, a pool named `local` often permits only backup,
ISO, and template content: inspect `proxmox probe TARGET` and explicitly enable
VM images in Proxmox storage settings if required. Migration does not change
storage content policy. Shared disks retain their pool and volume identity.
The plan checks aggregate free space for all local disks, including cloud-init
and unused volumes, and verifies every declared bridge exists on the destination.
Detach local ISO media first. External cloud-init snippets, hooks, custom QEMU
arguments, passthrough devices, host serial/parallel devices, and host-path disks
or bind mounts require operator-managed migration. Serial sockets are supported.
Guests with snapshots also require operator-managed migration: this workflow
verifies the current disk layout and cannot verify snapshot-only volumes or
snapshot-backend compatibility. These checks run again before shutdown and
destination activation, so a resource added after inspection is rejected.

Both hosts must belong to the same Proxmox cluster, with quorum and working
node-to-node SSH. Inspection verifies matching cluster CA identities and
reciprocal online node membership. HA-managed guests, templates, guest locks,
active native guest tasks, and pending configuration changes block the move
before shutdown. Dry runs and transfers use Proxmox's migration preconditions
to check whether the destination can preserve the guest identity introduced in
Proxmox VE 9.3. During a mixed-version rollout, upgrade an incompatible
destination before moving the guest there. A destination guest with the same
name or declared primary address also blocks the move. Proxmox retains its
native migration checks and guest locks. Basaltwater also holds guest, name,
and configured IPv4 leases on both nodes, sharing identity locks with its
provisioning workflow.

Offline migration is the default. A running VM or LXC receives a graceful
shutdown with a 180-second default, configurable with `--shutdown-timeout`.
Shutdown failure or a guest that remains running stops the workflow before
transfer; it never forces power off. After the native transfer, Basaltwater
verifies the destination's identity, network, hardware, disk attachments,
properties, boot settings, pools, and actual volume existence and minimum sizes,
and verifies the source configuration is absent. Informational QEMU disk sizes
may be corrected by Proxmox during the move; verification accepts corrections
to the inspected volume capacity, including decimal and bare-byte size formats.
Minimum disk capacity comes from the actual source volume, so stale size
metadata cannot hide a truncated destination or validate an oversized saved
storage declaration. EFI variable capacity follows the native firmware layout;
TPM state is verified against the source state file even when Proxmox reports
its fixed 4 MiB allocation. Other disk properties remain unchanged. These rules
follow [Proxmox's native size correction](https://github.com/proxmox/qemu-server/blob/master/src/PVE/QemuMigrate.pm).
QEMU also requires no live process at the source's recorded guest PID. The command
then updates matching saved setups and starts a guest that was originally
running. An originally stopped guest remains stopped. LXC follows this offline
workflow; `--online` and `--with-local-disks` are QEMU-only options.

The migration and startup SSH waits default to 3600 seconds, including in a
terminal; use `--timeout SECONDS` for a longer copy. Initial read-only SSH
inspection retains the shared interactive passphrase policy. Neither a timeout
nor interruption reliably cancels a native Proxmox task. If the command fails
or is interrupted, preserve its private workspace checkpoint under
`migrations/`, inspect the native task log and both nodes, then use `--resume`
with the same source, guest, destination, storage, and timeout options:

```bash
basaltw proxmox migrate pve1 agent-dev-01 pve2 \
  --target-storage local --timeout 14400 --resume --dry-run
basaltw proxmox migrate pve1 agent-dev-01 pve2 \
  --target-storage local --timeout 14400 --resume
```

Resume rejects an active native task. A recovery dry run performs the same
destination checks when the transfer is complete and previews only the remaining
saved updates and startup. It also rejects an uncertain source-side transfer
instead of previewing a copy that execution would refuse. When the guest is
verified on the destination, it finishes configuration checks, saved updates, and power-state
restoration without repeating the disk transfer. A failed shutdown can be
resumed safely. An uncertain transfer whose guest remains on the source is
never replayed automatically, including with `--resume`; complete native recovery
and reconcile the checkpoint using the [recovery guide](transaction-recovery.md).
There is no automatic reverse migration, forced unlock, or source restart.
Successful completion retains private evidence in the checkpoint's `.last.json`
and clears the active guard. A startup failure leaves the verified destination
binding saved and the checkpoint available for a later resume.

Live migration also requires compatible CPUs; Basaltwater's default `host` CPU
model makes an offline move preferable between different hardware generations.

Use a dry run first. It performs the live inspections and shows the full
shutdown, transfer, verification, saved-update, and startup plan without guest
mutations, leases, checkpoint creation, or saved-setup writes. It cannot prove
that a later transfer will succeed. The interactive shell supports the same
migration flags and rejects unknown options, including misspelled dry-run flags.

For an offline move to a differently named pool, obtain the VMID from
`proxmox ls`, verify a recoverable backup and enough space for all disks, then
run the commands in order, stopping on any error:

```bash
basaltw proxmox probe pve2
basaltw proxmox migrate pve1 101 pve2 --target-storage local --timeout 14400 --dry-run
basaltw proxmox migrate pve1 101 pve2 --target-storage local --timeout 14400
# After the guest finishes booting:
basaltw vm health pve2 101
```

Matching setups in the selected `--workspace` are updated automatically after
destination verification, without rerunning guest setup. Matching requires the
source binding, configured IPv4 address, guest name and type, and declared
managed disk identities and sizes to agree. QEMU root uses `scsi0`, while LXC
root uses `rootfs`; named QEMU disks
use retained `bw-NAME` or `it-NAME` serials. A partial identity match or stale
saved disk declaration blocks the move before shutdown. No matching setup is
also valid for an unmanaged guest and is reported in the plan.

Migration inspects saved guest identity, provider access, and storage declarations
without loading or replaying the full setup profile. Unrelated setup options,
including retired application fields such as `api_subdomain`, `install_ruby`,
and `reset_migrations`, do not block the move. Matching older records receive
only the verified provider updates and retain their other fields. Invalid
matching identity or storage still blocks shutdown. Unreadable JSON or an
invalid record structure also blocks inspection because its relationship to the
guest cannot be established safely.

Updates replace only the provider host, its verified SSH user/key path, the
unchanged primary bridge, and each disk's actual destination pool. Logical disk
names and amounts, guest credentials, mounts, caches, swap and SSD policy,
image staging, other setup options, run metadata, and history are preserved.
SSD emulation is an explicit disk policy and is not inferred from a pool name.
Concurrent changes to identity or provider fields require inspection; unrelated
edits survive. Each cache write is atomic and private, and partial updates can
be resumed. Local-name commands such as `vm show NAME` use the new binding when
the workspace's saved setups satisfy the current setup schema. Migration does
not upgrade retired setup policy; loading or rerunning those full configurations
still requires the [setup-version upgrade](../migration-guide.md).
Workspaces on other controllers cannot be discovered or
rewritten by this command; use the verified out-of-band rebind workflow above
on each additional controller.
