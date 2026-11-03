# Windows desktop and development integration

The 2026-11-02 CachyOS feedback pass initially included Windows feedback in error.
Windows follow-up is owned by a separate workstream. Commit `d6f3f191` already
added scan-code keys/chords and offline test cases, but those cases and live
SDL/WinForms delivery were not run on CachyOS. The Windows owner should review
and qualify that change; it is not a native Windows pass or an installed refresh.
The task/Ninja guidance from `ae5fe136` also awaits native recipe validation.

The subsequent Windows review reconciled those changes with bounded input
sequences, target-thread layout mapping and partial-delivery cleanup. The full
native Windows offline suite and focused desktop/CLI contracts passed on
2026-11-02, along with both documentation checkers. Live SDL/WinForms delivery,
frame-polled game input and the Ninja recipe remain unqualified; no refresh was
performed by that review. See the [game-testing plan](../plans/windows-game-testing.md)
for the remaining facilities and acceptance checks.

The native `agent_windows` setup installs stable PowerShell 7 when missing and
continues the pinned installation in it before applying development tools.
Windows PowerShell 5.1 remains the initial bootstrap and the bounded UI Automation
worker. Setup preserves switches, arrays and literal paths across the handoff.
`-Plan` neither installs PowerShell nor starts a replacement shell.

The same one-command installer and saved `basaltw refresh` selections supply
these features. No separate desktop installer is required. See
[Windows setup](windows.md) for installation and recovery.

## Terminal and artifact handoff

Setup adds a **Basaltwater** Windows Terminal profile and a **Basaltwater
Development** Start Menu shortcut. These open PowerShell 7 with current account
PATH settings. The Terminal profile is a managed JSON fragment; setup does not
replace personal Terminal settings or select a new default profile. Reapplying
setup refreshes the Basaltwater fragment and shortcut.
MSIX installations use the verified stable app execution alias so the profile
and shortcut continue working when PowerShell's versioned package is replaced.
The native CLI and bounded Windows PowerShell worker rebuild their own module
paths, avoiding incompatible PowerShell 7 modules inherited through intermediaries;
see [Microsoft's module-path guidance](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_psmodulepath).

`$PSVersionTable.PSVersion` reports the invoking shell. The prepared development
shell and supervised task guardians use PowerShell 7 (`pwsh.exe`). The public
`basaltw.exe` launcher transports literal argv to Windows PowerShell 5.1 for its
compatibility entry point, and desktop observation/control uses a separate,
hidden worker bounded to eight seconds. Doctor's `worker: WindowsPowerShell-5.1`
identifies that UI Automation backend, which loads Windows' installed .NET
Framework assemblies; it does not mean project commands run in PowerShell 5.1.
Invoke `basaltw` normally from PowerShell 7. Child module-path isolation preserves
the caller's `PSModulePath`; no global module-path changes or shell downgrade are
needed. Keep internal worker functions behind the public CLI. Use
`pwsh.exe -NoProfile -File SCRIPT.ps1` for project scripts that need PowerShell 7.

```powershell
basaltw desktop open 'F:\builds\package.zip' --reveal
basaltw desktop open 'F:\reports\result.html'
basaltw desktop terminal --project 'F:\Repos\game' --msvc
basaltw desktop notify --title 'Build finished' --message 'The package is ready.'
```

`open` uses the file's Windows association; `--reveal` selects it in Explorer.
`terminal --msvc` activates the supported x64 Visual Studio environment inside
that terminal. It does not change global PATH, project runtimes or compiler pins.
Notification delivery depends on the Windows session and notification settings;
task receipts/logs remain authoritative.

## Observe a native application

```powershell
basaltw desktop doctor
basaltw desktop status
basaltw desktop windows
basaltw desktop environment --json
basaltw desktop inspect --pid 1234
```

These are native Windows commands. Doctor tests interactive desktop/API access,
token logon identity and readable control state without screenshots, input or
application launch. It uses the token's AuthenticationId plus Windows boot and
session identity; it does not assume a logon SID appears in the .NET group list.
See [Microsoft's token statistics](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-token_statistics).
Status also reports human
control state. Window discovery returns current-account/session windows with
decimal handles, identities, physical geometry and monitor device IDs. It can
omit inaccessible windows. UI Automation inspection returns bounded controls
and references; some custom or embedded controls expose limited information.
If an element disappears while its window is being inspected, the observed
prefix is returned with `truncated: true`. This is incomplete evidence; observe
again when needed. Explicit stale root references and other provider errors
still fail, and actions always revalidate their target immediately before use.

`desktop environment` returns a bounded, read-only [host environment report](windows-environment.md)
with GPU/driver and display geometry, desktop-mode refresh and API scaling.
It captures no pixels and leaves control state and observation references
untouched. Test sessions retain the same report automatically; unknown sections,
per-window DPI and HDR remain explicit rather than implying qualification.

For an explicit capture, substitute a freshly observed window identity and
generation and choose a new PNG path in an existing directory:

```powershell
basaltw desktop screenshot --window 123456 --identity 'OBSERVED_IDENTITY' --generation OBSERVED_GENERATION --output 'F:\reports\window.png'
```

Alternatively supply an explicit `--monitor DEVICE` instead of the window
selectors. Capture does not raise the target. Window capture uses PrintWindow;
hardware-rendered applications may return incomplete or blank content. A PNG
file establishes capture completion, not visual correctness. Existing files
are never overwritten. Monitor captures can include other visible applications.

See the shared [observation contract](desktop-observations.md) for the same
lifecycle on Debian XFCE and CachyOS KDE Plasma and the platform-validation backlog.
A successful screenshot normally returns `result.observation_id` (32 lowercase
hex characters), `captured_at`, `geometry` (input width/height), `image_geometry`
(actual PNG width/height), `image_origin`, `desktop_origin` and `coordinate_space`.
Both geometry fields are `[width, height]` pairs, as on Linux.
Image origin is `[0,0]` in `window` or `monitor` coordinates; desktop origin is
separate and can be negative. Window identity and its entire rectangle are
revalidated after PrintWindow and before publishing the receipt. A failed native
capture removes its own partial output and preserves preexisting files.

During an active or uncertain keyboard lease, screenshots remain available for
monitoring but return a null `observation_id` and
`observation_unavailable_reason: keyboard-lease-active-or-uncertain`. They clear
preceding capture authority. Eligibility is checked before capture, so cleanup
finishing during capture does not authorize that image. Capture again after
confirmed key release, ownership closure and watchdog retirement before binding
another action. `capture_freshness` still describes the monitoring image's age.

`result.capture_freshness` contains `age_seconds`, `remaining_seconds`,
`max_age_seconds` and `fresh`. Age starts at capture admission, includes worker
and capture time, and uses Windows' monotonic Stopwatch clock. UTC rollback does
not extend it. `--max-age-seconds INTEGER` accepts 1–600, defaults to 60, and
is fresh through its age limit. Increase it explicitly only for stable turn-based work.
A capture can already be expired when returned. Freshness establishes elapsed
time and target continuity checks; it promises no semantic or scene validity.

To bind the next input to that image, supply `--observation ID` alongside all
existing `--window`, `--identity`, `--generation`, `--width` and `--height`
selectors. Use dimensions from the receipt's `geometry`. The latest successful
window capture establishes the identity for this route without another fresh
`windows` inventory. Native foreground, identity, geometry, physical-modifier
and occlusion guards still apply. A monitor capture cannot authorize window
input. Capture itself still uses the existing window-discovery selectors.

Only the latest successful capture token is retained in private state. Another
successful screenshot supersedes it; failed capture leaves it intact. Tokens
are single-use: input consumes the token durably before attempting dispatch,
including failed or uncertain input, and cannot replay it. Any desktop mutation
or control/session change invalidates it. Status and windows queries never renew
the token. Input without `--observation` and existing UI Automation/window-action
paths retain their existing guards and reference lifetimes.
Status exposes the current ID and freshness without renewing either. Discrete
token input rechecks the capture deadline inside cold and warm native workers.
Token-bound `hold` is explicitly unsupported pending watchdog deadline plumbing;
use the existing window-inventory hold route. Windows post-input capture remains
follow-up work rather than an implemented `--observe` option.

## Act on observed controls

```powershell
basaltw desktop element set-text --ref OBSERVED_REF --generation OBSERVED_GENERATION --text 'Example'
basaltw desktop window resize --window 123456 --identity 'OBSERVED_IDENTITY' --generation OBSERVED_GENERATION --width 1000 --height 700
basaltw desktop input key --window 123456 --identity 'OBSERVED_IDENTITY' --generation OBSERVED_GENERATION --width 1000 --height 700 --key ENTER
```

Observe again after mutations or an uncertain result. Window and element
references expire after 60 seconds and are invalidated before mutations.
Window actions verify their observed outcome; `invoke` and raw input explicitly
require application-specific verification. Raw input requires the window to
remain foreground with the observed geometry. Click coordinates are relative
to that window; multi-monitor origins and DPI scaling are handled by the worker.
Input supports scan-code keys, atomic Ctrl/Shift chords, Unicode text and clicks.
Named keys include ENTER, ESC, TAB, SPACE, arrows, BACKSPACE, DELETE, HOME, END,
PAGEUP, PAGEDOWN, INSERT and F1–F12, plus A–Z and 0–9. For example, use
`--key CTRL+SHIFT+HOME` with `input key` or `input chord`.
`desktop input --help` lists operations; `desktop input key --help` lists keys
and limits without desktop calls.
Unicode text is limited to 1024 UTF-16 code units and does not exercise an IME.
Scan codes use the target thread's keyboard layout and extended-key flags;
Unicode `input text` remains separate
from physical game bindings. See Microsoft's [keyboard event contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput).

`input sequence` uses the same window selectors and `--keys 'HOME,SHIFT+END,F11'`.
It accepts at most 32 chords and 1024 characters, validates the entire sequence,
then submits one [SendInput batch](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).
Each chord releases keys in reverse order. Sequences have no delays;
games polling keyboard state per frame may miss a tap. Use a bounded
[`input hold` keyboard lease](windows-held-input.md) for continuous state and
explicit repeat, with independent release-watchdog evidence. Use separate observed
steps when application transitions require waiting. Queue acceptance does not
prove application delivery. Partial delivery fails without replay and attempts
key releases. Worker termination or OS rejection cannot guarantee release;
inspect application and key state before retrying. Existing human-control, physical-modifier,
foreground, identity and geometry guards apply. Input does not bypass UAC or a
locked desktop.

Restoring a minimized window resumes its previous presentation, which can be
maximized. Restoring an already maximized, non-minimized window returns it to
normal size. Both outcomes are verified against the state before the action.
Close succeeds only after the window handle is destroyed. Applications can hide
their window during shutdown; that remains pending, and a surviving hidden
window or save prompt times out. Inspect before retrying an uncertain close.

One controller operates at a time. Human control state persists for this account:

```powershell
basaltw desktop control human-override --human-approved --seconds 900
basaltw desktop control disabled --human-approved
basaltw desktop control enabled --human-approved
```

Only an explicit human decision authorizes a control-state transition. Agents
can perform requested desktop work while enabled without another approval or
lease. Pause blocks input, window actions and artifact/terminal handoffs while
leaving observation available. The optional override timer restores enabled
control when it expires. Secure/alternate desktops and other accounts/sessions
are outside the adapter. GUI automation follows the active browser-tool policy;
this backend does not grant permission to substitute another browser.

## Supervised project tasks

Use [test sessions](windows-test-sessions.md) for disposable profiles,
project-defined readiness, project reservations and retained evidence. The
[game-testing facilities plan](../plans/windows-game-testing.md) records native
input acceptance and the remaining capture, pacing and recovery increments.

For a long T3 workload, submit once without waiting. Save its ID and initial
receipt in private task notes before calling `wait`. Record the executable/runtime
from its dry-run plan, worker source path/revision/dirty state and project revision.
Keep `basaltw info --json` (saved installation), `basaltw doctor --skills --json`
(bundle comparison against saved source) and selected checkout revision separate;
checkout success does not install it. Session-loaded skill revision can remain
unknown. Do not update the worker checkout while its task is active.

After interruption, inspect that same ID with `status`, `logs` and `wait`. Retain
the final receipt with exit/cleanup/timing/truncation and its task/log directory
under `%LOCALAPPDATA%\Basaltwater\development`. Caller wait timeout does not
authorize duplicate work. Project runners may need a verbose option for live detail.

```powershell
basaltw desktop develop --help
basaltw desktop develop doctor --project 'F:\Repos\game'
basaltw desktop develop node build --project 'F:\Repos\game' --dry-run
basaltw desktop develop node build --project 'F:\Repos\game' -- --verbose
basaltw desktop develop exec --project 'F:\Repos\game' -- node.exe script.js
basaltw desktop develop exec --project 'F:\Repos\native' --msvc -- cl.exe /c main.cpp
basaltw desktop develop editor --project 'F:\Repos\godot-game'
basaltw desktop develop run --project 'F:\Repos\godot-game'
basaltw desktop develop import --project 'F:\Repos\godot-game'
basaltw desktop develop status TASK_ID
basaltw desktop develop wait TASK_ID --timeout 120
basaltw desktop develop logs TASK_ID
basaltw desktop develop cancel TASK_ID
```

Tasks return IDs and run through limited interactive Scheduled Task guardians,
independently of the invoking T3 terminal. Project code starts only after its
process has entered an owned Windows Job Object. Basaltwater helper shells use
process-local execution-policy bypass without changing account or machine policy.
Cancellation and timeout settle
the process tree before a terminal receipt is published. Interrupted or uncertain
cleanup blocks setup/reboot; inspect the task rather than blindly replaying it.
Failed dispatch never replaces a running or completed guardian receipt.
Terminal publication includes verified Scheduler retirement and private-request
removal. An unregister failure leaves `cleanup-uncertain` with no completed
timestamp, so `wait` cannot report settled work while registration remains.
State readers and publishers share a per-file lock with a five-second acquisition
limit. Publication replaces an existing file from a complete temporary snapshot;
receipt validation and reading hold the same lock. Concurrent CLI polling sees
complete snapshots or an explicit busy error. This coordination covers the
Basaltwater state helpers; direct file readers do not participate in the lock.
`develop status` exits nonzero for failure, timeout, cancellation, interrupted
work or uncertain cleanup; queued/running/complete queries exit zero.

`develop wait TASK_ID [--timeout SECONDS]` polls for confirmed completion, with
a default caller wait of 60 seconds and a range of 1–86400. It returns one JSON
object containing `WaitState`, `TimedOut`, `TimeoutSeconds`, monotonic
`WaitedSeconds` and the authoritative `Receipt`. Exit zero means completed
success; exit one means a completed task failed, was cancelled/timed out, or
needs attention because launch or cleanup is uncertain. Exit 124 means the
caller stopped waiting while the task remained active. Wait timeout and Ctrl+C
do not cancel the task. Task timeout is separate, recorded in `Receipt.State`.
Use `logs` to collect output, and `cancel` only when you intend to stop the task.

Cancellation retires an unstarted task under the setup and guardian locks,
removes its registration and private environment, and prevents late execution.
A running guardian receives a cancellation request; confirm its terminal status.
If that guardian is gone during the same boot, inspect the task and reboot
Windows to establish cleanup. After a real boot, cancellation or setup retires
abandoned work as `interrupted` without replaying it. Scheduler or environment
cleanup failures remain visible and block maintenance.

`exec` accepts a real executable followed by literal arguments after `--`.
For a script, use `pwsh.exe -File script.ps1`; batch files require an explicit
wrapper. `--env NAME=VALUE` applies to the task, and `--msvc` activates Visual
Studio only in its child. Environment snapshots are private and removed after
confirmed cleanup. No artifact-upload credentials are needed. Tasks temporarily
inhibit idle sleep while active and release that request afterward; they do not
change the power plan or disable locking.

`node NPM_SCRIPT` names a script in `package.json`, such as `build`; it does not
accept a JavaScript file path. Arguments after `--` go to that npm script.
It requires a numeric `.node-version`/`.nvmrc` or explicit
`--runtime VERSION` and an already installed fnm runtime. It runs that runtime's
npm and rejects incompatible `packageManager` pins. Other package managers need
an explicitly provisioned executable through `exec`; system Node is not silently
substituted and no runtime is downloaded. To run a JavaScript file, use
`exec --project PATH -- node.exe script.js`, or give an absolute Node executable.
`exec` uses the requested executable and does not select a project runtime.
Godot honors `GODOT_BIN`, then the managed engine selection. Engine
pins, export templates and renderer requirements remain project-owned.

Default task timeout is one hour; `--timeout SECONDS` accepts 1–86400. Logs retain
up to 4 MiB per stream: the first 2 MiB and a rolling last 2 MiB, preserving late
errors. Output below the cap is unchanged. The raw log concatenates the retained
head and tail without an inserted gap marker; discarded bytes belong to the
missing middle. During execution the rolling tail is published periodically;
the final snapshot settles before confirmed completion. `logs` returns the last
64 KiB of retained output. Receipts and logs live in
`%LOCALAPPDATA%\Basaltwater\development`. Headless commands need no input control;
GUI interactions remain subject to desktop handoff and readiness rules.

New receipts expose `ExecutionStartedUtc`, `ExecutionEndedUtc` and monotonic
`ElapsedSeconds`. The interval starts immediately before recording/opening the
contained child's gate and ends after process-tree, output and private-environment
cleanup. It includes child startup and cleanup, excludes queue/guardian preparation,
and never uses wall-clock subtraction. Cancellation and timeout include cleanup.
Unstarted, interrupted or uncertain work has no measured end/duration; legacy
receipts retain their original fields without reconstructed timing.
`StreamLimitBytes` is 4194304. Settled `Streams.Stdout`/`Stderr` report `LimitBytes`,
`RetainedBytes`, `DiscardedBytes` and `Truncated`; new head/tail records also
include `RetentionPolicy: head-tail`, `HeadBytes` and `TailBytes`. Unknown
accounting is null, and older prefix-only records are not reconstructed.
Excess bytes continue to drain, including for failed children. `logs` separately
reports `Tails` with returned/omitted byte counts and truncation for each stream.
Tail omission refers to retained bytes, not bytes discarded at the stream cap.

`develop doctor --project PATH` is presence-only discovery: it reports selected
PowerShell, pinned fnm Node, applicable Godot and trusted native Git paths, missing
requirements, and checkout revision/dirty state. It never probes project shims or
launches GUI tools. Git is selected only from Program Files Git installations,
with reparse paths rejected; unavailable/corrupt/unborn metadata stays `unknown`,
and a directory outside a checkout is `nongit`. Dirty state covers staged,
working and untracked changes, with separate `TrackedDirty` and `Untracked`
flags; submodule contents are excluded. Git output is bounded to 8192 characters.
Untracked discovery records presence without retaining a file listing.
Git queries disable hooks, fsmonitor, external diff/text conversion and declared
clean/process filters, ignore inherited Git overrides and global/system config,
and use optional-lock-free bounded queries. No manifest or recipes are read or
executed: runtime pins and conventional package/Godot files define discovery's
scope. Package-manager version compatibility remains task-plan validation.
Use workstation doctor separately for host version health and project commands
for build/render qualification. The recipe adapter remains deferred pending a
reviewed consuming workflow.

### Optional Ninja analysis build

For a project supporting Ninja, use a separate build directory and a project-owned
PowerShell script submitted through `develop exec --msvc -- pwsh.exe -NoProfile
-File analysis.ps1`. Inside that task-local MSVC environment:

```powershell
$ErrorActionPreference = 'Stop'
cmake.exe -S . -B build-analysis -G Ninja -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cmake.exe --build build-analysis --verbose
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not (Test-Path build-analysis/compile_commands.json)) { throw 'Compile database missing' }
```

Select/install Ninja explicitly if absent, apply normal project CMake options and
use a new directory rather than changing the regular generator. Validate SDK
headers/libraries with a disposable executable including `windows.h`, calling a
Win32 function and linking/running successfully; finding `cl.exe` is insufficient.
Point the project analyzer at the generated database. This recipe awaits native
qualification; MSBuild success does not establish a Ninja/analyzer pass. Global
shell changes are unnecessary. See [native input QA](native-input-qa.md) for
manual device checks.

## Maintenance and qualification

`basaltw refresh` submits a durable background job and follows its live output
by default, allowing T3 upgrades to close/restart the desktop without killing
setup. `--detach` returns its ID without following; Ctrl+C stops following
without cancelling setup. Use `refresh --status ID` and `refresh --logs ID` for
progress, or `--follow ID` to reconnect. `--wait` runs setup in the invoking terminal.
`refresh --cancel ID` can cancel a queued job before setup starts; an applying
package installer must finish before another attempt.
Status reports `cleanup-uncertain` when a running job loses its worker in the
current boot. An installer may have survived, so inspect its logs and reboot
before another detached refresh. After reboot, the next refresh marks abandoned
queued/running jobs `interrupted` while holding their claim and setup locks.
It starts a new job without replaying the abandoned one; `--status` returns
nonzero for failed, interrupted or uncertain jobs.
`--dry-run` remains read-only and foreground. Refresh logs are capped at 16 MiB.
Active or uncertain development tasks block setup/reboot; wait for them or cancel
and confirm cleanup. Explicit installer, Windows Update or component servicing
requirements queue a visible desktop dialog showing the reason and a 60-second
countdown with Restart now and Later buttons. Pending file operations alone do
not trigger reboot. `--no-reboot` defers reboot for this run; see
[restart conditions and status](windows.md) for details.

The workstation doctor checks stable PowerShell 7 and, when T3 is selected,
desktop readiness. Neither installed GPU drivers, an accessible desktop nor a
healthy Sunshine service proves rendering, audio, encoder or streaming readiness.
Qualify actual Moonlight video/audio/input, display/scaling changes, lock and
reconnect behavior using the [Sunshine guide](windows-sunshine.md).
Use the [Windows qualification procedure](windows-qualification.md) for recorded
workload evidence. Offline contracts and owned-process tests do not establish
unattended GUI access, UAC automation or hardware-rendered capture support.
