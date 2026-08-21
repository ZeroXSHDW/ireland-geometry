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

## Cruth Field Atlas V2 — 2026-08-21

The public-facing report now opens as an immersive field atlas before revealing
the analytical dashboard. The V2 layer adds:

- a dismissible cinematic opening sequence with a mathematical orbit motif;
- a new “Irish field” chapter connecting coordinates, county context, named
  places, heritage, form, and shared life;
- live, interactive signal cards for golden ratio, golden angle, reflective
  symmetry, and orthogonal screening flags, calculated from the report pack and
  traceable into the pattern filter and map;
- a live WGS84 coordinate stamp, selection/county context, a Land → Coordinate
  → Footprint → Geometry → Heritage → Culture → Possibility sequence rail, and
  an explicit measurement ledger for area, perimeter, scale, aspect,
  compactness, and radial variation;
- V2 release language, replay controls, responsive layouts, reduced-motion
  handling, and a field-first atlas navigation state;
- generator parity in `scripts/report.py`, regenerated standalone/lazy reports,
  refreshed Pages publication hashes, and passing Pages/report/full test gates.

The visual layer remains explicitly non-inferential: mathematical screens are
shown as prompts for inspection and contemporary design hypotheses, not proof
of historic intention or a substitute for Irish lived culture.

## Geometry dossiers and map constellation — 2026-08-21

- Extended the selected-place card from a signal summary into a measured
  geometry dossier showing area, perimeter, length × width, aspect ratio,
  circularity, rectangularity, radial coefficient of variation, and Fourier
  descriptors F₁…F₄ for the selected footprint.
- Added a live map constellation that reports signal counts and percentages for
  the active field, with the current page label preserved for server-backed
  views and county-context coverage shown beside it.
- Kept the evidence boundary explicit in both surfaces: descriptors describe
  mapped geometry in the dated snapshot and do not establish historical intent.
- Regenerated the standalone/lazy reports and Pages artifact; pipeline
  verification, Pages audit, Node parsing, local HTTP, formatting, and the
  full project suite pass.

## County lens and place navigation — 2026-08-21

- Added a first-class county selector to the atlas filters and URL state so
  county-specific geometry, heritage, and cultural questions can be reopened
  and shared.
- Added top-county chips in the Contae / county mosaic; each chip applies the
  same county lens and returns the visitor to the target field.
- Added a data-derived county field note with target totals, NIAH coverage,
  named-place context, and golden-ratio/golden-angle rates where the full
  embedded snapshot permits them; lazy mode labels page-versus-total scope.
- Kept static and lazy report modes aligned, including the paginated report
  API, CSV/GeoJSON export filters, filter options, and OpenAPI discovery.
- Added report/API regression coverage and regenerated the published artifact;
  verification, Pages audit, Node parsing, local HTTP, formatting, and the
  full project suite pass.

## Shareable place focus — 2026-08-21

- Added a `focus` URL state for selected footprints, preserving county,
  pattern, cultural, and other active filters around the selected place.
- Added a “Copy place link” action to the selected-place card; static, lazy,
  and offline initialization restore the selected footprint when it is
  available in the current data view.
- Clearing a selection or filtering it out removes stale focus state, keeping
  shared links honest about what the current atlas is showing.
- Regenerated the published artifact; Pages audit, Node parsing, HTTP,
  formatting, and the full project suite pass.

## Mathematical grammar index — 2026-08-21

- Added an interactive Maths chapter to the field atlas with twelve cards for
  the report’s exact measured grammar: golden and Fibonacci proportions,
  Fibonacci dimensions, golden angle, reflective and rotational symmetry,
  orthogonality, circularity, rectangularity, aspect ratio, radial variation,
  and Fourier descriptors.
- Each card shows its equation, plain-language interpretation, and either the
  measured screen count or its descriptor scope. Screen cards trace into the
  existing pattern filter, table, and map; descriptor cards point visitors back
  to the measured target rows and selected geometry dossiers.
- The chapter is responsive, keyboard-addressable, URL-state compatible, and
  keeps the evidence boundary visible: mathematical resemblance is a prompt
  for inspection, not proof of historic intention or one Irish tradition.
- Regenerated and published the atlas; the pipeline, generated-JavaScript
  parser, Pages audit, local HTTP check, formatting, and full test suite pass.

## Field-to-studio reference bridge — 2026-08-21

- Added a “Carry geometry to studio” action to every selected footprint so the
  visitor can move from a real Irish place into the contemporary civic test-fit
  without losing its OSM identity, place context, heritage join, or measured
  geometry.
- Added a responsive field-reference panel inside the design studio showing
  the selected footprint’s dimensions, area, aspect ratio, circularity,
  screening signals, and NIAH status. A guarded “Use measured width as module”
  control lets the visitor translate observed scale into the editable design
  arithmetic while preserving the original reference.
- Extended the copy/download design brief with a field-reference section and
  kept the boundary explicit: this is a contemporary translation of measured
  evidence, not a reconstruction of historic intent.
- Regenerated and published the atlas; report tests, generated-JavaScript
  parsing, pipeline verification, Pages audit, local HTTP, formatting, and the
  full project suite pass.

## Heritage decade timeline — 2026-08-21

- Turned the existing `niah_decades.csv` evidence into a visible Oidhreacht /
  time field inside the cultural lens: 24 decade markers show dated worship
  rows, golden-angle rate, era-matched control rate, and the report verdict.
- Added an accessible decade readout with risk difference and adjusted-p-value
  context. Selecting a marker maps the decade to the existing NIAH century
  filter, target table, map, and shareable URL state; changing or clearing the
  filter removes stale timeline selection.
- Kept the analytical boundary explicit: the timeline describes a dated NIAH
  subset and screening comparisons, not a claim about period-wide intent or a
  single Irish architectural tradition.
- Regenerated and published the atlas; pipeline verification, report tests,
  generated-JavaScript parsing, Pages audit, local HTTP, formatting, and the
  full project suite pass.

## Two-footprint field comparison — 2026-08-21

- Added a comparison tray to the selected-place dossier: visitors can hold one
  real footprint as Field A, select another point or table row as Field B, and
  read both place context and measured geometry together.
- The side-by-side ledger reports area, aspect ratio, circularity, radial
  variation, shared screening signals, and signals unique to each footprint;
  differences are labeled explicitly as B − A from the current snapshot.
- Added remove/clear controls, full-state button labels, responsive layout, and
  an evidence caveat that keeps geometric overlap separate from shared
  authorship, period identity, or historic intent.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, formatting, and
  the full project suite pass.

## Shareable field comparison — 2026-08-21

- Added `compare_a` and `compare_b` URL state so a two-place geometry reading
  can be reopened with its active filters and sort state intact, without
  colliding with the route-comparison `compare=1` contract.
- Added a “Copy comparison link” action with clipboard feedback and a clear
  address-bar fallback; remove/clear/add actions keep the URL synchronized.
- Static embedded pages restore both targets directly. Lazy pages restore only
  targets present in the current page and announce any missing target instead
  of presenting a partial pair as complete.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## Talamh land threshold — 2026-08-21

- Added a data-driven land-context layer to the Irish cultural lens using the
  existing nearest mapped drivable-road samples by historic, worship,
  government, civic, and control group.
- Added an interactive group readout that can carry a target group into the
  existing filter and URL state, plus mapping-density bins for the current
  field and source-status context for routing, settlements, and boundaries.
- Kept the evidence boundary visible: centroid-to-road proximity is not a
  walking route, topographic/ecological model, settlement-quality score, or
  proof of design intent.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## Ainm named-place field — 2026-08-21

- Expanded the cultural `Ainm` lens from a count into a data-driven field of
  the most represented named settlement contexts, with row frequencies and
  embedded/lazy scope labels.
- Added named-place chips that carry a selected label into the existing text
  query, filter state, map, table, and shareable URL rather than creating a
  parallel search path.
- Kept the cultural boundary explicit: settlement labels come from the report
  context field and are not presented as etymology or a substitute for local
  knowledge.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## Selected geometry fingerprint — 2026-08-21

- Added a normalized canvas fingerprint to the selected-place dossier. Static
  embedded pages draw the selected GeoJSON outer ring with its measured extent,
  centre, and comparison guides while preserving the original source map/export.
- Added a descriptor-only guide for lazy views where the source ring is not
  embedded; it is explicitly labeled as a guide and never presented as an
  invented building boundary.
- Added accessible labels and explanatory copy tying the visual back to the
  measured descriptors and their non-historical evidence boundary.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## Shareable civic studio — 2026-08-21

- Added a `Copy studio link` action that serializes the selected equation,
  typology, season, material, grammar, all public-realm/performance/programme
  controls, and optional carried footprint reference.
- Studio links restore the scenario on load while preserving existing filters,
  focus, comparison, and route URL namespaces; lazy mode reports when a carried
  reference is not present in the current page.
- Kept the design boundary explicit: this is a contemporary civic test-fit,
  not a historic reconstruction or a construction/compliance model.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## Solas place-aware sky geometry — 2026-08-21

- Added a place-aware `Solas / sky geometry` layer inside the civic studio. A
  carried footprint now supplies its latitude and longitude to a seasonal
  solar reading; the uncarried studio uses the Ireland field centre explicitly.
- Derived approximate day length, solar-noon altitude, and sunrise/sunset
  bearings for the long-light, equinox, and low-light lenses, with an accessible
  visual horizon and plain-language orientation prompt.
- Added the same sky-and-place arithmetic to the copy/download concept brief,
  while keeping the boundary explicit: this is an indicative horizon estimate,
  not a site-specific daylight, glare, energy, or compliance model.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## Uisce water-event arithmetic — 2026-08-21

- Added a `Uisce / water geometry` layer to the civic studio with light,
  design-pulse, and heavy one-event rainfall controls.
- The studio now calculates covered module area × rainfall depth = event litres,
  then applies the editable capture setting and carries the typology’s water
  route into the readout.
- Added `studio_rain_event` to shareable studio URLs and to the copy/download
  concept brief, keeping the assumption visible and restorable.
- Kept the boundary explicit: the calculation is a transparent scenario
  arithmetic, not a hydrological, drainage, storage, flooding, water-quality,
  or compliance model.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## County geometry pulse — 2026-08-21

- Added a data-derived county constellation to the cultural lens. The embedded
  field now shows all 26 reported county contexts with visible target counts,
  NIAH reach, named-place (`Ainm`) counts, and golden-ratio/golden-angle
  screening rates.
- Each county card uses the existing `county` URL/filter path, keyboard-safe
  button semantics, and an active state; lazy views label the cards as current
  page scope rather than implying full-county totals.
- Kept the cultural boundary explicit: county pulses describe this snapshot’s
  mapped evidence and do not assign one architectural identity to a county.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## Makers named-design evidence — 2026-08-21

- Surfaced the existing architect-attribution evidence as a visible makers field:
  named-versus-unattributed comparisons plus the top source-linked names in the
  report pack.
- Maker chips reuse the existing text-query path to bring a recorded attribution
  into the target field; lazy mode keeps the attribution table pack-level while
  the resulting target field remains explicitly current-page/filter scope.
- Kept the evidence boundary explicit: attribution phrases are source-linked
  and exploratory, not proof of sole authorship, historic intent, or a shared
  architectural tradition.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## Pátrún spatial rhythm — 2026-08-21

- Added a spatial-rhythm field to the cultural lens using the existing Moran’s I
  neighbour screens, county-stratified golden-angle permutations, and spatial
  block-bootstrap intervals.
- Each cohort card shows the local similarity statistic, adjusted p-value,
  county-preserving difference, and block interval; selecting a card reuses the
  existing building-group filter and URL/view state.
- Kept the interpretation boundary explicit: these are situated diagnostics of
  the mapped snapshot, not proof of regional style, historic intent, or one
  Irish architectural tradition.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## Ciorcal multi-distance field scale — 2026-08-21

- Added a six-radius Ciorcal field to the cultural lens using the existing
  Ripley L(r) − r summaries at 100 m, 250 m, 500 m, 1 km, 2 km, and 5 km for
  the visible building cohorts.
- Each rail prints raw metric values while normalizing only the bar height
  within that cohort, so the shape can be read without hiding the underlying
  scale. Selecting a cohort reuses the existing group filter and view state.
- Kept the method boundary visible: translation correction and sampled
  bounding rectangles are reported, but the rail is not a significance envelope,
  route model, settlement-quality score, or proof of historic intent.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## Ailíniú directional-and-neighbour field — 2026-08-21

- Added an orientation field from the existing point-pattern artifact, placing
  worship and controls beside edge-bearing golden-angle shares, turn screens,
  peak bearing, and nearest-neighbour Fibonacci/sham comparisons.
- The worship card carries the existing group filter into Explore; the control
  field remains a reference distribution rather than a selectable target cohort.
- Kept the statistical boundary explicit: Monte Carlo and Poisson references
  remain exploratory point-pattern diagnostics, not evidence of conscious angle
  selection, cultural origin, or historic intent.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## Foinse source roots — 2026-08-21

- Added a visible source-roots field to the cultural lens from the existing
  historical source register: OpenStreetMap, NIAH, validated architect evidence,
  and the optional curated-history register.
- Friendly labels replace machine paths; status badges distinguish available,
  fallback, and not-provided layers. The evidence ledger also reports geometry
  validity, duplicate-centroid flags, and the expert review-queue count.
- Kept the provenance boundary explicit: missing curated history remains visible
  as missing rather than being filled by mathematical or heritage inference.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, Pages audit, local HTTP, and the full
  project suite pass.

## Cruth derived field print — 2026-08-21

- Added a second canvas study to the selected-place dossier beside the source
  boundary fingerprint. The field print translates aspect, circularity, radial
  variation, Fourier influence, symmetry flags, and golden-angle screens into a
  repeatable contemporary visual field.
- Added accessible labels and a descriptor-led note so the source outline and
  the derived study remain visibly separate; the print is explicitly not a
  historic ornament or a claim about cultural origin.
- Regenerated and published the atlas; pipeline verification, report/full
  tests, generated-JavaScript parsing, targeted canvas runtime, Pages audit,
  local HTTP, and the full project suite pass.

## Architect attribution precision — 2026-08-21

The NIAH architect extractor now requires explicit design, architect-role, or
tightly bounded design-context evidence before accepting a name. It handles
dated attributions, institutional role phrases, firms, initialed names, and
explicitly single-name architects while rejecting generic `by` references to
funders, military or railway bodies, clergy, map labels, and artwork suppliers.
The exact matched phrase remains in `architects_evidence.csv` with a high
confidence label. Refreshing the cached stage reduced the joined evidence
surface from 1,114 stale heuristic rows to 739 rows and the retained
per-architect table to 45 names; the worship and country-house comparison rows
remain available. The full report/manifest/verification refresh passes.

## Working rules

- Preserve raw downloads and existing generated artifacts while migrating.
- Keep output schemas backward-compatible where practical; add fields rather than silently removing them.
- Record meaningful changes, commands, timings, and verification results here.

## Cross-backend export parity contract — 2026-08-18

## Versioned exploratory scoring provenance — 2026-08-18

The footprint screening score is now driven by a versioned scoring block in the
tracked project and packaged analysis plans:

- `ireland-geometry.exploratory-score.v1` carries the score label, maximum,
  screening threshold, feature weights, and geometry thresholds;
- `scripts/analyze.py` accepts the plan explicitly, preserves legacy defaults
  for older plans, and writes `output/scoring_config.json` with normalized
  configuration, plan hash, and configuration hash;
- the analyzer stage cache fingerprints the selected plan, and verification
  rejects missing, unsupported, or mismatched scoring provenance and checks
  that `report_data.json` carries the same configuration hash;
- regression coverage includes plan parity, legacy fallback, hash changes, and
  non-inferential labeling. The full source suite now contains 197 tests.

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
the choice explicit. At that stage, ferry schedules, terminal platform
semantics, and service frequency were intentionally not inferred.

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

## Final six-hour audit pass — 2026-08-18

- The exploratory score is now plan-driven under
  `ireland-geometry.exploratory-score.v1`. The tracked and packaged plans carry
  the unchanged legacy weights/thresholds, `scripts/analyze.py` emits the
  normalized `output/scoring_config.json`, and the analyzer cache fingerprints
  the selected plan so a scoring-rule edit cannot reuse stale scores.
- `scripts/verify.py` checks the scoring contract, configuration hash, plan
  hash when the plan is available, non-inferential label, and report-data
  provenance alignment. The current report page exposes the scoring contract
  in its compact static payload.
- The existing national analysis was rerun from local cached inputs with
  `--no-network`, preserving 123,810 analyzed rows, 33,416 targets, and the
  existing score/ranking rules. The expensive unchanged 5,000-pair routing
  recomputation was stopped after roughly 26 minutes; its prior verified
  routing artifacts were retained, and downstream quality, holdout, columnar,
  schema, report, reproducibility, and verification stages all pass.
- The live initial lazy page measured 268,518 bytes for 50 rows versus the
  116 MB full report pack; the filtered worship/score export returned 10 rows
  and a 2,013-byte CSV. The server advertised
  `ireland-geometry.report-page.v1` and
  `ireland-geometry.report-export.v1` successfully.
- Final checks: 200 tests passed, Ruff, compileall, and `git diff --check`
  passed; dry-run JSON reported cache version 5, no writes, and cache hits for
  the refreshed analytical stages. Final verification passed for 123,810
  analysis rows / 33,416 targets.

## GitHub Pages deployment audit — 2026-08-18

- Added `scripts/pages_audit.py` as a standard-library-only deployment gate for
  the committed `docs/` site. It decodes the embedded dashboard pack and review
  queue, checks target/GeoJSON identity and summary counts, verifies scoring and
  manifest provenance fields, and rejects review ids outside the dashboard
  target set.
- The Pages workflow now runs the audit before configuring and uploading the
  Pages artifact, so malformed or internally inconsistent embedded data cannot
  be published by the workflow.
- Added focused unit coverage for aligned pages data and count/scope failures.

## Manifest/cache parameter alignment — 2026-08-18

- Added `ireland-geometry.manifest-cache.v1` to `scripts/verify.py`. It compares
  the preserved manifest build context with the exact command options embedded
  in reusable stage fingerprints, covering fetch policy, analysis-plan paths,
  optional source adapters, seeds/iteration counts, holdout settings, and
  routing limits/policies.
- The contract deliberately ignores diagnostic-only `last_invocation` values,
  while reporting missing stage records as `partial` and parameter drift as a
  verification failure. This closes the gap where a valid-looking manifest and
  valid-looking cache could otherwise describe different route workloads.
- The cached national lifecycle was refreshed offline. Verification passes for
  123,810 analysis rows / 33,416 targets, with all 28 alignment checks passing;
  Doctor exposes the same pass/check-count summary, and the source suite now
  contains 203 tests.

## Reproducible Pages publication — 2026-08-18

- Added `scripts/publish_pages.py` under
  `ireland-geometry.pages-publish.v1`. It accepts only a passing verification
  record with full manifest/cache alignment, copies the authoritative report
  and review artifacts into `docs/`, writes a redirect entrypoint, and records
  source revision/timestamps, output hashes, and verification provenance in
  `docs/pages_manifest.json`.
- Extended `scripts/pages_audit.py` to verify the publication manifest and every
  published file hash. The committed site was regenerated from the current
  verified output; its embedded report now carries revision `2bfd3a5`, passing
  verification, 123,810 analysis rows, and 33,416 targets.
- Added focused publisher success/refusal tests. The source suite now contains
  207 tests.
- Publication now builds and audits a temporary candidate before touching
  `docs/`; a failed candidate audit leaves an existing site unchanged. The
  regenerated site still passes the final post-copy audit.
- Pages publication now requires passing `schema_validation.json` and
  `reproducibility.json` in addition to final verification, and records their
  hashes/statuses in `pages_manifest.json`. Focused coverage includes refusal
  of a failed schema gate; the source suite now contains 207 tests.
- Final verification passed for the Pages audit, artifact byte/hash contract,
  Ruff, compileall, `git diff --check`, and both wheel and sdist package smoke
  tests; the local doctor reports a passing verification gate with 28 alignment
  checks and only the expected dirty-worktree warning.

## Pages capability inventory — 2026-08-18

- Doctor version 57 now exposes the static Pages publisher and audit modules,
  their versioned contracts, publication-manifest path, source revision, file
  count, and compact audit result under `capabilities.pages`.
- A checked-out site is reported `available` only when the same Pages audit used
  by CI passes; a project without a generated `docs/` site is reported
  `not_provided` rather than as a runtime failure.

## Complete Doctor readiness gate — 2026-08-18

- Doctor version 58 now treats verification, schema validation, and
  reproducibility as one required strict-readiness gate. The generated-output
  inventory includes `schema_validation.json` and `reproducibility.json`, and
  strict mode cannot report ready when either record is absent or failing.
- This aligns local Doctor readiness with the report server’s `analysis_ready`
  contract and adds regression coverage for the previously weaker verification-
  only path.

## Complete verified bundle gate — 2026-08-18

- The `ireland-geometry-bundle --require-verified` publication guard now
  requires and records passing `schema_validation.json` and
  `reproducibility.json` alongside the manifest and final verification record.
- `bundle.json` carries hashes, inclusion state, status, and counts for both
  independent validation records; bundle verification rejects missing,
  excluded, tampered, or failing validation provenance. CI now runs those two
  diagnostics before building its verified package smoke archive.

## Bundle capability reporting — 2026-08-18

- Doctor version 59 now exposes `capabilities.bundle.requires_complete_validation`
  so automation can distinguish the presence of a verified flag from an
  implementation that actually enforces verification, schema, and
  reproducibility together.

## Unified release/readiness gate — 2026-08-18

- Added `scripts/release_check.py` and the packaged
  `ireland-geometry-release-check` command under
  `ireland-geometry.release-check.v1`. The read-only contract composes Doctor
  strict readiness, the Pages audit, publication freshness against the current
  output report/review/provenance files, and optional complete-bundle
  verification.
- Pages freshness now compares the publication manifest's source hashes,
  revision, output-manifest timestamp, and published HTML bytes with the
  current `output/` directory. The integrated check passes against the real
  cached publication and would reject a stale site rather than relying only on
  internal site consistency.
- Doctor version 61 reports the unified gate and the nine-command installed
  surface. Wheel and source-distribution smoke tests verify the new entrypoint,
  packaged module, contract, and Doctor capability outside the source checkout.
- The source suite now passes 212 tests; Ruff, compileall, Pages audit, full
  verified bundle build/verification, and fresh wheel/sdist smoke all pass.

## Current-bundle provenance alignment — 2026-08-18

- The release gate now compares `manifest.json`, `verification.json`,
  `schema_validation.json`, and `reproducibility.json` hashes in a supplied
  verified archive with the current `output/` directory.
- A valid older archive is now rejected as stale; regression coverage mutates a
  current validation record after bundling and confirms the release gate fails.
- Doctor version 61 and the package-smoke assertions expose and verify the
  strengthened release capability.

## Current output artifact integrity — 2026-08-18

- The release gate now rechecks every artifact listed in `manifest.json`,
  including safe relative paths, byte counts, and SHA-256 values. This catches
  an output file changed after validation even when its old validation record
  still reports `passed: true`.
- Regression coverage includes the current-output pass path and preserves the
  stale-bundle rejection test; the gate remains read-only.

## Output inventory and diagnostic failure hardening — 2026-08-18

- The release gate now scans the current output tree and rejects files that are
  not represented by the manifest, while preserving the explicit workspace
  exceptions for `manifest.json` and `doctor.json`. Doctor version 62 exposes
  this as `capabilities.release_check.checks_output_inventory`.
- `repro_check.py` now returns a failed JSON contract for missing output or
  reference directories, including directory status and actionable errors,
  instead of leaking a `FileNotFoundError` traceback to automation.
- The source suite now passes 223 tests; focused release/Doctor/repro checks
  pass after the hardening changes.

## Recursive manifest verification — 2026-08-18

- The manifest builder now records regular files in nested output directories,
  preserving portable relative paths instead of silently omitting them.
- The standalone verifier now recursively checks output coverage and rejects
  symlinked output artifacts, including nested files. Regression coverage
  verifies listed nested artifacts, unlisted nested artifacts, and symlink
  rejection.
- Doctor version 63 reports the capability as
  `capabilities.validation.manifest_coverage`; CI/package assertions verify
  recursive and symlink-safe behavior in source and installed environments.

## Recursive reproducibility comparison — 2026-08-18

- `repro_check.py` now hashes nested regular output files using portable
  relative keys and skips symlinks, matching the manifest builder and verifier
  inventory rules.
- Regression coverage confirms nested stable artifacts participate in the
  reproducibility result while timestamped diagnostics remain excluded.

## Complete validation inventory — 2026-08-18

- The release output gate now requires explicit manifest records for
  `verification.json`, `schema_validation.json`, and `reproducibility.json`,
  in addition to checking their current hashes and byte sizes.
- A manifest that contains passing validation files but omits their artifact
  records is now rejected before publication or bundle release.
- Doctor version 64 exposes this release capability as
  `capabilities.release_check.checks_validation_records`.

## Pages source-artifact alignment — 2026-08-18

- `publish_pages.py` now refuses to publish if `report.html`, `review.html`,
  `verification.json`, `schema_validation.json`, or `reproducibility.json`
  differs from the current output manifest’s recorded bytes or SHA-256.
- Publication source paths also reject symlinks, and the candidate site is
  still audited before any committed files are replaced.
- Doctor version 65 exposes the publisher check under
  `capabilities.pages.publisher_checks_manifest_artifacts`.

## CI publication execution and Pages deployment gate — 2026-08-18

- The fresh wheel package-smoke now executes the packaged Pages publisher
  against a small verified report/review fixture, including the manifest and
  validation source-artifact checks, rather than only checking that the module
  is present in the wheel.
- The same package-smoke now executes the installed reproducibility and bundle
  symlink guards, including file and output-root cases.
- Verified bundle creation now independently checks every current manifest
  artifact's relative path, byte count, and SHA-256, including all three
  validation records; stale output is refused before archive creation.
- `pages_audit.py` now rejects symlinked required site files and invalid source
  manifest or verification SHA-256 values in `pages_manifest.json`.
- The Pages deployment workflow now waits for a successful `Ireland geometry
  checks` workflow run and checks out that run's exact commit before auditing
  and uploading the static site.
- Focused Pages tests pass; the full repository suite now contains 225 tests.

## Unified release symlink invariant — 2026-08-18

- The release output gate now rejects every output symlink, including
  `manifest.json` and `doctor.json`, which remain allowed only as unlisted
  inventory exceptions when they are regular files.
- Regression coverage exercises both exception paths and reports the checked
  `symlink_count` in the machine-readable output gate.
- Doctor and the installed-package CI assertions expose the capability as
  `capabilities.release_check.checks_output_symlinks`; the focused release and
  Doctor tests pass, with 227 tests in the full suite.

## Reproducibility symlink invariant — 2026-08-18

- `repro_check.py` now fails with structured JSON when an output file or output
  root is a symlink instead of silently omitting it from stable hashes.
- The result records `symlinks` and `reference_symlinks` using portable paths;
  it avoids writing a diagnostic through a symlinked output root.
- Doctor and CI expose this as
  `capabilities.pipeline.reproducibility_rejects_symlinks`; regression coverage
  passes for both file and directory symlinks, with 229 tests in the full suite.

## Bundle symlink invariant — 2026-08-18

- Bundle collection now fails instead of silently skipping symlinked output
  files or nested symlink directories, and the builder rejects a symlinked
  output root or archive destination.
- Doctor exposes the installed bundle behavior as
  `capabilities.bundle.rejects_symlinks`; focused bundle and Doctor coverage
  passes, with 231 tests in the full suite.

## Bundle CLI path-preservation hardening — 2026-08-18

- The bundle CLI no longer resolves `--out-dir` or `--archive` before the
  hardened builder sees them, so command-line use cannot bypass the symlink
  checks that direct API callers receive.
- End-to-end CLI regression coverage covers both symlinked output roots and
  archive destinations; the full suite now contains 233 tests.

## Verified bundle manifest alignment — 2026-08-18

- `ireland-geometry-bundle --require-verified` now requires a complete current
  manifest inventory and rechecks each listed artifact before bundling. Missing
  validation records, unsafe paths, stale bytes, and stale hashes fail closed.
- Doctor exposes this as `capabilities.bundle.checks_manifest_artifacts`; the
  package-smoke fixture now supplies the same manifest records used by the real
  cached output.
- Regression coverage rejects a tampered current artifact; the full suite now
  contains 234 tests.

## Report-server symlink boundary — 2026-08-18

- The local server now rejects a symlinked output root or any nested output
  symlink before binding, matching the verifier, reproducibility, release,
  bundle, and Pages publication invariants.
- Static GET/HEAD handling also refuses symlink components added after startup,
  so a local server cannot be turned into a path for exposing a linked file.
- Doctor version 66 exposes the behavior as
  `capabilities.report_server.rejects_symlinks`; source and packaged smoke
  coverage exercise the startup and request-time guards, with 236 tests in the
  full suite.

## Report-server current artifact readiness — 2026-08-18

- The local server now rechecks the current output tree against the manifest's
  recursive byte/hash inventory before reporting analytical readiness. Missing,
  stale, malformed, unlisted, or symlinked artifacts produce the shared
  `ireland-geometry.manifest-alignment.v1` result and force `analysis_ready` to
  false.
- Health, capabilities, metadata, and interpretation responses expose the same
  alignment record, so local automation receives the release gate's current
  artifact truth rather than only the persisted validation summaries.
- Doctor version 67 reports the implementation under
  `capabilities.report_server.checks_manifest_alignment`; focused stale-manifest
  coverage and packaged capability assertions were added. The full source
  suite now passes 237 tests.

## Doctor current output readiness — 2026-08-18

- Doctor strict readiness now invokes the same current output-manifest byte,
  hash, inventory, and symlink checks as the release gate. A persisted passing
  validation record cannot keep strict readiness green after an artifact is
  changed or an unlisted file is added.
- The result is available as
  `capabilities.validation.output_alignment`; tamper regression coverage now
  verifies that strict readiness fails closed while a complete current fixture
  remains ready.
- Doctor version 68 and both packaged CI capability assertions cover the
  strengthened gate; the full source suite remains at 237 tests.

## Runtime dashboard readiness envelope — 2026-08-18

- The initial lazy-dashboard page API response now exposes
  `ireland-geometry.report-runtime.v1`, including current validation and
  manifest-alignment results.
- When output has changed since report generation, the server rewrites the
  initial dashboard summary and validation finding to an incomplete/failing
  state instead of allowing persisted `analysis_ready: true` metadata to look
  current. Regression coverage exercises the stale-page response as well as
  the OpenAPI schema.
- Doctor version 69 inventories the runtime page capability, and wheel/sdist
  package assertions verify the new capability flag.

## Dependency-light packaged report serving — 2026-08-18

- `scripts/report.py` now imports geometry helpers only when generating
  polygon outlines. Server-side page filtering and CSV/GeoJSON export can be
  imported without eagerly loading Shapely, while full report generation keeps
  the declared geospatial dependency boundary.
- A subprocess regression blocks Shapely imports while exercising the helper
  APIs, and fresh wheel/sdist smoke tests now serve the runtime page endpoint
  in minimal environments. The full source suite now passes 238 tests.

## Report export readiness headers — 2026-08-18

- CSV and GeoJSON report exports now flatten the current
  `ireland-geometry.report-runtime.v1` envelope into stable
  `X-Ireland-Geometry-*` response headers, preserving the existing download
  payload shapes while exposing validation and manifest-alignment status to
  API clients.
- The export endpoint's OpenAPI response, capabilities discovery, and lazy
  dashboard client document and surface those headers; regression coverage
  checks both formats on an incomplete runtime.
- Doctor version 70 inventories the export-header implementation, and the
  packaged wheel/sdist assertions require the capability alongside the page
  runtime contract.

## Live dashboard runtime refresh — 2026-08-18

- The generated lazy dashboard now consumes runtime state from every
  paginated page response and from export headers, updating its readiness state
  and validation caveat when the output pack changes during a review.
- A toolbar runtime badge makes validated versus provisional state visible
  without changing the server-side page, CSV, or GeoJSON contracts; generated
  dashboard regression assertions cover the new client surface.

## Dashboard runtime capability inventory — 2026-08-18

- Doctor version 71 now checks both generated dashboard pages for the complete
  live runtime-refresh client: the runtime badge, page-response refresh, and
  export-header refresh handlers.
- The dashboard report capability is now marked incomplete when generated HTML
  predates that contract, preventing stale cached pages from appearing fully
  capable in diagnostics. Source and packaged Doctor assertions cover the
  versioned capability shape.

## Conditional runtime heartbeat endpoint — 2026-08-18

- The local server now exposes `GET /api/report/runtime` as a compact direct
  `ireland-geometry.report-runtime.v1` response. It reuses the current
  validation and manifest-alignment gate and supports deterministic ETag/304
  revalidation for automation clients.
- The server-backed dashboard polls that endpoint every 30 seconds while open;
  a failed heartbeat is shown as an unavailable last-known runtime rather than
  silently preserving a green badge. OpenAPI, capabilities discovery, Doctor,
  and packaged CI assertions cover the endpoint and its conditional behavior.
- Runtime interpretation patches now derive from an immutable baseline, so a
  later passing heartbeat restores the original finding and caveats after a
  transient stale or failed runtime.
- Doctor version 72 reports the endpoint under
  `capabilities.report_server.report_runtime_api`.

## Runtime snapshot identity — 2026-08-18

- The report-runtime envelope now includes stable identity metadata for the
  active manifest: generation time, Git revision and dirty state, package and
  manifest schema versions, and the manifest SHA-256.
- The generated dashboard includes the active build revision in its runtime
  badge, while unavailable manifests remain explicit rather than inventing an
  identity. OpenAPI and server regression coverage verify both the valid and
  unavailable snapshot shapes.
- Doctor version 73 inventories the source/server implementation under
  `capabilities.report_server.report_runtime_snapshot`; the packaged CI
  assertions require the capability alongside the existing runtime endpoint.

## Structured release-readiness blockers — 2026-08-18

- Doctor version 74 now exposes `summary.strict_blockers` with stable blocker
  codes for missing inputs/outputs, dirty or unavailable Git state, failed
  verification/validation/alignment, incomplete command installation, and
  stale required inputs.
- The Git state includes a bounded, JSON-safe worktree inventory with tracked
  and untracked counts, capped path records, and truncation metadata.
- `release_check.py` carries those blockers into the unified release result and
  renders `Doctor gate [code]` errors, making the existing strict gate
  actionable without changing its readiness rules. Regression tests cover the
  inventory, fail-closed Git behavior, and release propagation.

## Current input source alignment — 2026-08-18

- Doctor version 75 and the shared runtime now expose
  `ireland-geometry.source-alignment.v1`. The fast gate re-resolves every
  manifest source and compares availability, modification time, and byte size
  without reading large source payloads; hashed source records fail closed when
  they are missing, changed, or symlinked.
- `release_check.py --check-input-hashes` enables an explicit full SHA-256
  source audit for publication or handoff checks. The release result includes
  the source gate and its mode, counts, per-source status, and actionable
  errors.
- The local report server, conditional runtime endpoint, OpenAPI contract,
  lazy dashboard, and CSV/GeoJSON export headers now expose source alignment
  alongside validation and output-manifest alignment. A source mismatch forces
  `analysis_ready` false and marks findings provisional.
- Regression coverage includes metadata-versus-content tampering, missing
  hashed inputs, runtime ETag behavior, OpenAPI/export contracts, Doctor
  capability discovery, and release deep-hash refusal. The full source suite
  now passes 246 tests.

## Source-alignment read-surface parity — 2026-08-18

- Doctor version 76 now verifies that source alignment is rechecked and exposed
  consistently by `/__health`, `/api/capabilities`, `/api/metadata`,
  `/api/interpretation`, paginated report pages, the runtime heartbeat, and
  CSV/GeoJSON exports.
- The OpenAPI schemas require the shared source-alignment object on those
  readiness responses. `/api/capabilities` also publishes the canonical
  `source_alignment_surfaces` route list for automation clients.
- A changed manifest source now makes health/capabilities/interpretation
  analytical readiness false, degrades metadata, and rewrites the interpretation
  validation finding and caveat as provisional. Regression coverage exercises
  all four previously incomplete read endpoints together.

## Recursive source-tree symlink boundary — 2026-08-18

- Doctor version 77 and the shared runtime now enumerate symlinks inside every
  recorded source directory without following linked directories. Source
  hashing returns no digest for a linked tree, while metadata and full-hash
  alignment both fail with the relative link paths.
- `build_manifest` preserves source `symlink_paths` diagnostics when an input
  was recorded through an unsafe link, so the manifest cannot silently lose
  that provenance after the link is later replaced.
- The OpenAPI `SourceAlignment` schema exposes `symlink_count` and per-source
  `symlink_paths`. Regression coverage covers nested directory links, source
  hash refusal, manifest diagnostics, and the existing no-source response.
  The full source suite now passes 248 tests.

## Relocation-safe source resolution — 2026-08-18

- Doctor version 78 now shares the runtime’s source resolver with the
  standalone verifier. When a manifest retains both an old absolute path and
  a portable relative path, the active project/data roots are checked first;
  historical paths remain fallback candidates only.
- Regression coverage keeps the old and relocated files present with different
  bytes and verifies both runtime alignment and verifier resolution select the
  relocated source. The full source suite now passes 250 tests.

## Recursive source metadata fingerprint — 2026-08-18

- Doctor version 79 and the shared runtime now record `metadata_sha256` for
  regular source files and symlink-free directory trees. The digest covers the
  recursive relative path/type/size/mtime inventory without reading regular
  file contents, so fast alignment catches nested source-tree changes even
  when the directory root mtime is unchanged.
- `manifest_source_alignment` exposes `metadata_checked_count` plus expected
  and actual per-source metadata digests. The standalone verifier checks the
  same field, while the OpenAPI `SourceAlignment` schema and Doctor fallback
  contract remain complete when no manifest is available.
- Diagnostic manifest rewrites upgrade legacy source rows with the current
  metadata fingerprint while preserving their established content hashes;
  `--check-input-hashes` remains the authoritative deep check for content
  tampering that preserves metadata.
- Regression coverage includes nested road-graph drift, manifest upgrades,
  OpenAPI fields, and packaged/runtime consumers. The full source suite now
  passes 251 tests.

## Explicit verifier project-root contract — 2026-08-18

- The standalone verifier no longer infers the active project root from
  `out_dir.parent` when resolving manifest sources. `--project-root` is now an
  explicit CLI option, `run_pipeline.py` passes it on every verify stage, and
  source freshness, content hashes, and Git identity use the same root.
- Custom-layout regression coverage keeps old and relocated sources present
  while placing data and output outside the project tree. The verifier selects
  the active relocated source and accepts its freshness record rather than
  falling back to the historical checkout.
- This preserves the existing default behavior while making the documented
  relocation-safe path contract complete for separate `--data-root` and
  `--out-dir` deployments. The full source suite now passes 254 tests.

## Explicit report-server project-root contract — 2026-08-18

- Doctor version 80 now identifies the expanded diagnostic contract, including
  the explicit report-server project-root capability.
- The local report server now accepts `--project-root` and stores the active
  root on its server instance. Health, capabilities, metadata, report pages,
  runtime, interpretation, and CSV/GeoJSON export readiness all use it when
  rechecking manifest sources.
- Custom-layout API regression coverage keeps an old and relocated source
  present while placing output and data outside the project tree. Every tested
  readiness surface selects the active source, and export runtime headers stay
  aligned.
- Doctor now exposes the implementation as
  `capabilities.report_server.explicit_project_root`; default server behavior
  remains compatible for callers that do not provide an explicit root.
- The full source suite now passes 273 tests; wheel and sdist package smoke,
  live API checks, release artifact alignment, and the Pages audit also pass.

## Current verified snapshot bundle — 2026-08-18

- Built and independently verified the current `output.bundle.zip` with the
  complete `ireland-geometry.bundle.v1` publication guard: 62 artifacts and
  531,878,667 payload bytes.
- The required-bundle release check passes output inventory, full source hashes,
  Pages freshness, bundle provenance, and archive/current-output alignment; the
  only remaining blocker is the intentionally uncommitted worktree.

## Pipeline-integrated verified bundle — 2026-08-18

- Added opt-in `run_pipeline.py --bundle` and `--bundle-archive` flags. The
  pipeline requires `verify`, waits until the post-validation report refresh
  and final verification have completed, then invokes the complete
  `--require-verified` bundle guard.
- Dry-run JSON exposes the archive path plus `bundle` and `bundle-verify`
  operations under `post_validation_bundle`; Doctor v85 reports the capability
  and both command guards under `capabilities.pipeline.post_validation_bundle`.
- The offline pipeline smoke now creates and independently verifies its bundle,
  and packaged CI smoke covers the option and dry-run contract.

## Standalone bundle output boundary — 2026-08-18

- `ireland-geometry-bundle` now rejects an archive destination inside the
  generated output directory, matching the pipeline-integrated `--bundle`
  guard and the documented release contract.
- Regression coverage protects custom excludes and the rejection path, while
  Doctor exposes the invariant as
  `capabilities.bundle.rejects_archive_inside_output`.

## Pages publication output boundary — 2026-08-18

- `publish_pages.py` now rejects a Pages site directory inside the analytical
  output root, preventing a misconfigured publication from overwriting or
  contaminating the verified output inventory.
- Publisher regression coverage, Doctor capability discovery, and packaged CI
  assertions cover the new `capabilities.pages.publisher_rejects_site_inside_output`
  invariant.

## Pages publisher symlink-root boundary — 2026-08-18

- The Pages publisher now rejects symlinked output and site roots before
  reading or writing any publication files, matching the bundle and report
  server symlink boundaries.
- Regression coverage and Doctor v85 expose the invariant as
  `capabilities.pages.publisher_rejects_symlink_roots`.

## Pipeline output-root symlink boundary — 2026-08-18

- `run_pipeline.py` now rejects an existing symlink passed as `--out-dir` before
  dry-run planning or any output-directory creation, keeping the pipeline
  boundary aligned with the bundle, report server, and Pages publisher.
- The source and packaged smoke suites cover the fail-closed behavior, and
  Doctor v86 exposes it as
  `capabilities.pipeline.rejects_output_symlink`.

## Pages audit and release-root boundary — 2026-08-18

- `pages_audit.py` now rejects a symlinked Pages site root before resolving it,
  preserving the publication path boundary in direct audits; Doctor exposes
  this as `capabilities.pages.audit_rejects_symlink_root`.
- `release_check.py` now carries the same raw-root checks for output and Pages
  site paths, so a valid symlink target cannot make a release appear safe;
  Doctor v87 exposes `capabilities.release_check.rejects_symlink_roots`.
- Regression and fresh wheel/sdist smoke coverage exercise both paths. The
  release gate still has only the expected dirty-worktree blocker on this
  checkout; output, source, Pages, and bundle gates remain passing.

## Standalone output-writer symlink contract — 2026-08-18

- Added the shared `reject_symlink_root` runtime helper and applied it to the
  standalone report, schema-audit, columnar-export, and verifier commands.
  Their CLI paths now fail clearly before creating or replacing files through a
  symlinked output root.
- Doctor v88 publishes the versioned
  `ireland-geometry.output-safety.v1` inventory under
  `capabilities.output_safety`, covering the pipeline, diagnostics, exports,
  bundle, server, Pages, and release paths.
- Four focused regressions plus fresh package smoke coverage protect the new
  direct-command boundary.

## Strict readiness requires output safety — 2026-08-18

- Doctor v89 now treats an incomplete
  `ireland-geometry.output-safety.v1` inventory as a strict-readiness blocker,
  with stable code `output_safety_incomplete` and a diagnostic warning.
- Regression coverage proves that a package cannot report strict readiness
  while any user-facing output-root guard is missing.

## Existing file output-root guard — 2026-08-18

- The shared `reject_symlink_root` contract now rejects an existing regular
  file (or other non-directory) at an output-root path with a clear diagnostic,
  instead of allowing writers to surface a raw `FileExistsError`.
- The pipeline, bundle builder, report server, Pages publisher/audit, and
  release checker now handle the same existing-file case; structured readers
  report a clear non-directory status instead of following or obscuring it.
- Doctor exposes the behavior as `capabilities.output_safety.rejects_non_directory`
  plus per-consumer `non_directory_guards`, and keeps strict readiness blocked
  if any listed implementation regresses.

## Direct analytical stage output contract — 2026-08-19

- Added the shared `project_output_path` resolver to 19 independently runnable
  analytical stage CLIs, so direct execution and orchestrated pipeline runs
  reject symlinked or existing non-directory `--out-dir` roots consistently.
- The routing stage now applies the same fail-closed check to its separate
  `--write-graph` destination before graph materialization begins.
- Doctor v90 exposes the installed/source stage matrix as
  `capabilities.output_safety.stage_output_guards`; focused direct-stage and
  runtime-helper regressions now protect the contract, raising the full source
  suite to 287 passing tests.

## Data-root safety contract — 2026-08-19

- Added `project_data_path` and applied it to the pipeline, ingestion fetchers,
  and direct data-consuming analysis stages. Existing regular-file and
  symlinked data roots now fail before network access, input reads, or writes.
- The Geofabrik, NIAH, Overpass, and Sentinel-2 writers also guard their
  nested data destinations; the routing and pipeline paths retain their
  separate graph/output-root checks.
- Doctor v92 publishes `ireland-geometry.data-safety.v1` under
  `capabilities.data_safety`, makes it a strict-readiness requirement, and CI
  covers the full implementation matrix in both wheel and sdist installs; the
  source suite now contains 295 passing tests.

## Atomic ingestion file writes — 2026-08-19

- Added `atomic_write_stream` and replaced direct Overpass, Geofabrik, and
  Sentinel-2 destination writes with atomic replacement. Existing destination
  symlinks are replaced as files without modifying their external targets.
- NIAH archive downloads and extracted CSV members now stream through the same
  atomic helper, so large regional inputs are not buffered wholesale in memory
  and failed transfers leave no partial cache file.
- Doctor v93 publishes `ireland-geometry.file-write-safety.v1` under
  `capabilities.file_write_safety`, with strict-readiness and packaged wheel/
  sdist assertions covering the ingestion writer matrix.
- Runtime and Overpass integration regressions protect the no-follow and
  no-partial-file behavior; the source suite now contains 300 passing tests.

## Nested OSM cache-root boundary — 2026-08-19

- The Overpass fetcher now validates `data/raw` independently of the top-level
  data root, preventing a symlinked raw-cache directory from redirecting cell
  reads or writes.
- Doctor v94 exposes this implementation under
  `capabilities.data_safety.nested_guards`, and wheel/sdist CI assertions cover
  the packaged matrix.
- A CLI regression exercises the symlinked raw-cache failure before any network
  request; the source suite now contains 301 passing tests.

## Nested NIAH extraction boundary — 2026-08-19

- NIAH now validates each regional extraction directory before creating it or
  refreshing its CSV members, preventing a symlinked region path from redirecting
  archive extraction.
- Doctor v95 expands `capabilities.data_safety.nested_guards` to cover both
  Overpass raw cells and NIAH regional directories; wheel/sdist assertions and
  CLI regressions cover the packaged behavior.
- The source suite now contains 302 passing tests.

## Individual ingestion cache-file boundary — 2026-08-19

- Added the shared file-level no-follow guard for cached inputs. Overpass raw
  JSON, NIAH ZIP/CSV files, the Geofabrik PBF, and the combined OSM cache now
  reject symlinked files before reads; atomic refresh writes remain safe.
- Doctor v96 exposes the implementation under
  `capabilities.data_safety.file_guards`, with wheel/sdist assertions covering
  the packaged fetchers.
- Runtime and CLI regressions cover all three ingestion families; the source
  suite now contains 306 passing tests.

## Primary pipeline nested-tree preflight — 2026-08-19

- Added the shared `reject_symlink_tree` runtime helper and applied it to the
  primary pipeline's existing `data` and `output` roots before dry-run planning
  or stage dispatch. Nested file and directory symlinks now fail closed instead
  of being followed by a downstream stage.
- Doctor v97 exposes the pipeline capability as
  `capabilities.pipeline.rejects_nested_symlinks` and the data-safety inventory
  as `capabilities.data_safety.tree_guards`; wheel/sdist CI assertions cover the
  packaged implementation.
- Runtime and subprocess regressions cover nested tree rejection; the source
  suite now contains 308 passing tests.

## Direct analytical nested-input safety — 2026-08-19

- Added `project_data_tree_path` and applied it to standalone NIAH, architect,
  building-parts, historical, OSM-history, review, road, routing, and spatial
  covariate CLIs. Direct execution now rejects nested symlinked data trees just
  like the orchestrated pipeline.
- Added `project_input_path` for explicit analytical file/directory overrides,
  including `analyze --data`, LiDAR, historical/review/history sources,
  boundaries, road PBFs, and routing graph inputs.
- Doctor v98 exposes 11 recursive `capabilities.data_safety.tree_guards` and 9
  explicit `input_guards`; packaged CI assertions cover the complete matrices.
- Runtime, direct-stage, and subprocess regressions cover file and directory
  symlinks; the source suite now contains 313 passing tests.

## Operational reader and verifier boundaries — 2026-08-19

- Extended recursive data-tree protection to the standalone route query,
  complete verifier, and report server; explicit graph, manifest, schema, and
  holdout-plan inputs now reject symlinked files or directories before reads.
- Added `project_output_tree_path` for bounded output queries and schema audits,
  so generated artifacts cannot be redirected through nested output symlinks.
- Doctor v99 exposes the expanded operational data matrix plus
  `capabilities.output_safety.nested_guards`; CI asserts both packaged maps.
- CLI, verifier, report-server, and runtime regressions cover the new
  boundaries; the source suite now contains 319 passing tests.

## Operational input override regressions — 2026-08-19

- Added explicit schema-audit and holdout-plan symlink regressions, completing
  coverage for the operational input override matrix.
- The source suite now contains 321 passing tests.

## Direct analytical output-tree safety — 2026-08-19

- Replaced root-only output resolution with `project_output_tree_path` across
  all 19 independently runnable analytical stage writers. Each stage now
  rejects nested file or directory symlinks below an existing `--out-dir`
  before reading inputs or creating artifacts.
- Doctor v100 publishes the recursive stage matrix as
  `capabilities.output_safety.stage_output_tree_guards` while retaining the
  established `stage_output_guards` field; wheel/sdist CI assertions cover all
  19 packaged stage entry points.
- Added a direct-stage regression for nested output symlinks; the source suite
  now contains 322 passing tests.

## Routing graph output-tree safety — 2026-08-19

- The disk-backed routing writer now applies `reject_symlink_tree` to its
  separate `--write-graph` directory after validating the root, so existing
  nested file or directory symlinks cannot survive into graph materialization.
- Doctor v101 exposes the packaged/source behavior as
  `capabilities.data_safety.graph_output_tree_guards`; wheel and sdist CI
  assertions cover the graph-output matrix.
- Added a direct routing regression for a nested graph-directory symlink; the
  source suite now contains 323 passing tests.

## NIAH extraction-tree safety — 2026-08-19

- NIAH now recursively validates each existing regional extraction directory
  before skipping cached extraction or globbing CSV files. A nested symlink can
  no longer redirect the parser into an external directory through the
  `data/niah/<region>/*/*.csv` pattern.
- Doctor v102 exposes the stronger ingestion boundary as
  `capabilities.data_safety.extraction_tree_guards`; CI and the no-network CLI
  regression cover the packaged implementation.
- The source suite now contains 324 passing tests.

## Sentinel-2 cache-tree safety — 2026-08-19

- The standalone Sentinel-2 downloader now recursively validates the existing
  `data/satellite` directory before downloading bands, extending the
  same no-symlink cache invariant to its optional imagery writer.
- Doctor v103 exposes the behavior as
  `capabilities.data_safety.satellite_tree_guards`; CI and a credential-free
  mocked download-path regression cover the packaged implementation.
- The source suite now contains 325 passing tests.

## Route-query file-output safety — 2026-08-19

- Added `project_output_file_path`, a shared fail-closed resolver for explicit
  output files. It rejects an existing directory at the file destination,
  symlinked destination files, and symlinked parent directories before atomic
  JSON replacement begins.
- The standalone route query now applies the resolver to both `--out` and
  `--geojson-out`. Doctor v104 exposes the capability as
  `capabilities.output_safety.file_output_guards`, with wheel/sdist CI checks.
- Added runtime regressions for directory/file/parent symlink boundaries; the
  source suite now contains 327 passing tests.

## Doctor and bundle file-output safety — 2026-08-19

- Added the lower-level `reject_output_file` runtime boundary for library
  callers that need `ValueError` semantics while retaining the CLI wrapper
  through `project_output_file_path`.
- Doctor `--out` and bundle archive creation now reject symlinked destination
  files, directories used as files, and symlinked parent directories before
  atomic replacement or archive staging.
- Doctor v105 and packaged CI assertions expose both commands through
  `capabilities.output_safety.file_output_guards`; the source suite now
  contains 329 passing tests.

## Bundle verification and extraction path safety — 2026-08-19

- Bundle verification now rejects symlinked archive inputs and symlinked
  archive-input parent directories before opening the ZIP.
- Bundle extraction now rejects a symlinked destination and symlinked parent
  directories before creating staging directories.
- Doctor v106 exposes both boundaries under `capabilities.bundle`; source and
  packaged regressions cover archive verification and extraction refusal, and
  the source suite now contains 331 passing tests.

## Report-pack output-tree safety — 2026-08-19

- Report generation and columnar export now resolve `--out-dir` through the
  recursive output-tree guard, rejecting nested symlinks before reading source
  artifacts or replacing generated report-pack files.
- Doctor v107 exposes both commands under
  `capabilities.output_safety.nested_guards`; source regressions cover nested
  output links and the suite now contains 333 passing tests.

## Active tree-boundary diagnostics — 2026-08-19

- Doctor now scans the active data and output roots and reports nested symlink
  paths, counts, truncation state, non-directory roots, and unreadable trees
  under `capabilities.data_safety.observed_root` and
  `capabilities.output_safety.observed_root`.
- Strict readiness now emits stable `data_root_invalid` and
  `output_root_invalid` blockers for unsafe active trees; source regressions
  cover both nested boundaries and the suite now contains 334 passing tests.

## macOS temporary-output portability — 2026-08-19

- Explicit file destinations now allow the canonical macOS `/tmp` system alias
  (`/private/tmp`) while continuing to reject user-created symlink parents.
- Doctor v109 exposes the behavior as
  `capabilities.output_safety.allows_canonical_macos_tmp_alias`; a runtime
  regression and direct Doctor `--out /tmp/...` smoke cover the boundary; the
  source suite now contains 335 passing tests.

## Ferry service-window routing — 2026-08-19

- SQLite routing graphs now accept the versioned
  `ireland-geometry.ferry-schedules.v1` `ferry_schedules.json` companion
  contract. PBF graph extraction persists supported ferry `opening_hours` and
  optional per-way `duration_s` metadata into that contract.
- Explicit departures filter scheduled ferry edges, while route-query metrics
  apply crossing durations to arrival estimates without changing physical
  route-distance semantics. Legacy graphs without the companion contract
  remain readable and report schedule metadata as not provided.
- The route CLI, local route API/OpenAPI description, incremental stage cache,
  Doctor capability matrix, README, and final-status record now expose the
  same boundary. Regression coverage verifies open/closed windows, duration
  propagation, malformed-contract refusal, and the new capability fields.
- The full source suite now contains 338 passing tests; fresh wheel and sdist
  smoke both include the schedule schema and execute a scheduled ferry route.

## Ferry schedule calendar coverage and cached refresh — 2026-08-19

- The local PBF audit found 26 access-allowed ferry ways with `duration`, plus
  4 opening-hours tags. The ferry parser now supports the observed seasonal
  month clauses, per-day weekly clauses, and fixed closed dates.
- Public-holiday (`PH`) expressions are now parsed as calendar-qualified
  windows. They are evaluated only when a sibling
  `ireland-geometry.public-holidays.v1` `public_holidays.json` contract supplies
  explicit ISO dates; the route engine does not hardcode a jurisdictional
  calendar. The cached national graph now carries 26 crossing durations and 4
  parsed schedules, including 1 that requires the optional calendar.
- Regression coverage now includes the observed calendar syntax, date closure
  semantics, and explicit holiday-contract routing; the source suite grows to
  342 passing tests.

## Time-aware ferry waiting — 2026-08-19

- SQLite route metrics now find the next supported ferry opening when an
  explicit departure reaches a scheduled service outside its active window.
  Waiting time is included in estimated duration and arrival while physical
  route distance remains unchanged for the analytical diagnostic.
- Multi-segment ferry ways do not insert a second wait between their physical
  segments; the schedule is evaluated when the route enters the way. The route
  CLI, sampled routing output, local API/OpenAPI description, and documentation
  now expose this behavior.
- Regression coverage verifies a closed-window route waiting until the next
  weekday service and keeps existing distance/duration/path contracts intact.

## Ferry waiting observability — 2026-08-19

- The versioned route response now exposes `ferry_wait_s` and `ferry_wait_n`,
  separating schedule waiting from physical crossing duration and road travel
  time. The fields are present for CLI, local API, and GeoJSON properties.
- OpenAPI now requires and describes the two non-negative wait fields, while
  portable/non-ferry routes report zero rather than omitting the contract.
- Regression coverage verifies zero wait during an active service window and
  the exact next-window wait for a closed scheduled ferry.

## Dashboard ferry-wait summary — 2026-08-19

- The live dashboard route status now summarizes `ferry_wait_s` and
  `ferry_wait_n` as a readable duration/count while retaining the full JSON or
  GeoJSON payload for automation.
- Doctor checks this wait-observability surface independently across the
  standalone and lazy reports; report and Doctor regressions cover the contract.

## Ferry route breakdown — 2026-08-19

- Route results now expose a compact ferry breakdown: unique ferry way IDs,
  physical ferry distance, crossing time excluding service waits, and traversed
  ferry-edge count. Non-ferry and portable routes return explicit zero/empty
  values rather than omitting the fields.
- OpenAPI requires the breakdown fields, the dashboard summarizes them beside
  the objective and wait status, and Doctor checks the generated surface.
  Regression coverage verifies active and delayed scheduled-ferry routes.

## Optional fastest-route objective — 2026-08-19

- Point-to-point route queries now accept `objective=distance|duration` in the
  local API and `--objective distance|duration` in the CLI. Distance remains
  the default; duration selects the fastest estimated route using modeled
  crossing times, ferry waits, and conditional-profile travel time.
- OpenAPI, capabilities, route JSON/GeoJSON properties, the dashboard selector,
  and Doctor’s route matrix expose the two objectives consistently. Regression
  coverage proves that duration can choose a longer road over a physically
  shorter but slow ferry and rejects invalid objective values at the API.

## Vehicle-weight conditional routing — 2026-08-19

- The PBF restriction adapter now accepts vehicle-weight conditions such as
  `no_left_turn @ (weight>7.5)`, persisting the rule in the existing SQLite
  conditional-restriction contract. The refreshed national graph parses all
  78 conditional relations, stores 73 graph-resolved rules, and reports zero
  unsupported conditional expressions; four source references remain unresolved.
- An optional metric-tonne profile is available through `--weight-t` in the
  route CLI, `weight_t` in `/api/route`, `--routing-weight-t` in the pipeline,
  and the dashboard. The default remains profile-free, so weight rules are
  explicitly retained but inactive unless a profile is supplied.
- Route JSON/GeoJSON, OpenAPI, Doctor v111, report capability checks, and
  regression coverage expose the selected `vehicle_weight_t` profile and
  verify that a 10-tonne route avoids the restricted turn.

## Conditional road-access windows — 2026-08-19

- The PBF adapter now retains supported motor-vehicle access allow and deny
  windows such as `motor_vehicle=no` with
  `motor_vehicle:conditional=yes @ (19:00-07:00)`, plus bounded calendar date
  ranges such as `2026 Jul 04 - 2026 Oct 05`, in a dedicated SQLite
  `conditional_access` table. The table stores the rule mode; overnight,
  semicolon-separated weekly windows, and explicit date ranges are evaluated
  against the route’s departure plus elapsed travel time, while profile-free
  routes omit these profile-dependent ways safely.
- The refreshed national graph contains 27 supported conditional access rules
  (6 weekly allow windows, 3 bounded date ranges, and 18 deny windows), 23
  unsupported conditional-access tags reported in metadata, 24
  conditional/access-filtered ways excluded under the default policy, and 75 stored
  conditional turn rules (74 time windows plus 1 weight-based rule), and 2
  unresolved conditional references. The remaining references target
  pedestrian/cycleway-only ways and are intentionally outside the motor-
  vehicle graph.
- Route JSON/GeoJSON metadata, the local API capabilities contract, Doctor
  v112, the CLI sampled-routing method, documentation, and regression coverage
  now expose the conditional road-access capability and unsupported-tag
  warning. The national SQLite graph was rebuilt from the cached PBF, the
  100-pair pipeline sample and report were refreshed, and the full source
  suite passes 347 tests with Ruff, compileall, package smoke, Pages audit,
  verified bundle, and strict release checks (with only the expected dirty
  worktree blocker) validated.

## Vehicle-class conditional access — 2026-08-19

- The SQLite road graph contract is now v7. Its `conditional_access` table
  stores `vehicle_class` alongside rule mode and condition, while legacy
  two-/three-column fixtures remain readable with the `general` default.
- The PBF adapter now recognizes supported `delivery` conditional access
  values, including bare 24/7 delivery permissions and weekly/date-aware
  delivery windows. Delivery-only ways are retained in the default SQLite
  graph even when their base access tag is private/no; the general profile
  still blocks them, and `--vehicle-class delivery` activates them safely.
  Advisory `discouraged`, malformed, and weight-qualified delivery tags remain
  explicitly unsupported rather than being treated as hard access grants.
- The country-scale rebuild now contains 961,732 routable ways, 7,427,403
  nodes, 7,553,154 physical road segments, 36 supported conditional access
  rules (18 general and 18 delivery), 5 unsupported tags, and 15 excluded
  conditional/access-filtered ways. It applies 3,938 non-conditional turn
  restrictions and stores 73 resolved conditional turn rules from 78 supported
  relations.
- The standalone route CLI, sampled routing, pipeline manifest/verification
  contract, local `/api/route`, OpenAPI, capabilities response, Doctor v113,
  and generated dashboard now expose `general|delivery` consistently. The full
  348-test suite, Ruff, compileall, package build, artifact refresh, schema
  validation, reproducibility, verification, bundle, and Pages audit all pass;
  strict Doctor remains warning-only for the expected dirty worktree.

## Directional conditional road access — 2026-08-19

- The SQLite road graph contract is now v8. Conditional access rows carry a
  `direction` (`both`, `forward`, or `backward`) in addition to mode,
  condition, and vehicle class; legacy graphs and fixtures without the column
  remain readable with the `both` default.
- The PBF adapter recognizes directional OSM keys such as
  `motor_vehicle:forward:conditional`, stores the rule against the way’s node
  order, and applies it only to matching forward or reverse traversal. The
  portable CSV fallback continues to omit conditional-tagged ways because it
  cannot represent profile-aware directional access safely.
- The refreshed national graph contains 42 supported conditional access rules:
  24 general-traffic rules and 18 delivery-vehicle rules, including 6 forward
  directional rules. It contains 8 unsupported conditional-access tags and 18
  excluded conditional/access-filtered ways, 3,937 applied non-conditional turn
  restrictions, 73 stored conditional turn rules, and 4 unresolved conditional
  references.
- Route summaries, Doctor, API capabilities, and the v8 metadata expose the
  directional rule counts. Regression coverage verifies forward-only denial,
  reverse traversal, legacy row compatibility, and the published capability
  surface.
- Final validation passes with 349 tests, Ruff, compileall, package build,
  source/manifest alignment, reproducibility, verification, bundle
  verification, and Pages publication/audit; strict release readiness remains
  withheld only by the intentionally dirty worktree.

## Vehicle-weight conditional road access — 2026-08-19

- Supported conditional access now accepts weight predicates such as
  `delivery @ (weight>5T)` and evaluates them through the existing
  `--weight-t`/`weight_t` profile. The rule is fail-closed when an allow rule
  has no vehicle-weight profile; ambiguous `psv`, advisory `discouraged`, and
  malformed expressions remain explicitly unsupported.
- The national v8 graph now contains 43 supported conditional access rules:
  24 general-traffic and 19 delivery-vehicle rules, including 6 forward-only
  directional rules and 1 weight-qualified delivery rule. It reports 7
  unsupported tags and 17 excluded conditional/access-filtered ways, with
  961,730 routable ways, 7,427,402 nodes, and 7,553,151 physical segments.
- Route summaries, metadata, Doctor, capabilities, and regression coverage
  expose the weight-qualified access profile. The fixture route proves that a
  delivery vehicle reaches the restricted segment at 10 tonnes and remains on
  the fallback route without a qualifying profile.
- Final validation passes with 350 tests, Ruff, compileall, package build,
  source/manifest alignment, reproducibility, verification, bundle
  verification, and Pages publication/audit. The release gate passes every
  substantive artifact, source, Pages, and bundle check; strict readiness is
  withheld only by the intentionally dirty worktree.

## Public-service-vehicle conditional access — 2026-08-19

- A fresh scan of the cached national PBF found three highway ways with
  `motor_vehicle:forward:conditional=psv @ (Mo-Fr 07:00-19:00)`. The graph now
  models these as an explicit `psv` vehicle-class profile instead of reporting
  them as unsupported.
- The v8 rebuild contains 46 supported conditional road-access rules: 24
  general-traffic, 19 delivery-vehicle, and 3 public-service-vehicle rules;
  9 are forward-directional and 1 is weight-qualified. Unsupported tags fall
  from 7 to 4 and conditional/access-filtered exclusions from 17 to 14. The
  graph contains 961,733 routable ways, 7,427,406 nodes, 7,553,158 physical
  segments, and 3,938 applied non-conditional turn restrictions.
- `--vehicle-class psv`, `vehicle_class=psv`, the local API/OpenAPI contract,
  dashboard selector, Doctor v115, route summaries, and regression coverage
  now expose the profile. During an active psv window, general and delivery
  profiles are blocked from the psv-only traversal; outside the window they
  remain available.
- Final validation passes with 351 tests, Ruff, compileall, package build,
  source/manifest alignment, reproducibility, verification, bundle
  verification, and Pages publication/audit. The release gate passes every
  substantive check; strict readiness remains withheld only by the dirty
  worktree.

## Taxi conditional access — 2026-08-19

- The cached national PBF contains six highway taxi access windows: three
  `taxi:conditional` rules and three `taxi:backward:conditional` rules, all
  using `yes @ (Mo-Su 00:00-06:00)`. The parser now recognizes these as an
  explicit `taxi` vehicle class rather than leaving taxi-only roads excluded.
- The v8 rebuild contains 52 supported conditional road-access rules: 24
  general-traffic, 19 delivery-vehicle, 3 public-service-vehicle, and 6 taxi
  rules. Twelve are directional (9 forward, 3 backward) and 1 is
  weight-qualified. The graph contains 961,735 routable ways, 7,427,417
  nodes, 7,553,171 physical segments, and 3,940 applied non-conditional turn
  restrictions; 4 unsupported tags and 14 conditional/access-filtered ways
  remain explicitly reported.
- `--vehicle-class taxi`, `vehicle_class=taxi`, the local API/OpenAPI contract,
  dashboard selector, Doctor v116, route summaries, and regression coverage
  now expose taxi-specific access windows. Taxi profiles can use the modeled
  roads only during their active windows; other profiles remain blocked by the
  taxi-only rule.
- Final validation passes with 352 tests, Ruff, compileall, package build,
  source/manifest alignment, reproducibility, verification, bundle
  verification, and Pages publication/audit. The release gate passes every
  substantive check; strict readiness remains withheld only by the dirty
  worktree.

## Public-holiday calendar companion — 2026-08-19

- The cached ferry data contains one schedule with a `PH` opening-hours
  clause. The graph now ships an explicit
  `ireland-geometry.public-holidays.v1` companion with 80 Irish public-holiday
  dates covering 2023–2030; the 2026 dates were checked against the official
  Workplace Relations Commission list.
- SQLite graph builders validate the companion before replacing the graph and
  record its status, count, and minimum/maximum date in
  `road_graph_metadata.json`. Route summaries expose the same coverage bounds,
  so a missing or bounded calendar cannot be mistaken for an unqualified
  always-available holiday source.
- Doctor is version 118, the current-status documentation is aligned to the
  56-rule/3,940-restriction graph, and regression coverage now checks companion
  metadata and route coverage fields. The full suite now passes 354 tests.

## Direct public-service-vehicle conditional access — 2026-08-19

- The cached PBF audit found four routable secondary-road ways with direct
  `psv:backward:conditional=yes @ (Mo-Sa 07:00-10:00,16:00-19:00)` tags on
  Tyrconnell Road. The parser now recognizes direct `psv:*:conditional` keys
  and preserves their backward traversal direction.
- The refreshed v8 graph now stores 56 supported conditional road-access rules:
  24 general, 19 delivery, 7 public-service-vehicle, and 6 taxi. Directional
  rules rise from 12 to 16 (9 forward and 7 backward); unsupported and
  excluded counts remain 4 and 14.
- The national SQLite route smoke reaches a direct-PSV backward segment with
  `--vehicle-class psv`, and the full 354-test suite plus Ruff remain green.

## Generic maxweight way limits — 2026-08-19

- The cached PBF audit found 1,106 generic `maxweight` tags on supported
  highway ways. The v9 SQLite edge contract stores their parsed limit/status
  on each physical segment; the refreshed graph retains 1,101 routable ways,
  including 1,100 numeric limits across 9,903 segments and one ambiguous
  `below_default` way.
- `--weight-t`/`weight_t` now enforces numeric generic limits during SQLite
  Dijkstra. Unprofiled routes retain these limits but report them as inactive;
  ambiguous generic values are fail-closed only when a weight profile is
  active. HGV-specific and destination/unloading conditions remain explicitly
  outside this generic contract.
- A real national way with `maxweight=3.5` routes at 3.5 t and is rejected at
  4 t, while the unprofiled route remains unchanged. Route summaries, Doctor,
  API/OpenAPI capabilities, and the dashboard contract expose the feature.
- The full suite passes with 356 tests; Ruff, compileall, national graph
  rebuild, report refresh, 55-artifact reproducibility, verification,
  62-artifact bundle verification, Pages publication, and Pages audit all pass.

## HGV vehicle profile and maxweight:hgv — 2026-08-19

- The cached PBF audit found 35 numeric `maxweight:hgv` tags on supported
  highway ways. The v10 SQLite edge contract persists HGV-specific limit/status
  fields alongside generic `maxweight`; the national graph retains 35 supported
  HGV ways across 289 directed segments.
- `--vehicle-class hgv` and `vehicle_class=hgv` now activate those limits when
  `--weight-t`/`weight_t` is supplied. Generic limits remain enforced for all
  weighted profiles, while destination-dependent and unloading-specific HGV
  conditions plus `maxweightrating:hgv` remain explicitly out of scope.
- A real national edge routes at 3.0 t for the HGV profile, is rejected at
  4.0 t for HGV, and remains reachable at 4.0 t for the general profile.
  Route summaries, Doctor, API/OpenAPI, dashboard controls, documentation, and
  regression coverage expose the new capability.
- The full suite passes with 357 tests; Ruff, compileall, national graph
  rebuild, report refresh, 55-artifact reproducibility, verification,
  62-artifact bundle verification, Pages publication, and Pages audit all pass.

## Vehicle-height routing profile — 2026-08-19

- The cached PBF audit found 1,553 `maxheight` tags on supported highway ways;
  the refreshed v11 graph retains 1,509 routable ways, with 976 numeric legal
  limits, 22 explicit unlimited values, and 511 ambiguous values across 9,324
  directed segments. It also retains 12 `maxheight:physical` ways across 25
  directed segments, including 11 numeric values and 1 ambiguous value.
- SQLite edges now persist separate legal and physical height limit/status
  fields. `--height-m`/`height_m` enforces both constraints; without a height
  profile they remain retained but inactive, and ambiguous values are fail-closed
  only for height-qualified routing. v10 graphs remain readable with explicit
  height capability unavailable.
- The CLI, sampled pipeline (`--routing-height-m`), local API/OpenAPI, Doctor,
  dashboard, route responses, documentation, and regression coverage expose the
  height profile. A real national edge routes at 2.2 m and is rejected at 2.7 m.
- The full suite passes with 358 tests; Ruff, compileall, national graph rebuild,
  report refresh, reproducibility, verification, bundle verification, Pages
  publication, and Pages audit all pass for the v11 artifact set. The only
  release-gate warning is the intentionally preserved dirty worktree.

## Vehicle width, length, and axle-load routing profiles — 2026-08-19

- The cached PBF audit found 14 supported-highway `maxwidth` ways, 5
  `maxlength` ways, and 10 `maxaxleload` ways. Width and length values in
  OSM feet/inches notation are normalized to metres; all 29 routable ways in
  this snapshot resolve to supported numeric or explicit metric values.
- The v12 SQLite edge contract persists `maxwidth_m`, `maxlength_m`, and
  `maxaxleload_t` plus per-tag status fields. The refreshed national graph
  carries 131, 95, and 84 tagged directed segments respectively. Explicit
  `--width-m`, `--length-m`, and `--axleload-t` profiles enforce them;
  unprofiled routes retain the tags as inactive, and ambiguous values are
  fail-closed only for the corresponding active profile.
- The standalone CLI, sampled pipeline (`--routing-width-m`,
  `--routing-length-m`, `--routing-axleload-t`), local API/OpenAPI, Doctor,
  dashboard, route responses, and regression coverage expose all three
  profiles. National edge-filter smokes pass at each exact limit and reject
  the same edge above the limit.
- The full suite passes with 359 tests; Ruff, compileall, national graph
  rebuild, report refresh, 55-artifact reproducibility, verification,
  62-artifact bundle verification, Pages publication, and Pages audit all
  pass. The only release-gate warning remains the intentionally preserved
  dirty worktree.

## Per-way OSM maxspeed duration ceilings — 2026-08-20

- The cached PBF audit found 199,242 `maxspeed` tags on highway ways, with
  30 observed values dominated by numeric km/h and mph forms. It also found
  225 `maxspeed:conditional` tags and no `maxspeed:hgv` tags. OSM documents
  numeric `maxspeed` as a legal speed limit, defaulting to km/h unless an
  explicit unit is supplied ([OSM Key:maxspeed](https://wiki.openstreetmap.org/wiki/Key%3Amaxspeed)); this upgrade supports numeric km/h, mph, and
  knots values and leaves implicit/conditional forms unevaluated.
- The v13 SQLite edge contract persists `maxspeed_kmh` and
  `maxspeed_status`. The rebuilt national graph retains 196,440 routable
  `maxspeed` ways: 196,435 supported numeric ways across 2,115,207 directed
  segments and 5 non-numeric ways across 19 segments. The 225 conditional
  speed tags remain visible in metadata as retained-but-not-evaluated.
- Duration routing now caps each non-ferry edge at the lower of the configured
  fallback speed and its supported per-way legal ceiling. Shortest-distance
  routing is unchanged; explicit unlimited values do not cap a way, and
  unsupported values do not invent a ceiling. Old v12 SQLite graphs and
  portable graphs remain readable without the new profile.
- Focused parser, SQLite schema, per-way duration, API capability, and
  backward-compatibility tests pass. A national 5 km/h edge smoke produces the
  expected capped duration.
- The full suite now passes with 360 tests; Ruff, compileall, the refreshed
  report, reproducibility, verification, 62-artifact bundle verification,
  Pages publication/audit, and the Doctor capability inventory all pass. The
  strict release gate still reports only the intentionally preserved dirty
  worktree.

## Time-aware conditional speed limits — 2026-08-20

- The cached PBF audit shows 225 `maxspeed:conditional` ways. The existing
  weekly/calendar schedule parser safely evaluates 185 ways (including
  weekday and all-week windows); 40 ways use unsupported forms such as
  `flashing`, school-hours text, or school-holiday qualifiers and remain
  explicitly retained without an invented ceiling.
- The v14 SQLite edge contract adds `maxspeed_conditional_json` alongside the
  numeric `maxspeed` fields. The national graph stores 2,638 conditional-tagged
  directed segments, with 185 supported ways and 40 unsupported ways recorded
  in metadata. Supported schedules are parsed once at graph load and selected
  at edge-entry time from the supplied departure plus accumulated duration;
  active conditional limits are capped by the configured fallback speed and
  any base `maxspeed` ceiling.
- The route CLI/API, OpenAPI description, Doctor capabilities, graph summary,
  method text, and local docs now expose conditional-speed support. `24/7`
  conditional clauses can apply without a departure; other schedule windows
  require one. Shortest-distance routing remains unchanged, and unsupported
  conditional syntax is preserved for auditability rather than guessed.
- Focused parser, schedule-timing, SQLite, PBF-adapter, API, Doctor, and
  backward-compatibility checks pass. The complete v14 graph rebuild preserves
  the prior national counts while adding the conditional-speed coverage.

## Time-aware conditional one-way routing — 2026-08-20

- The cached PBF audit found five routable ways carrying
  `oneway:conditional` or `oneway:motor_vehicle:conditional`: one weekday
  schedule-only rule, two unsupported `no @ permit` rules, one unsupported
  `no @ private` rule, and one seasonal `yes @ May-Sep` rule. The safe subset
  therefore covers two ways; three remain retained as unsupported metadata.
- The v15 SQLite edge contract adds `oneway_conditional_json` alongside the
  existing numeric and conditional-speed fields. The national rebuild stores
  all five ways across 31 directed segments, with two supported ways and three
  unsupported ways recorded in graph metadata. Candidate SQL includes both
  directions only for tagged ways, after which the active schedule is applied
  against the base `oneway` state.
- Schedule-only conditional values are interpreted as temporary forward
  one-way rules. Supported explicit `yes` rules enforce the forward direction;
  supported `no` rules temporarily make the way bidirectional. Unsupported
  qualifiers such as `permit` and `private` preserve the base one-way state.
  Month-only clauses such as `May-Sep` are now supported by the calendar-aware
  schedule parser.
- The standalone route query, local API/OpenAPI capability matrix, graph
  summary, method text, Doctor inventory, national documentation, and
  regression fixtures expose the new capability. The focused and full
  capability test groups pass, and the national edge smoke shows the weekday
  rule blocking reverse traversal during its active window while retaining it
  outside the window.

## HGV permitted gross-weight rating routing — 2026-08-20

- The cached PBF audit found 180 `maxweightrating:hgv` tags on highway ways:
  178 numeric values and 2 ambiguous `no` values. This key describes an HGV's
  permitted gross-weight rating, distinct from the actual-mass `maxweight`
  restriction; see the [OSM `maxweightrating:hgv` key documentation](https://wiki.openstreetmap.org/wiki/Key%3Amaxweightrating%3Ahgv).
- The v16 SQLite edge contract adds separate
  `maxweightrating_hgv_t`/`maxweightrating_hgv_status` fields. The rebuilt
  national graph retains 179 routable tagged ways: 177 supported numeric ways
  and 2 ambiguous ways across 2,816 directed segments. Ambiguous values are
  retained for auditability and fail-closed only when a rating-qualified HGV
  route is requested.
- `--rating-t`, `--routing-rating-t`, API/OpenAPI `rating_t`, route response
  `vehicle_rating_t`, the dashboard, graph summaries, and Doctor now expose
  the profile. Doctor v119 inventories the separate capability. It applies only
  with `vehicle_class=hgv`; `weight_t` remains an independent actual-mass
  profile, and non-HGV classes do not activate the
  HGV-specific rating limits. Legacy v15 SQLite graphs remain readable with
  the rating profile reported as unavailable.
- Numeric parser, SQLite filtering, ambiguous-value fail-closed behavior,
  route API, pipeline forwarding, dashboard tokens, and Doctor capability
  regressions pass. The national graph rebuild completed successfully and
  reported 7,427,417 nodes and 7,553,171 physical road edges, unchanged apart
  from the v16 edge fields and metadata.

## Irish goods permitted-rating alias — 2026-08-20

- A targeted cached-PBF audit found 37 highway ways tagged
  `maxweightrating:goods=3`; all 37 are paired with `hgv=no`, matching the
  Irish HGV threshold pattern described in the [OSM `maxweightrating`
  documentation](https://wiki.openstreetmap.org/wiki/Key%3Amaxweightrating).
- The v17 SQLite contract now combines `maxweightrating:hgv` and
  `maxweightrating:goods` for the HGV permitted-rating profile. If both are
  present, the stricter supported ceiling applies; an unresolved component is
  fail-closed only when a rating-qualified HGV route is requested.
- The rebuilt national graph now retains 216 combined rating ways: 214
  supported numeric ways and 2 ambiguous ways across 3,180 directed segments.
  Existing HGV-only CLI/API/Doctor/dashboard contracts remain unchanged, while
  their descriptions now identify the Irish goods alias explicitly.
- Parser, combined-limit, SQLite routing, and metadata regressions pass; the
  national graph metadata reports `ireland-geometry-road-sqlite-v17`.

## Destination-qualified HGV routing — 2026-08-20

- The cached PBF audit found 29 routable `hgv=destination` ways, 13
  `maxweight:hgv:conditional=none @ destination` ways, and 9
  `maxweightrating:hgv:conditional=none @ destination` ways. The OSM
  conditional-restriction convention treats `none @ destination` as a
  destination exception to the corresponding base numeric restriction; see
  the [OSM conditional restrictions documentation](https://wiki.openstreetmap.org/wiki/Conditional_restrictions).
- The v18 SQLite edge contract adds `hgv_destination_json`. Static
  destination-only HGV access is fail-closed by default for the explicit HGV
  vehicle class. `--allow-hgv-destination`, `--routing-allow-hgv-destination`,
  the API/OpenAPI `allow_hgv_destination`, and the dashboard opt-in enable
  destination access and the matching supported numeric exceptions only when
  the route serves the restricted destination.
- The national graph was rebuilt from the cached PBF with 7,427,417 nodes,
  7,553,171 physical road edges, and 961,735 routable road ways. It now reports
  39 supported destination-qualified HGV ways across 333 directed segments;
  no unsupported destination clauses were present in the routable extract.
- Focused parser, SQLite, route API/OpenAPI, CLI forwarding, dashboard, and
  Doctor regressions pass. The route response and graph summary expose the
  active destination profile, and the indexed destination metadata keeps
  national graph loading bounded to the tagged edges.

## Multi-clause conditional road access — 2026-08-20

- The cached-PBF audit found conditional access values using multiple clauses
  on one key, including class-specific and general-traffic windows. The
  previous single-rule parser and `(way_id, direction)` primary key could not
  retain that source shape without overwriting a clause.
- The v19 SQLite contract adds `rule_n` to the conditional-access key and
  persists every supported clause per way and direction. Active
  class-specific allow rules form a union, active denies remove matching
  classes, and a way with allow clauses remains closed when no allow window is
  active. Legacy graphs without `rule_n` remain readable.
- The capability is exposed through route graph summaries, API capabilities,
  Doctor v121, and the national routing documentation. The rebuilt national
  graph retains 56 supported access rules, 4 unsupported tags, and 14 excluded
  ways; its current routable extract has no same-key multi-clause way, while
  dedicated regression fixtures exercise the full multi-clause evaluator.
- Parser, class/time matrix, legacy-schema, API capability, Doctor, Ruff, and
  focused routing tests pass. The national v19 rebuild completed with
  7,427,417 nodes, 7,553,171 physical road segments, and 961,735 routable
  ways.

## Height-unit normalization — 2026-08-20

- The cached PBF audit found eight routable legal `maxheight` values in the
  documented feet/inches form, including `9'6"`; the existing parser treated
  them as unsupported and therefore fail-closed for height-qualified routes.
- The v20 height parser now normalizes documented feet/inches values and
  explicit centimetre metric values to metres while retaining conservative
  rejection of ambiguous `default`/`below_default` values. The change applies
  to legal and physical height fields in generic SQLite and streamed PBF graph
  writers.
- The rebuilt national graph reports 1,509 legal-height ways across 9,324
  segments: 984 supported, 22 explicit unlimited, and 503 ambiguous values;
  physical height remains 12 ways across 25 segments with 11 supported and 1
  ambiguous value.
- Parser, SQLite route-filter, Doctor, Ruff, compile, and full regression
  coverage pass. OSM documents the `6'7"` notation in the [maxheight key
  documentation](https://wiki.openstreetmap.org/wiki/Key%3Amaxheight).

## Route path explainability — 2026-08-20

- The route engine already carried `way_id` through every SQLite edge
  expansion, but path reconstruction returned only node IDs and coordinates;
  clients could not audit which mapped way/segment satisfied a vehicle or
  turn-restriction decision.
- Detailed reconstruction now preserves ordered `path_way_ids` and directed
  `path_segments`, where each segment is identified by its
  `(from_node, to_node, way_id)` tuple. `path_segment_source` distinguishes
  SQLite edge-backed IDs from the portable graph fallback, which has no
  persisted way IDs. Each detailed segment also carries physical distance,
  estimated duration, ferry-wait seconds, and ferry status; response-level
  `path_segment_total_*` fields make the route totals independently
  reconcilable. Existing shortest-path return shapes remain compatible unless
  detailed reconstruction is requested.
- The capability is exposed in the standalone route CLI, `/api/route`,
  OpenAPI, GeoJSON filtering, and Doctor v123. Focused route, API, OpenAPI,
  Doctor, Ruff, and regression tests pass; national graph data remains on the
  v20 edge contract because this is a route-response enhancement.

## Route segment metrics — 2026-08-20

- Path-enabled route responses now carry physical `distance_m`, estimated
  `duration_s`, `wait_s`, and `ferry` fields for every ordered segment. The
  response also publishes `path_segment_total_distance_m`,
  `path_segment_total_duration_s`, and `path_segment_total_wait_s`, allowing
  clients to reconcile route totals independently of the path geometry.
- Ferry service waits are attached to the ferry segment that incurred them;
  scheduled crossing duration and wait remain separate. The CLI/API,
  OpenAPI, GeoJSON exclusion rules, dashboard route summary, Doctor v124, and
  ferry/turn regression fixtures cover the extension without changing the v20
  graph schema or legacy shortest-path return shapes.

## Route constraint provenance — 2026-08-20

- SQLite path segments now join their `(from_node, to_node, way_id)` identity
  back to the persisted `edges` row and expose normalized static OSM constraint
  records. Numeric legal limits cover weight, HGV weight/rating, height,
  physical height, width, length, axle load, and speed; destination-qualified
  HGV metadata is retained as a string-valued edge constraint.
- Every record carries its source key, normalized value/unit, parser status,
  the profile expression that could evaluate it, and an `evaluated` boolean.
  Supported or unlimited constraints without the relevant supplied profile,
  and unsupported constraints, remain visible but are marked unevaluated;
  ferry segments correctly return an empty edge-constraint list.
- The CLI, local route API, OpenAPI, route capability discovery, dashboard
  summary, Doctor v125, and focused profile-on/profile-off regression coverage
  now advertise and validate the provenance contract. No graph rebuild was
  required because the feature reads the existing v20 edge columns.

## Conditional route-rule provenance — 2026-08-20

- Detailed SQLite route segments now retain entry-time records for persisted
  `maxspeed:conditional`, `oneway:conditional`, and direction-aware
  `conditional_access` rules. Each record includes the source key, condition,
  mode, traversal direction, vehicle class, parser status, profile used,
  active state, and whether the rule was applied to the traversed edge.
- The route trace advances the same elapsed segment duration used by the
  schedule-aware router, so later segments are evaluated at their actual
  entry time. Supported 24/7 access clauses are now correctly represented as
  a one-interval schedule; the regression suite covers this latent parser
  defect as well as active and inactive multi-clause route rules.
- The CLI, local API, OpenAPI, capability discovery, dashboard summary,
  Doctor v126, and conditional-routing fixtures now expose the richer audit
  trail. Static edge constraints remain in `constraints`; conditional
  decisions are intentionally separated in `conditional_rules`.

## Transition-level turn-restriction provenance — 2026-08-20

- Detailed SQLite route segments now include `transition_rules` on the segment
  entering a restricted via node. The records preserve the relation ID,
  `from_way`, `to_way`, ordered `via_way_ids`, `kind`, condition, evaluation
  profile, active/evaluated state, and whether the chosen outgoing way was the
  relation target.
- Provenance follows the router's persisted restriction-prefix state, so both
  direct via-node relations and validated via-way relations are matched against
  the ordered path. Ferry legs reset the road-transition context, and the
  conditional records use the same departure/vehicle-weight semantics as the
  route evaluator.
- The route CLI/API, OpenAPI, capability discovery, dashboard summary, Doctor
  v127, and focused unconditional/conditional/via-way tests now expose the
  transition contract. Static edge constraints and edge-local conditional
  decisions remain separate from relation-level transition records.

## Human-readable route way context — 2026-08-20

- The SQLite graph contract is now v21. The existing `ways` table persists
  nullable OSM `name`, `ref`, `highway`, `route`, and `oneway` fields alongside
  each way's node sequence. Both generic SQLite fixtures and the streamed PBF
  writer populate the same schema; legacy graphs remain readable through
  dynamic-column fallbacks.
- Detailed route segments now expose a stable `road_context` object, allowing
  clients to identify a mapped road or ferry way without a second graph query.
  Missing source tags are represented as nulls rather than inferred labels.
- The route API/OpenAPI, route capability discovery, dashboard summary, Doctor
  v128, national v21 rebuild, and named-way regression fixtures now cover the
  extension. Routing semantics and existing edge/transition provenance remain
  unchanged.

## Geometry-derived route maneuvers — 2026-08-21

- Path-enabled route responses now include `maneuver_n` and `maneuvers` records
  derived from the ordered graph-node geometry and serialized road context.
  Records cover start, arrival, slight/ordinary turns, U-turns, mapped-way
  changes, and ferry boarding/landing transitions.
- Each maneuver retains the node and `[lon, lat]` coordinate, incoming/outgoing
  way IDs, bearings, signed turn angle, outgoing road context, and the
  distance/duration/wait to the next maneuver. Routing cost, restrictions, and
  graph storage are unchanged.
- The CLI/API, OpenAPI, GeoJSON exclusion contract, dashboard summary, Doctor
  v129, and road/ferry regression fixtures now expose the capability. The
  output is explicitly documented as geometry-derived route inspection guidance,
  not lane- or traffic-aware navigation.

## Dashboard maneuver guidance — 2026-08-21

- The generated standalone and lazy dashboards now render an accessible
  numbered maneuver list whenever a path-enabled JSON route returns
  `maneuvers`. Each row presents a human-readable action, mapped road context,
  and distance/duration/wait metrics.
- Selecting a maneuver focuses its coordinate on the live Leaflet map and
  highlights the same point in the dependency-free offline SVG fallback. The
  raw JSON response remains available beneath the guidance list.
- Doctor v130 inventories the dashboard-list capability, and report-generation
  regressions cover the required UI hooks. GeoJSON responses continue to omit
  path-only guidance fields by contract.

## Dashboard path segment inspector — 2026-08-21

- The standalone and lazy dashboards now expose a bounded, accessible path
  detail table for JSON routes with `path_segments`. Each row shows mapped OSM
  road context, way ID, distance, duration/wait, road/ferry mode, and counts of
  static constraints, conditional access rules, and transition turn rules.
- The table is deliberately capped at 250 rendered rows so unusually long
  paths cannot make the dashboard unresponsive; the summary reports when the
  display is truncated. GeoJSON responses retain the existing omission of
  path-only detail fields, so the inspector remains JSON-only.
- Doctor v131 inventories the segment-inspector capability. Report and Doctor
  regressions cover the generated hooks; the API contract and routing cost are
  unchanged.

## Dashboard route reproducibility — 2026-08-21

- Route inputs now use a prefixed, shareable URL state contract. The link keeps
  coordinates, speed and vehicle profiles, departure, objective, response
  format, path/ferry flags, and HGV destination opt-in together with the
  existing dashboard filters. Opening a `route=1` link restores those controls
  and reruns the local query.
- The route panel now provides `Copy link`, `JSON`, and `GeoJSON` actions. The
  JSON action downloads the exact latest response; the GeoJSON action preserves
  the server route GeoJSON shape for JSON responses and downloads the exact
  feature for GeoJSON responses.
- Doctor v132 inventories shareable route state and response downloads. Report,
  Doctor, and generated-artifact regressions cover the new hooks; no routing or
  API contract changes were required.

## Dashboard route evidence details — 2026-08-21

- Segment rows now use accessible expandable details for the existing route
  provenance arrays. Static constraints show key/value/unit and evaluation
  state; conditional records show their condition/profile and inactive/active/
  applied state; transition records show relation, kind, way/via context, and
  selection state.
- Aggregate counts remain in the collapsed row summary, while the 250-row
  rendering cap and JSON-only path-detail boundary remain unchanged.
- Doctor v133 inventories the detailed evidence renderer. Generated report and
  Doctor regressions cover the new hooks without changing route costs or API
  response fields.

## Bounded route matrix API — 2026-08-21

- The local server now exposes repeated `origin=lat,lon` and
  `destination=lat,lon` query parameters at `/api/route/matrix`, with a hard
  25-pair Cartesian bound to keep repeated Dijkstra work predictable.
- The new `ireland-geometry.route-matrix.v1` response preserves ordered
  origin/destination indices and returns compact pair summaries for reachability,
  snapping, route distance, estimated duration, arrival, and ferry timing.
  `include_path=1` adds the full existing point-to-point route contract under
  each pair, so vehicle restrictions and segment provenance remain available
  without making the default matrix response large.
- The endpoint reuses the loaded SQLite graph and all existing vehicle,
  departure, conditional-access, turn-restriction, ferry, and distance/duration
  semantics. Capabilities, OpenAPI, Doctor v134, and HTTP regression coverage
  publish the endpoint and pair bound.

## Standalone route matrix command — 2026-08-21

- The bounded matrix engine is now exposed as the installable
  `ireland-geometry-route-matrix` console command. Repeated `--origin` and
  `--destination` values use the same 25-pair guard and the same profile-aware
  routing semantics as the local HTTP endpoint.
- Default output is compact pair summaries; `--include-path` embeds the full
  point-to-point route response for each pair, and `--out` uses the existing
  symlink-safe output writer. CLI, Doctor, package-smoke, CI, and sdist entry
  point coverage now describe the eleven-command surface.

## Route profile comparison — 2026-08-21

- Added `query_route_comparison` and the
  `ireland-geometry.route-comparison.v1` contract for bounded comparisons of
  two to eight named vehicle profiles over one origin/destination pair. The
  first profile is the baseline; every row reports reachability, snap metadata,
  route distance/duration/ferry metrics, and explicit deltas from that
  baseline. Unreachable baselines produce null deltas rather than implying a
  numeric comparison between incomparable routes.
- Profile rows reuse the existing `query_route` implementation, so conditional
  access, turn restrictions, physical vehicle limits, ferry waits, way context,
  segment evidence, and geometry-derived maneuvers remain identical to direct
  route queries. `--include-path` embeds each full route response.
- Added the installable `ireland-geometry-route-compare` command with strict
  `NAME;key=value` profile parsing, plus the read-only `/api/route/compare`
  endpoint. OpenAPI, capabilities, health, Doctor v135, CI/package/sdist
  smoke checks, README guidance, and CLI/API regressions cover the new
  two-to-eight profile bound. The full source suite and Ruff checks pass.

## Dashboard route comparison — 2026-08-21

- Added a generated-dashboard comparison panel to both the standalone and lazy
  report templates. It accepts one `NAME;key=value` profile per line, submits
  the shared trip to `/api/route/compare`, and renders reachability, distance,
  duration, ferry-wait, and baseline-delta columns.
- Embedded profile routes can be selected from the comparison table to update
  the live or dependency-free offline map, maneuver list, segment inspector,
  and route download controls. Comparison JSON is downloadable directly from
  the panel.
- Added `compare=1` URL persistence for coordinates, common route options,
  profile lines, and path/ferry flags. Doctor v136 now inventories the
  dashboard panel, and live local-browser coverage verifies submission, path
  selection, URL restoration, and a zero-error console.

## Dashboard route matrix — 2026-08-21

- Added a generated-dashboard matrix panel to both the standalone and lazy
  report templates. It accepts separate origin and destination coordinate
  lines, reuses the route form's vehicle/departure/objective options, and
  enforces the 25-pair Cartesian bound in the browser before submission.
- Matrix rows report reachability, distance, duration, ferry wait, and arrival.
  When pair paths are requested, each embedded route can be selected to update
  the live or dependency-free offline map, maneuvers, segment inspector, and
  route download controls; the raw matrix response is downloadable as JSON.
- Added `matrix=1` URL persistence for coordinates and routing options, report
  assertions, CI/package Doctor checks, and README/Final Status coverage.
  Doctor v138 inventories the panel in both generated report variants.

## Structured route matrix API — 2026-08-21

- Added a structured `POST /api/route/matrix` request form with strict JSON
  `{lat,lon}` origin/destination arrays, shared route-profile options, a
  25-pair Cartesian bound, and a 128 KiB body guard. Existing repeated-query
  GET clients remain unchanged.
- The server reuses the GET validators and matrix engine, rejects malformed
  JSON, unsupported media types, non-finite values, unknown fields, and
  oversized bodies before loading the graph. OpenAPI, capabilities, health,
  Doctor v138, and API regression coverage publish the new method.
- The generated dashboard now submits matrices through the JSON body while
  retaining URL-persistent state and the same path-selection/map evidence
  behavior. The full source suite and Ruff checks pass after the upgrade.

## Structured route comparison API — 2026-08-21

- Added a structured `POST /api/route/compare` request form with strict
  `start`/`goal` `{lat,lon}` objects, two-to-eight `NAME;key=value` profiles,
  shared route options, and a 128 KiB body guard. Existing repeated-query GET
  clients remain unchanged.
- The server reuses the comparison profile parser, route-option validators, and
  `ireland-geometry.route-comparison.v1` response. OpenAPI, capabilities,
  health, Doctor v139, and API regression coverage publish the new method and
  malformed-body/media-type behavior.
- The generated dashboard now submits profile comparisons through the JSON
  body while retaining URL-persistent state, path selection, map evidence,
  and JSON download behavior. The source suite, Ruff checks, and live POST
  smoke coverage pass after the upgrade.

## Structured single-route API — 2026-08-21

- Added a structured `POST /api/route` request form with exact `start`/`goal`
  `{lat,lon}` objects, shared vehicle/departure/objective, ferry, and path
  options, an optional `json`/`geojson` format, and a 128 KiB body guard.
  Existing repeated-query GET clients and shareable dashboard URLs remain
  available.
- The server reuses the route validators and
  `ireland-geometry.route.v1` response contract, forcing path inclusion for
  GeoJSON responses and rejecting malformed, unsupported, unknown, or
  oversized requests before graph loading. OpenAPI, health, capabilities,
  Doctor v140, CI/package smoke, and HTTP regressions cover the method.
- The generated dashboard now submits the single-trip form through JSON while
  retaining URL-persistent state and the existing route download/map
  behavior.

## Structured analysis-query API — 2026-08-21

- Added a structured `POST /api/query` request form with strict flat JSON
  filters for backend selection, page bounds, target/control selection,
  minimum score, finite-score cursors, and invalid-score cursors. The 128 KiB
  body bound and type/field validation run before backend selection; existing
  GET query clients and the `ireland-geometry.query.v1` response remain
  unchanged.
- OpenAPI, health, capabilities, Doctor v141, CI/package smoke, README
  guidance, and HTTP regressions cover successful offset/cursor requests plus
  malformed, unsupported-media, unknown-field, invalid-type, and oversized
  body behavior.

## Structured report page/export API — 2026-08-21

- Added strict `application/json` `POST /api/report/page` and
  `POST /api/report/export` request forms. Page requests carry the existing
  dashboard filters plus bounded `limit`, `offset`, and `initial`; export
  requests require `format=csv|geojson` and carry the same filters.
- Both endpoints enforce a 128 KiB body limit, reject unknown fields, invalid
  types, malformed JSON, and unsupported media types before opening the report
  pack, and retain their existing query-string GET forms for links and clients.
- OpenAPI, health, capabilities, Doctor v142, CI/package/sdist smoke, README
  guidance, and HTTP regression coverage publish and exercise both structured
  methods, including CSV and GeoJSON exports.

## Dashboard structured report requests — 2026-08-21

- The lazy dashboard now bootstraps and refreshes report pages through the
  structured JSON `POST /api/report/page` form and downloads filtered CSV or
  GeoJSON through `POST /api/report/export`.
- Filter and route state remains persisted in shareable URL parameters; GET
  report endpoints remain available for direct links and existing clients.
  Doctor v143 inventories the dashboard POST wiring separately from the server
  endpoint capability, and generated-report regression coverage checks the
  structured request bodies.

## Report-page continuation metadata — 2026-08-21

- Added `page.next_offset` to `ireland-geometry.report-page.v1`, returning the
  next bounded offset when another filtered page exists and `null` at the end.
- Published the continuation behavior as `pagination_continuation: "next_offset"`
  in capabilities and a typed `ReportPagePagination` OpenAPI schema.
- Doctor v145, focused HTTP/OpenAPI tests, package assertions, and README/
  final-status guidance now cover the end-to-end continuation contract.

## Deterministic report sorting — 2026-08-21

- Added ascending `osm_id` as the deterministic tie-breaker for every report
  sort key, keeping page boundaries and filtered exports stable when primary
  values are equal.
- Published `page.sort_tiebreaker: "osm_id"` in the report response,
  capabilities, and typed OpenAPI schema.
- Doctor v146, package checks, focused ordering regressions, and report-page
  HTTP/OpenAPI tests now cover the stable ordering contract.

## Conditional API responses — 2026-08-21

- Made successful health, query, route, report, metadata, interpretation,
  capabilities, and OpenAPI responses conditionally cacheable with deterministic
  ETags and `Cache-Control: no-cache`; export bytes retain the same behavior.
- Added the `conditional_endpoints` discovery list and OpenAPI header metadata,
  with `304 Not Modified` HTTP regression coverage.
- Doctor v147, package smoke, and full source validation cover the shared
  conditional-response contract.

## Negotiated API JSON compression — 2026-08-21

- Added deterministic gzip negotiation for cacheable JSON API responses at or
  above 1,024 bytes when clients advertise `Accept-Encoding: gzip` or `*`.
- Compressed responses carry `Content-Encoding: gzip` and
  `Vary: Accept-Encoding`; ETags and gzip-aware `304` responses are tested over
  the representation actually sent.
- Capabilities/OpenAPI discovery and Doctor v148 now publish and gate the
  transport behavior, with package and CI assertions updated.

## HTTP HEAD inspection — 2026-08-21

- Added bodyless `HEAD` handling for every read-only API GET path, preserving
  ETags, cache headers, content lengths, and negotiated gzip metadata.
- Added matching OpenAPI `head` operations and the `head_endpoints` capability
  list for monitoring and cache-probing clients.
- Doctor v149, HTTP regressions, package smoke, and CI assertions cover the
  new method surface.

## Stale dashboard reload guard — 2026-08-21

- Added a runtime snapshot identity comparison to the lazy dashboard. A
  changed manifest, readiness regression, or failed artifact alignment marks
  the already-open view as requiring a deliberate reload.
- Added the accessible `runtimeReloadNotice` and `runtimeReload` action to
  both generated report variants; the server-only notice remains hidden for
  standalone/offline use.
- Added Doctor v150 capability detection, generated HTML checks, a Node
  runtime regression, CI/package assertions, and live browser verification.

## Recoverable dashboard loading — 2026-08-21

- Initial lazy API failures now preserve the dashboard shell and show an
  accessible retry action.
- Later page/filter failures show the same retry action and a clear unavailable
  state; raw HTML error rendering and ambiguous empty-table messaging are gone.
- Added Doctor v151 capability inventory, focused and package assertions, and
  live browser failure/retry verification.

## Correlated API errors — 2026-08-21

- Added request correlation IDs to API response headers, with validation and
  echoing of client IDs plus generated fallback IDs.
- Added the `ireland-geometry.api-error.v1` JSON envelope, stable error codes,
  no-store error responses, and JSON handling for unknown API paths/methods.
- Added OpenAPI/capability discovery, Doctor v152, HTTP regressions, and CI/
  package assertions.

## Correlated API access logs — 2026-08-21

- Added `request_id=...` to API access-log lines so response IDs can be traced
  through the local server log.
- Published `api_request_logging` and `api_request_log_field` in capabilities
  and OpenAPI-adjacent Doctor contracts.
- Added Doctor v153, focused/full validation, package assertions, and a live
  packaged-server correlation probe.

## Universal response correlation — 2026-08-21

- Extended request IDs and access-log correlation from API/health traffic to
  static pages, data packs, downloads, conditional responses, and static
  errors.
- Published the universal-response and universal-log capability fields.
- Added Doctor v156, HTTP regressions, package assertions, and documentation.

## Distribution bytecode hygiene — 2026-08-21

- Confirmed the previous wheel carried 44 stale `__pycache__`/`.pyc` entries
  from a reused build tree.
- Added setuptools exclusion configuration and package-smoke assertions for
  both wheel and sdist archives.
- Rebuilt the wheel from a clean build tree with zero bytecode-cache entries;
  documented the release hygiene contract.

## Baseline response security policy — 2026-08-21

- Added shared response-boundary headers for content-type sniffing, referrer
  policy, and browser feature permissions.
- Published the exact policy in capabilities and OpenAPI response contracts.
- Added Doctor v154, focused/full validation, HTTP regression coverage,
  package assertions, and documentation.

## Correlated server timing — 2026-08-21

- Added standard `Server-Timing` processing measurements to every server
  response and `duration_ms` to API/health access-log lines beside `request_id`.
- Published the timing header, metric, and log-field contract in capabilities
  and OpenAPI, with the measurement boundary documented explicitly.
- Added Doctor v155, focused/full validation, package assertions, and live
  packaged-server coverage.

## Portable route path explainability — 2026-08-21

- Replaced the lossy portable CSV/JSON graph tuple with a backwards-compatible
  `PortableRoadGraph` that keeps directed edge metadata while preserving the
  existing two-value unpacking API.
- Portable rows now retain supplied `way_id`, `name`, `ref`, `highway`, `route`,
  and `oneway` values. Path-enabled route responses emit `portable_edges` with
  ordered way IDs, segment metrics, and basic road context; graphs without way
  IDs continue to fail closed as `not_available`.
- Updated the route contract, OpenAPI enum, capability/Doctor inventory, graph
  metadata, persistence writer, regression tests, and package CI assertions.

## Active routing-backend discovery — 2026-08-21

- Added a runtime `routing_graph` inventory to health and `/api/capabilities`,
  distinguishing full SQLite semantics from the portable CSV/JSON backend and
  unavailable graph state.
- Route, matrix, and comparison endpoint discovery now repeats the active graph
  backend, profile semantics, feature flags, and path-segment sources. This
  prevents project-level vehicle/restriction support from being interpreted as
  support in a currently loaded portable graph.
- Added a typed OpenAPI capability schema, Doctor implementation detection, and
  live portable-graph HTTP regression coverage.

## Active graph cardinality discovery — 2026-08-21

- Extended the runtime `routing_graph` inventory with metadata-backed counts
  for graph nodes, directed base edges, ferry edges, and way-context rows.
- Health, `/api/capabilities`, and route/matrix/comparison endpoint discovery
  now expose those counts, allowing clients to distinguish a complete cached
  graph from a small portable fixture without opening the graph themselves.
- The inventory also exposes `metadata_status` as `available`, `not_provided`,
  or `invalid`, while missing, malformed, negative, or absent metadata counts
  remain explicit `null` values; the OpenAPI schema and HTTP regressions cover
  portable, unavailable, and invalid-sidecar states.

## Portable ferry geometry semantics — 2026-08-21

- Portable edges now retain an explicit ferry flag from `route=ferry` or
  `ferry=yes`, normalize the route context for CSV/JSON persistence, and expose
  a portable `ferry_edge_n` graph count.
- Portable routing skips those edges by default and includes them only with
  `include_ferries`. Compact and detailed route responses now preserve ferry
  way IDs, physical distance, and edge count; detailed responses additionally
  preserve `ferry: true`. Portable timing continues to use the basic geometry
  estimate and does not claim schedules, waits, or crossing durations.
- The batch routing path now keeps the `PortableRoadGraph` object through
  loading, rather than reducing it to bare adjacency and losing ferry policy.
  Active capability discovery and OpenAPI separately advertise
  `ferry_geometry` and `ferry_schedules` for backend-accurate inspection.
- Added regression coverage for opt-in exclusion, path metrics, persistence
  round-trips, capability discovery, and the portable/full-SQLite distinction.

## Fíorú validation field — 2026-08-21

- Added a visible `Fíorú / validation field` to the cultural lens, keeping
  independent artifact checks, holdout evidence, and human calibration in the
  same visual language as the Irish land and geometry layers.
- Connected the field to `summary.validation`, `holdout_results.csv`, and
  `review_calibration.csv`: the current pack shows three passing core gates,
  eight deterministic holdout results, and zero supplied expert labels without
  converting any of those states into a claim of historic intent.
- Added responsive styling, generated-report regression assertions, regenerated
  and published the Pages artifact, and passed pipeline verification, generated
  JavaScript parsing, Pages audit, local HTTP, `git diff --check`, and the full
  project test suite.

## Cineál heritage typology — 2026-08-21

- Added a `Cineál / heritage typology` field to the cultural lens, deriving its
  cards from the existing NIAH type, rating, group, circularity, φ, and θ fields
  on the report targets.
- The current view ranks its visible NIAH-linked building types and shows joined
  row count, golden-angle and golden-ratio rates, mean circularity, dominant
  mapped cohort, and common rating; selecting a card reuses the existing
  `niahType` filter and shareable URL state.
- Kept embedded and lazy scope explicit, added responsive styling and report
  regression assertions, regenerated and published the atlas, and passed
  pipeline verification, generated-JavaScript parsing, Pages audit, local HTTP,
  `git diff --check`, and the full project test suite.

## Fite county–type braid — 2026-08-21

- Added a `Fite / county–type braid` relationship field that groups the
  existing NIAH-linked rows by county and building type, keeping φ/θ screens
  attached to both place and source vocabulary.
- Six leading county cards show typed row count, within-county screen rates, and
  leading NIAH types; type chips set county and `niahType` together, while
  county headers reuse the existing county filter and URL state.
- Kept the embedded/lazy scope boundary visible, added responsive styling and
  report assertions, regenerated and published the atlas, and passed pipeline
  verification, generated-JavaScript parsing, Pages audit, local HTTP,
  `git diff --check`, and the full project test suite.

## Rian selected-building evidence trail — 2026-08-21

- Added a `Rian / evidence trail` panel to the selected-building dossier so a
  visitor can follow one footprint across four distinct source lanes: OSM
  geometry, NIAH heritage inventory, historical/attribution evidence, and
  review plus mapping history.
- Each lane now reports a small source-backed fingerprint, a clear `present`,
  `check`, or `not provided` state, and a direct source/review link where the
  current pack supplies one. Missing history or edit history stays visible as
  a data boundary rather than being inferred from mathematical resemblance.
- Added responsive evidence cards and generated-report regression assertions,
  regenerated and published the Pages artifact, and passed pipeline
  verification, generated-JavaScript parsing, Pages audit, local HTTP,
  `git diff --check`, and the full project test suite.

## Timpeall selected-building context ring — 2026-08-21

- Added a `Timpeall / surrounding field` layer to the selected-building
  dossier. It computes the five nearest mapped footprints from valid latitude
  and longitude pairs, shows straight-line centroid distance, place/group
  context, area/aspect, and shared φ/θ/symmetry symbols, and lets each nearby
  card become the next selected place.
- Added a small coordinate plot with radial distance rings and a visible scope
  label: the embedded report reads the full snapshot, while the lazy report
  reads only its current loaded page. Copy keeps the result explicitly
  geometric; it is not a road route, walking distance, or claim of shared
  historical design.
- Added responsive styling and report assertions, regenerated and published
  the Pages artifact, and passed both full pipeline verification passes,
  generated-JavaScript parsing, Pages audit, local HTTP, `git diff --check`,
  and the full project test suite.

## Paired relationship brief provenance — 2026-08-21

- Extended the studio’s copy/download concept brief so a carried Idir pair is
  preserved outside the live UI: Field A/B names, centroid span, A→B bearing,
  place context, and shared measured screens are now included in the exported
  brief when a pair is active.
- Kept the brief language contemporary and bounded; a relationship is carried
  as a design prompt, not rewritten as historic coordination or authorship.
- Regenerated and published the Pages artifact, and passed both full pipeline
  verification passes, generated-JavaScript parsing, Pages audit, local HTTP,
  `git diff --check`, and the full project test suite.

## Idir place-to-place comparison bridge — 2026-08-21

- Extended the two-place comparison tray with an `Idir / between places`
  relationship layer. It reports straight-line centroid span and bearing,
  same-settlement or same-county context, NIAH type/join relationship, shared
  mathematical screens, mapped-group relationship, and B−A shape deltas.
- Added a compact A→B field plot and responsive relationship ledger. The bridge
  stays hidden until two places are held, preserves comparison URL state, and
  keeps every result descriptive: distance is not a route and shared signals
  are not evidence of shared authorship or historic intent.
- Added generated-report assertions, regenerated and published the Pages
  artifact, and passed both full pipeline verification passes,
  generated-JavaScript parsing, Pages audit, local HTTP, `git diff --check`,
  and the full project test suite.

## Idir relationship-to-studio bridge — 2026-08-21

- Added a `Carry relationship to studio` handoff to the two-place Idir field.
  A/B place context, NIAH join status, centroid span, bearing, county bridge,
  and shared geometry screens now travel into a paired studio reference.
- Added shareable `studio_pair_a` and `studio_pair_b` state, a paired-place
  studio card, and an editable `Use A→B bearing as path rotation` control.
  The bearing is reduced to a directionless axis for the contemporary test-fit;
  it never becomes a claim about the historic relationship between buildings.
- Added responsive styling and report assertions, regenerated and published
  the Pages artifact, and passed both full pipeline verification passes,
  generated-JavaScript parsing, Pages audit, local HTTP, `git diff --check`,
  and the full project test suite.

## Idir land chord on the map — 2026-08-21

- Added a measured A→B chord to the actual map layer whenever two comparison
  places are held. The live Leaflet view now carries a dashed relationship line
  with A/B endpoint markers and hover readouts for straight-line span and
  bearing; the offline SVG fallback carries the same chord and labels.
- Comparison bounds now include both held places even when filters or a route
  would otherwise hide them, so the relationship remains visible as a land
  connection rather than only a card-level calculation.
- Added regression assertions, regenerated after versioning, published the
  updated Pages artifact, and passed pipeline verification, generated-
  JavaScript parsing, Pages audit, local HTTP, `git diff --check`, and the full
  project test suite.
