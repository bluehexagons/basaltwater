# Deploy and write application manifests

[Documentation index](deployments.md)

## Basic deployment

```bash
basaltw setup server_web web.example.com deploy \
  --ssl --ssl-email admin@example.com \
  --deploy web.example.com https://github.com/example/web.git
```

Without a manifest, ready-to-serve static files are deployed directly.
Automatic Node builds have been discontinued: repositories with `package.json`
must declare build commands and output in `basaltwater.json`, using the static
component example below. Rejection happens during controller preflight before
target setup. A conventional Go module with
`cmd/server/main.go` (or a root `main.go`) is also inferred as a service: it is
built with `go build`, stored as `.basaltwater/bin/app`, and given a stable,
automatically allocated internal port. Basaltwater supplies the conventional
`HOST`, `PORT`, and `LISTEN_ADDR` variables; inferred applications must honor
one of those settings and bind to loopback. Projects with a non-standard Go
entry point or runtime settings can provide an explicit manifest. Use
[`deployment-safety.md`](./deployment-safety.md)
for backup, persistent-state, rollback, and update-policy behavior.

For target-VM builds, basaltwater inspects uploaded sources before running
setup steps. It enables Go for `go.mod`, Node.js for `package.json`, Python for
`pyproject.toml`, `uv.lock`, or `requirements.txt`.
Separate runtime flags are therefore unnecessary for ordinary deployments.
Explicit flags remain useful when a repository generates those files later or
uses a non-standard layout.

Set the optional top-level `build_runtimes` array in `basaltwater.json` to
replace source-marker detection for deployment. Supported entries are `"go"`,
`"node"`, and `"python"`, without duplicates. For example, `"build_runtimes":
["go"]` skips Node and Python setup even when the repository has development-only
`package.json` or Python tooling. An empty array disables automatic runtime setup;
omitting the field retains detection, including monorepo children. The list
applies to every component, both target setup and the isolated build account,
including Node activation from `.nvmrc`. Explicit setup flags such as `--node`
still install the requested host tools, and requirements from other deployed
repositories are combined. Go version selection still reads the selected
repository's `go.mod` files. Other tools must be provisioned separately.
Update the controller's Basaltwater installation before using this field;
older versions reject unknown manifest fields during preflight.

Ruby/Rails deployment support has been discontinued. `v2.0.0` detects common
project markers and refuses deployment during controller preflight, before
setup files are uploaded or the target is changed. Basaltwater no longer builds,
deploys, or provides Ruby-specific application updates. Current Basaltwater
machines have no Rails services. Ruby applications are outside
Basaltwater's deployment workflows.

## Manifest shape

Service components may declare explicitly public UDP listeners:

```json
"public_udp": [{"port": 3478, "hostname": "stun.example.com"}]
```

Setup adds the host UFW allowances after successful repository deployments,
including when Cloudflare Tunnel is enabled. The application must bind these
ports itself; the manifest does not change its arguments. Ports must be literal
integers from 1024 through 65535, with at most 32 listeners per component. Only
one deployed service may own each public UDP port. `hostname` is optional and
describes a DNS-only A/AAAA record for the VM's public IP; it does not create an
HTTP route, tunnel ingress, or DNS record. Allow the same UDP port in a provider
firewall when one is enabled. Existing unrelated UFW rules are preserved.

The top-level object must contain `version: 1` and a `components` array.
Deployment requires at least one component; a CI-only package may use an empty
array when it declares `ci` workflows as described in
[project tooling](../agents/project-tooling.md). Component names use lowercase letters,
numbers, and hyphens and must be unique within the file.

Components may also declare an optional `artifacts` array for prebuilt package
selection. The manifest parser accepts it on `static`, `godot-web`, and `service`
components. It must contain 1–128 unique, non-empty canonical repository-relative
slash paths, each at most 4096 characters. Paths cannot be absolute, contain
backslashes, colons, control characters, empty segments, `.` segments, or `..`
segments. A static or Godot component may use `"."` by itself to select the
repository root. For example, `"artifacts": ["dist/index.html",
"dist/assets/app.js"]` selects those paths. Git/control metadata paths, such as
`.git/config` and `.git-credentials`, are rejected.

`lib.project_manifest.artifact_paths()` uses `artifacts` when present; otherwise
it selects `output` for static and Godot components or `binary` for a binary
service. An exec-only service starting with an unquoted literal
`{{release_dir}}/relative/executable` can infer that executable; other exec
commands require explicit exports for callers packaging them. Safe redundant
segments in legacy `output` and `binary`
defaults are normalized for export selection; explicit `artifacts` entries
must already be canonical.
Ordinary deployments build on the target using `output`, `binary` or `exec`.
Selected-builder deployments use these exports within the narrower
[supported manifest scope](deployments-selected-builder.md#build-on-a-saved-builder).

### Static site

```json
{
  "version": 1,
  "components": [
    {
      "name": "site",
      "type": "static",
      "domain": "{{domain}}",
      "path": "/",
      "build": ["npm ci", "npm run build"],
      "env": {"PUBLIC_MODE": "production"},
      "output": "dist"
    }
  ]
}
```

`output` is a repository-relative path served by Nginx after the build.
The manifest parser rejects absolute paths, paths that escape the repository
and unsafe Nginx syntax.
After all on-target component builds finish, setup copies the release into
private staging. Each ordinary static `output` and its ancestors must be real
directories. The public tree may contain directories and regular files. Setup
rejects symlinks, files with multiple hard links, special files, and missing
outputs before stopping services or replacing the active release. Selecting
`"."` applies these checks to the whole repository. Keep only intended public
content in the selected directory.

Nginx serves the snapshot at the configured output path and refuses symlink
traversal for both root sites and subpaths. The active release is a directory
swapped by rename. A `current` symlink in the serving path is also refused.
Redeploy an existing site to install the snapshot and regenerate its locations.

Component URL paths use the same Nginx-safe syntax as output paths.
Set the optional `spa` field to the boolean `true` when client-side routes need
to load directly. It defaults to `false`. When enabled, root sites fall back to
`/index.html`, and a static component mounted at `/dashboard` falls back to
`/dashboard/index.html`. This option is accepted only on `type: "static"`;
ordinary static sites keep returning 404 for missing paths, and Godot exports
always keep their missing-file 404 behavior. Longer sibling routes, such as a
service mounted at `/v1`, continue to use their own proxy location.
`build` may be one command,
an array of commands, or omitted when the checked-in output is ready to serve.
Build commands run from the repository root, and `env` values are available to
those commands only. Environment variable names must be shell identifiers: a
letter or underscore followed by letters, digits, or underscores. Do not put
secrets in a committed manifest.

Static subpaths redirect to a trailing slash, retain query parameters, and use
directory-bounded locations so `/guide/` cannot also match `/guide-other`.
With `--cloudflare`, origin redirects remain relative to preserve public HTTPS.
Generated asset cache headers require revalidation: fixed JavaScript and image
filenames must update with each release. Previously cached immutable responses
may still need a browser reload and Cloudflare cache purge at cutover.

### Godot web application

Declare `type: "godot-web"` explicitly; legacy static detection does not add
the headers required by a threaded Godot export. This is an additive version-1
manifest component with the static fields except `spa`:

```json
{
  "version": 1,
  "components": [{
    "name": "player",
    "type": "godot-web",
    "domain": "{{domain}}",
    "path": "/music/",
    "build": "python3 scripts/export_web.py",
    "output": "exports/web"
  }]
}
```

The build command belongs to the application; this example assumes it provides
that script. Pin and install the matching Godot engine and export templates in
the non-root build account's persistent home, or provision them beforehand.
Basaltwater does not guess an engine version or install Android/native SDKs.
Godot's headless web exporter needs matching templates and enough disk for its
downloads. Builds run only on requested deployments unless you separately enable
webhooks. `--dry-run` validates the manifest but does not run Godot.

Export to `index.html` with adjacent `index.js`, `index.wasm` and `index.pck`.
Staged output is checked before replacing the active release: nonempty required
files, WASM/PCK signatures, at most 4096 entries and 2 GiB, no symlinks or special
files, and no escape from staging. These are structural checks, not an audit of
application code or every referenced asset. Keep only public export files in
`output`. For an already-built export repository, omit `build` and set `output`
to its export directory (or `"."` for the repository root).

Use HTTPS (`--ssl` on a publicly resolvable domain). Nginx emits
`Cross-Origin-Opener-Policy: same-origin`,
`Cross-Origin-Embedder-Policy: require-corp`, JavaScript/WASM MIME types and
`Cache-Control: no-cache` on Godot responses. Fixed asset names revalidate instead
of relying on the generic site's asset revalidation policy. A subpath redirects
to its trailing-slash form; missing assets return 404, without a SPA fallback.
Root and subpath hosting are supported; path/output names must use safe ASCII
path characters. Existing static/service components retain their own policies.

After deployment, check headers on HTML, JS, WASM and worker files, then verify
`crossOriginIsolated === true` in a real browser. Test audio unlock, import,
complete-download offline reload and updates with an open session. Service
workers belong to each app and can override HTTP cache behavior. Cross-origin
iframes and external assets need compatible isolation policies. GitHub Pages
cannot configure these headers directly; use it for a project/instruction site.

The existing staged manifest activation and setup-level Nginx validation apply.
There is no new artifact upload command or coordinated zero-downtime switch of
files and Nginx configuration. Retain the previous approved artifact/commit for
redeployment and consult the rollback limitations in `deployment-safety.md`.
Webhook intake produces build/test artifacts through the managed worker;
it does not activate target deployments. Publishing execution is unimplemented.

### Static site plus API service

```json
{
  "version": 1,
  "components": [
    {
      "name": "site",
      "type": "static",
      "domain": "{{domain}}",
      "output": "frontend/dist",
      "build": "npm ci && npm run build"
    },
    {
      "name": "api",
      "type": "service",
      "domain": "api.{{domain}}",
      "path": "/",
      "build": "server/build.sh",
      "binary": "server/app",
      "port": "auto",
      "runtime_env": {
        "APP_DATABASE": "{{data_dir}}/app.sqlite3"
      },
      "env_file": "{{shared_dir}}/.env",
      "health": "/health",
      "sqlite_backup": "{{data_dir}}/app.sqlite3",
      "backup_retention": 14
    }
  ]
}
```

Each component owns its domain and URL path. A `{{domain}}` placeholder uses
the domain from `--deploy`; a literal domain does not require one. Service
components are reverse-proxied by Nginx to their loopback `port`. The service
must provide exactly one of `binary` (a repository-relative built artifact) or
`exec` (a command string).
