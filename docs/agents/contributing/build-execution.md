# Debian build execution adapter

`lib.build_execution` is an internal systemd adapter, separate from request
admission, durable job state, source preparation and artifact publication. It
does not yet expose a setup flag or runnable build service. Existing deployment
commands continue to build on the destination.

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
`/tmp` and `/var/tmp` are read-only inside the unit; `TMPDIR` selects `home/tmp`,
so ordinary temporary files cannot bypass the scratch capacity limit.
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
An observed helper failure also stops the service immediately, including log
overflow and command failures that leave descendants alive.

The caller must hold both the host worker lease and a durable running-job
record throughout execution and cleanup. Launch, observation or stop errors
raise an unconfirmed outcome. Keep that running record blocking other claims
until supervised processes are proved stopped; do not automatically relaunch
the request. A host restart also requires this reconciliation. The unit name
contains the admitted request ID and cannot be reused for a new attempt.

This adapter does not provision bounded scratch storage, enrollment, toolchains,
credentials or retention. Those remain prerequisites for enabling the first
end-to-end build-host slice. Its tests mock systemd and process boundaries;
native capability, cgroup, cancellation and resource-limit qualification is
still required. Persistent jobs under one build identity are mutually trusted;
this is not an untrusted pull-request isolation boundary.
