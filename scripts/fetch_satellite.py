#!/usr/bin/env python3
"""Satellite imagery channel: download Sentinel-2 tiles covering Ireland
from the Copernicus Data Space Ecosystem (free, open, licensed CC-BY).

This is the legal "satellite" source for the project. Yandex Maps does
not permit bulk automated retrieval; Copernicus data is open by design.

Setup (one time, free):
  1. Register at https://dataspace.copernicus.eu  (get username/password)
  2. Put them in the env vars CDSE_USER and CDSE_PASS, or pass
     --user / --pass on the command line.

Usage:
    python3 scripts/fetch_satellite.py                     # preview only (no auth needed)
    CDSE_USER=me@x.ie CDSE_PASS=secret python3 scripts/fetch_satellite.py \
        --download --date 2026-06-01 --cloud 30

What it does:
  * Lists the most recent Sentinel-2 L2A tiles intersecting Ireland.
  * With --download, downloads each tile's 10 m RGB bands (.jp2) into
    data/satellite/ — ready for GIS overlays or training imagery models.

Note: building-footprint geometry analysis uses OSM vector footprints
(data/combined.json); the satellite layer is for verification and for
future ML/imagery-based feature extraction.
"""

import argparse
import json
import os
import urllib.parse
import urllib.request

try:
    from runtime import (
        atomic_write_stream,
        project_data_path,
        reject_symlink_root,
        reject_symlink_tree,
    )
except ImportError:
    from scripts.runtime import (
        atomic_write_stream,
        project_data_path,
        reject_symlink_root,
        reject_symlink_tree,
    )

IRELAND_BBOX = "POLYGON((-10.7 51.3, -10.7 55.4, -5.8 55.4, -5.8 51.3, -10.7 51.3))"
CATALOGUE = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)
BANDS = ["B02", "B03", "B04"]  # blue, green, red (10 m)


def get_token(user: str, pwd: str) -> str:
    data = urllib.parse.urlencode(
        {"grant_type": "password", "username": user, "password": pwd}
    ).encode()
    req = urllib.request.Request(TOKEN_URL, data=data)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["access_token"]


def list_tiles(token: str, date: str, cloud: int) -> list[dict]:
    filt = (
        "Collection/Name eq 'S2MSI2A' and "
        f"OData.CSC.Intersects(area=geography'SRID=4326;{IRELAND_BBOX}') and "
        f"ContentDate/Start gt {date}T00:00:00.000Z and "
        f"Attributes/OData.CSC.Intersects(attributes=cloudCover) le {cloud}"
    )
    url = CATALOGUE + "?$filter=" + urllib.parse.quote(filt) + "&$top=50"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()).get("value", [])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--download", action="store_true", help="actually download the tiles")
    ap.add_argument(
        "--date", default="2026-01-01", help="earliest sensing date (ISO). Default 2026-01-01"
    )
    ap.add_argument("--cloud", type=int, default=40, help="max cloud cover %% (default 40)")
    ap.add_argument("--user", default=os.environ.get("CDSE_USER", ""))
    ap.add_argument("--pass", dest="pwd", default=os.environ.get("CDSE_PASS", ""))
    args = ap.parse_args()

    if not args.user or not args.pwd:
        print("[satellite] No Copernicus credentials found.")
        print("            Sentinel-2 download requires a free account at")
        print("            https://dataspace.copernicus.eu and CDSE_USER/CDSE_PASS")
        print("            env vars (or --user/--pass). Run with --download once set.")
        print("[satellite] Hint: you can already browse satellite imagery live in")
        print("            output/report.html (Esri World Imagery layer).")
        return

    token = get_token(args.user, args.pwd)
    tiles = list_tiles(token, args.date, args.cloud)
    print(
        f"[satellite] {len(tiles)} Sentinel-2 L2A tiles found for Ireland "
        f"(since {args.date}, cloud <= {args.cloud}%)"
    )
    if not args.download:
        for t in tiles[:20]:
            print(f"  {t['Name']}  {t.get('ContentDate', {}).get('Start', '')[:10]}")
        print("  ... run with --download to fetch the 10 m RGB bands.")
        return

    out = reject_symlink_root(
        project_data_path(None) / "satellite",
        label="satellite data directory",
    )
    out = reject_symlink_tree(out, label="satellite data directory")
    out.mkdir(parents=True, exist_ok=True)
    for t in tiles:
        pid = t["Id"]
        # Each band is a separate asset; fetch B02..B04.
        for band in BANDS:
            url = f"{CATALOGUE}('{pid}')/Assets('{band}')/$value"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            dest = out / f"{t['Name']}_{band}.jp2"
            print(f"[satellite] {band} -> {dest.name}", flush=True)
            with urllib.request.urlopen(req, timeout=600) as r:
                atomic_write_stream(dest, r)
    print(f"[satellite] done -> {out}")


if __name__ == "__main__":
    main()
