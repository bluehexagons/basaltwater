# Native Windows task diagnostics

Use `basaltw desktop develop diagnose TASK_ID` before cancelling a suspect run,
or afterward to retain its native exit evidence. This works for ordinary
supervised tasks and test sessions. It records a new private JSON snapshot
under the task's `diagnostics` directory and returns its `SnapshotPath`.

```powershell
basaltw desktop develop diagnose TASK_ID
basaltw desktop develop logs TASK_ID
basaltw desktop develop cancel TASK_ID
basaltw desktop develop wait TASK_ID --timeout 60
```

## Identity and scope

`ChildPid` in the normal receipt identifies the contained PowerShell launcher.
New workers also publish `target.json` with the actual executable's PID and
Windows creation FILETIME before resuming its suspended thread. That file is
published atomically under the same bounded state lock used by readers. Startup
refuses an existing target file rather than replaying over an old identity.

When the native exit code is observed, the launcher updates `target.json` with
`State: exited` and the unsigned 32-bit `ExitCode`. Diagnostic reports also show
`ExitCodeHex`, useful for Windows exception-style statuses. Codes are evidence,
not a diagnosis of the underlying cause. Cancellation, deadline expiry, guardian
loss or final evidence-write failure can leave only the initial `created`
record, with a null exit code. Do not reconstruct a native exit from an unrelated
or recycled PID. Older tasks can have no native identity at all.

The default live probe selects only this native root. For npm, Python or a
project wrapper, that means the wrapper executable. For child diagnostics,
start `exec`, `node` or `run` with `--track-children`, then use:

```powershell
basaltw desktop develop exec --project F:\Repos\game --track-children -- node.exe adapter.js
basaltw desktop develop diagnose TASK_ID --tree
basaltw desktop develop diagnose TASK_ID --member PID:FILETIME
```

`--tree` returns up to 32 retained members plus live resource samples and errors.
Copy a member's exact `Token` for `--member`; arbitrary PIDs and unretained or
stale creation tokens are refused. Legacy tasks and tasks launched without
tracking explicitly do not support either selector. The default root report
and root-only tasks retain their existing behavior.

The guardian samples exact named Job membership every 200ms (plus sampling and
publication time), validates owner/session/creation time, and pins query and
synchronize process handles for up to 128 retained identities, including
replacement workers. `members.json` is private, bounded to 128 KiB, and published
under the existing state mutex and atomic writer only when the catalog changes.
Roles distinguish helper, native root and child. `ParentToken` is included only
when a verified retained parent is still live and predates the child's creation;
otherwise it is null. No executable arguments or environment are cataloged.

Coverage is always partial: members that start and exit between samples may be
missed. `Overflow`, catalog errors, omitted sample counts and terminal
`UnobservedExits` remain explicit.
An observed exit retains unsigned `ExitCode` and `ExitCodeHex` from the pinned
handle, even after worker replacement or task cleanup. Exited members never
cause a PID lookup. Missing exit observations remain unknown, including after
guardian loss; terminal cleanup alone does not invent an exit code. Live selected
members undergo the same identity, membership, account and session checks as
the root on every counter sample.

Before reading resource counters, the isolated worker checks creation time,
current account/session and membership of the task's specific named Job Object.
Stale or unrelated identities fail closed. No live process is queried when the
receipt is terminal or reports uncertain/interrupted work. A process that exits
during collection produces an unavailable report rather than a live sample.

The launcher releases its temporary Job handle before publishing target evidence;
the diagnostic worker releases its query handle immediately after membership
verification. Neither keeps the Job alive during slower evidence/counter work:
[Windows terminates members when the last kill-on-close Job handle closes](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects#managing-job-objects).

## Report and limits

`State: live` contains cumulative kernel/user CPU seconds, current working-set
and private bytes, handle count, thread count and capture UTC. These are a
point-in-time root-process observation, not CPU percentages, tree totals, a
profiler trace or proof that an application is hung. Compare retained samples
with project observations when investigating a stall.

`State: settled` contains retained identity/exit evidence and no live counters.
It describes a task whose receipt confirms terminal cleanup, including failed
or cancelled tasks; diagnostic success does not turn a failed test into a pass.
`State: unavailable` retains the error and any valid identity information but
does not invent resource measurements. All reports include the observed task
state; diagnosis never changes that receipt, control state or application.

Exit 0 means a live/settled report was retained; exit 1 means unavailable data,
invalid input, exhausted retention or an evidence-write failure. The native
probe uses the existing eight-second worker deadline. At most 32 snapshots per
task are accepted, each limited to 32 KiB. Existing snapshots are retained;
further requests fail explicitly. The command does not cancel a task on timeout.
Use `status`, `logs` and the same task ID to reconcile an uncertain result.

Only identity, exit codes and counters are collected: no debugger attachment,
memory dump, stack trace, module/command-line inventory, window messages, pixels
or process mutation. Keep reports with private logs and session evidence. Test
sessions index `target.json` and `diagnostics`; profile removal leaves both
intact. Crash dumps and aggregate process-tree profiling remain separate future work.

Native qualification on 2026-11-02 passed the full Windows suite and a separate
interactive Scheduled Task session: live root counters, settled exit evidence,
profile removal and retained snapshots were observed. Real owned-process tests
also rejected stale creation times and a process outside the task's Job Object.
Game-specific crash and hang diagnosis remains unqualified.

Child diagnostics qualification uses `windows/tests/Run-DevelopmentProcessTests.ps1`
(or `-ChildDiagnosticsOnly` for the three child-focused cases). It exercises real
owned Windows processes and Job Objects, replacement after child exit 7, selected
worker counters, stale/unrelated tokens, terminal exits, cancellation and guardian
loss while an independent sleeper survives. Scheduler registration, notifications
and idle-sleep requests are mocked; this is not a new installed-Scheduler or game
qualification. `Run-DiagnosticsTests.ps1` covers bounded reports, malformed
catalogs, unavailable samples, terminal no-probe behavior and unchanged catalogs
with temporary files and mocked process workers on PowerShell 7 and 5.1.
