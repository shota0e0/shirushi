"""Direct offline tests; every mutation is confined to a temporary copy."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.request


ROOT = Path(__file__).parents[1]
CANONICAL = ROOT / "packaging/license_sources/inspection-helper/c2pa-mpl"
spec = importlib.util.spec_from_file_location("mpl_evidence", ROOT / "scripts/verify_c2pa_mpl_source_evidence.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)

# Independent test expectations: neither record bytes nor validator constants
# are the authority for these assertions.
EXPECTED = {
    "mod.rs": (52740, "90d0f896297b63b44986d04214bfdd896bd9f02fd1df47e8abdf86ddc9f4b5b2"),
    "rfc3161.rs": (15510, "1a3974bee08c695c556b897b4381d697bf094bc304e8628881feaa47a55719de"),
    "rfc3281.rs": (7345, "916728217ff7b38d3e6cc4cda1289ab060aad4ee7ed6ee73c06a85b2580c9830"),
    "rfc4210.rs": (1153, "a5206504a011198c59a30fcb7ebf2f85d89d1c4325047808109b383ff308558f"),
    "rfc5652.rs": (45433, "6ed39ddf6e037b2816d5d9a0b154cfc415367f0f82524e2043cb40b2964afc95"),
}
LICENSE_SHA = "3f3d9e0024b1921b067d6f7f88deb4a60cbe7a78e76c64e3f1d7fc3b779b9d04"


class SourceEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = {p.relative_to(CANONICAL).as_posix(): p.read_bytes()
                      for p in CANONICAL.rglob("*") if p.is_file()}

    @classmethod
    def tearDownClass(cls):
        after = {p.relative_to(CANONICAL).as_posix(): p.read_bytes()
                 for p in CANONICAL.rglob("*") if p.is_file()}
        if after != cls.before:
            raise AssertionError("canonical evidence mutated")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="shirushi-mpl-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "evidence"
        shutil.copytree(CANONICAL, self.root)
        self.record_path = self.root / "approved-source-set.json"

    def source(self, name="mod.rs"):
        return self.root / "sdk/src/crypto/asn1" / name

    def record(self):
        return json.loads(self.record_path.read_bytes())

    def save_record(self, record):
        self.record_path.write_bytes(json.dumps(record).encode("utf-8"))

    def rejected(self, category=None):
        with self.assertRaises(verifier.EvidenceError) as raised:
            verifier.validate(self.root)
        if category:
            self.assertEqual(str(raised.exception), category)
        self.assertNotIn(str(self.root), str(raised.exception))

    def test_valid_exact_evidence(self):
        result = verifier.validate(self.root)
        self.assertEqual(result, {"result": "MPL_SOURCE_EVIDENCE_VALID", "schemaVersion": 1,
                                 "sourceCount": 5, "totalSourceBytes": 122181,
                                 "licenseSha256": LICENSE_SHA})
        for name, (size, sha) in EXPECTED.items():
            data = self.source(name).read_bytes()
            self.assertEqual(len(data), size)
            self.assertEqual(hashlib.sha256(data).hexdigest(), sha)
            self.assertNotIn(b"\r", data)
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        license_data = (self.root / "MPL-2.0.txt").read_bytes()
        self.assertEqual(len(license_data), 16726)
        self.assertEqual(hashlib.sha256(license_data).hexdigest(), LICENSE_SHA)

    def test_missing_source(self):
        self.source().unlink()
        self.rejected("INVENTORY_MISSING")

    def test_extra_rs_source(self):
        self.source("extra.rs").write_bytes(b"extra")
        self.rejected("INVENTORY_UNEXPECTED")

    def test_extra_non_rs_source(self):
        self.source("extra.txt").write_bytes(b"extra")
        self.rejected("INVENTORY_UNEXPECTED")

    def test_extra_empty_directory(self):
        (self.root / "sdk/unlisted").mkdir()
        self.rejected("INVENTORY_UNEXPECTED")

    def test_source_byte_tamper(self):
        path = self.source()
        path.write_bytes(path.read_bytes()[:-1] + b" ")
        self.rejected("SOURCE_HASH_MISMATCH")

    def test_source_crlf_rejected(self):
        path = self.source()
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.rejected("FILE_SIZE_INVALID")

    def test_header_modified(self):
        path = self.source()
        path.write_bytes(path.read_bytes().replace(b"Mozilla", b"MOZILLA", 1))
        self.rejected("MPL_HEADER_MISMATCH")

    def test_header_removed(self):
        path = self.source()
        path.write_bytes(path.read_bytes()[len(verifier.MPL_HEADER):])
        self.rejected("MPL_HEADER_MISMATCH")

    def test_license_tamper(self):
        path = self.root / "MPL-2.0.txt"
        path.write_bytes(path.read_bytes()[:-1] + b" ")
        self.rejected("LICENSE_HASH_MISMATCH")

    def test_license_crlf_rejected(self):
        path = self.root / "MPL-2.0.txt"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.rejected("FILE_SIZE_INVALID")

    def test_wrong_license_hash(self):
        record = self.record()
        record["licenseText"]["sha256"] = "0" * 64
        self.save_record(record)
        self.rejected("APPROVAL_CONTRACT_MISMATCH")

    def test_wrong_source_size(self):
        record = self.record()
        record["files"][0]["size"] += 1
        self.save_record(record)
        self.rejected("APPROVAL_CONTRACT_MISMATCH")

    def test_wrong_source_sha(self):
        record = self.record()
        record["files"][0]["sha256"] = "0" * 64
        self.save_record(record)
        self.rejected("APPROVAL_CONTRACT_MISMATCH")

    def test_wrong_c2pa_commit(self):
        record = self.record()
        record["c2pa"]["commit"] = "0" * 40
        self.save_record(record)
        self.rejected("APPROVAL_CONTRACT_MISMATCH")

    def test_wrong_c2pa_version(self):
        record = self.record()
        record["c2pa"]["version"] = "0.85.1"
        self.save_record(record)
        self.rejected("APPROVAL_CONTRACT_MISMATCH")

    def test_wrong_original_commit(self):
        record = self.record()
        record["originalUpstream"]["commit"] = "0" * 40
        self.save_record(record)
        self.rejected("APPROVAL_CONTRACT_MISMATCH")

    def test_other_provenance_identity_changes(self):
        for section, key in [("c2pa", "name"), ("c2pa", "tag"), ("c2pa", "repository"),
                             ("originalUpstream", "project"), ("originalUpstream", "version"),
                             ("originalUpstream", "tag"), ("originalUpstream", "repository"),
                             ("licenseText", "authoritativeSource")]:
            with self.subTest(section=section, key=key):
                record = json.loads(self.before["approved-source-set.json"])
                record[section][key] = "unexpected"
                self.save_record(record)
                self.rejected("APPROVAL_CONTRACT_MISMATCH")

    def test_duplicate_json_key(self):
        raw = self.record_path.read_bytes().replace(b'"schemaVersion": 1', b'"schemaVersion": 1, "schemaVersion": 1', 1)
        self.record_path.write_bytes(raw)
        self.rejected("JSON_DUPLICATE_KEY")

    def test_nested_duplicate_json_key(self):
        raw = self.record_path.read_bytes().replace(b'"name": "c2pa"', b'"name": "c2pa", "name": "c2pa"', 1)
        self.record_path.write_bytes(raw)
        self.rejected("JSON_DUPLICATE_KEY")

    def test_unknown_json_field(self):
        record = self.record()
        record["helperSha256"] = "0" * 64
        self.save_record(record)
        self.rejected("RECORD_FIELDS_INVALID")

    def test_nested_unknown_json_field(self):
        record = self.record()
        record["c2pa"]["extra"] = "unexpected"
        self.save_record(record)
        self.rejected("RECORD_FIELDS_INVALID")

    def test_missing_json_field(self):
        record = self.record()
        del record["sourceScope"]
        self.save_record(record)
        self.rejected("RECORD_FIELDS_INVALID")

    def test_noncanonical_sha(self):
        record = self.record()
        record["files"][0]["sha256"] = record["files"][0]["sha256"].upper()
        self.save_record(record)
        self.rejected("RECORD_HASH_INVALID")

    def test_path_escape(self):
        for path in ["../outside.rs", "/absolute.rs", "C:/private/file.rs", "sdk/../mod.rs", "sdk\\mod.rs", "sdk//mod.rs", "sdk/./mod.rs"]:
            with self.subTest(path_kind=path):
                record = json.loads(self.before["approved-source-set.json"])
                record["files"][0]["relativePath"] = path
                self.save_record(record)
                self.rejected("RECORD_PATH_INVALID")

    def test_stale_unlisted_source_path(self):
        record = self.record()
        record["files"][0]["relativePath"] = "sdk/src/crypto/asn1/old.rs"
        self.save_record(record)
        self.rejected("APPROVAL_CONTRACT_MISMATCH")

    def test_duplicate_record_source(self):
        record = self.record()
        record["files"][1] = dict(record["files"][0])
        self.save_record(record)
        self.rejected("RECORD_PATH_COLLISION")

    def test_case_collision_record(self):
        record = self.record()
        record["files"][1]["relativePath"] = record["files"][0]["relativePath"].upper()
        self.save_record(record)
        self.rejected("RECORD_PATH_COLLISION")

    def test_wrong_schema(self):
        record = self.record()
        record["schemaVersion"] = 2
        self.save_record(record)
        self.rejected("APPROVAL_CONTRACT_MISMATCH")

    def test_boolean_is_not_integer(self):
        record = self.record()
        record["schemaVersion"] = True
        self.save_record(record)
        self.rejected("RECORD_TYPE_INVALID")

    def test_wrong_types_and_unexpected_containers(self):
        for key, value in [("schemaVersion", "1"), ("schemaVersion", 1.0), ("c2pa", []),
                           ("files", {}), ("licenseText", []), ("sourceScope", None)]:
            with self.subTest(key=key, kind=type(value).__name__):
                record = json.loads(self.before["approved-source-set.json"])
                record[key] = value
                self.save_record(record)
                self.rejected("RECORD_TYPE_INVALID")

    def test_wrong_nested_size_type(self):
        record = self.record()
        record["files"][0]["size"] = "52740"
        self.save_record(record)
        self.rejected("RECORD_TYPE_INVALID")

    def test_unsupported_license_and_classification(self):
        for key, value in [("license", "MIT"), ("classification", "EXACT_MATCH")]:
            with self.subTest(key=key):
                record = json.loads(self.before["approved-source-set.json"])
                record["sourceScope"][key] = value
                self.save_record(record)
                self.rejected("APPROVAL_CONTRACT_MISMATCH")

    def test_source_and_json_coordinated_tamper(self):
        path = self.source()
        data = path.read_bytes()[:-1] + b" "
        path.write_bytes(data)
        record = self.record()
        record["files"][0]["sha256"] = hashlib.sha256(data).hexdigest()
        self.save_record(record)
        self.rejected("APPROVAL_CONTRACT_MISMATCH")

    def test_license_and_json_coordinated_tamper(self):
        path = self.root / "MPL-2.0.txt"
        data = path.read_bytes()[:-1] + b" "
        path.write_bytes(data)
        record = self.record()
        record["licenseText"]["sha256"] = hashlib.sha256(data).hexdigest()
        self.save_record(record)
        self.rejected("APPROVAL_CONTRACT_MISMATCH")

    def test_malformed_and_trailing_json(self):
        for raw in [b"{", b"{}{}", self.before["approved-source-set.json"] + b"true", b'NaN', b'\xff']:
            with self.subTest(kind=raw[:8]):
                self.record_path.write_bytes(raw)
                self.rejected("JSON_INVALID")

    def test_record_size_bounded(self):
        self.record_path.write_bytes(b" " * (verifier.MAX_RECORD_BYTES + 1))
        self.rejected("FILE_SIZE_INVALID")

    def test_metadata_missing_or_extra(self):
        (self.root / "README.md").unlink()
        self.rejected("INVENTORY_MISSING")
        (self.root / "README.md").write_bytes(self.before["README.md"])
        (self.root / "provenance.json").write_bytes(b"{}")
        self.rejected("INVENTORY_UNEXPECTED")

    def test_non_regular_source(self):
        path = self.source()
        path.unlink()
        path.mkdir()
        self.rejected("INVENTORY_UNEXPECTED")

    def test_symlink_source_where_supported(self):
        path = self.source()
        original = Path(self.temporary.name) / "original.rs"
        shutil.copyfile(path, original)
        path.unlink()
        try:
            path.symlink_to(original)
        except (OSError, NotImplementedError):
            self.skipTest("platform does not permit symlink creation; no privilege/policy changes")
        self.rejected("LINK_OR_REPARSE_REJECTED")

    def test_symlink_source_directory_where_supported(self):
        directory = self.source().parent
        original = Path(self.temporary.name) / "original-directory"
        directory.rename(original)
        try:
            directory.symlink_to(original, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("platform does not permit directory symlink creation")
        self.rejected("LINK_OR_REPARSE_REJECTED")

    def test_windows_reparse_attribute(self):
        class Info:
            st_mode = 0o100644
            st_file_attributes = 0x400
        self.assertTrue(verifier._is_reparse(Info()))
        real_stat = Path.lstat

        def stat_with_reparse(path, *args, **kwargs):
            return Info() if path == self.source() else real_stat(path, *args, **kwargs)

        with patch.object(Path, "lstat", stat_with_reparse):
            self.rejected("LINK_OR_REPARSE_REJECTED")

    def test_network_and_process_independent(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
             patch.object(socket.socket, "connect", side_effect=AssertionError("network")), \
             patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")), \
             patch.object(subprocess, "Popen", side_effect=AssertionError("process")):
            self.assertEqual(verifier.validate(self.root)["result"], "MPL_SOURCE_EVIDENCE_VALID")

    def test_cli_success_is_stable(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exit_code = verifier.main(["--root", str(self.root)])
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(out.getvalue()), verifier.validate(self.root))
        self.assertNotIn(str(self.root), out.getvalue())

    def test_cli_failure_nonzero_and_path_free(self):
        self.source().unlink()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exit_code = verifier.main(["--root", str(self.root)])
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(out.getvalue()), {"result": "MPL_SOURCE_EVIDENCE_INVALID", "category": "INVENTORY_MISSING"})
        self.assertNotIn(str(self.root), out.getvalue())
        self.assertEqual(err.getvalue(), "")

    def test_cli_invalid_arguments_path_free(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exit_code = verifier.main(["--arbitrary", str(self.root)])
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(out.getvalue())["category"], "CLI_INVALID")
        self.assertNotIn(str(self.root), out.getvalue())


if __name__ == "__main__":
    unittest.main()
