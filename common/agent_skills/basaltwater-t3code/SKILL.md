---
name: basaltwater-t3code
description: Diagnose, pair, update, and recover a Basaltwater-managed T3 Code server on Debian.
metadata:
  managed-by: basaltwater
---

# Managed T3 Code on Debian

For a service problem, start with `basaltw agent doctor --capability t3code --json`.
Inspect the failed checks and the per-user `t3code.service`. Use
`basaltwater-vm-triage` when the symptoms point to host pressure. On CachyOS use
`basaltwater-cachyos-t3code`. For native Windows use the
[Windows setup guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows.md).

- [Operations](references/operations.md) covers service logs, pairing, linked
  access, and server-side Git credentials. Keep credentials and raw logs private.
- [Updates and recovery](references/updates.md) covers refresh receipts,
  installed versus loaded policy, launcher repair, and downgrade guards.
  For an upgrade from pre-0.1.0, use [thread migration](references/thread-migration.md).
- [Execution environment](../basaltwater-project-development/references/t3-workflows.md#establish-the-execution-environment)
  covers account, provider, and skill discovery mismatches.
- [Preview host troubleshooting](../basaltwater-project-development/references/collaborative-preview.md)
  covers Chrome prerequisites, private networking, and certificate trust.

A service restart interrupts active sessions. Inspect the refresh completion
receipt and unit before retrying a queued update. For a guidance reload in one
thread, use T3's **Restart agent session** after the turn ends. For work inside
a thread, follow the active T3 instructions and tool schemas.
