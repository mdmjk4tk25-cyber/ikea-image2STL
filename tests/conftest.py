import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = ROOT / "specs"
DRAWING_DIR = ROOT / "fixtures" / "drawings"
FIXTURES = ["schrank", "couchtisch", "bank"]
TYPES = {"schrank": "cabinet", "couchtisch": "coffee_table", "bank": "bench"}


def load(name: str) -> dict:
    return json.loads((SPEC_DIR / f"{name}.json").read_text())
