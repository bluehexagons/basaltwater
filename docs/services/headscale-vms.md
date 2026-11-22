# Set up a Headscale network with three VMs

Use three setup commands for the control server, an application peer and the
Unbound DNS server. Then publish DNS with one hookup command. Basaltwater handles
client preparation, private machine enrollment, Unbound installation and the
DNS checks in the required order.

All names and addresses below are fictional. Replace them with your own.
Use a [current Basaltwater installation](../getting-started/installation.md)
with `--headscale-name` and `--tailscale-tag` in `basaltw setup --help`.

## Connect the infrastructure

Before setup:

- Point public DNS for `vpn.example.com` and `join.example.com` at the control
  server's ingress address. Forward TCP 80/443 and UDP 3478 to that VM. Keep
  these names outside the MagicDNS suffix, `mesh.example.net`.
- Have a registered Proxmox node with bridge `vmbr0` and storage `local-lvm`.
  Use an explicit private SSH key Basaltwater can use without an interactive
  passphrase prompt. Its matching public key is installed on new guests.
  Verify the node's first SSH fingerprint through a trusted channel.

If the Proxmox node has not been configured and registered yet, do that once:

```bash
basaltw setup server_proxmox 192.0.2.2 root \
  --name pve1 --key ~/.ssh/id_ed25519 --no-auto-restart
```

Retain an existing node's setup rather than replacing it with this minimal
example. See [Proxmox workflows](../operations/proxmox.md) for node options.

## Set up the VMs

In Bash, keep the common VM options in an array. The example allocates 2 GiB
and two vCPUs per VM. Choose a working bootstrap DNS server; it must resolve the
control endpoint before enrollment and remain available outside the overlay.

```bash
network_vm=(
  --machine vm --provision-on pve1
  --key "$HOME/.ssh/id_ed25519" --provision-key "$HOME/.ssh/id_ed25519"
  --bridge vmbr0 --cores 2 --memory 2G --storage root local-lvm 16G
  --gateway 192.0.2.1 --dns 192.0.2.1 --no-auto-restart
)

# 1. Install the control server and save its verified management connection.
basaltw setup server_lite 192.0.2.10/24 agent "${network_vm[@]}" \
  --name mesh-control --hostname mesh-control --start-order 10 \
  --headscale --headscale-name mesh \
  --headscale-domain vpn.example.com --headscale-base-domain mesh.example.net \
  --headscale-public-ipv4 198.51.100.10 \
  --headscale-onboarding-domain join.example.com --ssl-email operator@example.com

# 2. Install and enroll an application VM, also used for independent DNS checks.
basaltw setup server_lite 192.0.2.12/24 agent "${network_vm[@]}" \
  --name mesh-app --hostname mesh-app --start-order 30 \
  --tailscale mesh --tailscale-tag tag:service --apt-install bind9-dnsutils

# 3. Install and enroll the DNS VM, then configure Unbound on it.
basaltw setup server_lite 192.0.2.11/24 agent "${network_vm[@]}" \
  --name mesh-dns --hostname mesh-dns --start-order 20 \
  --tailscale mesh --tailscale-tag tag:dns --unbound \
  --headscale-base-domain mesh.example.net
```

Wait for each command to succeed before starting the next. `--headscale-name`
saves the managed network and binds its verified root SSH connection.
`--tailscale-tag` authorizes tagged-machine enrollment through that connection;
the enrollment key stays out of arguments and saved configuration. On the DNS VM,
setup completes enrollment before installing Unbound. No separate enrollment or
second configuration pass is needed.

The control server stays outside its own overlay. New networks allow traffic
among enrolled peers; host firewalls and application authentication still apply.
Proxmox startup order controls guest starts, not service readiness.

## Publish DNS

```bash
basaltw headscale-manage dns-publish mesh --resolver mesh-dns --peer mesh-app
```

Basaltwater observes both clients, configures the private SSH authority needed
for checks, verifies Unbound and DNSSEC from the independent peer, and publishes
the actual assigned resolver addresses. There is no request JSON or key copying
to do yourself. The publication key is restricted to the DNS check protocol.
Keep the peer online and allow the control VM to reach both guests on SSH port 22.

After success, enrolled clients use MagicDNS for device names and Unbound for
other queries. More clients need only another setup command with
`--tailscale mesh --tailscale-tag tag:service`; they receive the DNS settings.
Personal devices use the instructions at `https://join.example.com` and require
their own [administrator approval](headscale-onboarding.md).

## Operate the network

```bash
basaltw headscale-manage dns-inspect mesh
basaltw fan mesh-dns mesh-app -- hostname
```

`fan` resolves the names saved by setup in the active workspace and uses each
VM's saved SSH credentials. The names do not need DNS records.

To change a machine's configuration, rerun its setup command with the desired
options. Add `--dry-run` to preview an automated setup or DNS hookup without
connecting or changing state. If enrollment or publication reports an uncertain
result, use [management recovery](headscale-management.md) or
[DNS reconciliation](headscale-dns.md#interrupted-operations) before retrying.

Before retiring the DNS VM, withdraw it while both clients remain running:

```bash
basaltw headscale-manage dns-withdraw mesh --resolver mesh-dns --peer mesh-app
```

Only after verified withdrawal should you rerun the DNS VM's setup with
`--no-unbound --no-tailscale`, or retire the VM. One resolver and one relay are
the initial availability tradeoff. See [Unbound](unbound.md) for local records,
[recovery](headscale-recovery.md) for backups, and the
[qualification record](../testing/headscale.md) for tested scope and remaining limits.
