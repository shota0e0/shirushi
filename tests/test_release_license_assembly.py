from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid


PROJECT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from assemble_release_candidate import (
    RUNTIME_PYTHON_DISTRIBUTIONS,
    assemble_licenses,
)


class ReleaseLicenseAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = PROJECT / "tests/.runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        self.root = runtime / f"license-assembly-{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root)
        runtime = self.root.parent
        if runtime.exists() and not any(runtime.iterdir()):
            runtime.rmdir()

    def test_every_audited_distribution_has_versioned_license_material(self) -> None:
        assemble_licenses(PROJECT, self.root)
        manifest = json.loads(
            (self.root / "LICENSES/PYTHON_COMPONENTS.json").read_text(encoding="utf-8")
        )
        actual = {
            item["component"]: item["version"] for item in manifest["components"]
        }
        self.assertEqual(RUNTIME_PYTHON_DISTRIBUTIONS, actual)
        self.assertTrue(all(item["includedFiles"] for item in manifest["components"]))
        component_markdown = self.root / "LICENSES/PYTHON_COMPONENTS.md"
        self.assertTrue(component_markdown.is_file())
        markdown = component_markdown.read_text(encoding="utf-8")
        self.assertIn("| `trustmark` | `0.9.0` |", markdown)
        self.assertIn("| `six` | `1.17.0` |", markdown)

    def test_required_native_and_primary_component_material_is_assembled(self) -> None:
        assemble_licenses(PROJECT, self.root)
        required = (
            "LICENSES/c2patool/LICENSE-MIT",
            "LICENSES/c2patool/LICENSE-APACHE",
            "LICENSES/c2patool/c2patool-v0.26.60-x86_64-pc-windows-msvc-sbom.json",
            "LICENSES/Python/LICENSE.txt",
            "LICENSES/Tcl-Tk/Tcl-license.terms",
            "LICENSES/Tcl-Tk/Tk-license.terms",
            "LICENSES/PyInstaller/COPYING.txt",
            "LICENSES/TrustMark/licenses/LICENSE",
            "LICENSES/PyTorch/licenses/LICENSE",
            "LICENSES/torchvision/LICENSE",
            "LICENSES/NumPy/LICENSE.txt",
            "LICENSES/Pillow/licenses/LICENSE",
            "LICENSES/cryptography/licenses/LICENSE",
            "LICENSES/cffi/licenses/LICENSE",
            "LICENSES/pycparser/licenses/LICENSE",
            "LICENSES/OpenSSL/LICENSE-APACHE-2.0.txt",
            "LICENSES/Microsoft-Runtime/NOTICE.txt",
        )
        for relative in required:
            with self.subTest(relative=relative):
                path = self.root / relative
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
