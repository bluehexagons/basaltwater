# Bounded Windows input leases

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
accepts up to eight distinct supported scan-code keys joined by `+`, including
CTRL/SHIFT. Existing single-key and CTRL/SHIFT requests retain their ordering.
Duration is 50–5000 milliseconds. Optional `--repeat-ms` is 50 through duration;
it submits repeated downs of the final key at absolute QPC deadlines. Without
that option, the request submits one down and one reverse-order release. It
does not promise hardware typematic behavior. The ownership record explicitly
supports masks through 255 (eight keys); the private record remains 64 bytes.

Choose exactly one hold selector: `--key`, `--timeline`, or `--button`.
`--timeline '0:W+J,150:J,250:A+J,350:-' --milliseconds 450` specifies desired
held sets at strictly increasing offsets. The first offset is zero, all offsets
precede expiry, `-` means no keys, and the union contains at most eight keys.
At most 32 events and 2048 characters are accepted. The complete timeline is
mapped and validated before any injection. Retained keys are not re-pressed;
ups precede new downs. A late event stops without catch-up or replay. Timelines
and mouse leases cannot use `--repeat-ms`.

Use `input move --x X --y Y` with the usual observed target selectors to place
the pointer, then reobserve. `input hold --button left --x X --y Y
--milliseconds 400` requires that exact pointer start. Buttons are `left`,
`right` or `middle`. Add both `--to-x` and `--to-y` for a bounded linear drag.
Both endpoints must lie inside the observed outer window. Moves carry a
synthetic marker and use the virtual desktop coordinate space, including negative
monitor origins. Target placement, foreground, geometry and pointer occlusion
are guarded throughout. Moves are scheduled every 20 ms; this is an OS input
schedule, not a game-frame trajectory. Partial delivery stops without replay.
`input wheel --x X --y Y --delta 120` accepts nonzero multiples of 120 within
-1200..1200, with the same target and physical-input guards as clicks.

`--observation ID` consumes the latest window capture once, before preparation.
Its absolute capture deadline survives plan construction, watchdog startup and
driver admission and is checked immediately before the first down. Expiry after
an accepted first down does not renew or extend the finite lease. Monitoring
captures during active/uncertain ownership have null IDs even if cleanup settles
during capture. Only a new capture after confirmed cleanup can authorize input.
Optional `--observe NEW.png` captures after successful input and cleanup; capture
failure retains completed input and must never be repaired by replaying the hold.

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
the human's keystrokes. If outside input touches an owned key/button, cleanup does not
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

An explicit [warm desktop worker](windows-worker.md) reuses the preparation and
driver process across requests. The independent release watchdog remains
per-lease; worker reuse does not waive any lifetime or foreground checks.

Private evidence lives below `%LOCALAPPDATA%\Basaltwater\desktop\leases\ID`.
The request freezes mapped scan codes/layout and observed identity. Driver and
watchdog reports retain UTC/QPC anchors, QPC frequency, monotonic start/end,
bounded sequence-numbered send records, scheduled and actual send-entry times,
lateness, requested/accepted counts, stop reason and release ownership. Anchors
describe separate clock observations, not a calibrated physical input timestamp.
Target stops use `foreground-changed`, `identity-changed`, `target-unavailable`,
`geometry-changed` and `layout-changed`; control stops use `control-changed` or
`control-unavailable`. Guard evidence retains `stop_field`, `expected` and
`observed` (each value at most 256 characters). `stopped_qpc` precedes release;
`ended_qpc`, release state and cleanup confirmation describe separate work.
Scheduler retirement must succeed before `cleanup_confirmed` becomes true.
No captures, raw physical-input log or application assertions are inferred.

Run `windows/tests/Run-HeldInputTests.ps1` in Windows PowerShell 5.1 for offline
ownership/transport coverage; it replaces all input calls and uses temporary
state. Real SDL qualification must separately prove down/up delivery, held state
across multiple ticks, explicit repeat, focus-loss cancellation and release after
driver/caller loss. Keep these results distinct from installed revision health
and game-specific assertions in the [acceptance record](windows-testing.md).

The new multi-key, timeline, pointer and observation routes have offline coverage,
not a live source qualification claim. The parent may run the opt-in source probe:

```powershell
powershell.exe -NoProfile -Sta -File windows/tests/Qualify-DesktopInput.ps1 -EvidenceDirectory 'F:\private\new-input-probe'
```

It opens one disposable WinForms window, retains effects/receipts and closes only
its owned child. It does not refresh installed code or change human control.
Run with no active warm worker.
Repeat with `-Warm` and another new evidence directory to exercise an owned
warm worker; the runner stops only the worker it starts. Separately qualify SDL
frame-polled overlap, cold/warm monitoring captures, capture expiry during slow
watchdog startup, fullscreen reason codes, physical takeover, cancellation and
hard caller/driver/watchdog loss. The probe alone does not establish these cases.
