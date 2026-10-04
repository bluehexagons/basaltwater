# Autonomous media touch-ups

Use this reference for game and website image, SVG, sound and short video edits.
Read the parent skill for desktop input, window targeting and session lifetime.

## Discover and plan

Run `basaltw agent manifest --json` in the project. Discover active executables
with `command -v`; check installed help/version before scripting. Debian's
`--inkscape`, `--gimp`, `--krita`, `--audacity` and `--shotcut` install its native
release packages. `--av-tools` adds FFmpeg/ffprobe, ImageMagick and ExifTool.
These selections are opt-in and do not enable a desktop on headless profiles.
An executable's presence is discovery, not a completed edit/export check.

Choose the smallest tool for the task: Inkscape for SVG geometry and vector UI,
GIMP for raster cleanup, Krita for brush/sprite/texture edits, Audacity for sound
edits, Shotcut for video timelines. Use trusted project CLI recipes for bulk
resizing, format conversion or deterministic exports when they suffice.
Inspect existing files and project import settings before changing dimensions,
color space, codecs, frame rate or channel layout.

## Shared desktop editing

Use absolute paths and target the editor's actual window after launch:

```bash
basaltw desktop exec -- inkscape /absolute/project/assets/icon.svg
basaltw desktop exec -- gimp /absolute/project/assets/banner.xcf
basaltw desktop exec -- krita /absolute/project/assets/sprite.kra
basaltw desktop exec -- audacity /absolute/project/assets/sound.wav
basaltw desktop exec -- shotcut /absolute/project/assets/clip.mlt
```

Save a task copy before destructive edits. Keep editable SVG, XCF, KRA, AUP3
or MLT sources and export delivery files separately. Preserve linked media and
relative paths; reopen the saved project to check it can find its dependencies.
Wait for export completion and inspect the artifact before closing your editor.
Complete the work autonomously; human handoff is optional when requested.

## Vectors and raster assets

For Inkscape, preserve `viewBox`, page bounds, IDs used by code, and the intended
aspect ratio. Check linked images and font availability. Keep editable text in
the source; convert text to paths in a delivery copy only when needed for
portable rendering. Confirm the installed CLI supports the requested options:

```bash
inkscape /absolute/project/assets/icon.svg --export-area-page \
  --export-type=png --export-width=256 \
  --export-filename=/absolute/artifacts/icon.png
```

For GIMP/Krita, save layers in the native project format and export PNG/WebP/JPEG
as required. GIMP 2 and 3 scripting APIs differ; do not assume an older batch
recipe works. For sprite edits, preserve frame grid, transparent padding,
pivots and atlas dimensions. Use nearest-neighbor scaling for pixel art when
the project requires it; inspect alpha edges against light and dark backgrounds.
Preserve texture channel meanings and data-map color space (normal/roughness
maps are not ordinary color images); check tiling seams where relevant.

Discover ImageMagick's installed interface: version 6 provides `identify` and
`convert`, whereas version 7 provides `magick`. Inspect dimensions, alpha and
profiles with the available tool. Never replace the editable original with a
flattened conversion. Reopen the exported asset and view it at the actual game
or website size. For SVG, validate browser rendering as well as Inkscape's
preview, following the session's browser-testing policy.

## Sound effects and short video

For Audacity, save AUP3 then export the required audio format. Check trim points,
fades, peak/clipping levels, channel count, sample rate and loop continuity.
Retain an uncompressed working source to avoid repeated lossy re-encoding.
For Shotcut, save MLT plus linked media, confirm project resolution/frame rate,
and export a short representative range on software graphics before committing
to a long encode. Choose the consuming project's supported codecs and formats;
do not assume GPU encoding exists.

```bash
ffprobe -v error -show_format -show_streams -of json /absolute/artifacts/clip.mp4
ffprobe -v error -show_format -show_streams -of json /absolute/artifacts/sound.wav
```

Check duration, stream presence, sample rate/channels, video dimensions, frame
rate and codecs. Inspect beginning/middle/end frames and transitions, and
listen to representative audio when playback is available. Default XRDP audio
redirection is disabled; silent desktop playback does not prove a silent file.
Waveforms, decoded sample measurements and metadata can validate structure and
signal but cannot establish perceived quality. Report that limit accurately.
The editor flags do not enable RDP audio or alter device permissions.

## Completion evidence

Reopen the saved editable project and delivery files. Check the consuming game
or website for import errors, appearance and playback; if unavailable, report
the narrower checks performed. Keep original/task/output paths, app versions,
export settings and representative screenshots or inspection results with the
task. Report completed artifacts and any remaining limitations. Leave unrelated
applications and the shared desktop running.
