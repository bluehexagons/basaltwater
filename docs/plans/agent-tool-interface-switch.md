# Agent tool interface switch for v2

The v2 switch covers the whole Basaltwater-owned agent tool inventory. Ship
the producers, their callers, managed skills, and updated examples in one
release. Agents start fresh sessions after the ordinary refresh. Shared request,
result, artifact, and lifecycle semantics are defined in the contributor
contract. [Tool inventory](../agents/agent-tool-inventory.md), [Interface contracts](../agents/contributing/tool-interface-contracts.md)

## Scope and ownership

Review each inventoried Basaltwater-owned interface at its public entry point.
Record its request grammar, default result mode, machine fields and exit or
error behavior in the owning reference. Trace consumers through repository
code, shipped skills, completion metadata, examples, and test fixtures. Include
saved task recipes or project integrations that Basaltwater ships. Use the
contract for one-shot machine results and document framing for streams,
interactive flows, and child-command passthrough. T3, Playwright, shell,
and project commands use their own interfaces. Their Basaltwater adapters
and the skills that hand data to them are in scope.

| Area | Implementation and handoff |
| --- | --- |
| Discovery and host tooling | Cover `agent context`, `manifest`, `doctor`, diagnostics, support, resources, containers, graphics, storage, reservations, maintenance, update, authentication, and privilege routes. Label installed, running, and session-observed information. Exercise discovery → documented tool, and keep interactive or protected input on its declared channel. [Diagnostics](../reference/cli-agent-diagnostics.md), [host commands](../reference/cli-agent-hosts.md), [operations](../reference/cli-agent-operations.md) |
| Desktop control and evidence | Cover Debian XRDP, CachyOS native control, and native Windows. Apply the shared result and artifact fields to capture, mapping, input, sequences, evidence, and media; label backend-specific selectors and grammars in help. Exercise capture → receipt → mapped input, and retained partial effects on failure. [Linux CLI](../../lib/desktop_cli.py), [Windows CLI](../../windows/Cli.Common.ps1), [desktop observations](../workstations/desktop-observations.md) |
| Supervised development and visual checks | Cover Linux and Windows development tasks, visual recipes, Blender, environment checks, and evidence readers. Carry one job ID through submit → ready or wait → status and logs. Pass the returned capture, report, or receipt to the consuming check. [Linux development CLI](../../desktop/development.py), [Windows development CLI](../../windows/Development.Common.ps1), [visual checks](../workstations/visual-comparisons.md) |
| Browser and gateway | Cover readiness, registration, disposable instances, T3 preview resolution, live forwards, static snapshots, and Godot exports. Standardize the Basaltwater result that carries `navigation` into T3. Keep declared readiness-stream and URL-only operations explicit. Exercise both resolver routes and their failure results. [Browser CLI](../../lib/agent_browser.py), [gateway CLI](../../common/service_tools/basaltwater_web.py) |
| Tasks and coordination | Cover task CLI/MCP, the worker and panel consumers, workspace, memory, inbox, and model selection. Match equivalent CLI/MCP request defaults and results, including list bounds and creation mode. Carry the same task/run identity into history and receipt lookup. Require an MCP revision with structured results and check that the client receives them on both success and tool failure. [Task CLI](../../lib/agent_tasks_cli.py), [Task MCP](../../lib/agent_tasks_mcp.py), [task operations](../agents/agent-task-controls.md) |
| Project tooling, deployment, and publishing | Cover inventoried project manifest and runtime commands, native host helpers, deployment receipts, and game publishing jobs and artifacts. Exercise manifest → literal recipe arguments, deployment → revision verification, and publish preparation → job status or reviewed artifact. [Project tooling](../reference/cli-project-workflows.md), [deployment receipts](../../lib/deployment_receipt.py) |
| Test and output helpers | Cover owned launchers and test runners, including the project development capture and reader scripts. Document human output, retained JSON, terminal status, and child-status forwarding where used. Exercise runner → saved summary and logs → bounded reader, with separate collection and child outcomes. [Output helpers](../../common/agent_skills/basaltwater-project-development/references/tool-output.md), [test targets](../../Makefile) |

## Existing files

| File role | Treatment |
| --- | --- |
| State that still drives work | Keep the store's existing schema when the new interface can map it directly. Where a new format simplifies the owning tool, migrate once under its existing ownership and locking rules. Preserve task and run identity, schedule settings, usage, and checkpoints where applicable. Validate before replacement and test interruption and repeated invocation with previous-version fixtures. |
| Historical evidence | Retain receipts, logs, and media as written. If the new tools need to consume an older artifact automatically, migrate or convert it at the owning boundary. For manual inspection, provide brief skill guidance on finding the old file and interpreting the relevant fields. |
| Session-bound observations and handles | Start a fresh observation in the new session. Use the current helper generation and capture or browser reference for subsequent actions. Durable job IDs can still be inspected through their owning tools. |

Treat result schema versions and stored-file versions independently. Keep
old-file guidance with the skill for the workflow that produced the files.
[Task history](../agents/agent-task-controls.md), [Desktop observations](../workstations/desktop-observations.md), [Recovery conventions](../operations/transaction-recovery.md)

## Implementation sequence

1. Map the full inventory to the owning entry points and consumers. Mark each
   result as a one-shot JSON view, a documented stream, or an interactive or
   passthrough operation. Identify stores that need a changed schema and files
   that need only manual inspection guidance.
2. Implement the shared result fields, validation, and exit semantics in the
   owning CLI and MCP adapters. Reuse existing serializers and domain models.
   Keep service status and child-process results available alongside the
   shared fields. Apply the same fixtures to equivalent Python and PowerShell
   operations where they share a contract.
3. Update each downstream caller, help surface, and skill as its producer
   changes. Add one-time migrations and old-file instructions where the
   file-role decisions call for them. Integrate the full scope for the v2
   release so a normal refresh installs a consistent set of interfaces.

## Acceptance

- Cover each changed public entry point with its documented request and
  response mode. Assert the machine fields and stdout/stderr or MCP transport
  fields for success and handled failure. Exercise invalid input before
  effects and timeout or partial effects wherever the operation supports them.
- Check the handoffs listed above by passing the actual returned arguments,
  handle, or artifact into the next operation. Exercise platform-specific
  behavior with the existing test suites and scoped native checks where needed.
- Check every changed store with representative saved data. Reopening a
  migrated store must keep the same records and avoid repeating the migration.
  Verify that the skill's manual inspection instructions work on a saved old
  artifact when that is the chosen route.
- Run the repository documentation and implementation checks and include the
  updated interfaces in the normal v2 qualification. Use a fresh agent session
  when checking installed skills and the MCP catalog.
  [Contributor checks](../agents/contributing/checks.md), [Release qualification](../release-qualification.md)
