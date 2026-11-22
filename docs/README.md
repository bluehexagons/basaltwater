# Basaltwater documentation

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/wordmark-dark.svg">
  <img src="brand/wordmark-light.svg" alt="Basaltwater" width="264" height="48">
</picture>

Start with [Try Basaltwater on a Debian VM](getting-started/getting-started.md)
for a guided first setup. The guides are grouped by the work they help you do;
each section index routes to its detailed pages.

## Browse by topic

| Topic | Start here |
| --- | --- |
| First setup and installation | [Getting started](getting-started/README.md) |
| CLI, machine types, and concise command help | [Reference](reference/README.md) |
| Coding agents, credentials, and agent services | [Agent systems](agents/README.md) |
| Debian and CachyOS desktops, WSL, and development | [Workstations](workstations/README.md) |
| Host operations, maintenance, networking, and Proxmox | [Operations](operations/README.md) |
| Shares, synchronization, backups, and storage recovery | [Storage](storage/README.md) |
| Deployments, web services, and applications | [Services](services/README.md) |
| Optional project-specific workflows | [Integrations](integrations/README.md) |
| Testing status, workflows, environments, and gaps | [Testing](testing/README.md) |
| Repository changes and coding-agent workflow | [Contributor guide](agents/contributing/README.md) |
| Current priorities, follow-up work, and release qualification | [Project plans](plans/README.md) |

## Project records

- The audit records fourteen findings, their fixes, the steps for existing
  installations and the checks completed for each change. [Security review](security-review.md)
- [Release qualification](release-qualification.md) tracks the v2.0 preview and
  stable release gate.
- [Migration guide](migration-guide.md) covers the completed rename and
  the remaining supported transition path for older installations.
- [Identity and visual assets](brand/README.md) documents the Basaltwater mark,
  palette, and reusable brand files.

The [root README](../README.md) introduces the project, supported targets, and
common capabilities. Examples in these guides use `basaltw` and placeholders;
keep private infrastructure and business details outside Git; see the
[documentation privacy rules](agents/project-standards.md#documentation-and-private-operational-state).

## Documentation conventions

Keep entrypoints short enough to scan before opening a procedure. Category
indexes route by task. Split long pages where a reader can choose a complete
procedure or reference without loading adjacent material. Keep prerequisites,
authority, and recovery boundaries with the procedure that needs them. When
moving a section, preserve incoming anchors and use direct links in new material.

Skill entrypoints carry triggers, prerequisites, and essential boundaries.
Ship optional procedures as relative references in the same bundle. Check
co-installation before linking to another bundle. See [skill maintenance](agents/agent-skill-maintenance.md).

Keep volatile versions and model settings near their source or a dated
verification. Use installed CLI help and the live provider catalog for
execution. Plans state scope, next action, and acceptance. Keep dated summaries
in [testing evidence](testing/evidence.md) and raw operational evidence private.
The [coverage map](plans/testing-coverage.md) gives known limits and the
[release matrix](release-qualification.md) governs exact-candidate publication.

Run `make docs-check` before committing link or command reference changes.
