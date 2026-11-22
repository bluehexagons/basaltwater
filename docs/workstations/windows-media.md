# Native Windows file media inspection

Windows 11 x64 has an opt-in file analyzer using the version-1
`file-media-analysis` contract. This is independent of desktop capture and the
Linux controller. Optional Python 3.11+ runs only the file analyzer and its
qualification runner; ordinary Windows setup and AV discovery do not require it.

```powershell
# Setup choices; apply only when installation is intended.
.\install.ps1 -Profile agent_windows -AgentTool codex -AvTools -Python
# Existing installation:
basaltw refresh --av-tools
basaltw media doctor
basaltw media 'C:\media\音 sample.mp4' --output 'C:\private\new-analysis'
# From a checkout with a chosen native interpreter:
python scripts/inspect_media_windows.py 'C:\media\clip.mp4' --output 'C:\private\new-analysis'
```

`-AvTools` saves the selection across refresh/resume. It discovers preinstalled
FFmpeg and ffprobe independently and installs the exact `Gyan.FFmpeg` package
from WinGet's `winget` source only when either executable is missing. Each
original executable is preserved even when the other needs installation.
Validated executable paths are saved in current-user `BASALTWATER_FFMPEG_EXE`
and `BASALTWATER_FFPROBE_EXE`; discovery prefers those paths, then PATH, then
WinGet portable package directories. Direct Python invocation uses those
environment variables, then absolute PATH directories if a saved executable has
been removed. This recovers from a WinGet upgrade when the replacement is on
PATH. Open a new terminal after setup.

Gyan is a third-party Windows build. The AV step revalidates existing binaries.
WinGet-managed FFmpeg can be updated by the [bulk maintenance pass](windows-tool-selection.md#review-selections-from-a-checkout).
To retry a failed Gyan package upgrade on its own, apply
`winget upgrade --id Gyan.FFmpeg --exact --source winget`, then rerun discovery
and qualification. Exact-source WinGet installation of Gyan.FFmpeg 9.0.2,
native discovery and the public source `media doctor` route pass. A full
workstation installer/refresh and a future version upgrade remain separate
acceptance.
The file-only qualification below records the discovered build's decoder results.
Keep decoder security updates current.

## Result and media contract

The [Linux media guide](media-analysis.md) describes shared metadata, stream
ordinals, timing, frame sampling, contact sheet and audio summary fields.
Windows retains those fields and the same WAV/FLAC/Ogg/Matroska/WebM/streamable
MP4 allowlist. Forced demuxers receive only an immutable in-memory snapshot over
`pipe:0`, with a pipe-only protocol allowlist and MOV external references disabled.
Playlists and unrecognized headers are `unsupported`. Seek-dependent or
truncated media, missing tools, decode errors and exhausted limits are
`incomplete`. Absent audio/video streams are separately `missing`. Interrupting
the analyzer retains `cancelled` when receipt storage remains available.
Only `complete` returns exit 0. Existing output directories are refused.

Three PNGs retain requested times, actual decoded PTS, image hashes and source
SHA256; the labelled SVG embeds the PNGs. Stream offsets/rotation remain in
metadata. The selected video receives a full bounded decode check. Audio records
channel/overall peak and RMS, integrated loudness, estimated true peak and
silence intervals. Sparse pictures and signal levels do not establish perceived
quality, sync, HDR, physical playback or GPU readiness.

## Windows containment and privacy

Use absolute paths on a local fixed drive with an existing trusted output
parent. UNC/device paths, alternate data streams (including streams attached
to an otherwise ordinary input), reparse components and special inputs are
refused. The input and its ancestors are held open against replacement, with
write/delete sharing denied on the source while reading. The captured bytes
and SHA256 identify all decoder inputs. Paths with spaces and Unicode are
literal arguments; no shell evaluates them.
Decoder children receive a minimal environment, excluding inherited FFREPORT,
provider credentials and user configuration variables; temporary paths point
inside the owned output directory.

A newly created output directory has a protected DACL granting its owner,
SYSTEM and administrators access; derived files inherit that ACL. A persistent
`.basaltwater-media.lock` file in the output parent is opened with no sharing
for admission. One operation per parent is admitted; the empty marker remains
after release. Different parents still require caller-owned admission.
Trust the output parent and avoid concurrent modifications by the same account.

Defaults are 128 MiB input, 120 seconds media and a 180-second total subprocess
deadline. Overrides `--max-mib`, `--max-seconds`, `--timeout` have ceilings of
512 MiB, 600 seconds and 900 seconds. Fixed metadata ceilings are 16 streams,
1920×1080 declared pixels, 8 channels and 8–192 kHz. Decoder allocation requests
are capped at 64 MiB; decoder/filter/PNG threads are one. Each subprocess starts
suspended and is assigned to a non-breakaway, kill-on-close Windows Job before
vendor code executes. Job limits are 1.5 GiB aggregate committed memory,
90 seconds of user CPU and eight processes. These differ from Linux
address-space/CPU limits.

Captured stdout/stderr together are bounded to 2 MiB. Output disk checks poll
every 10 ms, refusing any file above 16 MiB or bundle above 64 MiB; FFmpeg also
receives a 16 MiB output-file limit. Polling can overshoot between checks and is
not a disk quota. At least 128 MiB free space is required without reservation.
Input snapshot and Python interpreter memory are outside the child Job limit.
Resolution changes and pathological decoders can still fail within declared
metadata ceilings. These are refusal ceilings; the small native measurements
below do not establish throughput at those ceilings or a decoder sandbox.

Timeout, Ctrl+C and Python interruption terminate the entire owned Job and
confirm that its active count reaches zero. Abrupt analyzer termination closes
the Job handle and kills its members, but can prevent receipt creation. Partial
artifacts remain private for diagnosis; disk/power failure can prevent a final
manifest. Require a complete manifest before consuming results. Retain only as
needed and remove the owned bundle explicitly; no automatic deletion, upload,
device recording or audio-routing change occurs. Process hostile media in a
disposable VM without sensitive mounts/credentials, as an unprivileged account.

## Native qualification

Offline checks require no AV installation:

```powershell
pwsh -NoProfile -File windows/tests/Run-MediaTests.ps1
python -m unittest discover -s windows/tests -p test_media_windows.py -v
python -O -m unittest discover -s windows/tests -p test_media_windows.py
```

After FFmpeg/ffprobe installation/discovery, run from the checkout:

```powershell
python scripts/qualify_media_windows.py --output 'C:\private\new-media-run'
```

The new protected bundle contains the common 18 synthetic fixtures, linked
editable JSON recipe, trim/fade/scale/concatenation exports and analysis receipts.
It gates silent/no-audio cases, Unicode/spaces, VFR, low-rate/shorter/delayed
video, rotation, offset audio, packet-derived timing and malformed/truncated/
playlist refusal. Duration tolerance is 0.1 seconds, VFR sample lag at most
0.13 seconds, decoded RGB boundary mean absolute error below 10/255. It also
gates missing tools, deadline failure and real Python interruption while owning
a media child, with Job cleanup and a cancelled receipt. The separate offline
process fixture checks a real descendant tree. This instrumentation does not
qualify delivery of Ctrl+C through every terminal host.

Only successful runs write `qualification.json`. Explicit gates remain active
with `python -O`. Reports retain executable paths, effective limits, elapsed
time, Job CPU, peak Job commit and artifact byte counts; individual manifests
record versions and measurement filters. Raw paths/media/logs stay private.
On native Windows 11 x64 with Python 3.11 and 3.15.2 and Gyan FFmpeg/ffprobe 9.0.2,
the common 18-fixture suite passes, including deadline and cancellation cleanup.
The synthetic 2–6 second, at most 640×360 inputs analyze in under one second
per valid fixture in the initial run. The retained bundle is under 8 MiB;
the largest measured production Job commit is under 84 MiB. Decoded RGB boundary
errors are below 3/255. These are single-run observations with uncontrolled warm
caches; they do not establish sustained throughput, whole-machine memory,
1080p/120-second capacity or physical playback quality. Existing WinGet package
discovery and version probes pass; the qualification runner itself performs no
installation or upgrade. Separate [Windows evidence qualification](windows-evidence.md)
covers synthetic GPU-window capture, debug-video timing and owned-process audio.
Physical playback, device recovery, HDR fidelity and game outcomes require
their own checks.
