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
import json
from collections import Counter
from pathlib import Path

try:
    from runtime import atomic_write_csv, atomic_write_text, project_path
except ImportError:
    from scripts.runtime import atomic_write_csv, atomic_write_text, project_path


LABELS = {"supportive", "ambiguous", "not_supportive", "not_reviewed"}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_queue(dossiers: list[dict[str, str]], labels: list[dict[str, str]], top_n: int) -> list[dict[str, str]]:
    labels_by_id = {row.get("osm_id", ""): row for row in labels if row.get("osm_id")}
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
    data = json.dumps(queue, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    template = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ireland geometry expert review</title>
<style>body{font:14px/1.4 system-ui,sans-serif;color:#17202a;margin:24px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d9dee5;padding:7px;vertical-align:top;text-align:left}th{background:#f4f6f8;position:sticky;top:0}select,textarea,button{font:inherit;padding:5px}textarea{min-width:220px;min-height:42px}.muted{color:#667085}.toolbar{display:flex;gap:10px;align-items:center;margin:12px 0}a{color:#2563eb}</style></head>
<body><h1>Expert validation queue</h1><p class="muted">Labels are local review annotations, not automatic truth. Download the CSV, then rerun the review stage with it.</p>
<div class="toolbar"><button id="download">Download labels CSV</button><span id="count"></span></div>
<table><thead><tr><th>#</th><th>Candidate</th><th>Evidence</th><th>Label</th><th>Confidence</th><th>Source / notes</th></tr></thead><tbody id="rows"></tbody></table>
<script>const DATA=__DATA__;const esc=v=>String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
const choices=['supportive','ambiguous','not_supportive','not_reviewed'];
function render(){document.getElementById('count').textContent=`${DATA.length} candidates`;document.getElementById('rows').innerHTML=DATA.map((r,i)=>`<tr><td>${r.review_rank}</td><td><b>${esc(r.name||'Unnamed')}</b><br><span class="muted">${esc(r.osm_id)} · score ${esc(r.score)}</span><br><a href="${esc(r.map_url)}" target="_blank" rel="noopener">Open map</a></td><td>${esc(r.evidence_summary)}<br><span class="muted">${esc(r.flags)}</span></td><td><select data-i="${i}">${choices.map(c=>`<option ${r.label===c?'selected':''}>${c}</option>`).join('')}</select></td><td><input data-confidence="${i}" value="${esc(r.confidence)}" placeholder="0–1"></td><td><input data-reviewer="${i}" value="${esc(r.reviewer)}" placeholder="reviewer"><br><textarea data-notes="${i}" placeholder="evidence notes">${esc(r.notes)}</textarea></td></tr>`).join('');}
function sync(){document.querySelectorAll('select[data-i]').forEach(e=>DATA[e.dataset.i].label=e.value);document.querySelectorAll('[data-confidence]').forEach(e=>DATA[e.dataset.confidence].confidence=e.value);document.querySelectorAll('[data-reviewer]').forEach(e=>DATA[e.dataset.reviewer].reviewer=e.value);document.querySelectorAll('[data-notes]').forEach(e=>DATA[e.dataset.notes].notes=e.value);}
document.getElementById('download').onclick=()=>{sync();const cols=['osm_id','label','reviewer','reviewed_at','confidence','evidence_source','notes'];const q=v=>`"${String(v??'').replaceAll('"','""')}"`;const csv=[cols.join(','),...DATA.map(r=>cols.map(c=>q(r[c])).join(','))].join('\\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));a.download='expert-labels.csv';a.click();};render();</script></body></html>"""
    return template.replace("__DATA__", data)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=None, help="data directory; defaults to project data/")
    parser.add_argument("--out-dir", default=None, help="output directory; defaults to project output/")
    parser.add_argument("--labels", default=None, help="optional expert labels CSV")
    parser.add_argument("--top-n", type=int, default=1000)
    args = parser.parse_args(argv)
    data = project_path(args.data_root, "data")
    out = project_path(args.out_dir, "output")
    dossiers = read_csv(out / "candidate_dossiers.csv") or read_csv(out / "historical_validation.csv")
    label_path = project_path(args.labels, str(data / "review" / "labels.csv")) if args.labels else data / "review" / "labels.csv"
    labels = read_csv(label_path if label_path.exists() else Path("/does/not/exist"))
    queue = load_queue(dossiers, labels, max(1, args.top_n))
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
