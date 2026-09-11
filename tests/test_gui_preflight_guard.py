from __future__ import annotations

from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gui.app import CreatorWindow
from gui.inspection_presenter import preflight_warning
from inspection_service import InspectionResult


def inspection_result(*, integrity: str, c2pa: str, trustmark: str) -> InspectionResult:
    return InspectionResult(
        contract_version="1.0",
        source_path="sample.png",
        input_format="PNG",
        ai_training_use="NO_PERMISSION_INFO",
        ai_inference_use="NO_PERMISSION_INFO",
        rights_status="NOT_DETECTED",
        cawg_status="NOT_DETECTED",
        c2pa_status=c2pa,
        trustmark_status=trustmark,
        trustmark_payload_status="NOT_DETECTED",
        integrity_status=integrity,
        signature_status="INDETERMINATE",
        durable_recovery="NOT_DETECTED",
        warnings=(),
        technical_details={},
    )


PLAIN = inspection_result(integrity="UNKNOWN", c2pa="NOT_DETECTED", trustmark="NOT_DETECTED")
VALID = inspection_result(integrity="OK", c2pa="DETECTED", trustmark="DETECTED")
TRUSTMARK_ONLY = inspection_result(integrity="UNKNOWN", c2pa="NOT_DETECTED", trustmark="DETECTED")
VERIFICATION_FAILURE = inspection_result(
    integrity="VERIFICATION_FAILED", c2pa="VERIFICATION_FAILED", trustmark="DETECTED"
)


class PreflightCopyTests(unittest.TestCase):
    def test_plain_image_allows_normal_creation(self) -> None:
        self.assertIsNone(preflight_warning(PLAIN))

    def test_valid_shirushi_stops_with_existing_information_warning(self) -> None:
        warning = preflight_warning(VALID)
        self.assertIsNotNone(warning)
        self.assertEqual("この画像にはすでにしるしが付いています。", warning.heading)
        self.assertIn("既存情報に影響する可能性", warning.detail)

    def test_trustmark_only_copy_does_not_claim_rights_are_known(self) -> None:
        warning = preflight_warning(TRUSTMARK_ONLY)
        self.assertIsNotNone(warning)
        self.assertEqual("この画像にはしるしの識別情報が残っています。", warning.heading)
        self.assertIn("AI利用条件そのものは確認できません", warning.detail)

    def test_verification_failure_stops_with_indeterminate_warning(self) -> None:
        warning = preflight_warning(VERIFICATION_FAILURE)
        self.assertIsNotNone(warning)
        self.assertEqual(
            "既存のしるし情報を検出しましたが、状態を完全に確認できません。",
            warning.heading,
        )


class PreflightFlowTests(unittest.TestCase):
    def make_window(self, *, confirm: bool = False) -> CreatorWindow:
        window = CreatorWindow.__new__(CreatorWindow)
        window.input_path = Path("sample.png")
        window.cancel_event = threading.Event()
        window._begin_create = Mock()
        window._show_inspection = Mock()
        window._show_preflight_warning = Mock()
        window._confirm_reapply = Mock(return_value=confirm)
        window._show_message = Mock()
        return window

    def test_plain_result_continues_without_confirmation(self) -> None:
        window = self.make_window()
        window._handle_preflight_result(PLAIN)
        window._begin_create.assert_called_once_with(Path("sample.png"))
        window._confirm_reapply.assert_not_called()

    def test_existing_states_default_to_stop(self) -> None:
        for result in (VALID, TRUSTMARK_ONLY, VERIFICATION_FAILURE):
            with self.subTest(result=result):
                window = self.make_window(confirm=False)
                window._handle_preflight_result(result)
                window._show_inspection.assert_called_once_with(result)
                window._show_preflight_warning.assert_called_once()
                window._begin_create.assert_not_called()

    def test_explicit_confirmation_is_required_for_reapplication(self) -> None:
        window = self.make_window(confirm=True)
        window._handle_preflight_result(VALID)
        window._begin_create.assert_called_once_with(Path("sample.png"), preserve_inspection=True)

    def test_cancelled_preflight_never_creates(self) -> None:
        window = self.make_window(confirm=True)
        window.cancel_event.set()
        window._handle_preflight_result(PLAIN)
        window._begin_create.assert_not_called()
        window._show_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
