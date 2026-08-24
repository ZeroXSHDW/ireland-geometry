# Optional routable graph

For actual network distances, provide `road_nodes.csv` and `road_edges.csv`,
or a disk-backed `road_graph.sqlite`, in this directory; `--road-graph` also
accepts another directory/JSON/SQLite graph. CSV nodes contain
`node_id,lat,lon`; CSV edges contain `u,v,length_m` and optional
`oneway,highway`. SQLite stores physical road edges plus a separate optional
ferry-edge layer and indexed node cells, and applies OSM one-way semantics plus
validated via-node and via-way turn restrictions during Dijkstra. Both
backends use deterministic nearest-node snapping.

When a SQLite graph carries `ferry_schedules.json`, its optional `source` field
is a portable project-relative POSIX reference (or an `external/` basename for
inputs outside the project). Generated graph metadata never stores the
absolute path of the machine that built it.

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
complete v21 workspace graph contains 961,735 routable road ways, 7,427,417
nodes, and 7,553,171 physical road segments after excluding 161,518 access-filtered
ways. It also stores 1,549 ferry segments from 77 access-allowed ferry ways and
38 ferry route relations; these are excluded by default and can be enabled with
`--include-ferries`. It applies 3,940 non-conditional `no_*`/`only_*`
restrictions, including 181 validated via-way chains, and stores 73
graph-resolved conditional restriction rules from 78 supported relations: 74
time windows and one
vehicle-weight rule. Its metadata also records 56 supported conditional
road-access rules (24 general-traffic, 19 delivery-vehicle, 7
public-service-vehicle, and 6 taxi rules, including 16 directional rules and 1
weight-qualified rule), 4 unsupported conditional-access tags, and 14 ways
excluded by the default
conditional/access policy. Delivery-only ways are retained in the SQLite graph
but remain unavailable to the default general profile; pass
`--vehicle-class delivery` (or `vehicle_class=delivery` in the local API) to
evaluate them, or `--vehicle-class psv` (or `vehicle_class=psv`) for
public-service-vehicle windows, or `--vehicle-class taxi` (or
`vehicle_class=taxi`) for taxi-specific windows. Its
Multiple supported clauses on one conditional key are retained as an ordered
rule set per way and direction; active class-specific allows form a union,
then active denies remove matching classes. The graph metadata exposes the
number of multi-clause ways separately from the total rule count.
metadata records the source PBF,
counts, access policy, restriction and ferry coverage, and completeness flag;
four conditional references remain unresolved because their source ways are
pedestrian/cycleway-only; the current PBF has no unsupported conditional turn
expression. Time windows and supported conditional road-access rules require
`--departure` and `--speed-kmh` unless the stored rule is unconditional 24/7;
the delivery profile is selected with `--vehicle-class delivery`; the psv
profile is selected with `--vehicle-class psv`; the taxi profile is selected
with `--vehicle-class taxi`; the
weight-qualified delivery access rule requires `--weight-t` (or API
`weight_t`) and remains inactive without that profile;
the weight rule requires `--weight-t` (or API `weight_t`) and is inactive when
no vehicle profile is supplied. Generic OSM `maxweight` is also retained on
1,101 ways, with 1,100 numeric limits across 9,918 segments; `--weight-t` (or
API `weight_t`) enforces those limits on SQLite routes, and one ambiguous way
is fail-closed only when a weight profile is active. Numeric `maxweight:hgv` is
also retained on 35 ways across 289 directed segments; select
`--vehicle-class hgv` with `--weight-t` (or the matching API parameters) to
enforce those HGV-specific limits. A separate HGV permitted-rating profile
(`--rating-t`/`rating_t`) enforces numeric `maxweightrating:hgv` limits and the
Irish `maxweightrating:goods` alias on 216 routable ways: 214 supported numeric
ways and 2 ambiguous ways across 3,180 directed segments. This profile
describes permitted gross-weight rating rather than actual mass and is active
only for `--vehicle-class hgv`; ambiguous values are fail-closed for
rating-qualified routes. Destination-dependent and unloading-specific
conditions remain reported but are not evaluated. The v21 SQLite contract also
retains 39 supported destination-qualified HGV ways across 333 directed
segments, covering static `hgv=destination` access and observed
`none @ destination` exceptions for HGV actual weight or permitted rating.
Default `hgv` routes block static destination-only ways; pass
`--allow-hgv-destination` (or `allow_hgv_destination=true` in the API) only
when the route serves the restricted destination. That opt-in bypasses only
the corresponding supported conditional numeric exception; unrelated base
limits remain enforced. The cached ferry companion records 26 crossing
durations and 4 parsed seasonal/weekly service schedules, including 1
public-holiday-qualified schedule. The sibling `public_holidays.json`
companion now provides the explicit `ireland-geometry.public-holidays.v1` date
contract with 80 dates covering 2023–2030; graph metadata reports that count
and its date bounds. Time-aware route metrics wait for the next supported
opening when a ferry is reached outside its window. Terminal platform semantics
and service frequency are not modeled. Numeric legal `maxheight` is retained on
1,509 ways across 9,324 directed segments: 984 supported numeric values, 22
explicit unlimited values, and 503 ambiguous values. Numeric metre values and
documented feet/inches values such as `9'6"` are normalized to metres;
ambiguous values remain fail-closed for height-qualified routes. Numeric
`maxheight:physical` is retained on 12 ways
across 25 segments. Supply `--height-m` (or API `height_m`) to enforce both
height constraints; ambiguous values are fail-closed only for height-qualified
routes. Numeric `maxwidth` is retained on 14 ways across 131 directed
segments, `maxlength` on 5 ways across 95 directed segments, and `maxaxleload`
on 10 ways across 84 directed segments. Supply `--width-m`/`width_m`,
`--length-m`/`length_m`, or `--axleload-t`/`axleload_t` to enforce the
corresponding profiles; width/length feet-and-inches values are normalized to
metres. Unsupported, destination-dependent, and condition-dependent variants
remain outside this contract.
The v21 SQLite contract also stores numeric OSM `maxspeed` values in
`maxspeed_kmh` with a `maxspeed_status`, plus raw normalized conditional
clauses in `maxspeed_conditional_json`. Duration routing applies supported
ceilings per way, capped by the configured fallback speed. The current graph
retains 196,440 routable `maxspeed` ways: 196,435 supported numeric ways across
2,115,207 directed segments and 5 non-numeric ways across 19 segments. Its 225
`maxspeed:conditional` ways span 2,638 directed segments; 185 ways use
  supported weekly schedules evaluated from explicit departures, while 40 ways
  retain unsupported syntax without inventing a speed ceiling.
The v21 edge contract also stores combined `oneway:conditional` and
`oneway:motor_vehicle:conditional` clauses in `oneway_conditional_json`; five
ways span 31 directed segments, with two supported schedule ways and three
unsupported permit/private ways. Supported windows are evaluated from an
explicit departure; unsupported syntax preserves the base one-way semantics.

The v21 SQLite `ways` table additionally persists nullable OSM identity fields
(`name`, `ref`, `highway`, `route`, and `oneway`). Detailed route responses
expose these values as `road_context` on each segment; older graphs without
the extended columns remain readable and return null context fields.

The legacy CSV graph remains as a bounded portable fallback with 1,137,520
nodes and 2,149,088 directed edges. The complete graph is an OSM routing
snapshot, not a traffic-aware navigation network: conditional rules are
inactive or omitted unless a departure profile is supplied, ferry geometry is opt-in and
schedule-aware only for the modeled companion entries, and access permissions
are represented by the selected motor-vehicle tag policy rather than
user-specific permissions.
