# Pure controller delivery planning

`lib.build_setup_plan.prepare_delivery_plan()` converts one caller-pinned
`Manifest` and source identity into a `lib.build_delivery.DeliveryPlan`. It is
called by the [public `--build-on` controller](build-setup.md).
The controller owns source preparation, saved selection,
authenticated clients, durable delivery and recovery.

The keyword arguments are `destination`, `source`, `source_path`, `request_id`,
`idempotency_key`, `target_enrollment`, `target_principal`, and
`builder_capabilities`. IDs are caller-selected 32-character lowercase hex
values. `source` is the protocol's pinned revision/snapshot/manifest hash record;
`source_path` is a canonical absolute text path. Planning performs no file,
network, process or system operations, creates no IDs, and never checks snapshot
existence. The [delivery controller](build-delivery.md) later verifies the file.

Supply the administrator-owned, complete typed target enrollment and the
principal already resolved from the selected target transport identity. Supply
the authenticated builder's capabilities result, including observed
`tool_versions`. These facts are validated structurally here; planning cannot
prove their provenance or freshness. Delivery repeats live preflight and both
services enforce current grants independently.

## Routes and application authority

`destination` follows the existing `DOMAIN[/PATH]` deploy-spec parser, restricted
to a literal domain and safe normalized URL path. Filesystem destinations and
comma-separated destination lists are refused; callers plan each destination.
As in local manifest deployment, only `{{domain}}` in component domains is
rendered, using the destination domain. Component paths are literal and are used
directly: `example.com/releases` with component routes `/` and `/v1` still serves
`/` and `/v1`. The destination subpath does not prefix manifest routes. Domain
case/root-dot and trailing route slashes normalize through target-policy rules.
Other domain variables and path templates are refused.

Exactly one application for the selected principal must match the **complete**
component-name/type/domain/path map. Partial matches are refused. Strict target
policy parsing already rejects duplicate route ownership across applications,
including ambiguous otherwise matching applications. The request's project is
taken exclusively from that application's grant. No repository name, snapshot
filename or destination path supplies a project or target deployment root.

The generated small projection is checked with
`authorize_target_deployment()`. Only its serving fields enter the plan; the
private authorization result's deployment root is discarded. Commands and build
environment remain exclusively in the request. Exact export order and coverage
are enforced by the existing request and target parsers. Legacy safe `./dist`
and `./bin/api` defaults normalize using the existing artifact/runtime helpers.

## First supported slice

Static outputs, including SPA fallback, and self-contained Go services are
supported. Services require a `go` build runtime and reverse proxy, and may
declare a typed health path. A safe release-root exec command can infer its
executable export without changing ordinary manifests. Runtime settings are
projected through `lib.manifest_serving.runtime_from_component()` and require
an exact administrator-approved `runtime` mapping on the service route.
Health paths must survive target parsing unchanged; noncanonical endpoints such
as `/ready/` are refused rather than probing a different URL.
Target runtime validation still must prove a self-contained x86-64 ELF binary;
a Go declaration does not prove what arbitrary build commands produced.

Interpreter services, Godot, environment files, unmanaged working/data paths,
and alternate proxy settings are refused. Approved exec arguments, runtime
environment, managed working directories, SQLite backup/retention, public UDP
and fixed ports use the [target runtime policy](build-target-policy.md).
Unmodeled dataclass fields are refused too. CI/publishing workflows are outside
this bounded slice. SPA is the only static option in the current manifest and
target schemas. Existing target renderer cache/compression behavior remains its
own policy; no new gzip/expires override is invented or silently discarded.

Ready static outputs need no tool declaration. Command-bearing manifests require
explicit `build_runtimes`; Python static build commands and Go builds are
supported when their runtimes are granted and their installed versions are
present. An empty runtime list is insufficient when build commands are present.
Node-declared builds are refused even with a Node version: the current
capability contract does not qualify package-manager readiness.

Every request limit is the minimum of its protocol default and the builder's
project ceiling, additionally constrained by target artifact-byte/entry limits.
A generous administrator grant never raises the conservative default request.
Malformed ceilings fail through existing protocol validation. This supplies
bounded admission inputs, not evidence that a particular build will fit.
Reservation and staging wire-header bounds are checked without file inspection.

The deployment test-suite wildcard already includes `tests/test_build_setup_plan.py`.
Focused tests cover route parity with local descriptors, authority and mismatch
failures, conservative limits, supported and refused manifests, export coverage,
input detachment and a mocked I/O boundary. They perform no builds, SSH or native
runtime qualification.
