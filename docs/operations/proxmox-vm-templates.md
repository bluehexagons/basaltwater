# Reusable Proxmox VM templates

Use an immutable, never-booted Debian vendor image as the first reusable VM
template. Each clone receives its own first-boot configuration. This avoids
capturing accounts, credentials, static networking, or cloud-init cache from a
configured server. Basaltwater's normal provisioning imports a pinned image;
it does not yet provide VM template creation or clone flags. The commands below
are an operator-managed Proxmox workflow, separate from LXC archive templates.

## Build an unbooted template

Follow [Proxmox's cloud-init workflow](https://github.com/proxmox/pve-docs/blob/master/qm-cloud-init.adoc).
Choose unused VMIDs, a bridge, and storage suitable for the node. Examples use
template `9000`, clone `9101`, bridge `vmbr0`, file storage `local`, and
LVM-thin disk storage `local-lvm`; substitute the actual names and addresses.
Inspect host RAM and both thin-pool data and metadata headroom first. Native
commands do not perform Basaltwater's provisioning admission checks.

Download a pinned Debian `genericcloud` image over HTTPS and verify its SHA512
against the matching entry in [`lib/cloud_images.py`](../../lib/cloud_images.py).
Use `import`-capable file storage for staging; snippets also require file storage.
Block-only LVM-thin storage holds VM disks, not downloaded images or snippets.
Debian's distinct `nocloud` image does not run cloud-init; see the
[Debian image guidance](https://cloud.debian.org/images/cloud/).

On the Proxmox node, create the VM and import the verified image without
starting it:

```bash
qm create 9000 --name debian13-20260803 \
  --memory 1024 --cores 2 --cpu host --onboot 0 \
  --scsihw virtio-scsi-single --net0 virtio,bridge=vmbr0,firewall=1 \
  --serial0 socket --vga serial0 \
  --agent enabled=1,freeze-fs-on-backup=1 --rng0 source=/dev/urandom

qm set 9000 \
  --scsi0 local-lvm:0,import-from=local:import/debian-13-genericcloud-amd64-20260803-2559.qcow2,discard=on,ssd=1,iothread=1 \
  --ide2 local-lvm:cloudinit --boot order=scsi0
```

`ssd=1` is an explicit backing-device policy; omit it for HDD-like storage.
Use `cpu: host` on a single node when portability is unnecessary. Older CPUs
may lack AVX2 and cannot run an `x86-64-v3` guest model; see
[Proxmox CPU types](https://github.com/proxmox/pve-docs/blob/master/qm.adoc#qemu-cpu-types).
Debian `genericcloud` has fewer drivers than `generic`; qualify VirtIO devices
on the target hardware rather than assuming support for other device types.

If a larger minimum root disk is needed, grow it before conversion using the
verified resize command **on the controller**:

```bash
basaltw proxmox resize-disk pve1 9000 scsi0 10G
```

Require success before continuing. The native `qm resize` CLI can return zero
after its worker fails; Basaltwater verifies the native task outcome. The
guest's cloud-init growth modules expand its root partition/filesystem at first
boot. Shrinking is unsupported.

Back on the node:

```bash
qm template 9000
qm config 9000
```

Require `template: 1`, no running source, and no `ipconfig`, `ciuser`, `sshkeys`,
password, or `cicustom` fields. Record the image URL/SHA512, node, template
VMID, disk storage, hardware settings, bootstrap revision, and Basaltwater
revision in a provenance manifest. Keep this version unchanged; publish a new
template version when the image or bootstrap contract changes. Package updates
during first boot mean the image checksum alone does not reproduce installed
package versions.

## Customize each stopped clone

Start with a full clone:

```bash
qm clone 9000 9101 --name debian-lab-a --full 1 --storage local-lvm
```

Use a new VMID, name and address for every clone. Before its first boot, configure
networking and a unique cloud-init snippet. In this example, create
`/var/lib/vz/snippets/debian-lab-a.yaml` root-owned with mode `0600`; obtain the
directory using `pvesm path local:snippets/debian-lab-a.yaml` when `local` is
relocated. Replace both example public keys with the intended controller key
and use the clone's actual hostname:

```yaml
#cloud-config
hostname: debian-lab-a
manage_etc_hosts: true
package_update: true
packages:
  - qemu-guest-agent
write_files:
  - path: /etc/cloud/cloud.cfg.d/99-basaltwater-instance.cfg
    owner: root:root
    permissions: '0644'
    content: |
      manual_cache_clean: true
users:
  - name: root
    lock_passwd: true
    ssh_authorized_keys:
      - 'ssh-ed25519 REPLACE_WITH_CONTROLLER_PUBLIC_KEY'
  - name: agent
    groups: sudo
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - 'ssh-ed25519 REPLACE_WITH_CONTROLLER_PUBLIC_KEY'
ssh_pwauth: false
runcmd:
  - systemctl enable --now qemu-guest-agent
```

The persistent `manual_cache_clean` guard preserves this instance when its
temporary custom user-data is detached. It belongs in a system configuration
file, not only at the top level of user-data. See the
[cloud-init identity contract](proxmox.md#graphical-vm-hardware-baseline) before
reusing a booted guest as an image.

Configure and start the clone on the node:

```bash
qm set 9101 --ipconfig0 ip=10.0.0.51/24,gw=10.0.0.1 \
  --nameserver 1.1.1.1 --cicustom user=local:snippets/debian-lab-a.yaml
qm start 9101
```

Wait for the guest agent, then verify cloud-init and the guard:

```bash
qm agent 9101 ping
qm guest exec 9101 --timeout 190 --synchronous 1 -- /bin/sh -c \
  "timeout 180 cloud-init status --wait && grep -qxF 'manual_cache_clean: true' /etc/cloud/cloud.cfg.d/99-basaltwater-instance.cfg"
```

Require the guest-exec JSON to report `exited: 1` and `exitcode: 0`; the command's
transport status alone does not prove guest success. If verification fails,
preserve the clone and referenced snippet for inspection. Only after verified
success, detach `cicustom`, confirm it is absent with `qm config 9101`, and
remove that clone's snippet file. Do not delete a still-referenced snippet.

Read the guest's ED25519 public host key through the authenticated node/guest
agent, verify or enroll it, then use strict SSH checks. Do not accept an
unauthenticated controller-side scan as the trust source. Run the desired
Basaltwater setup profile against the clone after the bootstrap checks.

## Full and linked clones

A full clone copies the template disks and can use another compatible target
storage. A linked clone uses backend-specific cloning and is useful for
short-lived experiments on the same pool:

```bash
qm clone 9000 9102 --name debian-lab-b --full 0
```

Customize this clone independently before starting it. Directory-backed qcow2
linked clones depend on backing files. LVM-thin uses native thin-volume clones,
not a qcow2 chain; the
[Proxmox LVM-thin implementation](https://github.com/proxmox/pve-storage/blob/master/src/PVE/Storage/LvmThinPlugin.pm)
explicitly allows a clone to survive base-volume deletion. Retain immutable
template versions by policy and qualify the deployed backend before retiring
one. Both clone modes still depend on their storage pool's health and capacity;
neither is an independent backup.

## Qualification and remaining work

On a Proxmox VE 9.2 lab node with LVM-thin and the pinned Debian 13 image, two
full clones and one linked clone completed independent cloud-init bootstraps.
Qualification checks compare MAC addresses, SMBIOS UUIDs, machine IDs, cloud-init
instance IDs, and SSH host keys between guests. After snippet detachment, reboot
each clone and require its identities, intended networking, and strict SSH
access to remain stable. Filesystem UUIDs may be inherited from the vendor
image; they are not a substitute for checking guest identity.

Capturing a configured Basaltwater guest is a separate image-building workflow.
The persistent guard and cloud-init cache must be cleaned, and managed network
and hostname-preservation files, inherited network state, keys, credentials,
and application data need explicit preparation. Removing the guard alone does
not reset cloud-init's manual-clean cache marker. Do not capture an ordinary
server and assume it is safe to clone. See
[cloud-init first-boot detection](https://docs.cloud-init.io/en/latest/explanation/first_boot.html).

A future Basaltwater clone feature should validate template provenance and
clone mode, customize only a stopped clone, retain capacity and identity locks,
reuse bootstrap/guard verification, and clean up only the new clone on a
confirmed failure. It must preserve the template and sibling guests when
completion is uncertain. Cross-node migration, other storage backends, and
managed-guest image capture need separate qualification.
