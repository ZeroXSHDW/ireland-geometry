from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_workflow_checkout_enforces_patch_hygiene():
    workflow_paths = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert workflow_paths

    for workflow_path in workflow_paths:
        lines = workflow_path.read_text(encoding="utf-8").splitlines()
        checkout_indexes = [
            index
            for index, line in enumerate(lines)
            if "actions/checkout@" in line
        ]
        hygiene_indexes = [
            index for index, line in enumerate(lines) if "git diff --check" in line
        ]
        assert checkout_indexes, workflow_path
        assert len(hygiene_indexes) == len(checkout_indexes), workflow_path

        for position, checkout_index in enumerate(checkout_indexes):
            next_checkout = (
                checkout_indexes[position + 1]
                if position + 1 < len(checkout_indexes)
                else len(lines)
            )
            assert any(
                checkout_index < hygiene_index < next_checkout
                for hygiene_index in hygiene_indexes
            ), workflow_path


def test_workflows_use_fixed_runners_and_bounded_python_bootstraps():
    workflow_paths = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert workflow_paths
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.11"
    assert 'requires = ["setuptools==83.0.0", "wheel==0.46.2"]' in pyproject

    for workflow_path in workflow_paths:
        workflow = workflow_path.read_text(encoding="utf-8")
        assert "ubuntu-latest" not in workflow, workflow_path
        assert "pip install --upgrade pip" not in workflow, workflow_path
        assert "pip install --upgrade pip setuptools wheel" not in workflow, workflow_path
        assert "setuptools==80.9.0" not in workflow, workflow_path
        assert "wheel==0.45.1" not in workflow, workflow_path

    assert "python-version-file: .python-version" in (
        ROOT / ".github/workflows/ci.yml"
    ).read_text(encoding="utf-8")
    assert "python-version-file: .python-version" in (
        ROOT / ".github/workflows/pages.yml"
    ).read_text(encoding="utf-8")


def test_patch_hygiene_is_documented_for_contributors():
    for filename in ("README.md", "CONTRIBUTING.md"):
        content = (ROOT / filename).read_text(encoding="utf-8")
        assert "git diff --check" in content, filename
