"""Synthetic negatives supplement the separate real frozen-file validation.

No candidate binary is a fixture or executed. Trusted constants are never read
from the candidate record as approval. Temporary synthetic bytes are test-owned.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import verify_vc_runtime_candidate as verifier


RECORD = Path(__file__).parent.parent / "docs" / "development" / verifier.RECORD_FILENAME


def raw_json(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class FreezeRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = RECORD.read_bytes()  # Small, committed text fixture only.
        cls.value = verifier.parse_record(cls.raw)

    def test_exact_frozen_record_identity(self):
        self.assertEqual(len(self.raw), verifier.EXPECTED_RECORD_SIZE)
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), verifier.EXPECTED_RECORD_SHA256)
        self.assertEqual(self.value["executionCount"], 0)
        self.assertEqual(self.value["state"], "CANDIDATE")
        self.assertIsNone(self.value["minimumRuntimeVersion"])

    def test_signature_structure_is_one_trusted_primary_timestamp_only(self):
        self.assertEqual(verifier.signature_structure(self.value), "SINGLE_KNOWN_MICROSOFT_PRIMARY_RFC3161")
        signature = self.value["signatures"]["items"][0]
        self.assertEqual(signature["nestedSignatureCount"], 0)
        self.assertEqual(signature["winVerifyTrust"]["hresult"], "0x00000000")
        self.assertEqual(signature["signTool"]["allSignaturesExitCode"], 0)

    def test_unknown_nested_or_additional_signature_requires_review(self):
        for field in ("count", "secondaryCount", "certificateTableEntries"):
            value = copy.deepcopy(self.value)
            value["signatures"][field] += 1
            with self.subTest(field=field), self.assertRaisesRegex(verifier.CandidateError, "VC_REDIST_SIGNATURE_STRUCTURE_REVIEW_REQUIRED"):
                verifier.parse_record(raw_json(value))
        for field in ("nestedSignatureCount", "legacyCountersignerCount"):
            value = copy.deepcopy(self.value)
            value["signatures"]["items"][0][field] = 1
            with self.subTest(field=field), self.assertRaisesRegex(verifier.CandidateError, "VC_REDIST_SIGNATURE_STRUCTURE_REVIEW_REQUIRED"):
                verifier.parse_record(raw_json(value))

    def test_signer_or_timestamp_certificate_substitution_requires_review(self):
        for nested in (False, True):
            value = copy.deepcopy(self.value)
            item = value["signatures"]["items"][0]
            (item["timestamp"] if nested else item)["signer"]["derSha256"] = "0" * 64
            with self.assertRaisesRegex(verifier.CandidateError, "VC_REDIST_SIGNATURE_STRUCTURE_REVIEW_REQUIRED"):
                verifier.parse_record(raw_json(value))

    def test_missing_signature_and_timestamp_evidence_rejected(self):
        for remove in (lambda value: value.pop("signatures"),
                       lambda value: value["signatures"]["items"][0].pop("timestamp"),
                       lambda value: value["signatures"]["items"][0].pop("chain"),
                       lambda value: value["signatures"]["items"][0]["signTool"].pop("timestampVerified")):
            value = copy.deepcopy(self.value)
            remove(value)
            with self.assertRaisesRegex(verifier.CandidateError, "FREEZE_RECORD_SCHEMA_INVALID"):
                verifier.parse_record(raw_json(value))

    def test_unknown_top_and_nested_fields_rejected(self):
        for target in (lambda value: value, lambda value: value["wrapper"],
                       lambda value: value["source"]["redirects"][0],
                       lambda value: value["signatures"]["items"][0]["timestamp"]["signer"]):
            value = copy.deepcopy(self.value)
            target(value)["extra"] = True
            with self.assertRaisesRegex(verifier.CandidateError, "FREEZE_RECORD_SCHEMA_INVALID"):
                verifier.parse_record(raw_json(value))

    def test_type_confusion_bool_as_int_and_invented_approval(self):
        for field, replacement in (("schemaVersion", True), ("size", True), ("executionCount", False),
                                   ("size", float(verifier.EXPECTED_SIZE)), ("state", "OWNER_APPROVED"),
                                   ("executionCount", 1), ("minimumRuntimeVersion", "14.51.36247.0")):
            value = copy.deepcopy(self.value)
            value[field] = replacement
            with self.subTest(field=field), self.assertRaises(verifier.CandidateError):
                verifier.parse_record(raw_json(value))

    def test_duplicate_nonfinite_bom_trailing_size_and_malformed_json(self):
        for raw in (self.raw.replace(b'"schemaVersion": 1', b'"schemaVersion": 1, "schemaVersion": 1'),
                    self.raw.replace(b'"size": 18731856', b'"size": NaN'),
                    b"\xef\xbb\xbf" + self.raw, self.raw + b"{}", b"{", b"[]", b"", b"x" * (verifier.MAX_RECORD + 1)):
            with self.subTest(raw=raw[:25]), self.assertRaises(verifier.CandidateError):
                verifier.parse_record(raw)

    def test_byte_equivalent_record_reserialization_does_not_authorize_itself(self):
        with self.assertRaisesRegex(verifier.CandidateError, "FREEZE_RECORD_IDENTITY_MISMATCH"):
            verifier.parse_record(raw_json(self.value))

    def test_coordinated_record_candidate_identity_tamper_rejected(self):
        value = copy.deepcopy(self.value)
        value.update(size=100, sha256=hashlib.sha256(b"substituted").hexdigest())
        with self.assertRaisesRegex(verifier.CandidateError, "FREEZE_RECORD_IDENTITY_MISMATCH"):
            verifier.parse_record(raw_json(value))

    def test_coordinated_signature_success_claims_cannot_override_record_digest(self):
        value = copy.deepcopy(self.value)
        value["signatures"]["items"][0]["signTool"]["allSignaturesExitCode"] = 1
        with self.assertRaisesRegex(verifier.CandidateError, "FREEZE_RECORD_IDENTITY_MISMATCH"):
            verifier.parse_record(raw_json(value))

    def test_compatibility_and_minimum_remain_unproven(self):
        self.assertEqual(self.value["compatibilityClassification"], "VC_CANDIDATE_COMPATIBILITY_UNPROVEN")
        self.assertEqual(self.value["minimumPolicyClassification"], "VC_MINIMUM_POLICY_UNPROVEN")
        self.assertEqual(self.value["legalClassification"], "LEGAL_REVIEW_REQUIRED")

    def test_authenticode_image_digest_is_not_raw_file_digest(self):
        image = self.value["signatures"]["items"][0]["signTool"]["authenticodeImageDigestSha256"]
        self.assertNotEqual(image, verifier.EXPECTED_SHA256)

    def test_cli_has_no_download_source_expected_hash_or_policy_override(self):
        for option in ("--download", "--expected-sha256", "--source", "--policy"):
            with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                verifier.main(["--record", "ignored", "--candidate", "ignored", option, "value"])


@unittest.skipUnless(os.name == "nt", "Windows fixed-local-drive file policy")
class CandidateFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="shirushi-vc-validator-unit-")
        self.root = Path(self.temporary.name)
        self.owner = verifier._stamp(self.root.lstat())[:2]
        self.record = self.root / verifier.RECORD_FILENAME
        self.record.write_bytes(RECORD.read_bytes())
        self.candidate = self.root / verifier.EXPECTED_FILENAME
        self.candidate.write_bytes(b"synthetic bytes only")
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()))
        self.assertTrue(self.root.name.startswith("shirushi-vc-validator-unit-"))
        self.assertEqual(verifier._stamp(self.root.lstat())[:2], self.owner)
        self.assertFalse(getattr(self.root.lstat(), "st_file_attributes", 0) & verifier.REPARSE)
        self.temporary.cleanup()

    def reject(self, code, candidate=None):
        with self.assertRaisesRegex(verifier.CandidateError, code):
            verifier.verify_candidate(self.record, candidate if candidate is not None else self.candidate)

    def test_synthetic_measurement_match_supplements_separate_real_candidate_pass(self):
        original = verifier._measure
        def measurement(path, size, **kwargs):
            if path == self.candidate:
                self.assertEqual(size, verifier.EXPECTED_SIZE)
                return verifier.EXPECTED_SHA256, None
            return original(path, size, **kwargs)
        with mock.patch.object(verifier, "_measure", side_effect=measurement):
            result = verifier.verify_candidate(self.record, self.candidate)
        self.assertEqual(result["classification"], "VC_RUNTIME_CANDIDATE_IDENTITY_MATCH")
        self.assertEqual(result["state"], "CANDIDATE")
        self.assertEqual(result["executionCount"], 0)
        self.assertEqual(result["signatureVerification"], "FROZEN_ACQUISITION_EVIDENCE_ONLY")

    def test_wrong_basename_and_missing_file(self):
        self.reject("BASENAME_INVALID", self.root / "other.exe")
        self.candidate.unlink()
        self.reject("PATH_UNAVAILABLE")

    def test_wrong_size_and_truncated_file(self):
        self.reject("SIZE_MISMATCH")
        self.candidate.write_bytes(b"")
        self.reject("SIZE_MISMATCH")

    def test_wrong_hash_and_same_size_modified_synthetic_payload(self):
        raw = self.candidate.read_bytes()
        with mock.patch.object(verifier, "EXPECTED_SIZE", len(raw)), mock.patch.object(verifier, "parse_record", return_value=json.loads(self.record.read_bytes())):
            self.reject("CANDIDATE_HASH_MISMATCH")
            self.candidate.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
            self.reject("CANDIDATE_HASH_MISMATCH")

    def test_changed_record_size_or_same_size_identity_rejected(self):
        self.record.write_bytes(b"{}")
        self.reject("SIZE_MISMATCH")
        raw = RECORD.read_bytes().replace(b'"httpStatus": 200', b'"httpStatus": 201')
        self.record.write_bytes(raw)
        self.reject("FREEZE_RECORD_IDENTITY_MISMATCH")

    def test_candidate_directory_rejected(self):
        self.candidate.unlink()
        self.candidate.mkdir()
        self.reject("FILE_TYPE_INVALID")

    def test_path_policy_rejects_before_file_read(self):
        for raw in ("relative.exe", r"\\server\share\vc_redist.x64.exe", r"C:\a\..\vc_redist.x64.exe",
                    r"C:\a\vc_redist.x64.exe:stream", r"C:\CON\vc_redist.x64.exe", r"C:\a\VC_redist.x64.exe"):
            with self.subTest(raw=raw), self.assertRaises(verifier.CandidateError):
                verifier._path(raw, verifier.EXPECTED_FILENAME)
        with mock.patch.object(verifier, "_drive_type", return_value=4):
            with self.assertRaisesRegex(verifier.CandidateError, "LOCAL_FIXED_DRIVE_REQUIRED"):
                verifier._path(self.candidate, verifier.EXPECTED_FILENAME)

    def test_actual_hardlink_rejected(self):
        os.link(self.candidate, self.root / "alias")
        self.reject("HARDLINK_REJECTED")

    def test_actual_symlink_rejected_where_creation_supported(self):
        target = self.root / "target"
        self.candidate.rename(target)
        try:
            self.candidate.symlink_to(target)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("Symlink privilege unavailable; synthetic reparse test covers guard")
            raise
        self.reject("REPARSE_REJECTED")

    def test_synthetic_reparse_leaf_and_ancestor_rejected(self):
        original = Path.lstat
        for changed in (self.candidate, self.root):
            def metadata(path, **kwargs):
                actual = original(path, **kwargs)
                if path != changed:
                    return actual
                return SimpleNamespace(st_mode=actual.st_mode, st_file_attributes=verifier.REPARSE)
            with self.subTest(changed=changed.name), mock.patch.object(Path, "lstat", metadata):
                self.reject("REPARSE_REJECTED")

    def test_zero_file_identity_and_unknown_link_count_fail_closed(self):
        for inode, links in ((0, 1), (None, 1), (True, 1), (1, None), (1, 0), (1, True)):
            with self.assertRaisesRegex(verifier.CandidateError, "FILE_IDENTITY_UNAVAILABLE"):
                verifier._stamp(SimpleNamespace(st_dev=0, st_ino=inode, st_nlink=links))

    def test_small_real_read_is_bounded_and_checks_raw_bytes(self):
        raw = self.candidate.read_bytes()
        with mock.patch.object(verifier, "CHUNK", 3):
            sha, captured = verifier._measure(self.candidate, len(raw))
        self.assertEqual(sha, hashlib.sha256(raw).hexdigest())
        self.assertIsNone(captured)

    def test_read_handle_identity_substitution_rejected(self):
        actual = self.candidate.lstat()
        fake = SimpleNamespace(st_dev=actual.st_dev, st_ino=actual.st_ino + 1,
                               st_size=actual.st_size, st_mtime_ns=actual.st_mtime_ns, st_nlink=1)
        with mock.patch.object(verifier.os, "fstat", return_value=fake):
            with self.assertRaisesRegex(verifier.CandidateError, "SOURCE_CHANGED"):
                verifier._measure(self.candidate, actual.st_size)

    def test_unrelated_ancestor_metadata_change_does_not_replace_identity(self):
        original = Path.lstat
        changes = 0
        def metadata(path, **kwargs):
            nonlocal changes
            actual = original(path, **kwargs)
            if path != self.root:
                return actual
            changes += 1
            return SimpleNamespace(st_mode=actual.st_mode, st_file_attributes=0,
                                   st_dev=actual.st_dev, st_ino=actual.st_ino,
                                   st_size=actual.st_size + changes, st_mtime_ns=actual.st_mtime_ns + changes, st_nlink=actual.st_nlink)
        raw = self.candidate.read_bytes()
        with mock.patch.object(Path, "lstat", metadata):
            sha, _ = verifier._measure(self.candidate, len(raw))
        self.assertEqual(sha, hashlib.sha256(raw).hexdigest())

    def test_ancestor_identity_replacement_still_rejected(self):
        original = Path.lstat
        calls = 0
        def metadata(path, **kwargs):
            nonlocal calls
            actual = original(path, **kwargs)
            if path != self.root:
                return actual
            calls += 1
            return SimpleNamespace(st_mode=actual.st_mode, st_file_attributes=0,
                                   st_dev=actual.st_dev, st_ino=actual.st_ino + (calls > 1),
                                   st_size=actual.st_size, st_mtime_ns=actual.st_mtime_ns, st_nlink=actual.st_nlink)
        with mock.patch.object(Path, "lstat", metadata):
            with self.assertRaisesRegex(verifier.CandidateError, "SOURCE_CHANGED"):
                verifier._measure(self.candidate, self.candidate.stat().st_size)

    def test_deterministic_midread_mutation_rejected(self):
        raw = self.candidate.read_bytes()
        digest = hashlib.sha256()
        candidate = self.candidate
        class ChangingDigest:
            def update(self, block):
                digest.update(block)
                candidate.write_bytes(raw + b"changed")
            def hexdigest(self):
                return digest.hexdigest()
        with mock.patch.object(verifier.hashlib, "sha256", return_value=ChangingDigest()):
            with self.assertRaisesRegex(verifier.CandidateError, "SOURCE_CHANGED"):
                verifier._measure(self.candidate, len(raw))

    def test_cli_failure_is_path_free_and_execution_count_zero(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            exit_code = verifier.main(["--record", str(self.record), "--candidate", str(self.candidate)])
        self.assertEqual(exit_code, 2)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["executionCount"], 0)
        self.assertNotIn(str(self.root), stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
