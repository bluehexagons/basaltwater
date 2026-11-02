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

```fish
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

When a project requires `KHR_draco_mesh_compression`, verify the installed
extension, exporter settings, artifact and fresh import together. Blender may
log `MeshOptimizer is not available` for an optional bridge during an otherwise
successful export. Do not silently drop a compression requirement, replace the
native package or install unrelated libraries to suppress warnings.

## Render and compare

For structured asset observations, run:

```fish
basaltw agent blender inspect /absolute/project/prop.blend \
  --object Body --object Attachment --intersect Body Attachment \
  --render-views --timeout 300 --json
```

Inspection also accepts `.obj`, `.gltf` and `.glb`, importing them into an empty
factory scene with Blender's default import axes/units. Use `--dependency FILE`
for each external buffer, MTL and texture to hash it before and after the run.
Only declared files are hashed; geometry import does not prove textures loaded
or that a consumer uses the same axes/materials. Retain the relocated reimport
and consumer checks above.

For project-owned scene assemblies, pass a version 1 JSON file:

```json
{
  "version": 1,
  "parts": [{
    "name": "prop",
    "source": "exports/prop.glb",
    "matrix": [[1,0,0,0], [0,1,0,0], [0,0,1,2], [0,0,0,1]]
  }]
}
```

Each of up to 64 uniquely named parts imports OBJ/glTF/GLB below the assembly
directory. The optional row-major affine matrix transforms imported roots,
preserving child hierarchy; identity is the default. Object names become
`part:ImportedName` (at most 63 UTF-8 bytes). Inspect once to inventory names,
then designate pairs. The assembly, geometry files and declared companions
are hashed in `input_sha256`. Project procedural assemblies can instead save a
`.blend` checkpoint through a declared `visuals check` recipe and inspect that
checkpoint; the shared inspector does not execute assembly scripts.

`--support Prop Floor --support-tolerance 0.001` casts world -Z rays from
vertices within tolerance of Prop's lowest Z, starting one tolerance above each
vertex. It reports minimum/maximum signed vertical gap, gaps above tolerance,
missed support rays and sample truncation (4096 vertices per pair). Units are
Blender world units. Rotate a Y-up consumer assembly explicitly before checking.
This detects sampled floating bases or missing support, and complements
`--intersect`; it cannot prove stability, detect all penetrations, or cover
overhangs away from the sampled base. Empty meshes remain unverified. Inspection
success means observations completed, even when gaps/intersections are present.

This opens the source without auto-executing embedded scripts, using private
configuration and a background factory session. It never saves over the source.
`report.json` retains the source hash and outcome; `inspection.json` records
scene/frame, transforms, modifiers, source selection, evaluated bounds, material
slots/face counts, UV layer names, vertex-connected islands and their world
bounds, boundary/nonmanifold/loose edges, zero-area faces and zero-length normals.
Selection indices refer to the source mesh; topology and overlap indices refer
to evaluated viewport meshes. Render-only modifiers can differ. Instances are
not expanded. Inspect exact scene mesh names, restricting large assets with
repeatable `--object`. Selected non-mesh objects receive transform inventories.

`--render-views` creates front (-Y), rear (+Y) and underside (-Z) orthographic
views at a shared frame and framing, in solid, wire, face-orientation and island
modes. The CPU diagnostic materials replace original appearance in a separate
scene. Wire shading includes Cycles triangulation; orientation is blue for
front-facing and red for back-facing surfaces, not an outward-normal verdict.
Island views highlight components after each object's largest component in
orange, along with faces in non-intentional designated overlaps. Intentional
parts can also be disconnected. `diagnostics.blend` retains the diagnostic scene
and source scenes for further inspection. No live viewport overlay is toggled.

`--intersect A B` checks designated evaluated world-space surfaces using BVH
overlap. `--allow-intersection A B` records an intentional pair already designated
by `--intersect`; it suppresses that pair's orange highlight, retaining counts.
Overlap is an observation, not a defect classification. Containment, coplanar
duplicates, tiny protrusions, clearances and consumer appearance need additional
review. Pair tests above a two-million triangle-pair budget are explicitly
unverified; source evaluation is limited to one million vertices/edges/faces
combined and 256 objects. Component records retain at most 64 bounds and
selection/overlap samples at most 1000 indices/pairs, with truncation flags.

Optional render failure retains geometry observations and a failed report.
Successful inspection does not mean an asset passed visual review. Include
silhouettes, undersides, attachment junctions and close-ups in the project-owned
batch manifest for `basaltw agent visuals batch`; compare observed camera
position/target/ortho scale, frame, resolution and diagnostic mode across runs.
Different bounds change auto framing. Use a project-owned fixed camera recipe
when framing must remain identical.

`basaltw agent blender smoke --json` uses private configuration/scripts/data,
cache and temporary paths to render a bundled 128x128 Cycles CPU scene. Inspect
its PNG, scene, settings and log. UI, Eevee, GPU and asset interchange remain
separate checks. Use the project's established renderer/backend for project
captures; hardware presence does not establish CUDA/HIP/OneAPI support.

```fish
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
