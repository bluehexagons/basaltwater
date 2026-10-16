# Stable T3 orchestration V2 upgrade

Reviewed against stable `0.1.0`, published on 2026-10-15. Basaltwater setup
uses npm's stable `t3@latest` channel, which now includes orchestration V2.
Use the matching stable client's update action or the
[host-side update procedure](updates.md). Do not install a nightly or preview
to perform this upgrade.

## Before the first V2 launch

Finish active agent turns and terminal commands. Updating restarts the server
and interrupts connected clients. V1 live provider sessions do not carry over.
Check disk headroom with `basaltw agent doctor --capability host`: the upgrade
needs space for a database copy, migrations, and a private recovery copy.

After arranging the interruption, stop the managed service and copy the
complete `~/.t3/userdata` directory to private storage outside T3's data
directory. Keep permissions restricted: it contains conversations and
authentication state. Include SQLite sidecars and attachments; copying only
`state.sqlite` while the server runs is not a consistent recovery snapshot.
A custom `--base-dir` uses `<base-dir>/userdata`. Then perform the update.
Do not run a T3 server against the recovery copy or put it in a support bundle.

## Database migration and startup

The first V2 launch snapshots `userdata/state.sqlite` into
`userdata/statev2.sqlite` and migrates the copy. Existing threads appear
automatically; full transcripts import as needed or in the background. Later
launches use the existing V2 database. Changes made in either database after
the copy do not synchronize. Settings, attachments, and workspaces stay shared.

Basaltwater does not move these databases, edit SQL or migration IDs, or run a
separate import command. Keep both databases and their sidecars intact. Leave
preview-migration reconciliation to upstream. Do not downgrade or replace an
existing V2 database with its V1 source.

Database copying, migrations, and SQLite optimization can take minutes.
Basaltwater's setup readiness window is 30 minutes, matching upstream's launcher
preparation allowance, with progress every 30 seconds. A systemd failure is
reported earlier. Inspect `~/.t3/userdata/logs/boot-service.log` and
`journalctl --user -u t3code.service` before interrupting or retrying a slow
startup. An older Basaltwater readiness timeout does not prove migration failed.

Titles, projects, provider/model choices, permission and interaction modes,
branches/worktrees, archive/settlement/snooze/pin state, linked PRs, and supported
user/assistant messages and attachments carry over. Old live provider sessions,
run records, checkpoints/diffs, tool activity, approvals, and proposed plans
are not recreated.

## Update the client and verify

The client/server orchestration protocol changes from 1 to 2. A `0.0.45` client
cannot communicate with the new server; update it to stable `0.1.0` or a
compatible later release and reconnect. This is separate from service-state
protocol 3, which Basaltwater already supports. Do not delete state or reset
pairing to fix a protocol mismatch. The V2 desktop app uses a separate browser
profile, so websites opened inside it may need a fresh sign-in.

Run `basaltw agent doctor --capability t3code --capability host --record`, then
open representative older threads in the matching client and verify their
transcripts and project/worktree choices. Doctor readiness does not verify
client compatibility or every imported conversation.

The first new message in a migrated thread starts a fresh provider session
with a budgeted conversation handoff. Read the recent transcript and repeat
important earlier requirements. When T3 exposes thread-history reading tools,
use them to retrieve omitted saved text. Keep the recovery copy unchanged if
history is missing; inspect only that copy using SQLite read-only mode.

Upstream references: [stable thread migration](https://github.com/pingdotgg/t3code/blob/v0.1.0/docs/user/thread-migration.md),
[portable handoffs](https://github.com/pingdotgg/t3code/blob/v0.1.0/docs/user/portable-handoffs.md),
[service launcher](https://github.com/pingdotgg/t3code/blob/v0.1.0/apps/server/src/serviceLauncher.ts).
