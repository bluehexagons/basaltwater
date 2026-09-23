# Basaltwater rename contracts

The approved scope is a full rename with a one-time migration from recent
infra-tools installations. Permanent compatibility aliases and historical
release support are explicitly excluded. This supersedes the earlier proposal
to retain internal names through v2.x.

| Surface | Canonical contract | One-time cutover |
| --- | --- | --- |
| Distribution / Python / command | `basaltwater` / `basaltwater.py` / `basaltw` | Replace launchers; no old import shim or executable alias |
| Source hosting | `bluehexagons/basaltwater` | Update repository links and install examples to the renamed repository |
| System runtime and data | `/opt/basaltwater`, `/etc/basaltwater`, `/var/lib/basaltwater`, `/var/log/basaltwater` | Move recent data, preserve private bytes/modes, stage current runtime |
| User data | Basaltwater directories under `.config`, `.cache`, `.local/share`, `.local/state`, `Pictures` | Merge only disjoint entries; refuse conflicting data |
| Services / accounts / resources | Basaltwater names and owned configuration | Stop old units; rename accounts without changing UIDs; restore recorded activity under new names |
| Agent integration | `basaltwater-*` skill and MCP IDs | Replace managed skills and update agent configuration |
| Runtime settings | `BASALTWATER_*` only | Update managed shell/unit settings; external automation is an operator action |
| Deployment manifests | `basaltwater.json` | Repository owners rename their manifests before deployment |
| Recovery | Private migration journal | Reverse an interrupted cutover, not a supported downgrade after completion |

Default controller configuration reads automatically migrate recent default
client configuration, including saved hosts, with conflict and symlink checks.
Explicit custom paths are not redirected. `basaltw migrate` previews the remaining operation;
`--apply` explicitly cuts over a user installation, and `--system --apply` cuts
over the host. Normal setup automatically migrates recent target installations,
including existing login accounts, before running setup steps. Dry runs do not
probe or change targets. Recent-source eligibility, conflicts, linked-worktree handling,
custom paths and recovery are documented in the [migration guide](../BASALTWATER_MIGRATION.md).

The implementation keeps old-name strings only where needed to recognize
migration inputs, retain durable storage identities, reject retired interfaces, preserve historical evidence, or
address the current GitHub repository. Existing certificate/key bytes retain
their trust identity. These are not runtime command or path aliases.

The [release checklist](../BASALTWATER_RELEASE.md) separates automated repository
verification from disposable live-host qualification and external publication.

## Persistent storage identities

New named VM disks use `bw-<name>` serials, keeping the complete logical name
within Proxmox's 20-byte limit. New cache volume groups use
`basaltwater_<name>` with hyphens replaced by underscores.
Existing `it-<name>` serials and `it_<name>` volume groups remain unchanged,
including existing swap disks. New additions to existing VMs use the new names.

Provider and guest discovery recognize both generations and reject duplicate
identities for one logical disk. Setup records observed serials, filesystem
UUIDs and cache volume groups, verifies recorded identities on later reruns,
and refuses to initialize a missing recorded filesystem or cache. Mounts
continue to use filesystem UUIDs. No disk relabeling or LVM rename is performed.

Already-working systems need no setup rerun or reboot for this naming policy.
Their next normal setup rerun verifies and records retained identities.
This is durable-resource recognition, not support for operating old releases
or retaining old command aliases. Live storage qualification remains on the
release checklist.
