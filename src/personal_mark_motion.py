"""GUI-independent timing and scheduling for Personal Mark Add Motion."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
import os
import sys
from typing import Any

from personal_mark import PersonalMark, PersonalMarkRenderer, RenderPoint
from personal_mark_replay import ReplayScheduler


@dataclass(frozen=True)
class AddMotionTiming:
    stroke_total_ms: int = 1400
    min_stroke_ms: int = 120
    max_stroke_share: float = 0.60
    rights_ms: int = 500
    completion_ms: int = 400
    hold_ms: int = 100
    fade_ms: int = 300
    fade_steps: int = 4
    reduced_static_ms: int = 1600


@dataclass(frozen=True)
class AddMotionPlan:
    points: tuple[RenderPoint, ...]
    stroke_durations_ms: tuple[int, ...]
    rights_at_ms: int
    completion_at_ms: int
    fade_at_ms: int
    total_ms: int


@dataclass(frozen=True)
class MotionStage:
    """Actual displayed image rectangle inside a possibly letterboxed Preview."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("motion stage dimensions must be positive")


class PersonalMarkMotionPlanner:
    """Normalize handwritten geometry into a stable, distance-weighted UI timeline."""

    def __init__(self, timing: AddMotionTiming = AddMotionTiming()) -> None:
        self.timing = timing

    def plan(
        self,
        mark: PersonalMark,
        width: int,
        height: int,
        *,
        stage: MotionStage | None = None,
    ) -> AddMotionPlan:
        if stage is None:
            rendered = PersonalMarkRenderer.replay_points(mark, width, height)
        else:
            source = PersonalMarkRenderer.replay_points(mark, 480, 280)
            rendered = _fit_rendered_to_stage(source, stage)
        strokes = _group_rendered_strokes(rendered)
        durations = _allocate_stroke_durations(
            [_stroke_length(stroke) for stroke in strokes],
            self.timing,
        )

        points: list[RenderPoint] = []
        stroke_start = 0
        for stroke, duration in zip(strokes, durations, strict=True):
            distances = _cumulative_distances(stroke)
            total_distance = distances[-1] if distances else 0.0
            for index, point in enumerate(stroke):
                if total_distance > 0:
                    offset = round(duration * distances[index] / total_distance)
                else:
                    offset = 0
                points.append(
                    RenderPoint(
                        x=point.x,
                        y=point.y,
                        delay_ms=stroke_start + offset,
                        starts_stroke=index == 0,
                    )
                )
            stroke_start += duration

        rights_at = self.timing.stroke_total_ms
        completion_at = rights_at + self.timing.rights_ms
        fade_at = completion_at + self.timing.completion_ms + self.timing.hold_ms
        total = fade_at + self.timing.fade_ms
        return AddMotionPlan(
            points=tuple(points),
            stroke_durations_ms=durations,
            rights_at_ms=rights_at,
            completion_at_ms=completion_at,
            fade_at_ms=fade_at,
            total_ms=total,
        )


class PersonalMarkAddMotionController:
    """Run a planned Add Motion with cancellation-safe stage callbacks."""

    def __init__(
        self,
        scheduler: ReplayScheduler,
        *,
        on_begin: Callable[[], None],
        draw_point: Callable[[RenderPoint], None],
        show_rights: Callable[[], None],
        show_completion: Callable[[], None],
        show_fade: Callable[[float], None],
        on_clear: Callable[[], None],
        timing: AddMotionTiming = AddMotionTiming(),
    ) -> None:
        self.scheduler = scheduler
        self.on_begin = on_begin
        self.draw_point = draw_point
        self.show_rights = show_rights
        self.show_completion = show_completion
        self.show_fade = show_fade
        self.on_clear = on_clear
        self.timing = timing
        self._scheduled: list[Any] = []
        self._generation = 0
        self.running = False

    def start(self, plan: AddMotionPlan, *, reduced_motion: bool = False) -> bool:
        self.cancel()
        if not plan.points:
            return False
        self._generation += 1
        generation = self._generation
        self.running = True
        self.on_begin()

        if reduced_motion:
            for point in plan.points:
                self.draw_point(point)
            self.show_rights()
            self.show_completion()
            self._schedule(
                self.timing.reduced_static_ms,
                generation,
                self._finish,
            )
            return True

        for point in plan.points:
            self._schedule(
                point.delay_ms,
                generation,
                lambda current=point: self.draw_point(current),
            )
        self._schedule(plan.rights_at_ms, generation, self.show_rights)
        self._schedule(plan.completion_at_ms, generation, self.show_completion)
        interval = self.timing.fade_ms / self.timing.fade_steps
        for step in range(1, self.timing.fade_steps + 1):
            self._schedule(
                plan.fade_at_ms + round(interval * step),
                generation,
                lambda progress=step / self.timing.fade_steps: self.show_fade(progress),
            )
        self._schedule(plan.total_ms + 1, generation, self._finish)
        return True

    def cancel(self) -> None:
        was_running = self.running
        self._generation += 1
        for identifier in self._scheduled:
            try:
                self.scheduler.after_cancel(identifier)
            except Exception:
                # Tk rejects identifiers whose callbacks have already run.
                pass
        self._scheduled.clear()
        self.running = False
        if was_running:
            self.on_clear()

    def _schedule(
        self,
        delay_ms: int,
        generation: int,
        callback: Callable[[], None],
    ) -> None:
        self._scheduled.append(
            self.scheduler.after(
                delay_ms,
                lambda token=generation, action=callback: self._run(token, action),
            )
        )

    def _run(self, generation: int, callback: Callable[[], None]) -> None:
        if self.running and generation == self._generation:
            callback()

    def _finish(self) -> None:
        self._scheduled.clear()
        self.running = False
        self.on_clear()


def trigger_add_motion(
    successful: bool,
    mark: PersonalMark | None,
    start_motion: Callable[[PersonalMark], None],
) -> bool:
    """Start only for a Core success with a nonempty, validated Personal Mark."""
    if not successful or mark is None or not mark.strokes:
        return False
    start_motion(mark)
    return True


def prefers_reduced_motion(
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> bool:
    """Read an explicit override, then the Windows client-animation preference."""
    environment = os.environ if environ is None else environ
    override = environment.get("SHIRUSHI_REDUCED_MOTION", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    current_platform = sys.platform if platform is None else platform
    if current_platform != "win32":
        return False
    try:
        import ctypes

        enabled = ctypes.c_int(1)
        spi_get_client_area_animation = 0x1042
        result = ctypes.windll.user32.SystemParametersInfoW(
            spi_get_client_area_animation,
            0,
            ctypes.byref(enabled),
            0,
        )
        return bool(result) and not bool(enabled.value)
    except (AttributeError, OSError):
        return False


def _group_rendered_strokes(
    points: tuple[RenderPoint, ...],
) -> tuple[tuple[RenderPoint, ...], ...]:
    strokes: list[list[RenderPoint]] = []
    for point in points:
        if point.starts_stroke or not strokes:
            strokes.append([])
        strokes[-1].append(point)
    return tuple(tuple(stroke) for stroke in strokes)


def _fit_rendered_to_stage(
    points: tuple[RenderPoint, ...],
    stage: MotionStage,
) -> tuple[RenderPoint, ...]:
    if not points:
        return ()
    minimum_x = min(point.x for point in points)
    maximum_x = max(point.x for point in points)
    minimum_y = min(point.y for point in points)
    maximum_y = max(point.y for point in points)
    span_x = maximum_x - minimum_x
    span_y = maximum_y - minimum_y
    if span_x == 0 and span_y == 0:
        center_x = stage.x + stage.width * 0.5
        center_y = stage.y + stage.height * 0.58
        return tuple(
            RenderPoint(center_x, center_y, point.delay_ms, point.starts_stroke)
            for point in points
        )

    preferred_width = stage.width * 0.38
    maximum_height = stage.height * 0.30
    if span_x == 0:
        scale = maximum_height / span_y
    elif span_y == 0:
        scale = preferred_width / span_x
    else:
        scale = min(preferred_width / span_x, maximum_height / span_y)

    fitted_width = span_x * scale
    fitted_height = span_y * scale
    inset_x = stage.width * 0.04
    inset_y = stage.height * 0.04
    center_x = stage.x + stage.width * 0.5
    center_y = stage.y + stage.height * 0.58
    left = min(
        max(center_x - fitted_width / 2, stage.x + inset_x),
        stage.x + stage.width - inset_x - fitted_width,
    )
    top = min(
        max(center_y - fitted_height / 2, stage.y + inset_y),
        stage.y + stage.height - inset_y - fitted_height,
    )
    return tuple(
        RenderPoint(
            x=left + (point.x - minimum_x) * scale,
            y=top + (point.y - minimum_y) * scale,
            delay_ms=point.delay_ms,
            starts_stroke=point.starts_stroke,
        )
        for point in points
    )


def _stroke_length(stroke: tuple[RenderPoint, ...]) -> float:
    return sum(
        math.hypot(current.x - previous.x, current.y - previous.y)
        for previous, current in zip(stroke, stroke[1:])
    )


def _cumulative_distances(stroke: tuple[RenderPoint, ...]) -> tuple[float, ...]:
    if not stroke:
        return ()
    distances = [0.0]
    for previous, current in zip(stroke, stroke[1:]):
        distances.append(
            distances[-1] + math.hypot(current.x - previous.x, current.y - previous.y)
        )
    return tuple(distances)


def _allocate_stroke_durations(
    lengths: list[float],
    timing: AddMotionTiming,
) -> tuple[int, ...]:
    count = len(lengths)
    if count == 0:
        return ()
    total = timing.stroke_total_ms
    if count == 1:
        return (total,)

    floor = min(float(timing.min_stroke_ms), total / count)
    cap = max(floor, total * timing.max_stroke_share)
    weights = [max(length, 0.001) for length in lengths]
    allocated = [floor] * count
    remaining = total - floor * count
    active = set(range(count))

    while remaining > 1e-7 and active:
        weight_total = sum(weights[index] for index in active)
        capped: list[int] = []
        for index in active:
            proposed = remaining * weights[index] / weight_total
            if allocated[index] + proposed > cap:
                capped.append(index)
        if not capped:
            for index in active:
                allocated[index] += remaining * weights[index] / weight_total
            remaining = 0.0
            break
        for index in capped:
            addition = cap - allocated[index]
            allocated[index] = cap
            remaining -= addition
            active.remove(index)

    rounded = [int(math.floor(value)) for value in allocated]
    remainder = total - sum(rounded)
    order = sorted(
        range(count),
        key=lambda index: (allocated[index] - rounded[index], weights[index]),
        reverse=True,
    )
    for index in order[:remainder]:
        rounded[index] += 1
    return tuple(rounded)
