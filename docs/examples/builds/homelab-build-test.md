# Test one homelab site with a LAN builder

This provisions two new Debian 13 x86-64 VMs: a builder and a web server hosting
the `bluehexagons` site. Run the Bash commands from the controller's Basaltwater
checkout. The controller needs SSH access to Proxmox, the builder and the web
server. Artifacts pass through the controller, so a later production VPS does
not need network access to the LAN builder.

Use unused addresses and saved setup names. The examples assume your registered
Proxmox host `ts1`, image storage `scrap_100_1_write` and VM storage `local-lvm`
from the original setup command are available. The builder has 4 GiB RAM, four
cores and an 80 GiB root disk; the web VM has 1 GiB RAM, two cores and 20 GiB.
Replace the password placeholders and `192.168.0.10/32` with the controller's
actual SSH source address. Your existing administrative Ed25519 key and its
readable `.pub` companion are used for setup and restricted transport. Keep
the controller workspace with the saved configurations and verified SSH host
pins. No policy files, output directories or preparatory shell variables are
required: provision with the following two commands.

## Provision the builder

```bash
./basaltwater.py setup server_lite 192.168.0.53 deploy \
  --name homelab-build-test-builder --machine vm --provision-on ts1 \
  --image-storage scrap_100_1_write \
  --memory 4G --cores 4 --storage root local-lvm 80G --disk-ssd root \
  --password 'REPLACE_ME' --key "$HOME/.ssh/id_ed25519" \
  --build-server \
  --build-project build-test.hexagonalhomelab.com https://github.com/bluehexagons/bluehexagons.git \
  --go --node
```

## Provision the web VM and deploy the site

Run this after the builder setup succeeds. The target receives built files and
serves them with nginx; it does not need Go or Node. The initial `--build-on`
setup generates target grants from the complete manifests automatically.

```bash
./basaltwater.py setup server_web 192.168.0.54 deploy \
  --name homelab-build-test-web --machine vm --provision-on ts1 \
  --image-storage scrap_100_1_write \
  --memory 1G --cores 2 --storage root local-lvm 20G --disk-ssd root \
  --password 'REPLACE_ME' --key "$HOME/.ssh/id_ed25519" \
  --ssh-access-source 192.168.0.10/32 \
  --build-on homelab-build-test-builder \
  --deploy build-test.hexagonalhomelab.com https://github.com/bluehexagons/bluehexagons.git
```

Both commands pin and validate the source using shared deployment planning,
then save the generated enrollment snapshots with their setup. This site uses
a Node build, and its current repository also causes Go to be detected; the
builder therefore installs both. `--build-project` authorizes the destination;
use the same destination in the target's `--deploy`. Resource and storage
ceilings can also be set with [flags](../../services/build-operations.md#generated-policy-limits).
Passwords may be omitted to use the normal prompt instead of shell history.

This first test serves HTTP on the LAN and requires no public DNS changes or
Cloudflare authentication. Check the actual hostname route from the controller:

```bash
curl -fsS --resolve build-test.hexagonalhomelab.com:80:192.168.0.54 \
  http://build-test.hexagonalhomelab.com/
```

For browser access, configure local DNS or a hosts-file entry for the test name.
For later source updates, the saved setup retains the builder selection:

```bash
./basaltwater.py deploy homelab-build-test-web --yes
```

Before repeated deployments, review [capacity and history maintenance](../../services/build-operations.md#choose-maintenance-by-the-exhausted-resource).
There is no automatic garbage collection or remote history retirement. Before
adding sites or changing policy grants, follow the [policy update procedure](../../services/build-operations.md#add-a-site-or-change-its-grants).
The [six-site migration guide](homelab-local-builder.md) adds the original
Cloudflare/SSL options and explains the SSH source required for a production VPS.
Public Cloudflare authentication and public ACME issuance remain separate live
checks; the LAN HTTP test does not qualify them.
