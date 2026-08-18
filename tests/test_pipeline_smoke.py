"""Offline subprocess coverage for the smallest meaningful pipeline build."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

osmium = pytest.importorskip("osmium")
Node = osmium.osm.mutable.Node
Way = osmium.osm.mutable.Way


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "pipeline_smoke"
SMOKE_STAGES = (
    "analyze,negative-controls,niah,architects,sensitivity,spatial-covariates,"
    "osm-history,validation,building-parts,historical,review,point-pattern,"
    "spatial-stats,spatial-bootstrap,roads,road-proximity,road-routing,"
    "quality-audit,holdout,columnar,schema-audit,report,repro-check,verify"
)


def write_smoke_pbf(path: Path) -> None:
    """Write a tiny valid PBF so road stages exercise their real reader."""
    with osmium.SimpleWriter(str(path), overwrite=True) as writer:
        writer.add_node(Node(id=9001, location=(-8.08, 52.99)))
        writer.add_node(Node(id=9002, location=(-8.01, 53.04)))
        writer.add_node(Node(id=9003, location=(-8.08, 53.04)))
        writer.add_way(Way(id=9900, nodes=[9001, 9002], tags={"highway": "residential"}))
        writer.add_way(Way(id=9901, nodes=[9001, 9003], tags={"highway": "service"}))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_offline_pipeline_smoke_produces_verified_contracts(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    plan = project / "analysis_plan.json"
    shutil.copy2(ROOT / "analysis_plan.json", plan)
    pbf = project / "raw" / "smoke.osm.pbf"
    write_smoke_pbf(pbf)
    output = project / "output"
    command = [
        sys.executable,
        str(ROOT / "run_pipeline.py"),
        "--project-root",
        str(project),
        "--data-root",
        str(project),
        "--out-dir",
        str(output),
        "--pbf",
        str(pbf),
        "--analysis-plan",
        str(plan),
        "--stage",
        SMOKE_STAGES,
        "--no-network",
        "--incremental",
        "--mc",
        "2",
        "--bootstrap-iterations",
        "2",
        "--routing-max-pairs",
        "0",
        "--holdout-fraction",
        "0.5",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(f"smoke pipeline failed:\n{result.stdout}\n{result.stderr}")

    manifest = read_json(output / "manifest.json")
    schema = read_json(output / "schema_validation.json")
    report_data = read_json(output / "report_data.json")
    verification = read_json(output / "verification.json")
    scoring = read_json(output / "scoring_config.json")
    assert manifest["counts"]["analysis_results.csv"] == 31
    assert schema["passed"] is True
    assert report_data["summary"]["targets"] == 11
    assert len(report_data["targets"]) == 11
    assert scoring["contract"] == "ireland-geometry.exploratory-score.v1"
    assert report_data["scoring"]["config_sha256"] == scoring["config_sha256"]
    assert verification["passed"] is True
    assert verification["errors"] == []
    lazy_html = (output / "report_lazy.html").read_text(encoding="utf-8")
    assert "/api/report/page" in lazy_html
    assert "offline=1" in lazy_html
