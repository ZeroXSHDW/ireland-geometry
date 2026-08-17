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

The checked local snapshot was regenerated on 2026-08-17 with seed `20260816`
and 300 Monte Carlo iterations:

- 135,173 OSM area elements ingested;
- 123,810 footprints pass the 25 m² analysis threshold;
- 33,416 target buildings and 90,394 empirical controls;
- 9,455 OSM→NIAH spatial joins (8,884 analyzed rows);
- 41 NIAH significance tests, 2 point-pattern groups, 1,114 architect-evidence rows;
- 100,212 local target-control pairs, 12 matched tests, and 12 hierarchical tests;
- 246 footprints with mapped OSM building parts, 30 Ripley rows, 5 Moran rows,
  8 county permutations, and 5 road-proximity summaries;
- 123,810 mapping-history and spatial-covariate rows, 50,468 globally unique
  no-replacement validation pairs, 37,422 deterministic holdout rows, and a
  1,000-record expert-review queue;
- a 123,810-row data-quality audit and 12 spatial block-bootstrap uncertainty
  rows;
- LiDAR is explicitly `not_provided` for this snapshot; the ingestion contract
  is ready for normalized heights, GeoTIFF DSMs, or geographic LAS/LAZ;
- a standalone report at `output/report.html`, a hosted lazy-data version at
  `output/report_lazy.html`, and a provenance manifest at `output/manifest.json`.

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
.venv/bin/python run_pipeline.py --stage all --no-network --data-root /path/to/data --out-dir /path/to/output
.venv/bin/python run_pipeline.py --stage all --no-network --lidar data/lidar/building_heights.csv
.venv/bin/python -m pytest
.venv/bin/ruff check scripts tests run_pipeline.py
.venv/bin/python scripts/repro_check.py --out-dir output
```

Every stage is independently runnable, so an interrupted build can resume at
the last completed stage. `--lidar` accepts normalized CSV/GeoJSON, GeoTIFF
DSM/DTM, or optional LAS/LAZ input;
`--historical-references` accepts a curated CSV of independently checked
historical sources. Both are optional and their coverage is reported rather
than imputed. `--boundaries`, `--settlements`, `--osm-history`, `--road-graph`,
and `--review-labels` activate the corresponding external-source adapters.

## Pipeline stages

| Stage | Purpose |
|---|---|
| `fetch` | Download/cache the Geofabrik Ireland PBF and extract OSM areas, including relations, holes, and deterministic controls. |
| `fetch-niah` | Cache and normalize the five official NIAH regional archives. |
| `analyze` | Compute footprint metrics, pattern flags, scores, and control comparisons. |
| `niah` | Spatially join NIAH records, run era/rating/type tests, and audit the angle band. |
| `architects` | Extract validated architect mentions and produce evidence-linked exploratory rates. |
| `sensitivity` | Match local size-aware controls and fit matched-set plus random-effects stratified sensitivity models. |
| `spatial-covariates` | Attach administrative, settlement, and deterministic mapping-density strata. |
| `osm-history` | Summarize normalized OSM edit histories and expose mapping-age/quality coverage. |
| `validation` | Match controls without replacement within observed strata and emit balance diagnostics. |
| `building-parts` | Aggregate OSM `building:part` geometry and produce a coverage-aware optional LiDAR table. |
| `historical` | Build NIAH/architect/heritage evidence records and review-ready candidate dossiers. |
| `review` | Build an annotatable expert queue and calibration/confusion artifacts. |
| `quality-audit` | Audit missingness, numeric validity, duplicates, geometry quality, and source coverage. |
| `point-pattern` | Test inter-building bearings, turns, and nearest-neighbour spacing against nulls. |
| `spatial-stats` | Produce Ripley K/L, Moran's I, and county-preserving permutation diagnostics. |
| `spatial-bootstrap` | Estimate spatial block-bootstrap intervals and direction probabilities for primary signals. |
| `roads` | Compare road and river segment bearings with church-edge bearings. |
| `road-proximity` | Compare sampled target/control centroid proximity to mapped drivable roads. |
| `road-routing` | Compute Dijkstra shortest-path distances on a supplied graph or opt-in PBF conversion. |
| `holdout` | Apply the tracked deterministic holdout split and preregistered primary tests. |
| `columnar` | Always write JSONL; write Parquet/DuckDB when optional engines are installed. |
| `report` | Build a data-driven Leaflet dashboard with filters, map layers, downloads, and methods. |
| `repro-check` | Hash stable artifacts and optionally compare two runs. |
| `verify` | Validate all final artifacts, provenance hashes, statistical fields, report tokens, and report JavaScript syntax. |

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
| `matched_controls.csv` | Deterministic local target-to-control pairs matched on geography and log footprint area. |
| `matched_control_summary.csv` | Per-group match distance, area-ratio, same-cell, and control-reuse diagnostics. |
| `matched_significance.csv` | Matched-set effects and Holm-adjusted sensitivity tests. |
| `hierarchical_model.csv` | Random-effects stratified log-odds estimates with between-stratum variance (`tau2`) and confidence intervals. |
| `spatial_covariates.csv` | County/admin fallback, settlement, mapping cell/count, and density-bin covariates. |
| `mapping_history.csv` | Per-footprint OSM version/edit-age summary, with explicit missing-source status. |
| `data_quality.csv` / `data_quality_summary.json` | Grouped missingness, range checks, duplicate geometry diagnostics, and source coverage. |
| `spatial_bootstrap.csv` | Spatial block-bootstrap intervals and probabilities of a positive/negative target-control difference. |
| `matched_controls_strict.csv` | Globally unique-control pairs with no replacement and observed strata fields. |
| `matched_strict_significance.csv` | No-replacement matched effects and Holm-adjusted validation tests. |
| `niah_join.csv` | OSM→NIAH matches, region, date, rating, type, match mode, and distance. |
| `niah_significance.csv` | NIAH era, rating, and original-type tests. |
| `niah_decades.csv` | Decade-resolution era-matched tests with their own Holm family. |
| `niah_golden_angles.csv` | Matched vertex-angle records split into octagon-ish, golden-core, and high-side bands. |
| `point_pattern.csv` | Deterministic point-pattern statistics and Monte Carlo null summaries. |
| `point_pattern_turns.csv` | Inter-building turn-angle observations. |
| `ripley.csv` | Translation-edge-corrected K/L clustering summaries at multiple radii. |
| `moran.csv` | k-nearest-neighbour Moran's I for golden-angle flag clustering with label permutations. |
| `county_permutation.csv` | NIAH-county-preserving target/control permutations for geometry signals. |
| `roads_compare.csv` | Road/river bearing histograms and correlation/verdict rows. |
| `road_proximity.csv` | Sampled centroid distance to the nearest mapped drivable-road geometry. |
| `road_routing.csv` / `road_routing_pairs.csv` | Actual routed distances when a graph is supplied; otherwise explicit `not_provided` status. |
| `building_parts.csv` | OSM building-part counts, coverage, height/level tags, and repair status. |
| `lidar_coverage.csv` | Per-footprint optional LiDAR heights, provenance, and availability status. |
| `architects.csv` | Exploratory per-architect rates with Wilson intervals. |
| `architects_binary.csv` | Named-versus-anonymous comparisons by class. |
| `architects_evidence.csv` | Every accepted architect attribution with source registration number, evidence text, and confidence. |
| `historical_validation.csv` | Per-target evidence status, NIAH/architect/heritage links, warnings, and review priority. |
| `candidate_dossiers.csv` | Top candidate records ready for manual plan, imagery, and archive review. |
| `historical_source_register.csv` | Coverage and status of each historical evidence channel. |
| `review_queue.csv` / `review.html` | Expert labels queue, downloadable annotation page, and calibration inputs. |
| `holdout_results.csv` / `analysis_plan_used.json` | Preregistered split assignments, primary holdout effects, and plan hash. |
| `analysis_results.jsonl` / `analysis_results.parquet` / `analysis.duckdb` | Scalable exports; Parquet and DuckDB are optional. |
| `report.html` / `report_lazy.html` / `report_data.json` | Standalone dashboard plus a hosted dashboard that fetches the data pack lazily. |
| `manifest.json` | UTC build time, Git revision, parameters, source paths, SHA-256 hashes, byte sizes, and row counts. |
| `verification.json` | Machine-readable result from the final `verify` stage. |

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

It also records rectangularity, normalized angle entropy, radial variability,
four low-order radial Fourier descriptors, vertex density, hole-area fraction,
OSM building/height/levels tags, address context, and mapping-quality flags.
These descriptors are scale-aware and descriptive; they are not a learned
classifier and should not be interpreted as architectural authorship.

## Statistical design

- Ordinary buildings are sampled deterministically by the extractor and are
  retained as an empirical control population.
- Matched sensitivity controls use local KD-tree nearest neighbours within
  nearby log-area bands. Matching is with replacement; distance, area ratio,
  same-cell coverage, and reuse are written to diagnostics.
- The strict validation stage uses deterministic global greedy assignment with
  each control used at most once. Settlement and mapping-density bins are hard
  strata; county is hard only when an external boundary layer is supplied;
  construction era is compared only when observed for both records.
- The hierarchical sensitivity output is a random-effects meta-analysis of
  local cell/area strata, with a reported between-stratum variance (`tau2`).
  It is a dependency-free multilevel sensitivity model, not a claim that all
  OSM rows are independent.
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
- Ripley, Moran, and county-permutation outputs are spatial sensitivity
  diagnostics. County tests are limited to NIAH-matched rows; road proximity
  is nearest mapped-road distance, while `road-routing` is a separate actual
  shortest-path diagnostic that requires a routable graph.
- OSM edit history is a mapping-process covariate only. It is not construction
  age, architectural authorship, or evidence of intent.
- The holdout split is deterministic and plan-hashed; it does not convert a
  search-stage footprint score into a confirmatory causal estimate.
- Spatial block-bootstrap intervals resample 0.1-degree cells rather than
  individual buildings, giving a dependence-aware uncertainty diagnostic for
  the three primary geometry signals.

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
- The local size/geography-matched worship comparison remains elevated for the
  golden-angle flag (+4.97 percentage points; Holm-adjusted signal), while
  matched golden-ratio aspect matching is background. This is a sensitivity
  result with control reuse, not an independent sample.
- County-preserving golden-angle permutations are suggestive for worship;
  worship Moran's I is background after correction. Ripley K/L is descriptive
  clustering evidence and has no causal interpretation. Nearest-road distance
  is reported as a sampled proximity diagnostic; actual routing remains
  unavailable until a routable graph is supplied.
- Road and river segment histograms have near-zero correlation with the
  church-edge histogram, so those two tested network confounds do not explain
  the broad 44° peak. This remains an exploratory siting result.

## Limitations

- OSM is community-mapped. A footprint can be simplified, incomplete, tagged
  inconsistently, or represent only one part of a larger building complex.
- `building:part` is sparse and does not reconstruct a complete 3-D model.
  LiDAR outputs remain `not_provided` until a normalized height file is
  supplied; blank height is not interpreted as zero.
- NIAH is a Republic-of-Ireland heritage inventory and is not a complete
  census of every building; Northern Ireland target rows generally have no
  NIAH match. The join is centroid containment first, then nearest within
  50 m, and the match mode is retained.
- Mapped footprints are not architectural plans. OSM geometry cannot establish
  construction intent, hidden building parts, or historical design decisions.
- Thresholds and candidate families were selected for screening. A future
  confirmatory study should expand the tracked `analysis_plan.json`, use real
  boundary/settlement/history sources, and validate against independent plan
  data and expert labels.
- Sentinel-2 is optional and requires Copernicus credentials. The report's
  satellite layer is a live visual basemap, not a downloaded analytical input.

## Optional LiDAR and historical references

LiDAR can be supplied as a CSV with `osm_id`, `roof_height_m`,
`elevation_m`, `coverage_m2`, `source`, and `quality`, or as a GeoJSON
FeatureCollection with those properties. The normalized output preserves
which footprints were covered and where the values came from. GeoTIFF DSM/DTM
input is sampled at the footprint centroid and geographic-coordinate LAS/LAZ
is aggregated inside valid footprints when `.[geo3d]` is installed.

The historical-reference CSV is intentionally manual. It accepts
`osm_id` and/or `reg_no`, plus `source`, `source_type`, `source_url`,
`archive_ref`, `verified`, `independent`, `year`, `evidence_text`, image/plan
paths, and `notes`. Add only sources that a researcher has independently checked. The
pipeline combines those records with NIAH matching, OSM heritage metadata,
architect evidence, and review labels to create a traceable evidence queue; it
does not turn a source link or label into proof of design intent.

## Artifact contracts and provenance

The manifest is schema version 3. It records input hashes, output hashes,
byte sizes, CSV row counts, seed, Monte Carlo setting, optional-source paths,
analysis-plan hash, and the Git revision. `verify` checks the analytical tables,
ID alignment, no-replacement uniqueness, probability ranges, report
JavaScript, optional-source status, and artifact hashes. Atomic writes make
each stage safe to rerun after interruption.

## Licensing and attribution

- OpenStreetMap contributors, ODbL 1.0.
- National Inventory of Architectural Heritage / Department of Housing,
  Local Government and Heritage, CC BY 4.0.
- Sentinel-2 / Copernicus and Esri World Imagery are used according to their
  respective terms for optional visual/reference layers.
