# Build log

## Baseline — 2026-08-16

Project: Ireland Geometric Pattern Scan

Initial local snapshot before the reliability and dashboard pass:

- `data/combined.json`: 135,173 OSM elements
- `output/analysis_results.csv`: 123,810 analyzed rows
- Controls: 90,394
- Targets: 33,416
- `output/ireland_buildings.geojson`: 33,416 target features
- `output/niah_join.csv`: 9,455 joins
- `output/report.html`: about 5.39 MB
- Source tree had no Git repository, no test suite, and no project manifest.

Baseline checks performed:

- Python `compileall`: passed.
- Cached pipeline: completed successfully.
- Generated report inline JavaScript: parsed successfully with Node.
- `pytest`: unavailable in the existing environment; no tests were present.

Known baseline defects to address:

- `pyosmium` is imported but absent from runtime requirements.
- `run_pipeline.py` ignores most command-line arguments, including `--help`.
- Convexity is calculated as hull area divided by polygon area.
- Multipart geometry handling discards components.
- Statistical outputs round p-values and report raw significance after Holm correction.
- Report narrative is partly hard-coded and the UI has limited filtering/interactivity.
- Architect extraction contains false-positive names.

## Working rules

- Preserve raw downloads and existing generated artifacts while migrating.
- Keep output schemas backward-compatible where practical; add fields rather than silently removing them.
- Record meaningful changes, commands, timings, and verification results here.

## Reliability and analysis pass — 2026-08-16

Implemented and verified:

- Added `pyproject.toml`, runtime `requirements.txt` coverage for `pyosmium`,
  a package marker, deterministic CLI options, project-root path resolution,
  stage selection, `--help`, `--no-network`, seed/Monte Carlo controls, and a
  provenance manifest with source hashes and output counts.
- Added atomic text, bytes, JSON, and CSV writers and routed core pipeline
  artifacts through them so interrupted writes do not replace a valid output
  with a partial file.
- Added the missing `fetch-niah` pipeline stage and a cache-only mode. A
  refresh with `--no-network` now reuses cached archives and only re-extracts.
- Centralized geometry parsing and repair. Multipart exteriors and holes are
  preserved; all parts contribute to metrics; validity, repair, degeneracy,
  multipart, hole-count, and warnings are persisted. Convexity is now
  `area / convex_hull.area`.
- Centralized proportion statistics: Wilson intervals, risk differences,
  odds ratios, full-precision p-values with a positive numerical floor, and
  family-wise Holm correction with adjusted verdicts.
- Reworked NIAH joins and stratification, added source-region provenance,
  era-matched and decade families, rating/type diagnostics, and the
  octagon-ish/golden-core angle-band audit.
- Hardened architect extraction with evidence and confidence output plus
  false-positive regression tests. Point-pattern output now records seed,
  Monte Carlo iterations, and control sample size.
- Rebuilt the report as a data-driven, filterable Leaflet dashboard with
  clustered markers, top outlines, exports, statistical tables, methods, and
  provenance. Added a template-token regression test.
- Added 12 tests covering CLI behavior, geometry correctness/repair,
  statistics/Holm behavior, architect extraction, and report generation.

Final cached verification commands:

```text
.venv/bin/python run_pipeline.py --no-network --seed 20260816 --mc 300
.venv/bin/python -m pytest                 # 12 passed
.venv/bin/ruff check scripts tests run_pipeline.py  # passed
.venv/bin/python -m compileall -q scripts run_pipeline.py  # passed
```

The complete cached build produced 123,810 analysis rows, 33,416 target
features, 90,394 controls, 9,455 NIAH joins, 41 NIAH tests, two point-pattern
rows, 49 architect rows, and the interactive report. An intermediate final
verification exposed an accidental Python-3.10-only `itertools.pairwise` use
in the existing Python 3.9 environment; it was replaced with an indexed loop,
then the roads, architect, and report stages were rerun successfully.

## Verification hardening pass — 2026-08-16

- Added `--data-root` propagation to `fetch-niah`; no-network runs now avoid
  importing the HTTP client and can build into a separate data directory.
- Fixed multipart extraction so holes remain attached to the correct outer
  ring when an earlier invalid outer is discarded.
- Added `scripts/verify.py` and a final `verify` pipeline stage. It checks
  analysis/target/control contracts, GeoJSON ID/count alignment, p-value
  positivity and verdicts, NIAH source regions and match distances, manifest
  revision/source hashes, report template substitution, and inline Node.js
  syntax. It writes `output/verification.json`.
- Expanded manifest counts to include NIAH tables, point-pattern turns, roads,
  architect evidence, report bytes, verification bytes, and the manifest
  schema version.
- Added regression tests for custom-stage ordering, multipart hole alignment,
  p-value contracts, and the new verifier. The suite now has 16 tests.
- Corrected manifest CSV counts to parse records rather than physical lines,
  so quoted multiline architect evidence is counted accurately; added a
  regression test. The suite now has 17 tests.

## Enhancement pass — 2026-08-17

Implemented the complete follow-up scope against the cached Ireland snapshot:

- Added shape descriptors to the canonical analyzer: rectangularity, angle
  entropy, radial variability, four radial Fourier coefficients, vertex
  density, hole-area fraction, and OSM building/address/height context.
- Added deterministic KD-tree spatial matching with replacement, match-quality
  diagnostics, matched-set effects, and a dependency-free random-effects
  stratified log-odds sensitivity model.
- Added OSM `building:part` aggregation and a normalized CSV/GeoJSON LiDAR
  coverage contract that reports `not_provided` rather than treating missing
  heights as zeros.
- Added historical-validation rows, candidate dossiers, a source register,
  optional curated historical-reference input, and explicit manual-review
  warnings.
- Added Ripley K/L, Moran's I, county-preserving permutations, and sampled
  nearest-road proximity diagnostics alongside the existing point-pattern and
  road-bearing analyses.
- Extended the report data pack and popups, expanded the manifest to schema
  version 2 with output hashes, and expanded the verifier to cover every new
  artifact contract.
- Added the stage-resume workflow, optional-source CLI flags, a historical
  reference template, and regression tests. The suite now has 20 tests.

Final cached enhancement build:

```text
.venv/bin/python run_pipeline.py --stage all --no-network --seed 20260816 --mc 300
.venv/bin/python -m pytest                 # 20 passed
.venv/bin/ruff check scripts tests run_pipeline.py
.venv/bin/python -m compileall -q scripts run_pipeline.py
```

The final artifact contract passed for 123,810 analysis rows, 33,416 targets,
90,394 controls, 100,212 matched pairs, 33,416 historical-validation rows,
and the schema-version-2 manifest. LiDAR remained explicitly unavailable in
the local input snapshot.
