#!/usr/bin/env python3
"""Create an expert-review queue and calibration artifacts.

The queue is a lightweight, static annotation workflow: reviewers can label
candidate rows in the browser and download a CSV, which can then be supplied
back to this stage with ``--labels``.  Calibration metrics are emitted only
for explicit supportive/not-supportive labels; ambiguous records remain
visible but are not silently scored as either class.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

try:
    from review_ui import build_html as build_review_html
    from runtime import (
        atomic_write_csv,
        atomic_write_text,
        project_data_tree_path,
        project_input_path,
        project_output_tree_path,
    )
except ImportError:
    from scripts.review_ui import build_html as build_review_html
    from scripts.runtime import (
        atomic_write_csv,
        atomic_write_text,
        project_data_tree_path,
        project_input_path,
        project_output_tree_path,
    )


LABELS = {"supportive", "ambiguous", "not_supportive", "not_reviewed"}

# Keep the evidence contract in the review queue so the browser workspace can
# inspect the same dossier fields that drive candidate prioritisation.
DOSSIER_EVIDENCE_FIELDS = (
    "geometry_quality",
    "reg_no",
    "niah_name",
    "source_region",
    "county",
    "rating",
    "niah_type",
    "date_mid",
    "century",
    "match_mode",
    "dist_m",
    "architect",
    "architect_confidence",
    "architect_evidence",
    "reference_count",
    "reference_sources",
    "reference_types",
    "reference_urls",
    "archive_refs",
    "verified_reference_count",
    "independent_reference_count",
    "evidence_text",
    "image_paths",
    "plan_paths",
    "validation_status",
    "review_warnings",
    "osm_niah_ref",
    "osm_heritage",
    "wikidata",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def validate_labels(labels: list[dict[str, str]]) -> list[dict[str, str]]:
    """Validate and normalize labels supplied to the review pipeline.

    Labels outside the current candidate queue remain valid input and are
    reported by ``main`` as ignored. Duplicate IDs, missing identifiers,
    missing label columns, and unknown label values are rejected rather than
    silently allowing the last row to win.
    """
    errors: list[str] = []
    seen: set[str] = set()
    normalized: list[dict[str, str]] = []
    for row_number, row in enumerate(labels, 2):
        missing = [field for field in ("osm_id", "label") if field not in row]
        if missing:
            errors.append(f"row {row_number} is missing required column(s): {', '.join(missing)}")
            continue
        osm_id = str(row.get("osm_id") or "").strip()
        label = str(row.get("label") or "").strip()
        if not osm_id:
            errors.append(f"row {row_number} has a blank osm_id")
            continue
        if osm_id in seen:
            errors.append(f"row {row_number} duplicates osm_id {osm_id}")
            continue
        if label not in LABELS:
            errors.append(
                f"row {row_number} has invalid label {label!r}; "
                f"choose from {', '.join(sorted(LABELS))}"
            )
            continue
        seen.add(osm_id)
        normalized.append({**row, "osm_id": osm_id, "label": label})
    if errors:
        preview = "; ".join(errors[:5])
        suffix = f"; and {len(errors) - 5} more" if len(errors) > 5 else ""
        raise ValueError(f"invalid review labels: {preview}{suffix}")
    return normalized


def number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_queue(dossiers: list[dict[str, str]], labels: list[dict[str, str]], top_n: int) -> list[dict[str, str]]:
    labels_by_id = {row["osm_id"]: row for row in validate_labels(labels)}
    ordered = sorted(dossiers, key=lambda row: (-number(row.get("score")), row.get("osm_id", "")))[:top_n]
    output = []
    for rank, row in enumerate(ordered, 1):
        label = labels_by_id.get(row.get("osm_id", ""), {})
        candidate = {
            "review_rank": rank,
            "osm_id": row.get("osm_id", ""),
            "name": row.get("name", ""),
            "group": row.get("group", ""),
            "score": row.get("score", ""),
            "flags": row.get("flags", ""),
            "validation_status": row.get("validation_status", ""),
            "evidence_summary": row.get("evidence_summary", ""),
            "review_priority": row.get("review_priority", ""),
            "map_url": f"https://www.openstreetmap.org/{row.get('osm_id', '')}",
            "label": label.get("label", "not_reviewed") or "not_reviewed",
            "reviewer": label.get("reviewer", ""),
            "reviewed_at": label.get("reviewed_at", ""),
            "confidence": label.get("confidence", ""),
            "evidence_source": label.get("evidence_source", ""),
            "notes": label.get("notes", ""),
        }
        candidate.update({field: row.get(field, "") for field in DOSSIER_EVIDENCE_FIELDS})
        if candidate["label"] not in LABELS:
            candidate["label"] = "not_reviewed"
        output.append(candidate)
    return output


def calibration(queue: list[dict[str, str]]) -> list[dict[str, object]]:
    labelled = [row for row in queue if row.get("label") in {"supportive", "not_supportive"}]
    ambiguous = sum(row.get("label") == "ambiguous" for row in queue)
    rows = []
    for threshold in (0.5, 0.6, 0.7, 0.8, 0.9):
        outcomes = []
        for row in labelled:
            actual = int(row["label"] == "supportive")
            probability = min(max(number(row.get("score")) / 100.0, 0.0), 1.0)
            predicted = int(probability >= threshold)
            outcomes.append((actual, predicted, probability))
        tp = sum(actual == predicted == 1 for actual, predicted, _ in outcomes)
        fp = sum(actual == 0 and predicted == 1 for actual, predicted, _ in outcomes)
        fn = sum(actual == 1 and predicted == 0 for actual, predicted, _ in outcomes)
        tn = sum(actual == predicted == 0 for actual, predicted, _ in outcomes)
        precision = tp / (tp + fp) if tp + fp else ""
        recall = tp / (tp + fn) if tp + fn else ""
        f1 = 2 * precision * recall / (precision + recall) if isinstance(precision, float) and precision + recall else ""
        brier = sum((probability - actual) ** 2 for actual, _, probability in outcomes) / len(outcomes) if outcomes else ""
        rows.append(
            {
                "threshold": threshold,
                "labelled_n": len(labelled),
                "ambiguous_n": ambiguous,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": round(precision, 6) if isinstance(precision, float) else precision,
                "recall": round(recall, 6) if isinstance(recall, float) else recall,
                "f1": round(f1, 6) if isinstance(f1, float) else f1,
                "brier": round(brier, 6) if isinstance(brier, float) else brier,
                "status": "provided" if labelled else "not_provided",
                "method": "score/100 heuristic calibrated against explicit expert labels",
            }
        )
    return rows


def confusion(queue: list[dict[str, str]]) -> list[dict[str, object]]:
    labelled = [row for row in queue if row.get("label") in {"supportive", "not_supportive"}]
    counts = Counter(
        ("supportive" if number(row.get("score")) >= 70 else "not_supportive", row["label"])
        for row in labelled
    )
    rows = []
    for predicted in ("supportive", "not_supportive"):
        for actual in ("supportive", "not_supportive"):
            rows.append(
                {
                    "predicted": predicted,
                    "actual": actual,
                    "count": counts.get((predicted, actual), 0),
                    "labelled_n": len(labelled),
                    "status": "provided" if labelled else "not_provided",
                    "threshold": 70,
                }
            )
    return rows


def build_html(queue: list[dict[str, str]]) -> str:
    """Build the canonical review page from the shared review UI module."""
    return build_review_html(queue)



def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=None, help="data directory; defaults to project data/")
    parser.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    parser.add_argument("--labels", default=None, help="optional expert labels CSV")
    parser.add_argument("--top-n", type=int, default=1000)
    args = parser.parse_args(argv)
    if args.top_n < 1:
        parser.error("--top-n must be positive")
    data = project_data_tree_path(args.data_root)
    out = project_output_tree_path(args.out_dir)
    dossiers = read_csv(out / "candidate_dossiers.csv") or read_csv(out / "historical_validation.csv")
    label_path = (
        project_input_path(
            args.labels,
            str(data / "review" / "labels.csv"),
            label="review labels",
        )
        if args.labels
        else data / "review" / "labels.csv"
    )
    labels = read_csv(label_path if label_path.exists() else Path("/does/not/exist"))
    try:
        queue = load_queue(dossiers, labels, args.top_n)
    except ValueError as exc:
        parser.error(str(exc))
    label_ids = {
        str(row.get("osm_id") or "").strip()
        for row in labels
        if str(row.get("osm_id") or "").strip()
    }
    queue_ids = {row["osm_id"] for row in queue}
    ignored_labels = len(label_ids.difference(queue_ids))
    if ignored_labels:
        print(
            f"[review] ignored {ignored_labels:,} labels outside the current queue",
            file=sys.stderr,
        )
    fields = list(queue[0]) if queue else ["review_rank", "osm_id", "label"]
    atomic_write_csv(out / "review_queue.csv", fields, queue)
    atomic_write_csv(
        out / "review_calibration.csv",
        [
            "threshold", "labelled_n", "ambiguous_n", "tp", "fp", "fn", "tn", "precision",
            "recall", "f1", "brier", "status", "method",
        ],
        calibration(queue),
    )
    atomic_write_csv(
        out / "review_confusion.csv",
        ["predicted", "actual", "count", "labelled_n", "status", "threshold"],
        confusion(queue),
    )
    atomic_write_text(out / "review.html", build_html(queue))
    labelled_n = sum(row.get("label") in {"supportive", "not_supportive"} for row in queue)
    print(f"[review] wrote {len(queue):,} queue rows and {labelled_n:,} explicit labels")


if __name__ == "__main__":
    main()
