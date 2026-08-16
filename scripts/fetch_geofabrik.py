#!/usr/bin/env python3
"""PRIMARY ingestion channel: the complete OSM extract for Ireland.

Downloads the daily Geofabrik snapshot of ALL OpenStreetMap data for
Ireland + Northern Ireland (one 400 MB file, no API rate limits) and
extracts building footprints locally with pyosmium.

v2 improvements:
  * multipolygon RELATIONS are assembled (cathedral complexes, abbeys,
    monasteries) with courtyard holes preserved
  * round towers / ruined monasteries included (historic=* without a
    building tag, man_made=tower)
  * a deterministic CONTROL group of ordinary buildings (1-in-41 sample
    of everything else) is extracted so pattern matches can be tested
    for statistical significance against the background rate

Channels:
  * Geofabrik OSM extract (ireland-and-northern-ireland-latest.osm.pbf)
    -> processed fully offline -> data/combined.json
  * (optional) scripts/fetch_osm.py keeps the live Overpass API as a
    refresh channel for smaller on-demand updates.

Yandex Maps is intentionally NOT used (ToS forbids bulk automated
retrieval). OSM is openly licensed (ODbL) and is the most complete
vector footprint source for Ireland.

Usage:
    python3 scripts/fetch_geofabrik.py                 # download if needed + extract
    python3 scripts/fetch_geofabrik.py --no-download   # extract only
python3 scripts/fetch_geofabrik.py --refresh       # re-extract over cache
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import osmium

try:
    from runtime import atomic_write_json, project_path
except ImportError:
    from scripts.runtime import atomic_write_json, project_path

PBF_URL = "https://download.geofabrik.de/europe/ireland-and-northern-ireland-latest.osm.pbf"

SAMPLE_DIVISOR = 41  # keep every 41st ordinary building as control group

WORSHIP = {
    "church",
    "cathedral",
    "chapel",
    "basilica",
    "religious",
    "monastery",
    "abbey",
    "priory",
    "convent",
    "shrine",
}
GOV = {
    "government",
    "civic",
    "public",
    "townhall",
    "courthouse",
    "city_hall",
    "bank",
    "post_office",
    "museum",
    "library",
}
HIST = {"castle", "manor", "tower", "ruins"}
HIST_STANDALONE = {
    "ruins",
    "monastery",
    "abbey",
    "priory",
    "friary",
    "castle",
    "tower",
    "church",
    "chapel",
}


def wanted(tags: dict) -> bool:
    b = tags.get("building")
    h = tags.get("historic")
    if b in WORSHIP or tags.get("amenity") == "place_of_worship":
        return True
    if b in GOV or tags.get("office") == "government" or tags.get("amenity") == "townhall":
        return True
    if b in HIST:
        return True
    if b and h:
        return True
    if h in HIST_STANDALONE:
        return True
    return tags.get("man_made") == "tower"


def is_control(tags: dict) -> bool:
    """Ordinary building (not a target group) -> control candidate."""
    return bool(tags.get("building")) and not wanted(tags)


def ring_coords(ring) -> list:
    return [{"lat": n.location.lat, "lon": n.location.lon} for n in ring if n.location.valid()]


def geometry_from_rings(rings: list[tuple[list, list]]) -> dict | None:
    """Serialize valid outer rings while keeping each ring's holes attached.

    Filtering invalid outers must happen together with their holes. The old
    list-filtering approach could shift hole indexes when an invalid outer
    appeared before a valid one.
    """
    pairs = []
    for exterior, holes in rings:
        if len(exterior) < 4:
            continue
        valid_holes = [hole for hole in holes if len(hole) >= 4]
        pairs.append((exterior, valid_holes))
    if not pairs:
        return None
    if len(pairs) == 1:
        return {"exterior": pairs[0][0], "holes": pairs[0][1]}
    return {
        "exteriors": [exterior for exterior, _ in pairs],
        "holes_by_exterior": [holes for _, holes in pairs],
    }


class ExtractHandler(osmium.SimpleHandler):
    def __init__(self, out: list):
        super().__init__()
        self.out = out
        self.count = 0
        self.control = 0

    def area(self, a):
        """Called for every assembled area (closed ways + relations).
        apply_file() runs the two OSM passes automatically when an
        area() callback exists."""
        tags = dict(a.tags)
        if not tags:
            return
        ctrl = is_control(tags)
        if not (wanted(tags) or ctrl):
            return
        try:
            src_way = a.from_way().id
        except Exception:  # noqa: BLE001 - pyosmium exposes version-dependent exceptions
            src_way = a.id
        if ctrl:
            if src_way % SAMPLE_DIVISOR != 0:
                return
            self.control += 1
        outers = list(a.outer_rings())
        if not outers:
            return
        ring_pairs = []
        for outer in outers:
            exterior = ring_coords(outer)
            holes = [ring_coords(hole) for hole in a.inner_rings(outer)]
            ring_pairs.append((exterior, holes))
        geometry = geometry_from_rings(ring_pairs)
        if geometry is None:
            return
        try:
            is_mp = a.is_multipolygon() if callable(a.is_multipolygon) else a.is_multipolygon
        except Exception:  # noqa: BLE001 - pyosmium exposes version-dependent exceptions
            is_mp = False
        osm_type = "relation" if is_mp else "way"
        self.out.append(
            {
                "type": "area",
                "osm_type": osm_type,
                "id": a.id,
                "src_way_id": src_way,
                "tags": tags,
                "geometry": geometry,
                "control": ctrl,
            }
        )
        self.count += 1
        if self.count % 2500 == 0:
            print(f"[geofabrik] ...{self.count} kept (control {self.control})", flush=True)


def download_pbf(dest: Path) -> None:
    print(f"[geofabrik] downloading {PBF_URL}", flush=True)
    req = urllib.request.Request(PBF_URL, headers={"User-Agent": "ireland-geometry-scan/0.1"})
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(req, timeout=180) as r, tmp.open("wb") as fh:
        total = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            total += len(chunk)
            if total % (50 << 20) == 0:
                print(f"[geofabrik] ...{total / 1e6:.0f} MB", flush=True)
    tmp.rename(dest)
    print(f"[geofabrik] saved {dest} ({total / 1e6:.0f} MB)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pbf", default=None, help="PBF path; defaults to project data/raw/ireland-latest.osm.pbf"
    )
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument(
        "--out", default=None, help="combined JSON path; defaults to project data/combined.json"
    )
    ap.add_argument(
        "--refresh", action="store_true", help="re-extract even if data/combined.json exists"
    )
    args = ap.parse_args()

    out_path = project_path(args.out, "data/combined.json")
    if out_path.exists() and not args.refresh:
        n = len(json.loads(out_path.read_text()).get("elements", []))
        print(f"[geofabrik] using cached {out_path} ({n} elements); pass --refresh to re-extract")
        return

    pbf = project_path(args.pbf, "data/raw/ireland-latest.osm.pbf")
    if not pbf.exists():
        if args.no_download:
            sys.exit(f"[geofabrik] {pbf} missing; run without --no-download")
        pbf.parent.mkdir(parents=True, exist_ok=True)
        download_pbf(pbf)

    elements: list = []
    handler = ExtractHandler(elements)
    print(f"[geofabrik] scanning {pbf} (2 passes, assembling areas) ...", flush=True)
    handler.apply_file(str(pbf), locations=True)
    print(f"[geofabrik] kept {handler.count} footprints ({handler.control} control)", flush=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out_path, {"elements": elements})
    print(f"[geofabrik] wrote {out_path}")


if __name__ == "__main__":
    main()
