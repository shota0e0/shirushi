from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


PROJECT = Path(__file__).resolve().parents[1]
INSPECTION = PROJECT / "tests/fixtures/inspection"

# Product identity, documentation, demo art and test inputs are separate classes.
# Exact paths only: no blanket assets/ or fixture-directory exemption.
PRODUCT_BRANDING_ASSETS = frozenset({
    "assets/app_icon.png",
    "assets/app_icon.ico",
    "assets/branding/shirushi-app-icon-master.png",
})
DOCUMENTATION_ASSETS = frozenset({"docs/images/windows-smartscreen-guide.png"})
DEMO_ASSETS = frozenset({"web-canary/assets/demo-art.svg"})
TEST_FIXTURES = frozenset({
    "testdata/e2e/clean_fixture.jpg",
    "testdata/e2e/clean_fixture.png",
    "testdata/jpeg_realworld/camera_orientation_01.jpg",
    "testdata/jpeg_realworld/camera_orientation_02.jpg",
    "testdata/jpeg_realworld/camera_orientation_03.jpg",
    "testdata/jpeg_realworld/camera_orientation_06_icc.jpg",
    "testdata/jpeg_realworld/camera_orientation_08.jpg",
    "testdata/tamper/tamper_cawg_rights.png",
    "testdata/tamper/tamper_claim_reference.png",
    "testdata/tamper/tamper_pixel.png",
    "testdata/tamper/tamper_signature.png",
    "testdata/tamper/tamper_soft_binding.png",
    "tests/fixtures/inspection/c2pa_verification_failure.png",
    "tests/fixtures/inspection/metadata_stripped_trustmark_present.png",
    "tests/fixtures/inspection/plain_no_shirushi.jpg",
    "tests/fixtures/inspection/plain_no_shirushi.png",
    "tests/fixtures/inspection/source_synthetic.png",
    "tests/fixtures/inspection/valid_shirushi.jpg",
    "tests/fixtures/inspection/valid_shirushi.png",
})
IMAGE_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".tif", ".tiff", ".ico", ".svg",
})
ASSET_CLASSES = {
    "PRODUCT BRANDING ASSET": PRODUCT_BRANDING_ASSETS,
    "DOCUMENTATION ASSET": DOCUMENTATION_ASSETS,
    "DEMO ASSET": DEMO_ASSETS,
    "TEST FIXTURE": TEST_FIXTURES,
}


def public_candidate_paths() -> set[str]:
    # Include untracked candidates before the first public commit; do not stage.
    return set(subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=PROJECT, capture_output=True, check=True, text=True, encoding="utf-8",
    ).stdout.splitlines())


def asset_classification(path: str) -> str | None:
    return next((name for name, paths in ASSET_CLASSES.items() if path in paths), None)


class PublicFixtureGovernanceTests(unittest.TestCase):
    def test_redistribution_unknown_sources_are_not_tracked(self) -> None:
        tracked = public_candidate_paths()

        self.assertNotIn("tests/fixtures/inspection/source_cat.jfif", tracked)
        self.assertNotIn("input/original/baseline_adobe_cc.png", tracked)

    def test_inspection_fixture_group_is_synthetic_and_self_describing(self) -> None:
        manifest = json.loads((INSPECTION / "manifest.json").read_text(encoding="utf-8"))
        expected_files = {
            "source_synthetic.png",
            "plain_no_shirushi.png",
            "plain_no_shirushi.jpg",
            "valid_shirushi.png",
            "valid_shirushi.jpg",
            "metadata_stripped_trustmark_present.png",
            "c2pa_verification_failure.png",
            "manifest.json",
        }

        self.assertEqual(expected_files, {path.name for path in INSPECTION.iterdir() if path.is_file()})
        self.assertEqual(
            "Project-generated deterministic synthetic image; no external image assets.",
            manifest["source"]["provenance"],
        )
        self.assertEqual("scripts/creator_repeatability.py", manifest["source"]["generator"])

    def test_public_candidate_image_assets_are_exactly_classified(self) -> None:
        candidate = public_candidate_paths()
        images = {path for path in candidate if Path(path).suffix.lower() in IMAGE_SUFFIXES}
        classified = set().union(*ASSET_CLASSES.values())
        self.assertEqual(classified, images)
        self.assertEqual(24, len(classified))

    def test_asset_categories_do_not_overlap(self) -> None:
        assigned = [path for paths in ASSET_CLASSES.values() for path in paths]
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(3, len(PRODUCT_BRANDING_ASSETS))
        self.assertEqual(19, len(TEST_FIXTURES))

    def test_unknown_assets_and_fixture_siblings_are_not_allowed(self) -> None:
        for path in (
            "assets/unreviewed.png",
            "assets/branding/private.png",
            "docs/images/unreviewed.png",
            "web-canary/assets/private.svg",
            "testdata/e2e/private.png",
            "tests/fixtures/inspection/private.jpg",
            "assets/app_icon.png/extra.png",
        ):
            with self.subTest(path=path):
                self.assertIsNone(asset_classification(path))
        for path in PRODUCT_BRANDING_ASSETS:
            self.assertEqual("PRODUCT BRANDING ASSET", asset_classification(path))
            self.assertNotIn(path, TEST_FIXTURES)


if __name__ == "__main__":
    unittest.main()
