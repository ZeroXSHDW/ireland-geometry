"""Canonical geometry parsing and metric helpers.

OSM coordinates arrive in WGS84 longitude/latitude. Analysis uses a local
equirectangular metre projection around each geometry's centroid. The parser
accepts the original single-exterior cache format and the lossless
multi-exterior format emitted by the current Geofabrik extractor.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

from shapely import affinity
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, shape
from shapely.ops import unary_union


def _ring(points) -> list[tuple[float, float]]:
    coords: list[tuple[float, float]] = []
    for point in points or []:
        if isinstance(point, dict):
            if "lon" not in point or "lat" not in point:
                continue
            coords.append((float(point["lon"]), float(point["lat"])))
        else:
            try:
                coords.append((float(point[0]), float(point[1])))
            except (IndexError, TypeError, ValueError):
                continue
    if len(coords) >= 3 and coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def _make_polygon(exterior, holes=()) -> Polygon | None:
    ext = _ring(exterior)
    if len(ext) < 4:
        return None
    valid_holes = []
    shell = Polygon(ext)
    for raw_hole in holes or ():
        hole = _ring(raw_hole)
        if len(hole) < 4:
            continue
        try:
            candidate = Polygon(hole)
            if shell.covers(candidate.representative_point()):
                valid_holes.append(hole)
        except Exception:  # noqa: BLE001,S112 - malformed external rings are recoverable
            continue
    try:
        return Polygon(ext, valid_holes)
    except Exception:  # noqa: BLE001 - Shapely may raise different GEOS errors by version
        try:
            return Polygon(ext)
        except Exception:  # noqa: BLE001 - return an explicit missing geometry
            return None


def geometry_from_osm_geometry(raw) -> Polygon | MultiPolygon | None:
    if isinstance(raw, list):
        return _make_polygon(raw)
    if not isinstance(raw, dict):
        return None
    if raw.get("type") in {"Polygon", "MultiPolygon", "GeometryCollection"}:
        try:
            return shape(raw)
        except Exception:  # noqa: BLE001 - malformed GeoJSON is reported as missing
            return None

    exteriors = raw.get("exteriors") or raw.get("outer_rings")
    if exteriors is None:
        exterior = raw.get("exterior")
        exteriors = [exterior] if exterior is not None else []
    if not exteriors:
        return None

    holes_by_exterior = raw.get("holes_by_exterior")
    if holes_by_exterior is None:
        holes_by_exterior = [raw.get("holes", [])] + [[] for _ in exteriors[1:]]
    polygons = []
    for index, exterior in enumerate(exteriors):
        holes = holes_by_exterior[index] if index < len(holes_by_exterior) else []
        polygon = _make_polygon(exterior, holes)
        if polygon is not None and not polygon.is_empty:
            polygons.append(polygon)
    if not polygons:
        return None
    if len(polygons) == 1:
        return polygons[0]
    try:
        merged = unary_union(polygons)
        return merged if not merged.is_empty else MultiPolygon(polygons)
    except Exception:  # noqa: BLE001 - preserve a usable multipart fallback
        return MultiPolygon(polygons)


def geometry_from_element(element: dict):
    return geometry_from_osm_geometry(element.get("geometry"))


def geometry_from_geojson(raw):
    try:
        geom = shape(raw) if raw else None
    except Exception:  # noqa: BLE001 - malformed GeoJSON is reported as missing
        return None
    return geom


# Backwards-compatible public name used by the existing scripts.
polygon_from_element = geometry_from_element


def iter_polygons(geom) -> Iterator[Polygon]:
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        yield geom
    elif isinstance(geom, (MultiPolygon, GeometryCollection)):
        for part in geom.geoms:
            yield from iter_polygons(part)


def repair_geometry(geom):
    """Return (geometry, repaired, warning); never hides repair state."""
    if geom is None:
        return None, False, "missing"
    if geom.is_empty:
        return geom, False, "empty"
    if geom.is_valid:
        return geom, False, ""
    try:
        repaired = geom.buffer(0)
    except Exception as exc:  # noqa: BLE001 - geometry repair must never abort a batch
        return None, False, f"repair_failed:{type(exc).__name__}"
    if repaired.is_empty or not repaired.is_valid:
        return None, True, "repair_invalid"
    return repaired, True, "buffer0"


def count_holes(geom) -> int:
    return sum(len(poly.interiors) for poly in iter_polygons(geom))


def geometry_quality(geom, *, repaired: bool = False, warning: str = "") -> dict:
    parts = list(iter_polygons(geom)) if geom is not None else []
    valid = int(geom is not None and not geom.is_empty and geom.is_valid and bool(parts))
    return {
        "valid": valid,
        "repaired": int(repaired),
        "degenerate": int(not parts or all(p.area <= 0 for p in parts)),
        "multipart": int(len(parts) > 1),
        "hole_count": count_holes(geom) if parts else 0,
        "geometry_warning": warning or "",
    }


def to_local_meters(geom, lat0: float, lon0: float):
    """Project WGS84 geometry to local metre coordinates."""
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110540.0
    return affinity.affine_transform(geom, [kx, 0, 0, ky, -kx * lon0, -ky * lat0])


def interior_angles(geom) -> list[float]:
    angles: list[float] = []
    for polygon in iter_polygons(geom):
        rings = [polygon.exterior, *polygon.interiors]
        for ring in rings:
            points = list(ring.coords)
            if points and points[0] == points[-1]:
                points = points[:-1]
            n = len(points)
            for i in range(n):
                a = points[(i - 1) % n]
                b = points[i]
                c = points[(i + 1) % n]
                v1 = (a[0] - b[0], a[1] - b[1])
                v2 = (c[0] - b[0], c[1] - b[1])
                n1 = math.hypot(*v1)
                n2 = math.hypot(*v2)
                if n1 < 1e-9 or n2 < 1e-9:
                    continue
                cosv = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
                angles.append(math.degrees(math.acos(cosv)))
    return angles


def convexity_ratio(geom) -> float:
    area = float(geom.area) if geom is not None else 0.0
    hull_area = float(geom.convex_hull.area) if geom is not None else 0.0
    return area / hull_area if hull_area > 0 else 0.0


def exterior_rings(geom) -> list[list[tuple[float, float]]]:
    return [list(poly.exterior.coords) for poly in iter_polygons(geom)]
