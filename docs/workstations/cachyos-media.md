# CachyOS editor and GPU production

Use the [Linux media analyzer and bounded CPU recipes](media-analysis.md) for
offline work. Select existing `--av-tools`, `--audio-tools` and `--kdenlive`
options as needed, preserving the complete saved setup selection. Package
installation and doctor inventory are separate from native project acceptance.
No new all-media bundle, driver installation or audio routing is required.

## Editable Kdenlive round trip

Start with the synthetic media from `scripts/qualify_media.py` in a private
directory outside Git. Use the existing KDE session and task-specific Kdenlive
configuration; inspect installed `kdenlive --help` before using these options:

```bash
kdenlive --config /absolute/private/task/kdenliverc --no-welcome \
  -i '/absolute/private/task/音 sample.mp4'
```

1. Accept the source-matching 640×360/24 fps profile. Place the linked clip's
   video and audio on V1/A1 at timeline zero. Save a new `editor.kdenlive` beside
   the source. Do not replace an existing project.
2. In **Media → Add Media → Add Title Clip**, create a short editable title.
   Place it on V2 at timeline zero. A five-second title over the four-second
   clip intentionally produces a final second of title on black.
3. In **File → Project Settings**, select the project parent folder for task
   cache storage and enable **Proxy clips** on the Proxy tab. For the source
   clip, enable **Proxy Clip** in the bin context menu. Wait for the job and
   verify the actual proxy file and saved original/proxy references. A `P`
   badge alone is insufficient. Preserve project, source and cache together.
4. Save, quit and reopen the saved project with the same task config. Check
   profile, tracks, title, original/proxy links and timeline range. Scrub both
   the video/title overlap and title-only tail. A missing-link dialog is a
   failure, not permission to substitute unrelated media.
5. Render to a new path, then inspect the output with `basaltw-media`. Kdenlive
   26.08.3 supports the following synchronous render route; omit `--render-async`
   so completion remains observable:

```bash
timeout 180 kdenlive --config /absolute/private/task/kdenliverc --no-welcome \
  --render --render-preset MP4-H264/AAC \
  /absolute/private/task/editor.kdenlive /absolute/private/task/editor-render.mp4
basaltw-media /absolute/private/task/editor-render.mp4 \
  --output /absolute/private/task/editor-render-analysis
mpv --no-config --no-audio --keep-open=yes /absolute/private/task/editor-render.mp4
```

Verify the output path is absent before rendering; the editor CLI does not
provide the analyzer's no-overwrite contract. Use only trusted task projects;
this editor route is outside the analyzer's decoder resource limits. Inspect
title content and representative start/middle/end decoded images as well as
the complete manifest, H.264/AAC streams, 640×360 dimensions and five-second
duration (0.1-second mux tolerance). The muted player check verifies visible
consumption; physical listening remains a separate check. Close only task apps.

For the separately qualified hardware encoder, choose `--render-preset 'VAAPI H264'`
and another new output path. Inspect the installed preset's encoder definition
(`vcodec=h264_vaapi` in `export/profiles.xml`) and retain advancing video-engine
DRM counters from the owned `melt-7` render child. A preset label or H.264 stream
alone is insufficient evidence of hardware encoding. Keep CPU and hardware
results separate, including decoded start/middle/end comparisons and audio.

This preset's MP4 lacks a front-loaded index in the tested build. If pipe-based
analysis fails with partial-file errors, preserve the original and remux the
trusted task export to a new path before analyzing it:

```bash
timeout 60 prlimit --as=1610612736 --cpu=60 --fsize=16777216 -- \
  ffmpeg -nostdin -v error -n -i /absolute/private/task/editor-vaapi.mp4 \
  -map 0 -c copy -movflags +faststart /absolute/private/task/editor-vaapi-faststart.mp4
basaltw-media /absolute/private/task/editor-vaapi-faststart.mp4 \
  --output /absolute/private/task/editor-vaapi-analysis
```

This is an explicit trusted-file preparation step; it does not relax the
analyzer's pipe-only input contract. Verify matching decoded stream hashes of
original and remux with FFmpeg `-map 0 -f streamhash -hash sha256 -` before using
the copy as evidence for the original.

On native CachyOS, Kdenlive 26.08.3 / MLT 7.42.0 passes this save/quit/reopen,
linked media, generated proxy, editable title, synchronous CPU render and muted
MPV review. The proxy used the source's 640-pixel width; downscaled proxy quality
and long/complex projects are not established. Render analysis reports five
seconds of video and about 5.013 seconds of audio. The separate VA-API preset
also passes, with advancing same-process i915 video-engine counters, matching
decoded stream hashes across faststart remux, sampled PSNR above 41 dB against
the CPU baseline, audio RMS difference below 0.01 dB and muted MPV review.
These observations do not qualify GPU timeline processing.

## GPU effects are separate from encoding

Use the [explicit FFmpeg backend runner](media-analysis.md#cachyos-gpu-qualification)
for H.264 encode, decode and hardware scaling. The native Intel Arc result is
from integrated Meteor Lake graphics with iHD 26.3.5, not a discrete Arc board.
Do not extrapolate it to another GPU, codec or driver.

MLT's [OpenGL guidance](https://www.mltframework.org/docs/opengl/) describes
Movit effects and the Qt `qglsl` consumer. The installed MLT automatically uses
that consumer for an explicit Movit effect. A bounded, trusted synthetic test is:

```bash
timeout 45 prlimit --as=8589934592 --cpu=60 --fsize=16777216 -- \
  melt '/absolute/private/task/音 sample.mp4' in=0 out=95 \
  -attach-clip movit.saturation saturation=0 \
  -consumer avformat:/absolute/private/task/new-desaturated.mp4 \
  vcodec=libx264 acodec=aac threads=1 real_time=1
ffmpeg -nostdin -v info -i /absolute/private/task/new-desaturated.mp4 \
  -vf signalstats,metadata=print -frames:v 1 -an -f null -
```

Check the target is absent first: MLT can overwrite it. Check exit status,
decode the export, and compare the colored source with the grayscale output;
`lavfi.signalstats.SATAVG=0` is the synthetic expected output. Pixel change
alone does not identify a hardware renderer. During the owned `melt` process,
sample its `/proc/PID/fdinfo/*` DRM records and retain render-engine counter
deltas with the exact command and driver. A listed plugin or a software OpenGL
renderer is insufficient. Treat unavailable counters as unverified GPU use.

The native MLT 7.42.0 test passes with grayscale output and advancing i915 DRM
render-engine counters in the same process. H.264 delivery encoding here is
CPU `libx264`; the measured GPU operation is the Movit effect. This is a separate
MLT render, not evidence that Kdenlive's interactive timeline used Movit.
MLT also reports an unavailable RtAudio plugin (`librtaudio.so.7`); offline
exports succeed, but this does not qualify RtAudio playback.

Kdenlive's **Settings → Configure Kdenlive → Playback → GPU processing (Movit)**
is disabled and unchecked in the tested native build. Its
[playback documentation](https://docs.kdenlive.org/en/getting_started/configure_kdenlive/configuration_playback.html)
states that this option is disabled because of compatibility issues between
MLT and Movit. Treat interactive Movit as unavailable; use the explicit CPU
timeline baseline and separately measured MLT effect route above. Do not force
the config flag, replace libraries or install drivers to manufacture a pass.

NVIDIA/CUDA execution, discrete-GPU production,
physical playback, HDR and OBS/device capture remain separate acceptance.
Keep private raw logs, project XML, screenshots, device inventories and exports
outside Git. No audio services or routing are changed by these checks.
