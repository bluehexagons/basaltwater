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
Both commands prompt for any required password instead of recording it in shell
history. Keep the controller workspace with the saved configurations and verified
SSH host pins.

## Draft and review the policies

Set the controller source to the address or network the web VM actually sees
on SSH connections. The public companion of your existing administrative
Ed25519 key must be readable and must not be writable by other users
(`chmod 644 "$build_key.pub"` if necessary). The private key stays on the
controller. Use a new output directory; the draft helper refuses overwrites.

```bash
build_key="$HOME/.ssh/id_ed25519"
policy_dir="$HOME/basaltwater-build-test-policies"
controller_ssh_source='192.168.0.10/32' # replace with your controller's actual source

python3 scripts/prepare_build_policies.py \
  --builder-key "$build_key.pub" --target-key "$build_key.pub" \
  --output-dir "$policy_dir" \
  --deploy build-test.hexagonalhomelab.com https://github.com/bluehexagons/bluehexagons.git
```

Review both generated JSON files before provisioning. This site uses a Node
build, and its current repository also causes Go to be detected; install both
runtimes on the builder. Generated policy authority reflects the source checked
out when drafting. If the manifest changes before deployment, regenerate and
review the policies in a new directory. See [draft options](../../services/build-policy-drafts.md).

## Provision the builder

```bash
./basaltwater.py setup server_lite 192.168.0.53 deploy \
  --name homelab-build-test-builder --machine vm --provision-on ts1 \
  --image-storage scrap_100_1_write \
  --memory 4G --cores 4 --storage root local-lvm 80G --disk-ssd root \
  --key "$build_key" \
  --build-server --build-host-policy "$policy_dir/builder-enrollment.json" \
  --go --node
```

## Provision the web VM and deploy the site

Run this after the builder setup succeeds. The target receives built files and
serves them with nginx; it does not need Go or Node.

```bash
./basaltwater.py setup server_web 192.168.0.54 deploy \
  --name homelab-build-test-web --machine vm --provision-on ts1 \
  --image-storage scrap_100_1_write \
  --memory 1G --cores 2 --storage root local-lvm 20G --disk-ssd root \
  --key "$build_key" --ssh-access-source "$controller_ssh_source" \
  --build-on homelab-build-test-builder \
  --build-target-policy "$policy_dir/target-enrollment.json" \
  --deploy build-test.hexagonalhomelab.com https://github.com/bluehexagons/bluehexagons.git
```

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
