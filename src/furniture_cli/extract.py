"""Extract stage: dimensioned drawing (PNG/JPEG) -> spec JSON via Claude.

The model returns JSON constrained by a JSON schema (structured outputs).
The result is merged with the per-type parameter defaults, so every value
the drawing does not show is written into the spec explicitly as an
assumption that can be reviewed and corrected by hand before building.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import anthropic

from .spec import FURNITURE_TYPES, SCHEMA_VERSION

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16000

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def response_schema() -> dict:
    """JSON schema for the model's answer (structured-output compatible)."""

    def obj(props: dict, required: list[str] | None = None) -> dict:
        return {
            "type": "object",
            "properties": props,
            "required": required or list(props),
            "additionalProperties": False,
        }

    num, text = {"type": "number"}, {"type": "string"}
    all_params = sorted({p for defs in FURNITURE_TYPES.values() for p in defs})
    return obj(
        {
            "furniture_type": {"type": "string", "enum": sorted(FURNITURE_TYPES)},
            "overall": obj({"width": num, "depth": num, "height": num}),
            "params": {
                "type": "array",
                "items": obj(
                    {
                        "name": {"type": "string", "enum": all_params},
                        "value": num,
                        "source": {"type": "string", "enum": ["drawing", "assumed"]},
                    }
                ),
            },
            "measurements": {
                "type": "array",
                "items": obj({"label": text, "cm": num, "inch": text, "maps_to": text}),
            },
            "assumptions": {"type": "array", "items": text},
            "ambiguities": {"type": "array", "items": text},
        }
    )


def build_prompt(furniture_type: str | None = None) -> str:
    lines = [
        "You read dimensioned IKEA furniture drawings and return a parametric spec.",
        "",
        "Units: every number you return in overall and params is in millimetres "
        "(drawing cm x 10). Measurements keep the drawing's own cm value and the "
        "inch text exactly as printed (e.g. '27 1/2').",
        "Axes: width = left-right extent, depth = front-back extent, height = floor to top.",
        "",
        "For every dimension printed in the drawing add one measurement whose maps_to "
        "is the spec path it defines: 'overall.width', 'overall.depth', "
        "'overall.height' or 'params.<name>'. Do not invent dimensions that are not printed.",
        "",
        "Pick furniture_type and return a value for every parameter of that type. "
        "Use source 'drawing' only if the value is printed in the drawing or follows "
        "arithmetically from printed values; otherwise use source 'assumed' and give a "
        "realistic estimate (the listed default unless the drawing suggests otherwise).",
        "Parameters must make the stated overall dimensions add up.",
        "",
        "List each assumption you made in assumptions and every place where the drawing "
        "can be read more than one way in ambiguities (what the options are and which you chose).",
        "",
        "Furniture types and parameters (name: meaning, default mm):",
    ]
    for ftype, defs in FURNITURE_TYPES.items():
        lines.append(f"- {ftype}:")
        for name, pdef in defs.items():
            kind = ", integer count" if pdef.integer else ""
            lines.append(f"    {name}: {pdef.description} (default {pdef.default:g}{kind})")
    if furniture_type:
        lines += ["", f"The user says this drawing shows a {furniture_type}."]
    return "\n".join(lines)


def to_spec(answer: dict, name: str, source_image: str) -> dict:
    """Turn the model's answer into a spec, filling and annotating defaults."""
    ftype = answer["furniture_type"]
    defs = FURNITURE_TYPES[ftype]
    assumptions = list(answer.get("assumptions", []))
    ambiguities = list(answer.get("ambiguities", []))

    params: dict[str, float] = {}
    for item in answer.get("params", []):
        pname, value = item["name"], item["value"]
        if pname not in defs:
            ambiguities.append(f"Model returned parameter {pname!r}, not used by {ftype}; dropped.")
            continue
        params[pname] = int(value) if defs[pname].integer else value
        if item.get("source") == "assumed":
            assumptions.append(f"params.{pname} = {value:g} mm assumed ({defs[pname].description}).")
    for pname, pdef in defs.items():
        if pname not in params:
            params[pname] = int(pdef.default) if pdef.integer else pdef.default
            assumptions.append(
                f"params.{pname} = {pdef.default:g} default, not returned by the model."
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "furniture_type": ftype,
        "source_image": source_image,
        "units": "mm",
        "overall": answer["overall"],
        "params": params,
        "measurements": answer.get("measurements", []),
        "assumptions": assumptions,
        "ambiguities": ambiguities,
    }


class ExtractError(RuntimeError):
    pass


def extract(
    image_path: str | Path,
    *,
    name: str | None = None,
    furniture_type: str | None = None,
    model: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> dict:
    """Ask Claude for the dimensions in ``image_path``. Returns an unvalidated spec."""
    image_path = Path(image_path)
    media_type = _MEDIA_TYPES.get(image_path.suffix.lower())
    if media_type is None:
        raise ExtractError(f"unsupported image type {image_path.suffix!r}")
    data = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")

    client = client or anthropic.Anthropic()
    response = client.messages.create(
        model=model or os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL,
        max_tokens=MAX_TOKENS,
        system=build_prompt(furniture_type),
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": data},
                    },
                    {"type": "text", "text": "Extract the spec for this drawing."},
                ],
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": response_schema()}},
    )
    if response.stop_reason == "refusal":
        raise ExtractError("the model declined the request")
    if response.stop_reason == "max_tokens":
        raise ExtractError("the model's answer was cut off at max_tokens")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ExtractError("the model returned no text block")
    try:
        answer = json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractError(f"the model returned invalid JSON: {e}") from e

    if furniture_type and answer.get("furniture_type") != furniture_type:
        answer["ambiguities"] = [
            *answer.get("ambiguities", []),
            f"Model classified the drawing as {answer.get('furniture_type')!r}; "
            f"overridden to {furniture_type!r} as requested.",
        ]
        answer["furniture_type"] = furniture_type
    return to_spec(answer, name or image_path.stem, str(image_path))
