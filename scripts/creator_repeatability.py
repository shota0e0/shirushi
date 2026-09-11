"""Slice 01B: clean-PNG first-embed repeatability proof.

This is a fixed five-run test-signing harness, not a general batch feature.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import struct
import subprocess
import traceback
import uuid
import zlib
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from trustmark import TrustMark

from creator_e2e import ALGORITHM, IDENTIFIER_BITS, RIGHTS_PRESET, SCHEMA, run_pipeline
from creator_verify import VerificationFailure, verify_output


RUN_COUNT = 5
FIXTURE_SIZE = (1024, 1024)


class RepeatabilityFailure(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def configure_logging(path: Path) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"creator_repeatability.{uuid.uuid4().hex}")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(path, mode="x", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def close_logging(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)


def generate_clean_fixture(path: Path) -> None:
    """Create a deterministic synthetic fixture without external assets."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", FIXTURE_SIZE, "white")
    draw = ImageDraw.Draw(image)

    # Checker panel.
    cell = 32
    for y in range(64, 384, cell):
        for x in range(64, 384, cell):
            shade = 218 if ((x - 64) // cell + (y - 64) // cell) % 2 else 246
            draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(shade, shade, shade))

    # Fixed shapes.
    draw.ellipse((640, 96, 864, 320), fill=(218, 45, 52), outline=(110, 0, 0), width=4)
    draw.rectangle((640, 400, 864, 624), fill=(42, 100, 210), outline=(0, 35, 100), width=4)
    draw.polygon(((752, 680), (624, 912), (880, 912)), fill=(42, 164, 83), outline=(0, 80, 32))

    # Deterministic gradient panel.
    for offset in range(320):
        red = round(255 * offset / 319)
        blue = 255 - red
        draw.line((64 + offset, 680, 64 + offset, 912), fill=(red, 72, blue))

    font = ImageFont.load_default(size=32)
    text = "RIGHTS SIGNAL LAB"
    box = draw.textbbox((0, 0), text, font=font)
    width = box[2] - box[0]
    draw.rectangle((512 - width // 2 - 20, 480 - 30, 512 + width // 2 + 20, 480 + 34), fill="white", outline="black", width=2)
    draw.text((512 - width // 2, 480 - 19), text, fill="black", font=font)

    image.save(path, format="PNG", optimize=False, compress_level=6)


def parse_png_chunks(path: Path) -> list[dict[str, Any]]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RepeatabilityFailure("fixture is not a PNG")
    chunks: list[dict[str, Any]] = []
    offset = 8
    while offset < len(data):
        if offset + 12 > len(data):
            raise RepeatabilityFailure("truncated PNG chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type_bytes = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise RepeatabilityFailure("PNG chunk exceeds file boundary")
        payload = data[offset + 8 : offset + 8 + length]
        stored_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        computed_crc = zlib.crc32(chunk_type_bytes + payload) & 0xFFFFFFFF
        if stored_crc != computed_crc:
            raise RepeatabilityFailure("PNG chunk CRC mismatch")
        chunk_type = chunk_type_bytes.decode("latin-1")
        chunks.append({"type": chunk_type, "length": length, "offset": offset, "crc_valid": True})
        offset = end
        if chunk_type == "IEND":
            break
    if offset != len(data) or not chunks or chunks[-1]["type"] != "IEND":
        raise RepeatabilityFailure("invalid PNG termination")
    return chunks


def c2patool_fixture_check(c2patool: Path, fixture: Path, settings: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [str(c2patool), str(fixture), "--detailed", "--settings", str(settings)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    report = None
    if completed.stdout.strip():
        try:
            report = json.loads(completed.stdout)
        except json.JSONDecodeError:
            report = None
    active_manifest = report.get("active_manifest") if isinstance(report, dict) else None
    # c2patool may return either success or a no-manifest diagnostic for an
    # unsigned asset. Presence is determined from both its parsed report and
    # the independently parsed PNG chunks below.
    return {
        "returncode": completed.returncode,
        "active_manifest": active_manifest,
        "manifest_present": bool(active_manifest),
        "stderr": completed.stderr.strip() or None,
    }


def audit_clean_fixture(path: Path, tm: TrustMark, c2patool: Path, settings: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG" or image.size != FIXTURE_SIZE:
                raise RepeatabilityFailure("fixture PNG format/dimensions mismatch")
            decoded = tm.decode(image.convert("RGB"), MODE="binary", DETECTFIRST=False, ROTATION=False)
    except RepeatabilityFailure:
        raise
    except Exception as exc:
        raise RepeatabilityFailure(f"fixture validation failed: {exc}") from exc

    payload, present, schema = decoded
    chunks = parse_png_chunks(path)
    chunk_types = [item["type"] for item in chunks]
    raw = path.read_bytes()
    binary_markers = {
        marker: marker.encode("ascii") in raw
        for marker in ("c2pa", "jumb", "jumd", "Content Credentials")
    }
    c2pa_tool = c2patool_fixture_check(c2patool, path, settings)
    ca_bx_present = "caBX" in chunk_types
    if ca_bx_present or c2pa_tool["manifest_present"] or any(binary_markers.values()):
        raise RepeatabilityFailure("clean fixture contains C2PA/JUMBF/Content Credentials data")
    if bool(present):
        raise RepeatabilityFailure("clean fixture produced a TrustMark false positive")
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        "dimensions": list(FIXTURE_SIZE),
        "format": "PNG",
        "chunks": chunks,
        "chunk_types": chunk_types,
        "c2pa": "ABSENT",
        "cabx_present": False,
        "binary_markers": binary_markers,
        "c2patool_check": c2pa_tool,
        "trustmark": {
            "wm_present": False,
            "wm_schema_raw": schema,
            "wm_payload_raw": payload,
        },
    }


def run_negative_control(path: Path, tm: TrustMark, c2patool: Path, settings: Path) -> dict[str, Any]:
    """Prove that the normal post-write verifier rejects the clean fixture."""
    try:
        verify_output(path, FIXTURE_SIZE, "0" * IDENTIFIER_BITS, tm, c2patool, settings)
    except VerificationFailure as exc:
        if exc.result != "FAIL_C2PA":
            raise RepeatabilityFailure(f"clean fixture rejection was unexpected: {exc.result}: {exc}") from exc
        return {
            "result": "PASS",
            "post_write_verifier_invoked": True,
            "post_write_verifier_outcome": exc.result,
            "post_write_verifier_message": str(exc),
            "trustmark_absent": True,
            "c2pa_absent": True,
            "rights_assertion_absent": True,
        }
    raise RepeatabilityFailure("clean fixture was accepted as a valid rights-signal output")


def summarize_run(index: int, pipeline: dict[str, Any]) -> dict[str, Any]:
    verification = pipeline.get("verification") or {}
    trustmark = verification.get("trustmark") or {}
    soft_binding = verification.get("soft_binding") or {}
    c2pa = verification.get("c2pa") or {}
    signature = verification.get("signature") or {}
    identifier = pipeline.get("identifier")
    expected_soft_binding = f"{SCHEMA}*{identifier}" if identifier else None
    invariant = bool(
        pipeline.get("status") == "PASS"
        and trustmark.get("payload") == identifier
        and soft_binding.get("manifest_value") == expected_soft_binding
        and trustmark.get("reconstructed_soft_binding") == expected_soft_binding
        and c2pa.get("all_assertion_digests_match") is True
        and c2pa.get("soft_binding_assertion_digest_match") is True
        and signature.get("valid") is True
    )
    return {
        "run": index,
        "status": pipeline.get("status"),
        "output_path": pipeline.get("output", {}).get("path"),
        "output_sha256": pipeline.get("output", {}).get("sha256"),
        "identifier": identifier,
        "schema": trustmark.get("wm_schema"),
        "soft_binding": pipeline.get("soft_binding"),
        "wm_present": trustmark.get("wm_present"),
        "payload_length": trustmark.get("payload_length"),
        "payload_match": trustmark.get("payload_match"),
        "rights_entries": c2pa.get("rights_entries"),
        "active_manifest": c2pa.get("active_manifest"),
        "claim_instance_id": c2pa.get("claim_instance_id"),
        "assertion_digest": c2pa.get("soft_binding_assertion_digest"),
        "claim_references_match": c2pa.get("all_assertion_digests_match"),
        "signature_valid": signature.get("valid"),
        "integrity_invariant": invariant,
        "exception": pipeline.get("exception"),
    }


def write_csv(path: Path, runs: list[dict[str, Any]]) -> None:
    fields = [
        "run", "status", "output_path", "output_sha256", "identifier", "schema",
        "soft_binding", "wm_present", "payload_length", "payload_match",
        "active_manifest", "claim_instance_id", "assertion_digest",
        "claim_references_match", "signature_valid", "integrity_invariant", "exception",
    ]
    with path.open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(runs)


def run_repeatability(project: Path) -> dict[str, Any]:
    fixture = project / "testdata/e2e/clean_fixture.png"
    output_dir = project / "output/e2e/repeatability"
    aggregate_json = output_dir / "repeatability_result.json"
    aggregate_csv = output_dir / "repeatability_result.csv"
    aggregate_log = output_dir / "repeatability.log"
    c2patool = project / "tools/c2patool-0.26.60/c2patool/c2patool.exe"
    outputs = [output_dir / f"clean_embed_{index:02d}.png" for index in range(1, RUN_COUNT + 1)]
    run_jsons = [output_dir / f".run_{index:02d}_result.json" for index in range(1, RUN_COUNT + 1)]
    run_logs = [output_dir / f".run_{index:02d}.log" for index in range(1, RUN_COUNT + 1)]
    protected_paths = [aggregate_json, aggregate_csv, aggregate_log, *outputs, *run_jsons, *run_logs]
    conflicts = [str(path) for path in protected_paths if path.exists()]
    if conflicts:
        raise RepeatabilityFailure(f"refusing to overwrite existing repeatability artifacts: {conflicts}")
    if not c2patool.is_file():
        raise RepeatabilityFailure(f"c2patool not found: {c2patool}")

    generate_clean_fixture(fixture)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(aggregate_log)
    started = datetime.now().astimezone().isoformat()
    result: dict[str, Any] = {
        "phase": "Implementation Slice 01B — Clean PNG First-Embed Repeatability",
        "status": "RUNNING",
        "started_at": started,
        "finished_at": None,
        "fixture": None,
        "negative_control": None,
        "rights_preset": RIGHTS_PRESET,
        "runs": [],
        "summary": None,
        "exception": None,
    }
    settings = output_dir / f".settings-{uuid.uuid4().hex}.json"
    settings.write_text("{}\n", encoding="utf-8")
    try:
        tm = TrustMark(
            verbose=False,
            model_type="P",
            encoding_type=TrustMark.Encoding.BCH_4,
            loadRemover=False,
            loadBBoxDetector=False,
        )
        if int(tm.schemaCapacity()) != IDENTIFIER_BITS:
            raise RepeatabilityFailure("BCH_4 capacity is not 68 bits")
        fixture_audit = audit_clean_fixture(fixture, tm, c2patool, settings)
        result["fixture"] = fixture_audit
        fixture_hash = fixture_audit["sha256"]
        result["negative_control"] = run_negative_control(fixture, tm, c2patool, settings)
        logger.info(
            "Clean fixture PASS path=%s sha256=%s chunks=%s TrustMark=ABSENT C2PA=ABSENT",
            fixture.resolve(), fixture_hash, fixture_audit["chunk_types"],
        )
        logger.info(
            "Negative control PASS post_write_verifier_outcome=%s TrustMark=ABSENT C2PA=ABSENT rights=ABSENT",
            result["negative_control"]["post_write_verifier_outcome"],
        )

        for index, output in enumerate(outputs, start=1):
            if sha256_file(fixture) != fixture_hash:
                raise RepeatabilityFailure(f"fixture hash changed before run {index}")
            pipeline = run_pipeline(fixture, output, c2patool, run_jsons[index - 1], run_logs[index - 1])
            run = summarize_run(index, pipeline)
            result["runs"].append(run)
            logger.info(
                "run=%d status=%s identifier=%s output_sha256=%s invariant=%s",
                index, run["status"], run["identifier"], run["output_sha256"], run["integrity_invariant"],
            )
            if run["status"] != "PASS" or not run["integrity_invariant"]:
                raise RepeatabilityFailure(f"run {index} failed post-write verification")

        final_fixture_hash = sha256_file(fixture)
        identifiers = [run["identifier"] for run in result["runs"]]
        output_hashes = [run["output_sha256"] for run in result["runs"]]
        pass_count = sum(run["status"] == "PASS" for run in result["runs"])
        signature_count = sum(run["signature_valid"] is True for run in result["runs"])
        schema_consistent = all(run["schema"] == SCHEMA for run in result["runs"])
        rights_consistent = all(run["rights_entries"] == RIGHTS_PRESET["entries"] for run in result["runs"])
        summary = {
            "run_count": len(result["runs"]),
            "pass_count": pass_count,
            "fail_count": len(result["runs"]) - pass_count,
            "unique_identifier_count": len(set(identifiers)),
            "unique_output_sha256_count": len(set(output_hashes)),
            "fixture_sha256_before": fixture_hash,
            "fixture_sha256_after": final_fixture_hash,
            "fixture_unchanged": fixture_hash == final_fixture_hash,
            "schema_consistent": schema_consistent,
            "rights_consistent": rights_consistent,
            "signature_verify_count": signature_count,
            "integrity_invariant_count": sum(run["integrity_invariant"] for run in result["runs"]),
        }
        result["summary"] = summary
        success = (
            pass_count == RUN_COUNT
            and summary["unique_identifier_count"] == RUN_COUNT
            and summary["unique_output_sha256_count"] == RUN_COUNT
            and summary["fixture_unchanged"]
            and schema_consistent
            and rights_consistent
            and signature_count == RUN_COUNT
            and summary["integrity_invariant_count"] == RUN_COUNT
        )
        if not success:
            raise RepeatabilityFailure(f"repeatability acceptance criteria failed: {summary}")
        result["status"] = "PASS"
        logger.info("PASS summary=%s", json.dumps(summary, ensure_ascii=False, sort_keys=True))
    except Exception:
        result["status"] = "STOP"
        result["exception"] = traceback.format_exc()
        logger.exception("Repeatability STOP")
    finally:
        if settings.exists():
            settings.unlink()
        result["finished_at"] = datetime.now().astimezone().isoformat()
        aggregate_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        write_csv(aggregate_csv, result["runs"])
        close_logging(logger)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_repeatability(args.project.resolve())
    except RepeatabilityFailure as exc:
        print(json.dumps({"result": "STOP", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"result": result["status"], "summary": result["summary"]}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
