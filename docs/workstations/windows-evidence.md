# Windows game and AV evidence

Windows supplies three explicit collection routes alongside the ordinary
PrintWindow `desktop screenshot` command. Captures and traces are private data,
without input authority. Use source CLI help until a tested revision is installed:

```powershell
pwsh -NoProfile -File windows/Invoke-Basaltw.ps1 desktop capture --help
pwsh -NoProfile -File windows/Invoke-Basaltw.ps1 desktop pacing --help
pwsh -NoProfile -File windows/Invoke-Basaltw.ps1 desktop audio --help
```

Native helpers build on first use with the existing optional x64 MSVC/Windows
SDK tools. No compiler installation occurs. The private cache binds the source,
build contract and executable hashes; a changed binary is refused. Static CRT
builds do not require a separate Visual C++ redistributable. These collectors
do not enable control, change display/audio settings or automatically elevate.

## GPU window capture

Observe the window, then copy its exact selectors:

```powershell
basaltw desktop windows
basaltw desktop capture --window HWND --identity IDENTITY --generation GENERATION --width WIDTH --height HEIGHT --output C:\private\new-capture --milliseconds 2200 --frames 5 --interval-ms 400
```

The Windows Graphics Capture helper pins HWND, process creation identity,
account, session and caller. It uses a hardware D3D11 device and refuses
unsupported, minimized, hidden, closed, moved or resized targets. There is no
automatic PrintWindow fallback. Lock/secure desktop is unsupported. A separate
watchdog bounds a stalled COM/GPU/shutdown call to 20 seconds and stops the
helper on caller exit; a forced stop has no successful receipt.

Bounds are 100–10000 milliseconds, 1–120 frames, 16–10000 milliseconds between
selected frames, eight megapixels, 16 MiB encoded PNG per frame and 64 MiB total
PNG output. Duration can end before the requested frame count; inspect the
actual frames. These are sparse samples, not a promised recording frame rate.
GPU/COM runtime allocations are outside the encoded-byte bounds.

Each PNG has a SHA256 and a sequence record containing WGC's system-relative
100-ns frame timestamp, acquisition QPC/frequency, image dimensions, per-window
DPI, outer-window origin, image origin, client offset and client dimensions.
The frame journal is flushed after each completed image. The manifest is saved
before dispatch and retains errors; interrupted bundles stay incomplete.
Notification counts are diagnostic: source-frame loss remains null.

Per-frame display snapshots query the nearest monitor's active paths and
refresh rational. With a supported SDK/OS, Advanced Color Info 2 reports SDR,
WCG or HDR mode and HDR support/user enablement separately. Failed or
unsupported queries retain unknown state. This uses Microsoft's
[display configuration query API](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ne-wingdi-displayconfig_device_info_type),
without changing settings. The captured PNG is BGRA8 SDR output; HDR fidelity,
physical scanout, latency and AV synchronization remain separate assertions.
WGC images always have null observation IDs; use a fresh ordinary screenshot
for guarded input.

## Process pacing

Explicitly install the optional, hash-pinned
[PresentMon 2.6.0 console release](https://github.com/GameTechDev/PresentMon/releases/tag/v2.6.0),
or supply that reviewed executable with `--presentmon`:

```powershell
basaltw desktop pacing install
basaltw desktop pacing --task TASK_ID --project C:\repos\sample-game --output C:\private\new-pacing --seconds 5
```

Only the retained live task root, or an exact retained `--member PID:FILETIME`,
can be selected. Account, session, creation identity and Job membership are
rechecked natively. Child selection requires `--track-children`. Process-name
matching and unrelated PIDs are unsupported.

ETW requires appropriate Windows permissions. An access-denied trace is a
failure, never a smooth run. Use an explicitly approved elevated qualification
or an account already permitted to collect traces; this command never changes
account groups or raises UAC itself. Keep project launches in their ordinary
limited context and elevate only the collector when required.

The bundle retains raw v2/QPC CSV, tool/hash/version, selected identity, private
logs, clock anchors and cleanup evidence. Summaries group CPUFrameTime by swap
chain with mean, maximum, hitches over 50 ms, and p95 only with at least 20
samples. Empty, mixed-process, malformed or nonmonotonic data fails. Event loss
remains null when unavailable. CPU frame intervals do not establish physical
presentation or input latency; OpenGL/Vulkan have PresentMon-specific limits.
This console route retains CSV rather than raw ETL.

Collection accepts 1–30 seconds. PresentMon has its own timed exit; the native
supervisor watches caller/target and polls 64 MiB CSV/2 MiB diagnostic ceilings.
These are polled limits, not filesystem quotas. Each trace owns one retained
random session name. After interruption inspect that bundle, then reconcile
only its named session:

```powershell
basaltw desktop pacing cleanup C:\private\retained-pacing
```

Do not stop arbitrary ETW sessions or replay project input to repair evidence.

## Project events and audio

Capture and pacing accept `--events C:\private\project.jsonl`. This opt-in data
journal has at most 128 KiB, 1024 records and 4096 characters per line. Each
record has schema_version 1, strictly increasing sequence, UTC, QPC, QPC
frequency and a bounded event name; phase is optional. Unknown fields and an
incomplete trailing record fail. Producer clock provenance and missing events
remain project-owned. No journal contents execute commands. Correlate clocks
only within their recorded session/boot and retain uncertainty.

[Owned-process audio](windows-process-audio.md) supplies explicit bounded WAV
capture with unrelated-process exclusion, cancellation and original-caller
lifetime. [File media analysis](windows-media.md) supplies independent bounded
FFmpeg/ffprobe inspection, timestamped samples and signal measurements.

## Qualification

Offline checks never open a window, trace ETW or activate audio:

```powershell
powershell -NoProfile -File windows/tests/Run-CaptureTests.ps1
pwsh -NoProfile -File windows/tests/Run-ProcessAudioTests.ps1 -NativeBuild
```

Separate opt-in native runners create only disposable owned fixtures:

```powershell
pwsh -NoProfile -File windows/tests/Qualify-GraphicsCapture.ps1 -Output C:\private\new-gpu
pwsh -NoProfile -File windows/tests/Qualify-CaptureWatchdog.ps1 -Output C:\private\new-watchdog
pwsh -NoProfile -File windows/tests/Qualify-ProcessAudio.ps1 -Output C:\private\new-audio
```

Graphics qualification independently checks both color phases of a synthetic
D3D11 window and metadata. Its stop marker requests normal exit through the
disposable profile, so cleanup works even when desktop input is unavailable.
The runner waits for the supervised task to settle and verifies profile removal.
`-Pacing`
adds a process trace and requires observation of deliberate workload hitches;
it needs existing ETW permission. Do not elevate the entire fixture runner.
The watchdog runner deliberately stalls its main thread without touching the
GPU, proving the actual independent deadline/caller-loss path. These tests
establish only their recorded fixtures; other GPUs, mixed DPI/monitor origins,
HDR, physical device recovery and game-specific outcomes require target tests.

With optional Python/FFmpeg, preserve the actual WGC intervals in a bounded
lossless debugging clip and independently qualify its decoded pixels and PTS:

```powershell
python scripts/qualify_windows_capture_video.py --capture C:\private\retained-capture --output C:\private\new-debug-video
```

The runner checks frame digests, fixed dimensions within 1920×1080 pixels,
2–120 strictly increasing timestamps within ten seconds and at most 32 MiB
source PNGs. Contained media subprocesses retain the Windows Job/disk/log
limits from file analysis. The native five-frame run passes exact decoded-pixel
hash parity and a maximum 2 ms timestamp-error gate. It does not invent constant
frame spacing, add audio or establish physical AV synchronization.
