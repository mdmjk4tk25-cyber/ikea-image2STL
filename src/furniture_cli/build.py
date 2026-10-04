"""Build stage: spec -> build123d parts -> STEP and STL files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from build123d import Compound, export_step, export_stl

from .spec import validate_spec
from .templates import TEMPLATES, Parts

# Chordal tolerance for STL tessellation (mm). All current templates are
# planar, so this only matters for future curved parts.
STL_TOLERANCE = 0.01
STL_ANGULAR_TOLERANCE = 0.1


@dataclass
class BuildResult:
    out_dir: Path
    step: Path
    stl: Path
    part_files: dict[str, dict[str, Path]] = field(default_factory=dict)


def build_parts(spec: dict) -> Parts:
    validate_spec(spec)
    return TEMPLATES[spec["furniture_type"]](spec)


def assemble(parts: Parts, name: str) -> Compound:
    children = []
    for part_name, shape in parts.items():
        shape.label = part_name
        children.append(shape)
    return Compound(children=children, label=name)


def build(spec: dict, out_root: str | Path) -> BuildResult:
    """Write <out_root>/<name>/{<name>.step, <name>.stl, parts/<part>.{step,stl}}."""
    parts = build_parts(spec)
    name = spec["name"]
    out_dir = Path(out_root) / name
    parts_dir = out_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    # Drop files from a previous build so validate never sees stale parts.
    for old in [*parts_dir.glob("*.step"), *parts_dir.glob("*.stl")]:
        old.unlink()

    result = BuildResult(out_dir, out_dir / f"{name}.step", out_dir / f"{name}.stl")
    for part_name, shape in parts.items():
        step_path = parts_dir / f"{part_name}.step"
        stl_path = parts_dir / f"{part_name}.stl"
        _export(shape, step_path, stl_path)
        result.part_files[part_name] = {"step": step_path, "stl": stl_path}

    _export(assemble(parts, name), result.step, result.stl)
    return result


def _export(shape, step_path: Path, stl_path: Path) -> None:
    if not export_step(shape, step_path):
        raise RuntimeError(f"STEP export failed: {step_path}")
    if not export_stl(
        shape, stl_path, tolerance=STL_TOLERANCE, angular_tolerance=STL_ANGULAR_TOLERANCE
    ):
        raise RuntimeError(f"STL export failed: {stl_path}")
