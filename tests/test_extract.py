import json

import pytest
from conftest import DRAWING_DIR, FIXTURES, TYPES, load

from furniture_cli import cli
from furniture_cli.extract import (
    ExtractError,
    Label,
    extract,
    merge_passes,
    normalise_inch,
    read_labels,
)
from furniture_cli.spec import validate_spec


@pytest.fixture(scope="module")
def ocr_specs():
    """Run the real local OCR once per drawing."""
    return {
        name: extract(DRAWING_DIR / f"{name}.png", furniture_type=TYPES[name])
        for name in FIXTURES
    }


@pytest.mark.parametrize("name", FIXTURES)
def test_ocr_reads_every_drawing_dimension(name, ocr_specs):
    """Accuracy check: every dimension in the hand-checked spec is found and
    mapped to the same field with the same value."""
    reference = {m["maps_to"]: m["cm"] for m in load(name)["measurements"]}
    extracted = {m["maps_to"]: m["cm"] for m in ocr_specs[name]["measurements"]}
    assert extracted == reference


@pytest.mark.parametrize("name", FIXTURES)
def test_ocr_spec_is_valid_and_matches_reference(name, ocr_specs):
    spec = ocr_specs[name]
    validate_spec(spec)
    reference = load(name)
    assert spec["overall"] == reference["overall"]
    assert spec["params"] == reference["params"]


@pytest.mark.parametrize(
    "raw,cm,expected",
    [
        ('27 1/2"', 70, "27 1/2"),
        ('133/4"', 35, "13 3/4"),  # missing space
        ('21 5/s"', 55, "21 5/8"),  # s read for 8
        ('40 12"', 103, "40 1/2"),  # ½ read as 12
        ('44 %2"', 113, "44 1/2"),
        ('16 1%"', 41, ""),  # unreadable fraction
        ('72 1/2"', 70, ""),  # disagrees with cm
    ],
)
def test_normalise_inch(raw, cm, expected):
    assert normalise_inch(raw, cm) == expected


def _blank_png(path):
    import cv2
    import numpy as np

    cv2.imwrite(str(path), np.full((800, 1400, 3), 255, dtype=np.uint8))
    return path


def _engine(*items):
    """Fake OCR engine: items are (text, x, y, w, h)."""

    def run(_path):
        result = []
        for text, x, y, w, h in items:
            box = [[x - w / 2, y - h / 2], [x + w / 2, y - h / 2], [x + w / 2, y + h / 2], [x - w / 2, y + h / 2]]
            result.append((box, text, 0.9))
        return result, None

    return run


def test_split_inch_label_is_attached_to_nearest_cm_label(tmp_path):
    engine = _engine(("19 cm", 100, 100, 40, 130), ('(7 1/2")', 140, 100, 45, 130))
    (label,) = read_labels(_blank_png(tmp_path / "x.png"), engine, scales=(1.0,))
    assert label.cm == 19 and label.inch == "7 1/2" and label.vertical


def test_coffee_table_shelf_chain_uses_position(tmp_path):
    img = _blank_png(tmp_path / "t.png")
    engine = _engine(
        ("90 cm", 400, 100, 300, 50),
        ("55 cm", 900, 100, 300, 50),
        ("45 cm", 50, 500, 40, 300),
        ("20 cm", 1300, 300, 40, 130),  # upper: tabletop underside to shelf
        ("19 cm", 1300, 600, 40, 130),  # lower: floor to shelf
    )
    spec = extract(img, furniture_type="coffee_table", engine=engine, scales=(1.0,))
    assert spec["params"]["shelf_clearance_below_top"] == 200
    assert spec["params"]["shelf_height"] == 190
    assert spec["params"]["top_thickness"] == 50  # 450 - 200 - 190 - 10
    validate_spec(spec)


def test_missing_dimension_is_reported(tmp_path):
    img = _blank_png(tmp_path / "c.png")
    engine = _engine(("70 cm", 400, 50, 300, 40), ("70 cm", 50, 400, 40, 300))
    spec = extract(img, furniture_type="cabinet", engine=engine, scales=(1.0,))
    assert spec["overall"]["depth"] == 0
    assert any("overall.depth not found" in a for a in spec["ambiguities"])


def test_no_labels_is_an_error(tmp_path):
    img = _blank_png(tmp_path / "e.png")
    with pytest.raises(ExtractError):
        extract(img, furniture_type="bench", engine=_engine(("hello", 1, 1, 10, 10)), scales=(1.0,))


def test_cli_all_runs_locally(tmp_path, capsys):
    spec_path = tmp_path / "s.json"
    rc = cli.main(
        [
            "all",
            str(DRAWING_DIR / "schrank.png"),
            "--type",
            "cabinet",
            "--spec",
            str(spec_path),
            "--out",
            str(tmp_path),
        ]
    )
    assert rc == 0, capsys.readouterr().err
    assert json.loads(spec_path.read_text())["overall"]["width"] == 700


def test_cli_extract_keeps_invalid_spec_for_hand_editing(tmp_path, monkeypatch):
    engine = _engine(("70 cm", 400, 50, 300, 40))  # width only
    monkeypatch.setattr("furniture_cli.extract.make_engine", lambda threads=None: engine)
    spec_path = tmp_path / "s.json"
    rc = cli.main(
        [
            "extract", str(DRAWING_DIR / "schrank.png"), "--type", "cabinet",
            "--spec", str(spec_path), "--ocr-scales", "1",
        ]
    )
    assert rc == 1 and spec_path.exists()


def _label(cm, x, inch="", score=0.9):
    return Label(f"{cm:g} cm", cm, inch, x, 100, False, score)


def test_merge_passes_votes_on_cm_and_fills_inch():
    passes = [
        [_label(103, 500), _label(58, 900)],
        [_label(403, 502, score=0.95), _label(58, 901, inch="22 7/8")],  # misread
        [_label(103, 499, inch="40 1/2")],
    ]
    merged = sorted(merge_passes(passes, diagonal=2000), key=lambda lab: lab.x)
    assert [(lab.cm, lab.inch) for lab in merged] == [(103, "40 1/2"), (58, "22 7/8")]


def test_spec_option_is_single_image_only(tmp_path):
    images = [str(DRAWING_DIR / "schrank.png")] * 2
    rc = cli.main(["extract", *images, "--type", "cabinet", "--spec", str(tmp_path / "x.json")])
    assert rc == 2


def test_extract_many_images_in_parallel(tmp_path, monkeypatch):
    import shutil

    for n in ("a", "b"):
        shutil.copy(DRAWING_DIR / "schrank.png", tmp_path / f"{n}.png")
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["all", "a.png", "b.png", "--type", "cabinet", "--ocr-scales", "1", "-j", "2"])
    assert rc == 0
    for n in ("a", "b"):
        assert json.loads((tmp_path / "specs" / f"{n}.json").read_text())["overall"]["width"] == 700
        assert (tmp_path / "out" / n / "validation.json").exists()


def test_parallel_build_and_validate(tmp_path, capsys):
    specs = [f"specs/{n}.json" for n in FIXTURES]
    assert cli.main(["build", *specs, "--out", str(tmp_path), "-j", "3"]) == 0
    assert cli.main(["validate", *specs, "--out", str(tmp_path), "-j", "3"]) == 0
    out = capsys.readouterr().out
    assert all(f"OK: {n}" in out for n in FIXTURES)
