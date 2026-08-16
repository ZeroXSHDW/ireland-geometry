# Final status

Status: complete for the current cached snapshot.

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
- `output/report.html` interactive dashboard;
- `output/manifest.json` provenance record with source hashes and counts.

## Verification

```text
.venv/bin/python -m pytest                 # 12 passed
.venv/bin/ruff check scripts tests run_pipeline.py
.venv/bin/python -m compileall -q scripts run_pipeline.py
.venv/bin/python run_pipeline.py --help
```

Additional artifact assertions passed: manifest revision/counts, GeoJSON
parsing, source-region completeness, positive p-values/adjusted p-values, and
absence of unresolved report template tokens.

## Interpretation

In this snapshot, golden-ratio aspect matching is background relative to the
ordinary-building controls. The 137.5° footprint-angle flag is elevated in
target groups, including worship, but that result is an association in mapped
geometry and cannot establish design intent. NIAH era-matched tests show the
strongest worship differences in the 19th and 20th centuries after family
correction. Between-building tests do not support a golden-specific alignment;
the broad peak is near 44°, spiral turns do not support the hypothesis, and
architect attribution is non-significant.

## Remaining limitations

OSM geometry is community-mapped and is not an architectural plan. NIAH is a
heritage inventory for the Republic of Ireland rather than a complete census,
so Northern Ireland coverage is sparse. Candidate thresholds were chosen for
screening, and a confirmatory study should preregister hypotheses, spatially
and temporally match controls, account for mapping quality, and validate against
independent plan data.
