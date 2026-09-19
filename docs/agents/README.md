# Agent systems

This is the operator starting point for Basaltwater-managed coding VMs and
workstations. It groups the guides needed to choose a profile, set a security
posture, provide credentials, and verify the resulting environment.

For complete setup examples, including a fully capable T3 Code/RDP VM with a
separate browser-approved privileged-action panel, start with
[Agentic VMs](../AGENTIC_VMS.md).

It does not replace the repository
[contributor and coding-agent guide](contributing/README.md). That guide
applies when changing this repository; this page applies when operating a
managed machine.

## Choose a task

| I need to… | Start here | Then use |
| --- | --- | --- |
| Choose a headless or graphical coding profile | [Workstations](../WORKSTATIONS.md) | [Command-line agent flags](../COMMAND_LINE.md#agent-host-flags) |
| Configure a coding VM from an example | [Agentic VMs](../AGENTIC_VMS.md) | [Privilege approvals](../PRIVILEGE_APPROVALS.md) |
| Add agent tools to an existing CachyOS KDE desktop | [CachyOS local setup](../CACHYOS.md) | [CachyOS skill selection](../CACHYOS.md#skills-diagnostics-and-boundaries) |
| Choose default, passwordless-sudo, or hardened operation | [Agentic coding security](../AGENT_SECURITY.md) | [Workstations](../WORKSTATIONS.md) |
| Choose a credential workflow | [Credentials overview](../CREDENTIALS.md) | [SSH authentication](../SSH.md) |
| Seed, rotate, or recover coding-agent auth | [Agent authentication](../AGENT_AUTHENTICATION.md) | [Credentials overview](../CREDENTIALS.md) |
| Configure GitHub or self-hosted Git access | [Git access](../GIT_ACCESS.md) | [Credentials overview](../CREDENTIALS.md) |
| Use managed browser testing | [Agent browser automation](../BROWSER_AUTOMATION.md) | [Managed workflow skills](../AGENT_SKILLS.md) |
| Share native desktop applications with an agent | [Desktop automation](../DESKTOP_AUTOMATION.md) | [Shared XRDP desktop](../XRDP.md) |
| Install or operate T3 Code | [T3 Code server](../T3_CODE.md) | [Agent browser automation](../BROWSER_AUTOMATION.md) |
| Understand installed skills and capability routing | [Managed workflow skills](../AGENT_SKILLS.md) | [Command-line agent flags](../COMMAND_LINE.md#agent-host-flags) |
| Build or publish a Godot web project | [Godot Engine](../GODOT.md) | [Internal HTTPS sites](../INTERNAL_WEB.md) |

## Day-two checks

These controller-side checks apply to the Debian-based managed stack. For
`agent_cachyos`, use the [local checks and rerun workflow](../CACHYOS.md).

Run these commands from the controller after setup or when an agent service
needs attention:

```bash
basaltw agent doctor HOST USER --all-capabilities --json
basaltw agent maintenance status HOST USER
```

`--all-capabilities` checks provisioned capabilities and installed terminal
tools without failing for intentionally absent clients. Narrow the check with
`--capability host` or require a specific client with `--tool codex`.

For a deliberate terminal-agent upgrade, preview the selected tool first:

```bash
basaltw agent update HOST USER --tool codex --dry-run
```

Repeat without `--dry-run` to apply. This updates terminal-agent executables;
T3 uses its [own updater](../T3_CODE.md#service-and-update-model), and managed
skills refresh through saved setup. Maintenance holds are for
protecting active work from scheduled host maintenance; see
[recurring maintenance](../MAINTENANCE.md#agent-maintenance-holds).

## Boundaries worth checking first

| Concern | Guide |
| --- | --- |
| Privilege, sandboxing, prompt injection, and supply chain | [Agentic coding security](../AGENT_SECURITY.md) |
| Browser-approved privileged actions and allowlists | [Privilege approvals](../PRIVILEGE_APPROVALS.md) |
| Credential scope, copying, rotation, and lifecycle | [Agent authentication](../AGENT_AUTHENTICATION.md) |
| Browser access and private-network trust | [Agent browser automation](../BROWSER_AUTOMATION.md) |
| Host capability differences | [Machine types](../MACHINE_TYPES.md) |
| Managed worktrees and deployment smoke checks | [Managed workflow skills](../AGENT_SKILLS.md) |

For non-agent host operations, return to the
[documentation index](../README.md) or [quick reference](../QUICK_REFERENCE.md).
