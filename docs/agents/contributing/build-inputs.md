# Authenticated builder input admission

`lib.build_inputs.BuildInputStore` stages one already-authorized build request
for the internal worker. It authenticates no caller, grants no project access,
and provides no transport, CLI, service, or request deadline. A typed transport
must authenticate and authorize first, keep ownership separate from request
data, and pass its trusted principal as `owner`.

## API and ordering

```python
store = BuildInputStore(
    "/var/lib/basaltwater/build-inputs",
    max_records=128,
    max_bytes=64 * 1024**3,
)
status = store.admit(
    validated_request,
    owner=authenticated_principal,
    source=deadline_supervised_binary_stream,
    source_size=declared_snapshot_size,
    jobs=job_store,
)
```

The request is parsed with `lib.build_protocol.parse_build_request()` and bound
to its canonical full-request digest. `source_size` must be a positive integer
no greater than `DEFAULT_SOURCE_LIMITS.max_snapshot_bytes` from
`lib.build_source`. The store reserves, before reading the stream, the sum of
the source size, requested scratch capacity, requested artifact capacity,
`MAX_DESCRIPTOR_BYTES`, 1024 bytes of intent overhead, and requested log
capacity.

The store first persists an exclusive `0600` `<request-id>.upload.json` intent
containing only the authenticated owner, request digest, source identity and
size, and retention reservation. It does not retain commands or environment
values. Then it writes one exclusive `<request-id>.part` file in bounded
chunks, checks exact length, EOF, and the pinned source SHA-256, syncs the file,
sets it to `0400`, and publishes `<request-id>.source` with Linux no-replace
rename. That exact `.source` path is what `lib.build_worker` opens. The
published file is synced and rechecked before `BuildJobStore.submit()` is
called.

The source file itself is the publication marker. If the process stops after
the rename but before returning or submitting the job, the next same-owner,
same-request call verifies the complete published file and can safely call the
idempotent queue submission again. It never replaces that file. A call may
omit `source` only for this completed replay; when a replay stream is supplied,
the store consumes and verifies it too.

## Root and concurrency

Provision the root before constructing the store. It must be an existing
effective-UID-owned `0700` directory reached through no-follow components.
Every ancestor must be owned by root or the effective UID and must not allow
group or other users to rename entries. A trusted sticky parent such as `/tmp`
is allowed when it protects a trusted-owned child. The root must be dedicated
to builder inputs; unexpected entries, symlinks, hard links, special files,
and files without a retained intent fail closed.

A stable `.build-inputs.lock` and initialization witness serialize processes
that use the root. Do not remove or replace either while the store is in use.
Intents, the stable lock, and its witness are private regular files. The worker
continues to consume only `<request-id>.source`; it ignores admission metadata
and partial uploads.

## Replay, interruption, and retention

An identical owner, request digest, source size, and source identity can replay
the existing admission. A different owner, different request input, or
different size for the same request ID fails. `BuildJobStore.submit()` supplies
the queue's own durable replay protection, so losing its response does not
create a second job.

An interrupted or invalid stream leaves its intent and fixed `.part` file in
place. A later call refuses that incomplete intent and does not overwrite or
resume the partial data. A fully published source left before queue submission
is recoverable by verifying its type, owner, link count, mode, size, stable
file identity during the read, and full hash. Corrupt intents, ambiguous files,
or filesystem sync failures remain for operator reconciliation. The store does
not clean evidence or automatically replay work after an exception.

Every retained intent, including one with failed or partial input, counts
toward `max_records`. New and existing version-1 intents retain their full
worst-case reservation as the charge toward `max_bytes`. Only explicit
root-local [terminal settlement](build-retention.md) can replace that charge
with a verified conservative retained-byte charge; admission never invokes
settlement. Version-2 intents retain all original identity fields and immutable
`reservation_bytes`, adding only `charge_bytes` and `settlement_revision`.
Version-2 replay compares original inputs and submits the same ID to the job
store, including after reopening. Explicit source/log pruning writes version 3
with bounded tombstones and at most two disjoint pruning operations. Pending
pruning refuses replay before reading bytes and never resumes during admission.
Completed version-3 replay requires an existing exact certain-terminal job under
the short job lock, including its retained private job digest. It never calls
`submit()`, recreates payloads or queues work if a job record is absent. Owner,
request and source identity/size mismatches fail before stream reads. A supplied
stream must still pass bounded exact length, EOF and SHA-256 checks; an exact
replay may omit the stream after durable source pruning.

Version-1/2 intents remain limited to 1024 bytes. Version 3 is capped at 4096
bytes, covered by settlement's existing 4096-byte intent allowance; the original
reservation formula retains its immutable 1024-byte overhead. Aggregate metadata
is capped at `min(4 MiB, max_records * 4096)`. Each intent uses at most
one fixed `.part` path, covered by its source-size reservation. Default input
capacity remains 128 retained intents; the independently bounded job store
retains at most 4096 jobs. Neither ceiling is relaxed by settlement or pruning.
Full capacity rejects new input; only explicit root-local pruning can delete
selected terminal source/log files. Bundles, descriptors and replay evidence
remain retained, with no automatic garbage collection.

Settlement atomically replaces the bounded intent under the stable input lock.
A failed replacement leaves the previous charge intact. If replacement succeeds
but directory sync or acknowledgement fails, subsequent reads sync the settled
intent and directory before using its charge; explicit retry reconciles the
same verified revision without applying another credit. A leftover settlement
temporary file after a crash is ambiguous evidence and fails closed for root
reconciliation. Only an exact explicit pruning retry can reconcile a complete
canonical temporary matching its pending/completed transition, as described in
[terminal retention](build-retention.md). Payload files are never removed or
changed by settlement.

The transport owns stream deadline supervision and must ensure each read is
bounded by its deadline. It can use its deadline-aware stream wrapper before
passing the binary reader here. `BuildInputStore` does not accept a callback
that can outlive its call or supervise transport timeouts.

This is local durability and byte-identity handling, not protection from a
malicious process running as the same effective UID, proof of authorization,
source provenance, or build isolation. The internal
[process entrypoint](build-host-entrypoint.md) connects this store to
authenticated transport, project policy, durable jobs and the worker. Managed
[enrollment](build-host-setup.md) and [controller setup](build-setup.md) compose
those boundaries.
