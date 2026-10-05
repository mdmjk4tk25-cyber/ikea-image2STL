"""Extract stage: dimensioned drawing (PNG/JPEG) -> spec JSON, fully local.

1. OCR (RapidOCR, ONNX models bundled in the wheel, no network) finds the
   dimension labels such as "70 cm (27 1/2")" with their position and
   orientation.
2. A per-type rule maps each cm value to a spec field (overall width, seat
   height, ...). The rules use value ranking, text orientation and position.
3. Every parameter the drawing does not show is filled with its default and
   recorded in ``assumptions``. The spec is meant to be reviewed by hand
   before building.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .spec import CM_INCH_TOLERANCE_MM, FURNITURE_TYPES, SCHEMA_VERSION, parse_inch

# Text box is treated as vertical (rotated label) when taller than wide by this factor.
VERTICAL_ASPECT = 1.5

_CM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*cm", re.IGNORECASE)
_INCH_RE = re.compile(r"\(([^)]*)")
# Common OCR confusions inside inch labels.
_OCR_FIXES = str.maketrans({"s": "8", "S": "8", "l": "1", "I": "1", "O": "0", "o": "0"})


class ExtractError(RuntimeError):
    pass


@dataclass
class Label:
    text: str
    cm: float
    inch: str  # normalised inch text, "" if unreadable
    x: float  # box centre, image pixels
    y: float
    vertical: bool
    score: float


def _box_geometry(box) -> tuple[float, float, float, float]:
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return sum(xs) / len(xs), sum(ys) / len(ys), max(xs) - min(xs), max(ys) - min(ys)


def normalise_inch(raw: str, cm: float) -> str:
    """Turn OCR'd inch text into 'W N/D' if it is plausible for ``cm``, else ''.

    OCR often drops the space between whole inches and the fraction
    ("173/4") or misreads glyphs. Candidates are generated and the one
    closest to the cm value wins, if it is within the cm/inch tolerance.
    """
    s = raw.translate(_OCR_FIXES).replace('"', "").replace("″", "").strip()
    # The "½" glyph is often read as "12" or "%2".
    s = re.sub(r"^(\d+)\s+(?:12|%2)$", r"\1 1/2", s)
    candidates: list[str] = []
    m = re.fullmatch(r"(\d+)\s*(\d)\s*/\s*(\d{1,2})", s)
    if m:
        whole, num, den = m.groups()
        candidates.append(f"{whole} {num}/{den}")
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        candidates.append(s)
    best, best_err = "", None
    for cand in candidates:
        try:
            err = abs(parse_inch(cand) * 25.4 - cm * 10)
        except ValueError:
            continue
        if err <= CM_INCH_TOLERANCE_MM and (best_err is None or err < best_err):
            best, best_err = cand, err
    return best


def read_labels(image_path: str | Path, engine=None) -> list[Label]:
    """OCR the drawing and return every text box that carries a cm value.

    Inch text in a separate box (labels split over two lines) is attached to
    the nearest cm label.
    """
    if engine is None:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
    result, _ = engine(str(image_path))
    boxes = []
    for box, text, score in result or []:
        x, y, w, h = _box_geometry(box)
        boxes.append((text, x, y, w, h, float(score)))

    labels: list[Label] = []
    orphans = []
    for text, x, y, w, h, score in boxes:
        m = _CM_RE.search(text)
        if m:
            cm = float(m.group(1).replace(",", "."))
            inch_m = _INCH_RE.search(text[m.end() :])
            inch = normalise_inch(inch_m.group(1), cm) if inch_m else ""
            labels.append(Label(text, cm, inch, x, y, h > VERTICAL_ASPECT * w, score))
        elif _INCH_RE.search(text):
            orphans.append((text, x, y, max(w, h)))

    for text, x, y, size in orphans:
        free = [lab for lab in labels if not lab.inch]
        if not free:
            break
        near = min(free, key=lambda lab: (lab.x - x) ** 2 + (lab.y - y) ** 2)
        if ((near.x - x) ** 2 + (near.y - y) ** 2) ** 0.5 <= size:
            near.inch = normalise_inch(_INCH_RE.search(text).group(1), near.cm)
            near.text = f"{near.text} {text}"
    return labels


# --- per-type mapping -------------------------------------------------------
# Each rule returns {spec path: Label} plus notes for the ambiguities list.

Mapping = dict[str, Label]


def _split(labels: list[Label]) -> tuple[list[Label], list[Label]]:
    horizontal = sorted((lab for lab in labels if not lab.vertical), key=lambda lab: -lab.cm)
    vertical = sorted((lab for lab in labels if lab.vertical), key=lambda lab: -lab.cm)
    return horizontal, vertical


def _assign(paths: list[str], labels: list[Label], mapping: Mapping) -> list[Label]:
    for path, lab in zip(paths, labels):
        mapping[path] = lab
    return labels[len(paths) :]


def map_cabinet(labels: list[Label]) -> tuple[Mapping, list[str]]:
    """Front + side view: the vertical label is the height; of the horizontal
    labels the largest is the width, the next the depth."""
    horizontal, vertical = _split(labels)
    mapping: Mapping = {}
    rest = _assign(["overall.width", "overall.depth"], horizontal, mapping)
    rest += _assign(["overall.height"], vertical, mapping)
    return mapping, [
        "Mapping rule: vertical label = height, largest horizontal = width, next = depth."
    ] + _unused(rest)


def map_coffee_table(labels: list[Label]) -> tuple[Mapping, list[str]]:
    """Perspective view: horizontal labels are length (largest) and width.
    The largest vertical label is the height; the other two are the shelf
    chain, upper one = tabletop underside to shelf, lower one = floor to shelf."""
    horizontal, vertical = _split(labels)
    mapping: Mapping = {}
    rest = _assign(["overall.width", "overall.depth"], horizontal, mapping)
    rest += _assign(["overall.height"], vertical, mapping)
    chain = sorted(vertical[1:3], key=lambda lab: lab.y)  # top of image first
    rest += _assign(
        ["params.shelf_clearance_below_top", "params.shelf_height"], chain, mapping
    )
    rest += vertical[3:]
    return mapping, [
        "Mapping rule: largest horizontal = length, next = width; largest vertical = height; "
        "of the other vertical labels the upper = tabletop underside to shelf, "
        "the lower = floor to shelf underside."
    ] + _unused(rest)


def map_bench(labels: list[Label]) -> tuple[Mapping, list[str]]:
    """Horizontal labels by size: overall width > seat width > depth > seat
    depth. Vertical labels: overall height > seat height."""
    horizontal, vertical = _split(labels)
    mapping: Mapping = {}
    rest = _assign(
        ["overall.width", "params.seat_width", "overall.depth", "params.seat_depth"],
        horizontal,
        mapping,
    )
    rest += _assign(["overall.height", "params.seat_height"], vertical, mapping)
    return mapping, [
        "Mapping rule: horizontal labels by size = overall width, seat width, depth, seat depth; "
        "vertical labels by size = height, seat height."
    ] + _unused(rest)


def _unused(labels: list[Label]) -> list[str]:
    return [f"OCR label {lab.text!r} ({lab.cm:g} cm) was not mapped to any field." for lab in labels]


MAPPERS = {"cabinet": map_cabinet, "coffee_table": map_coffee_table, "bench": map_bench}


def derive_params(ftype: str, overall: dict, params: dict, assumptions: list[str]) -> None:
    """Fill params that follow arithmetically from read values and defaults."""
    if ftype == "coffee_table" and {"shelf_clearance_below_top", "shelf_height"} <= params.keys():
        top = (
            overall.get("height", 0)
            - params["shelf_clearance_below_top"]
            - params["shelf_height"]
            - params["shelf_thickness"]
        )
        if top > 0:
            params["top_thickness"] = top
            assumptions.append(
                f"params.top_thickness = {top:g} derived: height - shelf_clearance_below_top"
                " - shelf_height - shelf_thickness."
            )


def to_spec(
    labels: list[Label], furniture_type: str, name: str, source_image: str
) -> dict:
    """Map OCR labels to a spec; unmapped values get defaults and are documented."""
    if furniture_type not in MAPPERS:
        raise ExtractError(f"unknown furniture type {furniture_type!r}")
    mapping, ambiguities = MAPPERS[furniture_type](labels)
    defs = FURNITURE_TYPES[furniture_type]
    assumptions: list[str] = []

    overall: dict = {}
    params: dict = {}
    measurements = []
    for path, lab in mapping.items():
        section, key = path.split(".")
        (overall if section == "overall" else params)[key] = lab.cm * 10
        measurements.append({"label": lab.text, "cm": lab.cm, "inch": lab.inch, "maps_to": path})
        if not lab.inch:
            ambiguities.append(f"Inch value of {lab.text!r} unreadable; cm/inch cross-check skipped.")

    for key in ("width", "depth", "height"):
        if key not in overall:
            ambiguities.append(f"overall.{key} not found in the drawing; set it by hand.")
            overall[key] = 0

    defaulted = []
    for pname, pdef in defs.items():
        if pname not in params:
            params[pname] = int(pdef.default) if pdef.integer else pdef.default
            defaulted.append(pname)
    derive_params(furniture_type, overall, params, assumptions)
    for pname in defaulted:
        if not any(a.startswith(f"params.{pname} ") for a in assumptions):
            assumptions.append(
                f"params.{pname} = {params[pname]:g} default ({defs[pname].description}); "
                "not dimensioned in the drawing."
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "furniture_type": furniture_type,
        "source_image": source_image,
        "units": "mm",
        "overall": overall,
        "params": params,
        "measurements": measurements,
        "assumptions": assumptions,
        "ambiguities": ambiguities,
    }


def extract(
    image_path: str | Path,
    *,
    furniture_type: str,
    name: str | None = None,
    engine=None,
) -> dict:
    """Read ``image_path`` locally and return an unvalidated spec."""
    image_path = Path(image_path)
    if not image_path.is_file():
        raise ExtractError(f"image not found: {image_path}")
    labels = read_labels(image_path, engine)
    if not labels:
        raise ExtractError("no 'NN cm' labels found in the drawing")
    return to_spec(labels, furniture_type, name or image_path.stem, str(image_path))
