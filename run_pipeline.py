#!/usr/bin/env python3
"""Run the Ireland geometry pipeline from any working directory.

The default order is fetch -> fetch-niah -> analyze -> niah -> point-pattern -> roads ->
architects -> report -> verify. Use ``--stage`` to run one stage or a comma-separated
subset. All paths are resolved relative to this project unless absolute.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGES = (
    "fetch",
    "fetch-niah",
    "analyze",
    "niah",
    "point-pattern",
    "roads",
    "architects",
    "report",
    "verify",
)
SCRIPTS = {
    "fetch": "fetch_geofabrik.py",
    "fetch-niah": "fetch_niah.py",
    "analyze": "analyze.py",
    "niah": "niah.py",
    "point-pattern": "point_pattern.py",
    "roads": "roads.py",
    "architects": "architects.py",
    "report": "report.py",
    "verify": "verify.py",
}


def project_path(value: str | None, default: str) -> Path:
    candidate = Path(value) if value else Path(default)
    return candidate if candidate.is_absolute() else ROOT / candidate


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh", action="store_true", help="re-extract/download the primary cached input"
    )
    parser.add_argument("--stage", default="all", help="all, one stage, or comma-separated stages")
    parser.add_argument("--data-root", default=None, help="data directory (default: project data/)")
    parser.add_argument(
        "--out-dir", default=None, help="output directory (default: project output/)"
    )
    parser.add_argument(
        "--pbf", default=None, help="OSM PBF path (default: data/raw/ireland-latest.osm.pbf)"
    )
    parser.add_argument(
        "--seed", type=int, default=20260816, help="reproducibility seed for stochastic stages"
    )
    parser.add_argument(
        "--mc", type=int, default=300, help="Monte Carlo iterations for point-pattern tests"
    )
    parser.add_argument(
        "--no-network", action="store_true", help="never download; require cached data"
    )
    parser.add_argument(
        "--skip-satellite",
        action="store_true",
        help="accepted for automation compatibility; satellite is optional",
    )
    return parser.parse_args(argv)


def selected_stages(value: str) -> list[str]:
    names = list(STAGES) if value == "all" else [x.strip() for x in value.split(",") if x.strip()]
    unknown = [name for name in names if name not in STAGES]
    if unknown:
        raise SystemExit(f"Unknown stage(s): {', '.join(unknown)}; choose from {', '.join(STAGES)}")
    if "verify" in names and names[-1] != "verify":
        raise SystemExit("The verify stage must be last so it checks the completed artifact set")
    return names


def run_stage(
    name: str,
    args: argparse.Namespace,
    data_root: Path,
    out_dir: Path,
    pbf: Path,
    env: dict[str, str],
) -> None:
    command = [sys.executable, str(ROOT / "scripts" / SCRIPTS[name])]
    if name == "fetch":
        command += ["--pbf", str(pbf), "--out", str(data_root / "combined.json")]
        if args.refresh:
            command.append("--refresh")
        if args.no_network:
            command.append("--no-download")
    elif name == "fetch-niah":
        command += ["--data-root", str(data_root)]
        if args.refresh:
            command.append("--refresh")
        if args.no_network:
            command.append("--no-network")
    elif name == "analyze":
        command += ["--data", str(data_root / "combined.json"), "--out", str(out_dir)]
    elif name in {"niah", "architects"}:
        command += ["--data-root", str(data_root), "--out-dir", str(out_dir)]
    elif name == "point-pattern":
        command += ["--out-dir", str(out_dir), "--seed", str(args.seed), "--mc", str(args.mc)]
    elif name == "roads":
        command += ["--data-root", str(data_root), "--out-dir", str(out_dir), "--pbf", str(pbf)]
    elif name == "report":
        command += ["--out-dir", str(out_dir)]
    elif name == "verify":
        command += [
            "--data-root",
            str(data_root),
            "--out-dir",
            str(out_dir),
            "--manifest",
            str(out_dir / "manifest.json"),
        ]
    print(f"\n===== {name} =====", flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def write_manifest(args: argparse.Namespace, data_root: Path, out_dir: Path, pbf: Path) -> None:
    try:
        from scripts.runtime import atomic_write_json, build_manifest
    except ImportError:
        from runtime import atomic_write_json, build_manifest
    manifest = build_manifest(
        data_root=data_root,
        out_dir=out_dir,
        parameters={
            "stage": args.stage,
            "refresh": args.refresh,
            "no_network": args.no_network,
            "skip_satellite": args.skip_satellite,
            "seed": args.seed,
            "mc": args.mc,
        },
        sources=[
            {
                "kind": "geofabrik_osm_pbf",
                "path": pbf,
                "url": "https://download.geofabrik.de/europe/ireland-and-northern-ireland-latest.osm.pbf",
            },
            {"kind": "combined_osm_json", "path": data_root / "combined.json"},
            {
                "kind": "niah_json",
                "path": data_root / "niah" / "niah.json",
                "url": "https://www.buildingsofireland.ie/niah-data-download/",
            },
            *[
                {
                    "kind": "niah_archive",
                    "path": data_root / "niah" / f"{region}.zip",
                    "url": f"https://www.buildingsofireland.ie/niah/NIAHDataDownload/{region}.zip",
                }
                for region in ("Dublin", "Connacht", "Leinster", "Munster", "Ulster")
            ],
        ],
    )
    atomic_write_json(out_dir / "manifest.json", manifest, indent=2)
    print(f"[pipeline] wrote {out_dir / 'manifest.json'}", flush=True)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    stages = selected_stages(args.stage)
    data_root = project_path(args.data_root, "data")
    out_dir = project_path(args.out_dir, "output")
    pbf = project_path(args.pbf, "data/raw/ireland-latest.osm.pbf")
    data_root.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["IRELAND_GEOMETRY_SEED"] = str(args.seed)
    env["IRELAND_GEOMETRY_MC"] = str(args.mc)
    if args.no_network:
        env["IRELAND_GEOMETRY_NO_NETWORK"] = "1"
    verify_requested = "verify" in stages
    for stage in stages:
        if stage == "verify":
            continue
        run_stage(stage, args, data_root, out_dir, pbf, env)
    write_manifest(args, data_root, out_dir, pbf)
    if verify_requested:
        run_stage("verify", args, data_root, out_dir, pbf, env)
        write_manifest(args, data_root, out_dir, pbf)
    print("\nDone. Open output/report.html (or the served URL) to explore.")


if __name__ == "__main__":
    main()
