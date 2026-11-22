# Inspect project storage conventions

[Topic index](storage-cleanup.md)

## Inspect project conventions economically

Read each project's `AGENTS.md` and existing cleanup tooling before choosing
targets. `basaltw agent storage project /absolute/repo --json` describes an
optional cleanup policy and measures its roots without running commands. Use
`--policy relative/path.json` for a custom location. Existing layouts and
non-JSON systems are supported through project instructions. The standard
layout is a fallback only; inspection creates nothing. See
[project cleanup conventions](project-cleanup.md) for scripts, agent curation
and preserving selected screenshots/videos as development history.

For a reviewed `generated` rule, preview
`basaltw agent storage clean-generated /absolute/repo --rule RULE_NAME`.
The built-in remover accepts only that rule's disjoint directories and checks
tracked files, ownership, mounts, links, inventory coverage and active use.
Removal requires explicit `--apply` and separately authorized cleanup scope;
inspection never invokes it. Include writes to each target's parent when
preparing a follow-up, because disposal first isolates directories there.

Use Luna workers at medium reasoning and standard service for bounded
classification of selected file lists or redacted log excerpts when delegation
is available. Use low only for trivial deterministic work, and high/xhigh when
concrete complexity warrants it. Set file/byte limits and request compact findings
with paths or offsets. Do not copy bulk logs or private transcripts into worker
or parent context. If no worker interface is exposed, use bounded local summaries
and report uncertainty. The installed VM-triage skill has the same workflow.

## Choose a follow-up

The review should rank up to five candidates by observed benefit, retention and
rebuild or download cost. For example, a large Go build cache can justify a
native cache cleanup proposal after checking active builds; Playwright browser
downloads need installed-version and active-browser checks. A project directory
total should lead to a focused investigation of generated output, rather than
deletion of the project. Preserve source, Git data, dirty or unmerged worktrees,
credentials, sessions, attachments, backups and active runtime versions.

Resolve bounded exact-child classification during the initial pass when possible.
An ambiguous large build/evidence root should not prevent a smaller proven cache
or generated-output cleanup. Preserve the ambiguous material and select the useful
cleanup that can actually proceed. Do not repeatedly hand off the same inventory.

One coherent cleanup proposal should name exact paths, commands, active-use
checks, retention decisions, verification and the irreversible nature of
deletion. If safety is uncertain, propose a targeted read-only investigation
with a concrete decision to resolve. That investigation must produce an apply-ready
cleanup proposal or name the specific retention decision needed from the operator.
Large totals alone are insufficient.

Select **Approve once**, **Review follow-up task**, or **Block** on the report.
A custom cleanup task with `storage_operation: "none"` uses workspace mode, a
private run directory, and only the exact necessary additional write directories.
Fixed host cache operations use [separate settings](storage-cleanup-evidence.md#host-evidence-and-approved-cache-operations).
Do not grant the whole home. Protected
agent directories and system paths cannot receive ordinary task write grants;
use managed retention or separately authorized administrator work. The review
should explain such prerequisites rather than send another agent into the same
blocked operation. See [follow-up access](agent-tasks.md#request-a-follow-up-with-different-access).

The approved follow-up must recheck its targets, perform the approved removal,
and report removed paths, failures, and available filesystem bytes before and
after. Its self-contained prompt carries the selected retention and access
constraints; it must not copy the initial review's prohibition on removal.
