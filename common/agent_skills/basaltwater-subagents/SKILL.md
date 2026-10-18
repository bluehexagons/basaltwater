---
name: basaltwater-subagents
description: Plan bounded subagent tasks, choose cost-aware models, and coordinate T3 Code workflows on Debian, CachyOS, or Ubuntu WSL.
metadata:
  managed-by: basaltwater
---

# Subagent workflows

Use this workflow with the platform's workspace skill. It is installed on
Debian agent VMs, CachyOS workstations, and Ubuntu WSL; its references are local.
For general repository workflow and local instruction discovery, also read the
installed `basaltwater-project-development` skill. Delegate independent tasks
when context transfer, review, integration, retries, and model cost leave a
useful saving. Keep small, tightly coupled changes with one owner. Delegation
remains subject to the session's authorization and tools.

Start discovery with `basaltw agent manifest . --capability workspace
--capability skills --json`; add the relevant capability when needed, or use
`--capability web` for Node/web work. Each scope includes the recorded Basaltwater
installation revision. Skill discovery still leaves session access unverified.

Read [session context and skill access](references/session-context.md) when
launching fresh workers, changing accounts or providers, or delegating from an
unattended task. Skill files, selected guidance, and tools have separate scopes.

In T3 Code, read [native orchestration](references/t3-orchestration.md) before
delegating, choosing a worktree, coordinating queues or forks, or monitoring a
PR. Discover the live provider/model catalog; use native same-provider workers
when they support the chosen model, and T3 `delegate_task` otherwise. Ordinary
top-level threads require a request for separate conversations. Use T3's worktree
binding for T3-owned workers; the terminal lifecycle below applies to
Basaltwater-owned worktrees.

For authorized recurring work inside T3, read [T3 scheduling](references/t3-scheduling.md)
to choose thread binding, cadence, safe provider options, and webhook controls.
This shared reference does not require the independent Basaltwater task worker.

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
