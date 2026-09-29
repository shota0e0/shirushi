"""Isolated, atomic local persistence for Personal Mark stroke data."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from personal_mark import PersonalMark, PersonalMarkValidationError, PersonalMarkVersionError
from runtime_paths import resolve_runtime_paths


MAX_PERSONAL_MARK_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_PERSONAL_MARK_PATH = (
    resolve_runtime_paths().user_data_root / "personal-mark" / "personal-mark-v1.json"
)


@dataclass(frozen=True)
class PersonalMarkLoadResult:
    mark: PersonalMark | None
    issue: str | None = None


class PersonalMarkStore:
    """Store one local mark without sharing or modifying v0.1 settings files."""

    def __init__(self, path: Path = DEFAULT_PERSONAL_MARK_PATH) -> None:
        self.path = Path(path)

    def save(self, mark: PersonalMark) -> None:
        validated = PersonalMark.from_mapping(mark.to_mapping())
        encoded = json.dumps(
            validated.to_mapping(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_PERSONAL_MARK_FILE_BYTES:
            raise PersonalMarkValidationError("Personal Mark is too large to save")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def load(self) -> PersonalMarkLoadResult:
        try:
            if not self.path.is_file():
                return PersonalMarkLoadResult(None)
            if self.path.stat().st_size > MAX_PERSONAL_MARK_FILE_BYTES:
                return PersonalMarkLoadResult(None, "corrupted")
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return PersonalMarkLoadResult(PersonalMark.from_mapping(raw))
        except PersonalMarkVersionError:
            return PersonalMarkLoadResult(None, "version_mismatch")
        except (OSError, UnicodeError, json.JSONDecodeError, PersonalMarkValidationError):
            return PersonalMarkLoadResult(None, "corrupted")
