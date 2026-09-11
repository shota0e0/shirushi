"""Build public GUI inspection fixtures from the project synthetic image."""

from __future__ import annotations

import argparse
from functools import partial
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import uuid
import urllib.error

from PIL import Image, ImageChops, ImageOps
import trustmark


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
SCRIPTS = PROJECT / "scripts"
for entry in (SRC, SCRIPTS):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from creator_service import CreatorRequest, CreatorService
from creator_e2e import run_pipeline
from creator_tamper_rejection import make_pixel_tamper
from creator_verify import verify_contract
from inspection_service import InspectionResult, InspectionService, LocalTrustMarkProbe
from trustmark_model_manager import TrustMarkFactory, TrustMarkModelManager


DEFAULT_OUTPUT = PROJECT / "tests/fixtures/inspection"
SOURCE = PROJECT / "testdata/e2e/clean_fixture.png"
SOURCE_SHA256 = "DC39EE820F6A4CAC0138FBDF4313D54E8BF6D2763FE2505E3E33E711CC840418"


def installed_model_factory():
    """Use only the already-installed package models; fixture builds stay offline."""
    model_directory = Path(trustmark.__file__).resolve().parent / "models"
    manager = TrustMarkModelManager(
        cache_directory=model_directory,
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("fixture generation must not access the network")
        ),
    )
    return TrustMarkFactory(manager).create()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def decoded_psnr(reference: Image.Image, candidate: Image.Image) -> float | str | None:
    left = reference.convert("RGB")
    right = candidate.convert("RGB")
    if left.size != right.size:
        return None
    histogram = ImageChops.difference(left, right).histogram()
    squared_error = sum((index % 256) ** 2 * count for index, count in enumerate(histogram))
    if squared_error == 0:
        return "INF"
    mse = squared_error / (left.width * left.height * 3)
    return 10 * math.log10((255**2) / mse)


def inspection_record(result: InspectionResult) -> dict[str, object]:
    return {
        "ai_training_use": result.ai_training_use,
        "ai_inference_use": result.ai_inference_use,
        "rights_status": result.rights_status,
        "cawg_status": result.cawg_status,
        "c2pa_status": result.c2pa_status,
        "trustmark_status": result.trustmark_status,
        "trustmark_payload_status": result.trustmark_payload_status,
        "integrity_status": result.integrity_status,
        "signature_status": result.signature_status,
        "durable_recovery": result.durable_recovery,
        "reason_code": result.technical_details.get("reasonCode"),
        "warnings": list(result.warnings),
    }


EXPECTED = {
    "valid_shirushi.png": {
        "ai_training_use": "NOT_WANTED",
        "ai_inference_use": "NOT_WANTED",
        "rights_status": "DETECTED",
        "cawg_status": "DETECTED",
        "c2pa_status": "DETECTED",
        "trustmark_status": "DETECTED",
        "trustmark_payload_status": "MATCH",
        "integrity_status": "OK",
        "signature_status": "PREVIEW",
    },
    "valid_shirushi.jpg": {
        "ai_training_use": "NOT_WANTED",
        "ai_inference_use": "NOT_WANTED",
        "rights_status": "DETECTED",
        "cawg_status": "DETECTED",
        "c2pa_status": "DETECTED",
        "trustmark_status": "DETECTED",
        "trustmark_payload_status": "MATCH",
        "integrity_status": "OK",
        "signature_status": "PREVIEW",
    },
    "plain_no_shirushi.png": {
        "ai_training_use": "NO_PERMISSION_INFO",
        "ai_inference_use": "NO_PERMISSION_INFO",
        "rights_status": "NOT_DETECTED",
        "cawg_status": "UNKNOWN",
        "c2pa_status": "NOT_DETECTED",
        "trustmark_status": "NOT_DETECTED",
        "integrity_status": "UNKNOWN",
        "signature_status": "INDETERMINATE",
    },
    "plain_no_shirushi.jpg": {
        "ai_training_use": "NO_PERMISSION_INFO",
        "ai_inference_use": "NO_PERMISSION_INFO",
        "rights_status": "NOT_DETECTED",
        "cawg_status": "UNKNOWN",
        "c2pa_status": "NOT_DETECTED",
        "trustmark_status": "NOT_DETECTED",
        "integrity_status": "UNKNOWN",
        "signature_status": "INDETERMINATE",
    },
    "metadata_stripped_trustmark_present.png": {
        "ai_training_use": "NO_PERMISSION_INFO",
        "ai_inference_use": "NO_PERMISSION_INFO",
        "rights_status": "NOT_DETECTED",
        "cawg_status": "UNKNOWN",
        "c2pa_status": "NOT_DETECTED",
        "trustmark_status": "DETECTED",
        "trustmark_payload_status": "VALID_UNBOUND",
        "integrity_status": "UNKNOWN",
        "signature_status": "INDETERMINATE",
        "durable_recovery": "IDENTIFIER_RECOVERED",
    },
    "c2pa_verification_failure.png": {
        "ai_training_use": "NOT_WANTED",
        "ai_inference_use": "NOT_WANTED",
        "rights_status": "DETECTED",
        "cawg_status": "DETECTED",
        "c2pa_status": "VERIFICATION_FAILED",
        "trustmark_status": "DETECTED",
        "integrity_status": "VERIFICATION_FAILED",
        "signature_status": "INDETERMINATE",
        "reason_code": "C2PA_ASSET_DATA_HASH_MISMATCH",
    },
}


def require_expected(filename: str, actual: dict[str, object]) -> None:
    mismatches = {
        key: {"expected": expected, "actual": actual.get(key)}
        for key, expected in EXPECTED[filename].items()
        if actual.get(key) != expected
    }
    if mismatches:
        raise RuntimeError(f"{filename} did not reach its required measured state: {mismatches}")


def build(destination: Path) -> dict[str, object]:
    source = SOURCE.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite fixture directory: {destination}")
    if not source.is_file():
        raise FileNotFoundError(source)
    if sha256_file(source) != SOURCE_SHA256:
        raise RuntimeError("project synthetic source SHA-256 mismatch")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}-build-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        source_copy = staging / "source_synthetic.png"
        shutil.copyfile(source, source_copy)
        if sha256_file(source_copy) != sha256_file(source):
            raise RuntimeError("source copy SHA-256 mismatch")

        with Image.open(source) as opened:
            opened.load()
            source_format = opened.format
            source_mode = opened.mode
            source_size = opened.size
            source_info = dict(opened.info)
            source_exif = opened.getexif()
            oriented = ImageOps.exif_transpose(opened).convert("RGB")

        if source_format != "PNG" or source_mode != "RGB":
            raise RuntimeError("project synthetic source is not an RGB PNG")

        plain_png = staging / "plain_no_shirushi.png"
        plain_jpeg = staging / "plain_no_shirushi.jpg"
        save_common = {"icc_profile": source_info.get("icc_profile")}
        oriented.save(plain_png, format="PNG", compress_level=6, optimize=False, **save_common)
        oriented.save(
            plain_jpeg,
            format="JPEG",
            quality=95,
            subsampling=0,
            optimize=False,
            progressive=False,
            **save_common,
        )

        with Image.open(plain_png) as png_image, Image.open(plain_jpeg) as jpeg_image:
            png_image.load()
            jpeg_image.load()
            conversion = {
                "orientation_applied": source_exif.get(274) is not None,
                "source_dimensions": list(source_size),
                "clean_dimensions": list(oriented.size),
                "clean_mode": oriented.mode,
                "png_decoded_psnr_db": decoded_psnr(oriented, png_image),
                "jpeg_decoded_psnr_db": decoded_psnr(oriented, jpeg_image),
                "png_icc_preserved": png_image.info.get("icc_profile") == source_info.get("icc_profile"),
                "jpeg_icc_preserved": jpeg_image.info.get("icc_profile") == source_info.get("icc_profile"),
            }

        creator = CreatorService(
            log_path=staging / "generation.log",
            core_runner=partial(run_pipeline, trustmark_factory=installed_model_factory),
            verifier=partial(verify_contract, trustmark_factory=installed_model_factory),
        )
        creation_results = {}
        for clean_name, valid_name in (
            ("plain_no_shirushi.png", "valid_shirushi.png"),
            ("plain_no_shirushi.jpg", "valid_shirushi.jpg"),
        ):
            result = creator.create(CreatorRequest(staging / clean_name, staging / valid_name))
            creation_results[valid_name] = {
                "status": result.get("status"),
                "input_sha256": result.get("inputSha256"),
                "output_sha256": result.get("outputSha256"),
            }
            if result.get("status") != "SUCCESS":
                raise RuntimeError(f"CreatorService failed for {valid_name}: {result}")

        stripped = staging / "metadata_stripped_trustmark_present.png"
        with Image.open(staging / "valid_shirushi.png") as marked:
            marked.load()
            pixels_only = Image.frombytes("RGB", marked.size, marked.convert("RGB").tobytes())
        pixels_only.save(stripped, format="PNG", compress_level=6, optimize=False)

        tampered = staging / "c2pa_verification_failure.png"
        tamper_detail = make_pixel_tamper(staging / "valid_shirushi.png", tampered)

        inspector = InspectionService(
            verifier=partial(verify_contract, trustmark_factory=installed_model_factory),
            trustmark_probe=LocalTrustMarkProbe(factory=installed_model_factory),
        )
        inspections = {}
        for filename in EXPECTED:
            path = staging / filename
            result = inspector.inspect(path)
            actual = inspection_record(result)
            require_expected(filename, actual)
            inspections[filename] = actual

        fixtures = []
        for filename in EXPECTED:
            path = staging / filename
            with Image.open(path) as image:
                image.load()
                fixtures.append(
                    {
                        "filename": filename,
                        "bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                        "format": image.format,
                        "mode": image.mode,
                        "dimensions": list(image.size),
                        "inspection": inspections[filename],
                    }
                )

        manifest = {
            "manifest_version": "1.0",
            "purpose": "Owner GUI smoke fixtures for Shirushi inspection",
            "source": {
                "filename": "source_synthetic.png",
                "bytes": source_copy.stat().st_size,
                "sha256": sha256_file(source_copy),
                "format": source_format,
                "structure": "PNG",
                "dimensions": list(source_size),
                "mode": source_mode,
                "exif_present": bool(source_info.get("exif")),
                "orientation": source_exif.get(274),
                "icc_present": bool(source_info.get("icc_profile")),
                "icc_sha256": hashlib.sha256(source_info["icc_profile"]).hexdigest().upper()
                if source_info.get("icc_profile")
                else None,
                "xmp_present": bool(source_info.get("xmp")),
                "comment_present": bool(source_info.get("comment")),
                "c2pa": "NOT_DETECTED",
                "cawg_rights": "NOT_DETECTED",
                "trustmark": "NOT_DETECTED",
                "provenance": "Project-generated deterministic synthetic image; no external image assets.",
                "generator": "scripts/creator_repeatability.py",
            },
            "conversion": conversion,
            "rights_policy": {
                "cawg.ai_inference.use": "notAllowed",
                "cawg.ai_generative_training.use": "notAllowed",
            },
            "creation": creation_results,
            "tamper": tamper_detail,
            "fixtures": fixtures,
            "optional_fixtures": {
                "cawg_rights_missing": "NOT_CREATED: no existing stable omission constructor",
                "invalid_trustmark": "NOT_CREATED: avoided random corruption and ambiguous mixed failure",
            },
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        (staging / "generation.log").unlink(missing_ok=True)
        os.rename(staging, destination)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build(args.output)
    print(json.dumps({"result": "PASS", "output": str(args.output.resolve()), "fixtures": len(manifest["fixtures"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
