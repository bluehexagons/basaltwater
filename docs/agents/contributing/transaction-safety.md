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
| Setup runtime activation | `lib.setup_common` | Stage replacements beside the runtime, restore previous source or managed payloads on failure or interruption, and retain private backups when restoration cannot finish. |
| Private setup payloads | `lib.setup_payloads` | Use the `link_payloads()` context manager for lease links; partial publication and interrupted execution remove only that call's links, preserving preexisting or replaced entries. |
| Managed systemd units | `lib.unit_transaction` | Replacement and grouped removal stage and verify unit files, snapshot activation state, and restore the previous configuration when activation fails. |
| Application deployment | Static and manifest deployment orchestration | Stage a sibling release, retain the previous tree through activation and health checks, and preserve recovery state after interruption. |
| Generated Nginx configuration | Nginx reconciliation | Use its own lock, durable snapshots, `nginx -t`, reload verification, and recovery marker. This is separate from application release activation. |

Grouped unit rollback restores services before their timers or paths. Native
mount pairs require the automount to start before its matching mount: systemd
rejects starting an automount over an already mounted path. Keep this ordering
when extending `lib.unit_transaction`, and model that constraint in unit tests.
Rollback quiesces every requested activation, including activators whose normal
activation was never reached. If any prior definition cannot be restored, keep
the group stopped and retain recovery evidence. Services staged only for a
timer or socket remain outside activation so executing oneshots are left alone.
If a rollback stop or disable fails, or activity cannot be confirmed stopped,
the group retains its live candidate files and private originals. It does not
restore files, reload or restart that group; recovery must reconcile actual
processes and both definitions before retrying.

`replace_units(..., verify=callback)` checks application readiness after unit
activation and before completing the operation under its lock. A raised error
uses the same rollback and retained evidence as an activation failure. Dry runs
skip it, and `preserve_unchanged` cannot bypass it. The callback should observe
readiness; effects outside the managed units are not rolled back. The panel
service uses this boundary for its HTTP socket check, including unchanged-unit
restarts after manifest changes.

`replace_units(..., start_once=(service,))` runs an idle oneshot after publication
and before arming its timers. Every activated timer must also be deactivated
before publication. Jobs observed running before or after timer quiescence stay
outside start/stop/rollback, including jobs that finish naturally during setup.
The operation records preserved jobs and its own start intents. An uncertain
start requires checked job quiescence before file restoration. Audit exporter
setup uses this boundary for initial collection and timer updates; generated
snapshot data is outside unit rollback.

`replace_units(..., prepare=callback)` runs preparation after candidate validation
and grouped deactivation, before publication and activation, under the same
operation lock. A callback failure restores the original unit files and
activation state through the existing rollback. Dry runs skip the callback.
Callers must document callback side effects: filesystem ownership, credentials
and other application changes are outside unit rollback. Use this boundary for
mountpoint preparation so an active automount cannot trigger remote access or
redirect ownership changes onto a mounted remote root.

`remove_units(..., prepare=callback)` uses the same preparation boundary after
all grouped deactivations and before any unit file is unlinked. Use it for
checked application teardown that must succeed before removing the definitions.
Callback failures restore unit files and activation state through the existing
rollback; callback side effects are not rolled back. Dry runs and removals with
no managed files skip the callback.

For existing managed definitions, `LoadState=not-found` does not establish
that a unit is inactive: a reload after unlinking its file can leave processes
running. Deactivation checks activity and enablement independently, stops active
units before preparation, and disables enabled units even if they are unloaded.
Callers remain responsible for active units whose managed files are missing,
because grouped removal skips missing files.

Recovery decisions made before acquiring operation ownership must pass the
inspected record as `expected_record` to `OperationStateStore.transition()`.
Matching only the operation ID can replay a stale checkpoint after another
owner has recorded an interrupted mutation.

Maintenance timer provisioning treats environment values, script paths and
sandbox writable paths as literal data. Quote complete systemd words, escape
backslashes and quotes, and double percent signs to prevent specifier expansion;
`ExecStart` arguments also require doubled dollar signs to prevent variable
expansion. Shell quoting alone does not preserve these values in a unit file.

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
