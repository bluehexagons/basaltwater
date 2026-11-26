# Observe and control Windows applications

[Topic index](windows-desktop.md)

## Observe a native application

```powershell
basaltw desktop doctor
basaltw desktop status
basaltw desktop windows
basaltw desktop windows --task TASK_ID --json
basaltw desktop environment --json
basaltw desktop inspect --pid 1234
```

These are native Windows commands. Doctor tests interactive desktop/API access,
token logon identity and readable control state without screenshots, input or
application launch. It uses the token's AuthenticationId plus Windows boot and
session identity; it does not assume a logon SID appears in the .NET group list.
See [Microsoft's token statistics](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-token_statistics).
Control state and keyboard-lease files are created with the current account's
SID as owner and a private ACL. An existing file with an unexpected owner
stops the operation. Status also reports human control state.
Window discovery returns current-account/session windows with
decimal handles, identities, physical geometry and monitor device IDs. It can
omit inaccessible windows. UI Automation inspection returns bounded controls
and references; some custom or embedded controls expose limited information.
If an element disappears while its window is being inspected, the observed
prefix is returned with `truncated: true`. This is incomplete evidence; observe
again when needed. Explicit stale root references and other provider errors
still fail, and actions always revalidate their target immediately before use.

Use `windows --task TASK_ID` with the ID from a running `desktop develop`
task. It returns windows owned by live members of that task's Windows Job
Object, including a game process started by a PowerShell or npm wrapper.
Use the returned window identity and generation for capture or input.
This lookup works without `--track-children`.

`desktop environment` returns a bounded, read-only [host environment report](windows-environment.md)
with GPU/driver and display geometry, desktop-mode refresh and API scaling.
It captures no pixels and leaves control state and observation references
untouched. Test sessions retain the same report automatically; unknown sections,
per-window DPI and HDR remain explicit rather than implying qualification.

For a window capture, substitute a freshly observed identity and generation.
The default PNG path is unique and private under `%LOCALAPPDATA%\Basaltwater\desktop\captures`.
Read it from `result.output`, or use `--output` to choose a new PNG path in
an existing directory.

```powershell
basaltw desktop screenshot --window 123456 --identity 'OBSERVED_IDENTITY' --generation OBSERVED_GENERATION
```

Alternatively supply an explicit `--monitor DEVICE` instead of the window
selectors. Capture does not raise the target. Window capture uses PrintWindow;
hardware-rendered applications may return incomplete or blank content. A PNG
file establishes capture completion, not visual correctness. Existing files
are never overwritten. Monitor captures can include other visible applications.

See the shared [observation contract](desktop-observations.md) for the same
lifecycle on Debian XFCE and CachyOS KDE Plasma and the platform-validation backlog.
A successful screenshot normally returns `result.observation_id` (32 lowercase
hex characters), `captured_at`, `artifact_sha256`, `geometry` (input width/height),
`image_geometry` (actual PNG width/height), `image_origin`, `desktop_origin` and
`coordinate_space`. It includes the session generation, control revision and
content-free `last_input` summary when one is available.
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
Token-bound `hold` carries the deadline through preparation and watchdog
startup to the first down. Both forms return an `input_id`, delivery state, and
`observation_consumed` to show whether the supplied token was cleared before
the attempt. A rejection before that point reports `observation_consumed: false`.
Add `--observe` to capture the same window after completed input. Read the
chosen private PNG path from `result.observation.output`. Use
`--observe --output NEW.png` to choose a path. The existing `--observe NEW.png`
form also works.
The capture waits 120 ms by default. Set `--settle-ms` to an integer from 0 to
5000. If control changes, another input intervenes, or capture fails, the
response preserves the completed input and reports the capture failure.

Use `map-point --capture FRAME.json --image-coordinates --x X --y Y` to map
exact PNG pixels. For points in a resized view, supply `--view-width`,
`--view-height` and optional `--view-origin-x` and `--view-origin-y` margins.
Add `--to-x` and `--to-y` to map the second point for a drag. The command
checks the current observation and returns arguments for a separately
authorized input.

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
If Windows denies `window focus`, the reply reports `foreground_denied`.
If the discrete input guard rejects a background or minimized window, or
hold preparation rejects a background window, the reply reports
`foreground_required`, `submitted: false`, and
`input_delivery: not-sent`. Bring the target to the foreground in the
Windows session, refresh the window inventory, and take a fresh screenshot
for token-bound input. A lost hold-planning reply also reports
`input_delivery: not-sent`. Check `observation_consumed` to tell whether
the attempt used the screenshot token.
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
