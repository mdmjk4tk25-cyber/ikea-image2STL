"""Validate stage: check exported files against the spec.

* every part STEP re-imports as exactly one valid, closed solid with volume
* every part STL is watertight (closed, manifold, consistently oriented)
* the assembly bounding box (from STEP and from STL) matches the spec's
  overall width/depth/height within DIM_TOLERANCE_MM
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from build123d import import_step

from .mesh import check_mesh, read_stl
from .spec import validate_spec

DIM_TOLERANCE_MM = 0.5


@dataclass
class ValidationReport:
    name: str
    ok: bool = True
    parts: dict[str, dict] = field(default_factory=dict)
    overall: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def validate(spec: dict, out_root: str | Path) -> ValidationReport:
    validate_spec(spec)
    name = spec["name"]
    out_dir = Path(out_root) / name
    report = ValidationReport(name)

    parts_dir = out_dir / "parts"
    part_names = sorted(p.stem for p in parts_dir.glob("*.step"))
    if not part_names:
        report.fail(f"no parts found in {parts_dir}; run 'furniture build' first")
        return report

    for part in part_names:
        info: dict = {}
        step_file, stl_file = parts_dir / f"{part}.step", parts_dir / f"{part}.stl"
        shape = import_step(step_file)
        solids = shape.solids()
        info["solids"] = len(solids)
        info["valid"] = bool(shape.is_valid)
        info["volume_mm3"] = round(shape.volume, 3)
        info["closed_shells"] = all(sh.is_manifold for s in solids for sh in s.shells())
        if len(solids) != 1:
            report.fail(f"{part}: expected 1 solid, got {len(solids)}")
        if not info["valid"]:
            report.fail(f"{part}: STEP shape is not valid")
        if not info["closed_shells"]:
            report.fail(f"{part}: STEP solid has an open shell")
        if shape.volume <= 0:
            report.fail(f"{part}: non-positive volume")

        if not stl_file.exists():
            report.fail(f"{part}: missing {stl_file.name}")
        else:
            mesh = check_mesh(read_stl(stl_file))
            info["stl"] = {
                "triangles": mesh.triangles,
                "boundary_edges": mesh.boundary_edges,
                "nonmanifold_edges": mesh.nonmanifold_edges,
                "misoriented_edges": mesh.misoriented_edges,
                "watertight": mesh.watertight,
            }
            if not mesh.watertight:
                report.fail(f"{part}: STL is not watertight ({info['stl']})")
        report.parts[part] = info

    expected = spec["overall"]
    sources = {}
    step_file, stl_file = out_dir / f"{name}.step", out_dir / f"{name}.stl"
    if step_file.exists():
        bb = import_step(step_file).bounding_box()
        sources["step"] = (tuple(bb.min), tuple(bb.max))
    else:
        report.fail(f"missing {step_file}")
    if stl_file.exists():
        mesh = check_mesh(read_stl(stl_file))
        sources["stl"] = (mesh.bbox_min, mesh.bbox_max)
    else:
        report.fail(f"missing {stl_file}")

    for source, (lo, hi) in sources.items():
        for axis, key in enumerate(("width", "depth", "height")):
            actual = hi[axis] - lo[axis]
            delta = actual - expected[key]
            report.overall.setdefault(key, {"expected": expected[key]})[source] = round(actual, 4)
            if abs(delta) > DIM_TOLERANCE_MM:
                report.fail(
                    f"{source} {key}: {actual:.3f} mm vs spec {expected[key]} mm "
                    f"(off by {delta:+.3f}, tolerance {DIM_TOLERANCE_MM})"
                )
    return report
