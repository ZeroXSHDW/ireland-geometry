# Final status

Status: complete for the current cached snapshot; the validation, 3-D/evidence,
holdout, routing, point-to-point query, scalability, review, data-quality,
spatial-bootstrap, and reproducibility tranches are included.

## Latest operational upgrades

The route query now preserves ordered path explainability whenever a SQLite
graph exposes way IDs. Path-enabled CLI/API responses include `path_way_ids`,
`path_segment_n`, and directed `path_segments` identified by the
`(from_node, to_node, way_id)` tuple, plus `path_segment_source` so portable
fallback graphs report that segment IDs are unavailable. GeoJSON retains the
route geometry without duplicating path arrays in feature properties. Each
segment now carries physical distance, estimated duration, ferry-wait seconds,
and ferry status, with `path_segment_total_*` fields for route-total
reconciliation. Each SQLite segment also carries normalized static edge
constraints with parser status and a profile-evaluation flag; conditional
access rules now have per-segment `conditional_rules` records with
entry-time active/applied state and direction/class provenance. Relation-level
turn restrictions now have per-transition `transition_rules` records retaining
relation IDs, via-way context, condition, evaluation state, and whether the
chosen outgoing way was the relation target. SQLite segments now also expose
nullable `road_context` fields for OSM name, reference, highway class, route
type, and oneway state. Doctor v128,
OpenAPI, route API, CLI, dashboard, GeoJSON, ferry, and
turn-restriction regressions cover the contract. Doctor v124 added the
per-segment metric reconciliation capability.

The current v20 height-routing upgrade normalizes documented OSM
feet/inches `maxheight` values such as `9'6"` into metres for both national
PBF builds and generic SQLite graphs. The cached graph now reports 984
supported legal height values, 22 explicit unlimited values, and 503 ambiguous
values across 1,509 tagged ways; ambiguous values remain fail-closed only when
an explicit `height_m` profile is supplied. Doctor v122, route summaries, and
the height-routing regression fixtures cover the new parser behavior.

The current national routing snapshot is the v21 SQLite contract. It retains
39 supported destination-qualified HGV ways across 333 directed segments,
covering static `hgv=destination` access and observed `none @ destination`
exceptions for HGV actual weight or permitted rating. Default `vehicle_class=hgv`
routes block static destination-only ways. The CLI/API/dashboard opt-in
`--allow-hgv-destination` / `allow_hgv_destination=true` enables those ways only
for a route serving the restricted destination and bypasses only the matching
supported conditional numeric exception; unrelated base limits remain active.
The graph metadata reports zero unsupported destination clauses in this cached
extract. The v21 way-context extension is exposed in the route response,
OpenAPI, Doctor, pipeline manifest, and graph schema.

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

The national analysis stage was rerun from the existing local OSM/NIAH caches
without network access so the scoring artifact and cache records are aligned;
the complete SQLite road graph was rebuilt from the cached PBF and the
100-pair routing sample was refreshed against it.
Schema audit, report generation, reproducibility, and final verification all
pass for 123,810 analysis rows and 33,416 targets. The live initial page
measured 268,518 bytes for 50 rows, with server-side filtering and export
contracts active. The repository suite now passes 361 tests, with Ruff and
compileall clean, and includes a tiny offline subprocess fixture covering the
generated manifest, schema, report, verification, and scoring contracts.

The committed Pages dashboard now has a standard-library deployment audit:
`scripts/pages_audit.py` validates its embedded report pack, target/GeoJSON
alignment, scoring/manifest provenance, and review-queue scope before the
GitHub Pages artifact is uploaded. `scripts/publish_pages.py` now rebuilds the
committed site from verified `output/` artifacts and writes
`docs/pages_manifest.json`; the refreshed site carries the current `2bfd3a5`
revision and passing verification state.
The final publication audit, artifact hash/byte checks, and fresh wheel/sdist
package smoke tests also pass; the only remaining Doctor warning is the
expected uncommitted-worktree state.
The current cached output also has a complete verified `output.bundle.zip`
with 62 artifacts; the required-bundle release gate passes its provenance and
current-output alignment checks.
The routing layer now supports the versioned
`ireland-geometry.ferry-schedules.v1` companion contract for SQLite graphs.
New PBF graph builds persist supported ferry `opening_hours` and optional
per-way `duration_s`; explicit departures enforce weekly, seasonal, fixed-date,
and public-holiday service windows; time-aware route metrics wait for the next
supported opening when a ferry is reached outside its window; route responses
expose the resulting `ferry_wait_s` and `ferry_wait_n`. The cached graph now
includes the separate `ireland-geometry.public-holidays.v1` `public_holidays.json`
companion with 80 Irish dates covering 2023–2030; the route CLI/API apply
crossing durations to arrival estimates and report the calendar status and date
bounds. Route responses also expose the unique ferry ways,
physical ferry distance, crossing time excluding waits, and ferry-edge count.
The refreshed national graph carries 26 ferry
durations and 4 parsed schedules, including 1 that requires the optional
calendar.
Point-to-point routing now also accepts an explicit `distance` or `duration`
objective. Distance remains the default analytical behavior; duration selects
the fastest estimated route and can prefer a longer road over a physically
shorter but slow ferry.
Optional vehicle-weight profiles now evaluate OSM conditions such as
`weight>7.5` through `--weight-t` in the CLI, `weight_t` in the local API,
and the dashboard. The refreshed national graph parses all 78 conditional
restriction relations; 73 are graph-resolved, including 74 time windows and
one weight-based rule, with four conditional references unresolved. It also
  retains 56 supported conditional road-access rules (24 general-traffic, 19
  delivery-vehicle, 7 public-service-vehicle, and 6 taxi rules, including 16
  directional and 1 weight-qualified rule) for departure-aware route
	  evaluation; 4 unsupported conditional-access tags are reported, with 14
	  conditional/access-filtered ways
excluded under the default policy. With no profile, profile-dependent rules
remain inactive and are reported as such; unconditional 24/7 class rules can
be evaluated without a departure. The weight-qualified delivery access rule
	  also requires an explicit vehicle weight. The `psv` profile evaluates
  public-service-vehicle windows, while general and delivery profiles use
	  those roads outside their active class-specific windows. The `taxi` profile
	  evaluates taxi-specific windows. Multiple supported clauses on one conditional
	  access key are now retained as an ordered per-way/direction rule set; active
	  class-specific allows are unioned and active denies remove matching classes.
	  Doctor v122 advertises the schedule, waiting,
	  public-holiday, duration, vehicle-weight, vehicle-class,
and conditional-road-access capabilities while legacy
graphs remain readable with an explicit `not_provided` schedule state.
	The v21 SQLite graph retains 1,101 generic `maxweight` ways, including
1,100 numeric limits across 9,918 segments, and 35 numeric `maxweight:hgv`
ways across 289 directed segments. An explicit `weight_t` profile enforces
generic limits; the explicit `hgv` vehicle class additionally enforces numeric
HGV-specific limits. One ambiguous generic tag is fail-closed only for weighted
	routes. It separately retains 216 routable `maxweightrating:hgv`/Irish
	`maxweightrating:goods` ways, with 214 supported numeric values and 2 ambiguous
	values across 3,180 directed segments;
`rating_t` enforces this permitted gross-weight rating only for the explicit HGV
class and is distinct from actual `weight_t`. Destination-dependent and unloading-specific
conditions remain outside the contract. It also enforces numeric legal
`maxheight` limits on 1,509 ways across 9,324 segments and physical
`maxheight:physical` limits on 12 ways across 25 segments when `height_m` is
supplied; 984 legal values are supported, 22 are explicit unlimited values,
and 503 legal plus 1 physical values remain ambiguous and fail closed only for
height-qualified routes.
	The same v21 edge contract retains numeric `maxwidth` on 14 ways across 131
directed segments, `maxlength` on 5 ways across 95 segments, and `maxaxleload`
on 10 ways across 84 segments. Explicit `width_m`, `length_m`, and
`axleload_t` profiles enforce them; OSM feet/inches width and length values are
normalized to metres, and unsupported/destination-dependent variants remain
outside the contract.
It also retains numeric `maxspeed` on 196,440 routable ways: 196,435
supported ceilings across 2,115,207 directed segments and 5 non-numeric ways
across 19 segments. Duration estimates apply supported ceilings per way while
	keeping the configured speed as an upper bound. The v21 edge contract also
stores 225 `maxspeed:conditional` ways across 2,638 directed segments; 185
supported schedules are evaluated from explicit departures and 40 unsupported
clauses remain retained but unevaluated.
It also stores five conditional one-way ways across 31 directed segments in
`oneway_conditional_json`; two supported schedule ways are evaluated from
explicit departures and three unsupported permit/private clauses preserve the
base one-way semantics.
The dashboard route summary now renders ferry waiting in human-readable form,
while preserving the complete JSON/GeoJSON response; Doctor inventories that
wait-observability capability in both generated report modes.
The packaged CI smoke now executes the Pages publisher plus the reproducibility
and bundle symlink guards against verified fixtures, and the Pages workflow
deploys only after a successful CI run while checking out that run's exact
commit. The static Pages audit also rejects symlinked pages and malformed
publication source hashes.
Doctor version 73 now surfaces the same Pages publication/audit readiness under
`capabilities.pages`, including the published revision and file count.
Strict Doctor readiness now also requires the complete verification,
schema-validation, and reproducibility gate, matching the report server’s
analytical-readiness contract.
It additionally rechecks the current output-manifest byte/hash inventory, so
persisted passing validation records cannot mask a changed, unlisted, missing,
or symlinked artifact; the shared result is exposed under
`capabilities.validation.output_alignment`.
The verified bundle publication guard now enforces the same complete gate and
retains both validation records with hashes and inclusion provenance.
Doctor also reports that complete-validation enforcement explicitly under
`capabilities.bundle`.
The read-only `ireland-geometry-release-check` command now composes Doctor
strict readiness, current output-manifest artifact hash/size and inventory
checks, Pages audit plus freshness against the current output pack, and optional
complete-bundle verification under
`ireland-geometry.release-check.v1`. The installed command surface is now nine
entry points; the release gate can be run with
`--strict --require-pages --require-bundle --bundle output.bundle.zip`; it also
rejects a valid-but-stale archive whose four provenance hashes do not match the
current output directory. It also rejects newly added output files that are not
listed in the current manifest; Doctor exposes this as
`capabilities.release_check.checks_output_inventory`. The reproducibility
diagnostic now reports missing output/reference directories as structured
machine-readable failures. The release output gate also requires all three
validation records to be explicitly listed in the manifest and rejects output
symlinks, including the allowed diagnostic exception paths; Doctor exposes this
as `capabilities.release_check.checks_output_symlinks`.
The reproducibility diagnostic now reports the same symlink invariant as a
structured failure, including file or output-root paths, and Doctor exposes
this as `capabilities.pipeline.reproducibility_rejects_symlinks`.
Direct bundle creation now enforces current manifest byte/hash alignment in
addition to file, directory, and output-root symlink checks, and the CLI
preserves those checks before dispatch; Doctor exposes the capabilities as
`capabilities.bundle.checks_manifest_artifacts` and
`capabilities.bundle.rejects_symlinks`.
The local report server now rejects symlinked output roots and nested output
entries before serving, blocks links introduced after startup, and exposes the
same boundary as `capabilities.report_server.rejects_symlinks`.
Its health, capabilities, metadata, and interpretation responses also recheck
the current output bytes and hashes against the manifest inventory through
`ireland-geometry.manifest-alignment.v1`; stale or unlisted output therefore
cannot report `analysis_ready: true`. Doctor exposes this server capability as
`capabilities.report_server.checks_manifest_alignment`.
The initial lazy-dashboard page response additionally carries
`ireland-geometry.report-runtime.v1` and applies the current gate to its
summary and validation finding, keeping the visible dashboard state aligned
with the server health result.
The generated dashboard now reapplies that runtime envelope on every later
page or export response and shows a live toolbar badge, so an artifact change
during a long review becomes visibly provisional rather than leaving the
initial readiness text stale.
Report-page filtering/export imports are now geometry-dependency-light, so
fresh wheel and sdist installs can exercise the local page API before optional
geospatial libraries are loaded.
Filtered CSV and GeoJSON exports now carry the same
`ireland-geometry.report-runtime.v1` readiness contract in
`X-Ireland-Geometry-*` headers; the lazy dashboard surfaces non-passing export
states as provisional without changing either download format.
Doctor version 70 inventories the export-header capability under
`capabilities.report_server.report_export_runtime_headers`.
Doctor also inventories the generated dashboard’s live runtime-refresh client
under `capabilities.reports.live_runtime_refresh`, and marks the report
capability incomplete when either generated page is stale.
Doctor version 73 additionally inventories the direct runtime endpoint and its
snapshot identity under
`capabilities.report_server.report_runtime_api`. The server exposes the same
readiness envelope at `GET /api/report/runtime` with conditional ETags, and the
dashboard polls it every 30 seconds so an idle review cannot silently retain a
green readiness state after output changes. Each envelope identifies the active
manifest generation, Git revision/dirty state, package and manifest schema
versions, and manifest SHA-256; the dashboard badge shows the revision.
Doctor exposes the implementation as
`capabilities.report_server.report_runtime_snapshot`.

Doctor version 74 now publishes `summary.strict_blockers` as structured,
machine-readable causes for every strict-readiness failure. Dirty Git state
also includes a capped worktree path inventory with tracked/untracked counts;
the read-only release check propagates the blocker codes into its errors so a
failed release gate identifies the actionable cause instead of returning only
one generic Doctor failure.

Doctor version 75 added the versioned
`ireland-geometry.source-alignment.v1` gate. It rechecks the current source
paths recorded in `output/manifest.json` using modification time and byte size
by default, fails closed when a hashed source is missing or changed, and can
rehash every recorded source through `release_check.py --check-input-hashes`.
The local report server, runtime endpoint, dashboard refresh client, and
export headers carry the same source status.

Doctor version 76 extends that source gate across every read-only report
surface: health, capabilities, metadata, interpretation, report pages, runtime,
and CSV/GeoJSON exports. Each response now carries the same source-alignment
object, and source drift forces analytical readiness false and marks the
interpretation finding/caveat provisional. The capabilities response publishes
the endpoint list under `source_alignment_surfaces`.

Doctor version 77 extends the same invariant into source trees: recursive
source symlinks are enumerated, never traversed for hashing, and fail the
source-alignment gate in both metadata and full-hash modes. Manifests retain
the recorded `symlink_paths` diagnostic so a source built through an unsafe
link cannot silently become a passing input later.

Doctor version 78 closes the corresponding relocation edge: runtime source
alignment and standalone verification now share one resolver and prefer the
active portable source reference over a retained historical absolute path.
Regression coverage keeps both paths present with different contents and
proves that the relocated source is the one checked.

Doctor version 79 adds a recursive, content-free `metadata_sha256` fingerprint
to recorded source files and directories. Fast source alignment now catches
nested file additions, removals, and timestamp/size changes even when a
directory root timestamp is unchanged; the explicit full-hash release mode
remains the check for same-metadata content tampering. The verifier, OpenAPI
schema, package smoke checks, and regression suite expose the new counter and
per-source digests.

The standalone verifier now accepts the active `--project-root` and uses it
for portable source resolution, source freshness, content hashes, and Git
identity. Pipeline-generated verify commands carry that root explicitly, so a
project with separate data/output directories remains relocation-safe instead
of falling back to `out_dir.parent`.

The explicit-root verifier contract is covered by custom-layout relocation and
freshness regressions, and both fresh wheel and sdist installs expose the new
CLI option. The repository suite now passes 301 tests.

Doctor version 93 now identifies the explicit report-server root,
post-validation bundle contracts, including the pipeline's independent bundle
verification operation, and the direct analytical-stage output guard matrix.
The local report server accepts the same active `--project-root` contract.
All readiness surfaces and runtime export headers pass that root to source
alignment, so a served output directory outside the project cannot silently
fall back to a historical source. Doctor inventories the capability under
`capabilities.report_server.explicit_project_root`.
The pipeline also accepts `--bundle` and creates then independently verifies
the same complete verified archive only after the final verification/report
refresh sequence; dry-run JSON and Doctor expose both post-validation
operations.
The standalone bundle command now enforces the same outside-output archive
boundary as the pipeline, preventing a release ZIP from contaminating the
output manifest inventory; Doctor exposes this as
`capabilities.bundle.rejects_archive_inside_output`.
The Pages publisher likewise rejects a site directory inside the analytical
output root and Doctor exposes
`capabilities.pages.publisher_rejects_site_inside_output`.
The publisher also rejects symlinked output and site roots, exposed as
`capabilities.pages.publisher_rejects_symlink_roots`.
The pipeline likewise rejects an existing symlinked `--out-dir` before planning
or writing, including dry-run mode; Doctor exposes this as
`capabilities.pipeline.rejects_output_symlink`.
The static Pages audit and unified release check also reject symlinked site and
output roots before resolving them, and now fail clearly for existing
non-directory roots; Doctor exposes the release capability as
`capabilities.release_check.rejects_symlink_roots` and
`capabilities.release_check.handles_non_directory_roots`, with matching Pages
publisher/audit fields.
The shared `ireland-geometry.output-safety.v1` inventory now confirms that all
user-facing output writers and publication readers carry the same fail-closed
root guard under `capabilities.output_safety`; the shared guard rejects both
symlinked roots and existing non-directory paths.
The same helper now protects the `--out-dir` path of 19 direct analytical stage
CLIs, recursively rejecting nested symlinks as well as unsafe roots; the
per-stage matrices are exposed as
`capabilities.output_safety.stage_output_guards` and
`capabilities.output_safety.stage_output_tree_guards`. The routing graph writer
also recursively checks its separate `--write-graph` destination before graph
materialization. A matching
`ireland-geometry.data-safety.v1` inventory now covers the pipeline, ingestion
fetchers, and direct data-consuming stages, including regular-file and symlink
data roots.
Doctor v109 inventories nested OSM/NIAH cache boundaries, individual cached
files, the primary pipeline's nested data/output preflight, recursive
direct-stage data guards, and operational verifier/query/server boundaries.
Symlinked directories, OSM JSON, NIAH ZIP/CSV files, nested NIAH extraction
content, Sentinel-2 cache content, combined JSON, graph inputs, or the
Geofabrik PBF are rejected before cached reads or dispatch.
The standalone route query now resolves its explicit JSON and GeoJSON output
files through the same fail-closed boundary, rejecting output directories,
symlinked destination files, and symlinked parent directories; Doctor exposes
this as `capabilities.output_safety.file_output_guards`, now covering the
Doctor JSON report and bundle archive as well as route-query JSON/GeoJSON.
Bundle verification and extraction also reject symlinked archive inputs and
extraction parent directories; Doctor exposes those boundaries under
`capabilities.bundle`.
The report builder and columnar exporter now also reject nested symlinks below
their existing output roots before reading or replacing report-pack artifacts;
Doctor includes both under `capabilities.output_safety.nested_guards`.
Doctor v109 also reports the live data/output tree boundaries, including
relative offending symlink paths, and strict readiness blocks on either
`data_root_invalid` or `output_root_invalid`.
Explicit file outputs also allow macOS's canonical `/tmp` alias without
weakening rejection of user-created symlink parents.
Doctor also exposes `ireland-geometry.file-write-safety.v1` under
`capabilities.file_write_safety`, covering atomic ingestion downloads and cache
replacement. NIAH archive downloads and extracted CSV members stream through
the same atomic helper, avoiding wholesale buffering of large regional inputs.
Strict Doctor readiness now requires that inventory to be available before it
can report a green strict-ready result.

The manifest builder and standalone verifier now recursively cover nested output
files and reject output symlinks. Doctor exposes this under
`capabilities.validation.manifest_coverage`, keeping the core verification
invariant aligned with the release gate.

The verifier now also publishes the versioned
`ireland-geometry.manifest-cache.v1` alignment record. The refreshed national
snapshot passes all 28 manifest-to-stage-command checks, including the routing
pair limit, stochastic seeds, analysis plan, optional-source paths, and route
policy flags; a diagnostic invocation is kept in `last_invocation` without
altering the analytical comparison. Doctor JSON now exposes the same compact
alignment status for readiness automation.

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
- a complete access-aware SQLite road graph from 1,123,253 supported highway
  ways: 961,735 routable road ways, 7,427,417 nodes, and 7,553,171 physical
  road segments after excluding 161,518 access-filtered ways; 3,940 non-conditional
  OSM turn restrictions are applied, including 181 validated via-way chains;
  73 conditional rules are stored from 78 supported relations for optional
  departure/vehicle profiles; 74 are time windows and 1 is weight-based; 56
  supported conditional road-access rules are retained (24 general-traffic,
  19 delivery-vehicle, 7 public-service-vehicle, and 6 taxi, including 16
  directional and 1 weight-qualified rule), 4 unsupported
  conditional-access tags are reported, and 14 conditional/access-filtered
  ways are excluded under the default policy; the v17
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
.venv/bin/python -m pytest                 # 265 passed
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
3,940 non-conditional via-node/via-way restrictions are applied, and 73
graph-resolved conditional rules are stored from 78 supported relations: 74
time windows and one vehicle-weight condition. Fifty-six supported
  conditional road-access rules (24 general-traffic, 19 delivery-vehicle, 7
  public-service-vehicle, and 6 taxi, including 16 directional and 1
  weight-qualified rule) are evaluated from an explicit departure/profile
  selection; 4 unsupported
conditional-access tags are reported, with 14 conditional/access-filtered ways
excluded under the default policy. Time windows can be evaluated with an
explicit departure profile; unconditional 24/7 class rules do not require one,
and the weight rule requires a vehicle profile. Four conditional references are unresolved because their source ways are
pedestrian/cycleway-only; profile-dependent rules remain inactive without a
  profile. Use `--vehicle-class delivery` (or `vehicle_class=delivery`) to
  activate delivery-only ways; use `--vehicle-class psv` (or
  `vehicle_class=psv`) for public-service-vehicle windows; use
  `--vehicle-class taxi` (or `vehicle_class=taxi`) for taxi-specific windows.
  The general profile remains blocked from delivery-only ways and class-specific
  roads are available only within their active windows.
  Ferry geometry is opt-in. New SQLite graph builds also persist the
`ireland-geometry.ferry-schedules.v1` companion contract, which enforces
  supported weekly, seasonal, fixed-date, and public-holiday `opening_hours`
  windows for explicit departures, waiting for the next supported opening when
necessary, and route responses expose the resulting `ferry_wait_s` and
`ferry_wait_n`. Public-holiday windows require the separate
  `ireland-geometry.public-holidays.v1` `public_holidays.json` companion. The
  cached calendar carries 80 dates from 2023–2030 and metadata exposes its
  coverage bounds. Optional per-way crossing durations affect arrival
  estimates. The cached companion carries 26 durations and 4 parsed schedules,
  including 1 that requires the calendar. Terminal
  platform semantics and service frequency are not modeled. Numeric legal and
  physical vehicle-height restrictions are enforced when `height_m` is supplied;
  ambiguous height values fail closed only for height-qualified routes. Numeric
  width, length, and axle-load constraints are also enforced when their
  corresponding vehicle profiles are supplied; unsupported destination- or
  condition-dependent variants remain outside the contract.
  Access restrictions are applied from the most specific motor-vehicle tags.
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
nine console commands and is carried into the dry-run JSON, output manifest,
Doctor report, and local metadata API. Package-smoke derives the expected
version from installed distribution metadata in each Python 3.10–3.12 job and
checks all nine commands, so future version bumps do not require duplicated
release literals in CI.
Doctor now reports both declared entry points and runnable installed wrappers,
so a stale editable environment cannot appear fully packaged merely because
its metadata lists the newer commands. Strict readiness now requires all nine
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
	The routing adapter now supports a disk-backed SQLite v17 graph for complete PBF
conversion (`--graph-format sqlite --max-ways 0`) in addition to the bounded
CSV/in-memory path. Its indexed nearest-node lookup and SQLite Dijkstra avoid
constructing a multi-million-edge Python adjacency list. The current snapshot
contains 1,123,253 scanned highway ways, 961,735 routable road ways, 7,427,417
nodes, and 7,553,171 physical road segments after excluding 161,518
access-filtered ways; it applies 3,940 non-conditional via-node/via-way turn restrictions and
stores 73 graph-resolved conditional rules from 78 supported relations (74 time
windows plus 1 weight-based) and 56 supported conditional road-access rules
(24 general-traffic plus 19 delivery-vehicle plus 7 public-service-vehicle
plus 6 taxi, including 16 directional and 1 weight-qualified rule) while reporting 4
unsupported conditional-access tags and 14
conditional/access-filtered ways excluded under
the default policy;
	the v17 edge contract also retains 1,101 generic `maxweight` ways, with 1,100
numeric limits across 9,918 segments, and 35 numeric `maxweight:hgv` ways
across 289 directed segments; explicit `--weight-t`/`weight_t` profiles enforce
generic limits, while the `hgv` vehicle class enforces HGV-specific limits.
The one ambiguous generic tag is fail-closed only for weighted routes;
	the v17 edge contract separately retains 216 routable
	`maxweightrating:hgv`/Irish `maxweightrating:goods` ways, with 214 supported
	numeric values and 2 ambiguous values across 3,180 directed
segments; `rating_t`/`--rating-t` enforces permitted rating only for the HGV
vehicle class and remains distinct from actual `weight_t`. Destination-dependent
and unloading-specific conditions remain out of scope; the same edge contract
retains numeric legal `maxheight`
limits on 1,509 ways across 9,324 segments and physical `maxheight:physical`
limits on 12 ways across 25 segments; explicit `--height-m`/`height_m` profiles
enforce them and ambiguous values are fail-closed only for height-qualified
routes; it also retains numeric `maxwidth`, `maxlength`, and `maxaxleload`
profiles on 14/5/10 ways across 131/95/84 directed segments, respectively,
and exposes those profiles through the CLI, sampled pipeline, local API,
OpenAPI, Doctor, dashboard, and route responses.
	The same v17 edge contract retains numeric `maxspeed` on 196,440 ways,
including 196,435 supported ceilings across 2,115,207 directed segments and 5
non-numeric values across 19 segments. Duration routing applies those ceilings
per way without increasing the configured fallback speed; 225 conditional
speed ways span 2,638 directed segments, with 185 supported schedules
evaluated from explicit departures and 40 unsupported clauses retained but
unevaluated.
	The v17 edge contract also stores five conditional one-way ways across 31
directed segments; two supported schedules are evaluated from explicit
departures and three unsupported permit/private clauses preserve the base
one-way semantics.
It also carries 1,549 opt-in ferry segments
from 77 ways and 38 relations. The doctor reports the backend, counts, access
policy, conditional/ferry coverage, restriction coverage, and completeness
flag alongside the other capabilities.
The standalone route query command accepts arbitrary WGS84 coordinates against
the persisted graph and returns JSON with both snap distances and an estimated
arrival time. With `--include-path` or `--geojson-out`, it reconstructs the
traversed graph path; `--include-ferries` enables the static ferry layer. It
uses the same turn-restriction and conditional-profile engine as the sampled
pipeline routes. The CLI accepts `--vehicle-class general|delivery|hgv|psv|taxi`, and the
local API accepts the corresponding `vehicle_class` parameter; delivery-only
ways are available only to the delivery profile, while `hgv` activates numeric
`maxweight:hgv` limits when `--weight-t`/`weight_t` is supplied. Directional OSM
conditional access keys are retained in the v17 SQLite schema and evaluated against the
actual forward or backward traversal.
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
form with coordinate, speed, vehicle class, departure, path, ferry, JSON, and
GeoJSON options;
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
