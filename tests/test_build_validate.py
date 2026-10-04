import pytest
from conftest import FIXTURES, load

from furniture_cli.build import build, build_parts
from furniture_cli.cli import main
from furniture_cli.spec import validate_spec
from furniture_cli.validate import validate


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_builds_and_validates(name, tmp_path):
    spec = load(name)
    result = build(spec, tmp_path)
    assert result.step.exists() and result.stl.exists()
    report = validate(spec, tmp_path)
    assert report.ok, report.errors
    assert all(p["stl"]["watertight"] and p["solids"] == 1 for p in report.parts.values())


def _bbox_size(parts):
    from build123d import Compound

    bb = Compound(children=list(parts.values())).bounding_box()
    return bb.size.X, bb.size.Y, bb.size.Z


@pytest.mark.parametrize("name", FIXTURES)
def test_templates_follow_spec_dimensions(name):
    """No hard-coded sizes: scaling the overall dimensions moves the geometry."""
    spec = load(name)
    spec["measurements"] = []  # measurements pin the drawing values
    o = spec["overall"]
    o["width"] += 123
    o["depth"] += 45
    o["height"] += 67
    if spec["furniture_type"] == "coffee_table":
        spec["params"]["shelf_height"] += 67
    if spec["furniture_type"] == "bench":
        spec["params"]["seat_width"] += 123
    validate_spec(spec)
    assert _bbox_size(build_parts(spec)) == pytest.approx((o["width"], o["depth"], o["height"]))


def test_cabinet_shelves_and_doors_are_parametric():
    spec = load("schrank")
    spec["params"].update(door_count=3, shelf_count=2)
    parts = build_parts(spec)
    assert {"door_3", "shelf_1", "shelf_2"} <= set(parts)


def test_validate_detects_dimension_mismatch(tmp_path):
    spec = load("couchtisch")
    build(spec, tmp_path)
    spec["measurements"] = []
    spec["overall"]["width"] += 1  # 1 mm > 0.5 mm tolerance
    report = validate(spec, tmp_path)
    assert not report.ok
    assert any("width" in e for e in report.errors)


def test_validate_without_build_fails(tmp_path):
    report = validate(load("schrank"), tmp_path)
    assert not report.ok and "run 'furniture build'" in report.errors[0]


def test_rebuild_removes_stale_parts(tmp_path):
    spec = load("schrank")
    spec["params"]["shelf_count"] = 2
    build(spec, tmp_path)
    spec["params"]["shelf_count"] = 0
    build(spec, tmp_path)
    assert validate(spec, tmp_path).ok
    assert not list((tmp_path / "schrank" / "parts").glob("shelf_*"))


def test_cli_build_and_validate(tmp_path, capsys):
    spec = "specs/bank.json"
    assert main(["build", spec, "--out", str(tmp_path)]) == 0
    assert main(["validate", spec, "--out", str(tmp_path)]) == 0
    assert "OK: bank" in capsys.readouterr().out
    assert (tmp_path / "bank" / "validation.json").exists()


def test_cli_rejects_invalid_spec(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": 1}')
    assert main(["build", str(bad), "--out", str(tmp_path)]) == 1
    assert "invalid spec" in capsys.readouterr().err
