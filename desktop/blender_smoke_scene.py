"""Bundled scene payload executed by Blender's Python, not the host interpreter."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time


def main() -> None:
    import bpy
    from mathutils import Vector

    directory = Path(sys.argv[sys.argv.index("--") + 1])
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_monkey_add(location=(0, 0, 0.9))
    subject = bpy.context.object
    subject.name = "Smoke Suzanne"
    modifier = subject.modifiers.new("Subdivision", "SUBSURF")
    modifier.levels = modifier.render_levels = 1
    for polygon in subject.data.polygons:
        polygon.use_smooth = True
    material = bpy.data.materials.new("Teal")
    material.diffuse_color = (0.05, 0.55, 0.5, 1)
    subject.data.materials.append(material)

    bpy.ops.mesh.primitive_plane_add(size=200)
    floor = bpy.context.object
    material = bpy.data.materials.new("Floor")
    material.diffuse_color = (0.1, 0.12, 0.16, 1)
    floor.data.materials.append(material)
    bpy.ops.object.camera_add(location=(4, -6, 3.2))
    camera = bpy.context.object
    camera.name = "Smoke Camera"
    camera.rotation_euler = (Vector((0, 0, 0.9)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.lens = 55
    for location, energy, size in (((-3, -4, 6), 1000, 4), ((4, 1, 4), 800, 3)):
        bpy.ops.object.light_add(type="AREA", location=location)
        light = bpy.context.object
        light.data.energy = energy
        light.data.shape = "DISK"
        light.data.size = size
        light.rotation_euler = (Vector((0, 0, 0.9)) - light.location).to_track_quat("-Z", "Y").to_euler()

    scene = bpy.context.scene
    scene.camera = camera
    scene.frame_set(1)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    scene.cycles.seed = 0
    scene.cycles.use_denoising = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 2
    scene.render.resolution_x = scene.render.resolution_y = 128
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.filepath = str(directory / "render.png")
    scene.world.color = (0.08, 0.08, 0.08)
    bpy.ops.wm.save_as_mainfile(filepath=str(directory / "scene.blend"))
    started = time.monotonic()
    bpy.ops.render.render(write_still=True)
    settings = {
        "blender_version": bpy.app.version_string, "python_version": sys.version.split()[0],
        "scene": scene.name, "camera": camera.name, "frame": scene.frame_current,
        "width": scene.render.resolution_x, "height": scene.render.resolution_y,
        "engine": scene.render.engine, "device": scene.cycles.device,
        "samples": scene.cycles.samples, "seed": scene.cycles.seed,
        "threads": scene.render.threads, "denoising": scene.cycles.use_denoising,
        "render_seconds": round(time.monotonic() - started, 3), "render_completed": True,
    }
    (directory / "settings.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
