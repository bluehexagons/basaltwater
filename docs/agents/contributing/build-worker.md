# Internal build worker compositor

`lib.build_worker.run_next()` composes the existing durable job, source,
manifest, scratch, execution, artifact, and release adapters for one request.
It is a root-only internal API. It does not add a service, CLI, SSH dispatcher,
caller authentication, source upload, admission policy, activation, or host
recovery.

```python
status = run_next(
    store,
    uid=build_uid,
    gid=build_gid,
    inputs_root="/var/lib/basaltwater/build-inputs",
    scratch_root="/var/lib/basaltwater/build-scratch",
    artifacts_root="/var/lib/basaltwater/build-artifacts",
    logs_root="/var/log/basaltwater/builds",
    authorize=authorize_project_and_observe_tools,
)
```

The four roots must already exist as root-owned `0700` directories, reached
without following symlink components. Every ancestor must have a trusted owner
and must not allow group or other users to rename entries. A sticky root-owned
parent is accepted only when it protects a trusted-owned child. This matters
for the log path, which systemd opens later by its absolute pathname. The build
UID and GID must both be nonzero. A worker with no eligible job, including
while the store is draining, returns `None` without touching those roots.
Otherwise it claims the job durably before reading inputs or creating scratch
state.

## Input and policy checks

The private owner comes only from the returned `WorkerLease` claim. Request
fields never select an owner or filesystem path. The worker opens exactly
`<inputs_root>/<request-id>.source`; it requires a private root-owned regular
file with one link and mode `0400` or `0600`. The source extractor verifies the
framed snapshot and its admitted revision, snapshot digest, manifest digest,
file checksums, and history checksum before the worker uses any source file.

The worker reads the exact extracted `basaltwater.json` bytes through a
no-follow descriptor, checks their manifest digest, parses strict JSON, and
validates the admitted commands, environment, and exports against that
manifest. This first slice accepts only the exact `DEBIAN_PLATFORM` and
`static` or `service` components. Tool readiness is checked against the
manifest's declared `build_runtimes`. The trusted `authorize(owner, request)`
callback runs after claim and again immediately before launch; each call must
recheck current project policy and return observed tool versions. Only the
second set of ready tool versions is recorded in the artifact descriptor.

The caller remains responsible for target runtime and ELF compatibility
policy during activation. A build artifact describes bytes and build inputs;
it does not prove that a target can run those bytes.

## Scratch and execution boundary

After claim and the initial authorization check, the worker creates a fresh
bounded scratch image and unpacks source to the execution adapter's fixed
layout. The filesystem root and `source` directory remain root-owned and
traversable. Only `source/checkout` and `home` are handed to the build UID.
The root-owned `source/history.bundle` and `request.json` remain readable at
`0444`, outside writable directories. The worker does not give the build UID
traversal through broker-owned parents; `run_systemd_build()` bind-mounts the
scratch filesystem at its fixed `/run/basaltwater-build-work` path.

The worker creates a new exclusive `0600` log named `<request-id>.log` and a
bundle named `<request-id>.bundle` beneath the private artifacts root. Caller
policy owns log, input, artifact, and backing-storage retention limits. The
bundle is packaged only after `run_systemd_build()` confirms that the entire
service cgroup has stopped. The worker verifies the published bundle's file
identity, size, and digest before passing its descriptor to `lease.finish()`.

Cancellation is polled using the saved claim owner. The execution adapter
receives that poll callback for cooperative stop. A cancellation before launch
does not execute project commands. The worker checks cancellation before
artifact creation and again before final state. If cancellation races a
successful finish, the durable store check prevents a success result; a bundle
already published remains as an inspectable orphan and is not attached to a
cancelled job.

## Failure and recovery

Known validation failures and confirmed failed executions release the verified
scratch image before the job is finished as failed or, when cancellation is
confirmed, cancelled. Export `ValueError` after confirmed stop is a normal
failed build. Unconfirmed launch, probe, stop, mount, unmount, publication, or
storage outcomes retain the running claim and scratch evidence. The worker
raises only a fixed error for these cases; it does not return exception text,
request environment values, or build logs. `BaseException` follows the same
retention rule. A failed final state write is not retried.

A later worker cannot claim around a retained `running` record. Recovery must
be an explicit trusted `WorkerLease.recover_interrupted()` call whose cleanup
callback verifies that all work has stopped. This compositor never recovers,
replays, prunes artifacts, resets caches, or repairs host state automatically.

## Enablement boundary

This worker remains disabled until its caller supplies authenticated source
staging and retention admission, bounds inputs, logs, artifacts, and scratch
backing storage, enrolls and authorizes projects, provides activation-time
compatibility policy, and completes native qualification of the Debian
systemd, cgroup, and mounted scratch adapters. Before native qualification,
install the execution helper outside `/home`; the systemd adapter invokes its
own installed module path. The slice is not an isolation boundary for
untrusted pull requests or mutually hostile jobs sharing one build identity.

See [build jobs](build-jobs.md), [source snapshots](build-source.md),
[scratch storage](build-scratch.md), [build execution](build-execution.md),
and [build contracts](build-contracts.md) for the underlying adapter
contracts.
