#!/usr/bin/env python3
"""v5a: does the ROAD (or RIVER) network explain the ~44-deg alignment?

The v4 point-pattern stage found weak ~40-45 deg anisotropy in
inter-church segment bearings (peak at 44 deg, MC p=0.007) that is NOT
golden-specific. Two natural confounds are tested against the SAME
Geofabrik extract (data/raw/ireland-latest.osm.pbf):

  1. road siting — churches sit along roads; if the road network runs
     ~NE-SW, church-to-church edges inherit it.
  2. river/valley siting — Ireland's Caledonian structural grain runs
     NE-SW; churches concentrate along river valleys, which could put
     church-to-church edges at ~40-45 deg.

Each network's bearing histogram (segments >= 200 m, equal weight) is
compared with the inter-church edge histogram (built exactly as in
point_pattern.py): 44+-3 fraction vs the church figure, global peak,
and Pearson correlation of the 1-deg histograms.

Reads:  data/raw/ireland-latest.osm.pbf, output/analysis_results.csv
Writes: output/roads_compare.csv
"""

from __future__ import annotations

import csv
import io
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import osmium as o
from point_pattern import (
    bearings_between,
    frac_within_tol,
    nearest_neighbours,
    peak_over_grid,
    project,
)

OUT = ROOT / "output"
PBF = ROOT / "data" / "raw" / "ireland-latest.osm.pbf"
RESULTS = OUT / "analysis_results.csv"

GOLDEN_ANGLE = 137.50776405003785
TOL_DEG = 3.0
K_NEAREST = 4
MAX_R = 5000.0
MIN_SEG_M = 200.0
UNIFORM_FRAC_3 = 6.0 / 180.0  # expected fraction in any +-3 window

# drivable roads (churches are sited along these); exclude paths/ferries
ROAD_HIGHWAYS = {
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "service",
    "living_street",
    "track",
    "road",
    "motorway_link",
    "trunk_link",
    "primary_link",
    "secondary_link",
    "tertiary_link",
}
RIVER_WATERWAYS = {"river"}


def seg_bearing_deg(x0, y0, x1, y1):
    """Undirected bearing (mod 180) of a segment, in degrees."""
    a = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0
    return a


class NetworkHandler(o.SimpleHandler):
    """Collect ways tagged as drivable roads and/or rivers."""

    def __init__(self) -> None:
        super().__init__()
        self.roads: list[list[tuple[float, float]]] = []
        self.rivers: list[list[tuple[float, float]]] = []

    def way(self, w) -> None:
        tags = w.tags
        if tags.get("highway") in ROAD_HIGHWAYS:
            self._add(w, self.roads)
        elif tags.get("waterway") in RIVER_WATERWAYS:
            self._add(w, self.rivers)

    @staticmethod
    def _add(w, sink) -> None:
        coords = [(n.lon, n.lat) for n in w.nodes if n.location.valid()]
        if len(coords) >= 2:
            sink.append(coords)


def network_bearings(ways: list[list[tuple[float, float]]], name: str) -> list[float]:
    """Segment bearings (equal weight, len >= MIN_SEG_M)."""
    segs: list[float] = []
    n_raw = 0
    for coords in ways:
        lat0 = sum(c[1] for c in coords) / len(coords)
        kx = 111320.0 * math.cos(math.radians(lat0))
        ky = 110540.0
        pts = [(c[0] * kx, c[1] * ky) for c in coords]
        for i in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            n_raw += 1
            if math.hypot(x1 - x0, y1 - y0) >= MIN_SEG_M:
                segs.append(seg_bearing_deg(x0, y0, x1, y1))
    print(
        f"[roads] {name}: {len(ways)} ways, {n_raw} raw segments, "
        f"{len(segs)} kept (>= {MIN_SEG_M:.0f} m)",
        flush=True,
    )
    return segs


def church_edge_bearings():
    """Inter-church edge bearings, identical to point_pattern.py."""
    rows = [
        r
        for r in csv.DictReader(RESULTS.open())
        if r["group"] == "worship" and r["is_control"] == "0"
    ]
    lats = [float(r["lat"]) for r in rows]
    lons = [float(r["lon"]) for r in rows]
    xs, ys = project(lats, lons)
    nbrs = nearest_neighbours(xs, ys, K_NEAREST, max_r=MAX_R)
    pairs = set()
    for i, nb in enumerate(nbrs):
        for j in nb:
            pairs.add(tuple(sorted((i, j))))
    pairs = list(pairs)
    return bearings_between(xs, ys, pairs), len(rows)


def hist_corr(a: list[float], b: list[float]) -> float:
    """Pearson r of the two 1-deg histograms (0..180)."""
    ha = [0.0] * 180
    hb = [0.0] * 180
    for x in a:
        ha[min(int(x) % 180, 179)] += 1.0
    for x in b:
        hb[min(int(x) % 180, 179)] += 1.0
    sa = sum(ha)
    sb = sum(hb)
    ha = [v / sa for v in ha]
    hb = [v / sb for v in hb]
    ma, mb = sum(ha) / 180, sum(hb) / 180
    num = sum((x - ma) * (y - mb) for x, y in zip(ha, hb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ha) * sum((y - mb) ** 2 for y in hb))
    return num / den if den else 0.0


def main() -> None:
    import argparse

    global OUT, PBF, RESULTS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", default=None, help="data directory; defaults to project data/")
    ap.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    ap.add_argument(
        "--pbf", default=None, help="OSM PBF path; defaults to data/raw/ireland-latest.osm.pbf"
    )
    args = ap.parse_args()
    try:
        from runtime import (
            atomic_write_text,
            project_data_tree_path,
            project_input_path,
            project_output_tree_path,
        )
    except ImportError:
        from scripts.runtime import (
            atomic_write_text,
            project_data_tree_path,
            project_input_path,
            project_output_tree_path,
        )
    data_root = project_data_tree_path(args.data_root)
    OUT = project_output_tree_path(args.out_dir)
    PBF = project_input_path(
        args.pbf,
        str(data_root / "raw" / "ireland-latest.osm.pbf"),
        label="roads PBF",
    )
    RESULTS = OUT / "analysis_results.csv"

    if not RESULTS.exists():
        raise SystemExit(f"Missing {RESULTS}. Run scripts/analyze.py first.")

    edges, n_church = church_edge_bearings()
    c44 = frac_within_tol(edges, 44.0, TOL_DEG)
    cg = frac_within_tol(edges, GOLDEN_ANGLE, TOL_DEG)
    peak_a, peak_f = peak_over_grid(edges)
    print(
        f"[roads] church edges: n={len(edges)} 44+-3={c44 * 100:.2f}% "
        f"137.5+-3={cg * 100:.2f}% peak={peak_a:.0f}deg "
        f"({peak_f * 100:.2f}%)",
        flush=True,
    )

    if not PBF.exists():
        raise SystemExit(f"Missing {PBF}. Run fetch_geofabrik.py first.")
    handler = NetworkHandler()
    print(f"[roads] scanning {PBF.name} ...", flush=True)
    handler.apply_file(str(PBF), locations=True)
    rseg = np.asarray(network_bearings(handler.roads, "roads"), dtype=float)
    vseg = np.asarray(network_bearings(handler.rivers, "rivers"), dtype=float)

    rows = []
    for name, angles, c_frac, c_peak in (
        ("roads", rseg, c44, peak_a),
        ("rivers", vseg, c44, peak_a),
    ):
        if len(angles) < 500:
            print(f"[roads] {name}: too few segments ({len(angles)})", flush=True)
            continue
        a44 = frac_within_tol(angles, 44.0, TOL_DEG)
        ag = frac_within_tol(angles, GOLDEN_ANGLE, TOL_DEG)
        apk_a, apk_f = peak_over_grid(angles)
        r = hist_corr(list(edges), list(angles))
        print(
            f"[roads] {name}: n={len(angles)} 44+-3={a44 * 100:.2f}% "
            f"137.5+-3={ag * 100:.2f}% peak={apk_a:.0f}deg "
            f"({apk_f * 100:.2f}%)  hist r={r:.3f}",
            flush=True,
        )
        if r >= 0.40:
            v = (
                f"{name} histogram strongly matches church edges -> "
                f"church alignment explained by {name} siting"
            )
        elif r >= 0.15:
            v = (
                f"{name} partly matches church edges -> {name} siting "
                f"contributes, but not the full story"
            )
        else:
            v = (
                f"{name} do NOT explain the church 44-deg anisotropy "
                f"(r={r:.2f}; {name}@44deg {a44 * 100:.2f}% vs uniform "
                f"{UNIFORM_FRAC_3 * 100:.2f}%)"
            )
        print(f"[roads] {name} verdict: {v}", flush=True)
        rows.append((name, len(angles), a44, ag, apk_a, apk_f, r, v))

    stream = io.StringIO(newline="")
    w = csv.writer(stream)
    w.writerow(["metric", "church_edges"] + [r[0] for r in rows])
    w.writerow(["n", len(edges)] + [r[1] for r in rows])
    w.writerow(["frac_44_3_pct", round(c44 * 100, 2)] + [round(r[2] * 100, 2) for r in rows])
    w.writerow(["frac_137_3_pct", round(cg * 100, 2)] + [round(r[3] * 100, 2) for r in rows])
    w.writerow(["peak_angle_deg", round(peak_a, 1)] + [round(r[4], 1) for r in rows])
    w.writerow(["peak_frac_pct", round(peak_f * 100, 2)] + [round(r[5] * 100, 2) for r in rows])
    w.writerow(["hist_corr_r", "-"] + [round(r[6], 4) for r in rows])
    w.writerow(["n_churches", n_church] + ["-"] * len(rows))
    w.writerow(["verdict"] + ["-"] + [r[7] for r in rows])
    atomic_write_text(OUT / "roads_compare.csv", stream.getvalue())
    print("[roads] wrote output/roads_compare.csv")


if __name__ == "__main__":
    main()
