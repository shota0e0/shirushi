"""Synthetic policy/registry/file evidence; no installer is ever executed."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import prerequisite_readiness_v02 as readiness


TEST_POLICY = readiness.Policy("14.40.1.0")  # Synthetic, not product approval.


def vc(value="v14.40.1.0", *, source=readiness.VC_SOURCE, installed=1, architecture="X64_REGISTERED"):
    return readiness.Registration(source, "OK", {"Version": (value, "SZ"), "Installed": (installed, "DWORD")}, architecture)


def wv(value="140.0.1.2", *, source=readiness.WV_SOURCES[0], architecture="X64_VERIFIED"):
    # Explicit synthetic strong architecture observation, never live registry proof.
    return readiness.Registration(source, "OK", {"pv": (value, "SZ")}, architecture)


def rows(vc_row=None, wv_row=None):
    return [vc_row or vc(), readiness.Registration(readiness.VC_SOURCES[1], "MISSING", {}),
            wv_row or wv(), readiness.Registration(readiness.WV_SOURCES[1], "MISSING", {})]


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class ReadinessTests(unittest.TestCase):
    def assert_state(self, values, prerequisite, state, overall=None, *, policy=TEST_POLICY):
        result = readiness.evaluate(values, policy=policy)
        self.assertEqual(result["runtimes"][prerequisite]["status"], state)
        if overall is not None:
            self.assertEqual(result["overall"], overall)
        self.assertEqual(readiness.parse_result(readiness.encode_result(result, policy=policy), policy=policy), result)
        return result

    def test_both_synthetic_ready(self):
        self.assert_state(rows(), "vc", "READY", "READY")

    def test_vc_equal_and_newer_than_synthetic_policy(self):
        for value in ("v14.40.1.0", "14.50.0.0"):
            with self.subTest(value=value):
                self.assert_state(rows(vc(value)), "vc", "READY")

    def test_vc_outdated(self):
        self.assert_state(rows(vc("14.39.999.0")), "vc", "OUTDATED", "PREREQUISITES_REQUIRED")

    def test_vc_missing_and_installed_false(self):
        for observed in (readiness.Registration(readiness.VC_SOURCE, "MISSING", {}), vc(installed=0)):
            with self.subTest(observed=observed):
                self.assert_state(rows(observed), "vc", "MISSING", "PREREQUISITES_REQUIRED")

    def test_default_policy_is_unset_even_with_valid_registration(self):
        self.assertIsNone(readiness.BUILD_POLICY.vc_minimum)
        result = self.assert_state(rows(), "vc", "POLICY_UNSET", "READINESS_UNKNOWN", policy=readiness.BUILD_POLICY)
        self.assertIsNone(result["policy"]["vcMinimumVersion"])

    def test_vc_malformed_versions(self):
        for value in (None, "", "0.0.0.0", "v14.1", "14.0.0.0 extra", "14..0.0", "65536.0.0.0", True):
            with self.subTest(value=value):
                self.assert_state(rows(vc(value)), "vc", "DETECTION_FAILED", "READINESS_UNKNOWN")

    def test_vc_registration_type_and_unknown_field_rejected(self):
        for fields in ({"Version": ("14.40.1.0", "DWORD")},
                       {"Version": ("14.40.1.0", "SZ"), "Installed": (True, "DWORD")},
                       {"Version": ("14.40.1.0", "SZ"), "Installed": (2, "DWORD")},
                       {"Version": ("14.40.1.0", "SZ"), "secret": ("private", "SZ")}):
            with self.subTest(fields=fields):
                self.assert_state(rows(readiness.Registration(readiness.VC_SOURCE, "OK", fields, "X64_REGISTERED")), "vc", "DETECTION_FAILED")

    def test_vc_version_dwords_consistency(self):
        good = vc()
        good.values.update({key: (value, "DWORD") for key, value in zip(("Major", "Minor", "Bld", "Rbld"), (14, 40, 1, 0))})
        self.assert_state(rows(good), "vc", "READY")
        for changed in ({"Bld": (2, "DWORD")}, {"Rbld": (False, "DWORD")}, {"Rbld": (0, "SZ")}):
            bad = copy.deepcopy(good)
            bad.values.update(changed)
            self.assert_state(rows(bad), "vc", "DETECTION_FAILED")
        del good.values["Rbld"]
        self.assert_state(rows(good), "vc", "DETECTION_FAILED")

    def test_vc_observed_zero_padding_normalizes_without_policy_relaxation(self):
        observed = vc("v14.44.35211.00")
        observed.values.update({key: (value, "DWORD") for key, value in zip(("Major", "Minor", "Bld", "Rbld"), (14, 44, 35211, 0))})
        result = self.assert_state(rows(observed), "vc", "READY")
        self.assertEqual(result["runtimes"]["vc"]["observations"][0]["version"], "14.44.35211.0")
        values = rows(observed)
        values[1] = vc("14.44.35211.0", source=readiness.VC_SOURCES[1])
        self.assert_state(values, "vc", "READY")
        values[1] = vc("14.44.35211.1", source=readiness.VC_SOURCES[1])
        self.assert_state(values, "vc", "DETECTION_FAILED")
        with self.assertRaises(readiness.ReadinessError):
            readiness.Policy("14.44.35211.00")

    def test_vc_installed_flag_absent_is_unproven(self):
        observed = vc()
        del observed.values["Installed"]
        self.assert_state(rows(observed), "vc", "DETECTION_FAILED")

    def test_vc_query_failure(self):
        self.assert_state(rows(readiness.Registration(readiness.VC_SOURCE, "FAILED", {})), "vc", "DETECTION_FAILED", "READINESS_UNKNOWN")

    def test_vc_wrong_or_unknown_architecture(self):
        for architecture in ("UNPROVEN", "WRONG_ARCHITECTURE"):
            self.assert_state(rows(vc(architecture=architecture)), "vc", "DETECTION_FAILED")

    def test_vc_second_view_valid_first_missing(self):
        values = rows(readiness.Registration(readiness.VC_SOURCE, "MISSING", {}))
        values[1] = vc(source=readiness.VC_SOURCES[1])
        self.assert_state(values, "vc", "READY")

    def test_vc_two_views_preserve_version_or_installed_conflict(self):
        for second in (vc("14.50.0.0", source=readiness.VC_SOURCES[1]), vc(source=readiness.VC_SOURCES[1], installed=0)):
            values = rows()
            values[1] = second
            self.assert_state(values, "vc", "DETECTION_FAILED", "READINESS_UNKNOWN")

    def test_webview_synthetic_valid_strong_x64_evidence(self):
        self.assert_state(rows(), "webview2", "READY", "READY")

    def test_webview_live_pv_only_never_ready(self):
        self.assert_state(rows(wv_row=wv(architecture="UNPROVEN")), "webview2", "DETECTION_FAILED", "READINESS_UNKNOWN")

    def test_webview_missing_semantics(self):
        for value in (None, "", "0.0.0.0"):
            self.assert_state(rows(wv_row=wv(value)), "webview2", "MISSING", "PREREQUISITES_REQUIRED")
        self.assert_state(rows(wv_row=readiness.Registration(readiness.WV_SOURCES[0], "OK", {})), "webview2", "MISSING")

    def test_webview_malformed_versions_or_type(self):
        for value in ("v140.0.1.2", "140.1", "140.0.1.2 beta", True, "140.00.1.2", "99999.0.0.0"):
            self.assert_state(rows(wv_row=wv(value)), "webview2", "DETECTION_FAILED")
        observed = readiness.Registration(readiness.WV_SOURCES[0], "OK", {"pv": ("140.0.1.2", "DWORD")}, "X64_VERIFIED")
        self.assert_state(rows(wv_row=observed), "webview2", "DETECTION_FAILED")

    def test_webview_query_failure_and_wrong_architecture(self):
        for observed in (readiness.Registration(readiness.WV_SOURCES[0], "FAILED", {}), wv(architecture="WRONG_ARCHITECTURE")):
            self.assert_state(rows(wv_row=observed), "webview2", "DETECTION_FAILED", "READINESS_UNKNOWN")

    def test_webview_multiple_sources_same_version(self):
        values = rows()
        values[3] = wv(source=readiness.WV_SOURCES[1])
        self.assert_state(values, "webview2", "READY")

    def test_webview_multiple_sources_conflict_or_unreadable(self):
        for second in (wv("141.0.1.2", source=readiness.WV_SOURCES[1]), readiness.Registration(readiness.WV_SOURCES[1], "FAILED", {})):
            values = rows()
            values[3] = second
            self.assert_state(values, "webview2", "DETECTION_FAILED", "READINESS_UNKNOWN")

    def test_unknown_has_precedence_over_missing(self):
        self.assert_state(rows(wv_row=wv(None)), "vc", "POLICY_UNSET", "READINESS_UNKNOWN", policy=readiness.BUILD_POLICY)

    def test_strict_observation_set_no_duplicates_or_preview_sources(self):
        for values in (rows()[:3], rows() + [wv()], [rows()[1], *rows()[1:]]):
            with self.assertRaises(readiness.ReadinessError):
                readiness.evaluate(values)
        with self.assertRaises(readiness.ReadinessError):
            readiness.normalize(readiness.Registration("EdgeBrowser:Preview", "OK", {}))

    def test_strict_json_unknown_missing_wrong_types_and_false_ready(self):
        baseline = readiness.evaluate(rows())
        mutations = [lambda r: r.update(extra=1), lambda r: r.pop("purpose"),
                     lambda r: r.update(schemaVersion=True), lambda r: r.update(schemaVersion=1.0),
                     lambda r: r.update(overall="READY"),
                     lambda r: r["runtimes"]["vc"].update(status="READY"),
                     lambda r: r["runtimes"]["vc"]["observations"][0].update(installed=1),
                     lambda r: r["runtimes"]["vc"]["observations"][0].update(query=[]),
                     lambda r: r["runtimes"]["webview2"].update(extra=1),
                     lambda r: r["offlineInputs"]["webview2"].update(approval="OWNER_APPROVED"),
                     lambda r: r["offlineInputs"]["webview2"].update(expectedSize=True),
                     lambda r: r["offlineInputs"]["webview2"].update(expectedSha256="0" * 64),
                     lambda r: r["offlineInputs"]["webview2"].update(currentSignatureVerification="VALID")]
        for mutate in mutations:
            result = copy.deepcopy(baseline)
            mutate(result)
            with self.subTest(mutation=mutate), self.assertRaises(readiness.ReadinessError):
                readiness.parse_result(encoded(result))

    def test_coordinated_policy_and_status_tamper_rejected(self):
        forged = readiness.evaluate(rows(), policy=TEST_POLICY)
        with self.assertRaisesRegex(readiness.ReadinessError, "POLICY_MISMATCH"):
            readiness.parse_result(encoded(forged))

    def test_strict_json_duplicate_nonfinite_trailing_bom_size(self):
        good = readiness.encode_result(readiness.evaluate(rows()))
        for raw in (good.replace(b'"schemaVersion":1', b'"schemaVersion":1,"schemaVersion":1'),
                    good.replace(b'"schemaVersion":1', b'"schemaVersion":NaN'),
                    good + b"{}", b"\xef\xbb\xbf" + good, b"x" * (readiness.MAX_RESULT + 1), b"", b"[]"):
            with self.subTest(raw=raw[:30]), self.assertRaises(readiness.ReadinessError):
                readiness.parse_result(raw)

    def test_cli_has_no_minimum_hash_policy_or_source_override(self):
        for option in ("--vc-minimum", "--expected-sha256", "--policy", "--registry-source"):
            with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                readiness.main(["observe", option, "value"])

    def test_injected_reader_observer_is_read_only_and_default_policy_unset(self):
        observations = dict(zip((*readiness.VC_SOURCES, *readiness.WV_SOURCES), rows()))
        reader = SimpleNamespace(read=mock.Mock(side_effect=lambda source: observations[source]))
        result = readiness.observe(reader)
        self.assertEqual(reader.read.call_count, 4)
        self.assertEqual(result["overall"], "READINESS_UNKNOWN")
        self.assertEqual(result["offlineInputs"]["vc"]["status"], "VC_OFFLINE_INPUT_UNCONFIGURED")

    def test_registry_reader_allowlist_views_and_query_types(self):
        opened, queried = [], []
        class Key:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
        def open_key(hive, key, reserved, access):
            opened.append((hive, key, reserved, access))
            return Key()
        def query(_, name):
            queried.append(name)
            return ("v14.40.1.0", 1) if name == "Version" else (1, 4) if name == "Installed" else ("140.0.1.2", 1) if name == "pv" else (_ for _ in ()).throw(FileNotFoundError())
        fake = SimpleNamespace(HKEY_LOCAL_MACHINE=1, HKEY_CURRENT_USER=2, KEY_READ=0x20019,
                               KEY_WOW64_64KEY=0x100, KEY_WOW64_32KEY=0x200, REG_SZ=1, REG_DWORD=4,
                               OpenKey=open_key, QueryValueEx=query)
        with mock.patch.dict("sys.modules", {"winreg": fake}), mock.patch.object(readiness.os, "name", "nt"), mock.patch.object(readiness.platform, "machine", return_value="AMD64"):
            result = readiness.observe()
            self.assertEqual(result["runtimes"]["webview2"]["status"], "DETECTION_FAILED")
            self.assertTrue(all(row["architecture"] == "UNPROVEN" for row in result["runtimes"]["webview2"]["observations"]))
        self.assertEqual([item[3] for item in opened], [fake.KEY_READ | 0x100, fake.KEY_READ | 0x200, fake.KEY_READ | 0x200, fake.KEY_READ | 0x200])
        self.assertEqual([item[1] for item in opened], [readiness.VC_KEY, readiness.VC_KEY, readiness.WV_KEY, readiness.WV_KEY])
        self.assertEqual(queried, ["Version", "Installed", "Major", "Minor", "Bld", "Rbld"] * 2 + ["pv"] * 2)

    def test_registry_access_failure_is_not_missing(self):
        fake = SimpleNamespace(HKEY_LOCAL_MACHINE=1, KEY_READ=1, KEY_WOW64_64KEY=2,
                               OpenKey=mock.Mock(side_effect=PermissionError()))
        with mock.patch.dict("sys.modules", {"winreg": fake}), mock.patch.object(readiness.os, "name", "nt"), mock.patch.object(readiness.platform, "machine", return_value="AMD64"):
            self.assertEqual(readiness.WindowsRegistryReader().read(readiness.VC_SOURCE).query, "FAILED")


@unittest.skipUnless(os.name == "nt", "Windows local-file policy")
class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="shirushi-readiness-unit-")
        self.root = Path(self.temporary.name)
        self.owner = readiness._stamp(self.root.lstat())[:2]
        self.candidate = self.root / readiness.CANDIDATE_NAME
        self.candidate.write_bytes(b"synthetic-only")
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()))
        self.assertTrue(self.root.name.startswith("shirushi-readiness-unit-"))
        self.assertEqual(readiness._stamp(self.root.lstat())[:2], self.owner)
        self.assertFalse(getattr(self.root.lstat(), "st_file_attributes", 0) & readiness.REPARSE)
        self.temporary.cleanup()

    def test_synthetic_positive_digest_boundary_is_not_live_candidate_proof(self):
        with mock.patch.object(readiness, "_hash_candidate", return_value=readiness.CANDIDATE_SHA256) as boundary:
            result = readiness.verify_candidate(self.candidate)
        boundary.assert_called_once_with(self.candidate)
        self.assertEqual(result["webview2"]["status"], "CANDIDATE_IDENTITY_MATCH")
        self.assertEqual(result["webview2"]["approval"], "CANDIDATE")
        self.assertEqual(result["webview2"]["currentSignatureVerification"], "NOT_PERFORMED")
        self.assertEqual(result["vc"]["status"], "VC_OFFLINE_INPUT_UNCONFIGURED")
        readiness.validate_result(readiness.evaluate(rows(), offline_input=result))

    def test_actual_wrong_size_and_missing_candidate(self):
        self.assertEqual(readiness.verify_candidate(self.candidate)["webview2"]["error"], "SIZE_MISMATCH")
        self.candidate.unlink()
        self.assertEqual(readiness.verify_candidate(self.candidate)["webview2"]["error"], "PATH_UNAVAILABLE")

    def test_wrong_digest_is_not_candidate_match(self):
        with mock.patch.object(readiness, "_hash_candidate", return_value="0" * 64):
            self.assertEqual(readiness.verify_candidate(self.candidate)["webview2"]["error"], "HASH_MISMATCH")

    def test_bad_path_basename_and_network_drive_rejected_before_io(self):
        for path in ("relative.exe", r"\\server\share\file.exe", r"C:\a\..\file.exe", r"C:\a\file.exe:stream", r"C:\CON\file.exe", r"C:\a\file.exe", "C:/a/" + readiness.CANDIDATE_NAME.lower()):
            with self.subTest(path=path), mock.patch.object(readiness, "_hash_candidate") as reader:
                self.assertEqual(readiness.verify_candidate(path)["webview2"]["status"], "IDENTITY_REJECTED")
                reader.assert_not_called()
        with mock.patch.object(readiness, "_drive_type", return_value=4), mock.patch.object(readiness, "_hash_candidate") as reader:
            self.assertEqual(readiness.verify_candidate(self.candidate)["webview2"]["error"], "LOCAL_FIXED_DRIVE_REQUIRED")
            reader.assert_not_called()

    def test_directory_input_rejected(self):
        self.candidate.unlink()
        self.candidate.mkdir()
        self.assertEqual(readiness.verify_candidate(self.candidate)["webview2"]["error"], "FILE_TYPE_INVALID")

    def test_actual_symlink_rejected_when_creation_permitted(self):
        target = self.root / "target"
        target.write_bytes(b"synthetic")
        self.candidate.unlink()
        try:
            self.candidate.symlink_to(target)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("Symlink privilege unavailable; synthetic reparse test remains")
            raise
        self.assertEqual(readiness.verify_candidate(self.candidate)["webview2"]["error"], "REPARSE_REJECTED")

    def test_actual_hardlink_rejected(self):
        alias = self.root / "hardlink-alias"
        try:
            os.link(self.candidate, alias)
        except OSError as error:
            if getattr(error, "winerror", None) in (1, 50):
                self.skipTest("Filesystem hardlinks unsupported; synthetic count test remains")
            raise
        self.assertEqual(readiness.verify_candidate(self.candidate)["webview2"]["error"], "HARDLINK_REJECTED")

    def test_synthetic_hardlink_or_unavailable_link_count_rejected(self):
        actual = self.candidate.lstat()
        for links, expected in ((2, "HARDLINK_REJECTED"), (0, "FILE_IDENTITY_UNAVAILABLE"), (None, "FILE_IDENTITY_UNAVAILABLE"), (True, "FILE_IDENTITY_UNAVAILABLE")):
            metadata = SimpleNamespace(st_mode=actual.st_mode, st_file_attributes=0,
                                       st_dev=actual.st_dev, st_ino=actual.st_ino,
                                       st_size=actual.st_size, st_mtime_ns=actual.st_mtime_ns, st_nlink=links)
            with self.subTest(links=links), mock.patch.object(Path, "lstat", return_value=metadata):
                with self.assertRaisesRegex(readiness.ReadinessError, expected):
                    readiness._metadata(self.candidate)

    def test_synthetic_reparse_leaf_and_ancestor_rejected(self):
        original = Path.lstat
        for changed in (self.candidate, self.root):
            def lstat(path, **kwargs):
                actual = original(path, **kwargs)
                if path != changed:
                    return actual
                return SimpleNamespace(st_mode=actual.st_mode, st_file_attributes=readiness.REPARSE,
                                       st_dev=actual.st_dev, st_ino=actual.st_ino, st_size=actual.st_size, st_mtime_ns=actual.st_mtime_ns)
            with self.subTest(changed=changed.name), mock.patch.object(Path, "lstat", lstat):
                self.assertEqual(readiness.verify_candidate(self.candidate)["webview2"]["error"], "REPARSE_REJECTED")

    def test_file_identity_unavailable_fail_closed(self):
        for inode in (0, None, True):
            with self.assertRaisesRegex(readiness.ReadinessError, "FILE_IDENTITY_UNAVAILABLE"):
                readiness._stamp(SimpleNamespace(st_dev=0, st_ino=inode, st_size=1, st_mtime_ns=1))

    def test_small_synthetic_hash_stream_size_identity_and_bounded_reads(self):
        raw = self.candidate.read_bytes()
        with mock.patch.object(readiness, "CANDIDATE_SIZE", len(raw)), mock.patch.object(readiness, "CHUNK", 3):
            self.assertEqual(readiness._hash_candidate(self.candidate), hashlib.sha256(raw).hexdigest())

    def test_deterministic_mutation_during_hash_rejected(self):
        raw = self.candidate.read_bytes()
        real_digest = hashlib.sha256()
        candidate = self.candidate
        class MutatingDigest:
            def update(self, block):
                real_digest.update(block)
                candidate.write_bytes(raw + b"changed")
            def hexdigest(self):
                return real_digest.hexdigest()
        with mock.patch.object(readiness, "CANDIDATE_SIZE", len(raw)), mock.patch.object(readiness.hashlib, "sha256", return_value=MutatingDigest()):
            with self.assertRaisesRegex(readiness.ReadinessError, "SOURCE_CHANGED"):
                readiness._hash_candidate(self.candidate)

    def test_read_handle_identity_substitution_rejected(self):
        raw = self.candidate.read_bytes()
        actual = self.candidate.lstat()
        replacement = SimpleNamespace(st_dev=actual.st_dev, st_ino=actual.st_ino + 1, st_size=actual.st_size, st_mtime_ns=actual.st_mtime_ns, st_nlink=actual.st_nlink)
        with mock.patch.object(readiness, "CANDIDATE_SIZE", len(raw)), mock.patch.object(readiness.os, "fstat", return_value=replacement):
            with self.assertRaisesRegex(readiness.ReadinessError, "SOURCE_CHANGED"):
                readiness._hash_candidate(self.candidate)


if __name__ == "__main__":
    unittest.main()
