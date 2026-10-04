# Vector, raster, audio and video assets

Use native applications discovered by the manifest and check installed help
before version-sensitive actions. Choose Inkscape for vector geometry, GIMP
for raster cleanup, Krita for painting/sprites/textures, Audacity for sound
touch-ups and Shotcut/Kdenlive for video timelines. `--av-tools` supplies
FFmpeg/ffprobe, ImageMagick 7's `magick` and ExifTool on CachyOS.

Preserve SVG/XCF/KRA/AUP3/MLT editable task copies and linked resources. Export
delivery files separately, wait for completion, reopen sources and exports,
and check the consuming game/website. Human review is optional when requested;
report native UI coverage separately when session automation is unavailable.

## Inkscape actions and exports

Launch `inkscape /absolute/project/icon.svg` in the existing graphical session.
For disposable UI checks use a private `INKSCAPE_PROFILE_DIR` and, where
supported, a unique `--app-id-tag` so a launch does not reuse a personal instance.
Inspect actual window bounds: panels can impose a minimum height larger than
the display. Hide an oversized panel instead of assuming maximize fixed it.
KDE retains the owner's display geometry; Debian's default XRDP size does not
apply here.

For repeatable object edits, discover `inkscape --action-list` and target existing
SVG IDs. The following example translates a selected object and saves a separate
editable SVG; substitute a real ID and new output path after inspecting the
document:

```bash
inkscape /absolute/project/icon.svg --batch-process \
  --actions='select-by-id:task-rect;transform-translate:12,0;export-type:svg;export-filename:/absolute/artifacts/edited.svg;export-do'
inkscape /absolute/artifacts/edited.svg --query-id=task-rect --query-x
inkscape /absolute/artifacts/edited.svg --export-area-page \
  --export-type=png --export-width=256 --export-background-opacity=0 \
  --export-filename=/absolute/artifacts/icon.png
```

Action arguments have their own delimiters; use controlled IDs/paths and inspect
the installed actions rather than interpolating arbitrary document strings into
an action list. CLI success alone does not prove a selected ID existed or the
edit occurred. Verify saved geometry through queries/XML, reopen the SVG and
inspect its new export. Preserve `viewBox`, page bounds, code-referenced IDs,
aspect ratio, linked images and fonts. Keep text editable in the source; convert
to paths in a delivery copy only when required for portable rendering.
Check browser rendering through available browser tools when delivering SVG.

## Raster images and textures

Launch `gimp /absolute/project/banner.xcf` or `krita /absolute/project/sprite.kra`.
Keep layers and export the requested PNG/WebP/JPEG separately. CachyOS uses
GIMP 3; GIMP 2 batch recipes are not interchangeable. Inspect the installed batch
interpreter/PDB API before scripting. Use `--new-instance`, `--no-interface`
and a private `GIMP3_DIRECTORY` for disposable GIMP batch checks where supported.
Krita's CLI exposes `--export --export-filename`; confirm local help and use
isolated configuration/resources for tests instead of altering the user's profile.

Preserve sprite grids, transparent padding, pivots and atlas dimensions. Use
nearest-neighbor scaling when required by pixel art. Inspect alpha against light
and dark backgrounds; keep data-map channel meanings and color space intact,
and check texture tiling seams. `magick identify` reports dimensions, channels
and profiles. A flattened CLI conversion is a delivery asset, not a replacement
for the layered source. Inspect exports at the consuming application's scale.

## Sound and short video

Audacity preserves AUP3 sources; exports need explicit range, format, sample rate
and channels. Inspect the actual document and any Welcome dialog rather than
acting on a splash title. Use accessible Effect/Fading and Export Audio controls
when available. Optional mod-script-pipe is disabled by default: do not enable it
or restart unrelated work merely to perform an ordinary edit. Trusted FFmpeg
recipes can handle deterministic trims/fades when an editable Audacity project
or native effect is not required.

Shotcut preserves MLT and linked media. It initializes Qt even for help/version:
use `QT_QPA_PLATFORM=offscreen shotcut --version` only for terminal discovery.
Isolate UI tests with `--appdata /absolute/private/profile --noupgrade` before
the media path. Check loaded media, keyboard focus, Source trim and timeline
placement. In Export check **From** (Source versus Timeline), wait for the Jobs
result and verify the saved file. Kdenlive similarly needs its project profile,
media/proxy references and render range checked. Use a short CPU export as a
baseline; GPU decode/encode needs separate qualification.

```bash
ffprobe -v error -show_format -show_streams -of json /absolute/artifacts/clip.mp4
ffprobe -v error -show_format -show_streams -of json /absolute/artifacts/sound.wav
```

Check duration, stream presence, rate/channels, frame rate, codecs, clipping,
fade/loop boundaries and representative frames/audio. KDE uses the owner's
PipeWire devices; unavailable playback does not prove silence in the file.
Decoded samples and metadata establish signal/structure, not perceived quality.
Do not reconfigure audio devices or install GPU drivers solely for an export.

## Other supported native applications

| Application | Source and validation |
| --- | --- |
| Ardour | Retain the whole session/audio/plugin directory; verify exported range, rate, channels, peaks and playback. |
| LMMS | Preserve MMP/MMPZ, samples and plugins; verify loop/range, duration and output quality. |
| Scribus | Preserve SLA, linked images and fonts; preflight and check PDF page size, bleed, embedded fonts and rendered pages. |
| FreeCAD | Preserve FCStd, linked parts and constraints; recompute/reopen, then verify STEP/STL units and shape. |
| KiCad | Keep project, schematic, PCB and libraries; use installed `kicad-cli` for ERC/DRC and inspect fabrication exports. |
| OBS | Use a task profile/scene and selected capture source; verify a short video/audio recording. Wayland capture may need portal consent. |
| Remmina | Use the intended protocol and connection; check plugin and Secret Service integration without copying credentials. |

Material Maker/Godot exports and publishing tools have their own setup and
runtime requirements in the
[CachyOS software guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/CACHYOS_SOFTWARE.md).
Package availability does not authorize recording, publishing, flashing media
or connecting to a new host beyond the user's task.
