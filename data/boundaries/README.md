# Optional spatial covariates

Place an administrative GeoJSON FeatureCollection at
`data/boundaries/admin.geojson` and, optionally, a settlement layer at
`data/boundaries/settlements.geojson`. The loader expects longitude/latitude
coordinates and reads common properties such as `county`, `NAME_1`, `NAME_2`,
`name`, `place`, and `settlement_class`. Use `--boundaries` and
`--settlements` to point elsewhere.
