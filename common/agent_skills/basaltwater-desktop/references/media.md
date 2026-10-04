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

Agent-created desktops default to 1600x900 after updating Basaltwater. Existing
sessions and human RDP resolution choices are retained. Inkscape panels can
force a minimum window height larger than a 720px desktop; inspect actual
window bounds, hide the panel or use a larger resolution. An oversized window
can obscure controls even after a successful maximize request. Window captures
require the entire client area to fit; do not retry clipping as a resize race.
Fresh screenshots supply the geometry for subsequent input.

## Vectors and raster assets

For Inkscape, preserve `viewBox`, page bounds, IDs used by code, and the intended
aspect ratio. Check linked images and font availability. Keep editable text in
the source; convert text to paths in a delivery copy only when needed for
portable rendering. Confirm the installed CLI supports the requested options:

```bash
inkscape /absolute/project/assets/icon.svg --export-area-page \
  --export-type=png --export-width=256 \
  --export-background-opacity=0 \
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
The `--wait-window Audacity` launch can match its startup splash rather than a
loaded document. Inspect the real document and any Welcome dialog before input;
dialogs may have role `frame`, not `dialog`. Use returned AT-SPI button/menu
actions. For a fade, select the intended range, then inspect Effect > Fading >
Fade In/Out and invoke the observed menu item. `Ctrl+S` saves the project;
`Ctrl+Shift+E` opens Export Audio in the tested 3.7 version. Set both filename
and output folder, choose the intended format/rate/channels and export range,
then verify the saved samples rather than assuming a UI action completed it.
Optional [mod-script-pipe scripting](https://manual.audacityteam.org/man/scripting.html)
is disabled by default. It requires an intentional Preferences > Modules change
and restart; do not enable it or restart unrelated work just for a routine UI
touch-up. Scripts must honor desktop pause and use bounded operations.

For Shotcut, save MLT plus linked media, confirm project resolution/frame rate,
and export a short representative range on software graphics before committing
to a long encode. Choose the consuming project's supported codecs and formats;
do not assume GPU encoding exists.

Shotcut initializes Qt even for `--version`/`--help`. For terminal-only queries
use `QT_QPA_PLATFORM=offscreen shotcut --version`; this does not qualify its UI.
For an isolated test, launch with `--appdata /absolute/private/profile --noupgrade`
before the media path. Ordinary work should use the project's intended profile.
The initial title can be a splash; inspect the loaded media before acting.
For short trims, seek in the Source player, move focus out of the time field,
then use the installed version's `I`/`O` shortcuts to set in/out. Append the
trimmed Source to the timeline with `A` where supported, and save the MLT.
Shortcuts entered in a text field can edit text instead of the clip.
In Export, check **From**: Source exports the loaded source clip, while Timeline
exports the project tracks. Confirm hardware encoding is off for a CPU check,
invoke the observed Export File button, choose a new destination, and wait for
the Jobs result. Keep linked media with the MLT and reopen it before delivery.
See [Shotcut shortcuts](https://www.shotcut.org/howtos/keyboard-shortcuts/) and
[command-line options](https://www.shotcut.org/notes/command-line-options/).

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
