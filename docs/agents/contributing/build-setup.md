# Controller remote-build setup

`lib.build_setup.run_build_setup(config, *, apply_setup)` composes a saved
builder, pinned sources, minimal target enrollment, delivery and final artifact
profile setup. Its caller must hold the controller's existing setup-target lock.
It is an internal integration boundary until the public setup hook is wired.

The first slice requires explicit typed target enrollment, an enrolled saved
builder and controller-local SSH identity/trust files. The selected target key
must also authorize root setup; builder keys stay on the controller. Read-only
platform checks require Debian 13 x86-64 and refuse unfinished setup or legacy
enrollment before bootstrap. Selected-builder failures never fall back to a
target build. Custom steps, build-host roles, explicit build toolchains and
Cloudflare/SSL profile setup are outside this first slice.
Target profiles are restricted to `server_lite` and `server_web`.

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

All reservations precede submission, all verified artifacts stage before the
first activation, and activations remain sequential. Final setup receives
transient expected release identities, excludes reenrollment, and must validate
those identities under target maintenance ownership before applying its profile.
The target saves artifact mode; it receives no saved-builder reference or builder
credentials. Ordinary setup without a selected builder retains its existing flow.

Failures or interrupts after the bootstrap intent retain the guard and evidence,
even if the remote operation may have completed. There is no automatic retry,
replacement ID, fallback or cancellation. Inspect the recorded builder/target
IDs, target setup and enrollment markers, delivery receipts and actual installed
state before explicit operator reconciliation. A completed delivery followed by
a failed profile still requires reconciliation. Controller capacity checks do
not reserve blocks against concurrent consumers, quotas or inode exhaustion.
