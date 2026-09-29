#!/usr/bin/env python3
"""Download and cache the NIAH (National Inventory of Architectural Heritage)
open dataset — the official state record of Ireland's post-1700 built
heritage, CC BY 4.0, no credentials required.

Data source: https://www.buildingsofireland.ie/niah-data-download/
(five regional CSVs: Dublin, Connacht, Leinster, Munster, Ulster).

Key structured fields this project uses:
  * DATEFROM / DATETO  — construction date range (year)
  * RATING             — Regional / National / International (heritage rating)
  * ORIGINAL_TYPE      — classification (church/chapel, house, bridge, ...)
  * LATITUDE/LONGITUDE — record centroid (WGS84) for the spatial join
  * COMPOSITION/APPRAISAL — free text (architect, builder, descriptions)

Reads:  (network; cached in data/niah/*.zip + extracted CSVs)
Writes: data/niah/niah.json   (normalized records, one per building/feature)

Usage:
    python3 scripts/fetch_niah.py
    python3 scripts/fetch_niah.py --refresh   # re-download + re-extract
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import zipfile
from pathlib import Path

try:
    from .runtime import (
        atomic_write_stream,
        atomic_write_text,
        project_data_path,
        reject_symlink_path,
        reject_symlink_root,
        reject_symlink_tree,
    )
except ImportError:
    from runtime import (
        atomic_write_stream,
        atomic_write_text,
        project_data_path,
        reject_symlink_path,
        reject_symlink_root,
        reject_symlink_tree,
    )

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "niah"

REGIONS = {
    "Dublin": "https://www.buildingsofireland.ie/niah/NIAHDataDownload/Dublin.zip",
    "Connacht": "https://www.buildingsofireland.ie/niah/NIAHDataDownload/Connacht.zip",
    "Leinster": "https://www.buildingsofireland.ie/niah/NIAHDataDownload/Leinster.zip",
    "Munster": "https://www.buildingsofireland.ie/niah/NIAHDataDownload/Munster.zip",
    "Ulster": "https://www.buildingsofireland.ie/niah/NIAHDataDownload/Ulster.zip",
}

ENCODING = "latin-1"  # all regional CSVs are latin-1 (irish diacritics)


def parse_year(raw: str) -> int | None:
    """Extract a sane 4-digit construction year from a NIAH date cell.

    Handles the ~3 malformed values in 43k records:
      * '950'     (3-digit, kept as-is -> 950)
      * '11925'   (digit-split typo for 1925 -> 1925)
      * '175'     (kept as-is)
    """
    if not raw:
        return None
    m = re.search(r"\d+", str(raw))
    if not m:
        return None
    v = int(m.group())
    if 1000 <= v <= 2100:
        return v
    if 100 <= v < 1000:  # 3-digit year, keep
        return v
    if v > 2100:  # digit-split typo: try dropping leading digits
        s = str(v)
        for cut in range(1, len(s) - 3):
            cand = int(s[cut:])
            if 1000 <= cand <= 2100:
                return cand
    return None


def century_label(year: float) -> str:
    c = int(year) // 100 + 1
    if c <= 17:
        return "pre-18th"
    if c >= 21:
        return "21st"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(c % 10, "th")
    if c in (11, 12, 13):
        suffix = "th"
    return f"{c}{suffix}"


def century50_label(year: float) -> str:
    """50-year bin, e.g. 1845 -> '1850-1899' bucket start -> 1850s."""
    base = (int(year) // 50) * 50
    return f"{base}-{base + 49}"


def normalize(region: str, r: dict) -> dict:
    date_from = parse_year(r.get("DATEFROM", ""))
    date_to = parse_year(r.get("DATETO", ""))
    years = [y for y in (date_from, date_to) if y is not None]
    # guard against wildly divergent from/to (digit typos): trust `from`
    if len(years) == 2 and abs(years[0] - years[1]) > 500:
        years = [years[0]]
    date_mid = float(sum(years)) / len(years) if years else None

    rating = (r.get("RATING", "") or "").strip()
    rec = {
        "source_region": region,
        "reg_no": (r.get("REG_NO", "") or "").strip(),
        "name": (r.get("NAME", "") or "").strip(),
        "town": (r.get("TOWN", "") or "").strip(),
        "townland": (r.get("TOWNLAND", "") or "").strip(),
        "county": (r.get("COUNTY", "") or "").strip(),
        "rating": rating,
        "rating_high": rating in ("National", "International"),
        "original_type": (r.get("ORIGINAL_TYPE", "") or "").strip().lower(),
        "date_from": date_from,
        "date_to": date_to,
        "date_mid": round(date_mid, 1) if date_mid is not None else None,
        "century": century_label(date_mid) if date_mid is not None else None,
        "century50": century50_label(date_mid) if date_mid is not None else None,
        "lat": float(r["LATITUDE"]),
        "lon": float(r["LONGITUDE"]),
        "composition": (r.get("COMPOSITION", "") or "").strip(),
        "appraisal": (r.get("APPRAISAL", "") or "").strip(),
    }
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--refresh", action="store_true", help="re-download and re-extract the regional archives"
    )
    ap.add_argument(
        "--no-network",
        action="store_true",
        help="never download; require all regional archives to be cached",
    )
    ap.add_argument(
        "--data-root",
        default=None,
        help="data directory; defaults to the project data/ directory",
    )
    args = ap.parse_args()

    global DATA
    DATA = reject_symlink_root(
        project_data_path(args.data_root) / "niah",
        label="NIAH data directory",
    )
    DATA.mkdir(parents=True, exist_ok=True)
    requests_module = None

    # ---- download --------------------------------------------------------
    for region, url in REGIONS.items():
        zp = DATA / f"{region}.zip"
        if zp.exists() and (not args.refresh or args.no_network):
            try:
                reject_symlink_path(zp, label=f"NIAH {region} archive")
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            print(f"[niah] using cached {zp.name}", flush=True)
            continue
        if args.no_network:
            raise SystemExit(f"[niah] missing cached archive for {region}: {zp}")
        print(f"[niah] downloading {region} ...", flush=True)
        if requests_module is None:
            import requests as requests_module
        r = requests_module.get(url, timeout=120, stream=True)
        try:
            r.raise_for_status()
            bytes_written = atomic_write_stream(
                zp,
                r.iter_content(chunk_size=1024 * 1024),
            )
        finally:
            r.close()
        print(f"[niah]   saved {bytes_written / 1e6:.1f} MB", flush=True)

    # ---- extract ----------------------------------------------------------
    for region in REGIONS:
        zp = DATA / f"{region}.zip"
        try:
            reject_symlink_path(zp, label=f"NIAH {region} archive")
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        try:
            out_dir = reject_symlink_root(
                DATA / region,
                label=f"NIAH {region} extraction directory",
            )
            out_dir = reject_symlink_tree(
                out_dir,
                label=f"NIAH {region} extraction directory",
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if out_dir.exists() and not args.refresh:
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zp) as z:
            for member in z.namelist():
                if member.lower().endswith(".csv"):
                    with z.open(member) as source:
                        atomic_write_stream(out_dir / Path(member).name, source)
        print(f"[niah] extracted {region}", flush=True)

    # ---- parse -------------------------------------------------------------
    records = []
    for csv_path in sorted(glob.glob(str(DATA / "*" / "*.csv"))):
        try:
            reject_symlink_path(csv_path, label="NIAH extracted CSV")
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        region = Path(csv_path).parent.name
        with open(csv_path, encoding=ENCODING, newline="") as fh:
            for i, r in enumerate(csv.DictReader(fh)):
                try:
                    rec = normalize(region, r)
                except (ValueError, TypeError):
                    print(f"[niah] skipping malformed row {i} in {csv_path}", flush=True)
                    continue
                if rec["lat"] is not None and rec["lon"] is not None:
                    records.append(rec)
        print(f"[niah] parsed {Path(csv_path).name}", flush=True)

    out = DATA / "niah.json"
    atomic_write_text(out, json.dumps(records, ensure_ascii=False))
    n_dated = sum(1 for r in records if r["date_mid"] is not None)
    print(f"[niah] wrote {out} — {len(records)} records ({n_dated} with construction dates)")


if __name__ == "__main__":
    main()
