from __future__ import annotations

import hashlib
from functools import partial
import json
import math
from pathlib import Path
import sys
import unittest

from PIL import Image, ImageChops


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
SCRIPTS = PROJECT / "scripts"
for entry in (SRC, SCRIPTS):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from generate_inspection_gui_smoke_fixtures import EXPECTED
from inspection_service import InspectionService
from inspection_service import LocalTrustMarkProbe
from tests.trustmark_test_support import installed_model_factory
from creator_verify import verify_contract


ROOT = PROJECT / "tests/fixtures/inspection"
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def psnr(reference: Image.Image, candidate: Image.Image) -> float:
    difference = ImageChops.difference(reference.convert("RGB"), candidate.convert("RGB"))
    error = sum((index % 256) ** 2 * count for index, count in enumerate(difference.histogram()))
    if error == 0:
        return math.inf
    mse = error / (reference.width * reference.height * 3)
    return 10 * math.log10((255**2) / mse)


class InspectionGuiSmokeFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        inspector = InspectionService(
            verifier=partial(verify_contract, trustmark_factory=installed_model_factory),
            trustmark_probe=LocalTrustMarkProbe(factory=installed_model_factory)
        )
        cls.inspections = {name: inspector.inspect(ROOT / name) for name in EXPECTED}

    def test_01_required_fixture_set_is_exact_and_small(self) -> None:
        expected_files = {*EXPECTED, "source_synthetic.png", "manifest.json"}
        self.assertEqual(expected_files, {path.name for path in ROOT.iterdir() if path.is_file()})
        self.assertTrue(all(path.stat().st_size < 3 * 1024 * 1024 for path in ROOT.iterdir() if path.is_file()))

    def test_02_source_copy_and_manifest_hashes_match(self) -> None:
        self.assertEqual(MANIFEST["source"]["sha256"], sha256_file(ROOT / "source_synthetic.png"))
        self.assertEqual("Project-generated deterministic synthetic image; no external image assets.", MANIFEST["source"]["provenance"])
        by_name = {item["filename"]: item for item in MANIFEST["fixtures"]}
        for filename in EXPECTED:
            self.assertEqual(by_name[filename]["sha256"], sha256_file(ROOT / filename))

    def test_03_all_images_are_readable_rgb_and_keep_dimensions(self) -> None:
        expected_dimensions = tuple(MANIFEST["source"]["dimensions"])
        for filename in [*EXPECTED, "source_synthetic.png"]:
            with self.subTest(filename=filename), Image.open(ROOT / filename) as image:
                image.load()
                self.assertEqual(expected_dimensions, image.size)
                self.assertEqual("RGB", image.mode)

    def test_04_clean_conversion_preserves_oriented_visual_content(self) -> None:
        with (
            Image.open(ROOT / "source_synthetic.png") as source,
            Image.open(ROOT / "plain_no_shirushi.png") as png_image,
            Image.open(ROOT / "plain_no_shirushi.jpg") as jpeg_image,
        ):
            source.load()
            png_image.load()
            jpeg_image.load()
            synthetic = source.convert("RGB")
            self.assertEqual(synthetic.tobytes(), png_image.convert("RGB").tobytes())
            self.assertGreaterEqual(psnr(synthetic, jpeg_image), 50.0)

    def test_05_valid_png_and_jpeg_map_to_complete_preview_state(self) -> None:
        for filename in ("valid_shirushi.png", "valid_shirushi.jpg"):
            with self.subTest(filename=filename):
                result = self.inspections[filename]
                self.assertEqual("DETECTED", result.cawg_status)
                self.assertEqual("DETECTED", result.c2pa_status)
                self.assertEqual("DETECTED", result.trustmark_status)
                self.assertEqual("OK", result.integrity_status)
                self.assertEqual("PREVIEW", result.signature_status)

    def test_06_plain_png_and_jpeg_map_to_no_shirushi_state(self) -> None:
        for filename in ("plain_no_shirushi.png", "plain_no_shirushi.jpg"):
            with self.subTest(filename=filename):
                result = self.inspections[filename]
                self.assertEqual("NOT_DETECTED", result.rights_status)
                self.assertEqual("NOT_DETECTED", result.c2pa_status)
                self.assertEqual("NOT_DETECTED", result.trustmark_status)
                self.assertEqual("UNKNOWN", result.integrity_status)

    def test_07_metadata_stripped_fixture_retains_only_trustmark_identifier(self) -> None:
        result = self.inspections["metadata_stripped_trustmark_present.png"]
        self.assertEqual("NOT_DETECTED", result.c2pa_status)
        self.assertEqual("UNKNOWN", result.cawg_status)
        self.assertEqual("DETECTED", result.trustmark_status)
        self.assertEqual("VALID_UNBOUND", result.trustmark_payload_status)
        self.assertEqual("IDENTIFIER_RECOVERED", result.durable_recovery)

    def test_08_c2pa_failure_fixture_is_readable_and_not_missing(self) -> None:
        result = self.inspections["c2pa_verification_failure.png"]
        self.assertEqual("VERIFICATION_FAILED", result.c2pa_status)
        self.assertEqual("VERIFICATION_FAILED", result.integrity_status)
        self.assertEqual("C2PA_ASSET_DATA_HASH_MISMATCH", result.technical_details["reasonCode"])

    def test_09_measured_states_match_frozen_manifest_and_expectations(self) -> None:
        manifest_by_name = {item["filename"]: item["inspection"] for item in MANIFEST["fixtures"]}
        for filename, expected in EXPECTED.items():
            actual = self.inspections[filename]
            actual_fields = {
                "ai_training_use": actual.ai_training_use,
                "ai_inference_use": actual.ai_inference_use,
                "rights_status": actual.rights_status,
                "cawg_status": actual.cawg_status,
                "c2pa_status": actual.c2pa_status,
                "trustmark_status": actual.trustmark_status,
                "trustmark_payload_status": actual.trustmark_payload_status,
                "integrity_status": actual.integrity_status,
                "signature_status": actual.signature_status,
                "durable_recovery": actual.durable_recovery,
                "reason_code": actual.technical_details.get("reasonCode"),
                "warnings": list(actual.warnings),
            }
            self.assertEqual(manifest_by_name[filename], actual_fields)
            for key, value in expected.items():
                self.assertEqual(value, actual_fields[key], f"{filename}: {key}")


if __name__ == "__main__":
    unittest.main()
