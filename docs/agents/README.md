# Agent systems

This is the operator starting point for Basaltwater-managed coding VMs and
workstations. Use the [contributor guide](contributing/README.md) when changing
this repository; this index is for operating managed machines.

For a complete profile walkthrough, including a T3 Code/RDP VM with a separate
browser-approved privileged-action panel, start with
[Agentic VMs](agentic-vms.md). For workstation-specific setup, continue to the
[workstation index](../workstations/README.md).

## Choose a task

| Need | Guide |
| --- | --- |
| General workflow for any coding project | [Project development skill](../../common/agent_skills/basaltwater-project-development/SKILL.md) and [subagent workflows](agent-subagents.md) |
| Adapt a project to the reference development environment | [Project standards](project-standards.md) and [adoption skill](../../common/agent_skills/basaltwater-project-adoption/SKILL.md) |
| Design or substantially reshape a frontend | [Frontend design skill](../../common/agent_skills/basaltwater-frontend-design/SKILL.md) |
| Choose a headless or graphical coding profile | [Agentic VMs](agentic-vms.md) |
| Review isolation, sudo, prompt-injection, and supply-chain boundaries | [Agent security](agent-security.md) |
| Set up, rotate, or recover provider credentials | [Agent authentication](agent-authentication.md), [Credentials](credentials.md) |
| Inspect tools, workspace conventions, or deployment mappings | [Environment manifest](agent-environment.md) |
| Understand installed capabilities and skill routing | [Managed skills](agent-skills.md) |
| Keep context within a budget | [Context budgets](agent-context.md) |
| Delegate isolated work | [Subagent workflows](agent-subagents.md) |
| Save notes and shared todos | [Project memory](agent-memory.md) |
| Schedule follow-ups or continue a bounded goal | [Agent tasks](agent-tasks.md) |
| Configure GitHub or self-hosted Git access | [Git access](git-access.md) |
| Use project tooling or prepare native builds | [Project tooling](project-tooling.md) |
| Provision browser automation | [Browser automation](browser-automation.md) |
| Check Basaltwater-owned browser surfaces | [Browser testing](browser-testing.md) |
| Install or operate T3 Code | [T3 Code](t3-code.md) |
| Request an occasional privileged action | [Privilege approvals](privilege-approvals.md) |
| Administer privilege policy and records | [Privilege broker reference](privilege-broker-reference.md) |

## Day-two checks

These controller-side checks apply to the Debian-based managed stack. For
`agent_cachyos`, use the [CachyOS maintenance reference](../workstations/cachyos-maintenance.md).

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
T3 uses its [own updater](t3-code.md#service-and-update-model), and managed
skills refresh through saved setup. Maintenance holds protect active work from
scheduled host maintenance; see [recurring maintenance](../operations/maintenance.md#agent-maintenance-holds).

## Boundaries

| Concern | Guide |
| --- | --- |
| Host capability differences | [Machine types](../reference/machine-types.md) |
| Desktop application workflows | [Workstations](../workstations/README.md) |
| Host maintenance and recovery | [Operations](../operations/README.md) |
| Repository change rules | [Contributor guide](contributing/README.md) |
