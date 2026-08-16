from scripts.runtime import output_counts


def test_manifest_counts_csv_records_not_physical_lines(tmp_path):
    (tmp_path / "architects_evidence.csv").write_text(
        'id,evidence\n1,"first line\nsecond line"\n2,"single line"\n',
        encoding="utf-8",
    )
    counts = output_counts(tmp_path)
    assert counts["architects_evidence.csv"] == 2
