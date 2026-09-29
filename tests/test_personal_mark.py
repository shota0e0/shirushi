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

from gui.i18n import personal_mark_messages
from personal_mark import (
    PersonalMark,
    PersonalMarkRenderer,
    PersonalMarkValidationError,
    PersonalMarkVersionError,
    Stroke,
    StrokePoint,
    normalized_point,
)
from personal_mark_replay import PersonalMarkReplayController
from personal_mark_store import PersonalMarkStore


def sample_mark() -> PersonalMark:
    return PersonalMark(
        strokes=(
            Stroke((StrokePoint(0.1, 0.2, 0), StrokePoint(0.4, 0.5, 16))),
            Stroke((StrokePoint(0.8, 0.7, 0), StrokePoint(1.0, 1.0, 12))),
        )
    )


class PersonalMarkModelTests(unittest.TestCase):
    def test_empty_single_and_multi_stroke_round_trip(self) -> None:
        values = (
            PersonalMark.empty(),
            PersonalMark(strokes=(Stroke((StrokePoint(0.5, 0.5, 0),)),)),
            sample_mark(),
        )
        for value in values:
            self.assertEqual(value, PersonalMark.from_mapping(value.to_mapping()))

    def test_invalid_coordinates_are_rejected(self) -> None:
        raw = sample_mark().to_mapping()
        raw["strokes"][0]["points"][0]["x"] = 1.01
        with self.assertRaises(PersonalMarkValidationError):
            PersonalMark.from_mapping(raw)

    def test_version_mismatch_is_distinct(self) -> None:
        raw = sample_mark().to_mapping()
        raw["version"] = 2
        with self.assertRaises(PersonalMarkVersionError):
            PersonalMark.from_mapping(raw)

    def test_normalization_stays_in_range_and_preserves_shape_after_resize(self) -> None:
        point = normalized_point(25, 75, 100, 100, 12)
        self.assertEqual((0.25, 0.75), (point.x, point.y))
        clamped = normalized_point(-2, 120, 100, 100, 15)
        self.assertEqual((0.0, 1.0), (clamped.x, clamped.y))
        mark = PersonalMark(strokes=(Stroke((point,)),))
        small = PersonalMarkRenderer.replay_points(mark, 100, 200)[0]
        large = PersonalMarkRenderer.replay_points(mark, 400, 800)[0]
        self.assertEqual((25.0, 150.0), (small.x, small.y))
        self.assertEqual((100.0, 600.0), (large.x, large.y))

    def test_malformed_saved_shape_is_rejected(self) -> None:
        with self.assertRaises(PersonalMarkValidationError):
            PersonalMark.from_mapping({"version": 1, "type": "handwritten", "strokes": [{}]})


class PersonalMarkStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = PROJECT / "tests" / ".personal_mark_runtime"
        self.runtime.mkdir(exist_ok=True)
        self.path = self.runtime / f"{uuid.uuid4().hex}.json"

    def tearDown(self) -> None:
        for path in self.runtime.glob(f".{self.path.name}.*.tmp"):
            path.unlink(missing_ok=True)
        self.path.unlink(missing_ok=True)

    def test_save_and_reload(self) -> None:
        store = PersonalMarkStore(self.path)
        store.save(sample_mark())
        self.assertEqual(sample_mark(), store.load().mark)
        self.assertEqual(1, json.loads(self.path.read_text(encoding="utf-8"))["version"])

    def test_corrupted_data_falls_back_without_raising(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        loaded = PersonalMarkStore(self.path).load()
        self.assertIsNone(loaded.mark)
        self.assertEqual("corrupted", loaded.issue)

    def test_version_mismatch_falls_back_without_overwriting(self) -> None:
        self.path.write_text(
            json.dumps({"version": 99, "type": "handwritten", "strokes": []}),
            encoding="utf-8",
        )
        before = self.path.read_bytes()
        loaded = PersonalMarkStore(self.path).load()
        self.assertIsNone(loaded.mark)
        self.assertEqual("version_mismatch", loaded.issue)
        self.assertEqual(before, self.path.read_bytes())


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
        for identifier, _delay, callback in sorted(self.calls, key=lambda item: (item[1], item[0])):
            if identifier not in self.cancelled:
                callback()


class PersonalMarkReplayTests(unittest.TestCase):
    def test_replay_preserves_stroke_and_point_order(self) -> None:
        scheduler = FakeScheduler()
        drawn = []
        controller = PersonalMarkReplayController(scheduler, drawn.append)
        controller.start(sample_mark(), 100, 100)
        scheduler.run()
        self.assertEqual([(10.0, 20.0), (40.0, 50.0), (80.0, 70.0), (100.0, 100.0)], [(p.x, p.y) for p in drawn])
        self.assertEqual([True, False, True, False], [p.starts_stroke for p in drawn])

    def test_cancel_prevents_scheduled_drawing(self) -> None:
        scheduler = FakeScheduler()
        drawn = []
        controller = PersonalMarkReplayController(scheduler, drawn.append)
        controller.start(sample_mark(), 100, 100)
        controller.cancel()
        scheduler.run()
        self.assertEqual([], drawn)
        self.assertFalse(controller.running)

    def test_restart_cancels_old_generation_and_replays_once(self) -> None:
        scheduler = FakeScheduler()
        drawn = []
        controller = PersonalMarkReplayController(scheduler, drawn.append)
        controller.start(sample_mark(), 100, 100)
        first_ids = [identifier for identifier, _delay, _callback in scheduler.calls]
        controller.start(sample_mark(), 200, 200)
        scheduler.run()
        self.assertTrue(set(first_ids).issubset(set(scheduler.cancelled)))
        self.assertEqual([(20.0, 40.0), (80.0, 100.0), (160.0, 140.0), (200.0, 200.0)], [(p.x, p.y) for p in drawn])

    def test_new_ui_copy_exists_in_ja_and_en(self) -> None:
        self.assertEqual(set(personal_mark_messages("ja")), set(personal_mark_messages("en")))
        self.assertEqual("あなたのしるし", personal_mark_messages("ja")["title"])


if __name__ == "__main__":
    unittest.main()
