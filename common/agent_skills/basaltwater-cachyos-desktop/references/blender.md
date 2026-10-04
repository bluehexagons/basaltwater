# Blender models, interchange and renders

Use the native `blender` selected by `--blender`; setup also selects `libdecor`
for Wayland decorations. Updates stay with CachyOS. Check `blender --version`
before using scene APIs: rolling-release builds can be newer than Debian.
On the tested Blender 5.2.2, system Python and `python-numpy` are available;
NumPy is a native Blender package dependency. Query `sys.version`, `sys.path`
and NumPy inside Blender when diagnosing an add-on. A project virtual
environment is not automatically used by `bpy`.

## Edit, save and reopen

Work in a task copy or versioned `.blend`. Inspect existing objects, hierarchy,
modifiers, material nodes, UVs, rigging and external references before the edit.
Preserve those identities/dependencies as appropriate to the requested change.
Launch with `blender /absolute/project/model.blend` in KDE for native work.
Combine task-relevant viewport inspection with trusted `bpy` scripts for
repeatable geometry, material and scene changes. Check selection, mode and
active editor before context-sensitive operators or shortcuts.

For scripting, a background operation can load the working file and save a new
checkpoint without controlling a desktop window:

```bash
blender --background --disable-autoexec /absolute/project/model.blend \
  --python-exit-code 1 --python /absolute/project/touchup.py
```

Put `--python-exit-code` before `--python`, so Python exceptions fail the
command. `--disable-autoexec` prevents automatic embedded Python and scripted
drivers; the explicitly supplied script still runs. Use the project's chosen
execution policy when it needs trusted drivers or add-ons. Long UI console
operations should load a script through a short, inspected command; rapid
typing can lose characters. Use pacing if the available native tool supports it.

Save, reopen and check the requested changes and required external assets.
Blender 5 compresses saved `.blend` files by default: a missing plaintext
`BLENDER` header does not establish corruption. Reopen with Blender; preserve
the project's chosen save format and version compatibility. The bundled smoke
fixture explicitly disables compression for its header check.

## Textured glTF/GLB and OBJ/MTL

Keep a `.blend` working source and export the project's required format. Check
materials, UVs, scale, axes, evaluated geometry and required animation/rigging.
Procedural shaders may need baking to textures for the consumer. For a selected
static mesh in a trusted script:

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

Use a new export directory and confirm operator completion and output files.
Separate `.gltf` needs its buffers and images; `.glb` may still have project
dependencies worth checking. OBJ needs the referenced `.mtl` and images with
working relative paths. Inspect `mtllib`/`usemtl`, texture references and copied
files. OBJ is static geometry with limited material properties; it cannot carry
a Blender rig, animation, modifier stack or arbitrary shader graph.

Reimport from a relocated copy into a fresh scene so original workspace paths
cannot mask missing companions. Compare evaluated shape, UVs, normals and
material assignments; seams/triangulation can change vertex counts. Image data
can load lazily: force pixel access or render before treating `image.has_data`
alone as a missing-texture result. Inspect textured appearance in the native
viewport or a deliberate render and in the consuming application when available.

The tested CachyOS package includes a Draco bridge and passed a textured
compressed GLB export/fresh import with UVs and loaded pixels. Debian's
missing-Draco warning is not a CachyOS readiness result. Qualify the actual
asset when `KHR_draco_mesh_compression` is required. Blender 5.2.2 also logs
`MeshOptimizer is not available` for its missing optional bridge during ordinary
successful exports. Check enabled extensions, operator result, artifact and
fresh import together. Do not silently drop a compression requirement, replace
the native package or install unrelated libraries to suppress warnings.

## Render and compare

`basaltw agent blender smoke --json` uses private configuration/scripts/data,
cache and temporary paths to render a bundled 128x128 Cycles CPU scene. Inspect
its PNG, scene, settings and log. UI, Eevee, GPU and asset interchange remain
separate checks. Use the project's established renderer/backend for project
captures; hardware presence does not establish CUDA/HIP/OneAPI support.

```bash
blender --background --disable-autoexec /absolute/project/scene.blend \
  --render-output /absolute/artifacts/render- --render-format PNG --render-frame 1
```

Load the scene first, override settings afterward, and render last. Record
version, camera, frame, dimensions, engine, device, samples and seed. Compare
decoded pixels with `basaltw agent visuals compare`; PNG paths/dates/timing
metadata can vary even with identical pixels. A project harness can use
`visuals capture` for repeatable revision comparisons.

For disposable UI checks isolate `BLENDER_USER_CONFIG` before factory startup
or completing Quick Setup. Background testing should also isolate
`BLENDER_USER_SCRIPTS`, `BLENDER_USER_DATAFILES`, `XDG_CACHE_HOME` and temporary
files. Ordinary work may require configured add-ons; factory startup is not
the default project workflow. Close only the task's test instances.
