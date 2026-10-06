"""Preview assembly preparation only; no native/vendor/registry execution."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import prepare_v02_preview as preview
from scripts import package_v02_development as package
from scripts import explorer_context_v02 as explorer
from tests.test_package_v02_development import synthetic_pe


class PreviewContractTests(unittest.TestCase):
    def test_overlay_is_explicit_per_user_nsis_no_builtin_download(self):
        result = preview.config(Path("C:/owned/package"), Path("C:/owned"))
        self.assertEqual(result["identifier"], "io.shirushi.desktop.preview")
        self.assertEqual(result["version"], "0.2.0-preview.1")
        bundle = result["bundle"]
        self.assertEqual(bundle["targets"], ["nsis"])
        self.assertTrue(bundle["active"])
        self.assertEqual(bundle["windows"]["nsis"]["installMode"], "currentUser")
        self.assertEqual(bundle["windows"]["webviewInstallMode"], {"type": "skip"})
        self.assertEqual(set(bundle["resources"].values()), {package.HELPER, package.MANIFEST, *preview.DOCUMENTS})
        self.assertNotIn("externalBin", bundle)
        base = json.loads((Path(__file__).resolve().parents[1] / "desktop/tauri.conf.json").read_text())
        self.assertFalse(base["bundle"]["active"])
        preview_base = json.loads((Path(__file__).resolve().parents[1] / "desktop/tauri.preview.conf.json").read_text())
        self.assertFalse(preview_base["bundle"]["active"])
        for name in ("productName", "version", "identifier"):
            self.assertEqual(preview_base[name], result[name])

    def test_registration_uses_existing_descriptor_not_second_product_engine(self):
        with mock.patch.object(explorer, "plan", wraps=explorer.plan) as plan:
            text = preview.registration_hook("a" * 64)
            plan.assert_called_once_with(preview.SENTINEL, "a" * 64)
        for root in explorer.ROOTS: self.assertIn(root, text)
        self.assertIn("--shirushi-explorer add --", text)
        self.assertIn("--shirushi-explorer limited_inspect --", text)
        self.assertIn(r"$INSTDIR\shirushi-desktop.exe", text)
        self.assertNotIn(preview.SENTINEL, text)
        self.assertNotIn("HKLM", text)
        self.assertNotIn("Exec", text)
        for line in text.splitlines():
            if "DeleteRegKey" in line: self.assertIn("DeleteRegKey /ifempty HKCU", line)

    def test_ownership_requires_types_counts_paths_hash_and_link_rejection(self):
        text = preview.registration_hook("b" * 64)
        self.assertIn("RegOpenKeyExW", text)
        self.assertIn("i 8, i 0x20119", text)
        self.assertIn("SymbolicLinkValue", text)
        self.assertIn("RegQueryInfoKeyW", text)
        self.assertIn("$5 != 1", text)
        self.assertIn("ShirushiDesktopSha256", text)
        self.assertIn("b" * 64, text)
        self.assertIn("Call un.ShirushiAssertRegistration", text)
        self.assertIn("RegCreateKeyExW", text)
        self.assertIn("$6 != 1", text)
        self.assertIn("Call ShirushiRequireRegistration", text)
        self.assertIn("Call un.ShirushiAssertLeaf_0_0", text)
        self.assertIn("registration removal is not proven", text)
        self.assertEqual(text, preview.registration_hook("b" * 64))

    def test_invalid_hash_rejected_before_hook(self):
        with self.assertRaises(explorer.RegistrationError): preview.registration_hook("bad")

    def test_nsis_escaping_preserves_static_argv_only(self):
        self.assertEqual(preview.nsis_string('$"\n'), '$$$\\"$\\n')

    def test_hook_has_no_vendor_elevation_network_or_automatic_launch(self):
        repo = Path(__file__).resolve().parents[1]
        hook = (repo / "desktop/installer/preview-hooks.nsh").read_text()
        self.assertIn("36247", hook)
        self.assertIn("NSIS_HOOK_PREUNINSTALL", hook)
        self.assertIn("MUI_FINISHPAGE_RUN_NOTCHECKED", hook)
        self.assertIn("--shirushi-prerequisite-check", hook)
        for denied in ("vc_redist.x64.exe", "MicrosoftEdgeWebView2RuntimeInstallerX64.exe", "ExecShell", "runas", "Reboot", "inetc::"):
            self.assertNotIn(denied, hook)

    def test_public_preview_documents_explicitly_disclaim_identity_and_production_trust(self):
        repo = Path(__file__).resolve().parents[1]
        for name in ("README.md", "DISCLAIMER.md"):
            text = (repo / name).read_text(encoding="utf-8")
            for phrase in ("v0.2", "Preview", "公開テスト", "Production Trust", "作者本人性", "著作権保有", "第三者"):
                self.assertIn(phrase, text, name)
        notes = (repo / "docs/RELEASE_NOTES_V02_PREVIEW.md").read_text(encoding="utf-8")
        for phrase in ("public test C2PA credentials", "can be used by others", "NOT production-grade identity", "author identity verification", "copyright ownership proof", "PUBLIC RELEASE NOT CREATED", "per-user", "NOT a promise"):
            self.assertIn(phrase, notes)


@unittest.skipUnless(os.name == "nt", "Windows no-clobber assembly contract")
class PreviewPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shirushi-preview-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.desktop = self.root / package.DESKTOP
        self.helper = self.root / package.HELPER
        self.desktop.write_bytes(synthetic_pe(2))
        self.helper.write_bytes(synthetic_pe(3, 2))
        self.digest = hashlib.sha256(package.prepare_manifest(self.helper)).hexdigest()

    def prepare(self, name="prepared", digest=None):
        return preview.prepare(str(self.desktop), str(self.helper), str(self.root / name), digest or self.digest)

    def test_actual_staged_bytes_manifest_and_no_native_execution(self):
        before = self.desktop.read_bytes(), self.helper.read_bytes()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("NO_EXECUTE")), mock.patch("os.system", side_effect=AssertionError("NO_SHELL")):
            result = self.prepare()
        self.assertFalse(result["installerGenerated"])
        self.assertFalse(result["nativeBuildProven"])
        self.assertFalse(result["productionTrust"])
        self.assertEqual(before, (self.desktop.read_bytes(), self.helper.read_bytes()))
        package.audit_package(self.root / "prepared/package", result["packageAudit"])
        self.assertEqual(set(p.name for p in (self.root / "prepared/package").iterdir()), package.FILES)

    def test_same_inputs_repeat_manifest_registration_and_inventory(self):
        self.prepare("one")
        self.prepare("two")
        for name in ("package/inspection-helper.manifest.json", "explorer-generated.nsh", "preview-hooks.nsh", "preparation.json"):
            self.assertEqual((self.root / "one" / name).read_bytes(), (self.root / "two" / name).read_bytes())
        # Absolute resource locations intentionally differ; logical target names do not.
        overlays = [json.loads((self.root / name / "tauri-preview.generated.json").read_text()) for name in ("one", "two")]
        self.assertEqual(set(overlays[0]["bundle"]["resources"].values()), set(overlays[1]["bundle"]["resources"].values()))

    def test_wrong_compiled_digest_cannot_publish(self):
        with self.assertRaisesRegex(package.PackageError, "COMPILED_MANIFEST_DIGEST_MISMATCH"):
            self.prepare(digest="0" * 64)
        self.assertFalse((self.root / "prepared").exists())

    def test_destination_collision_preserves_previous(self):
        self.prepare()
        before = (self.root / "prepared/preparation.json").read_bytes()
        with self.assertRaises(package.PackageError): self.prepare()
        self.assertEqual((self.root / "prepared/preparation.json").read_bytes(), before)

    def test_stage_failure_never_publishes(self):
        with mock.patch.object(preview, "registration_hook", side_effect=explorer.RegistrationError("INJECTED")):
            with self.assertRaises(explorer.RegistrationError): self.prepare()
        self.assertFalse((self.root / "prepared").exists())
        self.assertEqual(list(self.root.glob(".shirushi-preview-stage-*")), [])


if __name__ == "__main__": unittest.main()
