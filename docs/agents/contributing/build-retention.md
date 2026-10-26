# Explicit terminal builder reservation settlement

`lib.build_retention.BuildRetentionStore` replaces a terminal input's unused
worst-case reservation charge with its verified conservative retained charge.
It is explicit root-local maintenance. It retains every source, log, bundle,
embedded descriptor, input intent, authenticated owner and request/replay ID.
It never runs during admission and provides no deletion, garbage collection,
pin policy, remote action, recovery API, or automatic repair.

## Trusted API and local CLI

```python
retention = BuildRetentionStore(
    inputs=input_store,
    jobs=job_store,
    scratch_root=configured_scratch_root,
    artifacts_root=configured_artifacts_root,
    logs_root=configured_logs_root,
)
plan = retention.inspect_retention(request_id)
receipt = retention.settle_terminal(request_id, expected_revision=plan["revision"])
```

The compositor accepts only trusted stores and administrator-selected roots.
All five roots must already exist as distinct, non-overlapping effective-UID-owned
`0700` directories, reached through protected no-follow ancestry. Root is checked
before store access. Request IDs use the existing lowercase 32-hex protocol;
expected revisions are lowercase 64-hex SHA-256 digests. No request supplies a
path. Only fixed ID-derived files beneath builder roots are opened. Target and
controller files are never traversed.

The installed [build-host entrypoint](build-host-entrypoint.md) exposes:

```bash
/usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py \
  --inspect-retention REQUEST_ID
/usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py \
  --settle-terminal REQUEST_ID --expected-revision PLAN_REVISION
```

Replace the example install path with the trusted installation path. Both
commands emit bounded JSON. The existing local `--config` option selects the
administrator configuration. Root, isolated Python, trusted installed code,
platform, account, configuration and root checks precede maintenance. Neither
mode accepts a principal or `--once`; settlement requires a revision. Any SSH
original-command, connection or client environment rejects maintenance before
project imports or configuration reads. The restricted SSH service and public
job/SSH client documents are unchanged.

Plans and receipts contain only request ID, terminal state, revision, original
reservation, current and proposed charges, settlement status, metadata charge,
and three bounded payload identities. They contain no commands, environment,
log contents or full descriptors and are limited to 16 KiB. Inspect changes no
charge. Apply recomputes the evidence and refuses a stale revision. An exact
retry returns the same settled receipt, including after reopening.

## Validation and concurrency

Each operation first acquires the existing nonblocking host worker lease, then
the input lock, then briefly reads a detached private job record under the job
state lock. It never acquires worker or input locks while holding job state and
never hashes payloads while holding job state. Drain and job reads remain
available during verification. A busy worker immediately refuses maintenance.
The lease is held through final verification and the atomic metadata save.

Only `succeeded`, `failed`, `cancelled` and `expired` jobs qualify. Queued,
running, uncertain, missing or unmatched job/input records, incomplete uploads,
any present ID scratch entry, or ambiguous publication evidence are refused.
Scratch is checked for absence without opening or descending into it. Unknown
artifact/log names, temporary publication trees, and payload IDs with no retained
input intent are ambiguous; root administrators own resolution.

The source must have its exact immutable intent size and SHA-256, bound to the
same job request digest, authenticated owner, source identity and original
reservation. Source is an owned single-link regular `0400` file; logs and bundles
are owned single-link regular `0600` files. No-follow opens and stable device,
inode, owner, mode, link count, size and timestamp checks bracket reads. Roots,
job evidence, named payload identities, partial absence and scratch absence are
checked again before saving.

Every present bundle is checked, even for cancellation or failure with
`artifact=None`. Its embedded descriptor must pass the existing canonical
descriptor/request validator, inventory checksums, size bounds and exact EOF.
Succeeded jobs additionally require a log and the bundle size, bundle SHA-256
and descriptor SHA-256 retained in the terminal job. Orphan bundles are retained
and charged rather than attached to an unsuccessful job. The descriptor bytes
are already inside the bundle and are counted there. Separate descriptor files
are not a builder layout and are refused as ambiguous evidence.

The revision binds immutable input identity, private terminal job digest,
configured root paths and directory identities, all retained payload hashes and
file identities, presence/absence and the calculated charge. It deliberately
excludes mutable intent charge/schema and unrelated drain state so an exact
settlement retry is idempotent. Changed settled evidence is refused for root
reconciliation; this API cannot authorize replacement evidence or repair state.

## Charge, bounds and durability

Each source, bundle and log costs `max(actual logical bytes, 4096)`, including
zero-byte files. Metadata costs five 4096-byte allowances for the input intent,
input lock/witness and job state/worker locks, plus
`max(4096, encoded private job record bytes + 1024)` for the job and ledger
envelope. Shared overhead is conservatively charged per input. A charge above
the immutable original reservation is refused. This accounting is a logical
admission quota, not a filesystem reservation or a measurement/guarantee of
physical allocated blocks, filesystem metadata, or available disk space.

Existing version-1 intents stay fully charged until explicit successful apply.
Settlement writes a version-2 intent containing the original reservation and
all original request/source/owner identities, plus the current charge and
revision. It checks the same 1024-byte intent and aggregate metadata bounds
before writing. Under the input lock it syncs a private fixed temporary intent,
atomically replaces the original intent and syncs the directory. No separate
counter is credited. Before replacement, failures preserve the entire prior
charge. After replacement, sync/reply ambiguity reconciles through the saved
revision; reads must sync the new record and directory before admission uses
it. A crash leaving the fixed temporary metadata file fails closed. No payload
is ever unlinked, truncated, chmodded or rewritten.

The default 128 input-intent ceiling, configured metadata bounds and independent
4096-job/16-MiB history ceilings still fail closed. At most one source, bundle
and log per admitted ID fit the protected layout; descriptor and payload sizes
remain bounded by protocol/request limits. Settlement can reclaim unused scratch
and output allowances for later admission, but cannot free a record slot or
physical disk bytes. Operators must retain the protected bundles and replay
metadata; this slice provides no unbounded history, automatic pruning or actual
GC. As with the underlying stores, a malicious process sharing root's effective
UID is outside the local durability boundary.
