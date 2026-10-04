# Blender workflows

Use Debian's configured-release APT package selected by `--blender`; normal
package maintenance owns updates. Check `blender --version` before using
version-specific APIs. Installation or PATH discovery does not verify rendering.

## Establish a CPU baseline

For a deliberate rendering check, run `basaltw agent blender smoke --json`.
It creates a small bundled Cycles CPU scene, uses two render threads and a
fixed seed, and isolates user configuration, scripts, data and cache paths.
The result points to a private directory containing `scene.blend`, `render.png`,
`settings.json`, `blender.log` and `report.json`. Inspect the image and logs.
`--output NEW_DIRECTORY` requires an existing parent and refuses an existing
destination; `--timeout` accepts 1–600 seconds, default 120. Failures retain
available evidence and exit nonzero. This fixture is intentionally low quality
and does not establish desktop, Eevee, add-on or GPU readiness.

Emulated graphics can render in software. Cycles CPU is a useful first check;
Eevee may need much longer to initialize a graphics context and compile
shaders. EGL warnings can accompany a successful fallback. Evaluate the exit
status, actual output and logs together; do not replace Debian Blender or
force a GPU backend just to silence a warning. Preserve a project's established
renderer when testing its output.

## Automate project scenes

Blender processes arguments in order: load the scene before output overrides
and put the render operation last. For example:

```bash
blender --background --disable-autoexec /absolute/project/scene.blend \
  --render-output /absolute/artifacts/render- --render-format PNG --render-frame 1
blender --background --disable-autoexec /absolute/project/scene.blend \
  --python-exit-code 1 --python /absolute/project/capture.py
```

Put `--python-exit-code 1` before `--python`; otherwise script errors can leave
a zero process exit status. `--disable-autoexec` blocks automatically embedded
Python and scripted drivers, while an explicitly supplied script still runs.
A trusted project requiring scripted drivers needs an explicit execution policy.
Record version, scene, camera, frame, resolution, engine, device, samples and
seed alongside the PNG. Use `basaltw agent visuals compare` for image evidence;
a project harness with `visuals capture` can compare isolated Git revisions.
Compare decoded pixels rather than whole-file hashes: Blender PNG metadata can
include scene paths, dates and timings even when repeated renders look identical.
Review that metadata before sharing captures.

Blender's `bpy` uses its bundled Python. Host `python3`/virtual-environment
packages are not automatically available; inspect `sys.version` inside Blender
and follow the project's Blender dependency recipe. Use absolute output paths
and resolve external assets relative to the blend file when necessary.

## Test the UI separately

Use this skill's shared-desktop lifecycle and control rules. Prefer `bpy` for
repeatable scene changes. Blender's custom UI can be absent from AT-SPI, so use
fresh application screenshots and desktop input when inspection is unavailable.
Verify changes by inspecting saved scene/export files as well as screenshots.

For disposable UI experiments, launch with a new private `BLENDER_USER_CONFIG`
directory, `--factory-startup` and `--disable-autoexec`. Quick Setup can still
appear on first run; complete it only after isolating the profile. Keep ordinary
project launches on their intended preferences/add-ons. Close only your own
test windows, inspect unsaved-work dialogs, and leave the shared session running.
