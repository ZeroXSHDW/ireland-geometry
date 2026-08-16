from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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
    assert "===== " not in result.stdout
