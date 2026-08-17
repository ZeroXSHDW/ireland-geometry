# Final status

Status: complete for the current cached snapshot; the validation, 3-D/evidence,
holdout, routing, scalability, review, data-quality, spatial-bootstrap, and
reproducibility tranches are included.

## Reproducible build

```text
.venv/bin/python run_pipeline.py --stage all --no-network --seed 20260816 --mc 300
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
  LiDAR not provided in this snapshot);
- 33,416 historical-validation rows, 1,000 candidate dossiers, 30 Ripley rows,
  5 Moran rows, 8 county permutations, and 5 road-proximity summaries;
- 123,810 spatial-covariate rows and mapping-history rows (history source not
  supplied locally);
- 50,468 strict no-replacement pairs, 12 strict significance rows, 37,422
  holdout assignments, 8 holdout results, and a 1,000-row review queue;
- explicit `not_provided` routed-road outputs; JSONL scale export, lazy report
  data pack, optional Parquet/DuckDB adapters, and CI/reproducibility checks;
- a 123,810-row data-quality audit with zero duplicate OSM IDs, 23 duplicate
  centroid locations flagged for review, 100% valid geometry, and 12 spatial
  block-bootstrap uncertainty rows;
- `output/report.html` interactive dashboard;
- `output/manifest.json` schema-version-3 provenance record with input/output
  hashes, byte sizes, and row counts.
- `output/verification.json` automated artifact-contract result.

## Verification

```text
.venv/bin/python -m pytest                 # 27 passed
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
also passed.

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
reported as a sampled nearest-road diagnostic; actual routing remains
`not_provided` until a routable graph is supplied.

The spatial block bootstrap remains directionally consistent with the main
golden-angle elevation, while making the spatial unit of resampling explicit;
it is an uncertainty diagnostic, not a causal estimate.

## Remaining limitations

OSM geometry is community-mapped and is not an architectural plan. NIAH is a
heritage inventory for the Republic of Ireland rather than a complete census,
so Northern Ireland coverage is sparse. Only 246 analyzed footprints have
mapped `building:part` associations and no LiDAR or OSM history file was
supplied. Boundary, settlement, road graph, and expert labels are also optional
and absent in this snapshot. Candidate thresholds were chosen for screening,
and a confirmatory study should populate those source contracts, review the
holdout plan, validate against independent plan or LiDAR data, and manually
check the candidate dossiers.
