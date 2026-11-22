"""Map source-PNG or presented-view points to monitor pixels, without input."""

from __future__ import annotations

from desktop.portal_capture import validate_crop


def map_observation_points(capture: dict, current: dict, view_geometry: list[int] | None, x: int, y: int, *,
                           view_origin: list[int] | None = None,
                           image_coordinates: bool = False,
                           to_x: int | None = None, to_y: int | None = None) -> dict:
    """Bind an explicit coordinate basis to an existing, still-current capture.

    Only the helper's current freshness is authoritative. This read-only check
    never captures, consumes a token or promises that a semantic target stayed put.
    """
    import re
    source = "capture"
    if "error" in capture:
        raise ValueError("Observation receipt failed; capture and inspect again, never replay input")
    if "observation" in capture:
        source = "observation"
        capture = capture["observation"]
        if not isinstance(capture, dict):
            raise ValueError("Receipt has no usable nested observation; capture and inspect again, never replay input")
    elif any(key in capture for key in ("input_id", "input_delivery", "input_completed_at", "dispatch_stage")):
        raise ValueError("Input receipt has no nested observation; capture and inspect again, never replay input")
    if "error" in capture:
        raise ValueError("Observation failed; capture and inspect again, never replay input")
    observation = capture.get("observation_id")
    if not isinstance(observation, str) or not re.fullmatch(r"[0-9a-f]{32}", observation):
        raise ValueError("Capture has no usable observation ID; capture and inspect again")
    for key in ("observation_id", "generation", "control_revision", "geometry", "image_origin", "image_geometry"):
        if capture.get(key) is None or capture[key] != current.get(key):
            raise ValueError(f"Capture {key} changed or is unavailable; capture and inspect again")
    if current.get("state") != "running" or current.get("capture_freshness", {}).get("fresh") is not True:
        raise ValueError("Capture expired or helper stopped; capture and inspect again")
    if image_coordinates and view_origin is not None:
        raise ValueError("Image coordinates do not use viewer margins")
    origin = [0, 0] if view_origin is None else view_origin
    if (not isinstance(origin, list) or len(origin) != 2
            or any(type(n) is not int or not 0 <= n <= 16384 for n in origin)):
        raise ValueError("View origin must be two nonnegative viewer-margin offsets")
    if type(x) is not int or type(y) is not int or (to_x is not None and type(to_x) is not int) or (to_y is not None and type(to_y) is not int):
        raise ValueError("Presented points must use integer pixels")
    result = map_capture_points(capture["geometry"], view_geometry, x - origin[0], y - origin[1],
        crop=capture["image_origin"] + capture["image_geometry"],
        image_coordinates=image_coordinates,
        to_x=to_x - origin[0] if to_x is not None else None,
        to_y=to_y - origin[1] if to_y is not None else None)
    context = {key: capture[key] for key in ("observation_id", "generation", "control_revision")}
    return {**result, **context, "capture_source": source,
            "view_origin": None if image_coordinates else origin, "capture_freshness": current["capture_freshness"],
            "input_context_args": f"--generation {context['generation']} --geometry {' '.join(map(str, capture['geometry']))} --observation {observation}",
            "limit": "Input revalidates the token; inspect moving/recentered targets again. No automatic retry."}


def map_capture_points(
    geometry: list[int], view_geometry: list[int] | None, x: int, y: int, *,
    crop: list[int] | None = None, to_x: int | None = None, to_y: int | None = None,
    image_coordinates: bool = False,
) -> dict:
    """Convert image-local points using explicit geometry, never live desktop state.

    Presented-view geometry excludes viewer margins. Image-coordinate mode uses
    pixels in the supplied PNG, independent of viewer scaling; it requires no view
    geometry. Both add the capture origin once. Integer division selects the
    source pixel; portal logical scaling remains the backend's responsibility.
    """
    if type(image_coordinates) is not bool:
        raise ValueError("Image coordinate selection must be boolean")
    if image_coordinates and view_geometry is not None:
        raise ValueError("Choose source-image coordinates or presented-view geometry, not both")
    for label, size in (("Monitor", geometry),) + (() if image_coordinates else (("View", view_geometry),)):
        if (not isinstance(size, list) or len(size) != 2
                or any(type(n) is not int or not 0 < n <= 16384 for n in size)):
            raise ValueError(f"{label} geometry must be two integer dimensions from 1 to 16384")
    if geometry[0] * geometry[1] > 32 * 1024 * 1024:
        raise ValueError("Monitor geometry exceeds the 32-megapixel capture limit")
    validate_crop(crop, geometry)
    origin_x, origin_y, width, height = crop if crop is not None else [0, 0, *geometry]
    point_geometry = [width, height] if image_coordinates else view_geometry

    def point(px: int, py: int) -> tuple[int, int]:
        if (type(px) is not int or type(py) is not int
                or not 0 <= px < point_geometry[0] or not 0 <= py < point_geometry[1]):
            raise ValueError("Point must be integer image-local pixels within the selected coordinate space")
        return (origin_x + px * width // point_geometry[0],
                origin_y + py * height // point_geometry[1])

    monitor_x, monitor_y = point(x, y)
    coordinates = {"x": monitor_x, "y": monitor_y}
    if (to_x is None) != (to_y is None):
        raise ValueError("Drag mapping requires both --to-x and --to-y")
    if to_x is not None:
        coordinates["to_x"], coordinates["to_y"] = point(to_x, to_y)
    return {
        "geometry": geometry,
        "image_geometry": [width, height],
        "image_origin": [origin_x, origin_y],
        "view_geometry": view_geometry,
        "point_space": "source-image" if image_coordinates else "presented-view",
        "scale": [width / point_geometry[0], height / point_geometry[1]],
        "rounding": "floor",
        "input_coordinates": coordinates,
        "input_args": " ".join(f"--{key.replace('_', '-')} {value}" for key, value in coordinates.items()),
        "input_sent": False,
    }
