# Internal web publishing

[Topic index](internal-web.md)

Run `basaltwater-web` on the configured VM as the site owner. Check that the
command is available before continuing. Bind development servers to loopback
and use the gateway's managed Nginx and firewall configuration.

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
to the VM and may need [client CA trust](client-ca-trust.md); `localhost` on
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
basaltwater-web publish site game --build-script build:web --output dist
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
`--build-script NAME` selects an existing package script; it defaults to `build`.
Projects can keep separate web and desktop commands such as `build:web` without
adding aliases for the gateway.

Initialization, static builds, and automatic previews select npm, pnpm, or Yarn
from the project's exact-version `packageManager` declaration and lockfiles.
Conflicting managers or linked/special lockfiles fail before project commands
run. With no declaration or lockfile, npm is the default. npm recognizes both
`package-lock.json` and `npm-shrinkwrap.json`; Yarn's lock header distinguishes
classic and modern releases when no version is declared. Existing lockfiles use
`npm ci`, pnpm's frozen mode, Yarn classic's frozen mode, or modern Yarn's
immutable mode. Without a lockfile, installation can create one; inspect Git
status before and after and follow the project's dependency policy. pnpm and
Yarn web commands use Corepack, which must already be available. Corepack can
download the selected package-manager version on first use; offline operation
requires that version in its cache. Manifest workflows retain native commands.

Builds and automatic previews use the project's Node selection (`.nvmrc`,
`.node-version`, and package engines) as the requesting user. An unavailable
compatible runtime fails without installing Node or changing the account default;
inspect it with `basaltw node doctor --project .`.

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
basaltwater-web site verify docs --project ~/repos/product --output dist --json
basaltwater-web site remove docs --yes
```

Publishing and removal of the same site share a lock, so removal waits for an
active publication before checking and deleting the result. Lock files must be
regular files with no symbolic or additional hard links.

`site doctor` verifies directory and root HTTP readiness. `site verify` checks
all artifact hashes against a private receipt activated with the snapshot,
then checks served HTTPS bytes for paths that Nginx serves. Any file with a
dot-prefixed path segment is checked on disk only and counted in the JSON
result as `hidden_artifacts`. `--project PATH` also compares the Git revision
and, when recorded, the build output directory inside that checkout. Use
`--output DIR` with `--project` to select a different local output directory.
Use `--local-only` to omit HTTP coverage, or `--timeout SECONDS` (1–300, default 60)
to bound the HTTPS phase. Verification never builds or republishes.

The mode-`0600` `.basaltwater-receipt.json` records the observed source commit,
dirty state, output directory, publication time, build mode, and SHA-256/size
inventory of at most 10,000 artifacts. It is excluded from public metadata and
Nginx denies dotfile requests. Failed activation retains the previous receipt
with its snapshot. Receipts are limited to 4 MiB; exceeding the size or artifact
limit fails before activation. Older publications can return `receipt_missing`.
An authorized publication within the limits generates their receipt.

Dirty or unavailable Git metadata leaves `source_matches` unverified (`null`)
with a warning. A different source commit or artifact inventory returns a
nonzero exit code and structured issue/remediation. `--no-build` records existing
output without proving it came from the observed source. Matching served bytes
do not prove JavaScript rendering, client-side routing, or browser cache freshness.
For older installations without the verification command, compare a public
artifact manually:

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

## Publish a Godot site

From a Godot project with its engine, matching web export templates, and a Web
export preset, run:

```bash
basaltwater-web publish site my-game --profile godot
```

Use `--preset NAME` for a different Web preset or `--debug` for a debug export.
The publisher checks the exported HTML, JavaScript, WASM, and PCK before
replacing the previous snapshot, even if Godot reports a successful build.
To publish an existing export with `index.html`, `index.js`, `index.wasm`, and
`index.pck`, run:

```bash
basaltwater-web publish site my-game --profile godot --no-build --output exports/web
```

The game is published at `/sites/USERNAME/my-game/` and managed with the same
`site list`, `site url`, `site doctor`, `site verify`, and `site remove` commands.
The gateway adds cross-origin isolation headers to Godot sites, and
`site doctor` checks those headers and the WebAssembly content type.
`site verify` checks the published snapshot and the bytes served over HTTPS,
including generated gzip files. When comparing the original export with
`site verify my-game --project .`, it uses the recorded output path for a
prebuilt export and excludes generated gzip copies from the local comparison.
For exports built directly by the publisher, pass `--output DIR` to compare
a separately exported local build.

Gateway setup and refresh move existing Godot snapshots from `/games/` to
`/sites/` without re-exporting. If the site name is taken, the game gets
`-godot`, then a numbered suffix if needed. Refresh prints each migrated URL.
Use `basaltwater-web site list` to find the new links and pass the migrated site
name explicitly when publishing a later build. On an older gateway,
an interrupted refresh leaves unmoved games reachable at their original URLs.
It retires the old `/games/` route once migration succeeds. A legacy snapshot
keeps its private receipt, including the recorded commit and original hashes.
For exports published without a receipt, migration records the artifact hashes
and leaves the source revision unknown. Read `source.commit` with
`basaltwater-web site verify NAME --json`; add `--project PATH` to compare
against a checkout. A receiptless legacy snapshot that exceeds the receipt
limits still moves with a warning. `site doctor` can check that snapshot, and
`site verify` reports `receipt_missing`.

For a custom domain, deploy a repository with a
[`godot-web` manifest component](deployments-manifests.md#godot-web-application).

## Start a managed live preview

Use the [native T3 preview](../agents/browser-t3-preview.md#collaborative-preview-and-private-networks)
for a development server running in a T3 workspace. Use a supervised gateway
preview when the server needs a managed lifecycle and a separate HTTPS URL
for hot-module replacement, an API, or WebSockets:

```bash
cd ~/repos/my-vite-project
basaltwater-web preview start my-project --project .
```

For Vite projects, Basaltwater detects the `dev` or `preview` package script,
allocates a private loopback port, supplies strict `127.0.0.1` binding
arguments, starts a bounded systemd service as the requesting user, waits for
HTTP readiness, and then creates the managed HTTPS forward. The service is not
enabled at boot.

Select a different Vite package script when a project separates web and desktop
development:

```bash
basaltwater-web preview start my-project --project . --script dev:web
```

This retains automatic dependency installation, project Node selection, and
strict loopback arguments. The selected script must accept Vite's `--host`,
`--port`, and `--strictPort` options. Do not combine `--script` with an explicit
command after `--`; use that command route for other server interfaces.

When dependencies are missing, installation runs as the requesting user with a
30-minute process-group deadline. Gateway and systemd control commands have a
60-second deadline; a timeout aborts the mutation and leaves the existing
publication or route in place where rollback is possible.

Override automatic Vite detection with an explicit argv command after `--`.
Use `{host}` and `{port}` placeholders without shell interpolation:

```bash
basaltwater-web preview start my-project --project . -- \
  ./serve-preview --host '{host}' --port '{port}'

basaltwater-web preview start my-game --project . --profile godot -- \
  ./preview-server --listen '{host}:{port}'
```

Preview routes support the same bounded request-body limit as low-level
forwards. Specify it before the command separator when an application accepts
larger uploads, such as a 128 MiB backup with multipart overhead:

```bash
basaltwater-web preview start upload-api --project . --max-body-size 129m -- \
  ./serve-preview --host '{host}' --port '{port}'
```

The limit accepts a positive integer with an optional `k`, `m`, or `g` suffix,
is capped at 1 GiB, and defaults to Nginx's 1 MiB limit when omitted.

The command is stored in an owner-only launcher beneath a root-managed
directory and is executed with `User=` and `Group=` set to the requesting
account. It is not executed by the privileged gateway process. `--profile
godot` adds cross-origin isolation headers for threaded Godot previews.

Useful lifecycle commands are:

```bash
basaltwater-web preview list
basaltwater-web preview status my-project --json
basaltwater-web preview url my-project
basaltwater-web preview logs my-project
basaltwater-web forward doctor my-project
basaltwater-web preview restart my-project --wait 60 --json
basaltwater-web preview stop my-project
basaltwater-web preview prune --yes
```

Use `--replace` to deliberately replace an existing same-named preview. The
gateway validates automatic script and package-manager metadata, stops the old
preview, and launches the replacement. If startup or activation fails, it
attempts to remove the new service and route. Use `preview prune` to remove
your stopped services and their routes.

`preview restart` reruns the existing service's saved command and waits for its
recorded HTTP health path. It preserves the loopback port, HTTPS URL, profile,
upload limit, and route; it does not install dependencies, change the command,
or rebuild a precompiled executable. Build that executable first when needed.
The default readiness deadline is 30 seconds; `--wait` accepts more than zero
and at most 300 seconds. A failed restart or readiness check returns a nonzero
status, retains the route, and can be investigated with `preview logs`.

Preview and forward commands report the URL selected by the gateway policy,
including a localhost HTTPS URL for loopback-only listeners with the VM CA.

During agent development, keeping a configured preview current is part of
editing its checkout. The [project development skill](../../common/agent_skills/basaltwater-project-development/SKILL.md#keep-development-previews-current)
sets the expectation across projects. The [gateway refresh guide](../../common/agent_skills/basaltwater-web-gateway/references/refresh.md)
covers checking hot reload, restarting live services, and republishing static
and Godot snapshots. Agents verify a changed resource at the existing URL
before handoff.

### Inspect a live preview's build status

Run `basaltwater-web preview status my-project` as the preview owner. The
command shows the configured checkout, service activity, an HTTPS probe of the
saved health path, and reported builds. Use `--json` for automation. Existing
previews can use this command without a restart or a route change.

To report rebuilds, have the project's build command or watcher atomically
replace `$HOME/.local/state/basaltwater/preview-builds/my-project.json` after
each completed attempt. Substitute the preview name for `my-project` and use
the absolute checkout path from `preview list`. Keep the directory at mode
`0700` and the file at `0600` under the preview account. Keep the report outside
the served tree and use a short error summary without private values.

```json
{
  "version": 1,
  "project": "/home/agent/repos/my-project",
  "last_success_at": "2026-01-01T10:00:00Z",
  "latest_error": {
    "at": "2026-01-01T10:02:00Z",
    "message": "Bundling failed in game.ts"
  }
}
```

Use UTC timestamps with `Z` or `+00:00`. Set `last_success_at` or
`latest_error` to `null` until that event has occurred. On failure update
`latest_error` and retain the last successful time. On success update
`last_success_at`. A newer success supersedes the error for the current build
status, while both timestamps remain visible. Keep error messages to one
printable line of up to 512 characters.

An absent report yields `build.status` of `not-reported`. A reported build
failure or unreadable report causes a nonzero exit even if `https.ok` is true.
An unavailable checkout, stopped service, or failed HTTPS check also causes a
nonzero exit. Build timestamps come from the project's reporting hook. To
verify the code being served, fetch a changed resource at the existing preview
URL and compare it with the expected output.

## Forward an existing loopback service

Resolve a gateway route when the task needs an independently reachable HTTPS
URL or gateway-provided isolation headers:

```bash
basaltwater-web preview resolve --route gateway --port 8080 --json
```

Resolution is read-only. It probes the loopback HTTP endpoint, finds a forward
owned by the requesting account with the same host, port, and profile, and
verifies that route's HTTPS endpoint with normal TLS validation. On success,
`navigation` contains the exact `{ "url": "https://..." }` argument for T3's
`preview_navigate`; supply the retained tab ID as well. Plain output prints
only the URL on success.

If no matching route exists, `issue: forward_missing` includes an executable
`command` that allocates a managed listener, waits for upstream readiness, and
retains the saved access policy. Run it within the requested preview scope and
resolve again. The suggested name avoids replacing any existing forward.
`--host ::1` selects IPv6 loopback; `--profile godot` requires the matching
profile and verifies its cross-origin isolation headers.

Failures return exit status 1, `ok: false`, and `component`, `issue`, and
`remediation`. Transport issues distinguish `connection_refused`, `timeout`,
`certificate_untrusted`, and other `transport_failure` cases; HTTP errors
include the observed status. Missing or invalid gateway policy returns
`gateway_unavailable`. Invalid inputs return status 2. The resolver never
starts a server, changes routes, or reads browser state. A successful VM-origin
probe does not prove that the connected T3 client can reach or trust the URL,
render the application, or bypass an older service-worker cache.

Keep `forward` as the low-level path when another service manager already owns
the upstream process:

```bash
basaltwater-web forward add api-preview \
  --to 127.0.0.1:3000 \
  --wait 30 \
  --health /health

basaltwater-web forward list
basaltwater-web forward url api-preview
basaltwater-web forward doctor api-preview
basaltwater-web forward remove api-preview
```

The forward list checks upstream TCP readiness. `forward doctor` checks the
named HTTPS listener, including listeners created by `preview start`.
`site doctor` checks the published snapshot when both use the same name. Use
`basaltwater-web forward prune --yes` only for unmanaged dead forwards; managed
preview services should be cleaned up through `preview prune`.

Nginx retains its 1 MiB request-body default unless the upstream service has a
larger documented limit. Set a bounded per-route limit when needed:

```bash
basaltwater-web forward add upload-api \
  --to 127.0.0.1:3000 \
  --max-body-size 50m
```

The limit accepts a positive integer with an optional `k`, `m`, or `g` suffix
and is capped at 1 GiB. T3 Code's managed route sets 50 MiB automatically.
The managed Syncthing route uses a dedicated profile to retain Syncthing's
loopback host validation while preserving the external host in forwarding
headers.
