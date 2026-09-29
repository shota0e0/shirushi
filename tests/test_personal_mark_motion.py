"""Public motion-domain coverage; 13 tests retained unchanged.

Two legacy Tk integration tests are EXCLUDED / PENDING in this public tree.
See docs/development/f2c-verification.md for exact IDs and local-only coverage.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
import uuid


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from personal_mark import PersonalMark, Stroke, StrokePoint
from personal_mark_motion import (
    AddMotionTiming,
    PersonalMarkAddMotionController,
    PersonalMarkMotionPlanner,
    MotionStage,
    prefers_reduced_motion,
    trigger_add_motion,
)
from personal_mark_store import PersonalMarkStore


def stroke(*points: tuple[float, float, int]) -> Stroke:
    return Stroke(tuple(StrokePoint(*point) for point in points))


class FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, object]] = []
        self.cancelled: list[int] = []
        self._next_id = 0

    def after(self, delay_ms, callback):
        self._next_id += 1
        self.calls.append((self._next_id, delay_ms, callback))
        return self._next_id

    def after_cancel(self, identifier):
        self.cancelled.append(identifier)

    def run(self) -> None:
        for identifier, _delay, callback in sorted(
            self.calls, key=lambda item: (item[1], item[0])
        ):
            if identifier not in self.cancelled:
                callback()


class PersonalMarkMotionTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = PersonalMarkMotionPlanner()

    def test_single_stroke_uses_normalized_total_not_saved_timestamps(self) -> None:
        fast = PersonalMark(strokes=(stroke((0.0, 0.0, 0), (1.0, 0.0, 1)),))
        slow = PersonalMark(strokes=(stroke((0.0, 0.0, 0), (1.0, 0.0, 120_000)),))
        fast_plan = self.planner.plan(fast, 200, 100)
        slow_plan = self.planner.plan(slow, 200, 100)
        self.assertEqual((1400,), fast_plan.stroke_durations_ms)
        self.assertEqual(
            [point.delay_ms for point in fast_plan.points],
            [point.delay_ms for point in slow_plan.points],
        )
        self.assertEqual(2700, fast_plan.total_ms)

    def test_multi_stroke_order_and_point_order_are_preserved(self) -> None:
        mark = PersonalMark(
            strokes=(
                stroke((0.1, 0.2, 999), (0.2, 0.3, 1000)),
                stroke((0.8, 0.7, 0), (0.9, 0.8, 1), (1.0, 0.9, 2)),
            )
        )
        plan = self.planner.plan(mark, 100, 100)
        self.assertEqual(
            [(10.0, 20.0), (20.0, 30.0), (80.0, 70.0), (90.0, 80.0), (100.0, 90.0)],
            [(point.x, point.y) for point in plan.points],
        )
        self.assertEqual([True, False, True, False, False], [p.starts_stroke for p in plan.points])
        self.assertEqual(1400, sum(plan.stroke_durations_ms))

    def test_very_short_stroke_receives_minimum_duration(self) -> None:
        mark = PersonalMark(
            strokes=(
                stroke((0.0, 0.0, 0), (0.00001, 0.0, 1)),
                stroke((0.0, 0.0, 0), (1.0, 1.0, 1)),
            )
        )
        plan = self.planner.plan(mark, 200, 100)
        self.assertGreaterEqual(plan.stroke_durations_ms[0], 120)

    def test_very_long_stroke_cannot_monopolize_multi_stroke_motion(self) -> None:
        long_points = tuple(
            (float(index % 2), 0.0, index) for index in range(51)
        )
        mark = PersonalMark(
            strokes=(
                stroke(*long_points),
                stroke((0.0, 0.0, 0), (0.01, 0.0, 1)),
            )
        )
        plan = self.planner.plan(mark, 200, 100)
        self.assertLessEqual(plan.stroke_durations_ms[0], 840)
        self.assertEqual(1400, sum(plan.stroke_durations_ms))

    def test_stage_timing_is_explicit_and_tunable(self) -> None:
        timing = AddMotionTiming(
            stroke_total_ms=1000,
            rights_ms=300,
            completion_ms=200,
            hold_ms=50,
            fade_ms=250,
        )
        plan = PersonalMarkMotionPlanner(timing).plan(
            PersonalMark(strokes=(stroke((0.0, 0.0, 0), (1.0, 1.0, 1)),)),
            100,
            100,
        )
        self.assertEqual((1000, 1300, 1550, 1800), (plan.rights_at_ms, plan.completion_at_ms, plan.fade_at_ms, plan.total_ms))

    def test_mark_is_fitted_inside_letterboxed_image_content_rectangle(self) -> None:
        mark = PersonalMark(
            strokes=(stroke((0.0, 0.0, 0), (1.0, 0.5, 1), (0.4, 1.0, 2)),)
        )
        stage = MotionStage(60, 0, 100, 150)
        plan = self.planner.plan(mark, 220, 150, stage=stage)
        xs = [point.x for point in plan.points]
        ys = [point.y for point in plan.points]
        self.assertGreaterEqual(min(xs), stage.x)
        self.assertLessEqual(max(xs), stage.x + stage.width)
        self.assertGreaterEqual(min(ys), stage.y)
        self.assertLessEqual(max(ys), stage.y + stage.height)
        self.assertAlmostEqual(stage.width * 0.38, max(xs) - min(xs))

    def test_fitted_mark_keeps_geometry_aspect_and_uses_lower_center(self) -> None:
        mark = PersonalMark(
            strokes=(stroke((0.0, 0.0, 0), (1.0, 1.0, 1)),)
        )
        stage = MotionStage(10, 20, 200, 100)
        plan = self.planner.plan(mark, 220, 150, stage=stage)
        first, last = plan.points
        fitted_ratio = (last.x - first.x) / (last.y - first.y)
        self.assertAlmostEqual(480 / 280, fitted_ratio)
        center_y = (first.y + last.y) / 2
        self.assertAlmostEqual(stage.y + stage.height * 0.58, center_y)


class PersonalMarkMotionControllerTests(unittest.TestCase):
    def test_normal_motion_orders_stroke_rights_completion_fade_and_clear(self) -> None:
        scheduler = FakeScheduler()
        events: list[str] = []
        controller = PersonalMarkAddMotionController(
            scheduler,
            on_begin=lambda: events.append("begin"),
            draw_point=lambda point: events.append(f"point:{point.x}"),
            show_rights=lambda: events.append("rights"),
            show_completion=lambda: events.append("completion"),
            show_fade=lambda progress: events.append(f"fade:{progress}"),
            on_clear=lambda: events.append("clear"),
        )
        plan = PersonalMarkMotionPlanner().plan(
            PersonalMark(strokes=(stroke((0.0, 0.0, 0), (1.0, 0.0, 1)),)),
            100,
            100,
        )
        self.assertTrue(controller.start(plan))
        scheduler.run()
        self.assertEqual("begin", events[0])
        self.assertLess(events.index("point:100.0"), events.index("rights"))
        self.assertLess(events.index("rights"), events.index("completion"))
        self.assertLess(events.index("completion"), events.index("fade:0.25"))
        self.assertEqual("clear", events[-1])
        self.assertFalse(controller.running)

    def test_reduced_motion_is_static_then_clears_without_fade(self) -> None:
        scheduler = FakeScheduler()
        events: list[str] = []
        controller = PersonalMarkAddMotionController(
            scheduler,
            on_begin=lambda: events.append("begin"),
            draw_point=lambda _point: events.append("point"),
            show_rights=lambda: events.append("rights"),
            show_completion=lambda: events.append("completion"),
            show_fade=lambda _progress: events.append("fade"),
            on_clear=lambda: events.append("clear"),
        )
        plan = PersonalMarkMotionPlanner().plan(
            PersonalMark(strokes=(stroke((0.0, 0.0, 0), (1.0, 0.0, 1)),)),
            100,
            100,
        )
        controller.start(plan, reduced_motion=True)
        self.assertEqual(["begin", "point", "point", "rights", "completion"], events)
        scheduler.run()
        self.assertNotIn("fade", events)
        self.assertEqual("clear", events[-1])

    def test_cancel_prevents_stale_callbacks(self) -> None:
        scheduler = FakeScheduler()
        events: list[str] = []
        controller = PersonalMarkAddMotionController(
            scheduler,
            on_begin=lambda: events.append("begin"),
            draw_point=lambda _point: events.append("point"),
            show_rights=lambda: events.append("rights"),
            show_completion=lambda: events.append("completion"),
            show_fade=lambda _progress: events.append("fade"),
            on_clear=lambda: events.append("clear"),
        )
        plan = PersonalMarkMotionPlanner().plan(
            PersonalMark(strokes=(stroke((0.0, 0.0, 0), (1.0, 0.0, 1)),)),
            100,
            100,
        )
        controller.start(plan)
        controller.cancel()
        scheduler.run()
        self.assertEqual(["begin", "clear"], events)


class PersonalMarkMotionTriggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = PROJECT / "tests" / ".personal_mark_runtime"
        self.runtime.mkdir(exist_ok=True)
        self.path = self.runtime / f"{uuid.uuid4().hex}.json"

    def tearDown(self) -> None:
        self.path.unlink(missing_ok=True)

    def test_success_runs_motion_and_failure_does_not(self) -> None:
        mark = PersonalMark(strokes=(stroke((0.0, 0.0, 0), (1.0, 1.0, 1)),))
        started: list[PersonalMark] = []
        self.assertTrue(trigger_add_motion(True, mark, started.append))
        self.assertFalse(trigger_add_motion(False, mark, started.append))
        self.assertEqual([mark], started)

    def test_missing_empty_malformed_and_unsupported_marks_skip_safely(self) -> None:
        started = []
        self.assertFalse(trigger_add_motion(True, None, started.append))
        self.assertFalse(trigger_add_motion(True, PersonalMark.empty(), started.append))

        self.path.write_text("{malformed", encoding="utf-8")
        malformed = PersonalMarkStore(self.path).load()
        self.assertEqual("corrupted", malformed.issue)
        self.assertFalse(trigger_add_motion(True, malformed.mark, started.append))

        self.path.write_text(
            json.dumps({"version": 99, "type": "handwritten", "strokes": []}),
            encoding="utf-8",
        )
        unsupported = PersonalMarkStore(self.path).load()
        self.assertEqual("version_mismatch", unsupported.issue)
        self.assertFalse(trigger_add_motion(True, unsupported.mark, started.append))
        self.assertEqual([], started)

    def test_reduced_motion_override_is_deterministic(self) -> None:
        self.assertTrue(prefers_reduced_motion({"SHIRUSHI_REDUCED_MOTION": "1"}, "linux"))
        self.assertFalse(prefers_reduced_motion({"SHIRUSHI_REDUCED_MOTION": "0"}, "win32"))



if __name__ == "__main__":
    unittest.main()
