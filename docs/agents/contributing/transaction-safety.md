# Transaction safety

Basaltwater's rollback is bounded to managed resources. A transaction stages a
replacement, verifies it before activation, and retains recovery evidence when
the prior state cannot be restored. An incomplete operation blocks a conflicting
retry until the recorded recovery path is inspected.

## Ownership

| Concern | Owner | Contract |
| --- | --- | --- |
| Persistent files and state | `lib.atomic_io` | Atomic same-directory publication and bounded regular-file reads; corrupt or unsupported state is preserved and reported. |
| Durable operation boundaries | `lib.operation_state` | Versioned markers and locks identify the active attempt, record phases, and prevent a fresh operation from bypassing unfinished work. |
| Managed systemd units | `lib.unit_transaction` | Replacement and grouped removal stage and verify unit files, snapshot activation state, and restore the previous configuration when activation fails. |
| Application deployment | Static and manifest deployment orchestration | Stage a sibling release, retain the previous tree through activation and health checks, and preserve recovery state after interruption. |
| Generated Nginx configuration | Nginx reconciliation | Use its own lock, durable snapshots, `nginx -t`, reload verification, and recovery marker. This is separate from application release activation. |

## Limits

Transactions protect the resources owned by these mechanisms. They do not
reverse package installation, user changes, arbitrary scripts, application
data writes, or database migrations. SQLite backups require an explicit restore
decision. Two independently atomic state files are not one filesystem
transaction, and diagnostic logs do not provide rollback.

Never delete a stable lock or operation marker to bypass a blocked retry. Use
the [operator recovery guide](../../operations/transaction-recovery.md) to
inspect the recorded identity, phase, and resource state. Keep private recovery
evidence until the operation is verified complete.

## Contributor checks

Required mutations use the strict default of `lib.remote_utils.run()`. An
intentional `check=False` call must inspect or verify its result. The command
inventory can help locate direct calls without importing or executing setup
code:

```bash
python3 scripts/audit_command_contracts.py --unchecked
python3 scripts/audit_command_contracts.py --json
```

The inventory classifies syntax and result consumption; it is not semantic
certification. Review wrappers, dynamic policies, and shell expressions
separately. Add focused failure-propagation coverage at the owning orchestration
boundary when changing a transaction contract.

Live host behavior remains subject to the
[release qualification matrix](../../release-qualification.md). Mocked system
calls and temporary-directory tests do not qualify Debian, LXC, or Proxmox
behavior.
