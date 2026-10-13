# bluehexagons_droplet deployment

This runbook prepares a fresh Debian web server for the portfolio, Go Click,
Don't Starve Food Guide, and an optional private static site. The saved
Basaltwater name is `bluehexagons_droplet`; the Linux hostname uses hyphens
because system hostnames do not accept underscores.

## Controller and server setup

Use the updated main branches of Basaltwater and the application repositories.
`basaltw` must launch the updated Basaltwater code; an older separately installed
copy will retain its old behavior. The controller needs Git access to every
repository. Keep private repository identifiers and deployment domains in your
private configuration; replace `PRIVATE_REPO_URL` and `PRIVATE_SITE_DOMAIN`
below locally. Check private repository access without placing tokens in a URL:

```bash
git ls-remote PRIVATE_REPO_URL HEAD
```

Use the controller's existing HTTPS credential helper, or replace that URL
with an SSH URL authenticated by the controller's SSH agent. The server needs
root SSH access with the setup key. Basaltwater creates the non-root `deploy`
management account; its build and application accounts are separate.

Replace `SERVER_IP` and `/path/to/server_key`, append `--dry-run` for preflight,
then execute the same command without `--dry-run`:

```bash
basaltw setup server_web SERVER_IP deploy \
  --key /path/to/server_key \
  --name bluehexagons_droplet \
  --hostname bluehexagons-droplet \
  --cloudflare \
  --deploy bluehexagons.com https://github.com/bluehexagons/bluehexagons.git \
  --deploy clicker.bluehexagons.com https://github.com/bluehexagons/goclick.git \
  --deploy foodguide.bluehexagons.com https://github.com/bluehexagons/foodguide.git \
  --deploy PRIVATE_SITE_DOMAIN PRIVATE_REPO_URL
```

The Food Guide manifest adds `bluehexagons.com/foodguide/` to the same Nginx
server block as the portfolio. It builds once and serves the same output on
both routes; do not deploy that manifest a second time for the subpath. A
manifest component's `path` owns its route, rather than inheriting the path
from `--deploy`. Keep all four repositories in each full setup/redeployment:
Nginx reconciles the generated sites to the supplied deployment set.

`--dry-run` clones all repositories into disposable controller staging and
validates their manifests without running builds or contacting the target.
The setup uploads their source and builds on the target; it does not copy local
uncommitted application edits or require Git credentials on the web server.
No `--ssl`, Rails, separate CI/CD server, webhook receiver, or application
container runtime is required for this tunnel deployment. Cloudflare supplies
public HTTPS; Nginx serves cloudflared over local HTTP.

## Required features

| Route | Deployment contract | State and build needs |
| --- | --- | --- |
| `bluehexagons.com/` | Static portfolio from `dist/` | Node 24 via `.nvmrc`; TypeScript, thumbnails and Vite; default build excludes the shop/API |
| `clicker.bluehexagons.com/` | Manifest Go service on an automatically allocated loopback port | Go version from `go.mod`; embedded frontend, pure-Go SQLite, dedicated runtime account, `/up` health check, secure cookies and proxy IP trust |
| `foodguide.bluehexagons.com/` and `bluehexagons.com/foodguide/` | Two static manifest components using `html/` | Node 24, development dependencies, TypeScript modules and Sharp sprite generation |
| `PRIVATE_SITE_DOMAIN/` | Private source, public static output | Controller Git credentials; copy only the site's public assets into its serving directory |

Goclick's release is `/var/www/clicker_bluehexagons_com`. Its database lives at
`/var/www/.basaltwater_shared/clicker_bluehexagons_com/server/data/goclick.db`;
the adjacent root-controlled `backups/` retains 14 consistent deployment
backups. Builds run under isolated non-root accounts, and manifest activation
rejects a failed health check and restores the previous release and units.
Deployment backups do not replace scheduled off-server database backups.

## Tunnel creation and traffic cutover

Before changing DNS, smoke-test the origin from the server with a Host header:

```bash
curl -fsS -H 'Host: bluehexagons.com' http://127.0.0.1/ -o /dev/null
curl -fsS -H 'Host: bluehexagons.com' http://127.0.0.1/foodguide/ -o /dev/null
curl -fsS -H 'Host: foodguide.bluehexagons.com' http://127.0.0.1/ -o /dev/null
curl -fsS -H 'Host: clicker.bluehexagons.com' http://127.0.0.1/up
curl -fsS -H 'Host: PRIVATE_SITE_DOMAIN' http://127.0.0.1/ -o /dev/null
```

On the server, run:

```bash
sudo setup-cloudflare-tunnel
```

Authenticate with Cloudflare, use `bluehexagons_droplet` as the tunnel name,
and install/start the service. The helper discovers four hostnames from Nginx;
the Food Guide subpath shares the apex hostname's ingress. The tunnel needs
outbound connectivity to Cloudflare, including
[port 7844 for its transport](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-with-firewall/).
Configure DNS routes for all four hostnames to this tunnel. If the zones are
in different Cloudflare accounts, one tunnel cannot serve them through the
same account workflow; resolve that before cutover. Existing DNS records may
need to be replaced rather than added alongside their current targets.

Goclick's Rails data import is a separate controlled cutover. Stop writes to
Minimal Clicker and copy a consistent backup of its **primary** production
SQLite database; the Rails admin JSON export cannot preserve passwords. Follow
the updated `goclick/docs/migration.md` before directing clicker traffic to the
new server. Obtain the actual hashed Linux runtime account with:

```bash
systemctl show app-clicker_bluehexagons_com-server.service -p User --value
```

Keep the Rails source backup and a pre-import Goclick backup. Existing users
will sign in again; password hashes and gameplay data are preserved, and Rails
refresh sessions are not imported. Verify representative accounts and counts
before switching traffic.

Enable HTTPS redirects at Cloudflare for these public sites. Goclick deliberately
sets Secure cookies, so browser authentication requires public HTTPS. Do not
enable Cloudflare caching for `/api/*` or authenticated clicker responses; purge
any old static cache rules/entries that override the new revalidation headers.
The helper closes direct public 80/443 only after cloudflared becomes active.
Check service health and public reachability separately after DNS cutover:

```bash
sudo systemctl status nginx cloudflared app-clicker_bluehexagons_com-server.service
curl -fsS https://clicker.bluehexagons.com/up
curl -fsS https://bluehexagons.com/foodguide/img/sprites/sprites.json -o /dev/null
curl -fsS https://foodguide.bluehexagons.com/img/sprites/sprites.json -o /dev/null
curl -fsS https://PRIVATE_SITE_DOMAIN/ -o /dev/null
```

## Audit scope and remaining work

The repository audit repaired retired Goclick manifest/path references, added
Food Guide's build and dual routes, isolated the private site's public output,
and pinned portfolio builds to Node 24. It also repaired tunnel helper symlink
imports, cloudflared validation argument order, refresh activation/retry state,
existing-service updates, loopback visitor-IP restoration, static subpath
matching/redirects, and fixed-name asset caching. Portfolio's locked
`source-map-js` was updated to the patched 1.2.2 release for
[GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q).

Validation runs locally against built artifacts and generated Nginx routes;
mocked tests cover system setup and recovery. Initial provisioning, real
Cloudflare authentication/DNS, external HTTPS, SSH/firewall reachability, and
the production Rails import still require the actual server and credentials.
Use [Cloudflare operations](CLOUDFLARE.md),
[deployment safety](DEPLOYMENT_SAFETY.md), and
[transaction recovery](TRANSACTION_RECOVERY.md) for ongoing operations.
