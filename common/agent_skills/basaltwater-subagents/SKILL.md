---
name: basaltwater-subagents
description: Plan bounded subagent tasks, choose cost-aware models, and coordinate T3 Code workflows on Debian, CachyOS, native Windows, or Ubuntu WSL.
metadata:
  managed-by: basaltwater
---

# Subagent workflows

Delegate authorized independent work when savings justify context transfer,
review, integration, retries, and model cost. Keep small coupled changes with
one owner. Discover only needed local capabilities, for example
`basaltw agent manifest . --capability workspace --json`. Add `skills` only
for catalog inspection; presence does not prove session access.

## Choose and brief a worker

Use the live session's provider/model catalog and option descriptors. Start
with Luna for provisional, bounded work such as classification, documentation,
small fixes, focused tests, and app-script or export validation. Use medium by
default for these tasks. Explicit deterministic, trivial tasks may use low when
their inputs and checks are narrow. Use high or xhigh for Luna only when the
task has concrete complexity that warrants it. Use Sol high for broad coding,
integration, ambiguous cross-file work, and reviews; use Astra only for a
justified specialist task. These are guidance suggestions, not performance
proof: retain project policy and record actual outcomes. Choose standard service.
Never autonomously select or inherit fast service (including Codex `priority`),
`max`, or `ultra`; honor explicit confirmed user selections. Apply settings
through the tool and check the returned provider, model, and options.

For a Luna assignment, name one bounded deliverable, its owned files or checks,
and observable acceptance criteria. Suitable active work includes a small,
well-scoped fix with its focused tests; running and interpreting a named test
selection; or validating a named app script/export and reporting status plus
bounded failure excerpts. Do not assign simultaneous control of the same
desktop or app session to multiple workers. Never blindly time out and relaunch
a desktop mutation; first establish whether it completed and inspect its state.

Give a self-contained brief: objective, findings, absolute project/skill paths,
allowed files, constraints, acceptance checks, and required handoff. Children
may lack the parent's conversation, loaded skills, tools, or browser. Request
bounded findings and evidence rather than complete logs or catalogs.
For repeated reviews, include the original brief plus a concise account of prior
findings, responses, and unresolved objections; reference detailed artifacts
instead of copying every earlier handoff. Request changes since the last round,
remaining blockers, acceptance evidence, and paths in the return.

Editors and checks that write files need isolated worktrees; read-only reviewers
may share the checkout. Assign one owner for shared config/lockfiles. Require
worktree, branch, base/final commits, files, validation, concerns, and actual
model settings in the handoff. The parent reviews and integrates changes and
owns combined validation, model records, and cleanup.

## Choose the relevant route

- **T3 Code:** follow its injected orchestration instructions and live schemas.
  Use `delegate_task` when native tools cannot apply the selected model/options,
  and `isolation: "worktree"` for T3-owned editors. Retain `taskId`; async
  completion wakes the parent. Each review round is a new delegated task.
  Top-level threads require an explicit request for separate conversations.
  Read [T3 orchestration](references/t3-orchestration.md) for missing session
  guidance, provider/context transitions, workspace lifecycle, or queue recovery.
- **Basaltwater-owned worktrees:** read [lifecycle](references/lifecycle.md)
  before creation, adoption, integration, or removal. Prefer normal merges
  that preserve ancestry. T3 owns its own worktree lifecycle.
- **Changed account/provider or unattended worker:** read
  [session context](references/session-context.md) for discovery, tool access,
  and runtime limits. Honor disabled skills; paths do not bypass selection.
- **Recurring T3 work:** read [scheduling](references/t3-scheduling.md).
- **Project model policy, new models, or outcome tracking:** read
  [model selection and learning](references/model-selection.md). Review existing
  project policy before choosing workers. The local `agent models` commands
  record/recommend; they do not discover online models or launch agents.
