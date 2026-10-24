# Reusable Proxmox VM templates

Start with an immutable, never-booted vendor cloud image and full clones.
Keep each published template version unchanged; build a new version when the
image or hardware contract changes. This avoids inheriting another guest's
network configuration, authorization, application secrets, and cached identity.

Basaltwater currently provisions QEMU guests by importing a cloud image.
`--image` accepts an image source, not a template VMID. Template creation and
cloning currently use native Proxmox commands. `PROXMOX_TEST_TEMPLATE` in the
live suite names an **LXC template archive**. See [Proxmox operations](proxmox.md)
for supported provisioning, reconciliation, and lifecycle commands.

## Build a vendor-image template

Use a pinned Debian `genericcloud` image from the repository's cloud-image
catalog and verify its SHA512 before import. Download images and checksums
through Debian's official HTTPS endpoints. `genericcloud` has a smaller driver
set than `generic`; qualify VirtIO disk and network support on the target
hardware. Debian's separate `nocloud` image does not provide cloud-init.
See [Debian cloud image guidance](https://cloudfront.debian.net/cdimage/cloud/).

Check the selected node, available VMIDs, bridge, storage capacity, and thin-pool
data **and metadata** headroom before creating fixtures. The following native
example uses the msatabox lab's storage names; choose unused IDs and suitable
pools on another installation. Run these commands on the Proxmox node after
verifying the locally staged image against the catalog checksum:

```bash
qm create 9000 --name debian13-20260803-template \
  --memory 768 --cores 1 --cpu host --onboot 0 --ostype l26 \
  --scsihw virtio-scsi-single --net0 virtio,bridge=vmbr0 \
  --serial0 socket --vga serial0 --agent enabled=1
qm disk import 9000 \
  /mnt/pve/lab-hdd/import/debian-13-genericcloud-amd64-20260803-2559.qcow2 \
  lab-ssd --format raw
qm config 9000
# Use the actual imported unused0 volume from the output above.
qm set 9000 --scsi0 lab-ssd:vm-9000-disk-0,discard=on,ssd=1,iothread=1 \
  --ide2 lab-ssd:cloudinit --boot order=scsi0
qm resize 9000 scsi0 6G
qm config 9000
qm template 9000
```

Confirm that resize completed successfully and the disk reports the intended
size before conversion. Never boot this source VM. Do not put an address,
guest hostname override, SSH authorization, password, or custom user-data
snippet in the reusable template. Its Proxmox inventory name identifies the
template version; each clone gets its own inventory name and guest settings.
This follows the [Proxmox cloud-init workflow](https://github.com/proxmox/pve-docs/blob/master/qm-cloud-init.adoc).

Use `cpu: host` for this single-node Ivy Bridge lab. Its CPU lacks AVX2;
`x86-64-v3` and `v4` are unsuitable. A migration-capable deployment needs a CPU
model supported by every destination. See [Proxmox CPU models](https://github.com/proxmox/pve-docs/blob/master/qm.adoc#qemu-cpu-types).

LVM-thin imports use native raw block volumes. Cloud-image staging and custom
snippets still need file-capable storage, such as `lab-hdd` and `local` in this
lab. See [LVM-thin storage](https://github.com/proxmox/pve-docs/blob/master/pve-storage-lvmthin.adoc).

## Clone and configure before first boot

```bash
# Full clone, optionally onto another compatible storage pool.
qm clone 9000 101 --full 1 --name new-guest-101 --storage lab-ssd
# Linked clone on the template's pool, for a separate disposable guest.
qm clone 9000 102 --full 0 --name new-guest-102
```

Leave each clone stopped until its hostname, unique reserved IP, gateway, DNS,
authorization, and cloud-init user data are configured. Generated Proxmox
user data can use `--ciuser` and `--sshkeys`; a `cicustom user=...` snippet
replaces that generated user data and must supply its own authorization.
Enabling QEMU guest-agent hardware does not install the guest package: include
`qemu-guest-agent` installation and service startup in the guest bootstrap.
Allocate additional data and swap disks blank for each clone.

Full clones have independent root disks and can target another supported pool.
Linked clones reduce initial copying. Storage-specific dependency semantics
matter: qcow2 backing chains require their base, while Proxmox's native
LVM-thin implementation says thin clones survive base-volume removal. Retain
published template versions until their clones are retired as an operational
policy; do not infer a universal backing-file chain. Both clone modes remain
subject to the target pool's capacity and availability.
See [clone semantics](https://github.com/proxmox/pve-docs/blob/master/qm.adoc#clones)
and the [LVM-thin implementation](https://github.com/proxmox/pve-storage/blob/master/src/PVE/Storage/LvmThinPlugin.pm).

## Preserve the identity boundary

Verify unique VMIDs, MACs, SMBIOS UUIDs, cloud-init instance IDs, machine IDs,
SSH host keys, hostnames, and addresses across independently booted clones.
Proxmox supplies fresh virtual hardware identifiers; guest identity still needs
first-boot verification. NoCloud uses instance identity to decide when to
initialize a guest. See [NoCloud](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html).

Basaltwater's first-boot user data installs
`/etc/cloud/cloud.cfg.d/99-basaltwater-instance.cfg` with
`manual_cache_clean: true`. Successful cloud-init completion and this guard
must be verified before detaching custom user data and deleting its snippet.
Keep the guard and cache during ordinary reboot and identity-preserving restore.
Snapshots from before guard installation do not gain that protection.

Capturing an already configured guest is a separate image-building operation.
Use a disposable builder and remove inherited authorization, application
credentials and state, SSH host keys, static network configuration, leases,
and persistent network client identity as appropriate. Reset Basaltwater's
`99-basaltwater-network.cfg` and `99-basaltwater-hostname.cfg` overrides as well
as the instance guard. Then run `cloud-init clean --machine-id` and shut down
without another boot before capture. Merely deleting the guard does not clear
cloud-init's trusted cache. `--configs network` cleans cloud-init-generated
network files, not arbitrary Basaltwater files. Check the installed version's
supported flags. See [first-boot behavior](https://docs.cloud-init.io/en/latest/explanation/first_boot.html)
and the [clean command](https://docs.cloud-init.io/en/latest/reference/cli.html).

## Qualification and future Basaltwater support

The msatabox PVE 9 qualification used a never-booted Debian 13 vendor template,
two full clones on separate thin pools, and a linked clone. Qualification
checks include concurrent first boot, guest-agent and cloud-init completion,
correct static networking, unique identities, independent writes, root growth,
and stable identity and data after snippet detachment and reboot. These checks
do not establish multi-node migration, HA, other storage backends, or safe
capture of arbitrary configured guests.

Record image URL and checksum, node/storage/template identity, hardware
settings, cloud-init version, bootstrap schema, and Basaltwater revision for
each published version. Current bootstrap installs package updates, so an image
checksum alone does not reproduce the installed package versions.

A future template provisioning option should validate source provenance and
clone mode, customize stopped clones, allocate blank additional disks, retain
capacity and concurrency guards, and reuse cloud-init guard verification and
SSH host-key enrollment. Failure cleanup must remove only the new clone and
its owned resources while preserving the source and siblings. Qualify this
failure path before automating template provisioning. Templates save repeated
image import; guest package installation and configuration still take time.
