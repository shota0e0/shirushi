"""Limited canary only: no normal Verify/TrustMark import or success mapping."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "src", ROOT / "scripts"):
    sys.path.insert(0, str(directory))

import inspection_metadata as module
from runtime_paths import RuntimeComponentError

FIXTURE = ROOT / "tests/fixtures/inspection/valid_shirushi.png"
TOOL = ROOT / "tools/c2patool-0.26.60/c2patool/c2patool.exe"
SETTINGS = ROOT / "config/verifier-settings.json"
SHA = "558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316"
SIZE = 319495


def good_report() -> dict:
    cabx, _, _, _, _ = module.extract_c2pa_jumbf(FIXTURE)
    active, = [node for node in module.parse_jumbf(cabx) if len(node["path"]) == 2]
    return {"active_manifest": active["label"], "validation_results": {"activeManifest": {
        "success": [{"code": "assertion.dataHash.match"}],
        "failure": [{"code": "signingCredential.untrusted"}],
    }}}


class LimitedTests(unittest.TestCase):
    def inspect(self, report=None, *, path=FIXTURE, digest=SHA, size=SIZE, tool_error=None):
        payload = json.dumps(good_report() if report is None else report).encode()
        outputs = [(0, b"c2patool 0.26.60\n", b""), (0, payload, b"")]
        with patch.object(module, "require_c2patool", return_value=TOOL, side_effect=tool_error), patch.object(module, "_run_tool", side_effect=outputs):
            return module.inspect_limited(path, TOOL, SETTINGS, expected_sha256=digest, expected_size=size)

    def test_expected_limited_inspection_real_claim_and_signature(self):
        result = self.inspect()
        module.validate_result(result, require_expected=True)
        self.assertEqual(result["overall"], "LIMITED_INSPECTION")
        self.assertEqual(result["c2pa"]["signature"], "PREVIEW")
        self.assertEqual(result["cawg"]["aiTrainingUse"], "NOT_WANTED")
        self.assertFalse(result["c2pa"]["trustValidated"])

    def test_c2pa_absent_is_not_permission(self):
        plain = ROOT / "tests/fixtures/inspection/plain_no_shirushi.png"
        result = self.inspect({}, path=plain, digest=hashlib.sha256(plain.read_bytes()).hexdigest(), size=plain.stat().st_size)
        self.assertEqual(result["reasonCode"], "C2PA_ABSENT")
        self.assertEqual(result["cawg"]["aiTrainingUse"], "NO_PERMISSION_INFO")
        self.assertEqual(result["overall"], "INCOMPLETE")

    def test_absent_cawg_branch_with_synthetic_node_inventory(self):
        # Synthetic parser fixture exercises absence without altering signed
        # image bytes or claiming an authentic omission fixture was created.
        original = module.parse_jumbf
        report = good_report()
        def without_rights(data):
            return [node for node in original(data) if node["label"] != "cawg.training-mining"]
        with patch.object(module, "parse_jumbf", side_effect=without_rights), patch.object(module, "verify_hashed_uri", return_value={"match": True, "target": {"path": []}}):
            result = self.inspect(report)
        self.assertEqual(result["reasonCode"], "CAWG_ABSENT")
        self.assertEqual(result["cawg"]["presence"], "ABSENT")
        self.assertEqual(result["overall"], "INCOMPLETE")

    def test_malformed_c2pa(self):
        with patch.object(module, "extract_c2pa_jumbf", side_effect=ValueError("private diagnostic")):
            result = self.inspect({"active_manifest": "synthetic"})
        self.assertEqual(result["reasonCode"], "C2PA_MALFORMED")
        self.assertNotIn("private diagnostic", json.dumps(result))

    def test_missing_tool(self):
        with self.assertRaisesRegex(module.LimitedInspectionError, "C2PATOOL_MISSING"):
            self.inspect(tool_error=RuntimeComponentError("C2PATOOL_MISSING"))

    def test_tool_hash_mismatch(self):
        with self.assertRaisesRegex(module.LimitedInspectionError, "C2PATOOL_INTEGRITY_FAILED"):
            self.inspect(tool_error=RuntimeComponentError("C2PATOOL_INTEGRITY_FAILED"))

    def test_fixture_changed(self):
        with self.assertRaisesRegex(module.LimitedInspectionError, "FIXTURE_CHANGED"):
            self.inspect(digest="0" * 64)

    def test_fixture_size_changed(self):
        with self.assertRaisesRegex(module.LimitedInspectionError, "FIXTURE_CHANGED"):
            self.inspect(size=SIZE + 1)

    def test_post_read_fingerprint_rechecked(self):
        with patch.object(module, "_fingerprint", side_effect=[None, module.LimitedInspectionError("FIXTURE_CHANGED")]):
            with self.assertRaisesRegex(module.LimitedInspectionError, "FIXTURE_CHANGED"):
                self.inspect()

    def test_tool_hash_rechecked_after_execution(self):
        with patch.object(module, "require_c2patool", side_effect=[TOOL, RuntimeComponentError("C2PATOOL_INTEGRITY_FAILED")]), patch.object(module, "_run_tool", side_effect=[(0, b"c2patool 0.26.60", b""), (0, json.dumps(good_report()).encode(), b"")]):
            with self.assertRaisesRegex(module.LimitedInspectionError, "C2PATOOL_INTEGRITY_FAILED"):
                module.inspect_limited(FIXTURE, TOOL, SETTINGS, expected_sha256=SHA, expected_size=SIZE)

    def test_asset_binding_failure(self):
        report = good_report()
        report["validation_results"]["activeManifest"]["failure"].append({"code": "assertion.dataHash.mismatch"})
        self.assertEqual(self.inspect(report)["overall"], "INCOMPLETE")

    def test_missing_asset_binding_evidence(self):
        report = good_report()
        report["validation_results"]["activeManifest"]["success"] = []
        self.assertEqual(self.inspect(report)["reasonCode"], "C2PA_INTEGRITY_FAILED")

    def test_assertion_digest_failure(self):
        with patch.object(module, "verify_hashed_uri", return_value={"match": False}):
            result = self.inspect()
        self.assertEqual(result["overall"], "INCOMPLETE")

    def test_all_outcomes_preserve_not_checked(self):
        for result in (self.inspect(), self.inspect({}), self.inspect({"active_manifest": "missing"})):
            self.assertEqual(result["trustmark"], "NOT_CHECKED")
            self.assertFalse(result["fullVerificationPerformed"])
            self.assertFalse(result["successMotionEligible"])

    def test_full_success_and_motion_mapping_rejected(self):
        valid = self.inspect()
        for key, value in (("overall", "PASS"), ("overall", "COMPLETE"), ("overall", "MATCH"), ("trustmark", "CHECKED"), ("fullVerificationPerformed", True), ("successMotionEligible", True)):
            with self.subTest(key=key, value=value):
                candidate = copy.deepcopy(valid)
                candidate[key] = value
                with self.assertRaises(module.LimitedInspectionError):
                    module.validate_result(candidate)

    def test_paths_and_unknown_fields_rejected(self):
        result = self.inspect()
        result["source"]["path"] = "unapproved"
        with self.assertRaises(module.LimitedInspectionError):
            module.validate_result(result)

    def test_validator_malformed_values_rejected(self):
        for candidate in (None, [], {"overall": "PASS"}):
            with self.assertRaises(module.LimitedInspectionError):
                module.validate_result(candidate)

    def test_report_rejects_duplicate_keys_and_nonfinite_numbers(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]'):
            with self.assertRaises(ValueError):
                module._strict_json(raw)

    def test_no_ml_or_product_service_import(self):
        # Preserve isolation when CI dependencies live in the explicit Candidate
        # B --target directory rather than the build-only interpreter's site.
        site = Path(module.Image.__file__).resolve().parents[1]
        code = "import sys; sys.path[:0]=" + repr([str(site), str(ROOT / "src"), str(ROOT / "scripts")]) + "; import inspection_metadata; assert not any(x.split('.')[0] in {'torch','torchvision','trustmark','torchmetrics','inspection_service','creator_verify'} for x in sys.modules)"
        completed = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, timeout=20, check=False)
        self.assertEqual(completed.returncode, 0, "isolated import must not load model or product service modules")

    def test_bounded_child_output(self):
        with patch.object(module, "MAX_OUTPUT_BYTES", 1024):
            with self.assertRaisesRegex(module.LimitedInspectionError, "C2PATOOL_OUTPUT_LIMIT"):
                module._run_tool([sys.executable, "-I", "-c", "import sys; sys.stdout.write('x'*16384)"])

    def test_child_timeout(self):
        with patch.object(module, "TOOL_TIMEOUT", 0.05):
            with self.assertRaisesRegex(module.LimitedInspectionError, "C2PATOOL_TIMEOUT"):
                module._run_tool([sys.executable, "-I", "-c", "import time; time.sleep(10)"])

    def test_child_normal(self):
        self.assertEqual(module._run_tool([sys.executable, "-I", "-c", "print('bounded')"]), (0, b"bounded\r\n" if os.name == "nt" else b"bounded\n", b""))

    @unittest.skipUnless(os.environ.get("SHIRUSHI_LIMITED_NATIVE_TEST") == "1", "explicit native test opt-in required")
    def test_real_pinned_c2patool(self):
        result = module.inspect_limited(FIXTURE, TOOL, SETTINGS, expected_sha256=SHA, expected_size=SIZE)
        module.validate_result(result, require_expected=True)


if __name__ == "__main__":
    unittest.main()
