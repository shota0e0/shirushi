"""Run Slice 04 JPEG repeatability, quality, and tamper acceptance checks."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import logging
import math
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np
from PIL import Image
from trustmark import TrustMark


PROJECT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from creator_e2e import JPEG_ENCODING_POLICY, RIGHTS_PRESET, run_pipeline
from creator_verify import verify_contract
from manifest_claim_audit import extract_c2pa_jumbf, node_cbor, parse_jumbf


FIXTURE = PROJECT / "testdata/e2e/clean_fixture.jpg"
C2PATOOL = PROJECT / "tools/c2patool-0.26.60/c2patool/c2patool.exe"
OUTPUT_ROOT = PROJECT / "output/e2e/jpeg"
RUN_COUNT = 5


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def psnr(reference: Path, candidate: Path) -> float:
    with Image.open(reference) as ref_image, Image.open(candidate) as out_image:
        ref = np.asarray(ref_image.convert("RGB"), dtype=np.float32)
        out = np.asarray(out_image.convert("RGB"), dtype=np.float32)
    mse = float(np.mean((ref - out) ** 2))
    return math.inf if mse == 0 else 10.0 * math.log10((255.0 * 255.0) / mse)


def c2pa_absent(path: Path, settings: Path) -> bool:
    completed = subprocess.run(
        [str(C2PATOOL), str(path), "--detailed", "--settings", str(settings)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    active = None
    if completed.stdout.strip():
        try:
            active = json.loads(completed.stdout).get("active_manifest")
        except json.JSONDecodeError:
            pass
    return not active


def jpeg_markers(path: Path) -> list[str]:
    raw = path.read_bytes()
    if raw[:2] != b"\xff\xd8":
        raise ValueError("invalid JPEG signature")
    markers = ["SOI"]
    offset = 2
    while offset + 1 < len(raw):
        if raw[offset] != 0xFF:
            raise ValueError("invalid JPEG marker sequence")
        while offset < len(raw) and raw[offset] == 0xFF:
            offset += 1
        marker = raw[offset]
        offset += 1
        if marker == 0xD9:
            markers.append("EOI")
            break
        name = f"APP{marker - 0xE0}" if 0xE0 <= marker <= 0xEF else {
            0xC0: "SOF0", 0xC2: "SOF2", 0xC4: "DHT", 0xDA: "SOS", 0xDB: "DQT", 0xDD: "DRI",
        }.get(marker, f"FF{marker:02X}")
        markers.append(name)
        if marker == 0xDA:
            markers.append("EOI")
            break
        if marker in {0x01, *range(0xD0, 0xD8)}:
            continue
        length = int.from_bytes(raw[offset : offset + 2], "big")
        if length < 2 or offset + length > len(raw):
            raise ValueError("invalid JPEG segment length")
        offset += length
    return markers


def locate_jpeg_scan(raw: bytes) -> int:
    offset = 2
    while offset + 4 <= len(raw):
        if raw[offset] != 0xFF:
            raise ValueError("invalid JPEG marker sequence")
        while offset < len(raw) and raw[offset] == 0xFF:
            offset += 1
        marker = raw[offset]
        offset += 1
        if marker == 0xDA:
            length = int.from_bytes(raw[offset : offset + 2], "big")
            return offset + length
        if marker in {0x01, *range(0xD0, 0xD9)}:
            continue
        length = int.from_bytes(raw[offset : offset + 2], "big")
        offset += length
    raise ValueError("JPEG SOS not found")


def make_pixel_tamper(source: Path, target: Path) -> int:
    raw = bytearray(source.read_bytes())
    scan_start = locate_jpeg_scan(raw)
    scan_end = raw.rfind(b"\xff\xd9")
    start = scan_start + max(64, (scan_end - scan_start) // 3)
    for offset in range(start, scan_end):
        if raw[offset] not in {0x00, 0xFF}:
            raw[offset] ^= 0x01
            target.write_bytes(raw)
            try:
                with Image.open(target) as image:
                    image.load()
                return offset
            except Exception:
                raw[offset] ^= 0x01
    raise ValueError("unable to create decodable JPEG pixel tamper")


def make_signature_tamper(source: Path, target: Path) -> int:
    cabx, _, _, _, _ = extract_c2pa_jumbf(source)
    nodes = parse_jumbf(cabx)
    signature_node = next(node for node in nodes if node["label"] == "c2pa.signature")
    signature_object, _, _ = node_cbor(cabx, signature_node)
    cose = signature_object["value"]
    signature = cose[3]
    raw = bytearray(source.read_bytes())
    offset = raw.find(signature)
    if offset < 0 or raw.find(signature, offset + 1) >= 0:
        raise ValueError("unable to locate one contiguous COSE signature in JPEG")
    raw[offset] ^= 0x01
    target.write_bytes(raw)
    return offset


def main() -> int:
    result_path = OUTPUT_ROOT / "jpeg_acceptance.json"
    log_path = OUTPUT_ROOT / "jpeg_acceptance.log"
    if result_path.exists() or log_path.exists() or (OUTPUT_ROOT / "runs").exists():
        raise SystemExit("refusing to overwrite existing JPEG acceptance artifacts")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("creator_jpeg_acceptance")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_path, mode="x", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    runs_dir = OUTPUT_ROOT / "runs"
    tamper_dir = OUTPUT_ROOT / "tamper"
    runs_dir.mkdir()
    tamper_dir.mkdir()
    settings = OUTPUT_ROOT / ".settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    fixture_sha = sha256_file(FIXTURE)
    tm = TrustMark(
        verbose=False,
        model_type="P",
        encoding_type=TrustMark.Encoding.BCH_4,
        loadRemover=False,
        loadBBoxDetector=False,
    )
    try:
        with Image.open(FIXTURE) as fixture_image:
            fixture_image.load()
            decoded = tm.decode(fixture_image.convert("RGB"), MODE="binary", DETECTFIRST=False, ROTATION=False)
            fixture_info = {
                "path": str(FIXTURE.resolve()),
                "sha256": fixture_sha,
                "size": FIXTURE.stat().st_size,
                "format": fixture_image.format,
                "mode": fixture_image.mode,
                "dimensions": list(fixture_image.size),
                "markers": jpeg_markers(FIXTURE),
                "app_markers": [name for name in jpeg_markers(FIXTURE) if name.startswith("APP")],
                "binary_c2pa_markers_absent": not any(
                    marker in FIXTURE.read_bytes()
                    for marker in (b"c2pa", b"jumb", b"jumd", b"Content Credentials")
                ),
                "c2pa_absent": c2pa_absent(FIXTURE, settings),
                "trustmark_absent": not bool(decoded[1]),
            }
        if fixture_info["format"] != "JPEG" or fixture_info["mode"] != "RGB" or fixture_info["dimensions"] != [1024, 1024]:
            raise RuntimeError("clean JPEG fixture has unexpected format")
        if (
            not fixture_info["c2pa_absent"]
            or not fixture_info["binary_c2pa_markers_absent"]
            or not fixture_info["trustmark_absent"]
        ):
            raise RuntimeError("clean JPEG fixture is not clean")

        runs: list[dict[str, Any]] = []
        for index in range(1, RUN_COUNT + 1):
            output = runs_dir / f"run_{index:02d}_rights.jpg"
            pipeline_result = run_pipeline(
                FIXTURE,
                output,
                C2PATOOL,
                runs_dir / f"run_{index:02d}.json",
                runs_dir / f"run_{index:02d}.log",
            )
            if pipeline_result["status"] != "PASS":
                raise RuntimeError(f"JPEG run {index} failed: {pipeline_result['exception']}")
            verification = pipeline_result["verification"]
            run = {
                "index": index,
                "status": pipeline_result["status"],
                "path": str(output.resolve()),
                "sha256": sha256_file(output),
                "size": output.stat().st_size,
                "identifier": pipeline_result["identifier"],
                "soft_binding": pipeline_result["soft_binding"],
                "wm_present": verification["trustmark"]["wm_present"],
                "schema": verification["trustmark"]["wm_schema"],
                "payload_match": verification["trustmark"]["payload_match"],
                "rights_entries": verification["c2pa"]["rights_entries"],
                "assertion_digests_match": verification["c2pa"]["all_assertion_digests_match"],
                "asset_binding_valid": not bool(verification["c2pa"]["c2patool_validation_status"]),
                "signature_valid": verification["signature"]["valid"],
                "trust_validated": verification["signature"]["certificate_trust_validation_performed"],
                "psnr_db": psnr(FIXTURE, output),
            }
            runs.append(run)
            logger.info("run=%d status=PASS sha256=%s psnr_db=%.4f", index, run["sha256"], run["psnr_db"])
        if sha256_file(FIXTURE) != fixture_sha:
            raise RuntimeError("JPEG fixture changed")
        if len({run["identifier"] for run in runs}) != RUN_COUNT or len({run["sha256"] for run in runs}) != RUN_COUNT:
            raise RuntimeError("JPEG repeatability uniqueness invariant failed")
        if min(run["psnr_db"] for run in runs) < 40.0:
            raise RuntimeError("JPEG quality fell below the 40 dB acceptance floor")

        control = Path(runs[0]["path"])
        pixel_path = tamper_dir / "pixel_tamper.jpg"
        signature_path = tamper_dir / "signature_tamper.jpg"
        pixel_offset = make_pixel_tamper(control, pixel_path)
        signature_offset = make_signature_tamper(control, signature_path)
        pixel_result = verify_contract(pixel_path, C2PATOOL, settings, tm)
        signature_result = verify_contract(signature_path, C2PATOOL, settings, tm)
        tampers = [
            {"case": "pixel", "path": str(pixel_path.resolve()), "sha256": sha256_file(pixel_path), "offset": pixel_offset, "verification": pixel_result},
            {"case": "signature", "path": str(signature_path.resolve()), "sha256": sha256_file(signature_path), "offset": signature_offset, "verification": signature_result},
        ]
        if any(item["verification"]["result"] == "PASS" for item in tampers):
            raise RuntimeError("tampered JPEG unexpectedly passed")

        result = {
            "phase": "Implementation Slice 04 — JPEG Support",
            "status": "PASS",
            "generated_at": datetime.now().astimezone().isoformat(),
            "fixture": fixture_info,
            "encoding_policy": JPEG_ENCODING_POLICY,
            "rights_preset": RIGHTS_PRESET,
            "runs": runs,
            "tamper_smoke": tampers,
            "summary": {
                "pass_count": sum(run["status"] == "PASS" for run in runs),
                "unique_identifiers": len({run["identifier"] for run in runs}),
                "unique_output_hashes": len({run["sha256"] for run in runs}),
                "fixture_unchanged": sha256_file(FIXTURE) == fixture_sha,
                "min_psnr_db": min(run["psnr_db"] for run in runs),
                "max_psnr_db": max(run["psnr_db"] for run in runs),
                "tamper_rejected_count": sum(item["verification"]["result"] != "PASS" for item in tampers),
            },
        }
        result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        logger.info("acceptance=PASS")
        return 0
    except Exception:
        logger.exception("acceptance=STOP")
        return 2
    finally:
        if settings.exists():
            settings.unlink()
        for current in list(logger.handlers):
            current.flush()
            current.close()
            logger.removeHandler(current)


if __name__ == "__main__":
    raise SystemExit(main())
