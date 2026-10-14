# Local project memory

`basaltw agent memory` keeps selected notes, todos, and references to private
credential files for cooperating agents on one Unix account. It needs no daemon,
MCP connection, database package, or open T3 client. It complements
[scheduled tasks](AGENT_TASKS.md) and [agent messages](MAINTENANCE_AGENTS.md);
it does not schedule or execute work.

Memory lives outside the checkout under
`~/.local/state/basaltwater/project-memory/SCOPE/records.json`, with private
directories and mode-`0600` files. Git worktrees and subdirectories share the
scope derived from their common Git directory. Non-Git projects use the resolved
directory. Separate clones or moved repositories have separate scopes. Use
`--project PATH` from any working directory to access a particular project.
This is same-account cooperation, not isolation between agents on that account.
The CLI also works in the configured Linux account on CachyOS and Ubuntu WSL;
native Windows agents have a separate account and cannot use that Linux store.

## Read only the relevant context

```bash
basaltw agent memory scope --project ~/repos/app --json
basaltw agent memory list --project ~/repos/app --limit 20 --json
basaltw agent memory list --project ~/repos/app --query deployment --json
basaltw agent memory get deployment-notes --project ~/repos/app --json
```

`scope`, `list`, and `get` do not create storage. List results contain metadata,
never note bodies, claim tokens, or secret file paths. The default lists notes
and todos; `--kind secret` explicitly lists secret-reference metadata. `--query`
matches names and non-secret text while still returning only metadata.
The default list limit is 20 and the maximum is 100; `total` and `truncated`
describe omitted matches. Read individual named entries as needed instead of
injecting all local memory into every prompt.

Keep notes short: decisions, observed source revisions, verified endpoints,
evidence paths, unfinished work, and the next useful check. Treat them as
potentially stale evidence. Recheck the relevant repository/runtime and current
authorization before acting. Notes and todo text never expand task permissions.
Keep secret values out of these fields.

## Create and update notes or todos

Write non-secret text into a file, then:

```bash
basaltw agent memory put deployment-notes --file handoff.txt --project ~/repos/app --json
basaltw agent memory put verify-rollout --kind todo --file todo.txt --project ~/repos/app --json
```

Use `--file -` for text on stdin. Text is bounded to 16 KiB of printable UTF-8.
Each project retains at most 256 entries and 4 MiB of serialized state. Nothing
is automatically evicted. Names use 1–64 letters, digits, dots, underscores, or
hyphens, starting with a letter or digit. Notes, todos, and secret references
have separate name spaces.

Creation uses `--if-revision 0`, the default. Updating an existing entry requires
its current revision from `get` or `list`:

```bash
basaltw agent memory put deployment-notes --file handoff.txt --if-revision 1 --project ~/repos/app --json
basaltw agent memory remove deployment-notes --if-revision 2 --project ~/repos/app --json
```

These numbers are examples; use the observed revision. A stale revision returns
`ok: false` and `issue: conflict` without overwriting the current record. Writes
hold a short, bounded project lock and atomically replace the index. A busy lock
returns `issue: busy`. If a result is uncertain, inspect the entry before
retrying. Revisions detect conflicts; they are not retained edit history.
Revisions are allocated across the project and never reused, even after deleting
every entry. They may skip numbers for an individual record; always read the
current revision instead of calculating it. Recreating a name cannot make an
old observation valid for its replacement.

## Claim a todo before shared work

```bash
basaltw agent memory claim verify-rollout --owner codex-rollout-1 --lease-seconds 900 --project ~/repos/app --json
basaltw agent memory renew verify-rollout --claim-token CLAIM_TOKEN --lease-seconds 900 --project ~/repos/app --json
basaltw agent memory finish verify-rollout --claim-token CLAIM_TOKEN --status done --project ~/repos/app --json
```

Use a unique cooperating-agent label and retain the returned `claim_token` in
the task context. It is a coordination handle, not an authenticated identity.
Leases last 30–86400 seconds, default 900. Only one live claim is allowed.
Renew it before expiry; finishing or renewing requires the current live token.
An expired claim can be acquired by another worker, and the old token cannot
finish that replacement's work. Get/list omit tokens and show claim owner and
expiry as Unix seconds.

Use `finish --status open` to release work, or `--status blocked` to retain a
blocked finding while releasing ownership. `reopen NAME --if-revision N` moves
a done or blocked todo back to open. Live claims prevent editing or deleting
their assignment. Finish leaves the text and status available for later review.
Write blockers and evidence in a separate note when an assignment is claimed.

Claims do not stop processes or isolate checkout files. Continue using managed
worktrees, explicit file ownership, and reviewed handoffs for concurrent editors.
Inspect status after a lost acknowledgment; do not repeat submissions blindly.

## Register a secret location without copying its value

```bash
basaltw agent memory put app-api --kind secret --file ~/.config/app/api-token --project ~/repos/app --json
basaltw agent memory get app-api --kind secret --project ~/repos/app --json
basaltw agent memory list --kind secret --project ~/repos/app --json
basaltw agent memory remove app-api --kind secret --if-revision 1 --project ~/repos/app --json
```

The source must already be a private, singly linked regular file owned by this
account below its home. Symlinked or writable-by-others parent directories are
rejected. `get` rechecks the current path's safety and returns its path; it never
reads, copies, or prints credential contents. Stdin secret values are rejected.
Removing a reference preserves the credential file. Rotating that file through
its existing credential workflow keeps the reference current when ownership and
permissions remain safe.

Use the application's secret manager or [authentication workflow](AGENT_AUTHENTICATION.md)
to create, use, and rotate credentials. Pass a referenced file directly to the
authorized consumer where supported; do not print it into prompts, logs, command
arguments, or Git. Memory is a location registry, not an encrypted vault.

## Availability and recovery

All commands emit JSON; `--json` enables formatted output. Failures return
nonzero. Invalid or oversized state, unsafe links, ownership, or permissions
fail without resetting or deleting existing records. Preserve the index for
inspection; do not remove its stable lock file to bypass another writer.

The CLI is included in updated Basaltwater source without a setup feature flag.
Older installed CLIs need the normal Basaltwater source refresh. From a checkout,
development checks can use `python3 basaltwater.py agent memory ...` under the
same account and execution permissions. Setup refreshes preserve this user state.
Version-1 indexes remain readable and migrate to version 2 on the next successful
write, preserving existing record revisions. Reading, a conflict, or a failed
write does not rewrite legacy state. After migration, use an updated CLI;
older versions reject the new format rather than reset it.
