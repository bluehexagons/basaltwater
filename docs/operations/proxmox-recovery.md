# Recovery, notifications, and smoke checks

[Documentation index](proxmox.md)

## Orphaned volumes and stuck locks

List unreferenced guest volumes before deleting anything:

```bash
basaltw proxmox clean-disks pve1 --dry-run
basaltw proxmox clean-disks pve1 --delete
```

`clean-disks` is list-only by default. `--delete` requires typing `yes` unless
`--yes`/`-y` is supplied; treat it as destructive because an orphaned-volume
check cannot infer whether an external workflow still needs a volume.

Cleanup requires consistent local and cluster guest inventories, effective
storage sharing metadata, replication configuration, and successful volume
listings from every active pool. It reads all QEMU and LXC configuration files
under `/etc/pve/nodes` using the host's Python 3, including pending, unused, and
snapshot sections. A volume referenced by another guest is protected even when
its reported owner VMID is absent. Configured storage IDs, including disabled or
node-restricted IDs, are read separately so references through an unlisted alias
are checked too. Native volume references and absolute host-path references are
compared with Proxmox volume paths and backing identities: referencing a disk
through one storage ID protects aliases of that disk under other IDs.
Missing, unreadable, malformed, or inconsistent
inventories abort cleanup; empty native guest lists are accepted when the other
inventories agree.

Backing comparisons require resolvable volume paths; inactive LVM devices with
missing aliases abort cleanup rather than guessing device identity. VirtioFS
directory mappings are resolved on the inspected node and protect volumes that
contain, equal, or lie beneath the shared directory. A missing or invalid mapping
aborts cleanup. A guest using native volume IDs, absolute host paths, or VirtioFS
on another node conservatively protects all shared-storage candidates because its paths and
aliases cannot be resolved locally. References on another node still do not
protect unrelated node-local volumes.
Custom QEMU `args` in any configuration section conservatively protect all
candidates in that guest's storage scope. These arguments can encode paths in
quoted strings or backend-specific syntax that the cleanup scan cannot fully
interpret; remove or review them separately before reclaiming those volumes.
During backing comparisons, native userspace RBD identifiers are conservatively
retained because they do not establish filesystem/device identity; unrelated
local-storage candidates remain eligible. An unresolved userspace RBD reference
conservatively protects all candidates. Unknown path formats still abort cleanup.

For shared storage, an owner or reference on any cluster node protects the disk.
For node-local storage, only owners and references on the inspected node count:
the same storage ID on another node can name different physical storage.
Configured replication jobs conservatively protect their guest's volumes,
including disabled jobs and jobs awaiting removal. Review those jobs separately
before reclaiming old replicas.

Every deletion repeats the complete scan and refuses a volume that is no longer
orphaned. Keep provisioning, migration, replication changes, and other guest
configuration changes paused during cleanup: inventory and deletion are separate
Proxmox operations. External workflows and storage dependencies outside guest
configuration still require manual review. CLI partial failures return nonzero;
the shell stops cleanup and reports the failure.
`--dry-run` is honored in both interfaces, including with `--delete --yes`.

After confirming that no backup, migration, or snapshot task is still active,
clear a stale Proxmox management lock:

```bash
basaltw proxmox unlock pve1 101 --dry-run
basaltw proxmox unlock pve1 101
```

The unlock operation only clears the guest lock; it does not repair a failed
underlying task or roll back partial storage changes.

## Cluster and notifications

```bash
basaltw proxmox probe-cluster 10.0.0.10 \
  --key ~/.ssh/proxmox_ed25519 --tag prod
basaltw proxmox hosts
basaltw proxmox audit pve1 pve2 pve3
basaltw proxmox rolling-update pve1 pve2 pve3
basaltw proxmox notifications install-webhook \
  pve1 https://notify.example/hook --send-test
basaltw proxmox notifications test-webhook pve1
basaltw proxmox shell
```

`probe-cluster` discovers nodes from Proxmox's configured names and seeds the
host registry. `rolling-update` reuses saved setup commands and workspace
credentials. It audits every target before changing any node, repeats the audit
immediately before each node update, after each update and reboot, and advances
only after verification. Evacuate or shut down guests before starting; running
or locked guests block updates even without a pending reboot. HA resources,
local Ceph configuration, and RBD/CephFS storage require operator-managed
maintenance and are rejected by this command. It does not automate evacuation,
HA maintenance mode or Ceph flags. These configuration locations follow the
[Proxmox administration guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf).
Repository/major-upgrade suitability and backup recovery remain operator checks.

SSH policy/reboot commands have 30-second limits; reachability probes use at
most 15 seconds and the remaining reboot budget. The first failure stops later
nodes. The workspace `cluster-update.json` stores per-node phases/results under
an exclusive lock. Inspect failed targets before resuming with the same ordered
names and unchanged saved configuration:

```bash
basaltw proxmox rolling-update pve1 pve2 pve3 --resume
```

Completed nodes are skipped; known failed patch runs can be retried explicitly.
A crash during `patching` or `rebooting` has an uncertain outcome and refuses
automatic replay. Confirm the old process has stopped, inspect target-side setup
markers, and finish/verify that node manually. After successful manual patch
recovery, set only its checkpoint phase to `patched`; after a verified reboot,
set it to `rebooted`. Resume repeats health/policy checks before proceeding.
Archive checkpoints privately if abandoning the operation; leave the stable
lock file in place. Success writes `cluster-update-last.json` and clears the
active marker. Dry runs do not create or consume checkpoints.

`notifications install-webhook` configures Proxmox's native notification
matcher; repeat `--severity` to limit routing, and use `--dry-run` before
writing the endpoint. Treat webhook URLs as sensitive values.
Command previews redact the entire URL, including userinfo and path tokens.
Notification commands suppress raw native output and transport exception details
because those can echo credentials. Failures report the affected host and exit
status when available; inspect notification configuration on that node for
diagnosis. A timeout leaves completion uncertain and does not replay the command.
Malformed URLs, invalid ports, and control characters are rejected locally
without echoing the URL in validation errors.
Endpoint and matcher names must differ because Proxmox shares their namespace.
The JSON payload preserves event metadata in the `details` string, including
quotes and line breaks. The built-in test has empty metadata; also check delivery
from a real backup event when validating a receiver and severity routing.

## Network planning

Use the separate [Network inventory](networking.md) workflow to build a
workspace-scoped inventory and render a read-only Proxmox firewall plan before
applying any control-plane lockdown.

## Real-system smoke test

For a first rollout, validate one VM and one LXC compatibility path:

```bash
basaltw proxmox hosts
basaltw proxmox probe pve1

basaltw setup workstation_dev 10.0.0.50 devuser \
  --provision-on pve1 --memory 8G --storage root 40G --cores 4 \
  --name dev-01-vm --rdp --password "$RDP_PASSWORD"

basaltw proxmox ls pve1
VMID=100  # replace with the VMID returned by `basaltw proxmox ls`
basaltw proxmox health pve1 "$VMID"
basaltw proxmox snapshot pve1 "$VMID" pre-modify
basaltw proxmox modify pve1 "$VMID" --cores 6 --memory 12G
basaltw proxmox stop pve1 "$VMID"
basaltw proxmox start pve1 "$VMID"

basaltw setup server_lite 10.0.0.60 appuser \
  --machine unprivileged --provision-on pve1 \
  --memory 2G --storage root 10G --storage template
```

For the VM path, verify SSH and optional RDP access. For LXC, focus on basic
provisioning and guest management; advanced desktop behavior is most reliable
on a VM.
