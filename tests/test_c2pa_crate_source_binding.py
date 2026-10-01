"""Offline direct tests; pass the exact existing archive explicitly.

python -B tests/test_c2pa_crate_source_binding.py --crate EXACT_ARCHIVE -v

Public-gate tests never override its approved checksum. Synthetic content tests
exercise the private parser separately; they are not archive approval evidence.
All mutations use temporary copies or in-memory tar/gzip data.
"""

import argparse
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "packaging/license_sources/inspection-helper/c2pa-mpl"
LOCK = ROOT / "tools/f3a-rust-sdk-parity/Cargo.lock"
spec = importlib.util.spec_from_file_location("crate_binding", ROOT / "scripts/verify_c2pa_crate_source_binding.py")
binding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(binding)
ARCHIVE_INPUT = None
SHA = "cd6fa73bf92e8ae8980779f39ad4bf2a1e575eb97f95f2accf1e73f0602c5b0b"
COMMIT = "3f40cdd22b60bf955d531b0301604e3f257e0a19"
PREFIX = "c2pa-0.85.0/"
SOURCE_NAMES = ("mod.rs", "rfc3161.rs", "rfc3281.rs", "rfc4210.rs", "rfc5652.rs")
VCS = {"git": {"sha1": COMMIT}, "path_in_vcs": "sdk"}


def synthetic_archive(entries):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, body, kind, target in entries:
            info = tarfile.TarInfo(name)
            info.type, info.linkname = kind, target
            info.size = len(body) if kind == tarfile.REGTYPE else 0
            archive.addfile(info, io.BytesIO(body) if kind == tarfile.REGTYPE else None)
    return gzip.compress(data.getvalue(), mtime=0)


def entry(name, body=b"", kind=tarfile.REGTYPE, target=""):
    return name, body, kind, target


class CrateSourceBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if ARCHIVE_INPUT is None:
            raise RuntimeError("explicit --crate input required; no cache discovery")
        cls.canonical_before = ARCHIVE_INPUT.read_bytes()
        if hashlib.sha256(cls.canonical_before).hexdigest() != SHA:
            raise RuntimeError("approved archive unavailable or hash mismatch; do not download")
        cls.evidence_before = {p.relative_to(EVIDENCE).as_posix(): p.read_bytes()
                               for p in EVIDENCE.rglob("*") if p.is_file()}
        cls.lock_before = LOCK.read_bytes()
        cls.temporary = tempfile.TemporaryDirectory(prefix="shirushi-crate-binding-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.archive = Path(cls.temporary.name) / "approved.crate"
        shutil.copyfile(ARCHIVE_INPUT, cls.archive)

    @classmethod
    def tearDownClass(cls):
        if ARCHIVE_INPUT.read_bytes() != cls.canonical_before or LOCK.read_bytes() != cls.lock_before:
            raise AssertionError("canonical archive/lock mutated")
        after = {p.relative_to(EVIDENCE).as_posix(): p.read_bytes()
                 for p in EVIDENCE.rglob("*") if p.is_file()}
        if after != cls.evidence_before:
            raise AssertionError("frozen evidence mutated")

    def setUp(self):
        self.temporary_case = tempfile.TemporaryDirectory(prefix="shirushi-crate-case-")
        self.addCleanup(self.temporary_case.cleanup)
        self.root = Path(self.temporary_case.name)
        self.evidence = self.root / "evidence"
        shutil.copytree(EVIDENCE, self.evidence)
        self.lock = self.root / "Cargo.lock"
        self.lock.write_bytes(self.lock_before)
        self.members = [entry(PREFIX + "Cargo.toml", b'[package]\nname = "c2pa"\nversion = "0.85.0"\n'),
                        entry(PREFIX + ".cargo_vcs_info.json", json.dumps(VCS).encode())]
        for name in SOURCE_NAMES:
            self.members.append(entry(PREFIX + "src/crypto/asn1/" + name,
                                      self.evidence_before["sdk/src/crypto/asn1/" + name]))

    def parse(self, members=None):
        return binding._inspect_archive(synthetic_archive(self.members if members is None else members))

    def parser_rejected(self, members, category):
        with self.assertRaises(binding.BindingError) as raised:
            self.parse(members)
        self.assertEqual(str(raised.exception), category)

    def replace(self, name, data):
        return [entry(path, data if path == name else body, kind, target)
                for path, body, kind, target in self.members]

    def validate(self):
        return binding.validate(self.archive, self.evidence, lock_path=self.lock)

    def test_exact_valid_crate_complete_chain(self):
        result = self.validate()
        self.assertEqual(result, {"result": "C2PA_CRATE_SOURCE_BINDING_VALID", "package": "c2pa",
                                 "version": "0.85.0", "archiveSha256": SHA, "archiveSize": 6743003,
                                 "vcsCommit": COMMIT, "pathInVcs": "sdk", "sourceCount": 5,
                                 "matchedSourceBytes": 122181})
        self.assertEqual(binding.mpl.validate(self.evidence)["result"], "MPL_SOURCE_EVIDENCE_VALID")

    def test_private_content_positive_is_not_public_approval(self):
        raw = synthetic_archive(self.members)
        self.assertNotEqual(hashlib.sha256(raw).hexdigest(), SHA)
        sources = binding._inspect_archive(raw)
        self.assertEqual(set(sources), {"sdk/src/crypto/asn1/" + name for name in SOURCE_NAMES})
        for path, data in sources.items():
            self.assertEqual(data, self.evidence_before[path])

    def test_wrong_archive_hash_rejected_before_inspection(self):
        path = self.root / "different.crate"
        path.write_bytes(self.canonical_before[:-1] + bytes([self.canonical_before[-1] ^ 1]))
        with patch.object(binding, "_inspect_archive", side_effect=AssertionError("must not inspect")):
            with self.assertRaisesRegex(binding.BindingError, "^ARCHIVE_HASH_MISMATCH$"):
                binding.validate(path, self.evidence, lock_path=self.lock)

    def test_truncated_archive_public_and_parser(self):
        path = self.root / "truncated.crate"
        path.write_bytes(self.canonical_before[:100])
        with self.assertRaisesRegex(binding.BindingError, "^ARCHIVE_HASH_MISMATCH$"):
            binding.validate(path, self.evidence, lock_path=self.lock)
        with self.assertRaisesRegex(binding.BindingError, "^ARCHIVE_FORMAT_INVALID$"):
            binding._inspect_archive(path.read_bytes())

    def test_malformed_archive_public_and_parser(self):
        path = self.root / "malformed.crate"
        path.write_bytes(b"not gzip/tar")
        with self.assertRaisesRegex(binding.BindingError, "^ARCHIVE_HASH_MISMATCH$"):
            binding.validate(path, self.evidence, lock_path=self.lock)
        for data in (path.read_bytes(), gzip.compress(b"not tar", mtime=0)):
            with self.subTest(kind=len(data)):
                with self.assertRaisesRegex(binding.BindingError, "^ARCHIVE_FORMAT_INVALID$"):
                    binding._inspect_archive(data)

    def test_wrong_package_name(self):
        self.parser_rejected(self.replace(PREFIX + "Cargo.toml", b'[package]\nname="other"\nversion="0.85.0"\n'), "PACKAGE_IDENTITY_MISMATCH")

    def test_wrong_package_version(self):
        self.parser_rejected(self.replace(PREFIX + "Cargo.toml", b'[package]\nname="c2pa"\nversion="0.85.1"\n'), "PACKAGE_IDENTITY_MISMATCH")

    def test_missing_vcs(self):
        self.parser_rejected([e for e in self.members if not e[0].endswith(".cargo_vcs_info.json")], "ARCHIVE_METADATA_MISSING")

    def test_wrong_vcs_commit(self):
        record = {"git": {"sha1": "0" * 40}, "path_in_vcs": "sdk"}
        self.parser_rejected(self.replace(PREFIX + ".cargo_vcs_info.json", json.dumps(record).encode()), "VCS_IDENTITY_MISMATCH")

    def test_wrong_path_in_vcs(self):
        record = {"git": {"sha1": COMMIT}, "path_in_vcs": "src"}
        self.parser_rejected(self.replace(PREFIX + ".cargo_vcs_info.json", json.dumps(record).encode()), "VCS_IDENTITY_MISMATCH")

    def test_vcs_strict_metadata(self):
        cases = [b"{", b"{}", b"[]", json.dumps(VCS).encode() + b"true",
                 b'{"git":{"sha1":"x","sha1":"x"},"path_in_vcs":"sdk"}',
                 json.dumps({**VCS, "extra": True}).encode(),
                 json.dumps({"git": {"sha1": COMMIT, "dirty": False}, "path_in_vcs": "sdk"}).encode(),
                 json.dumps({"git": {"sha1": COMMIT}, "path_in_vcs": True}).encode()]
        for raw in cases:
            with self.subTest(case=raw[:12]):
                self.parser_rejected(self.replace(PREFIX + ".cargo_vcs_info.json", raw), "VCS_METADATA_INVALID")

    def test_one_source_missing(self):
        self.parser_rejected([e for e in self.members if not e[0].endswith("rfc3161.rs")], "ARCHIVE_SOURCE_MISSING")

    def test_archive_source_byte_tamper(self):
        name, data, _, _ = self.members[2]
        self.parser_rejected(self.replace(name, data[:-1] + b" "), "ARCHIVE_SOURCE_HASH_MISMATCH")

    def test_archive_source_crlf_conversion(self):
        name, data, _, _ = self.members[2]
        self.parser_rejected(self.replace(name, data.replace(b"\n", b"\r\n")), "ARCHIVE_SELECTED_SIZE_INVALID")

    def test_wrong_source_path(self):
        entries = [entry(path.replace("src/crypto/asn1/mod.rs", "sdk/src/crypto/asn1/mod.rs"), data, kind, target)
                   for path, data, kind, target in self.members]
        self.parser_rejected(entries, "ARCHIVE_SOURCE_MISSING")

    def test_basename_collision_elsewhere_not_accepted_as_source(self):
        entries = [e for e in self.members if not e[0].endswith("/mod.rs")]
        entries.append(entry(PREFIX + "unrelated/mod.rs", self.members[2][1]))
        self.parser_rejected(entries, "ARCHIVE_SOURCE_MISSING")

    def test_path_traversal_entry(self):
        self.parser_rejected(self.members + [entry(PREFIX + "../outside.rs", b"x")], "ARCHIVE_PATH_INVALID")

    def test_absolute_path_entry(self):
        for name in ("/outside.rs", "C:/outside.rs", PREFIX + "src\\outside.rs"):
            with self.subTest(kind=name):
                self.parser_rejected(self.members + [entry(name, b"x")], "ARCHIVE_PATH_INVALID")

    def test_duplicate_approved_entry(self):
        self.parser_rejected(self.members + [self.members[2]], "ARCHIVE_DUPLICATE_ENTRY")

    def test_case_collision_entry(self):
        original = self.members[2]
        self.parser_rejected(self.members + [entry(original[0].replace("mod.rs", "MOD.rs"), original[1])], "ARCHIVE_DUPLICATE_ENTRY")

    def test_frozen_evidence_tamper(self):
        source = self.evidence / "sdk/src/crypto/asn1/mod.rs"
        source.write_bytes(source.read_bytes()[:-1] + b" ")
        with self.assertRaisesRegex(binding.BindingError, "^FROZEN_EVIDENCE_INVALID$"):
            self.validate()

    def test_source_and_frozen_json_coordinated_tamper(self):
        source = self.evidence / "sdk/src/crypto/asn1/mod.rs"
        data = source.read_bytes()[:-1] + b" "
        source.write_bytes(data)
        record_path = self.evidence / "approved-source-set.json"
        record = json.loads(record_path.read_bytes())
        record["files"][0]["sha256"] = hashlib.sha256(data).hexdigest()
        record_path.write_bytes(json.dumps(record).encode())
        with self.assertRaisesRegex(binding.BindingError, "^FROZEN_EVIDENCE_INVALID$"):
            self.validate()

    def test_unexpected_archive_structure(self):
        cases = [self.members + [entry(PREFIX + "src/crypto/asn1/extra.rs", b"x")],
                 [entry(e[0].replace(PREFIX, "other-0.85.0/", 1), *e[1:]) for e in self.members]]
        for entries, category in zip(cases, ("ARCHIVE_SOURCE_INVENTORY_INVALID", "ARCHIVE_PATH_INVALID")):
            with self.subTest(category=category):
                self.parser_rejected(entries, category)

    def test_links_and_nonregular_entries_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.DIRTYPE):
            with self.subTest(kind=kind):
                self.parser_rejected(self.members + [entry(PREFIX + "link", kind=kind, target=self.members[2][0])], "ARCHIVE_ENTRY_INVALID")

    def test_archive_bounds_and_trailing_data(self):
        with self.assertRaisesRegex(binding.BindingError, "^ARCHIVE_EXPANSION_LIMIT$"):
            binding._inspect_archive(gzip.compress(b"\0" * (binding.MAX_TAR_BYTES + 1), mtime=0))
        raw_tar = gzip.decompress(synthetic_archive(self.members))
        with self.assertRaisesRegex(binding.BindingError, "^ARCHIVE_STRUCTURE_INVALID$"):
            binding._inspect_archive(gzip.compress(raw_tar + b"x" * 512, mtime=0))
        with self.assertRaisesRegex(binding.BindingError, "^ARCHIVE_SELECTED_SIZE_INVALID$"):
            self.parse(self.replace(PREFIX + ".cargo_vcs_info.json", b" " * (binding.MAX_VCS_BYTES + 1)))

    def test_lock_identity_negative(self):
        changes = [(b'version = "0.85.0"', b'version = "0.85.1"'),
                   (SHA.encode(), b"0" * 64),
                   (b'registry+https://github.com/rust-lang/crates.io-index', b'other-registry')]
        for old, new in changes:
            with self.subTest(kind=old[:12]):
                self.lock.write_bytes(self.lock_before.replace(old, new))
                with self.assertRaisesRegex(binding.BindingError, "^LOCK_IDENTITY_MISMATCH$"):
                    self.validate()

    def test_lock_missing_duplicate_and_malformed(self):
        good = '[[package]]\nname="c2pa"\nversion="0.85.0"\nsource="registry+https://github.com/rust-lang/crates.io-index"\nchecksum="' + SHA + '"\n'
        for text, category in (("", "LOCK_METADATA_INVALID"), ("[[package]]\nname=\"other\"\n", "LOCK_PACKAGE_INVALID"),
                               (good + good, "LOCK_PACKAGE_INVALID"), ("[[package", "LOCK_METADATA_INVALID")):
            with self.subTest(category=category):
                self.lock.write_bytes(text.encode())
                with self.assertRaisesRegex(binding.BindingError, "^" + category + "$"):
                    self.validate()

    def test_offline_no_process_or_network(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
             patch.object(socket.socket, "connect", side_effect=AssertionError("network")), \
             patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")), \
             patch.object(subprocess, "Popen", side_effect=AssertionError("process")):
            self.assertEqual(self.validate()["result"], "C2PA_CRATE_SOURCE_BINDING_VALID")

    def test_cli_success_stable_and_path_free(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = binding.main(["--crate", str(self.archive), "--evidence-root", str(self.evidence)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), self.validate())
        self.assertNotIn(str(self.root), out.getvalue())
        self.assertNotIn(str(self.archive), out.getvalue())

    def test_cli_failure_nonzero_and_path_free(self):
        archive = self.root / "bad.crate"
        archive.write_bytes(b"bad")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = binding.main(["--crate", str(archive), "--evidence-root", str(self.evidence)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue()), {"result": "C2PA_CRATE_SOURCE_BINDING_INVALID", "category": "ARCHIVE_HASH_MISMATCH"})
        self.assertNotIn(str(self.root), out.getvalue())
        self.assertEqual(err.getvalue(), "")

    def test_cli_no_fallback_or_hash_override(self):
        cases = [[], ["--evidence-root", str(self.evidence)], ["--crate", str(self.archive), "--sha256", SHA],
                 ["--crate", str(self.archive), "--crate", str(self.archive)], ["--crate"]]
        for args in cases:
            with self.subTest(arguments=len(args)), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(binding.main(args), 2)
                self.assertEqual(json.loads(out.getvalue())["category"], "CLI_INVALID")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--crate", required=True)
    args, remaining = parser.parse_known_args()
    ARCHIVE_INPUT = Path(args.crate)
    unittest.main(argv=[sys.argv[0]] + remaining)
