from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock
import uuid


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from personal_mark_v2 import MarkV2Error, confirm_typed_save, personal_mark_v2_from_mapping, prepare_typed_save
from personal_mark_v2_store import PersonalMarkV2Store, PersonalMarkV2StoreError


def handwritten_mark():
    return personal_mark_v2_from_mapping(
        {
            "version": 2,
            "type": "handwritten",
            "coordinateSpace": {"width": 480, "height": 220},
            "strokes": [{"points": [{"x": 0.2, "y": 0.3, "t": 0}]}],
        }
    )


def typed_mark(text: str = "Mori"):
    return confirm_typed_save(prepare_typed_save(text))


def legacy_mapping() -> dict:
    return {
        "version": 1,
        "type": "handwritten",
        "strokes": [{"points": [{"x": 0.2, "y": 0.3, "t": 0}]}],
    }


class PersonalMarkV2StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = PROJECT / "tests" / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.root = runtime / f"f2b-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.v2_path = self.root / "personal-mark-v2.json"
        self.v1_path = self.root / "personal-mark-v1.json"
        self.store = PersonalMarkV2Store(self.v2_path, self.v1_path)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_atomic_v2_save_and_exact_load_preserve_v1(self) -> None:
        self.v1_path.write_text(json.dumps(legacy_mapping()), encoding="utf-8")
        before_v1 = self.v1_path.read_bytes()
        mark = handwritten_mark()
        self.store.save(mark)
        loaded = self.store.load()
        self.assertEqual("v2", loaded.state)
        self.assertEqual(mark, loaded.mark)
        self.assertEqual(before_v1, self.v1_path.read_bytes())
        self.assertEqual([], list(self.root.glob(".personal-mark-v2.json.*.tmp")))

    def test_typed_save_enforces_nfc_confirmation_at_save_boundary(self) -> None:
        preparation = prepare_typed_save("Cafe\u0301")
        mark = confirm_typed_save(preparation, accept_normalization=True)
        with self.assertRaises(MarkV2Error) as missing_draft:
            self.store.save(mark)
        self.assertEqual("TYPED_DRAFT_REQUIRED", missing_draft.exception.code)
        with self.assertRaises(MarkV2Error) as missing_confirmation:
            self.store.save(mark, typed_draft="Cafe\u0301")
        self.assertEqual("NORMALIZATION_CONFIRMATION_REQUIRED", missing_confirmation.exception.code)
        self.store.save(mark, typed_draft="Cafe\u0301", accept_normalization=True)
        self.assertEqual(mark, self.store.load().mark)

    def test_typed_special_confirmation_is_enforced_at_save_boundary(self) -> None:
        preparation = prepare_typed_save("A\u200dB")
        mark = confirm_typed_save(preparation, accept_special_characters=True)
        with self.assertRaises(MarkV2Error) as caught:
            self.store.save(mark, typed_draft=mark.text)
        self.assertEqual("SPECIAL_CHARACTER_CONFIRMATION_REQUIRED", caught.exception.code)
        self.store.save(mark, typed_draft=mark.text, accept_special_characters=True)

    def test_failed_atomic_replace_preserves_prior_v2_and_cleans_temporary(self) -> None:
        self.store.save(handwritten_mark())
        before = self.v2_path.read_bytes()
        with mock.patch("personal_mark_v2_store.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(PersonalMarkV2StoreError) as caught:
                self.store.save(handwritten_mark())
        self.assertEqual("IO_ERROR", caught.exception.code)
        self.assertEqual(before, self.v2_path.read_bytes())
        self.assertEqual([], list(self.root.glob(".personal-mark-v2.json.*.tmp")))

    def test_temporary_cleanup_failure_is_reported_without_losing_prior_v2(self) -> None:
        self.store.save(handwritten_mark())
        before = self.v2_path.read_bytes()
        with mock.patch("personal_mark_v2_store.os.replace", side_effect=PermissionError("denied")):
            with mock.patch.object(Path, "unlink", side_effect=PermissionError("cleanup denied")):
                with self.assertRaises(PersonalMarkV2StoreError) as caught:
                    self.store.save(handwritten_mark())
        self.assertEqual("TEMP_CLEANUP_FAILED", caught.exception.code)
        self.assertEqual(before, self.v2_path.read_bytes())

    def test_corrupt_or_unsupported_v2_never_falls_back_to_v1(self) -> None:
        self.v1_path.write_text(json.dumps(legacy_mapping()), encoding="utf-8")
        for raw, expected in (
            (b"{not json", "malformed"),
            (b'{"version":3,"type":"handwritten"}', "unsupported"),
            (b'{"version":2,"type":"future"}', "unsupported"),
        ):
            with self.subTest(expected=expected, raw=raw):
                self.v2_path.write_bytes(raw)
                loaded = self.store.load()
                self.assertEqual(expected, loaded.state)
                self.assertEqual("v2", loaded.source)
                self.assertIsNone(loaded.legacy_mark)

    def test_absent_v2_reads_valid_v1_without_migration(self) -> None:
        raw = json.dumps(legacy_mapping(), separators=(",", ":")).encode()
        self.v1_path.write_bytes(raw)
        loaded = self.store.load()
        self.assertEqual("legacy_v1", loaded.state)
        self.assertEqual("legacy-unknown", loaded.geometry_provenance)
        self.assertEqual(raw, loaded.raw_bytes)
        self.assertFalse(self.v2_path.exists())
        self.assertEqual(raw, self.v1_path.read_bytes())
        envelope = loaded.to_envelope()
        self.assertEqual("shirushi-personal-mark-read", envelope["contract"])
        self.assertEqual(1, envelope["contractVersion"])
        self.assertEqual(1, envelope["sourceVersion"])
        self.assertNotIn("coordinateSpace", envelope["payload"])

    def test_absent_malformed_unsupported_and_io_are_distinct(self) -> None:
        self.assertEqual("absent", self.store.load().state)
        self.v1_path.write_text("{}", encoding="utf-8")
        self.assertEqual("malformed", self.store.load().state)
        self.v1_path.write_text(json.dumps({"version": 7, "type": "handwritten", "strokes": []}), encoding="utf-8")
        self.assertEqual("unsupported", self.store.load().state)
        self.v1_path.unlink()
        self.v2_path.mkdir()
        loaded = self.store.load()
        self.assertEqual("io_error", loaded.state)
        self.assertEqual("v2", loaded.source)

    def test_oversized_or_pathological_legacy_is_malformed_not_accepted(self) -> None:
        raw = json.dumps(legacy_mapping()).encode() + b" " * (2 * 1024 * 1024)
        self.v1_path.write_bytes(raw)
        loaded = self.store.load()
        self.assertEqual("malformed", loaded.state)
        self.assertEqual("PAYLOAD_TOO_LARGE", loaded.error_code)

        self.v1_path.write_bytes(b"[" * 1100 + b"]" * 1100)
        self.assertEqual("malformed", self.store.load().state)

        value = legacy_mapping()
        value["strokes"][0]["points"][0]["x"] = 10**400
        self.v1_path.write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual("malformed", self.store.load().state)

    def test_unknown_profile_is_preserved_with_explicit_render_state(self) -> None:
        value = {
            "version": 2,
            "type": "typed",
            "text": "Mori",
            "renderProfile": {"id": "future-profile", "version": 9},
        }
        self.v2_path.write_text(json.dumps(value), encoding="utf-8")
        loaded = self.store.load()
        self.assertEqual("v2", loaded.state)
        support = loaded.to_envelope()["renderProfileSupport"]
        self.assertEqual(
            {"state": "UNSUPPORTED", "errorCode": "UNSUPPORTED_RENDER_PROFILE"},
            support,
        )

    def test_path_identity_alias_is_rejected(self) -> None:
        with self.assertRaises(PersonalMarkV2StoreError) as caught:
            PersonalMarkV2Store(self.v1_path, self.v1_path)
        self.assertEqual("PATH_ALIASES_LEGACY", caught.exception.code)

    def test_hardlink_alias_is_rejected(self) -> None:
        self.v1_path.write_text("legacy", encoding="utf-8")
        os.link(self.v1_path, self.v2_path)
        with self.assertRaises(PersonalMarkV2StoreError) as caught:
            PersonalMarkV2Store(self.v2_path, self.v1_path)
        self.assertEqual("PATH_ALIASES_LEGACY", caught.exception.code)

    def test_symlink_alias_is_rejected_when_supported(self) -> None:
        self.v1_path.write_text("legacy", encoding="utf-8")
        try:
            self.v2_path.symlink_to(self.v1_path)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(PersonalMarkV2StoreError) as caught:
            PersonalMarkV2Store(self.v2_path, self.v1_path)
        self.assertEqual("PATH_ALIASES_LEGACY", caught.exception.code)

    def test_dangling_v2_symlink_is_io_error_and_does_not_fallback(self) -> None:
        self.v1_path.write_text(json.dumps(legacy_mapping()), encoding="utf-8")
        try:
            self.v2_path.symlink_to(self.root / "missing-target.json")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        loaded = PersonalMarkV2Store(self.v2_path, self.v1_path).load()
        self.assertEqual("io_error", loaded.state)
        self.assertEqual("v2", loaded.source)
        self.assertIsNone(loaded.legacy_mark)

    def test_mocked_dangling_v2_entry_is_io_error_without_fallback(self) -> None:
        with mock.patch.object(Path, "open", side_effect=FileNotFoundError()):
            with mock.patch("personal_mark_v2_store.os.path.lexists", return_value=True):
                loaded = self.store.load()
        self.assertEqual("io_error", loaded.state)
        self.assertEqual("v2", loaded.source)
        self.assertIsNone(loaded.legacy_mark)

    def test_path_resolution_failure_is_fail_closed(self) -> None:
        with mock.patch.object(Path, "resolve", side_effect=OSError("unavailable")):
            with self.assertRaises(PersonalMarkV2StoreError) as caught:
                PersonalMarkV2Store(self.v2_path, self.v1_path)
        self.assertEqual("PATH_IDENTITY_UNAVAILABLE", caught.exception.code)

    def test_v2_directory_error_does_not_fallback(self) -> None:
        self.v1_path.write_text(json.dumps(legacy_mapping()), encoding="utf-8")
        self.v2_path.mkdir()
        loaded = self.store.load()
        self.assertEqual("io_error", loaded.state)
        self.assertEqual("v2", loaded.source)
        self.assertIsNone(loaded.legacy_mark)


if __name__ == "__main__":
    unittest.main()
