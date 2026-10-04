import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = ROOT / "specs"
DRAWING_DIR = ROOT / "fixtures" / "drawings"
FIXTURES = ["schrank", "couchtisch", "bank"]


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """Tests must never reach the API."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def load(name: str) -> dict:
    return json.loads((SPEC_DIR / f"{name}.json").read_text())
