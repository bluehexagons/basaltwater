# Basaltwater rename contracts

The approved scope is a full rename with a one-time migration from recent
infra-tools installations. Permanent compatibility aliases and historical
release support are explicitly excluded. This supersedes the earlier proposal
to retain internal names through v2.x.

| Surface | Canonical contract | One-time cutover |
| --- | --- | --- |
| Distribution / Python / command | `basaltwater` / `basaltwater.py` / `basaltw` | Replace launchers; no old import shim or executable alias |
| Source hosting | `bluehexagons/infra_tools` until the owner's repository rename | No premature URL changes |
| System runtime and data | `/opt/basaltwater`, `/etc/basaltwater`, `/var/lib/basaltwater`, `/var/log/basaltwater` | Move recent data, preserve private bytes/modes, stage current runtime |
| User data | Basaltwater directories under `.config`, `.cache`, `.local/share`, `.local/state`, `Pictures` | Merge only disjoint entries; refuse conflicting data |
| Services / accounts / resources | Basaltwater names and owned configuration | Stop old units; rename accounts without changing UIDs; restore recorded activity under new names |
| Agent integration | `basaltwater-*` skill and MCP IDs | Replace managed skills and update agent configuration |
| Runtime settings | `BASALTWATER_*` only | Update managed shell/unit settings; external automation is an operator action |
| Deployment manifests | `basaltwater.json` | Repository owners rename their manifests before deployment |
| Recovery | Private migration journal | Reverse an interrupted cutover, not a supported downgrade after completion |

Default commands never migrate on read. `basaltw migrate` previews the operation;
`--apply` explicitly cuts over a user installation, and `--system --apply` cuts
over the host. Normal setup automatically migrates recent target installations,
including existing login accounts, before running setup steps. Dry runs do not
probe or change targets. Recent-source eligibility, conflicts, linked-worktree handling,
custom paths and recovery are documented in the [migration guide](../BASALTWATER_MIGRATION.md).

The implementation keeps old-name strings only where needed to recognize
migration inputs, reject retired interfaces, preserve historical evidence, or
address the current GitHub repository. Existing certificate/key bytes retain
their trust identity. These are not runtime command or path aliases.

The [release checklist](../BASALTWATER_RELEASE.md) separates automated repository
verification from disposable live-host qualification and external publication.
