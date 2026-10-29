# Controller remote-build setup

`lib.build_setup.run_build_setup(config, *, apply_setup)` composes a saved
builder, pinned sources, minimal target enrollment, delivery and final artifact
profile setup. The public `run_remote_setup` selected-builder branch calls it
under the controller's existing setup-target lock. Without a selected builder,
ordinary setup preserves its existing flow. See the operator
[selected-builder guide](../../services/deployments.md#build-on-a-saved-builder).

Both controller CLI entrypoints validate selected-build configuration before
provisioning or starting a guest, resolving credentials, or saving setup state.
Patch and saved deploy use the same check. Unsupported profile options and
missing target enrollment therefore leave the saved setup and guests untouched.
Source preparation and live builder/target checks still run at execution; this
early configuration check does not establish source or host readiness.

The first slice requires explicit `--build-target-policy`, an enrolled saved
builder and controller-local SSH identity/trust files. Selection resolves an
exact, case-insensitive unique saved host, friendly name or tag, then binds the
selected public companion to its enrolled endpoint principal. The selected
target key must authorize both root setup and typed target enrollment; builder
keys stay on the controller. Read-only
platform checks require Debian 13 x86-64 and refuse unfinished setup or legacy
enrollment before bootstrap. Selected-builder failures never fall back to a
target build. Custom steps, build-host roles, legacy app-server roles, explicit
build toolchains and network activation are outside
this first slice. App-server steps weaken the trusted deployment parent permissions;
network activation cannot yet carry the verified address back to saved selection.
Targets must be remote, explicitly `vm` or `hardware`, with profiles restricted
to `server_lite` and `server_web`. See [saved selection](build-selection.md) and
[selection configuration](build-selection-config.md) for identity and replay rules.

Cloudflare and SSL use the ordinary profile steps. Minimal administrative
bootstrap separately saves protected serving settings under the target releases
root in `routing.json`, with an initialization witness. Activation and retained
refresh reuse these settings, keeping Cloudflare origins HTTP without redirects;
direct SSL uses the shared scoped certificate setup. Build requests cannot
change this policy. Missing initialized policy or a local profile mismatch
refuses with controller guidance before replay changes applications.

Root preflight, enrollment bootstrap and final profile uploads use the resolved
target endpoint's explicit identity, port and workspace host-key pins. The shared
SSH builder runs inside `strict_ssh_transport`, scoped with a `ContextVar`, for
each setup primitive call. It ignores ambient SSH configuration, agents,
certificates, forwarding and reusable ControlMaster sockets, enforces pinned
host keys and public-key batch authentication, and rejects another host within
that scope. No shared control socket is created or reused. Exceptions restore
the prior context; ordinary setup keeps its existing SSH behavior. Typed builder
and target clients retain their independent strict transport.

[Source preparation](build-setup-sources.md) accepts HTTPS committed
source with an explicit tracked `basaltwater.json`, pins complete revisions and
verifies manifest/snapshot identity. [Planning](build-setup-plan.md) supports ready
static outputs, Go/Node/Python builds and self-contained native services with
declared or safely inferred exports. Runtime selection honors explicit
`build_runtimes`, or shared source-marker inference recorded on the pinned
checkout. Approved service arguments, environment, managed working directories,
SQLite backups, UDP and fixed ports reuse ordinary manifest deployment.
Interpreter services, Godot, environment files, unmanaged runtime paths,
alternate proxy settings and CI/publishing workflows remain unsupported.
The target grant must match the complete component route and runtime map; its
project selects the builder grant.

All manifests, source identities, application grants, tools, limits and aggregate
retained artifact storage are checked before target bootstrap. Dry runs prepare
sources in a disposable local directory and perform read-only SSH probes; they
never enroll, reserve, build, stage, activate or save setup state.

Non-dry setup stores private attempts under the controller workspace's
`build-setups/HOST-HASH`. There are at most 32 retained attempts per host, with
no automatic garbage collection. `prepared.json` retains exact build requests,
serving projections, pinned snapshot paths and idempotency keys. The durable
`setup.json` guard precedes bootstrap and records phases through final profile
application. Delivery uses its own separate empty directory and receipt.
New directory entries are synced through their parents before bootstrap can
start, so a crash cannot discard the hierarchy containing acknowledged intent.

Minimal enrollment preserves the previous successful saved profile. All
reservations precede submission, all verified artifacts stage before the
first activation, and activations remain sequential. Final setup receives
transient expected release identities, excludes reenrollment, and must validate
those identities and their current completed admissions under target maintenance
ownership before beginning the setup operation or applying any profile step.
The maintenance lease spans the final profile and local response. Expectations
remain transient and are rechecked during retained artifact reconciliation; see
[activation and guarded replay](build-target-activation.md#artifact-mode-setup-replay).
The target saves artifact mode; it receives no saved-builder reference or builder
credentials. Local refresh reconciles installed descriptors and retained bundles
without a builder, source fetch or compiler. Missing or unsafe inputs refuse
with controller guidance; they never trigger a target build.

Failures or interrupts after the bootstrap intent retain the guard and evidence,
even if the remote operation may have completed. There is no automatic retry,
replacement ID, fallback or cancellation. Inspect the recorded builder/target
IDs, target setup and enrollment markers, delivery receipts and actual installed
state before explicit operator reconciliation. A completed delivery followed by
a failed profile still requires reconciliation. Controller capacity checks do
not reserve blocks against concurrent consumers, quotas or inode exhaustion.

## Offline unfinished-setup inspection

`lib.build_setup_recovery.inspect_build_setup_recovery(journal_path) -> dict`
projects only local metadata for an existing unfinished controller setup. Use a
canonical absolute `setup.json` path in its original protected host directory:

```bash
python3 -B -m lib.build_setup_recovery --journal /absolute/workspace/build-setups/HOST-HASH/setup.json
```

The `-B` startup flag (or startup `PYTHONDONTWRITEBYTECODE=1`) is required for a
read-only CLI invocation. Python imports `lib/__init__.py` before the module can
run; ordinary module imports and plain `python3 -m` retain normal bytecode-cache
behavior. The CLI refuses with `bytecode_enabled_use_python_B` when bytecode is
enabled, but that refusal cannot undo earlier package import cache writes. The
API promises unchanged evidence, not a globally write-free Python interpreter.

The inspector opens only the unfinished setup marker, the associated attempt's
`prepared.json`, and, when applicable, `delivery/delivery.json`, its `.last.json`
and `receipt-FIRST-IDEMPOTENCY-KEY.json`. It does not read the setup's rotating
last result, sibling attempts, source trees, snapshots, artifact bundles, logs,
credentials or remote files. Prepared requests are parsed to validate their
canonical digest and exact request/source/deployment/replay identities; command
and environment values are never projected. Payload contents remain unverified.
It never initializes an accessor or creates, repairs, syncs or removes a file,
directory, lock, journal or witness. It supplies no resume/reset, replay, retry,
cancellation, retention or deletion operation.

Directory traversal uses no-follow descriptor-relative opens from `/`, trusted
root/effective-UID ancestors (including trusted sticky parents), and owned
`0700` host/attempt/delivery directories. Evidence and stable lock files must be
effective-UID-owned, single-link regular `0600` files. Stable operation locks
must already exist and be empty. They are opened **read only** and locked
exclusively without waiting, setup first and then delivery, until validation and
projection finish. No `OperationStateStore` instance or ownership method is used.
Descriptor and directory-entry identities, modes and owners are rechecked before
returning, including absent optional entries. Files also pin links, sizes and
mtime/ctime; unrelated children may change ancestor directory timestamps.
Read access may update atime. This does not defend against a malicious process
with the same UID or establish an atomic snapshot against uncooperative writers.

Reads are bounded to 32 MiB for prepared metadata, 1 MiB per operation document,
and 64 KiB for the deterministic receipt; each read chunk is at most 64 KiB.
Only schema-1 producer records, at most 16 distinct application/request/key
identities, and supported fixed fields/phases are accepted. Duplicate JSON keys,
invalid encodings, oversized records, unsafe paths/files, changed identities,
conflicting acknowledgements/receipts and missing required evidence refuse.
During bootstrap (including its handled failure), absent delivery is legitimate
and reported as unknown. Once setup has recorded `delivering`, missing delivery
intent/evidence refuses, including an interruption during delivery initialization.
This deliberately conservative inspector does not reconstruct missing evidence.

The schema-versioned JSON projection is at most 64 KiB including its newline.
It contains `setup`, `delivery` and ordered `applications`: local phases/status,
internal controller operation IDs, prepared/profile digests, application/project,
exact request/source/deployment digests, original request IDs/idempotency keys,
known admission operation/generation/bindings, artifact identities, locally saved
stage/activation acknowledgements and unresolved intents. Unknown IDs are JSON
`null`, including a lost reserve reply; there are no generated replacement IDs.
No arbitrary paths, raw contexts, commands, environment values, credentials or
executable recovery instructions enter the report. `remote_state` is always
`unobserved` and `payload_contents` is always `unverified`. A completed local
acknowledgement or successful last result does not establish current remote state
or clear an unfinished guard. Markers retain precedence over last results.

The API raises `BuildSetupInspectionError` with a fixed `code`: `busy`,
`unavailable`, or `invalid_evidence`. The CLI exits 0 with the report on stdout,
or 2 with a bounded schema-versioned JSON error on stderr; argument errors use
`invalid_arguments`. Errors expose no evidence contents or supplied paths.
Focused temporary-fixture tests include the real controller SIGKILL window,
lost acknowledgements, final-profile failure, unchanged evidence metadata,
forbidden effect calls and a cold package-copy CLI/no-bytecode subprocess proof.
They do not observe a live target or establish remote recovery coverage.

Selected native qualification covers public HTTPS source preparation, a saved
builder and fresh Debian target, static and Go artifact delivery, guarded final
profile setup, verified HTTPS routes, retained target-only reconciliation and
idle reboot without target compilers. Controlled enrollment also waits through
an APT lock. This does not qualify all crash/restart boundaries, packaging
variants or profiles; see the [coverage map](../../plans/testing-coverage.md).
Mocked checks and read-only dry runs remain separate evidence.
