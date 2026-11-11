# Linux file media analysis and production

Use `basaltw-media INPUT --output NEW_DIRECTORY` after installing the Python
package, or `python3 -m lib.media_cli INPUT --output NEW_DIRECTORY` from a checkout.
This is an offline Debian and CachyOS CLI, independent of desktop sessions and
the existing native WAV capture/analysis API. `basaltw agent manifest --json` discovers
FFmpeg/ffprobe. Existing opt-in `--av-tools` supplies them; a minimal manual
installation is `ffmpeg` and `util-linux` (for `prlimit`) from the distribution.
Keep their distribution security updates current. No all-media bundle or default editor
selection is added.

The command accepts WAV, FLAC, Ogg, Matroska/WebM and streamable MP4 with an
`ftyp` header. MP4 usually needs a front-loaded index (`+faststart`). Other
containers, playlists, unknown timing and unavailable selected streams return
`unsupported`; failures and decoder/resource errors return `incomplete`.
Noncomplete results exit 1. Missing audio or video is recorded independently.
Select zero-based stream ordinals with `--video N` and `--audio N`.

The version-1 `file-media-analysis` manifest records the immutable input's
SHA256 and byte count, tool versions, complete stream metadata (including time
bases, offsets and rotation), limits, selected streams, errors and elapsed time.
Duration comes from the container or, when absent, the demuxed packet span;
unknown timing is unsupported. FFprobe packet output is bounded to 2 MiB and
discarded after deriving timing. Frame records contain requested sample time,
actual decoded PTS, image hash and source hash. PTS is in FFmpeg's normalized
input timeline; original stream start times remain in metadata. VFR samples
can land later than requested. Frames undergo FFmpeg autorotation and software
scaling; this does not qualify color/HDR presentation.

The three PNGs and embedded, labelled `contact-sheet.svg` sample the selected
video's beginning, middle and near-end, using its packet PTS range normalized
to the input timeline. They do not use a longer audio stream's end time; a
low-rate or single-frame stream can yield repeated images. Video without packet
timestamps is unsupported. Actual decoded PTS remains the frame's timing evidence.
The selected video also receives a bounded full decode check. Audio results
include per-channel/overall sample peak and RMS, integrated
loudness, estimated true peak and silence intervals at -50 dB for 0.25 seconds.
Nonfinite signal levels become JSON null (including digital silence); missing
peak/RMS fields for the overall summary or any selected channel fail the operation.
No semantic, perceived-quality, sync, physical playback or clipping diagnosis is claimed.

## Limits, privacy and failure handling

Defaults are 128 MiB input, 120 seconds and a 180-second total subprocess
deadline. Overrides `--max-mib`, `--max-seconds` and `--timeout` have ceilings of
512 MiB, 600 seconds and 900 seconds. Fixed limits are 16 streams, 1920×1080
declared pixels, 8 audio channels and 8–192 kHz. Each child has a 1.5 GiB address
space, 90 CPU seconds, 64 MiB single-allocation and 16 MiB output-file limit;
decoder/filter/encoder thread selections are one. These are refusal ceilings,
not promises that every input within them succeeds. Resolution changes or
pathological decoders can still hit resource limits.

One immutable in-memory input snapshot avoids retaining a full media copy.
Changes during snapshot, symlinks and nonregular sources are rejected. Forced
demuxers and a pipe-only protocol whitelist prevent secondary file/network
access; MOV external tracks are explicitly disabled. Seek-dependent media may
fail through the pipe. Resource limits do not sandbox decoder vulnerabilities:
process untrusted files as an unprivileged user in a disposable VM/container
without sensitive mounts or credentials. Never run this decoder as root.

Use an existing, trusted output parent on a local filesystem. A new mode-0700
directory refuses overwrites; artifacts are mode 0600. A nonblocking directory
lock permits one operation per output parent; use the same parent to serialize
jobs. Different parents/users require caller-owned admission. At least 128 MiB
free disk is checked, without a disk reservation. Three bounded PNGs plus an
embedded sheet need less than 128 MiB; the source snapshot uses RAM. Whole Python
process memory and concurrent callers are outside the child address-space cap.

Timeout or Ctrl-C terminates the owned child process group. Partial artifacts
remain with an incomplete/cancelled manifest for diagnosis; SIGKILL, power loss
or disk failure can prevent a receipt. A complete manifest is required before
using results. Storage failures remain failures, including failure to write the
manifest. Nothing uploads, records devices or changes routing. Outputs and JSON
stdout contain private source metadata: retain only as needed, then remove the
owned bundle explicitly. There is no automatic retention or deletion.

## Bounded production recipes

Work from trusted project media. Keep originals, recipe/settings and linked
media together; save editable editor projects separately if using an editor.
Choose a new output for every command. These examples use the synthetic MP4
made by the qualification script below. The shell's `timeout` bounds wall time;
`prlimit` bounds each export. FFmpeg `-n` refuses output replacement.

```bash
# Two-second audio trim, -3 dB adjustment and 100 ms boundary fades.
timeout 120 prlimit --as=1610612736 --fsize=16777216 --cpu=90 -- \
  ffmpeg -nostdin -v error -n -threads 1 -i '音 sample.mp4' \
  -map 0:a:0 -ss 1 -t 2 -filter_threads 1 \
  -af 'volume=-3dB,afade=t=in:d=0.1,afade=t=out:st=1.9:d=0.1' \
  -c:a pcm_s16le trimmed.wav

# Decode and re-encode a two-second scaled delivery segment.
timeout 120 prlimit --as=1610612736 --fsize=16777216 --cpu=90 -- \
  ffmpeg -nostdin -v error -n -threads 1 -i '音 sample.mp4' \
  -ss 1 -t 2 -filter_threads 1 -vf scale=320:180 \
  -c:v libx264 -threads 1 -pix_fmt yuv420p -c:a aac \
  -movflags +faststart trimmed.mp4

# Concatenate matching decoded segments, resetting the delivery timeline.
timeout 120 prlimit --as=1610612736 --fsize=16777216 --cpu=90 -- \
  ffmpeg -nostdin -v error -n -threads 1 -i trimmed.mp4 \
  -threads 1 -i trimmed.mp4 -filter_complex_threads 1 \
  -filter_complex '[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]' \
  -map '[v]' -map '[a]' -c:v libx264 -threads 1 -pix_fmt yuv420p \
  -c:a aac -movflags +faststart delivery.mp4
```

Analyze each output into a fresh directory. Check stream selection, dimensions,
sample rate, channels, duration (these fixtures allow 0.1 seconds for mux/codec
rounding), decoded boundary samples, fades and concatenation transitions.
Inspect representative images and listen when playback is available. Stream
copy cuts follow available keyframes and packet boundaries; re-encoding alone
does not establish frame accuracy. Verify decoded frame/sample boundaries for
the consuming project's requirements. Avoid repeated lossy intermediate exports.
When time, memory or disk limits are exceeded, shorten/downscale the task or
move production to a separately qualified CachyOS workstation. No GPU fallback
or remote transfer occurs automatically.

## Reproduce native qualification

```bash
python3 scripts/qualify_media.py --output /absolute/private/new-media-run
```

This opt-in script creates synthetic fixtures, an editable JSON recipe with its
linked source, exports and analysis receipts. It verifies silent/no-audio,
Unicode/space paths, VFR, low-rate/shorter/delayed video, rotation, offset audio,
packet-derived timing, malformed/playlist/truncated input refusal, deadline failure, real Ctrl-C child
cleanup and short CPU
trim/fade/scale/concatenation outputs. It records elapsed/child CPU time, largest
child RSS and retained bytes. Largest-child RSS is cumulative across the run,
not total guest memory or aggregate concurrent usage. The script is a native
qualification command, separate from mocked automated tests. See the
[coverage map](../plans/testing-coverage.md) for measured scope and remaining
platform boundaries. Continue platform work with the
[CachyOS handoff](../plans/audio-video-cachyos-handoff.md).

Qualification gates use explicit failures and remain active with `python -O`
or `PYTHONOPTIMIZE`. A failed media, deadline or cancellation check exits nonzero
without writing a successful `qualification.json`; partial fixtures remain for
diagnosis. Offline regressions cover normal and optimized execution using mocked
media processes. They do not replace the opt-in native qualification above.

On Debian 13 x86-64 with FFmpeg 7.1.5, the synthetic 2–4 second, 640×360 suite
completed analysis in under one second per valid fixture. Its two-second audio
and scaled-video exports and four-second concatenated delivery each finished
in under one second. Decoded RGB boundary comparisons passed a mean absolute
error ceiling of 10/255; this is a synthetic structural check, not perceptual
quality assessment. The retained fixture/export/analysis set occupied under
7 MiB. A separate 30-second 1280×720/30 fps H.264/AAC fixture encoded with
`libx264 -preset veryfast -threads 1` in about 11 seconds and analyzed in about
7 seconds; the largest observed child RSS across those operations was under
170 MiB. These are single-run observations on a shared VM, with warm caches
uncontrolled. They establish a small CPU workload envelope, not real-time,
whole-guest memory, 1080p/120-second throughput or tail-latency guarantees.

## CachyOS GPU qualification

The same 18-fixture CPU/file-analysis qualification passes natively on CachyOS
with FFmpeg 9.0.2. This qualifies the Linux analyzer there without changing its
limits or the native WAV evidence/capture API. The retained synthetic set is
under 8 MiB; decoded boundary comparisons pass the same 10/255 ceiling.

Run the separate, opt-in GPU recipe as an unprivileged desktop user:

```bash
python3 scripts/qualify_media_gpu.py --backend vaapi --device /dev/dri/renderD128 \
  --output /absolute/private/new-arc-run
# Larger, fixed-envelope workload on the same backend:
python3 scripts/qualify_media_gpu.py --backend vaapi --device /dev/dri/renderD128 \
  --profile full-hd --output /absolute/private/new-1080p-run
# On a separately available NVIDIA target, select its CUDA device explicitly:
python3 scripts/qualify_media_gpu.py --backend cuda --device 0 \
  --output /absolute/private/new-nvidia-run
```

Use the actual accessible render node, discovered locally; do not assume every
system assigns the same node. No driver installation, package update, device
recording or audio routing occurs. The script creates a trusted four-second
1280×720/30 fps H.264/AAC source by default (`--profile smoke`). The `full-hd`
profile uses 30 seconds at 1920×1080/30 fps with the same resource limits and
320×180 scale target. Both create a CPU transcode and separate hardware encode,
decode and scale exports. Decode requires hardware output frames and an explicit
download; scaling requires `scale_vaapi` or `scale_cuda` on hardware frames.
Missing backends fail visibly rather than silently choosing software. CPU
results remain separate when a GPU operation fails. Each successful export must
pass full bounded analysis, stream dimensions/audio and duration within 0.1 s.
It also compares decoded pictures at the start, midpoint and 0.1 seconds before
the end (0, 2 and 3.9 seconds for smoke; 0, 15 and 29.9 for full-hd), normalized to
320×180 YUV420P, requiring PSNR of at least 25 dB against the source. Audio
RMS must remain within 1 dB of the source and finite. Missing comparison
summaries, incorrect pictures and lost audio fail qualification. These are
synthetic structural checks, not perceptual quality or codec parity between
the backends; sparse samples do not establish whole-clip picture correctness.

Private logs, literal command arguments, per-command elapsed/child CPU time,
cumulative largest child RSS, output bytes and analysis receipts stay in the new
bundle. A complete `qualification.json` and exit 0 require all four exports.
Each command has a 180-second deadline with process-group cleanup, 120 CPU
seconds, 8 GiB address space (GPU mappings need more than the software analyzer)
and a 64 MiB output-file limit; captured logs are bounded to 2 MiB. These limits
are for trusted synthetic production, not a sandbox or general untrusted input
route. New directories refuse overwrites; remove only owned evidence when no
longer needed. The script never retries or changes backend on failure.

Integrated Meteor Lake Intel Arc with iHD 26.3.5 and FFmpeg 9.0.2 passes all four
exports, analysis and decoded comparisons (minimum sampled PSNR about 37 dB;
audio RMS change below 0.01 dB). This establishes VA-API H.264 encode/decode
and GPU scaling. The 30-second full-hd profile also passes all four exports,
analysis and comparisons. Its retained bundle is under 93 MiB; the cumulative
largest observed child RSS is under 481 MiB, excluding aggregate memory and GPU
allocations. These are single-run observations, with unequal encoder presets
and quality settings; they do not establish a speedup or sustained throughput.
Native deadline and Ctrl-C probes stop an active hardware decode/scale/encode
pipeline after observed video-engine activity, leaving no owned decoder child.
A deliberately invalid render device preserves CPU success and fails all
three hardware paths visibly.

NVIDIA/CUDA execution and interactive editor timeline effects remain unqualified;
the CUDA recipe is supplied for that separate target. The separate
[CachyOS editor/GPU guide](cachyos-media.md) records the qualified Kdenlive
project/proxy/title round trip, muted player review, additional VA-API codecs
and MLT Movit effect/hardware-encode tests.
