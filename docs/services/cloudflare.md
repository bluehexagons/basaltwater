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

Early firewall setup leaves TCP 80 and 443 under the existing origin policy.
A fresh tunnel-only setup adds no direct allowances for those ports. After
`cloudflared` is verified active, setup reconciles the direct web policy and
removes stale Basaltwater web rules. A failed or deferred tunnel activation
retains the existing origin firewall policy, including during a saved rerun.
An active UFW firewall must have a deny or reject incoming default.

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
basaltw setup server_web 10.23.0.30 deploy --cloudflare --lan-access \
  --deploy site.example.com https://github.com/example/site.git
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
basaltw patch 10.23.0.30 deploy --lan-access
basaltw patch 10.23.0.30 deploy --cloudflare-allow-direct
basaltw patch 10.23.0.30 deploy --no-cloudflare-allow-direct --no-lan-access --no-access-source
```

LAN clients reach Nginx directly alongside the public tunnel. Keep hosts-file
entries pointing the deployed hostnames at the VM, and open their HTTP URLs
for local testing. A bare IP or a `.local` hostname still needs a matching Nginx
route to select the intended site; the firewall option does not create hostname
aliases or a trusted local TLS certificate.

Setup and the tunnel helper save the policy for reruns. The helper can also
change it locally on a host with active UFW. These helper options affect TCP
80 and 443. Manage SSH sources separately through Basaltwater setup.

```bash
sudo setup-cloudflare-tunnel --non-interactive --allow-direct
sudo setup-cloudflare-tunnel --non-interactive --no-allow-direct --access-source 10.23.0.0/24
sudo setup-cloudflare-tunnel --non-interactive --no-allow-direct --clear-access-sources
```

An unchanged ingress and package leave the daemon running during a firewall-only
change. Setup removes stale owned direct-access rules when narrowing sources or
returning to tunnel-only mode. Antistatic and manifest UDP allowances remain independent.
Setup inspects the remaining active UFW rules before reporting success. An
unmanaged web allowance outside the requested sources stops setup with the
conflicting rule.
This includes IPv6 rules, port ranges, and application profiles such as
`Nginx Full`. Review `sudo ufw status numbered` and narrow or remove the rule;
setup preserves unrelated operator rules rather than deleting them silently.

## Create or update a tunnel

The setup run starts the wizard automatically when no tunnel exists. First-time
creation requires terminal input. An unattended run stops before tunnel
activation and retains the existing origin firewall policy. Run the helper on the
host for manual recovery or configuration changes.

```bash
sudo setup-cloudflare-tunnel
```

The helper installs or upgrades `cloudflared` from Cloudflare's signed APT
repository on every run. It authenticates with Cloudflare when creating a
tunnel, writes `/etc/cloudflared/config.yml`, and can install and enable the
`cloudflared` service. After verifying the service is active, it applies the
saved direct-access policy. Tunnel credentials and state are stored under
`/etc/cloudflared` with restrictive permissions.

Setup selects hostnames from deployments completed by the current profile, its
configured signed webhook domain, and its named Gogs and Antistatic services.
Select artifact-target hostnames and manually configured Nginx origins with the
helper. This keeps retained releases and unrelated virtual hosts out of the
automatic selection.

```bash
sudo setup-cloudflare-tunnel --non-interactive \
  --hostname api.example.com --hostname downloads.example.com
```

`--hostname` replaces the complete manual selection.
`--clear-hostnames` removes all manually selected names. Setup owns its
separate selection and preserves the manual list on reruns. On a fresh
interactive run without a managed selection, the helper prompts for hostnames.
Each selected name must be a literal DNS hostname on an enabled Nginx HTTP
site for port 80. Administrative and unrelated virtual hosts require explicit
operator selection. The helper removes routes whose HTTP origins are absent
and a routine refresh uses the recorded selection.
Each ingress entry sends its selected hostname to Nginx using Cloudflare's
`httpHostHeader` origin setting. [Cloudflare origin parameters](https://developers.cloudflare.com/tunnel/reference/origin-parameters/#httphostheader)

Older tunnel state contains only the last discovered routes. Rerun the saved
Basaltwater setup to migrate to its configured hostnames, or invoke the helper
with a reviewed `--hostname` list. A standalone unattended refresh of legacy
state without a selection stops for review. Managed setup also stops for review
when it has no hostname selection for a legacy tunnel. Review Cloudflare DNS and
Access policies for any retired hostname after migration.

Generated tunnel and Nginx configurations are validated before activation. A
non-interactive refresh compares complete hostname/origin entries, repairs a
missing or invalid config, and verifies that the service is active; it reports
failure when the service cannot be started instead of claiming success.
Changed ingress restarts cloudflared before recording the routes as applied;
failed restarts leave the prior route state and a `pending_activation` flag so
the next refresh retries even when only repairing an unchanged route set.
An unchanged, valid configuration leaves the running daemon alone when it is
already using the installed executable.
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

Rerun setup to apply changes to its managed hostname selection. Use the helper's
`--hostname` list to publish additional sites. Routine refresh keeps the saved
selection, including when another Nginx virtual host appears.

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

## Package updates

Every setup, patch, or helper run that refreshes a saved tunnel also refreshes
Cloudflare's signing key and stable APT source, refreshes package indexes, and
installs the current APT candidate. This migrates older standalone `.deb`
installations that lacked a repository. An installed `cloudflared` command no
longer skips the package check. Index download or signature failures stop setup
instead of accepting stale indexes as a successful update.
This package check runs even when general setup skips its already-completed
system package upgrade; `--refresh-packages` is not required for tunnel updates.

On security-enabled hosts, `auto-update-apt.timer` also upgrades packages from
this repository daily at 06:00, with the normal randomized delay. Cloudflare's
[package installations require package-manager updates](https://developers.cloudflare.com/tunnel/guides/update-cloudflared/);
the client's built-in updater does not maintain these installations.

Both tunnel setup and successful scheduled APT upgrades compare the running
service's executable with `/usr/bin/cloudflared`. A daemon still using the old
executable is restarted and checked for activation, even when ingress is
unchanged. A current daemon is left running. Scheduled updates leave an
inactive or missing service alone. A failed restart fails the maintenance job
and uses its normal failure notification. Later runs retry an older executable
that is still running, even without another package upgrade. If a failed
restart leaves the service inactive, inspect its journal and start it again
with `sudo systemctl start cloudflared`. A service configured to run another
executable must have its `ExecStart` path corrected before activation can pass.

Updating the controller checkout alone does not update a server. Rerun its
saved setup or patch to stage the current helper and scheduled updater:

```bash
basaltw patch server.example deploy --cloudflare
```

Then inspect package availability, the daemon's startup version, and the timer:

```bash
cloudflared --version
apt-cache policy cloudflared
sudo journalctl -u cloudflared -n 100 --no-pager
sudo systemctl status auto-update-apt.timer
sudo journalctl -u auto-update-apt.service -n 100 --no-pager
```

`cloudflared --version` reports the executable on disk; an older startup
version in the current daemon's journal can indicate that it still needs a
restart. Package activation briefly interrupts existing tunnel connections.

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

For signed build webhooks, use `--webhook-builds --webhook-domain DOMAIN`
with `--cloudflare`. The shared tunnel setup selects the configured webhook domain
and activates the HTTP origin. First-time
account authentication and public DNS configuration remain required.
See [webhook configuration](webhook-builds.md).

### Direct UDP services

The public hostname routes generated here do not proxy UDP. An Antistatic
deployment therefore keeps its direct UDP 3478 STUN access (and any required
direct TCP service port) even when `--cloudflare` is enabled; the public IP must
remain reachable for those ports. Use TCP ports other than 80 or 443 for hostless
Antistatic services on a Cloudflare origin. [Antistatic services](./antistatic.md)
Repository services can declare `public_udp` listeners in their manifest;
setup opens those UFW rules before tunnel activation and retains them when
closing public HTTP/HTTPS. Their optional hostnames describe DNS-only A/AAAA
records pointing directly at the public server IP; they are not tunnel ingress
routes. Provider firewall rules and DNS records still need configuration.

## Limitations and recovery

- `--cloudflare` changes Nginx behavior during setup and reconciles the origin
  firewall policy after the tunnel becomes active. The tunnel helper preserves
  existing SSH rules and UFW defaults. Use `--ssh-access-source` in setup to
  add a management network independently of direct web access.
- Tunnel creation requires an interactive browser login and Cloudflare account
  access; unattended setup cannot create the initial tunnel.
- If initial tunnel creation stalls or a previously verified tunnel becomes
  unavailable, inspect `cloudflared` and Nginx journals. An SSH port forward
  can reach the local origin for testing.
- To stop using the tunnel, restore the desired 80/443 firewall policy and
  remove or disable the Cloudflare-specific Nginx config after validating the
  replacement configuration.
