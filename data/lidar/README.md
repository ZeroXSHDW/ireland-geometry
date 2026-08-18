# Optional LiDAR/DSM input

The building-parts stage accepts normalized per-building CSV/JSON/GeoJSON, a
GeoTIFF DSM/DTM, or geographic-coordinate LAS/LAZ when the optional
`geo3d` dependencies are installed. Raster input is sampled with a 3×3
centroid window; point-cloud input is aggregated within valid building
footprints. Projected LAS/LAZ should be converted to the normalized contract
or reprojected before use. Missing input is reported as `not_provided`.
