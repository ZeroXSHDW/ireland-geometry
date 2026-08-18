# Final status

Status: complete for the current cached snapshot; the validation, 3-D/evidence,
holdout, routing, point-to-point query, scalability, review, data-quality,
spatial-bootstrap, and reproducibility tranches are included.

## Latest operational upgrades

The current cached output now includes a granular stage-cache contract and a
server-backed lazy dashboard. Stage-cache version 5 keeps Git identity as audit
metadata without making provenance-only controller edits invalidate analytical
stages. The default lazy report requests 50 target rows from
`/api/report/page`; server-side filtering, pagination, sorting, and CSV/GeoJSON
exports avoid downloading the 116 MB full pack. `?offline=1` remains available
for explicit full-pack offline review, and the standalone report is unchanged.
The analyzer also consumes the versioned
`ireland-geometry.exploratory-score.v1` plan block and publishes the normalized
weights/thresholds plus plan/configuration hashes in `output/scoring_config.json`.

The real cached snapshot was refreshed through the diagnostic lifecycle only
(no national analysis rebuild): schema audit, report generation, and final
verification all pass for 123,810 analysis rows and 33,416 targets. The initial
live page measured 264,839 bytes for 50 rows. The repository suite now passes
197 tests, with Ruff and compileall clean, and includes a tiny offline
subprocess fixture covering the generated manifest, schema, report, and
verification contracts plus scoring-plan regression tests.

## Reproducible build

```text
.venv/bin/python run_pipeline.py --stage all --no-network --seed 20260816 --mc 300 --routing-max-pairs 100
```

The build completed without network access. The exact input caches were:

- 135,173 OSM area elements from `data/combined.json`;
- 43,322 normalized NIAH records from five cached regional archives;
- the cached Geofabrik PBF at `data/raw/ireland-latest.osm.pbf`.

## Produced artifacts

- 123,810 analyzed footprints;
- 33,416 target features in `output/ireland_buildings.geojson`;
- 90,394 empirical controls;
- 9,455 OSM→NIAH joins, including source region and match mode;
- 41 NIAH significance rows;
- deterministic point-pattern output using seed `20260816` and 300 simulations;
- road/river confound comparison;
- 49 exploratory architect-rate rows and 1,114 evidence rows;
- 100,212 spatially matched target-control pairs, 12 matched sensitivity tests,
  and 12 random-effects hierarchical tests;
- 123,810 building-part/LiDAR coverage rows (246 mapped-part associations;
  LiDAR source not provided in this snapshot; normalized, GeoTIFF, and
  geographic LAS/LAZ adapters are locally available through the `geo3d` extra);
- 33,416 historical-validation rows, 1,000 candidate dossiers, 30 Ripley rows,
  5 Moran rows, 8 county permutations, and 5 road-proximity summaries;
- 123,810 spatial-covariate rows and mapping-history rows (history source not
  supplied locally);
- 50,468 strict no-replacement pairs, 12 strict significance rows, 37,422
  holdout assignments, 8 holdout results, and a 1,000-row review queue;
- a complete access-aware SQLite road graph from 1,123,212 supported highway
  ways: 961,724 routable road ways, 7,427,367 nodes, and 7,553,120 physical
  road segments after excluding 161,529 restricted ways; 3,938 non-conditional
  OSM turn restrictions are applied, including 181 validated via-way chains;
  72 conditional windows are stored for optional departure profiles; the v6
  graph also stores 1,549 ferry segments from 77 access-allowed ways and 38
  route relations, excluded unless explicitly requested; 97 of 100 sampled
  pairs are reachable, while the bounded CSV graph remains a portable fallback;
- a standalone `ireland-geometry-route` command for arbitrary WGS84
  point-to-point queries, nearest-node snap distances, estimated travel time,
  arrival timestamps, optional conditional departure profiles, and GeoJSON path
  output;
  JSONL, Parquet, and DuckDB scale exports covering all 123,810 analysis rows
  (`pyarrow 25.0.1`, `duckdb 1.5.5`), lazy report data pack, and
  CI/reproducibility checks;
- a versioned `ireland-geometry.columnar.v1` export contract with schema and
  order-independent row digests across CSV, JSONL, Parquet, and DuckDB;
  the current snapshot has full parity and the verifier recomputes it rather
  than trusting row counts alone;
- a 123,810-row data-quality audit with zero duplicate OSM IDs, 23 duplicate
  centroid locations flagged for review in both the summary and an inspectable
  duplicate-group CSV, 100% valid geometry, and 12 spatial
  block-bootstrap uncertainty rows;
- a tracked machine-readable schema registry and `schema_validation.json` audit
  covering the key analysis, matching, covariate, quality, bootstrap, and
  holdout artifacts;
- separately corrected 60°/120° conventional-angle negative-control diagnostics
  are included outside the primary golden-angle family;
- `output/report.html` interactive dashboard;
- `output/scoring_config.json` normalized scoring configuration and provenance;
- a data-derived interpretation panel in both dashboard modes, generated from
  the primary, negative-control, matched, NIAH-era, spatial, and holdout result
  rows rather than snapshot-specific prose; it also shows the analytical
  validation-gate state and explicit caveats;
- `output/interpretation.json`, a compact deterministic sidecar containing the
  same derived findings and validation gate for automation clients;
- a versioned `GET /api/interpretation` endpoint so automation can retrieve
  those findings without downloading the full `report_data.json` pack;
- `output/manifest.json` schema-version-3 provenance record with package version,
  Python/platform/dependency runtime signature, semantic input roles,
  input/output hashes, byte sizes, and row/feature counts across CSV, JSONL,
  Parquet, DuckDB, and GeoJSON exports; it retains absolute paths for legacy
  consumers and adds a versioned portable relative-path contract for copied
  projects.
- `output/verification.json` automated artifact-contract result.
- a resumable review workspace with queue search/state filters, explicit
  top-1,000 queue membership and out-of-queue deep-link notices, shareable
  search/state URL filters, expandable dossier evidence (NIAH, architect,
  references, validation, and warnings),
  evidence-source capture, edit timestamps, progress counts, and browser-local
  autosave, plus queue-bound versioned JSON backup/import (schema, queue,
  duplicate, and malformed-record guards), validated `--review-labels` CSV
  ingestion, and CSV export;
- dashboard table, map-popup, and header links that deep-link into the review
  queue by `osm_id`, with a return link to the standalone dashboard;
- a dashboard review-state filter whose selection is preserved in shareable URL
  state;
- accessible dashboard controls: named sort buttons with announced ordering,
  keyboard-focusable/activatable result rows, labeled filters, and live route
  and pagination status;
- accessible review controls: candidate-specific editor labels, named queue
  filters, a review-table caption, and polite save/progress announcements;

## Verification

```text
.venv/bin/python -m pytest                 # 189 passed (geo3d extra enabled)
.venv/bin/ruff check scripts tests run_pipeline.py
.venv/bin/python -m compileall -q scripts run_pipeline.py
.venv/bin/python run_pipeline.py --help
.venv/bin/python run_pipeline.py --stage verify --no-network
```

Additional artifact assertions passed: manifest revision/counts and source
hashes, GeoJSON parsing and ID alignment, source-region completeness, matched
ID alignment, positive p-values/adjusted p-values, shape/context schema,
historical and LiDAR coverage contracts, absence of unresolved report template
tokens, and inline report JavaScript syntax. The new history, routing, holdout,
review, columnar, quality-audit, spatial-bootstrap, and strict-match contracts
also passed. The verifier additionally recomputes Holm-adjusted p-values and
checks holdout split/plan/count reproducibility from the analysis rows.
The verifier also aligns `review_queue.csv`, `report_data.json`, and both
generated dashboard modes: queue IDs must be unique analyzed targets, every
report target must carry boolean `review.in_queue` membership, the queue count
and coverage percentage must agree, and the review page must retain its
out-of-queue notice and shareable URL-state contract.
The generated-artifact contracts also require the dashboard accessibility
tokens and delegated keyboard sort handling; browser checks confirmed the
sort URL state and the review editor labels/live regions.
The report-data contract also requires the three validation records, their
manifest gate, and structured interpretation findings/caveats, so a stale or
partially generated dashboard cannot silently present an unqualified summary.
The verifier also checks that `interpretation.json` matches the report pack's
structured interpretation, summary counts, and validation record.
It also recomputes the columnar export parity contract for every readable
backend, including normalized schema and row-value digests; a changed export
cannot pass merely because its row count is unchanged.
When `report` and `verify` are selected together, the pipeline refreshes and
re-verifies the final report pack after the validation records are written, so
new builds cannot retain a stale `not_provided` interpretation gate.
The dry-run JSON contract now explicitly lists that post-validation report and
verifier refresh, so automation sees the same four-operation lifecycle that
execution performs.

## Compact interpretation API

The local report server now exposes `/api/interpretation` under the stable
`ireland-geometry.interpretation.v1` contract. The response is backed by the
small deterministic `output/interpretation.json` artifact and includes the
derived headline, findings, caveats, summary counts, and validation gate.
Health and capabilities advertise the endpoint and sidecar presence; the
OpenAPI document describes its 200/404/500 responses; Doctor, CI, and package
smoke verify the endpoint contract. The sidecar is included in the manifest,
reproducibility comparison, verifier, and deterministic bundle.

## Interpretation

In this snapshot, golden-ratio aspect matching is background relative to the
ordinary-building controls. The 137.5° footprint-angle flag is elevated in
target groups, including worship, but that result is an association in mapped
geometry and cannot establish design intent. NIAH era-matched tests show the
strongest worship differences in the 19th and 20th centuries after family
correction. Between-building tests do not support a golden-specific alignment;
the broad peak is near 44°, spiral turns do not support the hypothesis, and
architect attribution is non-significant. The local size/geography-matched
worship comparison remains elevated for the golden-angle flag (+4.97 percentage
points; Holm-adjusted signal), while matched golden-ratio aspect matching is
background. The strict no-replacement match is a separate sensitivity result,
with reduced coverage recorded by group; it is not an independent sample.
County-preserving golden-angle permutations are suggestive for worship, but
Moran's I is not significant for worship after correction. Road proximity is
 reported as a sampled nearest-road diagnostic; actual routing is now available
 on the complete access-aware SQLite graph for the sampled pairs, with OSM
 one-way plus via-node/via-way turn semantics and explicit unreachable pairs
 retained in the output.

The spatial block bootstrap remains directionally consistent with the main
golden-angle elevation, while making the spatial unit of resampling explicit;
it is an uncertainty diagnostic, not a causal estimate.

The separately corrected conventional-angle negative controls are also elevated
for several target groups, especially 120° and government/worship 60° rows.
That makes the golden-angle result less specific to a single geometry feature
and reinforces the interpretation as a mapped-footprint association rather
than evidence of intentional design.

## Remaining limitations

OSM geometry is community-mapped and is not an architectural plan. NIAH is a
heritage inventory for the Republic of Ireland rather than a complete census,
so Northern Ireland coverage is sparse. Only 246 analyzed footprints have
mapped `building:part` associations and no LiDAR or OSM history file was
supplied. The LiDAR/3-D adapters are installed and tested, but they cannot add
coverage until a source file is supplied. Boundary, settlement, and expert
labels are optional and absent in this snapshot. The complete road graph is an
OSM routing snapshot rather than a traffic-aware navigation network:
3,938 non-conditional via-node/via-way
restrictions are applied, and 72 graph-resolved conditional windows can be
evaluated with an explicit departure profile. Four conditional references are
unresolved and one uses unsupported syntax; the default snapshot leaves them
inactive. Ferry schedules are not modeled. Access restrictions are applied
from the most specific motor-vehicle tags. Ferry geometry is opt-in and
schedule-free; terminal platform semantics and service frequency are not
modeled.
Candidate thresholds were chosen for screening,
and a confirmatory study should populate those source contracts, review the
holdout plan, validate against independent plan or LiDAR data, and manually
check the candidate dossiers.

The operational upgrade also adds a no-write dry-run planner and a standalone
doctor. The planner also emits the versioned `ireland-geometry.dry-run.v1`
JSON contract for automation. The active `.venv` now uses Python 3.11.15, which is inside the
declared Python >=3.10 support range; the doctor reports no hard runtime
errors. Strict readiness is currently true for the clean cached checkout; it
becomes false when the source tree is dirty, required inputs exceed a configured
freshness limit, or validation/output requirements are not met.
Pipeline and review CLI parameters now fail fast for invalid counts, fractions,
empty stage selections, and queue sizes before any output directory or stage
work is created.
The current `0.4.0` package identity is exposed through `--version` on all
eight console commands and is carried into the dry-run JSON, output manifest,
Doctor report, and local metadata API. Package-smoke derives the expected
version from installed distribution metadata in each Python 3.10–3.12 job and
checks all eight commands, so future version bumps do not require duplicated
release literals in CI.
Doctor now reports both declared entry points and runnable installed wrappers,
so a stale editable environment cannot appear fully packaged merely because
its metadata lists the newer commands. Strict readiness now requires all eight
wrappers as well as the clean worktree, cached inputs, generated outputs, and
passing verification.
Source-checkout execution resolves that identity from the local
`pyproject.toml`, while wheel installs resolve it from distribution metadata.
Manifest project identity also follows an explicit `--project-root` override,
so portable external-project builds record the same root used for their data
and output paths.
The runtime signature used by incremental caching is also preserved in the
manifest, Doctor JSON, reproducibility context, and metadata API.
The independent verifier validates the package/runtime provenance shape and
reports legacy manifests as warnings rather than rejecting them outright.
Stage fingerprints now include the shared runtime and cache-engine module
hashes plus each stage's local Python import closure, closing the invalidation
boundary for shared execution logic without making operational-only modules
invalidate analytical stages.
The manifest now also records 123,810 rows for each scale export and 33,416
GeoJSON features, rather than exposing byte sizes alone.

The current supported-runtime rebuild was executed with Python 3.11.15 using
the cached inputs and seed `20260816`. It completed all stages offline with
100% valid geometry, 50,466 strict pairs, 1,708 top-pattern rows, a passing
schema audit, 54 stable reproducibility artifacts, and a passing final
verifier.
Reproducibility comparisons now also validate normalized source hashes,
analysis parameters, schema version, and Git revision, while ignoring absolute
machine-specific source paths.
The incremental cache is now runtime-aware: stage-cache version 5 records the
Python/platform signature and declared core/optional data-engine versions,
invalidating stale results when that environment changes.
Each stage record now retains the exact fingerprint payload used for the cache
key, and the no-write dry-run exposes the resulting fingerprint and stable
cache reason. Fingerprints use the versioned
`ireland-geometry.stage-cache.fingerprint.v1` contract; Git revision remains
audit metadata, while provenance-only controller edits do not invalidate
analytical stages. The verifier checks that persisted fingerprint inputs
reproduce their recorded hash; Doctor reports the explainable-cache capability.
The default order also runs the routing stage before the quality audit, so the
audit's routing coverage reflects the current routing artifact.
The routing adapter now supports a disk-backed SQLite v6 graph for complete PBF
conversion (`--graph-format sqlite --max-ways 0`) in addition to the bounded
CSV/in-memory path. Its indexed nearest-node lookup and SQLite Dijkstra avoid
constructing a multi-million-edge Python adjacency list. The current snapshot
contains 1,123,212 scanned highway ways, 961,724 routable road ways, 7,427,367
nodes, and 7,553,120 physical road segments after excluding 161,529 restricted
ways; it applies 3,938 non-conditional via-node/via-way turn restrictions and
stores 72 graph-resolved conditional windows plus 1,549 opt-in ferry segments
from 77 ways and 38 relations. The doctor reports the backend, counts, access
policy, conditional/ferry coverage, restriction coverage, and completeness
flag alongside the other capabilities.
The standalone route query command accepts arbitrary WGS84 coordinates against
the persisted graph and returns JSON with both snap distances and an estimated
arrival time. With `--include-path` or `--geojson-out`, it reconstructs the
traversed graph path; `--include-ferries` enables the static ferry layer. It
uses the same turn-restriction and conditional-profile engine as the sampled
pipeline routes.
The final verifier now checks optional Parquet/DuckDB readability and row-count
alignment, while rejecting stale files for unavailable or failed backends.
The standalone and lazy reports now expose explicit readiness for optional
sources and columnar exports alongside their coverage counts.
The columnar stage streams JSONL and writes Parquet in 10,000-row batches; the
current supported environment has all three scale exports available, with
Parquet produced by `pyarrow 25.0.1` and DuckDB produced by `duckdb 1.5.5`.
The lazy dashboard now has the standard-library `ireland-geometry-serve`
command, which serves its HTML/data pack on localhost and exposes a `/__health`
smoke-test endpoint with explicit `ready`/`degraded` state; lazy readiness
requires the data pack while the embedded report does not. The current
generated report was served successfully. The
server compresses the data pack from roughly 115 MB to 8.3 MB for gzip-capable
clients, and the dashboard now has a dependency-free SVG map fallback when
Leaflet or basemap assets are unavailable. Gzip data-pack responses now carry
deterministic ETags and honor `If-None-Match`, while `/api/metadata` supports
the same 304 revalidation path; Doctor reports this conditional-cache
capability separately.
The same server now exposes a local `GET /api/route` endpoint that reuses the
SQLite routing graph for JSON or GeoJSON responses, including snap metadata,
departure/arrival estimates, optional path reconstruction, and explicit
`include_ferries=1` support. The health response and doctor capability matrix
advertise the endpoint and whether its graph is available.
The server also exposes a bounded `GET /api/query` endpoint using the same
DuckDB/Parquet/CSV/JSONL backend selection as the query CLI. It enforces a
1–1,000 row limit, supports a bounded deterministic `offset`, and returns
`has_more`/`next_offset` page metadata together with backend, filter, and row
metadata for local automation. It now also supports finite-score score/OSM-ID
cursors and an OSM-ID-only cursor for the malformed/non-finite score tail;
fallback backends can advance deep pages without retaining the entire offset
window, and responses expose `next_cursor` continuation metadata.
The CLI and HTTP JSON serializers normalize non-finite values to `null`, keeping
malformed optional rows standards-compliant instead of emitting `NaN` tokens.
The dashboard now exposes that route capability directly through a local route
form with coordinate, speed, departure, path, ferry, JSON, and GeoJSON options;
the full-graph browser smoke test completed successfully without console
errors.
When a path is returned, the dashboard overlays it on the live Leaflet map and
focuses the offline SVG fallback on the route, so the geometry is inspectable in
both dependency modes.
Doctor version 43 now inventories the route panel, paginated query, metadata, and
capabilities APIs,
live/offline overlay, and packaged schema registry separately from the server
API capability.
It also reports the generated expert-review page, queue size, and its
search/filter, local-resume, evidence-source, label-export, dossier-evidence,
and source-link affordances, including the queue evidence-column contract.
Dashboard/report capabilities now also expose the review link and generated
review-page presence, plus the review-state filter.
They also expose the accessibility upgrade in the Doctor matrix: both report
modes must contain semantic sorting, keyboard row activation, labeled filters,
and the generated review page must contain labeled editors and live regions.
The review capability matrix also checks the queue-bound JSON backup contract:
schema and queue identity guards, duplicate-ID rejection, and malformed-record
rejection are present in the generated page.
It also checks the pipeline-side review-label validator, which rejects malformed
CSV rows and reports valid labels outside the current queue instead of silently
overwriting or hiding them.
The doctor capability matrix now inventories all 26 pipeline stages, cacheable
versus always-run behavior, report modes, export backends, and independent
validation gates. Dashboard filters and sorting are persisted in URL parameters
so a reviewed state can be reopened or shared, including the explicit
dependency-free `offline=1` mode.
The `ireland-geometry-query` command provides bounded read-only queries over
DuckDB, Parquet, CSV, or JSONL exports, with exact OSM/group filters, target or
control selection, finite score thresholds, deterministic score ordering, offset
pagination, finite-score cursors, invalid-score continuation cursors, automatic
fallback from unreadable preferred exports, and JSON, JSONL, or CSV output.
Doctor reports the command, pagination/threshold, cursor, bounded-fallback,
and automatic-failover contracts only when the shared query implementation and
local HTTP wrapper both expose those guarantees. It also reports lightweight
per-backend readability probes and their errors, rather than treating file
presence alone as query readiness.
Automatic query fallback diagnostics are retained on CLI stderr and in the
HTTP `backend_fallbacks` response field. Report-server health now uses the
same readability probes: `query_available` is false when all exports are
unreadable, with `query_readable_backends` and `query_backend_health` exposed
for diagnosis. `/api/metadata` now carries the same `available`, `readable`,
and `error` fields for every export backend, even when a columnar status record
declares an artifact available but the file is missing or corrupt.
CI includes a dedicated Python 3.11 optional-data job covering both engines in
addition to the core 3.10–3.12 matrix.
CI also covers the `geo3d` extra. Doctor version 56 now reports the normalized,
GeoTIFF, and geographic LAS/LAZ adapters independently; the active environment
has `rasterio 1.4.4` and `laspy 2.7.0`, and the full suite passes 189 tests.
Doctor strict readiness now also requires a passing verification record and the
verifier requires complete in-tree manifest coverage.
Manifest source rows now carry UTC modification timestamps, and the manifest
also exposes `ireland-geometry.freshness.v1` with observed source ages. Doctor
reports those ages and supports `--max-input-age-days` for an explicit stale
input gate; the default remains advisory so cached projects retain their
previous readiness behavior. The verifier now rejects malformed, internally
inconsistent, or filesystem-stale freshness records in schema-3 manifests. The
standalone and lazy reports summarize the reported source count and oldest
cached-source age in their method/provenance panel. OpenAPI 3.1 now declares
the freshness object and source timestamp/age fields for metadata and
capability clients.
The local server now exposes `/api/capabilities`, a cacheable machine-readable
discovery document that inventories report readiness, endpoint contracts, query
limits/cursors, route availability, and per-backend readability. Health,
metadata, query, and route JSON responses carry stable v1 contract identifiers,
the discovery document exposes the canonical contract map, and Doctor/CI verify
the discovery surface and its packaged constants.
The standalone `ireland-geometry-bundle` command now turns the portable output
contract into a deterministic ZIP with per-artifact hashes and fixed timestamps;
the same command can verify an archive in place without extraction. Doctor and
the wheel smoke test verify the `ireland-geometry.bundle.v1` command, including
safe extraction into a new destination and an opt-in passing-verification guard
that binds bundle metadata to the source manifest and verification record.
The guard now requires both source records to be present and included, and the
independent bundle verifier rejects inconsistent provenance flags, hashes,
statuses, and row counts. Two real bundles from the current output are
byte-identical at 61 artifacts and 531,705,758 payload bytes; the packaged
wheel also passes the eight-command and verified-bundle smoke tests.
The local server additionally exposes `/api/openapi.json`, a dependency-free
OpenAPI 3.1 contract for health, discovery, metadata, bounded analysis query,
and route endpoints. Doctor version 56, the capabilities response, CI, and
the packaged wheel smoke test verify its presence and contract identifier.
Health and capabilities now distinguish HTTP serving readiness from analytical
readiness: `analysis_ready` is true only when the provenance manifest exists
and verification, schema audit, and reproducibility records all pass, with
per-record validation status exposed for automation.
The metadata endpoint exposes the same reproducibility record and link as its
verification and schema records.
The bundle CLI now also supports `--json` for creation, verification, and
extraction, returning the archive summary and verified provenance without
requiring human-text parsing. Doctor version 56 and package CI verify this
automation contract.
The schema-audit and reproducibility diagnostics now also accept `--json`,
emitting the exact JSON records they write to disk. Doctor reports this shared
diagnostic-output capability, and CI exercises the utility help and packaged
Doctor contract. The full source suite now passes 189 tests.
The schema-audit and reproducibility diagnostics are now also installed as
`ireland-geometry-schema-audit` and `ireland-geometry-repro`, bringing the
stable wheel command surface to eight commands. Each exposes `--version`; CI,
Doctor, source CLI tests, and the fresh-wheel smoke verify the entry points.
The package-smoke job now also builds a PEP 517 source distribution, installs it
in fresh Python 3.10, 3.11, and 3.12 environments outside the checkout, and
verifies the same eight entry points, package version, and packaged schema.
This closes the
release-artifact gap between editable/source checkouts, wheels, and sdists.
The canonical Python 3.11 wheel and sdist, plus a SHA-256 manifest, are now
retained as CI artifacts for 14 days after successful package-smoke runs.
The packaged distribution also now includes the default preregistered analysis
plan. Holdout execution prefers a project-root override and otherwise falls
back to that package resource; Doctor reports the selected source and SHA-256,
and external-project dry runs preserve the same resolution.
