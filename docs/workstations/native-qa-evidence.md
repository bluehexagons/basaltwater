# CachyOS audio, derivatives and shared QA timelines

These opt-in tools retain private evidence outside Git. They do not change
default devices, move application playback, start a portal, install packages,
or establish physical audibility. Use the checkout's CLI until a refresh delivers
it; inspect [source provenance](cachyos-provenance.md) when comparing runs.

## Retain process identities

Before a normal quit or controller context change, retain the selected live
application's Linux PID/start identity in a private bundle:

```sh
basaltw desktop evidence process --pid 12345 \
  --output /absolute/private/new-process --json
```

This local read needs no desktop helper or enabled input. It records the current
account's non-zombie PID/start ticks, UTC/monotonic observation and boot ID; it
reads no command line and starts or stops nothing. Select the actual game child
as well as its launcher when both matter. Link each returned `process.json` in
the run checkpoint before further work. A missing/foreign/zombie process fails
rather than inventing an identity. Output requires a new private bundle and
refuses overwrites. On the same boot, use the retained pair with
`evidence closeout ... --process PID START_TICKS` after normal UI quit. Identity
absence alone does not prove normal exit, and start ticks from another boot are
not comparable. This cannot reconstruct identities lost in an earlier run.

## Application audio

Run in the existing CachyOS KDE Wayland session as its ordinary user. Identify
the actual application's PID, then inspect its protocol-verified stream serials:

```sh
basaltw desktop --native audio sources --pid 12345 --json
basaltw desktop --native audio capture --pid 12345 --node-serial 456 \
  --seconds 10 --output /absolute/private/new-audio --json
```

The initial route supports native PipeWire stereo FL/FR streams. It follows the
node's client to the protocol-set `pipewire.sec.pid` and UID, verifies Linux
process start ticks, and selects one exact node serial. Application-provided PID
labels alone are insufficient. PulseAudio bridge processes, sandbox/helper
identities, mono/multichannel streams and ambiguous selections remain unsupported;
an empty discovery result is not proof the application has no sound. See
[PipeWire client identity properties](https://github.com/PipeWire/pipewire/blob/master/doc/dox/config/pipewire-props.7.md).

The recorder starts disconnected with automatic linking disabled, then receives
two explicit additional links from the selected application. Existing application
playback links must remain. It never selects the default sink monitor or an
automatic source. Graph snapshots check the source, recorder identity/ports,
exclusive incoming links and preserved playback links approximately every 100 ms
plus bounded probe time. Changed identity, foreign links, takeover/disabled state,
or missing graph evidence makes the capture unqualified. This is snapshot-based
verification, not a continuous graph trace or a security boundary against another
program running as the same account. Keep unrelated applications out of a QA run.

Limits are 1–120 seconds, 48 kHz stereo float32 WAV, 128 MiB, bounded graph/tool
output and independent caller/worker deadlines. The worker retains a Linux alarm
across exec, bounding its lifetime to requested duration plus 15 seconds even if
the caller disappears. Interrupted/failed captures retain private partial files
with `qualified: false`; inspect the receipt before reusing or retrying them.
The final manifest records process/node identity, link endpoints, requested and
observed format, UTC/monotonic timing, sample hash, graph-check count and owned-node
removal. It preserves raw recorder exit status. Current `pw-record --sample-count`
can exit 1 after completing its limit because it never sets the playback-drained
flag; only an exact-duration/format WAV with verified links and empty diagnostics
qualifies that completion. Other failures remain failures. See
[pw-cat options](https://pipewire.pages.freedesktop.org/pipewire/page_man_pw-cat_1.html)
and [the upstream sample-limit and exit paths](https://github.com/PipeWire/pipewire/blob/master/src/tools/pw-cat.c).

No normal playback link is removed during cleanup. Only the owned recorder and
its incident links are removed. `cleanup_confirmed: false` requires inspection;
do not stop shared PipeWire/WirePlumber services or relaunch a replacement blindly.
Forced parent termination can leave an initial receipt; it is incomplete evidence.

## Offline analysis and short excerpts

```sh
basaltw desktop --native audio analyze /absolute/private/new-audio/audio.wav \
  --capture-manifest /absolute/private/new-audio/manifest.json \
  --output /absolute/private/new-analysis --json
basaltw desktop --native evidence excerpt /absolute/private/new-audio/audio.wav \
  --start 2.5 --seconds 2 --output /absolute/private/new-excerpt --json
```

Analysis accepts one regular WAV, at most 128 MiB/120 seconds, 8–192 kHz and 1–8
channels. It copies stable source bytes privately before decoding and never plays
them. FFmpeg `astats`, `ebur128` true-peak scanning and `silencedetect` provide
overall/channel sample peaks and RMS, estimated true peak, integrated LUFS,
loudness range, one-second level windows and silence intervals. Raw tool logs,
version/filter settings, hashes, JSON and captioned Markdown stay in the bundle.
Missing required measurements fail rather than claiming a completed report.
Infinite dB readings, such as digital silence, are represented as null.
See the [FFmpeg filter documentation](https://www.ffmpeg.org/ffmpeg-filters.html).

Candidate findings include sample peaks within 0.01 dB of full scale, adjacent
one-second RMS changes of at least 12 dB, and silence at or below -50 dB for at
least 0.25 seconds. The report includes up to three two-second excerpts around
candidate times and reports omitted excerpts. Additional excerpts are bounded to
ten seconds and retain source hashes/time intervals. No normalization is applied.
A full-scale peak is a warning, not proof of clipping or distortion. Neither
these measurements nor an excerpt establishes broken loops, intelligible speech,
an audible device, or a correct application transition; review sound and behavior.

The optional capture receipt must qualify the exact same WAV hash and confirm
cleanup. It preserves capture timing bounds separately from analysis timestamps.
Without it, the audio's origin is unknown rather than inferred from file times.

## Literal crops and reviewed portable reports

```sh
basaltw desktop --native evidence crop /absolute/private/frame.png \
  --rectangle 100 80 640 360 --caption 'Pause menu after the second action' \
  --output /absolute/private/new-crop --json
basaltw desktop --native evidence export /absolute/private/new-crop/manifest.json \
  --reviewed-by 'QA reviewer' --output /absolute/private/reviewed-crop --json
```

Crops use literal source pixels, retain a source copy, and record source/derivative
hashes, source geometry, rectangle, caption and the transformation. PNGs must be
at most 32 megapixels. Coordinates refer to the supplied PNG; an earlier capture
crop's monitor origin must be applied separately when describing monitor pixels.
Originals are never rewritten. Inspect source and crop pixels before export.

`export` is an explicit operator affirmation that media, caption and private
content have been reviewed. It verifies hashes and copies only known crop or
excerpt media, regenerates Markdown from the bounded caption, and emits relative
portable references with reviewer attribution. It neither publishes nor uploads
anything. Application audio and accessibility records can contain private data;
their raw bundles are not automatically included in a portable export.

## Offline point previews

```sh
basaltw desktop --native evidence point-preview /absolute/private/frame.png \
  --rectangle 100 80 640 360 --point 420 260 --caption 'Proposed pause control' \
  --output /absolute/private/new-point-preview --json
```

This adds an annotated derivative to a normal literal-crop bundle. Each rectangle
is at most 1024 by 1024 source pixels; the integer point must be inside it. The
red cross marks that source-PNG pixel without modifying the source or `crop.png`.
`point-preview.json` binds the source hash, source geometry, rectangle, point,
literal manifest hash and annotated derivative hash. `point-preview.md` shows
both versions. Files stay private and output requires a new bundle. If annotation
fails, the literal prefix can remain; absence of `point-preview.json` means no
successful preview was recorded. Inspect partial bundles before discarding them.

The tool captures nothing, sends no input, reads no helper status and grants no
freshness or input authority. It does not establish presentation dimensions or
recognize a semantic target. Match its source hash to the capture receipt before
using the separate [source-coordinate mapping](desktop-observations.md#explicit-source-png-points-and-offline-previews)
route; mapping and subsequent input still validate the current observation.
Historical previews never authorize input. Annotated point manifests are not
supported by `evidence export`; only the separately reviewed literal manifest
uses the existing portable crop export contract.

## One observation timeline

New desktop action journals, [accessibility recordings](accessibility-testing.md)
and application-audio manifests share UTC, monotonic nanoseconds and a Linux boot
ID. Begin action evidence before the scenario, start bounded accessibility/audio
collection from independent terminals or supervised tasks, then perform the scene
actions and close each recorder. Use explicit private output paths for recovery.

```sh
basaltw desktop --native evidence timeline \
  --artifact /absolute/private/action-record.json \
  --artifact /absolute/private/accessibility/manifest.json \
  --artifact /absolute/private/new-audio/manifest.json \
  --output /absolute/private/new-timeline --json
```

The join snapshots inputs and sorts observed events on the same boot clock,
retaining source failures/truncation. It rejects old records without monotonic
timestamps and records from different boots. Audio start/link-ready/finish are
bounds; sample-zero onset and buffering offset remain unknown. Do not add a WAV
relative peak time to link-ready time and present it as exact desktop timing.
AT-SPI callback timestamps are observations, and announcements are not proof
that Orca spoke. These distinctions permit evidence-backed correlations without
inventing sample-accurate alignment, missing events, or model reasoning latency.

The 2026-11-02 local qualification used synthetic clean/clipped/silent WAVs,
literal PNG pixels, an owned PipeWire stream and receiver without speaker output,
and a disposable GTK application's name-change events. Real game capture,
PulseAudio bridging, physical listening, Orca speech and load/fault behavior
remain separate qualification work. Raw receipts are retained privately.
