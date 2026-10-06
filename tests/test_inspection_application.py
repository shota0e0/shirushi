from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import inspection_application as application_module
from inspection_application import (
    InspectionApplication,
    InspectionApplicationError,
    map_inspection_result,
    validate_inspection_result,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _result(
    snapshot: Path,
    *,
    verifier_result: str = "PASS",
    reason: str | None = None,
    input_format: str | None = "png",
    ai: str = "NOT_WANTED",
    rights: str = "DETECTED",
    cawg: str = "DETECTED",
    c2pa: str = "DETECTED",
    trustmark: str = "DETECTED",
    payload: str = "MATCH",
    integrity: str = "OK",
    signature: str = "PREVIEW",
    recovery: str = "NONE",
    contract_version: str = "1.0",
) -> SimpleNamespace:
    return SimpleNamespace(
        contract_version=contract_version,
        source_path=str(snapshot),
        input_format=input_format,
        ai_training_use=ai,
        ai_inference_use=ai,
        rights_status=rights,
        cawg_status=cawg,
        c2pa_status=c2pa,
        trustmark_status=trustmark,
        trustmark_payload_status=payload,
        integrity_status=integrity,
        signature_status=signature,
        durable_recovery=recovery,
        warnings=("safe",),
        technical_details={
            "verifierContractVersion": "1.0",
            "verifierResult": verifier_result,
            "reasonCode": reason,
            "input": {"sha256": _sha(snapshot), "path": str(snapshot)},
            "ignoredSecret": str(snapshot),
        },
    )


def _no_intent(snapshot: Path) -> SimpleNamespace:
    return _result(
        snapshot,
        verifier_result="FAIL_C2PA",
        reason="C2PA_CLAIM_MISSING",
        ai="NO_PERMISSION_INFO",
        rights="NOT_DETECTED",
        cawg="UNKNOWN",
        c2pa="NOT_DETECTED",
        trustmark="NOT_DETECTED",
        payload="NOT_AVAILABLE",
        integrity="UNKNOWN",
        signature="INDETERMINATE",
    )


def _identifier_only(snapshot: Path) -> SimpleNamespace:
    return _result(
        snapshot,
        verifier_result="FAIL_C2PA",
        reason="C2PA_CLAIM_MISSING",
        ai="NO_PERMISSION_INFO",
        rights="NOT_DETECTED",
        cawg="UNKNOWN",
        c2pa="NOT_DETECTED",
        trustmark="DETECTED",
        payload="VALID_UNBOUND",
        integrity="UNKNOWN",
        signature="INDETERMINATE",
        recovery="IDENTIFIER_RECOVERED",
    )


def _cannot_verify(snapshot: Path) -> SimpleNamespace:
    return _result(
        snapshot,
        verifier_result="FAIL_C2PA",
        reason="C2PA_ASSET_DATA_HASH_MISMATCH",
        c2pa="VERIFICATION_FAILED",
        payload="VALID_UNBOUND",
        integrity="VERIFICATION_FAILED",
    )


def _malformed(snapshot: Path) -> SimpleNamespace:
    return _result(
        snapshot,
        verifier_result="FAIL_WRITE",
        reason="PNG_INVALID",
        input_format=None,
        ai="NO_PERMISSION_INFO",
        rights="NOT_DETECTED",
        cawg="INDETERMINATE",
        c2pa="UNKNOWN",
        trustmark="NOT_DETECTED",
        payload="NOT_AVAILABLE",
        integrity="UNKNOWN",
        signature="INDETERMINATE",
    )


def _operational_write_failure(snapshot: Path) -> SimpleNamespace:
    value = _malformed(snapshot)
    value.technical_details["reasonCode"] = "INTERNAL_ERROR"
    return value


class _Service:
    def __init__(self, callback):
        self.callback = callback

    def inspect(self, path: Path):
        return self.callback(path)


class InspectionApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / "private-name.png"
        self.source.write_bytes(b"synthetic inspection source")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _inspect(self, callback):
        return InspectionApplication(lambda: _Service(callback)).inspect(self.source)

    def test_complete_result_is_exact_path_free_and_snapshot_is_cleaned(self) -> None:
        observed: list[Path] = []

        def inspect(snapshot: Path):
            observed.append(snapshot)
            self.assertNotEqual(self.source, snapshot)
            self.assertEqual(".png", snapshot.suffix)
            return _result(snapshot)

        value = self._inspect(inspect)
        self.assertEqual(
            {"contract", "contractVersion", "source", "verification", "personalMark", "successMotionEligible"},
            set(value),
        )
        self.assertEqual({"sha256", "size", "modifiedNs", "format"}, set(value["source"]))
        self.assertEqual("COMPLETE", value["verification"]["state"])
        self.assertTrue(value["successMotionEligible"])
        self.assertEqual({"state": "NOT_CHECKED", "reason": "READBACK_NOT_IMPLEMENTED"}, value["personalMark"])
        self.assertNotIn(str(self.source), repr(value))
        self.assertFalse(observed[0].parent.exists())

    def test_all_conservative_summary_states_are_preserved(self) -> None:
        cases = (
            (_no_intent, "NO_INTENT"),
            (_identifier_only, "IDENTIFIER_ONLY"),
            (_cannot_verify, "CANNOT_VERIFY"),
            (_malformed, "MALFORMED_OR_UNSUPPORTED"),
            (_operational_write_failure, "CANNOT_VERIFY"),
        )
        for factory, expected in cases:
            with self.subTest(expected):
                value = self._inspect(factory)
                self.assertEqual(expected, value["verification"]["state"])
                self.assertFalse(value["successMotionEligible"])

    def test_original_change_wins_even_when_service_throws_sensitive_error(self) -> None:
        def inspect(snapshot: Path):
            self.source.write_bytes(b"changed while inspecting")
            raise RuntimeError(f"do not expose {snapshot} or {self.source}")

        with self.assertRaises(InspectionApplicationError) as raised:
            self._inspect(inspect)
        self.assertEqual("INSPECTION_SOURCE_CHANGED", raised.exception.code)
        self.assertEqual("INSPECTION_SOURCE_CHANGED", str(raised.exception))
        self.assertNotIn(str(self.source), str(raised.exception))

    def test_snapshot_change_is_rejected(self) -> None:
        def inspect(snapshot: Path):
            value = _result(snapshot)
            snapshot.write_bytes(b"service changed its input")
            return value

        with self.assertRaises(InspectionApplicationError) as raised:
            self._inspect(inspect)
        self.assertEqual("INSPECTION_SNAPSHOT_CHANGED", raised.exception.code)

    def test_missing_nonregular_and_linked_sources_are_rejected(self) -> None:
        missing = self.root / "missing.png"
        with self.assertRaises(InspectionApplicationError) as missing_error:
            InspectionApplication(lambda: _Service(_result)).inspect(missing)
        self.assertEqual("INSPECTION_SOURCE_NOT_FOUND", missing_error.exception.code)

        with self.assertRaises(InspectionApplicationError) as directory_error:
            InspectionApplication(lambda: _Service(_result)).inspect(self.root)
        self.assertEqual("INSPECTION_SOURCE_NOT_REGULAR", directory_error.exception.code)

        link = self.root / "linked.png"
        try:
            link.symlink_to(self.source)
        except (NotImplementedError, OSError):
            self.skipTest("symbolic links are unavailable on this host")
        with self.assertRaises(InspectionApplicationError) as link_error:
            InspectionApplication(lambda: _Service(_result)).inspect(link)
        self.assertEqual("INSPECTION_SOURCE_LINKED", link_error.exception.code)

    def test_malformed_service_result_and_raw_service_error_fail_closed(self) -> None:
        with self.assertRaises(InspectionApplicationError) as malformed:
            self._inspect(lambda snapshot: SimpleNamespace(input_format="png"))
        self.assertEqual("INSPECTION_RESULT_INVALID", malformed.exception.code)

        sensitive = str(self.source)
        with self.assertRaises(InspectionApplicationError) as failed:
            self._inspect(lambda snapshot: (_ for _ in ()).throw(RuntimeError(sensitive)))
        self.assertEqual("INSPECTION_SERVICE_FAILED", failed.exception.code)
        self.assertNotIn(sensitive, str(failed.exception))

    def test_default_factory_refuses_unprepared_runtime_without_importing_core(self) -> None:
        with self.assertRaises(InspectionApplicationError) as raised:
            InspectionApplication().inspect(self.source)
        self.assertEqual("INSPECTION_RUNTIME_NOT_PREPARED", raised.exception.code)

    def test_validator_rejects_unknown_versions_types_keys_and_enum_collisions(self) -> None:
        valid = self._inspect(_result)
        mutations = []

        unknown_outer = deepcopy(valid)
        unknown_outer["contractVersion"] = 2
        mutations.append(unknown_outer)

        wrong_type = deepcopy(valid)
        wrong_type["source"]["size"] = True
        mutations.append(wrong_type)

        extra_key = deepcopy(valid)
        extra_key["path"] = str(self.source)
        mutations.append(extra_key)

        enum_collision = deepcopy(valid)
        enum_collision["verification"]["aiTrainingUse"] = "DETECTED"
        mutations.append(enum_collision)

        unknown_reason = deepcopy(valid)
        unknown_reason["verification"]["state"] = "CANNOT_VERIFY"
        unknown_reason["verification"]["integrity"] = "VERIFICATION_FAILED"
        unknown_reason["verification"]["trustmarkPayload"] = "VALID_UNBOUND"
        unknown_reason["verification"]["reasonCode"] = "PATH_C_USERS_PRIVATE"
        unknown_reason["successMotionEligible"] = False
        mutations.append(unknown_reason)

        forged_no_intent = deepcopy(self._inspect(_no_intent))
        forged_no_intent["verification"]["state"] = "CANNOT_VERIFY"
        mutations.append(forged_no_intent)

        forged_malformed = deepcopy(self._inspect(_malformed))
        forged_malformed["verification"]["state"] = "CANNOT_VERIFY"
        mutations.append(forged_malformed)

        forged_cannot = deepcopy(self._inspect(_cannot_verify))
        forged_cannot["verification"]["reasonCode"] = "PNG_INVALID"
        mutations.append(forged_cannot)

        malformed_with_operational_reason = deepcopy(self._inspect(_malformed))
        malformed_with_operational_reason["verification"]["reasonCode"] = "INTERNAL_ERROR"
        mutations.append(malformed_with_operational_reason)

        unknown_normal_format = deepcopy(valid)
        unknown_normal_format["source"]["format"] = "unknown"
        mutations.append(unknown_normal_format)

        unhashable_reason = deepcopy(valid)
        unhashable_reason["verification"]["reasonCode"] = ["PNG_INVALID"]
        mutations.append(unhashable_reason)

        missing_recovery = deepcopy(self._inspect(_identifier_only))
        missing_recovery["verification"]["durableRecovery"] = "NONE"
        mutations.append(missing_recovery)

        for value in mutations:
            with self.subTest(value=value):
                with self.assertRaises(InspectionApplicationError) as raised:
                    validate_inspection_result(value)
                self.assertEqual("INSPECTION_RESULT_INVALID", raised.exception.code)

        result = _result(self.source, contract_version="2.0")
        with self.assertRaises(InspectionApplicationError):
            map_inspection_result(result, valid["source"])

        sensitive_path = str(self.source)

        class ExplodingPath:
            def __fspath__(self):
                raise RuntimeError(sensitive_path)

        with self.assertRaises(InspectionApplicationError) as path_error:
            InspectionApplication(lambda: _Service(_result)).inspect(ExplodingPath())
        self.assertEqual("INSPECTION_SOURCE_UNAVAILABLE", path_error.exception.code)

    def test_source_limit_is_checked_before_service_execution(self) -> None:
        calls = []
        with mock.patch.object(application_module, "MAX_SOURCE_BYTES", 8):
            with self.assertRaises(InspectionApplicationError) as raised:
                self._inspect(lambda snapshot: calls.append(snapshot))
        self.assertEqual("INSPECTION_SOURCE_TOO_LARGE", raised.exception.code)
        self.assertEqual([], calls)

    def test_contradictory_complete_and_future_personal_mark_placeholder_are_rejected(self) -> None:
        valid = self._inspect(_result)
        contradictory = deepcopy(valid)
        contradictory["verification"]["c2pa"] = "NOT_DETECTED"
        with self.assertRaises(InspectionApplicationError):
            validate_inspection_result(contradictory)

        future_placeholder = deepcopy(valid)
        future_placeholder["personalMark"] = {"state": "ABSENT", "reason": "NOT_FOUND"}
        with self.assertRaises(InspectionApplicationError):
            validate_inspection_result(future_placeholder)

    def test_profile_like_service_data_is_never_substituted_or_exported(self) -> None:
        def inspect(snapshot: Path):
            result = _no_intent(snapshot)
            result.current_profile = {
                "aiTrainingUse": "NOT_WANTED",
                "personalMark": "must-not-cross",
            }
            return result

        value = self._inspect(inspect)
        self.assertEqual("NO_PERMISSION_INFO", value["verification"]["aiTrainingUse"])
        self.assertNotIn("must-not-cross", repr(value))
        self.assertEqual("NOT_CHECKED", value["personalMark"]["state"])

    def test_cleanup_failure_suppresses_an_otherwise_valid_result(self) -> None:
        def fail_after_removal(path: Path, expected_identity) -> None:
            shutil.rmtree(path)
            raise InspectionApplicationError("INSPECTION_CLEANUP_FAILED")

        with mock.patch.object(application_module, "_cleanup_directory", fail_after_removal):
            with self.assertRaises(InspectionApplicationError) as raised:
                self._inspect(_result)
        self.assertEqual("INSPECTION_CLEANUP_FAILED", raised.exception.code)

    def test_unverified_temporary_path_is_never_recursively_deleted(self) -> None:
        candidate = self.root / "unverified-temp"
        candidate.mkdir()
        real_lstat = os.lstat

        def fail_only_for_candidate(path):
            if Path(path) == candidate:
                raise OSError("synthetic metadata failure")
            return real_lstat(path)

        with (
            mock.patch.object(application_module.tempfile, "mkdtemp", return_value=str(candidate)),
            mock.patch.object(application_module.os, "lstat", side_effect=fail_only_for_candidate),
        ):
            with self.assertRaises(InspectionApplicationError) as raised:
                self._inspect(_result)
        self.assertEqual("INSPECTION_CLEANUP_FAILED", raised.exception.code)
        self.assertTrue(candidate.is_dir())
        shutil.rmtree(candidate)

    def test_existing_inspection_service_normalization_maps_without_real_processing(self) -> None:
        try:
            from inspection_service import InspectionService
        except (ImportError, OSError) as exc:
            self.skipTest(f"optional inspection dependencies unavailable: {type(exc).__name__}")

        def verifier(path: Path, c2patool: Path, settings: Path) -> dict:
            return {
                "contractVersion": "1.0",
                "result": "PASS",
                "reasonCode": None,
                "message": "not exported",
                "input": {
                    "path": str(path),
                    "sha256": _sha(path).upper(),
                    "format": "png",
                    "dimensions": {"width": 1, "height": 1},
                },
                "rights": {"preset": "AI利用拒否", "verified": True},
                "trustmark": {"present": True, "schema": 2, "payloadLength": 68, "payloadMatch": True},
                "softBinding": {"present": True, "algorithm": "com.adobe.trustmark.P", "match": True},
                "c2pa": {"claimPresent": True, "assertionDigestsMatch": True},
                "signature": {"present": True, "valid": True, "trustValidated": False},
                "diagnostics": [{"code": "VERIFICATION_PASS", "status": "pass"}],
            }

        def service_factory():
            return InspectionService(
                c2patool=Path("unused-c2patool"),
                settings_path=Path("unused-settings"),
                verifier=verifier,
                trustmark_probe=lambda path: (_ for _ in ()).throw(AssertionError("must not probe")),
            )

        value = InspectionApplication(service_factory).inspect(self.source)
        self.assertEqual("COMPLETE", value["verification"]["state"])
        self.assertTrue(value["successMotionEligible"])


if __name__ == "__main__":
    unittest.main()
