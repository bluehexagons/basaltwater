# Internal HTTPS sites and live previews

`basaltwater-web` publishes static builds and explicit loopback development servers
through the HTTPS gateway managed by basaltwater. The gateway reuses the VM's
certificate and saved access-source policy; do not bind development servers to
`0.0.0.0`, edit Nginx, or add UFW rules manually.

The gateway is installed by features that configure the internal web host,
including the T3 Code web interface and the Godot web bundle.
T3 Code installations also place the managed web-gateway skill in compatible
terminal agents, so the publishing workflow is discoverable without enabling
the Godot bundle.

Run `basaltwater-web` commands on that configured VM as the account that owns the
site, not on a separate controller. Check `command -v basaltwater-web` first. If it
is missing, provision a gateway-capable feature before continuing; the
[beginner walkthrough](GETTING_STARTED.md#5-try-another-feature) shows one path.

## Try a plain HTML page

Once the gateway is installed, this example needs no JavaScript project or
package manager. First run `basaltwater-web site list` and choose an unused site
name. The example uses `hello`; change it throughout if that name exists,
because publishing the same name replaces that site's snapshot.

Create a fresh directory in your home and a small page:

```bash
demo_dir="$(mktemp -d "$HOME/basaltwater-web-demo.XXXXXX")"
printf 'Source directory: %s\n' "$demo_dir"
mkdir "$demo_dir/public"
cat > "$demo_dir/public/index.html" <<'HTML'
<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>My Basaltwater demo</title>
<h1>Hello from Basaltwater!</h1>
<p>This page is served by the managed HTTPS gateway.</p>
</html>
HTML
basaltwater-web publish site hello --project "$demo_dir" --no-build --output public
basaltwater-web site doctor hello
basaltwater-web site url hello
```

`demo_dir` holds the new directory's path for this terminal session. Publication
does not require `sudo`.

The doctor should report a healthy site. Open the printed URL in a browser and
look for **Hello from Basaltwater!** A browser on another computer needs a route
to the VM and may need [client CA trust](CLIENT_CA_TRUST.md); `localhost` on
that computer refers to itself, not the VM. Use a gateway-reported VM address.

To change the page, edit `public/index.html` in the printed source directory
and repeat the publication command in the same terminal. To remove just the
hosted demo:

```bash
basaltwater-web site remove hello --yes
```

This keeps your source directory. The examples below are alternatives for
existing projects and live servers; they are not additional required steps.

## Publish a static site

For a Vite or similar JavaScript project with a `build` script:

```bash
cd ~/repos/my-site
basaltwater-web publish site
```

The publisher derives the site name from `package.json`, installs dependencies
when `node_modules` is absent, runs the build, detects `dist`, `build`, `out`, or
`public`, and atomically activates the result. It never runs project build
commands as root. Published sites share the existing TCP 8443 HTTPS origin and
do not consume a dedicated listener or firewall rule:

```text
https://HOST:8443/sites/USERNAME/my-site/
```

The active site is a copied snapshot, not a symlink to the checkout or build
directory. A later edit, commit, or standalone build leaves the hosted copy
unchanged until `basaltwater-web publish site` runs again. This allows failed builds
to leave the last publication intact, but it also means a healthy existing
route may be stale relative to the repository.

Applications are hosted below the `/sites/USERNAME/NAME/` prefix. Use relative
asset and data URLs or configure the build tool with that base; root-relative
paths such as `/assets/app.js` resolve outside the publication.

Specify the name, project, or output when detection is not sufficient:

```bash
basaltwater-web publish site docs --project ~/repos/product --output web/dist
basaltwater-web publish site docs --project ~/repos/product --no-build --output dist
basaltwater-web publish site docs --no-install
basaltwater-web publish site docs --json
basaltwater-web publish site docs --open
```

`--no-build` requires an existing output directory. `--no-install` skips the
automatic dependency installation but still runs the build. Static output must
contain `index.html`, remain inside the project, and contain no symbolic links
or special files. An unreadable output directory or other scan error stops
publication before activation and leaves the previous site intact.
When an npm project has no lockfile and `node_modules` is
absent, the automatic `npm install` can create `package-lock.json`; inspect Git
status before and after publication and handle that file according to the
project's dependency policy.

With `--json`, stdout contains only the publication result or error object.
Dependency installation and build logs stream to stderr, including on failure.

Dependency installation and the build share a one-hour deadline. Set
`--build-timeout SECONDS` to a positive limit when needed. Timeout terminates
the command's process group and preserves the existing publication; dependency
changes in the project are not rolled back. Copying and activation follow a
successful build and are outside this deadline.

Inspect and remove publications as the owning user:

```bash
basaltwater-web site list
basaltwater-web site url docs
basaltwater-web site doctor docs
basaltwater-web site remove docs --yes
```

Publishing and removal of the same site share a lock, so removal waits for an
active publication before checking and deleting the result. Lock files must be
regular files with no symbolic or additional hard links.

`site doctor` verifies the managed site directory and the HTTPS status of its
root URL. It does not compare the served tree with the current build, follow a
client-side route, or execute JavaScript. After publishing a change, compare a
representative non-sensitive served file with its build artifact when content
freshness matters:

```bash
set -o pipefail
published_url="$(basaltwater-web site url docs)"
curl --fail --silent --show-error "${published_url}assets/app.js" | sha256sum
sha256sum dist/assets/app.js
```

Replace `assets/app.js` with an actual file from your build output. Require
the download pipeline to succeed before comparing hashes; `pipefail` preserves
a failed HTTP request's exit status. Do not add `-k`.
A fragment such as `#/README.md` is client-side state and is
not sent in the HTTP request; test the corresponding public file and then use a
browser for the rendered route. A failed build or activation keeps the previous
publication available.

## Start a managed live preview

Use a supervised preview when hot-module replacement, an API, or WebSockets are
required:

```bash
cd ~/repos/my-vite-project
sudo basaltwater-web preview start my-project --project .
```

For Vite projects, Basaltwater detects the `dev` or `preview` package script,
allocates a private loopback port, supplies strict `127.0.0.1` binding
arguments, starts a bounded systemd service as the requesting user, waits for
HTTP readiness, and then creates the managed HTTPS forward. The service is not
enabled at boot.

When dependencies are missing, installation runs as the requesting user with a
30-minute process-group deadline. Gateway and systemd control commands have a
60-second deadline; a timeout aborts the mutation and leaves the existing
publication or route in place where rollback is possible.

Override automatic Vite detection with an explicit argv command after `--`.
Use `{host}` and `{port}` placeholders without shell interpolation:

```bash
sudo basaltwater-web preview start my-project --project . -- \
  ./serve-preview --host '{host}' --port '{port}'

sudo basaltwater-web preview start my-game --project . --profile godot -- \
  ./preview-server --listen '{host}:{port}'
```

The command is stored in an owner-only launcher beneath a root-managed
directory and is executed with `User=` and `Group=` set to the requesting
account. It is not executed by the privileged gateway process. `--profile
godot` adds cross-origin isolation headers for threaded Godot previews.

Useful lifecycle commands are:

```bash
basaltwater-web preview list
basaltwater-web preview url my-project
basaltwater-web preview logs my-project
basaltwater-web doctor my-project
sudo basaltwater-web preview stop my-project
sudo basaltwater-web preview prune --yes
```

Use `--replace` to deliberately replace an existing same-named preview. A
startup, readiness, route, or state-write failure rolls back both the service
and route. `preview prune` removes stopped services and their routes; it does
not touch another user's previews.

## Forward an existing loopback service

Keep `forward` as the low-level path when another service manager already owns
the upstream process:

```bash
sudo basaltwater-web forward add api-preview \
  --to 127.0.0.1:3000 \
  --wait 30 \
  --health /health

basaltwater-web forward list
basaltwater-web forward url api-preview
basaltwater-web doctor api-preview
sudo basaltwater-web forward remove api-preview
```

The forward list reports whether each upstream TCP port is ready. Use
`sudo basaltwater-web forward prune --yes` only for unmanaged dead forwards; managed
preview services should be cleaned up through `preview prune`.

Nginx retains its 1 MiB request-body default unless the upstream service has a
larger documented limit. Set a bounded per-route limit when needed:

```bash
sudo basaltwater-web forward add upload-api \
  --to 127.0.0.1:3000 \
  --max-body-size 50m
```

The limit accepts a positive integer with an optional `k`, `m`, or `g` suffix
and is capped at 1 GiB. T3 Code's managed route sets 50 MiB automatically.
The managed Syncthing route uses a dedicated profile to retain Syncthing's
loopback host validation while preserving the external host in forwarding
headers.

## Certificate trust

When the VM uses its local certificate authority, enroll the public CA only on
LAN clients that should open the private origin. Client enrollment is optional;
VM-local checks and managed Playwright already use the VM trust store. The
command prints the file, download URL, and SHA-256 fingerprint:

```bash
basaltwater-web ca
```

Verify the printed fingerprint before installing the public CA. See
[Client CA trust](CLIENT_CA_TRUST.md) for safe transfer, desktop, ChromeOS, and
mobile enrollment, application-specific trust limitations, and removal.

Do not disable TLS verification. `site doctor` and the general `doctor`
command verify the same trusted HTTPS endpoints that browsers should use. For
an authenticated forward, a `401` response with a `WWW-Authenticate` challenge
confirms that the protected endpoint is reachable; other error responses still
fail the check.

## Security and state

Forward and preview mutations share a root-owned nonblocking lock at
`/etc/basaltwater/internal-web/mutation.lock`. A competing command fails before
reading or changing state; retry after the active command finishes. Ownership
lasts through rollback and state writes and is released on process exit. Do not
delete the stable lock file to bypass a running command.

- Static builds and live commands run as the configured non-root owner.
- Live upstreams are limited to unprivileged loopback ports.
- Only Nginx's HTTPS listener receives a managed UFW rule.
- One explicitly named project is exposed at a time; ports are never discovered
  and exposed automatically.
- Generated Nginx, firewall, publication, preview, and systemd state is
  validated before mutation and scoped to its recorded owner.
- Preview units use systemd process, memory, privilege, device, kernel, and
  address-family limits.

Use `basaltwater-web forward reconcile` after repairing managed state, and
`basaltwater-web doctor NAME` to validate a reachable forwarded endpoint.
