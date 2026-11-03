# Bounded Windows keyboard holds

Use a finite lease when a game polls keyboard state across frames. Immediate
`input key` and `input sequence` taps can occur entirely between two game ticks.
Projects still own frame timing, deterministic replays and gameplay assertions.

```powershell
basaltw desktop input hold --window 123456 --identity OBSERVED_IDENTITY --generation OBSERVED_GENERATION --width 1000 --height 700 --key W --milliseconds 600
basaltw desktop input hold --help
basaltw desktop status
basaltw desktop input release --lease LEASE_ID
basaltw desktop input lease --lease LEASE_ID
```

The hold requires the same fresh observed window identity, generation, geometry,
current account/session and enabled human-control state as ordinary input. It
accepts one supported scan-code key with optional distinct CTRL/SHIFT prefixes.
Duration is 50–5000 milliseconds. Optional `--repeat-ms` is 50 through duration;
it submits repeated downs of the final key at absolute QPC deadlines. Without
that option, the request submits one down and one reverse-order release. It
does not promise hardware typematic behavior. Mouse holds are unsupported.

Only one lease can own input at a time. Other mutations are refused while it is
active or cleanup is uncertain. Observation, explicit human control transitions
and lease cancellation remain available; the account controller mutex is not
held during the wait. `status.input_lease` identifies the latest active or
unreconciled lease. `release` requests cancellation of that exact lease and never
sends arbitrary key ups. Inspect `lease` until its receipt confirms cleanup.
A synchronous hold returns nonzero if it stops early, fails or cannot confirm
cleanup; retain `reason` and `duration_completed` separately from accepted sends.

## Lifetime and ownership

A limited interactive Scheduled Task starts an independent release watchdog
before the bounded input worker may press a key. It survives the calling CLI,
T3 terminal and development Job. The driver checks the watchdog heartbeat;
the watchdog pins the driver's PID/creation identity. Both monitor the exact
caller identity, foreground/window identity and geometry, keyboard layout,
cancel marker and unchanged enabled control revision. Secure/locked desktops
remain unsupported. An expired lease or failed guard stops repeats and releases
the owned subset. Repeats delayed beyond 100 ms or a full repeat interval stop
instead of catching up; the clock is checked again after guards and ownership
waits, immediately before a send.

The processes coordinate through a private bounded ownership record and named
mutex. Down intent is flushed before entering SendInput so driver loss leaves
the watchdog enough information to release potentially submitted keys. Partial
downs release only the accepted prefix; downs and failed repeats are never
replayed. Release proceeds in reverse order. A partial/uncertain release is
retained without automatic replay and blocks further mutations and maintenance.

The watchdog installs keyboard/mouse hooks before becoming ready. Its synthetic
events carry a per-lease marker. Outside input stops the lease without retaining
the human's keystrokes. If outside input touches an owned key, cleanup does not
send a synthetic up for that key: its ownership is marked `physical-owner` and
requires operator inspection. Windows has one combined key state and cannot
separately release a synthetic press while preserving a simultaneous physical
press of the same key. A requested key already held is refused before input.

Microsoft's [SendInput contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
does not reset existing keyboard state and remains subject to integrity checks.
No user-space facility can guarantee release if Windows rejects SendInput,
both safety processes are forcibly killed, or the machine loses power. Receipts
report `submitted`, `physical-owner` or `uncertain`; `submitted` establishes
queue acceptance, never application delivery. Do not blindly replay an uncertain
action. Active and uncertain leases block setup/reboot admission. Preserve the
lease ID and evidence for inspection if the caller disappears.

If the watchdog dies after the driver confirms release, explicit `release`
can reconcile the terminal ownership record once that exact watchdog is gone,
then retire its registration. A failed startup can likewise close unstarted
ownership under the mutex. Neither path injects input or retries uncertain ups.
After the watchdog is gone, an explicit release can also observe all leased
keys up on the current interactive desktop and settle prior physical ownership
or uncertain release without injecting input. That observation is recorded as
reconciliation, preserving the original send evidence. Scheduler query failures
remain uncertainty, not proof that a registration is absent.

After a verified later Windows boot, `status` identifies an old lease as
`other-session` with `previous_boot: true`. Explicit `release --lease ID` retires
its registration and metadata without replay; the old receipt is preserved.
A new logon/session during the same boot is insufficient for that recovery.

## Evidence and qualification

This version starts cold workers and a Scheduled Task for each lease. One
qualification sample took about eight seconds from CLI process creation to the
first down; this is an observation, not a latency guarantee. Correlate actual
send timestamps with game markers, rather than treating command invocation as
the press time. Frame-synchronous input remains a project protocol concern.

Private evidence lives below `%LOCALAPPDATA%\Basaltwater\desktop\leases\ID`.
The request freezes mapped scan codes/layout and observed identity. Driver and
watchdog reports retain UTC/QPC anchors, QPC frequency, monotonic start/end,
bounded sequence-numbered send records, scheduled and actual send-entry times,
lateness, requested/accepted counts, stop reason and release ownership. Anchors
describe separate clock observations, not a calibrated physical input timestamp.
Scheduler retirement must succeed before `cleanup_confirmed` becomes true.
No captures, raw physical-input log or application assertions are inferred.

Run `windows/tests/Run-HeldInputTests.ps1` in Windows PowerShell 5.1 for offline
ownership/transport coverage; it replaces all input calls and uses temporary
state. Real SDL qualification must separately prove down/up delivery, held state
across multiple ticks, explicit repeat, focus-loss cancellation and release after
driver/caller loss. Keep these results distinct from installed revision health
and game-specific assertions in the [acceptance record](windows-testing.md).
