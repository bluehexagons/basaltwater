"""Asset inspection payload run by Blender; imports bpy only inside main."""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import sys


def bounds(vertices: list) -> dict | None:
    if not vertices:
        return None
    low = [min(v[axis] for v in vertices) for axis in range(3)]
    high = [max(v[axis] for v in vertices) for axis in range(3)]
    return {"min": low, "max": high, "dimensions": [b - a for a, b in zip(low, high)]}


def topology(vertices: list, edges: list, polygons: list) -> tuple[dict, list[int]]:
    """Return vertex-connected islands, including loose vertices and edges."""
    parents = list(range(len(vertices)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for a, b in edges:
        parents[root(b)] = root(a)
    groups = {}
    for index in range(len(vertices)):
        groups.setdefault(root(index), []).append(index)
    components = sorted(groups.values(), key=lambda group: (-len(group), group[0]))
    membership = [0] * len(vertices)
    records = []
    for index, group in enumerate(components):
        for vertex in group:
            membership[vertex] = index
        if index < 64:
            records.append({"id": index, "vertices": len(group), "bounds": bounds([vertices[v] for v in group])})
    uses = Counter(tuple(sorted((a, b))) for poly in polygons for a, b in zip(poly, [*poly[1:], poly[0]]))
    return {"component_count": len(components), "components": records,
            "components_truncated": len(components) > 64,
            "boundary_edges": sum(uses[tuple(sorted(edge))] == 1 for edge in edges),
            "nonmanifold_edges": sum(uses[tuple(sorted(edge))] > 2 for edge in edges),
            "loose_edges": sum(uses[tuple(sorted(edge))] == 0 for edge in edges)}, membership


def selected(elements) -> dict:
    indices = [item.index for item in elements if item.select]
    return {"count": len(indices), "indices": indices[:1000], "truncated": len(indices) > 1000}


def diagnostic_material(bpy, mode: str, highlight: bool = False):
    material = bpy.data.materials.new(f"Inspection {mode} {highlight}")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    color = (1, 0.22, 0.02, 1) if highlight else (0.12, 0.65, 0.62, 1)
    emission.inputs["Color"].default_value = color
    if mode in {"wire", "orientation"}:
        mix = nodes.new("ShaderNodeMixRGB")
        mix.inputs[1].default_value = (0.08, 0.3, 0.85, 1) if mode == "orientation" else color
        mix.inputs[2].default_value = (0.9, 0.04, 0.04, 1) if mode == "orientation" else (0.01, 0.02, 0.03, 1)
        if mode == "wire":
            source = nodes.new("ShaderNodeWireframe")
            source.use_pixel_size = True
            source.inputs["Size"].default_value = 1
            socket = source.outputs["Fac"]
        else:
            socket = nodes.new("ShaderNodeNewGeometry").outputs["Backfacing"]
        links.new(socket, mix.inputs[0])
        links.new(mix.outputs[0], emission.inputs["Color"])
    links.new(emission.outputs[0], output.inputs["Surface"])
    return material


def render_views(bpy, directory: Path, meshes: dict, highlights: dict, size: int) -> list[dict]:
    from mathutils import Vector

    scene = bpy.data.scenes.new("Asset inspection diagnostics")
    bpy.context.window.scene = scene
    objects = []
    all_vertices = []
    for name, (mesh, matrix, membership) in meshes.items():
        obj = bpy.data.objects.new(name + " inspection", mesh.copy())
        obj.matrix_world = matrix
        scene.collection.objects.link(obj)
        objects.append((name, obj, membership))
        all_vertices.extend(matrix @ vertex.co for vertex in mesh.vertices)
    extent = bounds(all_vertices)
    if extent is None or max(extent["dimensions"]) <= 0:
        raise ValueError("No nonzero mesh bounds available for diagnostic views")
    center = Vector([(a + b) / 2 for a, b in zip(extent["min"], extent["max"])])
    span = max(extent["dimensions"])
    camera = bpy.data.objects.new("Inspection camera", bpy.data.cameras.new("Inspection camera"))
    scene.collection.objects.link(camera)
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = span * 1.2
    camera.data.clip_start = max(1e-6, span * 0.001)
    camera.data.clip_end = span * 10
    scene.camera = camera
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 4
    scene.cycles.seed = 0
    scene.cycles.use_denoising = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 2
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "Standard"
    scene.use_nodes = False
    images = []
    materials = {(mode, flag): diagnostic_material(bpy, mode, flag)
                 for mode in ("solid", "wire", "orientation", "islands") for flag in (False, True)}
    for view, direction in (("front", (0, -1, 0)), ("rear", (0, 1, 0)), ("underside", (0, 0, -1))):
        camera.location = center + Vector(direction) * span * 3
        camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
        for mode in ("solid", "wire", "orientation", "islands"):
            for name, obj, membership in objects:
                obj.data.materials.clear()
                obj.data.materials.append(materials[mode, False])
                obj.data.materials.append(materials[mode, True])
                for polygon in obj.data.polygons:
                    polygon.material_index = int(mode == "islands" and (
                        membership[polygon.vertices[0]] > 0 or polygon.index in highlights.get(name, set())))
            filename = f"{view}-{mode}.png"
            scene.render.filepath = str(directory / filename)
            bpy.ops.render.render(write_still=True)
            images.append({"image": filename, "camera": view, "mode": mode,
                           "camera_position": list(camera.location), "target": list(center),
                           "ortho_scale": camera.data.ortho_scale, "width": size, "height": size,
                           "engine": "CYCLES", "device": "CPU", "samples": 4, "seed": 0})
    bpy.ops.wm.save_as_mainfile(filepath=str(directory / "diagnostics.blend"))
    return images


def main() -> None:
    import bpy
    from mathutils.bvhtree import BVHTree

    os.umask(0o077)
    directory = Path(sys.argv[sys.argv.index("--") + 1])
    request = json.loads((directory / "request.json").read_text())
    bpy.ops.wm.open_mainfile(filepath=request["source"], load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    # Synchronize edit selection back to the source mesh before evaluating it.
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    names = set(request["objects"])
    missing = names - set(scene.objects.keys())
    if missing:
        raise ValueError(f"Unknown scene objects: {sorted(missing)}")
    objects = sorted((obj for obj in scene.objects if not names or obj.name in names), key=lambda obj: obj.name)
    if len(objects) > 256:
        raise ValueError("Scene exceeds 256 objects; restrict with --object")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    records, meshes, surfaces, highlights = [], {}, {}, {}
    total = 0
    for obj in objects:
        record = {"name": obj.name, "type": obj.type, "parent": obj.parent.name if obj.parent else None,
                  "selected": obj.select_get(), "hidden_viewport": obj.hide_get(), "hidden_render": obj.hide_render,
                  "matrix_world": [list(row) for row in obj.matrix_world],
                  "location": list(obj.location), "rotation_euler": list(obj.rotation_euler), "scale": list(obj.scale),
                  "negative_determinant": obj.matrix_world.determinant() < 0,
                  "modifiers": [{"name": mod.name, "type": mod.type, "viewport": mod.show_viewport, "render": mod.show_render} for mod in obj.modifiers]}
        records.append(record)
        if obj.type != "MESH":
            continue
        record["source_selection"] = {"vertices": selected(obj.data.vertices), "edges": selected(obj.data.edges), "faces": selected(obj.data.polygons)}
        evaluated = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
        total += len(mesh.vertices) + len(mesh.edges) + len(mesh.polygons)
        if total > 1000000:
            raise ValueError("Evaluated geometry exceeds one million elements; restrict with --object")
        matrix = evaluated.matrix_world.copy()
        vertices = [list(matrix @ vertex.co) for vertex in mesh.vertices]
        polygons = [list(polygon.vertices) for polygon in mesh.polygons]
        graph, membership = topology(vertices, [list(edge.vertices) for edge in mesh.edges], polygons)
        meshes[obj.name] = mesh, matrix, membership
        surfaces[obj.name] = vertices, polygons
        record.update(evaluated={"vertices": len(mesh.vertices), "edges": len(mesh.edges), "faces": len(mesh.polygons),
                                 "bounds": bounds(vertices), **graph,
                                 "zero_area_faces": sum(poly.area <= 1e-12 for poly in mesh.polygons),
                                 "zero_length_normals": sum(poly.normal.length == 0 for poly in mesh.polygons),
                                 "materials": [material.name if material else None for material in mesh.materials],
                                 "material_face_counts": dict(Counter(str(poly.material_index) for poly in mesh.polygons)),
                                 "uv_layers": [layer.name for layer in mesh.uv_layers]})
    intersections = []
    for first, second in request["intersections"]:
        if first not in meshes or second not in meshes:
            raise ValueError(f"Intersection pair must name inspected scene meshes: {first}, {second}")
        a, b = surfaces[first], surfaces[second]
        # BVH.overlap materializes its result; bound the theoretical pair count.
        cost = sum(max(0, len(poly) - 2) for poly in a[1]) * sum(max(0, len(poly) - 2) for poly in b[1])
        intentional = {first, second} in [set(pair) for pair in request["allowed_intersections"]]
        if cost > 2000000:
            intersections.append({"objects": [first, second], "intentional": intentional, "status": "unverified", "reason": "triangle pair budget exceeded"})
            continue
        overlap = BVHTree.FromPolygons(*a).overlap(BVHTree.FromPolygons(*b)) if a[1] and b[1] else []
        intersections.append({"objects": [first, second], "intentional": intentional, "status": "observed",
                              "overlapping_face_pairs": len(overlap), "face_pairs": overlap[:1000], "truncated": len(overlap) > 1000})
        if not intentional:
            for name, indices in ((first, {pair[0] for pair in overlap}), (second, {pair[1] for pair in overlap})):
                highlights.setdefault(name, set()).update(indices)
    result = {"schema_version": 1, "blender_version": bpy.app.version_string, "scene": scene.name,
              "frame": scene.frame_current, "evaluation": "viewport dependency graph", "objects": records,
              "intersections": intersections, "images": [],
              "limits": ["Instances are not expanded; inspect source scene mesh objects.",
                         "Surface overlap does not detect full containment or prove a defect.",
                         "Coplanar overlap, unusual protrusions and outward normals need visual review.",
                         "Diagnostic materials do not represent consumer appearance."]}
    # Retain geometry observations if optional rendering fails or times out.
    (directory / "inspection.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    if request["render_views"]:
        result["images"] = render_views(bpy, directory, meshes, highlights, request["size"])
        (directory / "inspection.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
