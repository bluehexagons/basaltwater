# Storage integrity review

The implemented recovery commands are documented in [Scrub recovery](SCRUB_RECOVERY.md).
This review separates the fixes shipped with them from proposed follow-up work.

## Fixed in this change

- Findings persist across runs and distinguish damage, unavailable parity, missing
  files, concurrent changes, and execution failures.
- Existing parity survives newer source timestamps and missing source files.
- Repairs and restores work on private copies and verify before publication;
  original data and parity are retained. Baseline acceptance is explicit.
- Daily maintenance keeps unresolved damage visible without re-verifying it.
- Scheduled syncs block known suspect files in either affected tree and reject
  destinations overlapping a configured parity database, including aliases.
- Each completed operation checkpoints its schedule before the next job starts.

## Remaining gaps and automation priorities

| Priority | Gap | Proposed next step |
| --- | --- | --- |
| High | Sync runs before scrub and only knows previously recorded findings. Undetected corruption can still propagate; mirrors propagate deletions. | Add opt-in snapshot/version retention and verify-before-sync policies for critical roots. Test restore against the original baseline before publication. |
| High | Standalone scripts and custom inline setup calls can bypass the orchestrator's lock and integrity guards. | Consolidate all mutation entry points behind one runner, including setup. Preserve existing setup error propagation and add overlap tests. |
| High | Initial parity creation writes into the active database; interruption can leave an incomplete set. Creation has no subprocess deadline. | Stage and verify every new set, bound execution/output, detect source changes, and publish with a recoverable manifest. |
| High | Accept publishes multiple parity files; interruption is recoverable but not atomic. | Use generation directories and one atomic active-generation pointer, with migration for existing databases. Reconcile interrupted manifests at startup. |
| High | PAR2 filename conventions can be ambiguous for source names ending in `.par2` or resembling volume names. | Introduce unambiguous per-file identities in the generation layout; reject collisions before migration or writes. |
| Medium | Incomplete scans restart from the beginning, and operational failures remain due hourly. | Persist attempt state and bounded retry backoff separately from completed cadence. Resume only when file/parity identities and configuration still match. |
| Medium | Notifications summarize the latest run, which is not the same as persistent data health. | Separate per-job execution events from per-root integrity incidents. Notify on finding transitions, with explicit reminders and resolution events. |
| Medium | Recovery copies grow indefinitely; intentional deletions remain open. | Add disk-space preflight, retention reporting, and an explicit archive/retire workflow with a preview. Never automatically accept or delete unresolved evidence. |
| Medium | Large trees repeatedly load/write the findings JSON; recovery trees are inventoried too. | Introduce a transaction-scoped report cache or indexed store and exclude recovery payloads from inventory traversal. Benchmark on realistic NAS trees. |
| Medium | Live writers can invalidate checks and hard-linked names are not recovered as a group. | Prefer filesystem snapshots or a configured quiesce hook; detect multiple links and require an explicit policy. Test ownership, ACLs, and extended attributes on supported filesystems. |

These are follow-up proposals, not enabled capabilities. In particular, no policy
automatically treats a newer timestamp as proof that content is healthy.

## Web panel integration

The panel already exposes `storage-ops.service` in Scheduled jobs and Service
diagnostics. Those screens show timer/process state and readable journal entries;
they cannot establish whether all protected files are healthy. The panel runs
without root, while findings and recovery copies are private. Keep that boundary.

The recommended first increment is a **read-only Storage page** backed by a small,
root-produced snapshot, following the existing audit snapshot exporter pattern:

1. Export only configured job identifiers, scan timestamps, completion state,
   open counts by category, a bounded list of affected paths, and recommended
   actions. Omit file contents, recovery copies, raw tool output, and credentials.
2. Publish atomically with access limited to the panel account. Version the schema,
   cap its size, and validate it on read. Export after scans and CLI remediation;
   a periodic exporter can refresh status without performing storage work.
3. Show execution and integrity as separate statuses. Distinguish never scanned,
   stale/unavailable snapshot, running, incomplete, completed with findings, and
   verified at a recorded time. An unavailable report must never show green.
4. Add escaped file details, first/last detection, scheduled cadence, log links,
   and correctly quoted CLI examples. Reading or refreshing the page must never
   launch verification or repair. Show truncation explicitly for large reports.

A second increment can add **queued targeted verification**, followed by repair
and restore. Requests should refer to configured job/file identities, be handled
by an allowlisted privileged worker under the existing storage lock, and carry
an idempotency key and expected finding/file revision. Revalidate mounts and
paths when the worker executes. Record requester, request time, result, and
retained recovery location. Reject duplicate or stale requests and provide
bounded status polling without running commands in HTTP request handlers.

Mutation endpoints need authenticated authorization and CSRF protection. Show
the exact file and action in a confirmation view; baseline acceptance deserves
a separate explicit acknowledgement that it replaces the historical baseline.
Do not grant the web process arbitrary sudo, shell commands, or filesystem paths.
Keep terminal remediation available even when the panel or its snapshot is down.

Validation for those increments should cover stale/malformed/oversized snapshots,
HTML escaping, denied unauthorized/CSRF requests, duplicate submissions, busy
locks, changed files after confirmation, interrupted workers, and successful
resolution becoming visible without another full scrub.
