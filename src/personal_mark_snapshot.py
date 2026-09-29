"""Detached, immutable Python-authoritative preflight snapshot foundation.

The encoding here is a private, versioned Python application encoding.  It is
not RFC 8785 JCS, a C2PA assertion, an IPC payload, or a preflight token.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
import struct
from typing import Any, Mapping, TypeAlias

from personal_mark_v2 import (
    HandwrittenPersonalMarkV2,
    PERSONAL_MARK_V2_VERSION,
    PersonalMarkV2,
    TypedPersonalMarkV2,
    personal_mark_v2_from_mapping,
)


SNAPSHOT_ENCODING_VERSION = "shirushi-python-deterministic-v1"
SNAPSHOT_ENCODING_HEADER = b"SHIRUSHI-PREFLIGHT-SNAPSHOT\x00\x01"
MAX_SNAPSHOT_DEPTH = 16
MAX_SNAPSHOT_ITEMS = 200_000
MAX_SNAPSHOT_INPUT_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_ENCODED_BYTES = 8 * 1024 * 1024
MAX_SAFE_JSON_INTEGER = 2**53 - 1


class SnapshotValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FrozenObject:
    entries: tuple[tuple[str, "FrozenJsonValue"], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple):
            raise SnapshotValidationError("RIGHTS_NOT_FROZEN", "frozen object entries must be a tuple")
        keys: list[str] = []
        for entry in self.entries:
            if not isinstance(entry, tuple) or len(entry) != 2 or not isinstance(entry[0], str):
                raise SnapshotValidationError("RIGHTS_NOT_FROZEN", "frozen object entry is malformed")
            _validate_string(entry[0])
            _validate_frozen_value(entry[1])
            keys.append(entry[0])
        if len(keys) != len(set(keys)) or keys != sorted(keys, key=lambda key: key.encode("utf-8")):
            raise SnapshotValidationError("RIGHTS_NOT_FROZEN", "frozen object keys must be unique and sorted")

    def to_mapping(self) -> dict[str, Any]:
        _validate_frozen_graph(self)
        return {key: _thaw_unchecked(value) for key, value in self.entries}


@dataclass(frozen=True)
class FrozenArray:
    values: tuple["FrozenJsonValue", ...]

    def __post_init__(self) -> None:
        if not isinstance(self.values, tuple):
            raise SnapshotValidationError("RIGHTS_NOT_FROZEN", "frozen array values must be a tuple")
        for value in self.values:
            _validate_frozen_value(value)


FrozenJsonValue: TypeAlias = None | bool | int | float | str | FrozenObject | FrozenArray


@dataclass(frozen=True)
class TargetFingerprint:
    algorithm: str
    digest_hex: str

    def __post_init__(self) -> None:
        if self.algorithm != "sha256":
            raise SnapshotValidationError("UNSUPPORTED_FINGERPRINT_ALGORITHM", "only sha256 is supported")
        if not isinstance(self.digest_hex, str) or re.fullmatch(r"[0-9a-f]{64}", self.digest_hex) is None:
            raise SnapshotValidationError("INVALID_TARGET_FINGERPRINT", "sha256 digest must be exactly 64 lowercase hex characters")

    def to_mapping(self) -> dict[str, str]:
        return {"algorithm": self.algorithm, "digest": self.digest_hex}


@dataclass(frozen=True)
class SnapshotVersions:
    rights_schema_version: str
    preflight_protocol_version: str
    bridge_protocol_version: str
    core_service_contract_version: str
    text_policy_version: str
    personal_mark_schema_version: int = PERSONAL_MARK_V2_VERSION
    snapshot_encoding_version: str = SNAPSHOT_ENCODING_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("rights_schema_version", self.rights_schema_version),
            ("preflight_protocol_version", self.preflight_protocol_version),
            ("bridge_protocol_version", self.bridge_protocol_version),
            ("core_service_contract_version", self.core_service_contract_version),
            ("text_policy_version", self.text_policy_version),
            ("snapshot_encoding_version", self.snapshot_encoding_version),
        ):
            if not isinstance(value, str) or not value:
                raise SnapshotValidationError("INVALID_VERSION", f"{name} must be a nonempty string")
            _validate_string(value)
        if self.personal_mark_schema_version != PERSONAL_MARK_V2_VERSION:
            raise SnapshotValidationError("UNKNOWN_MARK_SCHEMA_VERSION", "snapshot requires Personal Mark schema v2")
        if self.snapshot_encoding_version != SNAPSHOT_ENCODING_VERSION:
            raise SnapshotValidationError("UNKNOWN_SNAPSHOT_ENCODING", "unsupported snapshot encoding version")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "rightsSchema": self.rights_schema_version,
            "personalMarkSchema": self.personal_mark_schema_version,
            "preflightProtocol": self.preflight_protocol_version,
            "bridgeProtocol": self.bridge_protocol_version,
            "coreServiceContract": self.core_service_contract_version,
            "textPolicy": self.text_policy_version,
            "snapshotEncoding": self.snapshot_encoding_version,
        }


@dataclass(frozen=True)
class PreflightSnapshot:
    target_fingerprint: TargetFingerprint
    rights_intent: FrozenObject
    personal_mark: PersonalMarkV2
    versions: SnapshotVersions
    operation_generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.target_fingerprint, TargetFingerprint):
            raise SnapshotValidationError("INVALID_TARGET_FINGERPRINT", "target fingerprint has the wrong type")
        if not isinstance(self.rights_intent, FrozenObject):
            raise SnapshotValidationError("RIGHTS_NOT_FROZEN", "Rights Intent snapshot must be a frozen object")
        _validate_frozen_graph(self.rights_intent)
        if not isinstance(self.versions, SnapshotVersions):
            raise SnapshotValidationError("INVALID_VERSIONS", "snapshot versions have the wrong type")
        if type(self.personal_mark) not in (TypedPersonalMarkV2, HandwrittenPersonalMarkV2):
            raise SnapshotValidationError("INVALID_PERSONAL_MARK", "Personal Mark snapshot has the wrong type")
        if (
            isinstance(self.operation_generation, bool)
            or not isinstance(self.operation_generation, int)
            or not 0 <= self.operation_generation <= MAX_SAFE_JSON_INTEGER
        ):
            raise SnapshotValidationError("INVALID_OPERATION_GENERATION", "operation generation must be a nonnegative safe JSON integer")
        validated_mark = personal_mark_v2_from_mapping(self.personal_mark.to_mapping())
        object.__setattr__(self, "personal_mark", validated_mark)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "targetFingerprint": self.target_fingerprint.to_mapping(),
            "rightsIntent": self.rights_intent.to_mapping(),
            "personalMark": self.personal_mark.to_mapping(),
            "versions": self.versions.to_mapping(),
            "operationGeneration": self.operation_generation,
        }

    def deterministic_bytes(self) -> bytes:
        return encode_snapshot(self)

    def sha256_digest(self) -> str:
        return hashlib.sha256(self.deterministic_bytes()).hexdigest()


def create_preflight_snapshot(
    *,
    target_fingerprint: TargetFingerprint,
    rights_intent: Mapping[str, Any],
    personal_mark: PersonalMarkV2,
    versions: SnapshotVersions,
    operation_generation: int,
) -> PreflightSnapshot:
    """Validate and detach all supplied data without target or service I/O."""

    frozen_rights = freeze_json_object(rights_intent)
    validated_mark = personal_mark_v2_from_mapping(personal_mark.to_mapping())
    return PreflightSnapshot(
        target_fingerprint=target_fingerprint,
        rights_intent=frozen_rights,
        personal_mark=validated_mark,
        versions=versions,
        operation_generation=operation_generation,
    )


def freeze_json_object(value: Mapping[str, Any]) -> FrozenObject:
    frozen = freeze_json_value(value)
    if not isinstance(frozen, FrozenObject):
        raise SnapshotValidationError("RIGHTS_NOT_OBJECT", "Rights Intent snapshot must be an object")
    return frozen


def freeze_json_value(
    value: Any,
    *,
    _depth: int = 1,
    _counter: list[int] | None = None,
    _byte_budget: list[int] | None = None,
) -> FrozenJsonValue:
    if _counter is None:
        _counter = [0]
    if _byte_budget is None:
        _byte_budget = [0]
    if _depth > MAX_SNAPSHOT_DEPTH:
        raise SnapshotValidationError("SNAPSHOT_DEPTH_EXCEEDED", "snapshot value exceeds maximum depth")
    _counter[0] += 1
    if _counter[0] > MAX_SNAPSHOT_ITEMS:
        raise SnapshotValidationError("SNAPSHOT_ITEMS_EXCEEDED", "snapshot contains too many values")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_JSON_INTEGER:
            raise SnapshotValidationError("INVALID_NUMBER", "snapshot integer exceeds the bounded JSON domain")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SnapshotValidationError("INVALID_NUMBER", "snapshot number must be finite")
        return value
    if isinstance(value, str):
        _validate_string(value)
        _consume_input_budget(_byte_budget, len(value.encode("utf-8")))
        return value
    if isinstance(value, Mapping):
        entries: list[tuple[str, FrozenJsonValue]] = []
        for key, child in value.items():
            if not isinstance(key, str):
                raise SnapshotValidationError("INVALID_OBJECT_KEY", "snapshot object keys must be strings")
            _validate_string(key)
            _consume_input_budget(_byte_budget, len(key.encode("utf-8")))
            entries.append(
                (
                    key,
                    freeze_json_value(
                        child,
                        _depth=_depth + 1,
                        _counter=_counter,
                        _byte_budget=_byte_budget,
                    ),
                )
            )
        entries.sort(key=lambda item: item[0].encode("utf-8"))
        return FrozenObject(tuple(entries))
    if isinstance(value, (list, tuple)):
        return FrozenArray(
            tuple(
                freeze_json_value(
                    child,
                    _depth=_depth + 1,
                    _counter=_counter,
                    _byte_budget=_byte_budget,
                )
                for child in value
            )
        )
    raise SnapshotValidationError("INVALID_JSON_VALUE", "snapshot accepts bounded JSON values only")


def thaw_json_value(value: FrozenJsonValue) -> Any:
    _validate_frozen_graph(value)
    return _thaw_unchecked(value)


def _thaw_unchecked(value: FrozenJsonValue) -> Any:
    if isinstance(value, FrozenObject):
        return {key: _thaw_unchecked(child) for key, child in value.entries}
    if isinstance(value, FrozenArray):
        return [_thaw_unchecked(child) for child in value.values]
    return value


def encode_snapshot(snapshot: PreflightSnapshot) -> bytes:
    """Produce collision-resistant type-and-length framed deterministic bytes."""

    frozen = freeze_json_value(snapshot.to_mapping())
    output = bytearray()
    _append(output, SNAPSHOT_ENCODING_HEADER)
    _encode_value(frozen, output)
    return bytes(output)


def _encode_value(value: FrozenJsonValue, output: bytearray) -> None:
    if value is None:
        _append(output, b"N")
        return
    if value is True:
        _append(output, b"T")
        return
    if value is False:
        _append(output, b"F")
        return
    if isinstance(value, int):
        payload = str(value).encode("ascii")
        _append(output, b"I" + _length(payload) + payload)
        return
    if isinstance(value, float):
        _append(output, b"D" + struct.pack(">d", value))
        return
    if isinstance(value, str):
        payload = value.encode("utf-8")
        _append(output, b"S" + _length(payload) + payload)
        return
    if isinstance(value, FrozenArray):
        _append(output, b"A" + _length_count(len(value.values)))
        for child in value.values:
            _encode_value(child, output)
        return
    if isinstance(value, FrozenObject):
        _append(output, b"O" + _length_count(len(value.entries)))
        for key, child in value.entries:
            _encode_value(key, output)
            _encode_value(child, output)
        return
    raise AssertionError("unreachable frozen value type")


def _length(payload: bytes) -> bytes:
    return len(payload).to_bytes(8, "big", signed=False)


def _length_count(count: int) -> bytes:
    return count.to_bytes(8, "big", signed=False)


def _append(output: bytearray, payload: bytes) -> None:
    if len(output) + len(payload) > MAX_SNAPSHOT_ENCODED_BYTES:
        raise SnapshotValidationError("SNAPSHOT_TOO_LARGE", "snapshot exceeds the bounded encoding limit")
    output.extend(payload)


def _consume_input_budget(budget: list[int], amount: int) -> None:
    budget[0] += amount
    if budget[0] > MAX_SNAPSHOT_INPUT_BYTES:
        raise SnapshotValidationError("SNAPSHOT_TOO_LARGE", "snapshot exceeds the bounded input limit")


def _validate_frozen_value(value: Any) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) <= MAX_SAFE_JSON_INTEGER:
            return
        raise SnapshotValidationError("RIGHTS_NOT_FROZEN", "frozen integer is out of range")
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise SnapshotValidationError("RIGHTS_NOT_FROZEN", "frozen number is not finite")
    if isinstance(value, str):
        _validate_string(value)
        return
    if isinstance(value, (FrozenObject, FrozenArray)):
        return
    raise SnapshotValidationError("RIGHTS_NOT_FROZEN", "frozen snapshot contains a mutable or invalid value")


def _validate_frozen_graph(root: FrozenJsonValue) -> None:
    """Bound a pre-frozen graph iteratively before recursive thaw/encoding."""

    stack: list[tuple[FrozenJsonValue, int]] = [(root, 1)]
    item_count = 0
    byte_count = 0
    while stack:
        value, depth = stack.pop()
        if depth > MAX_SNAPSHOT_DEPTH:
            raise SnapshotValidationError("SNAPSHOT_DEPTH_EXCEEDED", "snapshot value exceeds maximum depth")
        item_count += 1
        if item_count > MAX_SNAPSHOT_ITEMS:
            raise SnapshotValidationError("SNAPSHOT_ITEMS_EXCEEDED", "snapshot contains too many values")
        _validate_frozen_value(value)
        if isinstance(value, str):
            byte_count += len(value.encode("utf-8"))
        elif isinstance(value, FrozenObject):
            for key, child in value.entries:
                byte_count += len(key.encode("utf-8"))
                stack.append((child, depth + 1))
        elif isinstance(value, FrozenArray):
            for child in value.values:
                stack.append((child, depth + 1))
        if byte_count > MAX_SNAPSHOT_INPUT_BYTES:
            raise SnapshotValidationError("SNAPSHOT_TOO_LARGE", "snapshot exceeds the bounded input limit")


def _validate_string(value: str) -> None:
    for character in value:
        if 0xD800 <= ord(character) <= 0xDFFF:
            raise SnapshotValidationError("MALFORMED_UNICODE", "snapshot strings may not contain lone surrogates")
    value.encode("utf-8", errors="strict")
