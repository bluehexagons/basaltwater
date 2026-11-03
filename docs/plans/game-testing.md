# Game-testing capability follow-up

Use the [game-testing procedure](../workstations/game-testing.md) for available
capture/input, controller qualification, observer packets, retesting and cleanup.
This plan separates implementation needs from game-owned acceptance. Keep filled
briefs, actual inventories and raw evidence private; no particular game or
external test repository is required.

## Current boundaries

| Capability | State and next acceptance |
| --- | --- |
| Native pointer/keyboard targeting | Coordinate aid exists for resized full/cropped images and drag endpoints; each new gameplay controller still qualifies scene recognition and actual action effects. |
| Retained capture integrity | PNG file hashes and read-only evidence verification exist; file equality requires a separate review of what the image actually supports. Legacy records without file hashes remain unverified. |
| Long sessions | Journals are bounded at 500 events and 50 tasks. Use linked recording segments at safe checkpoints; any larger or streaming journal needs bounded storage, recovery and truncation acceptance before changing this contract. |
| Specialist assistance | Immutable-packet and deadline procedures are documented. A concurrent controller/observer trial must demonstrate shared artifact access, stale-advice rejection, exclusive live control and measured overhead before claiming a reliable team condition. |
| Model/resource comparisons | Actual model/effort, assistance, comparable settings, clocks and outcomes remain project-owned. Unknown usage/latency stays unknown; no automated model ranking is provided. |
| Virtual gamepad | No native gamepad action or qualified adapter exists. Implement and qualify the bounded route below before gameplay use. |

## Desktop observation follow-up

The [shared observation contract](../workstations/desktop-observations.md)
defines the implemented baseline: per-capture ages (1–600 seconds, default 60),
single-use observation IDs on Linux and Windows, explicit coordinate spaces,
mutation/control invalidation and capture admission clocks. Debian now supports
post-input capture and completion timestamps. Compact Linux output retains
window origins. Windows capture rechecks the target rectangle and removes its
own incomplete output on failure. Debian has native fixture coverage below;
CachyOS and Windows qualification remain open. Windows regressions are added
for the native pass. No model or gameplay benchmark is established by this
implementation.

### Debian XFCE qualification, 2026-11-03

A refreshed Debian 13 installation at `fe77475e` passed `desktop doctor` and
the live Geany smoke check. A fresh supervisor and a disposable native Tk
fixture then passed 15 public-CLI observation checks:

- Full-desktop, window and active-window captures, image-to-desktop mapping,
  pointer delivery, completion timestamps and `input --observe`.
- Supersession, single-use refusal, geometry and image-bound checks, and
  application-driven foreground, placement and resize rejection before input.
- Failed captures and read-only queries preserve the previous receipt without
  renewing it. A follow-up capture failure retained completed input and never
  replayed it or overwrote an existing file.
- Lifetime bounds, real default expiry after 60 seconds, one successful action
  after 90 seconds with an explicit 180-second capture, and the next capture's
  return to the default lifetime.

The fixture also exposed intermittent Unicode character loss in an unpaced
xdotool batch. The client now defaults non-ASCII text to revocable per-character
requests with 10 ms pacing. The checkout CLI, against the installed supervisor,
delivered three multilingual strings exactly, verified from application state.
Token-bound Unicode was refused before dispatch without consuming the receipt.
Single-use tokens still need server-paced Debian text; prefer accessible
`element set-text` with verification where available. The installed launcher
needs the next normal source refresh to receive this client change.

All fixture windows closed and their launch exited successfully. Raw receipts,
failed harness attempts and captures remain private. This pass did not qualify
RDP reconnect, display resize, human takeover, helper replacement during input,
clipboard transfer or actual games.

### Remaining platform acceptance

Run the following against disposable fixtures on each platform, recording the
tested revision and private evidence paths. Use the same match/save and timing
budget when comparing model workflows.

- [ ] Debian XFCE VM: qualify display resize/RDP reconnect, human takeover and
  helper replacement during input at a safe checkpoint. The native fixture
  coverage above establishes application-window resize rejection, not display
  resize or reconnect behavior. Repeat affected checks after backend changes.
- [ ] CachyOS KDE Plasma: safely restart the helper after updating; verify
  full/cropped capture IDs and scope, scaling, crop drag endpoints, holds,
  server-paced text, portal loss and human override during injection/acquisition.
- [ ] Windows 11: run `windows/tests/Run-DesktopTests.ps1` in Windows PowerShell
  5.1 (includes observation, warm-worker and transport regressions); compile
  native types, verify serialization and private-state migration, then qualify
  window captures and token input with cold and warm workers. Exercise mixed
  DPI, negative monitor origins, monitor-token rejection and window changes
  during PrintWindow. PowerShell/C# execution was unavailable in the Linux pass.
- [ ] All three: a default capture rejects input after 60 seconds; an explicit
  180-second capture can authorize one action after 90 seconds. New captures,
  mutations, helper/session replacement and human takeover invalidate old
  tokens. Failed captures and read-only status/window queries never renew age.
  Delayed target probes must not inject after the observation expires. Inspect
  application outcomes rather than scoring input acknowledgement as success.
  Debian's real-age, receipt reuse and failed-capture cases passed above; the
  other platform and lifecycle cases remain open.

Remaining implementation tasks, independently assignable after qualification:

- [ ] Apply the [reported Windows game-testing feedback](windows-game-testing.md#consuming-game-feedback-and-next-windows-pass):
  multi-key/timeline leases, wheel and mouse gestures, and specific target-stop
  diagnostics. Parent input help is fixed in source; native revalidation is open.
- [ ] Carry observation deadlines through Windows held-key preparation, watchdog
  startup and driver admission before enabling `hold --observation`. The new
  token route currently rejects holds explicitly; existing inventory-based
  holds retain their independent watchdog contract.
- [ ] Give Windows cold one-shot workers the same pinned caller/control and
  request-deadline envelope as warm workers. Discrete token input now checks
  capture expiry inside both workers; that is separate from full transport
  admission parity. Preserve structured submitted/uncertain outcomes through
  the public CLI instead of reducing them to generic error strings.
- [ ] Port post-input observation, compact capture output and explicit image
  coordinate mapping to Windows with the same completion-preserving failures.
  Add one-request server-paced Debian text before supporting single-use tokens
  with `--delay-ms`; per-character pacing cannot reuse a consumed token.
- [ ] Prototype guarded revalidation of a completed plan against a fresh
  observation. Bind the expected target and relevant scene conditions; start
  with conservative comparisons and measure false refusals from animation.
  A background recapture must never silently authorize old coordinates.
- [ ] Build a project-owned planner/operator trial using immutable packets,
  compact phase checkpoints and bounded escalation. Keep one operator in
  control; measure the full coordination cost before selecting lower effort
  or smaller models. Record actual harness settings and comparable outcomes.

## Bounded Linux virtual-pad adapter

Keep a prospective adapter in Basaltwater's desktop boundary, under the same
persistent human control policy and sole-controller discipline as native input.
Do not introduce a free-running input script, global remapping or additional
privileged daemon as a shortcut. Discover prerequisites read-only; any required
device/driver setup must have its own explicit selection and lifecycle.

The implementation must define an owned, identifiable device, explicit button,
d-pad, stick and trigger mappings, ranges, neutral values and bounded durations.
It must reject new actions while disabled or under human takeover. Completion,
error, timeout, disconnect, stop, permission change and worker death must release
buttons, center axes and destroy only the owned device as appropriate. A
watchdog independent of the requesting worker must cover worker death. Record
requested/completed event and neutralization timestamps privately without
inferring a game's response from device-event delivery.

Qualification proceeds in bounded stages:

1. A contained inspector verifies device identity, only the intended device,
   mapping/ranges, single buttons, d-pad, both sticks, triggers and neutral state.
2. Mocked failure tests and live inspector checks verify control-state rejection,
   cancellation, watchdog, neutralization and owned-device cleanup. Check drift,
   diagonals and repeat behavior; state which failure paths remain untested.
3. A game that actually exposes controller support verifies safe-menu navigation,
   confirm/cancel, prompt changes, then one short gameplay unit. Record native
   versus remapped input and mixed-input behavior while preserving settings.
4. Where safe and supported, test reconnect/hotplug and recovery. Retain event
   records and visual evidence. Mark features such as rumble untested when absent.

Promote support only for the platform/device/mapping/game combinations exercised.
Linux inspector success does not qualify another OS, browser gamepad behavior,
or an arbitrary game. This plan starts no device, installs no driver and schedules
no run; the adapter remains unimplemented.
