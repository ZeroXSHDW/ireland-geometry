from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from run_pipeline import selected_stages

ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_help_exits_without_running_work():
    result = subprocess.run(
        [sys.executable, str(ROOT / "run_pipeline.py"), "--help"],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--no-network" in result.stdout
    assert "fetch-niah" in result.stdout
    assert "verify" in result.stdout
    assert "===== " not in result.stdout


def test_verify_must_be_last_stage():
    assert selected_stages("analyze,verify") == ["analyze", "verify"]
    assert selected_stages("analyze,sensitivity,building-parts,verify") == [
        "analyze",
        "sensitivity",
        "building-parts",
        "verify",
    ]
    with pytest.raises(SystemExit, match="must be last"):
        selected_stages("verify,analyze")
