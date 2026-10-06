"""Local, read-only application boundary for inspection results.

This module deliberately does not enable the inspection runtime.  Callers must
inject a prepared service factory; the default fails closed so importing this
module cannot load models, acquire resources, or start network activity.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any, Callable, Mapping, Protocol, TypedDict, cast


MAX_SOURCE_BYTES = 64 * 1024 * 1024
APPLICATION_CONTRACT = "shirushi-inspection"
APPLICATION_CONTRACT_VERSION = 1
CORE_CONTRACT_VERSION = "1.0"


class SourceContract(TypedDict):
    sha256: str
    size: int
    modifiedNs: str
    format: str


class VerificationContract(TypedDict):
    state: str
    aiTrainingUse: str
    aiInferenceUse: str
    rights: str
    cawg: str
    c2pa: str
    trustmark: str
    trustmarkPayload: str
    integrity: str
    signature: str
    durableRecovery: str
    reasonCode: str | None


class PersonalMarkContract(TypedDict):
    state: str
    reason: str


class InspectionResultContract(TypedDict):
    contract: str
    contractVersion: int
    source: SourceContract
    verification: VerificationContract
    personalMark: PersonalMarkContract
    successMotionEligible: bool


class _InspectionService(Protocol):
    def inspect(self, input_path: Path) -> object: ...


ERROR_CODES = frozenset(
    {
        "INSPECTION_RUNTIME_NOT_PREPARED",
        "INSPECTION_SOURCE_NOT_FOUND",
        "INSPECTION_SOURCE_LINKED",
        "INSPECTION_SOURCE_NOT_REGULAR",
        "INSPECTION_SOURCE_TOO_LARGE",
        "INSPECTION_SOURCE_UNAVAILABLE",
        "INSPECTION_SOURCE_CHANGED",
        "INSPECTION_SNAPSHOT_CHANGED",
        "INSPECTION_SERVICE_FAILED",
        "INSPECTION_RESULT_INVALID",
        "INSPECTION_CLEANUP_FAILED",
    }
)


class InspectionApplicationError(RuntimeError):
    """Code-only boundary error; public messages never expose raw exceptions."""

    def __init__(self, code: str) -> None:
        safe_code = code if code in ERROR_CODES else "INSPECTION_SERVICE_FAILED"
        self.code = safe_code
        super().__init__(safe_code)


_RESULT_KEYS = frozenset(
    {"contract", "contractVersion", "source", "verification", "personalMark", "successMotionEligible"}
)
_SOURCE_KEYS = frozenset({"sha256", "size", "modifiedNs", "format"})
_VERIFICATION_KEYS = frozenset(
    {
        "state",
        "aiTrainingUse",
        "aiInferenceUse",
        "rights",
        "cawg",
        "c2pa",
        "trustmark",
        "trustmarkPayload",
        "integrity",
        "signature",
        "durableRecovery",
        "reasonCode",
    }
)
_PERSONAL_MARK_KEYS = frozenset({"state", "reason"})

_STATES = frozenset(
    {"COMPLETE", "NO_INTENT", "IDENTIFIER_ONLY", "CANNOT_VERIFY", "MALFORMED_OR_UNSUPPORTED"}
)
_AI_USE = frozenset({"NOT_WANTED", "NO_PERMISSION_INFO", "INDETERMINATE"})
_RIGHTS = frozenset({"DETECTED", "NOT_DETECTED", "PARTIAL"})
_CAWG = frozenset({"DETECTED", "NOT_DETECTED", "VERIFICATION_FAILED", "INDETERMINATE", "UNKNOWN"})
_C2PA = frozenset({"DETECTED", "NOT_DETECTED", "VERIFICATION_FAILED", "UNKNOWN"})
_TRUSTMARK = frozenset({"DETECTED", "NOT_DETECTED", "DECODE_FAILED", "UNKNOWN"})
_TRUSTMARK_PAYLOAD = frozenset({"MATCH", "MISMATCH", "INVALID", "NOT_AVAILABLE", "UNKNOWN", "VALID_UNBOUND"})
_INTEGRITY = frozenset({"OK", "VERIFICATION_FAILED", "UNKNOWN"})
_SIGNATURE = frozenset({"PREVIEW", "TRUSTED", "INDETERMINATE"})
_DURABLE_RECOVERY = frozenset({"NONE", "IDENTIFIER_RECOVERED"})
_FORMATS = frozenset({"png", "jpeg", "unknown"})
_VERIFIER_RESULTS = frozenset(
    {"PASS", "FAIL_WRITE", "FAIL_C2PA", "FAIL_TRUSTMARK", "FAIL_SOFT_BINDING", "FAIL_SIGNATURE"}
)
_MALFORMED_REASONS = frozenset(
    {"PNG_INVALID", "PNG_DIMENSIONS_MISMATCH", "JPEG_INVALID", "JPEG_DIMENSIONS_MISMATCH"}
)
_REASON_CODES = frozenset(
    {
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
    }
)
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"0|[1-9][0-9]*\Z")


def _invalid() -> InspectionApplicationError:
    return InspectionApplicationError("INSPECTION_RESULT_INVALID")


def _exact_mapping(value: object, keys: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value.keys()) != keys:
        raise _invalid()
    return cast(Mapping[str, Any], value)


def _enum(value: object, allowed: frozenset[str]) -> str:
    if type(value) is not str or value not in allowed:
        raise _invalid()
    return cast(str, value)


def validate_inspection_result(value: object) -> InspectionResultContract:
    """Validate and detach the exact public version-1 result schema.

    Validation raises :class:`InspectionApplicationError` with the fixed
    ``INSPECTION_RESULT_INVALID`` code.  It never includes offending values.
    """

    root = _exact_mapping(value, _RESULT_KEYS)
    if root["contract"] != APPLICATION_CONTRACT or type(root["contractVersion"]) is not int:
        raise _invalid()
    if root["contractVersion"] != APPLICATION_CONTRACT_VERSION:
        raise _invalid()

    source = _exact_mapping(root["source"], _SOURCE_KEYS)
    sha256 = source["sha256"]
    size = source["size"]
    modified_ns = source["modifiedNs"]
    source_format = source["format"]
    if type(sha256) is not str or _HEX_64.fullmatch(sha256) is None:
        raise _invalid()
    if type(size) is not int or not 0 < size <= MAX_SOURCE_BYTES:
        raise _invalid()
    if (
        type(modified_ns) is not str
        or len(modified_ns) > 20
        or _DECIMAL.fullmatch(modified_ns) is None
    ):
        raise _invalid()
    _enum(source_format, _FORMATS)

    verification = _exact_mapping(root["verification"], _VERIFICATION_KEYS)
    state = _enum(verification["state"], _STATES)
    ai_training = _enum(verification["aiTrainingUse"], _AI_USE)
    ai_inference = _enum(verification["aiInferenceUse"], _AI_USE)
    rights = _enum(verification["rights"], _RIGHTS)
    cawg = _enum(verification["cawg"], _CAWG)
    c2pa = _enum(verification["c2pa"], _C2PA)
    trustmark = _enum(verification["trustmark"], _TRUSTMARK)
    payload = _enum(verification["trustmarkPayload"], _TRUSTMARK_PAYLOAD)
    integrity = _enum(verification["integrity"], _INTEGRITY)
    signature = _enum(verification["signature"], _SIGNATURE)
    recovery = _enum(verification["durableRecovery"], _DURABLE_RECOVERY)
    reason = verification["reasonCode"]
    if reason is not None and (type(reason) is not str or reason not in _REASON_CODES):
        raise _invalid()

    if ai_training != ai_inference:
        raise _invalid()
    required_ai = {
        "DETECTED": "NOT_WANTED",
        "NOT_DETECTED": "NO_PERMISSION_INFO",
        "PARTIAL": "INDETERMINATE",
    }[rights]
    if ai_training != required_ai:
        raise _invalid()
    if integrity == "OK" and state != "COMPLETE":
        raise _invalid()
    if c2pa == "NOT_DETECTED" and integrity != "UNKNOWN":
        raise _invalid()
    if trustmark == "NOT_DETECTED" and payload != "NOT_AVAILABLE":
        raise _invalid()
    if trustmark == "DECODE_FAILED" and payload not in {"INVALID", "UNKNOWN"}:
        raise _invalid()
    if trustmark == "UNKNOWN" and payload != "UNKNOWN":
        raise _invalid()
    if trustmark == "DETECTED" and payload not in {"MATCH", "MISMATCH", "VALID_UNBOUND"}:
        raise _invalid()
    if payload == "MATCH" and state != "COMPLETE":
        raise _invalid()
    identifier_signal = (
        c2pa == "NOT_DETECTED"
        and trustmark == "DETECTED"
        and payload == "VALID_UNBOUND"
    )
    if (recovery == "IDENTIFIER_RECOVERED") != identifier_signal:
        raise _invalid()

    complete_tuple = (
        ai_training == "NOT_WANTED"
        and rights == "DETECTED"
        and cawg == "DETECTED"
        and c2pa == "DETECTED"
        and trustmark == "DETECTED"
        and payload == "MATCH"
        and integrity == "OK"
        and signature in {"PREVIEW", "TRUSTED"}
        and recovery == "NONE"
        and reason is None
    )
    no_intent_tuple = (
        ai_training == "NO_PERMISSION_INFO"
        and rights == "NOT_DETECTED"
        and cawg == "UNKNOWN"
        and c2pa == "NOT_DETECTED"
        and trustmark == "NOT_DETECTED"
        and payload == "NOT_AVAILABLE"
        and integrity == "UNKNOWN"
        and signature == "INDETERMINATE"
        and recovery == "NONE"
    )
    identifier_only_tuple = (
        ai_training == "NO_PERMISSION_INFO"
        and rights == "NOT_DETECTED"
        and cawg == "UNKNOWN"
        and c2pa == "NOT_DETECTED"
        and trustmark == "DETECTED"
        and payload == "VALID_UNBOUND"
        and integrity == "UNKNOWN"
        and signature == "INDETERMINATE"
        and recovery == "IDENTIFIER_RECOVERED"
    )
    if state != "COMPLETE" and reason is None:
        raise _invalid()
    if complete_tuple:
        derived_state = "COMPLETE"
    elif no_intent_tuple and reason == "C2PA_CLAIM_MISSING":
        derived_state = "NO_INTENT"
    elif identifier_only_tuple and reason == "C2PA_CLAIM_MISSING":
        derived_state = "IDENTIFIER_ONLY"
    elif reason in _MALFORMED_REASONS:
        derived_state = "MALFORMED_OR_UNSUPPORTED"
    else:
        derived_state = "CANNOT_VERIFY"
    if state != derived_state:
        raise _invalid()
    if state in {"COMPLETE", "NO_INTENT", "IDENTIFIER_ONLY"} and source_format not in {"png", "jpeg"}:
        raise _invalid()

    personal_mark = _exact_mapping(root["personalMark"], _PERSONAL_MARK_KEYS)
    if personal_mark["state"] != "NOT_CHECKED" or personal_mark["reason"] != "READBACK_NOT_IMPLEMENTED":
        raise _invalid()
    eligible = root["successMotionEligible"]
    if type(eligible) is not bool or eligible != (state == "COMPLETE"):
        raise _invalid()

    return {
        "contract": APPLICATION_CONTRACT,
        "contractVersion": APPLICATION_CONTRACT_VERSION,
        "source": {
            "sha256": sha256,
            "size": size,
            "modifiedNs": modified_ns,
            "format": cast(str, source_format),
        },
        "verification": {
            "state": state,
            "aiTrainingUse": ai_training,
            "aiInferenceUse": ai_inference,
            "rights": rights,
            "cawg": cawg,
            "c2pa": c2pa,
            "trustmark": trustmark,
            "trustmarkPayload": payload,
            "integrity": integrity,
            "signature": signature,
            "durableRecovery": recovery,
            "reasonCode": cast(str | None, reason),
        },
        "personalMark": {"state": "NOT_CHECKED", "reason": "READBACK_NOT_IMPLEMENTED"},
        "successMotionEligible": eligible,
    }


def _result_attribute(result: object, name: str) -> Any:
    try:
        return getattr(result, name)
    except Exception:
        raise _invalid() from None


def map_inspection_result(result: object, source: object) -> InspectionResultContract:
    """Map one existing InspectionResult to the path-free public contract."""

    source_mapping = _exact_mapping(source, _SOURCE_KEYS)
    result_contract_version = _result_attribute(result, "contract_version")
    if result_contract_version != CORE_CONTRACT_VERSION:
        raise _invalid()

    details = _result_attribute(result, "technical_details")
    if not isinstance(details, Mapping):
        raise _invalid()
    verifier_version = details.get("verifierContractVersion")
    verifier_result = details.get("verifierResult")
    reason = details.get("reasonCode")
    verifier_input = details.get("input")
    if (
        verifier_version != CORE_CONTRACT_VERSION
        or type(verifier_result) is not str
        or verifier_result not in _VERIFIER_RESULTS
    ):
        raise _invalid()
    if not isinstance(verifier_input, Mapping):
        raise _invalid()
    verifier_sha = verifier_input.get("sha256")
    source_sha = source_mapping.get("sha256")
    if type(verifier_sha) is not str or re.fullmatch(r"[0-9A-Fa-f]{64}", verifier_sha) is None:
        raise _invalid()
    if type(source_sha) is not str or verifier_sha.lower() != source_sha:
        raise _invalid()
    if verifier_result == "PASS":
        if reason is not None:
            raise _invalid()
    elif type(reason) is not str or reason not in _REASON_CODES:
        raise _invalid()

    fields = {
        "aiTrainingUse": _result_attribute(result, "ai_training_use"),
        "aiInferenceUse": _result_attribute(result, "ai_inference_use"),
        "rights": _result_attribute(result, "rights_status"),
        "cawg": _result_attribute(result, "cawg_status"),
        "c2pa": _result_attribute(result, "c2pa_status"),
        "trustmark": _result_attribute(result, "trustmark_status"),
        "trustmarkPayload": _result_attribute(result, "trustmark_payload_status"),
        "integrity": _result_attribute(result, "integrity_status"),
        "signature": _result_attribute(result, "signature_status"),
        "durableRecovery": _result_attribute(result, "durable_recovery"),
    }

    if verifier_result == "PASS":
        state = "COMPLETE"
    elif verifier_result == "FAIL_WRITE" and reason in _MALFORMED_REASONS:
        state = "MALFORMED_OR_UNSUPPORTED"
    elif (
        verifier_result == "FAIL_C2PA"
        and reason == "C2PA_CLAIM_MISSING"
        and fields["c2pa"] == "NOT_DETECTED"
        and fields["trustmark"] == "NOT_DETECTED"
    ):
        state = "NO_INTENT"
    elif (
        verifier_result == "FAIL_C2PA"
        and reason == "C2PA_CLAIM_MISSING"
        and fields["c2pa"] == "NOT_DETECTED"
        and fields["durableRecovery"] == "IDENTIFIER_RECOVERED"
    ):
        state = "IDENTIFIER_ONLY"
    else:
        state = "CANNOT_VERIFY"

    mapped: InspectionResultContract = {
        "contract": APPLICATION_CONTRACT,
        "contractVersion": APPLICATION_CONTRACT_VERSION,
        "source": {
            "sha256": cast(str, source_mapping["sha256"]),
            "size": cast(int, source_mapping["size"]),
            "modifiedNs": cast(str, source_mapping["modifiedNs"]),
            "format": cast(str, source_mapping["format"]),
        },
        "verification": {
            "state": state,
            **fields,
            "reasonCode": cast(str | None, reason),
        },
        "personalMark": {"state": "NOT_CHECKED", "reason": "READBACK_NOT_IMPLEMENTED"},
        "successMotionEligible": state == "COMPLETE",
    }
    return validate_inspection_result(mapped)


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
    )


def _is_link_or_reparse(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(
        getattr(value, "st_file_attributes", 0) & 0x400
    )


def _require_unlinked_path(path: Path, *, initial: bool) -> os.stat_result:
    current = Path(path.anchor)
    try:
        component_stat = os.lstat(current)
        if _is_link_or_reparse(component_stat):
            code = "INSPECTION_SOURCE_LINKED" if initial else "INSPECTION_SOURCE_CHANGED"
            raise InspectionApplicationError(code)
        for part in path.parts[1:]:
            current /= part
            component_stat = os.lstat(current)
            if _is_link_or_reparse(component_stat):
                code = "INSPECTION_SOURCE_LINKED" if initial else "INSPECTION_SOURCE_CHANGED"
                raise InspectionApplicationError(code)
        return component_stat
    except FileNotFoundError:
        code = "INSPECTION_SOURCE_NOT_FOUND" if initial else "INSPECTION_SOURCE_CHANGED"
        raise InspectionApplicationError(code) from None
    except InspectionApplicationError:
        raise
    except OSError:
        code = "INSPECTION_SOURCE_UNAVAILABLE" if initial else "INSPECTION_SOURCE_CHANGED"
        raise InspectionApplicationError(code) from None


def _open_source(path: Path, *, initial: bool) -> tuple[int, os.stat_result]:
    path_stat = _require_unlinked_path(path, initial=initial)
    if not stat.S_ISREG(path_stat.st_mode):
        code = "INSPECTION_SOURCE_NOT_REGULAR" if initial else "INSPECTION_SOURCE_CHANGED"
        raise InspectionApplicationError(code)
    if path_stat.st_nlink != 1:
        code = "INSPECTION_SOURCE_LINKED" if initial else "INSPECTION_SOURCE_CHANGED"
        raise InspectionApplicationError(code)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        code = "INSPECTION_SOURCE_UNAVAILABLE" if initial else "INSPECTION_SOURCE_CHANGED"
        raise InspectionApplicationError(code) from None
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode) or _stat_identity(opened_stat) != _stat_identity(path_stat):
            raise InspectionApplicationError("INSPECTION_SOURCE_CHANGED")
        if opened_stat.st_size > MAX_SOURCE_BYTES:
            raise InspectionApplicationError("INSPECTION_SOURCE_TOO_LARGE")
        return descriptor, opened_stat
    except Exception:
        os.close(descriptor)
        raise


def _snapshot_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return ".png"
    if suffix in {".jpg", ".jpeg"}:
        return ".jpeg"
    return ".bin"


def _copy_source(path: Path, destination: Path) -> tuple[os.stat_result, str]:
    descriptor, initial_stat = _open_source(path, initial=True)
    digest = hashlib.sha256()
    count = 0
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as source_stream, destination.open("xb") as target_stream:
            while True:
                block = source_stream.read(1024 * 1024)
                if not block:
                    break
                count += len(block)
                if count > MAX_SOURCE_BYTES:
                    raise InspectionApplicationError("INSPECTION_SOURCE_TOO_LARGE")
                digest.update(block)
                target_stream.write(block)
            target_stream.flush()
            post_copy_stat = os.fstat(source_stream.fileno())
    except InspectionApplicationError:
        raise
    except OSError:
        raise InspectionApplicationError("INSPECTION_SOURCE_UNAVAILABLE") from None
    if count != initial_stat.st_size or _stat_identity(post_copy_stat) != _stat_identity(initial_stat):
        raise InspectionApplicationError("INSPECTION_SOURCE_CHANGED")
    return initial_stat, digest.hexdigest()


def _hash_stable_file(path: Path, expected_stat: os.stat_result, error_code: str) -> str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        path_stat = os.lstat(path)
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise OSError
        descriptor = os.open(path, flags)
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            opened_stat = os.fstat(stream.fileno())
            if _stat_identity(opened_stat) != _stat_identity(expected_stat):
                raise OSError
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                if stream.tell() > MAX_SOURCE_BYTES:
                    raise OSError
            final_stat = os.fstat(stream.fileno())
        final_path_stat = os.lstat(path)
        if (
            _stat_identity(final_stat) != _stat_identity(expected_stat)
            or _stat_identity(final_path_stat) != _stat_identity(expected_stat)
        ):
            raise OSError
        return digest.hexdigest()
    except (OSError, ValueError):
        raise InspectionApplicationError(error_code) from None


def _directory_identity(value: os.stat_result) -> tuple[int, int, int]:
    return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode))


def _cleanup_directory(path: Path, expected_identity: tuple[int, int, int]) -> None:
    try:
        current = os.lstat(path)
        if (
            _is_link_or_reparse(current)
            or not stat.S_ISDIR(current.st_mode)
            or _directory_identity(current) != expected_identity
        ):
            raise OSError
        shutil.rmtree(path)
        if path.exists() or os.path.lexists(path):
            raise OSError
    except OSError:
        raise InspectionApplicationError("INSPECTION_CLEANUP_FAILED") from None


class InspectionApplication:
    """Execute one injected inspection against an integrity-bound snapshot."""

    def __init__(self, service_factory: Callable[[], _InspectionService] | None = None) -> None:
        self._service_factory = service_factory

    def inspect(self, path: str | os.PathLike[str]) -> InspectionResultContract:
        if self._service_factory is None:
            raise InspectionApplicationError("INSPECTION_RUNTIME_NOT_PREPARED")
        try:
            source_path = Path(path)
        except Exception:
            raise InspectionApplicationError("INSPECTION_SOURCE_UNAVAILABLE") from None
        if not source_path.is_absolute():
            raise InspectionApplicationError("INSPECTION_SOURCE_UNAVAILABLE")
        temporary_directory: Path | None = None
        temporary_identity: tuple[int, int, int] | None = None
        pending_error: InspectionApplicationError | None = None
        mapped: InspectionResultContract | None = None
        try:
            try:
                temporary_directory = Path(tempfile.mkdtemp(prefix="shirushi-inspection-"))
            except OSError:
                raise InspectionApplicationError("INSPECTION_SOURCE_UNAVAILABLE") from None
            try:
                temporary_stat = os.lstat(temporary_directory)
            except OSError:
                raise InspectionApplicationError("INSPECTION_CLEANUP_FAILED") from None
            if _is_link_or_reparse(temporary_stat) or not stat.S_ISDIR(temporary_stat.st_mode):
                raise InspectionApplicationError("INSPECTION_CLEANUP_FAILED")
            temporary_identity = _directory_identity(temporary_stat)
            snapshot = temporary_directory / ("source" + _snapshot_suffix(source_path))
            source_stat, source_sha = _copy_source(source_path, snapshot)
            try:
                snapshot_stat = os.lstat(snapshot)
            except OSError:
                raise InspectionApplicationError("INSPECTION_SNAPSHOT_CHANGED") from None
            snapshot_sha = _hash_stable_file(snapshot, snapshot_stat, "INSPECTION_SNAPSHOT_CHANGED")
            if snapshot_sha != source_sha:
                raise InspectionApplicationError("INSPECTION_SNAPSHOT_CHANGED")

            service_result: object | None = None
            service_error: InspectionApplicationError | None = None
            try:
                service = self._service_factory()
                inspect_method = getattr(service, "inspect", None)
                if not callable(inspect_method):
                    raise InspectionApplicationError("INSPECTION_SERVICE_FAILED")
                service_result = inspect_method(snapshot)
            except InspectionApplicationError as exc:
                service_error = (
                    InspectionApplicationError("INSPECTION_RUNTIME_NOT_PREPARED")
                    if exc.code == "INSPECTION_RUNTIME_NOT_PREPARED"
                    else InspectionApplicationError("INSPECTION_SERVICE_FAILED")
                )
            except Exception:
                service_error = InspectionApplicationError("INSPECTION_SERVICE_FAILED")

            snapshot_after_sha: str | None = None
            source_after_sha: str | None = None
            snapshot_check_error: InspectionApplicationError | None = None
            source_check_error: InspectionApplicationError | None = None
            try:
                snapshot_after_sha = _hash_stable_file(
                    snapshot, snapshot_stat, "INSPECTION_SNAPSHOT_CHANGED"
                )
            except InspectionApplicationError as exc:
                snapshot_check_error = exc
            try:
                source_after_sha = _hash_stable_file(
                    source_path, source_stat, "INSPECTION_SOURCE_CHANGED"
                )
            except InspectionApplicationError as exc:
                source_check_error = exc

            if source_check_error is not None or source_after_sha != source_sha:
                raise InspectionApplicationError("INSPECTION_SOURCE_CHANGED")
            if snapshot_check_error is not None or snapshot_after_sha != snapshot_sha:
                raise InspectionApplicationError("INSPECTION_SNAPSHOT_CHANGED")
            if service_error is not None:
                raise service_error

            input_format = _result_attribute(service_result, "input_format")
            safe_format = input_format if type(input_format) is str and input_format in {"png", "jpeg"} else "unknown"
            source_contract: SourceContract = {
                "sha256": source_sha,
                "size": source_stat.st_size,
                "modifiedNs": str(source_stat.st_mtime_ns),
                "format": safe_format,
            }
            mapped = map_inspection_result(service_result, source_contract)
        except InspectionApplicationError as exc:
            pending_error = exc
        except Exception:
            pending_error = InspectionApplicationError("INSPECTION_SERVICE_FAILED")
        finally:
            if temporary_directory is not None and temporary_identity is not None:
                try:
                    _cleanup_directory(temporary_directory, temporary_identity)
                except InspectionApplicationError as exc:
                    pending_error = exc

        if pending_error is not None:
            raise pending_error
        if mapped is None:
            raise InspectionApplicationError("INSPECTION_RESULT_INVALID")
        return mapped


__all__ = [
    "APPLICATION_CONTRACT",
    "APPLICATION_CONTRACT_VERSION",
    "ERROR_CODES",
    "InspectionApplication",
    "InspectionApplicationError",
    "InspectionResultContract",
    "MAX_SOURCE_BYTES",
    "map_inspection_result",
    "validate_inspection_result",
]
