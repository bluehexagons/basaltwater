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
| Repository changes and coding-agent workflow | [Contributor guide](agents/contributing/README.md) |
| Current priorities, follow-up work, and release qualification | [Project plans](plans/README.md) |

## Project records

- [Release qualification](release-qualification.md) tracks the v2.0 preview and
  stable release gate.
- [Migration guide](migration-guide.md) covers the completed rename and
  the remaining supported transition path for older installations.
- [Identity and visual assets](brand/README.md) documents the Basaltwater mark,
  palette, and reusable brand files.

The [root README](../README.md) introduces the project, supported targets, and
common capabilities. Examples in these guides use `basaltw` and placeholders;
never add real secrets to documentation.

## Documentation conventions

Guides lead with the task, use short procedures and lookup tables, and link to
a detailed reference instead of duplicating it. The category indexes own the
document maps, so add a new guide to its topic index and this page when the
topic itself is new.

Plans state current scope, next actions and acceptance. Update them in place;
keep progress/change logs and raw test evidence outside public planning pages.
The [coverage map](plans/testing-coverage.md) summarizes qualification limits,
and the release matrix remains the exact-candidate publication gate.
