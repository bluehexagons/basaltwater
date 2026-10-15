# Basaltwater documentation

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/wordmark-dark.svg">
  <img src="brand/wordmark-light.svg" alt="Basaltwater" width="264" height="48">
</picture>

Start with [Try Basaltwater on a Debian VM](getting-started/GETTING_STARTED.md)
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
| Project proposals, audits, and release planning | [Plans and issue index](plans/README.md) |

## Project records

- [Release qualification](BASALTWATER_RELEASE.md) tracks the v2.0 preview and
  stable release gate.
- [Migration guide](BASALTWATER_MIGRATION.md) covers the completed rename and
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
