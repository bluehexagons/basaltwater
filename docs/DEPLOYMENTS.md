# Deployments and manifests

`basaltw` deploys a repository with `--deploy DOMAIN GIT_URL`. A repository
can use automatic project-type detection, or place an `basaltwater.json` file at its
root to describe one or more static sites and services explicitly. The manifest
is validated before deployment; an invalid file stops deployment instead of
falling back to automatic detection.

`--dry-run` performs a shallow clone into disposable local staging and validates
the repository type and `basaltwater.json` without updating the persistent Git cache
before target setup begins. It does not execute repository build commands;
projects should run those commands in CI as a separate build preflight.

## Basic deployment

```bash
basaltw setup server_web web.example.com deploy \
  --ssl --ssl-email admin@example.com \
  --deploy web.example.com https://github.com/example/web.git
```

Without a manifest, the repository is classified as Node, static, or unknown
by the automatic detection rules. A conventional Go module with
`cmd/server/main.go` (or a root `main.go`) is also inferred as a service: it is
built with `go build`, stored as `.basaltwater/bin/app`, and given a stable,
automatically allocated internal port. Basaltwater supplies the conventional
`HOST`, `PORT`, and `LISTEN_ADDR` variables; inferred applications must honor
one of those settings and bind to loopback. Projects with a non-standard Go
entry point or runtime settings can provide an explicit manifest. Use
[`DEPLOYMENT_SAFETY.md`](./DEPLOYMENT_SAFETY.md)
for backup, persistent-state, rollback, and update-policy behavior.

For target-VM builds, basaltwater inspects uploaded sources before running
setup steps. It enables Go for `go.mod`, Node.js for `package.json`, Python for
`pyproject.toml`, `uv.lock`, or `requirements.txt`.
Separate runtime flags are therefore unnecessary for ordinary deployments.
Explicit flags remain useful when a repository generates those files later or
uses a non-standard layout.

Ruby and Rails deployments are not supported. `v2.0.0` detects common
Ruby project markers and stops locally before uploading setup files or changing
the target. Keep a pinned older basaltwater release for a legacy Rails site;
current setup runs leave its existing `rails-*.service` unit and same-domain
generated Nginx site in place when that whole domain is omitted from the new
deployment set.

The planned versioned convention for zero-config root Go services, inactive
nested-module discovery, standard health/state behavior, and compact monorepo
composition is documented in
[`plans/GO_SITE_CONVENTION.md`](./plans/GO_SITE_CONVENTION.md).

## Manifest shape

The top-level object must contain `version: 1` and a non-empty `components`
array. Component names use lowercase letters, numbers, and hyphens and must be
unique within the file.

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

`output` is relative to the repository and is served by Nginx after the build.
It must not be absolute or escape the repository. `build` may be one command,
an array of commands, or omitted when the checked-in output is ready to serve.
Build commands run from the repository root, and `env` values are available to
those commands only. Environment variable names must be shell identifiers: a
letter or underscore followed by letters, digits, or underscores. Do not put
secrets in a committed manifest.

### Godot web application

Declare `type: "godot-web"` explicitly; legacy static detection does not add
the headers required by a threaded Godot export. This is an additive version-1
manifest component with the same fields as `static`:

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
of receiving the generic site's immutable caching policy. A subpath redirects
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
redeployment and consult the rollback limitations in `DEPLOYMENT_SAFETY.md`.
Use the manifest-aware setup/patch deployment path; the legacy webhook executor
does not interpret this new component contract.

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

## Service options

In addition to the common fields (`name`, `type`, `domain`, `path`, `build`, and
`env`), a `service` component supports:

- `port`: an integer from 1024 through 65535, or `"auto"` for a stable
  basaltwater-managed assignment;
- `binary` or `exec`: exactly one is required; `exec` cannot use systemd's
  privilege-control prefixes (`+` or `!`);
- `working_dir`: repository-relative directory or a supported template path;
- `env_file`: absolute server path, or a path using deploy-time templates; the
  file must be readable but not writable by the component's dedicated service
  account, and its parent directory must not be writable by that account;
- `runtime_env`: environment values written directly into the generated
  systemd unit; values may use deploy-time templates and override matching
  entries from `env_file`;
- `health`: optional URL path polled on `127.0.0.1:port` after startup;
- `reverse_proxy`: set false for a worker or internal service that should not
  receive an Nginx route;
- `sqlite_backup`: optional absolute or templated SQLite database path backed
  up with SQLite's online backup API before release replacement; the resolved
  path must remain under that component's service-owned `{{data_dir}}`; and
- `backup_retention`: number of deployment backups to retain, from 1 to 100.

Basaltwater always writes the hardened systemd unit and runs it under the
component's dedicated service account. Repository-supplied unit files are not
accepted because installing one as root would bypass that isolation boundary.

Supported templates include `{{release_dir}}`, `{{base_dir}}`, `{{name}}`,
`{{service_name}}`, `{{domain}}`, `{{path}}`, `{{web_user}}`, `{{web_group}}`,
`{{port}}`, `{{binary}}`, `{{working_dir}}`, `{{env_file}}`, `{{shared_dir}}`,
and `{{data_dir}}`. Unknown templates fail validation. A health endpoint must
return a 2xx response within the 30-second activation deadline; persistent
failure rejects the release and restores the previous release and service
units.

## Runtime and update behavior

- Manifest deployments build every component on each deployment; they do not
  use incremental builds.
- Builds run in a temporary sibling release and are accepted only after every
  component build, service activation, and declared health check succeeds.
- Existing application services are stopped and verified inactive before
  basaltwater opens a declared SQLite database for its privileged backup; a
  backup failure restarts the unchanged release.
- Repository build commands run as an application-specific non-root build
  account that cannot modify another application's active release.
- Node and uv are provisioned in that application's persistent build home;
  Node projects may pin the build version with `.nvmrc`. Build caches survive
  release replacement without being shared between applications.
- Static output directories and service binaries are checked before any active
  service is stopped. A declared binary must exist and be executable.
- Existing release files are replaced only after services are stopped. Static
  files are owned by the deployment user.
- Legacy automatic static and Node deployments also build in a temporary
  sibling directory and replace the active tree atomically. A fetch,
  dependency, or build failure leaves the active release untouched.
- Every requested repository must be staged successfully before remote setup
  begins; one failed fetch aborts the complete setup instead of silently
  dropping that route from the desired deployment set.
- Each service receives a dedicated system user and persistent writable state
  only under `/var/www/.basaltwater_shared/<app>/<component>/data`. The parent
  component directory and its `backups` directory remain root-controlled.
- Service state remains outside the release directory, so replacing a release
  does not remove component data.
- Manifest deployments are serialized while stable ports are assigned and
  activated, preventing concurrent deployments from claiming the same port.
- Release files preserve executable bits instead of making every source file
  executable or writable.
- `basaltw patch HOST --deploy ...` reruns the saved deployment with the
  same manifest-aware path. Use `basaltw deploy PATTERN` to rerun saved
  configurations.

## Validation and troubleshooting

Manifest validation is strict: unknown fields, duplicate names, invalid domains,
unsafe paths, unsupported versions, invalid ports, invalid environment variable
names, duplicate resolved domain/path routes, and malformed service templates
are rejected. Control characters and systemd privilege-control command prefixes
are also rejected before any unit is installed. Check the remote deployment
output and inspect the generated service when a component does not start:

```text
sudo systemctl status app-<deployment>-<component>.service
sudo journalctl -u app-<deployment>-<component>.service -n 100 --no-pager
```

Replace `<deployment>` and `<component>` with the generated service name.
Basaltwater also refuses to replace an existing same-named Nginx site unless it
recognizes that site as one it generated. Rename or explicitly migrate a manual
site before assigning its domain to a managed deployment.

For Nginx and deployment rollback behavior, see
[`DEPLOYMENT_SAFETY.md`](./DEPLOYMENT_SAFETY.md). For webhook-triggered builds,
see [`CICD.md`](./CICD.md).
