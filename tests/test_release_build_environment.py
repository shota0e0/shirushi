from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_release_build_environment import (
    EXPECTED_C2PATOOL_SHA256,
    check_environment,
    parse_requirements,
)
from assemble_release_candidate import RUNTIME_PYTHON_DISTRIBUTIONS


class ReleaseBuildEnvironmentTests(unittest.TestCase):
    def test_all_contract_files_use_exact_pins(self) -> None:
        runtime = parse_requirements(PROJECT / "requirements-release-runtime.txt")
        build = parse_requirements(PROJECT / "requirements-release-build.txt")
        self.assertEqual("2.14.0+cpu", runtime["torch"])
        self.assertEqual("0.29.0+cpu", runtime["torchvision"])
        self.assertEqual("0.9.0", runtime["trustmark"])
        self.assertEqual("1.17.0", runtime["six"])
        self.assertEqual("6.22.2", build["pyinstaller"])
        self.assertTrue(set(runtime).isdisjoint(build))
        assembled = {
            name.lower().replace("_", "-"): version
            for name, version in RUNTIME_PYTHON_DISTRIBUTIONS.items()
        }
        normalized_runtime = {
            name: version.removesuffix("+cpu") for name, version in runtime.items()
        }
        self.assertEqual(normalized_runtime, assembled)

    def test_test_contract_adds_no_third_party_dependency(self) -> None:
        self.assertEqual(
            {}, parse_requirements(PROJECT / "requirements-release-test.txt")
        )

    def test_windows_build_guide_covers_the_machine_checked_contract(self) -> None:
        guide = (PROJECT / "docs" / "build-windows.md").read_text(encoding="utf-8")
        for required in (
            "CPython 3.12.10",
            "pip==25.0.1",
            "requirements-release-runtime.txt",
            "requirements-release-build.txt",
            "requirements-release-test.txt",
            "scripts\\check_release_build_environment.py",
            "packaging\\Shirushi.spec",
            "scripts\\assemble_release_candidate.py",
            "TrustMarkモデルファイルが0件",
        ):
            self.assertIn(required, guide)

        c2patool_root = PROJECT / "tools" / "c2patool-0.26.60" / "c2patool"
        for name in ("c2patool.exe", "README.md", "CHANGELOG.md"):
            self.assertTrue((c2patool_root / name).is_file(), name)
        license_root = PROJECT / "packaging" / "license_sources" / "c2patool"
        for name in (
            "LICENSE-MIT",
            "LICENSE-APACHE",
            "c2patool-v0.26.60-x86_64-pc-windows-msvc-sbom.json",
        ):
            self.assertTrue((license_root / name).is_file(), name)

    def test_current_release_environment_satisfies_contract(self) -> None:
        result = check_environment(PROJECT)
        self.assertEqual([], result["failures"])
        self.assertEqual("PASS", result["status"])
        self.assertEqual(
            EXPECTED_C2PATOOL_SHA256,
            result["checks"]["c2patoolSha256"],
        )

    def test_wrong_python_version_fails_closed(self) -> None:
        with mock.patch("check_release_build_environment.sys.version_info", (3, 12, 9)):
            result = check_environment(PROJECT)
        self.assertEqual("FAIL", result["status"])
        self.assertTrue(
            any("Python must be 3.12.10" in item for item in result["failures"])
        )


if __name__ == "__main__":
    unittest.main()
