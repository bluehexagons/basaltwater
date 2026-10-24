# Project cleanup conventions

Basaltwater agents support a project's existing layout, cleanup scripts and
instructions. Read `AGENTS.md`, contributor guidance and native tooling first.
An optional JSON policy makes script targets and agent curation rules visible
to the storage inspector; it does not replace a project's own system. Use the
managed `basaltwater-project-adoption` skill when converting a project.
Follow the [project standards](project-standards.md): reuse shared infrastructure
on Basaltwater, retain native project commands and document a manual route on
other environments.

## A default for projects without a system

Use this layout only when the project lacks suitable conventions:

| Location | Purpose | Cleanup |
| --- | --- | --- |
| `artifacts/generated/` | Reproducible builds, reports and temporary output | Shared guarded remover or native cleanup after reproducibility and active-use checks |
| `artifacts/review/` | Screenshots, videos and logs awaiting review | Agent selects useful evidence before proposing removal |
| `artifacts/archive/YYYY-MM-DD--milestone/` | Selected development history | Preserve; maintain a small index and provenance |
| `.basaltwater/cleanup.json` | Optional adapter describing rules | Track the policy; keep unrelated local control state ignored |

Do not move existing outputs merely to match the table. For example, a project
may keep builds in `out/`, capture reviews in `build/review-evidence/`, and
archives in `docs/development-history/`. Document those paths instead. Never
infer disposability from Git ignore rules, extensions or timestamps: ignored
build directories can contain release packages and valuable review evidence;
source textures and videos remain source assets.

## Inspect a policy

```bash
basaltw agent storage project /absolute/repo --json
basaltw agent storage project /absolute/repo --policy tools/cleanup.json --json
```

The default policy path is `.basaltwater/cleanup.json`; `--policy` selects a
literal project-relative path. Git, provider-state and credential locations
(including `.ssh`, `.credentials` and `.env.*`) are refused before opening the
policy. Default and custom paths beneath `.basaltwater` remain supported.
Inspection reads that bounded JSON file and filesystem metadata only. It never
executes scripts, reads artifact contents, creates a policy, relocates files or
authorizes cleanup. If absent, the result
contains a suggested starter policy, with agent review rules until exact generated
targets or a project-owned script have been reviewed. Check existing non-JSON systems before adopting
that suggestion.

The policy is at most 65536 bytes, with `version: 1`, project `instructions`,
optional `preserve` paths, and 1–16 `rules`. See this repository's
[working policy](../../.basaltwater/cleanup.json), which preserves its existing
layout and pairs the shared generated-output remover with manual evidence review.
Each rule declares:

| Field | Contract |
| --- | --- |
| `name` | Unique lowercase identifier, digits and hyphens allowed |
| `paths` | 1–16 literal relative directories; at most eight distinct inspection roots overall |
| `strategy` | `generated` for the built-in guarded remover, `script` for project-owned cleanup commands, or `agent` for review |
| `instructions` | Reproducibility, retention, active-use and verification requirements |
| `preview`, `apply` | Required literal argv arrays for script rules; forbidden for generated/agent rules; no automatic execution |
| `archive` | Optional protected destination for agent-selected evidence |
| `min_age_days` | Optional minimum age, 1–36500 days; never sufficient authority to delete |

Generated rules select whole directories and cannot use an age filter or archive.
Choose small, disjoint generated subdirectories when a parent also holds retained
files. For example, select `build/linux` and `build/web` to preserve a tracked
`build/.gdignore`. Automatic targets cannot overlap each other.

Paths cannot escape the repository, contain globs, or target known Git, agent
control or credential locations. Automatic targets cannot overlap preserved paths,
archives or agent-curated roots. Agent review may cover a mixed directory containing
preserved subtrees; those subtrees remain excluded from removal. Their measured
bytes are still included, so the total is not a reclamation estimate. Custom
policy files are also preserved. Rules describe intended scope, not a sandbox:
review commands and their implementation before running even a preview.

Inspection defaults to 50000 entries and 10 seconds shared across rule roots;
`--max-entries` and `--timeout` use the same limits as
[storage inspection](storage-cleanup.md). Missing roots, skipped links/mounts
and incomplete scans remain explicit evidence. A valid policy means its shape
is understood, not that its instructions or commands are trustworthy.

## Script contract

Prefer the project's existing cleanup tool. A new script should preview by
default, require an explicit apply option, select exact generated paths, and
stop on tracked files, unexpected ownership, active work or incomplete evidence.
Avoid broad `git clean`, recursive home cleanup or extension-based deletion.
Check every target before removing any of them, recheck identities and use at
removal, and return failure if a check or deletion fails. Stop builds and review
jobs first: process checks are best effort and cannot establish future disuse.

For projects without a native cleanup tool, a `generated` rule reuses Basaltwater's
guarded remover without copying deletion code into every repository:

```bash
basaltw agent storage clean-generated /absolute/repo --rule generated-output
basaltw agent storage clean-generated /absolute/repo --rule generated-output --apply
```

Preview is the default; `--dry-run` is also accepted. The command only accepts an
explicit named `generated` rule from the reviewed policy (`--policy` selects a
custom path). It never executes policy commands or accepts arbitrary path targets.
Read-only `storage project` inspection includes the concrete preview/apply argv.
The policy belongs to the project, so review it before granting cleanup access.

This repository's [policy](../../.basaltwater/cleanup.json) selects only root
`build/`, `dist/` and `htmlcov/`. The [shared remover](../../lib/agent_generated_cleanup.py)
checks every selected directory before deleting any: bounded inventory, Git,
owner, link, mount and process checks, including open files for the account.
Preview and apply also refuse nested Git markers (files or directories),
credentials and agent control state. This no-follow name inspection shares a
50000-entry, 10-second budget across targets and stops at 24 directory levels;
incomplete inspection refuses the whole rule before any removal.
It requires a user-owned
checkout beneath an owner-only directory (such as a mode-0700 home); shared
checkouts need coordinated project cleanup. It preserves `artifacts/`, `.venv/`,
source and brand exports.

Some non-dumpable account daemons hide their descriptors; the remover reports
that coverage limit rather than treating every unrelated target as busy.
Activity detection is best effort: stop project jobs before applying cleanup.
Apply needs write access to each selected directory's parent to isolate
directories before disposal; account for parent
directory operations when preparing follow-up access, rather than granting only
the removed directories or the whole home.
Do not copy another project's targets without reviewing what those directories
contain. Failed disposal may retain
a private recovery directory; report it rather than retrying deletion blindly.

The guarded remover uses Linux mount and process observations and is intended
for Debian and CachyOS. Other environments can follow the same policy through
native project tooling or manual review: stop project jobs, inspect the exact
targets and Git tracking, preserve retained evidence, then remove only authorized
generated directories using the platform's filesystem tools. Do not copy the
shared remover into each project or make a full Basaltwater setup necessary for
normal development. A policy remains useful documentation without the CLI.

## Agent curation and timeline evidence

Use Luna workers at medium reasoning and standard service for bounded
classification of selected file lists or redacted log excerpts when delegation
is available. Use low only for trivial deterministic work, and high/xhigh when
concrete complexity warrants it. Prefer metadata, hashes, small contact sheets
and representative samples; return a short list with exact paths and evidence
offsets. Do not send bulk logs, private transcripts or databases to the parent
or a worker. Without delegation, use deterministic summaries and document uncertainty.

Preserve captures that explain milestones, UI changes, regressions, comparisons
or released behavior. Propose a dated archive folder with original media and a
small Markdown index (or the project's existing index format) recording:

- Capture time, project commit/build and task/session reference when known.
- Original relative path, filename, SHA-256 and a concise caption.
- Why it was selected and any gaps in provenance; avoid invented dates.

Review private content before archiving or publishing. In an authorized cleanup,
verify copied media and checksums before removing original candidates. Keep
selected originals when thumbnails or contact sheets lose useful detail. Link
the dated folder from the archive index so later agents can build a visual
development timeline. A move on the same filesystem frees no space; external
archival needs its own destination, permissions and verified copy.

The initial storage review remains read-only. Its follow-up should name exact
cleanup and archive targets, retained evidence, expected benefit, write access,
verification and any irreversible step. Execute only the authorized scope; do
not turn a project convention into standing deletion permission. For provider
sessions and live app databases, use [session retention](session-retention.md).
