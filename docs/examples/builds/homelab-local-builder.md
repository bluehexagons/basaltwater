# Move the six homelab deployments to a LAN builder

For an initial trial on separate VMs without changing public DNS, start with the
[single-site LAN test](homelab-build-test.md).

Run these commands from the controller's Basaltwater checkout. The controller
must reach the builder and target over SSH. It relays artifacts, so this also
works with a production VPS that cannot reach the LAN builder.
The command blocks below use Bash, including the shared `sites` array.

Choose an unused builder address; `192.168.0.53` below is an example. Use your
controller's existing Ed25519 key that authorizes administrative setup on these
hosts. Its public companion is used for the restricted deployment transports.
Keep the controller workspace containing saved setups and verified host pins.
Private repositories need a working noninteractive global Git credential
helper on this controller, such as an existing GitHub CLI login configured for
Git. That login is used only for source cloning and stays on the controller.

First collect the original deployments and draft both enrollment envelopes:

```bash
sites=(
  --deploy hexagonalhomelab.com https://github.com/bluehexagons/bluehexagons.git
  --deploy clicker.hexagonalhomelab.com https://github.com/bluehexagons/goclick.git
  --deploy foodguide.hexagonalhomelab.com https://github.com/bluehexagons/foodguide.git
  --deploy designworkshomes.hexagonalhomelab.com https://github.com/bluehexagons/designworkshomes.git
  --deploy mm0.hexagonalhomelab.com 'https://github.com/bluehexagons/antistatic-server.git#branch=release/antistatic-0.9.125'
  --deploy mm0-beta.hexagonalhomelab.com 'https://github.com/bluehexagons/antistatic-server.git#branch=main'
)
build_key="$HOME/.ssh/id_ed25519"
policy_dir="$HOME/basaltwater-homelab-policies"
controller_ssh_source='192.168.0.10/32' # replace with this controller's address seen by the target

python3 scripts/prepare_build_policies.py \
  --builder-key "$build_key.pub" --target-key "$build_key.pub" \
  --output-dir "$policy_dir" "${sites[@]}"
```

Review the generated JSON before enrollment. Preserve its ordinary `/var/www`
deployment roots and component names when migrating an existing server; these
also identify service accounts and persistent data. The foodguide manifest
includes an additional `bluehexagons.com/foodguide/` route. The release
antistatic service declares direct UDP 3478 with hostname
`stun.antistaticgame.com`. Confirm those routes belong on this target. Cloudflare
Tunnel does not carry UDP: that hostname needs direct DNS, firewall permission
and, for a LAN target, a router port forward if public STUN access is required.
See [policy draft options and capacity](../../services/build-policy-drafts.md).

Provision the builder with Go and Node. Replace the example password and address
with your values:

```bash
./basaltwater.py setup server_lite 192.168.0.53 deploy \
  --name homelab-build --provision-on ts1 \
  --image-storage scrap_100_1_write \
  --memory 4G --cores 4 --storage root local-lvm 80G --disk-ssd root \
  --password 'redacted' --key "$build_key" \
  --build-server --build-host-policy "$policy_dir/builder-enrollment.json" \
  --go --node
```

Plan explicit builder maintenance. The default 64 GiB logical quota can fill
after roughly fifteen unsettled jobs, even with free disk space. Inspect and
settle terminal reservations using the [maintenance commands](../../services/build-systems.md#inspect-capacity-and-retain-evidence)
to reclaim unused charges while preserving payloads. Settlement does not free
record slots. The policies also default to 128 records, and the controller retains
at most 32 active attempts per target. Use [completed-history archival](../../agents/contributing/build-setup-history.md)
to free controller slots without deleting evidence. Builder and target record
limits still need operator planning. There is no automatic collection or remote
history retirement; archived controller data still consumes disk space.

The target command keeps the original deployment and serving options and adds
the saved builder, target policy and trusted controller SSH source:

```bash
./basaltwater.py setup server_web 192.168.0.52 deploy \
  --name bluehexagons-homelab-1 \
  --provision-on ts1 --image-storage scrap_100_1_write \
  --memory 1G --cores 2 --storage root local-lvm 20G --disk-ssd root \
  --password 'redacted' --key "$build_key" \
  --ssh-access-source "$controller_ssh_source" \
  --web-panel --ssl --web-panel-password 'redacted' \
  --notify webhook 'redacted' --notify webhook 'redacted' \
  --hostname bluehexagons-homelab \
  --cloudflare --cloudflare-allow-direct \
  --build-on homelab-build \
  --build-target-policy "$policy_dir/target-enrollment.json" \
  "${sites[@]}"
```

Initial Cloudflare tunnel creation uses the existing interactive authentication
wizard. With Cloudflare and SSL together, the origin remains HTTP and the
public tunnel supplies HTTPS. A direct HTTPS VPS without Cloudflare uses
`--ssl` and the ordinary certificate setup instead.

The SSH-only source allows delivery's connection bursts without restricting
public web access. Repeat it for every controller or administration network
that needs SSH access. Preflight verifies the current controller is included.
For the production VPS, use the controller's public egress IP or VPN source as
seen by that VPS; its LAN address usually does not apply there. Enrollment
reconciles existing SSH rules before delivery, preserving web ingress.

Use a separate target policy and saved setup for production. Do not point two
active tunnels at the same public hostname unintentionally. For later updates,
the saved controller setup retains the builder selection; target-local refresh
uses retained artifacts and does not contact the builder.

```bash
./basaltwater.py deploy bluehexagons-homelab-1 --yes
```

For an existing cloud VPS, use the [existing-target command](../../services/build-systems.md#start-with-a-selected-builder)
with `--machine vm`; omit the Proxmox provisioning, image, storage, CPU and memory
options above. Establish root SSH access and verified host trust first. The
same saved LAN builder can serve it. Before adding sites or changing authority,
follow [policy updates](../../services/build-operations.md#add-a-site-or-change-its-grants):
editing draft files alone does not update a server, and replacement builder
authority must retain projects needed by both stage and production.

The six unchanged manifests have been built and served on disposable Debian 13
VMs with a 2 GiB builder and 1 GiB target. Checks cover verified HTTPS, service
health, the additional foodguide route, SQLite backups and real UDP STUN. The
target's route to the builder was prohibited throughout. A separate retained
routing check verified Cloudflare/SSL HTTP origins; public Tunnel authentication
and public ACME issuance were not exercised in the lab.
The normal saved six-site deployment and actual target refresh with the builder
powered off both passed, preserving SQLite state and private backups. The
SSH-only trusted-source policy replaced the default connection limiter while
keeping public web ingress.
Administrator SSH capacity inspection and exact-revision terminal settlement
also passed, preserving source, log, artifact and replay identities. Maintenance
remains unavailable through the restricted build transport. Automated coverage
verifies that direct SSL delivery defers new certificates until final setup has
configured web ingress.
