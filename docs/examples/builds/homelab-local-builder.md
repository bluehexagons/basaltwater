# Move multiple applications to a LAN builder

For an initial trial on separate VMs without changing public DNS, start with the
[single-site LAN test](homelab-build-test.md).

Run these commands from the controller's Basaltwater checkout. The controller
must reach the builder and target over SSH. It relays artifacts, so this also
works with a production VPS that cannot reach the LAN builder.

All addresses, host/storage names, domains and repository URLs below are
fictional. Publish your own repositories with committed manifests before
substituting them in these commands. For example, use the
[Node static site](node-static/basaltwater.json) for `site.git` and the
[SPA/Go application](spa-go/basaltwater.json) for `app.git`.
Choose an unused builder address; `10.23.0.20` below is an example. Use your
controller's existing Ed25519 key that authorizes administrative setup on these
hosts. Its public companion is used for the restricted deployment transports.
Keep the controller workspace containing saved setups and verified host pins.
Private repositories need a working noninteractive global Git credential
helper on this controller, such as an existing GitHub CLI login configured for
Git. That login is used only for source cloning and stays on the controller.

The two setup commands generate and save enrollment from the selected sources;
there are no policy files or directories to prepare. Review the committed
manifests before running them. Generated grants preserve ordinary `/var/www`
deployment roots and component names when migrating an existing server; these
also identify service accounts and persistent data. Review additional routes
and public UDP listeners declared by your manifests. Cloudflare Tunnel does
not carry UDP: a UDP hostname needs direct DNS, firewall permission
and, for a LAN target, a router port forward if public STUN access is required.
See [generated policy limits](../../services/build-operations.md#generated-policy-limits).

Provision the builder with Go and Node. Replace the example address, registered
Proxmox host and storage pools with your values. Passwords are requested through
the normal secure prompt:

```bash
./basaltwater.py setup server_lite 10.23.0.20 deploy \
  --name lan-builder --machine vm --provision-on pve1 \
  --image-storage local \
  --memory 4G --cores 4 --storage root local-lvm 80G --disk-ssd root \
  --key "$HOME/.ssh/id_ed25519" \
  --build-server \
  --build-project site.example.com https://github.com/example/site.git \
  --build-project app.example.com https://github.com/example/app.git \
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

The target command selects both applications, Cloudflare/SSL serving options,
the saved builder and trusted controller SSH source. Replace `10.23.0.10/32`
with this controller's address as seen by the target:

```bash
./basaltwater.py setup server_web 10.23.0.30 deploy \
  --name lan-web --machine vm \
  --provision-on pve1 --image-storage local \
  --memory 1G --cores 2 --storage root local-lvm 20G --disk-ssd root \
  --key "$HOME/.ssh/id_ed25519" \
  --ssh-access-source 10.23.0.10/32 \
  --ssl --hostname lan-web \
  --cloudflare --cloudflare-allow-direct \
  --build-on lan-builder \
  --deploy site.example.com https://github.com/example/site.git \
  --deploy app.example.com https://github.com/example/app.git
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

Use a separate saved target setup for production; it generates its own grants.
Declare any different production destinations on the builder too. Do not point two
active tunnels at the same public hostname unintentionally. For later updates,
the saved controller setup retains the builder selection; target-local refresh
uses retained artifacts and does not contact the builder.

```bash
./basaltwater.py deploy lan-web --yes
```

For an existing cloud VPS, use the [existing-target command](../../services/build-systems.md#start-with-a-selected-builder)
with `--machine vm`; omit the Proxmox provisioning, image, storage, CPU and memory
options above. Establish root SSH access and verified host trust first. The
same saved LAN builder can serve it. Before adding sites or changing authority,
follow [policy updates](../../services/build-operations.md#add-a-site-or-change-its-grants).
Builder `--build-project` patches preserve projects needed by other targets.

See the [coverage map](../../plans/testing-coverage.md) for selected native
build, delivery, refresh and retention checks and their limits. These commands
are adaptable examples, not a record of a particular installation. Public
Tunnel authentication and public ACME issuance remain separate live checks.
Administrator maintenance is unavailable through the restricted build transport.
