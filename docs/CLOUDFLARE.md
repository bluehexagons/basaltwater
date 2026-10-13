# Cloudflare tunnels

`--cloudflare` prepares a web host to use Cloudflare Tunnel as its public edge.
On the first interactive setup run, it walks through authentication, tunnel
creation, and service activation after the deployed Nginx routes are ready.
Prompts appear in the original setup terminal over the same SSH connection;
open the authentication URL in your own browser. `--name` supplies the suggested
tunnel name. Later runs refresh the existing tunnel without prompting.

## Prepare a host

```bash
basaltw setup server_web example.com deploy \
  --cloudflare \
  --deploy example.com https://github.com/example/site.git
```

The setup flow first:

- configures Nginx to trust Cloudflare source ranges and the IPv4/IPv6 loopback
  connection from cloudflared for `CF-Connecting-IP` restoration;
- creates `/etc/cloudflared/README.md`; and
- installs `/usr/local/bin/setup-cloudflare-tunnel`.

Direct HTTP and HTTPS stay open during preconfiguration. If existing tunnel
state is present, setup refreshes the ingress configuration and verifies that
`cloudflared` is active. Only after that verification does it enable UFW's
default-deny incoming policy, rate-limit SSH, and remove public 80/tcp and
443/tcp allowances. A missing tunnel, failed refresh, or inactive service
cannot trigger the port closure.

Because the Cloudflare edge provides public HTTPS, Nginx serves the tunnel
origin over local HTTP without forcing an HTTP-to-HTTPS redirect. Proxied
applications still receive `X-Forwarded-Proto: https` so they generate secure
external URLs correctly. Keep SSH reachable through your management network
before enabling the firewall changes.
Static subpath and directory redirects remain relative so the local HTTP
origin cannot send visitors to a public HTTP URL or an internal port.

## Create or update a tunnel

The setup run starts the wizard automatically when no tunnel exists. First-time
creation requires terminal input; an unattended run stops with an actionable
error and leaves direct HTTP/HTTPS available. The helper remains available for
manual recovery or configuration changes on the host:

```bash
sudo setup-cloudflare-tunnel
```

The helper installs `cloudflared` from Cloudflare's signed APT repository when needed, runs Cloudflare authentication,
creates or reuses a tunnel, discovers hostnames from enabled Nginx sites, writes
`/etc/cloudflared/config.yml`, and can install and enable the `cloudflared`
systemd service. After verifying the service is active, the helper closes
direct public HTTP/HTTPS access. Tunnel credentials and state are stored under
`/etc/cloudflared` with restrictive permissions.

Generated tunnel and Nginx configurations are validated before activation. A
non-interactive refresh compares complete hostname/origin entries, repairs a
missing or invalid config, and verifies that the service is active; it reports
failure when the service cannot be started instead of claiming success.
Changed ingress restarts cloudflared before recording the routes as applied;
failed restarts leave the prior route state and a `pending_activation` flag so
the next refresh retries even when only repairing an unchanged route set.
An unchanged, valid configuration leaves the running daemon alone.
Interactive updates reuse the installed service. A restart briefly interrupts
existing tunnel connections; use replicas if uninterrupted edge access is needed.

After adding or removing an Nginx site, rerun the helper to refresh ingress
entries. A setup or patch run with `--cloudflare` also attempts a non-interactive
refresh when an existing tunnel state file is present.

Verify the service and generated routes:

```bash
sudo systemctl status cloudflared
sudo journalctl -u cloudflared -n 100 --no-pager
sudo sed -n '1,160p' /etc/cloudflared/config.yml
```

Point each hostname at the tunnel in Cloudflare DNS/Zero Trust. The generated
ingress uses `http://localhost:80`; keep the catch-all `http_status:404` rule
last. See the generated `/etc/cloudflared/README.md` for manual configuration
steps.

## Webhooks and direct services

For a CI/CD webhook, use the dedicated
[webhook tunnel route guide](../web/config/webhook_cloudflare_setup.md). Its
Nginx listener uses `127.0.0.1:8080`, separate from the generated site origin on
port 80 and the receiver on port 8765. The [CI/CD guide](./CICD.md) covers GitHub
configuration, signature validation, and the receiver service.

Cloudflare Tunnel does not proxy UDP. An Antistatic deployment therefore keeps
its direct UDP 3478 STUN access (and any required direct TCP service port) even
when `--cloudflare` is enabled; the public IP must remain reachable for those
ports. See [Antistatic services](./ANTISTATIC.md).
Repository services can declare `public_udp` listeners in their manifest;
setup opens those UFW rules before tunnel activation and retains them when
closing public HTTP/HTTPS. Their optional hostnames describe DNS-only A/AAAA
records pointing directly at the public server IP; they are not tunnel ingress
routes. Provider firewall rules and DNS records still need configuration.

## Limitations and recovery

- `--cloudflare` changes Nginx behavior immediately and firewall behavior only
  after a tunnel becomes active. Ensure the SSH management route is ready.
  SSH is rate-limited with UFW;
  add a management-network-specific rule separately if your policy needs one.
- Tunnel creation requires an interactive browser login and Cloudflare account
  access; unattended setup cannot create the initial tunnel.
- If initial tunnel creation has not completed, direct port 80 remains
  available for origin testing. If a previously verified tunnel later becomes
  unavailable, inspect `cloudflared` and Nginx journals before reopening ports.
- To stop using the tunnel, restore the desired 80/443 firewall policy and
  remove or disable the Cloudflare-specific Nginx config after validating the
  replacement configuration.
