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
- a complete SQLite road graph generated from 1,123,212 supported highway ways
  with access filtering applied: 961,724 routable road ways, 7,427,367 nodes,
  and 7,553,120 physical road segments; 3,938 non-conditional OSM turn
  restrictions are applied, including 181 validated via-way chains; 72
  conditional windows are stored for optional departure profiles; a separate
  ferry layer stores 1,549 segments from 77 access-allowed ways and 38 route
  relations (excluded unless `--include-ferries` is requested); the 100-pair
  sample has 97 reachable routes; the bounded CSV graph remains available as a
  portable fallback;
- 123,810 mapping-history and spatial-covariate rows, 50,468 globally unique
  no-replacement validation pairs, 37,422 deterministic holdout rows, and a
  1,000-record expert-review queue;
- a 123,810-row data-quality audit and 12 spatial block-bootstrap uncertainty
  rows;
- LiDAR is explicitly `not_provided` for this snapshot, but the active runtime
  has the declared `geo3d` extra installed (`rasterio 1.4.4`, `laspy 2.7.0`);
  normalized heights, GeoTIFF DSMs, and geographic LAS/LAZ adapters are locally
  available and covered by tests;
- a standalone `ireland-geometry-route` query command that accepts WGS84
  coordinates and returns machine-readable snap, distance, duration, arrival,
  departure-profile, and optional path/GeoJSON metadata from the persisted
  graph;
- a standalone report at `output/report.html`, a hosted lazy-data version at
  `output/report_lazy.html`, the compact `output/interpretation.json` sidecar,
  JSONL/Parquet/DuckDB analysis exports, and a provenance manifest at
  `output/manifest.json`.
- a versioned `ireland-geometry.columnar.v1` export contract: CSV, JSONL,
  Parquet, and DuckDB exports carry schema and order-independent row digests;
  the current supported build reports full cross-backend parity.

## Quick start

Use Python 3.10 or newer. The primary extractor needs the `osmium` Python
bindings (the project commonly documented as PyOsmium) and processes the PBF
locally. For a supported local environment, choose an installed interpreter
explicitly; Python 3.11 is the current workspace default and Python 3.10/3.12
are also supported.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
# Optional scalable exports:
.venv/bin/python -m pip install -e '.[data]'
# Optional GeoTIFF/LAS/LAZ LiDAR support:
.venv/bin/python -m pip install -e '.[geo3d]'

# Fully offline when the cached inputs in data/ are present:
.venv/bin/python run_pipeline.py --no-network --seed 20260816 --mc 300 --routing-max-pairs 100

# Normal run: download missing inputs, then run every stage:
.venv/bin/python run_pipeline.py

open output/report.html

# Recommended for the lazy report (serves report_data.json on the same origin):
.venv/bin/ireland-geometry-serve --open

# Query one route on the complete cached graph:
.venv/bin/ireland-geometry-route --road-graph data/roads \
  --start-lat 53.3498 --start-lon -6.2603 \
  --goal-lat 53.3438 --goal-lon -6.2672 \
  --departure 2026-08-17T08:00:00+00:00 --speed-kmh 50

# Add the traversed path as a GeoJSON LineString:
.venv/bin/ireland-geometry-route --road-graph data/roads \
  --start-lat 53.3498 --start-lon -6.2603 \
  --goal-lat 53.3438 --goal-lon -6.2672 \
  --geojson-out /tmp/ireland-route.geojson

# Create a relocatable ZIP of the generated outputs:
.venv/bin/ireland-geometry-bundle --out-dir output \
  --archive ireland-geometry.bundle.zip
```

`--no-network` fails clearly if a required cache is missing. `--refresh`
re-extracts or refreshes cached inputs; use it without `--no-network` when a
new upstream snapshot is intended. All paths are project-root-relative by
default, but `--data-root`, `--out-dir`, and `--pbf` accept absolute paths.
For a non-editable wheel install, relative paths use the caller's current
directory; pass `--project-root /path/to/project` to make the data/output
context explicit. Doctor reports both the project root and the packaged code
root, and the manifest records the explicit project root used for the build.

Useful commands:

```bash
.venv/bin/python run_pipeline.py --help
.venv/bin/python run_pipeline.py --stage analyze,niah,report --no-network
.venv/bin/python run_pipeline.py --stage all --no-network --data-root /path/to/data --out-dir /path/to/output
.venv/bin/python run_pipeline.py --stage all --no-network --lidar data/lidar/building_heights.csv
.venv/bin/python run_pipeline.py --stage all --no-network --incremental
.venv/bin/python run_pipeline.py --stage all --no-network --incremental --dry-run
.venv/bin/python run_pipeline.py --stage road-routing --road-from-pbf --routing-max-ways 100000 --routing-max-pairs 5000 --no-network
.venv/bin/python run_pipeline.py --stage road-routing --routing-include-ferries --no-network
.venv/bin/python scripts/road_routing.py --from-pbf --pbf data/raw/ireland-latest.osm.pbf --write-graph data/roads --max-ways 100000 --max-pairs 0
# Complete disk-backed graph export (0 means all supported highway ways):
.venv/bin/python scripts/road_routing.py --from-pbf --pbf data/raw/ireland-latest.osm.pbf --write-graph data/roads --graph-format sqlite --max-ways 0 --max-pairs 0
.venv/bin/python scripts/doctor.py
.venv/bin/python scripts/doctor.py --strict --max-input-age-days 30
.venv/bin/python scripts/serve_report.py --help
.venv/bin/python scripts/schema_audit.py --json --out-dir output
.venv/bin/python scripts/repro_check.py --json --out-dir output
.venv/bin/python -m pytest
.venv/bin/ruff check scripts tests run_pipeline.py
.venv/bin/python scripts/repro_check.py --out-dir output
```

Every stage is independently runnable, so an interrupted build can resume at
the last completed stage. `--lidar` accepts normalized CSV/JSON/GeoJSON, GeoTIFF
DSM/DTM, or optional LAS/LAZ input;
`--historical-references` accepts a curated CSV of independently checked
historical sources. Both are optional and their coverage is reported rather
than imputed. `--boundaries`, `--settlements`, `--osm-history`, `--road-graph`,
and `--review-labels` activate the corresponding external-source adapters.
For opt-in in-memory PBF routing, `--routing-max-ways` bounds graph extraction
and `--routing-max-pairs` bounds Dijkstra jobs. The direct routing adapter can
persist a reusable CSV graph with `--write-graph`; for a complete national
scan, use `--graph-format sqlite --max-ways 0`, which streams the graph to disk
instead of constructing a multi-million-edge Python adjacency list. Subsequent
runs consume either graph format through `--road-graph` without rebuilding it
from PBF. By default, ways tagged `access=private/no/restricted` or an
equivalent more-specific motor-vehicle restriction are excluded; pass
`--include-restricted` when that is the intended routing policy.
The installed `ireland-geometry-route` command queries arbitrary WGS84
coordinates against the persisted graph and returns JSON containing nearest
node IDs, snap distances, straight/routed distances, estimated duration, and
arrival time. Pass `--include-path` to include graph node IDs and GeoJSON-order
coordinates in the JSON, or `--geojson-out` to write a GeoJSON `LineString`.
It accepts the same `--departure` and `--speed-kmh` profile parameters, and
`--include-ferries` enables the persisted ferry geometry layer. Ferry schedules,
terminal platform semantics, and service frequency remain explicitly
unmodeled.
With `--incremental`, a stage is skipped only when its declared inputs,
parameters, code hashes, runtime signature, and expected output hashes still
match the tracked `output/stage_cache.json`; report, verification, and
reproducibility stages always run. The runtime signature includes the Python
implementation and declared core/optional data-engine versions, so changing
geospatial or columnar dependencies invalidates stale results. Optional input
files are fingerprinted only for the stages that consume them, so changing a
LiDAR, history, review, boundary, or routing source does not unnecessarily
invalidate unrelated analysis stages. The same runtime signature is now
recorded in the manifest and exposed through `/api/metadata`; remove that
cache file to force fresh cache discovery. Each stage fingerprint also hashes
the shared runtime and cache-engine modules, so changes to path, hashing, or
cache semantics cannot silently reuse an old stage. It also follows the local
Python import closure of each stage, so edits to shared analytical helpers such
as `geometry.py` or `stats.py` invalidate the stages that consume them without
making report-server or query-CLI edits rebuild analytical stages.
Stage-cache version 4 also records the exact fingerprint inputs with each
reusable stage and exposes stable dry-run reasons such as `cache_missing`,
`runtime_mismatch`, `fingerprint_inputs_missing`, `output_hash_mismatch`,
and `cache_hit`. This makes a
cache decision inspectable without rerunning the stage or reverse-engineering
the fingerprint.

Diagnostic-only reruns (`report`, `schema-audit`, `repro-check`, and `verify`)
preserve the existing analytical manifest context instead of replacing its
seed, Monte Carlo, source, or plan parameters with a partial invocation. The
new invocation is retained as `last_invocation`, and Doctor checks this
provenance-preservation contract.

The dry-run mode prints every stage command and whether incremental execution
would reuse it, without creating output files or running expensive analysis.
For automation, `--dry-run-json` emits the same plan as the versioned
`ireland-geometry.dry-run.v1` JSON contract, including stage status, commands,
declared inputs, expected outputs, the package version, and the explicit
no-write guarantee. Each cacheable stage also includes its fingerprint and a
machine-readable cache decision/reason; the plan advertises the cache
contract version and explanation support. When `report` and `verify` are
selected together, the plan also exposes the two post-validation refresh
operations that rerun the report and final verifier after validation records
exist, including their commands and outputs.
The standalone `schema_audit.py` and `repro_check.py` diagnostics also write
their JSON contracts to `schema_validation.json` and `reproducibility.json`;
pass `--json` when a caller needs the same result on stdout without parsing a
human summary. Doctor reports this diagnostic JSON capability.
The pipeline validates Monte Carlo/bootstrap counts, holdout fractions, and
stage selection before creating output directories or launching work; the
review CLI similarly validates its queue size before reading or writing
artifacts.
The standalone doctor reports supported Python/dependencies, cached required
inputs, optional-source coverage, generated outputs, pipeline stages, export
backends, dashboard modes, LiDAR/3-D adapters, validation gates,
report-serving capability, and Git state. Use
`scripts/doctor.py --json --out output/doctor.json` for a machine-readable
snapshot, or add `--strict` when a clean, fully cached, supported runtime with
a passing `output/verification.json` is required. After installation, the
equivalent console commands are `ireland-geometry`, `ireland-geometry-doctor`,
`ireland-geometry-serve`, `ireland-geometry-route`, and
`ireland-geometry-query`, `ireland-geometry-bundle`,
`ireland-geometry-schema-audit`, and `ireland-geometry-repro`; each supports
`--version` and reports the installed distribution version. Generated manifests, Doctor JSON, and the local
`/api/metadata` response carry the same package identity and runtime signature
for audit trails. Direct source-checkout execution reads the declared
`pyproject.toml` version before falling back to installed distribution metadata.
Doctor separately reports whether each declared console command has a runnable
installed wrapper (`installed_count` and `installation_status`); this catches
an environment whose package metadata is newer than its executable scripts.
Strict Doctor readiness also requires the complete installed wrapper set, in
addition to clean provenance, cached inputs, generated outputs, and passing
verification. Use `--max-input-age-days N` when freshness matters: the three
required source caches are reported with modification time and age, and strict
readiness fails if any exceeds the configured positive limit. Without that
option, freshness is still reported but does not impose an age policy.
Manifest and API source-freshness records use the versioned
`ireland-geometry.freshness.v1` contract.
Its capability matrix also reports whether the generated manifest is legacy or
supports relocation-safe relative paths, including portable artifact/source
counts and external-source counts.
The manifest also retains its historical absolute paths while adding a
versioned `path_contract` with relative artifact references (relative to the
output directory) and relative in-project source references (relative to the
project root). This lets a copied project resolve its own outputs without the
original machine path; genuinely external inputs remain explicitly marked as
`path_base: "external"`.

The schema registry is packaged with the installable distribution, so the
`schema-audit` stage and incremental cache retain their artifact contract in a
wheel or source-distribution install as well as in an editable checkout. The CI
package-smoke job builds both artifacts, installs the sdist in a fresh
supported-Python environment outside the checkout for each Python 3.10–3.12
matrix entry, and exercises every console entry point's help/version and
packaged-schema paths. Version assertions derive the expected value from the
installed distribution metadata in each environment, so a future package
version bump does not require a duplicated release literal in the workflow.
The same distribution carries the default preregistered analysis plan at
`schemas/analysis_plan.json`. A project-root `analysis_plan.json` overrides it;
otherwise the holdout stage uses the packaged plan and records its hash in
`analysis_plan_used.json`.
The source distribution also retains the root plan and optional LiDAR, road,
and boundary guidance files for users who unpack it as a project skeleton.
The canonical Python 3.11 wheel, sdist, and SHA-256 manifest are retained as
downloadable artifacts for 14 days from successful CI runs.

`ireland-geometry-serve` binds to `127.0.0.1:8000` and serves
`report_lazy.html` by default. Use `--open` to launch it in the default
browser, `--port 0` to select an available port, or `--report report.html` to
serve the self-contained dashboard. The `__health` endpoint can be used by a
smoke test or local automation: it reports `ready: false` and
`status: "degraded"` when the lazy report lacks its required data pack, while
an embedded `report.html` does not require that pack. Clients that advertise gzip receive the
115 MB report data pack compressed on the wire (the browser transparently
decompresses it); the server caches the compressed bytes until the pack
changes and returns a deterministic `ETag`, so repeat requests can revalidate
with `If-None-Match` and receive `304 Not Modified` without another download.
The metadata API uses the same ETag revalidation contract. Pass an explicit
non-local `--host` only when the output directory is intended to be reachable
by other machines. Append `?offline=1` to either
dashboard URL to force the dependency-free SVG map for air-gapped review or
fallback testing. Health also probes the DuckDB, Parquet, CSV, and JSONL
exports: `query_available` is true only when at least one backend is readable,
`query_readable_backends` lists the usable formats, and
`query_backend_health` preserves per-backend errors for diagnosis.
`ready` describes whether the selected report can be served; `analysis_ready`
is the stricter analytical gate and is true only when the provenance manifest
exists and verification, schema-validation, and reproducibility records all
pass. Both health and
capabilities responses include the per-record `validation` status so an
automation client can distinguish a live dashboard from validated artifacts.
`GET /api/interpretation` returns the compact machine-readable interpretation
sidecar, including derived findings, source-aware caveats, and the validation
gate, without requiring automation to download the large `report_data.json`
pack. It returns `404` when the sidecar has not been generated and uses the
same deterministic ETag revalidation behavior.
The same local server exposes `GET /api/route` for point-to-point routing, for
example `/api/route?start_lat=53&start_lon=-8&goal_lat=53&goal_lon=-8.002`.
It accepts optional `speed_kmh`, ISO-8601 `departure`, `include_path=1`, and
`include_ferries=1` parameters. Add `format=geojson` for a GeoJSON `Feature`
with the reconstructed route geometry. The endpoint uses `data/roads` by
default; `--data-root` or `--road-graph` can point the server at another local
graph. Ferry schedules are not modeled.
The served dashboard includes the same route form, displays the returned
distance/duration/snap metadata, and draws the returned path on either the live
Leaflet map or the dependency-free offline SVG map. It also preserves the full
optional path/GeoJSON payload. In standalone `file://` mode it remains visible
but explains that the local server is required.
The doctor capability matrix reports the route panel and both map-overlay modes
independently, so a generated dashboard can be checked without opening it.
It also exposes a bounded read-only `GET /api/query` endpoint backed by the
same DuckDB/Parquet/CSV/JSONL selection logic as `ireland-geometry-query`.
It accepts `osm_id`, `group`, `is_control`, `min_score`, `backend`, and a
1–1,000 row `limit`, plus a bounded `offset` for deterministic pagination.
For deep or repeated pages it also accepts the finite-score cursor pair
`after_score`/`after_osm_id`. Rows with malformed or non-finite optional scores
sort after finite rows and can continue with the OSM-ID-only
`after_invalid_osm_id` cursor; cursor pages retain only the requested window in
the CSV/JSONL fallback path and return `next_cursor` when another page exists.
Responses include `has_more`, offset/cursor continuation metadata, the selected
backend, filters, and rows as JSON. JSON and JSONL output normalize non-finite
row numbers to `null`, so malformed optional exports cannot produce
non-standard `NaN` or infinity tokens.
`GET /api/metadata` provides a compact build contract with manifest parameters,
row counts, source/artifact counts, verification, schema-audit, and
reproducibility status, plus usable export backends; each export backend now includes declared status,
file availability, runtime readability, and any probe error, alongside the
`readable_backends` list. Its response links to the full local JSON records.
`GET /api/capabilities` provides a single discovery document for local
automation: it reports report readiness, analytical validation readiness,
package version, endpoint paths,
versioned response contracts, query limits/cursors, route availability, and
per-backend readability. Its top-level `contracts` map is the canonical list;
the JSON surfaces identify themselves with stable
contracts: `ireland-geometry.health.v1`, `ireland-geometry.query.v1`,
`ireland-geometry.metadata.v1`, `ireland-geometry.route.v1`, and
`ireland-geometry.capabilities.v1`, plus
`ireland-geometry.interpretation.v1`. `GET /api/openapi.json` serves a
dependency-free OpenAPI 3.1 document for those read-only endpoints, including
the interpretation sidecar, query filters/cursors, route coordinate/profile
parameters, response schemas,
and the `ireland-geometry.openapi.v1` document contract. The schema endpoint
uses the same deterministic ETag revalidation behavior as the other local
metadata surfaces.
Filter and sort choices are mirrored into the URL (`q`, `group`, `score`,
`angle`, and related parameters), so a reviewed dashboard view can be copied
and reopened without losing its state.
The `Review state` filter is included in that URL state, so reviewed,
ambiguous, supportive, and not-yet-reviewed candidates can be triaged without
leaving the dashboard.
Dashboard sorting uses named buttons with announced `aria-sort` state, result
rows can be focused and activated with Enter or Space, and filters, route
status, and pagination expose accessible names or live-region updates. The
review workspace gives every editable field a candidate-specific accessible
label and announces save/progress state; the verifier and Doctor check these
contracts in both generated dashboard modes.

The generated `output/review.html` is a separate expert-review workspace. It
supports text and review-state filters, shareable `q`/`status` URL state,
browser-local autosave/resume keyed to the current queue, evidence-source
capture, edit timestamps, progress counts, and a complete `expert-labels.csv`
download. Each row also carries an
expandable dossier panel for NIAH identity, registry/date/rating context,
architect attribution, reference counts and links, validation status, and
warnings; those fields are included in text search. Use `Clear saved edits`
when a fresh queue should discard annotations held by that browser. The doctor
capability matrix reports whether the review page, evidence panel, source-link
rendering, and queue evidence columns are present. The dashboard links to the
queue from its header, table rows, and map popups; those links carry the
selected `osm_id` so the review page opens with that candidate filtered and
highlighted. The review page links back to the standalone dashboard.
Review annotations can also be downloaded as a versioned, queue-bound JSON
backup and imported into another browser session. Imports reject unsupported
schemas, backups from a different queue, duplicate IDs, and malformed label
records; valid records outside the current 1,000-row queue are reported and
ignored. CSV export remains available for spreadsheet editing and pipeline
ingestion. When a CSV is supplied through `--review-labels`, the pipeline
validates required columns, duplicate IDs, label values, and blank IDs before
building the queue; valid labels outside the current queue are reported as
ignored.
The dashboard distinguishes the 1,000-row top-candidate queue from the full
33,416-target snapshot: queued rows expose their annotation state, while
unqueued rows are labeled explicitly and deep-linked review pages explain the
queue boundary.

Optional-source readiness recognizes the adapter formats: normalized
CSV/JSON/GeoJSON/GeoTIFF/LAS/LAZ LiDAR sources, CSV/JSON OSM history, and CSV/JSON
historical or review inputs.

The columnar stage writes `columnar_status.json` under the stable
`ireland-geometry.columnar.v1` contract. It records the source schema,
normalized field kinds, an order-independent row digest, and per-backend
digests. The final verifier recomputes those digests for every readable
backend, so a Parquet, DuckDB, JSONL, or CSV export with changed values or
columns fails closed rather than passing on row count alone. When Parquet or
DuckDB is advertised as available, the verifier also checks that the file is
readable and contains the same row count; unavailable optional engines must
not leave stale artifacts behind. Doctor and the local metadata/capabilities
endpoints expose the parity status and coverage.

`ireland-geometry-query` provides bounded, read-only access to the generated
analysis exports. Automatic selection prefers DuckDB, then Parquet, then
CSV/JSONL and skips an unreadable preferred export when a valid fallback exists;
an explicit `--backend` request remains strict. The command supports
exact `--osm-id`/`--group` filters, `--is-control target|control`,
`--min-score` (finite values only), offset or finite-score/invalid-score cursor
pagination, `--limit` (default 100; `0` means all matches), and JSON, JSONL, or
CSV output.
Offset and cursor pagination are mutually exclusive; both are applied after
the deterministic score/OSM ordering, so successive pages are stable across
all backends. For example:

```bash
.venv/bin/ireland-geometry-query --group worship --min-score 60 --offset 25 --limit 25
.venv/bin/ireland-geometry-query --group worship --after-score 91 --after-osm-id way/2113323140 --limit 25
.venv/bin/ireland-geometry-query --backend jsonl --after-invalid-osm-id way/invalid-a --limit 25
.venv/bin/ireland-geometry-query --backend parquet --osm-id way/2113323140 --format csv
```

Doctor and the local server capability health now derive the offset/cursor,
finite-score, invalid-score continuation, bounded-fallback, and automatic
backend-failover guarantees from the shared query implementation as well as
the HTTP wrapper. Doctor also distinguishes export presence from lightweight
readability and reports per-backend probe errors. When automatic selection
skips a broken preferred export, the CLI reports the reason on stderr and
`/api/query` returns it in `backend_fallbacks`. A partial query module is
therefore reported as `incomplete` instead of advertising a feature that its
backend cannot enforce.

## Pipeline stages

| Stage | Purpose |
|---|---|
| `fetch` | Download/cache the Geofabrik Ireland PBF and extract OSM areas, including relations, holes, and deterministic controls. |
| `fetch-niah` | Cache and normalize the five official NIAH regional archives. |
| `analyze` | Compute footprint metrics, pattern flags, scores, and control comparisons. |
| `negative-controls` | Test conventional 60°/120° angle flags as separately corrected negative-control diagnostics. |
| `niah` | Spatially join NIAH records, run era/rating/type tests, and audit the angle band. |
| `architects` | Extract validated architect mentions and produce evidence-linked exploratory rates. |
| `sensitivity` | Match local size-aware controls and fit matched-set plus random-effects stratified sensitivity models. |
| `spatial-covariates` | Attach administrative, settlement, and deterministic mapping-density strata. |
| `osm-history` | Summarize normalized OSM edit histories and expose mapping-age/quality coverage. |
| `validation` | Match controls without replacement within observed strata and emit balance diagnostics. |
| `building-parts` | Aggregate OSM `building:part` geometry and produce a coverage-aware optional LiDAR table. |
| `historical` | Build NIAH/architect/heritage evidence records and review-ready candidate dossiers. |
| `review` | Build an annotatable expert queue and calibration/confusion artifacts. |
| `quality-audit` | Audit missingness, numeric validity, duplicates, geometry quality, and source coverage; export reviewable duplicate-centroid groups. |
| `point-pattern` | Test inter-building bearings, turns, and nearest-neighbour spacing against nulls. |
| `spatial-stats` | Produce Ripley K/L, Moran's I, and county-preserving permutation diagnostics. |
| `spatial-bootstrap` | Estimate spatial block-bootstrap intervals and direction probabilities for primary signals. |
| `roads` | Compare road and river segment bearings with church-edge bearings. |
| `road-proximity` | Compare sampled target/control centroid proximity to mapped drivable roads. |
| `road-routing` | Compute Dijkstra shortest-path distances on a supplied graph or opt-in PBF conversion. |
| `holdout` | Apply the tracked deterministic holdout split and preregistered primary tests. |
| `columnar` | Stream JSONL; write Parquet in bounded 10,000-row batches and DuckDB when optional engines are installed. |
| `schema-audit` | Validate key CSV artifacts against the tracked machine-readable registry in `schemas/artifacts.json`. |
| `report` | Build a data-driven Leaflet dashboard with filters, map layers, downloads, and methods. |
| `repro-check` | Hash stable artifacts and optionally compare two runs. |
| `verify` | Validate all final artifacts, provenance hashes, statistical fields, report tokens, and report JavaScript syntax. |

The optional `scripts/fetch_osm.py` Overpass channel and
`scripts/fetch_satellite.py` Sentinel-2 channel remain available for separate
refresh or imagery work. The main report uses live OSM and Esri basemap tiles
for visual reference; it does not silently bulk-scrape Yandex imagery. If the
Leaflet assets are unavailable, the report automatically falls back to a
dependency-free SVG map of the filtered target points and top outlines, while
retaining filters, the table, and downloads.

## Outputs

| File | Contents |
|---|---|
| `analysis_results.csv` | One row per analyzed target/control footprint, with dimensions, angles, symmetry, convexity, circularity, quality flags, and score. |
| `top_patterns.csv` | Target rows with score ≥ 55, ranked for inspection. |
| `ireland_buildings.geojson` | Target geometries and report properties. |
| `significance.csv` | Building-level target/control comparisons with Wilson intervals, risk differences, odds ratios, raw p, Holm-adjusted p, method, and verdict. |
| `negative_controls.csv` | Separately corrected conventional-angle diagnostics that test whether generic angular structure tracks target/control status. |
| `matched_controls.csv` | Deterministic local target-to-control pairs matched on geography and log footprint area. |
| `matched_control_summary.csv` | Per-group match distance, area-ratio, same-cell, and control-reuse diagnostics. |
| `matched_significance.csv` | Matched-set effects and Holm-adjusted sensitivity tests. |
| `hierarchical_model.csv` | Random-effects stratified log-odds estimates with between-stratum variance (`tau2`) and confidence intervals. |
| `spatial_covariates.csv` | County/admin fallback, settlement, mapping cell/count, and density-bin covariates. |
| `mapping_history.csv` | Per-footprint OSM version/edit-age summary, with explicit missing-source status. |
| `data_quality.csv` / `data_quality_summary.json` | Grouped missingness, range checks, duplicate geometry diagnostics, and source coverage. |
| `data_quality_duplicates.csv` | Exact duplicate-centroid groups with affected OSM IDs, target/control counts, and an explicit review flag. |
| `schema_validation.json` | Machine-readable required-column, numeric, and uniqueness audit for key artifacts. |
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
| `review_queue.csv` / `review.html` | Expert labels queue, filterable/resumable annotation page, downloadable labels, and calibration inputs. |
| `holdout_results.csv` / `analysis_plan_used.json` | Preregistered split assignments, primary holdout effects, and plan hash. |
| `analysis_results.jsonl` / `analysis_results.parquet` / `analysis.duckdb` | Scalable exports; JSONL, Parquet, and DuckDB avoid whole-analysis Python materialization. Parquet/DuckDB require the optional `data` extra. |
| `report.html` / `report_lazy.html` / `report_data.json` / `interpretation.json` | Standalone dashboard plus a lazy dashboard that fetches the data pack through the local `ireland-geometry-serve` command; both expose validation-gated, data-derived interpretation, while the compact sidecar supports automation without downloading the full pack. Optional-source readiness, duplicate-centroid drill-down, and filtered missingness/source audit tables with CSV export are included. |
| `manifest.json` | Package version, runtime signature, project root, UTC build time, Git revision, parameters, source paths, SHA-256 hashes, byte sizes, and row/feature counts for materialized exports. |
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
- The final verifier recomputes every Holm family and directional verdict, and
  checks that holdout assignments, plan hashes, sample counts, rates, and
  primary-signal totals are reproducible from the analysis CSV.
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
- Source-quality audits treat both `status` and `quality` fields consistently,
  including explicit unreadable, incomplete, and unavailable optional sources.

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
  is reported as a sampled proximity diagnostic; actual routing uses the
  complete SQLite graph for the sampled target/control pairs.
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
- Conventional-angle negative controls are intentionally reported separately;
  if they are elevated too, the result is better interpreted as generic shape,
  tagging, or mapping structure rather than golden-angle specificity.
- Sentinel-2 is optional and requires Copernicus credentials. The report's
  satellite layer is a live visual basemap, not a downloaded analytical input.
- The complete SQLite graph is a routable OSM snapshot, not a traffic-aware
  navigation network: non-conditional via-node and validated via-way
  `no_*`/`only_*` turn restrictions are applied. 72 graph-resolved conditional
  windows can be evaluated with `--departure` and `--speed-kmh`; the default
  snapshot leaves them inactive. Four conditional references are unresolved
  and one condition uses unsupported syntax. The v6 graph stores 1,549 ferry
  segments from 77 access-allowed ways and 38 route relations; use
  `--include-ferries` to include their static geometry, while schedules,
  terminal platform semantics, and service frequency are not modeled. Access
  restrictions are applied from the most specific motor-vehicle tags.
  The legacy bounded CSV graph is retained for lightweight portable diagnostics.

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

The manifest is schema version 3. It records the package version, Python/platform/
dependency runtime signature, input hashes, output hashes, byte sizes, CSV row
and materialized JSONL/Parquet/DuckDB/GeoJSON row or feature counts, seed,
Monte Carlo setting, optional-source paths, analysis-plan hash,
Git revision, and whether the working tree was dirty at build time. `verify` checks the analytical tables,
ID alignment, no-replacement uniqueness, probability ranges, schema registry,
package/runtime provenance shape, report
JavaScript, optional-source status, artifact hashes, and complete manifest
coverage. It rejects duplicate or external manifest paths and unlisted output
coverage. Its path contract preserves portable relative references alongside
the legacy absolute paths, and verification can resolve an artifact after the
project has been moved. It rejects duplicate or external manifest paths and
unlisted output files. Atomic writes make
each stage safe to rerun after interruption.
`ireland-geometry-bundle` packages those relative output artifacts into the
versioned `ireland-geometry.bundle.v1` ZIP contract. The archive contains a
machine-readable `bundle.json` with per-file SHA-256 hashes and fixed ZIP
timestamps, so two bundles made from unchanged inputs are byte-identical.
Workspace-only `doctor.json` and `stage_cache.json` files are excluded by
default; repeat `--exclude` to omit additional relative paths or globs. A
recipient can validate the archive in place without extracting it:
`.venv/bin/ireland-geometry-bundle --verify ireland-geometry.bundle.zip`.
Extraction also verifies every hash first and requires a new destination:
`.venv/bin/ireland-geometry-bundle --extract ireland-geometry.bundle.zip --destination restored-output`.
For a publication/share gate, add `--require-verified`; bundling then fails
unless the output contains both `manifest.json` and a passing
`verification.json`. The source files must remain in the archive, and their
hashes, path contract, verification status, and row counts are retained in
`bundle.json`; archive verification checks those provenance records for
consistency as well as checking every payload hash.
Add `--json` to bundle creation, verification, or extraction to receive the
same result as a machine-readable JSON document, including archive counts and
verified provenance; this avoids parsing human-oriented console text in release
automation.

## Licensing and attribution

- OpenStreetMap contributors, ODbL 1.0.
- National Inventory of Architectural Heritage / Department of Housing,
  Local Government and Heritage, CC BY 4.0.
- Sentinel-2 / Copernicus and Esri World Imagery are used according to their
  respective terms for optional visual/reference layers.
