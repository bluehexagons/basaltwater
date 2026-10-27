# Durable on-demand build jobs

`lib.build_jobs.BuildJobStore` is the local durable queue and single worker
lease for on-demand build requests. It stores build intent independently of a
transport connection so a caller can recover a lost response by querying or
resubmitting the same request ID. It does not provide SSH, setup integration,
caller authentication, project authorization, source readiness checks, command
supervision, or runtime execution.

## Caller responsibilities

- Create the store beneath an existing directory owned by the effective UID
  with owner read/write/search and no group or other permissions. The state
  file and stable sibling locks are private regular `0600` files. Do not remove
  or replace a lock file while the store is in use.
- Authenticate the caller and authorize its project before calling the store.
  Pass the trusted principal as `owner`; ownership is not accepted in the build
  request. Check that source inputs are ready and that the requested platform
  and limits are allowed.
- Keep the validated build request private. It contains literal commands and
  build environment values. `submit()` validates and retains the complete
  request; `status()` returns only request ID, sequence, project, source,
  platform, limits, state, cancellation state, timestamps, artifact identity,
  or a fixed reason. It never returns commands, environment values, or the full
  request.
- Recheck current authorization policy after `claim()` and immediately before
  execution. The queue is not a policy engine.
- Keep the `worker()` lease through execution and cleanup. Before calling
  `finish()`, verify that every supervised process has stopped and pass
  `work_stopped=True`. Pass `lease.fileno()` with `pass_fds` only to a trusted
  supervisor or guardian, never to request-controlled build commands. This
  keeps the flock held if the worker process exits while a supervised child is
  still alive. The production supervisor must also verify its systemd cgroup
  is empty before releasing the lease.
- Treat `recover_interrupted()` as a trusted cleanup boundary. Its callback
  must verify caller-owned process cleanup and return the literal boolean
  `True` only when no supervised work remains. Make cleanup idempotent because
  a failed state write can require the verification to be repeated.

## API

```python
store = BuildJobStore("/var/lib/basaltwater/build-jobs/state.json")

receipt = store.submit(validated_request, owner=authenticated_principal)
public_status = store.status(receipt["request_id"], owner=authenticated_principal)
public_status = store.cancel(receipt["request_id"], owner=authenticated_principal)
store.set_draining(True)

with store.worker() as lease:
    claim = lease.claim()
    if claim is not None:
        # The trusted parent rechecks policy, supervises the work, and cleans up.
        lease.finish(claim["request_id"], outcome="failed", work_stopped=True)
```

`submit(request, *, owner)` parses the complete version-1 build request using
`lib.build_protocol`, computes its canonical request digest, and durably saves
it before returning the public receipt. A globally unique request ID is bound
to that owner and digest for the lifetime of the store. The same owner and
identical request replay the same record, including after a lost reply or a
terminal result. Another owner or different input using that ID is rejected;
it never starts a second build.

After explicit source/log pruning or successful bundle release, input admission
instead uses
`replay_terminal(request, owner=..., expected_job_sha256=...)`. This requires an
existing certain-terminal job with exact owner, full request digest and private
job-record digest under the short state lock. Replay requires the established
state lock, exact initialization witness and ledger to exist, and reads them
without initializing, reconciling or expiring anything. It refuses lock
contention without waiting. It returns the existing public receipt without
creating work or changing job results. Missing, changed, active or uncertain
records refuse before replay stream reads.
Version-4 release tombstones use this path even when the original source is
still present. Pending release refuses replay. Bundle release changes no job
record: historical success, artifact identity, owner and receipt remain exact,
and input slots and job-history capacity are retained. Missing released payloads
are reported unavailable by the service without changing the job schema.
See [terminal retention](build-retention.md) for bounded tombstones and recovery.

`status(request_id, *, owner)` and `cancel(request_id, *, owner)` are
owner-scoped. Cancelling a queued job durably makes it terminal. Cancelling a
running job sets `cancel_requested`; the trusted worker decides how to stop and
must finish only after cleanup. Repeated status and cancel calls return the
cached job state.

`set_draining(True)` rejects new admissions and prevents workers from claiming
accepted queued work. Existing request replays, status, and cancellation remain
available. Set it to `False` to allow new claims and admissions again.

`worker()` yields a `WorkerLease` while holding the single host worker flock.
`lease.claim()` selects the lowest persisted submission sequence, rechecks its
expiry after loading state, records `running` durably, and only then returns
`{"request_id", "owner", "sequence", "request"}` to the trusted caller. The
saved authenticated owner enables policy rechecks and cancellation observation;
it is never taken from repository input or added to public status. It returns
`None` when there is no eligible work or the host is draining. A second claim
cannot bypass a running claim.

`lease.finish(request_id, *, outcome, work_stopped, artifact=None)` accepts
`succeeded`, `failed`, `cancelled`, or `uncertain`. Success requires an artifact
object with exactly `sha256`, `size`, and `descriptor`. The store reuses
`validate_artifact_for_request()` to validate descriptor structure and bind
its identity to the admitted request. It retains only the artifact digest,
bundle size, descriptor digest, and request/project/source/platform identity.
Failure, cancellation, and uncertainty retain no artifact and expose only a
fixed reason (`build_failed`, `cancelled`, or `worker_interrupted`). Raw
exception text and build output do not belong in this store.

If a process exits with a durable `running` claim, the next worker cannot claim
anything until it explicitly calls
`lease.recover_interrupted(request_id, cleanup=callback)`. Recovery requires a
different active lease from the one recorded on the interrupted claim, invokes
the trusted cleanup callback outside the state lock while retaining the worker
flock, then records the job as `uncertain`. It never retries that request.
Queued work may proceed after the uncertain record is durable. The callback
receives the request ID and a private copy of the full validated request.

## Bounds and persistence

The default queue wait is 900 seconds and the default maximum waiting queue is
8 jobs. `queue_ttl_seconds` and `max_queue_length` can lower or raise those
values within the module's validated bounds. Expired queued jobs become
terminal during status, cancellation, admission, drain, or claim operations;
claim always checks expiry again before writing `running`.

The store retains at most 4096 request IDs and 16 MiB of encoded state. Both
limits fail closed. Before accepting a new request, admission reserves encoded
space for every queued/running job's eventual lease, timestamps, fixed reason
and complete bounded artifact identity, plus future ledger-clock growth. The
size projection deliberately combines maximum fields and is never persisted.
An acknowledged job can therefore progress, cancel, expire or recover without
exhausting the ledger byte ceiling. Terminal history costs its exact retained
encoding; no identities are removed. This check applies to new admissions and
does not repair jobs accepted by older code without growth allowance. Capacity
inspection's metadata headroom remains raw unused bytes, not an admission
decision or an allowance after future lifecycle growth.
The store does not prune replay IDs, reset state, or provide a
repair path. Corrupt, unsupported, unsafe, or unreadable state is an error and
is never treated as an empty store. State updates use a private same-directory
atomic replacement and filesystem sync before acknowledgement. Stable state
and worker lock files are separate: status and admission hold the state lock
briefly, while the worker flock remains held through claim, execution, and
cleanup.
The stable state lock also holds a durable initialization witness. After that
witness is written, a missing ledger is an error requiring restoration, never
a fresh queue. Preserve the ledger and its stable locks together; removing both
destroys the local replay evidence and is not a supported reset operation.

The monotonically increasing submission sequence defines FIFO order even when
wall-clock timestamps repeat or move backward. Persisted timestamps are
finite, and the durable high-water mark prevents accepted jobs from receiving
earlier timestamps after a clock rollback.

These records provide local replay protection and capacity coordination. They
do not protect against a malicious process with the same effective UID, prove
authorization, validate source readiness, supervise processes, or establish
artifact provenance beyond the shared request/descriptor parser.
