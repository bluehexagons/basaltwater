# Build-host process entrypoint

`common/service_tools/build_host.py` composes the internal builder behind a
root-controlled launcher. Managed [enrollment](build-host-setup.md) installs
its authority, and [selected-builder setup](build-setup.md) uses its SSH service.
The initial host platform is Debian 13 x86-64. Standard deployment is unchanged.

Install the source beneath a root-controlled tree outside `/home` and invoke
the entrypoint with `/usr/bin/python3 -I`. Its configuration defaults to
`/etc/basaltwater/build-host/config.json`, inside a root-owned `0700` directory.
The no-follow, single-link file must be root-owned `0600`. It contains exactly:

- `schema: 1`, positive unprivileged `uid` and `gid`;
- `roots`: distinct non-overlapping private directories named `state`,
  `inputs`, `scratch`, `artifacts`, and `logs`;
- `policy`: the [project policy](build-host-policy.md);
- `max_records` and `max_bytes`: input admission's retained-record and reserved
  storage ceilings. Full capacity refuses new work and requires reconciliation.

The numeric IDs must match the primary UID and GID of the dedicated
`basaltwater-build` account and group. The account requires `/nonexistent` as
its home and a `nologin` shell; its group cannot include unrelated accounts.
Other passwd entries cannot share either numeric ID, and the GID cannot have
another group alias.
The helper refuses another service or operator identity before admission.

The worker runs with `--worker`, independently of SSH. `--once` processes at
most one claim. It re-reads current policy at both authorization boundaries;
changes to identity, roots or storage settings require a drained restart.
Only installed tools allowed by the project grant reach the pinned manifest's
readiness check. An interrupted or uncertain claim needs explicit recovery;
the process never replays it automatically. Its service should stop on failure,
leaving the claim and evidence available to an administrator.

For SSH, a dedicated key's forced command fixes `--serve --principal LABEL`.
Use OpenSSH `restrict` and disable password login for this endpoint; do not
grant the build identity that key or an interactive shell. The entrypoint
requires the exact original command `basaltwater-build-v1`, accepts one typed
request on stdin, and limits the connection to 15 minutes. The client cannot
select a principal, path, shell command, drain operation or configuration file.
Root administrators can select `--config` when operating the helper locally;
that option must never be interpolated from SSH input.

The connection deadline bounds normal framing, upload and download waits. A
kernel syscall that cannot be interrupted can delay process exit. Accepted
jobs are durable and use an independent worker, so controller disconnects do
not cancel them. Status queries and repeated request IDs reconcile lost replies.
Runtime secret values and target credentials do not belong in build inputs.

The entrypoint connects [bounded source admission](build-inputs.md) and the
[typed dispatcher](build-host-service.md) to the independent worker. Selected
native Debian checks cover SSH builds, artifact retrieval and selected public
setup flows; see the [coverage map](../../plans/testing-coverage.md) for limits.
Persistent jobs sharing an identity must be mutually trusted.

Explicit root-local [terminal settlement](build-retention.md) can lower unused
reservation charges after verifying certain terminal jobs and retained payloads.
It preserves payloads and replay IDs and is unavailable through SSH.
After settlement, `--prune-terminal ID --expected-revision DIGEST` explicitly
deletes source and log payloads by default. Repeat `--prune-kind source` or
`--prune-kind log` to choose kinds explicitly. Inspect reports availability and
pending/completed pruning; interrupted pruning requires its original revision
and kind set. This is also root-local only, rejected before imports/configuration
when any SSH environment marker is present. Bundles, job results and replay
identities remain retained; no automatic pruning policy or SSH action is added.

## Read-only local capacity inspection

```bash
/usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py \
  --inspect-capacity
```

Use the trusted installation path and optionally the local administrator's
`--config`. This mode has the same root, isolated Python, trusted code,
platform, account, configuration and root restrictions as `--inspect-retention`.
Any SSH original-command, connection or client environment marker rejects it
before project imports or file access. It accepts no principal, `--once`,
revision or prune kinds. It adds no SSH action.

The trusted Python API is
`lib.build_capacity.inspect_build_capacity(config: Mapping[str, Any]) -> dict[str, Any]`.
Pass the configuration returned by `load_config()`; only `roots`, `max_records`
and `max_bytes` are consumed and the mapping is unchanged. It returns detached
JSON-compatible aggregates, capped at 64 KiB, without paths, owners, request
commands, environment values or payload contents. Errors are fixed and contain
no retained evidence. Missing, malformed, unsafe or ambiguous accounting
evidence fails closed; it is never interpreted as an empty store.

`inputs` reports immutable original reservations, persisted current charges,
byte ceiling/headroom, retained slots and their ceiling/headroom, encoded intent
metadata bytes and their ceiling/headroom. It counts unsettled, settled, pruned
and pending-pruning intents separately. Every intent, including a tombstone or
incomplete upload, still occupies one of the heavyweight input admission slots.
`jobs` reports all replay records, the independent 4096-record ceiling and
headroom, actual encoded ledger bytes against its 16 MiB ceiling, draining and
persisted state counts. Neither settlement nor pruning frees a retained slot or
job replay record. Headroom in one dimension does not bypass another ceiling.

An input without a job can be an interrupted upload or publication before
submission: it remains fully charged and is counted under `inputs_without_jobs`
and, when applicable, `incomplete_inputs`. Existing jobs must match retained
input identities and reservations. Settled/pruned inputs require existing
certain-terminal jobs; version-3 pruning also requires the exact retained job
digest. Pending pruning keeps its previous charge, completed pruning keeps its
persisted charge, and uncertain jobs keep their full unsettled reservation.
Inspection never proposes or applies a lower charge.

`filesystems` groups the five configured root labels by their opened device ID.
Each group reports `f_bavail * f_frsize` as `available_bytes`, separately from
logical admission headroom. Shared roots do not multiply that value. Different
observations on one device use the minimum with an explicit observation; a
failed observation produces null and a fixed unavailable observation. These
values can change immediately and do not reserve storage. Packaging peak usage
includes both the private export snapshot and the bundle, alongside scratch,
source, logs and metadata. Logical quota is not a measurement or guarantee of
physical blocks; free disk establishes neither readiness nor admission capacity.

The store snapshot methods are private-evidence APIs for trusted maintenance:
`BuildInputStore.capacity_snapshot()` holds the existing stable input lock and
`BuildJobStore.capacity_snapshot()` copies validated state under the short job
lock. They acquire input then job state in the existing admission order and
refuse contention immediately. Exact existing witnesses are required; absent
locks, witnesses or ledgers are never initialized. The worker lock is optional
until a worker has used it; scratch, artifacts and logs need no invented locks.
Inspection does not take a worker/payload lease, expire jobs, invoke status,
sync, repair durability, submit, settle, prune or traverse payload trees. Payload
contents, packaging temporary trees and physical allocation remain unverified.
The result observes persisted metadata, not durable acknowledgement after a
lost sync/reply, a payload consistency audit, or a reservation/readiness decision.
Native crash/power-loss qualification remains outside the temporary-file tests.
