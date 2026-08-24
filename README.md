# Ireland Geometric Pattern Scan

Live dashboard: <https://zeroxshdw.github.io/ireland-geometry/>

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

The checked local snapshot was regenerated on 2026-08-21 with seed `20260816`
and 300 Monte Carlo iterations:

- 135,173 OSM area elements ingested;
- 123,810 footprints pass the 25 m² analysis threshold;
- 33,416 target buildings and 90,394 empirical controls;
- a versioned `ireland-geometry.exploratory-score.v1` scoring plan with
  self-describing weights, thresholds, and `output/scoring_config.json`
  provenance;
- 9,455 OSM→NIAH spatial joins (8,884 analyzed rows);
- 41 NIAH significance tests, 2 point-pattern groups, 739 architect-evidence rows
  and 45 retained architect-rate rows; architect evidence now requires explicit
  design or architect-role context and records the matched source phrase;
- 100,212 local target-control pairs, 12 matched tests, and 12 hierarchical tests;
- 246 footprints with mapped OSM building parts, 30 Ripley rows, 5 Moran rows,
  8 county permutations, and 5 road-proximity summaries;
- a complete SQLite road graph generated from 1,123,253 supported highway ways
  with access filtering applied: 961,735 routable road ways, 7,427,417 nodes,
  and 7,553,171 physical road segments; 3,940 non-conditional OSM turn
  restrictions are applied, including 181 validated via-way chains; 73
  conditional rules are stored from 78 supported relations for optional
  departure/vehicle profiles: 74 time windows and 1 weight-based condition.
  Fifty-six supported conditional road-access rules are retained (24 general,
  19 delivery-vehicle, 7 public-service-vehicle, and 6 taxi rules, including
  16 directional and 1 weight-qualified rule); 4 unsupported
  conditional-access tags are reported, with 14 conditional/access-filtered
  ways excluded under the default policy. Delivery-only ways remain in the SQLite graph but are
  inactive for the general profile. Multiple supported clauses on one
  conditional access key are retained as an
  ordered per-way/direction rule set; active class-specific allows are unioned
  and active denies then remove matching classes. Multi-clause coverage is
  reported separately in graph summaries and Doctor. The HGV profile also
  evaluates numeric `maxweight:hgv` limits: the current graph retains 35
  supported HGV limits
  across 289 directed segments. A separate HGV permitted-rating profile
  evaluates numeric `maxweightrating:hgv` limits plus the Irish
  `maxweightrating:goods` alias on 216 routable ways (214 supported and 2
  ambiguous) across 3,180 directed segments; `--rating-t` is
  distinct from actual vehicle mass and is enforced only with `vehicle_class=hgv`.
  The v21 graph also retains 39 supported destination-qualified HGV ways across
  333 directed segments: static `hgv=destination` access is blocked by default,
  while observed `none @ destination` weight/rating exceptions are enabled only
  by `--allow-hgv-destination` (or `allow_hgv_destination=true` in the API).
  That opt-in is intended only when the route serves the restricted destination.
  Explicit vehicle-height profiles also enforce
  numeric legal `maxheight` limits on 1,509 ways; documented feet/inches
  values such as `9'6"` are normalized to metres, while ambiguous values
  remain fail-closed for height-qualified routes. Physical `maxheight:physical`
  limits are retained on 12 ways. A separate
  v21 duration layer applies supported numeric OSM `maxspeed` ceilings on
  196,435 ways across 2,115,207 directed segments; 5 non-numeric `maxspeed`
  ways remain explicitly retained without an invented ceiling. Its edge
  contract also stores 225 `maxspeed:conditional` ways across 2,638 directed
  segments: 185 use safely supported schedules and 40 retain unsupported
  syntax. It also stores five conditional one-way ways across 31 directed
  segments: two use supported schedules and three retain unsupported
  permit/private syntax. A separate
  ferry layer stores 1,549 segments from 77 access-allowed ways and 38 route
  relations (excluded unless `--include-ferries` is requested); the 100-pair
  sample has 97 reachable routes; the bounded CSV graph remains available as a
  portable fallback. The sibling `public_holidays.json` companion supplies 80
  Irish public-holiday dates covering 2023–2030, including the 2026 dates
  validated against the official Workplace Relations Commission list;
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
  graph, including ordered SQLite way/segment explainability;
- a standalone `ireland-geometry-route-matrix` command that accepts repeated
  WGS84 `--origin LAT,LON` and `--destination LAT,LON` points, caps the
  Cartesian request at 25 pairs, and emits the same versioned matrix contract
  as the local API;
- a standalone `ireland-geometry-route-compare` command that compares two to
  eight named vehicle profiles over one trip, reports reachability and metric
  deltas from the first baseline profile, and can embed full route evidence;
- a standalone report at `output/report.html`, a hosted lazy-data version at
  `output/report_lazy.html`, the compact `output/interpretation.json` sidecar,
  JSONL/Parquet/DuckDB analysis exports, and a provenance manifest at
  `output/manifest.json`.
- a versioned `ireland-geometry.columnar.v1` export contract: CSV, JSONL,
  Parquet, and DuckDB exports carry schema and order-independent row digests;
  the current supported build reports full cross-backend parity.

## Quick start

Use Python 3.10 or newer. The checked-in `.python-version` selects Python 3.11
for the default local workflow and Pages runtime; the CI matrix also verifies
Python 3.10 and 3.12. The primary extractor needs the `osmium` Python bindings
(the project commonly documented as PyOsmium) and processes the PBF locally.
For a supported local environment, choose an installed interpreter explicitly.

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
  --departure 2026-08-17T08:00:00+00:00 --speed-kmh 50 \
  --objective duration --include-ferries

# Query a bounded origin/destination matrix:
.venv/bin/ireland-geometry-route-matrix --road-graph data/roads \
  --origin 53.3498,-6.2603 --origin 53.3438,-6.2672 \
  --destination 53.3445,-6.2408 --destination 53.3500,-6.2600 \
  --objective duration --include-path

# Compare the same trip for a general vehicle and a weighted HGV:
.venv/bin/ireland-geometry-route-compare --road-graph data/roads \
  --start 53.3498,-6.2603 --goal 53.3438,-6.2672 \
  --profile general \
  --profile 'hgv;vehicle_class=hgv;weight_t=7.5' \
  --objective duration --include-path

# Evaluate the weight-based conditional turn profile for a heavy vehicle:
.venv/bin/ireland-geometry-route --road-graph data/roads \
  --start-lat 54.3046561 --start-lon -8.1772739 \
  --goal-lat 54.3053215 --goal-lon -8.1775745 --weight-t 10

# Evaluate delivery-only conditional road access:
.venv/bin/ireland-geometry-route --road-graph data/roads \
  --start-lat 53.3498 --start-lon -6.2603 \
  --goal-lat 53.3438 --goal-lon -6.2672 \
  --vehicle-class delivery --departure 2026-08-17T20:00:00+00:00

# Enforce legal and physical height restrictions for a tall vehicle:
.venv/bin/ireland-geometry-route --road-graph data/roads \
  --start-lat 53.3498 --start-lon -6.2603 \
  --goal-lat 53.3438 --goal-lon -6.2672 --height-m 3.8

# Enforce width, length, and axle-load restrictions for a commercial vehicle:
.venv/bin/ireland-geometry-route --road-graph data/roads \
  --start-lat 53.3498 --start-lon -6.2603 \
  --goal-lat 53.3438 --goal-lon -6.2672 \
  --width-m 2.5 --length-m 12 --axleload-t 10

# Add the traversed path as a GeoJSON LineString:
.venv/bin/ireland-geometry-route --road-graph data/roads \
  --start-lat 53.3498 --start-lon -6.2603 \
  --goal-lat 53.3438 --goal-lon -6.2672 \
  --geojson-out /tmp/ireland-route.geojson

# Create a relocatable ZIP of the generated outputs:
.venv/bin/ireland-geometry-bundle --out-dir output \
  --archive ireland-geometry.bundle.zip

# Run the pipeline and create a verified bundle after the final validation:
.venv/bin/python run_pipeline.py --no-network --bundle
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
.venv/bin/python scripts/verify.py --project-root /path/to/project --data-root /path/to/data --out-dir /path/to/output
.venv/bin/python scripts/serve_report.py --project-root /path/to/project --out-dir /path/to/output --data-root /path/to/data
.venv/bin/python scripts/release_check.py --json --require-pages
# Optional deep source-content check (the default gate checks metadata only):
.venv/bin/python scripts/release_check.py --json --require-pages --check-input-hashes
.venv/bin/python -m pip check
git diff --check
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
arrival time. Pass `--include-path` to include graph node IDs, GeoJSON-order
coordinates, ordered `path_way_ids`, and directed `path_segments` in the JSON,
or `--geojson-out` to write a GeoJSON `LineString`. Each SQLite segment is
identified by its `(from_node, to_node, way_id)` tuple; `path_segment_source`
reports `sqlite_edges` for persisted SQLite edge IDs, `portable_edges` for
portable CSV/JSON edges that supply way IDs, and `not_available` when the graph
does not supply way IDs. Portable graphs retain supplied `name`, `ref`,
`highway`, `route`, and `oneway` context; rows tagged `route=ferry` (or
`ferry=yes`) retain opt-in ferry geometry as well. Portable ferry edges are
excluded unless `--include-ferries` is supplied; when included, path segments
report `ferry: true`, and compact/path-enabled route responses report basic
ferry distance/edge/way metrics. Portable graphs do not model ferry schedules,
waits, or crossing durations, so those timing fields remain zero. Portable
graphs also intentionally do not claim the
SQLite-only vehicle-profile or turn-restriction semantics. Segment records also report physical distance,
estimated duration, ferry-wait seconds, and ferry status; the response exposes
the corresponding `path_segment_total_*` reconciliation fields. SQLite-backed
segments also expose a `constraints` array of normalized static OSM edge
records (`key`, `value`, `unit`, `status`, `evaluated`, and `profile`), so a
client can see which persisted limits were present and whether the supplied
vehicle profile evaluated them. Relation-level turn restrictions are exposed
on the segment entering each via node through `transition_rules`, retaining
relation IDs, `via_way_ids`, `kind`, condition, active/evaluated state, and
`selected`/`applied` flags. Each segment also includes nullable `road_context`
fields (`name`, `ref`, `highway`, `route`, and `oneway`) from the persisted OSM
way record, allowing clients to display human-readable road identity without
another graph lookup.
Each segment also
contains `conditional_rules` for persisted `maxspeed:conditional`,
`oneway:conditional`, and direction-aware `conditional_access` rows; those
records retain the condition, mode, profile, active state, and whether the
rule was applied at the segment's entry time.
Path-enabled responses also include `maneuver_n` and `maneuvers`: deterministic
start/arrival, turn, mapped-way-change, and ferry-transition records with
bearings, the outgoing road context, and distance/duration/wait to the next
maneuver.
It accepts the same `--departure` and `--speed-kmh` profile parameters.
`--objective distance` (the default) preserves the shortest physical route;
`--objective duration` selects the fastest estimated route, allowing modeled
ferry crossing durations and service-window waits to change route selection.
`--weight-t` supplies an optional metric-tonne vehicle profile; when present,
weight-based conditional turn restrictions such as `weight>7.5` are evaluated.
Without a weight profile those rules remain explicitly retained but inactive.
`--rating-t` separately supplies an HGV's permitted gross-weight rating for
numeric `maxweightrating:hgv` limits and the Irish `maxweightrating:goods`
alias. It is not the vehicle's actual mass and
is evaluated only with `--vehicle-class hgv`; ambiguous values are fail-closed
for rating-qualified routes.
`--height-m` activates numeric legal and physical `maxheight` limits. The
complete SQLite graph also accepts `--width-m`, `--length-m`, and
`--axleload-t` for numeric `maxwidth`, `maxlength`, and `maxaxleload`
limits; width and length values written in OSM feet/inches notation are
normalized to metres. Without the corresponding profile, these limits remain
retained but inactive; ambiguous values are fail-closed only for the active
profile. The portable CSV fallback does not expose these per-edge profiles.
On v21 SQLite graphs, duration estimates also apply supported numeric OSM
`maxspeed` ceilings per way, capped by the supplied `--speed-kmh` fallback;
`mph` and `knots` values are normalized to km/h. The 225
`maxspeed:conditional` ways are persisted with their raw clauses; 185 ways
with supported weekly schedules are evaluated at edge-entry time when
`--departure` is supplied, while 40 unsupported ways remain retained but
unevaluated. Unconditional `24/7` clauses can apply without a departure.
Supported `oneway:conditional` and `oneway:motor_vehicle:conditional` windows
are evaluated from the same departure profile; schedule-only values are
interpreted as temporary forward one-way rules, while unsupported qualifiers
such as `permit` and `private` preserve the base one-way semantics.
`--include-ferries` enables the persisted ferry geometry layer in either graph
backend. Portable CSV/JSON graphs use the retained ferry geometry only and do
not provide schedule-aware timing. SQLite graphs may carry the versioned
`ferry_schedules.json` companion contract; when a
departure is supplied, supported weekly, seasonal, and fixed-date
`opening_hours` windows are enforced; time-aware metrics wait for the next
supported opening when a route reaches a closed service window. Optional per-way
`duration_s` values are reflected in arrival estimates. The cached national graph currently carries
26 ferry durations and 4 parsed schedules, including 1 schedule that requires
the explicit `ireland-geometry.public-holidays.v1` `public_holidays.json`
companion. The active calendar covers 2023–2030 and route metadata exposes its
date bounds; unsupported or absent schedule metadata remains explicit in the
route method, and terminal platform semantics and service frequency are not
modeled.
The installed `ireland-geometry-route-matrix` command uses the same profile
flags and accepts repeated `--origin LAT,LON` and `--destination LAT,LON`
arguments. It emits `ireland-geometry.route-matrix.v1` JSON in deterministic
origin-major/destination-minor order, with compact pair summaries by default;
`--include-path` embeds the complete point-to-point route response for each
pair. Requests above 25 Cartesian pairs fail before the graph is opened, and
`--out` uses the same symlink-safe output boundary as the single-route command.
The installed `ireland-geometry-route-compare` command accepts repeated
`--profile NAME[;key=value]` specifications, with the first profile as the
baseline and a hard two-to-eight profile bound. Supported keys are
`vehicle_class`, `weight_t`, `rating_t`, `height_m`, `width_m`, `length_m`,
`axleload_t`, and `allow_hgv_destination`. Its
`ireland-geometry.route-comparison.v1` response reports each profile's
reachability, snap metadata, distance/duration/ferry metrics, and deltas from
the baseline; `--include-path` embeds the existing full route contract per
profile. Profile comparisons use the same graph and departure-time semantics
as individual route queries.
The served dashboard exposes the same comparison through its **Compare vehicle
profiles** panel. Enter one profile specification per line, submit the shared
trip, inspect baseline deltas in the table, and select any embedded profile path
to focus the live or offline map. Comparison coordinates, options, and profile
lines are persisted in `compare=1` URL state, and the returned JSON can be
downloaded from the panel. File-opened reports cannot call this local endpoint;
serve the report with `ireland-geometry-serve`.
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
Stage-cache version 5 also records the exact fingerprint inputs with each
reusable stage under `ireland-geometry.stage-cache.fingerprint.v1` and exposes stable dry-run reasons such as `cache_missing`,
`runtime_mismatch`, `fingerprint_inputs_missing`, `output_hash_mismatch`,
and `cache_hit`. This makes a
cache decision inspectable without rerunning the stage or reverse-engineering
the fingerprint.
Git revision remains available as audit metadata but does not invalidate a
stage by itself; controller edits that only change manifest/provenance writing
also leave effective analytical fingerprints unchanged.

Diagnostic-only reruns (`report`, `schema-audit`, `repro-check`, and `verify`)
preserve the existing analytical manifest context instead of replacing its
seed, Monte Carlo, source, or plan parameters with a partial invocation. The
new invocation is retained as `last_invocation`, and Doctor checks this
provenance-preservation contract.
The verifier also checks the preserved build parameters against the exact
commands stored in each stage fingerprint under
`ireland-geometry.manifest-cache.v1`; the machine-readable verification record
reports `pass`, `partial`, or `fail` plus any unavailable stages and parameter
mismatches. Diagnostic-only overrides are intentionally excluded from this
comparison. `ireland-geometry-doctor --json` mirrors the contract, status,
check count, mismatch count, and unavailable-stage list in its verification
summary.

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
Pass `--bundle` with a stage selection that ends in `verify` to add a final
complete-bundle operation after all report refreshes and verification passes;
the pipeline independently verifies the archive immediately after building it.
The default archive is beside the output directory as `<name>.bundle.zip`; use
`--bundle-archive` to choose another path outside the output directory. Dry-run
JSON exposes both operations under `post_validation_bundle` without writing the
archive.
The standalone `schema_audit.py` and `repro_check.py` diagnostics also write
their JSON contracts to `schema_validation.json` and `reproducibility.json`;
pass `--json` when a caller needs the same result on stdout without parsing a
human summary. Missing output or comparison directories are reported as
structured failed JSON results rather than raw tracebacks. Stable artifact
hashing is recursive, so nested output files participate in the same cross-run
comparison as top-level files. Doctor reports this diagnostic JSON capability.
The reproducibility diagnostic also fails closed on symlinked output files or
output directories and records their portable paths in the JSON result,
matching the manifest verifier and release gate.
The pipeline validates Monte Carlo/bootstrap counts, holdout fractions, and
stage selection before creating output directories or launching work; the
review CLI similarly validates its queue size before reading or writing
artifacts. It also fails closed when an existing `--out-dir` is a symlink or
non-directory,
including in dry-run mode, and Doctor exposes this boundary as
`capabilities.pipeline.rejects_output_symlink`.
The shared output-safety inventory covers the pipeline, report, schema audit,
columnar export, verifier, reproducibility, bundle, server, Pages, release,
and 19 independently runnable analytical stage CLIs under the versioned
`ireland-geometry.output-safety.v1` contract at `capabilities.output_safety`.
Its `rejects_non_directory` flag and `non_directory_guards` inventory cover
existing file paths across the core consumers; `stage_output_guards` and
`stage_output_tree_guards` confirm that each direct analytical stage resolves
`--out-dir` through the same fail-closed helper and rejects nested symlinks
already present below that root. The routing stage also applies the boundary
to its separate `--write-graph` destination, including nested symlinks already
present in the graph directory before a disk-backed graph is materialized.
Report generation and columnar export apply the same recursive boundary before
reading or replacing report-pack artifacts.
The standalone route query applies the same boundary to explicit `--out` and
`--geojson-out` files, rejecting directories used as files, symlinked
destinations, and symlinked parent directories; Doctor exposes this as
`capabilities.output_safety.file_output_guards`, which also covers the Doctor
JSON report and bundle archive destinations.
Bundle verification rejects symlinked archive inputs, and extraction rejects
symlinked destination parents; Doctor exposes these boundaries under
`capabilities.bundle`.
Strict Doctor readiness requires that inventory to be available, so a package
with incomplete output guards cannot present a green strict-ready result.
The companion `ireland-geometry.data-safety.v1` inventory at
`capabilities.data_safety` applies the same fail-closed rule to the pipeline's
data root, ingestion fetchers, and direct data-consuming stages. This prevents
an existing file or symlinked data root from being followed during cached-input
reads or source downloads.
The Overpass fetcher also rejects a symlinked `data/raw` cache directory, and
NIAH rejects symlinked regional extraction directories and nested links within
them before reading or writing individual cache files. Doctor exposes the
regional-tree check as `capabilities.data_safety.extraction_tree_guards`.
The same file-level guard rejects symlinked cached OSM JSON, NIAH ZIP/CSV,
combined JSON, and Geofabrik PBF inputs before they are read.
The Sentinel-2 writer also recursively validates its `data/satellite` cache
directory before downloading bands; Doctor exposes this as
`capabilities.data_safety.satellite_tree_guards`.
The primary `run_pipeline` command also rejects any nested symlink already
present under existing `data` or `output` roots before planning, dry-run output,
or stage dispatch; Doctor exposes this as
`capabilities.pipeline.rejects_nested_symlinks` and
`capabilities.data_safety.tree_guards`.
Standalone analytical data consumers use the same recursive data-tree guard,
and their explicit file or graph overrides reject symlinked inputs as well;
Doctor exposes the implementation under
`capabilities.data_safety.tree_guards` and
`capabilities.data_safety.input_guards`.
The standalone route query, verifier, and report server apply the same
fail-closed data/graph checks, while bounded output queries and schema audits
reject nested output symlinks before reading generated artifacts.
Doctor also inspects the active data and output trees, exposing
`capabilities.data_safety.observed_root` and
`capabilities.output_safety.observed_root` with relative symlink paths and
stable `data_root_invalid`/`output_root_invalid` strict-readiness blockers.
The complementary `ireland-geometry.file-write-safety.v1` capability applies
atomic replacement to ingestion downloads and cache files, so a failed stream
does not leave a partial destination and a destination symlink cannot redirect
the write. NIAH archive downloads and extracted CSV members are streamed through
the same helper to avoid buffering large regional inputs wholesale.
The standalone doctor reports supported Python/dependencies, cached required
inputs, optional-source coverage, generated outputs, pipeline stages, export
backends, dashboard modes, LiDAR/3-D adapters, validation gates,
report-serving capability, and Git state. Use
`scripts/doctor.py --json --out output/doctor.json` for a machine-readable
snapshot, or add `--strict` when a clean, fully cached, supported runtime with
a passing verification, schema-validation, and reproducibility gate is
required. After installation, the
equivalent console commands are `ireland-geometry`, `ireland-geometry-doctor`,
`ireland-geometry-serve`, `ireland-geometry-route`,
`ireland-geometry-route-matrix`, `ireland-geometry-route-compare`, and
`ireland-geometry-query`, `ireland-geometry-bundle`,
`ireland-geometry-schema-audit`, `ireland-geometry-repro`, and
`ireland-geometry-release-check`; each supports
`--version` and reports the installed distribution version. Generated manifests, Doctor JSON, and the local
`/api/metadata` response carry the same package identity and runtime signature
for audit trails. Direct source-checkout execution reads the declared
`pyproject.toml` version before falling back to installed distribution metadata.
Doctor separately reports whether each declared console command has a runnable
installed wrapper (`installed_count` and `installation_status`); this catches
an environment whose package metadata is newer than its executable scripts.
Strict Doctor readiness also requires the complete installed wrapper set, in
addition to clean provenance, cached inputs, generated outputs, and all three
validation records passing plus a current byte/hash-aligned output manifest
inventory. Changed, missing, unlisted, or symlinked output artifacts therefore
fail strict readiness even if persisted validation JSON still says pass. Use
`--max-input-age-days N` when freshness matters: the three
required source caches are reported with modification time and age, and strict
readiness fails if any exceeds the configured positive limit. Without that
option, freshness is still reported but does not impose an age policy.
Doctor also rechecks every manifest source that has recorded metadata against
the current filesystem under `ireland-geometry.source-alignment.v1`. The fast
default compares source availability, modification time, byte size, and a
recursive metadata SHA-256 fingerprint of paths/types/sizes/timestamps without
reading large source payloads; `release_check.py --check-input-hashes` enables
the slower full SHA-256 source gate for publication audits. The local report
server, health/capabilities/metadata/interpretation endpoints, runtime
endpoint, dashboard, and CSV/GeoJSON exports expose the same source-alignment
result so a changed input source makes analytical findings provisional instead
of silently treating an old output as current.
The read-only `ireland-geometry-release-check` command composes this strict
Doctor gate with current output-manifest artifact hash/size and inventory
checks, the Pages audit/freshness, and complete verified-bundle gates. It
rejects newly added unlisted output files as well as changed or stale listed
artifacts or any output symlinks, including symlinked diagnostic exceptions,
and requires `verification.json`, `schema_validation.json`, and
`reproducibility.json` to be explicitly represented in the current manifest.
The release checker also rejects symlinked or existing non-directory output and
Pages site roots before resolving them, so a valid target directory cannot hide
an unsafe caller path; Doctor exposes this as
`capabilities.release_check.rejects_symlink_roots` and
`capabilities.release_check.handles_non_directory_roots`.
Use `--strict --require-pages --require-bundle --bundle output.bundle.zip` for
a publication release, or `--skip-pages` when only a portable artifact is in
scope.
Doctor JSON also exposes `summary.strict_blockers` with stable blocker codes
and capped Git worktree path details, while the release check propagates those
codes into its `Doctor gate [code]` errors. This makes an intentional dirty
checkout, missing cache, stale input, failed validation, or incomplete command
installation directly diagnosable without rerunning lower-level checks.
Manifest and API source-freshness records use the versioned
`ireland-geometry.freshness.v1` contract.
Current input-to-manifest checks use the versioned
`ireland-geometry.source-alignment.v1` contract. Source records with hashes are
required to remain available and metadata-aligned; optional source records
without hashes remain explicitly unavailable rather than being treated as
analytical inputs. The default runtime/server check is metadata-only, while
the release checker can rehash every recorded source with
`--check-input-hashes`.
Source file and directory records also enforce a recursive symlink boundary:
the source hash helper returns no hash for a symlinked tree, and the alignment
gate reports every nested link instead of traversing it. A manifest created
from such a source retains `symlink_paths` so the unsafe input remains visible
until the source is rebuilt safely.
The recursive `metadata_sha256` catches nested file additions, removals, and
metadata changes even when a directory’s own modification time is unchanged;
the full content hash remains the authoritative release check for same-metadata
tampering.
When a project is moved, the runtime and verifier prefer the current
portable `relative_path` under the active project/data roots before falling
back to retained historical absolute paths. This prevents an old checkout
that still exists on disk from being mistaken for the relocated source.
The standalone verifier accepts `--project-root` and the pipeline passes its
active root explicitly, so this same resolution remains correct when data and
outputs live outside the project directory.
The OpenAPI 3.1 document declares the source-freshness object and its
timestamp/age fields so clients can validate metadata and capability responses
without relying on permissive extra properties.
The standalone and lazy report method/provenance panel also summarizes the
number of reported cached sources and the oldest observed source age; the
manifest remains the detailed source-by-source record.
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
packaged-schema paths. It also executes the packaged Pages publisher and the
reproducibility/bundle symlink guards against small verified fixtures.
Version assertions derive the expected value from the
installed distribution metadata in each environment, so a future package
version bump does not require a duplicated release literal in the workflow.
The same distribution carries the default preregistered analysis plan at
`schemas/analysis_plan.json`. A project-root `analysis_plan.json` overrides it;
otherwise the holdout stage uses the packaged plan and records its hash in
`analysis_plan_used.json`.
The source distribution also retains the root plan and optional LiDAR, road,
and boundary guidance files for users who unpack it as a project skeleton.
Distribution builds exclude Python bytecode caches from the installable wheel
and source archive. The package-smoke job inspects both archives and fails if
`__pycache__`, `.pyc`, or `.pyo` entries appear, keeping compiled local state
out of release artifacts.
The canonical Python 3.11 wheel, sdist, and SHA-256 manifest are retained as
downloadable artifacts for 14 days from successful CI runs. The Pages
deployment workflow runs only after the `Ireland geometry checks` workflow
succeeds, then checks out that exact validated commit before auditing and
uploading `docs/`.

`ireland-geometry-serve` binds to `127.0.0.1:8000` and serves
`report_lazy.html` by default. Use `--open` to launch it in the default
browser, `--port 0` to select an available port, or `--report report.html` to
serve the self-contained dashboard. The lazy dashboard requests only the
first 50 target rows from the structured JSON `POST /api/report/page`; its
filters, sorting, pagination, and CSV/GeoJSON exports stay server-side, and
its report-page/export requests use the same strict JSON contracts while the
URL state remains shareable. The query-string GET forms remain available for
direct links and existing clients. Opening the report does not download the
full target/geometry pack. Append `?offline=1` when a
full-pack, dependency-free offline review is intentional; the standalone
`report.html` remains fully embedded. The `__health` endpoint can be used by a
smoke test or local automation: it reports `ready: false` and
`status: "degraded"` when the lazy report lacks its required data pack, while
an embedded `report.html` does not require that pack. Clients that advertise gzip receive the
full `report_data.json` pack compressed on the wire (the browser transparently
decompresses it); the server caches the compressed bytes until the pack
changes and returns a deterministic `ETag`, so repeat requests can revalidate
with `If-None-Match` and receive `304 Not Modified` without another download.
In the current cached snapshot the initial page was 268,518 bytes for 50 rows,
versus roughly 116 MB for the full report pack.
The metadata API uses the same ETag revalidation contract. Pass an explicit
non-local `--host` only when the output directory is intended to be reachable
by other machines. Append `?offline=1` to either
dashboard URL to force the dependency-free SVG map for air-gapped review or
fallback testing. Health also probes the DuckDB, Parquet, CSV, and JSONL
exports: `query_available` is true only when at least one backend is readable,
`query_readable_backends` lists the usable formats, and
`query_backend_health` preserves per-backend errors for diagnosis.
All successful read-only API representations are conditionally cacheable. The
capabilities response publishes their paths in `conditional_endpoints`; each
response carries a deterministic `ETag` and `Cache-Control: no-cache`, and a
matching `If-None-Match` request returns `304 Not Modified`. This includes
health, metadata, interpretation, report pages/runtime/exports, analysis
queries, and all route forms.
Cacheable JSON responses at or above 1,024 bytes also negotiate deterministic
gzip when the request advertises `Accept-Encoding: gzip` (or `*`); such
responses carry `Content-Encoding: gzip` and `Vary: Accept-Encoding`, and their
ETags identify the encoded representation. The `api_json_gzip` and
`api_json_gzip_min_bytes` fields in capabilities publish this transport
behavior.
Every read-only API GET path also accepts `HEAD`, returning the same
representation headers (including `Content-Length`, ETag, cache, and optional
gzip headers) without transferring a body. The `head_endpoints` capability
list and OpenAPI `head` operations publish this monitoring/cache-probing
surface.
Every server response, including static report pages and data packs, carries
`X-Ireland-Geometry-Request-ID`. Clients may send a validated
`X-Ireland-Geometry-Request-ID` or `X-Request-ID` value and receive it back;
otherwise the server generates a correlation ID. API failures use the
`ireland-geometry.api-error.v1` JSON envelope with `error_code` and
`request_id`, including unknown API paths and unsupported API methods rather
than falling back to an HTML error page. Error responses are `no-store`, while
successful representations retain their existing deterministic cache contract.
Every access-log line includes `request_id=...`, so static-load and API
failures can be traced through the same local log. Capabilities and OpenAPI
publish the error contract, request-ID header, and log field.
Every server response also carries the baseline browser security policy:
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and a
`Permissions-Policy` disabling geolocation, camera, and microphone access.
Capabilities and OpenAPI publish the exact header values, and Doctor verifies
that the policy is present at the shared response boundary.
Responses also expose standard `Server-Timing: ireland_geometry;dur=...`
processing measurements, and API/health access-log lines include the
corresponding metric as `duration_ms` beside the request ID. Capabilities and OpenAPI
publish the timing header, metric, and log-field names; the measurement covers
server processing through response-header emission, not network transfer time.
The lazy dashboard also compares the polled runtime snapshot with the build it
first loaded. If the manifest changes, analytical readiness falls away, or
artifact alignment becomes provisional, it keeps the current filters and view
but exposes an accessible reload action so rows from an older data pack are
not silently mistaken for the current build. The standalone report carries
the same guarded UI contract but keeps the server-only notice inactive.
`ready` describes whether the selected report can be served; `analysis_ready`
is the stricter analytical gate and is true only when the provenance manifest
exists, verification/schema-validation/reproducibility records all pass, and
the current output tree still matches the manifest's byte/hash inventory and
the current input sources remain aligned with the manifest. The shared
`ireland-geometry.manifest-alignment.v1` result reports stale, missing,
unlisted, malformed, or symlinked artifacts; the companion
`ireland-geometry.source-alignment.v1` result reports current input drift.
Health, capabilities, metadata, interpretation, report-page/runtime, and
export surfaces expose both results alongside the per-record `validation`
status, so an automation client can distinguish a live dashboard from current,
validated artifacts.
The initial `GET /api/report/page` response also carries the
`ireland-geometry.report-runtime.v1` envelope and rewrites the dashboard's
initial `summary.analysis_ready`/interpretation gate from current filesystem
state, so a stale report pack cannot present a green analytical-readiness
banner while served locally.
The dashboard reapplies that envelope on every later page request and shows a
live runtime badge in the toolbar; if the output changes during a review, the
visible state and interpretation caveat become provisional instead of
remaining silently stale.
The report-page filtering and export helpers load geometry libraries lazily;
the packaged server can therefore serve these JSON/CSV/GeoJSON surfaces in a
minimal runtime, while full report generation still requires the declared
geospatial dependencies.
CSV and GeoJSON export responses also carry the
`ireland-geometry.report-runtime.v1` status through
`X-Ireland-Geometry-*` headers, and the dashboard labels non-passing exports
as provisional. This keeps binary downloads machine-readable without changing
their CSV or GeoJSON payload shapes.
The server refuses a symlinked output root or any nested output symlink before
binding, and its static file handler also returns `404` for links introduced
after startup; Doctor exposes this as
`capabilities.report_server.rejects_symlinks`. Doctor also reports whether the
server performs current manifest-artifact alignment under
`capabilities.report_server.checks_manifest_alignment` and whether filtered
exports carry the runtime headers under
`capabilities.report_server.report_export_runtime_headers`.
The server accepts the active `--project-root` explicitly and uses it for
source alignment across health, capabilities, metadata, runtime, report-page,
interpretation, and export responses; Doctor exposes this as
`capabilities.report_server.explicit_project_root`.
The direct readiness endpoint is inventoried as
`capabilities.report_server.report_runtime_api`.
The capabilities response lists every read endpoint that rechecks source
alignment under `source_alignment_surfaces`, making the consistency boundary
discoverable without hard-coding routes.
The dashboard capability inventory also reports whether both generated report
pages contain the live runtime-refresh contract under
`capabilities.reports.live_runtime_refresh`; a stale generated page is marked
incomplete instead of being treated as a fully capable dashboard.
When a served build's manifest identity changes, or a previously ready build
becomes provisional during a review, both dashboards expose an accessible
`Data updated — reload` notice with a reload action. Doctor inventories this
guard under `capabilities.reports.runtime_reload_notice` so a generated page
cannot silently retain rows from an older data pack.
If the lazy page API cannot be reached or a later page/filter request fails,
the dashboard keeps its shell and exposes an accessible `Retry report request`
action instead of replacing the page with a raw error or leaving an ambiguous
empty table. Doctor inventories this under
`capabilities.reports.report_error_recovery`; the initial lazy bootstrap guard
is `capabilities.reports.lazy_initial_load_retry`.
`GET /api/interpretation` returns the compact machine-readable interpretation
sidecar, including derived findings, source-aware caveats, and the validation
gate, without requiring automation to download the large `report_data.json`
pack. It returns `404` when the sidecar has not been generated and uses the
same deterministic ETag revalidation behavior.
The committed GitHub Pages site lives in `docs/` and embeds its own report pack
so it remains usable as a static site. Run
`python scripts/publish_pages.py --json` after a passing full pipeline build to
copy the authoritative `output/report.html` and `output/review.html` into
`docs/`. The publisher refuses failed verification or partial manifest/cache
alignment or incomplete schema/reproducibility validation, writes
`docs/pages_manifest.json` under
`ireland-geometry.pages-publish.v1`, stages and audits the complete candidate
before replacing the published files, checks every published source file
against the current output-manifest hash and byte record, and audits the
resulting site again. The publication site must be outside the analytical
output directory so a misconfigured publish cannot overwrite or contaminate
the verified output inventory; symlinked output and site roots are rejected as
well. Before
deployment, `python scripts/pages_audit.py --json` checks the embedded
target/GeoJSON identity set, report summary counts, scoring and manifest
provenance, publication hashes, and review-queue scope; the Pages workflow runs
this audit before uploading the site artifact. Doctor JSON also exposes the
publisher/audit modules, publication contract, published revision, file count,
and current Pages audit result under `capabilities.pages`.
Doctor reports the publisher’s source-artifact alignment capability as
`capabilities.pages.publisher_checks_manifest_artifacts`.
It also reports the publication boundary as
`capabilities.pages.publisher_rejects_site_inside_output`.
Symlink-root and existing-file enforcement are reported as
`capabilities.pages.publisher_rejects_symlink_roots` and
`capabilities.pages.publisher_handles_non_directory_roots`.
The direct Pages audit reports the matching root guard as
`capabilities.pages.audit_rejects_symlink_root` and
`capabilities.pages.audit_handles_non_directory_root`.
The same local server exposes `GET /api/route` for point-to-point routing, for
example `/api/route?start_lat=53&start_lon=-8&goal_lat=53&goal_lon=-8.002`.
Clients can also `POST` `/api/route` with an `application/json` body containing
`start` and `goal` `{lat,lon}` objects plus the shared route options and an
optional `format` of `json` or `geojson`; the body is capped at 128 KiB. The
structured form returns the same `ireland-geometry.route.v1` response and
reuses the GET validators, while the repeated-parameter GET remains available
for shareable links and backwards compatibility.
It accepts optional `speed_kmh`, `weight_t`, `rating_t`, `height_m`, `width_m`, `length_m`,
`axleload_t`, `vehicle_class=general|delivery|hgv|psv|taxi`,
ISO-8601 `departure`, `objective=distance|duration`, `include_path=1`, and
`include_ferries=1`
parameters. Add `format=geojson` for a GeoJSON `Feature`
with the reconstructed route geometry. Path-enabled JSON also exposes ordered
`path_way_ids`, directed `path_segments`, per-segment distance/duration/wait
metrics, persisted OSM `road_context`, static per-segment edge `constraints`,
conditional `conditional_rules`, relation-level turn `transition_rules`, and
`path_segment_source` (`sqlite_edges`, `portable_edges`, or `not_available`);
portable graphs retain supplied basic way context but not SQLite-only vehicle
or turn-rule evaluation. Geometry-derived `maneuver_n`/`maneuvers` records with
bearings, turn kinds, ferry transitions, and distance/duration to the next
maneuver; GeoJSON
keeps those route fields out of feature properties while preserving the route
geometry. The endpoint uses `data/roads` by
default; `--data-root` or `--road-graph` can point the server at another local
graph. Portable graphs expose the active backend as `portable_basic` and mark
ferry geometry available while ferry schedules remain unavailable. When the
graph carries `ferry_schedules.json`, a supplied departure
enforces its supported weekly, seasonal, fixed-date, and public-holiday service
windows, waiting for the next supported opening when necessary. Route responses
expose `ferry_wait_s` and `ferry_wait_n` so clients can distinguish service
waiting from crossing time. Responses also expose the unique ferry way IDs,
physical ferry distance, crossing time excluding waits, and ferry-edge count,
plus the supplied `vehicle_weight_t`, `vehicle_rating_t`, and `vehicle_class` profiles. The
`delivery` class activates delivery-only conditional access, the `hgv` class
activates numeric `maxweight:hgv` limits when `weight_t` is supplied; `rating_t`
separately activates numeric `maxweightrating:hgv` limits for HGV routes, and the `psv`
class activates public-service-vehicle conditional access on SQLite graphs;
`height_m` activates numeric legal and physical height limits, while `width_m`,
`length_m`, and `axleload_t` activate numeric `maxwidth`, `maxlength`, and
`maxaxleload` limits; ambiguous tags are treated as impassable for the
corresponding qualified routes.
For HGV routes, `allow_hgv_destination=true` (or the CLI
`--allow-hgv-destination`) enables the supported destination-only access and
matching conditional exceptions; static destination-only access remains
blocked by default. Unrelated numeric limits remain enforced.
Duration estimates also apply supported numeric OSM `maxspeed` ceilings per
way on the v21 SQLite graph. Safely supported conditional speed schedules are
evaluated from an explicit `departure`; unsupported clauses remain reported
but unevaluated. Supported conditional one-way windows are evaluated from the
same departure profile, with unsupported qualifiers reported and left at the
base one-way state.
The default `general` profile leaves delivery-only ways unavailable and treats
psv and taxi windows as class-specific access windows. The supported HGV
destination forms above are modeled; other destination-dependent and
unloading-specific HGV conditions remain outside the contract. Numeric
`maxweightrating:hgv` limits are evaluated from the separate HGV `rating_t`
profile, while ambiguous values are fail-closed only for rating-qualified routes.
Public-holiday windows are evaluated when the graph directory carries the
explicit `public_holidays.json` contract; the cached graph includes the
2023–2030 Irish calendar and route responses expose its count and date bounds.
Graphs without the companion report that the calendar was not provided.
Responses expose both contract statuses and any modeled crossing durations.
The served dashboard includes the same route form, displays the returned
distance/duration/snap/vehicle-weight/ferry-breakdown/wait/maneuver metadata,
renders an accessible clickable maneuver list and a bounded per-segment path
detail table with mapped-road context, expandable static/conditional/turn-rule
evidence, and aggregate check counts,
and draws the returned path on either the live Leaflet map or the dependency-free
offline SVG map. It also preserves the full optional path/GeoJSON payload. In
standalone `file://` mode it remains visible but explains that the local server
is required. Route inputs are written into shareable URL state; `Copy link`
restores the form and reruns the query when opened, while `JSON` and `GeoJSON`
buttons download the latest route response without requiring a second CLI/API
call. The dashboard submits the route form through the structured POST body;
the URL state remains independent of the request method.
For repeated logistics or accessibility checks, the same server exposes the
bounded read-only `GET /api/route/matrix` contract. Supply repeated
`origin=lat,lon` and `destination=lat,lon` parameters; the Cartesian product is
limited to 25 pairs and returns ordered compact distance/duration/reachability
summaries with snap metadata. It accepts the same vehicle, departure, objective,
and ferry profile parameters as `/api/route`. `include_path=1` adds the full
versioned point-to-point route response under each pair, including segment and
restriction evidence, while the default keeps matrix responses compact. The
route-matrix contract is discoverable from `/api/capabilities` and
`/api/openapi.json`. Clients can also `POST` the same query to
`/api/route/matrix` as an `application/json` object with `origins` and
`destinations` arrays of `{lat,lon}` objects plus the shared route options;
the JSON body is capped at 128 KiB and is validated through the same contract.
The dashboard uses this structured form, while the repeated-parameter GET
form remains available for simple links and backwards compatibility.
The served dashboard now exposes the same capability through a **Route matrix**
panel. Enter one origin and destination per line, reuse the route form's
vehicle/departure/objective options, and run up to 25 ordered pairs. Compact
results show reachability, distance, duration, ferry wait, and arrival; when
pair paths are requested, selecting a row focuses its route on the live or
offline map. Matrix inputs and options persist in `matrix=1` URL state, and the
raw `ireland-geometry.route-matrix.v1` response can be downloaded as JSON.
For same-trip vehicle checks, `GET /api/route/compare` accepts the four
`start_*`/`goal_*` coordinates plus repeated URL-encoded `profile` values such
as `profile=general` and
`profile=hgv;vehicle_class=hgv;weight_t=7.5`. The first profile is the
baseline; two to eight profiles are allowed. Shared `objective`, `departure`,
`speed_kmh`, `include_path`, and `include_ferries` parameters use the same
semantics as `/api/route`. The endpoint returns
`ireland-geometry.route-comparison.v1`, and is discoverable from
`/api/capabilities` and `/api/openapi.json`. Clients can also `POST` the same
comparison to `/api/route/compare` as an `application/json` object with
`start`/`goal` `{lat,lon}` objects, a `profiles` array, and those shared
options; the body is capped at 128 KiB. The dashboard uses this structured
form, while the repeated-parameter GET remains available for backwards
compatibility.
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
Clients can also `POST` the same bounded query to `/api/query` as an
`application/json` object containing the flat `backend`, `limit`, `offset`,
filter, finite-score, and cursor fields; unknown fields, non-integer page
values, non-finite numbers, unsupported media types, and bodies over 128 KiB
are rejected before the backend is opened. The response remains
`ireland-geometry.query.v1`, and the repeated-parameter GET remains available
for links and existing clients.
`GET /api/metadata` provides a compact build contract with manifest parameters,
row counts, source/artifact counts, verification, schema-audit, and
reproducibility status, plus usable export backends; each export backend now includes declared status,
file availability, runtime readability, and any probe error, alongside the
`readable_backends` list. Its response links to the full local JSON records.
`GET /api/report/page` exposes the versioned
`ireland-geometry.report-page.v1` contract with bounded target pages and
filter options. Each `page` object includes `has_more` and an explicit
`next_offset` continuation value when another page exists, or `null` at the
end, so clients do not need to reconstruct offsets. Every supported sort uses
ascending `osm_id` as its deterministic tie-breaker, published as
`page.sort_tiebreaker`. `GET /api/report/runtime` exposes the current compact
`ireland-geometry.report-runtime.v1` readiness envelope directly, with the
same deterministic ETag revalidation behavior as other metadata surfaces. The
envelope also carries a stable snapshot identity: manifest generation time,
Git revision/dirty state, package and manifest schema versions, and the
current manifest SHA-256. Doctor inventories this under
`capabilities.report_server.report_runtime_snapshot`, and the dashboard badge
shows the active build revision when it is available.
`GET /api/report/export?format=csv|geojson` exposes the
matching `ireland-geometry.report-export.v1` export contract. Both endpoints
also accept strict `application/json` `POST` bodies: the page form carries the
same flat filters plus `limit`, `offset`, and `initial`, while the export form
requires `format` and carries the same filters. Unknown fields, invalid types,
unsupported media types, and bodies over 128 KiB are rejected before the
server-side pack is opened; the existing GET forms remain available for links
and existing clients. Both methods reuse the dashboard predicate and reload
the server-side pack only when its mtime/size changes. `GET /api/capabilities` provides a single discovery document for local
automation: it reports report readiness, analytical validation readiness,
package version, endpoint paths,
versioned response contracts, query limits/cursors, route availability, and
per-backend readability. The `routing_graph` inventory identifies the active
`sqlite` or `portable` backend, its `profile_semantics`, active path-segment
sources, and feature flags for vehicle profiles, conditional rules, turn
restrictions, ferry geometry, ferry schedules, way context, and path explainability. The route,
matrix, and comparison endpoint entries repeat those active graph semantics so
clients do not mistake project-level implementation support for features
available in the graph currently loaded by the server. The same inventory now
reports metadata-backed node, directed-edge, ferry-edge, and way-context counts
when the graph sidecar provides them, plus a `metadata_status` of
`available`, `not_provided`, or `invalid`; missing or invalid sidecars leave
the individual count fields explicitly null. Its top-level `contracts`
map is the canonical list;
the JSON surfaces identify themselves with stable
contracts: `ireland-geometry.health.v1`, `ireland-geometry.query.v1`,
`ireland-geometry.metadata.v1`, `ireland-geometry.route.v1`, and
`ireland-geometry.route-matrix.v1`, `ireland-geometry.route-comparison.v1`, and
`ireland-geometry.capabilities.v1`, plus
`ireland-geometry.interpretation.v1`,
`ireland-geometry.report-page.v1`, and
`ireland-geometry.report-export.v1`, plus the nested
`ireland-geometry.report-runtime.v1` envelope used by report pages and export
headers, and the direct runtime endpoint. The server-backed dashboard polls
that endpoint every 30 seconds while open, so an idle review becomes
provisional when the output tree changes or the runtime check becomes
unavailable. `GET /api/openapi.json` serves a
dependency-free OpenAPI 3.1 document for those read-only endpoints, including
the interpretation sidecar, paginated report filters/exports, query
filters/cursors, route coordinate/profile parameters, response schemas,
and the `ireland-geometry.openapi.v1` document contract. The schema endpoint
uses the same deterministic ETag revalidation behavior as the other local
metadata surfaces.
Filter and sort choices are mirrored into the URL (`q`, `group`, `score`,
`angle`, and related parameters), so a reviewed dashboard view can be copied
and reopened without losing its state.
The `Review state` filter is included in that URL state, so reviewed,
ambiguous, supportive, and not-yet-reviewed candidates can be triaged without
leaving the dashboard.
The route form uses the same shareable-state contract (`route=1` plus prefixed
route profile parameters), so a route investigation can be reopened with its
coordinates, vehicle profile, departure, objective, path, ferry, and response
settings intact.
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
| `scoring_config.json` | Versioned exploratory scoring contract, normalized weights/thresholds, and hashes for the plan and effective configuration. |
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

The score weights and thresholds are versioned in the tracked analysis plan
under `ireland-geometry.exploratory-score.v1`. The analyzer emits the normalized
configuration and both plan/configuration hashes in `scoring_config.json`, so a
changed screening rule becomes an explicit analytical input and cannot be
mistaken for an inferential result.

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
  `no_*`/`only_*` turn restrictions are applied. The cached graph stores 78
  parseable conditional restriction relations, including 74 time windows and
  one vehicle-weight condition; time windows can be evaluated with
  `--departure` and `--speed-kmh`, while weight conditions require
  `--weight-t` (or API `weight_t`). Without those profiles the corresponding
  rules remain inactive and are reported as such. The SQLite graph also
  evaluates the weight-qualified delivery access rule
  `delivery @ (weight>5T)` when a delivery vehicle weight is supplied.
  Supported conditional road
  access rules such as `motor_vehicle:conditional=yes @ (19:00-07:00)` and
  deny windows are also evaluated from the same departure profile. The SQLite
  graph additionally retains 19 delivery-vehicle, 7 public-service-vehicle,
  and 6 taxi rules, including one weight-qualified rule, and applies directional
  conditional access by
  traversal direction; select
  `--vehicle-class delivery` (or `vehicle_class=delivery`) to activate them,
  while the default general profile remains blocked from delivery-only ways.
  Use `--vehicle-class psv` (or `vehicle_class=psv`) for public-service-vehicle
  windows and `--vehicle-class taxi` (or `vehicle_class=taxi`) for taxi-specific
  windows. Unsupported conditional-access tags remain explicitly reported, excluded
  from the default graph, and are not evaluated. The portable CSV fallback omits
  conditional-tagged ways because it cannot evaluate per-way profiles. Four
  conditional references remain unresolved because their source ways are
  pedestrian/cycleway-only. The v21 SQLite graph retains 1,101 generic
  `maxweight` ways, including 1,100 numeric limits across 9,918 segments, and
  35 numeric `maxweight:hgv` ways across 289 segments; an explicit
  `--weight-t`/`weight_t` profile enforces generic limits, while the `hgv`
  profile additionally enforces HGV-specific limits. One ambiguous generic tag
  is fail-closed only for weighted routes. It also retains 216 routable
  `maxweightrating:hgv`/Irish `maxweightrating:goods` ways, including 214
  supported numeric limits and 2 ambiguous values across 3,180 segments;
  `--rating-t`/`rating_t` enforces these limits only for the explicit HGV
  class, separately from `--weight-t`.
  The graph also retains numeric legal
  `maxheight` limits on 1,509 ways across 9,324 segments and physical
  `maxheight:physical` limits on 12 ways across 25 segments; `--height-m` or
  API `height_m` enforces both, while ambiguous values are fail-closed only
  for height-qualified routes. It also retains numeric `maxwidth` on 14 ways
  across 131 directed segments, `maxlength` on 5 ways across 95 segments, and
  `maxaxleload` on 10 ways across 84 segments. Their corresponding
  `--width-m`/`width_m`, `--length-m`/`length_m`, and
  `--axleload-t`/`axleload_t` profiles enforce those limits. The PBF snapshot
  has no ambiguous values for these three tags; destination- or condition-
  dependent variants remain outside this contract. It also retains numeric
  `maxspeed` on 196,440 routable ways, including 196,435 supported ceilings
  across 2,115,207 directed segments and 5 non-numeric values across 19
  segments. Duration routing applies supported ceilings per way while keeping
  the configured speed as an upper bound. The v21 edge contract stores 225
  `maxspeed:conditional` ways across 2,638 directed segments; 185 supported
  schedules are evaluated from explicit departures and 40 unsupported clauses
  remain retained but unevaluated. It stores
  five conditional one-way ways across 31 directed segments in
  `oneway_conditional_json`; two supported schedule ways are evaluated from
  explicit departures, while three unsupported permit/private clauses retain
  the base one-way semantics. It stores
  1,549 ferry segments from 77 access-allowed ways and 38 route relations; use
  `--include-ferries` to include their geometry. SQLite graph builds can
  persist the `ireland-geometry.ferry-schedules.v1` companion contract;
  supported weekly, seasonal, fixed-date, and public-holiday `opening_hours`
  windows are enforced for explicit departures. The cached graph carries the
  separate `ireland-geometry.public-holidays.v1` `public_holidays.json`
  contract with 2023–2030 dates; route metadata exposes the supplied coverage
  bounds. Graphs without the companion report the calendar as not provided.
  Optional per-way crossing durations affect arrival estimates. The cached
  graph carries 26 durations and 4 parsed schedules, 1 of which requires that
  calendar. Terminal platform semantics and service frequency are not modeled.
  Geometry-derived maneuver records are route inspection guidance rather than
  full turn-by-turn navigation: they use graph-node bearings and mapped way
  identity, and do not model lanes, signage, traffic, or platform-specific
  ferry instructions.
  Access restrictions are applied from the most specific motor-vehicle tags.
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
coverage. It also validates the `ireland-geometry.freshness.v1` source-age
record, including timestamp arithmetic, source-row alignment, and current
filesystem modification times. The manifest builder and verifier recursively
cover nested output files and reject output symlinks, duplicate or external
manifest paths, and unlisted output coverage. Its path contract preserves portable relative references alongside
the legacy absolute paths, and verification can resolve an artifact after the
project has been moved. It rejects duplicate or external manifest paths and
unlisted output files. Atomic writes make
each stage safe to rerun after interruption.
`ireland-geometry-bundle` packages those relative output artifacts into the
versioned `ireland-geometry.bundle.v1` ZIP contract. The archive contains a
machine-readable `bundle.json` with per-file SHA-256 hashes and fixed ZIP
timestamps, so two bundles made from unchanged inputs are byte-identical.
The archive destination must be outside the output directory; this prevents a
release ZIP from becoming an unlisted output artifact on the next manifest or
release audit.
Workspace-only `doctor.json` and `stage_cache.json` files are excluded by
default; repeat `--exclude` to omit additional relative paths or globs. A
recipient can validate the archive in place without extracting it:
`.venv/bin/ireland-geometry-bundle --verify ireland-geometry.bundle.zip`.
Extraction also verifies every hash first and requires a new destination:
`.venv/bin/ireland-geometry-bundle --extract ireland-geometry.bundle.zip --destination restored-output`.
For a publication/share gate, add `--require-verified`; bundling then fails
unless the output contains `manifest.json`, a passing `verification.json`, and
passing `schema_validation.json` and `reproducibility.json` records whose
current bytes and SHA-256 values match the manifest. Bundle creation also
rejects symlinked output files or output directories, so the archive cannot
silently omit an output path. All four source records must remain in the archive;
their hashes, path contract,
validation statuses, and row counts are retained in `bundle.json`, and archive
verification checks those provenance records for consistency as well as every
payload hash.
The CLI preserves the supplied output and archive paths through this check.
Add `--json` to bundle creation, verification, or extraction to receive the
same result as a machine-readable JSON document, including archive counts and
verified provenance; this avoids parsing human-oriented console text in release
automation.
Doctor exposes whether the installed bundle implementation enforces this
complete validation guard as `capabilities.bundle.requires_complete_validation`.
The unified release check also compares the bundle's four provenance hashes
with the current output directory, so an older valid archive cannot be
mistaken for the current release. Doctor exposes whether the release
implementation performs the current output inventory check under
`capabilities.release_check.checks_output_inventory`.
It reports the matching symlink invariant under
`capabilities.release_check.checks_output_symlinks`.
It also reports publication-root enforcement under
`capabilities.release_check.rejects_symlink_roots`.
Doctor also reports recursive, symlink-safe manifest coverage under
`capabilities.validation.manifest_coverage`.
It reports the release gate’s explicit validation-record requirement under
`capabilities.release_check.checks_validation_records`.

## Licensing and attribution

- OpenStreetMap contributors, ODbL 1.0.
- National Inventory of Architectural Heritage / Department of Housing,
  Local Government and Heritage, CC BY 4.0.
- Sentinel-2 / Copernicus and Esri World Imagery are used according to their
  respective terms for optional visual/reference layers.

## Security

Report vulnerabilities privately using [SECURITY.md](SECURITY.md). Do not publish private source datasets, credentials, or deployment state.
