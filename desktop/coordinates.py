"""Map points in a presented capture to selected-monitor pixels, without input."""

from __future__ import annotations

from desktop.portal_capture import validate_crop


def map_capture_points(
    geometry: list[int], view_geometry: list[int], x: int, y: int, *,
    crop: list[int] | None = None, to_x: int | None = None, to_y: int | None = None,
) -> dict:
    """Convert image-local points using explicit geometry, never live desktop state.

    The view is the entire PNG as presented, excluding viewer margins. Integer
    division selects the source pixel containing the mapped point. Portal logical
    scaling remains the input backend's responsibility.
    """
    for label, size in (("Monitor", geometry), ("View", view_geometry)):
        if (not isinstance(size, list) or len(size) != 2
                or any(type(n) is not int or not 0 < n <= 16384 for n in size)):
            raise ValueError(f"{label} geometry must be two integer dimensions from 1 to 16384")
    if geometry[0] * geometry[1] > 32 * 1024 * 1024:
        raise ValueError("Monitor geometry exceeds the 32-megapixel capture limit")
    validate_crop(crop, geometry)
    origin_x, origin_y, width, height = crop if crop is not None else [0, 0, *geometry]

    def point(px: int, py: int) -> tuple[int, int]:
        if (type(px) is not int or type(py) is not int
                or not 0 <= px < view_geometry[0] or not 0 <= py < view_geometry[1]):
            raise ValueError("Point must be integer image-local pixels within the presented view")
        return (origin_x + px * width // view_geometry[0],
                origin_y + py * height // view_geometry[1])

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
        "scale": [width / view_geometry[0], height / view_geometry[1]],
        "rounding": "floor",
        "input_coordinates": coordinates,
        "input_args": " ".join(f"--{key.replace('_', '-')} {value}" for key, value in coordinates.items()),
        "input_sent": False,
    }
