# Development and testing with desktop applications

Start with `basaltw agent manifest --json`. Detected applications include their
active executable path, development workflows and instructions. Availability
means the executable was found on PATH; it does not verify the application's
version, add-ons, desktop session, audio devices or graphics backend.

Use the local desktop skill listed by the manifest before native UI work.
Managed Debian VMs can provide a shared XRDP desktop and `basaltw desktop`
tools. CachyOS workstations use their existing native desktop; do not assume
XRDP, X11, screenshot automation or the VM gateway exists there. If automation
is unavailable, prepare the project and ask for human validation in the native
application. Use the collaborative browser policy for web applications.

## Blender

Agent-assisted desktop work is a primary Blender use case on agentic desktop
VMs: touching up 3D models, adjusting materials and UVs, assembling scenes and
preparing assets for export. Support includes opening an existing model,
making targeted edits in the native application and verifying saved or exported
results autonomously. Human handoff is optional and applies when requested.

Select `--blender` in a Debian workstation or standard server setup, including
`agent_vm` for headless use. It installs Debian's APT `blender` package for the
configured release and follows normal host package updates. CachyOS's
`agent_cachyos` profile uses its native package with the same flag. See the
[desktop software support table](WORKSTATIONS.md#desktop-software-support-and-flags)
for other applications, flags and desktop access choices. Check
`blender --version` before using version-specific scene APIs or add-ons.

Debian setup also installs `python3-numpy`, which Blender's bundled glTF
import/export add-on needs but Debian Blender may not declare as a dependency.
Setup reruns repair a missing NumPy package even when Blender is already installed.
On an existing VM, an administrator can run `apt-get install python3-numpy`
without rerunning desktop setup or ending the shared session. Verify an actual
export and import; a render check does not exercise this dependency.

Debian Blender can report `Draco mesh compression is not available` with an
`ERROR` prefix while successfully exporting an uncompressed glTF/GLB. The
optional Blender Draco backend is unavailable on the tested Debian installation;
ordinary exports work with `export_draco_mesh_compression_enable=False` (the
default). Check operator completion, the output file and a fresh import instead
of treating that line alone as export failure. Assets requiring
`KHR_draco_mesh_compression` need a separately qualified compatible backend;
do not silently remove a project's compression requirement or substitute an
unrelated Draco library. Keep Debian's Blender package for ordinary asset work.

### Model touch-ups on the shared desktop

Use a desktop-capable profile such as `agent_code_vm` or `agent_workstation`
with `--blender`. On a headless profile, add `--desktop xfce` for the shared
native desktop; `--blender` itself selects the application. Follow the local
desktop skill's launch, observation, control and handoff rules.

Work from a task copy or versioned `.blend` file and inspect the model before
editing. Preserve the project's object identities, hierarchy, modifiers,
materials, UVs, rigging and external asset references as appropriate to the
requested change. Open that working file with
`basaltw desktop exec -- blender /absolute/project/model.blend`, using the
project's intended add-ons and embedded-script policy.

Combine viewport inspection and native editing with `bpy` automation where it
makes changes repeatable. Check the active editor, selected object and mode
before shortcuts or context-sensitive operators. Blender's custom UI may have
no AT-SPI controls; screenshots and desktop input still support agentic editing.
Use current observations for each interaction and inspect the changed geometry
or materials from views relevant to the task.

For console commands, use `desktop input ... text --delay-ms 10`; Blender can
drop characters at the default typing speed. Inspect the entered command before
Return and verify the resulting artifact. Write longer trusted operations to
a script file and type a short loader. See [paced desktop input](DESKTOP_AUTOMATION.md#pace-text-for-custom-editors)
for bounds and handling partial input.

Save a checkpoint, reopen the saved task copy and verify the requested edit and
its required dependencies. Check an exported asset when export is part of the
task. Retain useful before/after viewport evidence alongside the saved file.
Complete autonomous tasks by reporting the saved/exported paths, changes and
validation results; human review is not a completion gate. If human handoff is
requested, leave the working scene available and pause agent control when
transferring the shared desktop.

Desktop readiness therefore includes an edit/save/reopen workflow on the target
VM. The background CPU smoke check below supplies additional rendering evidence.

### glTF/GLB and OBJ/MTL assets

Treat import, touch-up and export of these formats as core autonomous workflows.
Keep a `.blend` working copy and choose the deliverable format required by the
project. Test actual textured assets; neither installation nor the CPU rendering
smoke check qualifies import/export. Debian's bundled glTF add-on uses NumPy;
its OBJ importer/exporter is built in.

For glTF, distinguish binary `.glb` from separate `.gltf`, buffer and image files.
Preserve referenced files and their relative paths when copying or delivering a
multi-file asset. Check materials, UVs, scale, axes and any required animation,
rigging or extensions after import. Procedural Blender materials may require
baking to textures for interchange; a successful export does not establish
visual fidelity in the consuming application.

OBJ requires its referenced `.mtl` and image files for textured materials. Check
the OBJ's `mtllib`/`usemtl` declarations and the MTL's texture paths; deliver the
whole directory with relative references. Blender 4.x uses `bpy.ops.wm.obj_import`
and `bpy.ops.wm.obj_export`; older recipes using `import_scene.obj` or
`export_scene.obj` may not work. For a selected static mesh in a trusted script:

```python
bpy.ops.export_scene.gltf(
    filepath="/absolute/export/model.gltf", export_format="GLTF_SEPARATE",
    use_selection=True, export_apply=True,
    export_draco_mesh_compression_enable=False,
)
bpy.ops.wm.obj_export(
    filepath="/absolute/export/model.obj", export_selected_objects=True,
    apply_modifiers=True, export_uv=True, export_normals=True,
    export_materials=True, path_mode="COPY",
)
```

Create a new export directory first and confirm selection and modifier settings.
`COPY` collects referenced OBJ textures; verify the resulting MTL paths and
copied files. OBJ/MTL carries static geometry and limited material properties;
it does not preserve a Blender rig, animation, modifier stack or arbitrary shader
graph. Avoid assuming glTF and OBJ material models are interchangeable.

Import the deliverable into a fresh scene from a relocated copy of the export
directory to catch missing companions and references to the original workspace.
Compare evaluated geometry, UV placement, normals, material assignments and
texture appearance. Compare shape rather than requiring identical vertex counts:
triangulation and UV/normal seams can change the exported topology. Inspect the
imported textured model in the native viewport and retain the export companions
alongside validation evidence. Check the consuming application when its own
material or extension behavior matters.

### Check rendering after setup

Run a deliberate smoke check as the coding account:

```bash
basaltw agent blender smoke --json
# Optional: choose a new evidence directory with an existing parent
basaltw agent blender smoke --output /absolute/artifacts/blender-check --timeout 120
```

This renders a bundled scene with Cycles on CPU at 128 × 128, eight samples,
seed 0, two render threads and no denoising. It uses factory startup and private
configuration, scripts, data, cache and temporary directories, so the check
does not depend on personal startup files or add-ons. No desktop is required.
The default evidence directory is a unique private run under
`~/.local/state/basaltwater/blender/`. Existing output directories are rejected;
`--timeout` accepts 1–600 seconds (default 120).

Inspect `render.png`, `scene.blend`, `settings.json`, `blender.log` and
`report.json`. Success requires Blender to exit successfully, record completion
with the expected settings, and produce a PNG of the expected dimensions and
a blend scene. The report records the executable, argv, Blender/Python versions,
camera, frame, renderer, device, samples, seed and elapsed times. Startup errors,
Python exceptions, timeouts and missing artifacts fail the check and retain
available evidence. A successful CPU check leaves UI and GPU readiness
unverified. This is a smoke fixture, not a performance benchmark or a project
capture harness; low sample counts can produce visible noise.

### Project renders and Python

Blender can render a project without opening its UI:

```bash
mkdir -p /absolute/ignored/artifact-directory
blender --background --disable-autoexec /absolute/project/scene.blend \
  --render-output /absolute/ignored/artifact-directory/render- \
  --render-format PNG --render-frame 1
```

Blender evaluates command-line arguments in order. Load the blend file first,
then override output settings, and render last. A project's scene automation
can use `--python /path/to/capture.py` with `--python-exit-code 1` before the
script argument so Python errors fail the command. Use a trusted project script
to set the scene, camera, frame, resolution, seed and render engine explicitly.
See Blender's official [command-line rendering](https://docs.blender.org/manual/en/latest/advanced/command_line/render.html)
and [argument reference](https://docs.blender.org/manual/en/latest/advanced/command_line/arguments.html).

`--disable-autoexec` disables automatic embedded Python, including scripted
drivers. A trusted project that relies on those drivers needs an explicitly
chosen auto-execution policy; do not silently change its rendering semantics.
An explicitly supplied `--python SCRIPT` still runs. Without
`--python-exit-code`, a Python exception can leave the process with exit status
zero. Blender runs its own Python environment. Debian's build uses system
Python libraries, including Debian's `python3-numpy`; upstream builds may bundle
Python instead. A project's virtual environment is not automatically available
in `bpy` scripts. Query `sys.version` and `sys.path` inside Blender and keep add-on dependencies
under the project's documented Blender environment.

Keep generated files in a declared, ignored artifact directory. Record the
Blender version, scene/camera, frame, dimensions, engine, CPU/GPU device and
render settings alongside each capture. A background render is evidence about
that render path; test interactive editing in the native desktop separately.
Do not select a GPU backend merely because Blender is installed. Use the
project's established backend and inspect a deliberate smoke render's logs.

### Software graphics and interactive checks

Emulated VM graphics commonly render in software. Prefer Cycles CPU for a
small initial background check; Eevee's graphics context and shader compilation
can add considerable startup time. EGL warnings alone do not prove failure:
Blender can recover through another context. Check exit status, logs and the
actual rendered image together. Conversely, `blender --version` alone proves
neither context creation nor rendering. Do not change drivers, install an
upstream Blender build or force a GPU backend solely to suppress a warning.

Test opening, editing and rendering through the native desktop separately.
Blender's custom UI may expose no AT-SPI controls; use recent application
screenshots and desktop input when accessibility inspection is unavailable.
Use `bpy` alongside native editing for repeatable scene changes. Test launches
can show the first-run Quick Setup dialog even with `--factory-startup`; use a
private `BLENDER_USER_CONFIG` directory before completing it so personal preferences
remain untouched. Factory startup is for isolated tests; ordinary project work
may require the user's configured add-ons and preferences.

Compare PNG results with `basaltw agent visuals compare`. Compare decoded
pixels for Blender captures: PNG metadata can include the blend
file's path, capture date and render timings, so whole-file hashes can differ
even when the pixels match. Review metadata as well when sharing captures.
For comparisons across revisions, provide a project capture harness to
`basaltw agent visuals capture`; it runs the same settings in two managed
worktrees. The harness must apply those settings and write one PNG to its
requested output path. See [visual comparisons](VISUAL_COMPARISONS.md).

## Media touch-ups for games and websites

For a small everyday tool set, select `--inkscape --gimp --av-tools` on your
usual Debian setup command. Inkscape covers vector assets; GIMP covers raster
cleanup; the existing conversion bundle supplies FFmpeg/ffprobe, ImageMagick
and ExifTool. Add the other editors only for work that needs them. All five
editors use Debian's configured APT versions, even with `--flatpak`; CachyOS
uses native packages. See [software support and flags](WORKSTATIONS.md#desktop-software-support-and-flags).

| Editor and flag | Typical touch-up | Editable source | Delivery examples |
| --- | --- | --- | --- |
| [Inkscape](https://inkscape.org/) `--inkscape` | Icons, logos, vector UI, SVG geometry | SVG | SVG, transparent PNG |
| [GIMP](https://www.gimp.org/about/) `--gimp` | Crops, retouching, alpha edges, image composition | XCF | PNG, JPEG, WebP |
| [Krita](https://krita.org/en/features/) `--krita` | Sprite painting, textures, brush edits | KRA | PNG, exported frame sequences |
| [Audacity](https://www.audacityteam.org/features/trim/) `--audacity` | Sound effect trims, fades, level adjustments | AUP3 | WAV, Ogg, other project-supported audio |
| [Shotcut](https://www.shotcut.org/features/) `--shotcut` | Clip trims, timelines, simple video titles | MLT plus linked media | Project-supported video container/codecs |

Launch a native editor through the shared desktop, for example:

```bash
basaltw desktop exec -- inkscape /absolute/project/assets/icon.svg
basaltw desktop exec -- gimp /absolute/project/assets/banner.xcf
```

Complete edits autonomously: save an editable task copy, export delivery assets,
reopen both, then inspect the actual game or website using them. Report the
source/export paths and the checks performed; human handoff is optional when
requested. Follow the desktop skill's `references/media.md` for application
workflows and export checks. Package discovery alone does not prove editing,
rendering or audio readiness.

For repeatable SVG-to-PNG export, check the installed `inkscape --help` and use
explicit page bounds, output path and size; width preserves the page aspect ratio:

```bash
inkscape /absolute/project/assets/icon.svg --export-area-page \
  --export-type=png --export-width=256 \
  --export-filename=/absolute/artifacts/icon.png
ffprobe -v error -show_format -show_streams -of json /absolute/artifacts/clip.mp4
```

Preserve SVG `viewBox`, linked images and required fonts; check raster alpha,
color profile, dimensions and atlas frame boundaries. Keep texture maps' color
space and channel meanings intact. Inspect exports at the game/UI's intended
scale rather than relying on editor zoom. Debian may provide ImageMagick 6's
`identify`/`convert` instead of `magick`; discover the installed commands first.
GIMP scripting differs between major versions, so inspect the installed API
before automating it.

Audio and video exports need duration, channel/sample-rate, codec and frame-rate
checks, plus representative playback when available. Default RDP audio
redirection is disabled; silence in the remote desktop is not evidence that a
file lacks audio. Report the limit when only waveform, metadata or decoded
sample checks were possible. Use short CPU exports on emulated graphics before
attempting a large video timeline.

## Other applications

FreeCAD and KiCad support CAD work; Kdenlive and Shotcut support video editing;
Audacity, Ardour and LMMS support audio work; Scribus supports page layout;
OBS supports recording. The manifest exposes these workflows only when the
corresponding executable is present.

Prefer project-owned sources and repeatable export recipes. Use a native UI
check when the change concerns editing behavior, layout, timeline interaction
or device selection. Keep capture settings and exported evidence with the
test result so another agent can repeat the check.

## Further capability-awareness improvements

- A short session-start summary of available workflows, workspace conventions,
  deployment mappings and instruction links, refreshed after provisioning or
  switching the active toolchain.
- Explicit states for each workflow: discovered, checked, unavailable, or
  unknown, with the last check's timestamp, context and remediation. Avoid
  treating an installed binary as a successful test.
- Project declarations of required capabilities and named build/test/capture
  recipes. Discovery can identify unmet requirements; executing recipes should
  remain a deliberate action using trusted project instructions.
- An `explain CAPABILITY` entry point with prerequisites, examples, evidence
  locations and relevant local skills, so agents need not load every guide.
- Session-aware routing advice that identifies whether a browser, terminal or
  desktop runs on the client or VM and supplies the usable preview address.
- Capture provenance that records application versions, renderer/device,
  inputs and revisions, with links to logs and visual comparisons.
- Coverage for applications launched through Flatpak or desktop entries, with
  their actual launch commands and sandbox limitations; PATH discovery alone
  does not inventory those installations.
