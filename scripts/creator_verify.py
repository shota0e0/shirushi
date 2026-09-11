"""Local post-write verification for Creator E2E PNG/JPEG outputs."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from typing import Any, Callable

from PIL import Image
from trustmark import TrustMark

if not getattr(sys, "frozen", False):
    source_directory = Path(__file__).resolve().parent.parent / "src"
    if str(source_directory) not in sys.path:
        sys.path.insert(0, str(source_directory))

from runtime_paths import RuntimeComponentError, require_c2patool, resolve_runtime_paths
from signature_verifier import SignatureVerificationError, verify_cose_signature
from trustmark_model_manager import create_trustmark

from manifest_claim_audit import (
    CborDecoder,
    build_cose_sig_structure,
    extract_c2pa_jumbf,
    find_active_manifest,
    node_cbor,
    parse_jumbf,
    verify_hashed_uri,
)


WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


EXPECTED_ALGORITHM = "com.adobe.trustmark.P"
EXPECTED_SCHEMA = 2
EXPECTED_RIGHTS = {
    "cawg.ai_inference": {"use": "notAllowed"},
    "cawg.ai_generative_training": {"use": "notAllowed"},
}
CONTRACT_VERSION = "1.0"
RESULT_ENUM = (
    "PASS",
    "FAIL_WRITE",
    "FAIL_C2PA",
    "FAIL_TRUSTMARK",
    "FAIL_SOFT_BINDING",
    "FAIL_SIGNATURE",
)
REASON_CODES = (
    "INTERNAL_ERROR",
    "FILE_NOT_FOUND",
    "PNG_INVALID",
    "PNG_DIMENSIONS_MISMATCH",
    "JPEG_INVALID",
    "JPEG_DIMENSIONS_MISMATCH",
    "C2PA_CLAIM_MISSING",
    "C2PA_MANIFEST_UNREADABLE",
    "C2PA_CLAIM_REFERENCE_MISMATCH",
    "C2PA_ASSERTION_DIGEST_MISMATCH",
    "C2PA_ASSET_DATA_HASH_MISMATCH",
    "C2PA_VALIDATION_FAILED",
    "C2PA_RIGHTS_ASSERTION_MISSING",
    "C2PA_RIGHTS_VALUE_MISMATCH",
    "TRUSTMARK_NOT_DETECTED",
    "TRUSTMARK_SCHEMA_MISMATCH",
    "TRUSTMARK_PAYLOAD_LENGTH_MISMATCH",
    "TRUSTMARK_PAYLOAD_MISMATCH",
    "SOFT_BINDING_MISSING",
    "SOFT_BINDING_ALGORITHM_MISMATCH",
    "SOFT_BINDING_VALUE_MISMATCH",
    "SIGNATURE_MISSING",
    "SIGNATURE_INVALID",
    "SIGNATURE_VERIFIER_UNAVAILABLE",
    "SIGNATURE_VERIFICATION_ERROR",
    "C2PATOOL_MISSING",
    "C2PATOOL_INTEGRITY_FAILED",
)

DEFAULT_REASON_BY_RESULT = {
    "FAIL_WRITE": "PNG_INVALID",
    "FAIL_C2PA": "C2PA_MANIFEST_UNREADABLE",
    "FAIL_TRUSTMARK": "TRUSTMARK_PAYLOAD_MISMATCH",
    "FAIL_SOFT_BINDING": "SOFT_BINDING_VALUE_MISMATCH",
    "FAIL_SIGNATURE": "SIGNATURE_INVALID",
}

SUPPORTED_IMAGE_FORMATS = {"PNG": "png", "JPEG": "jpeg"}


class VerificationFailure(RuntimeError):
    def __init__(self, result: str, message: str, reason_code: str | None = None):
        super().__init__(message)
        if result not in RESULT_ENUM or result == "PASS":
            raise ValueError(f"invalid verification result: {result}")
        resolved_reason = reason_code or DEFAULT_REASON_BY_RESULT[result]
        if resolved_reason not in REASON_CODES:
            raise ValueError(f"invalid verification reason code: {resolved_reason}")
        self.result = result
        self.reason_code = resolved_reason


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def validate_soft_binding_data(data: Any, identifier: str) -> str:
    expected_value = f"{EXPECTED_SCHEMA}*{identifier}"
    if not isinstance(data, dict):
        raise VerificationFailure("FAIL_SOFT_BINDING", "soft-binding assertion is missing or invalid", "SOFT_BINDING_MISSING")
    if data.get("alg") != EXPECTED_ALGORITHM:
        raise VerificationFailure("FAIL_SOFT_BINDING", "soft-binding algorithm mismatch", "SOFT_BINDING_ALGORITHM_MISMATCH")
    blocks = data.get("blocks")
    if not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict):
        raise VerificationFailure("FAIL_SOFT_BINDING", "soft-binding must contain exactly one block", "SOFT_BINDING_VALUE_MISMATCH")
    value = blocks[0].get("value")
    if value != expected_value:
        raise VerificationFailure("FAIL_SOFT_BINDING", "manifest soft-binding value mismatch", "SOFT_BINDING_VALUE_MISMATCH")
    return value


def evaluate_trustmark(decoded: tuple[Any, Any, Any], identifier: str) -> dict[str, Any]:
    payload, present, schema_raw = decoded
    schema = int(schema_raw)
    payload_text = payload if isinstance(payload, str) else repr(payload)
    if not present:
        raise VerificationFailure("FAIL_TRUSTMARK", "TrustMark was not detected", "TRUSTMARK_NOT_DETECTED")
    if schema != EXPECTED_SCHEMA:
        raise VerificationFailure("FAIL_TRUSTMARK", f"unexpected TrustMark schema: {schema}", "TRUSTMARK_SCHEMA_MISMATCH")
    if len(payload_text) != 68:
        raise VerificationFailure("FAIL_TRUSTMARK", f"unexpected TrustMark payload length: {len(payload_text)}", "TRUSTMARK_PAYLOAD_LENGTH_MISMATCH")
    if payload_text != identifier:
        raise VerificationFailure("FAIL_TRUSTMARK", "decoded TrustMark identifier mismatch", "TRUSTMARK_PAYLOAD_MISMATCH")
    return {
        "wm_present": True,
        "wm_schema": schema,
        "payload": payload_text,
        "payload_length": len(payload_text),
        "payload_match": True,
        "reconstructed_soft_binding": f"{schema}*{payload_text}",
    }


def _verify_cose_signature(cert_der: bytes, data: bytes, signature: bytes, algorithm_code: int) -> dict[str, Any]:
    try:
        return asdict(verify_cose_signature(cert_der, data, signature, algorithm_code))
    except SignatureVerificationError as exc:
        reason = "SIGNATURE_INVALID" if exc.kind == "SIGNATURE_MISMATCH" else "SIGNATURE_VERIFICATION_ERROR"
        raise VerificationFailure("FAIL_SIGNATURE", str(exc), reason) from exc
    except Exception as exc:
        raise VerificationFailure(
            "FAIL_SIGNATURE",
            "independent signature verifier failed",
            "SIGNATURE_VERIFICATION_ERROR",
        ) from exc


def _run_c2patool_report(c2patool: Path, image: Path, settings: Path) -> dict[str, Any]:
    try:
        executable = require_c2patool(c2patool)
    except RuntimeComponentError as exc:
        raise VerificationFailure("FAIL_C2PA", "required c2patool component is unavailable", exc.code) from exc
    completed = subprocess.run(
        [str(executable), str(image), "--detailed", "--settings", str(settings)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        shell=False,
        creationflags=WINDOWS_NO_WINDOW,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip()
        reason = "C2PA_CLAIM_MISSING" if "No claim found" in message else "C2PA_MANIFEST_UNREADABLE"
        raise VerificationFailure("FAIL_C2PA", f"c2patool read failed: {message}", reason)
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationFailure("FAIL_C2PA", f"c2patool report is not JSON: {exc}", "C2PA_MANIFEST_UNREADABLE") from exc
    return {"returncode": completed.returncode, "report": report, "stderr": completed.stderr.strip() or None}


def _require_no_c2patool_validation_errors(c2pa_report: dict[str, Any]) -> None:
    """Reject asset hard-binding and other validation errors reported by c2patool.

    This check deliberately runs after local assertion and signature checks so a
    directly mutated claim/signature retains the most specific primary result.
    """
    report = c2pa_report["report"]
    validation_status = report.get("validation_status")
    validation_results = report.get("validation_results") or {}
    failures: list[dict[str, Any]] = []
    active_failures = (validation_results.get("activeManifest") or {}).get("failure") or []
    failures.extend(active_failures)
    for ingredient in validation_results.get("ingredientDeltas") or []:
        failures.extend((ingredient.get("validationDeltas") or {}).get("failure") or [])

    # Slice 01 uses c2patool's explicitly test-only certificate, so lack of a
    # production trust anchor is expected. It does not excuse structural,
    # cryptographic, assertion, or asset-hash failures.
    actionable_failures = [
        failure for failure in failures if failure.get("code") != "signingCredential.untrusted"
    ]
    if validation_status or actionable_failures:
        codes = {failure.get("code") for failure in actionable_failures}
        reason_code = (
            "C2PA_ASSET_DATA_HASH_MISMATCH"
            if "assertion.dataHash.mismatch" in codes
            else "C2PA_VALIDATION_FAILED"
        )
        raise VerificationFailure(
            "FAIL_C2PA",
            "c2patool reported C2PA validation errors: "
            + json.dumps(
                {"validation_status": validation_status, "failures": actionable_failures},
                ensure_ascii=False,
                sort_keys=True,
            ),
            reason_code,
        )


def verify_output(
    output: Path,
    expected_dimensions: tuple[int, int],
    identifier: str,
    tm: TrustMark,
    c2patool: Path,
    settings: Path,
) -> dict[str, Any]:
    if not output.is_file():
        raise VerificationFailure("FAIL_WRITE", "input file does not exist", "FILE_NOT_FOUND")
    try:
        with Image.open(output) as image:
            image.load()
            image_format = image.format
            if image_format not in SUPPORTED_IMAGE_FORMATS:
                raise VerificationFailure("FAIL_WRITE", "input is not PNG or JPEG", "PNG_INVALID")
            if image.size != expected_dimensions:
                reason = "JPEG_DIMENSIONS_MISMATCH" if image_format == "JPEG" else "PNG_DIMENSIONS_MISMATCH"
                raise VerificationFailure("FAIL_WRITE", "input dimensions do not match expected dimensions", reason)
            stego = image.convert("RGB")
    except VerificationFailure:
        raise
    except Exception as exc:
        reason = "JPEG_INVALID" if output.suffix.lower() in {".jpg", ".jpeg"} else "PNG_INVALID"
        raise VerificationFailure("FAIL_WRITE", f"invalid input image: {exc}", reason) from exc

    c2pa_report = _run_c2patool_report(c2patool, output, settings)
    try:
        cabx, file_data_offset, chunks, provenance, container_format = extract_c2pa_jumbf(output)
        nodes = parse_jumbf(cabx)
        # Prefer the official c2patool reader's active-manifest selection.
        # Adobe-authored fixtures also carry an iTXt provenance URL, but a
        # freshly authored c2patool PNG need not use that Adobe-specific URL.
        report_active_label = c2pa_report["report"].get("active_manifest")
        report_matches = [
            node
            for node in nodes
            if node["label"] == report_active_label and len(node["path"]) == 2
        ]
        if len(report_matches) == 1:
            active = report_matches[0]
        else:
            active = find_active_manifest(nodes, provenance)
        claim_node = next(node for node in nodes if node["path"] == active["path"] + ("c2pa.claim.v2",))
        signature_node = next(node for node in nodes if node["path"] == active["path"] + ("c2pa.signature",))
        claim, claim_payload, _ = node_cbor(cabx, claim_node)
        signature_object, _, _ = node_cbor(cabx, signature_node)
    except Exception as exc:
        raise VerificationFailure("FAIL_C2PA", f"manifest/claim parse failed: {exc}", "C2PA_MANIFEST_UNREADABLE") from exc

    reference_results = []
    try:
        for group in ("created_assertions", "gathered_assertions"):
            for reference in claim.get(group, []):
                check = verify_hashed_uri(reference, active, nodes, cabx, claim["alg"], file_data_offset)
                check["reference_group"] = group
                reference_results.append(check)
    except Exception as exc:
        raise VerificationFailure("FAIL_C2PA", f"assertion reference verification failed: {exc}", "C2PA_CLAIM_REFERENCE_MISMATCH") from exc
    if not reference_results or not all(item["match"] for item in reference_results):
        raise VerificationFailure("FAIL_C2PA", "one or more assertion digests do not match", "C2PA_ASSERTION_DIGEST_MISMATCH")

    try:
        rights_node = next(node for node in nodes if node["path"] == active["path"] + ("c2pa.assertions", "cawg.training-mining"))
        rights_data, _, _ = node_cbor(cabx, rights_node)
    except Exception as exc:
        raise VerificationFailure("FAIL_C2PA", f"rights assertion missing/unreadable: {exc}", "C2PA_RIGHTS_ASSERTION_MISSING") from exc
    try:
        soft_node = next(node for node in nodes if node["path"] == active["path"] + ("c2pa.assertions", "c2pa.soft-binding"))
        soft_data, _, _ = node_cbor(cabx, soft_node)
    except Exception as exc:
        raise VerificationFailure("FAIL_SOFT_BINDING", f"soft-binding assertion missing/unreadable: {exc}", "SOFT_BINDING_MISSING") from exc
    if not isinstance(rights_data, dict) or rights_data.get("entries") != EXPECTED_RIGHTS:
        raise VerificationFailure("FAIL_C2PA", "CAWG rights assertion mismatch", "C2PA_RIGHTS_VALUE_MISMATCH")
    manifest_soft_binding = validate_soft_binding_data(soft_data, identifier)

    if not isinstance(signature_object, dict) or signature_object.get("cbor_tag") != 18:
        raise VerificationFailure("FAIL_SIGNATURE", "signature is not tagged COSE_Sign1", "SIGNATURE_MISSING")
    cose = signature_object.get("value")
    if not isinstance(cose, list) or len(cose) != 4:
        raise VerificationFailure("FAIL_SIGNATURE", "invalid COSE_Sign1 structure", "SIGNATURE_MISSING")
    protected_bytes, _, detached_payload, signature_bytes = cose
    protected = CborDecoder(protected_bytes).value()
    algorithm_code = protected.get(1)
    certificates = protected.get(33)
    if isinstance(certificates, bytes):
        certificates = [certificates]
    if detached_payload is not None or not isinstance(certificates, list) or not certificates:
        raise VerificationFailure("FAIL_SIGNATURE", "invalid detached payload or x5chain", "SIGNATURE_MISSING")
    signature_verification = _verify_cose_signature(
        certificates[0], build_cose_sig_structure(protected_bytes, claim_payload), signature_bytes, algorithm_code
    )

    # Asset hard-binding validation is independent of assertion-reference and
    # COSE verification.  In particular, a pixel-only mutation leaves all JUMBF
    # assertion bytes and the signature intact but must still be rejected.
    _require_no_c2patool_validation_errors(c2pa_report)

    trustmark = evaluate_trustmark(
        tm.decode(stego, MODE="binary", DETECTFIRST=False, ROTATION=False), identifier
    )
    if trustmark["reconstructed_soft_binding"] != manifest_soft_binding:
        raise VerificationFailure("FAIL_SOFT_BINDING", "decoded and manifest soft-binding values differ", "SOFT_BINDING_VALUE_MISMATCH")
    soft_reference = next(
        item for item in reference_results if item["target"]["path"] == list(soft_node["path"])
    )
    return {
        "file": {"exists": True, "format": container_format, "dimensions": list(expected_dimensions), "sha256": sha256_file(output)},
        "c2pa": {
            "manifest_present": True,
            "active_manifest": active["label"],
            "claim_instance_id": claim.get("instanceID"),
            "claim_generator_info": claim.get("claim_generator_info"),
            "rights_assertion_present": True,
            "rights_entries": rights_data["entries"],
            "assertion_reference_count": len(reference_results),
            "all_assertion_digests_match": True,
            "soft_binding_assertion_digest": soft_reference["stored_digest_hex"].upper(),
            "soft_binding_assertion_digest_match": soft_reference["match"],
            "c2patool_validation_status": c2pa_report["report"].get("validation_status"),
            "c2patool_stderr": c2pa_report["stderr"],
        },
        "soft_binding": {"algorithm": EXPECTED_ALGORITHM, "manifest_value": manifest_soft_binding, "match": True},
        "trustmark": trustmark,
        "signature": signature_verification,
    }


def _discover_manifest_identifier(input_path: Path, c2patool: Path, settings: Path) -> str:
    """Read the signed soft-binding identifier used as the verification expectation."""
    c2pa_report = _run_c2patool_report(c2patool, input_path, settings)
    active_label = c2pa_report["report"].get("active_manifest")
    if not active_label:
        raise VerificationFailure("FAIL_C2PA", "active C2PA claim is missing", "C2PA_CLAIM_MISSING")
    try:
        cabx, _, _, provenance, _ = extract_c2pa_jumbf(input_path)
        nodes = parse_jumbf(cabx)
        matches = [node for node in nodes if node["label"] == active_label and len(node["path"]) == 2]
        active = matches[0] if len(matches) == 1 else find_active_manifest(nodes, provenance)
        soft_node = next(
            node
            for node in nodes
            if node["path"] == active["path"] + ("c2pa.assertions", "c2pa.soft-binding")
        )
        soft_data, _, _ = node_cbor(cabx, soft_node)
    except StopIteration as exc:
        raise VerificationFailure("FAIL_SOFT_BINDING", "soft-binding assertion is missing", "SOFT_BINDING_MISSING") from exc
    except VerificationFailure:
        raise
    except Exception as exc:
        raise VerificationFailure("FAIL_C2PA", f"manifest discovery failed: {exc}", "C2PA_MANIFEST_UNREADABLE") from exc
    if not isinstance(soft_data, dict):
        raise VerificationFailure("FAIL_SOFT_BINDING", "soft-binding assertion is invalid", "SOFT_BINDING_MISSING")
    blocks = soft_data.get("blocks")
    value = blocks[0].get("value") if isinstance(blocks, list) and len(blocks) == 1 and isinstance(blocks[0], dict) else None
    if not isinstance(value, str) or not re.fullmatch(r"2\*[01]{68}", value):
        raise VerificationFailure("FAIL_SOFT_BINDING", "soft-binding value is not schema 2 / 68-bit binary", "SOFT_BINDING_VALUE_MISMATCH")
    return value[2:]


def _empty_contract(input_path: Path) -> dict[str, Any]:
    return {
        "contractVersion": CONTRACT_VERSION,
        "result": None,
        "reasonCode": None,
        "message": None,
        "input": {
            "path": str(input_path.resolve()),
            "sha256": None,
            "format": None,
            "dimensions": {"width": None, "height": None},
        },
        "rights": {"preset": "AI利用拒否", "verified": False},
        "trustmark": {"present": None, "schema": None, "payloadLength": None, "payloadMatch": None},
        "softBinding": {"present": None, "algorithm": None, "match": None},
        "c2pa": {"claimPresent": False, "assertionDigestsMatch": None},
        "signature": {"present": None, "valid": None, "trustValidated": False},
        "diagnostics": [],
    }


class _LazyTrustMark:
    """Delay model loading for failures detected before watermark decoding."""

    def __init__(self, factory: Callable[[], TrustMark] = create_trustmark) -> None:
        self._decoder: TrustMark | None = None
        self._factory = factory

    def decode(self, *args: Any, **kwargs: Any) -> Any:
        if self._decoder is None:
            candidate = self._factory()
            self._decoder = candidate
        return self._decoder.decode(*args, **kwargs)


def verify_contract(
    input_path: Path,
    c2patool: Path,
    settings: Path,
    tm: TrustMark | None = None,
    trustmark_factory: Callable[[], TrustMark] = create_trustmark,
) -> dict[str, Any]:
    """Return the stable v1.0 machine-readable verifier contract."""
    contract = _empty_contract(input_path)
    try:
        if not input_path.is_file():
            raise VerificationFailure("FAIL_WRITE", "input file does not exist", "FILE_NOT_FOUND")
        contract["input"]["sha256"] = sha256_file(input_path)
        try:
            with Image.open(input_path) as image:
                image.load()
                image_format = image.format
                if image_format not in SUPPORTED_IMAGE_FORMATS:
                    raise VerificationFailure("FAIL_WRITE", "input is not PNG or JPEG", "PNG_INVALID")
                dimensions = image.size
        except VerificationFailure:
            raise
        except Exception as exc:
            reason = "JPEG_INVALID" if input_path.suffix.lower() in {".jpg", ".jpeg"} else "PNG_INVALID"
            raise VerificationFailure("FAIL_WRITE", f"invalid input image: {exc}", reason) from exc
        contract["input"]["format"] = SUPPORTED_IMAGE_FORMATS[image_format]
        contract["input"]["dimensions"] = {"width": dimensions[0], "height": dimensions[1]}

        identifier = _discover_manifest_identifier(input_path, c2patool, settings)
        contract["c2pa"]["claimPresent"] = True
        decoder = tm or _LazyTrustMark(trustmark_factory)
        verification = verify_output(input_path, dimensions, identifier, decoder, c2patool, settings)
        contract.update({"result": "PASS", "reasonCode": None, "message": "Verification passed."})
        contract["rights"]["verified"] = True
        contract["trustmark"] = {
            "present": verification["trustmark"]["wm_present"],
            "schema": verification["trustmark"]["wm_schema"],
            "payloadLength": verification["trustmark"]["payload_length"],
            "payloadMatch": verification["trustmark"]["payload_match"],
        }
        contract["softBinding"] = {
            "present": True,
            "algorithm": verification["soft_binding"]["algorithm"],
            "match": verification["soft_binding"]["match"],
        }
        contract["c2pa"] = {"claimPresent": True, "assertionDigestsMatch": True}
        contract["signature"] = {
            "present": True,
            "valid": verification["signature"]["valid"],
            "trustValidated": verification["signature"]["certificate_trust_validation_performed"],
        }
        contract["diagnostics"] = [
            {"code": f"{image_format}_VALID", "status": "pass"},
            {"code": "C2PA_CLAIM_PRESENT", "status": "pass"},
            {"code": "C2PA_ASSERTION_DIGESTS_MATCH", "status": "pass"},
            {"code": "C2PA_RIGHTS_VALID", "status": "pass"},
            {"code": "SOFT_BINDING_MATCH", "status": "pass"},
            {"code": "SIGNATURE_VALID", "status": "pass"},
            {"code": "C2PA_ASSET_DATA_HASH_MATCH", "status": "pass"},
            {"code": "TRUSTMARK_PAYLOAD_MATCH", "status": "pass"},
        ]
    except VerificationFailure as exc:
        contract.update({"result": exc.result, "reasonCode": exc.reason_code, "message": str(exc)})
        contract["diagnostics"] = [{"code": exc.reason_code, "status": "fail"}]
    return contract


def format_human_output(contract: dict[str, Any]) -> str:
    if contract["result"] == "PASS":
        return "\n".join(
            [
                "VERIFICATION PASS",
                "",
                "Rights:",
                "  AI利用拒否",
                "",
                "C2PA:",
                "  Claim: OK",
                "  Assertions: OK",
                "",
                "TrustMark:",
                f"  Present: {'YES' if contract['trustmark']['present'] else 'NO'}",
                f"  Schema: {contract['trustmark']['schema']}",
                f"  Payload: {'MATCH' if contract['trustmark']['payloadMatch'] else 'MISMATCH'}",
                "",
                "Soft Binding:",
                "  MATCH",
                "",
                "Signature:",
                "  VALID",
                "  Trust validation: NOT CHECKED",
            ]
        )
    return "\n".join(
        [
            "VERIFICATION FAILED",
            "",
            "Result:",
            f"  {contract['result']}",
            "",
            "Reason:",
            f"  {contract['reasonCode']}",
            "",
            "Details:",
            f"  {contract['message']}",
        ]
    )


def contract_exit_code(contract: dict[str, Any]) -> int:
    if contract["result"] == "PASS":
        return 0
    return 2 if contract["result"] == "FAIL_WRITE" else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    runtime = resolve_runtime_paths()
    parser = argparse.ArgumentParser(description="Verify a rights-signal PNG/JPEG using contract v1.0.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument(
        "--c2patool",
        type=Path,
        default=runtime.c2patool,
    )
    return parser.parse_args(argv)


def cli(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.json_output and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.json_output and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    runtime = resolve_runtime_paths()
    settings = runtime.user_data_root / "temp" / f".creator-verify-{uuid.uuid4().hex}.json"
    try:
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text("{}\n", encoding="utf-8")
        contract = verify_contract(args.input.resolve(), args.c2patool.resolve(), settings)
        if args.json_output:
            print(json.dumps(contract, ensure_ascii=False, separators=(",", ":")))
        else:
            print(format_human_output(contract))
        return contract_exit_code(contract)
    except Exception as exc:
        internal = _empty_contract(args.input.resolve())
        internal.update(
            {
                "result": "FAIL_WRITE",
                "reasonCode": "INTERNAL_ERROR",
                "message": f"Unexpected internal error: {type(exc).__name__}: {exc}",
            }
        )
        internal["diagnostics"] = [{"code": "INTERNAL_ERROR", "status": "fail"}]
        if args.json_output:
            print(json.dumps(internal, ensure_ascii=False, separators=(",", ":")))
        else:
            print(f"INTERNAL ERROR\n\n{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        try:
            if settings.exists():
                settings.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(cli())
