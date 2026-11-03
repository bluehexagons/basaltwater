# Native Windows readiness and acceptance

Use separate evidence for installed-tool health, supervised execution, delivered
input and application behavior. Successful queue submission does not establish
which key an application received or whether its caret moved.

## Confirmed readiness

Native Windows qualification on 2026-11-02, through the installed launcher at
`b16d1ca0`, established the following scoped results:

| Surface | Observed result |
| --- | --- |
| Host and skills | All 28 host doctor checks passed; all five required skill bundles current. Absent optional bundles were informational; session-loaded provenance remained unknown. |
| Desktop | Interactive Windows PowerShell 5.1 worker healthy when called from PowerShell 7; human-control state unchanged. |
| Tool execution | Managed MSVC environment compiled and linked C++ against the Windows SDK and an existing SDL dependency tree. A fixture compilation error propagated as failure. A separate executable, GLSL-to-SPIR-V compilation and headless Godot script passed. |
| Project discovery | Antistatic had no missing discovered requirements. Discovery remains separate from a project build or release gate. |
| Environment | GPU/driver and desktop geometry, refresh and scale observations available without reported probe errors; also retained with test-session evidence. |
| Literal argv | Spaces, empty argument, literal quote and Unicode reached a native wide-character entrypoint unchanged. |
| Session | Disposable profile honored, synthetic settings confined to it, atomic readiness token accepted while the application lived. |
| Waiting and admission | A one-second caller wait returned 124 without cancelling the session; conflicting work for the same project was refused; admission resumed after completion. |
| Diagnostics | Live report matched the native root PID/creation identity and returned counters. Settled reports retained exit evidence without live counters. |
| Completion | Exit zero settled successfully. Deliberate native exit seven produced task failure, wait exit one and native exit `0x00000007`. Profile/request removed, native process gone, Scheduler registration absent; evidence retained. |

These are workstation smoke results, not a full project release qualification.
Use the [session](windows-test-sessions.md), [diagnostics](windows-diagnostics.md)
and [environment](windows-environment.md) contracts for limits and failure states.
Raw logs, captures, runtime identities and operational paths remain private.

## Navigation input correction

The installed `b16d1ca0` adapter had a confirmed SDL navigation defect: some
target layouts returned unprefixed scan codes even with `MapVirtualKeyEx` map
type 4. Inferring the extended flag only from an E0 prefix delivered keypad
keys. LEFT, for example, arrived as SDL scancode 92 (keypad 4), not 80.

Corrected source preserves layout-aware scan-code lookup and explicitly marks
the supported dedicated navigation VKs as extended. Both down and up events
retain that flag. Offline regressions exercise unprefixed and E0-prefixed maps,
ordinary nonextended keys, atomic sequences and reverse-order partial releases.
Foreground, identity, geometry, physical-modifier and human-control guards stay
in force. Refresh to the corrected revision before relying on SDL navigation.

Real SDL event qualification compares received events independently of CLI
acceptance. The expected dedicated scancodes are:

| Key | SDL scancode |
| --- | ---: |
| LEFT / RIGHT | 80 / 79 |
| UP / DOWN | 82 / 81 |
| HOME / END | 74 / 77 |
| PAGEUP / PAGEDOWN | 75 / 78 |
| INSERT / DELETE | 73 / 76 |

On 2026-11-02 a disposable real SDL 3 fixture reproduced the installed LEFT
failure, then received all ten dedicated navigation scancodes from corrected
checkout source through the guarded CLI. Down/up events were balanced.
`CTRL+SHIFT+HOME` delivered SDL scancodes `224,225,74`, with CTRL and SHIFT set
on HOME; `HOME,SHIFT+END,F11` delivered `74,225,77,68`. Backspace and F11 also
passed independently. The installed source was not patched during qualification.

The earlier installed review also delivered balanced presses/releases, Ctrl+A
with CTRL, Backspace, F11 and exact Japanese, accented, combining-mark and emoji
text. Direct Unicode delivery does not establish IME composition or clipboard
behavior. Immediate down/up taps do not establish frame-polled held input.

## Run repository checks

Run the native suite in Windows PowerShell 5.1, whose framework includes the
UI Automation assemblies. Its process fixtures exercise PowerShell 7 guardians;
loading the framework-only desktop test type directly in PowerShell 7 is not
the supported test route. From the repository in PowerShell 7:

```powershell
$testCode = '$env:PSModulePath = [Environment]::GetEnvironmentVariable("PSModulePath", "Machine"); & .\windows\tests\Run-NativeTests.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand ([Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($testCode)))
```

The full suite and focused desktop regressions passed on the corrected source.
Offline tests mock desktop calls; they do not replace real SDL event or
application-state acceptance. Do not mix event-fixture control with another
agent driving the same desktop.

## Confirmed consuming-game behavior

Corrected-source qualification also drove Antistatic's real feedback text-entry
widget through its SDL/native input path in a disposable profile. Starting from
`abcdef`, Left/Right moved the caret to 5/6, Home/End to 0/6, Shift+Home selected
the complete value, and Ctrl+Shift+Home retained the expected selection. The
`HOME,SHIFT+END` sequence selected all six characters; Delete removed the
selection and left an empty value with caret/selection at zero. All nine
application-state assertions passed, and the supervised process/profile settled.
No feedback was submitted and the ordinary game profile was not used.

This confirms those caret/selection outcomes in the tested game widget. Other
game widgets, IME composition, clipboard shortcuts and physical
controller behavior remain separate acceptance work. That initial pass used the
corrected checkout CLI; installed `b16d1ca0` requires refresh.

### Refreshed installed qualification

The installed `58206546` launcher subsequently passed ten real SDL input cases,
including dedicated navigation keys, Ctrl/Shift chords, bounded sequences and
exact committed Unicode text, with balanced key releases. Six rejection checks
covered duplicate modifiers, unsupported/empty sequence items, sequence/text
limits and changed geometry. No events reached the fixture for those requests;
an invalid trailing item did not submit the preceding valid HOME.

Antistatic then passed 27 real-game assertions using only the installed public
input commands. The checks repeat grapheme navigation/deletion, selection and
replacement, rejected over-limit edits, acceptance/cancellation and focus, and
add combined-key sequences plus selection surviving resize and F11 fullscreen
and window restoration. No private SendInput helper, clipboard manipulation or
feedback submission was used. Unsupported emoji glyph coverage remains separate.

A project adapter explicitly applied the supervised profile and published live
readiness after advancing frames with a valid render target. Normal game exit,
explicit fixture cancellation and screenshot smoke all settled; generated
profiles/private requests and Scheduler registrations were removed. The ordinary
game profile's inventory, hashes and modification times were unchanged, as was
human-control state. Full source/native project gates passed separately; those
results do not qualify a current clean package or a physical input/audio device.

## Owned workers and held keyboard state

Subsequent checkout qualification on 2026-11-02 exercised opt-in
[`--track-children`](windows-diagnostics.md) through an actual limited
interactive Scheduled Task and a disposable Node adapter. The first worker's
exit 7 survived replacement; exact replacement selection returned live
counters, a changed creation token was refused, and cancellation retained
terminal member exits while removing the profile and Scheduler registration.
Owned-process regressions separately cover guardian loss and an unrelated
sleeper surviving. Sampling remains partial for short-lived processes.

[`input hold`](windows-held-input.md) qualification used a real SDL 3 window
and its frame-polled keyboard state, separately from CLI queue acceptance.
A 600 ms W lease spanned more than 50 application ticks with balanced down/up.
Explicit 100 ms repeat produced repeated downs and one final up. Further
scenarios exercised owned-window minimize/focus loss, explicit cancellation,
driver termination, caller termination and watchdog termination. SDL held state
cleared in each case; watchdog-loss cleanup required explicit reconciliation
after the surviving driver released. Focus loss can itself clear SDL state;
the release receipt separately establishes the guard's up submission.

Offline coverage checks partial downs, reverse-order subset cleanup, uncertain
ups without replay, generic-modifier physical ownership, keypad/E0 distinction,
control revision changes, Scheduler query failure and closed ownership before
admission. Explicit observed-up and later-boot recovery use mocked state; no
physical-key takeover, actual reboot, locked desktop or forced OS rejection was
performed. Those boundaries remain separate qualification work.

The full native suite passed during implementation, followed by focused desktop
and ownership checks on the final changes. Raw event logs, timestamps, task IDs
and failure receipts remain private. This qualification uses checkout source;
it does not establish that the installed launcher has been refreshed, or prove
any game's movement/attack assertions. WGC recording, PresentMon and process
audio remain planned.

## Warm workers and reusable adapters

Checkout qualification on 2026-11-03 exercised explicit
[warm desktop workers](windows-worker.md) through a private native launcher
pointing at the checkout, without patching the installed source. A real limited
interactive Scheduled Task reused its native types and process across fresh
requests. SDL received balanced LEFT events, `CTRL+SHIFT+HOME`,
`HOME,SHIFT+END,F11` and exact 260-unit UTF-16 text containing Japanese, accented,
combining-mark and emoji characters. This qualifies committed Unicode delivery,
not IME composition, clipboard handling or new game-widget assertions.
An owned WinForms TextBox also passed Home/End caret movement,
Ctrl+Shift+Home and sequence selection, Delete, exact Unicode insertion and a
balanced mouse click. Per-key down/up counts were balanced. These are separate
toolkit checks; they do not establish every control's shortcut behavior.

Five warm LEFT CLI calls took about 2.27–2.32 seconds each; preceding cold
samples took 3.36–3.80 seconds. Separate read-only helper probes were about
0.36–0.43 seconds warm versus 1.14–1.18 seconds cold. These small sequential
samples included other test activity and are not a controlled benchmark, p95,
latency guarantee or input-to-display measurement. Hold watchdog startup remains
per lease and must be included separately when measuring held-input latency.

Warm SDL holds passed repeat, owned-window minimize/focus loss, cancellation,
caller loss, watchdog loss and worker loss. Held state cleared with balanced
events; watchdog loss required explicit receipt reconciliation. An outside-input
event during a separate startup correctly refused the lease before down and
settled cleanup. No physical input was suppressed or global release injected.
Worker loss required explicit worker stop to reconcile its registration; no
input was replayed in a replacement worker.

The full native suite passed, followed by focused desktop checks on final guard
changes. Mocked-API tests with real owned helpers cover expired requests, stale
caller creation identities, source drift, disabled control, single-worker
admission, timeout, late startup after retirement and maintenance blocking.
Forced partial keyboard/mouse submission remains mocked qualification.

The [Node adapter example](node-test-session-adapter.md) passed its eleven
owned-process acceptance cases and a real supervised run: live readiness,
observed owned child, normal exit, retained artifacts and removed profile.
Its assertions are synthetic. Raw events, process identities, timings and
cleanup receipts remain private. Those checkout results preceded the installed
qualification below.

### Installed warm-worker qualification

After refresh on 2026-11-03, installed `13e1e8af` reported complete setup,
passed all 28 host checks, skill inventory verification and desktop readiness.
Session-loaded skill provenance remained unknown. A real supervised task using
the managed MSVC environment compiled and linked a Windows SDK program, then
executed it successfully.

Through the installed native launcher, a warm worker delivered balanced SDL
LEFT events, `CTRL+SHIFT+HOME`, `HOME,SHIFT+END,F11` and exact 260-unit Unicode
text. An isolated WinForms fixture passed Home/End, selection, Delete, Unicode
and mouse-click checks. Its multiline control exposed no editable ValuePattern;
semantic value-setting was explicitly unqualified. These smoke checks do not
extend the earlier consuming-game or physical-device qualification.
Both GUI sessions completed with removed disposable profiles and retired
Scheduler registrations. The warm worker also stopped cleanly; final desktop
readiness passed. Raw operational evidence remains private.

## Repeatable acceptance

1. Record installed and tested source revisions, toolkit/runtime and keyboard
   layout in private evidence. Run `basaltw doctor --json`,
   `basaltw doctor --skills --json` and `basaltw desktop doctor` first.
2. Use a disposable owned SDL/text fixture or an isolated game profile. Preserve
   the current human-control state; only an explicit operator decision changes it.
3. Observe/focus the owned window and reobserve identity, generation and geometry
   before each request. Stop on foreground loss or an uncertain outcome.
4. Check all dedicated navigation keys, `CTRL+SHIFT+HOME`, and
   `HOME,SHIFT+END,F11` against the application's event log, including balanced
   releases and modifier state. Keep F11 separate in applications where it changes
   fullscreen/geometry; a sequence cannot wait for that transition.
5. Check the consuming game's actual caret and selection after each action.
   Include Home/End, Left/Right, Shift selection, Ctrl+Shift+Home and selection
   deletion. A correct SDL event alone does not establish text-editing behavior.
6. Confirm terminal cleanup through the original task ID and retain both success
   and refusal evidence. Do not relaunch blindly after a caller wait timeout.

Physical controllers, audio/streaming, GPU-window capture, frame pacing, HDR and
per-window DPI still need their own acceptance evidence. See the
[Windows game testing plan](../plans/windows-game-testing.md) for deferred work.
