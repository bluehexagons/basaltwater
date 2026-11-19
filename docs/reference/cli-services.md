# Deployment, managed builds, and services

[Documentation index](command-line.md)

## Deployment Flags

| Flag | Description |
|------|-------------|
| `--deploy DOMAIN GIT_URL` | Deploy a repository to a domain |
| `--deploy-latest DOMAIN_OR_PATH GIT_URL` | Deploy while bypassing the release/dependency freshness policy |
| `--deployment-lite` | Use cached/pre-uploaded repository files only |
| `--deployment-full` | Pull fresh repositories and rebuild everything |
| `--full-deploy` | Always rebuild deployments even if unchanged |
| `--ssl` | Enable TLS: Let's Encrypt for domains and an IP-SAN self-signed certificate for hostless Gogs |
| `--ssl-email EMAIL` | Email for SSL registration |
| `--cloudflare` | Configure Cloudflare Tunnel; after activation, retain direct HTTP/HTTPS only from `--lan-access` / `--access-source` networks unless unrestricted access is selected |
| `--cloudflare-allow-direct` / `--no-cloudflare-allow-direct` | Enable unrestricted direct TCP 80/443 alongside the tunnel, or return to source-restricted/tunnel-only access |

For hosts-file testing on the target's LAN, use `--cloudflare --lan-access`.
Use `--access-source IP_OR_CIDR` for explicit LAN/VPN networks. The public tunnel
continues to serve Cloudflare traffic; local clients reach Nginx directly.
`--cloudflare-allow-direct` explicitly opens TCP 80/443 to any client, taking
precedence over generic source restrictions for those two ports. The negative
form returns to the generic source policy; clear `--lan-access` and explicit
sources too when restoring tunnel-only mode through a patch. These choices are
saved and passed to the tunnel helper on subsequent setup runs. See
[direct access alongside a tunnel](../services/cloudflare.md#direct-access-alongside-the-tunnel)
for setup, patch, helper, and local hostname examples.

Repos can also ship `basaltwater.json` manifests for multi-component deploys; see
[Deployments and manifests](../services/deployments.md) for the schema and examples.
Ruby/Rails deployment support has been discontinued. Their repositories are
rejected before remote setup begins. Ruby applications are outside Basaltwater's
deployment workflows.

## Managed builds, webhooks and GitHub runners

See [build systems](../services/build-systems.md) for defaults, a fresh
two-command walkthrough, capacity and migration.

| Flag | Description |
| --- | --- |
| `--build-server` | Enroll selected Ed25519 public companion as deploy with wildcard projects; install Go, Node and Python |
| `--build-project DOMAIN HTTPS_REPO` | Optional restrictive project grant; repeat per destination |
| `--build-host-policy FILE` | Explicit public builder policy |
| `--build-on SAVED_BUILDER` | Build via SSH core; initial target setup generates exact authority |
| `--deploy DOMAIN HTTPS_REPO` | Target destination and committed source |
| `--build-target-auto` | Update authority from complete merged deployment set |
| `--build-target-policy FILE` | Explicit public target policy |
| `--build-limit RESOURCE INTEGER` | Generated resource ceiling; repeat per resource |
| `--build-max-records N`, `--build-max-bytes BYTES` | Generated retained capacity |
| `--no-build-server`, `--no-build-target`, `--no-build-on` | Explicitly disable corresponding role or selection; preserve user data |
| `--github-runner`, `--no-github-runner` | Enable optional runner on server_lite/server_web, or disable installed services preserving credentials |
| `--ci-repository OWNER/REPO` | Repeat private repository scopes |
| `--ci-organization ORGANIZATION` | Organization scope instead of repositories |
| `--ci-allowed-user LOGIN`, `--ci-label LABEL` | Repeat trusted users or labels |
| `--ci-runner-group GROUP` | Organization runner group |
| `--ci-registration-token INLINE` | Transient token for exactly one scope |
| `--ci-registration-token-file PATH`, `--ci-register-with-gh` | Alternative registration sources; mutually exclusive with inline token |
| `--webhook-builds` | Signed GitHub push intake into managed worker |
| `--webhook-repository OWNER/REPO`, `--webhook-branch BRANCH` | Repeat repository and branch allowlists; default branch main |
| `--webhook-domain DOMAIN`, `--ssl` | Public webhook HTTPS route |
| `--webhook-secret SECRET` | 32–512 character signing secret; generated and retained if omitted |
| `--webhook-github-token TOKEN` | Optional broker-only private-fetch credential |

No registration source leaves runners pending for panel registration.
Inline tokens cannot register repeated repository scopes. The dedicated
`server_github_runner` profile uses the same installer, with runner-only scope.
See [runner registration](../services/github-runner.md) and
[webhooks](../services/webhook-builds.md). Transient secrets are not saved in cache or
transported in SSH argv. Retired commands and flags are listed only in the
[migration guide](../services/build-systems.md#migrate-retired-installations).

## Antistatic

`--antistatic-server` and `--antistatic-db` deploy the managed release binaries.
Hostname-based specs are reverse-proxied through Nginx;
hostless specs such as `:8080` or `:8081` listen directly on the target port.

| Flag | Description |
|------|-------------|
| `--antistatic-server [DOMAIN][:PORT]` | Deploy the lobby server |
| `--antistatic-admin USERNAME` | Enable HTTPS-only report administration using the matching workspace credential |
| `--no-antistatic-admin` | Disable administration and remove its remote credential file |
| `--antistatic-db [DOMAIN][:PORT]` | Deploy antistatic-db |

The lobby server stores bounded report collections under
`/var/lib/antistatic`, sends a local `/health` probe after each service start,
and exposes STUN directly on UDP 3478. Hostname deployments redirect ordinary
HTTP traffic to HTTPS; `--cloudflare` instead marks tunnel traffic secure at
the private nginx-to-server boundary.

Admin access requires a hostname deployment and either `--ssl` or
`--cloudflare`. Store its password separately, then reference the username.
See [Antistatic services](../services/antistatic.md) for the complete workflow and
credential-storage behavior.

## Gogs

See [Gogs Git service](../services/gogs.md) for hostname and hostless modes, initial
credentials, Git-over-SSH, data layout, and update recovery.

Deploy a minimal self-hosted Git service with an optional hostname, port, and
data directory:

```bash
basaltw setup server_web 192.168.1.10 \
  --gogs git.example.com:3000 /var/lib/gogs \
  --ssl --ssl-email admin@example.com
```

The Gogs port is the direct public HTTP/HTTPS port and defaults to 3000; Gogs
does not implicitly claim port 443. With `--cloudflare`, it instead remains the
private backend port because the tunnel supplies the standard public HTTPS
endpoint. Port 80 is reserved when Gogs uses nginx, TLS, a hostname, or
Cloudflare; hostless direct plaintext mode may deliberately use it.
Hostname mode requires `--ssl` or `--cloudflare`.
Use `--gogs :3000` for a hostless loopback service reached through an SSH
tunnel. To expose hostless HTTPS on a trusted private network, add `--ssl` and
repeat `--gogs-source IP_OR_CIDR`. Hostless TLS uses an IP-SAN self-signed
certificate that clients must explicitly trust; reruns replace an invalid,
mismatched, or soon-expiring certificate. Only non-global IPv4 sources from
the combined generic and Gogs-specific policy are applied, active UFW is
required, and setup fails before binding externally if it cannot verify the
rules. A literal IP cannot be used as the hostname, Cloudflare requires a
hostname, and Let's Encrypt hostname mode rejects source restrictions because
HTTP-01 renewal needs public port 80. Gogs releases require
and verify the SHA-256 supplied in GitHub release asset metadata before
extraction, and activation can
roll back to the previous release if service, post-update, or health checks
fail.

| Flag | Description |
|------|-------------|
| `--gogs DOMAIN[:PORT] [DATA_PATH]` | Install Gogs on direct public HTTP/HTTPS `PORT` (default 3000), or use `PORT` as the private backend with `--cloudflare`; port 80 is reserved when nginx/TLS/hostname/Cloudflare is used; omit `DOMAIN` for loopback/source-restricted hostless mode |
| `--gogs-source IP_OR_CIDR` | Allow one private IPv4 source to a hostless Gogs listener; repeatable and requires active UFW |

Inspect an installed service from the control system with:

```bash
basaltw gogs health 192.168.1.10
basaltw gogs health 192.168.1.10 --json \
  --min-free-bytes 2147483648 --min-free-inodes 20000
```

The command inherits the saved setup username and SSH key unless overridden
with `--username` or `--key`. A non-root SSH user must have non-interactive
sudo, which requires the setup's explicit `--nopasswd` compatibility mode;
otherwise connect as root. Health is nonzero when the service,
SQLite database, managed paths, local filesystem, update service/timer,
capacity thresholds, or documented nginx upload limit is unhealthy. JSON also
reports release identity, per-category usage, the external URL, and whether
the LFS HTTP endpoint is configured for non-loopback access or is loopback-only.
Reverse-proxied deployments additionally require valid nginx configuration and
a successful target-local request through the direct TLS listener or
Cloudflare origin route. This is not an external client or authentication
probe, so it does not prove public DNS, routing, edge, firewall, or credential
health.

Configure a clean local worktree so GitHub remains the canonical fetch source,
Gogs receives mirrored Git refs, and only Gogs stores Git LFS objects:

```bash
basaltw gogs repo-configure ~/repos/project \
  --github-url https://github.com/team/project.git \
  --gogs-url https://git.example.com:3000/team/project.git \
  --track 'assets/**' --dry-run
```

Without `--dry-run`, the command configures repository-local Git LFS, the
`origin` and `gogs` remotes, two `origin` push URLs, `.lfsconfig`, and any
repeatable `--track` patterns. It never stages, commits, pushes, creates remote
repositories, or configures credentials. `--no-combined-push` configures only
GitHub as the `origin` push URL so the Gogs Git remote can be updated
explicitly. See [Gogs Git service](../services/gogs.md) for the storage, credential, and
off-network clone boundaries.

## HomeBox

See [HomeBox inventory](../services/homebox.md) for onboarding, storage, credentials,
updates, and recovery. One native systemd instance is supported per server.

| Flag | Description |
| --- | --- |
| `--homebox DOMAIN[:PORT] [DATA_PATH]` | HTTPS hostname (frontend default 443; requires `--ssl`), or `:PORT` for SSH-tunnel access; data defaults to `/var/lib/homebox` |
| `--homebox-port PORT` | Hostname mode's loopback backend port, default 7745 |
| `--homebox-admin EMAIL` | Initial owner email, default `admin@homebox.local`; target generates the password |
| `--homebox-version TAG` | Explicit stable release for an immediate upgrade, minimum/default `v0.27.0`; ordinary reruns retain the installed release while the managed timer checks newer stable releases weekly |
| `--no-homebox` | Disable service and remove its ingress while retaining data, secrets, releases, and backups |

Health, backup, and restore accept `--username`, `--key`, `--workspace`, and
`--json`. They inherit saved SSH settings; use `--username root` unless the
selected user has non-interactive sudo. Archive paths refer to the target.
Backup and restore stop the service; restore requires `--yes`. A dry run
validates command intent without connecting. Health exits nonzero for an
unhealthy or incomplete installation and probes HTTPS locally, so it does not
verify external DNS or routing. Enabled HomeBox services also receive a weekly
automatic stable-release check through the recovery-aware update timer;
`--homebox-version` remains available for a deliberate immediate upgrade.
Tags before `v0.27.0` are rejected in requests, installed state, and backup
metadata. Cloudflare ingress is not supported.
