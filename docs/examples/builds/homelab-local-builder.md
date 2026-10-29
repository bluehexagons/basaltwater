# Move the six homelab deployments to a LAN builder

Run these commands from the controller's Basaltwater checkout. The controller
must reach the builder and target over SSH. It relays artifacts, so this also
works with a production VPS that cannot reach the LAN builder.

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

The target command keeps the original deployment and serving options and adds
the saved builder and target policy:

```bash
./basaltwater.py setup server_web 192.168.0.52 deploy \
  --name bluehexagons-homelab-1 \
  --provision-on ts1 --image-storage scrap_100_1_write \
  --memory 1G --cores 2 --storage root local-lvm 20G --disk-ssd root \
  --password 'redacted' --key "$build_key" \
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

Use a separate target policy and saved setup for production. Do not point two
active tunnels at the same public hostname unintentionally. For later updates,
the saved controller setup retains the builder selection; target-local refresh
uses retained artifacts and does not contact the builder.
