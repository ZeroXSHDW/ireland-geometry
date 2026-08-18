# Optional routable graph

For actual network distances, provide `road_nodes.csv` and `road_edges.csv`,
or a disk-backed `road_graph.sqlite`, in this directory; `--road-graph` also
accepts another directory/JSON/SQLite graph. CSV nodes contain
`node_id,lat,lon`; CSV edges contain `u,v,length_m` and optional
`oneway,highway`. SQLite stores physical road edges plus a separate optional
ferry-edge layer and indexed node cells, and applies OSM one-way semantics plus
validated via-node and via-way turn restrictions during Dijkstra. Both
backends use deterministic nearest-node snapping.

The direct adapter can persist a bounded portable CSV graph:

```bash
python scripts/road_routing.py \
  --from-pbf --pbf data/raw/ireland-latest.osm.pbf \
  --write-graph data/roads --max-ways 100000 --max-pairs 0
```

The exported graph contains directed `road_nodes.csv`, `road_edges.csv`, and
`road_graph_metadata.json`; a later pipeline run can consume it with
`--road-graph data/roads`.

For a complete graph, use the disk-backed exporter:

```bash
python scripts/road_routing.py \
  --from-pbf --pbf data/raw/ireland-latest.osm.pbf \
  --write-graph data/roads --graph-format sqlite --max-ways 0 --max-pairs 0
```

`--max-ways 0` means all supported highway ways and is intentionally accepted
only with SQLite output, because a complete graph is too large for a safe
in-memory adjacency list. By default, ways blocked by the most specific
`motor_vehicle`, `motorcar`, `vehicle`, or `access` tag are excluded. The
complete workspace graph contains 961,724 routable road ways, 7,427,367
nodes, and 7,553,120 physical road segments after excluding 161,529 restricted
ways. It also stores 1,549 ferry segments from 77 access-allowed ferry ways and
38 ferry route relations; these are excluded by default and can be enabled with
`--include-ferries`. It applies 3,938 non-conditional `no_*`/`only_*`
restrictions, including 181 validated via-way chains, and stores 72
graph-resolved conditional windows. Its metadata records the source PBF,
counts, access policy, restriction and ferry coverage, and completeness flag;
four conditional references are unresolved and one conditional expression uses
unsupported syntax. Ferry schedules, terminal platform semantics, and service
frequency are not modeled.

The legacy CSV graph remains as a bounded portable fallback with 1,137,520
nodes and 2,149,088 directed edges. The complete graph is an OSM routing
snapshot, not a traffic-aware navigation network: conditional windows are
inactive unless a departure profile is supplied, ferry geometry is opt-in and
schedule-free, and access permissions are represented by the selected
motor-vehicle tag policy rather than user-specific permissions.
