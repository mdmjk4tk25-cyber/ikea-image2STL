"""Furniture spec: type definitions, loading, saving and validation.

A spec is a JSON file in millimetres. It is the single source of truth for
the build stage: templates read every dimension from it and contain no
numeric dimensions of their own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# cm values in IKEA drawings are rounded to whole cm, inch values to 1/8".
# Worst case disagreement is ~5 mm + ~1.6 mm, so 8 mm flags real misreads
# (e.g. 27 vs 72) without tripping on rounding.
CM_INCH_TOLERANCE_MM = 8.0
# A measurement must map exactly (to float noise) to the spec value it names.
MAPPING_TOLERANCE_MM = 0.5


@dataclass(frozen=True)
class ParamDef:
    description: str
    default: float  # assumption used when the drawing does not show the value
    integer: bool = False
    minimum: float = 0.0  # exclusive unless integer, then inclusive


FURNITURE_TYPES: dict[str, dict[str, ParamDef]] = {
    "cabinet": {
        "panel_thickness": ParamDef("Thickness of side, top and bottom panels", 18),
        "back_thickness": ParamDef("Thickness of the back panel (inset between sides/top/bottom)", 3),
        "door_count": ParamDef("Number of front doors, side by side", 2, integer=True, minimum=0),
        "door_thickness": ParamDef("Door thickness; doors are inset flush with the carcass front", 18),
        "door_gap": ParamDef("Gap around and between doors", 3),
        "shelf_count": ParamDef("Number of evenly spaced inner shelves", 0, integer=True, minimum=0),
        "shelf_thickness": ParamDef("Inner shelf thickness", 18),
    },
    "coffee_table": {
        "top_thickness": ParamDef("Tabletop thickness", 50),
        "leg_size": ParamDef("Square leg cross-section edge length", 50),
        "shelf_thickness": ParamDef("Lower shelf thickness", 10),
        "shelf_clearance_below_top": ParamDef(
            "Vertical gap from tabletop underside to shelf top surface", 190
        ),
        "shelf_height": ParamDef("Floor to shelf underside", 200),
    },
    "bench": {
        "seat_height": ParamDef("Floor to top of seat", 440),
        "seat_width": ParamDef("Clear seat width between the two side frames", 1030),
        "seat_depth": ParamDef("Seat depth from front edge to backrest front face", 410),
        "seat_thickness": ParamDef("Seat slab thickness", 40),
        "frame_member": ParamDef("Side-frame member cross-section (Y and Z extent)", 40),
        "armrest_height": ParamDef("Floor to top of armrest", 620),
        "stretcher_height": ParamDef("Floor to underside of the low side stretcher", 150),
        "backrest_thickness": ParamDef("Backrest slab thickness (measured horizontally)", 30),
    },
}


class SpecError(ValueError):
    """Raised when a spec fails validation. ``errors`` lists every problem."""

    def __init__(self, errors: list[str]):
        super().__init__("invalid spec:\n  - " + "\n  - ".join(errors))
        self.errors = errors


_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}


def parse_inch(text: str) -> float:
    """Parse IKEA inch notation: '27 ½"', '27 1/2', '13.75', '7 ⅞'."""
    s = text.replace('"', "").replace("″", "").replace("in", "").strip()
    for sym, val in _FRACTIONS.items():
        if sym in s:
            s = s.replace(sym, "").strip()
            return (float(s) if s else 0.0) + val
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s+(\d+)\s*/\s*(\d+))?", s)
    if m:
        whole = float(m.group(1))
        if m.group(2):
            whole += int(m.group(2)) / int(m.group(3))
        return whole
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", s)
    if m:
        return int(m.group(1)) / int(m.group(2))
    raise ValueError(f"cannot parse inch value {text!r}")


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _resolve(spec: dict, path: str) -> Any:
    node: Any = spec
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise KeyError(path)
        node = node[key]
    return node


def validate_spec(spec: dict) -> dict:
    """Check structure, ranges, cm/inch consistency and geometric feasibility.

    Returns the spec unchanged; raises SpecError listing all problems.
    """
    errors: list[str] = []
    if not isinstance(spec, dict):
        raise SpecError(["spec must be a JSON object"])

    if spec.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if spec.get("units") != "mm":
        errors.append("units must be 'mm'")
    if not isinstance(spec.get("name"), str) or not spec.get("name"):
        errors.append("name must be a non-empty string")
    elif not re.fullmatch(r"[A-Za-z0-9_.-]+", spec["name"]):
        errors.append("name may only contain letters, digits, '_', '-', '.'")

    ftype = spec.get("furniture_type")
    if ftype not in FURNITURE_TYPES:
        errors.append(f"furniture_type must be one of {sorted(FURNITURE_TYPES)}")

    overall = spec.get("overall")
    if not isinstance(overall, dict):
        errors.append("overall must be an object with width, depth, height")
        overall = {}
    for key in ("width", "depth", "height"):
        v = overall.get(key)
        if not _is_number(v) or v <= 0:
            errors.append(f"overall.{key} must be a positive number")
    extra = set(overall) - {"width", "depth", "height"}
    if extra:
        errors.append(f"overall has unknown keys {sorted(extra)}")

    params = spec.get("params")
    if not isinstance(params, dict):
        errors.append("params must be an object")
        params = {}
    if ftype in FURNITURE_TYPES:
        defs = FURNITURE_TYPES[ftype]
        for name, pdef in defs.items():
            if name not in params:
                errors.append(f"params.{name} is missing ({pdef.description})")
                continue
            v = params[name]
            if not _is_number(v):
                errors.append(f"params.{name} must be a number")
            elif pdef.integer and (v != int(v) or v < pdef.minimum):
                errors.append(f"params.{name} must be an integer >= {pdef.minimum:g}")
            elif not pdef.integer and v <= pdef.minimum:
                errors.append(f"params.{name} must be > {pdef.minimum:g}")
        unknown = set(params) - set(defs)
        if unknown:
            errors.append(f"params has unknown keys {sorted(unknown)} for type {ftype}")

    for i, m in enumerate(spec.get("measurements", [])):
        where = f"measurements[{i}]"
        if not isinstance(m, dict) or not _is_number(m.get("cm")):
            errors.append(f"{where} needs a numeric 'cm'")
            continue
        mm = m["cm"] * 10
        if m.get("inch"):
            try:
                inch_mm = parse_inch(str(m["inch"])) * 25.4
                if abs(inch_mm - mm) > CM_INCH_TOLERANCE_MM:
                    errors.append(
                        f"{where} ({m.get('label', '')}): {m['cm']} cm and {m['inch']}\" "
                        f"disagree by {abs(inch_mm - mm):.1f} mm"
                    )
            except ValueError as e:
                errors.append(f"{where}: {e}")
        target = m.get("maps_to")
        if target:
            try:
                value = _resolve(spec, target)
            except KeyError:
                errors.append(f"{where}: maps_to {target!r} does not exist in spec")
            else:
                if _is_number(value) and abs(value - mm) > MAPPING_TOLERANCE_MM:
                    errors.append(f"{where}: {target}={value} but drawing says {mm:g} mm")

    for key in ("assumptions", "ambiguities"):
        v = spec.get(key, [])
        if not isinstance(v, list) or not all(isinstance(s, str) for s in v):
            errors.append(f"{key} must be a list of strings")

    if not errors:
        errors.extend(_feasibility(ftype, overall, params))
    if errors:
        raise SpecError(errors)
    return spec


def _feasibility(ftype: str, o: dict, p: dict) -> list[str]:
    """Type-specific checks that the parameters describe buildable geometry."""
    e: list[str] = []
    W, D, H = o["width"], o["depth"], o["height"]
    if ftype == "cabinet":
        t = p["panel_thickness"]
        if 2 * t >= W or 2 * t >= H:
            e.append("panel_thickness too large for overall width/height")
        if p["door_thickness"] + p["back_thickness"] >= D:
            e.append("door_thickness + back_thickness must be < depth")
        n = int(p["door_count"])
        if n:
            door_w = (W - 2 * t - (n + 1) * p["door_gap"]) / n
            door_h = H - 2 * t - 2 * p["door_gap"]
            if door_w <= 0 or door_h <= 0:
                e.append("doors do not fit: reduce door_gap or panel_thickness")
        k = int(p["shelf_count"])
        if k and k * p["shelf_thickness"] >= H - 2 * t:
            e.append("shelves do not fit inside the carcass")
    elif ftype == "coffee_table":
        stack = (
            p["top_thickness"]
            + p["shelf_clearance_below_top"]
            + p["shelf_thickness"]
            + p["shelf_height"]
        )
        if abs(stack - H) > MAPPING_TOLERANCE_MM:
            e.append(
                f"top_thickness + shelf_clearance_below_top + shelf_thickness + shelf_height"
                f" = {stack:g}, expected height {H:g}"
            )
        if 2 * p["leg_size"] >= min(W, D):
            e.append("leg_size too large for tabletop")
    elif ftype == "bench":
        m = p["frame_member"]
        side = (W - p["seat_width"]) / 2
        if side <= 0:
            e.append("seat_width must be smaller than overall width")
        if not (p["seat_thickness"] < p["seat_height"] < p["armrest_height"] <= H):
            e.append("need seat_thickness < seat_height < armrest_height <= height")
        if p["armrest_height"] - m <= p["seat_height"]:
            e.append("armrest bottom must be above seat top")
        if p["seat_depth"] + p["backrest_thickness"] > D:
            e.append("seat_depth + backrest_thickness must be <= depth")
        if p["stretcher_height"] + m >= p["seat_height"] - p["seat_thickness"]:
            e.append("stretcher must sit below the seat underside")
        if 2 * m >= D:
            e.append("frame_member too large for depth")
    return e


def load_spec(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        try:
            spec = json.load(f)
        except json.JSONDecodeError as e:
            raise SpecError([f"{path}: not valid JSON ({e})"]) from e
    return validate_spec(spec)


def save_spec(spec: dict, path: str | Path) -> None:
    validate_spec(spec)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
