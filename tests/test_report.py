import csv
import json

from scripts.report import build_report


def write_csv(path, fieldnames, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_report_is_data_driven_and_replaces_template_tokens(tmp_path):
    analysis_fields = [
        "osm_id",
        "name",
        "group",
        "subtype",
        "lat",
        "lon",
        "score",
        "area_m2",
        "perimeter_m",
        "length_m",
        "width_m",
        "aspect_ratio",
        "n_vertices",
        "convexity",
        "circularity",
        "has_golden_angle",
        "golden_ratio_err_pct",
        "fib_ratio_err_pct",
        "valid",
        "repaired",
        "multipart",
        "hole_count",
        "geometry_warning",
        "flags",
    ]
    write_csv(
        tmp_path / "analysis_results.csv",
        analysis_fields,
        [
            {
                "osm_id": "way/1",
                "name": "Test Chapel",
                "group": "worship",
                "subtype": "church",
                "lat": 53.0,
                "lon": -8.0,
                "score": 72,
                "area_m2": 100,
                "perimeter_m": 40,
                "length_m": 12,
                "width_m": 8,
                "aspect_ratio": 1.5,
                "n_vertices": 4,
                "convexity": 1,
                "circularity": 0.78,
                "has_golden_angle": 0,
                "golden_ratio_err_pct": 7,
                "fib_ratio_err_pct": 4,
                "valid": 1,
                "repaired": 0,
                "multipart": 0,
                "hole_count": 0,
                "geometry_warning": "",
                "flags": "orthogonal",
            }
        ],
    )
    (tmp_path / "ireland_buildings.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"osm_id": "way/1"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [-8.0, 53.0],
                                    [-7.999, 53.0],
                                    [-7.999, 53.001],
                                    [-8.0, 53.001],
                                    [-8.0, 53.0],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(json.dumps({"generated_at": "test"}), encoding="utf-8")

    html = build_report(tmp_path)

    assert "__DATA__" not in html
    assert "__MARKER_LIMIT__" not in html
    assert "filtered.slice(0,800)" in html
    assert "Test Chapel" in html
    assert "const PACK =" in html
    assert "const MATCHED =" in html
