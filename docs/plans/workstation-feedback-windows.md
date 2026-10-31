# Windows workstation feedback handoff

Status: ready for a native Windows agent; implementation has not started.
Prepared on Debian on 2026-10-31 from clean `main` at
`89762e1f8956e66c8f8cdf604042284c95a122ea`. Start from current `main`, record
the actual full HEAD and dirty state, and preserve unrelated work.

## Objective and boundaries

Bring the useful reliability and evidence improvements from the
[Debian workstation work](workstation-feedback-debian.md) to Windows while
keeping Windows a thinner tools-and-skills layer. Complete the two required
increments below first. Discovery is a subsequent bounded improvement; a
project-recipe adapter is conditional, not required for the first delivery.

Reuse the native PowerShell CLI, managed skill installer, Scheduled Task
guardians, Windows Job Objects and project-owned commands. Do not port the
Linux controller, `agent` command hierarchy, services, privilege broker, GLX/Xvfb
diagnostics or CachyOS systemd supervision. Do not add a Python dependency to
the ordinary native launcher/diagnostics. WSL remains a separately selected
environment and does not establish native Windows qualification.

This is workstation work, independent of the proposed
[Windows build-host backend](windows-support.md). It does not include managed
build hosts, panels, signing, publication, storefront credentials, automatic
graphics-tool installation or a new rendering/comparison framework.

Read `AGENTS.md`, the [contributor guide](../agents/contributing/README.md),
[native Windows setup](../workstations/windows.md),
[desktop/tasks](../workstations/windows-desktop.md) and only relevant installed
skill references before editing. Repository guidance owns project decisions;
platform skills own session operation; active orchestration guidance owns
delegation and provider settings. Read target files completely before changes.

## Existing implementation and portable findings

| Area | Windows owner / reference |
| --- | --- |
| Skill installation and inventory | `windows/AgentWorkstation.Common.ps1`, `windows/agent-skills.json` |
| Native CLI and health | `windows/Invoke-Basaltw.ps1`, `windows/Cli.Common.ps1`, `windows/WorkstationHealth.Common.ps1` |
| Development requests, receipts and status | `windows/Development.Common.ps1` |
| Guardian, timing and completion | `windows/Invoke-DevelopmentTask.ps1` |
| Native containment and bounded stream draining | `windows/Development.Process.ps1`, existing Job Object helper in `windows/GitHubRunner.Common.ps1` |
| Workflow guidance | `common/agent_skills/basaltwater-windows-workspace/`, Windows workstation docs |
| Qualification and return evidence | [Windows qualification](../workstations/windows-qualification.md), [handoff templates](../agents/handoffs.md) |

Windows already excludes generated `__pycache__`, `.pyc` and `.pyo` from native
skill delivery and has cache-exclusion tests. Its ownership inventory is
`.basaltwater-windows-files.json`, with a different format from the Linux
inventory. Reuse it; do not introduce a competing inventory. Native tasks already
have IDs, owner/session/boot and worker/child identities, exit status, cancellation,
cleanup uncertainty, private environment cleanup and bounded logs. Preserve
these safeguards rather than replacing the execution layer.

The Debian implementation and follow-up passed 9,009 tests/six skips with 82%
combined coverage plus static/brand/wheel checks. Native Debian checks exercised
software GLX, Blender CPU rendering, recipe failure/timeout/log quotas/settings,
skill delivery and a Geany edit/save/cleanup fixture. These are Debian evidence,
not Windows acceptance. Private raw evidence stays on Debian; no receiving agent
should assume another machine's `/tmp` paths or local notes exist.

Commit `89762e1f8956e66c8f8cdf604042284c95a122ea` corrected a relevant shared
contract: Python equality allowed settings to change from `1` to `true`, `0` to
`false`, or `1.0` to `1`, including nested values. Both recipe checks and revision
capture now compare normalized JSON preserving those distinctions. Any later
Windows settings adapter must preserve the same distinctions, allow harmless
whitespace/key-order changes, and reject invalid/non-finite JSON. Do not assume
PowerShell's ordinary equality or JSON reserialization preserves all needed types.

## Required increment 1: skill freshness and completion

Add a read-only native report comparing known installed Windows bundles with the
saved installation's source. Integrate it with the existing health/CLI surface;
choose and document the smallest suitable selector, without importing Linux
commands. Distinguish `current`, `different`, `incomplete`, `unsafe`, and `personal`.
Missing optional bundles are informational. Report which source revision was
compared; a developer checkout is not automatically the installed source.

Add an explicit installation completion marker to the native inventory. Clear
it before publishing bundle changes and set it only after file delivery, stale
cleanup and final inventory persistence finish. Preserve the existing old/new
digest checkpoint and retry behavior. Validate malformed/unsupported state and
define how older inventories without the marker are read. Matching bytes alone
must not promote an explicitly unfinished installation to current.

Compare entrypoints and supporting resources without scanning arbitrary personal
skills, provider configuration, credentials or session files. Apply native path,
reparse-point and ACL/ownership checks; do not translate POSIX mode assumptions.
Preserve personal overrides, edited files and collision refusals. A diagnostic
must not run setup, update tools, launch providers or modify catalogs. Report
installed freshness separately from whether this agent has loaded the skill.

Acceptance covers both provider catalogs, clean/idempotent installs, generated
caches, legacy inventories, interruption at file and completion boundaries,
safe retry, unchanged stale-bytecode cleanup, edited bytecode/personal retention,
unsafe paths/reparse points and malformed inventories. Add meaningful cases to
the owning native tests; extend existing cases where they already cover behavior.

## Required increment 2: complete existing task evidence

Extend development receipts/status with UTC execution start/end times and
monotonic elapsed duration. Define the measured interval explicitly, separate
queue delay from execution, and avoid deriving elapsed time from wall-clock
subtraction. Keep timing valid on success, failure, cancellation and timeout;
leave unknown values explicit after interrupted/uncertain cleanup. Preserve old
receipts without fabricating new timing fields or durations across reboot.

Expose per-stream retention limits and truncation flags. Distinguish bytes
discarded at the existing 4 MiB stream cap from output omitted by the existing
64 KiB `logs` tail. Continue draining excess output without deadlocking children;
do not silently imply that the returned tail or retained log is complete.

Keep the existing receipt authoritative for owner, boot/session, worker/child
PID plus creation identity, exit status and cleanup outcome. Publish terminal
completion only after containment/output/private-environment cleanup settles.
Do not weaken Job Object assignment-before-execution, dispatch-race guards,
maintenance blocking or cancellation ownership. Do not add a second supervisor.

Acceptance includes disposable successful/failed jobs, cancellation, timeout,
stdout/stderr floods (including a failed flooded child), clean log tails,
worker loss, legacy/corrupt records and uncertain cleanup. Native process tests
must confirm owned descendants terminate and unrelated processes survive.
Mocks and process fixtures have different scopes; report both.

## Subsequent work and decision gates

After the required increments, extend `desktop develop doctor --project PATH`
with compact checkout revision/dirty state, selected executable paths and
missing requirements. If reading `basaltwater-agent.json`, reuse its declared
contract for recipe names/requirements and do not execute recipes during
discovery. Handle nongit projects and unavailable Git metadata honestly.
Keep host tool-version health probes separate from presence-only project
discovery; never execute arbitrary project shims or GUI apps for inventory.
Installed tools, GPU/driver metadata and desktop API readiness do not establish
project rendering or physical-display qualification.

Only add a project-check adapter when a reviewed consuming-project workflow
shows repeated integration work it would remove. A possible native
`desktop develop check NAME` is a proposal, not an existing command. It should
submit a declared recipe through the existing guardian and retain source,
settings, logs and artifact locations. Projects keep runtime pins, renderers,
capture scripts, full-image assertions and performance thresholds. Do not
translate Linux argv into PowerShell or invent a second recipe format. If no
clear consumer exists, explicitly defer this adapter.

Keep Windows skill updates short: route to the native commands and shared
checkpoint/handoff templates, and distinguish discovery, health and verification.
Do not copy long Linux procedures or duplicate model/provider policy.

## Native validation and delivery

Run as the intended Windows account in its existing installation. Record OS
build, PowerShell versions, source provenance, selections and current task state.
Inventory existing jobs before launching fixtures; preserve unrelated apps,
provider logins and personal settings. A full refresh can install updates and
restart T3 or request reboot; it is not a diagnostic command. Qualify with
temporary catalogs and owned tasks first. Use normal guarded delivery only for
the intended installed skill changes, then compare their actual catalog.

Run the affected native entry points while editing, then the complete suite
once after final implementation. From the checkout, examples are:

```powershell
pwsh.exe -NoProfile -File .\windows\tests\Run-AgentWorkstationTests.ps1
pwsh.exe -NoProfile -File .\windows\tests\Run-WorkstationHealthTests.ps1
pwsh.exe -NoProfile -File .\windows\tests\Run-DevelopmentTests.ps1
pwsh.exe -NoProfile -File .\windows\tests\Run-DevelopmentProcessTests.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\tests\Run-NativeTests.ps1
python scripts/check_cli_docs.py
python scripts/check_doc_links.py
git diff --check
```

Use the actual selected executables, retain bounded private logs with exit status
and duration, and inspect failures before retries. Check the native tests'
current prerequisites: the complete suite requires real Python 3.11+ and stable
PowerShell 7; process fixtures exercise actual owned Windows processes while
mocking Scheduler/notifications. The examples do not install prerequisites.
Run affected Windows PowerShell 5.1 entry-point contracts too; moving the guardian
to PowerShell 7 did not remove the 5.1 bootstrap/CLI boundary. Keep full Debian
Make/coverage validation in Debian CI; Linux-only success is not native proof.

Commit and push completed, validated increments to `main` as authorized for
this work, subject to the receiving session's authority. If native prerequisites
or interactive access are unavailable, complete independent code/tests/docs and
record the exact unverified acceptance cases without claiming native success.
Browser tests, physical GPU/display, streaming, audio/input, lock/reconnect,
reboot and installation-interruption qualification remain separate; do not
reboot or disturb the shared desktop solely to make this handoff look complete.

Return the [implementation handoff](../agents/templates/implementation-handoff.md):
exact checkout/branch, base/tested/delivered commits and dirty state; changed
behavior; commands/status/durations; private evidence; installed bundle/source
comparison; deferred work and unverified cases. Keep one current checkpoint and
separate history. Record every owned running/uncertain task with ID, state,
owner, evidence and `desktop develop status/logs/cancel` commands, or checked
none. Cancellation is a request: confirm terminal cleanup before handback.

At preparation, no Windows jobs were launched or inspected from Debian; there
are no Windows task handles to transfer. Recheck the actual Windows account
before starting. Existing Debian desktop/test handles do not grant ownership
over Windows sessions or lab guests.
