---
name: basaltwater-project-development
description: Apply a repository-agnostic workflow for planning, changing, reviewing, and documenting software projects, including discovery of local instructions and useful independent subagent work.
metadata:
  managed-by: basaltwater
---

# Project development

Use this workflow for software work in any repository. The repository's own
`AGENTS.md`, `CONTRIBUTING` guide, README, and task-specific documentation define
its local conventions and checks. Read the relevant instructions before acting;
do not carry Basaltwater repository rules into another project. Follow higher
priority session and user instructions as well.

## Start with the project

- Identify the requested outcome, scope, constraints, and acceptance conditions.
- Read the root `AGENTS.md` and applicable contributor guide before editing.
  Inspect nested instruction files along the path to each target too; a nested
  `AGENTS.md` or directory-specific guide can add rules for that area.
- Inspect the current worktree and preserve changes that are not part of the task.
- Read the relevant implementation, callers, configuration, tests, and docs far
  enough to understand the existing behavior and its boundaries.

## Make a scoped change

- Follow the project's patterns and put changes where that project expects them.
- Keep edits within the requested scope. Update documentation that describes
  current behavior and durable decisions; keep task progress in the project's
  tracking tools instead of lasting docs.
- For a new frontend screen or component, a substantial UI redesign, or work
  where visual and interaction design are part of the request, use the installed
  `basaltwater-frontend-design` skill. Skip it for backend-only work, isolated
  bug fixes, and small copy edits.
- Consider subagents for independent research, file audits, or review when the
  context and time saved justify handoff and integration costs. Keep coupled
  edits under one owner. Use the installed `basaltwater-subagents` skill for
  delegation, isolation, and handoff details. For broad audits, split independent
  file groups into bounded read-only reviews, then reconcile findings before edits.

## Review and report

- Choose focused checks defined by the project and relevant to the changed area.
  Report checks that were skipped and any remaining uncertainty.
- Review the final diff and worktree state for scope, accidental files, generated
  artifacts, and exposed secrets.
- Summarize the behavior changed, files affected, checks run, and any follow-up.
  Commit, push, or publish only when the user or the applicable project workflow
  authorizes it.
