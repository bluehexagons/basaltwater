# Controller remote-build setup

`lib.build_setup.run_build_setup(config, *, apply_setup)` composes a saved
builder, pinned sources, minimal target enrollment, delivery and final artifact
profile setup. The public `run_remote_setup` selected-builder branch calls it
under the controller's existing setup-target lock. Without a selected builder,
ordinary setup preserves its existing flow. See the operator
[selected-builder guide](../../services/deployments.md#build-on-a-saved-builder).

The first slice requires explicit `--build-target-policy`, an enrolled saved
builder and controller-local SSH identity/trust files. Selection resolves an
exact, case-insensitive unique saved host, friendly name or tag, then binds the
selected public companion to its enrolled endpoint principal. The selected
target key must authorize both root setup and typed target enrollment; builder
keys stay on the controller. Read-only
platform checks require Debian 13 x86-64 and refuse unfinished setup or legacy
enrollment before bootstrap. Selected-builder failures never fall back to a
target build. Custom steps, build-host roles, legacy app-server roles, explicit
build toolchains, network activation and Cloudflare/SSL profile setup are outside
this first slice. App-server steps weaken the trusted deployment parent permissions;
network activation cannot yet carry the verified address back to saved selection.
Targets must be remote, explicitly `vm` or `hardware`, with profiles restricted
to `server_lite` and `server_web`. See [saved selection](build-selection.md) and
[selection configuration](build-selection-config.md) for identity and replay rules.

Root preflight, enrollment bootstrap and final profile uploads use the resolved
target endpoint's explicit identity, port and workspace host-key pins. The shared
SSH builder runs inside `strict_ssh_transport`, scoped with a `ContextVar`, for
each setup primitive call. It ignores ambient SSH configuration, agents,
certificates, forwarding and reusable ControlMaster sockets, enforces pinned
host keys and public-key batch authentication, and rejects another host within
that scope. No shared control socket is created or reused. Exceptions restore
the prior context; ordinary setup keeps its existing SSH behavior. Typed builder
and target clients retain their independent strict transport.

[Source preparation](build-setup-sources.md) accepts public HTTPS committed
source with an explicit tracked `basaltwater.json`, pins complete revisions and
verifies manifest/snapshot identity. [Planning](build-setup-plan.md) supports ready
static outputs, Python static builds and Go binary services with declared exports
and explicit `build_runtimes` for commands. It refuses Node, Godot, custom exec,
interpreter services, environment files/runtime environment, custom paths,
SQLite settings, UDP, fixed ports, alternate proxy settings and CI/publishing
workflows. The target grant must match the complete component route map; its
project selects the builder grant. Do not broaden this contract through inference.

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

Mocked controller checks and read-only dry runs do not qualify the live public
setup flow. Native public-source and end-to-end deployment qualification remain
separate requirements.
