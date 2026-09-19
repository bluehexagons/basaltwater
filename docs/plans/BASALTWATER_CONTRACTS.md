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

Default controller configuration reads automatically migrate recent default
client configuration, including saved hosts, with conflict and symlink checks.
Explicit custom paths are not redirected. `basaltw migrate` previews the remaining operation;
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

## Outstanding storage identity cutover

The full rename is not yet complete for abbreviated persistent storage IDs:
`lib/vm_storage.py` still generates `it-<name>` disk serials, and
`common/storage_steps.py` uses `it_<name>` LVM volume groups. These are
on-disk/provider identities, not command aliases. Their replacement needs a
coordinated provider and guest migration, including existing saved records,
cache volumes, mapper paths, required mounts, and interruption recovery.
A text substitution would make existing disks undiscoverable. Do not rename
or reformat live volumes to satisfy a branding scan. Track this work before
declaring the full-rename acceptance criteria complete.
