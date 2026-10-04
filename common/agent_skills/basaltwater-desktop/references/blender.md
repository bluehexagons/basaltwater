# Blender workflows

Use Debian's configured-release APT package selected by `--blender`; normal
package maintenance owns updates. Check `blender --version` before using
version-specific APIs. Installation or PATH discovery does not verify rendering.

## Touch up models on the desktop

Agentic native editing is a primary use case on desktop VMs: model touch-ups,
material and UV adjustments, scene assembly and asset preparation. Use this
skill's shared-desktop lifecycle, observation and control rules. Complete these
tasks autonomously by default; use human-handoff rules only for a requested
transfer of control.

Work in a task copy or versioned `.blend` file. Inspect the model and its
dependencies first; preserve object identities, hierarchy, modifiers,
materials, UVs and rigging as appropriate to the requested change. Launch the
working scene through `basaltw desktop exec -- blender /absolute/project/model.blend`
with the project's intended preferences, add-ons and embedded-script policy.

Use native selection, viewport inspection and editing for visual work, and
`bpy` where it makes scene changes repeatable. Check the active editor, object
selection and mode before shortcuts or context-sensitive operators. Blender's
custom UI can be absent from AT-SPI; fresh application screenshots and desktop
input remain usable. Inspect geometry or materials from task-relevant views.

Save a checkpoint and reopen the saved task copy to verify the edit and required
dependencies. Verify exports when requested and retain useful before/after
viewport evidence. Report the saved/exported paths, edits and validation results
to complete autonomous work; do not wait for human review unless requested.
For a requested handoff, leave the working scene available and pause agent
control when transferring the shared desktop. Desktop readiness includes this
edit/save/reopen workflow; a CPU render alone leaves it unverified.

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

## Isolate disposable UI experiments

For disposable UI experiments, launch with a new private `BLENDER_USER_CONFIG`
directory, `--factory-startup` and `--disable-autoexec`. Quick Setup can still
appear on first run; complete it only after isolating the profile. Keep ordinary
project launches on their intended preferences/add-ons. Close only your own
test windows, inspect unsaved-work dialogs, and leave the shared session running.
