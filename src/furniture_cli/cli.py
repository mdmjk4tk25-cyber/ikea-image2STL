"""Command line: furniture {extract,build,validate,all}."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .spec import FURNITURE_TYPES, SpecError, load_spec, validate_spec

DEFAULT_OUT = "out"
DEFAULT_SPEC_DIR = "specs"


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def cmd_extract(args: argparse.Namespace) -> int:
    from .extract import ExtractError, extract

    image = Path(args.image)
    if not image.is_file():
        _err(f"image not found: {image}")
        return 2
    spec_path = Path(args.spec or Path(DEFAULT_SPEC_DIR) / f"{args.name or image.stem}.json")
    try:
        spec = extract(image, name=args.name, furniture_type=args.type)
    except ExtractError as e:
        _err(f"extract failed: {e}")
        return 1
    # Write even an invalid spec so it can be corrected by hand.
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {spec_path}")
    try:
        validate_spec(spec)
    except SpecError as e:
        _err(f"{spec_path} needs manual correction before build:\n{e}")
        return 1
    args.spec = str(spec_path)
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    from .build import build

    spec = load_spec(args.spec)
    result = build(spec, args.out)
    print(f"wrote {result.step}")
    print(f"wrote {result.stl}")
    print(f"wrote {len(result.part_files)} parts to {result.out_dir / 'parts'}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from .validate import validate

    spec = load_spec(args.spec)
    report = validate(spec, args.out)
    report_path = Path(args.out) / spec["name"] / "validation.json"
    if report_path.parent.is_dir():
        report_path.write_text(report.to_json() + "\n", encoding="utf-8")
    for part, info in report.parts.items():
        tight = info.get("stl", {}).get("watertight")
        print(f"  {part:<20} solids={info['solids']} valid={info['valid']} watertight={tight}")
    for key, vals in report.overall.items():
        print(f"  {key:<20} spec={vals['expected']} " + " ".join(
            f"{src}={v}" for src, v in vals.items() if src != "expected"
        ))
    if report.ok:
        print(f"OK: {spec['name']} passes all checks")
        return 0
    for e in report.errors:
        _err(e)
    return 1


def cmd_all(args: argparse.Namespace) -> int:
    rc = cmd_extract(args)
    if rc:
        return rc
    return cmd_build(args) or cmd_validate(args)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="furniture",
        description="Dimensioned IKEA drawing -> JSON spec -> STEP/STL.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def extract_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("image", help="drawing (PNG/JPEG)")
        p.add_argument("--spec", "-s", dest="spec", help="spec path (default specs/<name>.json)")
        p.add_argument("--name", help="spec name (default: image file stem)")
        p.add_argument(
            "--type", required=True, choices=sorted(FURNITURE_TYPES), help="furniture type"
        )

    p = sub.add_parser("extract", help="read dimensions from a drawing into a spec (local OCR)")
    extract_args(p)
    p.set_defaults(func=cmd_extract)

    for name, func, help_ in (
        ("build", cmd_build, "build STEP/STL from a spec"),
        ("validate", cmd_validate, "check watertightness and overall dimensions"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("spec", help="spec JSON")
        p.add_argument("--out", "-o", default=DEFAULT_OUT, help="output root (default: out)")
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
