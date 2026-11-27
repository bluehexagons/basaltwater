---
name: basaltwater-subagents
description: Brief authorized subagents, choose models, and integrate their work.
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

For large documentation reviews, give each worker a bounded file group and
request findings, coverage limits, and paths. GPT-6 Luna at medium reasoning
and Standard service is a starting point when the live catalog exposes it.
Use separate passes for broad audits and final integration review.

Use the live session's provider/model catalog and option descriptors. Prefer
available subscription usage; verify authentication, quota, and whether a model
requires separately billed credits. Compare acceptance, latency, retries and
parent rework as well as allowance consumption. API prices are a fallback
comparison, not subscription charges. Read
[current model choices](references/model-selection.md#current-workload-choices)
when choosing across providers or considering Terra or specialist escalation.

Retain proven project settings and prefer standard service. Fast service
(including Codex `priority`), `max`, and `ultra` require an explicit user
selection. Follow the active session's settings rules and verify the selection
the tool reports. Assign one worker at a time to control a shared desktop.

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

- **T3 Code:** follow its session instructions and live schemas. Consult
  [T3 integration boundaries](references/t3-orchestration.md) for linked
  machines, ownership of worktrees, or session and queue recovery.
- **Basaltwater-owned worktrees:** read [lifecycle](references/lifecycle.md)
  before creation, adoption, integration, or removal. Prefer normal merges
  that preserve ancestry. T3 owns its own worktree lifecycle.
- **Changed account/provider or unattended worker:** read
  [session context](references/session-context.md) for discovery, tool access,
  and runtime limits. Honor disabled skills; paths do not bypass selection.
- **T3 schedules and workflows:** read [scheduling](references/t3-scheduling.md)
  for migration, run subjects, concurrency pools, and recovery.
- **Project model policy, new models, or outcome tracking:** read
  [project model policy](references/model-policy.md). Review existing
  project policy before choosing workers. The local `agent models` commands
  record/recommend; they do not discover online models or launch agents.
