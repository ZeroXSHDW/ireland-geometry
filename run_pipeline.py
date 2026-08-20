#!/usr/bin/env python3
"""Run the Ireland geometry pipeline from any working directory.

The default order is fetch -> fetch-niah -> analyze -> negative-controls -> niah -> architects -> sensitivity ->
spatial-covariates -> osm-history -> validation -> building-parts -> historical -> review ->
point-pattern -> spatial-stats -> spatial-bootstrap -> roads -> road-proximity -> road-routing -> quality-audit -> holdout ->
columnar -> schema-audit -> report -> repro-check -> verify. When both
``report`` and ``verify`` are selected, the report is refreshed once more
after the final validation pass so its data pack and compact interpretation
sidecar reflect the completed build. Use ``--stage`` to run one stage or a
comma-separated subset. ``--bundle`` adds a complete verified archive after
the final verification/report-refresh sequence; ``--bundle-archive`` can set
its destination. All paths are resolved relative to this project unless
absolute.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _default_project_root() -> Path:
    configured = os.environ.get("IRELAND_GEOMETRY_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if (ROOT / "pyproject.toml").is_file():
        return ROOT
    return Path.cwd().resolve()


PROJECT_ROOT = _default_project_root()

try:
    from scripts.road_routing import VEHICLE_CLASSES
    from scripts.runtime import (
        default_analysis_plan_path,
        package_version,
        reject_symlink_root,
        reject_symlink_tree,
    )
except ImportError:
    from road_routing import VEHICLE_CLASSES
    from runtime import (
        default_analysis_plan_path,
        package_version,
        reject_symlink_root,
        reject_symlink_tree,
    )

STAGES = (
    "fetch",
    "fetch-niah",
    "analyze",
    "negative-controls",
    "niah",
    "architects",
    "sensitivity",
    "spatial-covariates",
    "osm-history",
    "validation",
    "building-parts",
    "historical",
    "review",
    "point-pattern",
    "spatial-stats",
    "spatial-bootstrap",
    "roads",
    "road-proximity",
    "road-routing",
    "quality-audit",
    "holdout",
    "columnar",
    "schema-audit",
    "report",
    "repro-check",
    "verify",
)
SCRIPTS = {
    "fetch": "fetch_geofabrik.py",
    "fetch-niah": "fetch_niah.py",
    "analyze": "analyze.py",
    "negative-controls": "negative_controls.py",
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
    "schema-audit": "schema_audit.py",
    "report": "report.py",
    "repro-check": "repro_check.py",
    "verify": "verify.py",
}
DIAGNOSTIC_ONLY_STAGES = frozenset({"report", "repro-check", "schema-audit", "verify"})


def project_path(value: str | None, default: str) -> Path:
    candidate = Path(value) if value else Path(default)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="re-extract/download the primary cached input"
    )
    parser.add_argument("--stage", default="all", help="all, one stage, or comma-separated stages")
    parser.add_argument(
        "--project-root",
        default=None,
        help="project directory for relative data/output paths (default: checkout root or current directory)",
    )
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
        "--lidar", default=None, help="optional normalized LiDAR CSV/JSON/GeoJSON for building-part stage"
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
    parser.add_argument(
        "--routing-include-restricted",
        action="store_true",
        help="retain OSM private/restricted/no-access ways during PBF routing extraction",
    )
    parser.add_argument(
        "--routing-include-ferries",
        action="store_true",
        help="include persisted ferry geometry and any modeled service windows in routing",
    )
    parser.add_argument(
        "--routing-max-ways",
        type=int,
        default=100_000,
        help="maximum highway ways to scan for --road-from-pbf",
    )
    parser.add_argument(
        "--routing-max-pairs",
        type=int,
        default=5_000,
        help="maximum target/control pairs to route",
    )
    parser.add_argument(
        "--routing-departure",
        default=None,
        help="optional ISO-8601 local departure time for conditional turn restrictions",
    )
    parser.add_argument(
        "--routing-speed-kmh",
        type=float,
        default=50.0,
        help="assumed routing speed for conditional turn windows",
    )
    parser.add_argument(
        "--routing-weight-t",
        type=float,
        default=None,
        help="optional vehicle weight profile in metric tonnes for conditional routing",
    )
    parser.add_argument(
        "--routing-rating-t",
        type=float,
        default=None,
        help=(
            "optional HGV permitted gross-weight rating in metric tonnes for "
            "maxweightrating:hgv and Irish maxweightrating:goods routing"
        ),
    )
    parser.add_argument(
        "--routing-height-m",
        type=float,
        default=None,
        help="optional vehicle height profile in metres for dimensional routing",
    )
    parser.add_argument(
        "--routing-width-m",
        type=float,
        default=None,
        help="optional vehicle width profile in metres for dimensional routing",
    )
    parser.add_argument(
        "--routing-length-m",
        type=float,
        default=None,
        help="optional vehicle length profile in metres for dimensional routing",
    )
    parser.add_argument(
        "--routing-axleload-t",
        type=float,
        default=None,
        help="optional vehicle axle-load profile in metric tonnes for dimensional routing",
    )
    parser.add_argument(
        "--routing-vehicle-class",
        choices=VEHICLE_CLASSES,
        default="general",
        help="vehicle-class profile for conditional road access (default: general)",
    )
    parser.add_argument(
        "--routing-allow-hgv-destination",
        action="store_true",
        help=(
            "allow an explicit hgv routing profile to use destination-only "
            "ways and supported destination weight/rating exceptions"
        ),
    )
    parser.add_argument("--review-labels", default=None, help="optional expert review labels CSV")
    parser.add_argument("--analysis-plan", default=None, help="preregistered analysis plan JSON")
    parser.add_argument("--holdout-fraction", type=float, default=None)
    parser.add_argument("--bootstrap-iterations", type=int, default=200, help="spatial block-bootstrap iterations")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="reuse a stage only when its code, inputs, parameters, and output hashes still match",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show the stage plan and cache decisions without running stages or writing files",
    )
    parser.add_argument(
        "--dry-run-json",
        action="store_true",
        help="emit the dry-run plan as machine-readable JSON (implies --dry-run)",
    )
    parser.add_argument(
        "--bundle",
        action="store_true",
        help="create a complete verified bundle after the final verify stage",
    )
    parser.add_argument(
        "--bundle-archive",
        default=None,
        help="bundle ZIP path for --bundle (default: beside --out-dir as <name>.bundle.zip)",
    )
    args = parser.parse_args(argv)
    if args.dry_run_json:
        args.dry_run = True
    if args.bundle_archive and not args.bundle:
        parser.error("--bundle-archive requires --bundle")
    if args.mc < 1:
        parser.error("--mc must be positive")
    if args.bootstrap_iterations < 1:
        parser.error("--bootstrap-iterations must be positive")
    if args.holdout_fraction is not None and (
        not math.isfinite(args.holdout_fraction) or not 0.0 < args.holdout_fraction < 1.0
    ):
        parser.error("--holdout-fraction must be finite and strictly between 0 and 1")
    if args.routing_max_ways < 1:
        parser.error("--routing-max-ways must be positive")
    if args.routing_max_pairs < 0:
        parser.error("--routing-max-pairs must be non-negative")
    if not math.isfinite(args.routing_speed_kmh) or args.routing_speed_kmh <= 0:
        parser.error("--routing-speed-kmh must be a finite positive number")
    if args.routing_weight_t is not None and (
        not math.isfinite(args.routing_weight_t) or args.routing_weight_t <= 0
    ):
        parser.error("--routing-weight-t must be a finite positive number of tonnes")
    if args.routing_rating_t is not None and (
        not math.isfinite(args.routing_rating_t) or args.routing_rating_t <= 0
    ):
        parser.error("--routing-rating-t must be a finite positive number of tonnes")
    if args.routing_height_m is not None and (
        not math.isfinite(args.routing_height_m) or args.routing_height_m <= 0
    ):
        parser.error("--routing-height-m must be a finite positive number of metres")
    for value, option, unit in (
        (args.routing_width_m, "--routing-width-m", "metres"),
        (args.routing_length_m, "--routing-length-m", "metres"),
        (args.routing_axleload_t, "--routing-axleload-t", "tonnes"),
    ):
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error(f"{option} must be a finite positive number of {unit}")
    return args


def selected_stages(value: str) -> list[str]:
    names = list(STAGES) if value == "all" else [x.strip() for x in value.split(",") if x.strip()]
    if not names:
        raise SystemExit("At least one pipeline stage must be selected")
    unknown = [name for name in names if name not in STAGES]
    if unknown:
        raise SystemExit(f"Unknown stage(s): {', '.join(unknown)}; choose from {', '.join(STAGES)}")
    if "verify" in names and names[-1] != "verify":
        raise SystemExit("The verify stage must be last so it checks the completed artifact set")
    return names


def build_stage_command(
    name: str,
    args: argparse.Namespace,
    data_root: Path,
    out_dir: Path,
    pbf: Path,
) -> list[str]:
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
        plan_path = (
            project_path(args.analysis_plan, "analysis_plan.json")
            if args.analysis_plan
            else default_analysis_plan_path(PROJECT_ROOT, package_root=ROOT)
        )
        command += ["--plan", str(plan_path)]
    elif name == "negative-controls":
        command += ["--out-dir", str(out_dir)]
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
        command += [
            "--data-root",
            str(data_root),
            "--out-dir",
            str(out_dir),
            "--max-pairs",
            str(args.routing_max_pairs),
            "--speed-kmh",
            str(args.routing_speed_kmh),
        ]
        if args.routing_departure:
            command += ["--departure", args.routing_departure]
        if args.routing_weight_t is not None:
            command += ["--weight-t", str(args.routing_weight_t)]
        if args.routing_rating_t is not None:
            command += ["--rating-t", str(args.routing_rating_t)]
        if args.routing_height_m is not None:
            command += ["--height-m", str(args.routing_height_m)]
        if args.routing_width_m is not None:
            command += ["--width-m", str(args.routing_width_m)]
        if args.routing_length_m is not None:
            command += ["--length-m", str(args.routing_length_m)]
        if args.routing_axleload_t is not None:
            command += ["--axleload-t", str(args.routing_axleload_t)]
        command += ["--vehicle-class", args.routing_vehicle_class]
        if args.routing_allow_hgv_destination:
            command.append("--allow-hgv-destination")
        if args.road_graph:
            command += ["--road-graph", str(project_path(args.road_graph, str(data_root / "roads")))]
        if args.road_from_pbf:
            command += [
                "--from-pbf",
                "--pbf",
                str(pbf),
                "--max-ways",
                str(args.routing_max_ways),
            ]
            if args.routing_include_restricted:
                command.append("--include-restricted")
            if args.routing_include_ferries:
                command.append("--include-ferries")
        elif args.routing_include_ferries:
            command.append("--include-ferries")
    elif name == "holdout":
        command += ["--out-dir", str(out_dir), "--seed", str(args.seed)]
        plan_path = (
            project_path(args.analysis_plan, "analysis_plan.json")
            if args.analysis_plan
            else default_analysis_plan_path(PROJECT_ROOT, package_root=ROOT)
        )
        command += ["--plan", str(plan_path)]
        if args.holdout_fraction is not None:
            command += ["--fraction", str(args.holdout_fraction)]
    elif name in {"columnar", "report", "repro-check"} or name == "schema-audit":
        command += ["--out-dir", str(out_dir)]
    elif name == "verify":
        command += [
            "--data-root",
            str(data_root),
            "--out-dir",
            str(out_dir),
            "--project-root",
            str(PROJECT_ROOT),
            "--manifest",
            str(out_dir / "manifest.json"),
        ]
    return command


def default_bundle_archive(out_dir: Path, value: str | None = None) -> Path:
    """Resolve the optional post-build bundle path beside the output directory."""
    default = out_dir.parent / f"{out_dir.name}.bundle.zip"
    return project_path(value, str(default)).expanduser() if value else default


def build_bundle_command(out_dir: Path, archive: Path) -> list[str]:
    """Build a complete verified bundle from the final output directory."""
    return [
        sys.executable,
        str(ROOT / "scripts" / "bundle.py"),
        "--out-dir",
        str(out_dir),
        "--archive",
        str(archive),
        "--require-verified",
    ]


def build_bundle_verify_command(archive: Path) -> list[str]:
    """Verify the archive produced by the post-validation bundle operation."""
    return [
        sys.executable,
        str(ROOT / "scripts" / "bundle.py"),
        "--verify",
        str(archive),
    ]


def run_bundle(out_dir: Path, archive: Path, env: dict[str, str]) -> None:
    """Build and independently verify the post-validation publication bundle."""
    command = build_bundle_command(out_dir, archive)
    print("\n===== bundle =====", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=True)
    verify_command = build_bundle_verify_command(archive)
    print("\n===== bundle-verify =====", flush=True)
    subprocess.run(verify_command, cwd=PROJECT_ROOT, env=env, check=True)


def run_stage(
    name: str,
    args: argparse.Namespace,
    data_root: Path,
    out_dir: Path,
    pbf: Path,
    env: dict[str, str],
    command: list[str] | None = None,
) -> None:
    command = command or build_stage_command(name, args, data_root, out_dir, pbf)
    print(f"\n===== {name} =====", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=True)


def manifest_parameters(args: argparse.Namespace) -> dict[str, object]:
    """Return the invocation parameters that define an analytical build."""
    return {
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
        "routing_include_restricted": args.routing_include_restricted,
        "routing_include_ferries": args.routing_include_ferries,
        "routing_max_ways": args.routing_max_ways,
        "routing_max_pairs": args.routing_max_pairs,
        "routing_departure": args.routing_departure,
        "routing_speed_kmh": args.routing_speed_kmh,
        "routing_weight_t": args.routing_weight_t,
        "routing_rating_t": args.routing_rating_t,
        "routing_height_m": args.routing_height_m,
        "routing_width_m": args.routing_width_m,
        "routing_length_m": args.routing_length_m,
        "routing_axleload_t": args.routing_axleload_t,
        "routing_vehicle_class": args.routing_vehicle_class,
        "routing_allow_hgv_destination": args.routing_allow_hgv_destination,
        "review_labels": args.review_labels,
        "analysis_plan": args.analysis_plan,
        "holdout_fraction": args.holdout_fraction,
        "bootstrap_iterations": args.bootstrap_iterations,
        "seed": args.seed,
        "mc": args.mc,
        "incremental": args.incremental,
    }


def write_manifest(
    args: argparse.Namespace,
    data_root: Path,
    out_dir: Path,
    pbf: Path,
    *,
    preserve_context: bool = False,
) -> None:
    try:
        from scripts.runtime import atomic_write_json, build_manifest
    except ImportError:
        from runtime import atomic_write_json, build_manifest
    current_parameters = manifest_parameters(args)
    manifest = build_manifest(
        data_root=data_root,
        out_dir=out_dir,
        parameters=current_parameters,
        project_root=PROJECT_ROOT,
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
                "path": project_path(args.road_graph, str(data_root / "roads")),
            },
            {
                "kind": "analysis_plan",
                "path": (
                    project_path(args.analysis_plan, "analysis_plan.json")
                    if args.analysis_plan
                    else default_analysis_plan_path(PROJECT_ROOT, package_root=ROOT)
                ),
            },
            {
                "kind": "optional_review_labels",
                "path": project_path(args.review_labels, str(data_root / "review" / "labels.csv")),
            },
        ],
    )
    existing_path = out_dir / "manifest.json"
    if preserve_context and existing_path.is_file():
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict) and isinstance(existing.get("parameters"), dict):
            manifest["parameters"] = existing["parameters"]
            if isinstance(existing.get("sources"), list):
                current_sources = {}
                for source in manifest.get("sources", []):
                    if not isinstance(source, dict):
                        continue
                    identity = source.get("relative_path") or source.get("path")
                    if identity:
                        current_sources[(source.get("path_base"), str(identity))] = source
                preserved_sources = []
                for source in existing["sources"]:
                    if not isinstance(source, dict):
                        preserved_sources.append(source)
                        continue
                    identity = source.get("relative_path") or source.get("path")
                    current = current_sources.get((source.get("path_base"), str(identity)))
                    if current is None and identity:
                        current = next(
                            (
                                candidate
                                for (candidate_base, candidate_identity), candidate in current_sources.items()
                                if candidate_identity == str(identity)
                            ),
                            None,
                        )
                    merged = dict(source)
                    if current is not None:
                        for field in (
                            "modified_at",
                            "source_kind",
                            "path_base",
                            "relative_path",
                            "metadata_sha256",
                        ):
                            if field in current:
                                merged[field] = current[field]
                    preserved_sources.append(merged)
                manifest["sources"] = preserved_sources
            manifest["provenance_context_preserved"] = True
            manifest["last_invocation"] = current_parameters
    atomic_write_json(out_dir / "manifest.json", manifest, indent=2)
    print(f"[pipeline] wrote {out_dir / 'manifest.json'}", flush=True)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    global PROJECT_ROOT
    # A process may call ``main`` more than once (test runners, notebooks, or
    # embedding applications). Recompute the default before applying a new
    # explicit root so one invocation cannot leak its project context into the
    # next one.
    PROJECT_ROOT = _default_project_root()
    if args.project_root:
        candidate = Path(args.project_root).expanduser()
        PROJECT_ROOT = (candidate if candidate.is_absolute() else Path.cwd() / candidate).resolve()
    stages = selected_stages(args.stage)
    if args.bundle and "verify" not in stages:
        raise SystemExit("--bundle requires the verify stage")
    try:
        data_root = reject_symlink_root(
            project_path(args.data_root, "data"),
            label="data directory",
        )
        out_dir = reject_symlink_root(project_path(args.out_dir, "output"))
        reject_symlink_tree(data_root, label="data directory")
        reject_symlink_tree(out_dir, label="output directory")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    pbf = project_path(args.pbf, "data/raw/ireland-latest.osm.pbf")
    bundle_archive = default_bundle_archive(out_dir, args.bundle_archive)
    if args.bundle:
        try:
            bundle_archive.resolve().relative_to(out_dir.resolve())
        except ValueError:
            pass
        else:
            raise SystemExit("--bundle-archive must be outside the output directory")
    if not args.dry_run:
        data_root.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["IRELAND_GEOMETRY_PROJECT_ROOT"] = str(PROJECT_ROOT)
    env["IRELAND_GEOMETRY_SEED"] = str(args.seed)
    env["IRELAND_GEOMETRY_MC"] = str(args.mc)
    if args.no_network:
        env["IRELAND_GEOMETRY_NO_NETWORK"] = "1"
    verify_requested = "verify" in stages
    post_validation_refresh = verify_requested and "report" in stages
    preserve_manifest_context = bool(
        set(stages).issubset(DIAGNOSTIC_ONLY_STAGES)
        and (out_dir / "manifest.json").is_file()
    )
    try:
        from scripts.runtime import atomic_write_json, sha256_path
        from scripts.stage_cache import (
            CACHE_NAME,
            CACHE_VERSION,
            NON_CACHEABLE,
            fingerprint_from_payload,
            fingerprint_payload,
            input_paths,
            load_cache,
            record_output_hashes,
            reuse_diagnostics,
            save_record,
            stage_outputs,
        )
    except ImportError:
        from runtime import atomic_write_json, sha256_path
        from stage_cache import (
            CACHE_NAME,
            CACHE_VERSION,
            NON_CACHEABLE,
            fingerprint_from_payload,
            fingerprint_payload,
            input_paths,
            load_cache,
            record_output_hashes,
            reuse_diagnostics,
            save_record,
            stage_outputs,
        )
    cache_path = out_dir / CACHE_NAME
    cache = load_cache(cache_path)
    input_signature_cache: dict[str, str | None] = {}
    plan_rows: list[dict[str, object]] = []
    for stage in stages:
        if stage == "verify":
            continue
        command = build_stage_command(stage, args, data_root, out_dir, pbf)
        cache_inputs = input_paths(
            stage,
            data_root=data_root,
            out_dir=out_dir,
            pbf=pbf,
            args=args,
            schema_path=ROOT / "schemas" / "artifacts.json",
            root=PROJECT_ROOT,
        )
        signatures = {}
        for path in cache_inputs:
            key = str(path)
            if key not in input_signature_cache:
                input_signature_cache[key] = sha256_path(path)
            signatures[key] = input_signature_cache[key]
        stage_fingerprint_inputs = fingerprint_payload(
            stage,
            command=command,
            script_path=ROOT / "scripts" / SCRIPTS[stage],
            controller_path=ROOT / "run_pipeline.py",
            input_signatures=signatures,
        )
        stage_fingerprint = fingerprint_from_payload(stage_fingerprint_inputs)
        outputs = stage_outputs(stage, data_root, out_dir)
        if stage in NON_CACHEABLE:
            cache_info = {
                "cacheable": False,
                "reusable": False,
                "reason": "always_run",
                "fingerprint": stage_fingerprint,
            }
        elif args.refresh:
            cache_info = {
                "cacheable": True,
                "reusable": False,
                "reason": "refresh_requested",
                "fingerprint": stage_fingerprint,
            }
        elif not args.incremental:
            cache_info = {
                "cacheable": True,
                "reusable": False,
                "reason": "incremental_disabled",
                "fingerprint": stage_fingerprint,
            }
        else:
            cache_info = reuse_diagnostics(cache, stage, stage_fingerprint, outputs)
        reusable = bool(cache_info["reusable"])
        if args.dry_run:
            status = "cached" if reusable else "run"
            if stage in NON_CACHEABLE:
                status = "always-run"
            plan_rows.append(
                {
                    "name": stage,
                    "status": status,
                    "cacheable": stage not in NON_CACHEABLE,
                    "fingerprint": stage_fingerprint,
                    "cache": cache_info,
                    "command": command,
                    "inputs": [str(path) for path in cache_inputs],
                    "outputs": [str(path) for path in outputs],
                }
            )
            if not args.dry_run_json:
                print(f"[dry-run] {stage}: {status} ({cache_info['reason']})", flush=True)
                print(f"[dry-run]   command: {' '.join(command)}", flush=True)
            continue
        if reusable:
            print(f"\n===== {stage} (cached) =====", flush=True)
            continue
        if stage == "repro-check":
            # Reproducibility comparison consumes the manifest context. Write
            # the current pre-check manifest before the stage, then write the
            # final manifest again below after the reproducibility artifact is
            # produced.
            write_manifest(
                args,
                data_root,
                out_dir,
                pbf,
                preserve_context=preserve_manifest_context,
            )
        run_stage(stage, args, data_root, out_dir, pbf, env, command=command)
        if stage not in NON_CACHEABLE:
            # Re-read dynamic output declarations after the stage runs.  The
            # columnar stage may remove unavailable optional backends, so a
            # pre-run declaration can otherwise leave stale missing paths in
            # the cache record.
            save_record(
                cache,
                stage,
                stage_fingerprint,
                record_output_hashes(stage_outputs(stage, data_root, out_dir)),
                fingerprint_inputs=stage_fingerprint_inputs,
            )
            atomic_write_json(cache_path, cache, indent=2)
    if args.dry_run:
        post_validation_operations: list[dict[str, object]] = []
        bundle_operation: dict[str, object] | None = None
        bundle_verification_operation: dict[str, object] | None = None
        if verify_requested:
            verify_command = build_stage_command("verify", args, data_root, out_dir, pbf)
            plan_rows.append(
                {
                    "name": "verify",
                    "status": "always-run",
                    "cacheable": False,
                    "cache": {
                        "cacheable": False,
                        "reusable": False,
                        "reason": "always_run",
                    },
                    "command": verify_command,
                    "inputs": [str(data_root), str(out_dir / "manifest.json")],
                    "outputs": [str(out_dir / "verification.json")],
                }
            )
            if not args.dry_run_json:
                print("[dry-run] verify: always-run", flush=True)
                print(f"[dry-run]   command: {' '.join(verify_command)}", flush=True)
        if post_validation_refresh:
            report_command = build_stage_command("report", args, data_root, out_dir, pbf)
            post_validation_operations = [
                {
                    "name": "report",
                    "phase": "post_validation_refresh",
                    "status": "always-run",
                    "cacheable": False,
                    "cache": {
                        "cacheable": False,
                        "reusable": False,
                        "reason": "post_validation_refresh",
                    },
                    "command": report_command,
                    "inputs": [str(out_dir)],
                    "outputs": [
                        str(out_dir / "report.html"),
                        str(out_dir / "report_lazy.html"),
                        str(out_dir / "report_data.json"),
                        str(out_dir / "interpretation.json"),
                    ],
                },
                {
                    "name": "verify",
                    "phase": "post_validation_refresh",
                    "status": "always-run",
                    "cacheable": False,
                    "cache": {
                        "cacheable": False,
                        "reusable": False,
                        "reason": "post_validation_refresh",
                    },
                    "command": verify_command,
                    "inputs": [str(data_root), str(out_dir / "manifest.json")],
                    "outputs": [str(out_dir / "verification.json")],
                },
            ]
            plan_rows.extend(post_validation_operations)
            if not args.dry_run_json:
                print("[dry-run] report (post-validation refresh): always-run", flush=True)
                print(f"[dry-run]   command: {' '.join(report_command)}", flush=True)
                print("[dry-run] verify (post-validation refresh): always-run", flush=True)
                print(f"[dry-run]   command: {' '.join(verify_command)}", flush=True)
        if args.bundle:
            bundle_command = build_bundle_command(out_dir, bundle_archive)
            bundle_verify_command = build_bundle_verify_command(bundle_archive)
            bundle_operation = {
                "name": "bundle",
                "phase": "post_validation_bundle",
                "status": "always-run",
                "cacheable": False,
                "cache": {
                    "cacheable": False,
                    "reusable": False,
                    "reason": "post_validation_bundle",
                },
                "command": bundle_command,
                "inputs": [
                    str(out_dir / "manifest.json"),
                    str(out_dir / "verification.json"),
                    str(out_dir / "schema_validation.json"),
                    str(out_dir / "reproducibility.json"),
                ],
                "outputs": [str(bundle_archive)],
            }
            bundle_verification_operation = {
                "name": "bundle-verify",
                "phase": "post_validation_bundle",
                "status": "always-run",
                "cacheable": False,
                "cache": {
                    "cacheable": False,
                    "reusable": False,
                    "reason": "post_validation_bundle_verify",
                },
                "command": bundle_verify_command,
                "inputs": [str(bundle_archive)],
                "outputs": [],
            }
            plan_rows.extend([bundle_operation, bundle_verification_operation])
            if not args.dry_run_json:
                print("[dry-run] bundle: always-run", flush=True)
                print(f"[dry-run]   command: {' '.join(bundle_command)}", flush=True)
                print("[dry-run] bundle-verify: always-run", flush=True)
                print(f"[dry-run]   command: {' '.join(bundle_verify_command)}", flush=True)
        if args.dry_run_json:
            print(
                json.dumps(
                    {
                        "contract": "ireland-geometry.dry-run.v1",
                        "package_version": package_version(),
                        "dry_run": True,
                        "no_files_written": True,
                        "requested_stage": args.stage,
                        "project_root": str(PROJECT_ROOT),
                        "data_root": str(data_root),
                        "out_dir": str(out_dir),
                        "pbf": str(pbf),
                        "no_network": args.no_network,
                        "incremental": args.incremental,
                        "cache_version": CACHE_VERSION,
                        "cache_explanations": True,
                        "post_validation_refresh": {
                            "enabled": post_validation_refresh,
                            "operations": post_validation_operations,
                        },
                        "post_validation_bundle": {
                            "enabled": args.bundle,
                            "archive": str(bundle_archive) if args.bundle else None,
                            "operation": bundle_operation,
                            "verification": bundle_verification_operation,
                            "operations": (
                                [bundle_operation, bundle_verification_operation]
                                if args.bundle
                                else []
                            ),
                        },
                        "stage_count": len(plan_rows),
                        "stages": plan_rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print("[dry-run] no files written", flush=True)
        return
    write_manifest(
        args,
        data_root,
        out_dir,
        pbf,
        preserve_context=preserve_manifest_context,
    )
    if verify_requested:
        run_stage("verify", args, data_root, out_dir, pbf, env)
        write_manifest(
            args,
            data_root,
            out_dir,
            pbf,
            preserve_context=preserve_manifest_context,
        )
        if post_validation_refresh:
            # The first report is needed before verification so the verifier
            # can inspect it. Refresh it after the validation records exist so
            # a first build cannot finish with a stale not_provided gate in
            # report_data.json or interpretation.json, then verify that final
            # report pack once more before publishing the final manifest.
            run_stage("report", args, data_root, out_dir, pbf, env)
            write_manifest(
                args,
                data_root,
                out_dir,
                pbf,
                preserve_context=preserve_manifest_context,
            )
            run_stage("verify", args, data_root, out_dir, pbf, env)
        write_manifest(
            args,
            data_root,
            out_dir,
            pbf,
            preserve_context=preserve_manifest_context,
        )
        if args.bundle:
            run_bundle(out_dir, bundle_archive, env)
    print("\nDone. Open output/report.html (or the served URL) to explore.")


if __name__ == "__main__":
    main()
