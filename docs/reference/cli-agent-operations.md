# Agent workflows and maintenance commands

[Documentation index](cli-setup-services.md)

`agent workspace` provides local task isolation for concurrent agents. `create`
places a dedicated `agent/TASK` branch below
`~/.local/share/basaltwater/worktrees`, leaving the primary checkout's files
untouched. `list` and `status` report branch, commit, and dirty state without
printing changed file names. The default base is the primary checkout's `HEAD`;
creation does not fetch remote refs or copy uncommitted changes. To start from
current remote main, fetch it first and pass `--base origin/main`. `remove`
accepts only a registered worktree below that managed root, refuses dirty or
untracked work, requires an explicit ownership record and a branch merged
into the primary checkout's current `HEAD`, and never has a
force mode. Use its `--dry-run` before cleanup.
`create --branch feature/NAME` supports named feature branches; `adopt` explicitly
registers an existing worktree. `group TASK --repository PATH` reports owned
matching worktrees across repeatable repository selections. Branch naming alone
does not authorize removal, including for legacy `agent/*` worktrees.

`status` also reports the primary repository, its current branch and commit,
and `merged_into_primary`. Normal merges preserve the ancestry required by
cleanup; cherry-pick and squash require later source-branch integration or
retaining the worktree. The parent owns integration and removal. See the
[complete workflow](../agents/agent-subagents.md).

`agent models init REPOSITORY` creates `.basaltwater/agent-models.json` in the
primary checkout and refuses to overwrite existing policies. `recommend
REPOSITORY TASK_CLASS --available JSON_FILE` intersects current session
capabilities with fresh verified catalog metadata and reviewed project results;
it never invokes a model or changes session settings. Astra requires
`--astra-reason JUSTIFICATION`. Recommendations exclude fast service, `max`,
and `ultra`, and return nonzero with a null selection when none is eligible.
`record REPOSITORY --task CLASS --model ID --effort EFFORT --outcome
accepted|reworked|failed --validation TEXT` stores the parent's outcome in
`.basaltwater/agent-model-results.json`. Optional `--cost-usd`, `--seconds`,
`--rework-minutes`, and `--attempts` retain observed cost, latency, rework, and
retry evidence; unavailable billing stays unknown. `report REPOSITORY` groups
history by task, evaluation version, exact model, and effort. All four commands
emit JSON and accept `--json`. New model IDs need project catalog edits, not
code changes. See [model choices and adaptation](../agents/agent-subagents.md).

`agent maintenance hold` creates or renews a private automatic-restart hold
for 8 hours by default. `--hours N` accepts 1–72 hours, `status` reports the
active or expired deadline, and `release` is idempotent. An invalid marker
fails safe and should be released and recreated. The optional `HOST USER` form
runs the same operation as the target account through managed SSH. A hold does
not override `--auto-restart-force-days`; once that deadline is reached, the
restart proceeds even if a session, hold, or recognized workload remains.

`agent tasks` operates on the current non-root account's shared task state.
`create TASK_JSON` defaults to an inactive draft; choose `--run-now` or
`--schedule` to activate it. `--request-key` deduplicates retried creation.
`start_at` in the settings schedules an explicit first run, including Once.
`update TASK_ID TASK_JSON` changes only supplied settings. `show` reports its
checkpoint and conversation/workspace bindings; `history --limit N` reads
retained history beyond the panel's latest 40 records. `receipt TASK_ID RUN_ID`
reads exact evidence, including for a removed task. `list --summary
--history-limit N` omits prompts/output and includes 0–40 recent run summaries.
`notify TASK_ID MESSAGE
--key KEY` wakes enabled work with deduplicated event evidence. `worker` runs
the independent foreground scheduler; `--once` handles currently due work.
`mcp` exposes read-only stdio tools, with explicit `--allow-write` for task
controls. All inspection and lifecycle commands emit JSON and accept `--json`.
See [persistent tasks](../agents/agent-tasks.md) for settings, budgets, managed workspaces,
recovery and stable-version T3 integration limits.

`agent memory` stores account-local project notes, todos, and references to
existing private secret files outside Git. Every action accepts `--project`
(default `.`); Git worktrees share their common Git directory's scope.
`list --limit N --query TEXT` returns bounded metadata without bodies or secret
paths. `get NAME --kind note|todo|secret` reads one selected record; secret mode
returns a checked path without reading its contents. `put NAME --file PATH|-`
creates only by default; `--if-revision N` is required to update existing entries.
Secret mode registers an existing private file and rejects stdin values.
`remove` and `reopen` require observed revisions. Todo `claim --owner LABEL`
returns a token; `renew` and `finish` require its live `--claim-token`.
All commands emit JSON and accept `--json`. These records do not schedule work
or grant permissions. See [local project memory](../agents/agent-memory.md) for privacy,
lease expiry, atomic writes, limits, and recovery.

`agent support-bundle` composes a stable local JSON snapshot from the agent,
T3 Code, browser, host, and maintenance diagnostics. It includes aggregate T3
log sizes and counts, not log text. It also records the Basaltwater project
version, validated source commit, and dirty-state boolean when available, while
omitting the source branch. Browser diagnostics retain the safe-coordinate,
private-evidence, WebGL-settle, and launcher-security states so a stale or
unsafe managed launcher remains identifiable in a support artifact. Tool paths,
home paths, Git identity, repository contents and status names, prompts,
sessions, and credential contents are omitted. Without `--output` it prints
JSON; an output path must be a new file below the current user's home and is
written with mode `0600`. Browser configuration is inventoried without starting
Chromium; add `--browser-smoke` when it is relevant to exercise an explicitly
installed Playwright fallback for the report.
Manifest and support share the [installation provenance contract](../agents/agent-environment.md):
recorded `setup_snapshot` and observed `running_source` remain separate. A
checkout commit does not create a setup receipt. Add `--container-storage` for
aggregate engine/storage data; graph-root paths, image names, labels, registry
URLs and cgroup paths remain excluded. Graphics probes are always separate.

`agent update` deliberately updates installed user-managed terminal agents; it
is never run by an automatic host timer. With no `--tool` selection, it skips
absent or externally managed agents. An explicit `--tool` selection still
reports a missing or externally managed executable as an error. The command
uses each vendor's supported path: OpenAI's standalone installer for Codex,
`claude update`, and `opencode upgrade`. It refuses executables resolved
outside the current user's home so package-manager installations remain under
their package manager. It must be run as the account that owns that home and
reports the exact executable path being updated; `/home/developer/.local/bin/codex` and
`/home/agent/.local/bin/codex` are separate installations.
Before changing a tool it checks `--version` and `--help`, retains the previous
executable, writes an atomic `in_progress` record, and repeats both checks after
the vendor updater exits. A changed or unusable executable is rolled back when
the update fails. Non-secret results are stored with mode `0600` in
`~/.local/state/basaltwater/agent-tools.json`; one prior executable per tool is
retained in the adjacent `agent-backups` directory. Codex installer bytes are
downloaded before execution with a size limit and their observed SHA-256 is
recorded, but upstream does not publish a pinned digest through this installer
contract, so the hash is audit evidence rather than independent publisher
verification.

After a non-dry-run update, the command also checks the selected terminal
tools and host readiness, adds T3 Code readiness when that managed service is
present, and saves the redacted readiness record described above. An unhealthy
post-update result or failure to persist it makes the update command exit
nonzero even when the vendor updater itself succeeded. Inspect the evidence
with `basaltw agent doctor --last-record`; update JSON output retains its
existing per-tool result-array contract. The setup-internal invocation uses
`--tools-only-readiness` so an unrelated pre-existing T3 or host condition does
not make a successful terminal-agent update fail. The complete host/T3 result is
still stored and a warning points to the follow-up check; run the default
command or `basaltw agent doctor --capability host --capability t3code` for
the full audit.

The optional `HOST USER` form runs that update as the target VM user. Run
`basaltw agent update HOST USER --dry-run`, then repeat it without
`--dry-run` to apply. It does not use sudo or update another user's
installation. Remote doctor and update use the workspace `known_hosts` file
with strict host-key checking, like other managed SSH operations.

Setup reruns still skip the official installer when a selected command is
already available, then reconcile selected user-installed terminal agents
through the verified updater. Executables owned outside the target user's home
are left to their package manager. Use `basaltw agent update` for an
agent-only update outside setup or when you want to select tools explicitly.
Codex-enabled setup reruns also perform one bounded authentication freshness
check before the updater, giving renewable credentials a chance to recover on
an infrequently started VM. Setup verifies the selected terminal tools after
the update without making an unrelated pre-existing T3 or host condition block
that update; use the composite doctor command above when those broader
conditions need to be audited.
For example, when an operator is logged in as another account:

```bash
sudo -u agent -H sh -lc \
  'cd /home/agent && basaltw agent update --tool codex'
```

The updater resets the working directory and user-scoped environment before
calling the vendor installer, so the invoking account's home and PATH do not
leak into the update.

For a deliberately chosen vendor update outside Basaltwater, verify the
installed tool's supported update mechanism and use its owning account and
home directory. The managed command above supplies those settings and adds
preflight checks and rollback.

Credential rotation does not rebuild the VM or overwrite repositories:

```bash
basaltw agent auth set 10.0.0.10 agent --tool gh --file /run/secrets/gh-hosts.yml
basaltw agent auth login 10.0.0.10 agent --open-browser
basaltw agent auth status 10.0.0.10 agent --json
python3 basaltwater.py agent auth login 10.0.0.10 agent --method api-key
```

`auth set` accepts a controller-local file or interactive source selection.
Only `--tool gh` accepts `--active`; it can retrieve a keyring-backed
token through the controller's `gh auth token`. Codex, Claude Code, and
OpenCode active credential copying has been removed. GitHub
input is filtered to `github.com` and is installed with an atomic mode-`0600`
replacement. Status reports installation, credential file metadata, safe Codex
refresh and cached-token dates, the GitHub authentication check, and the names
of selected Claude environment overrides. Credential contents and override
values stay private. Normal setup preserves existing credentials except for a
safe refresh of known-outdated Codex auth. Use `auth login` for Codex
reauthorization, [native Claude sign-in](../agents/agent-authentication.md#claude-code-attended-testing)
for Claude subscriptions, and `auth set` for authorized explicit file imports.
`auth login` defaults to a target-owned ChatGPT subscription session. The
controller prints a device URL/code; `--open-browser` optionally opens the URL.
It needs no local Codex installation. Explicit `--method api-key` accepts a
hidden prompt, `--api-key-file PATH`, or `--api-key-stdin` and warns that API
usage is separately billed rather than covered by the ChatGPT subscription.
Keys never enter argv or setup history. Credential pulling is removed; see
[Agent authentication](../agents/agent-authentication.md#authorize-codex-on-a-target).

The normal restart policy defers for active login sessions, coding agents,
build and Git processes, terminal multiplexers, maintenance holds, and
processes running in a Basaltwater-managed agent worktree. It can still force
a reboot after seven days by default. For a host running long unattended agent
tasks, use both `--no-auto-restart` and `--auto-restart-force-days 0` only if
automatic restarts must be fully disabled, then manage pending security
reboots explicitly.
