# Progress log

Status as of 2026-10-05. This file records what was done and how to resume.

## Goal

Python CLI (uv, Python 3.12, build123d) that turns dimensioned IKEA drawings (PNG, cm + inch) into parametric 3D models exported as STEP and STL:

1. `extract`: local OCR reads dimensions from the image into a JSON spec that can be checked and corrected by hand. No external API calls (changed 2026-10-05, see below).
2. `build`: one build123d template per furniture type (cabinet, coffee table, bench), no hard-coded dimensions.
3. `validate`: every part watertight, overall dimensions match the spec within 0.5 mm.
4. README documents all assumptions and drawing ambiguities.

## State

| Item | State |
|---|---|
| Code | All four stages on `main` (PR #1, merged 2026-10-04); local-only extract on branch `claude/furniture-cli-initial-vroskm` (PR #2) |
| Tests | 57 passing (`uv run pytest`), also with networking disabled (`unshare -n`) |
| End-to-end | `build` + `validate` pass for all three reference specs |
| `extract` | Runs locally on all three drawings: 14/14 cm values read and mapped correctly, 10/14 inch values readable |
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
   - `extract.py`: originally the Claude Messages API; replaced by local OCR on 2026-10-05.
   - `cli.py`: `furniture extract|build|validate|all`.
4. Wrote the README (German) with every assumption and ambiguity per drawing.

## 2026-10-05: no external API calls

Sebastian asked that everything runs locally. Changes:

- Removed the `anthropic` dependency and the Messages API call. Added `rapidocr-onnxruntime` (ONNX OCR models ship inside the wheel; nothing is downloaded at runtime).
- New `extract.py`: OCR finds `NN cm` labels with position and orientation; one mapping rule per furniture type assigns them to spec fields (value ranking, vertical vs horizontal text, vertical position for the coffee-table shelf chain). Undimensioned params get defaults, recorded in `assumptions`.
- `--type` is now required for `extract` and `all`; `--model` is gone.
- OCR inch text is normalised (½ read as `12`/`%2`, missing spaces, `s` for 8) and used only as a cm cross-check.
- Tesseract 5.3 was tried first and read only 4/14 cm values correctly. A local vision-language model (Ollama) was rejected: multi-GB, slow on CPU, nondeterministic, and OCR already reads every value.
- Limitation: the mapping rules fit these IKEA layouts; other layouts may need rule changes or hand correction of the spec.

## 2026-10-05: tuning for the target machine

Target: Framework Desktop, Ryzen AI Max+ 395 (16C/32T), Radeon 8060S, 128 GB, Windows x64.

- Verified `uv.lock` has `win_amd64` cp312 wheels for OCP, onnxruntime, opencv, numpy.
- All commands take multiple inputs and run them in parallel (`--jobs`, default one per logical CPU). Pool uses `spawn` (Windows-compatible; `fork` deadlocked next to ONNX Runtime/OCCT threads in a test run). OCR workers get `cpu_count / jobs` ONNX threads.
- Multi-scale OCR (1x, 2x, 3x, majority vote) is the default: inch 10/14 instead of 9/14, about 3x OCR time. `--ocr-scales 1` for speed.
- Images read via `np.fromfile` + `cv2.imdecode` for non-ASCII Windows paths.
- Not done: DirectML GPU and NPU (untestable here; `onnxruntime-directml` conflicts with `onnxruntime`; little gain for small models). Nothing was run on the target machine.

## Decisions (initial build, 2026-10-04)

- ~~Anthropic SDK for `extract`~~ replaced by local OCR on 2026-10-05.
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
| No `ANTHROPIC_API_KEY` in session | Moot: extract is local since 2026-10-05 |

Environment finding: `cadquery-ocp-novtk` links `libGL.so.1` directly (checked with `ldd`), so the libGL block in the setup script is required.

## Next steps

1. Review and merge PR #2 (local extract).
2. Decide the open assumptions above (especially bench 41 cm) and update specs/README if needed.
3. Try `furniture extract` on further IKEA drawings; extend the mapping rules where the layout differs.
4. Run the test suite and a timing of `furniture extract` with `-j 1` vs default on the Framework Desktop (Windows).
5. Optional: add a GitHub Actions workflow (`uv sync`, `apt-get install libgl1`, `uv run pytest`).
