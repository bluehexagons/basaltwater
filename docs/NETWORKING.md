# Networking

Static host configuration, network inventory, and read-only Proxmox firewall
planning.

## Static Host Configuration

The shared `setup` and `patch` options can persist a hostname and static
dual-stack network configuration on Debian targets:

```bash
basaltw setup server_lite 10.20.0.15 admin \
  --hostname storage-01 \
  --ip 10.20.0.15/24 --gateway 10.20.0.1 \
  --ipv6 2001:db8:20::15/64 --gateway6 2001:db8:20::1 \
  --dns 10.20.0.53 --dns 2001:db8:20::53
```

Both address flags require a prefix length. A gateway must be another address
on that link (an IPv6 link-local gateway is also accepted). `--dns` is repeatable, and
`--network-interface` overrides default-route interface detection. The setup
supports active NetworkManager, systemd-networkd, and ifupdown installations.
It disables future cloud-init network regeneration when cloud-init is present.

Direct remote setup stages persistent configuration without bouncing the live
interface, which prevents a mid-run SSH disconnect. Activate it with a planned
reboot or explicit interface restart after reviewing the generated settings.

For an existing host that must move immediately, add `--activate-network` to a
saved-host patch:

```bash
basaltw patch 10.20.0.15 admin \
  --ip 10.20.0.25/24 --gateway 10.20.0.1 \
  --dns 10.20.0.53 --network-interface eth0 \
  --activate-network
```

This is a transactional handoff. Run it from a separate controller; local-host
activation is rejected because it cannot prove external reachability. The
target adds the new address as a temporary
secondary address and uses source-specific routing for its requested gateway,
without removing the address used by the active SSH setup. The controller then
requires every requested IPv4/IPv6 endpoint to return the unique transaction
identity created through the original authenticated connection. Before making
changes it snapshots the relevant NetworkManager properties or network config
files. It verifies both old and new endpoints around persistence, keeps commit
and finalize operations safe to retry after lost SSH responses, and restores
the persistent snapshot plus temporary live state if a later check fails. A
successful handoff moves the saved host record to the new address; the
one-shot activation flag is not saved. The old live address remains available
until reboot, when the verified persistent configuration becomes the sole
assignment.

Hosted Proxmox guests receive their initial addressing during provisioning and
therefore boot with it active. The controller retains the gateway and DNS
defaults resolved from the selected Proxmox bridge in the saved setup. On a
rerun, it restores those values before building the remote setup arguments. If
an older cache is missing them, the command refreshes the defaults from
Proxmox instead of silently skipping the provisioning handoff.

After guest SSH becomes available, hosted setup verifies the live IPv4 default
route. If cloud-init brought up the address without the route, Basaltwater
repairs it automatically before package installation. For VMs, this check and
the remote setup upload use retained, key-only root SSH. This stable privileged
channel applies to both VMs and LXCs and does not depend on the configured
account's current sudo policy. A standard VM user joins `sudo` without
receiving a temporary passwordless bootstrap rule. Use `--nopasswd` to grant
that user the managed unrestricted rule explicitly; root transport allows a
later rerun to add or remove it safely. Interactive runs may prompt for the
configured SSH key's passphrase; non-interactive runs require that key to be
available through an SSH agent. See [SSH authentication](SSH.md). The normal
static network step then
persists the same gateway through the guest's active network backend. A failed
verification stops setup with the SSH/error detail so a guest cannot be
reported as successfully configured while lacking its expected route.

Existing Proxmox VMs and LXCs can use the same verified patch handoff as
physical hosts and non-Proxmox VMs, including when a saved hosted configuration
still contains its Proxmox metadata. Before guest persistence, the controller
identifies exactly one matching VM or LXC on the saved Proxmox node. It then
updates `qm ipconfig0` or `pct net0` after guest SSH is verified, preserving the
existing bridge, firewall, MAC, device type, and unchanged address-family
fields. It checks guest SSH again after the Proxmox update and restores the
previous Proxmox value if that final check fails. The preflight now requires
every listed guest to be readable, refuses metadata that changed concurrently,
and reads the value back after applying it. Use `patch ... --activate-network`
for existing hosted guests; initial hosted setup boots directly on its
configured address and rejects the live-handoff flag.

Proxmox host (`server_proxmox`) identity and bridge changes remain outside this
generic workflow because they require cluster-aware planning. Generic setup
also refuses a selected Linux bridge at runtime to avoid erasing Proxmox
bridge ports or other topology settings from ifupdown configuration.

## LAN Access Inference

`--lan-access` scopes managed inbound services to the target's adjacent private
network. A target in any RFC 1918 block produces its local `/24`; an IPv6 ULA
target produces `/64`. When `--ip` or `--ipv6` declares a private address and
prefix, that explicit network is used instead, but it is bounded by the
enclosing RFC 1918 or ULA block. Hostnames are resolved on the controller before
the setup arguments are sent, so the target never expands the flag into every
private-address range.

If no private target network can be inferred, setup stops before applying the
remote configuration. Remove the flag and use repeatable `--access-source`
values for routed management networks, VLANs, VPN clients, or a subnet wider
than the normal inference. `--no-lan-access` removes the saved inference on a
later patch.

## Inventory and Firewall Planning

The `network` command manages a workspace-backed, provider-neutral network
inventory and can generate a read-only Proxmox control-plane firewall plan from
that inventory.

## Scope

The inventory can create and inspect named profiles; track management sources,
control-plane addresses, guest networks, subnets, VLANs, and hosts; import
registered Proxmox nodes and guest networks; and render an abstract or concrete
Proxmox firewall plan for review.

It does not apply or roll back firewall changes, integrate with switches,
routers, or cloud providers, or discover infrastructure beyond the Proxmox
registry and guest configuration import.

Inventory is stored at `<workspace>/network_inventory.json` with mode `0600`.
Like other workspace-scoped features, pass `--workspace PATH` after `network`
to isolate data for a project or environment.

## Common Workflow

1. Create a profile and seed it with at least one management source.
2. Add manual hosts or import known Proxmox nodes.
3. Import guest networks from the registered Proxmox hosts.
4. Review the generated lockdown plan before touching any firewall config.

```bash
# Create a profile
basaltw network init homelab \
  --management 192.168.1.0/24 \
  --control-plane 10.0.0.10 \
  --guest-network 10.0.10.0/24

# Add metadata manually when needed
basaltw network add-host homelab pve1 10.0.0.10 \
  --provider proxmox \
  --role control-plane \
  --role proxmox

# Or import from the saved Proxmox host registry
basaltw network import-proxmox homelab --tag prod
basaltw network import-proxmox-guests homelab --tag prod

# Review the abstract plan
basaltw network plan-proxmox homelab

# Render the matching Proxmox snippets without applying them
basaltw network plan-proxmox homelab --proxmox
```

## Commands

| Command | Purpose |
|---------|---------|
| `basaltw network list` | List saved network profiles |
| `basaltw network show <profile>` | Show one profile in text form |
| `basaltw network show <profile> --json` | Dump one profile as JSON |
| `basaltw network init <profile> [...]` | Create a profile with management, control-plane, guest-network, subnet, or VLAN entries |
| `basaltw network add-host <profile> <name> <address> [...]` | Add a tagged host record to a profile |
| `basaltw network import-proxmox <profile> [...]` | Import registered Proxmox nodes into the profile |
| `basaltw network import-proxmox-guests <profile> [...]` | Import guest networks from registered Proxmox hosts |
| `basaltw network plan-proxmox <profile>` | Print the abstract read-only lockdown plan |
| `basaltw network plan-proxmox <profile> --proxmox` | Render concrete Proxmox firewall artifacts for review |
| `basaltw network plan-proxmox <profile> --json` | Emit machine-readable JSON |

## Safety model

`plan-proxmox` is intentionally read-only. It never writes remote files or talks
to the Proxmox firewall API.

The planner refuses to produce an apply-safe result until the profile includes:

- at least one management source
- at least one control-plane address

If guest networks are missing, the planner still renders a warning so you can
see that the guest-to-control-plane deny rule has no source set yet.

`plan-proxmox --proxmox` renders the artifacts that would back the policy:

- `/etc/pve/firewall/cluster.fw`
- `/etc/pve/nodes/<node>/host.fw`
- `/etc/pve/firewall/<VMID>.fw`

Use these outputs for review, diffing, or future automation; the current CLI
does not apply them for you.
