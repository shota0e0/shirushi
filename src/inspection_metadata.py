"""Development-only, bounded C2PA/CAWG inspection; never full verification.

This module intentionally does not import the normal inspection service,
creator verifier, TrustMark, or model runtimes. Callers select fixed canary
fixtures; this is not a user-file inspection or command execution interface.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any

from PIL import Image

from runtime_paths import C2PATOOL_VERSION, RuntimeComponentError, require_c2patool
from signature_verifier import verify_cose_signature
from manifest_claim_audit import (
    CborDecoder, build_cose_sig_structure, extract_c2pa_jumbf,
    node_cbor, parse_jumbf, resolve_uri_node, verify_hashed_uri,
)

MAX_FIXTURE_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
TOOL_TIMEOUT = 20
REASONS = {
    "LIMITED_SCOPE", "C2PA_ABSENT", "C2PA_MALFORMED", "CAWG_ABSENT",
    "CAWG_UNSUPPORTED", "C2PA_INTEGRITY_FAILED",
}


class LimitedInspectionError(RuntimeError):
    """Only fixed codes cross the canary boundary, never exception paths."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fingerprint(path: Path, expected_sha256: str, expected_size: int) -> None:
    try:
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or type(expected_size) is not int or not 0 < expected_size <= MAX_FIXTURE_BYTES
                or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256)
                or path.stat().st_size != expected_size
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256.lower()):
            raise LimitedInspectionError("FIXTURE_CHANGED")
    except OSError as exc:
        raise LimitedInspectionError("FIXTURE_CHANGED") from exc


def _run_tool(args: list[str]) -> tuple[int, bytes, bytes]:
    """Read bounded pipes, enforce a deadline, and reap the direct child."""
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    read_error = threading.Event()
    try:
        child = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except OSError as exc:
        raise LimitedInspectionError("C2PATOOL_START_FAILED") from exc

    def read(stream: Any, buffer: bytearray) -> None:
        try:
            while block := stream.read(4096):
                if len(buffer) + len(block) > MAX_OUTPUT_BYTES:
                    overflow.set()
                    return
                buffer.extend(block)
        except (OSError, ValueError):
            read_error.set()

    readers = [threading.Thread(target=read, args=(stream, buffer), daemon=True)
               for stream, buffer in zip((child.stdout, child.stderr), buffers)]
    for reader in readers:
        reader.start()
    try:
        deadline = time.monotonic() + TOOL_TIMEOUT
        while child.poll() is None:
            if overflow.is_set() or read_error.is_set():
                raise LimitedInspectionError("C2PATOOL_OUTPUT_LIMIT")
            if time.monotonic() >= deadline:
                raise LimitedInspectionError("C2PATOOL_TIMEOUT")
            time.sleep(0.01)
        for reader in readers:
            reader.join(timeout=max(0, deadline - time.monotonic()))
        if any(reader.is_alive() for reader in readers):
            raise LimitedInspectionError("C2PATOOL_TIMEOUT")
        if overflow.is_set() or read_error.is_set():
            raise LimitedInspectionError("C2PATOOL_OUTPUT_LIMIT")
        return child.returncode, bytes(buffers[0]), bytes(buffers[1])
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()
        for reader in readers:
            reader.join(timeout=1)


def _strict_json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject(_: str) -> None:
        raise ValueError("non-finite number")

    result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject)
    if not isinstance(result, dict):
        raise ValueError("report object required")
    return result


def _base(sha: str, size: int, image_format: str) -> dict[str, Any]:
    return {
        "contract": "shirushi-limited-inspection", "contractVersion": 1,
        "overall": "INCOMPLETE", "reasonCode": "C2PA_MALFORMED",
        "trustmark": "NOT_CHECKED", "fullVerificationPerformed": False,
        "successMotionEligible": False,
        "source": {"sha256": sha.lower(), "size": size, "format": image_format},
        "c2pa": {"state": "NOT_INSPECTED", "presence": "UNKNOWN", "parse": False,
                 "assertionDigestsValid": False, "assetBindingValid": False,
                 "signature": "INDETERMINATE", "trustValidated": False},
        "cawg": {"state": "NOT_INSPECTED", "presence": "UNKNOWN",
                 "aiTrainingUse": "NO_PERMISSION_INFO", "aiInferenceUse": "NO_PERMISSION_INFO"},
    }


def _inspect_manifest(path: Path, report: dict[str, Any], result: dict[str, Any]) -> None:
    active_label = report.get("active_manifest")
    if not active_label:
        result["reasonCode"] = "C2PA_ABSENT"
        result["c2pa"]["presence"] = "ABSENT"
        return
    result["c2pa"]["presence"] = "PRESENT"
    cabx, offset, _, _, _ = extract_c2pa_jumbf(path)
    nodes = parse_jumbf(cabx)
    active, = [node for node in nodes if node["label"] == active_label and len(node["path"]) == 2]
    claim_node, = [node for node in nodes if node["path"] == active["path"] + ("c2pa.claim.v2",)]
    claim, payload, _ = node_cbor(cabx, claim_node)
    signature_node = resolve_uri_node(claim["signature"], active, nodes)
    if signature_node["path"] != active["path"] + ("c2pa.signature",):
        raise ValueError("unexpected signature target")
    signature, _, _ = node_cbor(cabx, signature_node)
    result["c2pa"]["parse"] = True
    references = [reference for group in ("created_assertions", "gathered_assertions")
                  for reference in claim.get(group, [])]
    checks = [verify_hashed_uri(ref, active, nodes, cabx, claim["alg"], offset) for ref in references]
    result["reasonCode"] = "C2PA_INTEGRITY_FAILED"
    if not checks or not all(check["match"] for check in checks):
        return
    result["c2pa"]["assertionDigestsValid"] = True
    if not isinstance(signature, dict) or signature.get("cbor_tag") != 18:
        return
    protected_bytes, _, detached, signed = signature["value"]
    decoder = CborDecoder(protected_bytes)
    protected = decoder.value()
    if decoder.offset != len(protected_bytes) or detached is not None:
        return
    certificates = protected[33]
    certificate = certificates if isinstance(certificates, bytes) else certificates[0]
    verified = verify_cose_signature(certificate, build_cose_sig_structure(protected_bytes, payload), signed, protected[1])
    if not verified.valid:
        return
    result["c2pa"]["signature"] = "PREVIEW"
    validation = report.get("validation_results") or {}
    active_validation = validation.get("activeManifest") or {}
    failures = list(active_validation.get("failure") or [])
    for ingredient in validation.get("ingredientDeltas") or []:
        failures.extend((ingredient.get("validationDeltas") or {}).get("failure") or [])
    # The fixture uses a test certificate, never a production trust assertion.
    if report.get("validation_status") or any(item.get("code") != "signingCredential.untrusted" for item in failures):
        return
    if not any(item.get("code") == "assertion.dataHash.match" for item in active_validation.get("success") or []):
        return
    result["c2pa"].update(state="INSPECTED", assetBindingValid=True)
    rights = [node for node in nodes if node["path"] == active["path"] + ("c2pa.assertions", "cawg.training-mining")]
    if not rights:
        result["cawg"]["presence"] = "ABSENT"
        result["reasonCode"] = "CAWG_ABSENT"
        return
    right, = rights
    result["cawg"]["presence"] = "PRESENT"
    # Rights must be in this signed claim, not merely an unreferenced box.
    if not any(check["target"]["path"] == list(right["path"]) for check in checks):
        return
    data, _, _ = node_cbor(cabx, right)
    expected = {"cawg.ai_inference": {"use": "notAllowed"},
                "cawg.ai_generative_training": {"use": "notAllowed"}}
    if not isinstance(data, dict) or data.get("entries") != expected:
        result["reasonCode"] = "CAWG_UNSUPPORTED"
        return
    result["cawg"].update(state="INSPECTED", aiTrainingUse="NOT_WANTED", aiInferenceUse="NOT_WANTED")
    result.update(overall="LIMITED_INSPECTION", reasonCode="LIMITED_SCOPE")


def inspect_limited(fixture: Path, c2patool: Path, settings: Path, *, expected_sha256: str, expected_size: int) -> dict[str, Any]:
    """Inspect a fingerprint-bound fixture with the pinned local executable."""
    _fingerprint(fixture, expected_sha256, expected_size)
    if not c2patool.is_absolute() or c2patool.is_symlink():
        raise LimitedInspectionError("C2PATOOL_INTEGRITY_FAILED")
    try:
        executable = require_c2patool(c2patool)
    except RuntimeComponentError as exc:
        raise LimitedInspectionError(exc.code) from exc
    # Fixed empty verifier settings: no network-fetch or trust-anchor overrides.
    try:
        if not settings.is_absolute() or settings.is_symlink() or settings.stat().st_size > 64 or _strict_json(settings.read_bytes()) != {}:
            raise ValueError("invalid settings")
    except (OSError, ValueError) as exc:
        raise LimitedInspectionError("SETTINGS_INVALID") from exc
    try:
        with Image.open(fixture) as image:
            if image.format not in {"PNG", "JPEG"} or not 0 < image.width * image.height <= MAX_PIXELS:
                raise ValueError("invalid image bounds")
            image_format = image.format
            image.load()
    except Exception as exc:
        raise LimitedInspectionError("FIXTURE_INVALID") from exc
    result = _base(expected_sha256, expected_size, image_format)
    try:
        status, version, _ = _run_tool([str(executable), "--version"])
        if status != 0 or version.decode("utf-8").strip() != "c2patool " + C2PATOOL_VERSION:
            raise LimitedInspectionError("C2PATOOL_VERSION_MISMATCH")
        status, stdout, stderr = _run_tool([str(executable), str(fixture), "--detailed", "--settings", str(settings)])
        if status != 0:
            if b"No claim found" in stderr:
                result["reasonCode"] = "C2PA_ABSENT"
                result["c2pa"]["presence"] = "ABSENT"
        else:
            try:
                _inspect_manifest(fixture, _strict_json(stdout), result)
            except Exception:
                # Parser/crypto diagnostics never include path/metadata text.
                result["reasonCode"] = "C2PA_MALFORMED"
    finally:
        _fingerprint(fixture, expected_sha256, expected_size)
        try:
            require_c2patool(executable)
        except RuntimeComponentError as exc:
            raise LimitedInspectionError(exc.code) from exc
    validate_result(result)
    return result


def _validate_result(result: Any, *, require_expected: bool = False) -> None:
    """Reject schema drift and every full-verification/success interpretation."""
    def require(condition: bool) -> None:
        if not condition:
            raise LimitedInspectionError("LIMITED_RESULT_INVALID")

    require(isinstance(result, dict) and set(result) == set(_base("0" * 64, 1, "PNG")))
    require(result["contract"] == "shirushi-limited-inspection" and type(result["contractVersion"]) is int and result["contractVersion"] == 1)
    require(result["overall"] in {"LIMITED_INSPECTION", "INCOMPLETE"} and result["reasonCode"] in REASONS)
    require(result["trustmark"] == "NOT_CHECKED" and result["fullVerificationPerformed"] is False and result["successMotionEligible"] is False)
    source, c2pa, cawg = result["source"], result["c2pa"], result["cawg"]
    for name in ("source", "c2pa", "cawg"):
        require(isinstance(result[name], dict) and set(result[name]) == set(_base("0" * 64, 1, "PNG")[name]))
    require(isinstance(source["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", source["sha256"]) is not None)
    require(type(source["size"]) is int and 0 < source["size"] <= MAX_FIXTURE_BYTES and source["format"] in {"PNG", "JPEG"})
    require(c2pa["state"] in {"INSPECTED", "NOT_INSPECTED"} and cawg["state"] in {"INSPECTED", "NOT_INSPECTED"})
    require(c2pa["presence"] in {"PRESENT", "ABSENT", "UNKNOWN"} and cawg["presence"] in {"PRESENT", "ABSENT", "UNKNOWN"})
    require(all(type(c2pa[key]) is bool for key in ("parse", "assertionDigestsValid", "assetBindingValid")))
    require(c2pa["signature"] in {"PREVIEW", "INDETERMINATE"} and c2pa["trustValidated"] is False)
    require(all(cawg[key] in {"NOT_WANTED", "NO_PERMISSION_INFO"} for key in ("aiTrainingUse", "aiInferenceUse")))
    expected = (c2pa == {"state": "INSPECTED", "presence": "PRESENT", "parse": True,
                         "assertionDigestsValid": True, "assetBindingValid": True,
                         "signature": "PREVIEW", "trustValidated": False}
                and cawg == {"state": "INSPECTED", "presence": "PRESENT", "aiTrainingUse": "NOT_WANTED", "aiInferenceUse": "NOT_WANTED"}
                and result["reasonCode"] == "LIMITED_SCOPE")
    if result["overall"] == "LIMITED_INSPECTION" or require_expected:
        require(expected and result["overall"] == "LIMITED_INSPECTION")
    else:
        require(result["reasonCode"] != "LIMITED_SCOPE")
    if c2pa["state"] == "INSPECTED":
        require(c2pa["presence"] == "PRESENT" and c2pa["parse"] and c2pa["assertionDigestsValid"] and c2pa["assetBindingValid"] and c2pa["signature"] == "PREVIEW")
    if cawg["state"] == "INSPECTED":
        require(expected)
    else:
        require(cawg["aiTrainingUse"] == cawg["aiInferenceUse"] == "NO_PERMISSION_INFO")


def validate_result(result: Any, *, require_expected: bool = False) -> None:
    """Validate a path-free limited result or raise a fixed boundary error."""
    try:
        _validate_result(result, require_expected=require_expected)
    except (KeyError, TypeError, ValueError) as exc:
        raise LimitedInspectionError("LIMITED_RESULT_INVALID") from exc
