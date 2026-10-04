"""Synthetic byte assembly only; no fixture binary is executed or committed."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import package_v02_development as package


def synthetic_pe(subsystem: int, marker: int = 1) -> bytes:
    raw = bytearray(1024)
    raw[:2] = b"MZ"
    struct.pack_into("<I", raw, 60, 128)
    raw[128:132] = b"PE\0\0"
    struct.pack_into("<HH", raw, 132, 0x8664, 1)
    struct.pack_into("<HH", raw, 148, 240, 0x22)
    struct.pack_into("<H", raw, 152, 0x20B)
    struct.pack_into("<H", raw, 220, subsystem)
    struct.pack_into("<II", raw, 408, 512, 512)
    raw[-1] = marker
    return bytes(raw)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@unittest.skipUnless(os.name == "nt", "Windows no-clobber/local-volume contract")
class AssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="shirushi-v02-unit-")
        self.root = Path(self.temporary.name)
        self.root_identity = package._identity(self.root.lstat())
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.desktop = self.inputs / package.DESKTOP
        self.helper = self.inputs / package.HELPER
        self.desktop.write_bytes(synthetic_pe(2))
        self.helper.write_bytes(synthetic_pe(3, 2))
        self.output = self.root / "development-package"
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        # Exclusive test-owned root, never an arbitrary computed recursive target.
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()))
        self.assertTrue(self.root.name.startswith("shirushi-v02-unit-"))
        self.assertFalse(package._reparse(self.root.lstat()))
        self.assertEqual(package._identity(self.root.lstat()), self.root_identity)
        self.temporary.cleanup()

    def assemble(self, **kwargs) -> dict:
        return package.assemble(self.desktop, self.helper, self.output, **kwargs)

    def rejected(self, callable_, code: str | None = None) -> None:
        with self.assertRaises(package.PackageError) as caught:
            callable_()
        if code is not None:
            self.assertEqual(str(caught.exception), code)

    def test_valid_assembly_exact_flat_inventory_and_original_bytes(self) -> None:
        before = self.desktop.read_bytes(), self.helper.read_bytes()
        record = self.assemble()
        self.assertEqual(set(p.name for p in self.output.iterdir()), package.FILES)
        self.assertEqual(self.desktop.read_bytes(), before[0])
        self.assertEqual(self.helper.read_bytes(), before[1])
        self.assertEqual((self.output / package.DESKTOP).read_bytes(), before[0])
        self.assertEqual((self.output / package.HELPER).read_bytes(), before[1])
        package.audit_package(self.output, record)
        self.assertEqual(record["runtimeBinding"], "UNPROVEN")

    def test_manifest_is_exact_current_helper_schema_and_actual_bytes(self) -> None:
        record = self.assemble()
        raw = (self.output / package.MANIFEST).read_bytes()
        manifest = package.parse_manifest(raw)
        self.assertEqual(set(manifest), {"schemaVersion", "relativePath", "size", "sha256", "protocolVersion"})
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["protocolVersion"], 1)
        self.assertEqual(manifest["relativePath"], package.HELPER)
        self.assertEqual(manifest["size"], (self.output / package.HELPER).stat().st_size)
        self.assertEqual(manifest["sha256"], sha((self.output / package.HELPER).read_bytes()))
        self.assertEqual(raw, package._canonical(manifest))
        self.assertEqual(record["files"][package.MANIFEST]["sha256"], sha(raw))

    def test_prebuild_preparation_raw_bytes_equal_packaged_manifest(self) -> None:
        raw = package.prepare_manifest(self.helper)
        frozen_digest = sha(raw)
        self.assemble()
        self.assertEqual((self.output / package.MANIFEST).read_bytes(), raw)
        self.assertEqual(sha((self.output / package.MANIFEST).read_bytes()), frozen_digest)
        self.assertFalse(raw.endswith(b"\n"))

    def test_helper_only_preparation_needs_no_desktop_and_cli_emits_exact_bytes(self) -> None:
        self.desktop.unlink()
        output, error = io.StringIO(), io.StringIO()
        output.buffer = io.BytesIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            self.assertEqual(package.main(["prepare-manifest", "--helper", str(self.helper)]), 0)
        raw = output.buffer.getvalue()
        self.assertEqual(raw, package.prepare_manifest(self.helper))
        self.assertFalse(raw.endswith(b"\n"))
        self.assertIn("DEVELOPMENT_MANIFEST_SHA256: " + sha(raw), error.getvalue())
        self.assertFalse(self.output.exists())

    def test_helper_preparation_rejects_wrong_basename_architecture_and_directory(self) -> None:
        wrong = self.inputs / "other.exe"
        wrong.write_bytes(self.helper.read_bytes())
        self.rejected(lambda: package.prepare_manifest(wrong), "SOURCE_BASENAME_INVALID")
        invalid = bytearray(synthetic_pe(3))
        struct.pack_into("<H", invalid, 132, 0x14c)
        self.helper.write_bytes(invalid)
        self.rejected(lambda: package.prepare_manifest(self.helper), "PE_IDENTITY_INVALID")
        self.helper.unlink()
        self.helper.mkdir()
        self.rejected(lambda: package.prepare_manifest(self.helper), "PATH_TYPE_INVALID")

    def test_helper_preparation_detects_mutation_during_serialization(self) -> None:
        original = package._helper_manifest
        def mutate(size, digest):
            raw = original(size, digest)
            self.helper.write_bytes(synthetic_pe(3, 99))
            return raw
        with mock.patch.object(package, "_helper_manifest", side_effect=mutate):
            self.rejected(lambda: package.prepare_manifest(self.helper), "SOURCE_CHANGED")

    def test_missing_desktop(self) -> None:
        self.desktop.unlink()
        self.rejected(self.assemble, "PATH_UNAVAILABLE")
        self.assertFalse(self.output.exists())

    def test_missing_helper(self) -> None:
        self.helper.unlink()
        self.rejected(self.assemble, "PATH_UNAVAILABLE")
        self.assertFalse(self.output.exists())

    def test_source_tamper_after_independent_snapshot_before_assembly(self) -> None:
        frozen = package.capture_inputs(self.desktop, self.helper)
        self.helper.write_bytes(synthetic_pe(3, 99))
        self.rejected(lambda: self.assemble(snapshot=frozen), "SOURCE_CHANGED")
        self.assertFalse(self.output.exists())

    def test_staged_helper_tamper_not_published(self) -> None:
        original = package._copy
        def tamper(snapshot, target, owner):
            original(snapshot, target, owner)
            if target.name == package.HELPER:
                target.write_bytes(synthetic_pe(3, 99))
        with mock.patch.object(package, "_copy", side_effect=tamper):
            self.rejected(self.assemble, "STAGED_HELPER_CHANGED")
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".shirushi-v02-stage-*")))

    def test_staged_manifest_tamper_not_published(self) -> None:
        original = package.audit_package
        def tamper(root, record):
            (root / package.MANIFEST).write_bytes(b"{}")
            original(root, record)
        with mock.patch.object(package, "audit_package", side_effect=tamper):
            self.rejected(self.assemble, "PACKAGE_IDENTITY_MISMATCH")
        self.assertFalse(self.output.exists())

    def test_audit_rejects_manifest_tamper_and_unexpected_file(self) -> None:
        record = self.assemble()
        (self.output / package.MANIFEST).write_bytes(b"{}")
        self.rejected(lambda: package.audit_package(self.output, record), "PACKAGE_IDENTITY_MISMATCH")
        (self.output / "private.pdb").write_bytes(b"not permitted")
        self.rejected(lambda: package.audit_package(self.output, record), "PACKAGE_INVENTORY_INVALID")

    def test_wrong_helper_basename_is_not_discovered_or_normalized(self) -> None:
        wrong = self.inputs / "other-helper.exe"
        self.helper.rename(wrong)
        self.rejected(lambda: package.assemble(self.desktop, wrong, self.output), "SOURCE_BASENAME_INVALID")

    def test_output_existing_and_case_collision_never_overwrite(self) -> None:
        collision = self.root / self.output.name.upper()
        collision.mkdir()
        marker = collision / "owner.txt"
        marker.write_bytes(b"preserve")
        self.rejected(self.assemble)
        self.assertEqual(marker.read_bytes(), b"preserve")
        self.assertEqual(list(collision.iterdir()), [marker])

    def test_partial_copy_failure_cleans_only_private_stage_no_publish(self) -> None:
        original = package._copy
        def fail(snapshot, target, owner):
            if target.name == package.HELPER:
                target.write_bytes(b"partial")
                raise package.PackageError("CONTROLLED_COPY_FAILURE")
            original(snapshot, target, owner)
        with mock.patch.object(package, "_copy", side_effect=fail):
            self.rejected(self.assemble, "CONTROLLED_COPY_FAILURE")
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".shirushi-v02-stage-*")))
        self.assertEqual(self.desktop.read_bytes(), synthetic_pe(2))

    def test_repeated_assembly_is_logically_and_byte_deterministic(self) -> None:
        first = self.assemble()
        second_root = self.root / "development-package-second"
        second = package.assemble(self.desktop, self.helper, second_root)
        self.assertEqual(first, second)
        for name in package.FILES:
            self.assertEqual((self.output / name).read_bytes(), (second_root / name).read_bytes())

    def test_source_file_link_rejected_if_creation_permitted(self) -> None:
        real = self.inputs / "real-helper.exe"
        self.helper.rename(real)
        try:
            os.symlink(real, self.helper)
        except OSError:
            self.skipTest("Windows symlink privilege unavailable; synthetic reparse guards covered")
        try:
            self.rejected(self.assemble, "REPARSE_PATH_REJECTED")
        finally:
            self.helper.unlink()

    def test_output_parent_directory_link_rejected_if_creation_permitted(self) -> None:
        alias = self.root / "alias"
        try:
            os.symlink(self.inputs, alias, target_is_directory=True)
        except OSError:
            self.skipTest("Windows symlink privilege unavailable; synthetic reparse guards covered")
        try:
            self.rejected(lambda: package.assemble(self.desktop, self.helper, alias / "output"), "REPARSE_PATH_REJECTED")
        finally:
            alias.unlink()

    def test_reparse_source_and_output_ancestors_synthetic_attribution(self) -> None:
        original = Path.lstat
        for flagged in (self.inputs, self.root):
            def flagged_lstat(path):
                actual = original(path)
                return SimpleNamespace(st_mode=actual.st_mode, st_file_attributes=package.REPARSE) if path == flagged else actual
            with mock.patch.object(Path, "lstat", flagged_lstat):
                self.rejected(self.assemble, "REPARSE_PATH_REJECTED")

    def test_mapped_drive_classification_rejected_before_file_access(self) -> None:
        with mock.patch.object(package, "_drive_type", return_value=4):
            self.rejected(self.assemble, "LOCAL_FIXED_DRIVE_REQUIRED")

    def test_duplicate_case_inventory_rejected(self) -> None:
        record = self.assemble()
        file = self.output / package.MANIFEST
        file.rename(self.output / package.MANIFEST.upper())
        self.rejected(lambda: package.audit_package(self.output, record), "PACKAGE_INVENTORY_INVALID")

    def test_duplicate_casefold_entries_rejected_before_read(self) -> None:
        record = self.assemble()
        listing = mock.MagicMock()
        listing.__enter__.return_value = iter([
            SimpleNamespace(name=package.DESKTOP),
            SimpleNamespace(name=package.DESKTOP.upper()),
            SimpleNamespace(name=package.MANIFEST),
        ])
        with mock.patch.object(package.os, "scandir", return_value=listing), mock.patch.object(package, "_read_file") as read:
            self.rejected(lambda: package.audit_package(self.output, record), "PACKAGE_INVENTORY_INVALID")
            read.assert_not_called()

    def test_stage_owner_substitution_before_first_copy_never_writes(self) -> None:
        original = package._copy
        substituted = []
        def replace(snapshot, target, owner):
            displaced = self.root / "owned-stage-displaced"
            target.parent.rename(displaced)
            target.parent.mkdir()
            substituted.append(target.parent)
            original(snapshot, target, owner)
        with mock.patch.object(package, "_copy", side_effect=replace):
            self.rejected(self.assemble, "ASSEMBLY_FAILED_CLEANUP_REFUSED")
        self.assertEqual(list(substituted[0].iterdir()), [])
        self.assertFalse(self.output.exists())

    def test_stage_reparse_observation_before_first_copy_never_writes(self) -> None:
        original = package._copy
        original_metadata = package._metadata
        attempted = []
        def guarded(snapshot, target, owner):
            attempted.append(target)
            def metadata(path, directory=False):
                if path == target.parent:
                    raise package.PackageError("REPARSE_PATH_REJECTED")
                return original_metadata(path, directory)
            with mock.patch.object(package, "_metadata", side_effect=metadata):
                original(snapshot, target, owner)
        with mock.patch.object(package, "_copy", side_effect=guarded):
            self.rejected(self.assemble, "REPARSE_PATH_REJECTED")
        self.assertFalse(attempted[0].exists())
        self.assertFalse(self.output.exists())

    def test_stage_owner_substitution_before_manifest_never_writes(self) -> None:
        original = package._stage_destination
        substituted = []
        def replace(target, owner):
            if target.name == package.MANIFEST:
                displaced = self.root / "owned-stage-displaced"
                target.parent.rename(displaced)
                target.parent.mkdir()
                substituted.append(target.parent)
            original(target, owner)
        with mock.patch.object(package, "_stage_destination", side_effect=replace):
            self.rejected(self.assemble, "ASSEMBLY_FAILED_CLEANUP_REFUSED")
        self.assertEqual(list(substituted[0].iterdir()), [])
        self.assertFalse(self.output.exists())

    def test_zero_source_parent_or_stage_identity_never_writes_or_unlinks(self) -> None:
        original = package._metadata
        for target in ("source", "parent", "stage"):
            with self.subTest(target=target):
                stage_observations = []
                def metadata(path, directory=False):
                    actual = original(path, directory)
                    stage = directory and path.name.startswith(".shirushi-v02-stage-")
                    if stage:
                        stage_observations.append(path)
                    invalid = ((target == "source" and path == self.desktop)
                               or (target == "parent" and path == self.root)
                               or (target == "stage" and stage and len(stage_observations) > 1))
                    if invalid:
                        return SimpleNamespace(st_dev=actual.st_dev, st_ino=0,
                                               st_size=actual.st_size, st_mtime_ns=actual.st_mtime_ns)
                    return actual
                with mock.patch.object(package, "_metadata", side_effect=metadata), mock.patch.object(Path, "unlink") as unlink:
                    self.rejected(self.assemble, "ASSEMBLY_FAILED_CLEANUP_REFUSED" if target == "stage" else "FILE_IDENTITY_UNAVAILABLE")
                    unlink.assert_not_called()
                self.assertFalse(self.output.exists())
                if target == "stage":
                    self.assertEqual(list(stage_observations[0].iterdir()), [])

    def test_no_clobber_publish_even_destination_created_after_preflight(self) -> None:
        original = os.rename
        def collision(source, target):
            Path(target).mkdir()
            (Path(target) / "owner.txt").write_bytes(b"must remain")
            return original(source, target)
        with mock.patch.object(package.os, "rename", side_effect=collision):
            self.rejected(self.assemble, "NO_CLOBBER_PUBLISH_FAILED")
        self.assertEqual((self.output / "owner.txt").read_bytes(), b"must remain")

    def test_unsafe_stage_cleanup_refuses_unexpected_reparse_without_following(self) -> None:
        original = package.audit_package
        def unsafe(root, record):
            raise package.PackageError("CONTROLLED_AUDIT_FAILURE")
        original_metadata = package._metadata
        def metadata(path, directory=False):
            if not directory and path.name == package.HELPER and path.parent.name.startswith(".shirushi-v02-stage-"):
                raise package.PackageError("REPARSE_PATH_REJECTED")
            return original_metadata(path, directory)
        with mock.patch.object(package, "audit_package", side_effect=unsafe), mock.patch.object(package, "_metadata", side_effect=metadata):
            self.rejected(self.assemble, "ASSEMBLY_FAILED_CLEANUP_REFUSED")
        self.assertFalse(self.output.exists())

    def test_noncanonical_manifest_rejected_even_if_external_record_hash_matches(self) -> None:
        record = self.assemble()
        manifest = self.output / package.MANIFEST
        raw = json.dumps(json.loads(manifest.read_bytes()), indent=2).encode()
        manifest.write_bytes(raw)
        record["files"][package.MANIFEST] = {"size": len(raw), "sha256": sha(raw)}
        self.rejected(lambda: package.audit_package(self.output, record), "MANIFEST_NOT_CANONICAL")

    def test_coordinated_helper_manifest_tamper_does_not_authorize_itself(self) -> None:
        trusted = self.assemble()
        (self.output / package.HELPER).write_bytes(synthetic_pe(3, 99))
        manifest = json.loads((self.output / package.MANIFEST).read_bytes())
        manifest["sha256"] = sha(synthetic_pe(3, 99))
        (self.output / package.MANIFEST).write_bytes(package._canonical(manifest))
        self.rejected(lambda: package.audit_package(self.output, trusted), "PACKAGE_IDENTITY_MISMATCH")
        trusted_raw = package._canonical(trusted) + b"\n"
        tampered = copy.deepcopy(trusted)
        tampered["files"][package.HELPER]["sha256"] = manifest["sha256"]
        manifest_raw = (self.output / package.MANIFEST).read_bytes()
        tampered["files"][package.MANIFEST] = {"size": len(manifest_raw), "sha256": sha(manifest_raw)}
        record = self.root / "external-record.json"
        record.write_bytes(package._canonical(tampered) + b"\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(package.main(["audit", "--package-root", str(self.output), "--record", str(record), "--expected-record-sha256", sha(trusted_raw)]), 2)

    def test_cli_exact_stdout_bytes_digest_and_external_record_audit(self) -> None:
        output, error = io.StringIO(), io.StringIO()
        output.buffer = io.BytesIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            self.assertEqual(package.main(["assemble", "--desktop", str(self.desktop), "--helper", str(self.helper), "--output-root", str(self.output)]), 0)
        raw = output.buffer.getvalue()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertIn(sha(raw), error.getvalue())
        record = self.root / "external-record.json"
        record.write_bytes(raw)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(package.main(["audit", "--package-root", str(self.output), "--record", str(record), "--expected-record-sha256", sha(raw)]), 0)


class SchemaAndHeaderTests(unittest.TestCase):
    def rejected(self, callable_) -> None:
        with self.assertRaises(package.PackageError):
            callable_()

    def test_header_x64_pe32_plus_and_subsystems(self) -> None:
        package._pe(synthetic_pe(2), 1024, 2)
        package._pe(synthetic_pe(3), 1024, 3)
        self.rejected(lambda: package._pe(synthetic_pe(3), 1024, 2))

    def test_invalid_dos_signature_pe_signature_machine_magic_and_sections(self) -> None:
        for offset, format_, value in [(0, "<H", 0), (128, "<I", 0), (132, "<H", 0x14C), (152, "<H", 0x10B), (134, "<H", 97), (148, "<H", 111), (150, "<H", 0x2002), (408, "<I", 2048)]:
            raw = bytearray(synthetic_pe(2))
            struct.pack_into(format_, raw, offset, value)
            self.rejected(lambda: package._pe(bytes(raw), len(raw), 2))
        for raw in (b"", b"MZ", synthetic_pe(2)[:512]):
            self.rejected(lambda: package._pe(raw, len(raw), 2))

    def test_manifest_missing_unknown_fields_duplicates_and_invalid_values(self) -> None:
        manifest = {"schemaVersion": 1, "relativePath": package.HELPER, "size": 1024, "sha256": "a" * 64, "protocolVersion": 1}
        for key in manifest:
            value = dict(manifest)
            del value[key]
            self.rejected(lambda: package.parse_manifest(package._canonical(value)))
        for key, value in [("extra", 1), ("size", True), ("size", 0), ("size", package.MAX_EXECUTABLE + 1), ("sha256", "A" * 64), ("relativePath", "../" + package.HELPER), ("relativePath", "other.exe"), ("protocolVersion", 2), ("schemaVersion", True)]:
            changed = dict(manifest, **{key: value})
            self.rejected(lambda: package.parse_manifest(package._canonical(changed)))
        for raw in (b'{"size":1,"size":2}', b"{}{}", b" " * 4097, b"\xef\xbb\xbf{}", b"[]"):
            self.rejected(lambda: package.parse_manifest(raw))

    def test_record_missing_unknown_alternate_hash_and_case_names(self) -> None:
        record = {"schemaVersion": 1, "artifactType": "DEVELOPMENT_PACKAGE", "target": "x86_64-pc-windows-msvc", "runtimeBinding": "UNPROVEN", "files": {name: {"size": 10, "sha256": "a" * 64} for name in package.FILES}}
        package.parse_record(package._canonical(record))
        for key in record:
            value = copy.deepcopy(record)
            del value[key]
            self.rejected(lambda: package.parse_record(package._canonical(value)))
        for key, value in [("extra", True), ("artifactType", "RELEASE"), ("runtimeBinding", "PROVEN"), ("target", "x86"), ("schemaVersion", True)]:
            changed = dict(record, **{key: value})
            self.rejected(lambda: package.parse_record(package._canonical(changed)))
        changed = copy.deepcopy(record)
        changed["files"][package.HELPER.upper()] = changed["files"][package.HELPER]
        self.rejected(lambda: package.parse_record(package._canonical(changed)))
        self.rejected(lambda: package.parse_record(b" " * 8193))

    def test_path_escape_reserved_ads_unc_relative_and_trailing_aliases(self) -> None:
        for path in ("relative.exe", "C:\\", "C:relative.exe", "\\\\server\\share\\file.exe", "\\\\?\\C:\\file.exe", "C:/a/../file.exe", "C:/a/./file.exe", "C:/a/file.exe:stream", "C:/a/NUL.exe", "C:/a/COM¹.txt", "C:/a/file.exe.", "C:/a/file.exe ", "C:/a//file.exe"):
            self.rejected(lambda: package._path(path))

    def test_reparse_attribute_and_symlink_are_rejected(self) -> None:
        self.assertTrue(package._reparse(SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=package.REPARSE)))
        self.assertTrue(package._reparse(SimpleNamespace(st_mode=stat.S_IFLNK)))
        self.assertFalse(package._reparse(SimpleNamespace(st_mode=stat.S_IFREG)))

    def test_unavailable_or_invalid_file_identity_fails_closed(self) -> None:
        self.assertEqual(package._identity(SimpleNamespace(st_dev=1, st_ino=2)), (1, 2))
        self.assertEqual(package._identity(SimpleNamespace(st_dev=0, st_ino=2)), (0, 2))
        for device, inode in ((0, 0), (1, 0), (1, None), (None, 2), (1, True), (False, 2), (1, -1)):
            self.rejected(lambda: package._identity(SimpleNamespace(st_dev=device, st_ino=inode)))

    def test_cli_failure_does_not_disclose_supplied_private_paths(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(package.main(["--unknown", "C:/private-owner/secret"]), 2)
        self.assertNotIn("private-owner", error.getvalue())
        self.assertNotIn("secret", error.getvalue())


if __name__ == "__main__":
    unittest.main()
