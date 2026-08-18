import json
from pathlib import Path

from scripts.analyze import (
    DEFAULT_SCORING_CONFIG,
    SCORING_CONTRACT,
    _scoring_config_hash,
    load_scoring_config,
    scoring_config_artifact,
)

ROOT = Path(__file__).resolve().parents[1]


def test_project_and_packaged_plans_publish_the_same_scoring_contract():
    project = json.loads((ROOT / "analysis_plan.json").read_text(encoding="utf-8"))
    packaged = json.loads((ROOT / "schemas" / "analysis_plan.json").read_text(encoding="utf-8"))
    assert project["scoring"] == packaged["scoring"]
    assert project["scoring"]["contract"] == SCORING_CONTRACT
    assert project["scoring"]["screening_threshold"] == 55.0


def test_scoring_config_artifact_records_hash_and_plan_hash(tmp_path):
    plan = tmp_path / "analysis_plan.json"
    plan.write_text((ROOT / "analysis_plan.json").read_text(encoding="utf-8"), encoding="utf-8")
    config = load_scoring_config(plan)
    artifact = scoring_config_artifact(plan, config)
    assert artifact["contract"] == SCORING_CONTRACT
    assert artifact["config_sha256"] == _scoring_config_hash(config)
    assert artifact["plan_sha256"]
    assert artifact["config"]["label"].endswith("not an inferential statistic.")


def test_legacy_plan_keeps_the_previous_scoring_defaults(tmp_path):
    plan = tmp_path / "legacy-plan.json"
    plan.write_text('{"plan_id":"legacy","version":1}', encoding="utf-8")
    assert load_scoring_config(plan) == DEFAULT_SCORING_CONFIG


def test_scoring_rule_changes_change_the_scoring_hash(tmp_path):
    plan = tmp_path / "analysis_plan.json"
    payload = json.loads((ROOT / "analysis_plan.json").read_text(encoding="utf-8"))
    plan.write_text(json.dumps(payload), encoding="utf-8")
    first = load_scoring_config(plan)
    payload["scoring"]["weights"]["golden_ratio"] = 26.0
    plan.write_text(json.dumps(payload), encoding="utf-8")
    second = load_scoring_config(plan)
    assert _scoring_config_hash(first) != _scoring_config_hash(second)
