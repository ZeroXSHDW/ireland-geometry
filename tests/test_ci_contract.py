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


def test_patch_hygiene_is_documented_for_contributors():
    for filename in ("README.md", "CONTRIBUTING.md"):
        content = (ROOT / filename).read_text(encoding="utf-8")
        assert "git diff --check" in content, filename
