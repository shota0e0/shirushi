"""Offline exact VC candidate/record validation; no acquisition or execution.

This proves byte identity against independently frozen source constants and
historical acquisition evidence. It does NOT perform fresh Authenticode trust,
approve distribution, establish an installed runtime, or set readiness policy.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import sys

EXPECTED_FILENAME = "vc_redist.x64.exe"
EXPECTED_SIZE = 18731856
EXPECTED_SHA256 = "843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c"
RECORD_FILENAME = "v02-vc-runtime-offline-candidate.json"
EXPECTED_RECORD_SIZE = 7362
EXPECTED_RECORD_SHA256 = "6d00952879411aff1f2f18cc3936d18233c7a0d3abdd94159a1643b5b84345e6"
EXPECTED_SIGNER_DER_SHA256 = "c30b441672c82883d92eddac6d24cb57e9960bda4486c7fb5865e74157f35850"
EXPECTED_TIMESTAMP_DER_SHA256 = "2cad33a99aef874ead5a5c5c2f9618c5f29da750234d0845ff3962c7343326b7"
MAX_RECORD = 16384
CHUNK = 1024 * 1024
REPARSE = 0x400
RESERVED = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|\Z)", re.I)


class CandidateError(Exception):
    """Closed public error category; never a private path or raw exception."""


def _fail(code: str) -> None:
    raise CandidateError(code)


def _strings(*names):
    return {name: str for name in names}


CERTIFICATE = {**_strings("subject", "issuer", "derSha256", "serial", "validFromUtc", "validToUtc", "certificateSignatureAlgorithm"), "eku": (str, 1, 8)}
CHAIN = {**_strings("validation", "revocationMode", "verificationFlags", "freshness"),
         "errorStatuses": (str, 0, 0), "certificateDerSha256Order": (str, 3, 3)}
SIGNATURE = {"index": int, **_strings("role", "digestAlgorithm", "cmsMathematicalSignature"),
             "nestedSignatureCount": int, "legacyCountersignerCount": int,
             "signer": CERTIFICATE, "chain": CHAIN,
             "timestamp": {**_strings("type", "attributeOid", "contentTypeOid", "timestampUtc", "digestAlgorithm", "cmsMathematicalSignature"), "signer": CERTIFICATE, "chain": CHAIN},
             "winVerifyTrust": {**_strings("action", "selector", "hresult", "revocation"), "verifiedIndex": int, "md2Md4Disabled": bool},
             "signTool": {**_strings("policy", "authenticodeImageDigestSha256"), "signatureIndexExitCode": int, "allSignaturesExitCode": int, "warnings": int, "errors": int, "timestampVerified": bool}}
SCHEMA = {
    "schemaVersion": int, **_strings("artifactType", "state", "filename", "sha256", "fileVersion", "productVersion", "productName", "companyName", "originalFilename", "retrievedAtUtc", "compatibilityClassification", "minimumPolicyClassification", "legalClassification"),
    "size": int, "executionCount": int, "minimumRuntimeVersion": type(None),
    "wrapper": {**_strings("machine", "format", "packageArchitectureEvidence", "innerBinaryArchitecture", "installedRuntimeArchitecture"), "subsystem": int, "sections": int, "characteristics": int},
    "source": {**_strings("documentationUrl", "requestedUrl", "finalUrl"), "redirects": ({**_strings("url", "host"), "status": int}, 1, 8), "httpStatus": int, "contentLength": int, "downloadCount": int, "rawIdentityRecordedBeforeMetadata": bool},
    "signatures": {"count": int, "secondaryCount": int, "certificateTableEntries": int, "pkcs7Size": int, **_strings("pkcs7Sha256", "policy"), "items": (SIGNATURE, 1, 8)},
    "verifier": _strings("basename", "sha256", "fileVersion", "productVersion", "verifiedAtUtc"),
    "buildEvidence": {"historicalDesktopCi": int, "currentDevelopmentCi": int, **_strings("historicalDesktopCommit", "toolsetPathVersion", "linkerFileVersion", "linkerProductVersion", "linkerBasename", "linkerSha256", "candidateVsToolsetPathVersion", "candidateVsLinkerFileVersion", "currentBuildToolsVersion", "helperBuildToolsVersion")},
    "evidenceRefs": (str, 5, 5)
}


def _shape(value, schema) -> None:
    if isinstance(schema, dict):
        if not isinstance(value, dict) or set(value) != set(schema):
            _fail("FREEZE_RECORD_SCHEMA_INVALID")
        for key, child in schema.items():
            _shape(value[key], child)
    elif isinstance(schema, tuple):
        child, minimum, maximum = schema
        if not isinstance(value, list) or not minimum <= len(value) <= maximum:
            _fail("FREEZE_RECORD_SCHEMA_INVALID")
        for item in value:
            _shape(item, child)
    elif type(value) is not schema or (schema is str and (not value or len(value) > 1024)):
        _fail("FREEZE_RECORD_SCHEMA_INVALID")


def signature_structure(value: dict) -> str:
    """Explicit narrow acquisition structure; no secondary-signature exception."""
    signatures = value["signatures"]
    if signatures["count"] != 1 or signatures["secondaryCount"] != 0 or signatures["certificateTableEntries"] != 1 or len(signatures["items"]) != 1:
        return "VC_REDIST_SIGNATURE_STRUCTURE_REVIEW_REQUIRED"
    item = signatures["items"][0]
    if (item["index"] != 0 or item["role"] != "PRIMARY_EMBEDDED_AUTHENTICODE"
            or item["nestedSignatureCount"] != 0 or item["legacyCountersignerCount"] != 0
            or item["signer"]["derSha256"] != EXPECTED_SIGNER_DER_SHA256
            or item["timestamp"]["signer"]["derSha256"] != EXPECTED_TIMESTAMP_DER_SHA256
            or item["timestamp"]["type"] != "RFC3161"):
        return "VC_REDIST_SIGNATURE_STRUCTURE_REVIEW_REQUIRED"
    return "SINGLE_KNOWN_MICROSOFT_PRIMARY_RFC3161"


def parse_record(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_RECORD or raw.startswith(b"\xef\xbb\xbf"):
        _fail("FREEZE_RECORD_SIZE_INVALID")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("DUPLICATE_JSON_FIELD")
            result[key] = value
        return result
    def nonfinite(_):
        _fail("FREEZE_RECORD_JSON_INVALID")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    except (UnicodeError, ValueError, RecursionError):
        _fail("FREEZE_RECORD_JSON_INVALID")
    _shape(value, SCHEMA)
    structure = signature_structure(value)
    if structure != "SINGLE_KNOWN_MICROSOFT_PRIMARY_RFC3161":
        _fail(structure)
    if (value["schemaVersion"] != 1 or value["artifactType"] != "VC_RUNTIME_X64_OFFLINE_CANDIDATE"
            or value["filename"] != EXPECTED_FILENAME or value["size"] != EXPECTED_SIZE
            or value["sha256"] != EXPECTED_SHA256 or value["executionCount"] != 0 or value["state"] != "CANDIDATE"):
        _fail("FREEZE_RECORD_IDENTITY_MISMATCH")
    # Exact reviewed record identity pins every nested field, trust result and
    # source without letting the record choose its own expected bytes/policy.
    if len(raw) != EXPECTED_RECORD_SIZE or hashlib.sha256(raw).hexdigest() != EXPECTED_RECORD_SHA256:
        _fail("FREEZE_RECORD_IDENTITY_MISMATCH")
    return value


def _drive_type(root: str) -> int:
    if os.name != "nt":
        _fail("WINDOWS_REQUIRED")
    function = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    function.argtypes, function.restype = [ctypes.c_wchar_p], ctypes.c_uint32
    return int(function(root))


def _path(raw: str | Path, basename: str) -> Path:
    text = str(raw)
    if len(text) > 4096 or not re.match(r"\A[A-Za-z]:[\\/]", text):
        _fail("ABSOLUTE_LOCAL_PATH_REQUIRED")
    parts = re.split(r"[\\/]", text[3:])
    if not parts or len(parts) > 32:
        _fail("PATH_INVALID")
    for part in parts:
        if not part or part in (".", "..") or part.endswith((" ", ".")) or RESERVED.match(part) or any(ord(c) < 32 or c in '<>:"|?*' for c in part):
            _fail("PATH_INVALID")
    windows = PureWindowsPath(text)
    if _drive_type(windows.drive + "\\") != 3:
        _fail("LOCAL_FIXED_DRIVE_REQUIRED")
    if windows.name != basename:
        _fail("BASENAME_INVALID")
    return Path(text)


def _stamp(value) -> tuple:
    if type(value.st_dev) is not int or value.st_dev < 0 or type(value.st_ino) is not int or value.st_ino <= 0 or type(getattr(value, "st_nlink", None)) is not int or value.st_nlink <= 0:
        _fail("FILE_IDENTITY_UNAVAILABLE")
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_nlink


def _metadata(path: Path, directory: bool = False):
    try:
        value = path.lstat()
    except OSError:
        _fail("PATH_UNAVAILABLE")
    if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & REPARSE:
        _fail("REPARSE_REJECTED")
    if not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)):
        _fail("FILE_TYPE_INVALID")
    _stamp(value)
    if not directory and value.st_nlink != 1:
        _fail("HARDLINK_REJECTED")
    return value


def _ancestors(path: Path) -> tuple:
    # Directory size/mtime may change when another process creates an unrelated
    # sibling. Preserve directory identity, not that unrelated activity; leaf
    # and open-handle size/mtime/link count remain fully checked below.
    return tuple((parent, _stamp(_metadata(parent, True))[:2]) for parent in reversed(path.parents))


def _measure(path: Path, expected_size: int, *, capture: bool = False) -> tuple[str, bytes | None]:
    if capture and expected_size > MAX_RECORD:
        _fail("FREEZE_RECORD_SIZE_INVALID")
    ancestors = _ancestors(path)
    before = _metadata(path)
    if before.st_size != expected_size:
        _fail("SIZE_MISMATCH")
    digest, count, raw = hashlib.sha256(), 0, bytearray() if capture else None
    try:
        with path.open("rb") as source:
            if _stamp(os.fstat(source.fileno())) != _stamp(before):
                _fail("SOURCE_CHANGED")
            while block := source.read(CHUNK):
                count += len(block)
                if count > expected_size:
                    _fail("SOURCE_CHANGED")
                digest.update(block)
                if capture:
                    raw.extend(block)
            after_open = os.fstat(source.fileno())
    except OSError:
        _fail("FILE_READ_FAILED")
    if count != expected_size or _stamp(after_open) != _stamp(before) or _stamp(_metadata(path)) != _stamp(before) or _ancestors(path) != ancestors:
        _fail("SOURCE_CHANGED")
    return digest.hexdigest(), bytes(raw) if capture else None


def verify_candidate(record_path: str | Path, candidate_path: str | Path) -> dict:
    record_file = _path(record_path, RECORD_FILENAME)
    _, raw = _measure(record_file, EXPECTED_RECORD_SIZE, capture=True)
    record = parse_record(raw)
    candidate = _path(candidate_path, EXPECTED_FILENAME)
    sha, _ = _measure(candidate, EXPECTED_SIZE)
    if sha != EXPECTED_SHA256:
        _fail("CANDIDATE_HASH_MISMATCH")
    return {"schemaVersion": 1, "classification": "VC_RUNTIME_CANDIDATE_IDENTITY_MATCH",
            "filename": EXPECTED_FILENAME, "size": EXPECTED_SIZE, "sha256": sha,
            "freezeRecordSha256": EXPECTED_RECORD_SHA256, "state": "CANDIDATE",
            "signatureVerification": "FROZEN_ACQUISITION_EVIDENCE_ONLY",
            "compatibilityClassification": record["compatibilityClassification"],
            "minimumPolicyClassification": record["minimumPolicyClassification"],
            "legalClassification": "LEGAL_REVIEW_REQUIRED", "executionCount": 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, help="Explicit frozen JSON record path")
    parser.add_argument("--candidate", required=True, help="Explicit existing candidate path; no acquisition")
    args = parser.parse_args(argv)
    try:
        result = verify_candidate(args.record, args.candidate)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except CandidateError as error:
        print(json.dumps({"classification": "VC_RUNTIME_CANDIDATE_IDENTITY_REJECTED", "error": str(error), "executionCount": 0}, sort_keys=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
