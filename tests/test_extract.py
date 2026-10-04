import json
from types import SimpleNamespace

from conftest import DRAWING_DIR, load

from furniture_cli import cli
from furniture_cli.extract import build_prompt, extract, response_schema
from furniture_cli.spec import validate_spec


def answer_from_spec(spec: dict) -> dict:
    """What a perfect model answer for a fixture looks like."""
    return {
        "furniture_type": spec["furniture_type"],
        "overall": spec["overall"],
        "params": [
            {"name": k, "value": v, "source": "assumed"} for k, v in spec["params"].items()
        ],
        "measurements": spec["measurements"],
        "assumptions": ["a"],
        "ambiguities": [],
    }


class FakeClient:
    def __init__(self, answer: dict, stop_reason: str = "end_turn"):
        self.answer, self.stop_reason, self.calls = answer, stop_reason, []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(type="text", text=json.dumps(self.answer))
        return SimpleNamespace(stop_reason=self.stop_reason, content=[block])


def _walk_objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for v in schema.values():
            yield from _walk_objects(v)


def test_schema_is_structured_output_compatible():
    for obj in _walk_objects(response_schema()):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_prompt_lists_every_parameter():
    prompt = build_prompt()
    assert "shelf_clearance_below_top" in prompt and "seat_depth" in prompt


def test_extract_with_fake_client_yields_valid_spec():
    reference = load("couchtisch")
    client = FakeClient(answer_from_spec(reference))
    spec = extract(DRAWING_DIR / "couchtisch.png", client=client, model="test-model")
    validate_spec(spec)
    assert spec["params"] == reference["params"]
    req = client.calls[0]
    assert req["model"] == "test-model"
    assert req["messages"][0]["content"][0]["source"]["media_type"] == "image/png"
    assert req["output_config"]["format"]["type"] == "json_schema"


def test_missing_params_get_defaults_and_are_documented():
    answer = answer_from_spec(load("schrank"))
    answer["params"] = [p for p in answer["params"] if p["name"] != "door_gap"]
    spec = extract(DRAWING_DIR / "schrank.png", client=FakeClient(answer))
    assert spec["params"]["door_gap"] == 3
    assert any("door_gap" in a for a in spec["assumptions"])


def test_type_override_is_recorded():
    answer = answer_from_spec(load("bank"))
    answer["furniture_type"] = "cabinet"
    spec = extract(DRAWING_DIR / "bank.png", client=FakeClient(answer), furniture_type="bench")
    assert spec["furniture_type"] == "bench"
    assert any("overridden" in a for a in spec["ambiguities"])


def test_cli_all_runs_offline(tmp_path, monkeypatch, capsys):
    answer = answer_from_spec(load("schrank"))

    def fake_extract(image, **kw):
        return extract(image, client=FakeClient(answer), **kw)

    monkeypatch.setattr("furniture_cli.extract.extract", fake_extract)
    spec_path = tmp_path / "s.json"
    rc = cli.main(
        ["all", str(DRAWING_DIR / "schrank.png"), "--spec", str(spec_path), "--out", str(tmp_path)]
    )
    assert rc == 0, capsys.readouterr().err
    assert json.loads(spec_path.read_text())["furniture_type"] == "cabinet"


def test_cli_extract_keeps_invalid_spec_for_hand_editing(tmp_path, monkeypatch):
    answer = answer_from_spec(load("schrank"))
    answer["overall"]["width"] = 720  # disagrees with the 70 cm measurement

    monkeypatch.setattr(
        "furniture_cli.extract.extract",
        lambda image, **kw: extract(image, client=FakeClient(answer), **kw),
    )
    spec_path = tmp_path / "s.json"
    rc = cli.main(["extract", str(DRAWING_DIR / "schrank.png"), "--spec", str(spec_path)])
    assert rc == 1 and spec_path.exists()
