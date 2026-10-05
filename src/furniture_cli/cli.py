"""Command line: furniture {extract,build,validate,all}.

Every command accepts several inputs and processes them in parallel worker
processes (``--jobs``, default: one per logical CPU, capped at the number of
inputs). OCR workers split the CPU threads between them so ONNX Runtime does
not oversubscribe the cores.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .spec import FURNITURE_TYPES, SpecError, load_spec, validate_spec

DEFAULT_OUT = "out"
DEFAULT_SPEC_DIR = "specs"


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _cpu_count() -> int:
    return os.cpu_count() or 1


def _run(func, items: list, jobs: int | None) -> list:
    """Map ``func`` over ``items``; in worker processes when it pays off.

    Results come back in input order. ``func`` returns (exit_code, stdout, stderr).
    """
    jobs = max(1, min(jobs or _cpu_count(), len(items)))
    if jobs == 1:
        return [func(item) for item in items]
    # "spawn" everywhere: it is the only start method on Windows, and forking
    # a process that already runs ONNX Runtime / OCCT threads can deadlock.
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=jobs, mp_context=ctx) as pool:
        return list(pool.map(func, items))


def _report(results: list) -> int:
    rc = 0
    for code, out, err in results:
        if out:
            print(out)
        if err:
            _err(err)
        rc = max(rc, code)
    return rc


# --- per-item workers (top level so they pickle on Windows' spawn start) ----


def _extract_one(job: tuple) -> tuple[int, str, str]:
    image, spec_path, name, ftype, scales, threads = job
    from .extract import ExtractError, extract, make_engine

    image = Path(image)
    if not image.is_file():
        return 2, "", f"image not found: {image}"
    try:
        spec = extract(
            image, name=name, furniture_type=ftype, engine=make_engine(threads), scales=scales
        )
    except ExtractError as e:
        return 1, "", f"{image}: extract failed: {e}"
    # Write even an invalid spec so it can be corrected by hand.
    spec_path = Path(spec_path)
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        validate_spec(spec)
    except SpecError as e:
        return 1, f"wrote {spec_path}", f"{spec_path} needs manual correction before build:\n{e}"
    return 0, f"wrote {spec_path}", ""


def _build_one(job: tuple) -> tuple[int, str, str]:
    spec_path, out = job
    from .build import build

    try:
        spec = load_spec(spec_path)
    except (SpecError, FileNotFoundError) as e:
        return 1, "", f"{spec_path}: {e}"
    result = build(spec, out)
    return 0, "\n".join(
        [
            f"wrote {result.step}",
            f"wrote {result.stl}",
            f"wrote {len(result.part_files)} parts to {result.out_dir / 'parts'}",
        ]
    ), ""


def _validate_one(job: tuple) -> tuple[int, str, str]:
    spec_path, out = job
    from .validate import validate

    try:
        spec = load_spec(spec_path)
    except (SpecError, FileNotFoundError) as e:
        return 1, "", f"{spec_path}: {e}"
    report = validate(spec, out)
    report_path = Path(out) / spec["name"] / "validation.json"
    if report_path.parent.is_dir():
        report_path.write_text(report.to_json() + "\n", encoding="utf-8")
    lines = []
    for part, info in report.parts.items():
        tight = info.get("stl", {}).get("watertight")
        lines.append(f"  {part:<20} solids={info['solids']} valid={info['valid']} watertight={tight}")
    for key, vals in report.overall.items():
        sources = " ".join(f"{src}={v}" for src, v in vals.items() if src != "expected")
        lines.append(f"  {key:<20} spec={vals['expected']} {sources}")
    if report.ok:
        lines.append(f"OK: {spec['name']} passes all checks")
        return 0, "\n".join(lines), ""
    return 1, "\n".join(lines), "\n".join(f"{spec['name']}: {e}" for e in report.errors)


# --- commands ---------------------------------------------------------------


def _spec_paths(args: argparse.Namespace) -> list[str] | None:
    if (args.spec or args.name) and len(args.images) > 1:
        _err("--spec and --name only work with a single image")
        return None
    return [
        args.spec or str(Path(DEFAULT_SPEC_DIR) / f"{args.name or Path(img).stem}.json")
        for img in args.images
    ]


def cmd_extract(args: argparse.Namespace) -> int:
    spec_paths = _spec_paths(args)
    if spec_paths is None:
        return 2
    jobs = max(1, min(args.jobs or _cpu_count(), len(args.images)))
    threads = max(1, _cpu_count() // jobs) if jobs > 1 else None
    work = [
        (img, spec, args.name, args.type, tuple(args.ocr_scales), threads)
        for img, spec in zip(args.images, spec_paths)
    ]
    rc = _report(_run(_extract_one, work, jobs))
    args.specs = spec_paths
    return rc


def cmd_build(args: argparse.Namespace) -> int:
    return _report(_run(_build_one, [(s, args.out) for s in args.specs], args.jobs))


def cmd_validate(args: argparse.Namespace) -> int:
    return _report(_run(_validate_one, [(s, args.out) for s in args.specs], args.jobs))


def cmd_all(args: argparse.Namespace) -> int:
    rc = cmd_extract(args)
    if rc:
        return rc
    return cmd_build(args) or cmd_validate(args)


def _scales(text: str) -> list[float]:
    try:
        values = [float(v) for v in text.split(",") if v.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("expected comma-separated numbers, e.g. 1,2,3")
    if not values or any(v <= 0 for v in values):
        raise argparse.ArgumentTypeError("scales must be positive")
    return values


def make_parser() -> argparse.ArgumentParser:
    from .extract import DEFAULT_SCALES

    parser = argparse.ArgumentParser(
        prog="furniture",
        description="Dimensioned IKEA drawing -> JSON spec -> STEP/STL.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--jobs", "-j", type=int, default=None,
            help="parallel worker processes (default: one per logical CPU)",
        )

    def extract_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("images", nargs="+", help="drawing(s) (PNG/JPEG)")
        p.add_argument("--spec", "-s", dest="spec", help="spec path, single image only")
        p.add_argument("--name", help="spec name, single image only (default: file stem)")
        p.add_argument(
            "--type", required=True, choices=sorted(FURNITURE_TYPES), help="furniture type"
        )
        p.add_argument(
            "--ocr-scales", type=_scales, default=list(DEFAULT_SCALES),
            help="OCR passes at these upscale factors, merged by vote "
            f"(default: {','.join(f'{s:g}' for s in DEFAULT_SCALES)}; use 1 for the fastest run)",
        )
        common(p)

    p = sub.add_parser("extract", help="read dimensions from drawings into specs (local OCR)")
    extract_args(p)
    p.set_defaults(func=cmd_extract)

    for name, func, help_ in (
        ("build", cmd_build, "build STEP/STL from specs"),
        ("validate", cmd_validate, "check watertightness and overall dimensions"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("specs", nargs="+", help="spec JSON file(s)")
        p.add_argument("--out", "-o", default=DEFAULT_OUT, help="output root (default: out)")
        common(p)
        p.set_defaults(func=func)

    p = sub.add_parser("all", help="extract, build and validate in one go")
    extract_args(p)
    p.add_argument("--out", "-o", default=DEFAULT_OUT, help="output root (default: out)")
    p.set_defaults(func=cmd_all)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        return args.func(args)
    except SpecError as e:
        _err(str(e))
        return 1
    except FileNotFoundError as e:
        _err(str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
