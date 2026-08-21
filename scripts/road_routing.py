#!/usr/bin/env python3
"""Compute actual shortest-path distances on a supplied road graph.

The graph contract is intentionally simple and portable: ``road_nodes.csv``
contains ``node_id,lat,lon`` and ``road_edges.csv`` contains ``u,v,length_m``
with optional ``oneway`` and ``highway`` fields.  A JSON object with ``nodes``
and ``edges`` arrays is also accepted.  A PBF conversion is available with
``--from-pbf`` but is opt-in because a country-wide graph can be large.
SQLite graph directories may also contain the optional
``ireland-geometry.ferry-schedules.v1`` ``ferry_schedules.json`` companion
contract for weekly/calendar-qualified service windows, next-opening waits,
and crossing durations,
plus an optional ``ireland-geometry.public-holidays.v1``
``public_holidays.json`` date companion for ``PH`` tokens.
Vehicle-weight conditional restrictions can be evaluated with an explicit
metric-tonne profile; without one they remain retained but inactive.
Numeric generic OSM ``maxweight`` way limits can also be enforced with that
profile on SQLite graphs; ambiguous generic limits are treated as impassable
only for weighted routes, while unprofiled routes retain them but do not
evaluate them.
Numeric OSM ``maxweightrating:hgv`` and Irish ``maxweightrating:goods`` limits
are persisted separately from actual-mass restrictions and can be enforced for
the explicit HGV vehicle class with a permitted-rating profile; this is
deliberately distinct from the actual ``maxweight`` profile.
Numeric legal and physical OSM ``maxheight`` limits can likewise be enforced
with an explicit vehicle-height profile on SQLite graphs; ambiguous values are
treated as impassable only when a height profile is supplied.
Numeric OSM ``maxwidth``, ``maxlength``, and ``maxaxleload`` limits can likewise
be enforced with explicit vehicle-dimension profiles on SQLite graphs;
ambiguous values are treated as impassable only when the corresponding profile
is supplied. Width and length values may use the OSM feet/inches notation.
The explicit ``hgv`` vehicle-class profile also enforces numeric
``maxweight:hgv`` limits when a weight is supplied.
Numeric OSM ``maxspeed`` values are persisted on v21 SQLite edges and cap
duration estimates per way without changing shortest-distance routing.
Safely supported ``maxspeed:conditional`` windows are persisted and evaluated
against an explicit departure profile; unsupported syntax remains retained but
is not evaluated.
Safely supported ``oneway:conditional`` and
``oneway:motor_vehicle:conditional`` windows are persisted on v21 SQLite edges
and evaluated against an explicit departure profile; unsupported syntax remains
retained and the base one-way semantics are preserved.
Public-service-vehicle conditional access can be evaluated with the explicit
``psv`` vehicle-class profile; general and delivery profiles treat a
``motor_vehicle:conditional=psv`` window as a temporary psv-only exception.
Taxi-specific conditional access can be evaluated with the explicit ``taxi``
vehicle-class profile.
Multiple supported conditional access clauses on one way and direction are
persisted as an ordered rule set; active class-specific allows are unioned and
active denies remove matching classes.
SQLite way records also retain nullable human-readable OSM context fields
(``name``, ``ref``, ``highway``, ``route``, and ``oneway``) for detailed route
explainability.
The explicit ``hgv`` vehicle-class profile also recognizes static
``hgv=destination`` and the observed ``none @ destination`` HGV weight/rating
exceptions. Static destination-only ways are blocked by default and can be
enabled only with an explicit destination-delivery profile; numeric base
limits remain enforced unless the corresponding conditional exception is
explicitly enabled.

Portable CSV/JSON graphs retain directed ``way_id`` values, optional
human-readable road context, and explicit ferry identity when those columns
are supplied. Ferry geometry is excluded unless ``include_ferries`` is
enabled; portable graphs do not claim SQLite-only ferry schedules, waits, or
crossing-duration semantics, nor the richer SQLite-only restriction and
vehicle-profile semantics.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import heapq
import json
import math
import os
import re
import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from runtime import (
        atomic_write_csv,
        atomic_write_json,
        project_data_tree_path,
        project_input_path,
        project_output_tree_path,
        project_path,
        reject_symlink_root,
        reject_symlink_tree,
    )
except ImportError:
    from scripts.runtime import (
        atomic_write_csv,
        atomic_write_json,
        project_data_tree_path,
        project_input_path,
        project_output_tree_path,
        project_path,
        reject_symlink_root,
        reject_symlink_tree,
    )


ROUTE_OBJECTIVES = ("distance", "duration")
VEHICLE_CLASSES = ("general", "delivery", "hgv", "psv", "taxi")
CONDITIONAL_ACCESS_DIRECTIONS = ("both", "forward", "backward")
ROAD_GRAPH_FORMAT = "ireland-geometry-road-sqlite-v21"
ROAD_CONTEXT_FIELDS = ("name", "ref", "highway", "route", "oneway")


def _optional_way_tag(value: object) -> str | None:
    """Normalize one persisted OSM way tag without inventing a value."""
    text = str(value or "").strip()
    return text or None


def validate_vehicle_class(value: object) -> str:
    """Normalize the supported routing vehicle-class profile."""
    vehicle_class = str(value or "general").strip().lower()
    if vehicle_class not in VEHICLE_CLASSES:
        raise ValueError(
            f"vehicle_class must be one of: {', '.join(VEHICLE_CLASSES)}"
        )
    return vehicle_class


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def pairwise(values: list[tuple]) -> list[tuple[tuple, tuple]]:
    return [(values[index - 1], values[index]) for index in range(1, len(values))]


HIGHWAY_TYPES = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "service", "living_street", "road", "motorway_link", "trunk_link",
    "primary_link", "secondary_link", "tertiary_link",
}
BLOCKED_ACCESS_VALUES = {"no", "private", "restricted"}
WEEKDAY_INDEX = {name: index for index, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"))}
MONTH_INDEX = {
    name: index
    for index, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}
CONDITIONAL_RESTRICTION_RE = re.compile(
    r"^\s*((?:no|only)_[A-Za-z0-9_]+)\s*@\s*(.+?)\s*$"
)
VEHICLE_WEIGHT_CONDITION_RE = re.compile(
    r"^\s*weight\s*(>=|<=|>|<)\s*(\d+(?:\.\d+)?)\s*(?:t|tonnes?)?\s*$",
    re.IGNORECASE,
)
MAXWEIGHT_VALUE_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(?:t|tonnes?|lt)?\s*$",
    re.IGNORECASE,
)
MAXHEIGHT_VALUE_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(cm|m|met(?:er|re)s?)?\s*$",
    re.IGNORECASE,
)
MAXHEIGHT_FEET_VALUE_RE = re.compile(
    r'^\s*(\d+(?:\.\d+)?)\s*\'\s*'
    r'(\d+(?:\.\d+)?)\s*"\s*$',
    re.IGNORECASE,
)
MAXDIMENSION_METRIC_VALUE_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(?:m|met(?:er|re)s?)?\s*$",
    re.IGNORECASE,
)
MAXDIMENSION_FEET_VALUE_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(?:'|ft|feet)"
    r"(?:\s*(\d+(?:\.\d+)?)\s*(?:\"|in|inch(?:es)?))?\s*$",
    re.IGNORECASE,
)
MAXSPEED_VALUE_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*"
    r"(?:(km/?h|kmh|kph|mph|knots?|kt))?\s*$",
    re.IGNORECASE,
)
CONDITIONAL_ACCESS_RE = re.compile(
    r"^\s*([A-Za-z_]+)\s*@\s*(.+?)\s*$",
    re.IGNORECASE,
)
CONDITIONAL_ACCESS_ALLOW_VALUES = {"yes", "permissive"}
CONDITIONAL_ACCESS_DENY_VALUES = {"no", "closed"}
MAXWEIGHT_STATUS_NOT_PROVIDED = "not_provided"
MAXWEIGHT_STATUS_SUPPORTED = "supported"
MAXWEIGHT_STATUS_UNLIMITED = "unlimited"
MAXWEIGHT_STATUS_UNSUPPORTED = "unsupported"
MAXWEIGHT_UNLIMITED_VALUES = {"none", "no", "unlimited", "unrestricted"}
CONDITIONAL_ACCESS_CLASS_VALUES = {"delivery", "hgv", "psv", "taxi"}
DAY_RANGE_RE = re.compile(
    r"^(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?$"
)
TIME_RANGE_RE = re.compile(r"^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$")
DAY_TOKEN_PATTERN = r"(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?"
WEEKLY_GROUP_RE = re.compile(
    rf"(?P<days>{DAY_TOKEN_PATTERN}(?:\s*,\s*{DAY_TOKEN_PATTERN})*)\s+"
    rf"(?P<times>\d{{1,2}}:\d{{2}}\s*-\s*\d{{1,2}}:\d{{2}}"
    rf"(?:\s*,\s*\d{{1,2}}:\d{{2}}\s*-\s*\d{{1,2}}:\d{{2}})*)"
)
MONTH_RANGE_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"(?:-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))?$",
    re.IGNORECASE,
)
DATE_CLOSED_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})$",
    re.IGNORECASE,
)
DATE_RANGE_RE = re.compile(
    r"^(?P<start_year>\d{4})\s+"
    r"(?P<start_month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"(?P<start_day>\d{1,2})\s*-\s*"
    r"(?P<end_year>\d{4})\s+"
    r"(?P<end_month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"(?P<end_day>\d{1,2})$",
    re.IGNORECASE,
)
FERRY_SCHEDULE_CONTRACT = "ireland-geometry.ferry-schedules.v1"
PUBLIC_HOLIDAY_CONTRACT = "ireland-geometry.public-holidays.v1"


def _load_public_holiday_contract(path: Path) -> tuple[str, frozenset[date]]:
    """Load and validate the explicit public-holiday date companion."""
    if path.is_symlink():
        raise ValueError("public holiday contract must not be a symlink")
    if not path.is_file():
        return "not_provided", frozenset()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"public holiday contract is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("contract") != PUBLIC_HOLIDAY_CONTRACT:
        raise ValueError(f"public holiday contract must be {PUBLIC_HOLIDAY_CONTRACT}")
    values = payload.get("dates", [])
    if not isinstance(values, list):
        raise TypeError("public holiday contract dates must be a list")
    parsed: set[date] = set()
    for value in values:
        text = str(value).strip()
        try:
            holiday = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                f"public holiday contract date must be ISO-8601: {text!r}"
            ) from exc
        if holiday.isoformat() != text:
            raise ValueError(
                f"public holiday contract date must be YYYY-MM-DD: {text!r}"
            )
        parsed.add(holiday)
    return "available", frozenset(parsed)


def _public_holiday_bounds(holidays: frozenset[date]) -> tuple[str | None, str | None]:
    """Return stable ISO bounds for the supplied holiday-date coverage."""
    if not holidays:
        return None, None
    return min(holidays).isoformat(), max(holidays).isoformat()


def is_access_restricted(tags: object) -> bool:
    """Return whether the most specific motor-vehicle access tag blocks routing."""
    for key in ("motor_vehicle", "motorcar", "vehicle", "access"):
        try:
            value = str(tags.get(key, "")).lower()
        except AttributeError:
            return False
        if value:
            return value in BLOCKED_ACCESS_VALUES
    return False


@dataclass(frozen=True)
class WeeklySchedule:
    """A deliberately small, deterministic subset of OSM opening-hours syntax.

    ``intervals`` covers year-round weekly windows. ``seasonal_intervals`` is
    used only for ferry metadata and adds month-constrained windows. Explicit
    ``date_ranges`` model bounded calendar periods such as a dated road
    closure/opening. Public holiday windows are evaluated only when an explicit
    holiday-date contract is available; otherwise their ordinary weekday
    component remains usable and the missing calendar is reported by the route
    metadata.
    """

    intervals: tuple[tuple[frozenset[int], int, int], ...]
    seasonal_intervals: tuple[
        tuple[frozenset[int], frozenset[int], int, int], ...
    ] = ()
    closed_dates: frozenset[tuple[int, int]] = frozenset()
    public_holiday_intervals: tuple[tuple[int, int], ...] = ()
    seasonal_public_holiday_intervals: tuple[
        tuple[frozenset[int], int, int], ...
    ] = ()
    date_ranges: tuple[tuple[date, date], ...] = ()

    @property
    def requires_public_holiday_calendar(self) -> bool:
        return bool(
            self.public_holiday_intervals
            or self.seasonal_public_holiday_intervals
        )

    @property
    def always_active(self) -> bool:
        """Return whether this schedule is an unconditional 24/7 window."""
        return (
            len(self.intervals) == 1
            and self.intervals[0] == (frozenset(range(7)), 0, 24 * 60)
            and not self.seasonal_intervals
            and not self.closed_dates
            and not self.public_holiday_intervals
            and not self.seasonal_public_holiday_intervals
            and not self.date_ranges
        )

    def active_at(
        self,
        moment: datetime,
        public_holidays: frozenset[date] | None = None,
    ) -> bool:
        if (moment.month, moment.day) in self.closed_dates:
            return False
        if any(
            start <= moment.date() <= end
            for start, end in self.date_ranges
        ):
            return True
        minute = moment.hour * 60 + moment.minute
        weekday = moment.weekday()
        if public_holidays is not None and moment.date() in public_holidays:
            if any(
                start <= minute < end
                for start, end in self.public_holiday_intervals
            ):
                return True
            if any(
                moment.month in months and start <= minute < end
                for months, start, end in self.seasonal_public_holiday_intervals
            ):
                return True
        if any(
            weekday in days
            and moment.month in months
            and start <= minute < end
            for months, days, start, end in self.seasonal_intervals
        ):
            return True
        return any(
            weekday in days and start <= minute < end
            for days, start, end in self.intervals
        )

    def next_active_at(
        self,
        moment: datetime,
        public_holidays: frozenset[date] | None = None,
        *,
        max_days: int = 731,
    ) -> datetime | None:
        """Return the next service start at or after ``moment``.

        Ferry routes may reach a valid service window before it opens. The
        search is day-based rather than minute-by-minute so it remains cheap
        when a graph contains many physical segments for one ferry way.
        ``None`` means that no supported window occurs within the bounded
        horizon.
        """
        if self.active_at(moment, public_holidays):
            return moment
        if max_days < 0:
            return None
        current_minute = moment.hour * 60 + moment.minute
        for day_offset in range(max_days + 1):
            day_moment = moment + timedelta(days=day_offset)
            if (day_moment.month, day_moment.day) in self.closed_dates:
                continue
            weekday = day_moment.weekday()
            candidates: list[int] = []
            if any(
                start <= day_moment.date() <= end
                for start, end in self.date_ranges
            ):
                candidates.append(0)
            candidates.extend(
                start
                for days, start, _end in self.intervals
                if weekday in days
            )
            candidates.extend(
                start
                for months, days, start, _end in self.seasonal_intervals
                if day_moment.month in months and weekday in days
            )
            if public_holidays is not None and day_moment.date() in public_holidays:
                candidates.extend(start for start, _end in self.public_holiday_intervals)
                candidates.extend(
                    start
                    for months, start, _end in self.seasonal_public_holiday_intervals
                    if day_moment.month in months
                )
            for candidate_minute in sorted(set(candidates)):
                if day_offset == 0 and candidate_minute <= current_minute:
                    continue
                return day_moment.replace(
                    hour=candidate_minute // 60,
                    minute=candidate_minute % 60,
                    second=0,
                    microsecond=0,
                )
        return None


@dataclass(frozen=True)
class ConditionalAccessRule:
    """A time-window access rule retained on a routable way."""

    mode: str
    schedule: WeeklySchedule
    vehicle_class: str = "general"
    direction: str = "both"
    weight_condition: tuple[str, float] | None = None


def _parse_month_spec(value: str) -> frozenset[int] | None:
    match = MONTH_RANGE_RE.fullmatch(value.strip())
    if not match:
        return None
    first = MONTH_INDEX[match.group(1).title()]
    last = MONTH_INDEX[(match.group(2) or match.group(1)).title()]
    if first <= last:
        return frozenset(range(first, last + 1))
    return frozenset((*range(first, 13), *range(1, last + 1)))


def _parse_weekly_clause(
    condition: str,
    *,
    allow_public_holidays: bool = False,
) -> WeeklySchedule | None:
    """Parse one weekday/time clause without month qualifiers."""
    if condition.lower() in {"off", "closed"}:
        return WeeklySchedule(())
    if condition.lower() == "24/7":
        return WeeklySchedule(((frozenset(range(7)), 0, 24 * 60),))
    def parse_days(day_text: str) -> tuple[frozenset[int], bool] | None:
        parsed_days: set[int] = set()
        public_holiday = False
        for day_token in day_text.split(","):
            if day_token.strip().upper() == "PH":
                if not allow_public_holidays:
                    return None
                public_holiday = True
                continue
            day_match = DAY_RANGE_RE.fullmatch(day_token.strip())
            if not day_match:
                return None
            first = WEEKDAY_INDEX[day_match.group(1)]
            last = WEEKDAY_INDEX[day_match.group(2) or day_match.group(1)]
            if first <= last:
                parsed_days.update(range(first, last + 1))
            else:
                parsed_days.update((*range(first, 7), *range(last + 1)))
        if not parsed_days and not public_holiday:
            return None
        return frozenset(parsed_days), public_holiday

    def parse_times(
        time_text: str,
        days: frozenset[int],
        public_holiday: bool,
    ) -> tuple[list[tuple[frozenset[int], int, int]], list[tuple[int, int]]] | None:
        intervals: list[tuple[frozenset[int], int, int]] = []
        public_holiday_intervals: list[tuple[int, int]] = []
        for part in time_text.split(","):
            match = TIME_RANGE_RE.fullmatch(part.strip())
            if not match:
                return None
            values: list[int] = []
            for clock in match.groups():
                hour, minute = (int(piece) for piece in clock.split(":"))
                if hour > 24 or minute > 59 or (hour == 24 and minute != 0):
                    return None
                values.append(hour * 60 + minute)
            if values[0] == values[1]:
                return None
            if values[0] < values[1]:
                intervals.append((days, values[0], values[1]))
                if public_holiday:
                    public_holiday_intervals.append((values[0], values[1]))
            else:
                intervals.append((days, values[0], 24 * 60))
                next_days = frozenset((day + 1) % 7 for day in days)
                intervals.append((next_days, 0, values[1]))
                if public_holiday:
                    public_holiday_intervals.extend(
                        ((values[0], 24 * 60), (0, values[1]))
                    )
        return intervals, public_holiday_intervals

    grouped = list(WEEKLY_GROUP_RE.finditer(condition))
    if grouped:
        intervals: list[tuple[frozenset[int], int, int]] = []
        cursor = 0
        for match in grouped:
            if condition[cursor:match.start()].strip(" ,"):
                return None
            parsed_days = parse_days(match.group("days"))
            if parsed_days is None:
                return None
            days, public_holiday = parsed_days
            parsed = parse_times(match.group("times"), days, public_holiday)
            if parsed is None:
                return None
            intervals.extend(parsed[0])
            cursor = match.end()
        if condition[cursor:].strip(" ,"):
            return None
        return WeeklySchedule(tuple(intervals)) if intervals else None

    tokens = condition.split(None, 1)
    days = frozenset(range(7))
    public_holiday = False
    if len(tokens) == 2:
        parsed_days = parse_days(tokens[0])
        if parsed_days is not None:
            days, public_holiday = parsed_days
            time_text = tokens[1]
        else:
            time_text = condition
    else:
        time_text = condition
    parsed = parse_times(time_text, days, public_holiday)
    if parsed is None:
        return None
    intervals, public_holiday_intervals = parsed
    return (
        WeeklySchedule(
            tuple(intervals),
            public_holiday_intervals=tuple(public_holiday_intervals),
        )
        if intervals
        else None
    )


def parse_weekly_schedule(value: str, *, allow_calendar: bool = False) -> WeeklySchedule | None:
    """Parse supported weekday windows, optionally with ferry calendar qualifiers.

    The default mode intentionally remains the weekly-only subset used by
    conditional turn restrictions. ``allow_calendar`` additionally accepts
    seasonal month clauses such as ``Jun-Aug: Mo-Sa 07:00-21:50``, bounded date
    ranges such as ``2026 Jul 04 - 2026 Oct 05``, fixed closed dates such as
    ``Dec 25``, and public-holiday windows such as ``Mo-Sa,PH 07:45-21:30``.
    Public-holiday dates are supplied separately by the graph's explicit
    ``public_holidays.json`` companion contract.
    """
    condition = str(value).strip()
    if condition.startswith("(") and condition.endswith(")"):
        condition = condition[1:-1].strip()
    if not condition or ">" in condition:
        return None
    if not allow_calendar:
        if ";" in condition:
            return None
        return _parse_weekly_clause(condition)
    if condition.lower() in {"off", "closed"}:
        return WeeklySchedule(())
    if condition.lower() == "24/7":
        return WeeklySchedule(((frozenset(range(7)), 0, 24 * 60),))
    intervals: list[tuple[frozenset[int], int, int]] = []
    seasonal_intervals: list[tuple[frozenset[int], frozenset[int], int, int]] = []
    closed_dates: set[tuple[int, int]] = set()
    public_holiday_intervals: list[tuple[int, int]] = []
    seasonal_public_holiday_intervals: list[tuple[frozenset[int], int, int]] = []
    date_ranges: list[tuple[date, date]] = []
    for raw_clause in condition.split(";"):
        clause = raw_clause.strip()
        if not clause:
            return None
        date_range_match = DATE_RANGE_RE.fullmatch(clause)
        if date_range_match:
            try:
                start = date(
                    int(date_range_match.group("start_year")),
                    MONTH_INDEX[date_range_match.group("start_month").title()],
                    int(date_range_match.group("start_day")),
                )
                end = date(
                    int(date_range_match.group("end_year")),
                    MONTH_INDEX[date_range_match.group("end_month").title()],
                    int(date_range_match.group("end_day")),
                )
            except (KeyError, TypeError, ValueError):
                return None
            if start > end:
                return None
            date_ranges.append((start, end))
            continue
        date_match = DATE_CLOSED_RE.fullmatch(clause)
        if date_match:
            month = MONTH_INDEX[date_match.group(1).title()]
            day = int(date_match.group(2))
            if day > calendar.monthrange(2024, month)[1]:
                return None
            closed_dates.add((month, day))
            continue
        month_spec: frozenset[int] | None = None
        if ":" in clause:
            prefix, remainder = (part.strip() for part in clause.split(":", 1))
            parsed_month_spec = _parse_month_spec(prefix)
            if parsed_month_spec is not None:
                month_spec = parsed_month_spec
                clause = remainder
        if month_spec is None:
            tokens = clause.split(None, 1)
            if tokens and _parse_month_spec(tokens[0]) is not None:
                month_spec = _parse_month_spec(tokens[0])
                clause = tokens[1].strip() if len(tokens) == 2 else ""
        if month_spec is not None and not clause:
            seasonal_intervals.append(
                (month_spec, frozenset(range(7)), 0, 24 * 60)
            )
            continue
        parsed = _parse_weekly_clause(clause, allow_public_holidays=True)
        if parsed is None:
            return None
        if month_spec is None:
            intervals.extend(parsed.intervals)
            public_holiday_intervals.extend(parsed.public_holiday_intervals)
        else:
            seasonal_intervals.extend(
                (month_spec, days, start, end)
                for days, start, end in parsed.intervals
            )
            seasonal_public_holiday_intervals.extend(
                (month_spec, start, end)
                for start, end in parsed.public_holiday_intervals
            )
    if not intervals and not seasonal_intervals and not closed_dates and not date_ranges:
        return None
    return WeeklySchedule(
        tuple(intervals),
        tuple(seasonal_intervals),
        frozenset(closed_dates),
        tuple(public_holiday_intervals),
        tuple(seasonal_public_holiday_intervals),
        tuple(date_ranges),
    )


def parse_vehicle_weight_condition(value: str) -> tuple[str, float] | None:
    """Parse an OSM vehicle-weight condition expressed in metric tonnes."""
    condition = str(value).strip()
    if condition.startswith("(") and condition.endswith(")"):
        condition = condition[1:-1].strip()
    match = VEHICLE_WEIGHT_CONDITION_RE.fullmatch(condition)
    if not match:
        return None
    try:
        threshold = float(match.group(2))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(threshold) or threshold < 0:
        return None
    return match.group(1), threshold


def parse_maxweight_profile(value: object) -> tuple[str, float | None]:
    """Classify a generic OSM ``maxweight`` value for SQLite edge routing.

    The supported numeric subset is expressed in metric tonnes.  Explicit
    unlimited values are retained as metadata but do not constrain routing;
    any other non-empty value is unresolved and is handled conservatively
    when a vehicle weight profile is supplied.
    """
    raw = str(value or "").strip()
    if not raw:
        return MAXWEIGHT_STATUS_NOT_PROVIDED, None
    if raw.lower() in MAXWEIGHT_UNLIMITED_VALUES:
        return MAXWEIGHT_STATUS_UNLIMITED, None
    match = MAXWEIGHT_VALUE_RE.fullmatch(raw)
    if not match:
        return MAXWEIGHT_STATUS_UNSUPPORTED, None
    try:
        limit_t = float(match.group(1))
    except (TypeError, ValueError):
        return MAXWEIGHT_STATUS_UNSUPPORTED, None
    if not math.isfinite(limit_t) or limit_t < 0:
        return MAXWEIGHT_STATUS_UNSUPPORTED, None
    return MAXWEIGHT_STATUS_SUPPORTED, limit_t


def parse_maxweightrating_hgv_profile(value: object) -> tuple[str, float | None]:
    """Parse the HGV permitted-weight-rating restriction.

    ``maxweightrating:hgv`` describes the vehicle's permitted gross-weight
    rating, not its current actual mass.  The common ``no`` value is not an
    unrestricted rating for this tag, so it remains unsupported and is
    fail-closed when a rating profile is supplied.
    """
    raw = str(value or "").strip()
    if not raw:
        return MAXWEIGHT_STATUS_NOT_PROVIDED, None
    normalized = raw.lower()
    if normalized in {"none", "unlimited", "unrestricted"}:
        return MAXWEIGHT_STATUS_UNLIMITED, None
    if normalized in {"no", "yes"}:
        return MAXWEIGHT_STATUS_UNSUPPORTED, None
    return parse_maxweight_profile(raw)


def parse_maxweightrating_hgv_t(value: object) -> float | None:
    """Return a numeric HGV permitted-rating value, or ``None``."""
    status, limit = parse_maxweightrating_hgv_profile(value)
    return limit if status == MAXWEIGHT_STATUS_SUPPORTED else None


def parse_maxweightrating_hgv_profiles(
    hgv_value: object,
    goods_value: object = "",
) -> tuple[str, float | None]:
    """Combine HGV and goods-vehicle permitted-rating restrictions.

    Irish mapping commonly pairs ``hgv=no`` with
    ``maxweightrating:goods=3``.  For the explicit HGV route profile that
    goods-qualified limit is an additional permitted-rating ceiling.  If both
    qualified tags are present, both restrictions apply and the strictest
    numeric ceiling wins.  An unresolved component remains fail-closed when a
    rating-qualified HGV route is requested.
    """
    parsed = [
        parse_maxweightrating_hgv_profile(hgv_value),
        parse_maxweightrating_hgv_profile(goods_value),
    ]
    provided = [
        (status, limit)
        for status, limit in parsed
        if status != MAXWEIGHT_STATUS_NOT_PROVIDED
    ]
    if not provided:
        return MAXWEIGHT_STATUS_NOT_PROVIDED, None
    if any(status == MAXWEIGHT_STATUS_UNSUPPORTED for status, _limit in provided):
        return MAXWEIGHT_STATUS_UNSUPPORTED, None
    numeric_limits = [
        limit for status, limit in provided if status == MAXWEIGHT_STATUS_SUPPORTED
    ]
    if numeric_limits:
        return MAXWEIGHT_STATUS_SUPPORTED, min(numeric_limits)
    return MAXWEIGHT_STATUS_UNLIMITED, None


def parse_hgv_destination_profiles(tags: object) -> tuple[dict[str, str], ...]:
    """Persist destination-qualified HGV access and limit exceptions.

    The supported subset is deliberately narrow and source-observed:
    ``hgv=destination`` is a static HGV destination-only access rule, while
    ``maxweight:hgv:conditional=none @ destination`` and
    ``maxweightrating:hgv:conditional=none @ destination`` are exceptions to
    their corresponding base numeric limits. Parenthesized ``(destination)``
    is equivalent. Other destination-bearing clauses remain visible as
    unsupported metadata so a route cannot silently treat them as supported.
    """
    entries: list[dict[str, str]] = []
    try:
        static_value = str(tags.get("hgv", "")).strip()
    except AttributeError:
        return ()
    if static_value.lower() == "destination":
        entries.append(
            {
                "source": "hgv",
                "kind": "access",
                "raw": static_value,
                "condition": "destination",
                "status": MAXWEIGHT_STATUS_SUPPORTED,
            }
        )
    conditional_sources = (
        ("maxweight:hgv:conditional", "maxweight_hgv_exception"),
        ("maxweightrating:hgv:conditional", "maxweightrating_hgv_exception"),
    )
    for source, kind in conditional_sources:
        try:
            raw = str(tags.get(source, "")).strip()
        except AttributeError:
            raw = ""
        if not raw:
            continue
        for clause in _split_conditional_clauses(raw):
            if "@" not in clause:
                continue
            value_text, condition = (part.strip() for part in clause.split("@", 1))
            normalized_condition = condition.strip().lower()
            if (
                normalized_condition.startswith("(")
                and normalized_condition.endswith(")")
            ):
                normalized_condition = normalized_condition[1:-1].strip()
            if "destination" not in normalized_condition:
                continue
            status = (
                MAXWEIGHT_STATUS_SUPPORTED
                if normalized_condition == "destination"
                and value_text.lower() in {"none", "unlimited", "unrestricted"}
                else MAXWEIGHT_STATUS_UNSUPPORTED
            )
            entries.append(
                {
                    "source": source,
                    "kind": kind if status == MAXWEIGHT_STATUS_SUPPORTED else f"{kind}_unsupported",
                    "raw": clause,
                    "condition": condition,
                    "status": status,
                }
            )
    return tuple(entries)


def parse_maxweight_t(value: object) -> float | None:
    """Return a parsed generic ``maxweight`` limit in metric tonnes."""
    status, limit_t = parse_maxweight_profile(value)
    return limit_t if status == MAXWEIGHT_STATUS_SUPPORTED else None


def parse_maxheight_profile(value: object) -> tuple[str, float | None]:
    """Classify and normalize an OSM ``maxheight`` value to metres.

    OSM's documented feet/inches form (for example ``6'7"``) is normalized
    alongside the ordinary metre form. Centimetres are accepted as an
    explicit metric unit; ambiguous values such as ``default`` remain
    unsupported and fail closed for height-qualified routes.
    """
    raw = str(value or "").strip()
    if not raw:
        return MAXWEIGHT_STATUS_NOT_PROVIDED, None
    if raw.lower() in MAXWEIGHT_UNLIMITED_VALUES:
        return MAXWEIGHT_STATUS_UNLIMITED, None
    match = MAXHEIGHT_VALUE_RE.fullmatch(raw)
    if match:
        try:
            limit_m = float(match.group(1))
        except (TypeError, ValueError):
            return MAXWEIGHT_STATUS_UNSUPPORTED, None
        if str(match.group(2) or "").lower() == "cm":
            limit_m /= 100.0
    else:
        feet_match = MAXHEIGHT_FEET_VALUE_RE.fullmatch(raw)
        if not feet_match:
            return MAXWEIGHT_STATUS_UNSUPPORTED, None
        try:
            feet = float(feet_match.group(1))
            inches = float(feet_match.group(2))
        except (TypeError, ValueError):
            return MAXWEIGHT_STATUS_UNSUPPORTED, None
        if inches >= 12:
            return MAXWEIGHT_STATUS_UNSUPPORTED, None
        limit_m = feet * 0.3048 + inches * 0.0254
    if not math.isfinite(limit_m) or limit_m < 0:
        return MAXWEIGHT_STATUS_UNSUPPORTED, None
    return MAXWEIGHT_STATUS_SUPPORTED, limit_m


def parse_maxheight_m(value: object) -> float | None:
    """Return a parsed OSM ``maxheight`` limit in metres."""
    status, limit_m = parse_maxheight_profile(value)
    return limit_m if status == MAXWEIGHT_STATUS_SUPPORTED else None


def parse_maxdimension_profile(value: object) -> tuple[str, float | None]:
    """Classify an OSM width/length value and normalize it to metres.

    OSM dimension tags default to metres but also allow feet and inches, for
    example ``8'6"`` or ``8 ft 6 in``. Explicit unlimited values are retained
    without constraining routing; non-empty values outside this numeric subset
    remain unsupported and are handled conservatively for active profiles.
    """
    raw = str(value or "").strip()
    if not raw:
        return MAXWEIGHT_STATUS_NOT_PROVIDED, None
    if raw.lower() in MAXWEIGHT_UNLIMITED_VALUES:
        return MAXWEIGHT_STATUS_UNLIMITED, None
    match = MAXDIMENSION_METRIC_VALUE_RE.fullmatch(raw)
    limit_m: float | None = None
    if match:
        try:
            limit_m = float(match.group(1))
        except (TypeError, ValueError):
            limit_m = None
    else:
        match = MAXDIMENSION_FEET_VALUE_RE.fullmatch(raw)
        if match:
            try:
                feet = float(match.group(1))
                inches = float(match.group(2) or 0.0)
                limit_m = feet * 0.3048 + inches * 0.0254
            except (TypeError, ValueError):
                limit_m = None
    if limit_m is None or not math.isfinite(limit_m) or limit_m < 0:
        return MAXWEIGHT_STATUS_UNSUPPORTED, None
    return MAXWEIGHT_STATUS_SUPPORTED, limit_m


def parse_maxwidth_profile(value: object) -> tuple[str, float | None]:
    """Classify an OSM ``maxwidth`` value and normalize it to metres."""
    return parse_maxdimension_profile(value)


def parse_maxwidth_m(value: object) -> float | None:
    """Return a parsed OSM ``maxwidth`` limit in metres."""
    status, limit_m = parse_maxwidth_profile(value)
    return limit_m if status == MAXWEIGHT_STATUS_SUPPORTED else None


def parse_maxlength_profile(value: object) -> tuple[str, float | None]:
    """Classify an OSM ``maxlength`` value and normalize it to metres."""
    return parse_maxdimension_profile(value)


def parse_maxlength_m(value: object) -> float | None:
    """Return a parsed OSM ``maxlength`` limit in metres."""
    status, limit_m = parse_maxlength_profile(value)
    return limit_m if status == MAXWEIGHT_STATUS_SUPPORTED else None


def parse_maxaxleload_profile(value: object) -> tuple[str, float | None]:
    """Classify an OSM ``maxaxleload`` value in metric tonnes."""
    return parse_maxweight_profile(value)


def parse_maxaxleload_t(value: object) -> float | None:
    """Return a parsed OSM ``maxaxleload`` limit in metric tonnes."""
    status, limit_t = parse_maxaxleload_profile(value)
    return limit_t if status == MAXWEIGHT_STATUS_SUPPORTED else None


def parse_maxspeed_profile(value: object) -> tuple[str, float | None]:
    """Classify an OSM ``maxspeed`` value and normalize it to km/h.

    OSM numeric ``maxspeed`` values default to km/h; explicit ``mph`` and
    ``knots`` suffixes are converted to km/h. Implicit, variable, walking,
    and other non-numeric values remain unsupported so duration routing does
    not invent a legal speed ceiling.
    """
    raw = str(value or "").strip()
    if not raw:
        return MAXWEIGHT_STATUS_NOT_PROVIDED, None
    if raw.lower() in MAXWEIGHT_UNLIMITED_VALUES:
        return MAXWEIGHT_STATUS_UNLIMITED, None
    match = MAXSPEED_VALUE_RE.fullmatch(raw)
    if not match:
        return MAXWEIGHT_STATUS_UNSUPPORTED, None
    try:
        limit_kmh = float(match.group(1))
    except (TypeError, ValueError):
        return MAXWEIGHT_STATUS_UNSUPPORTED, None
    unit = str(match.group(2) or "").lower()
    if unit == "mph":
        limit_kmh *= 1.609344
    elif unit in {"knot", "knots", "kt"}:
        limit_kmh *= 1.852
    if not math.isfinite(limit_kmh) or limit_kmh < 0:
        return MAXWEIGHT_STATUS_UNSUPPORTED, None
    return MAXWEIGHT_STATUS_SUPPORTED, limit_kmh


def parse_maxspeed_kmh(value: object) -> float | None:
    """Return a parsed OSM ``maxspeed`` ceiling in km/h."""
    status, limit_kmh = parse_maxspeed_profile(value)
    return limit_kmh if status == MAXWEIGHT_STATUS_SUPPORTED else None


def _split_conditional_clauses(value: str) -> tuple[str, ...]:
    """Split semicolon-separated conditional clauses outside parentheses."""
    clauses: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for index, character in enumerate(value):
        if character in {'"', "'"}:
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            continue
        if quote is not None:
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif character == ";" and depth == 0:
            clause = value[start:index].strip()
            if clause:
                clauses.append(clause)
            start = index + 1
    clause = value[start:].strip()
    if clause:
        clauses.append(clause)
    return tuple(clauses)


def parse_maxspeed_conditional_profile(value: object) -> tuple[dict[str, object], ...]:
    """Parse the safely evaluable subset of an OSM ``maxspeed:conditional`` tag.

    Each returned entry retains the raw clause, normalized speed status/value,
    and condition text. The route graph reparses the condition into a
    ``WeeklySchedule`` once at load time, so unsupported syntax remains
    visible in the persisted edge contract without affecting routing.
    """
    raw = str(value or "").strip()
    if not raw:
        return ()
    entries: list[dict[str, object]] = []
    for clause in _split_conditional_clauses(raw):
        if "@" in clause:
            speed_text, condition = (part.strip() for part in clause.split("@", 1))
        else:
            speed_text = clause
            condition = "24/7"
        speed_status, speed_kmh = parse_maxspeed_profile(speed_text)
        schedule = parse_weekly_schedule(condition, allow_calendar=True)
        status = (
            speed_status
            if speed_status in {MAXWEIGHT_STATUS_SUPPORTED, MAXWEIGHT_STATUS_UNLIMITED}
            and schedule is not None
            else MAXWEIGHT_STATUS_UNSUPPORTED
        )
        entries.append(
            {
                "raw": clause,
                "condition": condition,
                "speed_kmh": speed_kmh,
                "status": status,
            }
        )
    return tuple(entries)


def parse_oneway_conditional_profile(value: object) -> tuple[dict[str, object], ...]:
    """Parse the safely evaluable subset of an OSM conditional one-way tag.

    OSM commonly writes a schedule-only value such as ``Mo-Fr 08:00-10:00``
    for a temporary forward one-way restriction. Explicit ``yes`` and ``no``
    clauses are also supported. Other access qualifiers, including
    ``permit`` and ``private``, remain retained as unsupported metadata so the
    base one-way semantics are not silently changed.
    """
    raw = str(value or "").strip()
    if not raw:
        return ()
    entries: list[dict[str, object]] = []
    for clause in _split_conditional_clauses(raw):
        if "@" in clause:
            mode_text, condition = (part.strip() for part in clause.split("@", 1))
        else:
            mode_text = "yes"
            condition = clause
        mode = mode_text.lower()
        if mode in {"1", "true"}:
            mode = "yes"
        elif mode == "0":
            mode = "no"
        schedule = parse_weekly_schedule(condition, allow_calendar=True)
        status = (
            MAXWEIGHT_STATUS_SUPPORTED
            if mode in {"yes", "no"} and schedule is not None
            else MAXWEIGHT_STATUS_UNSUPPORTED
        )
        entries.append(
            {
                "raw": clause,
                "mode": mode,
                "condition": condition,
                "status": status,
            }
        )
    return tuple(entries)


def parse_oneway_conditional_profiles(*values: object) -> tuple[dict[str, object], ...]:
    """Combine supported source tags into one persisted conditional profile."""
    entries: list[dict[str, object]] = []
    for value in values:
        entries.extend(parse_oneway_conditional_profile(value))
    return tuple(entries)


def parse_conditional_access_profile_rules_value(
    value: object,
) -> tuple[tuple[str, str, str], ...] | None:
    """Parse one conditional-access tag into an ordered rule set.

    OSM allows multiple clauses on one conditional key, for example
    ``delivery @ (Mo-Sa 07:00-11:00); yes @ (Mo-Sa 18:30-24:00)``.  The
    previous single-rule parser silently rejected that source shape.  Every
    clause must still be independently supported; a mixed supported/unknown
    value remains ``None`` so the graph builder can fail closed.
    """
    raw = str(value or "").strip()
    if not raw:
        return ()
    clauses = _split_conditional_clauses(raw)
    if not clauses:
        return ()
    parsed_rules: list[tuple[str, str, str]] = []
    for clause in clauses:
        normalized = clause.strip().lower()
        if normalized in CONDITIONAL_ACCESS_ALLOW_VALUES:
            parsed_rules.append(("allow", "general", "24/7"))
            continue
        if normalized in CONDITIONAL_ACCESS_DENY_VALUES:
            parsed_rules.append(("deny", "general", "24/7"))
            continue
        if normalized in CONDITIONAL_ACCESS_CLASS_VALUES:
            parsed_rules.append(("allow", normalized, "24/7"))
            continue
        match = CONDITIONAL_ACCESS_RE.fullmatch(clause)
        if not match:
            return None
        access_value = match.group(1).lower()
        if access_value in CONDITIONAL_ACCESS_ALLOW_VALUES:
            mode = "allow"
            vehicle_class = "general"
        elif access_value in CONDITIONAL_ACCESS_DENY_VALUES:
            mode = "deny"
            vehicle_class = "general"
        elif access_value in CONDITIONAL_ACCESS_CLASS_VALUES:
            mode = "allow"
            vehicle_class = access_value
        else:
            return None
        condition = match.group(2).strip()
        schedule = parse_weekly_schedule(condition)
        if schedule is None:
            schedule = parse_weekly_schedule(condition, allow_calendar=True)
        if schedule is None and parse_vehicle_weight_condition(condition) is None:
            return None
        parsed_rules.append((mode, vehicle_class, condition))
    return tuple(parsed_rules)


def parse_conditional_access_profile_rule(value: str) -> tuple[str, str, str] | None:
    """Return one ``(mode, vehicle_class, condition)`` access rule.

    This compatibility helper intentionally returns ``None`` for a
    multi-clause value. Call :func:`parse_conditional_access_profile_rules_value`
    when all clauses are needed.
    """
    parsed = parse_conditional_access_profile_rules_value(value)
    if parsed is None or len(parsed) != 1:
        return None
    return parsed[0]


def parse_conditional_access_rule(value: str) -> tuple[str, str] | None:
    """Return a supported general allow/deny rule for compatibility."""
    parsed = parse_conditional_access_profile_rule(value)
    if parsed is None or parsed[1] != "general":
        return None
    return parsed[0], parsed[2]


def parse_conditional_access(value: str) -> str | None:
    """Return the supported weekly condition for an explicitly allowed value."""
    parsed = parse_conditional_access_rule(value)
    return parsed[1] if parsed is not None and parsed[0] == "allow" else None


def conditional_access_rule(tags: object) -> tuple[str, str] | None:
    """Return a supported general motor-vehicle access rule from OSM tags."""
    parsed = conditional_access_profile_rule(tags)
    return (parsed[0], parsed[2]) if parsed is not None and parsed[1] == "general" else None


def conditional_access_profile_rules(
    tags: object,
) -> list[tuple[str, str, str, str]] | None:
    """Return supported conditional access rules with traversal directions.

    The result contains ``(mode, vehicle_class, condition, direction)`` rows.
    ``direction`` is ``both`` for the ordinary OSM key and ``forward`` or
    ``backward`` for directional keys.  ``None`` means a conditional access
    value was present but could not be safely evaluated; an empty list means
    no supported conditional access keys were present.
    """
    rules: list[tuple[str, str, str, str]] = []
    for direction in CONDITIONAL_ACCESS_DIRECTIONS:
        suffix = "" if direction == "both" else f":{direction}"
        for key in (
            "motor_vehicle",
            "motorcar",
            "vehicle",
            "access",
            "hgv",
            "taxi",
            "psv",
        ):
            try:
                value = tags.get(f"{key}{suffix}:conditional", "")
            except AttributeError:
                return []
            if not value:
                continue
            parsed = parse_conditional_access_profile_rules_value(str(value))
            if parsed is None:
                return None
            for rule in parsed:
                if key in {"hgv", "taxi", "psv"}:
                    rule = (rule[0], key, rule[2])
                rules.append((*rule, direction))
            break
    return rules


def conditional_access_profile_rule(tags: object) -> tuple[str, str, str] | None:
    """Return a supported conditional access rule including vehicle class.

    This compatibility wrapper preserves the historical three-field return
    value.  Direction-aware callers should use
    :func:`conditional_access_profile_rules`.
    """
    rules = conditional_access_profile_rules(tags)
    if not rules:
        return None
    return rules[0][:3]


def conditional_access_condition(tags: object) -> str | None:
    """Return a supported motor-vehicle conditional-access window from OSM tags."""
    parsed = conditional_access_rule(tags)
    return parsed[1] if parsed is not None and parsed[0] == "allow" else None


def has_conditional_access_tag(tags: object) -> bool:
    """Return whether a supported vehicle access key has a conditional tag."""
    for direction in CONDITIONAL_ACCESS_DIRECTIONS:
        suffix = "" if direction == "both" else f":{direction}"
        for key in (
            "motor_vehicle",
            "motorcar",
            "vehicle",
            "access",
            "hgv",
            "taxi",
            "psv",
        ):
            try:
                if tags.get(f"{key}{suffix}:conditional", ""):
                    return True
            except AttributeError:
                return False
    return False


def has_multi_clause_conditional_access(tags: object) -> bool:
    """Return whether a selected conditional-access key has multiple clauses."""
    for direction in CONDITIONAL_ACCESS_DIRECTIONS:
        suffix = "" if direction == "both" else f":{direction}"
        for key in (
            "motor_vehicle",
            "motorcar",
            "vehicle",
            "access",
            "hgv",
            "taxi",
            "psv",
        ):
            try:
                value = tags.get(f"{key}{suffix}:conditional", "")
            except AttributeError:
                return False
            if not value:
                continue
            parsed = parse_conditional_access_profile_rules_value(value)
            return parsed is not None and len(parsed) > 1
    return False


def vehicle_weight_condition_active(
    vehicle_weight_t: float,
    condition: tuple[str, float],
) -> bool:
    """Return whether a metric-tonne vehicle profile activates a condition."""
    operator, threshold = condition
    if operator == ">":
        return vehicle_weight_t > threshold
    if operator == ">=":
        return vehicle_weight_t >= threshold
    if operator == "<":
        return vehicle_weight_t < threshold
    return vehicle_weight_t <= threshold


def parse_duration_seconds(value: object) -> float | None:
    """Parse the small duration subset used by ferry way metadata."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    text = str(value or "").strip().lower()
    if not text:
        return None
    clock = re.fullmatch(r"(\d{1,3}):(\d{2})(?::(\d{2}))?", text)
    if clock:
        hours = int(clock.group(1))
        minutes = int(clock.group(2))
        seconds = int(clock.group(3) or 0)
        if minutes > 59 or seconds > 59:
            return None
        result = float(hours * 3600 + minutes * 60 + seconds)
        return result if result > 0 else None
    parts = re.fullmatch(
        r"(?:(\d+(?:\.\d+)?)\s*h(?:ours?)?\s*)?"
        r"(?:(\d+(?:\.\d+)?)\s*m(?:in(?:ute)?s?)?\s*)?"
        r"(?:(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?\s*)?",
        text,
    )
    if not parts or not any(parts.groups()):
        return None
    hours, minutes, seconds = (float(part or 0) for part in parts.groups())
    result = hours * 3600 + minutes * 60 + seconds
    return result if math.isfinite(result) and result > 0 else None


def parse_conditional_restriction(value: str) -> tuple[str, str] | None:
    """Return ``(no|only, condition)`` when a condition is supported."""
    match = CONDITIONAL_RESTRICTION_RE.fullmatch(str(value).strip())
    if not match:
        return None
    condition = match.group(2).strip()
    if parse_weekly_schedule(condition) is None and parse_vehicle_weight_condition(condition) is None:
        return None
    kind = "no" if match.group(1).startswith("no_") else "only"
    return kind, condition


def straight_distance(a: dict[str, str], b: dict[str, str]) -> float:
    lat0 = math.radians((number(a.get("lat")) + number(b.get("lat"))) / 2)
    dx = (number(a.get("lon")) - number(b.get("lon"))) * 111320 * math.cos(lat0)
    dy = (number(a.get("lat")) - number(b.get("lat"))) * 110540
    return math.hypot(dx, dy)


@dataclass(frozen=True)
class PortableEdge:
    """One directed edge retained by a portable CSV/JSON graph."""

    from_node: str
    to_node: str
    length_m: float
    way_id: str = ""
    direction: str = "forward"
    road_context: dict[str, str | None] | None = None
    ferry: bool = False


class PortableRoadGraph:
    """In-memory graph that keeps the legacy two-value unpacking contract.

    ``coordinates, adjacency = graph`` remains valid for callers that use the
    original portable graph API.  The object additionally retains directed edge
    metadata so route responses can expose way IDs, basic road context, and
    ferry identity when the supplied CSV/JSON rows provide them.
    """

    def __init__(
        self,
        coordinates: dict[str, tuple[float, float]],
        adjacency: dict[str, list[tuple[str, float]]],
        edges_by_from: dict[str, list[PortableEdge]],
    ) -> None:
        self.coordinates = coordinates
        self.adjacency = adjacency
        self.edges_by_from = edges_by_from

    def __iter__(self):
        """Yield coordinates and adjacency for backwards-compatible unpacking."""
        yield self.coordinates
        yield self.adjacency

    def __getitem__(self, index: int):
        if index in {0, -2}:
            return self.coordinates
        if index in {1, -1}:
            return self.adjacency
        raise IndexError(index)

    def __len__(self) -> int:
        return 2

    def neighbours(self, node: str) -> tuple[PortableEdge, ...]:
        """Return deterministic directed edges leaving ``node``."""
        return tuple(self.edges_by_from.get(node, ()))

    @property
    def node_count(self) -> int:
        return len(self.coordinates)

    @property
    def edge_count(self) -> int:
        return sum(len(edges) for edges in self.edges_by_from.values())

    @property
    def ferry_edge_count(self) -> int:
        """Return the number of directed ferry edges retained by the graph."""
        return sum(
            1
            for values in self.edges_by_from.values()
            for edge in values
            if edge.ferry
        )

    @property
    def has_way_ids(self) -> bool:
        edges = [edge for values in self.edges_by_from.values() for edge in values]
        return bool(edges) and all(edge.way_id for edge in edges)

    @property
    def has_way_context(self) -> bool:
        return any(
            edge.way_id
            or any(
                edge.road_context.get(field)
                for field in ROAD_CONTEXT_FIELDS
                if field != "oneway"
            )
            for values in self.edges_by_from.values()
            for edge in values
            if edge.road_context is not None
        )

    @property
    def way_context_n(self) -> int:
        return len({
            edge.way_id
            for values in self.edges_by_from.values()
            for edge in values
            if edge.way_id
        })


def _portable_road_context(row: dict, oneway: str) -> dict[str, str | None]:
    """Normalize optional human-readable fields from one graph row."""
    return {
        field: _optional_way_tag(row.get(field, oneway if field == "oneway" else ""))
        for field in ROAD_CONTEXT_FIELDS
    }


def graph_from_rows(nodes: list[dict], edges: list[dict]) -> PortableRoadGraph:
    coordinates = {
        str(row.get("node_id", row.get("id", ""))): (number(row.get("lat")), number(row.get("lon")))
        for row in nodes
        if str(row.get("node_id", row.get("id", ""))).strip()
    }
    adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)
    edges_by_from: dict[str, list[PortableEdge]] = defaultdict(list)
    for row in edges:
        u = str(row.get("u", row.get("from", ""))).strip()
        v = str(row.get("v", row.get("to", ""))).strip()
        if not u or not v or u not in coordinates or v not in coordinates:
            continue
        length = number(row.get("length_m"), -1)
        if length < 0:
            lat_u, lon_u = coordinates[u]
            lat_v, lon_v = coordinates[v]
            length = straight_distance(
                {"lat": str(lat_u), "lon": str(lon_u)},
                {"lat": str(lat_v), "lon": str(lon_v)},
            )
        if not math.isfinite(length) or length < 0:
            continue
        oneway = str(row.get("oneway", "")).lower()
        way_id = str(row.get("way_id", row.get("id", ""))).strip()
        context = _portable_road_context(row, oneway)
        route = str(row.get("route", "")).strip().lower()
        ferry_tag = str(row.get("ferry", "")).strip().lower()
        is_ferry = route == "ferry" or ferry_tag not in {"", "0", "false", "no", "none"}
        if is_ferry and not context.get("route"):
            # Keep JSON/CSV rows that use ``ferry=yes`` round-trippable through
            # the portable contract, whose human-readable context includes
            # ``route`` but does not need a separate ferry column.
            context["route"] = "ferry"

        def append_edge(
            from_node: str,
            to_node: str,
            direction: str,
            *,
            edge_length: float = length,
            edge_way_id: str = way_id,
            edge_context: dict[str, str | None] = context,
            edge_ferry: bool = is_ferry,
        ) -> None:
            adjacency[from_node].append((to_node, edge_length))
            edges_by_from[from_node].append(
                PortableEdge(
                    from_node=from_node,
                    to_node=to_node,
                    length_m=edge_length,
                    way_id=edge_way_id,
                    direction=direction,
                    road_context=dict(edge_context),
                    ferry=edge_ferry,
                )
            )

        if oneway == "-1":
            append_edge(v, u, "backward")
        else:
            append_edge(u, v, "forward")
            if oneway not in {"yes", "1", "true"}:
                append_edge(v, u, "backward")
    return PortableRoadGraph(coordinates, dict(adjacency), dict(edges_by_from))


class SQLiteRoadGraph:
    """Disk-backed graph for country-scale routing without an adjacency dump."""

    CELL_DEG = 0.01

    def __init__(self, path: Path):
        self.path = path
        self.connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        metadata_unsupported_n = 0
        metadata_excluded_n = 0
        metadata_maxweight: dict[str, int] = {}
        metadata_maxweight_available = False
        metadata_maxweightrating_hgv: dict[str, int] = {}
        metadata_maxweightrating_hgv_available = False
        metadata_maxheight: dict[str, int] = {}
        metadata_maxheight_available = False
        metadata_dimensions: dict[str, dict[str, int]] = {}
        metadata_dimensions_available = False
        metadata_maxspeed: dict[str, int] = {}
        metadata_maxspeed_available = False
        metadata_maxspeed_conditional_available = False
        metadata_oneway_conditional: dict[str, int] = {}
        metadata_oneway_conditional_available = False
        metadata_hgv_destination: dict[str, int] = {}
        metadata_hgv_destination_available = False
        metadata_path = path.parent / "road_graph_metadata.json"
        if metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(metadata, dict):
                    metadata_unsupported_n = int(
                        metadata.get("conditional_access_unsupported_n", 0)
                    )
                    metadata_excluded_n = int(
                        metadata.get("conditional_access_excluded_n", 0)
                    )
                    maxweight_keys = (
                        "maxweight_way_n",
                        "maxweight_supported_way_n",
                        "maxweight_unlimited_way_n",
                        "maxweight_unsupported_way_n",
                        "maxweight_segment_n",
                        "maxweight_hgv_way_n",
                        "maxweight_hgv_supported_way_n",
                        "maxweight_hgv_unlimited_way_n",
                        "maxweight_hgv_unsupported_way_n",
                        "maxweight_hgv_segment_n",
                    )
                    metadata_maxweight_available = all(
                        key in metadata for key in maxweight_keys
                    )
                    if metadata_maxweight_available:
                        metadata_maxweight = {
                            key: int(metadata.get(key, 0)) for key in maxweight_keys
                        }
                    maxweightrating_hgv_keys = (
                        "maxweightrating_hgv_way_n",
                        "maxweightrating_hgv_supported_way_n",
                        "maxweightrating_hgv_unlimited_way_n",
                        "maxweightrating_hgv_unsupported_way_n",
                        "maxweightrating_hgv_segment_n",
                    )
                    metadata_maxweightrating_hgv_available = all(
                        key in metadata for key in maxweightrating_hgv_keys
                    )
                    if metadata_maxweightrating_hgv_available:
                        metadata_maxweightrating_hgv = {
                            key: int(metadata.get(key, 0))
                            for key in maxweightrating_hgv_keys
                        }
                    maxheight_keys = (
                        "maxheight_way_n",
                        "maxheight_supported_way_n",
                        "maxheight_unlimited_way_n",
                        "maxheight_unsupported_way_n",
                        "maxheight_segment_n",
                        "maxheight_physical_way_n",
                        "maxheight_physical_supported_way_n",
                        "maxheight_physical_unlimited_way_n",
                        "maxheight_physical_unsupported_way_n",
                        "maxheight_physical_segment_n",
                    )
                    metadata_maxheight_available = all(
                        key in metadata for key in maxheight_keys
                    )
                    if metadata_maxheight_available:
                        metadata_maxheight = {
                            key: int(metadata.get(key, 0)) for key in maxheight_keys
                        }
                    dimension_keys = {
                        name: (
                            f"{name}_way_n",
                            f"{name}_supported_way_n",
                            f"{name}_unlimited_way_n",
                            f"{name}_unsupported_way_n",
                            f"{name}_segment_n",
                        )
                        for name in ("maxwidth", "maxlength", "maxaxleload")
                    }
                    metadata_dimensions_available = all(
                        all(key in metadata for key in keys)
                        for keys in dimension_keys.values()
                    )
                    if metadata_dimensions_available:
                        metadata_dimensions = {
                            name: {key: int(metadata.get(key, 0)) for key in keys}
                            for name, keys in dimension_keys.items()
                        }
                    maxspeed_keys = (
                        "maxspeed_way_n",
                        "maxspeed_supported_way_n",
                        "maxspeed_unlimited_way_n",
                        "maxspeed_unsupported_way_n",
                        "maxspeed_segment_n",
                        "maxspeed_conditional_way_n",
                    )
                    metadata_maxspeed_available = all(
                        key in metadata for key in maxspeed_keys
                    )
                    maxspeed_conditional_keys = (
                        "maxspeed_conditional_supported_way_n",
                        "maxspeed_conditional_unsupported_way_n",
                        "maxspeed_conditional_segment_n",
                    )
                    metadata_maxspeed_conditional_available = all(
                        key in metadata for key in maxspeed_conditional_keys
                    )
                    if metadata_maxspeed_available:
                        metadata_maxspeed = {
                            key: int(metadata.get(key, 0)) for key in (
                                *maxspeed_keys,
                                *maxspeed_conditional_keys,
                            )
                        }
                    oneway_conditional_keys = (
                        "oneway_conditional_way_n",
                        "oneway_conditional_supported_way_n",
                        "oneway_conditional_unsupported_way_n",
                        "oneway_conditional_segment_n",
                    )
                    metadata_oneway_conditional_available = all(
                        key in metadata for key in oneway_conditional_keys
                    )
                    if metadata_oneway_conditional_available:
                        metadata_oneway_conditional = {
                            key: int(metadata.get(key, 0))
                            for key in oneway_conditional_keys
                        }
                    hgv_destination_keys = (
                        "hgv_destination_way_n",
                        "hgv_destination_supported_way_n",
                        "hgv_destination_unsupported_way_n",
                        "hgv_destination_segment_n",
                    )
                    metadata_hgv_destination_available = all(
                        key in metadata for key in hgv_destination_keys
                    )
                    if metadata_hgv_destination_available:
                        metadata_hgv_destination = {
                            key: int(metadata.get(key, 0))
                            for key in hgv_destination_keys
                        }
            except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
                metadata_unsupported_n = 0
                metadata_excluded_n = 0
        self.node_count = int(self.connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
        self.edge_count = int(self.connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
        self.bounds = self.connection.execute(
            "SELECT MIN(cell_lat), MAX(cell_lat), MIN(cell_lon), MAX(cell_lon) FROM nodes"
        ).fetchone()
        edge_columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(edges)")
        }
        self.has_way_ids = "way_id" in edge_columns
        self.way_context_columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(ways)")
        }
        self.has_way_context = "way_id" in self.way_context_columns
        self.way_context_n = (
            int(self.connection.execute("SELECT COUNT(*) FROM ways").fetchone()[0])
            if self.has_way_context
            else 0
        )
        self.has_maxweight_profiles = {
            "maxweight_t",
            "maxweight_status",
        }.issubset(edge_columns)
        self.has_hgv_maxweight_profiles = {
            "maxweight_hgv_t",
            "maxweight_hgv_status",
        }.issubset(edge_columns)
        self.has_hgv_maxweightrating_profiles = {
            "maxweightrating_hgv_t",
            "maxweightrating_hgv_status",
        }.issubset(edge_columns)
        self.has_maxheight_profiles = {
            "maxheight_m",
            "maxheight_status",
            "maxheight_physical_m",
            "maxheight_physical_status",
        }.issubset(edge_columns)
        self.has_vehicle_dimension_profiles = all(
            {
                f"{name}_m" if name != "maxaxleload" else "maxaxleload_t",
                f"{name}_status",
            }.issubset(edge_columns)
            for name in ("maxwidth", "maxlength", "maxaxleload")
        )
        self.has_maxspeed_profiles = {
            "maxspeed_kmh",
            "maxspeed_status",
        }.issubset(edge_columns)
        self.has_maxspeed_conditional_profiles = (
            "maxspeed_conditional_json" in edge_columns
        )
        self.has_oneway_conditional_profiles = (
            "oneway_conditional_json" in edge_columns
        )
        self.has_hgv_destination_profiles = "hgv_destination_json" in edge_columns
        if self.has_maxweight_profiles and metadata_maxweight_available:
            self.maxweight_way_n = metadata_maxweight["maxweight_way_n"]
            self.maxweight_supported_way_n = metadata_maxweight[
                "maxweight_supported_way_n"
            ]
            self.maxweight_unlimited_way_n = metadata_maxweight[
                "maxweight_unlimited_way_n"
            ]
            self.maxweight_unsupported_way_n = metadata_maxweight[
                "maxweight_unsupported_way_n"
            ]
            self.maxweight_segment_n = metadata_maxweight["maxweight_segment_n"]
            self.maxweight_hgv_way_n = metadata_maxweight["maxweight_hgv_way_n"]
            self.maxweight_hgv_supported_way_n = metadata_maxweight[
                "maxweight_hgv_supported_way_n"
            ]
            self.maxweight_hgv_unlimited_way_n = metadata_maxweight[
                "maxweight_hgv_unlimited_way_n"
            ]
            self.maxweight_hgv_unsupported_way_n = metadata_maxweight[
                "maxweight_hgv_unsupported_way_n"
            ]
            self.maxweight_hgv_segment_n = metadata_maxweight[
                "maxweight_hgv_segment_n"
            ]
        else:
            self.maxweight_way_n = 0
            self.maxweight_supported_way_n = 0
            self.maxweight_unlimited_way_n = 0
            self.maxweight_unsupported_way_n = 0
            self.maxweight_segment_n = 0
            self.maxweight_hgv_way_n = 0
            self.maxweight_hgv_supported_way_n = 0
            self.maxweight_hgv_unlimited_way_n = 0
            self.maxweight_hgv_unsupported_way_n = 0
            self.maxweight_hgv_segment_n = 0
            self.maxweightrating_hgv_way_n = 0
            self.maxweightrating_hgv_supported_way_n = 0
            self.maxweightrating_hgv_unlimited_way_n = 0
            self.maxweightrating_hgv_unsupported_way_n = 0
            self.maxweightrating_hgv_segment_n = 0
            if self.has_maxweight_profiles:
                hgv_columns = (
                    ", MAX(maxweight_hgv_status), MAX(maxweight_hgv_t)"
                    if self.has_hgv_maxweight_profiles
                    else ""
                )
                rows = self.connection.execute(
                    f"""
                    SELECT way_id, MAX(maxweight_status), MAX(maxweight_t){hgv_columns}
                    FROM edges
                    GROUP BY way_id
                    """
                )
                for row in rows:
                    _way_id, status, _limit_t = row[:3]
                    hgv_status = row[3] if self.has_hgv_maxweight_profiles else None
                    status = str(status)
                    if status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                        self.maxweight_way_n += 1
                        if status == MAXWEIGHT_STATUS_SUPPORTED:
                            self.maxweight_supported_way_n += 1
                        elif status == MAXWEIGHT_STATUS_UNLIMITED:
                            self.maxweight_unlimited_way_n += 1
                        else:
                            self.maxweight_unsupported_way_n += 1
                    hgv_status = str(hgv_status or MAXWEIGHT_STATUS_NOT_PROVIDED)
                    if hgv_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                        self.maxweight_hgv_way_n += 1
                        if hgv_status == MAXWEIGHT_STATUS_SUPPORTED:
                            self.maxweight_hgv_supported_way_n += 1
                        elif hgv_status == MAXWEIGHT_STATUS_UNLIMITED:
                            self.maxweight_hgv_unlimited_way_n += 1
                        else:
                            self.maxweight_hgv_unsupported_way_n += 1
                self.maxweight_segment_n = int(
                    self.connection.execute(
                        """
                        SELECT COUNT(*) FROM edges
                        WHERE maxweight_status != ?
                        """,
                        (MAXWEIGHT_STATUS_NOT_PROVIDED,),
                    ).fetchone()[0]
                )
                if self.has_hgv_maxweight_profiles:
                    self.maxweight_hgv_segment_n = int(
                        self.connection.execute(
                            """
                            SELECT COUNT(*) FROM edges
                            WHERE maxweight_hgv_status != ?
                            """,
                            (MAXWEIGHT_STATUS_NOT_PROVIDED,),
                        ).fetchone()[0]
                    )
        self.maxweightrating_hgv_way_n = 0
        self.maxweightrating_hgv_supported_way_n = 0
        self.maxweightrating_hgv_unlimited_way_n = 0
        self.maxweightrating_hgv_unsupported_way_n = 0
        self.maxweightrating_hgv_segment_n = 0
        if self.has_hgv_maxweightrating_profiles:
            if metadata_maxweightrating_hgv_available:
                self.maxweightrating_hgv_way_n = metadata_maxweightrating_hgv[
                    "maxweightrating_hgv_way_n"
                ]
                self.maxweightrating_hgv_supported_way_n = metadata_maxweightrating_hgv[
                    "maxweightrating_hgv_supported_way_n"
                ]
                self.maxweightrating_hgv_unlimited_way_n = metadata_maxweightrating_hgv[
                    "maxweightrating_hgv_unlimited_way_n"
                ]
                self.maxweightrating_hgv_unsupported_way_n = metadata_maxweightrating_hgv[
                    "maxweightrating_hgv_unsupported_way_n"
                ]
                self.maxweightrating_hgv_segment_n = metadata_maxweightrating_hgv[
                    "maxweightrating_hgv_segment_n"
                ]
            else:
                rows = self.connection.execute(
                    """
                    SELECT way_id, MAX(maxweightrating_hgv_status)
                    FROM edges
                    GROUP BY way_id
                    """
                )
                for _way_id, status in rows:
                    status = str(status)
                    if status == MAXWEIGHT_STATUS_NOT_PROVIDED:
                        continue
                    self.maxweightrating_hgv_way_n += 1
                    suffix = {
                        MAXWEIGHT_STATUS_SUPPORTED: "supported_way_n",
                        MAXWEIGHT_STATUS_UNLIMITED: "unlimited_way_n",
                    }.get(status, "unsupported_way_n")
                    setattr(
                        self,
                        f"maxweightrating_hgv_{suffix}",
                        getattr(self, f"maxweightrating_hgv_{suffix}") + 1,
                    )
                self.maxweightrating_hgv_segment_n = int(
                    self.connection.execute(
                        """
                        SELECT COUNT(*) FROM edges
                        WHERE maxweightrating_hgv_status != ?
                        """,
                        (MAXWEIGHT_STATUS_NOT_PROVIDED,),
                    ).fetchone()[0]
                )
        self.hgv_destination_way_n = 0
        self.hgv_destination_supported_way_n = 0
        self.hgv_destination_unsupported_way_n = 0
        self.hgv_destination_segment_n = 0
        self.hgv_destination_by_way: dict[str, tuple[dict[str, str], ...]] = {}
        if self.has_hgv_destination_profiles:
            if metadata_hgv_destination_available:
                self.hgv_destination_way_n = metadata_hgv_destination[
                    "hgv_destination_way_n"
                ]
                self.hgv_destination_supported_way_n = metadata_hgv_destination[
                    "hgv_destination_supported_way_n"
                ]
                self.hgv_destination_unsupported_way_n = metadata_hgv_destination[
                    "hgv_destination_unsupported_way_n"
                ]
                self.hgv_destination_segment_n = metadata_hgv_destination[
                    "hgv_destination_segment_n"
                ]
            for way_id, raw in self.connection.execute(
                "SELECT way_id, hgv_destination_json FROM edges "
                "WHERE hgv_destination_json != '[]'"
            ):
                try:
                    entries = json.loads(raw or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    entries = []
                if not isinstance(entries, list):
                    entries = []
                normalized = tuple(
                    entry for entry in entries
                    if isinstance(entry, dict)
                )
                if normalized:
                    self.hgv_destination_by_way[str(way_id)] = normalized
            if not metadata_hgv_destination_available:
                self.hgv_destination_way_n = len(self.hgv_destination_by_way)
                self.hgv_destination_supported_way_n = sum(
                    all(
                        str(entry.get("status"))
                        in {MAXWEIGHT_STATUS_SUPPORTED, MAXWEIGHT_STATUS_UNLIMITED}
                        for entry in entries
                    )
                    for entries in self.hgv_destination_by_way.values()
                )
                self.hgv_destination_unsupported_way_n = (
                    self.hgv_destination_way_n
                    - self.hgv_destination_supported_way_n
                )
                self.hgv_destination_segment_n = int(
                    self.connection.execute(
                        "SELECT COUNT(*) FROM edges WHERE hgv_destination_json != '[]'"
                    ).fetchone()[0]
                )
        if self.has_maxheight_profiles and metadata_maxheight_available:
            self.maxheight_way_n = metadata_maxheight["maxheight_way_n"]
            self.maxheight_supported_way_n = metadata_maxheight[
                "maxheight_supported_way_n"
            ]
            self.maxheight_unlimited_way_n = metadata_maxheight[
                "maxheight_unlimited_way_n"
            ]
            self.maxheight_unsupported_way_n = metadata_maxheight[
                "maxheight_unsupported_way_n"
            ]
            self.maxheight_segment_n = metadata_maxheight["maxheight_segment_n"]
            self.maxheight_physical_way_n = metadata_maxheight[
                "maxheight_physical_way_n"
            ]
            self.maxheight_physical_supported_way_n = metadata_maxheight[
                "maxheight_physical_supported_way_n"
            ]
            self.maxheight_physical_unlimited_way_n = metadata_maxheight[
                "maxheight_physical_unlimited_way_n"
            ]
            self.maxheight_physical_unsupported_way_n = metadata_maxheight[
                "maxheight_physical_unsupported_way_n"
            ]
            self.maxheight_physical_segment_n = metadata_maxheight[
                "maxheight_physical_segment_n"
            ]
        else:
            self.maxheight_way_n = 0
            self.maxheight_supported_way_n = 0
            self.maxheight_unlimited_way_n = 0
            self.maxheight_unsupported_way_n = 0
            self.maxheight_segment_n = 0
            self.maxheight_physical_way_n = 0
            self.maxheight_physical_supported_way_n = 0
            self.maxheight_physical_unlimited_way_n = 0
            self.maxheight_physical_unsupported_way_n = 0
            self.maxheight_physical_segment_n = 0
            if self.has_maxheight_profiles:
                rows = self.connection.execute(
                    """
                    SELECT
                        way_id,
                        MAX(maxheight_status), MAX(maxheight_m),
                        MAX(maxheight_physical_status), MAX(maxheight_physical_m)
                    FROM edges
                    GROUP BY way_id
                    """
                )
                for row in rows:
                    (
                        _way_id,
                        status,
                        _limit_m,
                        physical_status,
                        _physical_limit_m,
                    ) = row
                    status = str(status)
                    physical_status = str(physical_status)
                    if status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                        self.maxheight_way_n += 1
                        if status == MAXWEIGHT_STATUS_SUPPORTED:
                            self.maxheight_supported_way_n += 1
                        elif status == MAXWEIGHT_STATUS_UNLIMITED:
                            self.maxheight_unlimited_way_n += 1
                        else:
                            self.maxheight_unsupported_way_n += 1
                    if physical_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                        self.maxheight_physical_way_n += 1
                        if physical_status == MAXWEIGHT_STATUS_SUPPORTED:
                            self.maxheight_physical_supported_way_n += 1
                        elif physical_status == MAXWEIGHT_STATUS_UNLIMITED:
                            self.maxheight_physical_unlimited_way_n += 1
                        else:
                            self.maxheight_physical_unsupported_way_n += 1
                self.maxheight_segment_n = int(
                    self.connection.execute(
                        """
                        SELECT COUNT(*) FROM edges
                        WHERE maxheight_status != ?
                        """,
                        (MAXWEIGHT_STATUS_NOT_PROVIDED,),
                    ).fetchone()[0]
                )
                self.maxheight_physical_segment_n = int(
                    self.connection.execute(
                        """
                        SELECT COUNT(*) FROM edges
                        WHERE maxheight_physical_status != ?
                        """,
                        (MAXWEIGHT_STATUS_NOT_PROVIDED,),
                    ).fetchone()[0]
                )
        for dimension in ("maxwidth", "maxlength", "maxaxleload"):
            for suffix in (
                "way_n",
                "supported_way_n",
                "unlimited_way_n",
                "unsupported_way_n",
                "segment_n",
            ):
                setattr(self, f"{dimension}_{suffix}", 0)
        if self.has_vehicle_dimension_profiles:
            for dimension in ("maxwidth", "maxlength", "maxaxleload"):
                if metadata_dimensions_available:
                    values = metadata_dimensions[dimension]
                    for suffix, value in values.items():
                        setattr(self, suffix, value)
                    continue
                value_column = "maxaxleload_t" if dimension == "maxaxleload" else f"{dimension}_m"
                status_column = f"{dimension}_status"
                rows = self.connection.execute(
                    f"""
                    SELECT way_id, MAX({status_column}), MAX({value_column})
                    FROM edges
                    GROUP BY way_id
                    """
                )
                for _way_id, status, _limit in rows:
                    status = str(status)
                    if status == MAXWEIGHT_STATUS_NOT_PROVIDED:
                        continue
                    setattr(
                        self,
                        f"{dimension}_way_n",
                        getattr(self, f"{dimension}_way_n") + 1,
                    )
                    suffix = {
                        MAXWEIGHT_STATUS_SUPPORTED: "supported_way_n",
                        MAXWEIGHT_STATUS_UNLIMITED: "unlimited_way_n",
                    }.get(status, "unsupported_way_n")
                    setattr(
                        self,
                        f"{dimension}_{suffix}",
                        getattr(self, f"{dimension}_{suffix}") + 1,
                    )
                setattr(
                    self,
                    f"{dimension}_segment_n",
                    int(
                        self.connection.execute(
                            f"SELECT COUNT(*) FROM edges WHERE {status_column} != ?",
                            (MAXWEIGHT_STATUS_NOT_PROVIDED,),
                        ).fetchone()[0]
                    ),
                )
        self.maxspeed_way_n = 0
        self.maxspeed_supported_way_n = 0
        self.maxspeed_unlimited_way_n = 0
        self.maxspeed_unsupported_way_n = 0
        self.maxspeed_segment_n = 0
        self.maxspeed_conditional_way_n = 0
        self.maxspeed_conditional_supported_way_n = 0
        self.maxspeed_conditional_unsupported_way_n = 0
        self.maxspeed_conditional_segment_n = 0
        self.maxspeed_conditional_rule_n = 0
        self.maxspeed_conditional_by_way: dict[
            str, tuple[tuple[float | None, WeeklySchedule], ...]
        ] = {}
        if self.has_maxspeed_profiles and metadata_maxspeed_available:
            self.maxspeed_way_n = metadata_maxspeed["maxspeed_way_n"]
            self.maxspeed_supported_way_n = metadata_maxspeed[
                "maxspeed_supported_way_n"
            ]
            self.maxspeed_unlimited_way_n = metadata_maxspeed[
                "maxspeed_unlimited_way_n"
            ]
            self.maxspeed_unsupported_way_n = metadata_maxspeed[
                "maxspeed_unsupported_way_n"
            ]
            self.maxspeed_segment_n = metadata_maxspeed["maxspeed_segment_n"]
            self.maxspeed_conditional_way_n = metadata_maxspeed[
                "maxspeed_conditional_way_n"
            ]
        elif self.has_maxspeed_profiles:
            rows = self.connection.execute(
                """
                SELECT way_id, MAX(maxspeed_status), MAX(maxspeed_kmh)
                FROM edges
                GROUP BY way_id
                """
            )
            for _way_id, status, _limit_kmh in rows:
                status = str(status)
                if status == MAXWEIGHT_STATUS_NOT_PROVIDED:
                    continue
                self.maxspeed_way_n += 1
                suffix = {
                    MAXWEIGHT_STATUS_SUPPORTED: "supported_way_n",
                    MAXWEIGHT_STATUS_UNLIMITED: "unlimited_way_n",
                }.get(status, "unsupported_way_n")
                setattr(
                    self,
                    f"maxspeed_{suffix}",
                    getattr(self, f"maxspeed_{suffix}") + 1,
                )
            self.maxspeed_segment_n = int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM edges WHERE maxspeed_status != ?",
                    (MAXWEIGHT_STATUS_NOT_PROVIDED,),
                ).fetchone()[0]
            )
        conditional_way_status: dict[str, str] = {}
        if self.has_maxspeed_conditional_profiles:
            rows = self.connection.execute(
                """
                SELECT way_id, MAX(maxspeed_conditional_json)
                FROM edges
                WHERE maxspeed_conditional_json != '[]'
                GROUP BY way_id
                """
            )
            for way_id, raw_json in rows:
                try:
                    entries = json.loads(raw_json)
                except (TypeError, ValueError, json.JSONDecodeError):
                    entries = ()
                if not isinstance(entries, list) or not entries:
                    continue
                has_unsupported = False
                schedules: list[tuple[float | None, WeeklySchedule]] = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        has_unsupported = True
                        continue
                    status = str(entry.get("status", MAXWEIGHT_STATUS_UNSUPPORTED))
                    condition = str(entry.get("condition", ""))
                    schedule = parse_weekly_schedule(condition, allow_calendar=True)
                    if schedule is None or status not in {
                        MAXWEIGHT_STATUS_SUPPORTED,
                        MAXWEIGHT_STATUS_UNLIMITED,
                    }:
                        has_unsupported = True
                        continue
                    speed_kmh = entry.get("speed_kmh")
                    if status == MAXWEIGHT_STATUS_SUPPORTED:
                        try:
                            speed_kmh = float(speed_kmh)
                        except (TypeError, ValueError):
                            has_unsupported = True
                            continue
                        if not math.isfinite(speed_kmh) or speed_kmh < 0:
                            has_unsupported = True
                            continue
                    else:
                        speed_kmh = None
                    schedules.append((speed_kmh, schedule))
                way_key = str(way_id)
                if schedules:
                    self.maxspeed_conditional_by_way[way_key] = tuple(schedules)
                    self.maxspeed_conditional_rule_n += len(schedules)
                conditional_way_status[way_key] = (
                    MAXWEIGHT_STATUS_UNSUPPORTED
                    if has_unsupported
                    else MAXWEIGHT_STATUS_SUPPORTED
                )
            if metadata_maxspeed_conditional_available and metadata_maxspeed_available:
                self.maxspeed_conditional_supported_way_n = metadata_maxspeed[
                    "maxspeed_conditional_supported_way_n"
                ]
                self.maxspeed_conditional_unsupported_way_n = metadata_maxspeed[
                    "maxspeed_conditional_unsupported_way_n"
                ]
                self.maxspeed_conditional_segment_n = metadata_maxspeed[
                    "maxspeed_conditional_segment_n"
                ]
            else:
                self.maxspeed_conditional_supported_way_n = sum(
                    status == MAXWEIGHT_STATUS_SUPPORTED
                    for status in conditional_way_status.values()
                )
                self.maxspeed_conditional_unsupported_way_n = sum(
                    status == MAXWEIGHT_STATUS_UNSUPPORTED
                    for status in conditional_way_status.values()
                )
                self.maxspeed_conditional_way_n = len(conditional_way_status)
                self.maxspeed_conditional_segment_n = int(
                    self.connection.execute(
                        "SELECT COUNT(*) FROM edges WHERE maxspeed_conditional_json != '[]'"
                    ).fetchone()[0]
                )
        self.oneway_conditional_way_n = 0
        self.oneway_conditional_supported_way_n = 0
        self.oneway_conditional_unsupported_way_n = 0
        self.oneway_conditional_segment_n = 0
        self.oneway_conditional_rule_n = 0
        self.oneway_conditional_by_way: dict[
            str, tuple[tuple[str, WeeklySchedule], ...]
        ] = {}
        self.oneway_conditional_unsupported_by_way: set[str] = set()
        if self.has_oneway_conditional_profiles:
            conditional_way_status: dict[str, str] = {}
            rows = self.connection.execute(
                """
                SELECT way_id, MAX(oneway_conditional_json)
                FROM edges
                WHERE oneway_conditional_json != '[]'
                GROUP BY way_id
                """
            )
            for way_id, raw_json in rows:
                try:
                    entries = json.loads(raw_json)
                except (TypeError, ValueError, json.JSONDecodeError):
                    entries = ()
                if not isinstance(entries, list) or not entries:
                    continue
                has_unsupported = False
                schedules: list[tuple[str, WeeklySchedule]] = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        has_unsupported = True
                        continue
                    mode = str(entry.get("mode", "")).lower()
                    status = str(entry.get("status", MAXWEIGHT_STATUS_UNSUPPORTED))
                    condition = str(entry.get("condition", ""))
                    schedule = parse_weekly_schedule(condition, allow_calendar=True)
                    if (
                        mode not in {"yes", "no"}
                        or status != MAXWEIGHT_STATUS_SUPPORTED
                        or schedule is None
                    ):
                        has_unsupported = True
                        continue
                    schedules.append((mode, schedule))
                way_key = str(way_id)
                if schedules:
                    self.oneway_conditional_by_way[way_key] = tuple(schedules)
                    self.oneway_conditional_rule_n += len(schedules)
                conditional_way_status[way_key] = (
                    MAXWEIGHT_STATUS_UNSUPPORTED
                    if has_unsupported
                    else MAXWEIGHT_STATUS_SUPPORTED
                )
                if has_unsupported:
                    self.oneway_conditional_unsupported_by_way.add(way_key)
            if metadata_oneway_conditional_available:
                self.oneway_conditional_way_n = metadata_oneway_conditional[
                    "oneway_conditional_way_n"
                ]
                self.oneway_conditional_supported_way_n = metadata_oneway_conditional[
                    "oneway_conditional_supported_way_n"
                ]
                self.oneway_conditional_unsupported_way_n = metadata_oneway_conditional[
                    "oneway_conditional_unsupported_way_n"
                ]
                self.oneway_conditional_segment_n = metadata_oneway_conditional[
                    "oneway_conditional_segment_n"
                ]
            else:
                self.oneway_conditional_way_n = len(conditional_way_status)
                self.oneway_conditional_supported_way_n = sum(
                    status == MAXWEIGHT_STATUS_SUPPORTED
                    for status in conditional_way_status.values()
                )
                self.oneway_conditional_unsupported_way_n = sum(
                    status == MAXWEIGHT_STATUS_UNSUPPORTED
                    for status in conditional_way_status.values()
                )
                self.oneway_conditional_segment_n = int(
                    self.connection.execute(
                        "SELECT COUNT(*) FROM edges WHERE oneway_conditional_json != '[]'"
                    ).fetchone()[0]
                )
        turn_columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(turn_restrictions)")
        }
        conditional_columns = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(conditional_turn_restrictions)")
        }
        conditional_access_columns = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(conditional_access)")
        }
        turn_table_exists = bool(self.has_way_ids and turn_columns)
        conditional_table_exists = bool(self.has_way_ids and conditional_columns)
        conditional_access_table_exists = bool(
            self.has_way_ids and conditional_access_columns
        )
        self.has_via_way_json = "via_way_json" in turn_columns
        ferry_columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(ferry_edges)")
        }
        ferry_table_exists = bool(ferry_columns)
        ferry_way_id_exists = "way_id" in ferry_columns
        self.forbidden_turns: dict[tuple[tuple[str, ...], str], set[str]] = defaultdict(set)
        self.only_turns: dict[tuple[tuple[str, ...], str], set[str]] = defaultdict(set)
        self.conditional_forbidden_turns: dict[
            tuple[tuple[str, ...], str], list[tuple[str, WeeklySchedule]]
        ] = defaultdict(list)
        self.conditional_only_turns: dict[
            tuple[tuple[str, ...], str], list[tuple[str, WeeklySchedule]]
        ] = defaultdict(list)
        self.conditional_forbidden_weight_turns: dict[
            tuple[tuple[str, ...], str], list[tuple[str, tuple[str, float]]]
        ] = defaultdict(list)
        self.conditional_only_weight_turns: dict[
            tuple[tuple[str, ...], str], list[tuple[str, tuple[str, float]]]
        ] = defaultdict(list)
        self._unconditional_prefixes: set[tuple[str, ...]] = set()
        self._conditional_prefixes: set[tuple[str, ...]] = set()
        self.restriction_prefixes: set[tuple[str, ...]] = set()
        def load_rows(table: str, columns: set[str], conditional: bool) -> None:
            if not columns:
                return
            selected = "via_node, from_way, to_way, kind"
            has_via_json = "via_way_json" in columns
            has_condition = "condition" in columns
            if has_via_json:
                selected += ", via_way_json"
            if has_condition:
                selected += ", condition"
            for row in self.connection.execute(f"SELECT {selected} FROM {table}"):
                via_node, from_way, to_way, kind = row[:4]
                offset = 4
                if has_via_json:
                    try:
                        via_ways = tuple(str(value) for value in json.loads(row[offset]))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        via_ways = ()
                    offset += 1
                else:
                    via_ways = ()
                prefix = (str(from_way), *via_ways)
                key = (prefix, str(via_node))
                if conditional:
                    condition = str(row[offset]) if has_condition else ""
                    schedule = parse_weekly_schedule(condition)
                    weight_condition = parse_vehicle_weight_condition(condition)
                    if schedule is None and weight_condition is None:
                        continue
                    if schedule is not None:
                        target = (
                            self.conditional_only_turns
                            if kind == "only"
                            else self.conditional_forbidden_turns
                        )
                        target[key].append((str(to_way), schedule))
                    else:
                        target = (
                            self.conditional_only_weight_turns
                            if kind == "only"
                            else self.conditional_forbidden_weight_turns
                        )
                        target[key].append((str(to_way), weight_condition))
                    prefixes = self._conditional_prefixes
                else:
                    target = self.only_turns if kind == "only" else self.forbidden_turns
                    target[key].add(str(to_way))
                    prefixes = self._unconditional_prefixes
                for length in range(1, len(prefix) + 1):
                    prefixes.add(prefix[:length])

        if turn_table_exists:
            load_rows("turn_restrictions", turn_columns, conditional=False)
        if conditional_table_exists:
            load_rows("conditional_turn_restrictions", conditional_columns, conditional=True)
        self.conditional_restriction_n = sum(
            len(values)
            for values in self.conditional_forbidden_turns.values()
        ) + sum(len(values) for values in self.conditional_only_turns.values())
        self.conditional_weight_restriction_n = sum(
            len(values)
            for values in self.conditional_forbidden_weight_turns.values()
        ) + sum(len(values) for values in self.conditional_only_weight_turns.values())
        self.conditional_restriction_n += self.conditional_weight_restriction_n
        self.conditional_access_by_way: dict[
            tuple[str, str], list[ConditionalAccessRule]
        ] = defaultdict(list)
        self.conditional_access_vehicle_class_n: dict[str, int] = {
            vehicle_class: 0 for vehicle_class in VEHICLE_CLASSES
        }
        self.conditional_access_direction_n: dict[str, int] = {
            direction: 0 for direction in CONDITIONAL_ACCESS_DIRECTIONS
        }
        self.conditional_access_weight_n = 0
        self.conditional_access_multiclause_n = int(
            metadata.get("conditional_access_multiclause_n", 0)
        )
        self.conditional_access_unsupported_n = metadata_unsupported_n
        self.conditional_access_excluded_n = metadata_excluded_n
        if conditional_access_table_exists:
            has_access_mode = "mode" in conditional_access_columns
            has_access_vehicle_class = "vehicle_class" in conditional_access_columns
            has_access_direction = "direction" in conditional_access_columns
            has_access_rule_n = "rule_n" in conditional_access_columns
            selected = "way_id, condition"
            if has_access_mode:
                selected += ", mode"
            if has_access_vehicle_class:
                selected += ", vehicle_class"
            if has_access_direction:
                selected += ", direction"
            if has_access_rule_n:
                selected += ", rule_n"
            order = "way_id, direction"
            if has_access_rule_n:
                order += ", rule_n"
            for row in self.connection.execute(
                f"SELECT {selected} FROM conditional_access ORDER BY {order}"
            ):
                way_id, condition = row[:2]
                offset = 2
                mode = str(row[offset]).lower() if has_access_mode else "allow"
                if has_access_mode:
                    offset += 1
                vehicle_class = (
                    str(row[offset]).lower()
                    if has_access_vehicle_class
                    else "general"
                )
                if has_access_vehicle_class:
                    offset += 1
                direction = (
                    str(row[offset]).lower()
                    if has_access_direction
                    else "both"
                )
                if mode not in {"allow", "deny"} or (
                    vehicle_class not in VEHICLE_CLASSES
                ) or direction not in CONDITIONAL_ACCESS_DIRECTIONS:
                    self.conditional_access_unsupported_n += 1
                    self.conditional_access_excluded_n += 1
                    continue
                schedule = parse_weekly_schedule(str(condition))
                if schedule is None:
                    schedule = parse_weekly_schedule(str(condition), allow_calendar=True)
                weight_condition = (
                    None if schedule is not None
                    else parse_vehicle_weight_condition(str(condition))
                )
                if schedule is None and weight_condition is None:
                    self.conditional_access_unsupported_n += 1
                    self.conditional_access_excluded_n += 1
                else:
                    self.conditional_access_by_way[(str(way_id), direction)].append(
                        ConditionalAccessRule(
                            mode=mode,
                            schedule=schedule or WeeklySchedule(()),
                            vehicle_class=vehicle_class,
                            direction=direction,
                            weight_condition=weight_condition,
                        )
                    )
                    self.conditional_access_vehicle_class_n[vehicle_class] += 1
                    self.conditional_access_direction_n[direction] += 1
                    if weight_condition is not None:
                        self.conditional_access_weight_n += 1
        self.conditional_access_n = sum(
            len(rules) for rules in self.conditional_access_by_way.values()
        )
        if not self.conditional_access_multiclause_n:
            self.conditional_access_multiclause_n = sum(
                len(rules) > 1 for rules in self.conditional_access_by_way.values()
            )
        self.turn_restriction_n = sum(len(values) for values in self.forbidden_turns.values()) + sum(
            len(values) for values in self.only_turns.values()
        )
        self.restriction_nodes = {
            via_node
            for _prefix, via_node in self.forbidden_turns
        } | {
            via_node
            for _prefix, via_node in self.only_turns
        } | {
            via_node
            for _prefix, via_node in self.conditional_forbidden_turns
        } | {
            via_node
            for _prefix, via_node in self.conditional_only_turns
        } | {
            via_node
            for _prefix, via_node in self.conditional_forbidden_weight_turns
        } | {
            via_node
            for _prefix, via_node in self.conditional_only_weight_turns
        }
        self.departure: datetime | None = None
        self.vehicle_weight_t: float | None = None
        self.vehicle_height_m: float | None = None
        self.vehicle_width_m: float | None = None
        self.vehicle_length_m: float | None = None
        self.vehicle_axleload_t: float | None = None
        self.vehicle_class = "general"
        self.speed_mps = 50.0 / 3.6
        self.include_ferries = False
        self.ferry_edge_count = int(
            self.connection.execute("SELECT COUNT(*) FROM ferry_edges").fetchone()[0]
        ) if ferry_table_exists else 0
        self.ferry_way_ids = {
            str(row[0])
            for row in self.connection.execute("SELECT DISTINCT way_id FROM ferry_edges")
        } if ferry_table_exists and ferry_way_id_exists else set()
        self.ferry_segment_count_by_way = {
            str(row[0]): int(row[1])
            for row in self.connection.execute(
                "SELECT way_id, COUNT(*) FROM ferry_edges GROUP BY way_id"
            )
        } if ferry_table_exists and ferry_way_id_exists else {}
        self.ferry_schedule_by_way: dict[str, WeeklySchedule] = {}
        self.ferry_duration_by_way: dict[str, float] = {}
        self.ferry_schedule_path = path.parent / "ferry_schedules.json"
        self.ferry_schedule_contract_status = "not_provided"
        self.ferry_schedule_unsupported_n = 0
        self.public_holiday_path = path.parent / "public_holidays.json"
        self.public_holidays: frozenset[date] | None = None
        self.public_holiday_contract_status = "not_provided"
        self.public_holiday_n = 0
        self.public_holiday_min_date: str | None = None
        self.public_holiday_max_date: str | None = None
        self.ferry_public_holiday_schedule_n = 0
        self.last_ferry_wait_s = 0.0
        self.last_ferry_wait_n = 0
        self.last_ferry_way_ids: tuple[str, ...] = ()
        self.last_ferry_distance_m = 0.0
        self.last_ferry_crossing_s = 0.0
        self.last_ferry_edge_n = 0
        if self.ferry_schedule_path.is_symlink():
            raise ValueError("ferry schedule contract must not be a symlink")
        if self.public_holiday_path.is_symlink():
            raise ValueError("public holiday contract must not be a symlink")
        (
            self.public_holiday_contract_status,
            parsed_holidays,
        ) = _load_public_holiday_contract(self.public_holiday_path)
        if self.public_holiday_contract_status == "available":
            self.public_holidays = parsed_holidays
            self.public_holiday_n = len(parsed_holidays)
            (
                self.public_holiday_min_date,
                self.public_holiday_max_date,
            ) = _public_holiday_bounds(parsed_holidays)
        if self.ferry_schedule_path.is_file():
            try:
                payload = json.loads(self.ferry_schedule_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"ferry schedule contract is unreadable: {exc}") from exc
            if not isinstance(payload, dict) or payload.get("contract") != FERRY_SCHEDULE_CONTRACT:
                raise ValueError(
                    f"ferry schedule contract must be {FERRY_SCHEDULE_CONTRACT}"
                )
            entries = payload.get("schedules", [])
            if not isinstance(entries, list):
                raise ValueError("ferry schedule contract schedules must be a list")
            self.ferry_schedule_contract_status = "available"
            for entry in entries:
                if not isinstance(entry, dict) or not str(entry.get("way_id", "")).strip():
                    self.ferry_schedule_unsupported_n += 1
                    continue
                way_id = str(entry["way_id"]).strip()
                opening_hours = str(entry.get("opening_hours", "")).strip()
                if opening_hours:
                    schedule = parse_weekly_schedule(opening_hours, allow_calendar=True)
                    if schedule is None:
                        self.ferry_schedule_unsupported_n += 1
                    else:
                        self.ferry_schedule_by_way[way_id] = schedule
                        if schedule.requires_public_holiday_calendar:
                            self.ferry_public_holiday_schedule_n += 1
                duration = parse_duration_seconds(
                    entry.get("duration_s", entry.get("duration"))
                )
                if duration is not None:
                    self.ferry_duration_by_way[way_id] = duration
        self.ferry_schedule_n = len(self.ferry_schedule_by_way)
        self.ferry_duration_n = len(self.ferry_duration_by_way)
        self.ferry_schedules_modeled = self.ferry_schedule_contract_status == "available"
        self.has_conditional_restrictions = bool(
            conditional_table_exists and self.conditional_restriction_n
        )
        self.set_time_profile(None)

    def set_time_profile(
        self,
        departure: datetime | None,
        speed_kmh: float = 50.0,
        include_ferries: bool = False,
        vehicle_weight_t: float | None = None,
        vehicle_rating_t: float | None = None,
        vehicle_height_m: float | None = None,
        vehicle_width_m: float | None = None,
        vehicle_length_m: float | None = None,
        vehicle_axleload_t: float | None = None,
        vehicle_class: str = "general",
        allow_hgv_destination: bool = False,
    ) -> None:
        """Configure optional time, ferry, vehicle, and destination profiles."""
        if not math.isfinite(speed_kmh) or speed_kmh <= 0:
            raise ValueError("routing speed must be a finite positive number")
        if vehicle_weight_t is not None and (
            not math.isfinite(vehicle_weight_t) or vehicle_weight_t <= 0
        ):
            raise ValueError("vehicle weight must be a finite positive number of tonnes")
        if vehicle_rating_t is not None and (
            not math.isfinite(vehicle_rating_t) or vehicle_rating_t <= 0
        ):
            raise ValueError(
                "vehicle rating must be a finite positive number of tonnes"
            )
        if vehicle_height_m is not None and (
            not math.isfinite(vehicle_height_m) or vehicle_height_m <= 0
        ):
            raise ValueError("vehicle height must be a finite positive number of metres")
        for value, label, unit in (
            (vehicle_width_m, "vehicle width", "metres"),
            (vehicle_length_m, "vehicle length", "metres"),
            (vehicle_axleload_t, "vehicle axle load", "tonnes"),
        ):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{label} must be a finite positive number of {unit}")
        vehicle_class = validate_vehicle_class(vehicle_class)
        self.departure = departure
        self.speed_mps = speed_kmh / 3.6
        self.include_ferries = bool(include_ferries and self.ferry_edge_count)
        self.vehicle_weight_t = vehicle_weight_t
        self.vehicle_rating_t = vehicle_rating_t
        self.vehicle_height_m = vehicle_height_m
        self.vehicle_width_m = vehicle_width_m
        self.vehicle_length_m = vehicle_length_m
        self.vehicle_axleload_t = vehicle_axleload_t
        self.vehicle_class = vehicle_class
        self.allow_hgv_destination = bool(allow_hgv_destination)
        self.last_ferry_wait_s = 0.0
        self.last_ferry_wait_n = 0
        self.last_ferry_way_ids = ()
        self.last_ferry_distance_m = 0.0
        self.last_ferry_crossing_s = 0.0
        self.last_ferry_edge_n = 0
        self.restriction_prefixes = set(self._unconditional_prefixes)
        if departure is not None or vehicle_weight_t is not None:
            self.restriction_prefixes.update(self._conditional_prefixes)
        self.max_restriction_prefix_n = max(
            (len(prefix) for prefix in self.restriction_prefixes),
            default=0,
        )
        self.has_turn_restrictions = bool(
            self.restriction_prefixes
            and (
                self.turn_restriction_n
                or (departure is not None and self.conditional_restriction_n)
                or (
                    vehicle_weight_t is not None
                    and self.conditional_weight_restriction_n
                )
            )
        )

    def _edge_constraint_filter_sql(self) -> tuple[str, tuple[float, ...]]:
        """Return SQL predicates for the active weight and dimension profiles."""
        predicates: list[str] = []
        parameters: list[float] = []
        if self.has_maxweight_profiles and self.vehicle_weight_t is not None:
            predicates.append(
                " AND (maxweight_status IN ('not_provided', 'unlimited')"
                " OR (maxweight_status = 'supported' AND maxweight_t >= ?))"
            )
            parameters.append(self.vehicle_weight_t)
            if self.vehicle_class == "hgv" and self.has_hgv_maxweight_profiles:
                predicates.append(
                    " AND (maxweight_hgv_status IN ('not_provided', 'unlimited')"
                    " OR (maxweight_hgv_status = 'supported' AND maxweight_hgv_t >= ?)"
                    + (
                        " OR (hgv_destination_json LIKE '%\"kind\":\"maxweight_hgv_exception\"%')"
                        if self.allow_hgv_destination
                        and self.has_hgv_destination_profiles
                        else ""
                    )
                    + ")"
                )
                parameters.append(self.vehicle_weight_t)
        if (
            self.vehicle_class == "hgv"
            and self.has_hgv_maxweightrating_profiles
            and self.vehicle_rating_t is not None
        ):
            predicates.append(
                " AND (maxweightrating_hgv_status IN ('not_provided', 'unlimited')"
                " OR (maxweightrating_hgv_status = 'supported'"
                " AND maxweightrating_hgv_t >= ?)"
                + (
                    " OR (hgv_destination_json LIKE '%\"kind\":\"maxweightrating_hgv_exception\"%')"
                    if self.allow_hgv_destination
                    and self.has_hgv_destination_profiles
                    else ""
                )
                + ")"
            )
            parameters.append(self.vehicle_rating_t)
        if self.has_maxheight_profiles and self.vehicle_height_m is not None:
            predicates.extend(
                [
                    (
                        " AND (maxheight_status IN ('not_provided', 'unlimited')"
                        " OR (maxheight_status = 'supported' AND maxheight_m >= ?))"
                    ),
                    (
                        " AND (maxheight_physical_status IN ('not_provided', 'unlimited')"
                        " OR (maxheight_physical_status = 'supported' AND maxheight_physical_m >= ?))"
                    ),
                ]
            )
            parameters.extend([self.vehicle_height_m, self.vehicle_height_m])
        if self.has_vehicle_dimension_profiles:
            dimension_profiles = (
                ("maxwidth", "maxwidth_m", self.vehicle_width_m),
                ("maxlength", "maxlength_m", self.vehicle_length_m),
                ("maxaxleload", "maxaxleload_t", self.vehicle_axleload_t),
            )
            for _name, value_column, profile in dimension_profiles:
                if profile is None:
                    continue
                status_column = f"{_name}_status"
                predicates.append(
                    f" AND ({status_column} IN ('not_provided', 'unlimited')"
                    f" OR ({status_column} = 'supported' AND {value_column} >= ?))"
                )
                parameters.append(profile)
        return "".join(predicates), tuple(parameters)

    def close(self) -> None:
        self.connection.close()

    def nearest_node(self, row: dict[str, str]) -> str | None:
        if not self.node_count or self.bounds[0] is None:
            return None
        lat = number(row.get("lat"))
        lon = number(row.get("lon"))
        cell_lat = math.floor(lat / self.CELL_DEG)
        cell_lon = math.floor(lon / self.CELL_DEG)
        min_lat_cell, max_lat_cell, min_lon_cell, max_lon_cell = (int(value) for value in self.bounds)
        max_ring = max(
            abs(cell_lat - min_lat_cell),
            abs(cell_lat - max_lat_cell),
            abs(cell_lon - min_lon_cell),
            abs(cell_lon - max_lon_cell),
        )
        best: str | None = None
        best_distance = float("inf")
        for ring in range(max_ring + 1):
            for lat_cell in range(cell_lat - ring, cell_lat + ring + 1):
                for lon_cell in range(cell_lon - ring, cell_lon + ring + 1):
                    if max(abs(lat_cell - cell_lat), abs(lon_cell - cell_lon)) != ring:
                        continue
                    for node, node_lat, node_lon in self.connection.execute(
                        "SELECT node_id, lat, lon FROM nodes WHERE cell_lat = ? AND cell_lon = ?",
                        (lat_cell, lon_cell),
                    ):
                        distance = (node_lat - lat) ** 2 + (node_lon - lon) ** 2
                        if distance < best_distance or (distance == best_distance and node < (best or node)):
                            best = node
                            best_distance = distance
            if best is not None:
                lat_min = (cell_lat - ring) * self.CELL_DEG
                lat_max = (cell_lat + ring + 1) * self.CELL_DEG
                lon_min = (cell_lon - ring) * self.CELL_DEG
                lon_max = (cell_lon + ring + 1) * self.CELL_DEG
                outside_distance = min(
                    lat - lat_min,
                    lat_max - lat,
                    lon - lon_min,
                    lon_max - lon,
                ) ** 2
                if best_distance <= outside_distance:
                    return best
        return best

    def turn_allowed(
        self,
        via_node: str,
        incoming_way: str | None,
        outgoing_way: str,
        restriction_state: tuple[tuple[str, ...], ...] | None = None,
        elapsed_distance_m: float = 0.0,
        elapsed_time_s: float | None = None,
    ) -> bool:
        """Apply via-node and via-way no/only turn rules for one transition."""
        if not incoming_way or not self.has_turn_restrictions:
            return True
        active = restriction_state
        if active is None:
            active = ((incoming_way,),)
        forbidden: set[str] = set()
        allowed: set[str] = set()
        for prefix in active:
            key = (prefix, via_node)
            forbidden.update(self.forbidden_turns.get(key, set()))
            allowed.update(self.only_turns.get(key, set()))
            if self.departure is not None:
                arrival = self.departure + timedelta(
                    seconds=(
                        max(0.0, elapsed_time_s)
                        if elapsed_time_s is not None
                        else max(0.0, elapsed_distance_m) / self.speed_mps
                    )
                )
                for target, schedule in self.conditional_forbidden_turns.get(key, ()):
                    if schedule.active_at(arrival):
                        forbidden.add(target)
                for target, schedule in self.conditional_only_turns.get(key, ()):
                    if schedule.active_at(arrival):
                        allowed.add(target)
            if self.vehicle_weight_t is not None:
                for target, condition in self.conditional_forbidden_weight_turns.get(key, ()):
                    if vehicle_weight_condition_active(self.vehicle_weight_t, condition):
                        forbidden.add(target)
                for target, condition in self.conditional_only_weight_turns.get(key, ()):
                    if vehicle_weight_condition_active(self.vehicle_weight_t, condition):
                        allowed.add(target)
        if allowed and outgoing_way not in allowed:
            return False
        return outgoing_way not in forbidden

    def advance_restriction_state(
        self,
        active: tuple[tuple[str, ...], ...],
        last_way: str,
        outgoing_way: str,
    ) -> tuple[tuple[str, ...], ...]:
        """Advance the compact way-run suffix state used by restricted routing."""
        if not self.has_turn_restrictions:
            return ()
        if last_way and outgoing_way == last_way:
            return active
        candidates = [(outgoing_way,)]
        candidates.extend(prefix + (outgoing_way,) for prefix in active)
        next_active: set[tuple[str, ...]] = set()
        for sequence in candidates:
            for length in range(1, min(self.max_restriction_prefix_n, len(sequence)) + 1):
                suffix = sequence[-length:]
                if suffix in self.restriction_prefixes:
                    next_active.add(suffix)
        return tuple(sorted(next_active, key=lambda prefix: (len(prefix), prefix)))

    def conditional_access_allowed(
        self,
        way_id: str,
        elapsed_distance_m: float = 0.0,
        elapsed_time_s: float | None = None,
        direction: str = "both",
    ) -> bool:
        """Apply ordered time-/weight-qualified access rules at entry.

        Multiple active allow clauses form a union of permitted vehicle
        classes; active deny clauses then remove their matching class. A way
        with allow clauses remains closed when none of those clauses is active,
        preserving the fail-closed behavior of the original single-rule
        implementation.
        """
        rules = self.conditional_access_by_way.get((str(way_id), str(direction)))
        if rules is None:
            rules = self.conditional_access_by_way.get((str(way_id), "both"))
        if not rules:
            return True
        elapsed = (
            max(0.0, elapsed_time_s)
            if elapsed_time_s is not None
            else max(0.0, elapsed_distance_m) / self.speed_mps
        )
        moment = (
            self.departure + timedelta(seconds=elapsed)
            if self.departure is not None
            else None
        )
        active_rules: list[ConditionalAccessRule] = []
        for rule in rules:
            if rule.weight_condition is not None:
                if self.vehicle_weight_t is None:
                    # An allow rule cannot be safely granted without the
                    # profile needed to evaluate its weight predicate. A deny
                    # rule is inactive until a weight profile is supplied.
                    continue
                active = vehicle_weight_condition_active(
                    self.vehicle_weight_t,
                    rule.weight_condition,
                )
            elif moment is None:
                active = rule.schedule.always_active
            else:
                active = rule.schedule.active_at(moment, self.public_holidays)
            if active:
                active_rules.append(rule)

        for rule in active_rules:
            if rule.mode == "deny" and (
                rule.vehicle_class == "general"
                or rule.vehicle_class == self.vehicle_class
            ):
                return False

        allow_rules = [rule for rule in rules if rule.mode == "allow"]
        if not allow_rules:
            return True
        active_allow_classes = {
            rule.vehicle_class
            for rule in active_rules
            if rule.mode == "allow"
        }
        if (
            not active_allow_classes
            and self.vehicle_class != "psv"
            and {rule.vehicle_class for rule in allow_rules} == {"psv"}
        ):
            # ``motor_vehicle:conditional=psv @ ...`` is an exception on a
            # normally usable road: it becomes psv-only while active, then
            # returns to the base general-traffic state outside the window.
            return True
        return bool(
            "general" in active_allow_classes
            or self.vehicle_class in active_allow_classes
        )

    def hgv_destination_allowed(self, way_id: str) -> bool:
        """Apply static destination-only HGV access with conservative defaults."""
        if self.vehicle_class != "hgv":
            return True
        entries = self.hgv_destination_by_way.get(str(way_id))
        if not entries:
            return True
        if any(str(entry.get("kind")) == "access" for entry in entries):
            return self.allow_hgv_destination and all(
                str(entry.get("status"))
                in {MAXWEIGHT_STATUS_SUPPORTED, MAXWEIGHT_STATUS_UNLIMITED}
                for entry in entries
            )
        return True

    def conditional_maxspeed_kmh(
        self,
        way_id: str,
        elapsed_distance_m: float = 0.0,
        elapsed_time_s: float | None = None,
    ) -> float | None:
        """Return the active conditional speed ceiling for one way, if any."""
        rules = self.maxspeed_conditional_by_way.get(str(way_id))
        if not rules:
            return None
        moment: datetime | None = None
        if self.departure is not None:
            elapsed = (
                max(0.0, elapsed_time_s)
                if elapsed_time_s is not None
                else max(0.0, elapsed_distance_m) / self.speed_mps
            )
            moment = self.departure + timedelta(seconds=elapsed)
        active_limits: list[float] = []
        for speed_kmh, schedule in rules:
            active = (
                schedule.always_active
                if moment is None
                else schedule.active_at(moment, self.public_holidays)
            )
            if active and speed_kmh is not None:
                active_limits.append(speed_kmh)
        return min(active_limits) if active_limits else None

    def oneway_conditional_allowed(
        self,
        way_id: str,
        base_oneway: str,
        direction: str,
        elapsed_distance_m: float = 0.0,
        elapsed_time_s: float | None = None,
    ) -> bool:
        """Apply the active conditional one-way state to one traversal."""
        base_value = str(base_oneway or "").lower()
        if base_value == "-1":
            base_allowed = {"backward"}
        elif base_value in {"yes", "1", "true"}:
            base_allowed = {"forward"}
        else:
            base_allowed = {"forward", "backward"}
        rules = self.oneway_conditional_by_way.get(str(way_id))
        if not rules or str(way_id) in self.oneway_conditional_unsupported_by_way:
            return direction in base_allowed
        moment: datetime | None = None
        if self.departure is not None:
            elapsed = (
                max(0.0, elapsed_time_s)
                if elapsed_time_s is not None
                else max(0.0, elapsed_distance_m) / self.speed_mps
            )
            moment = self.departure + timedelta(seconds=elapsed)
        active_modes: list[str] = []
        for mode, schedule in rules:
            active = (
                schedule.always_active
                if moment is None
                else schedule.active_at(moment, self.public_holidays)
            )
            if active:
                active_modes.append(mode)
        if not active_modes:
            return direction in base_allowed
        # Multiple active clauses are uncommon in the observed extract. A
        # simultaneous ``no`` clause is treated as the less restrictive state;
        # otherwise any active ``yes`` clause enforces the forward direction.
        if "no" in active_modes:
            allowed = {"forward", "backward"}
        else:
            allowed = {"forward"}
        return direction in allowed

    def neighbours(
        self,
        node: str,
        incoming_way: str | None = None,
        restriction_state: tuple[tuple[str, ...], ...] | None = None,
        elapsed_distance_m: float = 0.0,
        elapsed_time_s: float | None = None,
        previous_ferry_way: str | None = None,
    ):
        """Yield directed neighbours while applying one-way and turn semantics."""
        maxweight_filter, maxweight_parameters = self._edge_constraint_filter_sql()
        if self.has_oneway_conditional_profiles:
            way_id_select = "way_id" if self.has_way_ids else "''"
            road_query = f"""
            SELECT v, length_m, {way_id_select}, 'forward', oneway, oneway_conditional_json FROM edges
            WHERE u = ? AND (oneway NOT IN ('-1') OR oneway_conditional_json != '[]'){maxweight_filter}
            UNION ALL
            SELECT u, length_m, {way_id_select}, 'backward', oneway, oneway_conditional_json FROM edges
            WHERE v = ? AND (oneway NOT IN ('yes', '1', 'true') OR oneway_conditional_json != '[]'){maxweight_filter}
            """
        elif self.has_way_ids:
            road_query = f"""
            SELECT v, length_m, way_id, 'forward' FROM edges
            WHERE u = ? AND oneway NOT IN ('-1'){maxweight_filter}
            UNION ALL
            SELECT u, length_m, way_id, 'backward' FROM edges
            WHERE v = ? AND oneway NOT IN ('yes', '1', 'true'){maxweight_filter}
            """
        else:
            road_query = f"""
            SELECT v, length_m, '', 'forward' FROM edges
            WHERE u = ? AND oneway NOT IN ('-1'){maxweight_filter}
            UNION ALL
            SELECT u, length_m, '', 'backward' FROM edges
            WHERE v = ? AND oneway NOT IN ('yes', '1', 'true'){maxweight_filter}
            """
        parameters: tuple[object, ...] = (
            node,
            *maxweight_parameters,
            node,
            *maxweight_parameters,
        )
        query = road_query
        if self.include_ferries:
            ferry_profile_select = ", oneway, '[]'" if self.has_oneway_conditional_profiles else ""
            query = f"""
                {road_query}
                UNION ALL
                SELECT v, length_m, way_id, 'forward'{ferry_profile_select} FROM ferry_edges
                WHERE u = ? AND oneway NOT IN ('-1')
                UNION ALL
                SELECT u, length_m, way_id, 'backward'{ferry_profile_select} FROM ferry_edges
                WHERE v = ? AND oneway NOT IN ('yes', '1', 'true')
            """
            parameters = (*parameters, node, node)
        for row in self.connection.execute(query, parameters):
            if self.has_oneway_conditional_profiles:
                neighbour, weight, way_id, direction, base_oneway, _conditional_json = row
            else:
                neighbour, weight, way_id, direction = row
                base_oneway = ""
            way_id = str(way_id)
            if not self.hgv_destination_allowed(way_id):
                continue
            if self.has_oneway_conditional_profiles and not self.oneway_conditional_allowed(
                way_id,
                str(base_oneway),
                direction,
                elapsed_distance_m,
                elapsed_time_s,
            ):
                continue
            if not self.conditional_access_allowed(
                way_id,
                elapsed_distance_m,
                elapsed_time_s,
                direction=direction,
            ):
                continue
            if (
                self.include_ferries
                and way_id in self.ferry_way_ids
                and previous_ferry_way != way_id
                and self.departure is not None
                and way_id in self.ferry_schedule_by_way
            ):
                elapsed = (
                    max(0.0, elapsed_time_s)
                    if elapsed_time_s is not None
                    else max(0.0, elapsed_distance_m) / self.speed_mps
                )
                if not self.ferry_schedule_by_way[way_id].active_at(
                    self.departure + timedelta(seconds=elapsed),
                    self.public_holidays,
                ):
                    continue
            if self.turn_allowed(
                node,
                incoming_way,
                way_id,
                restriction_state,
                elapsed_distance_m,
                elapsed_time_s,
            ):
                yield neighbour, weight, way_id

    def neighbours_with_metrics(
        self,
        node: str,
        incoming_way: str | None = None,
        restriction_state: tuple[tuple[str, ...], ...] | None = None,
        elapsed_distance_m: float = 0.0,
        elapsed_time_s: float = 0.0,
        previous_ferry_way: str | None = None,
        include_wait: bool = False,
    ):
        """Yield metric edges, optionally including the ferry wait component."""
        maxweight_filter, maxweight_parameters = self._edge_constraint_filter_sql()
        maxspeed_select = (
            ", maxspeed_kmh, maxspeed_status"
            if self.has_maxspeed_profiles
            else ""
        )
        ferry_maxspeed_select = ", NULL, 'not_provided'" if self.has_maxspeed_profiles else ""
        oneway_conditional_select = (
            ", oneway, oneway_conditional_json"
            if self.has_oneway_conditional_profiles
            else ""
        )
        ferry_oneway_conditional_select = (
            ", oneway, '[]'"
            if self.has_oneway_conditional_profiles
            else ""
        )
        if self.has_oneway_conditional_profiles:
            way_id_select = "way_id" if self.has_way_ids else "''"
            road_query = f"""
            SELECT v, length_m, {way_id_select}, 'forward'{maxspeed_select}{oneway_conditional_select} FROM edges
            WHERE u = ? AND (oneway NOT IN ('-1') OR oneway_conditional_json != '[]'){maxweight_filter}
            UNION ALL
            SELECT u, length_m, {way_id_select}, 'backward'{maxspeed_select}{oneway_conditional_select} FROM edges
            WHERE v = ? AND (oneway NOT IN ('yes', '1', 'true') OR oneway_conditional_json != '[]'){maxweight_filter}
            """
        elif self.has_way_ids:
            road_query = f"""
            SELECT v, length_m, way_id, 'forward'{maxspeed_select} FROM edges
            WHERE u = ? AND oneway NOT IN ('-1'){maxweight_filter}
            UNION ALL
            SELECT u, length_m, way_id, 'backward'{maxspeed_select} FROM edges
            WHERE v = ? AND oneway NOT IN ('yes', '1', 'true'){maxweight_filter}
            """
        else:
            road_query = f"""
            SELECT v, length_m, '', 'forward'{maxspeed_select} FROM edges
            WHERE u = ? AND oneway NOT IN ('-1'){maxweight_filter}
            UNION ALL
            SELECT u, length_m, '', 'backward'{maxspeed_select} FROM edges
            WHERE v = ? AND oneway NOT IN ('yes', '1', 'true'){maxweight_filter}
            """
        parameters: tuple[object, ...] = (
            node,
            *maxweight_parameters,
            node,
            *maxweight_parameters,
        )
        query = road_query
        if self.include_ferries:
            query = f"""
                {road_query}
                UNION ALL
                SELECT v, length_m, way_id, 'forward'{ferry_maxspeed_select}{ferry_oneway_conditional_select} FROM ferry_edges
                WHERE u = ? AND oneway NOT IN ('-1')
                UNION ALL
                SELECT u, length_m, way_id, 'backward'{ferry_maxspeed_select}{ferry_oneway_conditional_select} FROM ferry_edges
                WHERE v = ? AND oneway NOT IN ('yes', '1', 'true')
            """
            parameters = (*parameters, node, node)
        for row in self.connection.execute(query, parameters):
            if self.has_maxspeed_profiles and self.has_oneway_conditional_profiles:
                (
                    neighbour,
                    distance_m,
                    way_id,
                    direction,
                    maxspeed_kmh,
                    maxspeed_status,
                    base_oneway,
                    _conditional_json,
                ) = row
            elif self.has_maxspeed_profiles:
                (
                    neighbour,
                    distance_m,
                    way_id,
                    direction,
                    maxspeed_kmh,
                    maxspeed_status,
                ) = row
                base_oneway = ""
            else:
                if self.has_oneway_conditional_profiles:
                    neighbour, distance_m, way_id, direction, base_oneway, _conditional_json = row
                else:
                    neighbour, distance_m, way_id, direction = row
                    base_oneway = ""
                maxspeed_kmh = None
                maxspeed_status = MAXWEIGHT_STATUS_NOT_PROVIDED
            way_id = str(way_id)
            if not self.hgv_destination_allowed(way_id):
                continue
            if self.has_oneway_conditional_profiles and not self.oneway_conditional_allowed(
                way_id,
                str(base_oneway),
                direction,
                elapsed_distance_m,
                elapsed_time_s,
            ):
                continue
            is_ferry = way_id in self.ferry_way_ids
            if not self.conditional_access_allowed(
                way_id,
                elapsed_distance_m,
                elapsed_time_s,
                direction=direction,
            ):
                continue
            if (
                is_ferry
                and previous_ferry_way != way_id
                and self.departure is not None
                and way_id in self.ferry_schedule_by_way
            ):
                service_at = self.departure + timedelta(seconds=max(0.0, elapsed_time_s))
                next_service = self.ferry_schedule_by_way[way_id].next_active_at(
                    service_at,
                    self.public_holidays,
                )
                if next_service is None:
                    continue
                wait_s = max(0.0, (next_service - service_at).total_seconds())
            else:
                wait_s = 0.0
            if not self.turn_allowed(
                node,
                incoming_way,
                way_id,
                restriction_state,
                elapsed_distance_m,
                elapsed_time_s,
            ):
                continue
            duration_s = (
                self.ferry_duration_by_way.get(way_id)
                if is_ferry
                else None
            )
            if duration_s is not None:
                duration_s /= max(1, self.ferry_segment_count_by_way.get(way_id, 1))
            if duration_s is None:
                edge_speed_mps = self.speed_mps
                if maxspeed_status == MAXWEIGHT_STATUS_SUPPORTED:
                    limit_kmh = number(maxspeed_kmh, 0.0)
                    edge_speed_mps = min(edge_speed_mps, limit_kmh / 3.6)
                conditional_limit_kmh = self.conditional_maxspeed_kmh(
                    way_id,
                    elapsed_distance_m,
                    elapsed_time_s,
                )
                if conditional_limit_kmh is not None:
                    edge_speed_mps = min(edge_speed_mps, conditional_limit_kmh / 3.6)
                if edge_speed_mps <= 0:
                    continue
                duration_s = float(distance_m) / edge_speed_mps
            result = (neighbour, float(distance_m), wait_s + duration_s, way_id)
            yield (*result, wait_s) if include_wait else result


def write_sqlite_graph(
    path: Path,
    coordinates: dict[str, tuple[float, float]],
    edges: list[dict[str, str]],
    *,
    source: str,
    complete: bool = False,
    max_ways: int | None = None,
    way_n: int | None = None,
) -> None:
    """Write a portable SQLite graph from physical edges and node coordinates."""
    path.mkdir(parents=True, exist_ok=True)
    public_holiday_status, public_holidays = _load_public_holiday_contract(
        path / "public_holidays.json"
    )
    public_holiday_min_date, public_holiday_max_date = _public_holiday_bounds(
        public_holidays
    )
    database = path / "road_graph.sqlite"
    temporary = path / ".road_graph.sqlite.tmp"
    temporary.unlink(missing_ok=True)
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE nodes (
                node_id TEXT PRIMARY KEY,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                cell_lat INTEGER NOT NULL,
                cell_lon INTEGER NOT NULL
            );
            CREATE TABLE edges (
                u TEXT NOT NULL,
                v TEXT NOT NULL,
                length_m REAL NOT NULL,
                oneway TEXT NOT NULL,
                way_id TEXT NOT NULL,
                maxweight_t REAL,
                maxweight_status TEXT NOT NULL DEFAULT 'not_provided',
                maxweight_hgv_t REAL,
                maxweight_hgv_status TEXT NOT NULL DEFAULT 'not_provided',
                maxheight_m REAL,
                maxheight_status TEXT NOT NULL DEFAULT 'not_provided',
                maxheight_physical_m REAL,
                maxheight_physical_status TEXT NOT NULL DEFAULT 'not_provided',
                maxwidth_m REAL,
                maxwidth_status TEXT NOT NULL DEFAULT 'not_provided',
                maxlength_m REAL,
                maxlength_status TEXT NOT NULL DEFAULT 'not_provided',
                maxaxleload_t REAL,
                maxaxleload_status TEXT NOT NULL DEFAULT 'not_provided',
                maxspeed_kmh REAL,
                maxspeed_status TEXT NOT NULL DEFAULT 'not_provided',
                maxspeed_conditional_json TEXT NOT NULL DEFAULT '[]',
                oneway_conditional_json TEXT NOT NULL DEFAULT '[]',
                maxweightrating_hgv_t REAL,
                maxweightrating_hgv_status TEXT NOT NULL DEFAULT 'not_provided',
                hgv_destination_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE ferry_edges (
                u TEXT NOT NULL,
                v TEXT NOT NULL,
                length_m REAL NOT NULL,
                oneway TEXT NOT NULL,
                way_id TEXT NOT NULL
            );
            CREATE TABLE ways (
                way_id TEXT PRIMARY KEY,
                node_json TEXT NOT NULL DEFAULT '[]',
                oneway TEXT,
                highway TEXT,
                name TEXT,
                ref TEXT,
                route TEXT
            );
            CREATE TABLE turn_restrictions (
                relation_id TEXT NOT NULL,
                via_node TEXT NOT NULL,
                from_way TEXT NOT NULL,
                to_way TEXT NOT NULL,
                kind TEXT NOT NULL,
                via_way_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE conditional_turn_restrictions (
                relation_id TEXT NOT NULL,
                via_node TEXT NOT NULL,
                from_way TEXT NOT NULL,
                to_way TEXT NOT NULL,
                kind TEXT NOT NULL,
                via_way_json TEXT NOT NULL DEFAULT '[]',
                condition TEXT NOT NULL
            );
            CREATE TABLE conditional_access (
                way_id TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'both',
                condition TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'allow',
                vehicle_class TEXT NOT NULL DEFAULT 'general',
                rule_n INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (way_id, direction, rule_n)
            );
            """
        )
        connection.executemany(
            "INSERT INTO nodes(node_id, lat, lon, cell_lat, cell_lon) VALUES (?, ?, ?, ?, ?)",
            [
                (node, lat, lon, math.floor(lat / SQLiteRoadGraph.CELL_DEG), math.floor(lon / SQLiteRoadGraph.CELL_DEG))
                for node, (lat, lon) in coordinates.items()
            ],
        )
        edge_rows: list[
            tuple[
                str,
                str,
                float,
                str,
                str,
                float | None,
                str,
                float | None,
                str,
                float | None,
                str,
                float | None,
                str,
                float | None,
                str,
                float | None,
                str,
                float | None,
                str,
                float | None,
                str,
                str,
                str,
                float | None,
                str,
                str,
            ]
        ] = []
        way_context_rows: dict[str, list[str | None]] = {}
        for row in edges:
            u = str(row.get("u", row.get("from", ""))).strip()
            v = str(row.get("v", row.get("to", ""))).strip()
            if not u or not v:
                continue
            raw_maxweight = row.get("maxweight", row.get("maxweight_t", ""))
            maxweight_status, maxweight_t = parse_maxweight_profile(raw_maxweight)
            raw_maxweight_hgv = row.get(
                "maxweight:hgv",
                row.get(
                    "maxweight_hgv",
                    row.get("maxweight_hgv_t", ""),
                ),
            )
            maxweight_hgv_status, maxweight_hgv_t = parse_maxweight_profile(
                raw_maxweight_hgv
            )
            raw_maxheight = row.get("maxheight", row.get("maxheight_m", ""))
            maxheight_status, maxheight_m = parse_maxheight_profile(raw_maxheight)
            raw_maxheight_physical = row.get(
                "maxheight_physical",
                row.get("maxheight_physical_m", ""),
            )
            maxheight_physical_status, maxheight_physical_m = parse_maxheight_profile(
                raw_maxheight_physical
            )
            maxwidth_status, maxwidth_m = parse_maxwidth_profile(
                row.get("maxwidth", row.get("maxwidth_m", ""))
            )
            maxlength_status, maxlength_m = parse_maxlength_profile(
                row.get("maxlength", row.get("maxlength_m", ""))
            )
            maxaxleload_status, maxaxleload_t = parse_maxaxleload_profile(
                row.get("maxaxleload", row.get("maxaxleload_t", ""))
            )
            maxspeed_status, maxspeed_kmh = parse_maxspeed_profile(
                row.get("maxspeed", row.get("maxspeed_kmh", ""))
            )
            maxspeed_conditional_json = json.dumps(
                list(
                    parse_maxspeed_conditional_profile(
                        row.get(
                            "maxspeed:conditional",
                            row.get("maxspeed_conditional", ""),
                        )
                    )
                ),
                separators=(",", ":"),
            )
            oneway_conditional_json = json.dumps(
                list(
                    parse_oneway_conditional_profiles(
                        row.get("oneway:conditional", row.get("oneway_conditional", "")),
                        row.get(
                            "oneway:motor_vehicle:conditional",
                            row.get("oneway_motor_vehicle_conditional", ""),
                        ),
                    )
                ),
                separators=(",", ":"),
            )
            maxweightrating_hgv_status, maxweightrating_hgv_t = (
                parse_maxweightrating_hgv_profiles(
                    row.get(
                        "maxweightrating:hgv",
                        row.get("maxweightrating_hgv", ""),
                    ),
                    row.get(
                        "maxweightrating:goods",
                        row.get("maxweightrating_goods", ""),
                    ),
                )
            )
            hgv_destination_json = json.dumps(
                list(parse_hgv_destination_profiles(row)),
                separators=(",", ":"),
            )
            edge_way_id = str(row.get("way_id", "")).strip()
            if hgv_destination_json != "[]" and not edge_way_id:
                edge_way_id = f"edge/{u}/{v}"
            if edge_way_id:
                context = way_context_rows.setdefault(
                    edge_way_id,
                    [edge_way_id, "[]", None, None, None, None, None],
                )
                context_values = [
                    str(row.get("oneway", "")).lower().strip() or None,
                    _optional_way_tag(row.get("highway")),
                    _optional_way_tag(row.get("name")),
                    _optional_way_tag(row.get("ref")),
                    _optional_way_tag(row.get("route")),
                ]
                for index, value in enumerate(context_values, start=2):
                    if context[index] is None and value is not None:
                        context[index] = value
            edge_rows.append(
                (
                    u,
                    v,
                    number(row.get("length_m"), 0.0),
                    str(row.get("oneway", "")).lower(),
                    edge_way_id,
                    maxweight_t,
                    maxweight_status,
                    maxweight_hgv_t,
                    maxweight_hgv_status,
                    maxheight_m,
                    maxheight_status,
                    maxheight_physical_m,
                    maxheight_physical_status,
                    maxwidth_m,
                    maxwidth_status,
                    maxlength_m,
                    maxlength_status,
                    maxaxleload_t,
                    maxaxleload_status,
                    maxspeed_kmh,
                    maxspeed_status,
                    maxspeed_conditional_json,
                    oneway_conditional_json,
                    maxweightrating_hgv_t,
                    maxweightrating_hgv_status,
                    hgv_destination_json,
                )
            )
        connection.executemany(
            """
            INSERT INTO edges(
                u, v, length_m, oneway, way_id, maxweight_t, maxweight_status,
                maxweight_hgv_t, maxweight_hgv_status, maxheight_m, maxheight_status,
                maxheight_physical_m, maxheight_physical_status, maxwidth_m,
                maxwidth_status, maxlength_m, maxlength_status, maxaxleload_t,
                maxaxleload_status, maxspeed_kmh, maxspeed_status,
                maxspeed_conditional_json, oneway_conditional_json,
                maxweightrating_hgv_t, maxweightrating_hgv_status,
                hgv_destination_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            edge_rows,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO ways(way_id, node_json, oneway, highway, name, ref, route) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [tuple(row) for row in way_context_rows.values()],
        )
        connection.execute("CREATE INDEX nodes_cell ON nodes(cell_lat, cell_lon)")
        connection.execute("CREATE INDEX edges_u ON edges(u)")
        connection.execute("CREATE INDEX edges_v ON edges(v)")
        connection.execute(
            "CREATE INDEX edges_hgv_destination ON edges(way_id) "
            "WHERE hgv_destination_json != '[]'"
        )
        connection.execute("CREATE INDEX ferry_edges_u ON ferry_edges(u)")
        connection.execute("CREATE INDEX ferry_edges_v ON ferry_edges(v)")
        connection.execute("ANALYZE")
        connection.commit()
        connection.close()
        os.replace(temporary, database)
    except Exception:
        try:
            connection.close()
        except (UnboundLocalError, AttributeError):
            pass
        temporary.unlink(missing_ok=True)
        raise
    atomic_write_json(
        path / "road_graph_metadata.json",
        {
            "format": ROAD_GRAPH_FORMAT,
            "storage": "sqlite",
            "directed": True,
            "source": source,
            "node_n": len(coordinates),
            "edge_n": len(edge_rows),
            "complete": complete,
            "max_ways": max_ways,
            "way_n": way_n,
            "way_context": True,
            "way_context_n": len(way_context_rows),
            "ferry_way_n": 0,
            "ferry_segment_n": 0,
            "ferry_edge_n": 0,
            "ferry_relation_n": 0,
            "ferry_schedules_modeled": False,
            "ferry_schedule_contract": FERRY_SCHEDULE_CONTRACT,
            "ferry_schedule_n": 0,
            "ferry_schedule_unsupported_n": 0,
            "ferry_public_holiday_schedule_n": 0,
            "public_holiday_contract": public_holiday_status,
            "public_holiday_n": len(public_holidays),
            "public_holiday_min_date": public_holiday_min_date,
            "public_holiday_max_date": public_holiday_max_date,
            "ferry_duration_n": 0,
            "conditional_access_n": 0,
            "conditional_access_vehicle_class_n": {vehicle_class: 0 for vehicle_class in VEHICLE_CLASSES},
            "conditional_access_direction_n": {direction: 0 for direction in CONDITIONAL_ACCESS_DIRECTIONS},
            "conditional_access_weight_n": 0,
            "conditional_access_multiclause_n": 0,
            "conditional_access_unsupported_n": 0,
            "conditional_access_excluded_n": 0,
            "maxweight_way_n": len({
                row[4] for row in edge_rows
                if row[6] != MAXWEIGHT_STATUS_NOT_PROVIDED
            }),
            "maxweight_supported_way_n": len({
                row[4] for row in edge_rows
                if row[6] == MAXWEIGHT_STATUS_SUPPORTED
            }),
            "maxweight_unlimited_way_n": len({
                row[4] for row in edge_rows
                if row[6] == MAXWEIGHT_STATUS_UNLIMITED
            }),
            "maxweight_unsupported_way_n": len({
                row[4] for row in edge_rows
                if row[6] == MAXWEIGHT_STATUS_UNSUPPORTED
            }),
            "maxweight_segment_n": sum(
                row[6] != MAXWEIGHT_STATUS_NOT_PROVIDED for row in edge_rows
            ),
            "maxweight_hgv_way_n": len({
                row[4] for row in edge_rows
                if row[8] != MAXWEIGHT_STATUS_NOT_PROVIDED
            }),
            "maxweight_hgv_supported_way_n": len({
                row[4] for row in edge_rows
                if row[8] == MAXWEIGHT_STATUS_SUPPORTED
            }),
            "maxweight_hgv_unlimited_way_n": len({
                row[4] for row in edge_rows
                if row[8] == MAXWEIGHT_STATUS_UNLIMITED
            }),
            "maxweight_hgv_unsupported_way_n": len({
                row[4] for row in edge_rows
                if row[8] == MAXWEIGHT_STATUS_UNSUPPORTED
            }),
            "maxweight_hgv_segment_n": sum(
                row[8] != MAXWEIGHT_STATUS_NOT_PROVIDED for row in edge_rows
            ),
            "maxweightrating_hgv_way_n": len({
                row[4] for row in edge_rows
                if row[24] != MAXWEIGHT_STATUS_NOT_PROVIDED
            }),
            "maxweightrating_hgv_supported_way_n": len({
                row[4] for row in edge_rows
                if row[24] == MAXWEIGHT_STATUS_SUPPORTED
            }),
            "maxweightrating_hgv_unlimited_way_n": len({
                row[4] for row in edge_rows
                if row[24] == MAXWEIGHT_STATUS_UNLIMITED
            }),
            "maxweightrating_hgv_unsupported_way_n": len({
                row[4] for row in edge_rows
                if row[24] == MAXWEIGHT_STATUS_UNSUPPORTED
            }),
            "maxweightrating_hgv_segment_n": sum(
                row[24] != MAXWEIGHT_STATUS_NOT_PROVIDED for row in edge_rows
            ),
            "hgv_destination_way_n": len({
                row[4] for row in edge_rows if row[25] != "[]"
            }),
            "hgv_destination_supported_way_n": len({
                row[4]
                for row in edge_rows
                if row[25] != "[]"
                and all(
                    str(entry.get("status"))
                    in {MAXWEIGHT_STATUS_SUPPORTED, MAXWEIGHT_STATUS_UNLIMITED}
                    for entry in json.loads(row[25])
                )
            }),
            "hgv_destination_unsupported_way_n": len({
                row[4]
                for row in edge_rows
                if row[25] != "[]"
                and any(
                    str(entry.get("status")) == MAXWEIGHT_STATUS_UNSUPPORTED
                    for entry in json.loads(row[25])
                )
            }),
            "hgv_destination_segment_n": sum(
                row[25] != "[]" for row in edge_rows
            ),
            "maxheight_way_n": len({
                row[4] for row in edge_rows
                if row[10] != MAXWEIGHT_STATUS_NOT_PROVIDED
            }),
            "maxheight_supported_way_n": len({
                row[4] for row in edge_rows
                if row[10] == MAXWEIGHT_STATUS_SUPPORTED
            }),
            "maxheight_unlimited_way_n": len({
                row[4] for row in edge_rows
                if row[10] == MAXWEIGHT_STATUS_UNLIMITED
            }),
            "maxheight_unsupported_way_n": len({
                row[4] for row in edge_rows
                if row[10] == MAXWEIGHT_STATUS_UNSUPPORTED
            }),
            "maxheight_segment_n": sum(
                row[10] != MAXWEIGHT_STATUS_NOT_PROVIDED for row in edge_rows
            ),
            "maxheight_physical_way_n": len({
                row[4] for row in edge_rows
                if row[12] != MAXWEIGHT_STATUS_NOT_PROVIDED
            }),
            "maxheight_physical_supported_way_n": len({
                row[4] for row in edge_rows
                if row[12] == MAXWEIGHT_STATUS_SUPPORTED
            }),
            "maxheight_physical_unlimited_way_n": len({
                row[4] for row in edge_rows
                if row[12] == MAXWEIGHT_STATUS_UNLIMITED
            }),
            "maxheight_physical_unsupported_way_n": len({
                row[4] for row in edge_rows
                if row[12] == MAXWEIGHT_STATUS_UNSUPPORTED
            }),
            "maxheight_physical_segment_n": sum(
                row[12] != MAXWEIGHT_STATUS_NOT_PROVIDED for row in edge_rows
            ),
            "maxwidth_way_n": len({
                row[4] for row in edge_rows
                if row[14] != MAXWEIGHT_STATUS_NOT_PROVIDED
            }),
            "maxwidth_supported_way_n": len({
                row[4] for row in edge_rows
                if row[14] == MAXWEIGHT_STATUS_SUPPORTED
            }),
            "maxwidth_unlimited_way_n": len({
                row[4] for row in edge_rows
                if row[14] == MAXWEIGHT_STATUS_UNLIMITED
            }),
            "maxwidth_unsupported_way_n": len({
                row[4] for row in edge_rows
                if row[14] == MAXWEIGHT_STATUS_UNSUPPORTED
            }),
            "maxwidth_segment_n": sum(
                row[14] != MAXWEIGHT_STATUS_NOT_PROVIDED for row in edge_rows
            ),
            "maxlength_way_n": len({
                row[4] for row in edge_rows
                if row[16] != MAXWEIGHT_STATUS_NOT_PROVIDED
            }),
            "maxlength_supported_way_n": len({
                row[4] for row in edge_rows
                if row[16] == MAXWEIGHT_STATUS_SUPPORTED
            }),
            "maxlength_unlimited_way_n": len({
                row[4] for row in edge_rows
                if row[16] == MAXWEIGHT_STATUS_UNLIMITED
            }),
            "maxlength_unsupported_way_n": len({
                row[4] for row in edge_rows
                if row[16] == MAXWEIGHT_STATUS_UNSUPPORTED
            }),
            "maxlength_segment_n": sum(
                row[16] != MAXWEIGHT_STATUS_NOT_PROVIDED for row in edge_rows
            ),
            "maxaxleload_way_n": len({
                row[4] for row in edge_rows
                if row[18] != MAXWEIGHT_STATUS_NOT_PROVIDED
            }),
            "maxaxleload_supported_way_n": len({
                row[4] for row in edge_rows
                if row[18] == MAXWEIGHT_STATUS_SUPPORTED
            }),
            "maxaxleload_unlimited_way_n": len({
                row[4] for row in edge_rows
                if row[18] == MAXWEIGHT_STATUS_UNLIMITED
            }),
            "maxaxleload_unsupported_way_n": len({
                row[4] for row in edge_rows
                if row[18] == MAXWEIGHT_STATUS_UNSUPPORTED
            }),
            "maxaxleload_segment_n": sum(
                row[18] != MAXWEIGHT_STATUS_NOT_PROVIDED for row in edge_rows
            ),
            "maxspeed_way_n": len({
                row[4] for row in edge_rows
                if row[20] != MAXWEIGHT_STATUS_NOT_PROVIDED
            }),
            "maxspeed_supported_way_n": len({
                row[4] for row in edge_rows
                if row[20] == MAXWEIGHT_STATUS_SUPPORTED
            }),
            "maxspeed_unlimited_way_n": len({
                row[4] for row in edge_rows
                if row[20] == MAXWEIGHT_STATUS_UNLIMITED
            }),
            "maxspeed_unsupported_way_n": len({
                row[4] for row in edge_rows
                if row[20] == MAXWEIGHT_STATUS_UNSUPPORTED
            }),
            "maxspeed_segment_n": sum(
                row[20] != MAXWEIGHT_STATUS_NOT_PROVIDED for row in edge_rows
            ),
            "maxspeed_conditional_way_n": len({
                row[4] for row in edge_rows if row[21] != "[]"
            }),
            "maxspeed_conditional_supported_way_n": len({
                row[4]
                for row in edge_rows
                if row[21] != "[]"
                and all(
                    str(entry.get("status"))
                    in {MAXWEIGHT_STATUS_SUPPORTED, MAXWEIGHT_STATUS_UNLIMITED}
                    for entry in json.loads(row[21])
                )
            }),
            "maxspeed_conditional_unsupported_way_n": len({
                row[4]
                for row in edge_rows
                if row[21] != "[]"
                and any(
                    str(entry.get("status")) == MAXWEIGHT_STATUS_UNSUPPORTED
                    for entry in json.loads(row[21])
                )
            }),
            "maxspeed_conditional_segment_n": sum(
                row[21] != "[]" for row in edge_rows
            ),
            "oneway_conditional_way_n": len({
                row[4] for row in edge_rows if row[22] != "[]"
            }),
            "oneway_conditional_supported_way_n": len({
                row[4]
                for row in edge_rows
                if row[22] != "[]"
                and all(
                    str(entry.get("status")) == MAXWEIGHT_STATUS_SUPPORTED
                    for entry in json.loads(row[22])
                )
            }),
            "oneway_conditional_unsupported_way_n": len({
                row[4]
                for row in edge_rows
                if row[22] != "[]"
                and any(
                    str(entry.get("status")) == MAXWEIGHT_STATUS_UNSUPPORTED
                    for entry in json.loads(row[22])
                )
            }),
            "oneway_conditional_segment_n": sum(
                row[22] != "[]" for row in edge_rows
            ),
            "turn_restriction_n": 0,
            "restriction_conditional_n": 0,
            "restriction_conditional_supported_n": 0,
            "restriction_conditional_weight_n": 0,
            "restriction_conditional_stored_n": 0,
            "restriction_conditional_unsupported_n": 0,
            "restriction_conditional_unresolved_n": 0,
            "restriction_conditional_geometry_unsupported_n": 0,
        },
        indent=2,
    )


def load_graph(path: Path) -> PortableRoadGraph | SQLiteRoadGraph:
    if path.is_dir():
        sqlite_path = path / "road_graph.sqlite"
        if sqlite_path.is_file():
            return SQLiteRoadGraph(sqlite_path)
        return graph_from_rows(read_csv(path / "road_nodes.csv"), read_csv(path / "road_edges.csv"))
    if path.suffix.lower() in {".sqlite", ".db"}:
        return SQLiteRoadGraph(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return graph_from_rows(payload.get("nodes", []), payload.get("edges", []))
    raise ValueError("road graph JSON must be an object containing nodes and edges")


def graph_from_pbf(
    path: Path,
    max_ways: int,
    *,
    include_restricted: bool = False,
    include_ferries: bool = False,
) -> PortableRoadGraph:
    try:
        import osmium
    except ImportError as exc:  # pragma: no cover - optional adapter
        raise SystemExit("the osmium package (PyOsmium bindings) is required for --from-pbf") from exc

    class Handler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.coordinates: dict[str, tuple[float, float]] = {}
            self.edges: list[dict[str, str]] = []
            self.count = 0

        def way(self, way) -> None:
            route = str(way.tags.get("route", "")).lower()
            ferry_tag = str(way.tags.get("ferry", "")).lower()
            is_ferry = route == "ferry" or bool(ferry_tag and ferry_tag != "no")
            if (
                (not is_ferry and max_ways > 0 and self.count >= max_ways)
                or (not is_ferry and way.tags.get("highway") not in HIGHWAY_TYPES)
                or (is_ferry and not include_ferries)
                or (not is_ferry and has_conditional_access_tag(way.tags))
                or (not include_restricted and is_access_restricted(way.tags))
            ):
                return
            points = [(str(node.ref), node.lat, node.lon) for node in way.nodes if node.location.valid()]
            if len(points) < 2:
                return
            if not is_ferry:
                self.count += 1
            for node_id, lat, lon in points:
                self.coordinates[node_id] = (float(lat), float(lon))
            oneway = str(way.tags.get("oneway", "")).lower()
            for first, second in pairwise(points):
                a, b = first[0], second[0]
                length = straight_distance(
                    {"lat": str(first[1]), "lon": str(first[2])},
                    {"lat": str(second[1]), "lon": str(second[2])},
                )
                self.edges.append(
                    {
                        "u": a,
                        "v": b,
                        "length_m": str(length),
                        "oneway": oneway,
                        "way_id": str(way.id),
                        "name": str(way.tags.get("name", "")),
                        "ref": str(way.tags.get("ref", "")),
                        "highway": str(way.tags.get("highway", "")),
                        "route": str(way.tags.get("route", "")),
                    }
                )

    handler = Handler()
    handler.apply_file(str(path), locations=True)
    nodes = [{"node_id": key, "lat": value[0], "lon": value[1]} for key, value in handler.coordinates.items()]
    return graph_from_rows(nodes, handler.edges)


def write_sqlite_graph_from_pbf(
    path: Path,
    output: Path,
    max_ways: int = 0,
    *,
    include_restricted: bool = False,
) -> dict[str, int | bool]:
    """Stream a PBF highway scan into a disk-backed graph.

    ``max_ways=0`` means all supported highway ways.  Nodes and edges are
    batched into SQLite so the complete graph does not require a Python
    adjacency list containing millions of objects.
    """
    try:
        import osmium
    except ImportError as exc:  # pragma: no cover - optional adapter
        raise SystemExit("the osmium package (PyOsmium bindings) is required for --from-pbf") from exc

    output.mkdir(parents=True, exist_ok=True)
    public_holiday_status, public_holidays = _load_public_holiday_contract(
        output / "public_holidays.json"
    )
    public_holiday_min_date, public_holiday_max_date = _public_holiday_bounds(
        public_holidays
    )
    database = output / "road_graph.sqlite"
    temporary = output / ".road_graph.sqlite.tmp"
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE nodes (
            node_id TEXT PRIMARY KEY,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            cell_lat INTEGER NOT NULL,
            cell_lon INTEGER NOT NULL
        );
        CREATE TABLE edges (
            u TEXT NOT NULL,
            v TEXT NOT NULL,
            length_m REAL NOT NULL,
            oneway TEXT NOT NULL,
            way_id TEXT NOT NULL,
            maxweight_t REAL,
            maxweight_status TEXT NOT NULL DEFAULT 'not_provided',
            maxweight_hgv_t REAL,
            maxweight_hgv_status TEXT NOT NULL DEFAULT 'not_provided',
            maxheight_m REAL,
            maxheight_status TEXT NOT NULL DEFAULT 'not_provided',
            maxheight_physical_m REAL,
            maxheight_physical_status TEXT NOT NULL DEFAULT 'not_provided',
            maxwidth_m REAL,
            maxwidth_status TEXT NOT NULL DEFAULT 'not_provided',
            maxlength_m REAL,
            maxlength_status TEXT NOT NULL DEFAULT 'not_provided',
            maxaxleload_t REAL,
            maxaxleload_status TEXT NOT NULL DEFAULT 'not_provided',
            maxspeed_kmh REAL,
            maxspeed_status TEXT NOT NULL DEFAULT 'not_provided',
            maxspeed_conditional_json TEXT NOT NULL DEFAULT '[]',
            oneway_conditional_json TEXT NOT NULL DEFAULT '[]',
            maxweightrating_hgv_t REAL,
            maxweightrating_hgv_status TEXT NOT NULL DEFAULT 'not_provided',
            hgv_destination_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE ferry_edges (
            u TEXT NOT NULL,
            v TEXT NOT NULL,
            length_m REAL NOT NULL,
            oneway TEXT NOT NULL,
            way_id TEXT NOT NULL
        );
        CREATE TABLE ways (
            way_id TEXT PRIMARY KEY,
            node_json TEXT NOT NULL,
            oneway TEXT,
            highway TEXT,
            name TEXT,
            ref TEXT,
            route TEXT
        );
        CREATE TABLE turn_restrictions (
            relation_id TEXT NOT NULL,
            via_node TEXT NOT NULL,
            from_way TEXT NOT NULL,
            to_way TEXT NOT NULL,
            kind TEXT NOT NULL,
            via_way_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE conditional_turn_restrictions (
            relation_id TEXT NOT NULL,
            via_node TEXT NOT NULL,
            from_way TEXT NOT NULL,
            to_way TEXT NOT NULL,
            kind TEXT NOT NULL,
            via_way_json TEXT NOT NULL DEFAULT '[]',
            condition TEXT NOT NULL
        );
            CREATE TABLE conditional_access (
                way_id TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'both',
                condition TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'allow',
                vehicle_class TEXT NOT NULL DEFAULT 'general',
                rule_n INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (way_id, direction, rule_n)
            );
        """
    )

    class Handler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.way_n = 0
            self.segment_n = 0
            self.node_refs = 0
            self.ferry_way_n = 0
            self.ferry_segment_n = 0
            self.ferry_node_refs = 0
            self.ferry_relation_n = 0
            self.ferry_schedule_n = 0
            self.ferry_schedule_unsupported_n = 0
            self.ferry_public_holiday_schedule_n = 0
            self.ferry_duration_n = 0
            self.conditional_access_n = 0
            self.conditional_access_vehicle_class_n = {
                vehicle_class: 0 for vehicle_class in VEHICLE_CLASSES
            }
            self.conditional_access_direction_n = {
                direction: 0 for direction in CONDITIONAL_ACCESS_DIRECTIONS
            }
            self.conditional_access_weight_n = 0
            self.conditional_access_multiclause_n = 0
            self.conditional_access_unsupported_n = 0
            self.conditional_access_excluded_n = 0
            self.maxweight_way_n = 0
            self.maxweight_supported_way_n = 0
            self.maxweight_unlimited_way_n = 0
            self.maxweight_unsupported_way_n = 0
            self.maxweight_segment_n = 0
            self.maxweight_hgv_way_n = 0
            self.maxweight_hgv_supported_way_n = 0
            self.maxweight_hgv_unlimited_way_n = 0
            self.maxweight_hgv_unsupported_way_n = 0
            self.maxweight_hgv_segment_n = 0
            self.maxweightrating_hgv_way_n = 0
            self.maxweightrating_hgv_supported_way_n = 0
            self.maxweightrating_hgv_unlimited_way_n = 0
            self.maxweightrating_hgv_unsupported_way_n = 0
            self.maxweightrating_hgv_segment_n = 0
            self.hgv_destination_way_n = 0
            self.hgv_destination_supported_way_n = 0
            self.hgv_destination_unsupported_way_n = 0
            self.hgv_destination_segment_n = 0
            self.maxheight_way_n = 0
            self.maxheight_supported_way_n = 0
            self.maxheight_unlimited_way_n = 0
            self.maxheight_unsupported_way_n = 0
            self.maxheight_segment_n = 0
            self.maxheight_physical_way_n = 0
            self.maxheight_physical_supported_way_n = 0
            self.maxheight_physical_unlimited_way_n = 0
            self.maxheight_physical_unsupported_way_n = 0
            self.maxheight_physical_segment_n = 0
            self.maxwidth_way_n = 0
            self.maxwidth_supported_way_n = 0
            self.maxwidth_unlimited_way_n = 0
            self.maxwidth_unsupported_way_n = 0
            self.maxwidth_segment_n = 0
            self.maxlength_way_n = 0
            self.maxlength_supported_way_n = 0
            self.maxlength_unlimited_way_n = 0
            self.maxlength_unsupported_way_n = 0
            self.maxlength_segment_n = 0
            self.maxaxleload_way_n = 0
            self.maxaxleload_supported_way_n = 0
            self.maxaxleload_unlimited_way_n = 0
            self.maxaxleload_unsupported_way_n = 0
            self.maxaxleload_segment_n = 0
            self.maxspeed_way_n = 0
            self.maxspeed_supported_way_n = 0
            self.maxspeed_unlimited_way_n = 0
            self.maxspeed_unsupported_way_n = 0
            self.maxspeed_segment_n = 0
            self.maxspeed_conditional_way_n = 0
            self.maxspeed_conditional_supported_way_n = 0
            self.maxspeed_conditional_unsupported_way_n = 0
            self.maxspeed_conditional_segment_n = 0
            self.oneway_conditional_way_n = 0
            self.oneway_conditional_supported_way_n = 0
            self.oneway_conditional_unsupported_way_n = 0
            self.oneway_conditional_segment_n = 0
            self.excluded_access_way_n = 0
            self.restriction_relation_n = 0
            self.restriction_conditional_n = 0
            self.restriction_conditional_supported_n = 0
            self.restriction_conditional_weight_n = 0
            self.restriction_conditional_unsupported_n = 0
            self.restriction_conditional_unresolved_n = 0
            self.restriction_conditional_geometry_unsupported_n = 0
            self.restriction_via_way_n = 0
            self.restriction_via_way_unresolved_n = 0
            self.restriction_via_way_unsupported_n = 0
            self.restriction_unsupported_n = 0
            self.node_rows: list[tuple[str, float, float, int, int]] = []
            self.way_rows: list[
                tuple[str, str, str | None, str | None, str | None, str | None, str | None]
            ] = []
            self.edge_rows: list[
                tuple[
                    str,
                    str,
                    float,
                    str,
                    str,
                    float | None,
                    str,
                    float | None,
                    str,
                    float | None,
                    str,
                    float | None,
                    str,
                    float | None,
                    str,
                    float | None,
                    str,
                    float | None,
                    str,
                    float | None,
                    str,
                    str,
                    str,
                    float | None,
                    str,
                    str,
                ]
            ] = []
            self.ferry_edge_rows: list[tuple[str, str, float, str, str]] = []
            self.ferry_schedule_rows: list[dict[str, object]] = []
            self.conditional_access_rows: list[
                tuple[str, str, str, str, str, int]
            ] = []
            self.restriction_rows: list[tuple[str, str, str, str, str, str]] = []
            self.conditional_restriction_rows: list[tuple[str, str, str, str, str, str, str]] = []

        def flush(self) -> None:
            if self.node_rows:
                connection.executemany(
                    "INSERT OR IGNORE INTO nodes(node_id, lat, lon, cell_lat, cell_lon) VALUES (?, ?, ?, ?, ?)",
                    self.node_rows,
                )
                self.node_rows.clear()
            if self.way_rows:
                connection.executemany(
                    "INSERT OR REPLACE INTO ways(way_id, node_json, oneway, highway, name, ref, route) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    self.way_rows,
                )
                self.way_rows.clear()
            if self.edge_rows:
                connection.executemany(
                    """
                    INSERT INTO edges(
                        u, v, length_m, oneway, way_id, maxweight_t, maxweight_status,
                        maxweight_hgv_t, maxweight_hgv_status, maxheight_m, maxheight_status,
                        maxheight_physical_m, maxheight_physical_status, maxwidth_m,
                        maxwidth_status, maxlength_m, maxlength_status, maxaxleload_t,
                        maxaxleload_status, maxspeed_kmh, maxspeed_status,
                    maxspeed_conditional_json, oneway_conditional_json,
                    maxweightrating_hgv_t, maxweightrating_hgv_status,
                    hgv_destination_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self.edge_rows,
                )
                self.edge_rows.clear()
            if self.ferry_edge_rows:
                connection.executemany(
                    "INSERT INTO ferry_edges(u, v, length_m, oneway, way_id) VALUES (?, ?, ?, ?, ?)",
                    self.ferry_edge_rows,
                )
                self.ferry_edge_rows.clear()
            if self.conditional_access_rows:
                connection.executemany(
                    "INSERT OR REPLACE INTO conditional_access(way_id, direction, condition, mode, vehicle_class, rule_n) VALUES (?, ?, ?, ?, ?, ?)",
                    self.conditional_access_rows,
                )
                self.conditional_access_rows.clear()
            connection.commit()

        def way(self, way) -> None:
            highway = str(way.tags.get("highway", ""))
            route = str(way.tags.get("route", "")).lower()
            ferry_tag = str(way.tags.get("ferry", "")).lower()
            is_ferry = route == "ferry" or bool(ferry_tag and ferry_tag != "no")
            access_rules = [] if is_ferry else conditional_access_profile_rules(way.tags)
            if not is_ferry and highway not in HIGHWAY_TYPES:
                return
            if (
                not is_ferry
                and has_conditional_access_tag(way.tags)
                and access_rules is None
            ):
                self.conditional_access_unsupported_n += 1
                self.conditional_access_excluded_n += 1
                self.excluded_access_way_n += 1
                return
            if has_multi_clause_conditional_access(way.tags):
                self.conditional_access_multiclause_n += 1
            if (
                not include_restricted
                and is_access_restricted(way.tags)
                and (
                    not access_rules
                    or any(
                        rule[0] == "deny" or rule[1] == "general"
                        for rule in access_rules
                    )
                )
            ):
                if access_rules:
                    self.conditional_access_excluded_n += 1
                self.excluded_access_way_n += 1
                return
            if not is_ferry and max_ways > 0 and self.way_n >= max_ways:
                return
            points = [(str(node.ref), node.lat, node.lon) for node in way.nodes if node.location.valid()]
            if len(points) < 2:
                return
            way_id = str(way.id)
            oneway = str(way.tags.get("oneway", "")).lower()
            way_name = _optional_way_tag(way.tags.get("name", ""))
            way_ref = _optional_way_tag(way.tags.get("ref", ""))
            way_route = "ferry" if is_ferry else None
            for node_id, lat, lon in points:
                self.node_rows.append(
                    (
                        node_id,
                        float(lat),
                        float(lon),
                        math.floor(float(lat) / SQLiteRoadGraph.CELL_DEG),
                        math.floor(float(lon) / SQLiteRoadGraph.CELL_DEG),
                    )
                )
            self.way_rows.append(
                (
                    way_id,
                    json.dumps([point[0] for point in points]),
                    _optional_way_tag(oneway),
                    _optional_way_tag(highway),
                    way_name,
                    way_ref,
                    way_route,
                )
            )
            if is_ferry:
                self.ferry_way_n += 1
                self.ferry_node_refs += len(points)
                opening_hours = str(way.tags.get("opening_hours", "")).strip()
                duration_s = parse_duration_seconds(way.tags.get("duration"))
                if opening_hours or duration_s is not None:
                    entry: dict[str, object] = {"way_id": way_id}
                    if opening_hours:
                        entry["opening_hours"] = opening_hours
                        schedule = parse_weekly_schedule(opening_hours, allow_calendar=True)
                        if schedule is None:
                            self.ferry_schedule_unsupported_n += 1
                        else:
                            self.ferry_schedule_n += 1
                            if schedule.requires_public_holiday_calendar:
                                self.ferry_public_holiday_schedule_n += 1
                    if duration_s is not None:
                        entry["duration_s"] = duration_s
                        self.ferry_duration_n += 1
                    self.ferry_schedule_rows.append(entry)
                for first, second in pairwise(points):
                    length = straight_distance(
                        {"lat": str(first[1]), "lon": str(first[2])},
                        {"lat": str(second[1]), "lon": str(second[2])},
                    )
                    self.ferry_edge_rows.append((first[0], second[0], length, oneway, way_id))
                    self.ferry_segment_n += 1
                if len(self.ferry_edge_rows) >= 100_000:
                    self.flush()
                return
            maxweight_status, maxweight_t = parse_maxweight_profile(
                way.tags.get("maxweight", "")
            )
            maxweight_hgv_status, maxweight_hgv_t = parse_maxweight_profile(
                way.tags.get("maxweight:hgv", "")
            )
            maxweightrating_hgv_status, maxweightrating_hgv_t = (
                parse_maxweightrating_hgv_profiles(
                    way.tags.get("maxweightrating:hgv", ""),
                    way.tags.get("maxweightrating:goods", ""),
                )
            )
            hgv_destination_entries = parse_hgv_destination_profiles(way.tags)
            hgv_destination_json = json.dumps(
                list(hgv_destination_entries),
                separators=(",", ":"),
            )
            if hgv_destination_entries:
                self.hgv_destination_way_n += 1
                if all(
                    str(entry.get("status"))
                    in {MAXWEIGHT_STATUS_SUPPORTED, MAXWEIGHT_STATUS_UNLIMITED}
                    for entry in hgv_destination_entries
                ):
                    self.hgv_destination_supported_way_n += 1
                else:
                    self.hgv_destination_unsupported_way_n += 1
            maxheight_status, maxheight_m = parse_maxheight_profile(
                way.tags.get("maxheight", "")
            )
            maxheight_physical_status, maxheight_physical_m = parse_maxheight_profile(
                way.tags.get("maxheight:physical", "")
            )
            maxwidth_status, maxwidth_m = parse_maxwidth_profile(
                way.tags.get("maxwidth", "")
            )
            maxlength_status, maxlength_m = parse_maxlength_profile(
                way.tags.get("maxlength", "")
            )
            maxaxleload_status, maxaxleload_t = parse_maxaxleload_profile(
                way.tags.get("maxaxleload", "")
            )
            maxspeed_status, maxspeed_kmh = parse_maxspeed_profile(
                way.tags.get("maxspeed", "")
            )
            maxspeed_conditional_entries = parse_maxspeed_conditional_profile(
                way.tags.get("maxspeed:conditional", "")
            )
            maxspeed_conditional_json = json.dumps(
                list(maxspeed_conditional_entries),
                separators=(",", ":"),
            )
            oneway_conditional_entries = parse_oneway_conditional_profiles(
                way.tags.get("oneway:conditional", ""),
                way.tags.get("oneway:motor_vehicle:conditional", ""),
            )
            oneway_conditional_json = json.dumps(
                list(oneway_conditional_entries),
                separators=(",", ":"),
            )
            if oneway_conditional_entries:
                self.oneway_conditional_way_n += 1
                if all(
                    str(entry.get("status")) == MAXWEIGHT_STATUS_SUPPORTED
                    for entry in oneway_conditional_entries
                ):
                    self.oneway_conditional_supported_way_n += 1
                else:
                    self.oneway_conditional_unsupported_way_n += 1
            if maxspeed_conditional_entries:
                self.maxspeed_conditional_way_n += 1
                if all(
                    str(entry.get("status"))
                    in {MAXWEIGHT_STATUS_SUPPORTED, MAXWEIGHT_STATUS_UNLIMITED}
                    for entry in maxspeed_conditional_entries
                ):
                    self.maxspeed_conditional_supported_way_n += 1
                else:
                    self.maxspeed_conditional_unsupported_way_n += 1
            if maxweight_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                self.maxweight_way_n += 1
                if maxweight_status == MAXWEIGHT_STATUS_SUPPORTED:
                    self.maxweight_supported_way_n += 1
                elif maxweight_status == MAXWEIGHT_STATUS_UNLIMITED:
                    self.maxweight_unlimited_way_n += 1
                else:
                    self.maxweight_unsupported_way_n += 1
            if maxweight_hgv_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                self.maxweight_hgv_way_n += 1
                if maxweight_hgv_status == MAXWEIGHT_STATUS_SUPPORTED:
                    self.maxweight_hgv_supported_way_n += 1
                elif maxweight_hgv_status == MAXWEIGHT_STATUS_UNLIMITED:
                    self.maxweight_hgv_unlimited_way_n += 1
                else:
                    self.maxweight_hgv_unsupported_way_n += 1
            if maxweightrating_hgv_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                self.maxweightrating_hgv_way_n += 1
                if maxweightrating_hgv_status == MAXWEIGHT_STATUS_SUPPORTED:
                    self.maxweightrating_hgv_supported_way_n += 1
                elif maxweightrating_hgv_status == MAXWEIGHT_STATUS_UNLIMITED:
                    self.maxweightrating_hgv_unlimited_way_n += 1
                else:
                    self.maxweightrating_hgv_unsupported_way_n += 1
            if maxheight_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                self.maxheight_way_n += 1
                if maxheight_status == MAXWEIGHT_STATUS_SUPPORTED:
                    self.maxheight_supported_way_n += 1
                elif maxheight_status == MAXWEIGHT_STATUS_UNLIMITED:
                    self.maxheight_unlimited_way_n += 1
                else:
                    self.maxheight_unsupported_way_n += 1
            if maxheight_physical_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                self.maxheight_physical_way_n += 1
                if maxheight_physical_status == MAXWEIGHT_STATUS_SUPPORTED:
                    self.maxheight_physical_supported_way_n += 1
                elif maxheight_physical_status == MAXWEIGHT_STATUS_UNLIMITED:
                    self.maxheight_physical_unlimited_way_n += 1
                else:
                    self.maxheight_physical_unsupported_way_n += 1
            for name, status in (
                ("maxwidth", maxwidth_status),
                ("maxlength", maxlength_status),
                ("maxaxleload", maxaxleload_status),
                ("maxspeed", maxspeed_status),
            ):
                if status == MAXWEIGHT_STATUS_NOT_PROVIDED:
                    continue
                setattr(self, f"{name}_way_n", getattr(self, f"{name}_way_n") + 1)
                suffix = {
                    MAXWEIGHT_STATUS_SUPPORTED: "supported_way_n",
                    MAXWEIGHT_STATUS_UNLIMITED: "unlimited_way_n",
                }.get(status, "unsupported_way_n")
                setattr(
                    self,
                    f"{name}_{suffix}",
                    getattr(self, f"{name}_{suffix}") + 1,
                )
            self.way_n += 1
            self.node_refs += len(points)
            for rule_n, (mode, vehicle_class, condition, direction) in enumerate(
                access_rules
            ):
                self.conditional_access_n += 1
                self.conditional_access_vehicle_class_n[vehicle_class] += 1
                self.conditional_access_direction_n[direction] += 1
                self.conditional_access_rows.append(
                    (way_id, direction, condition, mode, vehicle_class, rule_n)
                )
                if parse_vehicle_weight_condition(condition) is not None:
                    self.conditional_access_weight_n += 1
            for first, second in pairwise(points):
                length = straight_distance(
                    {"lat": str(first[1]), "lon": str(first[2])},
                    {"lat": str(second[1]), "lon": str(second[2])},
                )
                self.edge_rows.append(
                    (
                        first[0],
                        second[0],
                        length,
                        oneway,
                        way_id,
                        maxweight_t,
                        maxweight_status,
                        maxweight_hgv_t,
                        maxweight_hgv_status,
                        maxheight_m,
                        maxheight_status,
                        maxheight_physical_m,
                        maxheight_physical_status,
                        maxwidth_m,
                        maxwidth_status,
                        maxlength_m,
                        maxlength_status,
                        maxaxleload_t,
                        maxaxleload_status,
                        maxspeed_kmh,
                        maxspeed_status,
                        maxspeed_conditional_json,
                        oneway_conditional_json,
                        maxweightrating_hgv_t,
                        maxweightrating_hgv_status,
                        hgv_destination_json,
                    )
                )
                self.segment_n += 1
                if maxweight_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                    self.maxweight_segment_n += 1
                if maxweight_hgv_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                    self.maxweight_hgv_segment_n += 1
                if maxweightrating_hgv_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                    self.maxweightrating_hgv_segment_n += 1
                if hgv_destination_entries:
                    self.hgv_destination_segment_n += 1
                if maxheight_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                    self.maxheight_segment_n += 1
                if maxheight_physical_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                    self.maxheight_physical_segment_n += 1
                if maxwidth_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                    self.maxwidth_segment_n += 1
                if maxlength_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                    self.maxlength_segment_n += 1
                if maxaxleload_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                    self.maxaxleload_segment_n += 1
                if maxspeed_status != MAXWEIGHT_STATUS_NOT_PROVIDED:
                    self.maxspeed_segment_n += 1
                if maxspeed_conditional_entries:
                    self.maxspeed_conditional_segment_n += 1
                if oneway_conditional_entries:
                    self.oneway_conditional_segment_n += 1
            if len(self.edge_rows) >= 100_000:
                self.flush()

        def relation(self, relation) -> None:
            relation_type = str(relation.tags.get("type", ""))
            if relation_type == "route" and str(relation.tags.get("route", "")).lower() == "ferry":
                self.ferry_relation_n += 1
            if relation_type != "restriction":
                return
            self.restriction_relation_n += 1
            conditional_value = next(
                (
                    relation.tags.get(key)
                    for key in (
                        "restriction:motor_vehicle:conditional",
                        "restriction:motorcar:conditional",
                        "restriction:vehicle:conditional",
                        "restriction:conditional",
                    )
                    if relation.tags.get(key)
                ),
                None,
            )
            condition = ""
            if conditional_value:
                self.restriction_conditional_n += 1
                parsed = parse_conditional_restriction(str(conditional_value))
                if parsed is None:
                    self.restriction_conditional_unsupported_n += 1
                    return
                kind, condition = parsed
                self.restriction_conditional_supported_n += 1
                if parse_vehicle_weight_condition(condition) is not None:
                    self.restriction_conditional_weight_n += 1
            else:
                restriction = str(
                    relation.tags.get("restriction:motor_vehicle", "")
                    or relation.tags.get("restriction:motorcar", "")
                    or relation.tags.get("restriction", "")
                ).lower()
                if restriction.startswith("no_"):
                    kind = "no"
                elif restriction.startswith("only_"):
                    kind = "only"
                else:
                    self.restriction_unsupported_n += 1
                    return
            from_ways = [str(member.ref) for member in relation.members if member.role == "from" and member.type == "w"]
            to_ways = [str(member.ref) for member in relation.members if member.role == "to" and member.type == "w"]
            via_nodes = [str(member.ref) for member in relation.members if member.role == "via" and member.type == "n"]
            via_ways = [str(member.ref) for member in relation.members if member.role == "via" and member.type == "w"]
            if via_ways:
                self.restriction_via_way_n += 1
                if len(from_ways) != 1 or len(to_ways) != 1 or via_nodes:
                    self.restriction_via_way_unsupported_n += 1
                    self.restriction_unsupported_n += 1
                    return
                row = (f"relation/{relation.id}", "", from_ways[0], to_ways[0], kind, json.dumps(via_ways))
                if condition:
                    self.conditional_restriction_rows.append((*row, condition))
                else:
                    self.restriction_rows.append(row)
                return
            if len(from_ways) != 1 or len(to_ways) != 1 or len(via_nodes) != 1:
                self.restriction_unsupported_n += 1
                return
            row = (f"relation/{relation.id}", via_nodes[0], from_ways[0], to_ways[0], kind, "[]")
            if condition:
                self.conditional_restriction_rows.append((*row, condition))
            else:
                self.restriction_rows.append(row)

    handler = Handler()
    try:
        handler.apply_file(str(path), locations=True)
        handler.flush()
        way_sequences: dict[str, list[str]] = {}
        restriction_rows: list[tuple[str, str, str, str, str, str]] = []
        conditional_restriction_rows: list[tuple[str, str, str, str, str, str, str]] = []
        unresolved_restriction_n = 0
        source_rows = (*handler.restriction_rows, *handler.conditional_restriction_rows)
        referenced_way_ids = {
            way_id
            for _relation_id, _via_node, from_way, to_way, _kind, via_way_json, *_condition in source_rows
            for way_id in (from_way, to_way, *json.loads(via_way_json))
        }
        for way_id in referenced_way_ids:
            row = connection.execute(
                "SELECT node_json FROM ways WHERE way_id = ?",
                (way_id,),
            ).fetchone()
            if row is not None:
                try:
                    way_sequences[way_id] = [str(node_id) for node_id in json.loads(row[0])]
                except (TypeError, ValueError, json.JSONDecodeError):
                    way_sequences[way_id] = []
        def resolve_row(
            relation_id: str,
            via_node: str,
            from_way: str,
            to_way: str,
            kind: str,
            via_way_json: str,
            *,
            conditional: bool = False,
        ) -> tuple[str, str, str, str, str, str] | None:
            nonlocal unresolved_restriction_n
            via_ways = tuple(str(value) for value in json.loads(via_way_json))
            if via_ways:
                sequence_ids = (from_way, *via_ways, to_way)
                sequences = [way_sequences.get(way_id, []) for way_id in sequence_ids]
                if any(not sequence for sequence in sequences):
                    handler.restriction_via_way_unresolved_n += 1
                    if conditional:
                        handler.restriction_conditional_unresolved_n += 1
                    unresolved_restriction_n += 1
                    return None
                junction_nodes: list[str] = []
                valid_chain = True
                for first, second in pairwise(sequences):
                    shared_endpoints = {first[0], first[-1]} & {second[0], second[-1]}
                    if len(shared_endpoints) != 1:
                        valid_chain = False
                        break
                    junction_nodes.append(next(iter(shared_endpoints)))
                if not valid_chain:
                    handler.restriction_via_way_unsupported_n += 1
                    if conditional:
                        handler.restriction_conditional_geometry_unsupported_n += 1
                    handler.restriction_unsupported_n += 1
                    return None
                via_node = junction_nodes[-1]
                if not connection.execute(
                    "SELECT 1 FROM nodes WHERE node_id = ?",
                    (via_node,),
                ).fetchone():
                    handler.restriction_via_way_unresolved_n += 1
                    if conditional:
                        handler.restriction_conditional_unresolved_n += 1
                    unresolved_restriction_n += 1
                    return None
                return (relation_id, via_node, from_way, to_way, kind, json.dumps(list(via_ways)))
            from_sequence = way_sequences.get(from_way, [])
            to_sequence = way_sequences.get(to_way, [])
            via_exists = connection.execute(
                "SELECT 1 FROM nodes WHERE node_id = ?",
                (via_node,),
            ).fetchone()
            if not from_sequence or not to_sequence or not via_exists:
                if conditional:
                    handler.restriction_conditional_unresolved_n += 1
                unresolved_restriction_n += 1
                return None
            if (
                via_node not in (from_sequence[0], from_sequence[-1])
                or via_node not in (to_sequence[0], to_sequence[-1])
            ):
                if conditional:
                    handler.restriction_conditional_geometry_unsupported_n += 1
                handler.restriction_unsupported_n += 1
                return None
            return (relation_id, via_node, from_way, to_way, kind, "[]")

        for row in handler.restriction_rows:
            resolved = resolve_row(*row)
            if resolved is not None:
                restriction_rows.append(resolved)
        for row in handler.conditional_restriction_rows:
            resolved = resolve_row(*row[:6], conditional=True)
            if resolved is not None:
                conditional_restriction_rows.append((*resolved, row[6]))
        if restriction_rows:
            connection.executemany(
                "INSERT INTO turn_restrictions(relation_id, via_node, from_way, to_way, kind, via_way_json) VALUES (?, ?, ?, ?, ?, ?)",
                restriction_rows,
            )
        if conditional_restriction_rows:
            connection.executemany(
                "INSERT INTO conditional_turn_restrictions(relation_id, via_node, from_way, to_way, kind, via_way_json, condition) VALUES (?, ?, ?, ?, ?, ?, ?)",
                conditional_restriction_rows,
            )
        connection.execute("CREATE INDEX nodes_cell ON nodes(cell_lat, cell_lon)")
        connection.execute("CREATE INDEX edges_u ON edges(u)")
        connection.execute("CREATE INDEX edges_v ON edges(v)")
        connection.execute(
            "CREATE INDEX edges_hgv_destination ON edges(way_id) "
            "WHERE hgv_destination_json != '[]'"
        )
        connection.execute("CREATE INDEX ferry_edges_u ON ferry_edges(u)")
        connection.execute("CREATE INDEX ferry_edges_v ON ferry_edges(v)")
        connection.execute("CREATE INDEX turns_via_from ON turn_restrictions(via_node, from_way)")
        connection.execute(
            "CREATE INDEX conditional_turns_via_from ON conditional_turn_restrictions(via_node, from_way)"
        )
        connection.execute("ANALYZE")
        node_n = int(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
        edge_n = int(connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
        ferry_edge_n = int(connection.execute("SELECT COUNT(*) FROM ferry_edges").fetchone()[0])
        turn_restriction_n = int(connection.execute("SELECT COUNT(*) FROM turn_restrictions").fetchone()[0])
        restriction_via_way_applied_n = int(connection.execute(
            "SELECT COUNT(*) FROM turn_restrictions WHERE via_way_json != '[]'"
        ).fetchone()[0])
        conditional_restriction_n = int(
            connection.execute("SELECT COUNT(*) FROM conditional_turn_restrictions").fetchone()[0]
        )
        conditional_via_way_applied_n = int(connection.execute(
            "SELECT COUNT(*) FROM conditional_turn_restrictions WHERE via_way_json != '[]'"
        ).fetchone()[0])
        restriction_via_way_applied_n += conditional_via_way_applied_n
        connection.commit()
        connection.close()
        os.replace(temporary, database)
    except Exception:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    metadata = {
        "format": ROAD_GRAPH_FORMAT,
        "storage": "sqlite",
        "directed": True,
        "source": str(path),
        "node_n": node_n,
        "edge_n": edge_n,
        "way_n": handler.way_n,
        "way_context": True,
        "way_context_n": handler.way_n + handler.ferry_way_n,
        "segment_n": handler.segment_n,
        "node_refs": handler.node_refs,
        "ferry_way_n": handler.ferry_way_n,
        "ferry_segment_n": handler.ferry_segment_n,
        "ferry_node_refs": handler.ferry_node_refs,
        "ferry_edge_n": ferry_edge_n,
        "ferry_relation_n": handler.ferry_relation_n,
        "ferry_schedules_modeled": True,
        "ferry_schedule_contract": FERRY_SCHEDULE_CONTRACT,
        "ferry_schedule_n": handler.ferry_schedule_n,
        "ferry_schedule_unsupported_n": handler.ferry_schedule_unsupported_n,
        "ferry_public_holiday_schedule_n": handler.ferry_public_holiday_schedule_n,
        "public_holiday_contract": public_holiday_status,
        "public_holiday_n": len(public_holidays),
        "public_holiday_min_date": public_holiday_min_date,
        "public_holiday_max_date": public_holiday_max_date,
        "ferry_duration_n": handler.ferry_duration_n,
        "conditional_access_n": handler.conditional_access_n,
        "conditional_access_vehicle_class_n": dict(handler.conditional_access_vehicle_class_n),
        "conditional_access_direction_n": dict(handler.conditional_access_direction_n),
        "conditional_access_weight_n": handler.conditional_access_weight_n,
        "conditional_access_multiclause_n": handler.conditional_access_multiclause_n,
        "conditional_access_unsupported_n": handler.conditional_access_unsupported_n,
        "conditional_access_excluded_n": handler.conditional_access_excluded_n,
        "maxweight_way_n": handler.maxweight_way_n,
        "maxweight_supported_way_n": handler.maxweight_supported_way_n,
        "maxweight_unlimited_way_n": handler.maxweight_unlimited_way_n,
        "maxweight_unsupported_way_n": handler.maxweight_unsupported_way_n,
        "maxweight_segment_n": handler.maxweight_segment_n,
        "maxweight_hgv_way_n": handler.maxweight_hgv_way_n,
        "maxweight_hgv_supported_way_n": handler.maxweight_hgv_supported_way_n,
        "maxweight_hgv_unlimited_way_n": handler.maxweight_hgv_unlimited_way_n,
        "maxweight_hgv_unsupported_way_n": handler.maxweight_hgv_unsupported_way_n,
        "maxweight_hgv_segment_n": handler.maxweight_hgv_segment_n,
        "maxweightrating_hgv_way_n": handler.maxweightrating_hgv_way_n,
        "maxweightrating_hgv_supported_way_n": handler.maxweightrating_hgv_supported_way_n,
        "maxweightrating_hgv_unlimited_way_n": handler.maxweightrating_hgv_unlimited_way_n,
        "maxweightrating_hgv_unsupported_way_n": handler.maxweightrating_hgv_unsupported_way_n,
        "maxweightrating_hgv_segment_n": handler.maxweightrating_hgv_segment_n,
        "hgv_destination_way_n": handler.hgv_destination_way_n,
        "hgv_destination_supported_way_n": handler.hgv_destination_supported_way_n,
        "hgv_destination_unsupported_way_n": handler.hgv_destination_unsupported_way_n,
        "hgv_destination_segment_n": handler.hgv_destination_segment_n,
        "maxheight_way_n": handler.maxheight_way_n,
        "maxheight_supported_way_n": handler.maxheight_supported_way_n,
        "maxheight_unlimited_way_n": handler.maxheight_unlimited_way_n,
        "maxheight_unsupported_way_n": handler.maxheight_unsupported_way_n,
        "maxheight_segment_n": handler.maxheight_segment_n,
        "maxheight_physical_way_n": handler.maxheight_physical_way_n,
        "maxheight_physical_supported_way_n": handler.maxheight_physical_supported_way_n,
        "maxheight_physical_unlimited_way_n": handler.maxheight_physical_unlimited_way_n,
        "maxheight_physical_unsupported_way_n": handler.maxheight_physical_unsupported_way_n,
        "maxheight_physical_segment_n": handler.maxheight_physical_segment_n,
        "maxwidth_way_n": handler.maxwidth_way_n,
        "maxwidth_supported_way_n": handler.maxwidth_supported_way_n,
        "maxwidth_unlimited_way_n": handler.maxwidth_unlimited_way_n,
        "maxwidth_unsupported_way_n": handler.maxwidth_unsupported_way_n,
        "maxwidth_segment_n": handler.maxwidth_segment_n,
        "maxlength_way_n": handler.maxlength_way_n,
        "maxlength_supported_way_n": handler.maxlength_supported_way_n,
        "maxlength_unlimited_way_n": handler.maxlength_unlimited_way_n,
        "maxlength_unsupported_way_n": handler.maxlength_unsupported_way_n,
        "maxlength_segment_n": handler.maxlength_segment_n,
        "maxaxleload_way_n": handler.maxaxleload_way_n,
        "maxaxleload_supported_way_n": handler.maxaxleload_supported_way_n,
        "maxaxleload_unlimited_way_n": handler.maxaxleload_unlimited_way_n,
        "maxaxleload_unsupported_way_n": handler.maxaxleload_unsupported_way_n,
        "maxaxleload_segment_n": handler.maxaxleload_segment_n,
        "maxspeed_way_n": handler.maxspeed_way_n,
        "maxspeed_supported_way_n": handler.maxspeed_supported_way_n,
        "maxspeed_unlimited_way_n": handler.maxspeed_unlimited_way_n,
        "maxspeed_unsupported_way_n": handler.maxspeed_unsupported_way_n,
        "maxspeed_segment_n": handler.maxspeed_segment_n,
        "maxspeed_conditional_way_n": handler.maxspeed_conditional_way_n,
        "maxspeed_conditional_supported_way_n": handler.maxspeed_conditional_supported_way_n,
        "maxspeed_conditional_unsupported_way_n": handler.maxspeed_conditional_unsupported_way_n,
        "maxspeed_conditional_segment_n": handler.maxspeed_conditional_segment_n,
        "oneway_conditional_way_n": handler.oneway_conditional_way_n,
        "oneway_conditional_supported_way_n": handler.oneway_conditional_supported_way_n,
        "oneway_conditional_unsupported_way_n": handler.oneway_conditional_unsupported_way_n,
        "oneway_conditional_segment_n": handler.oneway_conditional_segment_n,
        "max_ways": max_ways,
        "complete": max_ways == 0,
        "include_restricted": include_restricted,
        "excluded_access_way_n": handler.excluded_access_way_n,
        "turn_restriction_n": turn_restriction_n,
        "restriction_relation_n": handler.restriction_relation_n,
        "restriction_applied_n": turn_restriction_n,
        "restriction_unresolved_n": unresolved_restriction_n,
        "restriction_conditional_n": handler.restriction_conditional_n,
        "restriction_conditional_supported_n": handler.restriction_conditional_supported_n,
        "restriction_conditional_weight_n": handler.restriction_conditional_weight_n,
        "restriction_conditional_unsupported_n": handler.restriction_conditional_unsupported_n,
        "restriction_conditional_stored_n": conditional_restriction_n,
        "restriction_conditional_unresolved_n": handler.restriction_conditional_unresolved_n,
        "restriction_conditional_geometry_unsupported_n": handler.restriction_conditional_geometry_unsupported_n,
        "restriction_via_way_n": handler.restriction_via_way_n,
        "restriction_via_way_applied_n": restriction_via_way_applied_n,
        "restriction_via_way_unresolved_n": handler.restriction_via_way_unresolved_n,
        "restriction_via_way_unsupported_n": handler.restriction_via_way_unsupported_n,
        "restriction_unsupported_n": handler.restriction_unsupported_n,
        "turn_restrictions_supported": True,
    }
    atomic_write_json(
        output / "ferry_schedules.json",
        {
            "contract": FERRY_SCHEDULE_CONTRACT,
            "source": str(path),
            "schedules": sorted(
                handler.ferry_schedule_rows,
                key=lambda entry: str(entry.get("way_id", "")),
            ),
        },
        indent=2,
    )
    atomic_write_json(output / "road_graph_metadata.json", metadata, indent=2)
    return {
        "node_n": node_n,
        "edge_n": edge_n,
        "way_n": handler.way_n,
        "ferry_way_n": handler.ferry_way_n,
        "ferry_edge_n": ferry_edge_n,
        "ferry_relation_n": handler.ferry_relation_n,
        "ferry_schedule_n": handler.ferry_schedule_n,
        "ferry_schedule_unsupported_n": handler.ferry_schedule_unsupported_n,
        "ferry_public_holiday_schedule_n": handler.ferry_public_holiday_schedule_n,
        "ferry_duration_n": handler.ferry_duration_n,
        "conditional_access_n": handler.conditional_access_n,
        "conditional_access_vehicle_class_n": dict(handler.conditional_access_vehicle_class_n),
        "conditional_access_direction_n": dict(handler.conditional_access_direction_n),
        "conditional_access_weight_n": handler.conditional_access_weight_n,
        "conditional_access_multiclause_n": handler.conditional_access_multiclause_n,
        "conditional_access_unsupported_n": handler.conditional_access_unsupported_n,
        "conditional_access_excluded_n": handler.conditional_access_excluded_n,
        "maxweight_way_n": handler.maxweight_way_n,
        "maxweight_supported_way_n": handler.maxweight_supported_way_n,
        "maxweight_unlimited_way_n": handler.maxweight_unlimited_way_n,
        "maxweight_unsupported_way_n": handler.maxweight_unsupported_way_n,
        "maxweight_segment_n": handler.maxweight_segment_n,
        "maxweight_hgv_way_n": handler.maxweight_hgv_way_n,
        "maxweight_hgv_supported_way_n": handler.maxweight_hgv_supported_way_n,
        "maxweight_hgv_unlimited_way_n": handler.maxweight_hgv_unlimited_way_n,
        "maxweight_hgv_unsupported_way_n": handler.maxweight_hgv_unsupported_way_n,
        "maxweight_hgv_segment_n": handler.maxweight_hgv_segment_n,
        "maxweightrating_hgv_way_n": handler.maxweightrating_hgv_way_n,
        "maxweightrating_hgv_supported_way_n": handler.maxweightrating_hgv_supported_way_n,
        "maxweightrating_hgv_unlimited_way_n": handler.maxweightrating_hgv_unlimited_way_n,
        "maxweightrating_hgv_unsupported_way_n": handler.maxweightrating_hgv_unsupported_way_n,
        "maxweightrating_hgv_segment_n": handler.maxweightrating_hgv_segment_n,
        "hgv_destination_way_n": handler.hgv_destination_way_n,
        "hgv_destination_supported_way_n": handler.hgv_destination_supported_way_n,
        "hgv_destination_unsupported_way_n": handler.hgv_destination_unsupported_way_n,
        "hgv_destination_segment_n": handler.hgv_destination_segment_n,
        "maxheight_way_n": handler.maxheight_way_n,
        "maxheight_supported_way_n": handler.maxheight_supported_way_n,
        "maxheight_unlimited_way_n": handler.maxheight_unlimited_way_n,
        "maxheight_unsupported_way_n": handler.maxheight_unsupported_way_n,
        "maxheight_segment_n": handler.maxheight_segment_n,
        "maxheight_physical_way_n": handler.maxheight_physical_way_n,
        "maxheight_physical_supported_way_n": handler.maxheight_physical_supported_way_n,
        "maxheight_physical_unlimited_way_n": handler.maxheight_physical_unlimited_way_n,
        "maxheight_physical_unsupported_way_n": handler.maxheight_physical_unsupported_way_n,
        "maxheight_physical_segment_n": handler.maxheight_physical_segment_n,
        "maxwidth_way_n": handler.maxwidth_way_n,
        "maxwidth_supported_way_n": handler.maxwidth_supported_way_n,
        "maxwidth_unlimited_way_n": handler.maxwidth_unlimited_way_n,
        "maxwidth_unsupported_way_n": handler.maxwidth_unsupported_way_n,
        "maxwidth_segment_n": handler.maxwidth_segment_n,
        "maxlength_way_n": handler.maxlength_way_n,
        "maxlength_supported_way_n": handler.maxlength_supported_way_n,
        "maxlength_unlimited_way_n": handler.maxlength_unlimited_way_n,
        "maxlength_unsupported_way_n": handler.maxlength_unsupported_way_n,
        "maxlength_segment_n": handler.maxlength_segment_n,
        "maxaxleload_way_n": handler.maxaxleload_way_n,
        "maxaxleload_supported_way_n": handler.maxaxleload_supported_way_n,
        "maxaxleload_unlimited_way_n": handler.maxaxleload_unlimited_way_n,
        "maxaxleload_unsupported_way_n": handler.maxaxleload_unsupported_way_n,
        "maxaxleload_segment_n": handler.maxaxleload_segment_n,
        "maxspeed_way_n": handler.maxspeed_way_n,
        "maxspeed_supported_way_n": handler.maxspeed_supported_way_n,
        "maxspeed_unlimited_way_n": handler.maxspeed_unlimited_way_n,
        "maxspeed_unsupported_way_n": handler.maxspeed_unsupported_way_n,
        "maxspeed_segment_n": handler.maxspeed_segment_n,
        "maxspeed_conditional_way_n": handler.maxspeed_conditional_way_n,
        "maxspeed_conditional_supported_way_n": handler.maxspeed_conditional_supported_way_n,
        "maxspeed_conditional_unsupported_way_n": handler.maxspeed_conditional_unsupported_way_n,
        "maxspeed_conditional_segment_n": handler.maxspeed_conditional_segment_n,
        "oneway_conditional_way_n": handler.oneway_conditional_way_n,
        "oneway_conditional_supported_way_n": handler.oneway_conditional_supported_way_n,
        "oneway_conditional_unsupported_way_n": handler.oneway_conditional_unsupported_way_n,
        "oneway_conditional_segment_n": handler.oneway_conditional_segment_n,
        "turn_restriction_n": turn_restriction_n,
        "restriction_conditional_stored_n": conditional_restriction_n,
        "restriction_via_way_applied_n": restriction_via_way_applied_n,
        "complete": max_ways == 0,
    }


def write_graph(
    path: Path,
    coordinates: dict[str, tuple[float, float]] | PortableRoadGraph,
    adjacency: dict[str, list[tuple[str, float]]] | None = None,
    *,
    source: str,
) -> None:
    """Persist a directed graph in the portable node/edge contract."""
    portable_graph = coordinates if isinstance(coordinates, PortableRoadGraph) else None
    if portable_graph is not None:
        coordinates = portable_graph.coordinates
        adjacency = portable_graph.adjacency
        edge_rows = [
            {
                "u": edge.from_node,
                "v": edge.to_node,
                "length_m": round(edge.length_m, 3),
                "way_id": edge.way_id,
                **(
                    edge.road_context
                    if edge.road_context is not None
                    else {}
                ),
                "oneway": "yes",
            }
            for node in sorted(portable_graph.edges_by_from)
            for edge in sorted(
                portable_graph.edges_by_from[node],
                key=lambda item: (item.to_node, item.length_m, item.way_id, item.direction),
            )
        ]
    else:
        if adjacency is None:
            raise ValueError("portable graph adjacency is required")
        edge_rows = [
            {"u": node, "v": neighbour, "length_m": round(length, 3), "oneway": "yes"}
            for node in sorted(adjacency)
            for neighbour, length in sorted(adjacency[node], key=lambda item: (item[0], item[1]))
        ]
    path.mkdir(parents=True, exist_ok=True)
    node_rows = [
        {"node_id": node, "lat": lat, "lon": lon}
        for node, (lat, lon) in sorted(coordinates.items())
    ]
    edge_fields = ["u", "v", "length_m", "oneway", "way_id", "name", "ref", "highway", "route"]
    atomic_write_csv(path / "road_nodes.csv", ["node_id", "lat", "lon"], node_rows)
    atomic_write_csv(path / "road_edges.csv", edge_fields, edge_rows)
    atomic_write_json(
        path / "road_graph_metadata.json",
        {
            "format": "ireland-geometry-road-graph-v1",
            "directed": True,
            "source": source,
            "node_n": len(node_rows),
            "edge_n": len(edge_rows),
            "way_context": bool(portable_graph and portable_graph.has_way_context),
            "way_context_n": portable_graph.way_context_n if portable_graph else 0,
            "path_segment_source": (
                "portable_edges"
                if portable_graph and portable_graph.has_way_ids
                else "not_available"
            ),
            "ferry_edge_n": portable_graph.ferry_edge_count if portable_graph else 0,
        },
        indent=2,
    )


@dataclass(frozen=True)
class NodeIndex:
    """Deterministic nearest-node index with a coarse 2-D spatial grid.

    The latitude-sorted arrays are retained as a compatibility fallback.  The
    grid avoids scanning every road node in a latitude band, which becomes
    expensive on country-scale graphs with more than a million nodes.
    """

    latitudes: list[float]
    nodes: list[str]
    cells: dict[tuple[int, int], tuple[str, ...]]
    bounds: tuple[int, int, int, int] | None
    cell_deg: float = 0.01


def build_node_index(coordinates: dict[str, tuple[float, float]]) -> NodeIndex:
    """Build a deterministic 2-D grid plus a latitude-sorted fallback index."""
    cell_deg = 0.01
    ordered = sorted((lat, node) for node, (lat, _lon) in coordinates.items())
    cells: dict[tuple[int, int], list[str]] = defaultdict(list)
    for node, (lat, lon) in coordinates.items():
        cells[(math.floor(lat / cell_deg), math.floor(lon / cell_deg))].append(node)
    return NodeIndex(
        latitudes=[lat for lat, _node in ordered],
        nodes=[node for _lat, node in ordered],
        cells={key: tuple(sorted(value)) for key, value in cells.items()},
        bounds=(
            min(key[0] for key in cells),
            max(key[0] for key in cells),
            min(key[1] for key in cells),
            max(key[1] for key in cells),
        ) if cells else None,
        cell_deg=cell_deg,
    )


def _nearest_node_from_grid(
    row: dict[str, str],
    coordinates: dict[str, tuple[float, float]],
    index: NodeIndex,
) -> str | None:
    lat = number(row.get("lat"))
    lon = number(row.get("lon"))
    cell_deg = index.cell_deg
    cell_lat = math.floor(lat / cell_deg)
    cell_lon = math.floor(lon / cell_deg)
    best: str | None = None
    best_distance = float("inf")
    ring = 0
    if index.bounds is None:
        return None
    min_lat_cell, max_lat_cell, min_lon_cell, max_lon_cell = index.bounds
    max_ring = max(
        abs(cell_lat - min_lat_cell),
        abs(cell_lat - max_lat_cell),
        abs(cell_lon - min_lon_cell),
        abs(cell_lon - max_lon_cell),
    )
    while ring <= max_ring:
        for lat_cell in range(cell_lat - ring, cell_lat + ring + 1):
            for lon_cell in range(cell_lon - ring, cell_lon + ring + 1):
                if max(abs(lat_cell - cell_lat), abs(lon_cell - cell_lon)) != ring:
                    continue
                for node in index.cells.get((lat_cell, lon_cell), ()):
                    node_lat, node_lon = coordinates[node]
                    distance = (node_lat - lat) ** 2 + (node_lon - lon) ** 2
                    if distance < best_distance or (distance == best_distance and node < (best or node)):
                        best = node
                        best_distance = distance
        if best is not None:
            lat_min = (cell_lat - ring) * cell_deg
            lat_max = (cell_lat + ring + 1) * cell_deg
            lon_min = (cell_lon - ring) * cell_deg
            lon_max = (cell_lon + ring + 1) * cell_deg
            outside_distance = min(
                lat - lat_min,
                lat_max - lat,
                lon - lon_min,
                lon_max - lon,
            ) ** 2
            if best_distance <= outside_distance:
                return best
        ring += 1
    return best


def nearest_node(
    row: dict[str, str],
    coordinates: dict[str, tuple[float, float]] | SQLiteRoadGraph,
    index: NodeIndex | tuple[list[float], list[str]] | None = None,
) -> str | None:
    if isinstance(coordinates, SQLiteRoadGraph):
        return coordinates.nearest_node(row)
    if not coordinates:
        return None
    lat = number(row.get("lat"))
    lon = number(row.get("lon"))
    if index is None:
        return min(
            coordinates,
            key=lambda node: (coordinates[node][0] - lat) ** 2 + (coordinates[node][1] - lon) ** 2,
        )
    if isinstance(index, NodeIndex):
        return _nearest_node_from_grid(row, coordinates, index)
    latitudes, nodes = index
    if not latitudes:
        return None
    seed = min(bisect_left(latitudes, lat), len(latitudes) - 1)
    if seed and abs(latitudes[seed - 1] - lat) < abs(latitudes[seed] - lat):
        seed -= 1
    best = nodes[seed]
    best_distance = (coordinates[best][0] - lat) ** 2 + (coordinates[best][1] - lon) ** 2
    radius = math.sqrt(best_distance)
    left = bisect_left(latitudes, lat - radius)
    right = bisect_right(latitudes, lat + radius)
    for node in nodes[left:right]:
        distance = (coordinates[node][0] - lat) ** 2 + (coordinates[node][1] - lon) ** 2
        if distance < best_distance:
            best = node
            best_distance = distance
    return best


def _reconstruct_state_path(state: tuple, predecessors: dict[tuple, tuple]) -> list[str]:
    nodes = [str(state[0])]
    while state in predecessors:
        state = predecessors[state]
        nodes.append(str(state[0]))
    nodes.reverse()
    return nodes


def _reconstruct_state_path_details(
    state: tuple,
    predecessors: dict[tuple, tuple],
    predecessor_way_ids: dict[tuple, str],
    predecessor_edge_details: dict[tuple, tuple[float, float | None, float, bool]],
) -> tuple[list[str], list[str], list[dict[str, object]]]:
    """Reconstruct nodes plus the ordered directed way/segment traversals."""
    states = [state]
    while states[-1] in predecessors:
        states.append(predecessors[states[-1]])
    states.reverse()
    nodes = [str(current[0]) for current in states]
    way_ids: list[str] = []
    segments: list[dict[str, object]] = []
    for previous, current in pairwise(states):
        way_id = str(predecessor_way_ids.get(current, ""))
        way_ids.append(way_id)
        segments.append({
            "from_node": str(previous[0]),
            "to_node": str(current[0]),
            "way_id": way_id,
        })
        details = predecessor_edge_details.get(current)
        if details is not None:
            distance_m, duration_s, wait_s, ferry = details
            segments[-1].update(
                {
                    "distance_m": distance_m,
                    "duration_s": duration_s,
                    "wait_s": wait_s,
                    "ferry": ferry,
                }
            )
    return nodes, way_ids, segments


def _reconstruct_node_path(goal: str, predecessors: dict[str, str]) -> list[str]:
    nodes = [goal]
    while nodes[-1] in predecessors:
        nodes.append(predecessors[nodes[-1]])
    nodes.reverse()
    return nodes


def _reconstruct_portable_path_details(
    goal: str,
    predecessors: dict[str, str],
    predecessor_edges: dict[str, PortableEdge],
) -> tuple[list[str], list[str], list[dict[str, object]]]:
    """Reconstruct portable way IDs and directed edge context."""
    nodes = _reconstruct_node_path(goal, predecessors)
    way_ids: list[str] = []
    segments: list[dict[str, object]] = []
    for from_node, to_node in pairwise(nodes):
        edge = predecessor_edges.get(to_node)
        if edge is None:
            continue
        way_ids.append(edge.way_id)
        segments.append(
            {
                "from_node": from_node,
                "to_node": to_node,
                "way_id": edge.way_id,
                "distance_m": edge.length_m,
                "ferry": edge.ferry,
                "road_context": dict(edge.road_context or {}),
            }
        )
    return nodes, way_ids, segments


def shortest_path(
    start: str,
    goal: str,
    adjacency: dict[str, list[tuple[str, float]]] | PortableRoadGraph | SQLiteRoadGraph,
    *,
    departure: datetime | None = None,
    speed_kmh: float = 50.0,
    vehicle_weight_t: float | None = None,
    vehicle_rating_t: float | None = None,
    vehicle_height_m: float | None = None,
    vehicle_width_m: float | None = None,
    vehicle_length_m: float | None = None,
    vehicle_axleload_t: float | None = None,
    vehicle_class: str = "general",
    allow_hgv_destination: bool = False,
    return_path: bool = False,
    return_path_details: bool = False,
    include_ferries: bool = False,
) -> float | tuple[float, list[str]] | tuple[float, list[str], list[str], list[dict[str, object]]] | None:
    """Return shortest distance, optionally with nodes and directed segments."""
    return_path = return_path or return_path_details
    if isinstance(adjacency, SQLiteRoadGraph):
        adjacency.set_time_profile(
            departure,
            speed_kmh,
            include_ferries=include_ferries,
            vehicle_weight_t=vehicle_weight_t,
            vehicle_rating_t=vehicle_rating_t,
            vehicle_height_m=vehicle_height_m,
            vehicle_width_m=vehicle_width_m,
            vehicle_length_m=vehicle_length_m,
            vehicle_axleload_t=vehicle_axleload_t,
            vehicle_class=vehicle_class,
            allow_hgv_destination=allow_hgv_destination,
        )
    if start == goal:
        if return_path_details:
            return 0.0, [start], [], []
        return (0.0, [start]) if return_path else 0.0
    if isinstance(adjacency, SQLiteRoadGraph) and adjacency.has_turn_restrictions:
        # Keep only active suffixes of restriction way-runs in the search
        # state. Ordinary graph nodes still collapse to a single state once
        # no restriction prefix can continue from them.
        empty_state: tuple[tuple[str, ...], ...] = ()
        State = tuple[str, str, tuple[tuple[str, ...], ...], str]
        distances: dict[State, float] = {
            (start, "", empty_state, ""): 0.0
        }
        predecessors: dict[State, State] = {}
        predecessor_way_ids: dict[State, str] = {}
        predecessor_edge_details: dict[State, tuple[float, None, float, bool]] = {}
        queue: list[tuple[float, str, str, tuple[tuple[str, ...], ...], str]] = [
            (0.0, start, "", empty_state, "")
        ]
        while queue:
            distance, node, incoming_way, restriction_state, previous_ferry_way = heapq.heappop(queue)
            state = (node, incoming_way, restriction_state, previous_ferry_way)
            if distance != distances.get(state):
                continue
            if node == goal:
                if return_path_details:
                    path, way_ids, segments = _reconstruct_state_path_details(
                        state,
                        predecessors,
                        predecessor_way_ids,
                        predecessor_edge_details,
                    )
                    return distance, path, way_ids, segments
                return (
                    (distance, _reconstruct_state_path(state, predecessors))
                    if return_path
                    else distance
                )
            for neighbour, weight, way_id in adjacency.neighbours(
                node,
                incoming_way,
                restriction_state,
                distance,
                previous_ferry_way=previous_ferry_way,
            ):
                next_restriction_state = adjacency.advance_restriction_state(
                    restriction_state,
                    incoming_way,
                    way_id,
                )
                next_incoming_way = way_id if next_restriction_state else ""
                next_ferry_way = way_id if way_id in adjacency.ferry_way_ids else ""
                next_state = (
                    neighbour,
                    next_incoming_way,
                    next_restriction_state,
                    next_ferry_way,
                )
                candidate = distance + weight
                if candidate < distances.get(next_state, float("inf")):
                    distances[next_state] = candidate
                    if return_path:
                        predecessors[next_state] = state
                    if return_path_details:
                        predecessor_way_ids[next_state] = str(way_id)
                        predecessor_edge_details[next_state] = (
                            float(weight),
                            None,
                            0.0,
                            way_id in adjacency.ferry_way_ids,
                        )
                    heapq.heappush(
                        queue,
                        (
                            candidate,
                            neighbour,
                            next_incoming_way,
                            next_restriction_state,
                            next_ferry_way,
                        ),
                    )
        return None
    if isinstance(adjacency, SQLiteRoadGraph):
        State = tuple[str, str]
        distances: dict[State, float] = {(start, ""): 0.0}
        predecessors: dict[State, State] = {}
        predecessor_way_ids: dict[State, str] = {}
        predecessor_edge_details: dict[State, tuple[float, None, float, bool]] = {}
        queue: list[tuple[float, str, str]] = [(0.0, start, "")]
        while queue:
            distance, node, previous_ferry_way = heapq.heappop(queue)
            state = (node, previous_ferry_way)
            if distance != distances.get(state):
                continue
            if node == goal:
                if return_path_details:
                    path, way_ids, segments = _reconstruct_state_path_details(
                        state,
                        predecessors,
                        predecessor_way_ids,
                        predecessor_edge_details,
                    )
                    return distance, path, way_ids, segments
                return (
                    (distance, _reconstruct_state_path(state, predecessors))
                    if return_path
                    else distance
                )
            for neighbour, weight, way_id in adjacency.neighbours(
                node,
                elapsed_distance_m=distance,
                previous_ferry_way=previous_ferry_way,
            ):
                next_ferry_way = way_id if way_id in adjacency.ferry_way_ids else ""
                next_state = (neighbour, next_ferry_way)
                candidate = distance + weight
                if candidate < distances.get(next_state, float("inf")):
                    distances[next_state] = candidate
                    if return_path:
                        predecessors[next_state] = state
                    if return_path_details:
                        predecessor_way_ids[next_state] = str(way_id)
                        predecessor_edge_details[next_state] = (
                            float(weight),
                            None,
                            0.0,
                            way_id in adjacency.ferry_way_ids,
                        )
                    heapq.heappush(queue, (candidate, neighbour, next_ferry_way))
        return None
    if isinstance(adjacency, PortableRoadGraph):
        distances = {start: 0.0}
        predecessors: dict[str, str] = {}
        predecessor_edges: dict[str, PortableEdge] = {}
        queue = [(0.0, start)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances.get(node):
                continue
            if node == goal:
                if return_path_details:
                    path, way_ids, segments = _reconstruct_portable_path_details(
                        node,
                        predecessors,
                        predecessor_edges,
                    )
                    return distance, path, way_ids, segments
                return (
                    (distance, _reconstruct_node_path(node, predecessors))
                    if return_path
                    else distance
                )
            for edge in adjacency.neighbours(node):
                if edge.ferry and not include_ferries:
                    continue
                candidate = distance + edge.length_m
                if candidate < distances.get(edge.to_node, float("inf")):
                    distances[edge.to_node] = candidate
                    if return_path:
                        predecessors[edge.to_node] = node
                    if return_path_details:
                        predecessor_edges[edge.to_node] = edge
                    heapq.heappush(queue, (candidate, edge.to_node))
        return None
    distances = {start: 0.0}
    predecessors: dict[str, str] = {}
    queue = [(0.0, start)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances.get(node):
            continue
        if node == goal:
            if return_path_details:
                path = _reconstruct_node_path(node, predecessors)
                return distance, path, [], []
            return (
                (distance, _reconstruct_node_path(node, predecessors))
                if return_path
                else distance
            )
        for neighbour, weight in adjacency.get(node, []):
            candidate = distance + weight
            if candidate < distances.get(neighbour, float("inf")):
                distances[neighbour] = candidate
                if return_path:
                    predecessors[neighbour] = node
                heapq.heappush(queue, (candidate, neighbour))
    return None


def shortest_path_metrics(
    start: str,
    goal: str,
    adjacency: dict[str, list[tuple[str, float]]] | PortableRoadGraph | SQLiteRoadGraph,
    *,
    departure: datetime | None = None,
    speed_kmh: float = 50.0,
    vehicle_weight_t: float | None = None,
    vehicle_rating_t: float | None = None,
    vehicle_height_m: float | None = None,
    vehicle_width_m: float | None = None,
    vehicle_length_m: float | None = None,
    vehicle_axleload_t: float | None = None,
    vehicle_class: str = "general",
    allow_hgv_destination: bool = False,
    return_path: bool = False,
    return_path_details: bool = False,
    include_ferries: bool = False,
    objective: str = "distance",
) -> tuple[float, float] | tuple[float, float, list[str]] | tuple[float, float, list[str], list[str], list[dict[str, object]]] | None:
    """Return a distance/duration route with schedule-aware travel time.

    ``distance`` preserves the analytical default. ``duration`` selects the
    fastest estimated route and lets optional ferry crossing durations and
    service-window waits affect route selection. Both metrics are returned.
    """
    if objective not in ROUTE_OBJECTIVES:
        raise ValueError(f"objective must be one of: {', '.join(ROUTE_OBJECTIVES)}")
    if not isinstance(adjacency, SQLiteRoadGraph):
        route = shortest_path(
            start,
            goal,
            adjacency,
            departure=departure,
            speed_kmh=speed_kmh,
            vehicle_weight_t=vehicle_weight_t,
            vehicle_rating_t=vehicle_rating_t,
            vehicle_height_m=vehicle_height_m,
            vehicle_width_m=vehicle_width_m,
            vehicle_length_m=vehicle_length_m,
            vehicle_axleload_t=vehicle_axleload_t,
            vehicle_class=vehicle_class,
            allow_hgv_destination=allow_hgv_destination,
            return_path=return_path,
            return_path_details=return_path_details,
            include_ferries=include_ferries,
        )
        if route is None:
            return None
        if return_path_details:
            distance, path, way_ids, segments = route
            for segment in segments:
                distance_m = segment.get("distance_m")
                if distance_m is not None:
                    segment["duration_s"] = float(distance_m) / (speed_kmh / 3.6)
                    segment["wait_s"] = 0.0
                    segment["ferry"] = bool(segment.get("ferry", False))
        elif return_path:
            distance, path = route
        else:
            distance = route
        duration = float(distance) / (speed_kmh / 3.6)
        if return_path_details:
            return distance, duration, path, way_ids, segments
        return (distance, duration, path) if return_path else (distance, duration)

    adjacency.set_time_profile(
        departure,
        speed_kmh,
        include_ferries=include_ferries,
        vehicle_weight_t=vehicle_weight_t,
        vehicle_rating_t=vehicle_rating_t,
        vehicle_height_m=vehicle_height_m,
        vehicle_width_m=vehicle_width_m,
        vehicle_length_m=vehicle_length_m,
        vehicle_axleload_t=vehicle_axleload_t,
        vehicle_class=vehicle_class,
        allow_hgv_destination=allow_hgv_destination,
    )
    if start == goal:
        if return_path_details:
            return 0.0, 0.0, [start], [], []
        return (0.0, 0.0, [start]) if return_path else (0.0, 0.0)

    empty_state: tuple[tuple[str, ...], ...] = ()
    State = tuple[str, str, tuple[tuple[str, ...], ...], str]
    start_state: State = (start, "", empty_state, "")
    distances: dict[State, float] = {start_state: 0.0}
    durations: dict[State, float] = {start_state: 0.0}
    ferry_waits: dict[State, float] = {start_state: 0.0}
    ferry_wait_counts: dict[State, int] = {start_state: 0}
    ferry_way_sets: dict[State, frozenset[str]] = {start_state: frozenset()} if include_ferries else {}
    ferry_distances: dict[State, float] = {start_state: 0.0} if include_ferries else {}
    ferry_crossing_durations: dict[State, float] = {start_state: 0.0} if include_ferries else {}
    ferry_edge_counts: dict[State, int] = {start_state: 0} if include_ferries else {}
    predecessors: dict[State, State] = {}
    predecessor_way_ids: dict[State, str] = {}
    predecessor_edge_details: dict[State, tuple[float, float, float, bool]] = {}
    queue: list[tuple[float, float, float, str, str, tuple[tuple[str, ...], ...], str]] = [
        (0.0, 0.0, 0.0, start, "", empty_state, "")
    ]
    while queue:
        _priority, distance, duration, node, incoming_way, restriction_state, previous_ferry_way = heapq.heappop(queue)
        state = (node, incoming_way, restriction_state, previous_ferry_way)
        if (
            distance != distances.get(state)
            or duration != durations.get(state)
        ):
            continue
        if node == goal:
            adjacency.last_ferry_wait_s = ferry_waits[state]
            adjacency.last_ferry_wait_n = ferry_wait_counts[state]
            if include_ferries:
                adjacency.last_ferry_way_ids = tuple(sorted(ferry_way_sets[state]))
                adjacency.last_ferry_distance_m = ferry_distances[state]
                adjacency.last_ferry_crossing_s = ferry_crossing_durations[state]
                adjacency.last_ferry_edge_n = ferry_edge_counts[state]
            if return_path_details:
                path, way_ids, segments = _reconstruct_state_path_details(
                    state,
                    predecessors,
                    predecessor_way_ids,
                    predecessor_edge_details,
                )
                return distance, duration, path, way_ids, segments
            path = _reconstruct_state_path(state, predecessors) if return_path else None
            return (
                (distance, duration, path)
                if return_path
                else (distance, duration)
            )
        for neighbour, edge_distance, edge_duration, way_id, edge_wait_s in adjacency.neighbours_with_metrics(
            node,
            incoming_way,
            restriction_state,
            elapsed_distance_m=distance,
            elapsed_time_s=duration,
            previous_ferry_way=previous_ferry_way,
            include_wait=True,
        ):
            next_restriction_state = adjacency.advance_restriction_state(
                restriction_state,
                incoming_way,
                way_id,
            )
            next_incoming_way = way_id if next_restriction_state else ""
            next_ferry_way = way_id if way_id in adjacency.ferry_way_ids else ""
            next_state = (
                neighbour,
                next_incoming_way,
                next_restriction_state,
                next_ferry_way,
            )
            candidate_distance = distance + edge_distance
            candidate_duration = duration + edge_duration
            candidate_ferry_wait_s = ferry_waits[state] + edge_wait_s
            candidate_ferry_wait_n = ferry_wait_counts[state] + int(edge_wait_s > 0)
            if include_ferries and way_id in adjacency.ferry_way_ids:
                candidate_ferry_way_set = ferry_way_sets[state] | {way_id}
                candidate_ferry_distance = ferry_distances[state] + edge_distance
                candidate_ferry_crossing_s = ferry_crossing_durations[state] + max(0.0, edge_duration - edge_wait_s)
                candidate_ferry_edge_n = ferry_edge_counts[state] + 1
            elif include_ferries:
                candidate_ferry_way_set = ferry_way_sets[state]
                candidate_ferry_distance = ferry_distances[state]
                candidate_ferry_crossing_s = ferry_crossing_durations[state]
                candidate_ferry_edge_n = ferry_edge_counts[state]
            incumbent_distance = distances.get(next_state, float("inf"))
            incumbent_duration = durations.get(next_state, float("inf"))
            if objective == "duration":
                better = (
                    candidate_duration < incumbent_duration
                    or (
                        candidate_duration == incumbent_duration
                        and candidate_distance < incumbent_distance
                    )
                )
            else:
                better = (
                    candidate_distance < incumbent_distance
                    or (
                        candidate_distance == incumbent_distance
                        and candidate_duration < incumbent_duration
                    )
                )
            if better:
                distances[next_state] = candidate_distance
                durations[next_state] = candidate_duration
                ferry_waits[next_state] = candidate_ferry_wait_s
                ferry_wait_counts[next_state] = candidate_ferry_wait_n
                if include_ferries:
                    ferry_way_sets[next_state] = candidate_ferry_way_set
                    ferry_distances[next_state] = candidate_ferry_distance
                    ferry_crossing_durations[next_state] = candidate_ferry_crossing_s
                    ferry_edge_counts[next_state] = candidate_ferry_edge_n
                if return_path:
                    predecessors[next_state] = state
                if return_path_details:
                    predecessor_way_ids[next_state] = str(way_id)
                    predecessor_edge_details[next_state] = (
                        float(edge_distance),
                        float(edge_duration),
                        float(edge_wait_s),
                        way_id in adjacency.ferry_way_ids,
                    )
                heapq.heappush(
                    queue,
                    (
                        candidate_duration if objective == "duration" else candidate_distance,
                        candidate_distance,
                        candidate_duration,
                        neighbour,
                        next_incoming_way,
                        next_restriction_state,
                        next_ferry_way,
                    ),
                )
    return None


def parse_departure(value: str | None) -> datetime | None:
    """Parse a local ISO-8601 departure timestamp for conditional routing."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("departure must be an ISO-8601 date/time") from exc


def write_unavailable(out: Path, reason: str) -> None:
    atomic_write_csv(
        out / "road_routing_pairs.csv",
        ["target_osm_id", "control_osm_id", "target_group", "straight_distance_m", "route_distance_m", "reachable", "status", "method"],
        [{"target_osm_id": "", "control_osm_id": "", "target_group": "", "straight_distance_m": "", "route_distance_m": "", "reachable": 0, "status": reason, "method": "no road graph supplied"}],
    )
    atomic_write_csv(
        out / "road_routing.csv",
        ["group", "sample_n", "route_n", "reachable_pct", "median_route_m", "p90_route_m", "median_straight_m", "network_to_straight_ratio", "status", "source", "method"],
        [{"group": "all", "sample_n": 0, "route_n": 0, "reachable_pct": 0, "median_route_m": "", "p90_route_m": "", "median_straight_m": "", "network_to_straight_ratio": "", "status": reason, "source": "unavailable", "method": "actual shortest-path distance requires a supplied graph"}],
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--road-graph", default=None, help="directory or JSON graph contract")
    parser.add_argument("--from-pbf", action="store_true", help="build a graph from --pbf")
    parser.add_argument("--write-graph", default=None, help="persist a PBF-built graph to this directory")
    parser.add_argument(
        "--graph-format",
        choices=("csv", "sqlite"),
        default="csv",
        help="graph format for --write-graph; SQLite supports complete disk-backed exports",
    )
    parser.add_argument("--pbf", default=None)
    parser.add_argument("--max-ways", type=int, default=100_000, help="0 scans all supported highway ways")
    parser.add_argument("--max-pairs", type=int, default=5000)
    parser.add_argument(
        "--departure",
        default=None,
        help="optional ISO-8601 local departure time for conditional road profiles",
    )
    parser.add_argument(
        "--speed-kmh",
        type=float,
        default=50.0,
        help="assumed routing speed for advancing conditional turn windows (default: 50)",
    )
    parser.add_argument(
        "--weight-t",
        type=float,
        default=None,
        help="optional vehicle weight profile in metric tonnes for weight-based restrictions",
    )
    parser.add_argument(
        "--rating-t",
        type=float,
        default=None,
        help=(
            "optional HGV permitted gross-weight rating in metric tonnes for "
            "maxweightrating:hgv and maxweightrating:goods restrictions"
        ),
    )
    parser.add_argument(
        "--height-m",
        type=float,
        default=None,
        help="optional vehicle height profile in metres for height-based restrictions",
    )
    parser.add_argument(
        "--vehicle-class",
        choices=VEHICLE_CLASSES,
        default="general",
        help="vehicle-class profile for class-specific conditional access (default: general)",
    )
    parser.add_argument(
        "--allow-hgv-destination",
        action="store_true",
        help=(
            "allow an explicit hgv profile to use destination-only ways and "
            "supported destination exceptions to HGV weight/rating limits"
        ),
    )
    parser.add_argument(
        "--include-restricted",
        action="store_true",
        help="retain ways tagged private/restricted/no for motor-vehicle routing",
    )
    parser.add_argument(
        "--include-ferries",
        action="store_true",
        help=(
            "include persisted ferry geometry; SQLite graphs may also apply "
            "schedules, waits, and durations"
        ),
    )
    args = parser.parse_args(argv)
    if args.max_ways < 0:
        parser.error("--max-ways must be non-negative (0 means all supported highway ways)")
    if args.max_pairs < 0:
        parser.error("--max-pairs must be non-negative")
    if not math.isfinite(args.speed_kmh) or args.speed_kmh <= 0:
        parser.error("--speed-kmh must be a finite positive number")
    if args.weight_t is not None and (
        not math.isfinite(args.weight_t) or args.weight_t <= 0
    ):
        parser.error("--weight-t must be a finite positive number of tonnes")
    if args.rating_t is not None and (
        not math.isfinite(args.rating_t) or args.rating_t <= 0
    ):
        parser.error("--rating-t must be a finite positive number of tonnes")
    if args.height_m is not None and (
        not math.isfinite(args.height_m) or args.height_m <= 0
    ):
        parser.error("--height-m must be a finite positive number of metres")
    try:
        departure = parse_departure(args.departure)
    except ValueError as exc:
        parser.error(str(exc))
    if args.from_pbf and args.max_ways == 0 and (not args.write_graph or args.graph_format != "sqlite"):
        parser.error("complete PBF scans require --write-graph and --graph-format sqlite")
    data = project_data_tree_path(args.data_root)
    out = project_output_tree_path(args.out_dir)
    if args.write_graph and not args.from_pbf:
        raise SystemExit("--write-graph requires --from-pbf")
    graph_path = (
        project_input_path(
            args.road_graph,
            str(data / "roads"),
            label="road graph input",
        )
        if args.road_graph
        else data / "roads"
    )
    source = str(graph_path)
    coordinates: dict[str, tuple[float, float]] = {}
    adjacency: dict[str, list[tuple[str, float]]] = {}
    graph: SQLiteRoadGraph | None = None
    portable_graph: PortableRoadGraph | None = None
    try:
        if args.from_pbf:
            pbf = project_input_path(
                args.pbf,
                str(data / "raw" / "ireland-latest.osm.pbf"),
                label="road routing PBF",
            )
            source = str(pbf)
            if args.write_graph:
                try:
                    graph_output = reject_symlink_root(
                        project_path(args.write_graph, str(data / "roads")),
                        label="road graph directory",
                    )
                    graph_output = reject_symlink_tree(
                        graph_output,
                        label="road graph directory",
                    )
                except ValueError as exc:
                    parser.error(str(exc))
                if args.graph_format == "sqlite":
                    write_sqlite_graph_from_pbf(
                        pbf,
                        graph_output,
                        max_ways=args.max_ways,
                        include_restricted=args.include_restricted,
                    )
                    graph = SQLiteRoadGraph(graph_output / "road_graph.sqlite")
                else:
                    portable_graph = graph_from_pbf(
                        pbf,
                        args.max_ways,
                        include_restricted=args.include_restricted,
                        include_ferries=args.include_ferries,
                    )
                    write_graph(graph_output, portable_graph, source=str(pbf))
                    coordinates, adjacency = portable_graph
                source = str(graph_output)
            else:
                portable_graph = graph_from_pbf(
                    pbf,
                    args.max_ways,
                    include_restricted=args.include_restricted,
                    include_ferries=args.include_ferries,
                )
                coordinates, adjacency = portable_graph
        elif graph_path.is_dir() and not (
            (graph_path / "road_nodes.csv").exists() or (graph_path / "road_graph.sqlite").exists()
        ):
            write_unavailable(out, "not_provided")
            print("[road-routing] graph directory has no supported graph; wrote explicit not_provided outputs")
            return
        elif graph_path.exists():
            loaded = load_graph(graph_path)
            if isinstance(loaded, SQLiteRoadGraph):
                graph = loaded
            else:
                portable_graph = loaded
                coordinates, adjacency = portable_graph
        else:
            write_unavailable(out, "not_provided")
            print("[road-routing] no road graph supplied; wrote explicit not_provided outputs")
            return
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        write_unavailable(out, "provided_but_unreadable")
        print(f"[road-routing] graph unavailable: {exc}")
        return
    if graph is not None:
        graph_empty = graph.node_count == 0 or (
            graph.edge_count == 0 and graph.ferry_edge_count == 0
        )
        graph_node_n = graph.node_count
    else:
        graph_empty = not coordinates or not adjacency
        graph_node_n = len(coordinates)
    if graph_empty:
        if graph is not None:
            graph.close()
        write_unavailable(out, "empty_graph")
        return
    analysis_rows = read_csv(out / "analysis_results.csv")
    by_id = {row.get("osm_id", ""): row for row in analysis_rows}
    pairs = read_csv(out / "matched_controls_strict.csv") or read_csv(out / "matched_controls.csv")
    pairs = sorted(pairs, key=lambda row: (row.get("target_osm_id", ""), row.get("control_osm_id", "")))[: args.max_pairs]
    node_index = None if graph is not None else build_node_index(coordinates)
    graph_coordinates = graph if graph is not None else coordinates
    graph_adjacency = (
        graph
        if graph is not None
        else portable_graph
        if portable_graph is not None
        else adjacency
    )
    if graph is not None:
        routing_method = "Dijkstra on SQLite road graph; indexed nearest graph node snap"
        if graph.has_turn_restrictions:
            routing_method += "; OSM via-node and validated via-way turn restrictions"
        if graph.conditional_restriction_n:
            if departure is None:
                routing_method += "; conditional windows retained but inactive"
            else:
                routing_method += f"; conditional windows evaluated from {args.departure} at {args.speed_kmh:g} km/h"
        if graph.conditional_access_n:
            if departure is None:
                routing_method += "; conditional road access windows retained but inactive"
            else:
                routing_method += f"; conditional road access windows evaluated from {args.departure} at {args.speed_kmh:g} km/h"
        if graph.conditional_access_weight_n:
            routing_method += (
                f"; vehicle-weight conditional road access evaluated at {args.weight_t:g} t"
                if args.weight_t is not None
                else "; vehicle-weight conditional road access retained but inactive"
            )
        if args.vehicle_class != "general":
            routing_method += f"; vehicle-class profile={args.vehicle_class}"
        if graph.hgv_destination_way_n:
            routing_method += (
                "; HGV destination-only rules explicitly enabled"
                if args.allow_hgv_destination and args.vehicle_class == "hgv"
                else "; HGV destination-only rules enforced for hgv profiles"
            )
        if graph.conditional_access_unsupported_n:
            if graph.conditional_access_excluded_n:
                routing_method += (
                    f"; {graph.conditional_access_unsupported_n} unsupported conditional "
                    f"road-access tags excluded ({graph.conditional_access_excluded_n} ways)"
                )
            else:
                routing_method += (
                    f"; {graph.conditional_access_unsupported_n} unsupported conditional "
                    "road-access tags are not evaluated"
                )
        if graph.conditional_weight_restriction_n:
            routing_method += (
                f"; vehicle-weight conditional restrictions evaluated at {args.weight_t:g} t"
                if args.weight_t is not None
                else "; vehicle-weight conditional restrictions retained but inactive"
            )
        if graph.maxweight_way_n:
            if args.weight_t is None:
                routing_method += "; generic maxweight limits retained but inactive"
            else:
                routing_method += f"; generic maxweight limits enforced at {args.weight_t:g} t"
                if graph.maxweight_unsupported_way_n:
                    routing_method += (
                        f"; {graph.maxweight_unsupported_way_n} ambiguous generic "
                        "maxweight ways treated as impassable for weighted routing"
                    )
        if graph.maxweight_hgv_way_n:
            if args.vehicle_class != "hgv":
                routing_method += "; HGV-specific maxweight limits retained for hgv profiles"
            elif args.weight_t is None:
                routing_method += "; HGV-specific maxweight limits retained but inactive"
            else:
                routing_method += f"; HGV-specific maxweight limits enforced at {args.weight_t:g} t"
                if graph.maxweight_hgv_unsupported_way_n:
                    routing_method += (
                        f"; {graph.maxweight_hgv_unsupported_way_n} ambiguous HGV "
                        "maxweight ways treated as impassable for weighted routing"
                    )
        if graph.maxweightrating_hgv_way_n:
            if args.vehicle_class != "hgv":
                routing_method += "; HGV maxweightrating limits retained for hgv profiles (including maxweightrating:goods aliases)"
            elif args.rating_t is None:
                routing_method += "; HGV maxweightrating limits retained but inactive (including maxweightrating:goods aliases)"
            else:
                routing_method += (
                    f"; HGV maxweightrating limits enforced at {args.rating_t:g} t (including maxweightrating:goods aliases)"
                )
                if graph.maxweightrating_hgv_unsupported_way_n:
                    routing_method += (
                        f"; {graph.maxweightrating_hgv_unsupported_way_n} ambiguous HGV "
                        "maxweightrating ways treated as impassable for rating-qualified routing"
                    )
        if graph.maxheight_way_n or graph.maxheight_physical_way_n:
            if args.height_m is None:
                routing_method += "; maxheight limits retained but inactive"
            else:
                routing_method += f"; maxheight limits enforced at {args.height_m:g} m"
                unsupported_n = (
                    graph.maxheight_unsupported_way_n
                    + graph.maxheight_physical_unsupported_way_n
                )
                if unsupported_n:
                    routing_method += (
                        f"; {unsupported_n} ambiguous maxheight ways treated as impassable "
                        "for height-qualified routing"
                    )
        if graph.ferry_edge_count:
            if args.include_ferries:
                routing_method += "; ferry geometry included"
                if graph.ferry_schedule_n:
                    routing_method += (
                        "; ferry service windows and waiting evaluated"
                        if departure
                        else "; ferry service windows retained but not evaluated"
                    )
                elif graph.ferry_schedule_contract_status == "available":
                    routing_method += "; ferry service windows unavailable in contract"
                else:
                    routing_method += "; ferry service schedule not provided"
                if graph.ferry_duration_n:
                    routing_method += "; ferry crossing durations applied to route timing"
                if graph.ferry_public_holiday_schedule_n:
                    if departure is None:
                        routing_method += "; public-holiday calendar retained but not evaluated"
                    elif graph.public_holiday_contract_status == "available":
                        routing_method += "; public-holiday calendar applied"
                    else:
                        routing_method += "; public-holiday calendar not provided"
            else:
                routing_method += "; ferry geometry available but excluded"
    else:
        routing_method = "Dijkstra on supplied road graph; nearest graph node snap"
        if portable_graph is not None and portable_graph.ferry_edge_count:
            if args.include_ferries:
                routing_method += (
                    "; ferry geometry included; ferry schedules, waits, and "
                    "crossing durations unavailable in portable graph"
                )
            else:
                routing_method += "; ferry geometry available but excluded"
        elif args.include_ferries:
            routing_method += "; ferry geometry unavailable in portable graph"
    routed = []
    for pair in pairs:
        target = by_id.get(pair.get("target_osm_id", ""))
        control = by_id.get(pair.get("control_osm_id", ""))
        if not target or not control:
            continue
        start = nearest_node(target, graph_coordinates, node_index)
        goal = nearest_node(control, graph_coordinates, node_index)
        route_result = (
            (
                shortest_path_metrics(
                    start,
                    goal,
                    graph_adjacency,
                    departure=departure,
                    speed_kmh=args.speed_kmh,
                    vehicle_weight_t=args.weight_t,
                    vehicle_rating_t=args.rating_t,
                    vehicle_height_m=args.height_m,
                    vehicle_class=args.vehicle_class,
                    allow_hgv_destination=args.allow_hgv_destination,
                    include_ferries=args.include_ferries,
                )
                if graph is not None
                else shortest_path(
                    start,
                    goal,
                    graph_adjacency,
                    departure=departure,
                    speed_kmh=args.speed_kmh,
                    vehicle_weight_t=args.weight_t,
                    vehicle_rating_t=args.rating_t,
                    vehicle_height_m=args.height_m,
                    vehicle_class=args.vehicle_class,
                    allow_hgv_destination=args.allow_hgv_destination,
                    include_ferries=args.include_ferries,
                )
            )
            if start and goal
            else None
        )
        route = route_result[0] if graph is not None and route_result is not None else route_result
        routed.append(
            {
                "target_osm_id": target["osm_id"],
                "control_osm_id": control["osm_id"],
                "target_group": target.get("group", "other"),
                "straight_distance_m": round(straight_distance(target, control), 2),
                "route_distance_m": round(route, 2) if route is not None else "",
                "reachable": int(route is not None),
                "status": "provided",
                "method": routing_method,
            }
        )
    atomic_write_csv(
        out / "road_routing_pairs.csv",
        ["target_osm_id", "control_osm_id", "target_group", "straight_distance_m", "route_distance_m", "reachable", "status", "method"],
        routed,
    )
    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in routed:
        by_group[row["target_group"]].append(row)
    summaries = []
    for group, group_rows in sorted(by_group.items()):
        routes = sorted(number(row["route_distance_m"]) for row in group_rows if row["route_distance_m"] != "")
        straight = sorted(number(row["straight_distance_m"]) for row in group_rows)
        ratio = [route / line for route, line in zip(routes, straight[: len(routes)]) if line > 0]
        summaries.append(
            {
                "group": group,
                "sample_n": len(group_rows),
                "route_n": len(routes),
                "reachable_pct": round(100 * len(routes) / len(group_rows), 2),
                "median_route_m": round(routes[len(routes) // 2], 2) if routes else "",
                "p90_route_m": round(routes[min(len(routes) - 1, int(len(routes) * 0.9))], 2) if routes else "",
                "median_straight_m": round(straight[len(straight) // 2], 2) if straight else "",
                "network_to_straight_ratio": round(sum(ratio) / len(ratio), 4) if ratio else "",
                "status": "provided",
                "source": source,
                "method": routing_method,
            }
        )
    atomic_write_csv(
        out / "road_routing.csv",
        ["group", "sample_n", "route_n", "reachable_pct", "median_route_m", "p90_route_m", "median_straight_m", "network_to_straight_ratio", "status", "source", "method"],
        summaries or [{"group": "all", "sample_n": 0, "route_n": 0, "reachable_pct": 0, "median_route_m": "", "p90_route_m": "", "median_straight_m": "", "network_to_straight_ratio": "", "status": "no_pairs", "source": source, "method": "Dijkstra"}],
    )
    if graph is not None:
        graph.close()
    print(f"[road-routing] routed {len(routed):,} pairs on {graph_node_n:,} graph nodes")


if __name__ == "__main__":
    main()
