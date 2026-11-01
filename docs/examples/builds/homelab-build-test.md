# Test one site with a LAN builder

This provisions two new Debian 13 x86-64 VMs: a builder and a web server hosting
a sample Node-built site. Run the Bash commands from the controller's Basaltwater
checkout. The controller needs SSH access to Proxmox, the builder and the web
server. Artifacts pass through the controller, so a later production VPS does
not need network access to the LAN builder.

All addresses, host/storage names, domains and repository URLs below are
fictional examples. Choose unused addresses and setup names, and substitute
your registered Proxmox host and storage pools. Copy the
[Node static example](node-static/basaltwater.json) into your own Git repository
and publish it before replacing `https://github.com/example/site.git` below.
The builder has 4 GiB RAM, four cores and an 80 GiB root disk; the web VM has
1 GiB RAM, two cores and 20 GiB. Replace `192.0.2.10/32` with the controller's
actual SSH source address. Your existing administrative Ed25519 key and its
readable `.pub` companion are used for setup and restricted transport. Keep
the controller workspace with the saved configurations and verified SSH host
pins. No policy files, output directories or preparatory shell variables are
required: provision with the following two commands.

## Provision the builder

```bash
./basaltwater.py setup server_lite 192.0.2.20 operator \
  --name build-local --machine vm --provision-on lab-node \
  --image-storage image-pool \
  --memory 4G --cores 4 --storage root vm-pool 80G --disk-ssd root \
  --key "$HOME/.ssh/id_ed25519" \
  --build-server
```

## Provision the web VM and deploy the site

Run this after the builder setup succeeds. The target receives built files and
serves them with nginx; it does not need Go or Node. The initial `--build-on`
setup generates target grants from the complete manifests automatically.

```bash
./basaltwater.py setup server_web 192.0.2.30 operator \
  --name lan-test-web --machine vm --provision-on lab-node \
  --image-storage image-pool \
  --memory 1G --cores 2 --storage root vm-pool 20G --disk-ssd root \
  --key "$HOME/.ssh/id_ed25519" \
  --ssh-access-source 192.0.2.10/32 \
  --build-on build-local \
  --deploy test.example.com https://github.com/example/site.git
```

Target setup pins and validates source using shared deployment planning,
then saves generated target enrollment with its setup. This site uses
a Node build, so the builder installs Node. Bare `--build-server` grants wildcard project authority; optional
`--build-project` selects restrictive grants. Resource and
storage ceilings can also be set with [flags](../../services/build-operations.md#generated-policy-limits).
The commands omit passwords to use the normal secure prompt.

This LAN test requires no public DNS changes or Cloudflare authentication.
Managed activation configures HTTPS with a local certificate and redirects HTTP.
For this disposable test, check the hostname route while accepting that local
certificate; use a trusted certificate for ordinary access:

```bash
curl -kfsS --resolve test.example.com:443:192.0.2.30 \
  https://test.example.com/
```

For browser access, configure local DNS or a hosts-file entry for the test name.
For later source updates, the saved setup retains the builder selection:

```bash
./basaltwater.py deploy lan-test-web --yes
```

Before repeated deployments, review [capacity and history maintenance](../../services/build-operations.md#choose-maintenance-by-the-exhausted-resource).
There is no automatic garbage collection or remote history retirement. Before
adding sites or changing policy grants, follow the [policy update procedure](../../services/build-operations.md#add-a-site-or-change-its-grants).
The [multi-application migration guide](homelab-local-builder.md) adds
Cloudflare/SSL options and explains the SSH source required for a production VPS.
Public Cloudflare authentication and public ACME issuance remain separate live
checks; the LAN test does not qualify them.
