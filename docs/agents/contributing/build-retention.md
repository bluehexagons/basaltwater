# Explicit terminal builder settlement, pruning and bundle release

`lib.build_retention.BuildRetentionStore` replaces a terminal input's unused
worst-case reservation charge with its verified conservative retained charge.
Settlement preserves all payloads. After settlement, explicit `prune_terminal()`
can delete selected source and log payloads. A separate `release_bundle()`
action relinquishes builder retrieval of an exact successful bundle and removes
its bytes, including its embedded descriptor. Unsuccessful orphan bundles remain
charged. Input intents, authenticated owners, replay identities and terminal job
results remain retained. These actions never run during admission. There is no
age policy, recursive sweep, target/controller traversal, history compaction or
remote action. Downloads never release a bundle hold.

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
settled = retention.settle_terminal(request_id, expected_revision=plan["revision"])
pruned = retention.prune_terminal(
    request_id, expected_revision=settled["revision"], kinds=("source", "log"),
)
released = retention.release_bundle(request_id, expected_revision=pruned["revision"])
```

The compositor accepts only trusted stores and administrator-selected roots.
All five roots must already exist as distinct, non-overlapping effective-UID-owned
`0700` directories, reached through protected no-follow ancestry. Root is checked
before store access. Request IDs use the existing lowercase 32-hex protocol;
expected revisions are lowercase 64-hex SHA-256 digests. No request supplies a
path. Only fixed ID-derived files beneath builder roots are opened.

The installed [build-host entrypoint](build-host-entrypoint.md) exposes:

```bash
/usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py \
  --inspect-retention REQUEST_ID
/usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py \
  --settle-terminal REQUEST_ID --expected-revision PLAN_REVISION
/usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py \
  --prune-terminal REQUEST_ID --expected-revision PLAN_REVISION
# Select one kind; omit --prune-kind to select both source and log.
/usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py \
  --prune-terminal REQUEST_ID --expected-revision PLAN_REVISION --prune-kind source
/usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py \
  --release-bundle REQUEST_ID --expected-revision PLAN_REVISION
```

Replace the example install path with the trusted installation path. Commands
emit bounded JSON. Repeat `--prune-kind` to select both explicitly. Settlement
must complete before pruning or bundle release. Bundle release requires an exact
`succeeded` job, and cannot be selected with `--prune-kind` or combined with
source/log deletion. Root's explicit release authorizes relinquishing builder
retrieval; it does not change historical success or the job's artifact identity.
The local `--config` option selects administrator configuration. Root, isolated
Python, trusted installed code, platform, account,
configuration and root checks precede maintenance. No maintenance mode accepts
a principal or `--once`; settlement, pruning and release require a revision. Any
SSH original-command, connection or client environment rejects maintenance before
project imports or configuration reads. No SSH action or user panel is added.

Plans and receipts contain request ID, terminal state, revision, original
reservation, current/proposed charges, settlement status, metadata charge,
three bounded payload identities, availability, and at most three pending/completed
disjoint payload operations. Operations expose their original expected revision,
selected kinds, previous charge and intended final charge. Plans contain no commands,
environment, log contents or full descriptors and are limited to 16 KiB.
Inspect changes no charge or payloads and never resumes deletion.
`pending_present` and `pending_absent` distinguish interrupted deletion progress;
`pruned` identifies completed tombstones. Apply revalidates evidence and refuses
stale revisions. Completed same-operation retries return the same result,
including after reopening, later bundle release or pruning other kinds separately.

## Validation and concurrency

Maintenance acquires worker (nonblocking), input, exclusive per-job payload
protection (nonblocking), then a short job-state snapshot, in that order. It
never acquires worker/input locks while holding job state and never hashes
payloads under job state. The job snapshot requires the existing state lock,
exact initialization witness and ledger; it reads them without creating,
rewriting, syncing or repairing job-state evidence, and refuses state-lock
contention without waiting. This guarantee covers job-state evidence; input-side
durability reconciliation may still occur during maintenance. Drain and job
reads remain available during hashing. A busy worker, log reader or artifact
download refuses maintenance immediately. All protection is held through final
verification and metadata save.

`lib.build_payloads.payload_lease()` uses Linux OFD record locks on the existing
stable worker-lock inode, independent of its worker `flock`. A request ID hashes
to one byte offset in a 56-bit space; a collision only causes conservative
contention. Locking does not write or grow the file and adds no lock-file or
record costs. Private root, no-follow, single-link ownership/mode and named
inode checks also apply. Never delete or replace that stable file. Service log
and artifact reads acquire shared protection before opening bytes, reauthorize
against a fresh job snapshot, and retain protection through verification,
response streaming, flush and cleanup, including `BaseException` paths.

Settlement and source/log pruning accept `succeeded`, `failed`, `cancelled` and
`expired` jobs. Bundle release accepts only `succeeded`. Queued,
running, uncertain, missing or unmatched job/input records, incomplete uploads,
any present ID scratch entry, or ambiguous publication evidence are refused.
Scratch is checked without opening or descending into it. Unknown artifact/log
names, temporary publication trees, and payload IDs with no retained input
intent are ambiguous; root administrators own resolution.

Before pruning, source must have its immutable intent size and SHA-256, bound
to the same request digest, owner and original reservation. Source is an owned
single-link regular `0400` file; logs and bundles are owned single-link regular
`0600` files. No-follow opens and stable device, inode, owner, mode, link count,
size and timestamp checks bracket reads. Roots, job evidence, named identities,
partial absence and scratch absence are checked again before saving.
Deletion is descriptor-relative and rejects replacement before unlink.

Every bundle is checked, including cancellation/failure with `artifact=None`.
Its embedded descriptor must pass canonical descriptor/request validation,
inventory checksums, size bounds and exact EOF. Succeeded jobs initially require
a log and the bundle size, bundle SHA-256 and descriptor SHA-256 retained in the
job. Orphan bundles remain charged without attaching them to unsuccessful jobs.
Descriptor bytes are counted inside the bundle. Separate descriptor files are
ambiguous evidence. Missing unrecorded payloads fail closed; only selected
deletions recorded in a durable pruning intent can explain later absence.

The settlement revision binds immutable input identity, private terminal job
digest, configured root paths and directory identities, retained hashes and
identities, presence/absence and charge. It excludes mutable charge/schema
and unrelated drain state, preserving existing version-2 settlement retries.
Prune/release revisions chain from that original revision and each completed
kind set, using exactly `["bundle"]` for release. Original payload evidence stays
separate from observed availability, so successful artifact validation and old
source/log receipts remain exact after bundle absence is recorded.
Changed evidence requires root reconciliation; pruning never authorizes
replacement evidence or changes a terminal result.

## Charge, bounds and durability

Each retained source, bundle and log costs `max(actual logical bytes, 4096)`,
including zero-byte files. Metadata costs five 4096-byte allowances for the input
intent, input lock/witness and job state/worker locks, plus
`max(4096, encoded private job record bytes + 1024)` for the job/ledger envelope.
The existing intent allowance covers the new bounded tombstone; OFD protection
reuses an already-charged stable lock. Shared overhead is overcounted per input.
A charge above the immutable original reservation is refused. Accounting is a
logical admission quota, not a filesystem reservation or a measurement/guarantee
of physical blocks, filesystem metadata or available disk space.

Version-1 intents stay fully charged until explicit successful settlement.
Settlement writes version 2, retaining all original identities and reservation,
adding current charge and revision. Versions 1/2 retain their 1024-byte cap.
Source/log pruning writes version 3 with original payload identities/hashes,
terminal job digest and at most two disjoint operations. Release converts version
2 or completed version 3 to version 4 in the same envelope. Version 4 requires
nonnull original bundle evidence and exactly one standalone bundle operation;
later source/log pruning preserves version 4. At most three disjoint operations
are allowed, with only the last pending. Versions 1–3 retain their existing
encoding, semantics and revisions. Versions 3/4 have a strict 4096-byte cap;
pending and completed intent, plan and aggregate bounds are checked before unlink.
Aggregate metadata is capped at `min(4 MiB, max_records * 4096)`. The immutable
original reservation formula still includes exactly 1024 bytes of intent overhead.

A durable pending intent binds fixed ID-derived selected payload paths, original
identities/hashes, original expected revision, immutable input/job identities,
prior charge and intended availability before any selected unlink. The full
prior charge remains until selected deletions, affected directory syncs, final
evidence checks and atomic completion save succeed. No separate counter is
credited. Explicit resume requires the exact original revision and kind set,
verifies remaining files, permits absence only for selected recorded deletions,
syncs affected directories even when already absent, then saves completion.
Unselected evidence must still match. Completed retries never double-credit and
sync affected directories again.

Metadata replacement syncs a private fixed temporary intent, replaces the
original and syncs the input directory. Post-replace sync/reply uncertainty is
reconciled by syncing the saved record and directory before admission uses its
charge. Admission never resumes pruning. A leftover temporary fails closed for
admission/inspection, including read-only capacity inspection. An exact
`settle_terminal(ID, expected_revision=...)` retry from a retained version-1
intent can discard only its own complete canonical version-2 temporary, exactly
matching the freshly recomputed proposed transition. It revalidates the full
job, payload, root, revision, quota and metadata evidence under the usual locks
before discarding that owned, single-link, no-follow `0600` bounded file. It
syncs the input directory, then performs the ordinary durable settlement save;
it never adopts the staged charge. Interruption during discard, sync or a new
pre-replace save retains the original reservation charge and permits the same
exact retry. Inspection and admission never discard or resume staged settlement.
Explicit same-operation prune can discard only a fully
validated, canonical temporary matching the exact pending or completed next
transition; it then redoes deletion checks and syncs without adopting a staged
lower charge. Partial or unrelated temporaries require root reconciliation.
The same rule covers the first release from version 2 or completed version 3:
an exact release retry revalidates revision, evidence and limits before discarding
its matching complete canonical version-4 pending/completed transition. It syncs
the discard and repeats the ordinary transition; it never adopts a temporary's
lower charge. Only explicit release with the original revision can resume a
pending bundle operation. Inspect, settlement, source/log pruning and admission
cannot resume it. Unsafe, partial, stale or unrelated temporaries are preserved.

After completed pruning or release, input replay compares owner, request digest,
source size and digest before reading bytes. It uses a short job-lock snapshot that
requires the exact retained terminal job digest and never calls `submit()`.
The snapshot requires an existing stable state lock, exact initialization
witness and ledger; it is read-only and refuses lock contention without
initializing or reconciling job evidence. Missing, changed, active or uncertain
jobs refuse before replay stream reads; pruned payloads are never recreated. A
supplied stream still undergoes bounded exact size, EOF and SHA-256 checks;
source-less exact replay also returns the existing receipt. This exact job replay
also applies when source is still present after bundle release. Pending
pruning/release refuses submit replay until explicit completion. A pruned terminal log returns
the service's fixed `unavailable`, never `waiting`. A released bundle returns
fixed `unavailable` with zero payload bytes; status retains historical success
and artifact identity without any protocol or job schema change.

The default 128 input-intent ceiling and independent 4096-job/16-MiB history
ceilings still fail closed. Pruning cannot free slots or provide indefinite
history. Settlement frees unused logical allowances; pruning additionally removes
selected physical files, subject to filesystem semantics. Bundle release frees
exactly `max(bundle size, 4096)` once; it never frees input slots or job history.
Unsuccessful or uncertain bundles and replay metadata remain pinned. Tests use
small temporary payloads and mocked failures; native crash/durability
qualification belongs to the integration boundary. A
malicious process sharing root's effective UID is outside this local boundary.
