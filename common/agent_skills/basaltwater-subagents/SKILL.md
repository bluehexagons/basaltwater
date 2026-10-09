---
name: basaltwater-subagents
description: Plan bounded subagent tasks, choose cost-aware OpenAI models, and review and integrate work on Debian agent VMs or CachyOS workstations.
metadata:
  managed-by: basaltwater
---

# Subagent workflows

Use this workflow with the platform's workspace skill. It is installed on both
Debian agent VMs and CachyOS workstations; its references are available locally.
Delegate independent tasks when context transfer, review, integration, retries,
and model cost leave a useful saving. Keep small, tightly coupled changes with
one owner. Delegation remains subject to the session's authorization and tools.

Agents share the filesystem. Read-only reviewers may inspect the primary
checkout; editors and checks that write files need separate managed worktrees.
Assign explicit file boundaries and one owner for configuration and lockfiles.
The parent owns integration, combined validation, project model records, and
cleanup. Require each editor to return its worktree, branch, base and final
commit, changed files, validation, unresolved concerns, and actual model settings.

Choose normal branch merges upfront to preserve ancestry for managed cleanup.
Read [the complete lifecycle](references/lifecycle.md) for assignments, handoffs,
review, merge, validation, removal, and cherry-pick or squash implications.

Read [model selection and project learning](references/model-selection.md)
before choosing workers. Start with OpenAI Luna and low or supported disabled
thinking for simple classification, Sol for coding and reviews, and Astra only
when a specialized task justifies its cost. Verify actual session capabilities;
do not imply a recommendation changes tool settings. Never autonomously choose
or inherit fast service, `max`, or `ultra`. Explicit user selections govern
exceptions; clarify weak or ambiguous requests without repeating confirmation
for an already-confirmed selection.

The parent reviews project policy and recent outcomes, refreshes metadata when
actual model availability changes, and evaluates promising new IDs on bounded
tasks. Keep a proven incumbent until the candidate earns promotion. The local
`basaltw agent models` commands record evidence and recommend settings; they do
not discover models online, launch agents, or spend money.
