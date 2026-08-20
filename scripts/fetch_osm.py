#!/usr/bin/env python3
"""Fetch building footprints of interest for ALL of Ireland
(Republic of Ireland + Northern Ireland) from OpenStreetMap via the
public Overpass API.

Channels used:
  * OpenStreetMap vector footprints (churches / places of worship,
    government & civic buildings, historic castles/manors/ruins)
    -> Overpass API  (open data, ODbL)

Yandex Maps is intentionally NOT used: its terms of service prohibit
bulk automated retrieval of map data. OSM covers Ireland's building
footprints far more completely for this kind of analysis.

To stay inside the anonymous Overpass API's query limits, the island
is fetched as a 4x3 grid of bounding boxes (each ~120x150 km). Results
are cached in data/raw/<group>__<cell>.json and merged into
data/combined.json. Nothing is re-downloaded unless --refresh is passed.

Usage:
    python3 scripts/fetch_osm.py                # fetch all groups/cells
    python3 scripts/fetch_osm.py --refresh      # force re-download
    python3 scripts/fetch_osm.py --groups worship --bbox 51.3,-10.7,52.7,-8.4
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from runtime import (
        atomic_write_json,
        project_data_path,
        reject_symlink_path,
        reject_symlink_root,
    )
except ImportError:
    from scripts.runtime import (
        atomic_write_json,
        project_data_path,
        reject_symlink_path,
        reject_symlink_root,
    )

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

# Ireland extent (incl. Northern Ireland)
LON_MIN, LON_MAX, LAT_MIN, LAT_MAX = -10.7, -5.8, 51.3, 55.4
GRID_COLS, GRID_ROWS = 4, 3

# Each group is a list of complete Overpass QL statements; __BBOX__ is
# replaced with "(south,west,north,east)" per grid cell.
#
# IMPORTANT: exact-value filters (["building"="church"]) are evaluated via
# Overpass's tag index and are hundreds of times faster than regexes.
GROUPS = {
    "worship": [
        'way["building"="church"]__BBOX__;',
        'way["building"="cathedral"]__BBOX__;',
        'way["building"="chapel"]__BBOX__;',
        'way["building"="basilica"]__BBOX__;',
        'way["building"="monastery"]__BBOX__;',
        'way["building"="abbey"]__BBOX__;',
        'way["building"="priory"]__BBOX__;',
        'way["building"="convent"]__BBOX__;',
        'way["building"="religious"]__BBOX__;',
        'way["amenity"="place_of_worship"]__BBOX__;',
        'rel["building"="church"]__BBOX__;',
        'rel["building"="cathedral"]__BBOX__;',
        'rel["building"="chapel"]__BBOX__;',
        'rel["amenity"="place_of_worship"]__BBOX__;',
    ],
    "government": [
        'way["building"="government"]__BBOX__;',
        'way["building"="civic"]__BBOX__;',
        'way["building"="public"]__BBOX__;',
        'way["building"="townhall"]__BBOX__;',
        'way["building"="courthouse"]__BBOX__;',
        'way["building"="city_hall"]__BBOX__;',
        'way["office"="government"]__BBOX__;',
        'way["amenity"="townhall"]__BBOX__;',
    ],
    "historic": [
        'way["building"="castle"]__BBOX__;',
        'way["building"="manor"]__BBOX__;',
        'way["building"="tower"]__BBOX__;',
        'way["building"="ruins"]__BBOX__;',
        'way["building"]["historic"]__BBOX__;',
    ],
}


def grid_cells():
    """Yield (name, (s, w, n, e)) for a 4x3 grid covering Ireland."""
    cells = []
    lat_step = (LAT_MAX - LAT_MIN) / GRID_ROWS
    lon_step = (LON_MAX - LON_MIN) / GRID_COLS
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            s = LAT_MIN + r * lat_step
            n = LAT_MIN + (r + 1) * lat_step
            w = LON_MIN + c * lon_step
            e = LON_MIN + (c + 1) * lon_step
            cells.append(
                (f"r{r + 1}c{c + 1}", (round(s, 4), round(w, 4), round(n, 4), round(e, 4)))
            )
    return cells


def build_query(group: str, bbox: tuple) -> str:
    bbox_str = f"({','.join(str(v) for v in bbox)})"
    lines = "\n".join("  " + ln.replace("__BBOX__", bbox_str) for ln in GROUPS[group])
    return "[out:json][timeout:180][maxsize:1073741824];\n(\n" + lines + "\n);\n" + "out geom;"


def post_overpass(query: str, mirror: str) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(
        mirror,
        data=body,
        headers={"User-Agent": "ireland-geometry-scan/0.1 (research; contact: local)"},
    )
    with urllib.request.urlopen(req, timeout=290) as resp:
        return json.loads(resp.read())


def fetch_cell(group: str, cell: tuple, data_dir: Path, refresh: bool) -> int:
    """Fetch one group for one grid cell; returns element count."""
    name, bbox = cell
    raw_dir = reject_symlink_root(data_dir / "raw", label="OSM raw data directory")
    out = raw_dir / f"{group}__{name}.json"
    if out.exists() and not refresh:
        try:
            reject_symlink_path(out, label="OSM raw cache file")
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        try:
            return len(json.loads(out.read_text()).get("elements", []))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    query = build_query(group, bbox)
    last_err = None
    for attempt, mirror in enumerate(MIRRORS, 1):
        try:
            print(f"[fetch] {group} {name} {bbox} <- {mirror.split('/')[2]} ...", flush=True)
            payload = post_overpass(query, mirror)
            n = len(payload.get("elements", []))
            out.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(out, payload)
            return n
        except (
            OSError,
            TimeoutError,
            ValueError,
            urllib.error.URLError,
            json.JSONDecodeError,
        ) as exc:
            last_err = exc
            detail = ""
            if isinstance(exc, urllib.error.HTTPError):
                detail = exc.read(400).decode(errors="replace").splitlines()[-2:]
                detail = " ".join(detail)
            print(f"[fetch]   failed ({exc}) {detail}", flush=True)
            time.sleep(5)
    print(f"[fetch] ERROR: {group} {name} after all mirrors: {last_err}", file=sys.stderr)
    return -1


def combine(data_dir: Path) -> None:
    merged: dict = {}
    raw_dir = reject_symlink_root(data_dir / "raw", label="OSM raw data directory")
    for f in sorted(raw_dir.glob("*.json")):
        try:
            reject_symlink_path(f, label="OSM raw cache file")
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        try:
            payload = json.loads(f.read_text())
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        for el in payload.get("elements", []):
            merged[(el["type"], el["id"])] = el
    comb = {"elements": list(merged.values())}
    atomic_write_json(data_dir / "combined.json", comb)
    print(f"[fetch] combined: {len(comb['elements'])} unique elements", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--groups", default="worship,government,historic", help="comma-separated groups"
    )
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--bbox", default="", help="single bbox override: s,w,n,e (testing)")
    args = ap.parse_args()

    data_dir = project_data_path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        raw_dir = reject_symlink_root(data_dir / "raw", label="OSM raw data directory")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    raw_dir.mkdir(parents=True, exist_ok=True)

    groups = [g for g in args.groups.split(",") if g]
    for g in groups:
        if g not in GROUPS:
            sys.exit(f"[fetch] unknown group '{g}' (known: {', '.join(GROUPS)})")

    cells = grid_cells()
    if args.bbox:
        s, w, n, e = (float(x) for x in args.bbox.split(","))
        cells = [("custom", (s, w, n, e))]

    total = 0
    for cell in cells:
        for g in groups:
            n = fetch_cell(g, cell, data_dir, args.refresh)
            if n < 0:
                sys.exit(1)
            total += n
            time.sleep(2)  # be polite to the public API
    combine(data_dir)
    print(f"[fetch] done. {total} element rows fetched (before dedupe).")


if __name__ == "__main__":
    main()
