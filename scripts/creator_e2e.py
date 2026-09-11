"""Headless PNG/JPEG rights-signal E2E pipeline.

This is a test-signing proof, not a production credential issuer.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import sys
import traceback
import uuid
from typing import Any, Callable

from PIL import Image
from trustmark import TrustMark

if not getattr(sys, "frozen", False):
    source_directory = Path(__file__).resolve().parent.parent / "src"
    if str(source_directory) not in sys.path:
        sys.path.insert(0, str(source_directory))

from runtime_paths import (
    C2PATOOL_VERSION,
    RuntimeComponentError,
    require_c2patool,
    resolve_runtime_paths,
)
from trustmark_model_manager import ModelPreparationError, create_trustmark

from creator_verify import VerificationFailure, verify_output


TOOL_VERSION = "0.1.0-slice01"
MODEL_VARIANT = "P"
ENCODING_NAME = "BCH_4"
SCHEMA = 2
IDENTIFIER_BITS = 68
ALGORITHM = "com.adobe.trustmark.P"
WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
RIGHTS_PRESET = {
    "name": "AI利用拒否",
    "assertion": "cawg.training-mining",
    "entries": {
        "cawg.ai_inference": {"use": "notAllowed"},
        "cawg.ai_generative_training": {"use": "notAllowed"},
    },
}
FINAL_RESULTS = {"PASS", "FAIL_WRITE", "FAIL_C2PA", "FAIL_TRUSTMARK", "FAIL_SOFT_BINDING", "FAIL_SIGNATURE"}
SUPPORTED_EXTENSIONS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}
JPEG_ENCODING_POLICY = {
    "quality": 95,
    "subsampling": 0,
    "optimize": False,
    "progressive": False,
}


class PipelineFailure(RuntimeError):
    def __init__(self, result: str, message: str, error_code: str | None = None):
        super().__init__(message)
        self.result = result
        self.error_code = error_code


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def validate_input_image(path: Path) -> tuple[tuple[int, int], str, str]:
    if not path.is_file():
        raise PipelineFailure("FAIL_WRITE", f"input file does not exist: {path}")
    expected_format = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
    if expected_format is None:
        raise PipelineFailure("FAIL_WRITE", "input extension must be .png, .jpg, or .jpeg")
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
            if image.format != expected_format:
                raise PipelineFailure(
                    "FAIL_WRITE",
                    f"input signature/decoder format {image.format!r} does not match {path.suffix}",
                )
            dimensions = image.size
    except PipelineFailure:
        raise
    except Exception as exc:
        raise PipelineFailure("FAIL_WRITE", f"invalid image input: {exc}") from exc
    return dimensions, sha256_file(path), expected_format


def validate_input_png(path: Path) -> tuple[tuple[int, int], str]:
    """Compatibility wrapper retained for existing Slice 01 callers/tests."""
    dimensions, digest, image_format = validate_input_image(path)
    if image_format != "PNG":
        raise PipelineFailure("FAIL_WRITE", "input must be PNG")
    return dimensions, digest


def validate_output_path(path: Path, input_suffix: str | None = None) -> None:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise PipelineFailure("FAIL_WRITE", "output extension must be .png, .jpg, or .jpeg")
    if input_suffix is not None and path.suffix.lower() != input_suffix.lower():
        raise PipelineFailure("FAIL_WRITE", "output extension must match the input extension")
    if path.exists():
        raise PipelineFailure("FAIL_WRITE", f"refusing to overwrite existing output: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise PipelineFailure("FAIL_WRITE", f"cannot create output directory: {exc}") from exc
    if not path.parent.is_dir():
        raise PipelineFailure("FAIL_WRITE", "output parent is not a directory")


def configure_logging(path: Path) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"creator_e2e.{id(path)}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def build_manifest(identifier: str, source_name: str) -> dict[str, Any]:
    return {
        "claim_generator_info": [{"name": "rights-signal-lab Creator E2E", "version": TOOL_VERSION}],
        "title": f"Rights signal: {source_name}",
        "instance_id": f"xmp:iid:{uuid.uuid4()}",
        "assertions": [
            {
                "label": "cawg.training-mining",
                "data": {"entries": RIGHTS_PRESET["entries"]},
            },
            {
                "label": "c2pa.soft-binding",
                "data": {
                    "alg": ALGORITHM,
                    "blocks": [{"scope": {}, "value": f"{SCHEMA}*{identifier}"}],
                },
            },
        ],
    }


def _write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _jpeg_save_options(source_info: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = dict(JPEG_ENCODING_POLICY)
    for key in ("icc_profile", "exif", "dpi", "xmp", "comment"):
        value = source_info.get(key)
        if value is not None:
            options[key] = value
    return options


def run_pipeline(
    input_path: Path,
    output_path: Path,
    c2patool: Path,
    result_json: Path,
    log_path: Path,
    trustmark_factory: Callable[[], TrustMark] = create_trustmark,
) -> dict[str, Any]:
    started_at = datetime.now().astimezone().isoformat()
    logger = configure_logging(log_path)
    result: dict[str, Any] = {
        "slice": "Implementation Slice 04 — PNG/JPEG Support",
        "status": "RUNNING",
        "started_at": started_at,
        "finished_at": None,
        "production_ready": False,
        "test_signing": True,
        "test_signing_note": "c2patool built-in test credential; not trusted for production; no key installed in system trust store",
        "input": {"path": str(input_path.resolve()), "sha256": None, "dimensions": None},
        "output": {"path": str(output_path.resolve()), "sha256": None, "dimensions": None},
        "rights_preset": RIGHTS_PRESET,
        "identifier": None,
        "soft_binding": None,
        "verification": None,
        "encoding": None,
        "tools": {
            "python": platform.python_version(),
            "pillow": importlib.metadata.version("Pillow"),
            "trustmark": importlib.metadata.version("trustmark"),
            "c2patool": None,
            "c2patool_path": str(c2patool.resolve()),
        },
        "exception": None,
        "error_code": None,
    }
    input_hash_before = None
    try:
        try:
            c2patool = require_c2patool(c2patool)
        except RuntimeComponentError as exc:
            raise PipelineFailure("FAIL_C2PA", "required c2patool component is unavailable", exc.code) from exc
        version_process = subprocess.run([str(c2patool), "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, shell=False, creationflags=WINDOWS_NO_WINDOW)
        if version_process.returncode != 0 or version_process.stdout.strip() != f"c2patool {C2PATOOL_VERSION}":
            raise PipelineFailure("FAIL_C2PA", "c2patool version check failed", "C2PATOOL_INTEGRITY_FAILED")
        result["tools"]["c2patool"] = version_process.stdout.strip()
        dimensions, input_hash_before, image_format = validate_input_image(input_path)
        result["input"].update({"sha256": input_hash_before, "dimensions": list(dimensions), "format": image_format})
        validate_output_path(output_path, input_path.suffix)
        result["output"]["format"] = image_format
        result["encoding"] = (
            {"format": "JPEG", **JPEG_ENCODING_POLICY, "metadata_preserved": ["ICC", "EXIF", "DPI", "XMP", "comment"]}
            if image_format == "JPEG"
            else {"format": "PNG", "optimize": False, "compress_level": 6}
        )

        identifier = format(secrets.randbits(IDENTIFIER_BITS), f"0{IDENTIFIER_BITS}b")
        result["identifier"] = identifier
        result["soft_binding"] = f"{SCHEMA}*{identifier}"
        logger.info("TEST-ONLY E2E start input=%s input_sha256=%s dimensions=%s", input_path.resolve(), input_hash_before, dimensions)
        logger.info("TrustMark config model=%s encoding=%s schema=%d payload_bits=%d identifier=%s", MODEL_VARIANT, ENCODING_NAME, SCHEMA, IDENTIFIER_BITS, identifier)
        logger.info("Signing mode=c2patool built-in test credential; production trust is not claimed")

        try:
            tm = trustmark_factory()
        except ModelPreparationError as exc:
            raise PipelineFailure("FAIL_WRITE", "model preparation failed", exc.code) from exc
        capacity = tm.schemaCapacity()
        if int(capacity) != IDENTIFIER_BITS:
            raise PipelineFailure("FAIL_TRUSTMARK", f"BCH_4 capacity mismatch: {capacity}")

        # tempfile.TemporaryDirectory uses restrictive mode bits that can become
        # inaccessible under some managed Windows sandboxes.  Create the one-run
        # work directory explicitly with ordinary inherited ACLs instead.
        temp_dir = output_path.parent / f".creator-e2e-{uuid.uuid4().hex}"
        if temp_dir.exists():
            raise PipelineFailure("FAIL_WRITE", "unexpected work-directory collision")
        try:
            temp_dir.mkdir(mode=0o755)
            watermarked = temp_dir / f"watermarked{output_path.suffix}"
            manifest_path = temp_dir / "manifest.json"
            settings_path = temp_dir / "test-settings.json"
            staged_output = temp_dir / f"signed-output{output_path.suffix}"
            try:
                with Image.open(input_path) as source:
                    source.load()
                    source_info = dict(source.info)
                    rgb = source.convert("RGB")
                encoded = tm.encode(rgb, identifier, MODE="binary")
                if encoded.size != dimensions:
                    raise PipelineFailure("FAIL_WRITE", "TrustMark embedding changed dimensions")
                if image_format == "JPEG":
                    encoded.save(watermarked, format="JPEG", **_jpeg_save_options(source_info))
                else:
                    encoded.save(watermarked, format="PNG", optimize=False, compress_level=6)
            except PipelineFailure:
                raise
            except Exception as exc:
                raise PipelineFailure("FAIL_WRITE", f"TrustMark embedding/output failed: {exc}") from exc

            manifest_path.write_text(json.dumps(build_manifest(identifier, input_path.name), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            settings_path.write_text("{}\n", encoding="utf-8")
            sign_command = [str(c2patool), str(watermarked), "--manifest", str(manifest_path), "--output", str(staged_output), "--settings", str(settings_path)]
            signed = subprocess.run(sign_command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, shell=False, creationflags=WINDOWS_NO_WINDOW)
            logger.info("c2patool sign returncode=%d", signed.returncode)
            if signed.stderr.strip():
                logger.info("c2patool sign stderr=%s", signed.stderr.strip().replace("\n", " | "))
            if signed.returncode != 0 or not staged_output.is_file():
                raise PipelineFailure("FAIL_C2PA", f"c2patool signing failed: {signed.stderr.strip() or signed.stdout.strip()}")

            try:
                verification = verify_output(staged_output, dimensions, identifier, tm, c2patool, settings_path)
            except VerificationFailure as exc:
                raise PipelineFailure(exc.result, str(exc)) from exc
            result["verification"] = verification
            if output_path.exists():
                raise PipelineFailure("FAIL_WRITE", "output appeared during processing; refusing overwrite")
            os.rename(staged_output, output_path)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        if sha256_file(input_path) != input_hash_before:
            raise PipelineFailure("FAIL_WRITE", "input hash changed during processing")
        result["output"].update({"sha256": sha256_file(output_path), "dimensions": list(dimensions)})
        result["status"] = "PASS"
        logger.info("PASS output=%s output_sha256=%s", output_path.resolve(), result["output"]["sha256"])
    except PipelineFailure as exc:
        result["status"] = exc.result if exc.result in FINAL_RESULTS else "FAIL_WRITE"
        result["exception"] = str(exc)
        result["error_code"] = exc.error_code
        logger.error("%s %s", result["status"], exc)
    except Exception:
        result["status"] = "FAIL_WRITE"
        result["exception"] = traceback.format_exc()
        logger.exception("Unhandled pipeline failure")
    result["finished_at"] = datetime.now().astimezone().isoformat()
    _write_result(result_json, result)
    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)
    return result


def parse_args() -> argparse.Namespace:
    runtime = resolve_runtime_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--c2patool", type=Path, default=runtime.c2patool)
    parser.add_argument("--result-json", type=Path, default=runtime.user_data_root / "state/creator_e2e_result.json")
    parser.add_argument("--log", type=Path, default=runtime.user_data_root / "logs/creator_e2e.log")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_pipeline(args.input, args.output, args.c2patool, args.result_json, args.log)
    print(json.dumps({"result": result["status"], "output": result["output"], "result_json": str(args.result_json.resolve()), "log": str(args.log.resolve())}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
