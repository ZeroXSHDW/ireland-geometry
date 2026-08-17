from __future__ import annotations

import math

from scripts.fetch_geofabrik import geometry_from_rings
from scripts.geometry import (
    convexity_ratio,
    count_holes,
    geometry_from_element,
    geometry_quality,
    interior_angles,
    iter_polygons,
    repair_geometry,
    shape_descriptors,
    to_local_meters,
)


def point(lat: float, lon: float) -> dict[str, float]:
    return {"lat": lat, "lon": lon}


def test_rectangle_and_projection_are_in_metres():
    element = {
        "geometry": {
            "exterior": [
                point(53.0, -8.0),
                point(53.0, -7.999),
                point(53.001, -7.999),
                point(53.001, -8.0),
            ]
        }
    }
    geom = geometry_from_element(element)
    assert geom is not None
    local = to_local_meters(geom, geom.centroid.y, geom.centroid.x)
    assert 7000 < local.area < 9000
    assert math.isclose(convexity_ratio(local), 1.0, abs_tol=1e-9)


def test_concavity_and_holes_are_preserved():
    element = {
        "geometry": {
            "exterior": [
                point(53.0, -8.0),
                point(53.0, -7.99),
                point(53.004, -7.99),
                point(53.004, -7.996),
                point(53.002, -7.996),
                point(53.002, -8.0),
            ],
            "holes": [
                [
                    point(53.0005, -7.999),
                    point(53.0005, -7.998),
                    point(53.0015, -7.998),
                    point(53.0015, -7.999),
                ]
            ],
        }
    }
    geom = geometry_from_element(element)
    assert geom is not None
    assert count_holes(geom) == 1
    assert convexity_ratio(geom) < 1.0
    assert len(interior_angles(geom)) >= 6


def test_multipolygon_components_are_not_dropped():
    element = {
        "geometry": {
            "exteriors": [
                [
                    point(53.0, -8.0),
                    point(53.0, -7.999),
                    point(53.001, -7.999),
                    point(53.001, -8.0),
                ],
                [
                    point(53.0, -7.99),
                    point(53.0, -7.989),
                    point(53.001, -7.989),
                    point(53.001, -7.99),
                ],
            ],
            "holes_by_exterior": [[], []],
        }
    }
    geom = geometry_from_element(element)
    assert geom is not None
    assert len(list(iter_polygons(geom))) == 2
    assert geometry_quality(geom)["multipart"] == 1


def test_invalid_geometry_is_repaired_and_recorded():
    element = {
        "geometry": {
            "exterior": [
                point(53.0, -8.0),
                point(53.002, -7.998),
                point(53.002, -8.0),
                point(53.0, -7.998),
            ]
        }
    }
    geom = geometry_from_element(element)
    repaired, was_repaired, warning = repair_geometry(geom)
    assert repaired is not None
    assert was_repaired
    assert warning == "buffer0"
    assert geometry_quality(repaired, repaired=True, warning=warning)["valid"] == 1


def test_filtered_outer_ring_keeps_its_holes_attached():
    invalid_outer = [point(53.0, -8.0), point(53.0, -8.0), point(53.0, -8.0)]
    valid_outer = [
        point(53.0, -7.99),
        point(53.0, -7.98),
        point(53.01, -7.98),
        point(53.01, -7.99),
    ]
    hole = [
        point(53.002, -7.988),
        point(53.002, -7.985),
        point(53.005, -7.985),
        point(53.005, -7.988),
    ]
    result = geometry_from_rings([(invalid_outer, [hole]), (valid_outer, [hole])])
    assert result is not None
    assert result["exterior"] == valid_outer
    assert result["holes"] == [hole]


def test_shape_descriptors_are_finite_and_scale_normalized():
    element = {
        "geometry": {
            "exterior": [
                point(53.0, -8.0),
                point(53.0, -7.999),
                point(53.001, -7.999),
                point(53.001, -8.0),
            ]
        }
    }
    geom = geometry_from_element(element)
    local = to_local_meters(geom, geom.centroid.y, geom.centroid.x)
    descriptors = shape_descriptors(local)
    assert 0 < descriptors["rectangularity"] <= 1
    assert 0 <= descriptors["angle_entropy"] <= 1
    assert all(math.isfinite(descriptors[f"fourier_{i}"]) for i in range(1, 5))
