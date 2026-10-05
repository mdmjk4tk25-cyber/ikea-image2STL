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
    s = re.sub(r"^(\d+)\s+(?:12|%2|1%2|V2|1V2|1V/2)$", r"\1 1/2", s)
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


# OCR passes at these upscale factors are merged. Upscaling gives the text
# recogniser more pixels per glyph, which mainly helps the small inch
# fractions; the detector input size is capped by RapidOCR, so each extra
# pass costs well under a second on a desktop CPU.
DEFAULT_SCALES: tuple[float, ...] = (1.0, 2.0, 3.0)
# Labels from different passes within this fraction of the image diagonal
# are treated as the same label.
MERGE_DISTANCE = 0.03

_ENGINES: dict[int, object] = {}


def make_engine(threads: int | None = None):
    """RapidOCR engine, cached per process. ``threads`` caps ONNX Runtime's
    intra-op threads (None: let ONNX Runtime use all cores)."""
    key = threads or 0
    if key not in _ENGINES:
        from rapidocr_onnxruntime import RapidOCR

        kwargs = {}
        if threads:
            kwargs = {"intra_op_num_threads": threads, "inter_op_num_threads": 1}
        _ENGINES[key] = RapidOCR(**kwargs)
    return _ENGINES[key]


def load_image(image_path: str | Path):
    """Read an image with OpenCV; np.fromfile also handles non-ASCII Windows paths."""
    import cv2
    import numpy as np

    data = np.fromfile(str(image_path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if img is None:
        raise ExtractError(f"cannot read image {image_path}")
    return img


def _labels_from_ocr(result, scale: float) -> list[Label]:
    """cm labels from one OCR pass, in original-image coordinates."""
    labels: list[Label] = []
    orphans = []
    for box, text, score in result or []:
        x, y, w, h = (v / scale for v in _box_geometry(box))
        m = _CM_RE.search(text)
        if m:
            cm = float(m.group(1).replace(",", "."))
            inch_m = _INCH_RE.search(text[m.end() :])
            inch = normalise_inch(inch_m.group(1), cm) if inch_m else ""
            labels.append(Label(text, cm, inch, x, y, h > VERTICAL_ASPECT * w, float(score)))
        elif _INCH_RE.search(text):
            orphans.append((text, x, y, max(w, h)))

    # Inch text in its own box (two-line labels) goes to the nearest cm label.
    for text, x, y, size in orphans:
        free = [lab for lab in labels if not lab.inch]
        if not free:
            break
        near = min(free, key=lambda lab: (lab.x - x) ** 2 + (lab.y - y) ** 2)
        if ((near.x - x) ** 2 + (near.y - y) ** 2) ** 0.5 <= size:
            near.inch = normalise_inch(_INCH_RE.search(text).group(1), near.cm)
            near.text = f"{near.text} {text}"
    return labels


def merge_passes(passes: list[list[Label]], diagonal: float) -> list[Label]:
    """Combine labels from several OCR passes.

    Labels at the same place form a cluster. The cm value is decided by
    majority vote (ties: higher summed confidence), the inch text is the
    most frequent readable one among the winners.
    """
    limit = MERGE_DISTANCE * diagonal
    clusters: list[list[Label]] = []
    for labels in passes:
        for lab in labels:
            for cluster in clusters:
                ref = cluster[0]
                if ref.vertical == lab.vertical and (
                    (ref.x - lab.x) ** 2 + (ref.y - lab.y) ** 2
                ) ** 0.5 <= limit:
                    cluster.append(lab)
                    break
            else:
                clusters.append([lab])

    merged = []
    for cluster in clusters:
        votes: dict[float, list[Label]] = {}
        for lab in cluster:
            votes.setdefault(lab.cm, []).append(lab)
        winners = max(votes.values(), key=lambda ls: (len(ls), sum(lab.score for lab in ls)))
        best = max(winners, key=lambda lab: lab.score)
        inches = [lab.inch for lab in winners if lab.inch]
        inch = max(set(inches), key=inches.count) if inches else ""
        text = next((lab.text for lab in winners if lab.inch == inch), best.text)
        merged.append(Label(text, best.cm, inch, best.x, best.y, best.vertical, best.score))
    return merged


def read_labels(
    image_path: str | Path,
    engine=None,
    scales: tuple[float, ...] = DEFAULT_SCALES,
) -> list[Label]:
    """OCR the drawing at each scale and return the merged cm labels."""
    import cv2

    engine = engine or make_engine()
    img = load_image(image_path)
    passes = []
    for scale in scales:
        scaled = img if scale == 1 else cv2.resize(
            img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
        )
        result, _ = engine(scaled)
        passes.append(_labels_from_ocr(result, scale))
    h, w = img.shape[:2]
    return merge_passes(passes, (h * h + w * w) ** 0.5)


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
    scales: tuple[float, ...] = DEFAULT_SCALES,
) -> dict:
    """Read ``image_path`` locally and return an unvalidated spec."""
    image_path = Path(image_path)
    if not image_path.is_file():
        raise ExtractError(f"image not found: {image_path}")
    labels = read_labels(image_path, engine, scales)
    if not labels:
        raise ExtractError("no 'NN cm' labels found in the drawing")
    return to_spec(labels, furniture_type, name or image_path.stem, str(image_path))
