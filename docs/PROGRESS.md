# Progress log

Status as of 2026-10-04. Project paused; this file records what was done and how to resume.

## Goal

Python CLI (uv, Python 3.12, build123d) that turns dimensioned IKEA drawings (PNG, cm + inch) into parametric 3D models exported as STEP and STL:

1. `extract`: Claude reads dimensions from the image into a JSON spec that can be checked and corrected by hand.
2. `build`: one build123d template per furniture type (cabinet, coffee table, bench), no hard-coded dimensions.
3. `validate`: every part watertight, overall dimensions match the spec within 0.5 mm.
4. README documents all assumptions and drawing ambiguities.

## State

| Item | State |
|---|---|
| Code | Complete for all four stages, on branch `claude/furniture-cli-initial-vroskm` |
| PR | [#1](https://github.com/mdmjk4tk25-cyber/ikea-image2STL/pull/1), draft, base `main`, assigned to mdmjk4tk25-cyber |
| `main` | Only an empty "Initial commit" (created so the PR has a base) |
| Tests | 41 passing locally (`uv run pytest`), all offline |
| End-to-end | `build` + `validate` pass for all three reference specs |
| `extract` live | **Not run.** No `ANTHROPIC_API_KEY` was available in the session |
| CI | None configured in the repo |

Verified dimensions (STEP and STL bounding box, exact match):

| Spec | Type | W × D × H (mm) | Parts |
|---|---|---|---|
| `specs/schrank.json` | cabinet | 700 × 350 × 700 | 2 sides, top, bottom, back, 2 doors |
| `specs/couchtisch.json` | coffee_table | 900 × 550 × 450 | top, 4 legs, shelf |
| `specs/bank.json` | bench | 1130 × 580 × 840 | 2 side frames, seat, backrest |

Every part: 1 valid solid, closed shells, watertight STL.

## What was done

1. Scaffolded the repo from empty: `pyproject.toml` (hatchling, deps `build123d`, `anthropic`; dev `pytest`), `uv.lock`, `.python-version` 3.12.
2. Copied the three reference drawings to `fixtures/drawings/` and wrote hand-checked specs in `specs/`.
3. Implemented `src/furniture_cli/`:
   - `spec.py`: parameter definitions per type with defaults, validation (structure, cm vs inch within 8 mm, each measurement equals its mapped spec value within 0.5 mm, per-type feasibility).
   - `templates.py`: cabinet, coffee_table, bench.
   - `build.py`: per-part and assembly STEP/STL export to `out/<name>/`; clears stale part files.
   - `mesh.py`: stdlib STL reader and watertightness check (boundary, non-manifold, misoriented edges).
   - `validate.py`: re-imports STEP, checks solids and shells, checks STL, compares bounding boxes; writes `validation.json`.
   - `extract.py`: Messages API with image input and structured outputs (JSON schema); fills missing params with defaults and records them in `assumptions`.
   - `cli.py`: `furniture extract|build|validate|all`.
4. Wrote the README (German) with every assumption and ambiguity per drawing.

## Decisions

- **Anthropic SDK instead of stdlib HTTP** for `extract`: one dependency, gives retries and typed errors.
- **Model**: `--model`, else `$CLAUDE_MODEL`, else `claude-sonnet-5-5`.
- **No refusal fallback** configured; a refusal stops `extract` with an error.
- **Defaults live in the spec layer**, not the templates. `extract` writes them into the spec explicitly so they can be reviewed.
- **Invalid extracted specs are still written** to disk (exit code 1) so they can be fixed by hand.
- **Empty root commit** pushed as `main` (user approved via decision card) so PR #1 could be opened.

## Open assumptions to review

Full list in `README.md` and in each spec's `assumptions` / `ambiguities`. The ones most likely to matter:

- Bench: 41 cm read as seat depth; could be armrest length.
- Bench: armrest height 620 mm, stretcher 150 mm, members 40 mm are estimates; rattan curves flattened to slabs.
- Coffee table: shelf 10 mm assumed, so top = 50 mm (450 − 190 − 10 − 200).
- Cabinet: panels 18 mm, back 3 mm, door gap 3 mm; doors inset; no shelves.

## Blockers hit

| Blocker | Resolution |
|---|---|
| `git push` returned 403 (Claude GitHub App not linked) | User connected the repo; push succeeded |
| Repo empty, no base branch for a PR | Created `main` from the empty root commit with user approval |
| No `ANTHROPIC_API_KEY` in session | Open: `extract` untested against the live API |

Environment finding: `cadquery-ocp-novtk` links `libGL.so.1` directly (checked with `ldd`), so the libGL block in the setup script is required.

## Next steps

1. Run `extract` live on the three drawings with a real key and diff against the hand-checked specs:
   ```bash
   uv run furniture extract fixtures/drawings/couchtisch.png --spec /tmp/couchtisch.json
   diff <(jq -S . specs/couchtisch.json) <(jq -S . /tmp/couchtisch.json)
   ```
2. Decide the open assumptions above (especially bench 41 cm) and update specs/README if needed.
3. Optional: add a GitHub Actions workflow (`uv sync`, `apt-get install libgl1`, `uv run pytest`).
4. Review and merge PR #1.
