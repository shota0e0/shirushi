"""Slice 01C: create controlled tamper copies and prove verifier rejection."""

from __future__ import annotations

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

from PIL import Image, ImageDraw
from trustmark import TrustMark

from creator_verify import (
    VerificationFailure,
    _verify_cose_signature,
    sha256_file,
    verify_output,
)
from manifest_claim_audit import (
    CborDecoder,
    build_cose_sig_structure,
    extract_png_cabx,
    node_cbor,
    parse_jumbf,
    verify_hashed_uri,
)


CONTROL_RELATIVE = Path("output/e2e/repeatability/clean_embed_01.png")
REPEATABILITY_RESULT_RELATIVE = Path("output/e2e/repeatability/repeatability_result.json")
FIXTURE_RELATIVE = Path("testdata/e2e/clean_fixture.png")
EXPECTED_DIMENSIONS = (1024, 1024)


class TamperFailure(RuntimeError):
    pass


def configure_logging(path: Path) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"creator_tamper.{uuid.uuid4().hex}")
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


def png_chunks(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise TamperFailure(f"invalid PNG: {path}")
    chunks = []
    offset = 8
    while offset < len(raw):
        length = int.from_bytes(raw[offset : offset + 4], "big")
        kind = raw[offset + 4 : offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        end = payload_end + 4
        if end > len(raw):
            raise TamperFailure("truncated PNG chunk")
        payload = raw[payload_start:payload_end]
        stored_crc = int.from_bytes(raw[payload_end:end], "big")
        if stored_crc != zlib.crc32(kind + payload) & 0xFFFFFFFF:
            raise TamperFailure("PNG CRC mismatch")
        chunks.append(
            {
                "type": kind.decode("ascii"),
                "offset": offset,
                "payload_start": payload_start,
                "payload_end": payload_end,
                "end": end,
                "data": payload,
            }
        )
        offset = end
        if kind == b"IEND":
            break
    if offset != len(raw):
        raise TamperFailure("bytes found after IEND")
    return chunks


def chunk_payload_hash(path: Path, kind: str) -> str:
    digest = hashlib.sha256()
    matches = [chunk for chunk in png_chunks(path) if chunk["type"] == kind]
    if not matches:
        raise TamperFailure(f"PNG has no {kind} chunk")
    for chunk in matches:
        digest.update(chunk["data"])
    return digest.hexdigest().upper()


def write_png_chunks(path: Path, chunks: list[tuple[str, bytes]]) -> None:
    if path.exists():
        raise TamperFailure(f"refusing overwrite: {path}")
    assembled = bytearray(b"\x89PNG\r\n\x1a\n")
    for kind_text, payload in chunks:
        kind = kind_text.encode("ascii")
        assembled.extend(struct.pack(">I", len(payload)))
        assembled.extend(kind)
        assembled.extend(payload)
        assembled.extend(struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))
    path.write_bytes(assembled)


def replace_cabx_byte(source: Path, target: Path, cabx_offset: int, new_byte: int) -> dict[str, Any]:
    if target.exists():
        raise TamperFailure(f"refusing overwrite: {target}")
    raw = bytearray(source.read_bytes())
    cabx_chunk = next(chunk for chunk in png_chunks(source) if chunk["type"] == "caBX")
    if not 0 <= cabx_offset < len(cabx_chunk["data"]):
        raise TamperFailure("caBX mutation offset outside payload")
    file_offset = cabx_chunk["payload_start"] + cabx_offset
    before = raw[file_offset]
    if before == new_byte:
        raise TamperFailure("mutation did not change byte")
    raw[file_offset] = new_byte
    payload = bytes(raw[cabx_chunk["payload_start"] : cabx_chunk["payload_end"]])
    crc = zlib.crc32(b"caBX" + payload) & 0xFFFFFFFF
    raw[cabx_chunk["payload_end"] : cabx_chunk["end"]] = struct.pack(">I", crc)
    target.write_bytes(raw)
    return {
        "caBX_offset": cabx_offset,
        "png_file_offset": file_offset,
        "before_hex": f"{before:02X}",
        "after_hex": f"{new_byte:02X}",
        "png_caBX_crc_recomputed": True,
    }


def unique_payload_offset(payload: bytes, needle: bytes, description: str) -> int:
    positions = []
    start = 0
    while True:
        position = payload.find(needle, start)
        if position < 0:
            break
        positions.append(position)
        start = position + 1
    if len(positions) != 1:
        raise TamperFailure(f"{description} occurrence count was {len(positions)}, expected 1")
    return positions[0]


def active_context(control: Path, active_label: str) -> dict[str, Any]:
    cabx, file_data_offset, _, _ = extract_png_cabx(control)
    nodes = parse_jumbf(cabx)
    matches = [node for node in nodes if node["label"] == active_label and len(node["path"]) == 2]
    if len(matches) != 1:
        raise TamperFailure("unable to locate active manifest")
    active = matches[0]
    required = {}
    for label in ("c2pa.soft-binding", "cawg.training-mining", "c2pa.claim.v2", "c2pa.signature"):
        node_matches = [node for node in nodes if node["path"] == active["path"] + (("c2pa.assertions", label) if label in ("c2pa.soft-binding", "cawg.training-mining") else (label,))]
        if len(node_matches) != 1:
            raise TamperFailure(f"unable to locate {label}")
        required[label] = node_matches[0]
    return {"cabx": cabx, "file_data_offset": file_data_offset, "nodes": nodes, "active": active, **required}


def make_pixel_tamper(source: Path, target: Path) -> dict[str, Any]:
    temp = target.with_name(f".{target.stem}-{uuid.uuid4().hex}.png")
    try:
        with Image.open(source) as image:
            image.load()
            changed = image.copy()
        draw = ImageDraw.Draw(changed)
        draw.rectangle((16, 16, 25, 25), fill=(0, 0, 0))
        changed.save(temp, format="PNG", optimize=False, compress_level=6)
        source_chunks = png_chunks(source)
        new_idat = [(chunk["type"], chunk["data"]) for chunk in png_chunks(temp) if chunk["type"] == "IDAT"]
        rebuilt: list[tuple[str, bytes]] = []
        inserted = False
        for chunk in source_chunks:
            if chunk["type"] == "IDAT":
                if not inserted:
                    rebuilt.extend(new_idat)
                    inserted = True
                continue
            rebuilt.append((chunk["type"], chunk["data"]))
        write_png_chunks(target, rebuilt)
    finally:
        if temp.exists():
            temp.unlink()
    with Image.open(source) as original, Image.open(target) as mutated:
        original.load()
        mutated.load()
        differences = sum(
            a != b
            for a, b in zip(original.get_flattened_data(), mutated.get_flattened_data())
        )
    if differences != 100:
        raise TamperFailure(f"pixel mutation changed {differences} pixels, expected 100")
    if chunk_payload_hash(source, "caBX") != chunk_payload_hash(target, "caBX"):
        raise TamperFailure("pixel mutation changed caBX")
    return {
        "region": {"x": 16, "y": 16, "width": 10, "height": 10},
        "new_rgb": [0, 0, 0],
        "changed_pixel_count": differences,
        "dimensions_unchanged": True,
        "caBX_payload_unchanged": True,
        "metadata_stripped": False,
    }


def make_soft_binding_tamper(source: Path, target: Path, context: dict[str, Any], identifier: str) -> dict[str, Any]:
    data, payload, content = node_cbor(context["cabx"], context["c2pa.soft-binding"])
    old_value = data["blocks"][0]["value"]
    expected = f"2*{identifier}"
    if old_value != expected:
        raise TamperFailure("control soft-binding value mismatch")
    new_last = "1" if old_value[-1] == "0" else "0"
    new_value = old_value[:-1] + new_last
    relative = unique_payload_offset(payload, old_value.encode("ascii"), "soft-binding value")
    mutation = replace_cabx_byte(source, target, content["payload_start"] + relative + len(old_value) - 1, ord(new_last))
    return {"old_value": old_value, "new_value": new_value, "bit_flipped": 67, "pixels_unchanged": True, **mutation}


def make_claim_reference_tamper(source: Path, target: Path, context: dict[str, Any]) -> dict[str, Any]:
    claim, payload, content = node_cbor(context["cabx"], context["c2pa.claim.v2"])
    references = [*claim.get("created_assertions", []), *claim.get("gathered_assertions", [])]
    matches = [reference for reference in references if reference.get("url", "").endswith("/c2pa.soft-binding") or reference.get("url", "").endswith("c2pa.soft-binding")]
    if len(matches) != 1 or not isinstance(matches[0].get("hash"), bytes):
        raise TamperFailure("unable to identify one soft-binding hashed URI")
    old_digest = matches[0]["hash"]
    relative = unique_payload_offset(payload, old_digest, "soft-binding reference digest")
    new_first = old_digest[0] ^ 0x01
    mutation = replace_cabx_byte(source, target, content["payload_start"] + relative, new_first)
    new_digest = bytes([new_first]) + old_digest[1:]
    return {
        "reference_url": matches[0]["url"],
        "old_digest_hex": old_digest.hex().upper(),
        "new_digest_hex": new_digest.hex().upper(),
        "digest_bit_flipped": 0,
        "assertion_body_unchanged": True,
        "pixels_unchanged": True,
        **mutation,
    }


def make_signature_tamper(source: Path, target: Path, context: dict[str, Any]) -> dict[str, Any]:
    signature_object, payload, content = node_cbor(context["cabx"], context["c2pa.signature"])
    cose = signature_object.get("value") if isinstance(signature_object, dict) else None
    if not isinstance(cose, list) or len(cose) != 4 or not isinstance(cose[3], bytes):
        raise TamperFailure("unable to identify COSE_Sign1 signature bytes")
    signature = cose[3]
    relative = unique_payload_offset(payload, signature, "COSE signature bytes")
    index = len(signature) - 1
    new_byte = signature[index] ^ 0x01
    mutation = replace_cabx_byte(source, target, content["payload_start"] + relative + index, new_byte)
    return {
        "signature_length": len(signature),
        "signature_byte_index": index,
        "signature_bit_flipped": 0,
        "claim_unchanged": True,
        "assertions_unchanged": True,
        "pixels_unchanged": True,
        **mutation,
    }


def make_rights_tamper(source: Path, target: Path, context: dict[str, Any]) -> dict[str, Any]:
    data, payload, content = node_cbor(context["cabx"], context["cawg.training-mining"])
    if data.get("entries", {}).get("cawg.ai_inference", {}).get("use") != "notAllowed":
        raise TamperFailure("control inference right mismatch")
    key_position = unique_payload_offset(payload, b"cawg.ai_inference", "inference key")
    value_position = payload.find(b"notAllowed", key_position + len(b"cawg.ai_inference"))
    if value_position < 0:
        raise TamperFailure("unable to locate inference use value")
    new_value = "notAllowee"  # same-width invalid value; no CBOR box resizing
    mutation = replace_cabx_byte(source, target, content["payload_start"] + value_position + 9, ord("e"))
    return {
        "field": "cawg.ai_inference.use",
        "old_value": "notAllowed",
        "new_value": new_value,
        "same_width_cbor_mutation": True,
        "pixels_unchanged": True,
        **mutation,
    }


def c2patool_report(path: Path, c2patool: Path, settings: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [str(c2patool), str(path), "--detailed", "--settings", str(settings)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    report = None
    try:
        report = json.loads(completed.stdout) if completed.stdout.strip() else None
    except json.JSONDecodeError:
        pass
    return {
        "returncode": completed.returncode,
        "active_manifest": report.get("active_manifest") if isinstance(report, dict) else None,
        "validation_status": report.get("validation_status") if isinstance(report, dict) else None,
        "validation_results": report.get("validation_results") if isinstance(report, dict) else None,
        "stderr": completed.stderr.strip() or None,
    }


def collect_secondary_diagnostics(
    path: Path,
    identifier: str,
    active_label: str,
    tm: TrustMark,
    c2patool: Path,
    settings: Path,
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "idat_sha256": chunk_payload_hash(path, "IDAT"),
        "cabx_sha256": chunk_payload_hash(path, "caBX"),
        "c2patool": c2patool_report(path, c2patool, settings),
    }
    with Image.open(path) as image:
        image.load()
        payload, present, schema = tm.decode(image.convert("RGB"), MODE="binary", DETECTFIRST=False, ROTATION=False)
    diagnostics["trustmark"] = {
        "wm_present": bool(present),
        "wm_schema": int(schema),
        "payload": payload,
        "payload_match": payload == identifier,
    }
    try:
        cabx, file_offset, _, _ = extract_png_cabx(path)
        nodes = parse_jumbf(cabx)
        active = next(node for node in nodes if node["label"] == active_label and len(node["path"]) == 2)
        claim_node = next(node for node in nodes if node["path"] == active["path"] + ("c2pa.claim.v2",))
        signature_node = next(node for node in nodes if node["path"] == active["path"] + ("c2pa.signature",))
        rights_node = next(node for node in nodes if node["path"] == active["path"] + ("c2pa.assertions", "cawg.training-mining"))
        soft_node = next(node for node in nodes if node["path"] == active["path"] + ("c2pa.assertions", "c2pa.soft-binding"))
        claim, claim_payload, _ = node_cbor(cabx, claim_node)
        rights, _, _ = node_cbor(cabx, rights_node)
        soft, _, _ = node_cbor(cabx, soft_node)
        reference_checks = []
        for group in ("created_assertions", "gathered_assertions"):
            for reference in claim.get(group, []):
                check = verify_hashed_uri(reference, active, nodes, cabx, claim["alg"], file_offset)
                reference_checks.append({"url": check["url"], "match": check["match"]})
        diagnostics["claim_references"] = {
            "all_match": all(item["match"] for item in reference_checks),
            "mismatches": [item["url"] for item in reference_checks if not item["match"]],
        }
        diagnostics["rights_entries"] = rights.get("entries") if isinstance(rights, dict) else None
        diagnostics["soft_binding"] = soft

        signature_object, _, _ = node_cbor(cabx, signature_node)
        cose = signature_object["value"]
        protected_bytes, _, detached_payload, signature_bytes = cose
        protected = CborDecoder(protected_bytes).value()
        certificates = protected.get(33)
        if isinstance(certificates, bytes):
            certificates = [certificates]
        if detached_payload is not None or not certificates:
            raise ValueError("invalid detached COSE/x5chain")
        try:
            signature_result = _verify_cose_signature(
                certificates[0], build_cose_sig_structure(protected_bytes, claim_payload), signature_bytes, protected.get(1)
            )
            diagnostics["signature_valid"] = signature_result["valid"]
        except VerificationFailure as exc:
            diagnostics["signature_valid"] = False
            diagnostics["signature_error"] = str(exc)
    except Exception as exc:
        diagnostics["local_parse_error"] = f"{type(exc).__name__}: {exc}"
    return diagnostics


def verify_case(path: Path, identifier: str, tm: TrustMark, c2patool: Path, settings: Path) -> dict[str, Any]:
    try:
        verify_output(path, EXPECTED_DIMENSIONS, identifier, tm, c2patool, settings)
    except VerificationFailure as exc:
        return {"result": exc.result, "primary_reason": str(exc), "passed": False}
    return {"result": "PASS", "primary_reason": None, "passed": True}


def write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    fields = ["case", "mutation", "sha256", "must_not_pass", "expected_primary", "verifier_result", "primary_reason"]
    with path.open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        for case in cases:
            writer.writerow(
                {
                    "case": case["case"],
                    "mutation": case["mutation"],
                    "sha256": case["sha256"],
                    "must_not_pass": case["must_not_pass"],
                    "expected_primary": " / ".join(case["expected_primary"]),
                    "verifier_result": case["verifier"]["result"],
                    "primary_reason": case["verifier"]["primary_reason"],
                }
            )


def run(project: Path) -> dict[str, Any]:
    control = project / CONTROL_RELATIVE
    repeatability_path = project / REPEATABILITY_RESULT_RELATIVE
    fixture = project / FIXTURE_RELATIVE
    tamper_dir = project / "testdata/tamper"
    output_dir = project / "output/e2e/tamper"
    result_path = output_dir / "tamper_rejection_result.json"
    csv_path = output_dir / "tamper_rejection_result.csv"
    log_path = output_dir / "tamper_rejection.log"
    c2patool = project / "tools/c2patool-0.26.60/c2patool/c2patool.exe"
    targets = {
        "pixel": tamper_dir / "tamper_pixel.png",
        "soft_binding": tamper_dir / "tamper_soft_binding.png",
        "claim_reference": tamper_dir / "tamper_claim_reference.png",
        "signature": tamper_dir / "tamper_signature.png",
        "rights": tamper_dir / "tamper_cawg_rights.png",
    }
    conflicts = [str(path) for path in [*targets.values(), result_path, csv_path, log_path] if path.exists()]
    if conflicts:
        raise TamperFailure(f"refusing to overwrite existing artifacts: {conflicts}")
    if not control.is_file() or not fixture.is_file() or not repeatability_path.is_file() or not c2patool.is_file():
        raise TamperFailure("required Slice 01B artifact or c2patool is missing")

    repeatability = json.loads(repeatability_path.read_text(encoding="utf-8"))
    control_run = repeatability["runs"][0]
    identifier = control_run["identifier"]
    active_label = control_run["active_manifest"]
    previous_hashes_before = {run["output_path"]: sha256_file(Path(run["output_path"])) for run in repeatability["runs"]}
    expected_hashes = {run["output_path"]: run["output_sha256"] for run in repeatability["runs"]}
    if previous_hashes_before != expected_hashes:
        raise TamperFailure("Slice 01B output hash does not match recorded value")
    control_hash_before = sha256_file(control)
    fixture_hash_before = sha256_file(fixture)

    tamper_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(log_path)
    settings = output_dir / f".settings-{uuid.uuid4().hex}.json"
    settings.write_text("{}\n", encoding="utf-8")
    result: dict[str, Any] = {
        "phase": "Implementation Slice 01C — Signed-output Tamper Rejection",
        "status": "RUNNING",
        "started_at": datetime.now().astimezone().isoformat(),
        "finished_at": None,
        "verification_order": [
            "PNG validity and dimensions",
            "c2patool manifest readability",
            "local caBX/JUMBF/CBOR parsing",
            "claim assertion-reference digests",
            "CAWG rights assertion",
            "c2pa.soft-binding value",
            "detached COSE signature",
            "c2patool validation status including asset hard binding",
            "TrustMark decode and identifier match",
        ],
        "control": None,
        "cases": [],
        "source_integrity": None,
        "summary": None,
        "exception": None,
    }
    try:
        tm = TrustMark(
            verbose=False,
            model_type="P",
            encoding_type=TrustMark.Encoding.BCH_4,
            loadRemover=False,
            loadBBoxDetector=False,
        )
        control_verification = verify_output(control, EXPECTED_DIMENSIONS, identifier, tm, c2patool, settings)
        result["control"] = {
            "path": str(control.resolve()),
            "sha256": control_hash_before,
            "verifier": "PASS",
            "wm_present": control_verification["trustmark"]["wm_present"],
            "schema": control_verification["trustmark"]["wm_schema"],
            "rights_present": control_verification["c2pa"]["rights_assertion_present"],
            "soft_binding_match": control_verification["soft_binding"]["match"],
            "claim_references_match": control_verification["c2pa"]["all_assertion_digests_match"],
            "signature_valid": control_verification["signature"]["valid"],
        }
        logger.info("Control PASS path=%s sha256=%s", control.resolve(), control_hash_before)
        context = active_context(control, active_label)

        mutations = [
            ("pixel", "pixel-only 10x10 RGB mutation", ["FAIL_C2PA", "FAIL_TRUSTMARK"], make_pixel_tamper(control, targets["pixel"])),
            ("soft_binding", "c2pa.soft-binding identifier bit mutation", ["FAIL_C2PA", "FAIL_SOFT_BINDING"], make_soft_binding_tamper(control, targets["soft_binding"], context, identifier)),
            ("claim_reference", "soft-binding hashed-URI digest bit mutation", ["FAIL_C2PA"], make_claim_reference_tamper(control, targets["claim_reference"], context)),
            ("signature", "COSE_Sign1 signature bit mutation", ["FAIL_SIGNATURE"], make_signature_tamper(control, targets["signature"], context)),
            ("rights", "cawg.ai_inference.use byte mutation", ["FAIL_C2PA"], make_rights_tamper(control, targets["rights"], context)),
        ]
        for case_name, mutation_name, expected_primary, mutation_detail in mutations:
            path = targets[case_name]
            verifier = verify_case(path, identifier, tm, c2patool, settings)
            diagnostics = collect_secondary_diagnostics(path, identifier, active_label, tm, c2patool, settings)
            case = {
                "case": case_name,
                "path": str(path.resolve()),
                "mutation": mutation_name,
                "mutation_detail": mutation_detail,
                "sha256": sha256_file(path),
                "must_not_pass": True,
                "expected_primary": expected_primary,
                "verifier": verifier,
                "classification_acceptable": verifier["result"] in expected_primary,
                "secondary_diagnostics": diagnostics,
            }
            result["cases"].append(case)
            logger.info(
                "case=%s result=%s acceptable=%s sha256=%s reason=%s",
                case_name, verifier["result"], case["classification_acceptable"], case["sha256"], verifier["primary_reason"],
            )

        control_hash_after = sha256_file(control)
        fixture_hash_after = sha256_file(fixture)
        previous_hashes_after = {path: sha256_file(Path(path)) for path in previous_hashes_before}
        source_integrity = {
            "control_sha256_before": control_hash_before,
            "control_sha256_after": control_hash_after,
            "control_unchanged": control_hash_before == control_hash_after,
            "fixture_sha256_before": fixture_hash_before,
            "fixture_sha256_after": fixture_hash_after,
            "fixture_unchanged": fixture_hash_before == fixture_hash_after,
            "slice_01b_output_hashes_before": previous_hashes_before,
            "slice_01b_output_hashes_after": previous_hashes_after,
            "all_slice_01b_outputs_unchanged": previous_hashes_before == previous_hashes_after,
        }
        result["source_integrity"] = source_integrity
        rejected = sum(not case["verifier"]["passed"] for case in result["cases"])
        classified = sum(case["classification_acceptable"] for case in result["cases"])
        summary = {
            "case_count": len(result["cases"]),
            "rejected_count": rejected,
            "unexpected_pass_count": len(result["cases"]) - rejected,
            "acceptable_classification_count": classified,
            "claim_reference_classification": next(case["verifier"]["result"] for case in result["cases"] if case["case"] == "claim_reference"),
            "signature_classification": next(case["verifier"]["result"] for case in result["cases"] if case["case"] == "signature"),
        }
        result["summary"] = summary
        success = (
            rejected == len(result["cases"])
            and classified == len(result["cases"])
            and summary["claim_reference_classification"] == "FAIL_C2PA"
            and summary["signature_classification"] == "FAIL_SIGNATURE"
            and source_integrity["control_unchanged"]
            and source_integrity["fixture_unchanged"]
            and source_integrity["all_slice_01b_outputs_unchanged"]
        )
        if not success:
            raise TamperFailure(f"tamper rejection acceptance criteria failed: {summary}, {source_integrity}")
        result["status"] = "PASS"
        logger.info("PASS summary=%s", json.dumps(summary, ensure_ascii=False, sort_keys=True))
    except Exception:
        result["status"] = "PARTIAL" if result["cases"] else "STOP"
        result["exception"] = traceback.format_exc()
        logger.exception("Tamper rejection did not reach full PASS")
    finally:
        if settings.exists():
            settings.unlink()
        result["finished_at"] = datetime.now().astimezone().isoformat()
        result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        write_csv(csv_path, result["cases"])
        close_logging(logger)
    return result


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    try:
        result = run(project)
    except TamperFailure as exc:
        print(json.dumps({"result": "STOP", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"result": result["status"], "summary": result["summary"]}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
