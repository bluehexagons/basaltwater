# Skill access by session

[Documentation index](agent-skills.md)

## Access from different sessions

Keep discovery separate from procedural context. The catalog advertises skill
names and descriptions; an installed base skill does not require reading its
whole body on every request. Basaltwater entrypoints contain task routing and
essential boundaries. Read only the linked workflow needed for the current
operation, then load more detail when a specific step requires it. Do not
preload every reference or run an operations checklist for ordinary code edits.

Operations, tasks, workspaces, triage, browser, desktop, and gateway skills use
this layout. Discover a Basaltwater capability with a scoped manifest or doctor
when the task needs it. Use the general `basaltw agent context` report when
you need a host inventory or help finding a capability.

In a T3 session, follow its injected instructions and live schemas for native
thread, PR, schedule, and preview work. Basaltwater skills describe host
operations and the boundaries between T3 work and independent jobs.

The workspace skill also routes long-running/shared projects to
[local project memory](agent-memory.md). Read a bounded index and selected
records when relevant; memory is not automatically injected into prompts.
Secret entries register private file locations without reading their values.

| Launch context | Catalog used |
| --- | --- |
| Codex CLI, including `codex exec` | The provider account's `~/.agents/skills`, plus repository and other Codex-supported scopes |
| T3 Code on Debian or CachyOS | The account running the provider on the selected connection, using the same provider catalog as terminal sessions |
| Basaltwater panel prompt task | The panel account's `~/.agents/skills`; the runner fixes `HOME` and `CODEX_HOME` and starts a new terminal process |
| T3 Code on Windows connected to Ubuntu WSL | The selected distribution's Linux account/catalog |
| Subagent | Its actual account, working directory, and provider configuration; pass relevant skill paths when assigning a fresh worker |

Skill access has three separate checks: the files exist and are readable, the
provider advertises/enables the skill, and the agent reads it for this task.
Basaltwater handles installation. It does not replace the provider's discovery
or guarantee that every skill is loaded on every turn. Codex custom agents
inherit `skills.config` and MCP settings unless overridden; fresh conversation
context and thread-scoped tools are separate. See the
[official OpenAI documentation](https://learn.chatgpt.com/docs/build-skills) and
[subagent configuration](https://learn.chatgpt.com/docs/agent-configuration/subagents).

Run `basaltw agent manifest /absolute/project --capability skills --json` as the provider account to
list known Basaltwater entrypoints in both user catalogs. `skills` reports
`current`, `different`, `incomplete`, `unsafe`, or `personal` against the running
Basaltwater installation's source. The comparison includes file contents and
permissions, including script executability. Existing ownership inventories are
validated and compared with the source resource digests; obsolete recorded
resources or stale digests report `different`, while malformed or unsafe
inventories report `unsafe`. Legacy bundles without an inventory are compared
directly with source files. `different` can also mean an older bundle or local
edits; setup preserves edited supporting resources rather than overwriting them.
This is an inventory of known Basaltwater names, not personal, plugin, repository,
or system skills. `session_access` stays `unverified`; it does not read provider
configuration or credentials. Use the active skill list (`/skills` in interactive
Codex) to verify discovery, and respect disabled skills. Large Codex catalogs
can omit metadata from initial context; pass the relevant skill name and path.

For an exact installed instruction footprint, use
`basaltw agent manifest . --capability skills --json`. Each skill's
`instruction_footprint` separates `entrypoint` (`SKILL.md`) from `references`
(known Markdown files under `references/`, including nested files and `.MD`).
Each group reports `measured_files`, `utf8_bytes`, and `code_points`. Counts
use the installed bytes decoded strictly as UTF-8, without newline or Unicode
normalization; a multibyte character or combining sequence can make the two
counts differ. Scripts, assets, agent configuration, ownership inventories,
and unrelated personal files are excluded. No reference contents or individual
reference paths are returned.

Footprint and group `status` values are `complete`, `incomplete`, or `unmeasured`,
independent of source freshness: a safely readable locally edited bundle can
have a complete footprint and `different` freshness. Personal or unsafe bundles
are unmeasured. Missing instruction files, invalid UTF-8, or obsolete recorded
references outside the current known source reads make the relevant measurement incomplete.
Missing entrypoints leave references unmeasured. No additional files are opened
to fill those gaps. Groups with no measured files use null counts unless a
complete measurement establishes that the group has no reference files; that
case reports zero. Empty catalogs are unmeasured, not complete zero inventories.

Each catalog and the overall `skills` object also expose `instruction_footprint`
with sums of measured files only. Partial sums remain incomplete; absent optional
catalogs do not make the overall sum incomplete. Copies installed in both catalogs
are counted separately. These are on-disk instruction sizes, not token counts,
metadata discovery costs, or evidence of context loaded by an actual session.
There is no tokenizer dependency; `session_access` remains `unverified`.

To compare prompt impact across skill revisions, use fresh sessions with the
same provider, account, model, options, project instructions, and task prompts.
Record the advertised Basaltwater skill names, entrypoints and references read,
and first-turn input tokens when the provider exposes them. Exercise an ordinary
edit, T3 preview with an allowed fallback, a native PR watch, and terminal-only
host work. Check that each keeps its intended execution path before comparing
the counts. Use the manifest to verify the on-disk revision separately.

Choose platform guidance first, then session-specific references. Keep a single
capability-selected catalog per account rather than replacing it whenever an
agent launches. T3 preview, native desktop grants, and unattended permissions
depend on the active session. The shared subagent skill ships
[`references/session-context.md`](../../common/agent_skills/basaltwater-subagents/references/session-context.md)
with a worker assignment example and guidance for each context.

Windows `server_wsl` installs `basaltw`, `basaltwater-wsl-workspace`,
`basaltwater-project-development`, `basaltwater-project-adoption`,
`basaltwater-frontend-design`, and `basaltwater-subagents` when a supported
coding agent is selected. These are Linux-user skills; native Windows providers
have separate homes and are not provisioned by this profile. Native PowerShell
artifact jobs execute committed scripts rather than Codex prompts. WSL does not
receive VM maintenance, panel, XRDP, or managed browser skills. See
[Windows/WSL](../workstations/windows-wsl.md).
