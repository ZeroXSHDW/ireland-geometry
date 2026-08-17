#!/usr/bin/env python3
"""Run the Ireland geometry pipeline from any working directory.

The default order is fetch -> fetch-niah -> analyze -> niah -> architects -> sensitivity ->
spatial-covariates -> osm-history -> validation -> building-parts -> historical -> review ->
quality-audit -> point-pattern -> spatial-stats -> spatial-bootstrap -> roads -> road-proximity -> road-routing -> holdout ->
columnar -> report -> repro-check -> verify. Use ``--stage`` to run one stage or a comma-separated
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
    "architects",
    "sensitivity",
    "spatial-covariates",
    "osm-history",
    "validation",
    "building-parts",
    "historical",
    "review",
    "quality-audit",
    "point-pattern",
    "spatial-stats",
    "spatial-bootstrap",
    "roads",
    "road-proximity",
    "road-routing",
    "holdout",
    "columnar",
    "report",
    "repro-check",
    "verify",
)
SCRIPTS = {
    "fetch": "fetch_geofabrik.py",
    "fetch-niah": "fetch_niah.py",
    "analyze": "analyze.py",
    "niah": "niah.py",
    "architects": "architects.py",
    "sensitivity": "sensitivity.py",
    "spatial-covariates": "spatial_covariates.py",
    "osm-history": "osm_history.py",
    "validation": "validation.py",
    "building-parts": "building_parts.py",
    "historical": "historical_validation.py",
    "review": "review.py",
    "quality-audit": "data_quality.py",
    "point-pattern": "point_pattern.py",
    "spatial-stats": "spatial_stats.py",
    "spatial-bootstrap": "spatial_bootstrap.py",
    "roads": "roads.py",
    "road-proximity": "road_proximity.py",
    "road-routing": "road_routing.py",
    "holdout": "holdout.py",
    "columnar": "columnar.py",
    "report": "report.py",
    "repro-check": "repro_check.py",
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
    parser.add_argument(
        "--lidar", default=None, help="optional normalized LiDAR CSV/GeoJSON for building-part stage"
    )
    parser.add_argument(
        "--historical-references",
        default=None,
        help="optional curated historical reference CSV for the historical stage",
    )
    parser.add_argument("--osm-history", default=None, help="optional normalized OSM history CSV/JSON")
    parser.add_argument("--boundaries", default=None, help="optional administrative boundary GeoJSON")
    parser.add_argument("--settlements", default=None, help="optional settlement GeoJSON")
    parser.add_argument("--road-graph", default=None, help="optional road graph directory or JSON")
    parser.add_argument("--road-from-pbf", action="store_true", help="opt in to building a routing graph from the PBF")
    parser.add_argument("--review-labels", default=None, help="optional expert review labels CSV")
    parser.add_argument("--analysis-plan", default=None, help="preregistered analysis plan JSON")
    parser.add_argument("--holdout-fraction", type=float, default=None)
    parser.add_argument("--bootstrap-iterations", type=int, default=200, help="spatial block-bootstrap iterations")
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
    elif name == "sensitivity":
        command += ["--out-dir", str(out_dir)]
    elif name == "spatial-covariates":
        command += ["--data-root", str(data_root), "--out-dir", str(out_dir)]
        if args.boundaries:
            command += ["--boundaries", str(project_path(args.boundaries, str(data_root / "boundaries" / "admin.geojson")))]
        if args.settlements:
            command += ["--settlements", str(project_path(args.settlements, str(data_root / "boundaries" / "settlements.geojson")))]
    elif name == "osm-history":
        command += ["--data-root", str(data_root), "--out-dir", str(out_dir)]
        if args.osm_history:
            command += ["--history", str(project_path(args.osm_history, str(data_root / "history" / "osm_history.csv")))]
    elif name == "validation":
        command += ["--out-dir", str(out_dir)]
    elif name == "building-parts":
        command += ["--data-root", str(data_root), "--out-dir", str(out_dir)]
        if args.lidar:
            command += ["--lidar", str(project_path(args.lidar, str(data_root / "lidar" / "building_heights.csv")))]
    elif name == "historical":
        command += ["--data-root", str(data_root), "--out-dir", str(out_dir)]
        if args.historical_references:
            command += [
                "--references",
                str(project_path(args.historical_references, str(data_root / "historical" / "references.csv"))),
            ]
    elif name == "review":
        command += ["--data-root", str(data_root), "--out-dir", str(out_dir)]
        if args.review_labels:
            command += ["--labels", str(project_path(args.review_labels, str(data_root / "review" / "labels.csv")))]
    elif name == "quality-audit":
        command += ["--out-dir", str(out_dir)]
    elif name in {"point-pattern", "spatial-stats"}:
        command += ["--out-dir", str(out_dir), "--seed", str(args.seed), "--mc", str(args.mc)]
    elif name == "spatial-bootstrap":
        command += [
            "--out-dir", str(out_dir), "--seed", str(args.seed),
            "--iterations", str(args.bootstrap_iterations),
        ]
    elif name == "roads":
        command += ["--data-root", str(data_root), "--out-dir", str(out_dir), "--pbf", str(pbf)]
    elif name == "road-proximity":
        command += [
            "--data-root",
            str(data_root),
            "--out-dir",
            str(out_dir),
            "--pbf",
            str(pbf),
            "--seed",
            str(args.seed),
        ]
    elif name == "road-routing":
        command += ["--data-root", str(data_root), "--out-dir", str(out_dir)]
        if args.road_graph:
            command += ["--road-graph", str(project_path(args.road_graph, str(data_root / "roads")))]
        if args.road_from_pbf:
            command += ["--from-pbf", "--pbf", str(pbf)]
    elif name == "holdout":
        command += ["--out-dir", str(out_dir), "--seed", str(args.seed)]
        if args.analysis_plan:
            command += ["--plan", str(project_path(args.analysis_plan, "analysis_plan.json"))]
        if args.holdout_fraction is not None:
            command += ["--fraction", str(args.holdout_fraction)]
    elif name in {"columnar", "report", "repro-check"}:
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
            "lidar": args.lidar,
            "historical_references": args.historical_references,
            "osm_history": args.osm_history,
            "boundaries": args.boundaries,
            "settlements": args.settlements,
            "road_graph": args.road_graph,
            "road_from_pbf": args.road_from_pbf,
            "review_labels": args.review_labels,
            "analysis_plan": args.analysis_plan,
            "holdout_fraction": args.holdout_fraction,
            "bootstrap_iterations": args.bootstrap_iterations,
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
            {
                "kind": "optional_lidar_normalized",
                "path": project_path(
                    args.lidar, str(data_root / "lidar" / "building_heights.csv")
                ),
            },
            {
                "kind": "optional_historical_references",
                "path": project_path(
                    args.historical_references,
                    str(data_root / "historical" / "references.csv"),
                ),
            },
            {
                "kind": "optional_osm_history",
                "path": project_path(args.osm_history, str(data_root / "history" / "osm_history.csv")),
            },
            {
                "kind": "optional_admin_boundaries",
                "path": project_path(args.boundaries, str(data_root / "boundaries" / "admin.geojson")),
            },
            {
                "kind": "optional_settlements",
                "path": project_path(args.settlements, str(data_root / "boundaries" / "settlements.geojson")),
            },
            {
                "kind": "optional_road_graph",
                "path": project_path(args.road_graph, str(data_root / "roads" / "road_nodes.csv")),
            },
            {
                "kind": "analysis_plan",
                "path": project_path(args.analysis_plan, "analysis_plan.json"),
            },
            {
                "kind": "optional_review_labels",
                "path": project_path(args.review_labels, str(data_root / "review" / "labels.csv")),
            },
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
