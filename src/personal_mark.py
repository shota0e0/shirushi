"""Versioned Personal Mark stroke model and size-independent renderer."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping


PERSONAL_MARK_VERSION = 1
PERSONAL_MARK_TYPE = "handwritten"
MAX_STROKES = 128
MAX_POINTS_PER_STROKE = 4096
MAX_TOTAL_POINTS = 20_000
MAX_STROKE_DURATION_MS = 120_000


class PersonalMarkValidationError(ValueError):
    """Raised when stroke data is malformed or outside the supported bounds."""


class PersonalMarkVersionError(PersonalMarkValidationError):
    """Raised when persisted data uses an unsupported schema version."""


@dataclass(frozen=True)
class StrokePoint:
    x: float
    y: float
    t: int

    def to_mapping(self) -> dict[str, float | int]:
        return {"x": self.x, "y": self.y, "t": self.t}


@dataclass(frozen=True)
class Stroke:
    points: tuple[StrokePoint, ...]

    def to_mapping(self) -> dict[str, list[dict[str, float | int]]]:
        return {"points": [point.to_mapping() for point in self.points]}


@dataclass(frozen=True)
class PersonalMark:
    version: int = PERSONAL_MARK_VERSION
    type: str = PERSONAL_MARK_TYPE
    strokes: tuple[Stroke, ...] = ()

    @classmethod
    def empty(cls) -> "PersonalMark":
        return cls()

    @classmethod
    def from_mapping(cls, value: Any) -> "PersonalMark":
        if not isinstance(value, Mapping):
            raise PersonalMarkValidationError("Personal Mark must be an object")
        version = value.get("version")
        if not _is_integer(version):
            raise PersonalMarkValidationError("version must be an integer")
        if version != PERSONAL_MARK_VERSION:
            raise PersonalMarkVersionError(f"unsupported Personal Mark version: {version}")
        if value.get("type") != PERSONAL_MARK_TYPE:
            raise PersonalMarkValidationError("unsupported Personal Mark type")
        raw_strokes = value.get("strokes")
        if not isinstance(raw_strokes, list):
            raise PersonalMarkValidationError("strokes must be an array")
        if len(raw_strokes) > MAX_STROKES:
            raise PersonalMarkValidationError("too many strokes")

        strokes: list[Stroke] = []
        total_points = 0
        for raw_stroke in raw_strokes:
            if not isinstance(raw_stroke, Mapping):
                raise PersonalMarkValidationError("stroke must be an object")
            raw_points = raw_stroke.get("points")
            if not isinstance(raw_points, list):
                raise PersonalMarkValidationError("points must be an array")
            if not raw_points:
                raise PersonalMarkValidationError("saved strokes must contain a point")
            if len(raw_points) > MAX_POINTS_PER_STROKE:
                raise PersonalMarkValidationError("too many points in a stroke")
            total_points += len(raw_points)
            if total_points > MAX_TOTAL_POINTS:
                raise PersonalMarkValidationError("too many points in Personal Mark")

            points: list[StrokePoint] = []
            previous_t = -1
            for raw_point in raw_points:
                point = _point_from_mapping(raw_point)
                if point.t < previous_t:
                    raise PersonalMarkValidationError("point timestamps must be nondecreasing")
                previous_t = point.t
                points.append(point)
            strokes.append(Stroke(tuple(points)))
        return cls(version=version, type=PERSONAL_MARK_TYPE, strokes=tuple(strokes))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": self.type,
            "strokes": [stroke.to_mapping() for stroke in self.strokes],
        }


@dataclass(frozen=True)
class RenderPoint:
    x: float
    y: float
    delay_ms: int
    starts_stroke: bool


class PersonalMarkRenderer:
    """Convert normalized strokes into deterministic canvas-space replay points."""

    STROKE_GAP_MS = 80

    @classmethod
    def replay_points(
        cls,
        mark: PersonalMark,
        width: int,
        height: int,
    ) -> tuple[RenderPoint, ...]:
        if width <= 0 or height <= 0:
            raise ValueError("render dimensions must be positive")
        rendered: list[RenderPoint] = []
        base_delay = 0
        for stroke in mark.strokes:
            for index, point in enumerate(stroke.points):
                rendered.append(
                    RenderPoint(
                        x=point.x * width,
                        y=point.y * height,
                        delay_ms=base_delay + point.t,
                        starts_stroke=index == 0,
                    )
                )
            if stroke.points:
                base_delay += stroke.points[-1].t + cls.STROKE_GAP_MS
        return tuple(rendered)


def normalized_point(x: float, y: float, width: int, height: int, t: int) -> StrokePoint:
    """Normalize a captured canvas point, clamping coordinates to its bounds."""
    if width <= 0 or height <= 0:
        raise ValueError("capture dimensions must be positive")
    if not math.isfinite(x) or not math.isfinite(y):
        raise PersonalMarkValidationError("captured coordinates must be finite")
    if not _is_integer(t) or not 0 <= t <= MAX_STROKE_DURATION_MS:
        raise PersonalMarkValidationError("captured timestamp is outside the supported range")
    return StrokePoint(
        x=min(1.0, max(0.0, x / width)),
        y=min(1.0, max(0.0, y / height)),
        t=t,
    )


def mark_from_strokes(strokes: Iterable[Iterable[StrokePoint]]) -> PersonalMark:
    """Create and validate a mark from captured stroke sequences."""
    value = PersonalMark(strokes=tuple(Stroke(tuple(points)) for points in strokes))
    return PersonalMark.from_mapping(value.to_mapping())


def _point_from_mapping(value: Any) -> StrokePoint:
    if not isinstance(value, Mapping):
        raise PersonalMarkValidationError("point must be an object")
    x = value.get("x")
    y = value.get("y")
    t = value.get("t")
    if not _is_number(x) or not _is_number(y):
        raise PersonalMarkValidationError("point coordinates must be finite numbers")
    x_float = float(x)
    y_float = float(y)
    if not math.isfinite(x_float) or not math.isfinite(y_float):
        raise PersonalMarkValidationError("point coordinates must be finite numbers")
    if not 0.0 <= x_float <= 1.0 or not 0.0 <= y_float <= 1.0:
        raise PersonalMarkValidationError("point coordinates must be normalized")
    if not _is_integer(t) or not 0 <= t <= MAX_STROKE_DURATION_MS:
        raise PersonalMarkValidationError("point timestamp is outside the supported range")
    return StrokePoint(x_float, y_float, t)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
