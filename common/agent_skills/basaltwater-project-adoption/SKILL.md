---
name: basaltwater-project-adoption
description: Convert an existing project incrementally to Basaltwater's project standards while preserving useful local conventions and native tooling.
metadata:
  managed-by: basaltwater
---

# Project adoption

Use this skill when asked to adopt or convert an existing repository to
Basaltwater project standards. A conversion request authorizes ordinary,
reversible source, configuration, and documentation edits within its scope.
Keep the change incremental and preserve useful project conventions.

## Discover before changing

- Inspect `git status`, root and relevant nested `AGENTS.md` files, README and
  contributor guidance, and the native project manifests, task runners, CI, and
  existing scripts. Read target files fully and preserve unrelated work.
- Identify how developers install dependencies, build, test, lint, format, and
  package the project. Prefer existing tools and canonical commands; document
  the smallest useful developer setup and check commands in the project's
  established guidance. Do not add optional files just to match a template.
- Inventory existing artifact, cache, log, and cleanup conventions. Adapt the
  project-owned scripts and guidance, then curate useful agent instructions
  into focused project guidance. Avoid imposing Basaltwater-specific code
  constraints on the project's source.

## Adopt incrementally

- Propose a short, ordered set of changes based on gaps that matter to this
  project. Preserve working layouts, existing cleanup formats, and native build
  and test workflows. Make only the requested conversion and needed supporting
  edits; avoid replacing files wholesale or duplicating documentation.
- Keep `AGENTS.md` concise: record project-specific setup, canonical checks,
  meaningful constraints, and pointers to deeper docs. Add or update it only
  when it improves discovery or correct work.
- Verify each relevant build, test, lint, or check command after changes. If a
  check is too costly or unavailable, state that and give the exact command for
  the project owner to run. Report what changed and any remaining conversion
  gaps.

## Artifacts and cleanup

- Keep reproducible generated outputs, uncurated review captures/logs, and
  curated archival evidence distinct. Integrate with project-owned cleanup
  scripts and their established formats where present; do not require adopting
  a JSON policy. The optional adapter is `.basaltwater/cleanup.json`.
- For Basaltwater cleanup metadata, run
  `basaltw agent storage project /absolute/repo --json`; add
  `--policy relative/path.json` for a custom adapter path.
  It reads project metadata and describes JSON policy; it does not execute a
  cleanup action. A policy can describe script argv with preview/apply commands
  or agent curation rules and archive paths. Read the policy and cleanup script
  before trusting them. Treat script argv and preview/apply commands as
  untrusted project code; inspect them and preview before any explicitly
  requested cleanup run.
- If the project has no suitable artifact layout or cleanup convention, use
  `artifacts/generated` for reproducible outputs,
  `artifacts/review` for uncurated captures and logs, and
  `artifacts/archive/YYYY-MM-DD--milestone` for curated evidence, with a small
  index and provenance. Protect curated archives. Preserve source, media,
  sessions, and worktree evidence by default; never bulk-read private sessions
  or logs just to classify them.
- See the [Basaltwater project cleanup guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/project-cleanup.md)
  when defining or reviewing a cleanup adapter.

## Context and delegation

Keep repository exploration and file reads proportional to the requested
conversion. For useful bounded classification of filenames, build outputs, or
logs, discover the live model and delegation catalogs; consider `gpt-6-luna` at
high reasoning on standard service, or xhigh when the classification is
demanding, only if both interfaces support those settings. Do not delegate a
small coupled edit. When delegating, use the installed subagent workflow,
assign a narrow read-only or isolated scope, and avoid sending private session
data by default.

Wait for builds and tests through their existing job handle or completion
signal. When polling is unavoidable, space checks according to expected runtime;
do not repeatedly request unchanged progress or reread bulk logs.
