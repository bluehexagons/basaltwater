# Debian build execution adapter

`lib.build_execution` is the internal systemd adapter used by the
[on-demand build worker](build-worker.md), separate from request admission,
durable job state, source preparation and artifact publication. Managed
[enrollment](build-host-setup.md) and the [`--build-on` controller](build-setup.md)
compose it into remote builds; ordinary deployments without builder selection
continue to build on the destination.

The trusted worker provisions a fresh workspace, private home, validated request
file and empty broker-owned log beneath safe managed parents. It validates the
dedicated build account and installed toolchain/platform policy. The adapter
passes only fixed helper arguments to systemd; repository commands and their
non-secret environment inputs remain in the private request file. The helper
checks the request digest before execution. No controller or deployment
credentials belong in the build account or its input.
The caller places the verified history bundle, workspace, private home and
`home/tmp` on the bounded scratch filesystem. The fixed unprivileged helper
imports local history and checks the restored source before project commands.
`/var/tmp` is read-only inside the unit. `/tmp` is bind-mounted from the same
scratch `home/tmp` used by `TMPDIR`, including for the shared Node selector's
fixed-path probes. Temporary files remain within the scratch capacity limit.
The managed `HOME` and `TMPDIR` values take precedence over component environment
entries; project-specific build inputs cannot redirect these managed paths.
The service bind-mounts only the scratch filesystem at the fixed private unit
path `/run/basaltwater-build-work`. Its layout is `source/checkout`,
`source/history.bundle`, `home/tmp`, and `request.json`. This gives the build
identity access without granting traversal through broker-owned state parents.
The filesystem root and `source` parent remain root-owned and traversable;
only the checkout and home are handed to the build identity. The request and
history stay root-owned and readable, outside those writable directories.

The transient system service applies memory, swap, CPU, process-count and work
time limits. A fixed `setpriv` command drops UID/GID, supplementary groups and
all capabilities before launching isolated Python and literal shell commands.
The fixed launcher retains `CAP_SETPCAP` only to let `setpriv` drop the
[capability bounding set](https://man7.org/linux/man-pages/man7/capabilities.7.html);
the build process receives no capabilities, including in its bounding set.
The environment is replaced rather than inherited. The unit deliberately
omits explicit `User=root`, following the existing broker capability boundary.
The helper caps the combined output across component commands; reaching the
bound fails the build instead of retaining unlimited output.

The service uses [`ExitType=cgroup` and `RemainAfterExit=yes`](https://www.freedesktop.org/software/systemd/man/257/systemd.service.html#ExitType=)
to retain its result while tracking descendants. The coordinator additionally
checks the recursive [`cgroup.events` population](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#un-populated-notification).
A successful main process alone never authorizes artifact packaging. Timeout
or cancellation requests stop the entire service; cleanup may wait past the
work deadline while kernel I/O prevents process exit.
The helper waits for stdout readiness in short intervals and polls each command
between waits. It reports a nonzero command exit promptly even when a descendant
keeps the output pipe open, so the coordinator can stop the whole service
without waiting for pipe EOF.
Successful commands still drain stdout through EOF. An observed helper failure
stops the service immediately, including log overflow and command failures that
leave descendants alive; the coordinator still waits for an empty cgroup before
packaging artifacts or releasing capacity.
Systemd may garbage-collect the transient unit after a stop request. The
coordinator accepts a missing unit only after its own stop request succeeded
and the unified cgroup is confirmed absent or unpopulated. A populated cgroup,
an unreadable cgroup state, or disappearance before a stop remains unconfirmed
and keeps the running claim in place.

The caller must hold both the host worker lease and a durable running-job
record throughout execution and cleanup. Launch, observation or stop errors
raise an unconfirmed outcome. Keep that running record blocking other claims
until supervised processes are proved stopped; do not automatically relaunch
the request. A host restart also requires this reconciliation. The unit name
contains the admitted request ID and cannot be reused for a new attempt.

This adapter does not provision bounded scratch storage, enrollment, toolchains,
credentials or retention. The worker and setup layers own those contracts.
`required_tools(manifest, source_path=pinned_checkout)` and
`check_tool_readiness(manifest, observed_versions, source_path=pinned_checkout)`
share marker-based runtime inference between controller and worker. Explicit
`build_runtimes`, including an empty list, overrides inference. Commands without
an explicit selection require the pinned checkout; shell text is never scanned.
Markers include Go modules, Node packages/pins, and Python project files, while
dependency and metadata directories and links are ignored. The worker checks
both initial and fresh observations filtered by the current builder grant.

Builder `--node` setup reuses the managed Node installer in
`/opt/basaltwater-build-tools`, running installer commands as the build identity
while drained and freezing the inventory as root-owned afterward. It contains
tools only; each job retains its fresh scratch HOME and no credentials.
Managed Node execution shares the ordinary deployment command wrapper, including
nested `nvm use`, pin precedence and engines policy. Node/npm and conventional
frontend package-manager declarations are checked unprivileged inside the unit;
Corepack network activation is disabled. Missing versions or exact package
managers must be provisioned explicitly by the operator before retrying. The
broker never installs tools to satisfy a repository. Go commands retain
`/usr/local/go/bin` on PATH.
Native Debian checks cover the homelab's pinned Node 24 and Node 26 builds,
including npm commands and the scratch-backed temporary mount.
Its unit tests mock systemd and process boundaries; selected Debian execution
also has bounded native coverage. See the [coverage map](../../plans/testing-coverage.md)
for tested boundaries and remaining qualification. Persistent jobs under one
build identity are mutually trusted; this is not an untrusted pull-request
isolation boundary.
