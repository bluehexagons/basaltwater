# Subagent workflows and model choices

Start with the installed [subagent skill](../../common/agent_skills/basaltwater-subagents/SKILL.md).
Check the active session's delegation tools and live model catalog. A fresh worker
needs a bounded assignment, relevant paths, and a clear acceptance check. For
large documentation reviews, divide the corpus into independent read-only batches
and ask for findings and coverage limits. The parent reconciles findings and
verifies edits.

| Task | Reference |
| --- | --- |
| Choose a model, effort, and service tier | [Workload choices](../../common/agent_skills/basaltwater-subagents/references/model-selection.md#current-workload-choices) |
| Recommend or record settings for a project | [Project policy and outcomes](../../common/agent_skills/basaltwater-subagents/references/model-policy.md) |
| Delegate from T3 or work across linked environments | [T3 orchestration boundaries](../../common/agent_skills/basaltwater-subagents/references/t3-orchestration.md) |
| Create, adopt, integrate, or clean up a Basaltwater-owned worktree | [Owned worktree lifecycle](../../common/agent_skills/basaltwater-subagents/references/lifecycle.md) |
| Change provider, account, or execution environment | [Session context](../../common/agent_skills/basaltwater-subagents/references/session-context.md) |
| Schedule an independent prompt or bounded goal | [Agent tasks](agent-tasks.md) |

GPT-6 Luna at medium reasoning and Standard service is a starting point for
bounded documentation, extraction, and classification. GPT-6.2 Sol at high
remains the starting point for broad implementation and substantive review.
Treat these as workload defaults and use the live catalog to confirm the exact
model, effort, and tier before starting a worker. The local model selector
recommends and records results using project policy and a supplied session
inventory. See the [selector reference](../../common/agent_skills/basaltwater-subagents/references/model-selection.md#openai-selector-defaults).
