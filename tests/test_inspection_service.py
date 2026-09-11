from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_service import (
    InspectionService,
    TrustMarkObservation,
)
from gui.inspection_presenter import (
    inspection_explanation,
    inspection_heading,
    inspection_primary_rows,
    inspection_summary_rows,
    inspection_technical_rows,
    signature_explanation,
    technical_details_text,
)


def verifier_contract(
    result: str,
    reason: str | None = None,
    *,
    image_format: str = "png",
    trustmark_present: bool | None = None,
    signature_valid: bool | None = None,
    trust_validated: bool = False,
) -> dict:
    passed = result == "PASS"
    return {
        "contractVersion": "1.0",
        "result": result,
        "reasonCode": reason,
        "message": "raw verifier message is not a user-facing source",
        "input": {
            "path": r"C:\images\sample." + ("jpg" if image_format == "jpeg" else "png"),
            "sha256": "A" * 64,
            "format": image_format,
            "dimensions": {"width": 1024, "height": 768},
        },
        "rights": {"preset": "AI利用拒否", "verified": passed},
        "trustmark": {
            "present": True if passed else trustmark_present,
            "schema": 2 if passed else None,
            "payloadLength": 68 if passed else None,
            "payloadMatch": True if passed else None,
        },
        "softBinding": {"present": passed, "algorithm": "com.adobe.trustmark.P" if passed else None, "match": True if passed else None},
        "c2pa": {"claimPresent": passed, "assertionDigestsMatch": True if passed else None},
        "signature": {
            "present": True if passed else None,
            "valid": True if passed and signature_valid is None else signature_valid,
            "trustValidated": trust_validated,
        },
        "diagnostics": [{"code": reason or "VERIFICATION_PASS", "status": "pass" if passed else "fail"}],
    }


def inspect_contract(contract: dict, observation: TrustMarkObservation) -> object:
    verifier_calls = []
    probe_calls = []

    def verifier(path: Path, c2patool: Path, settings: Path) -> dict:
        verifier_calls.append((path, c2patool, settings))
        return contract

    def probe(path: Path) -> TrustMarkObservation:
        probe_calls.append(path)
        return observation

    service = InspectionService(verifier=verifier, trustmark_probe=probe)
    value = service.inspect(Path("sample.png"))
    if len(verifier_calls) != 1:
        raise AssertionError("inspection must call the existing verifier exactly once")
    expected_probe_calls = 0 if contract["trustmark"]["present"] is not None else 1
    if len(probe_calls) != expected_probe_calls:
        raise AssertionError("TrustMark probe must run only when verifier state is incomplete")
    return value


class InspectionServiceTests(unittest.TestCase):
    def test_01_valid_marked_png(self) -> None:
        result = inspect_contract(verifier_contract("PASS"), TrustMarkObservation(True, 2, 68, True))
        self.assertEqual("DETECTED", result.rights_status)
        self.assertEqual("DETECTED", result.cawg_status)
        self.assertEqual("DETECTED", result.c2pa_status)
        self.assertEqual("DETECTED", result.trustmark_status)
        self.assertEqual("MATCH", result.trustmark_payload_status)
        self.assertEqual("OK", result.integrity_status)
        self.assertEqual("PREVIEW", result.signature_status)
        self.assertEqual(
            "この画像には、AIによる学習・生成利用を希望しない意思が記録されています。",
            inspection_explanation(result),
        )
        self.assertEqual("完全性を確認済み", dict(inspection_primary_rows(result))["情報の状態"])
        self.assertNotEqual("正常", dict(inspection_primary_rows(result))["情報の状態"])

    def test_02_valid_marked_jpeg(self) -> None:
        result = inspect_contract(
            verifier_contract("PASS", image_format="jpeg"), TrustMarkObservation(True, 2, 68, True)
        )
        self.assertEqual("jpeg", result.input_format)
        self.assertEqual("NOT_WANTED", result.ai_training_use)
        self.assertEqual("NOT_WANTED", result.ai_inference_use)

    def test_03_image_without_rights_information(self) -> None:
        result = inspect_contract(
            verifier_contract("FAIL_C2PA", "C2PA_CLAIM_MISSING"),
            TrustMarkObservation(False, None, None, True),
        )
        self.assertEqual("NOT_DETECTED", result.rights_status)
        self.assertEqual("NO_PERMISSION_INFO", result.ai_training_use)
        self.assertEqual("NO_PERMISSION_INFO", result.ai_inference_use)
        primary = dict(inspection_primary_rows(result))
        self.assertEqual("意思表示なし", primary["AI学習利用"])
        self.assertEqual("意思表示なし", primary["AI生成 / 推論利用"])
        self.assertEqual("意思表示なし", primary["情報の状態"])
        self.assertIn("利用を許可または拒否していることを意味しません", inspection_explanation(result))

    def test_04_c2pa_metadata_stripped_without_remaining_trustmark(self) -> None:
        result = inspect_contract(
            verifier_contract("FAIL_C2PA", "C2PA_CLAIM_MISSING"),
            TrustMarkObservation(False, None, None, True),
        )
        self.assertEqual("NOT_DETECTED", result.c2pa_status)
        self.assertEqual("NOT_DETECTED", result.trustmark_status)
        self.assertEqual("UNKNOWN", result.integrity_status)

    def test_05_trustmark_only_detection_recovers_identifier_not_rights(self) -> None:
        result = inspect_contract(
            verifier_contract("FAIL_C2PA", "C2PA_CLAIM_MISSING"),
            TrustMarkObservation(True, 2, 68, True),
        )
        self.assertEqual("NOT_DETECTED", result.c2pa_status)
        self.assertEqual("DETECTED", result.trustmark_status)
        self.assertEqual("IDENTIFIER_RECOVERED", result.durable_recovery)
        self.assertEqual("NOT_DETECTED", result.rights_status)
        self.assertTrue(any("権利条件は回復できません" in warning for warning in result.warnings))
        self.assertEqual("しるしの識別情報を確認しました", inspection_heading(result))
        self.assertEqual("識別情報のみ", dict(inspection_primary_rows(result))["情報の状態"])
        self.assertIn("AI利用条件そのものは確認できません", inspection_explanation(result))

    def test_06_c2pa_verification_failure_is_not_missing(self) -> None:
        result = inspect_contract(
            verifier_contract("FAIL_C2PA", "C2PA_ASSET_DATA_HASH_MISMATCH"),
            TrustMarkObservation(True, 2, 68, True),
        )
        self.assertEqual("VERIFICATION_FAILED", result.c2pa_status)
        self.assertEqual("VERIFICATION_FAILED", result.integrity_status)
        self.assertEqual("DETECTED", result.rights_status)
        self.assertEqual(
            "しるしの情報は見つかりましたが、完全性を確認できませんでした",
            inspection_heading(result),
        )
        self.assertEqual("確認できません", dict(inspection_primary_rows(result))["情報の状態"])

    def test_07_invalid_trustmark_payload(self) -> None:
        result = inspect_contract(
            verifier_contract("FAIL_TRUSTMARK", "TRUSTMARK_PAYLOAD_LENGTH_MISMATCH"),
            TrustMarkObservation(True, 2, 12, True),
        )
        self.assertEqual("DECODE_FAILED", result.trustmark_status)
        self.assertEqual("INVALID", result.trustmark_payload_status)

    def test_08_wrong_trustmark_payload_is_distinct_from_decode_failure(self) -> None:
        result = inspect_contract(
            verifier_contract("FAIL_TRUSTMARK", "TRUSTMARK_PAYLOAD_MISMATCH"),
            TrustMarkObservation(True, 2, 68, True),
        )
        self.assertEqual("DETECTED", result.trustmark_status)
        self.assertEqual("MISMATCH", result.trustmark_payload_status)
        self.assertTrue(any("期待値と一致しません" in warning for warning in result.warnings))

    def test_09_missing_cawg_assertion(self) -> None:
        result = inspect_contract(
            verifier_contract("FAIL_C2PA", "C2PA_RIGHTS_ASSERTION_MISSING"),
            TrustMarkObservation(True, 2, 68, True),
        )
        self.assertEqual("NOT_DETECTED", result.rights_status)
        self.assertEqual("NOT_DETECTED", result.cawg_status)
        self.assertEqual("VERIFICATION_FAILED", result.c2pa_status)
        self.assertTrue(any("CAWG Rights assertion" in warning for warning in result.warnings))

    def test_10_gui_mapping_is_human_facing_and_details_are_whitelisted(self) -> None:
        contract = verifier_contract("PASS")
        contract["rights"]["token"] = "must-not-be-displayed"
        contract["c2pa"]["privateKey"] = "must-not-be-displayed"
        result = inspect_contract(contract, TrustMarkObservation(True, 2, 68, True))
        rows = dict(inspection_summary_rows(result))
        self.assertEqual("希望しない", rows["AI学習利用"])
        self.assertEqual("希望しない", rows["AI生成 / 推論利用"])
        self.assertEqual("検出", rows["C2PA"])
        self.assertEqual("検出", rows["CAWG Rights"])
        self.assertEqual("プレビュー", rows["署名"])
        self.assertEqual("しるしを確認しました", inspection_heading(result))
        self.assertEqual("プレビュー", dict(inspection_technical_rows(result))["署名"])
        self.assertEqual("現在は正式な信頼済み署名ではありません。", signature_explanation(result))
        details = technical_details_text(result)
        self.assertIn('"payloadValueExposed": false', details)
        self.assertNotIn("raw verifier message", details)
        self.assertNotIn("must-not-be-displayed", details)

    def test_11_trust_copy_separates_preview_from_future_trust_validation(self) -> None:
        preview = inspect_contract(
            verifier_contract("PASS"), TrustMarkObservation(True, 2, 68, True)
        )
        self.assertEqual("PREVIEW", preview.signature_status)
        self.assertEqual("プレビュー", dict(inspection_technical_rows(preview))["署名"])
        self.assertNotEqual("信頼済み", dict(inspection_technical_rows(preview))["署名"])

        future = inspect_contract(
            verifier_contract("PASS", trust_validated=True),
            TrustMarkObservation(True, 2, 68, True),
        )
        self.assertEqual("TRUSTED", future.signature_status)
        self.assertEqual("署名の信頼検証済み", dict(inspection_technical_rows(future))["署名"])


if __name__ == "__main__":
    unittest.main()
