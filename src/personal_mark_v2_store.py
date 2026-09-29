"""Atomic v2 persistence and non-migrating v2/v1 dual-read behavior."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from personal_mark import PersonalMark, PersonalMarkValidationError, PersonalMarkVersionError
from personal_mark_store import DEFAULT_PERSONAL_MARK_PATH, MAX_PERSONAL_MARK_FILE_BYTES
from personal_mark_v2 import (
    MAX_LOCAL_RECORD_BYTES,
    MarkV2Error,
    PersonalMarkV2,
    TypedPersonalMarkV2,
    encode_personal_mark_v2,
    parse_personal_mark_v2,
    personal_mark_v2_from_mapping,
    resolve_render_profile,
    validate_typed_save_boundary,
)
from runtime_paths import resolve_runtime_paths


READ_CONTRACT = "shirushi-personal-mark-read"
READ_CONTRACT_VERSION = 1
LEGACY_GEOMETRY_PROVENANCE = "legacy-unknown"
DEFAULT_PERSONAL_MARK_V2_PATH = (
    resolve_runtime_paths().user_data_root / "personal-mark" / "personal-mark-v2.json"
)


class PersonalMarkV2StoreError(RuntimeError):
    """Persistence-boundary failure with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PersonalMarkV2LoadResult:
    state: str
    mark: PersonalMarkV2 | None = None
    legacy_mark: PersonalMark | None = None
    raw_bytes: bytes | None = None
    source: str | None = None
    error_code: str | None = None

    @property
    def source_version(self) -> int | None:
        if self.state == "v2":
            return 2
        if self.state == "legacy_v1":
            return 1
        return None

    @property
    def geometry_provenance(self) -> str | None:
        return LEGACY_GEOMETRY_PROVENANCE if self.state == "legacy_v1" else None

    def to_envelope(self) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "contract": READ_CONTRACT,
            "contractVersion": READ_CONTRACT_VERSION,
            "state": self.state,
        }
        if self.state == "v2" and self.mark is not None:
            envelope["sourceVersion"] = 2
            envelope["mark"] = self.mark.to_mapping()
            if isinstance(self.mark, TypedPersonalMarkV2):
                resolution = resolve_render_profile(self.mark.render_profile)
                envelope["renderProfileSupport"] = {
                    "state": resolution.state.value,
                    "errorCode": resolution.error_code,
                }
        elif self.state == "legacy_v1" and self.legacy_mark is not None:
            envelope.update(
                {
                    "sourceVersion": 1,
                    "geometryProvenance": LEGACY_GEOMETRY_PROVENANCE,
                    "payload": self.legacy_mark.to_mapping(),
                }
            )
        elif self.state in {"malformed", "unsupported", "io_error"}:
            envelope["source"] = self.source
            envelope["errorCode"] = self.error_code
        return envelope


class PersonalMarkV2Store:
    """Store v2 separately and consult v1 only when the v2 path is absent."""

    def __init__(
        self,
        v2_path: Path = DEFAULT_PERSONAL_MARK_V2_PATH,
        v1_path: Path = DEFAULT_PERSONAL_MARK_PATH,
    ) -> None:
        self.v2_path = Path(v2_path)
        self.v1_path = Path(v1_path)
        self._ensure_paths_are_isolated()

    def save(
        self,
        mark: PersonalMarkV2,
        *,
        typed_draft: str | None = None,
        accept_normalization: bool = False,
        accept_special_characters: bool = False,
    ) -> None:
        """Validate confirmations, then atomically replace only the v2 file."""

        self._ensure_paths_are_isolated()
        validated = personal_mark_v2_from_mapping(mark.to_mapping())
        if isinstance(validated, TypedPersonalMarkV2):
            if typed_draft is None:
                raise MarkV2Error(
                    "TYPED_DRAFT_REQUIRED",
                    "Typed saves must carry the draft text through the save boundary",
                )
            validated = validate_typed_save_boundary(
                validated,
                draft_text=typed_draft,
                accept_normalization=accept_normalization,
                accept_special_characters=accept_special_characters,
            )
        elif typed_draft is not None or accept_normalization or accept_special_characters:
            raise MarkV2Error(
                "INVALID_SAVE_CONFIRMATION",
                "Typed confirmation arguments are not valid for Handwritten marks",
            )
        encoded = encode_personal_mark_v2(validated)

        try:
            self.v2_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PersonalMarkV2StoreError("IO_ERROR", "could not prepare v2 storage directory") from exc

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{self.v2_path.name}.",
                suffix=".tmp",
                dir=self.v2_path.parent,
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._ensure_paths_are_isolated()
            os.replace(temporary_path, self.v2_path)
            temporary_path = None
        except PersonalMarkV2StoreError:
            raise
        except OSError as exc:
            raise PersonalMarkV2StoreError("IO_ERROR", "could not atomically save Personal Mark v2") from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as exc:
                    raise PersonalMarkV2StoreError(
                        "TEMP_CLEANUP_FAILED",
                        "failed to clean up an incomplete Personal Mark v2 save",
                    ) from exc

    def load(self) -> PersonalMarkV2LoadResult:
        """Read v2 first; malformed/unsupported/I/O v2 blocks all v1 fallback."""

        v2_read = _read_optional_file(self.v2_path, MAX_LOCAL_RECORD_BYTES)
        if v2_read.state == "io_error":
            return PersonalMarkV2LoadResult(
                "io_error", source="v2", error_code=v2_read.error_code
            )
        if v2_read.state == "too_large":
            return PersonalMarkV2LoadResult(
                "malformed",
                raw_bytes=v2_read.raw_bytes,
                source="v2",
                error_code="PAYLOAD_TOO_LARGE",
            )
        if v2_read.state == "present":
            assert v2_read.raw_bytes is not None
            try:
                mark = parse_personal_mark_v2(v2_read.raw_bytes)
            except MarkV2Error as exc:
                if exc.code in {"UNKNOWN_SCHEMA_VERSION", "UNKNOWN_TYPE"}:
                    return PersonalMarkV2LoadResult(
                        "unsupported",
                        raw_bytes=v2_read.raw_bytes,
                        source="v2",
                        error_code=exc.code,
                    )
                return PersonalMarkV2LoadResult(
                    "malformed",
                    raw_bytes=v2_read.raw_bytes,
                    source="v2",
                    error_code=exc.code,
                )
            return PersonalMarkV2LoadResult(
                "v2", mark=mark, raw_bytes=v2_read.raw_bytes, source="v2"
            )

        v1_read = _read_optional_file(self.v1_path, MAX_PERSONAL_MARK_FILE_BYTES)
        if v1_read.state == "absent":
            return PersonalMarkV2LoadResult("absent")
        if v1_read.state == "io_error":
            return PersonalMarkV2LoadResult(
                "io_error", source="v1", error_code=v1_read.error_code
            )
        if v1_read.state == "too_large":
            return PersonalMarkV2LoadResult(
                "malformed",
                raw_bytes=v1_read.raw_bytes,
                source="v1",
                error_code="PAYLOAD_TOO_LARGE",
            )
        assert v1_read.raw_bytes is not None
        try:
            text = v1_read.raw_bytes.decode("utf-8", errors="strict")
            raw = json.loads(text)
            legacy = PersonalMark.from_mapping(raw)
        except PersonalMarkVersionError:
            return PersonalMarkV2LoadResult(
                "unsupported",
                raw_bytes=v1_read.raw_bytes,
                source="v1",
                error_code="UNKNOWN_SCHEMA_VERSION",
            )
        except (
            UnicodeError,
            json.JSONDecodeError,
            PersonalMarkValidationError,
            RecursionError,
            OverflowError,
        ):
            return PersonalMarkV2LoadResult(
                "malformed",
                raw_bytes=v1_read.raw_bytes,
                source="v1",
                error_code="MALFORMED_LEGACY_V1",
            )
        return PersonalMarkV2LoadResult(
            "legacy_v1",
            legacy_mark=legacy,
            raw_bytes=v1_read.raw_bytes,
            source="v1",
        )

    def _ensure_paths_are_isolated(self) -> None:
        if _paths_alias(self.v2_path, self.v1_path):
            raise PersonalMarkV2StoreError(
                "PATH_ALIASES_LEGACY",
                "Personal Mark v2 path must not alias the legacy v1 path",
            )


@dataclass(frozen=True)
class _ReadResult:
    state: str
    raw_bytes: bytes | None = None
    error_code: str | None = None


def _read_optional_file(path: Path, byte_limit: int) -> _ReadResult:
    try:
        with path.open("rb") as stream:
            raw = stream.read(byte_limit + 1)
    except FileNotFoundError:
        if os.path.lexists(path):
            return _ReadResult("io_error", error_code="IO_ERROR")
        return _ReadResult("absent")
    except OSError:
        return _ReadResult("io_error", error_code="IO_ERROR")
    if len(raw) > byte_limit:
        return _ReadResult("too_large", raw_bytes=raw)
    return _ReadResult("present", raw_bytes=raw)


def _paths_alias(first: Path, second: Path) -> bool:
    try:
        first_normalized = os.path.normcase(str(first.resolve(strict=False)))
        second_normalized = os.path.normcase(str(second.resolve(strict=False)))
    except OSError as exc:
        raise PersonalMarkV2StoreError(
            "PATH_IDENTITY_UNAVAILABLE",
            "could not establish isolated Personal Mark storage paths",
        ) from exc
    if first_normalized == second_normalized:
        return True
    try:
        first_exists = _target_exists(first)
        second_exists = _target_exists(second)
        return first_exists and second_exists and os.path.samefile(first, second)
    except OSError as exc:
        raise PersonalMarkV2StoreError(
            "PATH_IDENTITY_UNAVAILABLE",
            "could not establish isolated Personal Mark storage paths",
        ) from exc


def _target_exists(path: Path) -> bool:
    try:
        path.stat()
        return True
    except FileNotFoundError:
        return False
