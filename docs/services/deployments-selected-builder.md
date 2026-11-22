# Deploy through a saved builder

[Documentation index](deployments.md)

## Build on a saved builder

Add `--build-on SAVED-BUILDER` to build supported manifest deployments on an
enrolled saved builder and deliver verified artifacts to the target. Omitting
the selection keeps the existing build-on-target flow for ordinary deployments.
Selected Debian Node/static/Go flows have native coverage, including saved
revision updates, SQLite backups, UDP and retained refresh. Consult the
[coverage map](../plans/testing-coverage.md) for the tested boundaries; a dry run
does not establish that a particular deployment will succeed.
The [real project qualification](../testing/build-projects.md) additionally
covers a React/Vite manifest with separate deployment and CI commands.

Enroll the builder with `--build-server`, which automatically installs Go, Node
and Python and grants wildcard projects. Optionally restrict enrollment with
`--build-project DOMAIN[/PATH] HTTPS_URL` for each destination.
Initial `--build-on` setup generates matching target grants
from its deployment manifests, using the selected SSH public companion. No
enrollment files are required; see the [two-command provisioning example](../examples/builds/homelab-build-test.md).
Custom `--build-host-policy FILE` and `--build-target-policy FILE` remain available.

For an already enrolled builder named `builds` and an existing remote target:

Provision root setup access for the selected target key separately before this
command. Typed enrollment grants forced-command transport access and installs
no root key; preflight and setup use root with the same selected identity file.

```bash
basaltw setup server_web web.example.com deploy --machine vm \
  --key /home/controller/.ssh/web_ed25519 \
  --ssh-access-source 192.0.2.5/32 \
  --build-on builds \
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
`server_web`. Initial target enrollment is generated unless an explicit policy
or saved enrollment is present. Later deployments reuse that saved snapshot;
`--build-target-auto` explicitly updates it from the complete merged deployment
set. Builder and target project IDs derive from the same destination, so use
matching domains and paths in `--build-project` and `--deploy`. The controller needs
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
Services may request a target-owned `runtime_binding` ID, with a matching target
runtime grant and a separately provisioned private file. See
[runtime secret bindings](runtime-bindings.md) for setup, rotation and recovery.

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
unmanaged working or database paths, alternate proxy settings and publishing
workflows. A manifest may also declare `ci`, but deployment runs only its
component build commands; CI install/build/test steps require a separate workflow
build. CI-only manifests cannot be deployed.
Environment grants are literal administrator-approved configuration;
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
```

The patch deploys and saves the change; no subsequent `deploy` is needed to
finish it. Use `basaltw deploy web.example.com --yes` later to replay the saved
selection. The patch replaces the source for the named destination, preserving
omitted sibling destinations and the saved builder. Subsequent saved deployments
use v2.
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
Use the [operations guide](build-operations.md) to add sites, update saved policy
snapshots or distinguish logical quota, physical storage and record exhaustion.

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
