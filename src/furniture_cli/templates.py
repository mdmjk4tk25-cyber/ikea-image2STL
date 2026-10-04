"""build123d templates, one per furniture type.

Coordinate system: X = width (left to right), Y = depth (front y=0 to back),
Z = height (floor z=0). Every dimension comes from the spec; the templates
only encode how parts relate to each other.
"""

from __future__ import annotations

from collections.abc import Callable

from build123d import Align, Box, Part, Plane, Polygon, Pos, Solid, extrude

Parts = dict[str, Part | Solid]

_MIN = (Align.MIN, Align.MIN, Align.MIN)


def _box(x: float, y: float, z: float, dx: float, dy: float, dz: float) -> Part:
    """Axis-aligned box with its minimum corner at (x, y, z)."""
    return Pos(x, y, z) * Box(dx, dy, dz, align=_MIN)


def cabinet(spec: dict) -> Parts:
    o, p = spec["overall"], spec["params"]
    W, D, H = o["width"], o["depth"], o["height"]
    t, bt = p["panel_thickness"], p["back_thickness"]
    dt, gap = p["door_thickness"], p["door_gap"]

    parts: Parts = {
        "side_left": _box(0, 0, 0, t, D, H),
        "side_right": _box(W - t, 0, 0, t, D, H),
        "bottom": _box(t, 0, 0, W - 2 * t, D - bt, t),
        "top": _box(t, 0, H - t, W - 2 * t, D - bt, t),
        "back": _box(t, D - bt, t, W - 2 * t, bt, H - 2 * t),
    }

    n_shelves, st = int(p["shelf_count"]), p["shelf_thickness"]
    if n_shelves:
        inner_h = H - 2 * t
        spacing = (inner_h - n_shelves * st) / (n_shelves + 1)
        for i in range(n_shelves):
            z = t + spacing * (i + 1) + st * i
            parts[f"shelf_{i + 1}"] = _box(t, dt, z, W - 2 * t, D - bt - dt, st)

    n_doors = int(p["door_count"])
    if n_doors:
        door_w = (W - 2 * t - (n_doors + 1) * gap) / n_doors
        door_h = H - 2 * t - 2 * gap
        for i in range(n_doors):
            x = t + gap + i * (door_w + gap)
            parts[f"door_{i + 1}"] = _box(x, 0, t + gap, door_w, dt, door_h)
    return parts


def coffee_table(spec: dict) -> Parts:
    o, p = spec["overall"], spec["params"]
    W, D, H = o["width"], o["depth"], o["height"]
    tt, leg = p["top_thickness"], p["leg_size"]
    leg_h = H - tt

    parts: Parts = {"top": _box(0, 0, leg_h, W, D, tt)}
    corners = {
        "leg_front_left": (0, 0),
        "leg_front_right": (W - leg, 0),
        "leg_back_left": (0, D - leg),
        "leg_back_right": (W - leg, D - leg),
    }
    for name, (x, y) in corners.items():
        parts[name] = _box(x, y, 0, leg, leg, leg_h)
    # Shelf spans between the inner faces of the legs along X and uses the
    # full depth (the legs sit outside its X range, so nothing intersects).
    parts["shelf"] = _box(leg, 0, p["shelf_height"], W - 2 * leg, D, p["shelf_thickness"])
    return parts


def bench(spec: dict) -> Parts:
    o, p = spec["overall"], spec["params"]
    W, D, H = o["width"], o["depth"], o["height"]
    m = p["frame_member"]
    side = (W - p["seat_width"]) / 2
    seat_h, seat_t = p["seat_height"], p["seat_thickness"]
    arm_h, st_h = p["armrest_height"], p["stretcher_height"]
    seat_d, back_t = p["seat_depth"], p["backrest_thickness"]

    def side_frame(x: float) -> Part:
        front_post = _box(x, 0, 0, side, m, arm_h)
        back_post = _box(x, D - m, 0, side, m, H)
        armrest = _box(x, 0, arm_h - m, side, D, m)
        stretcher = _box(x, 0, st_h, side, D, m)
        return front_post + back_post + armrest + stretcher

    # Backrest: slab leaning back from the rear edge of the seat to the top
    # rear corner, drawn in the YZ plane and extruded across the seat width.
    profile = Polygon(
        (seat_d, seat_h),
        (seat_d + back_t, seat_h),
        (D, H),
        (D - back_t, H),
        align=None,
    )
    backrest = Pos(side, 0, 0) * extrude(Plane.YZ * profile, amount=p["seat_width"])

    return {
        "frame_left": side_frame(0),
        "frame_right": side_frame(W - side),
        "seat": _box(side, 0, seat_h - seat_t, p["seat_width"], seat_d, seat_t),
        "backrest": backrest,
    }


TEMPLATES: dict[str, Callable[[dict], Parts]] = {
    "cabinet": cabinet,
    "coffee_table": coffee_table,
    "bench": bench,
}
