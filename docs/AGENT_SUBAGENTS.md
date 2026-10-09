# Subagent workflows and model choices

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

Choose normal branch merges before starting. Managed cleanup requires task
commits to be ancestors of the primary checkout's current `HEAD`; cherry-pick
and squash do not satisfy that condition. `workspace status --json` exposes
ancestry, while `remove --dry-run` checks all cleanup conditions. The shipped
[complete lifecycle](../common/agent_skills/basaltwater-agent-workspace/references/lifecycle.md)
connects worktree creation, bounded assignments, standard handoffs, commit review,
merge, validation, and removal in one example.

The workspace skill also ships
[model selection and learning](../common/agent_skills/basaltwater-agent-workspace/references/model-selection.md).
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

Recommendations intersect verified project metadata with current runtime
availability and safe efforts. New IDs can be added without upgrading
Basaltwater; no version whitelist controls selection. The result identifies
uncatalogued or stale models so the agent can verify official metadata, update
the project policy, and evaluate candidates. Proven choices use recent outcomes
for the same task, evaluation version, model ID, and effort. Untested candidates
are exposed for trials; they do not displace a proven choice automatically.
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
