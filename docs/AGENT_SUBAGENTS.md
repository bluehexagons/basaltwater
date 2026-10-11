# Subagent workflows and model choices

Debian agent-enabled setups, CachyOS workstations, and Ubuntu WSL coding profiles
install the shared `basaltwater-subagents` skill with local lifecycle,
model-selection, and session-context references.
Their workspace skills retain each platform's account and setup boundaries.
Detailed guidance needs neither a Basaltwater checkout nor a GitHub connection.

Fresh workers need the relevant project instructions and absolute skill paths
in their assignment. Their enabled skills and tools depend on the provider,
account, and working directory; they may not inherit the parent's loaded skill
bodies or T3 browser attachment. The shipped
[session-context guide](../common/agent_skills/basaltwater-subagents/references/session-context.md)
explains terminal, T3, WSL, and unattended task boundaries and includes a bounded
worker assignment. Use `basaltw agent manifest /absolute/project --json` for
local catalog inventory, then check discovery in the active agent session.

Use the active harness's messaging tools for authorized parent/worker
coordination. Check whether a message wakes an idle worker or needs a separate
follow-up task. Independent terminal processes and T3 threads do not share a
Basaltwater inbox; panel tasks cannot receive follow-up input during a run.
The session-context guide explains these boundaries for all three platforms.

Use delegation for independent tasks with clear acceptance checks. User guides,
historical records, and an independent review can split well; small changes
touching the same configuration usually benefit from one owner. Account for
context transfer, parent review, integration, retries, and model cost when
deciding whether a worker will save time.

Editing agents share a filesystem and need separate managed worktrees. A
read-only reviewer can inspect the primary checkout; builds, formatters, and
other checks that write files need isolation. Assign file boundaries explicitly,
including a single owner for shared configuration and lockfiles. The parent owns
integration, combined validation, project model records, and cleanup.

Managed creation records private ownership and defaults to `agent/TASK`;
`workspace create REPOSITORY TASK --branch feature/NAME` selects a new feature
branch instead. Existing secondary worktrees below the managed root, including
older `agent/*` worktrees, need explicit `workspace adopt WORKTREE --task TASK`.
Adoption changes ownership metadata only and preserves dirty files. Supply
`--root PATH` for custom roots. Cleanup requires matching ownership, clean
files, and merge ancestry for both task and feature branches. JSON refusals
include `ok: false`, a stable `issue`, and `remediation`.

`workspace group TASK REPOSITORY RELATED_REPOSITORY --json` provides a read-only
view of matching owned branches and paths across related projects, plus missing
repositories. Use the same task label when creating or adopting each worktree;
integration remains explicit in each repository.

Choose normal branch merges before starting. Managed cleanup requires task
commits to be ancestors of the primary checkout's current `HEAD`; cherry-pick
and squash do not satisfy that condition. `workspace status --json` exposes
ancestry, while `remove --dry-run` checks all cleanup conditions. The shipped
[complete lifecycle](../common/agent_skills/basaltwater-subagents/references/lifecycle.md)
connects worktree creation, bounded assignments, standard handoffs, commit review,
merge, validation, and removal in one example.

The shared subagent skill also ships
[model selection and learning](../common/agent_skills/basaltwater-subagents/references/model-selection.md).
Start with Luna for simple categorization or extraction, Sol for coding and
reviews, and rarely Astra for a justified specialized task. Verify the exact
model and effort supported by the current session. Use standard service and
exclude fast mode, `max`, and `ultra` unless explicitly selected by the user.

These local commands create a per-project policy, recommend a setting from an
actual session inventory, and retain the parent's reviewed outcomes:

```bash
basaltw agent models init ~/repos/PROJECT --json
basaltw agent models recommend ~/repos/PROJECT coding --available SESSION_MODELS.json --json
basaltw agent models record ~/repos/PROJECT --task coding --model gpt-6.1-sol --effort medium --outcome accepted --validation 'Focused checks and parent review passed' --json
basaltw agent models report ~/repos/PROJECT --json
```

The primary checkout holds `.basaltwater/agent-models.json` and
`.basaltwater/agent-model-results.json`; linked-worktree invocations resolve to
the same records. Writes use a shared Git-directory lock and atomic file
replacement. The files contain project policy and non-secret observations,
and can be committed so the project carries its own evidence across sessions.
Initialization preserves existing policies. Recording requires a validation
result; observed costs, runtime, retry counts, and parent rework are optional.
Unknown billing remains unknown, including subscription sessions. Neither
command launches agents or calls a model API.
Unobserved parent rework also remains unknown; reports expose a known subtotal
and observation count rather than present missing observations as no rework.

Recommendations intersect verified project metadata with current runtime
availability and safe efforts. New IDs can be added without upgrading
Basaltwater; no version whitelist controls selection. The result identifies
uncatalogued or stale models so the agent can verify official metadata, update
the project policy, and evaluate candidates. Proven choices use recent outcomes
for the same task, evaluation version, model ID, and effort. Untested candidates
are exposed for trials; they do not displace a proven choice automatically.
Proven higher efforts remain eligible even when a lower supported default has
failed. Provisional choices use the lowest supported effort meeting the task's
minimum, and do not automatically retry with untested higher effort.
An empty eligible set returns nonzero and a null selection.

The starter catalog is dated **2026-10-09**, with standard API token rates and
reasoning support checked against the
[OpenAI model catalog](https://developers.openai.com/api/docs/models) and
[reasoning guide](https://developers.openai.com/api/docs/guides/reasoning).
Those are estimates for comparison, not a quote for a subscription account or
a guarantee that a particular tool exposes the same models. The workload
defaults apply the task/cost tradeoffs described by
[OpenAI model selection](https://developers.openai.com/api/docs/guides/model-selection).
Review the seed on initialization, after model releases or pricing/access
changes, and when outcomes regress. New model evidence stays separate from
older model versions, and old observations remain available for comparison.
