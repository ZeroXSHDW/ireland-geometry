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
