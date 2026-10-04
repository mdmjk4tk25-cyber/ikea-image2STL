import copy

import pytest
from conftest import FIXTURES, load

from furniture_cli.spec import FURNITURE_TYPES, SpecError, parse_inch, validate_spec


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_specs_are_valid(name):
    validate_spec(load(name))


@pytest.mark.parametrize(
    "text,value",
    [("27 1/2", 27.5), ('13 ¾"', 13.75), ("7 ⅞", 7.875), ("44.5", 44.5), ("3/8", 0.375), ("40", 40)],
)
def test_parse_inch(text, value):
    assert parse_inch(text) == pytest.approx(value)


def _errors(spec):
    with pytest.raises(SpecError) as exc:
        validate_spec(spec)
    return "\n".join(exc.value.errors)


def test_cm_inch_disagreement_is_flagged():
    spec = load("schrank")
    spec["measurements"][0]["inch"] = "72 1/2"
    assert "disagree" in _errors(spec)


def test_measurement_must_match_mapped_value():
    spec = load("schrank")
    spec["overall"]["width"] = 720
    assert "drawing says 700" in _errors(spec)


def test_missing_and_unknown_params():
    spec = load("couchtisch")
    del spec["params"]["leg_size"]
    spec["params"]["colour"] = 3
    errs = _errors(spec)
    assert "params.leg_size is missing" in errs and "colour" in errs


def test_coffee_table_stack_must_add_up():
    spec = load("couchtisch")
    spec["params"]["top_thickness"] = 60
    assert "expected height 450" in _errors(spec)


def test_bench_infeasible_armrest():
    spec = load("bank")
    spec["params"]["armrest_height"] = 400
    assert "armrest" in _errors(spec)


def test_unknown_type_and_bad_units():
    spec = copy.deepcopy(load("schrank"))
    spec["furniture_type"] = "sofa"
    spec["units"] = "cm"
    errs = _errors(spec)
    assert "furniture_type" in errs and "units" in errs


def test_door_count_must_be_integer():
    spec = load("schrank")
    spec["params"]["door_count"] = 1.5
    assert "integer" in _errors(spec)


def test_every_type_has_defaults_for_all_params():
    for defs in FURNITURE_TYPES.values():
        assert all(p.default >= 0 for p in defs.values())
