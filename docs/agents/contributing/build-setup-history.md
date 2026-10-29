# Completed controller setup history

`lib.build_setup_history` explicitly archives one completed controller attempt
without deleting its sources, artifacts or receipts. This frees an active
attempt slot under the 32-attempt limit; it does **not** free disk space or
builder/target record slots. There is no automatic collection or remote action.

After the final profile reports success, controller setup writes a private,
durable `profile-success.json` in the attempt before clearing its setup guard.
It contains the exact setup operation and context, including prepared-input
digest and acknowledged release identities. Failure to persist it retains the
setup guard even though the profile may already have succeeded remotely.

Inspect the original host journal and attempt ID from the completed setup:

```bash
python3 -B -m lib.build_setup_history \
  --journal /absolute/workspace/build-setups/HOST-HASH/setup.json \
  --attempt ATTEMPT_ID
```

Review the bounded JSON identities and copy its `revision` into an explicit
archive request:

```bash
python3 -B -m lib.build_setup_history \
  --journal /absolute/workspace/build-setups/HOST-HASH/setup.json \
  --attempt ATTEMPT_ID --archive --expected-revision SHA256
```

The startup `-B` flag prevents import-time bytecode writes. Inspection creates
no files, locks or directories. Read access may update atime. Both operations
require existing private evidence and idle setup/delivery locks. Archive discovers
the normalized controller resource (case-insensitive host without a final dot),
then revalidates under resource, setup and delivery
ownership in that order before mutation. Any unfinished setup guard,
even for another attempt, or unfinished delivery guard refuses maintenance.
Missing locks, unsafe ancestry, links, foreign ownership, non-private metadata,
conflicting destination state, malformed or inconsistent evidence, and stale
revisions refuse. No guard is cleared or repaired.

Validation shares the offline recovery inspector's protected descriptor graph,
strict parsers and request/application checks. It checks at most 16 applications,
32 MiB prepared metadata, 1 MiB per operation document and a 64 KiB delivery
receipt. Final setup success, prepared digest, successful completed delivery,
per-application acknowledgements and expected releases must agree. It reads no
source, bundle, extracted payload, credentials or log. Payload contents remain
`unverified` and remote state remains `unobserved`: a historical success never
proves current target state or authorizes deployment replay.

Archive renames the owned attempt directory to `archived-ATTEMPT_ID` in the same
host directory and syncs publication. It preserves file bytes, modes, inodes,
request IDs, idempotency keys and release generations. Only that explicit name
form is excluded from the active attempt count. Archived data still consumes
disk and needs ordinary backup/storage planning. No archive deletion or offload
command is provided.

Original absolute paths inside metadata are preserved as historical identities;
they are not rewritten into replayable paths. Inspect an archive using the same
original journal path and attempt ID. An identical archive request is safe to
repeat after a lost reply or directory-sync failure: it validates the same
revision and finishes sync without replacing evidence or deploying anything.
If both active and archive names exist, maintenance refuses instead of choosing
or overwriting one. Cooperating controller operations are excluded; this does
not defend against an uncoordinated privileged or same-UID writer.

Older attempts without a success receipt qualify only when the host's still
retained last result proves that exact attempt succeeded. Archiving first saves
that proof privately in the attempt before moving it, so later result rotation
does not erase the proof. Older unrelated, failed or unproven attempts remain
untouched and require operator reconciliation. There is no guessed completion.

The API `maintain_completed_setup(journal_path, attempt_id, *,
expected_revision=None)` inspects by default and archives only with an exact
revision. Its report contains identities, revision and archive status, without
paths, commands or environment values. Fixed refusal codes are `busy`,
`unfinished_operation`, `revision_changed`, `invalid_evidence` or `unavailable`.
The CLI exits 0 for a report and 2 for a bounded JSON refusal; invalid arguments
and missing `-B` have their own fixed codes.

Selected qualification archived the real six-application controller attempt on
its existing filesystem. All 1,814 preexisting files retained content hashes,
inodes and modes; the only new file was the private legacy success proof. The
host result stayed unchanged, active slots fell from five to four, and an exact
archive retry passed. No deployment transport ran and both lab guests stayed
stopped. This does not qualify power loss or remote history retirement.
