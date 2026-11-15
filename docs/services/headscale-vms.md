# Three VMs with Headscale and Unbound

This Bash walkthrough uses Basaltwater to provision three Debian QEMU VMs on
Proxmox, install Headscale, enroll two clients, install Unbound and explicitly
publish the verified resolver. Use a trusted Linux controller with a
[current Basaltwater installation](../getting-started/installation.md).
Confirm `basaltw setup --help` includes `--headscale`, `--tailscale` and
`--unbound`; older releases may lack these capabilities.

| VM | Example management address | Role | Startup order |
| --- | --- | --- | --- |
| `mesh-control` | `192.0.2.10` | Headscale, owned DERP/STUN, static onboarding | 10 |
| `mesh-dns` | `192.0.2.11` | Enrolled client and recursive Unbound | 20 |
| `mesh-app` | `192.0.2.12` | Enrolled application VM and independent DNS-check peer | 30 |

All addresses, names and paths are fictional. Replace them before execution.
Overlay addresses are assigned during enrollment; do not copy example overlay
addresses into real publication requests. The control VM stays outside its own
overlay. One resolver and one relay are the initial availability tradeoff.
This is a sequential procedure, not a dependency-aware fleet manifest.

## Prepare the controller and Proxmox node

The node must accept the selected root SSH key and have a usable bridge,
sufficient memory, disk storage and file storage for image imports/snippets.
See [Proxmox workflows](../operations/proxmox.md). These examples allocate 2 GiB
and two vCPUs per VM; they are example allocations, not a capacity benchmark.

Use one Bash session and private workspace throughout. These commands create
new automation keys; choose unused file paths. Headscale's bounded SSH adapters
disable agents and interactive prompts, so their private keys must be usable
without a passphrase prompt.

```bash
set -euo pipefail
umask 077
export STATE="$HOME/.local/state/basaltwater/mesh-demo"
WORKSPACE="$STATE/workspace"
GUEST_KEY="$STATE/guest_ed25519"
PVE_KEY="$HOME/.ssh/proxmox_ed25519"
PVE=192.0.2.2
export CONTROL=192.0.2.10 DNS=192.0.2.11 PEER=192.0.2.12
GATEWAY=192.0.2.1
BOOTSTRAP_DNS=192.0.2.1
CONTROL_DOMAIN=vpn.example.com
ONBOARDING_DOMAIN=join.example.com
MAGIC_SUFFIX=mesh.example.net
PUBLIC_RELAY_IPV4=198.51.100.10
export CONTROL_URL="https://$CONTROL_DOMAIN"

install -d -m 0700 "$STATE" "$WORKSPACE"
test ! -e "$GUEST_KEY"
ssh-keygen -q -t ed25519 -N '' -f "$GUEST_KEY"
bw() { basaltw --workspace "$WORKSPACE" "$@"; }
root_ssh() {
  local target="$1"; shift
  bw ssh --username root --key "$GUEST_KEY" "$target" -- "$@"
}
remote_bw() {
  local target="$1"; shift
  root_ssh "$target" /usr/bin/python3 /opt/basaltwater/basaltwater.py "$@"
}

# Verify the displayed fingerprint through an independent trusted source.
bw ssh-key enroll "$PVE"
bw setup server_proxmox "$PVE" root --key "$PVE_KEY" \
  --name pve1 --no-auto-restart
bw proxmox probe pve1
bw vm list pve1 --json
```

For an already configured and registered Proxmox host, retain its existing
configuration instead of replacing it with this minimal host example. The
matching guest `GUEST_KEY.pub` is installed for root and the guest's `agent`
account during new VM provisioning. Proxmox and guest keys can differ:
`--provision-key` selects the former and `--key` selects the latter.

## Provision the control VM

Before running this step, public DNS for both HTTPS names must resolve to the
actual ingress address. Route TCP 80/443 and UDP 3478 to the control VM through
the upstream firewall/NAT. Supply the actual reachable relay address. Do not
put the control/onboarding names inside the MagicDNS suffix or depend on the
overlay resolver to resolve them. Native hosting requires direct ingress and
trusted TLS; HTTP through a Cloudflare tunnel is insufficient.

```bash
bw setup server_lite "$CONTROL/24" agent \
  --name mesh-control --hostname mesh-control --tags mesh-demo \
  --machine vm --provision-on pve1 --provision-key "$PVE_KEY" \
  --key "$GUEST_KEY" --bridge vmbr0 --cores 2 \
  --memory 2G --storage root local-lvm 16G \
  --gateway "$GATEWAY" --dns "$BOOTSTRAP_DNS" \
  --autostart --start-order 10 --start-delay 15 --shutdown-timeout 120 \
  --protection --no-auto-restart \
  --headscale --headscale-domain "$CONTROL_DOMAIN" \
  --headscale-base-domain "$MAGIC_SUFFIX" \
  --headscale-public-ipv4 "$PUBLIC_RELAY_IPV4" \
  --headscale-onboarding-domain "$ONBOARDING_DOMAIN" \
  --ssl-email operator@example.com

bw headscale add mesh --url "$CONTROL_URL" --origin managed \
  --onboarding-url "https://$ONBOARDING_DOMAIN"
bw headscale show mesh
```

Add `--dry-run` to a setup command to validate its configuration and preview
the handoff before applying it. A preview does not qualify ACME, reachability,
native enrollment or DNS readiness. `--no-auto-restart` keeps setup from rebooting
each guest in the middle of the procedure; handle required reboots separately.
Proxmox startup delays order guest starts on that node; they do not wait for
Headscale or DNS readiness and do not coordinate dependencies across nodes.

## Prepare and enroll the DNS and application VMs

Install clients first, using bootstrap DNS. Do not select `--unbound` on a fresh,
unenrolled client. Both clients receive `dig` for independent publication checks.
Keep ordinary setup hostnames distinct so MagicDNS names are useful.

```bash
for role in dns app; do
  if [ "$role" = dns ]; then target="$DNS"; order=20
  else target="$PEER"; order=30; fi
  bw setup server_lite "$target/24" agent \
    --name "mesh-$role" --hostname "mesh-$role" --tags mesh-demo \
    --machine vm --provision-on pve1 --provision-key "$PVE_KEY" \
    --key "$GUEST_KEY" --bridge vmbr0 --cores 2 \
    --memory 2G --storage root local-lvm 16G \
    --gateway "$GATEWAY" --dns "$BOOTSTRAP_DNS" \
    --autostart --start-order "$order" --start-delay 15 \
    --shutdown-timeout 120 --protection --no-auto-restart \
    --apt-install bind9-dnsutils \
    --tailscale mesh --headscale-base-domain "$MAGIC_SUFFIX"
done

bw headscale-manage bind mesh --ssh-host "$CONTROL" \
  --ssh-key "$GUEST_KEY" --ssh-known-hosts "$WORKSPACE/known_hosts"
bw headscale-manage enroll mesh --target-host "$DNS" \
  --target-key "$GUEST_KEY" --target-known-hosts "$WORKSPACE/known_hosts" \
  --tag tag:dns
bw headscale-manage enroll mesh --target-host "$PEER" \
  --target-key "$GUEST_KEY" --target-known-hosts "$WORKSPACE/known_hosts" \
  --tag tag:service
bw headscale-manage inspect mesh --remote
```

The saved reference grants discovery; `bind` separately authorizes root SSH
management. New guest host keys are enrolled through the authenticated provider;
do not substitute an unauthenticated scan. Default management/target SSH ports
are 22; `--ssh-port` and `--target-port` select others for these commands.

These commands explicitly approve service tags for userless tagged machines;
creating a personal Headscale user is not a prerequisite for this machine flow.
New managed networks start with an explicit allow-all network policy. Host
firewalls, listening addresses and application authentication still apply.

Enrollment is serialized within a workspace. Do not run the two `enroll`
commands in parallel or automatically retry a failure: private controller and
server journals retain uncertain issuance/delivery. Use the
[inspection/reconciliation procedure](headscale-management.md) first.
Each exchange opens a new SSH connection; a host's SSH rate limits may require
pausing between operations. A connection refusal does not authorize replaying
an uncertain enrollment.

## Install Unbound after enrollment

The DNS VM now has a verified native identity. Patch its saved configuration;
the network selection, bootstrap DNS and provisioning metadata are retained.
`patch` changes guest services; use `setup` for provider startup/storage changes.

```bash
bw patch "$DNS" --unbound --headscale-base-domain "$MAGIC_SUFFIX"
remote_bw "$DNS" headscale-client status --url "$CONTROL_URL"
remote_bw "$PEER" headscale-client status --url "$CONTROL_URL"
```

Unbound binds the assigned overlay addresses on UDP/TCP 53, with overlay-only
firewall access, native recursion and DNSSEC. It is not published to clients yet.
Its upstream recursion needs outbound UDP/TCP 53 to authoritative DNS servers.
`--headscale-resolver IP` only saves a candidate and does not publish it.

The `remote_bw` helper uses the installed source directly because a fresh
`server_lite` VM may not have a `basaltw` launcher on PATH. If a system launcher
is already installed, the equivalent command is `sudo basaltw ...` on that VM.

## Give the control VM explicit DNS-check SSH authority

Publication runs as root on the control VM. It must reach root SSH on the DNS
VM and independent peer using its own private files. A controller-side
`headscale-manage bind` does not copy that authority to the control VM.
This example grants a separate publication key to those two guests only.
Allow control-to-guest TCP 22 through any SSH source restrictions. The current
DNS publication SSH schema uses port 22.

```bash
test ! -e "$STATE/dns_publish_ed25519"
ssh-keygen -q -t ed25519 -N '' -f "$STATE/dns_publish_ed25519"
for target in "$DNS" "$PEER"; do
  bw key push "$target" --username root --key "$GUEST_KEY" \
    --pubkey "$STATE/dns_publish_ed25519.pub"
done
cp "$WORKSPACE/known_hosts" "$STATE/pinned-guests"
chmod 0600 "$STATE/pinned-guests"
root_ssh "$CONTROL" install -d -m 0700 /root/mesh-ops
bw push "$STATE/dns_publish_ed25519" "$CONTROL:/root/mesh-ops/dns-publish.key" \
  --username root --key "$GUEST_KEY"
bw push "$STATE/pinned-guests" "$CONTROL:/root/mesh-ops/known_hosts" \
  --username root --key "$GUEST_KEY"
root_ssh "$CONTROL" chown root:root \
  /root/mesh-ops/dns-publish.key /root/mesh-ops/known_hosts
root_ssh "$CONTROL" chmod 0600 \
  /root/mesh-ops/dns-publish.key /root/mesh-ops/known_hosts
```

The ownership step matters: rsync preserves source ownership when the receiver
has permission. The root publication adapter requires private root-owned files.
Retain those credentials and the exact pinned host keys for withdrawal/recovery.

## Build, review and publish the actual DNS request

Read the DNS node's exact installed binding and the independent peer's observed
identity. This preserves the native address list instead of guessing addresses
or using names as proof. The files below contain private infrastructure metadata;
keep them outside Git. The generator refuses an already advertised candidate.

```bash
bw pull "$DNS:/var/lib/basaltwater/unbound/binding.json" "$STATE/dns-binding.json" \
  --username root --key "$GUEST_KEY"
remote_bw "$PEER" headscale-client status --url "$CONTROL_URL" > "$STATE/peer.json"
python3 - <<'PY'
import json
import os
from pathlib import Path

state = Path(os.environ["STATE"])
binding = json.loads((state / "dns-binding.json").read_text())
identity = json.loads((state / "peer.json").read_text())
assert binding["network_url"] == identity["login_server"] == os.environ["CONTROL_URL"]
assert binding["advertised"] is False and binding["publication_id"] is None
assert identity["online"] is True and identity["node_id"] != binding["node_id"]
peer = dict(version=1, network_url=identity["login_server"],
            node_id=identity["node_id"], addresses=identity["addresses"],
            advertised=False, publication_id=None)
def remote(host):
    return dict(host=host, user="root", key="/root/mesh-ops/dns-publish.key",
                known_hosts="/root/mesh-ops/known_hosts")
request = dict(binding=binding, dns_remote=remote(os.environ["DNS"]),
               peer=peer, peer_remote=remote(os.environ["PEER"]),
               expected=None, old_remote=None)
with (state / "publish.json").open("x") as output:
    json.dump(request, output, indent=2)
    output.write("\n")
PY

# Review both exact node IDs, every overlay address, endpoint and SSH destination.
cat "$STATE/publish.json"
bw push "$STATE/publish.json" "$CONTROL:/root/mesh-ops/publish.json" \
  --username root --key "$GUEST_KEY"
root_ssh "$CONTROL" chown root:root /root/mesh-ops/publish.json
root_ssh "$CONTROL" chmod 0600 /root/mesh-ops/publish.json
remote_bw "$CONTROL" headscale-dns publish --input /root/mesh-ops/publish.json --dry-run
remote_bw "$CONTROL" headscale-dns publish --input /root/mesh-ops/publish.json
remote_bw "$CONTROL" headscale-dns inspect > "$STATE/publication.json"
```

Dry-run validates schemas and private files; live publication independently
checks control/DNS/peer identities, resolver health and valid/bogus DNSSEC over
UDP/TCP, then verifies the peer's effective resolver map and device-local DNS.
Success publishes the resolver and enables managed global DNS with MagicDNS.
Any unconfirmed result requires [DNS reconciliation](headscale-dns.md#interrupted-operations),
not another blind `publish`. The peer must remain online for publication,
withdrawal and reconciliation. Its normal application work may continue.

## Operate and extend the VM group

```bash
bw list mesh-demo
bw cmd "$DNS"
bw vm list pve1 --json
bw fan --username root --key "$GUEST_KEY" "$DNS" "$PEER" -- systemctl is-active tailscaled
bw fan --username root --key "$GUEST_KEY" "$DNS" "$PEER" -- getent hosts "$CONTROL_DOMAIN"
root_ssh "$PEER" getent hosts "mesh-dns.$MAGIC_SUFFIX"
root_ssh "$PEER" dig @100.100.100.100 example.org A

# During an appropriate maintenance window, rerun each saved setup in order.
for target in "$CONTROL" "$DNS" "$PEER"; do
  bw deploy "$target" --yes
done
```

Use `fan` for commands that may run independently. It does not provision VMs or
enforce application dependencies. Rerun saved setups explicitly in the intended
order; broad tag selection is not a readiness-aware rollout. To add a fourth
VM, repeat client preparation with a unique address/name, then perform one
explicit enrollment. It will receive the published DNS settings natively.

For local A/AAAA/CNAME records, use the reviewed
[Unbound records commands](unbound.md). For personal devices, create the intended
user through [administration](headscale-administration.md), then follow the
[static onboarding](headscale-onboarding.md) and exact Auth-ID approval flow.
Machine enrollment above does not approve a person or an untagged device.

## Withdraw before disabling the resolver

Keep both clients running while withdrawing. Refresh inspection and use its
exact advertised binding, including the assigned publication ID. This example
assumes the original DNS node and peer still have the reviewed identities and
SSH authority; changed identities require separate reconciliation.

```bash
remote_bw "$CONTROL" headscale-dns inspect > "$STATE/publication.json"
python3 - <<'PY'
import json
import os
from pathlib import Path

state = Path(os.environ["STATE"])
publication = json.loads((state / "publication.json").read_text())
original = json.loads((state / "publish.json").read_text())
expected = publication["binding"]
assert publication["journal"] is None
assert expected["advertised"] is True and expected["publication_id"] is not None
for field in ("network_url", "node_id", "addresses"):
    assert expected[field] == original["binding"][field]
request = dict(expected=expected, dns_remote=original["dns_remote"],
               peer=original["peer"], peer_remote=original["peer_remote"])
with (state / "withdraw.json").open("x") as output:
    json.dump(request, output, indent=2)
    output.write("\n")
PY

cat "$STATE/withdraw.json"
bw push "$STATE/withdraw.json" "$CONTROL:/root/mesh-ops/withdraw.json" \
  --username root --key "$GUEST_KEY"
root_ssh "$CONTROL" chown root:root /root/mesh-ops/withdraw.json
root_ssh "$CONTROL" chmod 0600 /root/mesh-ops/withdraw.json
remote_bw "$CONTROL" headscale-dns withdraw --input /root/mesh-ops/withdraw.json --dry-run
remote_bw "$CONTROL" headscale-dns withdraw --input /root/mesh-ops/withdraw.json
remote_bw "$CONTROL" headscale-dns inspect

# Only after verified withdrawal succeeds:
bw patch "$DNS" --no-unbound --no-tailscale
```

Do not submit the original unpublished binding as the withdrawal expectation.
Any uncertain result requires inspection and reconciliation before teardown.
See [DNS publication](headscale-dns.md) for replacement and interrupted-operation
schemas and [recovery](headscale-recovery.md) for private backups and restore.

The [qualification record](../testing/headscale.md) states the native Debian
coverage and remaining ACME/platform/recovery boundaries. These examples do not
establish high availability, cross-node dependencies, subnet routing or an exit node.
