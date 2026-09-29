"""F2C.6 package creation/audit tests; no Cargo build or native execution."""

from __future__ import annotations

import importlib.util
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import sys
import tomllib
import unittest
from contextlib import redirect_stdout
from unittest import mock
import uuid


PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "f2c6_package", PROJECT / "scripts/package_f2c6_canary.py"
)
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)
SHA = "1" * 40


def synthetic_pe(machine: int = 0x8664, extra: bytes = b"") -> bytes:
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, machine)
    return bytes(data) + extra


class F2C6CanaryTests(unittest.TestCase):
    def setUp(self):
        runtime = PROJECT / "tests/.runtime"
        runtime.mkdir(exist_ok=True)
        self.case = runtime / f"f2c6-package-{uuid.uuid4().hex}"
        self.case.mkdir(mode=0o755)
        self.source = self.case / "source"
        self.source.mkdir()
        self.registry = self.case / "registry" / "dep-1.0.0"
        self.registry.mkdir(parents=True)
        self.addCleanup(self.remove_owned_fixture)
        self._write_source_fixture()

    def remove_owned_fixture(self):
        runtime = (PROJECT / "tests/.runtime").resolve()
        target = self.case.resolve()
        self.assertEqual(runtime, target.parent)
        self.assertTrue(target.name.startswith("f2c6-package-"))
        self.assertFalse(self.case.is_symlink() or self.case.is_junction())
        shutil.rmtree(target)

    def write(self, root: Path, name: str, data: bytes | str):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)

    def _write_source_fixture(self):
        for source_name, _ in set(PACKAGE.SOURCE_COPIES.values()):
            if source_name == "scripts/canary/demo-personal-mark-v2.json":
                data = json.dumps(PACKAGE.NIKI_FIXTURE, ensure_ascii=False) + "\n"
            elif source_name == "LICENSE":
                data = "Synthetic project MIT license\n"
            else:
                data = f"Synthetic public source: {source_name}\n"
            self.write(self.source, source_name, data)
        self.write(self.source, "desktop/Cargo.toml", "[package]\nname='shirushi-desktop'\nversion='0.2.0'\n")
        self.write(
            self.source,
            "desktop/Cargo.lock",
            """version = 3

[[package]]
name = "shirushi-desktop"
version = "0.2.0"

[[package]]
name = "fake-dependency"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
""",
        )
        self.write(self.registry, "Cargo.toml", "[package]\nname='fake-dependency'\nversion='1.0.0'\n")
        self.write(self.registry, "LICENSE-MIT", "Synthetic dependency MIT license\n")
        root_id = "path+file:///fixture/desktop#shirushi-desktop@0.2.0"
        dep_id = "registry+https://github.com/rust-lang/crates.io-index#fake-dependency@1.0.0"
        metadata = {
            "packages": [
                {
                    "id": root_id,
                    "name": "shirushi-desktop",
                    "version": "0.2.0",
                    "source": None,
                    "manifest_path": str((self.source / "desktop/Cargo.toml").resolve()),
                    "license": "MIT",
                    "license_file": None,
                },
                {
                    "id": dep_id,
                    "name": "fake-dependency",
                    "version": "1.0.0",
                    "source": "registry+https://github.com/rust-lang/crates.io-index",
                    "manifest_path": str((self.registry / "Cargo.toml").resolve()),
                    "license": "MIT",
                    "license_file": None,
                },
            ],
            "resolve": {
                "root": root_id,
                "nodes": [
                    {"id": root_id, "deps": [{"pkg": dep_id}]},
                    {"id": dep_id, "deps": []},
                ],
            },
        }
        self.metadata = self.case / "cargo-metadata.json"
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        self.rust_notice = self.case / "COPYRIGHT-library.html"
        self.rust_notice.write_text("Synthetic Rust standard library notice\n", encoding="utf-8")
        self.executable = self.case / "shirushi-desktop.exe"
        self.executable.write_bytes(synthetic_pe())

    @staticmethod
    def source_audit(_root: Path):
        return {"audit": "PASS", "webAssetCount": 29, "sidecarResourceCount": 10}

    def create(self, name: str = "package") -> Path:
        output = self.case / name
        result = PACKAGE.create_package(
            self.source,
            self.executable,
            output,
            self.metadata,
            self.rust_notice,
            repository="example/shirushi",
            event_name="pull_request",
            tested_sha=SHA,
            head_sha="2" * 40,
            base_sha="3" * 40,
            source_audit=self.source_audit,
        )
        self.assertEqual("PASS", result["audit"])
        return output

    def copy_package(self, original: Path, name: str) -> Path:
        destination = self.case / name
        shutil.copytree(original, destination)
        return destination

    def prepare_missing_license_crates(self):
        expected_names = {
            "alloc-stdlib", "defmt-parser", "selectors", "unic-char-property",
            "unic-char-range", "unic-common", "unic-ucd-ident",
            "unic-ucd-version", "webview2-com", "webview2-com-macros",
            "webview2-com-sys",
        }
        self.assertEqual(expected_names, {key[0] for key in PACKAGE.LICENSE_SUPPLEMENTS})
        self.assertEqual(11, len(PACKAGE.LICENSE_SUPPLEMENTS))
        lock_path = PROJECT / "desktop/Cargo.lock"
        lock = PACKAGE._lock_index(lock_path)
        self.write(self.source, "desktop/Cargo.lock", lock_path.read_bytes())
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        root = metadata["packages"][0]
        root_id = root["id"]
        metadata["packages"] = [root]
        metadata["resolve"]["nodes"] = [{"id": root_id, "deps": []}]
        package_roots = {}
        source_paths = set()
        for (name, version, checksum, expression), (repo, commit, path_in_vcs, materials) in sorted(
            PACKAGE.LICENSE_SUPPLEMENTS.items()
        ):
            self.assertEqual(checksum, lock[(name, version, PACKAGE.CRATES_IO_SOURCE)]["checksum"])
            crate_root = self.case / "registry" / f"{name}-{version}"
            crate_root.mkdir()
            self.write(crate_root, "Cargo.toml", f"[package]\nname='{name}'\nversion='{version}'\n")
            vcs = {"git": {"sha1": commit}}
            if path_in_vcs is not None:
                vcs["path_in_vcs"] = path_in_vcs
            self.write(crate_root, ".cargo_vcs_info.json", json.dumps(vcs))
            package_roots[name] = crate_root
            package_id = f"{PACKAGE.CRATES_IO_SOURCE}#{name}@{version}"
            metadata["packages"].append({
                "id": package_id, "name": name, "version": version,
                "source": PACKAGE.CRATES_IO_SOURCE,
                "repository": repo,
                "manifest_path": str((crate_root / "Cargo.toml").resolve()),
                "license": expression, "license_file": None,
            })
            metadata["resolve"]["nodes"][0]["deps"].append({"pkg": package_id})
            metadata["resolve"]["nodes"].append({"id": package_id, "deps": []})
            for file_name, source_path, digest, source_url in materials:
                self.assertEqual(file_name, Path(source_path).name)
                raw = (PROJECT / source_path).read_bytes()
                self.assertEqual(digest, PACKAGE.sha256(raw))
                if name == "selectors":
                    self.assertEqual(
                        "https://www.mozilla.org/media/MPL/2.0/index.f75d2927d3c1.txt",
                        source_url,
                    )
                else:
                    prefix = repo.replace(
                        "https://github.com/", "https://api.github.com/repos/"
                    ) + "/git/blobs/"
                    blob = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
                    self.assertEqual(prefix + hashlib.sha1(blob).hexdigest(), source_url)
                if source_path not in source_paths:
                    self.write(self.source, source_path, raw)
                    source_paths.add(source_path)
        actual_paths = {
            path.relative_to(PROJECT).as_posix()
            for path in (PROJECT / "packaging/license_sources/f2c6-rust").rglob("*")
            if path.is_file()
        }
        self.assertEqual(source_paths, actual_paths)
        self.assertEqual(8, len(source_paths))
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        return metadata, package_roots, source_paths

    def test_exact_eleven_license_supplements_and_artifact_manifest(self):
        _, _, source_paths = self.prepare_missing_license_crates()
        report = PACKAGE.preflight_dependency_licenses(self.source, self.metadata)
        self.assertEqual("PASS", report["audit"])
        self.assertEqual(0, report["missingLicenseTextCount"])
        document, texts = PACKAGE.collect_dependency_licenses(self.source, self.metadata)
        inventory = json.loads(document)
        self.assertEqual(12, inventory["packageCount"])
        by_name = {item["name"]: item for item in inventory["packages"]}
        for (name, _, _, _), (_, _, _, materials) in PACKAGE.LICENSE_SUPPLEMENTS.items():
            self.assertEqual(
                {file_name for file_name, _, _, _ in materials},
                {item["sourceFileName"] for item in by_name[name]["materials"]},
            )
            self.assertEqual(
                {digest for _, _, digest, _ in materials},
                {item["sha256"] for item in by_name[name]["materials"]},
            )
        self.assertEqual(8, len(source_paths))
        self.assertEqual(9, len(texts))  # eight supplements plus the project LICENSE
        output = self.create("supplemented-package")
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        names = {item["path"] for item in manifest["files"]}
        self.assertEqual(
            PACKAGE.CORE_FILES | PACKAGE.GENERATED_FILES | set(texts),
            names,
        )
        self.assertEqual("PASS", PACKAGE.audit_package(output)["audit"])

    def test_license_supplement_provenance_and_hash_fail_closed(self):
        metadata, roots, _ = self.prepare_missing_license_crates()
        defmt = next(item for item in metadata["packages"] if item["name"] == "defmt-parser")
        defmt["repository"] = "https://github.com/not-the-owner/defmt"
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "repository provenance mismatch"):
            PACKAGE.preflight_dependency_licenses(self.source, self.metadata)
        defmt["repository"] = "https://github.com/knurling-rs/defmt"
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        marker = roots["defmt-parser"] / ".cargo_vcs_info.json"
        marker.write_text('{"git":{"sha1":"' + "0" * 40 + '"},"path_in_vcs":"parser"}',
                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "VCS provenance mismatch"):
            PACKAGE.preflight_dependency_licenses(self.source, self.metadata)
        expected_commit = PACKAGE.LICENSE_SUPPLEMENTS[next(
            key for key in PACKAGE.LICENSE_SUPPLEMENTS if key[0] == "defmt-parser"
        )][1]
        marker.write_text(json.dumps({
            "git": {"sha1": expected_commit}, "path_in_vcs": "parser"
        }), encoding="utf-8")
        copied = self.source / "packaging/license_sources/f2c6-rust/defmt/LICENSE-MIT"
        copied.write_bytes(copied.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "supplemental license hash mismatch"):
            PACKAGE.preflight_dependency_licenses(self.source, self.metadata)
        self.write(self.source, "packaging/license_sources/f2c6-rust/defmt/LICENSE-MIT",
                   (PROJECT / "packaging/license_sources/f2c6-rust/defmt/LICENSE-MIT").read_bytes())
        defmt["license"] = "MIT"
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        report = PACKAGE.preflight_dependency_licenses(self.source, self.metadata)
        self.assertEqual("FAIL", report["audit"])
        self.assertEqual(["defmt-parser"], [item["name"] for item in report["missingLicenseTexts"]])
        defmt["license"] = "MIT OR Apache-2.0"
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        lock_path = self.source / "desktop/Cargo.lock"
        lock_text = lock_path.read_text(encoding="utf-8")
        defmt_checksum = next(
            key[2] for key in PACKAGE.LICENSE_SUPPLEMENTS if key[0] == "defmt-parser"
        )
        self.assertEqual(1, lock_text.count(defmt_checksum))
        lock_path.write_text(lock_text.replace(defmt_checksum, "f" * 64), encoding="utf-8")
        report = PACKAGE.preflight_dependency_licenses(self.source, self.metadata)
        self.assertEqual("FAIL", report["audit"])
        self.assertEqual(["defmt-parser"], [item["name"] for item in report["missingLicenseTexts"]])

    def test_native_license_material_takes_precedence_over_supplement(self):
        _, roots, _ = self.prepare_missing_license_crates()
        native = b"Native crate license text\n"
        self.write(roots["alloc-stdlib"], "LICENSE", native)
        report = PACKAGE.preflight_dependency_licenses(self.source, self.metadata)
        self.assertEqual("PASS", report["audit"])
        document, texts = PACKAGE.collect_dependency_licenses(self.source, self.metadata)
        by_name = {item["name"]: item for item in json.loads(document)["packages"]}
        self.assertEqual(["LICENSE"], [
            item["sourceFileName"] for item in by_name["alloc-stdlib"]["materials"]
        ])
        self.assertIn(native, texts.values())

    def test_positive_exact_package_and_manifest(self):
        actual_manifest = tomllib.loads((PROJECT / "desktop/Cargo.toml").read_text(encoding="utf-8"))
        self.assertEqual("MIT", actual_manifest["package"].get("license"))
        self.assertEqual(("LICENSE", "project-license"), PACKAGE.SOURCE_COPIES["LICENSE"])
        output = self.create()
        report = PACKAGE.audit_package(output)
        self.assertEqual("PASS", report["audit"])
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        names = {entry["path"] for entry in manifest["files"]}
        self.assertEqual(manifest["fileCount"], len(names))
        self.assertTrue(PACKAGE.CORE_FILES | PACKAGE.GENERATED_FILES <= names)
        dependency_names = names - PACKAGE.CORE_FILES - PACKAGE.GENERATED_FILES
        self.assertTrue(dependency_names)
        self.assertTrue(all(PACKAGE.DEPENDENCY_TEXT.fullmatch(name)
                            for name in dependency_names))
        self.assertNotIn("manifest.json", names)
        self.assertNotIn("SHA256SUMS", names)
        license_entry = next(entry for entry in manifest["files"] if entry["path"] == "LICENSE")
        self.assertEqual("project-license", license_entry["classification"])
        self.assertEqual("LICENSE", license_entry["source"]["identifier"])
        self.assertEqual((self.source / "LICENSE").read_bytes(), (output / "LICENSE").read_bytes())
        sums = (output / "SHA256SUMS").read_text(encoding="ascii")
        self.assertIn("  manifest.json\n", sums)
        self.assertNotIn("  SHA256SUMS\n", sums)
        self.assertEqual(PACKAGE.NIKI_FIXTURE, json.loads((
            output / "demo-profile/Shirushi/personal-mark/personal-mark-v2.json"
        ).read_text(encoding="utf-8")))

    def test_license_preflight_normal_pass_and_workflow_order(self):
        metadata_document = json.loads(self.metadata.read_text(encoding="utf-8"))
        metadata_document["packages"][1]["license"] = (
            "(MIT OR Apache-2.0) AND Unicode-3.0"
        )
        self.metadata.write_text(json.dumps(metadata_document), encoding="utf-8")
        report = PACKAGE.preflight_dependency_licenses(self.source, self.metadata)
        self.assertEqual({
            "schemaVersion", "diagnostic", "audit", "reason", "target",
            "resolvedPackageCount", "missingLicenseTextCount", "missingLicenseTexts",
        }, set(report))
        self.assertEqual("PASS", report["audit"])
        self.assertEqual(2, report["resolvedPackageCount"])
        self.assertEqual(0, report["missingLicenseTextCount"])
        self.assertEqual([], report["missingLicenseTexts"])

        workflow = (PROJECT / ".github/workflows/f2c1-windows-verification.yml").read_text(
            encoding="utf-8"
        )
        fetch = workflow.index("id: cargo_fetch")
        metadata = workflow.index("id: canary_metadata")
        preflight = workflow.index("id: canary_license_preflight")
        native_test = workflow.index("id: cargo_test")
        self.assertLess(fetch, metadata)
        self.assertLess(metadata, preflight)
        self.assertLess(preflight, native_test)
        self.assertEqual(1, workflow.count("preflight-licenses"))

    def test_license_preflight_reports_all_missing_sorted_and_cli_fails_once(self):
        (self.registry / "LICENSE-MIT").unlink()
        second_root = self.case / "registry" / "alpha-dependency-2.0.0"
        second_root.mkdir()
        self.write(second_root, "Cargo.toml", "[package]\nname='alpha-dependency'\nversion='2.0.0'\n")
        lock_text = (self.source / "desktop/Cargo.lock").read_text(encoding="utf-8")
        lock_text += """
[[package]]
name = "alpha-dependency"
version = "2.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
"""
        (self.source / "desktop/Cargo.lock").write_text(lock_text, encoding="utf-8")
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        second_id = (
            "registry+https://github.com/rust-lang/crates.io-index"
            "#alpha-dependency@2.0.0"
        )
        metadata["packages"].append({
            "id": second_id,
            "name": "alpha-dependency",
            "version": "2.0.0",
            "source": PACKAGE.CRATES_IO_SOURCE,
            "manifest_path": str((second_root / "Cargo.toml").resolve()),
            "license": "Apache-2.0 OR MIT",
            "license_file": None,
        })
        metadata["resolve"]["nodes"][0]["deps"].append({"pkg": second_id})
        metadata["resolve"]["nodes"].append({"id": second_id, "deps": []})
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")

        report = PACKAGE.preflight_dependency_licenses(self.source, self.metadata)
        self.assertEqual("FAIL", report["audit"])
        self.assertEqual(2, report["missingLicenseTextCount"])
        self.assertEqual(
            ["alpha-dependency", "fake-dependency"],
            [item["name"] for item in report["missingLicenseTexts"]],
        )
        self.assertEqual(
            {"name", "version", "sourceId", "lockChecksum", "licenseExpression"},
            set(report["missingLicenseTexts"][0]),
        )
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn(str(self.case), serialized)
        self.assertLessEqual(len(serialized.encode("ascii")), PACKAGE.MAX_PREFLIGHT_OUTPUT_BYTES)

        output = io.StringIO()
        argv = [
            "package_f2c6_canary.py", "preflight-licenses",
            "--source-root", str(self.source),
            "--cargo-metadata", str(self.metadata),
        ]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(output):
            self.assertEqual(1, PACKAGE.main())
        lines = output.getvalue().splitlines()
        self.assertEqual(1, len(lines))
        self.assertEqual(report, json.loads(lines[0]))

    def test_license_preflight_missing_declaration_and_malformed_metadata_error(self):
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        metadata["packages"][1]["license"] = None
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no declared license: fake-dependency 1.0.0"):
            PACKAGE.preflight_dependency_licenses(self.source, self.metadata)

        metadata["packages"][1]["license"] = "(MIT OR Apache-2.0)\nPRIVATE"
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "malformed license expression"):
            PACKAGE.preflight_dependency_licenses(self.source, self.metadata)

        self.metadata.write_text('{"packages": "not-a-list"}', encoding="utf-8")
        output = io.StringIO()
        argv = [
            "package_f2c6_canary.py", "preflight-licenses",
            "--source-root", str(self.source),
            "--cargo-metadata", str(self.metadata),
        ]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(output):
            self.assertEqual(2, PACKAGE.main())
        report = json.loads(output.getvalue())
        self.assertEqual("ERROR", report["audit"])
        self.assertEqual(0, report["missingLicenseTextCount"])
        self.assertEqual([], report["missingLicenseTexts"])
        self.assertNotIn(str(self.case), output.getvalue())

        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        (self.source / "desktop/Cargo.lock").write_text("not valid TOML =", encoding="utf-8")
        output = io.StringIO()
        with mock.patch.object(sys, "argv", argv), redirect_stdout(output):
            self.assertEqual(2, PACKAGE.main())
        report = json.loads(output.getvalue())
        self.assertEqual("ERROR", report["audit"])
        self.assertEqual("invalid Cargo.lock", report["reason"])

    def test_missing_extra_tamper_and_extra_executable_fail(self):
        original = self.create()
        missing = self.copy_package(original, "missing")
        (missing / "README.md").unlink()
        with self.assertRaisesRegex(ValueError, "set mismatch|missing source"):
            PACKAGE.audit_package(missing)

        extra = self.copy_package(original, "extra")
        self.write(extra, "unexpected.txt", "extra\n")
        with self.assertRaisesRegex(ValueError, "set mismatch"):
            PACKAGE.audit_package(extra)

        tampered = self.copy_package(original, "tampered")
        (tampered / "README.md").write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "size mismatch|hash mismatch"):
            PACKAGE.audit_package(tampered)

        executable = self.copy_package(original, "extra-exe")
        self.write(executable, "second.exe", synthetic_pe())
        with self.assertRaisesRegex(ValueError, "set mismatch|executable"):
            PACKAGE.audit_package(executable)

    def test_path_traversal_case_collision_and_cache_names_fail(self):
        for names, reason in (
            (["../escape"], "unsafe"),
            (["README.md", "readme.md"], "case-colliding"),
            (["cache/file.txt"], "forbidden"),
            (["debug.pdb"], "forbidden"),
        ):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, reason):
                PACKAGE._unique_names(names)

    def test_bad_manifest_and_checksum_coverage_fail(self):
        original = self.create()
        bad = self.copy_package(original, "bad-manifest")
        manifest = json.loads((bad / "manifest.json").read_text(encoding="utf-8"))
        manifest["artifactType"] = "RELEASE"
        (bad / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "artifact type"):
            PACKAGE.audit_package(bad)

        bad_sums = self.copy_package(original, "bad-sums")
        lines = (bad_sums / "SHA256SUMS").read_text(encoding="ascii").splitlines()
        (bad_sums / "SHA256SUMS").write_text("\n".join(lines[:-1]) + "\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            PACKAGE.audit_package(bad_sums)

    def test_manifest_secret_unknown_field_and_utf16_executable_secret_fail(self):
        original = self.create()
        bad = self.copy_package(original, "manifest-secret")
        manifest_path = bad / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["privateBuildPath"] = r"C:\Users\alice\private\checkout"
        manifest_data = PACKAGE._json_bytes(manifest)
        manifest_path.write_bytes(manifest_data)
        sums_path = bad / "SHA256SUMS"
        lines = sums_path.read_text(encoding="ascii").splitlines()
        lines = [
            f"{PACKAGE.sha256(manifest_data)}  manifest.json" if line.endswith("  manifest.json") else line
            for line in lines
        ]
        sums_path.write_text("\n".join(lines) + "\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "personal/source path|manifest fields"):
            PACKAGE.audit_package(bad)

        bad_source = self.copy_package(original, "manifest-source-hash")
        source_manifest_path = bad_source / "manifest.json"
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        readme_entry = next(entry for entry in source_manifest["files"]
                            if entry["path"] == "README.md")
        readme_entry["source"]["sha256"] = "f" * 64
        source_manifest_data = PACKAGE._json_bytes(source_manifest)
        source_manifest_path.write_bytes(source_manifest_data)
        source_sums_path = bad_source / "SHA256SUMS"
        source_lines = source_sums_path.read_text(encoding="ascii").splitlines()
        source_lines = [
            f"{PACKAGE.sha256(source_manifest_data)}  manifest.json"
            if line.endswith("  manifest.json") else line
            for line in source_lines
        ]
        source_sums_path.write_text("\n".join(source_lines) + "\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "bad source provenance: README.md"):
            PACKAGE.audit_package(bad_source)

        marker = "-----BEGIN PRIVATE KEY-----".encode("utf-16le")
        self.executable.write_bytes(synthetic_pe(extra=marker))
        with self.assertRaisesRegex(ValueError, "secret-like"):
            self.create("utf16-secret")

    def test_linked_roots_are_rejected_before_canonicalization(self):
        package = self.create()
        with mock.patch.object(PACKAGE, "_linked", side_effect=lambda path: path == package):
            with self.assertRaisesRegex(ValueError, "invalid package directory"):
                PACKAGE.audit_package(package)
        with mock.patch.object(PACKAGE, "_linked", side_effect=lambda path: path == self.source):
            with self.assertRaisesRegex(ValueError, "invalid source root"):
                PACKAGE.create_package(
                    self.source, self.executable, self.case / "linked-source",
                    self.metadata, self.rust_notice,
                    repository="example/shirushi", event_name="pull_request",
                    tested_sha=SHA, head_sha=SHA, base_sha=SHA,
                    source_audit=self.source_audit,
                )

    def test_secret_and_private_source_path_fail_before_output(self):
        for content in (
            "-----BEGIN PRIVATE KEY-----\n",
            r"C:\Users\alice\private\file.txt" + "\n",
            r"C:\dev\private-checkout\file.rs" + "\n",
        ):
            with self.subTest(content=content):
                readme = self.source / "scripts/canary/README.md"
                original = readme.read_bytes()
                readme.write_text(content, encoding="utf-8")
                output = self.case / f"rejected-{uuid.uuid4().hex}"
                with self.assertRaisesRegex(ValueError, "secret-like|personal/source"):
                    PACKAGE.create_package(
                        self.source, self.executable, output, self.metadata, self.rust_notice,
                        repository="example/shirushi", event_name="workflow_dispatch",
                        tested_sha=SHA, head_sha=SHA, base_sha=None,
                        source_audit=self.source_audit,
                    )
                self.assertFalse(output.exists())
                readme.write_bytes(original)

    def test_wrong_fixture_and_non_amd64_pe_fail(self):
        fixture = self.source / "scripts/canary/demo-personal-mark-v2.json"
        fixture.write_text(json.dumps({**PACKAGE.NIKI_FIXTURE, "text": "Someone Else"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Niki"):
            self.create("wrong-fixture")
        fixture.write_text(json.dumps(PACKAGE.NIKI_FIXTURE), encoding="utf-8")
        self.executable.write_bytes(synthetic_pe(machine=0x014C))
        with self.assertRaisesRegex(ValueError, "AMD64"):
            self.create("wrong-machine")

    def test_output_must_be_fresh_and_outside_source(self):
        existing = self.case / "existing"
        existing.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            PACKAGE.create_package(
                self.source, self.executable, existing, self.metadata, self.rust_notice,
                repository="example/shirushi", event_name="pull_request",
                tested_sha=SHA, head_sha=SHA, base_sha=SHA,
                source_audit=self.source_audit,
            )
        inside = self.source / "artifact"
        with self.assertRaisesRegex(ValueError, "outside source"):
            PACKAGE.create_package(
                self.source, self.executable, inside, self.metadata, self.rust_notice,
                repository="example/shirushi", event_name="pull_request",
                tested_sha=SHA, head_sha=SHA, base_sha=SHA,
                source_audit=self.source_audit,
            )

    def test_unknown_license_and_missing_license_text_fail_closed(self):
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        metadata["packages"][0]["license"] = None
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no declared license: shirushi-desktop 0.2.0"):
            self.create("missing-root-license")
        metadata["packages"][0]["license"] = "unknown"
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown license: shirushi-desktop 0.2.0"):
            self.create("unknown-root-license")
        metadata["packages"][0]["license"] = "MIT"
        metadata["packages"][1]["license"] = None
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no declared license"):
            self.create("unknown-license")
        metadata["packages"][1]["license"] = "MIT"
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        (self.registry / "LICENSE-MIT").unlink()
        with self.assertRaisesRegex(ValueError, "no license/notice text"):
            self.create("missing-license")

    def test_metadata_not_in_lock_and_manifest_tamper_fail(self):
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        metadata["packages"][1]["version"] = "9.9.9"
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not locked"):
            self.create("not-locked")


if __name__ == "__main__":
    unittest.main()
