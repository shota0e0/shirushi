"""Verification-only audit negatives; no Cargo, app launch or real profile I/O."""

from pathlib import Path
import hashlib
import importlib.util
import re
import shutil
import unittest
import uuid


PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("f2c_audit", PROJECT / "scripts/verify_f2c1_ci.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class F2C1AuditTests(unittest.TestCase):
    def setUp(self):
        runtime = PROJECT / "tests/.runtime"
        runtime.mkdir(exist_ok=True)
        # Match the established test harness: inherit workspace ACLs instead of
        # tempfile's restrictive Windows ACL. Never modify an existing ACL.
        self.root = runtime / f"f2c-ci-audit-{uuid.uuid4().hex}"
        self.root.mkdir(mode=0o755)
        self.addCleanup(self.remove_owned_fixture)
        self.mapping = AUDIT.asset_mapping((PROJECT / "desktop/src/asset_stage.rs").read_text())
        for name in (*AUDIT.SIDECAR_FILES, *AUDIT.NATIVE_FILES,
                     "assets/app_icon.png", "assets/app_icon.ico",
                     *(f"web/{name}" for name in self.mapping.values())):
            dest = self.root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PROJECT / name, dest)

    def remove_owned_fixture(self):
        runtime = (PROJECT / "tests/.runtime").resolve()
        target = self.root.resolve()
        self.assertEqual(runtime, target.parent)
        self.assertTrue(target.name.startswith("f2c-ci-audit-"))
        self.assertFalse(self.root.is_symlink() or self.root.is_junction())
        shutil.rmtree(target)

    def edit(self, name, before, after):
        file = self.root / name
        original = file.read_text(encoding="utf-8")
        self.assertIn(before, original)
        file.write_text(original.replace(before, after), encoding="utf-8")

    def stage_fixture(self):
        for dest, source in self.mapping.items():
            target = self.root / "desktop/.generated/web" / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / "web" / source, target)

    def test_source_audit_does_not_build_or_stage(self):
        report = AUDIT.audit(self.root)
        self.assertEqual("PASS", report["audit"])
        self.assertEqual(29, report["webAssetCount"])
        self.assertEqual("PENDING", report["nativeManualVerification"])
        self.assertEqual("NOT READY", report["g2"])
        self.assertFalse((self.root / "desktop/.generated").exists())

    def test_missing_sidecar_resource_fails(self):
        (self.root / "src/data/personal_mark_unicode16.json").unlink()
        with self.assertRaisesRegex(ValueError, "missing resource"):
            AUDIT.audit(self.root)

    def test_unlisted_sidecar_import_fails(self):
        self.edit("src/desktop_bridge.py", "import json", "import unapproved_module")
        with self.assertRaisesRegex(ValueError, "unlisted sidecar dependency"):
            AUDIT.audit(self.root)

    def test_missing_frontend_import_fails(self):
        self.edit("web/desktop.js", '"./bootstrap.js"', '"./not-bundled.js"')
        with self.assertRaisesRegex(ValueError, "dependency missing"):
            AUDIT.audit(self.root)

    def test_expanded_permission_fails(self):
        self.edit("desktop/capabilities/main-window.json",
                  '"allow-bridge-load-personal-mark"', '"shell:allow-execute"')
        with self.assertRaisesRegex(ValueError, "expanded permissions"):
            AUDIT.audit(self.root)

    def test_bundle_enable_fails(self):
        self.edit("desktop/tauri.conf.json", '"active": false', '"active": true')
        with self.assertRaisesRegex(ValueError, "unapproved distribution"):
            AUDIT.audit(self.root)

    def test_fixed_interpreter_change_fails(self):
        self.edit("desktop/src/host.rs", ".venv-py312/Scripts/python.exe", "other/python.exe")
        with self.assertRaisesRegex(ValueError, "sidecar layout changed"):
            AUDIT.audit(self.root)

    def test_staged_audit_requires_real_directory(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            AUDIT.audit(self.root, staged=True)

    def test_exact_synthetic_staging_passes_without_native_claim(self):
        self.stage_fixture()
        report = AUDIT.audit(self.root, staged=True)
        self.assertEqual("build-staged", report["mode"])
        self.assertEqual("PENDING", report["nativeManualVerification"])

    def test_staged_extra_and_modified_bytes_fail(self):
        self.stage_fixture()
        extra = self.root / "desktop/.generated/web/dev-preview.js"
        extra.write_text("fixture", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "set mismatch"):
            AUDIT.audit(self.root, staged=True)
        extra.unlink()
        (self.root / "desktop/.generated/web/desktop.js").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "bytes mismatch"):
            AUDIT.audit(self.root, staged=True)

    def test_unsafe_and_duplicate_asset_mapping_fail(self):
        source = (self.root / "desktop/src/asset_stage.rs").read_text()
        for modified in (source.replace('"styles.css"', '"../styles.css"'),
                         source.replace('"desktop.js"', '"styles.css"')):
            with self.subTest(), self.assertRaises(ValueError):
                AUDIT.asset_mapping(modified)


class F3AModeIsolationTests(unittest.TestCase):
    """New proof mode cannot alter either existing mode or upload a runtime."""

    def setUp(self):
        self.workflow = (PROJECT / ".github/workflows/f2c1-windows-verification.yml").read_text(encoding="utf-8")

    def job(self, name):
        match = re.search(r"^  " + re.escape(name) + r":\n(.*?)(?=^  [a-z0-9_]+:\n|\Z)", self.workflow, re.M | re.S)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_existing_normal_steps_are_unchanged(self):
        steps = self.job("verify").split("    steps:\n", 1)[1]
        self.assertEqual("b09d7611f9135128ffbd97dbd94deff516c3eb658926bb28ee4baaebb46ccae9", hashlib.sha256(steps.encode()).hexdigest())
        self.assertIn("inputs.f3a_runtime_reproduction != true && inputs.first_rust_only != true &&", self.job("verify"))

    def test_existing_first_rust_steps_are_unchanged(self):
        steps = self.job("first_rust").split("    steps:\n", 1)[1]
        # Job-separating blank line is not part of the existing step payload.
        steps = steps.rstrip("\n") + "\n"
        self.assertEqual("bf02c597559a0e5c986892b8d8f0b25dfe5325ce88395166509f38af1a2f8fc6", hashlib.sha256(steps.encode()).hexdigest())
        self.assertIn("inputs.first_rust_only == true && inputs.f3a_runtime_reproduction != true", self.job("first_rust"))

    def test_new_mode_defaults_off_and_rejects_conflicting_modes(self):
        inputs = self.workflow.split("  pull_request:", 1)[0]
        self.assertRegex(inputs, r"f3a_runtime_reproduction:\n(?:.*\n){3}        default: false")
        job = self.job("f3a_runtime")
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.f3a_runtime_reproduction == true", job)
        self.assertIn('throw "Select exactly one bounded F3A mode"', job)

    def test_reproduction_has_no_runtime_upload_or_legacy_package_step(self):
        job = self.job("f3a_runtime")
        for forbidden in ("actions/upload-artifact", "package_f2c6_canary.py", "workflow_dispatch.yml", "--upgrade", "Get-Command python", "ExecutionPolicy", "Bypass"):
            self.assertNotIn(forbidden, job)
        for required in ("verify_f3a_ci_runtime.py fetch", "prepare_f3a_limited_runtime.py", "verify_f3a_ci_runtime.py assemble", "verify_f3a_ci_runtime.py prove", "--locked --offline", "native-proof.json"):
            self.assertIn(required, job)

    def test_immutable_dependency_inputs_have_exact_byte_attributes(self):
        attributes = (PROJECT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        for path in ("packaging/f3a-limited-runtime-lock.json", "packaging/f3a-limited-requirements.txt"):
            self.assertIn(path + " -text", attributes)


if __name__ == "__main__":
    unittest.main()
