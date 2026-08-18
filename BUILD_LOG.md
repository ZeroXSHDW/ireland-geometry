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

- The OSM bindings were imported as `osmium` but were not declared under the
  published distribution name.
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

## Cross-backend export parity contract — 2026-08-18

The scalable export layer now proves semantic parity instead of checking only
that optional files exist and have the expected row count:

- `scripts/columnar.py` writes `columnar_status.json` under
  `ireland-geometry.columnar.v1`, recording columns, inferred field kinds,
  schema SHA-256, and an order-independent multiset digest of normalized rows
  for CSV, JSONL, Parquet, and DuckDB.
- Empty-string/null normalization and numeric normalization make the digest
  stable across the string-oriented and typed backends while still detecting
  changed values, missing columns, extra columns, or duplicate-row changes.
- `scripts/verify.py` recomputes every readable backend's digest and rejects
  stale or tampered exports; Doctor, `/api/metadata`, and `/api/capabilities`
  expose the contract, parity status, checked backends, and coverage.
- Regression tests cover successful parity and a tampered JSONL export. The
  real cached 123,810-row run completed with `parity=pass/full` and final
  verification `PASS`.
- Doctor is now version 56. It records source freshness metadata and supports
  `--max-input-age-days` so strict readiness can reject stale required caches.
  The source suite now passes 189 tests and Ruff checks remain green.
- The verifier now validates the `ireland-geometry.freshness.v1` manifest block,
  including age arithmetic, source alignment, and current source modification
  times; tampered or stale freshness metadata is rejected.
- The standalone and lazy report packs now carry a compact source-freshness
  summary and render the reported source count/oldest age in the method and
  provenance panel; the full manifest remains the detailed record.
- The OpenAPI 3.1 contract now declares `SourceFreshness` and references it
  from metadata and capabilities responses, including source timestamp and age
  fields for schema-aware automation.
- Stage-cache version 5 now uses the versioned
  `ireland-geometry.stage-cache.fingerprint.v1` contract. Git revision is kept
  for audit metadata but no longer invalidates stages by itself, and
  provenance-only controller edits no longer force analytical rebuilds; the
  v4 cache is intentionally invalidated and refreshed.

## Compact interpretation sidecar and API — 2026-08-18

The structured, data-derived report interpretation is now a small first-class
artifact rather than something automation must extract from the full report
data pack:

- `scripts/report.py` writes deterministic `output/interpretation.json` with
  the interpretation contract, summary counts, validation gate, headline,
  findings, and source-aware caveats.
- `ireland-geometry-serve` exposes `GET /api/interpretation` under
  `ireland-geometry.interpretation.v1`, with ETag revalidation and explicit
  `404`/`500` responses for missing or malformed sidecars.
- Health, capabilities, OpenAPI, Doctor, CI, the verifier, reproducibility
  comparison, and bundle inventory now advertise or validate the sidecar.
- Focused server, verifier, report, and Doctor regression tests cover the new
  contract, including stale/mismatched sidecar detection.
- The pipeline now refreshes and re-verifies the report pack after the final
  validation pass whenever `report,verify` are selected, preventing a fresh
  build from ending with a stale `not_provided` interpretation gate.
- The `ireland-geometry.dry-run.v1` plan now advertises those post-validation
  operations explicitly, and Doctor/CI verify the lifecycle capability instead
  of leaving the extra execution pass implicit.
- The final source suite now passes 182 tests, including the lifecycle-plan
  regression.

## Data-derived report interpretation and validation display — 2026-08-18

Implemented the next dashboard capability upgrade:

- `scripts/report.py` now reads the primary significance, conventional-angle
  negative-control, matched-control, NIAH-era, county-permutation, Moran, and
  deterministic holdout artifacts once and derives a compact structured
  interpretation from those rows. It never embeds the current snapshot's
  observed rates or p-values in the report narrative.
- Both the standalone and lazy dashboards now show a data-derived
  interpretation panel with result statuses, effect/rate summaries, the
  validation-gate state, and source-aware caveats. The method panel also shows
  analytical readiness separately from HTTP/report serving readiness.
- Report data now carries the same verification/schema/reproducibility
  validation record shape used by the local server. The independent verifier
  checks the manifest gate, record presence, structured finding fields, and
  interpretation IDs so stale report packs fail closed.
- Doctor version 52 reports the interpretation panel as a dashboard capability;
  source tests, CI package checks, and the generated-report contract cover the
  new surface.

## Reliability and analysis pass — 2026-08-16

Implemented and verified:

- Added `pyproject.toml`, runtime `requirements.txt` coverage for the `osmium`
  PyOsmium bindings,
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

## Validation and scale tranche — 2026-08-17

Added the remaining validation and production extensions:

- `spatial-covariates.py` adds optional administrative/settlement GeoJSON
  joins plus deterministic mapping-density cells and explicit fallback status;
- `osm_history.py` accepts normalized OSM version histories and records edit
  age, editors, changesets, geometry/tag edits, and missing-source status;
- `validation.py` performs deterministic no-replacement matching within
  observed settlement/density strata, with county and era comparability rules;
- `building_parts.py` now has optional GeoTIFF DSM/DTM and geographic LAS/LAZ
  adapters in addition to normalized LiDAR records;
- `historical_validation.py` now carries source type, archive references,
  verification/independence, evidence text, images, and plan paths;
- `review.py` creates an HTML annotation queue, downloadable labels, binary
  calibration metrics, and confusion tables;
- `road_routing.py` provides a portable graph contract, Dijkstra distances, and
  an opt-in PBF graph adapter; no graph was supplied in this snapshot;
- `holdout.py` runs the tracked `analysis_plan.json` split and plan-hashed
  holdout effects;
- `columnar.py` always writes JSONL and optionally writes Parquet/DuckDB, while
  the report also has a separate data pack and lazy-loading HTML variant;
- CI, stable-artifact hashing, source templates, schema-version-3 manifest
  coverage, and verifier contracts were added.

Integration outputs from the cached no-network run: 50,468 strict pairs,
37,422 holdout rows, 1,000 review rows, 123,810 history/covariate rows, and
explicit `not_provided` LiDAR/history/routing records. Final checks: 25 tests,
Ruff, compileall, report JavaScript syntax, repro check, and the independent
artifact verifier all passed.

## Quality and uncertainty tranche — 2026-08-17

- Added `data_quality.py`, which audits grouped missingness, numeric ranges,
  duplicate IDs/centroids, geometry repair/multipart rates, and optional-source
  coverage without silently altering rows.
- Added `spatial_bootstrap.py`, a deterministic 0.1-degree block bootstrap for
  golden-angle, golden-ratio, and Fibonacci-ratio target/control differences.
  It reports spatial intervals and direction probabilities rather than
  pretending individual buildings are independent.
- Extended the report data pack and statistical table with bootstrap results,
  and extended the verifier to check the quality summary, bootstrap bounds, and
  lazy/review page JavaScript syntax.

The current cached snapshot reports zero duplicate OSM IDs, 23 duplicate
centroid locations flagged for inspection, 100% valid geometry, and 12
available block-bootstrap rows. The expanded suite now has 27 tests.

## Reliability and specificity tranche — 2026-08-17

- Added `data_quality_duplicates.csv`, an inspectable exact-centroid review
  queue with affected OSM IDs and target/control composition.
- Added the tracked `schemas/artifacts.json` registry and `schema-audit` stage;
  the verifier now requires a passing machine-readable schema validation pack.
- Added deterministic stage fingerprints and `--incremental` reuse. Cache
  decisions include input hashes, stage/controller code hashes, command
  parameters, the Python/dependency runtime signature, and expected output
  hashes; report, repro, and verify always run.
- Added separately corrected 60°/120° conventional-angle negative controls and
  exposed them in the statistical dashboard and analysis plan.
- Optimized report generation to build the data pack once, added a visible
  data-quality section, and expanded the report statistics table with the
  negative-control family.

Final offline rebuild and incremental smoke run:

```text
.venv/bin/python run_pipeline.py --stage all --no-network --seed 20260816 --mc 300 --bootstrap-iterations 200
.venv/bin/python run_pipeline.py --stage all --no-network --seed 20260816 --mc 300 --bootstrap-iterations 200 --incremental
.venv/bin/python -m pytest                 # 32 passed
.venv/bin/ruff check scripts tests run_pipeline.py
.venv/bin/python -m compileall -q scripts tests run_pipeline.py
```

Both full and incremental runs passed the schema audit, 51-artifact
reproducibility check, and final verifier. The incremental run skipped every
unchanged deterministic stage and regenerated only report/repro/verify.

## Operational usability tranche — 2026-08-17

Added the operational checks needed to inspect and reuse the project safely:

- `run_pipeline.py --dry-run` now prints each resolved stage command and its
  incremental cache decision without executing stages or writing files;
- `scripts/doctor.py` reports the supported Python floor, required and optional
  dependencies, required cached inputs, optional-source coverage, generated
  outputs, and Git cleanliness in human- or machine-readable form;
- installable `ireland-geometry` and `ireland-geometry-doctor` console commands
  were added to the package metadata and CI help checks;
- corrected the runtime dependency to the published `osmium` distribution;
  its import module is also `osmium`.

Focused validation after the upgrade: 54 tests passed, 1 optional GeoTIFF test
skipped in the core environment; Ruff passed, compileall
passed, dry-run produced no files, and both installed console entry points
executed. The local doctor correctly reports the existing Python 3.9.6
environment as outside the declared Python >=3.10 support range; the project
CI matrix remains the supported 3.10–3.12 runtime.

## Supported-runtime rebuild and geometry hardening — 2026-08-17

The full current tree was rebuilt offline under Python 3.11.15 using the
cached Geofabrik/NIAH inputs. Validated outputs are 123,810 analysis rows,
33,416 targets, 90,394 controls, 50,466 strict pairs, 1,708 top-pattern rows,
and 100% valid geometry. Schema audit, 54-artifact reproducibility, and final
verification all passed.

The rebuild exposed repeated Shapely 2.1 `oriented_envelope` warnings and a
slow per-footprint Shapely interpolation loop. `geometry.py` now contains a
warning-contained rotated-rectangle helper and direct equal-perimeter ring
resampling, with a Python 3.9 fallback for the legacy local environment. The
full rebuild completed without the warning flood; the focused core suite now has
57 passing tests with one optional GeoTIFF test skipped, while the geo3d suite
has 58 passing tests under Python 3.11.

## Runtime-aware cache upgrade — 2026-08-17

Stage-cache format version 2 now records Python/platform details and the
versions of the core and optional data-engine distributions. Those details
are part of every deterministic stage fingerprint, and a cache with a
different runtime signature is discarded automatically. This closes a
reproducibility gap observed during the supported-runtime rebuild, where
geospatial dependency changes altered analytical counts despite unchanged
source, inputs, and command parameters.

The version-2 incremental smoke run reused all 23 deterministic stages and
reran only report, reproducibility, and verification; all three completed
successfully.

The opt-in `--road-from-pbf` routing mode now declares the source PBF as a
stage input, so replacing that source cannot silently reuse an old routing
result. The graph README distinguishes the pipeline wrapper flag from the
direct adapter flag.

The default stage order now places `quality-audit` after `road-routing`,
matching the audit's declared routing-status input and preventing an
incremental run from auditing a stale routing artifact.

The default routing cache dependency is also limited to `road_nodes.csv` and
`road_edges.csv`; editing the graph README no longer invalidates the routing
stage.

The doctor now requires both routing graph components before reporting routing
as available, and exposes JSONL, Parquet, DuckDB, and columnar-status outputs in
its capability snapshot.

Incremental input declarations are now stage-scoped: optional LiDAR, history,
boundary, review, routing, and analysis-plan files are fingerprinted only by
their consuming stages. A custom routing graph also replaces the default graph
components in that stage's dependency set.

Doctor strict-readiness now requires a passing `verification.json` in addition
to supported dependencies, required cached inputs, and a clean worktree; the
verification state is also exposed in text and JSON reports.

## Columnar capability upgrade — 2026-08-17

The columnar stage now removes stale optional artifacts when an engine is absent
and writes Parquet and DuckDB files through temporary paths before replacement.
The supported Python 3.11 environment was validated with `pyarrow 25.0.1` and
`duckdb 1.5.5`: both exports contain all 123,810 analysis rows, the report and
reproducibility pack regenerated successfully, and final verification passed
with 54 stable artifacts.

The verifier now validates those optional files when advertised as available,
including backend row counts, and rejects stale files when a backend is marked
`not_installed` or `error`.

Manifest verification now rejects duplicate, external, missing, or unlisted
output artifacts, making the provenance inventory complete rather than partial.

The incremental stage cache now includes Parquet and DuckDB in the recorded
columnar outputs whenever their status is `available`; deleting either file
forces the columnar stage to regenerate it.

Cache reuse now also rejects a columnar cache record when an unavailable backend
has left a stale Parquet or DuckDB file behind, forcing one cleanup pass before
the stage can be reused.

The doctor’s optional-source scan now detects supported LiDAR/DSM, history,
historical-reference, and review formats while ignoring repository templates.

CI now has a dedicated Python 3.11 `.[dev,data]` job that imports both
optional engines and runs the full test/lint/compile/doctor checks, in addition
to the core 3.10–3.12 matrix.

CI also has a dedicated Python 3.11 `.[dev,geo3d]` job. The supported
environment exercises normalized LiDAR plus a real GeoTIFF 3×3 centroid-window
sample with `laspy 2.7.0` and `rasterio 1.4.4`; the optional-data suite reaches
57 passing tests with one optional GeoTIFF test skipped, while the geo3d suite
reaches 58 passing tests.

## Routable-graph usability upgrade — 2026-08-17

The routing adapter can now persist a bounded PBF conversion as a reusable
portable directed graph (`road_nodes.csv`, `road_edges.csv`, and metadata).
Nearest-node snapping uses a latitude-sorted index rather than scanning every
graph node for every pair, and the pipeline exposes explicit limits for graph
ways and routed pairs. The focused core suite now has 57 passing tests under
Python 3.9 and Python 3.11, with one optional GeoTIFF test skipped; the geo3d
suite has 58 passing tests.

## Readiness and provenance contract upgrade — 2026-08-17

Doctor version 2 now exposes the final verification state and only reports
strict readiness when the supported runtime, required inputs, core output pack,
passing verification record, and clean worktree are all present. The verifier
also requires every output artifact to be listed exactly once in the in-tree
manifest, rejecting external, missing, duplicate, or unlisted paths.

The supported Python 3.11.15 incremental run reused all 23 deterministic stages,
reran report/reproducibility/verification, and passed with 123,810 analysis rows,
54 stable artifacts, and a 61-artifact manifest. The full test inventory is 57
core passes plus one optional GeoTIFF skip, or 58 passes with `geo3d` installed.

Normalized LiDAR ingestion now accepts JSON arrays, `{"rows": [...]}` payloads,
and GeoJSON FeatureCollections; malformed normalized inputs fall back to an
explicit `provided_but_unreadable` coverage status instead of aborting the
building-parts stage.

The report data pack and both dashboard variants now include a source-readiness
summary for LiDAR, OSM history, boundaries, routing, historical references,
review labels, columnar exports, and final verification, so zero coverage is
not presented as an undifferentiated empty result.

## Statistical integrity and source-quality contract upgrade — 2026-08-17

The final verifier now recomputes family-wise Holm adjustments and directional
verdicts from each artifact's raw p-values and declared test family. This
detects stale or manually altered adjusted significance values while retaining
the existing finite-range and method-field checks.

Holdout verification now recomputes the deterministic `sha256(seed:osm_id)`
split and checks exact analysis-ID coverage, seed/fraction consistency, source
group/control labels, plan-file hash, preregistered signal keys, target/control
counts, successes, rates, risk differences, status, and plan alpha. A changed
assignment or aggregate can no longer pass as a merely present CSV.

The data-quality audit now normalizes source status from either `status` or
`quality` columns and counts explicit unreadable, incomplete, failed, and
unavailable states as missing source coverage. Regression coverage was added
for Holm tampering, holdout split tampering, and quality-only source statuses.

Validation after the upgrade: the supported Python 3.11.15 suite has 62
passing tests; the local Python 3.9.6 suite has 61 passing tests plus one
optional GeoTIFF skip. Ruff, compileall, the offline incremental rebuild, the
54-artifact reproducibility check, and the final verifier all pass.

## Routable graph activation and snapping performance upgrade — 2026-08-17

The cached Geofabrik PBF was converted into the documented reusable road-graph
contract with a 100,000-way bound: 1,137,520 nodes and 2,149,088 directed
edges. The routing stage now consumes that graph for bounded shortest-path
diagnostics; the current 100-pair sample has 16 reachable pairs. Reachability
and graph truncation remain explicit, so this is not presented as complete
national routing coverage.

Real-graph profiling exposed that the previous latitude-sorted snapping path
could scan a large node band for every query. `road_routing.py` now builds a
deterministic 0.01-degree 2-D grid index with an exact expanding-ring stopping
rule and retains the latitude-sorted path for compatibility. The index was
covered by a cross-cell exactness regression test; the 100-pair routing run,
report, reproducibility pack, manifest, and final verifier all pass.

## Streaming columnar export upgrade — 2026-08-17

The columnar stage no longer loads the complete analysis CSV into a Python list
for JSONL and Parquet export. JSONL is written row-by-row through an atomic
temporary file, and Parquet uses a 10,000-row `pyarrow` batch writer; DuckDB
continues to ingest the CSV directly. A compatibility `read_csv` helper remains
available for callers that explicitly need an in-memory list.

The supported rebuild exercised the new path with 123,810 rows: JSONL,
Parquet, and DuckDB were all written successfully, and both queryable formats
were independently checked at 123,810 rows. Streaming and bounded-batch
regression coverage was added, followed by a passing reproducibility pack and
final verifier. The supported Python 3.11.15 suite now has 63 passing tests;
the local Python 3.9.6 suite has 62 passing tests plus one optional GeoTIFF
skip. Ruff, compileall, and the incremental dry-run also pass.

## Local report serving upgrade — 2026-08-17

The generated lazy dashboard now has a supported localhost-first server at
`scripts/serve_report.py`, packaged as `ireland-geometry-serve`. It serves
`report_lazy.html` and its `report_data.json` pack on one origin, supports
`--open`, alternate ports and the self-contained `report.html`, and exposes a
machine-readable `/__health` endpoint. The default bind address is
`127.0.0.1`; non-local binding is explicit and emits a warning. Server
fixtures, path-boundary checks, health responses, entry-point help, Ruff,
compileall, and a smoke test against the current 120 MB data pack all pass.
The complete supported suite now passes 69 tests; the local suite passes 68
tests plus one optional GeoTIFF skip.

## Access-aware routing upgrade — 2026-08-17

The SQLite PBF exporter now applies the most specific motor-vehicle access
policy: `motor_vehicle`, `motorcar`, `vehicle`, then `access`; blocked values
are `no`, `private`, and `restricted`. The complete graph rebuild scanned
1,123,212 supported ways, excluded 161,488 restricted ways, and retained
961,724 routable ways, 7,425,839 nodes, and 7,553,120 physical segments. The
100-pair pipeline sample now records 97 reachable routes and 3 explicit
unreachable pairs. `--include-restricted` remains available for analyses that
intentionally model private or restricted access.
Doctor version 3 now exposes this server as a capability, including its
localhost default, selected report, and whether the current data pack is
present.
The server now also honors `Accept-Encoding: gzip` for `report_data.json`,
using deterministic in-memory caching and reducing the current pack from
roughly 115 MB to about 8.3 MB on the wire without changing the browser-side
fetch contract.

## Offline dashboard fallback upgrade — 2026-08-17

The report’s former Leaflet failure path only showed a text warning. It now
renders a dependency-free SVG fallback with target points, top candidate
outlines, score colors, coordinate grid, selection details, and the same
filter-driven point set. The existing table, exports, statistical panels, and
report-data fetch remain available when CDN assets or basemap tiles cannot be
loaded. `?offline=1` can force this mode for air-gapped review and automated
smoke tests. The report also gained the missing `renderAll()` path, restoring
table and map updates after filters or searches change.

## Capability inventory and shareable dashboard state — 2026-08-17

The doctor now exposes a machine-readable capability matrix for all 26 pipeline
stages, their cacheability and script presence, dashboard modes, CSV/JSONL/
Parquet/DuckDB export backends, and verification/schema/reproducibility gates.
The report filters and sort order are now restored from and written to URL
parameters, making an offline or served review view reproducible by link while
preserving the explicit `offline=1` mode.

## Complete disk-backed routing upgrade — 2026-08-17

The routing adapter now has a SQLite graph backend and a streaming PBF exporter.
`--graph-format sqlite --max-ways 0` scans all supported highway ways into an
indexed disk-backed graph, avoiding an unsafe in-memory adjacency list. The
cached PBF contains 1,123,212 supported ways, 8,188,839 nodes, and 8,333,081
physical segments. The pipeline now consumes that graph through the existing
`--road-graph data/roads` contract; the 100-pair sample reached 100% of pairs.
The legacy 100,000-way CSV graph remains available as a lightweight portable
fallback. Doctor, stage-cache inputs, tests, and documentation now expose the
backend and completeness metadata; doctor schema version 4 records the SQLite
backend, way/node/segment counts, and complete flag.
The supported suite now has 73 passing tests; the local runtime has 72 passing
tests plus one optional GeoTIFF skip.

## Turn-restriction routing upgrade — 2026-08-17

The complete SQLite exporter now preserves OSM way IDs and parses restriction
relations. Simple via-node `no_*` and `only_*` restrictions are validated
against the access-filtered way/node set and applied during state-aware
Dijkstra. The v4 graph applies 3,938 non-conditional restrictions (3,347
`no_*` and 591 `only_*`), including 181 validated via-way chains, across
961,724 routable ways. It records 78 conditional relations, two unresolved
and two unsupported via-way cases, 127 unsupported relation cases, and 143
unresolved references. The 100-pair sample remains 97/100 reachable, with the
method field identifying one-way plus via-node/via-way turn semantics.
Synthetic SQLite junction tests cover forbidden, only-allowed, and via-way
transitions. The supported suite now has 75 passing tests; the local Python
3.9.6 suite has 74 passing tests plus one optional GeoTIFF skip.

## Conditional turn-profile upgrade — 2026-08-17

The v5 SQLite graph preserves conditional OSM turn restrictions in a separate
table. The cached PBF contains 78 conditional restriction relations: 77 use
supported weekday/time windows, 72 resolve against the access-filtered graph,
four have unresolved references, and one uses unsupported weight-based syntax.
`road_routing.py --departure <ISO-8601> --speed-kmh <value>` evaluates the
stored windows at estimated junction arrival time; without a departure the
conditional rules remain explicitly inactive. Regression coverage includes
weekday/time parsing, active versus inactive routing, and CLI/pipeline
propagation of the profile parameters.

## Point-to-point route query upgrade — 2026-08-17

Added `scripts/route_query.py` and the installable `ireland-geometry-route`
command. It accepts arbitrary WGS84 start/goal coordinates, snaps them to the
nearest persisted graph nodes, applies the existing one-way, via-node, via-way,
and optional conditional turn semantics, and emits JSON with snap distances,
straight and routed distances, estimated duration, departure/arrival times,
graph metadata, and the routing method. The doctor capability matrix now
advertises the query interface, and focused regression coverage exercises an
active conditional detour plus its computed arrival time.

The query now supports opt-in shortest-path reconstruction without changing
the pipeline's distance-only performance path. `--include-path` adds graph node
IDs and GeoJSON-order coordinates to the JSON result; `--geojson-out` writes a
single `LineString` feature (or a `Point` for a zero-length route). The real
cached graph produced a 173-coordinate GeoJSON smoke-test route.
The final supported-runtime suite has 78 passing tests; the local Python 3.9.6
suite has 77 passing tests plus one expected optional GeoTIFF skip.

## Opt-in ferry-geometry upgrade — 2026-08-17

The PBF audit found 38 ferry route relations and 118 ferry-tagged ways; 77
ways remain after the same motor-vehicle access policy used for roads. SQLite
v6 now stores their 1,549 physical segments in a separate `ferry_edges` table,
along with 1,528 additional geometry nodes. Road routing remains unchanged by
default; `--include-ferries` on `road_routing.py`, `run_pipeline.py`, or
`ireland-geometry-route` enables the layer. The method and doctor metadata make
the choice explicit. Ferry schedules, terminal platform semantics, and service
frequency are intentionally not inferred.

## Local route API upgrade — 2026-08-17

The localhost-first report server now exposes `GET /api/route`, reusing the
standalone query engine and the same persisted SQLite graph as the pipeline.
The endpoint validates WGS84 coordinates and routing parameters, returns JSON
with snap/path/duration metadata, and supports `format=geojson` for a
LineString feature. `departure`, `speed_kmh`, `include_path`, and
`include_ferries` are propagated to the existing routing semantics; the health
endpoint and doctor version 11 advertise graph availability and the route
endpoint. Server integration tests cover the JSON and GeoJSON responses.

## Dashboard route-query integration — 2026-08-17

The generated standalone and lazy dashboards now include a compact local route
form backed by `/api/route`. It accepts start/goal WGS84 coordinates, speed and
departure profiles, static-ferry/path toggles, and JSON or GeoJSON output. The
panel reports the route summary and preserves the full machine-readable result;
file mode explains why the local server is required. Browser verification
against the complete graph returned a reachable 2.34 km route and a GeoJSON
Feature with no console errors.

## Dashboard route-geometry overlay — 2026-08-17

Route results now render their normalized GeoJSON-order path directly on the
dashboard. Live Leaflet mode adds a blue polyline and fits the map to the route;
offline SVG mode switches to a focused route view with the same path. Browser
checks verified both modes against the full graph, including 158 path points,
HTTP 200 route responses, and zero console errors.

## Dashboard capability-inventory hardening — 2026-08-17

Doctor version 12 now checks the generated standalone and lazy reports for the
route-query panel, live Leaflet overlay, and offline SVG overlay independently.
This prevents the capability matrix from claiming a complete dashboard based
only on the presence of the report server and data pack.

## Resumable expert-review workspace — 2026-08-17

The generated review page now supports search and review-state filtering over
the 1,000-row queue, progress counts, evidence-source capture, automatic edit
timestamps, and browser-local autosave/resume keyed to the current queue. The
CSV export includes the complete editable annotation contract, and a clear
saved-edits control makes it possible to discard browser-local state. Doctor
version 13 inventories the page, queue size, UI module, and each review
affordance separately. Browser verification covered loading, filtering,
annotation, reload/resume, and the rendered table layout.

## Dashboard-to-review deep-link and triage upgrade — 2026-08-17

The standalone and lazy dashboards now expose the expert-review queue from the
header, every visible target row, and map popups. Links carry the target
`osm_id`; the review page prefilters and highlights that candidate and provides
a return link to `report.html`. The dashboard also filters targets by
`not_reviewed`, `supportive`, `ambiguous`, or `not_supportive`, with the
selection preserved in shareable URL state. Doctor reports the link, filter,
and generated review-page capabilities independently.

## Dashboard-to-review deep-link upgrade — 2026-08-17

The standalone and lazy dashboards now expose the expert-review queue from the
header, every visible target row, and map popups. Links carry the target
`osm_id`; the review page prefilters and highlights that candidate and provides
a return link to `report.html`. Doctor reports both the dashboard link token and
the generated review page independently. Regression coverage now checks the
generated HTML contract, and browser verification covers the deep-link path.

## Review evidence-context upgrade — 2026-08-17

The review queue now carries the candidate dossier evidence contract instead of
reducing each row to a single summary string. The generated page exposes that
context in an expandable, escaped evidence panel: NIAH identity and registry
metadata, architect attribution and confidence/evidence, reference counts and
source links, geometry/match context, validation state, heritage identifiers,
and review warnings. Search now covers the same evidence fields, while the
editable label/source/notes contract remains unchanged. Doctor now verifies the
evidence-detail and source-link UI tokens as well as the queue columns. The
regenerated queue contains 1,000 rows, including 118 NIAH names, 139 registry
numbers, 28 architect attributions, and 774 warning records. Browser verification
expanded a real NIAH-matched row (`way/632336806`) and confirmed the registry,
location, rating, date, validation, and warning details.

## Dashboard quality-audit drill-down — 2026-08-17

The dashboard now makes the quality-audit duplicate-centroid artifact
inspectable instead of reporting only a count. A collapsible table exposes each
group's centroid, OSM members, target/control counts, group labels, and review
status. Each member has a source-record link plus a local focus action that
filters and centers the dashboard on target rows. Doctor version 15 checks the
quality panel, drill-down markup, and focus-action contract independently in
both standalone and lazy reports.

## Dashboard field/source quality audit — 2026-08-17

The quality section now also exposes the full `data_quality.csv` audit: analysis
field checks and optional-source coverage rows can be filtered by scope and
status, with missing percentages, invalid counts, notes, and status badges
visible in the dashboard. A CSV download preserves the complete audit contract
for offline analysis. Doctor version 16 checks the audit table and export
controls in both standalone and lazy reports. The current cached audit has 78
rows (72 analysis checks and 6 source-coverage checks), including explicit
`not_provided` status for mapping history and LiDAR coverage.

## Review queue coverage boundary — 2026-08-17

The dashboard and review workspace now make queue membership explicit. The
current queue contains the top 1,000 of 33,416 target rows (2.99% coverage),
so dashboard filters distinguish `not_queued` from `not_reviewed`. Queued rows
retain their annotation state; unqueued rows are labeled in the table and map
popup, and an `osm_id` deep link outside the queue shows a scope notice rather
than an empty-looking review page. Doctor version 17 checks the membership
contract and the review-page notice in the generated artifacts.

## Supported local runtime rebuild — 2026-08-17

The active local environment was rebuilt from Python 3.11.15, with the prior
Python 3.9 environment preserved outside the workspace for recovery. The
declared core and developer dependencies were reinstalled from `pyproject.toml`.
The doctor now reports Python support as available with no hard runtime errors;
the remaining warning is only the intentionally dirty worktree. The full test,
Ruff, compilation, artifact verification, schema, reproducibility, and offline
pipeline checks pass under the supported runtime.

## Shareable expert-review filters — 2026-08-17

The review workspace now restores its text search and label-state filter from
`q` and `status` URL parameters and updates those parameters in place as the
reviewer works. Existing `osm_id` deep links are retained, so a filtered queue
view can be bookmarked or shared without losing target highlighting. Doctor
version 17 checks both the restore and `history.replaceState` affordances.

## Verifier-enforced review artifact contract — 2026-08-17

The independent verifier now checks more than file presence and JavaScript
syntax for the review workflow. It rejects duplicate or non-target queue IDs,
requires `report_data.json` queue membership to match `review_queue.csv`,
recomputes the reported queue coverage percentage, and checks the standalone
and lazy dashboard tokens for explicit unqueued handling. It also requires the
review page's out-of-queue notice and shareable `q`/`status` URL-state contract.
The regenerated verification record passes for 123,810 analysis rows and
33,416 targets.

## Optional columnar engines enabled — 2026-08-17

The supported Python 3.11 environment now installs the declared `data` extra:
`pyarrow 25.0.1` and `duckdb 1.5.5`. The incremental columnar stage regenerated
JSONL, Parquet, and DuckDB exports for all 123,810 analysis rows. Direct
readability checks and the independent verifier agree on the row count, and
Doctor now reports both optional engines and their generated artifacts as
available; LiDAR/DSM and OSM-history inputs remain genuinely not provided.

## Analysis export query CLI — 2026-08-17

The project now includes `ireland-geometry-query`, a bounded read-only query
surface over the generated DuckDB, Parquet, CSV, and JSONL exports. It prefers
DuckDB, supports exact OSM/group and target/control filters plus score
thresholds, defaults to 100 rows, and emits JSON, JSONL, or CSV without
materializing a new project artifact. The command is covered by focused tests,
an editable-install console entry point, and Doctor version 18 capability
inventory.

## Accessible dashboard and review controls — 2026-08-18

The generated dashboard now exposes semantic sort buttons with announced
ordering, keyboard-focusable result rows activated by Enter or Space, labeled
filters, and polite route/pagination status. The review workspace now labels
each candidate editor, names its queue controls, captions the results table,
and announces save/progress state. A delegated dashboard keyboard handler
keeps sorting functional after generated DOM updates. The verifier, Doctor,
and regression tests enforce the accessibility tokens in both standalone and
lazy reports; browser checks confirmed sort URL state and review editor/live
region semantics.

## Local geo3d capability upgrade — 2026-08-18

The active Python 3.11 environment now installs the declared `geo3d` extra:
`rasterio 1.4.4` and `laspy 2.7.0`. Doctor version 19 exposes normalized,
GeoTIFF DSM/DTM, and geographic LAS/LAZ adapter readiness separately. The
optional GeoTIFF centroid-window test and a new synthetic geographic LAS
point-in-footprint aggregation test both pass; the full supported suite now
passes 94 tests. The current snapshot still has no LiDAR source file, so
coverage remains explicitly `not_provided` while the ingestion path is locally
verified and ready for data.

## Portable review annotation backups — 2026-08-18

The expert-review page now supports versioned JSON backup and import in addition
to the existing CSV export. Imports match by `osm_id`, preserve label, reviewer,
timestamp, confidence, evidence-source, and notes fields, ignore rows outside
the current queue, and announce the imported count through the existing live
status region. The backup contract is now queue-bound: unsupported schemas,
different queue identities, duplicate IDs, and malformed label records are
rejected, while out-of-queue IDs are reported as ignored. Doctor, the
independent verifier, and regression tests enforce the export/import and
accessible file-input contracts; the regenerated page also passes JavaScript
syntax and browser interaction smoke checks.

## Local analysis query API — 2026-08-18

The localhost report server now exposes a bounded read-only `GET /api/query`
endpoint. It reuses the query CLI’s DuckDB/Parquet/CSV/JSONL selection and
filter semantics, caps requests at 1,000 rows, returns backend/filter metadata,
and advertises availability through `/__health` and Doctor. Integration tests
cover a real CSV-backed query, target/group/score filters, invalid-limit
rejection, and coexistence with the existing route API; a smoke test against
the current DuckDB snapshot returned live worship target rows successfully.

## Paginated local analysis queries — 2026-08-18

The shared query engine now applies a deterministic `offset` after score/OSM
ordering for DuckDB, Parquet, CSV, and JSONL, while preserving the CLI’s
`limit=0` all-matches behavior. The local `/api/query` endpoint exposes the
same page contract with `offset`, `has_more`, and `next_offset`, caps offsets
at 10,000,000, and fetches one look-ahead row to determine continuation. Doctor
and the regression suite now advertise and verify pagination across the query
surfaces.

## Accurate query capability health — 2026-08-18

The server health endpoint and Doctor now determine query availability from any
usable export backend rather than assuming CSV is present. This preserves the
JSONL-only fallback contract for reduced or portable output directories, with a
dedicated health regression test covering that mode.

## Diagnostic artifact provenance — 2026-08-18

`doctor.json` is now explicitly classified as a timestamped diagnostic rather
than a stable pipeline artifact. The manifest, verifier, and reproducibility
check all preserve the file for inspection while excluding it from hash and
completeness contracts, so running Doctor after a build no longer creates a
stale-manifest verification failure. Doctor version 23 records the updated
contract.

## Contract-driven capability inventory — 2026-08-18

Doctor version 24 now derives report-server route/query flags, gzip support,
query pagination limits, and standalone route-query feature flags from the
implementation contracts it inspects. Partial installations are reported as
`incomplete` instead of advertising every feature merely because a module file
exists; the complete project continues to report all supported capabilities.

## Query threshold validation — 2026-08-18

The shared query engine now rejects non-finite `min_score` values before backend
selection. The CLI, HTTP API, DuckDB, Parquet, CSV, and JSONL paths therefore
share the same finite-threshold contract instead of silently treating `NaN` or
infinity as an empty result.

Doctor version 25 and the local health response now advertise the finite-score
contract explicitly alongside pagination and backend availability.

## Provenance-aware reproducibility comparison — 2026-08-18

`repro_check.py` now records normalized manifest context and compares it when a
reference output directory is supplied. Source paths are normalized away while
source hashes, schema version, relevant parameters, and Git revision remain
strict comparison inputs. The pipeline writes a pre-check manifest so the
current run’s context is available to the reproducibility stage; mismatched
context is reported separately from deterministic artifact-byte differences.
Manifest sources now preserve their semantic `source_kind` alongside the
physical `file`/`directory` kind, so provenance comparisons retain the role of
each input as well as its hash.

## Local health readiness contract — 2026-08-18

The report server's `/__health` response now distinguishes `ready: true` from
`status: "degraded"` when a lazy report is missing its required data pack.
Embedded standalone reports correctly remain ready without `report_data.json`.
The response exposes whether the data pack is required and whether gzip is
actually available, while Doctor version 28 checks that the server exposes the
same readiness contract. The full supported suite now passes 107 tests.

## Fail-fast CLI parameter contract — 2026-08-18

The pipeline now rejects non-positive Monte Carlo and bootstrap counts,
non-finite or out-of-range holdout fractions, and empty stage selections before
creating directories or launching a stage. The review CLI likewise rejects a
non-positive queue size instead of silently clamping it. Doctor version 29
exposes the pipeline parameter-validation capability; regression coverage now
checks the failure timing and messages. The full supported suite now passes 111
tests.

## Review-label input contract — 2026-08-18

The pipeline-side `--review-labels` CSV import now validates required
`osm_id`/`label` fields, rejects blank IDs, duplicate IDs, and unknown labels,
and normalizes surrounding whitespace. Labels outside the current queue remain
valid but are reported as ignored, matching the browser JSON-backup behavior.
Doctor version 27 exposes the label-input validation capability, and the
unreachable legacy review-page template was removed so the pipeline has one
canonical UI implementation. The full supported suite now passes 105 tests.

## Query capability contract tightening — 2026-08-18

Before version 26, Doctor inferred query pagination and finite-score support from the
HTTP wrapper alone. Its capability matrix now checks the shared
`query_data.py` implementation for the offset and finite-`min_score` guards as
well, and the report-server status requires both layers before advertising the
contract. Regression coverage confirms that a present but incomplete query
module is reported as `incomplete` rather than available. The full supported
suite now passes 102 tests. Doctor also exposes the bounded portable-fallback
query capability introduced in this tranche.

## Self-contained wheel schema contract — 2026-08-18

The tracked `schemas/artifacts.json` registry is now a packaged `schemas`
resource, so wheel installs retain the schema input required by the
`schema-audit` stage and incremental cache. Doctor version 30 reports the
registry, and the CI package-smoke job builds a wheel, verifies the packaged
resource, and runs all five installed console entry points through `--help`.
The source test suite now includes a package-resource regression check and
passes 113 tests.

## Machine-readable dry-run contract — 2026-08-18

The pipeline now accepts `--dry-run-json` and emits the versioned
`ireland-geometry.dry-run.v1` contract. It reports cache decisions, commands,
declared inputs, expected outputs, runtime paths, and the no-write guarantee
without requiring callers to parse console text. Doctor version 31 inventories
the capability, the package-smoke job exercises it from the installed wheel,
and regression coverage now passes 114 tests.

## Portable installed-project roots — 2026-08-18

Runtime path resolution now separates the packaged code root from the user
project root. Editable/source execution retains repository-relative defaults;
non-editable wheels use `IRELAND_GEOMETRY_PROJECT_ROOT` or the caller's current
directory, and `ireland-geometry --project-root` provides an explicit override.
Doctor version 32 reports both roots, routes capability inspection to the
package resources, and scans data/output under the user project. Installed
wheel integration coverage now exercises relative project paths end to end.

## Local metadata API contract — 2026-08-18

The report server now exposes `GET /api/metadata`, returning compact manifest,
verification, schema-validation, and columnar-export summaries with links to
the full local records. Health advertises the endpoint and whether a manifest
is present; Doctor version 33 requires the implementation contract before
reporting the server as complete. Server regression coverage exercises the
real HTTP response, and the full suite now passes 118 tests.

## Diagnostic manifest-context preservation — 2026-08-18

Diagnostic-only pipeline reruns no longer overwrite the analytical manifest
context with partial-stage parameters. Existing sources and analytical
parameters are preserved while refreshed artifact hashes remain current; the
new invocation is retained under `last_invocation`. Doctor version 34 exposes
the contract, and regression coverage now passes 119 tests.

## Package identity contract — 2026-08-18

The declared `0.4.0` distribution version is now resolved through one shared
runtime helper. All five console commands expose `--version`, and the same
identity is included in the dry-run JSON contract, generated manifest, Doctor
JSON/report, and local report-server metadata API. Wheel smoke coverage checks
the installed command versions, while regression coverage now passes 124 tests.

## Explicit project-root manifest identity — 2026-08-18

Manifest creation now accepts the resolved project root from the pipeline
controller instead of implicitly using the runtime module's import-time root.
This keeps `manifest.json` and its Git identity aligned with `--project-root`
in portable wheel and external-project runs. Regression coverage exercises the
override through both the runtime helper and a diagnostic pipeline invocation;
the full suite now passes 125 tests.

## Runtime provenance contract — 2026-08-18

The Python/platform/dependency signature already used by incremental stage
cache invalidation is now promoted into the generated manifest, Doctor JSON,
reproducibility context, and local metadata API. Older manifests without this
field remain readable; new comparisons detect runtime identity changes. Doctor
version 36 exposes the contract, and the source suite covers runtime mismatch
detection alongside the existing artifact checks. The independent verifier
validates the provenance shape while retaining legacy-manifest compatibility;
the full suite now passes 128 tests.

## Shared execution-module cache invalidation — 2026-08-18

Stage fingerprints now include hashes of both `scripts/runtime.py` and the
cache engine itself, in addition to the stage script, controller, inputs, and
runtime signature. Changes to shared path, hashing, or cache semantics now
force affected stages to be reevaluated instead of silently reusing old
records. Regression coverage confirms that changing the shared runtime module
changes the stage fingerprint; the full suite now passes 129 tests.

## Complete export count provenance — 2026-08-18

Manifest construction now records canonical row counts for JSONL, Parquet, and
DuckDB scale exports and feature counts for the building GeoJSON, using the
analysis CSV as the shared row source and the GeoJSON FeatureCollection for
feature counts. The existing manifest hash/byte/row contract and columnar
verifier checks cover the new fields. The full suite now passes 130 tests.

## Source-version portability — 2026-08-18

The shared version helper now reads the local `[project].version` from
`pyproject.toml` during direct source execution before consulting installed
distribution metadata. This prevents an uninstalled checkout from reporting
`0+unknown` or accidentally inheriting an unrelated globally installed
distribution version. Regression coverage passes in the full 131-test suite.

## Conditional report-server caching — 2026-08-18

The localhost report server now emits deterministic representation ETags for
gzip `report_data.json` responses and honors `If-None-Match` with a bodyless
`304 Not Modified`. The compressed bytes and their ETag share the existing
change-aware in-memory cache, so an unchanged 115 MB source pack is not
recompressed or retransmitted on repeat browser/automation requests. The
compact `/api/metadata` response uses the same ETag revalidation contract with
`Cache-Control: no-cache`; dynamic health, query, and route responses remain
`no-store`. Doctor version 37 now reports conditional data-pack caching
independently, and HTTP regression coverage checks cache hits and
invalidation after the report pack changes.

## Cursor-paginated query upgrade — 2026-08-18

The shared analysis query engine and local `/api/query` endpoint now support a
score/OSM-ID cursor (`after_score` plus `after_osm_id`) in addition to the
existing offset pages. The cursor uses the same descending score, ascending OSM
ordering across DuckDB, Parquet, CSV, and JSONL, rejects partial/non-finite
cursors, and cannot be combined with an offset. CSV/JSONL fallback pages now
retain only `limit + 1` post-cursor rows rather than `offset + limit` rows,
which keeps deep automation pages bounded. HTTP responses return `next_cursor`
and suppress offset continuation when a cursor was requested. Doctor version 39
now requires and reports the cursor contract; regression coverage exercises tied
scores, both optional columnar backends, HTTP continuation, and invalid input.
The full source suite now passes 132 tests.

## Dependency-aware stage cache — 2026-08-18

Stage-cache version 3 now follows each stage script's transitive local Python
import closure and includes those module hashes in the fingerprint. Changes to
shared analytical helpers such as `geometry.py`, `stats.py`, `sensitivity.py`,
or `review_ui.py` can no longer leave stale stage outputs reusable merely
because the stage entry script itself was unchanged. The closure is resolved
from the project containing the stage script, so installed-package execution
still fingerprints an external project's code correctly. Operational-only
modules such as the report server and query CLI are not imported by analytical
stages and therefore do not cause unrelated analytical cache churn. Regression
coverage checks helper invalidation and the analyze-stage closure; the existing
runtime/platform and output-hash checks remain in force.
The full source suite now passes 135 tests.

## Strict query JSON serialization — 2026-08-18

The query CLI and local HTTP server now sanitize non-finite row numbers before
JSON serialization and use `allow_nan=False`. Malformed or legacy JSONL rows
with `NaN` or infinity therefore return standards-compliant `null` values rather
than JavaScript-incompatible tokens; CSV output remains unchanged. Regression
coverage exercises both CLI JSON/JSONL writers and the real JSONL-backed HTTP
query endpoint.

## Portable manifest path contract — 2026-08-18

Manifest construction now preserves the existing absolute `path` values while
adding `path_contract` version 1. Output artifacts carry a path relative to the
output directory, and in-project sources carry a path relative to the project
root; external sources are explicitly marked instead of being presented as
portable. Data/output root relative references are recorded as well. The
verifier resolves a moved artifact through its relative reference when the
legacy absolute path is no longer present, while remaining compatible with
older manifests. The metadata API exposes the contract and portable/external
path counts. Relocation regression coverage was added to the runtime,
verifier, and server tests.
Doctor version 40 now reports whether the manifest is legacy or relocation
aware, along with portable artifact/source and external-source counts. The
full source suite passes 137 tests.

## Explainable incremental cache — 2026-08-18

Stage-cache version 4 now persists each stage's exact fingerprint payload,
including command, input hashes, runtime, controller/code hashes, and local
import-closure hashes. The shared reuse check now returns stable diagnostic
reasons for cache hits, missing/version-mismatched caches, fingerprint drift,
missing outputs, stale optional exports, and output-hash changes. The
machine-readable dry-run includes the fingerprint, cache explanation, cache
version, and no-write contract, while the verifier recomputes each persisted
fingerprint to detect tampered or internally inconsistent cache records.
Doctor version 41 reports the explainable-cache capability, and CI package
smoke asserts the new dry-run fields. The focused cache/CLI/Doctor/verifier
regressions pass; the full source suite now passes 140 tests.

## Invalid-score cursor continuation — 2026-08-18

The shared query engine now exposes an OSM-ID-only cursor for rows whose
optional score is malformed or non-finite. Finite-score rows retain the
existing score/OSM-ID cursor, while the invalid-score tail can now be resumed
without offset pagination. The contract is implemented consistently in
DuckDB, Parquet, CSV, and JSONL backends, the CLI, and `/api/query`; strict JSON
output still renders malformed numeric values as `null`. Doctor version 42 and
the package smoke check report and exercise the added capability, with
regression coverage for fallback, columnar, HTTP, and invalid-input paths.
The full source suite now passes 141 tests.

## Automatic query-backend failover — 2026-08-18

Automatic query selection now tries the preferred DuckDB/Parquet/CSV/JSONL
backend in order but advances to the next available export when the preferred
file cannot be queried. Explicit `--backend` requests remain strict, so a
caller can still detect a backend-specific failure. The HTTP health payload and
Doctor capability matrix expose the failover contract, and regression coverage
uses an unreadable preferred DuckDB file with a valid CSV fallback. Doctor now
performs cheap per-backend readability probes, preserving file-presence details
while distinguishing corrupt exports from usable ones. The full source suite
distinguishes corrupt exports from usable ones. CLI and HTTP callers also
receive the fallback reason when a preferred export is skipped. The full source
suite then passed 142 tests.

## Probe-aware report health — 2026-08-18

The local report server health endpoint now uses the same lightweight backend
readability probes as Doctor. `query_available` is true only when at least one
DuckDB, Parquet, CSV, or JSONL export is readable; the response also exposes
`query_readable_backends` and `query_backend_health` so corrupt or malformed
exports are distinguishable from missing optional formats. Regression coverage
checks both readable JSONL and invalid-only query directories. The full source
suite now passes 143 tests.

## Probe-aware metadata exports — 2026-08-18

The compact `/api/metadata` response now uses the shared query-backend probes
instead of trusting `columnar_status.json` alone. Every DuckDB, Parquet, CSV,
and JSONL entry reports declared status, file availability, runtime readability,
and any probe error, with a `readable_backends` summary. This keeps metadata,
Doctor, health, and query fallback decisions aligned when an optional export is
missing or corrupt.

## Versioned local API capability discovery — 2026-08-18

The report server now exposes `GET /api/capabilities`, a cacheable discovery
document that reports report readiness, package version, endpoint paths, query
limits/cursors, route availability, and per-backend readability in one stable
response. Health, query, metadata, and route JSON responses now carry explicit
v1 contract identifiers, while the capability response exposes the canonical
contract map and route GeoJSON carries the route contract as a GeoJSON foreign
member as well as a feature property. Doctor version 43 and the packaged CI
smoke test verify the new endpoint and constants; the full source suite now
passes 144 tests.

## Deterministic portable artifact bundles — 2026-08-18

The existing manifest relative-path contract is now operational through the
standalone `ireland-geometry-bundle` command. It packages regular generated
outputs into a relocatable ZIP, writes a `bundle.json` with the
`ireland-geometry.bundle.v1` contract and per-artifact SHA-256/byte metadata,
uses fixed ZIP timestamps for byte-identical reruns, skips workspace-only Doctor
and stage-cache files by default, supports additional exclusion globs, and can
verify every member hash in place without extraction. A verified archive can
also be safely extracted into a new destination without permitting traversal
paths or overwriting an existing directory. The optional `--require-verified`
guard refuses to publish a bundle without a passing verification record and
records manifest/verification hashes and summary counts in `bundle.json`.
Doctor version 44, CI, and the packaged wheel smoke test cover the command; the
full source suite now passes 156 tests.

## Packaged console-command inventory — 2026-08-18

Doctor now parses the `[project.scripts]` table from `pyproject.toml`, reports
all six installed console commands and their module/entrypoint targets, and
marks the capability incomplete when a declared target is missing. CI exercises
the bundle command through both `--help` and `--version`, and package-smoke
asserts the six-command inventory so new entry points cannot silently drift out
of release checks. The full source suite now passes 157 tests.

## Verified bundle provenance guard — 2026-08-18

The portable bundle contract now treats its source manifest and verification
record as structured provenance rather than unvalidated annotations. New
bundles record whether each source exists and is included, its SHA-256, the
manifest path contract, verification status, and available row counts. The
`--require-verified` publication gate requires both source files and a passing
verification record, and refuses exclusion of either source. The independent
verifier accepts legacy v1 bundles without these fields but strictly checks new
records for expected paths, inclusion symmetry, hash agreement, status/boolean
agreement, non-negative counts, and passing-gate consistency. Regression
coverage now includes missing-manifest and tampered-provenance cases.
Doctor remains version 45; the current real output produces two byte-identical
60-artifact bundles (531,684,085 payload bytes), verifies and safely extracts
them, and the installed wheel passes the same six-command and verified-bundle
smoke tests. The full source suite now passes 161 tests.

## Versioned local API schema — 2026-08-18

The local report server now serves `/api/openapi.json`, a dependency-free
OpenAPI 3.1 document for the health, capabilities, metadata, bounded query,
and point-to-point route endpoints. The document identifies itself with
`ireland-geometry.openapi.v1`, lists the canonical response contracts,
describes query filters/cursors and route coordinate/profile parameters, and
defines JSON/GeoJSON response and error schemas. It is cacheable with the same
strong ETag behavior as the other local metadata surfaces. The capabilities
document, Doctor version 46, CI package smoke, and endpoint regression tests
all verify the schema surface; the full source suite now passes 162 tests.

## Machine-readable bundle CLI results — 2026-08-18

The `ireland-geometry-bundle` command now accepts `--json` for build, verify,
and extract modes. The JSON result mirrors the library summary and includes
the archive contract, counts, `require_verified` state, and manifest/verification
provenance records, so release automation no longer needs to parse human
console text. Doctor version 47 reports the capability, the packaged CI smoke
uses JSON build and verify results, and regression coverage exercises all three
CLI modes. The full source suite now passes 163 tests.

## Machine-readable diagnostic utilities — 2026-08-18

The standalone `schema_audit.py` and `repro_check.py` utilities now accept
`--json` and emit the exact contracts they write to
`schema_validation.json`/`reproducibility.json`. Failure status is preserved
through the JSON mode, so automation can inspect the complete result while
still receiving a non-zero exit code. Doctor version 48 reports the shared
diagnostic JSON capability; regression tests, CI help checks, and packaged
Doctor smoke cover the surface. The full source suite now passes 165 tests.

## Packaged diagnostic command surface — 2026-08-18

The two automation-ready diagnostics are now first-class wheel entry points:
`ireland-geometry-schema-audit` and `ireland-geometry-repro`. Both expose
`--version` and retain their `--json` result contracts. Doctor now inventories
eight installed commands, CI exercises their help/version paths and packaged
diagnostic JSON, and source command tests cover the expanded inventory. The
fresh wheel smoke passed; the full source suite now passes 167 tests.

## Source-distribution release smoke — 2026-08-18

The package-smoke job now invokes the setuptools PEP 517 `build_sdist` hook in
addition to building the wheel. It installs the resulting source distribution
into a fresh Python 3.11 environment outside the checkout, verifies the exact
eight console entry points and packaged schema, and runs each command's
`--version` path plus Doctor JSON. This closes the packaging gap where an
editable checkout or wheel could pass while an sdist omitted a new command or
schema resource. The local sdist install smoke passed for package version
`0.4.0`; the source suite remains at 167 passing tests.

## Complete source-distribution context — 2026-08-18

The sdist manifest also now retains the root plan and the optional LiDAR, road,
and boundary README guidance files; CI checks those archive members before the
fresh-environment install.
The installed wheel/sdist smoke is now a Python 3.10–3.12 matrix, matching the
declared support range instead of validating only the workspace's Python 3.11.
The canonical Python 3.11 artifacts are retained for 14 days by CI together
with `package-sha256sums.txt`, making the exact smoke-tested distributions
downloadable without a second build.

## Packaged default analysis plan — 2026-08-18

The default preregistered `analysis_plan.json` was previously available only in
the source checkout, leaving an installed command pointed at a new external
project root without a holdout plan. The package now carries the same plan as
`schemas/analysis_plan.json`; a project-root plan still overrides it, while
the holdout stage, incremental cache, manifest provenance, and verifier all
resolve the packaged fallback consistently. Doctor version 51 reports the
selected project/packaged source, plan identity, and SHA-256. Wheel/sdist
smoke checks now verify the resource, and the full source suite passes 171
tests.

## Metadata-driven package version smoke — 2026-08-18

The package-smoke workflow no longer embeds the current `0.4.0` release value
in its assertions. Each wheel and source-distribution environment reads the
installed `ireland-geometry-scan` version from distribution metadata and
compares all eight console commands' `--version` output with that value. The
Doctor and dry-run checks use the same source of truth, so a future version
bump only changes package metadata rather than requiring synchronized CI
literals.

## Console-wrapper installation health — 2026-08-18

Doctor now separates a declared console entry point from its installed,
runnable wrapper. The JSON capability reports `installed_count`,
`installation_status`, and each wrapper path; the human report includes the
same count. CI package smoke requires all eight wrappers, catching stale or
partial installations where distribution metadata has been updated but the
executable scripts have not.
Strict Doctor readiness now includes this installation gate, so a clean output
snapshot cannot be marked ready when its command surface is incomplete.
Doctor contract version 51 records this expanded capability surface.

## Analytical readiness API contract — 2026-08-18

The local report server now separates “the dashboard can be served” from “the
analysis artifacts passed their gates.” `__health` and `/api/capabilities`
retain their existing `ready` field and add `analysis_ready` plus a validation
summary for the provenance manifest plus `verification.json`,
`schema_validation.json`, and `reproducibility.json`. The OpenAPI document describes the shared validation
schema, and server regression coverage exercises both not-provided and all-pass
states.
The metadata endpoint now exposes the reproducibility status and link alongside
its existing verification and schema records.

## Granular cache invalidation and lightweight lazy dashboard — 2026-08-18

The next operational pass addressed the two largest remaining local-workflow
costs: unnecessary cache misses and browser downloads of the full report pack.

- Stage-cache version 5 now separates effective stage inputs from audit-only Git
  identity. Documentation/provenance-only controller edits no longer invalidate
  every analytical stage; stage fingerprints retain the exact command, stage
  code/import closure, runtime, declared inputs, and cache contract. Regression
  coverage proves Git/controller-only changes are ignored while effective
  command changes still invalidate.
- `report_lazy.html` now defaults to `/api/report/page`, loading 50 targets and
  compact static sections. Filtering, sorting, pagination, and CSV/GeoJSON
  export reuse one server-side report predicate; `?offline=1` remains an
  explicit full-pack fallback and `report.html` stays standalone.
- The server caches the parsed report pack by mtime/size and advertises the
  `ireland-geometry.report-page.v1` and `ireland-geometry.report-export.v1`
  contracts through capabilities and OpenAPI. Against the current cached
  national output, the initial page was 264,839 bytes for 50 rows versus the
  116 MB full `report_data.json`; a filtered worship page returned 10 rows and
  the CSV export was 2,013 bytes.
- Added `tests/fixtures/pipeline_smoke` and an offline subprocess test covering
  the real analysis-to-report-to-verify lifecycle with a valid tiny PBF, NIAH
  fixture, schema audit, reproducibility check, and passing verification.
- Final checks: 193 tests passed, Ruff and compileall passed, the full cached
  diagnostic lifecycle was refreshed without a national rebuild, and final
  verification passed for 123,810 analysis rows / 33,416 targets.
