# Set up and join hosts

[Documentation index](proxmox.md)

## Quick setup: Proxmox host to coding VM

[Install Basaltwater](../getting-started/installation.md) on a trusted Linux orchestration machine first. The
Proxmox host does not need a checkout; setup uploads the installed source to
`/opt/basaltwater`.

```bash
basaltw channel
```

If the orchestration machine is itself a Proxmox VM, install and activate its
guest agent during local self-setup:

```bash
sudo basaltw self-setup --qemu-guest-agent
```

The option installs `qemu-guest-agent` and runs
`systemctl enable --now qemu-guest-agent`. VMs provisioned by Basaltwater
already receive the same package and service configuration through cloud-init.

Set up, register, and inspect the host:

```bash
basaltw setup server_proxmox 10.0.0.10 root \
  --key ~/.ssh/proxmox_ed25519 \
  --name pve1

basaltw proxmox probe pve1
basaltw proxmox top pve1
basaltw proxmox ls pve1
basaltw vm list pve1 --json
```

Successful `server_proxmox` setup registers the host in the selected workspace;
`--name` supplies the registry name and defaults to the host address when omitted.
New host records explicitly store `schema_version: 1` and
`provider: proxmox`. Records produced by earlier development builds are not
silently reinterpreted. If loading the registry reports an unsupported schema
or missing provider, run `basaltw proxmox remove NAME` using the name shown
in that record, then run the normal `server_proxmox` setup or
`basaltw proxmox add` command again. Removal matches only the stored name
or address and can delete an incompatible record without interpreting it.
This is an intentional breaking-release boundary, not a credential or
guest-data migration.

The matching `.pub` file must sit beside the private key, and the Proxmox
`root` account must accept that key.

## Optional host web panel

Add `--web-panel` to install the authenticated Basaltwater panel on this
Proxmox host. Its dashboard covers visible cluster nodes, guests, storage and
recent task activity, with guest search/filter/sort, per-node guest allocations,
node capacity/status, local-node guest addresses and validated application
links from saved Notes, local-node ZFS health and cluster Ceph health when
configured. Guest, node, storage and task links open their native Proxmox
details; **Open Proxmox VE** opens the full panel. A separate root collector
publishes a sanitized read-only snapshot; the web worker remains unprivileged
and cannot control guests. Host metrics, services, mounted storage, scheduled
jobs and Proxmox service/firewall diagnostics are also available. See the
[web panel reference](../services/web-panel-status-reference.md#host-snapshot) for
scope, collection limits and troubleshooting. Rerun panel setup on an existing
installation to install the updated service and collector. Dashboard refreshes
read only the cache; guest management uses Proxmox's own UI and login.

The port is optional: without `--ssl`, it defaults to 80; with `--ssl`, it
defaults to 443. Choose another available panel port with
`--web-panel PORT`; port 8006 is reserved for the native Proxmox UI and is
rejected for the panel. The Proxmox UI remains on 8006 and appears as a link in
the dashboard. To opt in and serve the panel over TLS on port 9443, use:

```bash
basaltw setup server_proxmox 10.0.0.10 root \
  --key ~/.ssh/proxmox_ed25519 --name pve1 \
  --web-panel 9443 --ssl
```

With `--ssl`, the Proxmox path installs Nginx and OpenSSL as needed, then
creates or reuses a panel certificate independently of Proxmox's `/etc/pve`
certificate. The panel serves HTTPS on the selected port and exposes a
certificate-only CA enrollment and download listener on TCP port 8442. This
is public CA enrollment, not an ACME issuance listener; it does not require
port 80, and the panel does not use the shared web origin on port 8443. The
panel displays the CA fingerprint; on the host, verify it with
`sudo sha256sum /srv/basaltwater/web/basaltwater-ca.crt`. Clients can install
the downloaded CA certificate to trust the panel using [Client CA
trust](../services/client-ca-trust.md). This Proxmox TLS path does not install
or configure the `basaltwater-web` publisher/control helper, its web policy,
or NSS trust.

Setup prepends native Proxmox node-level `ACCEPT` rules for TCP traffic to the
selected panel port and, with TLS, port 8442. The rules allow only those ports
and the selected sources (`--access-source` or `--lan-access`); without a
source restriction, they are unrestricted. Each owned rule has the exact
comment `Managed by basaltwater web panel`. Setup does not switch the firewall
engine, enable or disable the global firewall, or change the cluster-wide
`management` IP set. Existing cluster, node, upstream, or operator `DROP`
policy can still block connections, so verify effective access through your
existing firewall policy before relying on the panel. Other Proxmox UI and SSH
firewall policy remains operator-managed.

Remove the panel with `--no-web-panel`. This removes only the owned panel
firewall rules, Nginx site, Nginx systemd isolation drop-in, panel backend and
configuration, Proxmox inventory collector/timer/snapshot and Fail2ban
configuration. It preserves the shared CA, Nginx/OpenSSL packages, native
Proxmox UI and configuration, and unowned firewall rules.

New VM provisioning uses an SSH identity's `.pub` file for cloud-init. The key
is installed for both root and the configured guest setup username. Cloud-init
keeps their passwords locked and SSH password authentication disabled; target
setup can subsequently configure an explicitly requested user password. Key-only
root SSH is the stable privileged setup channel for route repair and remote
setup handoff. The guest user joins `sudo`, but receives an unrestricted
managed `NOPASSWD` rule only when `--nopasswd` is explicit. This also lets a
rerun enable or remove that rule without depending on its previous state.
Existing-only reconciliation of a saved VM uses its established SSH access and
does not require the matching `.pub` file to remain on the controller. `--key`
is optional: Basaltwater first uses a matching key
associated with the registered Proxmox host, then the local
`~/.ssh/id_ed25519`, `id_ecdsa`, or `id_rsa` key.
If the Proxmox node key and guest key differ, pass `--provision-key` for the
node and `--key` for the guest. A registered host without a key retains
OpenSSH's default identities and SSH configuration; a guest `--key` does not
replace that host policy. Use `--provision-key` for an explicit node-key override.
VM image downloads and cloud-init snippets are
placed through Proxmox storage APIs. By default, image staging prefers an
active file-based storage with `import` content and falls back to `iso` content;
use `--image-storage STORAGE` to choose the source storage explicitly. The
selected node must also have active `snippets` content for cloud-init data.
The `server_proxmox` setup profile enables snippets on active `local` directory
storage when the node has no snippets pool configured; see the host defaults below.
Custom VM image URLs must use HTTPS and include a matching 128-character
SHA-512 value with `--image-sha512`; the curated Debian catalog carries pinned
hashes automatically. New and cached downloads both require a matching digest
before import. See [VM image selection](../reference/machine-types.md#provisioning-a-proxmox-vm).

For a new or saved managed guest, Basaltwater reads the Ed25519 host key from
the selected VMID or CTID over authenticated Proxmox SSH. VMs need a working
QEMU guest agent. A conflict in either trust file or a failed management-channel
read stops setup before the first direct guest login. For explicit enrollment,
rotation and address reuse, see [SSH authentication](ssh.md#proxmox-guest-host-keys).

Guest SSH readiness accepts TCP reachability from either the Proxmox node or
the controller. Source-restricted guest firewall rules can permit only the
controller because key enrollment uses the Proxmox management channel.

## Joining a cluster with two-factor authentication

Use an enrolled cluster member's root SSH access to join an empty registered
node, including when `root@pam` and other users have 2FA enabled:

```bash
basaltw proxmox join-cluster pve-new --via pve1 --dry-run
basaltw proxmox join-cluster pve-new --via pve1
```

Both names refer to registered hosts in the selected workspace. Run the normal
`server_proxmox` setup first, or use `proxmox add` and explicitly enroll each
host's SSH key. Both nodes must run supported Proxmox VE 9.x, accept root SSH,
and use distinct Proxmox node names and addresses. The existing cluster must
have quorum. The joining node must contain no VMs or containers and must not
already have cluster configuration. The controller needs SSH connectivity to
both nodes; the joining node also needs API connectivity to the selected member
on port 8006 and connectivity on the cluster's Corosync networks.

The dry run connects to both nodes for read-only inspections and prints the
native join command. It does not issue a ticket, join, or write the registry.
The normal command asks for `yes` after showing that joining replaces the
standalone node's `/etc/pve` data and inherits the cluster's storage configuration.
`--yes` supplies that confirmation for unattended use and retains all checks.

Basaltwater obtains a signed `root@pam` API ticket through the enrolled member's
already authenticated root SSH connection. Proxmox accepts a valid ticket as
an authentication credential, allowing its normal `pvecm add` API join to run
without collecting a password or 2FA response. The API certificate fingerprint
comes from that same trusted node and is pinned for the join. The ticket stays
in memory and travels through SSH stdin; it is not placed in command arguments,
environment variables, files, saved configuration, or output. This creates no
permanent API token or 2FA-exempt account and does not change login policy.
It relies on the root privileges already granted to the controller. Tickets
retain Proxmox's normal two-hour lifetime; they are not single-use credentials.
See [Proxmox's ticket authentication implementation](https://github.com/proxmox/pve-access-control/blob/master/src/PVE/API2/AccessControl.pm)
and [native cluster join](https://github.com/proxmox/pve-docs/blob/master/pvecm.adoc).

For a single-link cluster, the default link 0 address is the joining node's
reported management IP. Specify a separate Corosync address, or every link
in a cluster with multiple networks, explicitly:

```bash
basaltw proxmox join-cluster pve-new --via pve1 \
  --link 0=10.10.0.24 --link 1=10.20.0.24
```

Link indices are 0–7 and addresses must be literal IPv4 or IPv6 addresses.
`--join-address HOST` overrides the member address used by the joining node;
it must still reach that member's pinned API certificate. Network configuration
and firewall policy are not changed by this command.

The join holds the new node's Basaltwater setup/maintenance lock and repeats
the empty-node check immediately before calling Proxmox. It uses no force flag.
Afterward it verifies cluster CA identity, quorum, and reciprocal online node
membership. Joining replaces standalone SSH authorization with the cluster's
shared database, so Basaltwater verifies a fresh connection using the enrolled
member's SSH identity before updating the new host's registry entry to that
identity and invalidating cached facts. Descriptions, tags, and explicit defaults
are retained. A matching saved `server_proxmox` setup also receives the verified
SSH identity so future patches and rolling updates can reconnect; its other
arguments, run metadata, and history remain unchanged. Concurrent connection
edits are preserved and reported for review. Run `basaltw proxmox probe pve-new`
to refresh discovery.

The native join SSH timeout defaults to 900 seconds; `--timeout SECONDS` accepts
1–7200. A failed or interrupted join can have changed cluster membership and
must be inspected in Proxmox's task log and on both nodes before retrying. The
command never automatically replays a join or deletes cluster configuration.
An active native join or an existing member with the same name/address blocks
a new join. A node already verified in the intended cluster is checked and
registered without running `pvecm add` again, even when it now has guests.
Automated coverage uses mocked Proxmox APIs and commands; a real-cluster join
remains an operator smoke test.

## Concurrent setup and provisioning

Independent setup processes on one controller may target different servers at
the same time. Controller staging directories and setup payload leases are
unique per run, shared repository caches are locked per repository, and
workspace credential, Proxmox-host, network-inventory, and SSH host-key updates
are serialized so one writer cannot discard another writer's changes. History
records also carry a unique run suffix when multiple operations finish during
the same second.

Only one setup may mutate a particular target at a time. The controller rejects
an overlapping same-target run, and the target holds
`/run/lock/basaltwater-setup.lock` while `/opt/basaltwater` is replaced and the
remote setup executes. This target lock also protects against a second
controller using a separate workspace.

Multiple VM or LXC provisions on one Proxmox node can proceed concurrently when
their requested IPv4 addresses and hostnames differ. Basaltwater holds
address- and hostname-specific locks on the Proxmox node for the full provider
workflow, so separate controllers also reject simultaneous claims for the same
guest identity. A short node-wide admission lock serializes the final capacity
check and guest creation or existing-VM resource reconciliation. Image and
template staging remains outside that admission lock, so independent work can
still proceed in parallel. Because Proxmox's `nextid` lookup does not reserve
the returned VMID, QEMU and LXC creation also retry bounded VMID collisions.
Shared VM image downloads are locked per destination on the Proxmox node
through download and checksum verification, LXC template downloads are
idempotent under per-template locks, and each VM receives a per-run cloud-init
snippet.
