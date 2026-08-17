# Final status

Status: complete for the current cached snapshot; the matched-control,
shape-descriptor, historical-validation, spatial-statistics, and production
hardening pass is included.

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
- `output/report.html` interactive dashboard;
- `output/manifest.json` schema-version-2 provenance record with input/output
  hashes, byte sizes, and row counts.
- `output/verification.json` automated artifact-contract result.

## Verification

```text
.venv/bin/python -m pytest                 # 20 passed
.venv/bin/ruff check scripts tests run_pipeline.py
.venv/bin/python -m compileall -q scripts run_pipeline.py
.venv/bin/python run_pipeline.py --help
.venv/bin/python run_pipeline.py --stage verify --no-network
```

Additional artifact assertions passed: manifest revision/counts and source
hashes, GeoJSON parsing and ID alignment, source-region completeness, matched
ID alignment, positive p-values/adjusted p-values, shape/context schema,
historical and LiDAR coverage contracts, absence of unresolved report template
tokens, and inline report JavaScript syntax.

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
background. County-preserving golden-angle permutations are suggestive for
worship, but Moran's I is not significant for worship after correction. Road
proximity is reported as a sampled nearest-road diagnostic, not a route model.

## Remaining limitations

OSM geometry is community-mapped and is not an architectural plan. NIAH is a
heritage inventory for the Republic of Ireland rather than a complete census,
so Northern Ireland coverage is sparse. Only 246 analyzed footprints have
mapped `building:part` associations and no LiDAR file was supplied. Candidate
thresholds were chosen for screening, and a confirmatory study should
preregister hypotheses, use authoritative boundaries and mapping-age/settlement
covariates, validate against independent plan or LiDAR data, and manually check
the candidate dossiers.
