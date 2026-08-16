# Ireland Geometric Pattern Scan

Reproducible geospatial analysis of Irish building footprints. The pipeline
extracts target buildings and an empirical ordinary-building control group
from OpenStreetMap, measures footprint geometry, joins the Republic of
Ireland's National Inventory of Architectural Heritage (NIAH), tests
between-building patterns, and builds an interactive HTML report.

The project is a pattern-screening tool. A high score or a small p-value is
not evidence of architectural intent; it identifies patterns that deserve
inspection against the source footprint, imagery, historical record, and a
pre-specified or independently replicated analysis.

## Current cached build

The checked local snapshot was regenerated on 2026-08-16 with seed `20260816`
and 300 Monte Carlo iterations:

- 135,173 OSM area elements ingested;
- 123,810 footprints pass the 25 m² analysis threshold;
- 33,416 target buildings and 90,394 empirical controls;
- 9,455 OSM→NIAH spatial joins (8,884 analyzed rows);
- 41 NIAH significance tests, 2 point-pattern groups, 1,114 architect-evidence rows;
- an interactive report at `output/report.html` and a provenance manifest at
  `output/manifest.json`.

## Quick start

Use Python 3.10 or newer. The primary extractor needs the `pyosmium` package
and processes the PBF locally.

```bash
python3.10 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'

# Fully offline when the cached inputs in data/ are present:
.venv/bin/python run_pipeline.py --no-network --seed 20260816 --mc 300

# Normal run: download missing inputs, then run every stage:
.venv/bin/python run_pipeline.py

open output/report.html
```

`--no-network` fails clearly if a required cache is missing. `--refresh`
re-extracts or refreshes cached inputs; use it without `--no-network` when a
new upstream snapshot is intended. All paths are project-root-relative by
default, but `--data-root`, `--out-dir`, and `--pbf` accept absolute paths.

Useful commands:

```bash
.venv/bin/python run_pipeline.py --help
.venv/bin/python run_pipeline.py --stage analyze,niah,report --no-network
.venv/bin/python -m pytest
.venv/bin/ruff check scripts tests run_pipeline.py
```

## Pipeline stages

| Stage | Purpose |
|---|---|
| `fetch` | Download/cache the Geofabrik Ireland PBF and extract OSM areas, including relations, holes, and deterministic controls. |
| `fetch-niah` | Cache and normalize the five official NIAH regional archives. |
| `analyze` | Compute footprint metrics, pattern flags, scores, and control comparisons. |
| `niah` | Spatially join NIAH records, run era/rating/type tests, and audit the angle band. |
| `point-pattern` | Test inter-building bearings, turns, and nearest-neighbour spacing against nulls. |
| `roads` | Compare road and river segment bearings with church-edge bearings. |
| `architects` | Extract validated architect mentions and produce evidence-linked exploratory rates. |
| `report` | Build a data-driven Leaflet dashboard with filters, map layers, downloads, and methods. |

The optional `scripts/fetch_osm.py` Overpass channel and
`scripts/fetch_satellite.py` Sentinel-2 channel remain available for separate
refresh or imagery work. The main report uses live OSM and Esri basemap tiles
for visual reference; it does not silently bulk-scrape Yandex imagery.

## Outputs

| File | Contents |
|---|---|
| `analysis_results.csv` | One row per analyzed target/control footprint, with dimensions, angles, symmetry, convexity, circularity, quality flags, and score. |
| `top_patterns.csv` | Target rows with score ≥ 55, ranked for inspection. |
| `ireland_buildings.geojson` | Target geometries and report properties. |
| `significance.csv` | Building-level target/control comparisons with Wilson intervals, risk differences, odds ratios, raw p, Holm-adjusted p, method, and verdict. |
| `niah_join.csv` | OSM→NIAH matches, region, date, rating, type, match mode, and distance. |
| `niah_significance.csv` | NIAH era, rating, and original-type tests. |
| `niah_decades.csv` | Decade-resolution era-matched tests with their own Holm family. |
| `niah_golden_angles.csv` | Matched vertex-angle records split into octagon-ish, golden-core, and high-side bands. |
| `point_pattern.csv` | Deterministic point-pattern statistics and Monte Carlo null summaries. |
| `point_pattern_turns.csv` | Inter-building turn-angle observations. |
| `roads_compare.csv` | Road/river bearing histograms and correlation/verdict rows. |
| `architects.csv` | Exploratory per-architect rates with Wilson intervals. |
| `architects_binary.csv` | Named-versus-anonymous comparisons by class. |
| `architects_evidence.csv` | Every accepted architect attribution with source registration number, evidence text, and confidence. |
| `report.html` | Standalone interactive dashboard: search, filters, pagination, clustered markers, top outlines, OSM links, CSV/GeoJSON downloads, significance tables, and provenance. |
| `manifest.json` | UTC build time, Git revision, parameters, source paths, SHA-256 hashes, byte sizes, and row counts. |

## Geometry and pattern measurement

`scripts/geometry.py` is the canonical geometry layer used by extraction,
analysis, NIAH, and report generation. It accepts legacy single-ring caches,
the current lossless multi-exterior format, and GeoJSON; preserves multipart
polygons and holes; records invalid/repair/degenerate state; and projects each
footprint into local metres before metric calculation.

The analyzer measures minimum-rotated-rectangle aspect ratio, Fibonacci ratio
and dimension proximity, vertex angles including 137.5078°, reflective and
rotational IoU, circularity, perimeter, and convexity. Convexity is defined as

```text
polygon area / convex-hull area
```

so a valid polygon is at most 1.0, rather than the inverted ratio used by the
original prototype. The composite score is a prioritisation heuristic, not a
probability or a model of design quality.

## Statistical design

- Ordinary buildings are sampled deterministically by the extractor and are
  retained as an empirical control population.
- Primary rates use building-level two-proportion z-tests, Wilson confidence
  intervals, risk differences, and continuity-corrected odds ratios.
- Holm–Bonferroni adjustment is applied within named test families. Reports
  display adjusted verdicts, not raw-significance highlights.
- NIAH century tests compare worship rows with NIAH-dated ordinary controls in
  the same century where possible. The decade table has a separate decade
  family adjustment.
- Point-pattern orientation tests keep the observed edge set fixed and rotate
  edges under an isotropic null; peak-angle tests account for searching over
  the angle grid. The control sample and seed are written to the output.
- Road/river and per-architect analyses are exploratory diagnostics, not
  independent confirmation of architectural intent.

P-values are retained at full floating-point precision in CSV outputs and are
formatted for humans without printing `p=0`; very small values mean “below
the numerical/reporting threshold,” not literally impossible outcomes.

## Snapshot findings

These statements describe the cached artifacts above, not universal claims:

- Aspect-ratio golden-ratio matching is at or below the ordinary-building
  control rate for worship, government, historic, and civic groups after Holm
  correction; it behaves like background coincidence in this snapshot.
- The 137.5° footprint-angle flag is more common in every target group than
  in the empirical control sample. For worship the observed rate is 7.09%
  versus 0.69% in 90,394 controls; the output records the effect size and
  adjusted p-value. This is an association in mapped footprints, not proof of
  deliberate golden-angle construction.
- In the NIAH era-matched family, the 18th-century worship result is
  suggestive after adjustment, while the 19th- and 20th-century results are
  signals in this snapshot. National/International versus Regional rating is
  background after the full rating-family correction.
- The between-building result is not a golden-specific alignment: worship
  bearings peak broadly near 44°, while the golden-angle band is only a weak
  excess and the golden-core comparator is not compelling. Spiral-turn tests
  do not support the hypothesis, and Fibonacci nearest-neighbour matches do
  not exceed the sham comparison. Architect-designed versus anonymous worship
  is also non-significant in the current evidence set.
- Road and river segment histograms have near-zero correlation with the
  church-edge histogram, so those two tested network confounds do not explain
  the broad 44° peak. This remains an exploratory siting result.

## Limitations

- OSM is community-mapped. A footprint can be simplified, incomplete, tagged
  inconsistently, or represent only one part of a larger building complex.
- NIAH is a Republic-of-Ireland heritage inventory and is not a complete
  census of every building; Northern Ireland target rows generally have no
  NIAH match. The join is centroid containment first, then nearest within
  50 m, and the match mode is retained.
- Mapped footprints are not architectural plans. OSM geometry cannot establish
  construction intent, hidden building parts, or historical design decisions.
- Thresholds and candidate families were selected for screening. A future
  confirmatory study should preregister the hypotheses, use spatial/temporal
  matching, model mapping quality, and validate against independent plan data.
- Sentinel-2 is optional and requires Copernicus credentials. The report's
  satellite layer is a live visual basemap, not a downloaded analytical input.

## Licensing and attribution

- OpenStreetMap contributors, ODbL 1.0.
- National Inventory of Architectural Heritage / Department of Housing,
  Local Government and Heritage, CC BY 4.0.
- Sentinel-2 / Copernicus and Esri World Imagery are used according to their
  respective terms for optional visual/reference layers.
