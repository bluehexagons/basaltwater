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
default-deny incoming policy, configure SSH access, and remove public 80/tcp and
443/tcp allowances unless unrestricted direct access is requested. Explicit or
inferred access sources retain source-restricted direct web access. A missing
tunnel, failed refresh, or inactive service cannot trigger the port closure.

Because the Cloudflare edge provides public HTTPS, Nginx serves the tunnel
origin over local HTTP without forcing an HTTP-to-HTTPS redirect. Nginx honors
`X-Forwarded-Proto: https` from cloudflared's loopback connection and uses its
own connection scheme for direct clients. LAN HTTP requests remain HTTP even
if a client sends a forged HTTPS header; backend TLS checks remain effective.
Keep SSH reachable through your management network before enabling the
firewall changes.
Static subpath and directory redirects remain relative so the local HTTP
origin cannot send visitors to a public HTTP URL or an internal port.

Repository deployments skip Let's Encrypt origin certificate requests when
`--cloudflare` is enabled, including when `--ssl` is also present in the setup
configuration. Cloudflare manages the browser-facing certificate; the local
HTTP origin does not need Certbot. This lets initial tunnel activation proceed
before public hostname routing is configured.

## Local testing with a hosts file

A client's hosts-file override affects that client only. Let's Encrypt's
validation servers cannot see it, so it cannot satisfy an HTTP-01 challenge for
a hostname without public DNS. It also does not create a Cloudflare DNS route.
An override pointing directly at the VM bypasses Cloudflare and its edge
certificate: use the local HTTP origin or explicitly trust the generated
self-signed Nginx certificate for local HTTPS testing. After tunnel activation
closes direct web ports, origin tests can use an SSH port forward or the direct
access options below.

## Direct access alongside the tunnel

Use `--cloudflare --lan-access` to keep direct HTTP/HTTPS available from the
target-adjacent private LAN. Debian infers IPv4 `/24` or IPv6 ULA `/64` from the
setup target, unless an explicit static address supplies another prefix. The
controller sends these resolved sources to the target; this does not infer the
controller's LAN as a substitute for a remote target's network.

```bash
basaltw setup server_web 192.168.0.52 deploy --cloudflare --lan-access \
  --deploy hexagonalhomelab.com https://github.com/example/site.git
```

For a specific LAN, VPN, or client, use repeatable `--access-source IP_OR_CIDR`
instead. For unrestricted direct access on TCP 80 and 443, add
`--cloudflare-allow-direct`. This explicit unrestricted option takes precedence
over source restrictions for those two ports. `--no-cloudflare-allow-direct`
returns them to the source-restricted policy, or tunnel-only access when no
sources are configured. Clear saved LAN sources with `--no-lan-access` and
explicit generic sources with `--no-access-source` when removing direct access
through a patch. Include these options in your full setup command, or patch an
already saved host:

```bash
basaltw patch 192.168.0.52 deploy --lan-access
basaltw patch 192.168.0.52 deploy --cloudflare-allow-direct
basaltw patch 192.168.0.52 deploy --no-cloudflare-allow-direct --no-lan-access --no-access-source
```

LAN clients reach Nginx directly alongside the public tunnel. Keep hosts-file
entries pointing the deployed hostnames at the VM, and open their HTTP URLs
for local testing. A bare IP or a `.local` hostname still needs a matching Nginx
route to select the intended site; the firewall option does not create hostname
aliases or a trusted local TLS certificate.

Setup and the tunnel helper save the policy for reruns. The helper can also
change it locally without an initial authentication wizard:

```bash
sudo setup-cloudflare-tunnel --non-interactive --allow-direct
sudo setup-cloudflare-tunnel --non-interactive --no-allow-direct --access-source 192.168.0.0/24
sudo setup-cloudflare-tunnel --non-interactive --no-allow-direct --clear-access-sources
```

An unchanged ingress does not restart the tunnel for a firewall-only change.
Owned stale direct-access rules are removed when narrowing sources or returning
to tunnel-only mode. Antistatic and manifest UDP allowances remain independent.
Setup inspects the remaining active UFW rules before reporting success. An
unmanaged web allowance outside the requested sources, or a broad SSH allowance
when management sources are configured, stops setup with the conflicting rule.
This includes IPv6 rules, port ranges, and application profiles such as
`Nginx Full`. Review `sudo ufw status numbered` and narrow or remove the rule;
setup preserves unrelated operator rules rather than deleting them silently.

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
systemd service. After verifying the service is active, the helper applies the
saved direct-access policy. Tunnel credentials and state are stored under
`/etc/cloudflared` with restrictive permissions.
Discovery reads actual `server_name` directives, including quoted names, and
ignores comments, log-format strings, wildcard/default names, and local IP or
`localhost` server names. These local names do not become public tunnel routes.

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
New installations keep the connection established by `cloudflared service
install` instead of immediately restarting it. Refreshes also install a missing
service, so a saved tunnel can recover from an interrupted service installation
without another login or tunnel creation.

Setup installs `/etc/systemd/system/cloudflared.service.d/50-basaltwater-startup.conf`
with a finite 120-second startup allowance for DNS retries and automatic
QUIC/HTTP2 transport fallback. It retains the service's readiness notification;
a process that has not connected cannot pass activation merely by running.
Service commands have bounded waits. Startup failures print the cloudflared
version, selected systemd properties, and the last 40 journal entries, omitting
settings and redacting inline authentication secrets. Pending routes stay
unapplied and setup leaves the firewall unchanged until activation succeeds.

After adding or removing an Nginx site, rerun the helper to refresh ingress
entries. A setup or patch run with `--cloudflare` also attempts a non-interactive
refresh when an existing tunnel state file is present.

Verify the service and generated routes:

```bash
sudo systemctl status cloudflared
sudo journalctl -u cloudflared -n 100 --no-pager
sudo systemctl show cloudflared -p Type -p TimeoutStartUSec -p Result -p ExecMainStatus
sudo sed -n '1,160p' /etc/cloudflared/config.yml
```

A service startup timeout does not identify the connection failure by itself.
Inspect the journal for DNS resolution, transport timeouts, or credential
errors. The VM needs working DNS for Cloudflare's tunnel endpoints and outbound
port 7844: UDP for QUIC or TCP for HTTP/2. The default automatic protocol can
fall back when one transport is blocked; neither can connect if both are
blocked. Check host and provider/network firewalls using
[Cloudflare's connectivity guide](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/troubleshoot-tunnels/connectivity-prechecks/).
Client hosts-file overrides for site domains do not replace this outbound
connection and do not cause the daemon to become ready.

Point each hostname at the tunnel in Cloudflare DNS/Zero Trust with a proxied
CNAME targeting `<TUNNEL_UUID>.cfargotunnel.com`. The helper manages local
ingress and the daemon; it does not create or replace public DNS records.
Missing hostname records or records pointing at an old tunnel still need to be
configured in the Cloudflare account. The generated ingress uses
`http://localhost:80`; keep the catch-all `http_status:404` rule last. See the
generated `/etc/cloudflared/README.md` for manual configuration steps and
[Cloudflare's DNS instructions](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/dns/).

## Webhooks and direct services

### Large uploads

The Cloudflare edge has its own
[upload limits](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/#upload-limits),
separate from the application's and Nginx's body limits. Free and Pro zones allow
100 MB, and the zone's Maximum Upload Size can be lower. A local `129m` gateway
limit therefore does not make a 128 MiB backup upload fit through Cloudflare.
Check the zone policy before qualifying backup restores or large Git LFS uploads.
Use an already authorized direct LAN/VPN HTTPS endpoint for larger local uploads,
keeping the intended hostname and certificate verification, or use an upload
workflow that splits requests into smaller parts. DNS-only records are not a
tunnel route and do not provide the Cloudflare edge certificate.

### Webhooks

For a CI/CD webhook, use the dedicated
[webhook tunnel route guide](../web/config/webhook_cloudflare_setup.md). Its
Nginx listener uses `127.0.0.1:8080`, separate from the generated site origin on
port 80 and the receiver on port 8765. The [CI/CD guide](./CICD.md) covers GitHub
configuration, signature validation, and the receiver service.

### Direct UDP services

The public hostname routes generated here do not proxy UDP. An Antistatic
deployment therefore keeps its direct UDP 3478 STUN access (and any required
direct TCP service port) even when `--cloudflare` is enabled; the public IP must
remain reachable for those ports. See [Antistatic services](./ANTISTATIC.md).
Repository services can declare `public_udp` listeners in their manifest;
setup opens those UFW rules before tunnel activation and retains them when
closing public HTTP/HTTPS. Their optional hostnames describe DNS-only A/AAAA
records pointing directly at the public server IP; they are not tunnel ingress
routes. Provider firewall rules and DNS records still need configuration.

## Limitations and recovery

- `--cloudflare` changes Nginx behavior immediately and firewall behavior only
  after a tunnel becomes active. Ensure the SSH management route is ready.
  SSH follows generic access sources, or is rate-limited with UFW when none are set;
  add a management-network-specific rule separately if your policy needs one.
- Tunnel creation requires an interactive browser login and Cloudflare account
  access; unattended setup cannot create the initial tunnel.
- If initial tunnel creation has not completed, direct port 80 remains
  available for origin testing. If a previously verified tunnel later becomes
  unavailable, inspect `cloudflared` and Nginx journals before reopening ports.
- To stop using the tunnel, restore the desired 80/443 firewall policy and
  remove or disable the Cloudflare-specific Nginx config after validating the
  replacement configuration.
