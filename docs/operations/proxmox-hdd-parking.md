# Park an idle Proxmox VM on HDD storage

Move a stopped VM's disks to a slower pool when it will be idle for an extended
period. Keep its VMID, network configuration and guest identity, then shut it
down with autostart disabled. This frees SSD capacity and guest RAM while
retaining a bootable machine. Keep a backup on independent physical storage:
moving the live disks changes their location. A backup on the same HDD as the
parked disks does not protect against that drive failing.

This guide uses native Proxmox disk moves followed by Basaltwater's existing
storage verification. Basaltwater does not have a same-node parking command.
Its `proxmox migrate` command moves guests between nodes.

## Prepare

Inspect the VM on its Proxmox host:

```bash
qm config 120
qm listsnapshot 120
pvesm status
```

Record every required volume, including data disks, cloud-init, EFI variables
and TPM state where present. Check attached installation media and external
devices too. Use a destination with the required content types and enough free
space; a directory pool needs **Disk image** (`images`) enabled for QEMU disks.
When changing content types, retain the pool's existing uses. A mounted HDD
pool should retain its mount-point guard.

If normal I/O reports media or unrecoverable read errors, stop the parking
workflow and assess recovery before moving or booting affected guests. Free
capacity, an active storage status, and a passing SMART summary do not establish
that the data can be read. The normal move procedure below assumes readable
source storage.

The tested lab workflow used a full-clone QEMU VM without snapshots, moved from
LVM-thin raw storage to an ext4 directory pool. Linked clones, snapshot history,
EFI/TPM, passthrough and LXC require their own checks. Keep reusable templates
unchanged when parking a guest.

Record a few representative file hashes and the machine ID, SSH host key and
filesystem UUID for comparison after the move. Quiesce the VM's workload, shut
it down gracefully, confirm the stopped state, and disable autostart:

```bash
qm shutdown 120 --timeout 120
qm status 120
qm set 120 --onboot 0
```

Create and confirm a backup before removing the SSD copy. For a registered host
and backup-capable storage pool:

```bash
basaltw proxmox backup pve1 120 --storage archive --mode stop --timeout 3600
```

## Move and verify

Run on the Proxmox host, replacing the VMID, disk keys and destination pool:

```bash
qm disk move 120 scsi0 archive --format qcow2 --delete 1
qm disk move 120 ide2 archive --format raw --delete 1
```

The second command applies when `ide2` is the VM's cloud-init disk. Move each
other required volume deliberately. The examples use `--delete 1`, which removes
the source volume after a successful copy. Omit it to retain the source as an
unused disk until after boot verification; that copy continues using SSD space.
Remove a retained copy through Proxmox only after identifying its exact volume
and verifying the active disk.

Check the native task's final **OK** result and the new references in
`qm config 120`. A CLI return code alone is insufficient when a worker's result
is uncertain. After a timeout or lost connection, inspect the task and current
configuration before retrying. Do not copy or rename backing files directly.
See the [native disk-move reference](https://pve.proxmox.com/pve-docs/qm.1.html).

Start the guest and verify its identity, networking, representative files,
applications and storage:

```bash
qm start 120
```

For a managed guest, rerun its saved Basaltwater setup command with the new
`--storage root archive 10G` declaration, retaining the actual size, logical
disk names and all other desired settings. Repeat declarations for moved data
disks. Add `--verify-provider`; use `--no-disk-ssd root` for a root disk now on
HDD, preserving any SSD hints for disks still on SSD.

Setup verifies the observed pool and minimum size before adopting the changed
declaration. It does not move an existing disk for you. The old pool declaration
with `--verify-provider` should fail while the disk is on the new pool. Setup
may start a stopped managed VM, so finish reconciliation before leaving it
parked. Check any pending hardware changes and verify them after the next boot.

## Leave parked or return to SSD

After verification and reconciliation:

```bash
qm shutdown 120 --timeout 120
qm status 120
```

Confirm that autostart remains disabled and all required disks reference the HDD
pool. Keep the tested wake-up command and updated saved configuration. External
automation can still start a VM; disabled autostart controls host boot behavior.

To return it to SSD, shut it down and use native disk moves to the SSD pool,
choosing a format that pool supports. Update the saved storage declarations and
SSD hints, then repeat provider, identity and application checks. The initial
lab result covers HDD parking and wake-up; reverse moves remain a separate
[coverage case](../plans/testing-coverage.md).
