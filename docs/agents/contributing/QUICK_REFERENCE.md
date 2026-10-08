# Contributor quick reference

## Types and key files

| Need | Location |
| --- | --- |
| JSON and collection aliases | `lib/types.py`: `JSONDict`, `StrList`, `MaybeStr`, `StepFunc` |
| Initial setup configuration | `lib/config.py`: `SetupConfig` |
| Periodic-operation configuration | `lib/runtime_config.py`: `RuntimeConfig` |
| CLI arguments | `lib/arg_parser.py` |
| Local bootstrap | `lib/orchestrator_bootstrap.py`, `install.sh` |

## Validation

| Input | Use |
| --- | --- |
| Paths, package names, network values, setup configuration | `lib.validation` |
| Hosts, IP addresses, usernames | `lib.validators` |

Validate before remote or system mutations.
Identity validators match the complete input, reject control-character suffixes,
and accept ASCII IPv4 digits only. Hostnames may have one terminal DNS root dot;
repeated terminal dots and names longer than 253 characters are rejected.

## Capability helpers

```python
from lib.machine_state import (
    can_manage_firewall,
    can_manage_swap,
    can_modify_kernel,
    can_restart_system,
    is_container,
    is_hardware,
    is_vm,
)
```

Use the helper for the operation, not a generic container check.

## Setup composition

`PluginDefinition` registrations in `plugins/` select setup steps. Composition
plugins own `step_builder` functions; capability plugins supply shared
extensions or custom steps. Extend the owning plugin path rather than adding a
second dispatcher.

## Repository areas

| Area | Location |
| --- | --- |
| Core libraries | `lib/` |
| User setup and CLI tools | `common/` |
| Desktop and RDP | `desktop/` |
| Firewall and SSH | `security/` |
| Nginx and TLS | `web/` |
| Samba | `smb/` |
| Rsync and par2 | `sync/` |
| Application deployment | `deploy/` |

For edit workflow and tests, see the [contributor guide](README.md).

## Execution and persistent state

Use the strict default of `lib.remote_utils.run()` for required mutations.
`check=False` returns a result for probes, optional work or verified installers;
inspect or verify it before treating the operation as successful. Timeouts raise
in either mode. Inventory direct calls without running setup code:

```bash
python3 scripts/audit_command_contracts.py --unchecked
python3 scripts/audit_command_contracts.py --json
```

The report describes syntax and result consumption; it does not prove correct
return-code handling. Review dynamic policies and wrappers separately.

Use `lib.atomic_io` for atomic persistent writes and bounded, regular-file JSON
reads. Distinguish missing state from corrupt/unsupported state; preserve the
latter and raise an actionable path-specific error. Use
`lib.operation_state` at durable operation boundaries and
`lib.unit_transaction.replace_units()` for managed systemd replacement.
Snapshots protect files and activation state, not arbitrary application data.
See the [transaction plan](../../plans/TRANSACTIONAL_EXECUTION.md) and
[operator recovery guide](../../TRANSACTION_RECOVERY.md).
