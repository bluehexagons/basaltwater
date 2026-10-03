# Upcoming stable upgrade to T3 orchestration V2

Reviewed on 2026-10-03 against `0.0.46-nightly.20261003.2623`.
Basaltwater targets the stable npm `latest` channel, which published `0.0.45`
at review time. The nightly was tested in isolation to prepare for the next
stable release. Apply this procedure when V2 reaches stable, after checking
that release's migration contract. Use the stable client's **Update server**
action or its matching stable version in [the host-side update procedure](updates.md).
Upgrades are forward-only; this preparation does not enable nightly deployment.

## Before the first V2 launch

Finish active agent turns and terminal commands. The update restarts the
server and cannot preserve V1 live provider sessions. Check host disk headroom
with `basaltw agent doctor --capability host`: V2 needs space for a database
copy, migrations, and a private recovery copy.

Once the operator has scheduled the interruption, stop the managed service
and copy the complete `~/.t3/userdata` directory to private storage outside
T3's data directory. Keep its permissions restricted: the copy contains
conversations and authentication state. Include SQLite sidecars and
attachments; copying only `state.sqlite` while the server runs is not a
consistent recovery snapshot. A custom T3 home uses `<home>/userdata`.
Then perform the upstream update. Do not point any server at the recovery
copy or include its contents in a support bundle.

## What the upstream migration does

The first V2 launch snapshots `userdata/state.sqlite` into
`userdata/statev2.sqlite`, then migrates that copy. Existing threads appear
automatically; full transcripts import as needed or in the background.
Subsequent launches use the existing V2 database. Basaltwater does not move
these files, edit SQL, reset migration IDs, or run a separate import command.
Upstream also reconciles the migration numbering used by earlier V2 previews;
leave that bookkeeping to T3. Keep both databases and their sidecars intact.
Cache maintenance must not treat either database as disposable data.

Titles, projects, provider/model choices, permission and interaction modes,
branches/worktrees, archive/settlement/snooze/pin state, linked PRs, and supported
user/assistant messages and attachments carry over. Old live provider sessions,
run records, checkpoints/diffs, tool activity, approvals, and proposed plans
are not recreated. Settings, attachments, and workspaces remain shared.

## Continue and verify

The client/server orchestration wire protocol changes from 1 to 2. Update the
side named by T3's version-mismatch notice and reconnect. This is separate
from service-state protocol 3, which remains supported by Basaltwater.
Do not try to repair a protocol mismatch by deleting state or resetting pairing.

Run `basaltw agent doctor --capability t3code --capability host --record`, then
open representative older threads in the matching client and verify their
transcripts and project/worktree choices. Doctor readiness does not verify
the client protocol or every imported conversation.

The first new message in a migrated thread starts a fresh provider session
with a budgeted conversation handoff. Read the recent transcript and repeat
important earlier requirements before continuing. When T3 exposes thread
history retrieval, use it to recover omitted saved text instead of assuming
the handoff includes the whole conversation. Keep the recovery copy unchanged
if history is missing; inspect only that copy using SQLite read-only mode.

Upstream references: [thread migration](https://github.com/pingdotgg/t3code/blob/v0.0.46-nightly.20261003.2623/docs/user/thread-migration.md),
[portable handoffs](https://github.com/pingdotgg/t3code/blob/v0.0.46-nightly.20261003.2623/docs/user/portable-handoffs.md).
