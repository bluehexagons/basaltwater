# Reconcile and inspect existing Proxmox guests

[Topic index](proxmox-host-safety.md)

Named mounted disks may also be added to an existing QEMU VM that has saved
Basaltwater provisioning metadata. Start with its reconstructed setup command,
or supply the same target, system type, setup user, and `--provision-on` value,
then add only the new disk and mount declarations:

```bash
basaltw setup server_lite 10.0.0.60 admin \
  --provision-on pve1 \
  --storage team-data bulk-lvm 512G \
  --storage-mount team-data /srv/team-files ext4 empty
```

The saved root disk, existing data disks, mounts, provider identity, and
network defaults are merged automatically when omitted. Existing declarations
may instead be repeated unchanged. Basaltwater verifies every old managed disk
before mutation, checks capacity for only the additions, uses the first free
SCSI slots without touching unrelated disks, and verifies each new stable
`bw-NAME` identity. All requested slots, storage capacity, and any accompanying
memory-floor policy are checked before the first attachment. Target setup then
formats only a blank exact-identity disk and requires an empty mount path.
Multiple new mounted disks may be declared in one command. The same concise
command remains valid after success and is treated as an idempotent subset of
the saved layout. If one attachment succeeds before a later provider response
fails, a retry recognizes the attached identity and continues safely; saved
setup metadata changes only after remote setup succeeds.

Saved swap declarations and policy are retained when omitted, including on
`--verify-provider` reruns. Explicit swap options keep their normal replacement
and clearing semantics; one-time `--swap-initialize` authorization is never
inherited.

This additive path does not adopt a manually attached disk, replace a missing
saved disk, attach data storage to LXC, add `/home` to an existing VM, create
new cache or swap media, or detach or resize a disk. Supported empty mount
targets are `/data`, `/home` on a newly provisioned QEMU VM, or paths below
`/srv`, `/var/lib`, `/opt`, and `/mnt`. For `/home`, cloud-init defers creation
of the setup user until the blank disk has been mounted. Populated-path
migration is still rejected. `--image-storage` remains only the staging pool
for the VM image; it is not guest data storage. An existing unsaved VM is not
treated as permission to adopt or add disks.

Inspect a provisioned VM by its saved local `--name`:

```bash
basaltw vm show agent-dev-01
basaltw vm health agent-dev-01
```

The explicit provider host and VMID form remains available:

```bash
basaltw proxmox ls pve1
basaltw vm show pve1 100
basaltw vm health pve1 100
```

Replace `100` with the VMID returned by `proxmox ls`.

`--rdp` requires `--password` for the non-root desktop account. Set
`RDP_PASSWORD` in a secure secret source rather than placing a literal in
shell history; the password is not persisted in saved setup state.

After a guest has been provisioned, a later `setup` invocation can retain
`--provision-on`. If the target has saved local setup metadata, Basaltwater
reuses its recorded Proxmox details and skips the full provider-shape check
before updating the guest. It still identifies the guest on the authenticated
Proxmox node and verifies its host key before the first direct connection.
Guest-shape options emitted by a saved reconstructed command are also
accepted when they match that metadata. Changing any of
`--machine`, `--bridge`, `--memory`, `--balloon-min`, `--balloon-shares`,
`--allow-memory-overcommit`, `--storage`, `--cores`, `--cpu-type`,
`--base`, `--image`, or `--image-storage` requests a provisioning check instead.
Supplying any `--disk-discard`, `--disk-ssd`, or `--disk-backup` policy also
requests a provider check even when it matches local metadata. This lets an
explicit setup rerun repair disk-hardware drift or apply policy first recorded
by an older basaltwater release. A target without saved provisioning metadata
still requires `--memory` and root `--storage` on its first run. Any provider
check associated with saved VM metadata requires that VM to still exist;
Basaltwater does not silently create a replacement when reconciliation cannot
find it.

Use `--verify-provider` to check a cached provisioned guest against Proxmox even
when its declaration matches saved local metadata. Setup reconciles and
verifies the provider-side VM name, vCPU count, memory maximum, balloon
minimum, balloon shares, CPU model, and managed hardware hints on every SCSI
disk declared by logical name. Root is identified as `scsi0`; named data and
cache disks are identified by stable `bw-NAME` or retained `it-NAME` serials. Unrelated
manually attached SCSI disks are not modified. Disk reconciliation preserves
the existing volume reference, size, serial, and unowned options. Before
changing memory, setup repeats the host
capacity check while replacing the existing VM's allocation rather than
counting it twice. An unsafe balloon floor remains blocked unless
`--allow-memory-overcommit` is explicit. The check uses current running guest
configurations and retains the larger current or proposed allocation for the
VM being changed, so a pending decrease does not count as freed memory.
A running VM may need to be restarted before the guest observes a vCPU,
memory-maximum, CPU-model, or disk-hardware change; setup reports that
requirement without interrupting the guest automatically.

If a managed VM disk was moved to another pool or resized outside Basaltwater,
update its `--storage` declaration and rerun setup. As long as the declaration
retains the same logical disk names, setup bypasses the stale cached layout and
requires Proxmox to report each disk on the requested pool at or above the
requested size before saving the new declaration. `--verify-provider` performs
the same storage check when the declaration itself is unchanged. This adopts
verified metadata drift only: setup still does not move, resize, or detach an
existing disk. New named mounted disks are the exception and use the additive
workflow above.

After a GUI migration, an out-of-band move, or a move from another controller,
rerun the saved setup command with the new
`--provision-on` destination. Basaltwater keeps the explicit destination host,
credentials, and bridge instead of restoring the cached source binding. It
requires the old source VM to be stopped or absent and the destination to have
the saved IPv4 address, VM name, and managed-disk identities. If storage pools
or live capacities changed, repeat `--storage` with the new values and
unchanged logical names. Every rebind verifies the destination bridge, pool,
and minimum size of each declared disk. A missing destination is rejected
instead of creating a new VM; a matching stopped destination is started only
after those checks and a fresh memory-capacity check, even when its memory
declaration is unchanged, and is allowed time for SSH to return. The saved
provider binding changes only after remote setup succeeds. A replacement clone can
follow this path while the original remains stopped; a coexisting clone needs
a unique address, hostname, and saved setup identity.

Existing-guest setup does not replace, resize, or detach root/data volumes, and
does not mutate bridges, cache declarations, base images, or image staging
storage. It may add explicitly declared blank mounted data disks through the
saved-guest workflow above. It may also update the managed discard, SSD,
I/O-thread, and backup hints in place while verifying that the volume
reference, size, serial, and other unowned options remain intact.
When saved metadata shows that another provisioning-only declaration changed,
setup rejects the rerun instead of saving a value it did not apply. Use the
explicit `vm` or `proxmox` management command for a supported lifecycle change,
or provision a replacement guest.

During a saved VM provisioning check, the configured IPv4 address must match
a unique VM with the saved or requested Proxmox name. If the corrected
declaration changes its Proxmox name, Basaltwater applies and verifies the
rename before continuing setup. Setup rejects a desired name already owned
by a different VM. Repeating the saved `--name` and `--hostname`
values is an ordinary idempotent rerun and does not perform a full provider
check. Changing `--hostname`, or changing `--name` when it supplies the VM
hostname, performs the identity check. Regardless of whether a full
provisioning check is needed, Basaltwater verifies the saved guest's SSH host
key through the authenticated Proxmox node before checking the guest route or
starting remote setup. A guest without matching saved metadata
still requires explicit host-key enrollment. Use the explicit host and VMID
forms to inspect or repair provider-side drift and duplicates created by older
releases.
