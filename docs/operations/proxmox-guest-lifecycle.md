# Operate and recover guests

[Documentation index](proxmox.md)

## Guest lifecycle

### Resource pressure and boot recovery

Start troubleshooting a slow or memory-constrained host with the node summary,
then inspect the guests contributing to the load:

```bash
basaltw proxmox top pve1
basaltw vm stats fileserver
basaltw vm stats build-agent --json
```

`vm stats` reads Proxmox's provider-side counters and does not require the QEMU
guest agent. It shows current CPU and memory use, disk allocation, cumulative
disk/network I/O, and uptime. Warnings highlight high CPU, memory, disk, or
reported swap use. Treat one sample as a clue: compare several samples before
resizing a VM, and check `proxmox top` to retain enough host memory for
Proxmox itself.

After a power outage, starting every VM together can overwhelm an older disk
or a small CPU. Inspect and configure typed boot settings with the saved local
VM name:

```bash
basaltw vm autostart fileserver
basaltw vm autostart fileserver \
  --enable --order 1 --start-delay 30 --shutdown-timeout 120
basaltw vm autostart database \
  --enable --order 2 --start-delay 45 --shutdown-timeout 180
basaltw vm autostart build-agent --disable
```

Lower order values start first and stop last. Put infrastructure dependencies
such as storage and DNS first, give slow services enough delay, and leave
disposable build or test VMs disabled. Unspecified order and delay values are
preserved when autostart is enabled again.
Proxmox's shorthand `startup: 2,up=30` is read as `order=2,up=30`, so changing
only a delay preserves the order. Malformed, duplicate, or unknown startup
members block schedule edits instead of silently discarding existing settings.

For a small host, a practical routine is:

1. Run `proxmox audit` and `proxmox top` before maintenance.
2. Use `vm stats` on the busiest guests before adding CPU or memory.
3. Keep host memory and disk headroom instead of allocating every available
   unit to guests.
4. Stagger essential autostart guests and disable nonessential ones.
5. Keep verified backups; a VM snapshot is convenient rollback state, not a
   substitute for a backup on separate storage.
6. Prefer `vm shutdown` to `vm stop`; immediate power-off can damage guest
   filesystems and application data.

### Power-state commands

For a Basaltwater-provisioned VM, prefer its saved local name:

```bash
basaltw vm status agent-dev-01
basaltw vm start agent-dev-01
basaltw vm pause agent-dev-01       # alias: suspend
basaltw vm resume agent-dev-01
basaltw vm shutdown agent-dev-01 --timeout 60
basaltw vm stop agent-dev-01        # immediate; may cause data loss
basaltw vm reboot agent-dev-01      # alias: restart
```

These commands also accept an explicit registered provider host and VMID, and
`--json` returns the provider-neutral result envelope. `shutdown` is the clean
power-off path; `stop` exits the guest immediately. Shutdown and reboot pass
an optional timeout to Proxmox and all lifecycle commands report the observed
state after the provider operation completes.

For noninteractive shutdown and reboot commands, the SSH wait is at least
120 seconds and includes 60 seconds beyond an explicit native timeout. Terminal
sessions retain the interactive SSH passphrase policy. If a power command's
SSH wait expires, Basaltwater makes one noninteractive status check with a
30-second limit and reports an uncertain outcome as an error, even if the
observed state matches the request. Inspect the Proxmox task log and guest state
before retrying; Basaltwater does not replay the command or force a shutdown.

For LXC, `pause`/`suspend` invokes Proxmox's experimental `pct suspend`
checkpoint operation. It can fail on otherwise healthy containers, including
when CRIU cannot checkpoint nested namespaces created by systemd services.
Inspect the task error and observed state; do not assume the container paused.
Use graceful shutdown/start for reliable LXC maintenance, and keep confinement
enabled rather than weakening it to make checkpointing work.
An inactive LXC checkpoint appears as `stopped`; `resume` submits it to
`pct resume`, which reports a missing or invalid checkpoint. A running LXC
cannot be resumed. QEMU resume continues to require a paused guest; use `start`
for a stopped VM.

The provider-specific forms remain available for compatibility:

```bash
basaltw proxmox status pve1 101
basaltw proxmox start pve1 101
basaltw proxmox stop pve1 101
basaltw proxmox pause pve1 101
basaltw proxmox resume pve1 101
basaltw proxmox health pve1 101
```

Guest health probes use a validated static IPv4 address from `net0` (LXC) or
`ipconfig0` (QEMU). DHCP and missing or invalid addresses leave network probes
unavailable and the health result unhealthy; this command does not discover
DHCP leases or query the guest agent for an address. Probe timeouts must be
positive whole seconds.

For compatibility, the older `proxmox stop` command keeps its original
behavior: it requests a graceful shutdown unless `--force` is supplied. New
automation should use the explicit generic `vm shutdown` or `vm stop` form.

Show a summary for one or more nodes:

```bash
basaltw proxmox top pve1 pve2
```

The summary includes node CPU, memory, storage, and guest counts. It is a
read-only health and capacity view; `probe` should be run first when a host's
storage or bridge data has not been cached.

Run a maintenance-safety audit before planned work:

```bash
basaltw proxmox audit pve1 pve2
basaltw proxmox audit pve1 --json
```

The audit checks core Proxmox services, quorum on clustered nodes, active tasks,
configured storage, guest locks, running guests, and whether a reboot is pending.
Root must have at least 4 GiB free and `/boot` at least 256 MiB, including when
it uses a separate filesystem. Filesystems with fixed inode limits also need
at least 1024 free root inodes and 128 free boot inodes. Invalid or incomplete
capacity responses block maintenance, including automated kernel installation.
Task, guest, and storage inventories must return valid JSON; empty or malformed
responses block maintenance.
Every enabled storage pool must be accessible, while disabled pools are
reported separately. It also reports host RAM and swap use,
swap devices, swappiness, and whether the previous boot journal is retained.
The previous kernel journal is scanned for OOM kills, blocked tasks, lockups,
watchdog faults, storage failures, critical temperatures, and hardware-error
indicators. Routine thermal/EDAC initialization and watchdog shutdown messages
are omitted; the last 40 matching lines appear in text and JSON output.
A failed or empty kernel journal read produces a warning, even when the boot
itself is retained. ZFS zvol-backed
swap is a failing audit condition, while no swap, swappiness above 10, high
memory/swap use, and missing prior-boot evidence are warnings. The command is
read-only and exits nonzero when a health check fails.

When enabled LVM-thin storage is active, the audit also reads LVM's data and
metadata percentages for all active thin pools on the host. Either percentage
at 80% produces a warning; at 95% it fails the audit and blocks automatic
maintenance. These are Basaltwater's headroom thresholds, not LVM limits.
Missing pool measurements and disabled `dmeventd` monitoring produce warnings.
Monitoring alone does not prove automatic extension is configured or that the
volume group has free space. This check never extends, repairs, or fills a pool.

The audit also checks pool accessibility. It does not check ZFS pool or scrub
health, SMART/NVMe condition, detailed backup storage, certificate expiry, or
timer delivery. It does not prove that a backup is fresh, intact, stored
off-host, or restorable in isolation.

Proxmox SSH inspection commands reuse a short-lived OpenSSH control connection.
With an encrypted key, the first connection prompts for its passphrase and the
remaining checks reuse that authenticated connection. All Proxmox operations
allow that prompt when launched from a terminal; piped or parallel operations
require the key to be loaded into an SSH agent. See
[SSH authentication](ssh.md) for setup and troubleshooting.

Modify resources and configuration:

```bash
basaltw proxmox modify pve1 101 --cores 4 --memory 8G
basaltw proxmox reconfigure pve1 101 --set hostname=newbox
basaltw proxmox reconfigure pve1 101 --set balloon=4096
basaltw proxmox resize-disk pve1 101 rootfs 40G
basaltw proxmox resize-disk pve1 102 scsi0 40G
```

Reconfiguration and disk growth detect the guest type before submitting
container or VM arguments. Use `rootfs` or `mpN` for containers and the disk
slot (for example, `scsi0`) for VMs. VM disk growth increases the virtual disk;
grow its partition and filesystem inside the guest afterward.

`resize-disk` accepts an absolute positive integer size with a K, M, G, or T
suffix; lowercase suffixes are normalized to uppercase. Shrinking is rejected
by Proxmox. LXC growth uses `pct resize`. VM growth invokes the same native
resize API through `pvesh` and requires its returned task to be stopped with
exit status `OK`. A zero CLI exit code alone cannot establish VM resize success:
[`qm resize`](https://github.com/proxmox/qemu-server/blob/master/src/PVE/CLI/qm.pm)
does not propagate the asynchronous worker result through its task-exit callback.

Resize execution on the node is limited to one hour, followed by a ten-second
termination grace period. VM task verification has a separate 30-second limit.
SSH retains its normal interactive authentication policy; these remote limits
start after authentication. Noninteractive SSH waits are also bounded (3660
seconds for resize, 50 seconds for verification). A timeout, lost connection,
missing task ID, or unverifiable outcome reports an error and never replays the
mutation. Interrupting the wait does not prove cancellation or undo partial
storage changes. Inspect the Proxmox task log and disk state before retrying.
Large or unusually slow resizes exceeding this fixed limit require an
operator-managed native workflow. Automated coverage is mocked; live VM and LXC
verification remains an operator check.

`proxmox config HOST VMID --pending` and shell `config VMID --pending` show only
unapplied changes. Native `cur` records are omitted, `new` records appear under
the option name with their new value, and `del` records appear as `<deleted>`.
The deletion marker describes removal of that configuration option; it does
not assert that an associated storage volume will be erased. Parsing follows
[Proxmox's pending formatter](https://github.com/proxmox/pve-guest-common/blob/master/src/PVE/GuestHelpers.pm).

For an existing VM, Proxmox stores `memory` and `balloon` in MiB. Keep
`balloon` no greater than `memory`; setting both to the same value disables
dynamic reclamation without removing the balloon device.

Snapshots and rollback:

```bash
basaltw proxmox snapshots pve1 101
basaltw proxmox snapshot pve1 101 pre_upgrade --description "before kernel update"
basaltw proxmox rollback pve1 101 pre_upgrade
basaltw proxmox delsnapshot pve1 101 pre_upgrade
```

Snapshot deletion requires typing `yes`, or `--yes` for automation. `--dry-run`
previews the command without confirmation or deletion. The interactive shell
uses the same confirmation and dry-run options.
Snapshot names follow Proxmox's configuration ID format: 2–40 ASCII characters,
starting with a letter and continuing with letters, digits, underscores, or
hyphens (for example, `pre-upgrade`). See the
[native schema](https://github.com/proxmox/pve-common/blob/master/src/PVE/JSONSchema.pm).

`vm destroy` is permanent and asks for confirmation. For a Basaltwater
provisioned VM, use its exact saved local name; Basaltwater resolves the
registered provider host and VMID, then verifies the observed QEMU name and
configured IPv4 address before prompting:

```bash
basaltw vm destroy agent-dev-01
basaltw vm destroy agent-dev-01 --yes
basaltw vm destroy pve1 101
```

The provider host/VMID form is useful for a QEMU VM without saved local setup
metadata. `--yes` skips only confirmation, while `--force` force-stops a
running VM before destruction. The command verifies that the VM is absent
afterward and retains the saved setup declaration for deliberate
reprovisioning; remove that declaration separately with
`basaltwater rm agent-dev-01` when appropriate. The legacy
`basaltw proxmox destroy` path remains available during the broader guest
command migration.
