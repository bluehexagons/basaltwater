# Native desktop application work on CachyOS

Use the existing KDE Wayland session and native packages. The managed
`basaltwater-cachyos-desktop` skill covers application scripts, assets, exports,
and native interaction. Discover only the needed application with
`basaltw agent manifest . --application blender --json`; executable presence
does not establish editing, export, GPU, or audio readiness.

For Godot, Electron, and native project launches over T3, Sunshine/Moonlight,
or SSH, use the [remote development guide](cachyos-development.md).
`basaltw desktop --native develop` launches supervised tasks without requesting
screen/input access, while honoring the persistent agent control state.
For manual game tests, use the [playtest procedure](game-testing.md) for fresh
worker qualification, scene verification, game-specific waits and bounded briefs.
Use [native input QA](native-input-qa.md) for multilingual IME, clipboard,
physical controller checks, environment records and evidence closeout.

## Agent permission and portal access

[Agent desktop control](desktop-control.md) explains the three persistent states:
enabled, human override, and disabled. Enabled agents can perform authorized
desktop work without asking to unpause or obtaining a control lease. The human
control window offers timed or indefinite takeover and explicit enable/disable
controls. Restarting a helper or closing a connection preserves this choice.
Open **Agent Desktop Control** from KDE's application menu even when the helper
is stopped or agents are disabled; setup installs the launcher with native tools.

KDE portal permission is separate. Start locally as the graphical desktop user:

```fish
basaltw desktop --native doctor
basaltw desktop --native start --remember
# Approve one monitor, keyboard, pointer, and persistence if KDE requests them.
basaltw desktop --native status
basaltw desktop --native handoff
basaltw desktop --native exec -- inkscape /absolute/task/icon.svg
basaltw desktop --native screenshot --output /absolute/task/observe-1.png
```

`--remember` opts into a reusable KDE grant. Later `start` calls restore it when
KDE permits; initial or withdrawn access can still require human approval.
Wait for `running` before input. Native helpers have no default lifetime limit.
An optional `--session-seconds` bounds backend availability without changing
agent permission. Nothing starts the helper at login.

If a monitor is asleep or capture is black/frozen, enabled agents should try
`basaltw desktop --native wake`, wait briefly, and inspect a fresh screenshot
before input. This bounded KDE display-power request needs no running portal
and preserves agent permission, screen locking and idle policy. See the
[native control reference](../../common/agent_skills/basaltwater-cachyos-desktop/references/native-control.md#sleeping-displays)
and [Moonlight recovery](cachyos-maintenance.md#sleeping-monitors-and-moonlight).
Native `doctor` now reports `checks.display_power` with observed output names,
on/off/mixed state and observation time. The local CachyOS doctor also includes
`session.display-power` prominently among deferred observations when asleep or
unknown. Queries are bounded and read-only; unlocked sessions and responding
XWayland sockets do not establish display power or SDL window readiness.
Unknown results stay unknown, including missing tools, timeouts and unrecognized
output. KDE may report only some outputs, so this is not a physical-panel check.

The [native control reference](../../common/agent_skills/basaltwater-cachyos-desktop/references/native-control.md)
is the operational guide for consent stages, saved grants, bounded capture/input,
accessibility, physical-key troubleshooting, stop/revoke, and recovery.
It is installed with the skill; use it for exact procedures and limits.

## Post-input observations

After inspecting a fresh capture and confirming focus, use its actual generation,
dimensions and coordinates:

```fish
basaltw desktop --native input click --generation GEN --geometry 2560 1440 \
  --x 400 --y 200 --hold-ms 120 --observe --settle-ms 150
basaltw desktop --native screenshot --next-frame --compact
```

`input --observe` completes and releases the bounded input, waits 120 ms by
default (configurable 0–5000), then opens a capture stream, skips its initial
sample and awaits a subsequent sample. Frame acquisition has an eight-second
budget; a static/stalled stream can time out. Capture failure retains completed
input timestamps and returns nonzero: inspect state and capture again rather
than replaying the input. A changed control revision cancels the observation.
This operation does not assert that an application's animation or rendering has
finished. Project-owned waits/assertions still decide readiness.

Native input returns `input_completed_at` (UTC) and
`input_completed_monotonic_ns` after release. Native captures include
`frame_received_at` (UTC), `frame_received_monotonic_ns`, `frame_pts_ns`, and
`captured_at` (PNG completion). Receipt time describes sample acquisition;
PipeWire/GStreamer PTS is stream-relative and is not a compositor presentation
time or directly comparable with monotonic input time. Skipping a sample reduces
initial-buffer ambiguity but cannot establish input-to-photon latency.

Physical game bindings can use `input keycode --codes 26` for left bracket or
`--codes 29 30` for left Ctrl+A. These are Linux evdev codes, without an XKB
offset or Godot key constants. Native chords accept 1–8 distinct codes (1–255),
the same bounded hold/observation options, and reverse-order release on
interruption. `status.input_state` reports pending helper-injected press counts
and named modifiers; physical and other providers' modifier state is unavailable.
See the [keyboard contract](../../common/agent_skills/basaltwater-cachyos-desktop/references/native-control.md#observe-act-verify).

Native `input drag --x X --y Y --to-x X --to-y Y --duration-ms 300` supports
bounded straight-line canvas gestures with buttons 1–3 and the same fresh
generation/geometry requirements. Cancellation releases the button, and failed
release stops the helper. `screenshot --crop X Y WIDTH HEIGHT` returns
`image_origin` and `image_geometry` alongside full-monitor `geometry`. Add the
origin to crop-image coordinates before input; the helper then converts monitor
pixels to portal logical coordinates. Crops remain selected-monitor observations.
If the PNG is presented at a different size, use
`basaltw desktop --native map-point --geometry WIDTH HEIGHT --view-geometry WIDTH HEIGHT
--x X --y Y` to calculate monitor-pixel input arguments before acting. Pass the
same `--crop` when mapping a cropped screenshot; optional `--to-x`/`--to-y` map
a drag endpoint. This read-only aid reports scale and origin, validates bounds,
and sends no input. It does not establish scene, focus or capture freshness.
See [mapping examples and viewer limits](game-testing.md#map-a-presented-image).
See the [native control reference](../../common/agent_skills/basaltwater-cachyos-desktop/references/native-control.md#observe-act-verify)
for bounds, interruption and mapping details. Drag/crop contract tests use mocked
system calls; live application interaction is a separate qualification.

`screenshot --compact` returns the image path, generation, geometry, timestamps,
pixel SHA-256 and `changed`, omitting the status envelope. Full screenshot
metadata remains the default; use `status`/`doctor` for diagnostics. Native
`changed` compares visible RGBA pixels, geometry and crop rectangle with the preceding successful
capture in this helper generation, ignoring row padding; the first comparison
is null. False means equal pixels, not a stalled app; true can include a cursor
or unrelated desktop content. Debian compact results retain available metadata,
without inventing native timing or change detection. Native screenshot help
hides window-capture switches; attempts still fail explicitly.

Native captures also include `artifact_sha256`, a SHA-256 of the PNG file bytes,
distinct from the visible-pixel digest. Compact output and evidence journals
retain it. This supports integrity checks without decoding or editing the image.

## Manual testing evidence

Start recording before the supervised launch and interaction loop, then finish
after inspecting the application's renderer/device and final task state:

```fish
basaltw desktop --native evidence start --project /absolute/project --json
# Launch with develop, observe/interact, then wait or stop only owned tasks.
basaltw desktop --native evidence finish --task TASK_ID \
  --renderer 'OpenGL; device observed in the application log' --json
```

One opt-in record per helper links the generation, Git revision/dirty state,
input completion metadata, captures and pixel hashes, native launch tokens and
exit snapshots, and development task log/report paths and process status.
Tasks created for that exact project during the recording are attached
automatically; repeat `--task` to include tasks launched earlier. Renderer text
is explicitly operator-observed, never inferred from installed GPU inventory.
It is not a project assertion or release qualification.

For session-scoped discovery use `develop list --project /absolute/project
--since 2026-01-15T10:00:00Z --until 2026-01-15T11:00:00Z --json`. Bounds include
their endpoints and require explicit timezones. The listing compares instants
in UTC and filters before limiting results to 50 matching tasks. Evidence
finalization uses its recording interval automatically, so older task history
does not consume that limit. `matches_truncated` reports excess matches;
`scan_truncated` reports the separate 500-entry scan bound. Either makes
`truncated` true; a bounded scan cannot establish that all session tasks were
found. Listing reads private records without starting or stopping tasks;
use each task's `status` for current process state.

Records live outside repositories in private
`~/.local/state/basaltwater/desktop-evidence/*.json` files (0700 directory,
0600 files). They reference existing images/logs rather than copying them;
preserve those artifacts with the record. The journal omits typed text, key
values, argv and portal credentials. It caps events at 500 and task attachments
at 50 and reports truncation; only this helper's desktop actions are observed.
Process snapshots can still be running or unverified and are not proof of
completion. Git lookup is bounded; unavailable revisions remain null.

Finishing freezes the journal before collecting task status outside the portal
request budget. If interrupted, or if the helper stops, use the saved path from
start/status with `evidence finalize /absolute/record.json --task TASK_ID
--renderer 'observed renderer' --json`. Stopped-helper checkpoints retain
`finalized: false` until this collection succeeds, and `recording_end_reason`
identifies helper shutdown; an in-flight operation may be absent from that
checkpoint. Status retains the last evidence path across helper exit.
Finalizing again refreshes task snapshots while retaining attached tasks and
the renderer annotation unless replaced explicitly. Disk failures are reported
as `evidence_error` without disguising completed input as a retryable failure.
Evidence finish/finalize never stops a process or closes the desktop. Review
captures and logs before sharing; records have no automatic cleanup timer.

Use `basaltw desktop --native evidence verify /absolute/record.json --json` to
check recorded capture files without a running helper or changing the record.
The result reports file-hash matches, missing/mismatched/unverified references,
truncation and finalization separately. Missing hashes on older captures remain
unverified; pixel hashes cannot substitute for PNG file hashes. Unsafe files,
empty capture inventories or truncated journals cannot report full success.
Only the first ten problems are printed, with an omission flag. See
[retention and verification limits](game-testing.md#retain-and-verify-evidence).

## Installation and verification

Selecting a supported desktop application during setup installs
`python-gobject`, `at-spi2-core`, `gstreamer`, `gst-plugins-base`,
`gst-plugin-pipewire`, `gtk3`, `libxkbcommon`, and `libkscreen`. Blender also selects
`libdecor`. Setup never starts automation or changes KDE preferences, account
groups, graphics drivers, or existing control permission. Rerun setup to install
runtime and guidance changes; restart an existing native helper to load them.
The CachyOS doctor reports prerequisites without starting automation.

Use `--native` explicitly; ordinary `basaltw desktop` addresses Debian XRDP.
The helper uses a private same-account Unix socket, without a network listener,
global input daemon, systemd service, or autostart.

During helper shutdown, `status` can encounter a reset connection. It retries
only that read-only query, up to three attempts with 50 ms between attempts.
Persistent resets remain an error, and invalid replies or timeouts are not
retried. `start` still refuses to launch while the previous helper holds its
session lock; wait for teardown and inspect status before trying again.
Input, launches and control changes are never replayed by this recovery.

Prefer application scripting for bulk edits and focused UI checks for interaction.
Use exact PID/name/role/root selectors, fresh monitor captures, task copies,
and isolated test profiles. Reopen editable sources and verify exports in their
consuming project. [Application recipes](desktop-development.md) explain native
API differences, companion files, formats, and render settings.

Capture/input permission does not establish every application's readiness.
Window inventory is partial AT-SPI coverage; it cannot prove compositor focus
or window absence. Window-specific capture, compositor window management,
clipboard and desktop logout remain unavailable. Monitor images
can include personal content; inspect evidence before sharing it.
