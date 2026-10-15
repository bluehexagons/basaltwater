# Agent systems

This is the operator starting point for Basaltwater-managed coding VMs and
workstations. Use the [contributor guide](contributing/README.md) when changing
this repository; this index is for operating managed machines.

For a complete profile walkthrough, including a T3 Code/RDP VM with a separate
browser-approved privileged-action panel, start with
[Agentic VMs](AGENTIC_VMS.md). For workstation-specific setup, continue to the
[workstation index](../workstations/README.md).

## Choose a task

| Need | Guide |
| --- | --- |
| Choose a headless or graphical coding profile | [Agentic VMs](AGENTIC_VMS.md) |
| Review isolation, sudo, prompt-injection, and supply-chain boundaries | [Agent security](AGENT_SECURITY.md) |
| Set up, rotate, or recover provider credentials | [Agent authentication](AGENT_AUTHENTICATION.md), [Credentials](CREDENTIALS.md) |
| Inspect tools, workspace conventions, or deployment mappings | [Environment manifest](AGENT_ENVIRONMENT.md) |
| Understand installed capabilities and skill routing | [Managed skills](AGENT_SKILLS.md) |
| Keep context within a budget | [Context budgets](AGENT_CONTEXT.md) |
| Delegate isolated work | [Subagent workflows](AGENT_SUBAGENTS.md) |
| Save notes and shared todos | [Project memory](AGENT_MEMORY.md) |
| Schedule follow-ups or continue a bounded goal | [Agent tasks](AGENT_TASKS.md) |
| Configure GitHub or self-hosted Git access | [Git access](GIT_ACCESS.md) |
| Use project tooling or prepare native builds | [Project tooling](PROJECT_TOOLING.md) |
| Provision browser automation | [Browser automation](BROWSER_AUTOMATION.md) |
| Check Basaltwater-owned browser surfaces | [Browser testing](BROWSER_TESTING.md) |
| Install or operate T3 Code | [T3 Code](T3_CODE.md) |
| Request an occasional privileged action | [Privilege approvals](PRIVILEGE_APPROVALS.md) |
| Administer privilege policy and records | [Privilege broker reference](PRIVILEGE_BROKER_REFERENCE.md) |

## Day-two checks

These controller-side checks apply to the Debian-based managed stack. For
`agent_cachyos`, use the [CachyOS maintenance reference](../workstations/CACHYOS_MAINTENANCE.md).

```bash
basaltw agent doctor HOST USER --all-capabilities --json
basaltw agent maintenance status HOST USER
```

`--all-capabilities` checks provisioned capabilities and installed terminal
tools without failing for intentionally absent clients. Narrow the check with
`--capability host` or require a specific client with `--tool codex`.

The host check observes both the coding account's home filesystem and `/`.
A separate home mount does not hide root pressure that can block package
updates or managed runtimes. JSON keeps the home observation in `disk` and the
root observation in `disk.root_filesystem`; readiness records retain both
capacity observations without the account's home path.

For a deliberate terminal-agent upgrade, preview it first:

```bash
basaltw agent update HOST USER --tool codex --dry-run
```

Repeat without `--dry-run` to apply. This updates terminal-agent executables;
T3 uses its [own updater](T3_CODE.md#service-and-update-model), and managed
skills refresh through saved setup. Maintenance holds protect active work from
scheduled host maintenance; see [recurring maintenance](../operations/MAINTENANCE.md#agent-maintenance-holds).

## Boundaries

| Concern | Guide |
| --- | --- |
| Host capability differences | [Machine types](../reference/MACHINE_TYPES.md) |
| Desktop application workflows | [Workstations](../workstations/README.md) |
| Host maintenance and recovery | [Operations](../operations/README.md) |
| Repository change rules | [Contributor guide](contributing/README.md) |
