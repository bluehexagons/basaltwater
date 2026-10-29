# Deployments and manifests

`basaltw` deploys a repository with `--deploy DOMAIN GIT_URL`. A repository
can use automatic project-type detection, or place a `basaltwater.json` file at its
root to describe one or more static sites and services explicitly. The manifest
is validated before deployment; an invalid file stops deployment instead of
falling back to automatic detection.

See [build systems](build-systems.md) to choose between target builds, selected
builders, webhooks and Actions runners. [Complete example projects](../examples/builds/README.md)
include ready static, Python static and combined SPA/Go applications with
matching enrollment templates.

Use `basaltw manifest init --dry-run` to propose a manifest from Node, Go,
full-stack, or static project structure, then `basaltw manifest init` to create
it. Existing files are preserved. Review the generated commands, output, and
service settings before deployment; see [project tooling](../agents/project-tooling.md)
for inference limits, CI workflows, and project Node selection.

For ordinary target builds, `--dry-run` performs a shallow clone into disposable
local staging and validates the repository type and `basaltwater.json` without
updating the persistent Git cache before target setup begins. It does not execute
repository build commands; projects should run those commands in CI as a separate
build preflight.
Repositories with the same final name (for example, two different `site.git`
URLs) receive distinct upload paths based on their complete URLs. Deployment
source trees must not contain symlinks or special files; the controller checks
this before reading the manifest or uploading the source.
Git URLs must not contain embedded HTTP credentials or query strings. An optional
`#branch=NAME` or `#tag=NAME` selects an explicit branch or tag; all other fragments
are rejected. Quote URLs with selectors:

```bash
basaltw setup server_web web.example.com deploy \
  --deploy app.example.com 'https://github.com/example/app.git#branch=main' \
  --deploy stable.example.com 'https://github.com/example/app.git#branch=release/stable'
```

Each source selector receives its own cache and upload path. Setup fetches the
selected ref on every run and stops if it is missing, including during dry runs.
Without a selector, deployments continue to follow the repository's default branch.
For ordinary target builds, use the controller's Git credential helper or SSH
key for private repositories. Selected-builder deployments use HTTPS and the
controller's configured global Git credential helper, as described below.
Displayed commands also replace sensitive URLs from older saved configurations
with a placeholder that must be filled in before replay.

## Build on a saved builder

Add `--build-on SAVED-BUILDER` to build supported manifest deployments on an
enrolled saved builder and deliver verified artifacts to the target. Omitting
the selection keeps the existing build-on-target flow for ordinary deployments.
Selected Debian static/Go public flows have native coverage, including saved
revision updates and retained refresh. Consult the
[coverage map](../plans/testing-coverage.md) for the tested boundaries; a dry run
does not establish that a particular deployment will succeed.

For an already enrolled builder named `builds`, an existing remote target, and a
controller-local typed target policy matching the manifest's routes:

Provision root setup access for the selected target key separately before this
command. Typed enrollment grants forced-command transport access and installs
no root key; preflight and setup use root with the same selected identity file.

```bash
basaltw setup server_web web.example.com deploy --machine vm \
  --key /home/controller/.ssh/web_ed25519 \
  --ssh-access-source 192.0.2.5/32 \
  --build-on builds --build-target-policy /home/controller/web-target.json \
  --deploy app.example.com 'https://github.com/example/app.git#tag=v1' \
  --dry-run
```

Review the preflight, then repeat without `--dry-run` to apply. Use the same
workspace that holds the saved builder and enrolled host keys. Selection matches
the entire saved host, friendly name or tag case-insensitively and requires one
unique record; host matches have no priority over names or tags. Ambiguous,
missing or unenrolled builders refuse. A chosen builder's failure never falls
back to another builder or to building on the target.

Replace the SSH source with the controller's address as seen by the target.
Web targets require effective trusted SSH sources to permit delivery's
connection bursts. Repeat `--ssh-access-source` for other administration sources;
it restricts SSH while keeping public web ingress. Generic `--access-source`
or `--lan-access` also qualifies but applies to other managed services.
Preflight verifies the current controller is included, and minimal enrollment
reconciles an existing active firewall before delivery. For a cloud target,
use the controller's public egress IP or VPN source instead of its LAN address.

Both endpoints require managed Debian 13 x86_64 enrollment and an explicit VM or
hardware machine type. The target must be remote and use `server_lite` or
`server_web`, with an explicit `--build-target-policy`. The controller needs
existing private SSH identity files, their Ed25519 public companions and its
workspace `known_hosts`. Each selected public key must match an enrollment
principal; typed SSH accounts come from that enrollment. The selected target key
must also authorize root setup on that target. Credentials remain on the
controller. See the contributor [selection contract](../agents/contributing/build-selection.md)
for exact identity, permissions and trust requirements.

Source must be an HTTPS repository with an explicit, committed
`basaltwater.json`. Default HEAD, `#branch=NAME` and `#tag=NAME` are pinned to
complete commit IDs. The controller clones with history and tags in a private
workspace and verifies snapshot and manifest identity; it does not use ambient
Git configuration for checkout, redirects, LFS, submodules or inferred
manifests. Cloning tries anonymous access first, then the controller's configured
noninteractive global Git credential helper. Credentials are scoped to that
repository, used only during clone, and never sent to the builder or target.
There are 1–16 application destinations per setup. Each manifest's
complete component/route map must match exactly one application granted to the
selected target principal, and the builder must grant its project, platform and
runtimes.

Supported manifests contain ready static output (including SPA), shell, Node or
Python static builds, or self-contained native services. Explicit
`build_runtimes`, including `[]`, overrides the ordinary deployment's shared
source marker inference. Otherwise the controller and worker infer tools from
the same pinned checkout. Export serving files through `artifacts`; `output`,
`binary`, or an exec-only service's literal release executable supplies the
default export when `artifacts` is omitted. Ready static output needs no runtime
declaration. For example, a Python static
build can declare:

```json
{
  "version": 1,
  "build_runtimes": ["python"],
  "components": [{
    "name": "site",
    "type": "static",
    "domain": "{{domain}}",
    "build": "python3 scripts/build.py",
    "output": "dist",
    "artifacts": ["dist"]
  }]
}
```

Services require a literal `binary` export or an `exec` command starting with
`{{release_dir}}/relative/executable`. The verified executable must be a
self-contained x86-64 ELF executable. The default binary command receives
`HOST=127.0.0.1` and `PORT`; manifests can instead declare arguments and
`runtime_env`, with matching exact runtime grants in the target policy.
Automatic and explicitly granted fixed ports, canonical health paths, managed
working directories, SQLite backups and public UDP declarations are supported.
Persistent data remains in the ordinary deployment's shared directory when
the same target root and component names are retained.

A ready SPA and Go API can share one application and release bundle:

```json
{
  "version": 1,
  "build_runtimes": ["go"],
  "components": [
    {
      "name": "site", "type": "static", "domain": "{{domain}}",
      "path": "/", "output": "public", "artifacts": ["public"], "spa": true
    },
    {
      "name": "api", "type": "service", "domain": "{{domain}}",
      "path": "/api", "port": "auto", "health": "/health",
      "build": "mkdir -p bin && CGO_ENABLED=0 go build -trimpath -o bin/api .",
      "binary": "bin/api", "artifacts": ["bin/api"]
    }
  ]
}
```

This example assumes a Go module at the repository root and committed frontend
assets in `public`. SPA deep links use the frontend fallback; `/api` requests
retain their prefix and query when proxied, and API errors remain API responses.
The target policy must authorize both components as one complete application.
A separately authorized application can retain a neighboring route such as
`/neighbor` on the same domain. This example does not build an npm frontend.

The selected-builder flow refuses Godot/interpreter services, `env_file`,
unmanaged working or database paths, alternate proxy settings and CI/publishing
workflows. Environment grants are literal administrator-approved configuration;
do not put secrets in manifests or policy drafts. It also refuses target
Go, Node and Godot installation flags, build-host and legacy app-server roles,
network activation and custom setup steps. SSL and Cloudflare settings are
installed through administrative setup and retained separately from artifact
authority. Cloudflare with SSL retains an HTTP origin without an ACME redirect;
direct SSL uses the shared certificate and nginx helpers. Delivery reuses valid
installed certificates; new certificate issuance waits for final profile setup
to configure web ingress. Until that succeeds, routes without a certificate use
HTTP. `--python`
remains a profile option that installs Python tooling and uv; a Python static
build needs Python on the builder, not that target flag. The broader manifest
examples below describe ordinary target builds; they do not expand this bounded
scope.

The controller checks sources, grants, tools, limits and retained-artifact
capacity before minimally enrolling the target. Enrollment preserves the saved
profile. It reserves all applications before submitting builds, stages every
verified artifact before the first activation, then activates sequentially.
The final full profile runs in artifact mode under exclusive target maintenance,
checking exact installed releases and current completed admissions before its
first profile mutation. It omits reenrollment. Several activations and profile
application are separate durable boundaries, so the entire setup is not atomic.

The target retains artifact mode and installed release evidence, without the
builder reference or builder credentials. Local `basaltw refresh` reconciles
retained descriptors and bundles without fetching application source, contacting
a builder or invoking a compiler. Missing, unsafe or unavailable inputs refuse
with controller deployment guidance. Patches preserve omitted builder selection;
`--no-build-on` clears the controller's selection and artifact mode.

To update a saved selected-builder deployment from v1 to v2:

```bash
basaltw patch web.example.com --deploy app.example.com 'https://github.com/example/app.git#tag=v2'
basaltw deploy web.example.com --yes
```

The patch replaces the source for the named destination, preserving omitted
sibling destinations and the saved builder. Subsequent saved deployments use v2.
Comma-separated destinations are updated individually. Domain case, a final DNS
dot, an omitted root slash and trailing path slashes are normalized for matching;
path case remains significant. Identical repeated selections collapse, while
different sources for the same normalized destination within one patch are
rejected before source preparation or transport. These rules also apply when
explicitly selecting or switching builders in the patch. Ordinary target builds,
including a patch with `--no-build-on`, retain their existing full-pair merge
behavior; artifact mode or enrollment alone does not enable replacement.

Dry runs clone and pin sources in disposable local storage and perform read-only
SSH capability/platform probes using existing trust. They never bootstrap,
reserve, submit builds, stage, activate or save setup state. Non-dry attempts
retain private inputs under `<workspace>/build-setups/HOST-HASH`, with at most 32
active attempts per host and no automatic garbage collection. Explicit
[completed-history archival](../agents/contributing/build-setup-history.md)
frees controller attempt slots while retaining all payloads and identities;
it does not free disk space or builder/target slots. On failure or an uncertain
response, preserve the durable guard, pinned sources, prepared inputs, delivery
receipts and exact request/operation IDs. Inspect actual target/enrollment state
before explicit operator reconciliation. There are no automatic retries,
replacement IDs, fallback or cancellation, including when delivery completed but
the final profile failed. See [controller setup](../agents/contributing/build-setup.md),
[source preparation](../agents/contributing/build-setup-sources.md) and
[planning](../agents/contributing/build-setup-plan.md) for detailed contracts.

Builder retention is separate from these controller workspaces and target
releases. See [builder capacity and maintenance](build-systems.md#inspect-capacity-and-retain-evidence)
for local-root capacity inspection, exact-revision settlement, source/log
pruning and successful bundle release. Administrators can run these actions
through normal root or sudo SSH sessions; forced-command transport contexts
are rejected. They never reclaim input/history slots. Downloading a bundle does
not release its retention.

For a bounded offline view of an unfinished controller build setup, use its
original absolute journal path:

```bash
python3 -B -m lib.build_setup_recovery --journal /absolute/workspace/build-setups/HOST-HASH/setup.json
```

Keep `-B` (or set `PYTHONDONTWRITEBYTECODE=1` at startup): ordinary Python package
imports can create bytecode caches before this module starts. The inspector
requires existing protected evidence and idle locks, reads only fixed associated
metadata, and emits at most 64 KiB for at most 16 applications. It reports original
replay identities, known admission IDs and locally saved acknowledgements or
unresolved intents, with unknown IDs left `null`. It always reports remote state
as `unobserved` and payload contents as `unverified`; a saved completion does not
clear an unfinished guard or prove current target state. Unsafe, malformed,
conflicting or missing required evidence refuses without repair. This command
does not contact hosts, inspect payloads, resume, reset, retry or delete anything.
See the [offline inspection contract](../agents/contributing/build-setup.md#offline-unfinished-setup-inspection)
for permissions, read limits, output and refusal semantics.

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
[supported manifest scope](#build-on-a-saved-builder).

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
It must not be absolute, escape the repository, or contain characters that can
change an Nginx directive. Component URL paths have the same Nginx-safe syntax.
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
The webhook executor also supports a single declared Godot-web output;
multi-component activation still uses the manifest-aware setup/patch path.

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

The following describes ordinary build-on-target deployments. For selected
builders and retained artifact refresh, use [the flow above](#build-on-a-saved-builder).

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
  Node projects may pin the build version with `.nvmrc` at the root and in
  conventional `frontend/`, `client/`, or `web/` packages. Build caches survive
  release replacement without being shared between applications.
- Build homes under `/var/lib/basaltwater/build-users` remain private to their
  respective accounts. Every deployment repairs home ownership and permissions,
  restores a traverse-only ACL on the root-private state parent after runtime
  staging, and verifies access as the build user before installing tools.
  Existing build accounts receive the same repair on reruns; sibling state and
  credential file modes are preserved.
- Static output directories and service binaries are checked before any active
  service is stopped. A declared binary must exist and be executable.
- Existing release files are replaced only after services are stopped. Static
  files are owned by the deployment user.
- Automatic static deployments stage in a temporary sibling directory and
  replace the active tree atomically. A staging failure leaves the active
  release untouched; failure to clean up the previous release retains it for
  later cleanup without reporting a failed deployment.
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
[`deployment-safety.md`](./deployment-safety.md). For webhook-triggered builds,
see [`cicd.md`](./cicd.md).
