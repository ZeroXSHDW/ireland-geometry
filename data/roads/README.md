# Optional routable graph

For actual network distances, provide `road_nodes.csv` and
`road_edges.csv` in this directory, or pass `--road-graph` with another
directory/JSON graph. Nodes contain `node_id,lat,lon`; edges contain
`u,v,length_m` and optional `oneway,highway`. The routing stage uses
deterministic nearest-node snapping and Dijkstra shortest paths. `--road-from-pbf`
is available for an opt-in in-memory conversion of the supplied OSM PBF.
