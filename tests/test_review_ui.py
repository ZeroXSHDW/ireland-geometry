import pytest

from scripts.review import load_queue, validate_labels
from scripts.review import main as review_main
from scripts.review_ui import build_html


def test_review_html_supports_filtering_resume_and_complete_label_export():
    html = build_html(
        [
            {
                "review_rank": 1,
                "osm_id": "way/1",
                "name": "Test Chapel",
                "group": "worship",
                "score": "91",
                "flags": "angle,ratio",
                "evidence_summary": "A candidate with evidence.",
                "niah_name": "Test Chapel",
                "reg_no": "12345678",
                "county": "Cork",
                "rating": "Regional",
                "century": "19th",
                "architect": "A. Example",
                "architect_confidence": "high",
                "architect_evidence": "by A. Example",
                "reference_count": "2",
                "verified_reference_count": "1",
                "independent_reference_count": "1",
                "reference_sources": "NIAH|Archive",
                "reference_types": "inventory|catalogue",
                "reference_urls": "https://example.test/source|archive-record-1",
                "archive_refs": "ARC-123",
                "evidence_text": "A short dossier excerpt.",
                "validation_status": "manual_review_required",
                "review_warnings": "near NIAH join",
                "map_url": "https://www.openstreetmap.org/way/1",
                "label": "not_reviewed",
                "reviewer": "",
                "reviewed_at": "",
                "confidence": "",
                "evidence_source": "",
                "notes": "",
            }
        ]
    )

    assert "id=\"search\"" in html
    assert "id=\"statusFilter\"" in html
    assert "restoreViewState" in html
    assert "syncViewState" in html
    assert "params.set('status'" in html
    assert "history.replaceState" in html
    assert 'aria-label="Search review queue"' in html
    assert 'aria-label="Review label for' in html
    assert 'aria-live="polite"' in html
    assert "localStorage" in html
    assert "Clear saved edits" in html
    assert "href=\"report.html\"" in html
    assert "TARGET_ID" in html
    assert "targetNotice" in html
    assert "not in the current" in html
    assert "target-row" in html
    assert "data-edit=\"evidence_source\"" in html
    assert "Evidence detail" in html
    assert "Source links" in html
    assert "https://example.test/source" in html
    assert "reviewed_at" in html
    assert "expert-labels.csv" in html
    assert "expert-labels.json" in html
    assert "function downloadJson" in html
    assert "async function importJson" in html
    assert 'aria-label="Import review labels JSON"' in html
    assert "Unsupported JSON backup schema" in html
    assert "different review queue" in html
    assert "invalid label records" in html
    assert "Test Chapel" in html
    assert "__QUEUE_KEY__" not in html


def test_load_queue_carries_dossier_evidence_into_review_rows():
    queue = load_queue(
        [
            {
                "osm_id": "way/2",
                "score": "88",
                "niah_name": "Manor Mill",
                "reg_no": "20818055",
                "architect": "A. Architect",
                "reference_urls": "https://example.test/niah",
                "review_warnings": "near NIAH join",
            }
        ],
        [],
        1,
    )

    assert queue[0]["niah_name"] == "Manor Mill"
    assert queue[0]["reg_no"] == "20818055"
    assert queue[0]["architect"] == "A. Architect"
    assert queue[0]["reference_urls"] == "https://example.test/niah"
    assert queue[0]["review_warnings"] == "near NIAH join"


def test_review_label_csv_validation_normalizes_and_rejects_corruption():
    normalized = validate_labels(
        [{"osm_id": " way/1 ", "label": " supportive ", "notes": "keep"}]
    )
    assert normalized[0]["osm_id"] == "way/1"
    assert normalized[0]["label"] == "supportive"

    with pytest.raises(ValueError, match="duplicates osm_id"):
        validate_labels(
            [
                {"osm_id": "way/1", "label": "supportive"},
                {"osm_id": "way/1", "label": "ambiguous"},
            ]
        )
    with pytest.raises(ValueError, match="invalid label"):
        validate_labels([{"osm_id": "way/1", "label": "maybe"}])
    with pytest.raises(ValueError, match="blank osm_id"):
        validate_labels([{"osm_id": "", "label": "supportive"}])
    with pytest.raises(ValueError, match="missing required column"):
        validate_labels([{"osm_id": "way/1"}])


def test_load_queue_rejects_invalid_review_labels():
    with pytest.raises(ValueError, match="invalid review labels"):
        load_queue(
            [{"osm_id": "way/1", "score": "90"}],
            [{"osm_id": "way/1", "label": "unsupported"}],
            1,
        )


def test_review_cli_reports_labels_outside_current_queue(tmp_path, capsys):
    output = tmp_path / "output"
    data = tmp_path / "data"
    output.mkdir()
    data.mkdir()
    (output / "candidate_dossiers.csv").write_text(
        "osm_id,score,name\nway/1,90,Queued Chapel\nway/2,80,Other Chapel\n",
        encoding="utf-8",
    )
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "osm_id,label\nway/1,supportive\nway/9,ambiguous\n",
        encoding="utf-8",
    )

    review_main(
        [
            "--data-root",
            str(data),
            "--out-dir",
            str(output),
            "--labels",
            str(labels),
            "--top-n",
            "1",
        ]
    )

    assert "ignored 1 labels outside the current queue" in capsys.readouterr().err
    assert "way/1,supportive" in (output / "review_queue.csv").read_text(encoding="utf-8")


def test_review_cli_rejects_nonpositive_queue_size(capsys):
    with pytest.raises(SystemExit):
        review_main(["--top-n", "0"])
    assert "--top-n must be positive" in capsys.readouterr().err
