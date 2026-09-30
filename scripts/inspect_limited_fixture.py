"""Thin, fixed-fixture entry for the DEVELOPMENT CANARY; no full Verify path."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
from typing import Any

OPERATION = "limited_c2pa_cawg_inspection"
# Outer transport v2 is independent of the unchanged kernel contractVersion=1.
OUTER_CONTRACT_VERSION = 2
FIXTURE_SHA256 = "558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316"
FIXTURE_SIZE = 319495
MAX_JSON_BYTES = 64 * 1024
MAX_JSON_DEPTH = 8
ERROR_EXITS = {
    "INVALID_ARGUMENTS": 2,
    "ENVIRONMENT_MISMATCH": 12,
    "RUNTIME_UNAVAILABLE": 12,
    "FIXTURE_CHANGED": 10,
    "FIXTURE_INVALID": 13,
    "SETTINGS_INVALID": 10,
    "C2PATOOL_MISSING": 12,
    "C2PATOOL_INTEGRITY_FAILED": 10,
    "C2PATOOL_VERSION_MISMATCH": 12,
    "C2PATOOL_START_FAILED": 13,
    "C2PATOOL_OUTPUT_LIMIT": 13,
    "C2PATOOL_TIMEOUT": 13,
    "LIMITED_RESULT_INVALID": 14,
    "RESULT_SERIALIZATION_FAILED": 16,
    "INTERNAL_ENTRY_FAILURE": 16,
}


class EntryFailure(Exception):
    def __init__(self, code: str):
        self.code = code if code in ERROR_EXITS else "INTERNAL_ENTRY_FAILURE"
        super().__init__(self.code)


def failure_envelope(code: str) -> dict[str, Any]:
    if code not in ERROR_EXITS:
        code = "INTERNAL_ENTRY_FAILURE"
    return {"contractVersion": OUTER_CONTRACT_VERSION, "operation": OPERATION,
            "result": "INSPECTION_FAILED", "error": {"code": code}}


def success_envelope(inspection: dict[str, Any], kernel: Any) -> dict[str, Any]:
    # Preserve the existing strict inner schema and its expected-fixture checks.
    kernel.validate_result(inspection, require_expected=True)
    if inspection["source"] != {"sha256": FIXTURE_SHA256,
                                "size": FIXTURE_SIZE, "format": "PNG"}:
        raise EntryFailure("LIMITED_RESULT_INVALID")
    return {"contractVersion": OUTER_CONTRACT_VERSION, "operation": OPERATION,
            "result": "LIMITED_INSPECTION", "completeness": "INCOMPLETE",
            "checks": {"c2pa": "INSPECTED", "cawg": "INSPECTED",
                       "trustmark": "NOT_CHECKED"}, "inspection": inspection}


def validate_envelope(value: Any, kernel: Any) -> None:
    if not isinstance(value, dict) or type(value.get("contractVersion")) is not int:
        raise EntryFailure("LIMITED_RESULT_INVALID")
    if (value.get("contractVersion") != OUTER_CONTRACT_VERSION
            or value.get("operation") != OPERATION):
        raise EntryFailure("LIMITED_RESULT_INVALID")
    if value.get("result") == "INSPECTION_FAILED":
        if (set(value) != {"contractVersion", "operation", "result", "error"}
                or not isinstance(value["error"], dict)
                or set(value["error"]) != {"code"}
                or not isinstance(value["error"]["code"], str)
                or value["error"]["code"] not in ERROR_EXITS):
            raise EntryFailure("LIMITED_RESULT_INVALID")
        return
    if (set(value) != {"contractVersion", "operation", "result", "completeness",
                      "checks", "inspection"}
            or value.get("result") != "LIMITED_INSPECTION"
            or value.get("completeness") != "INCOMPLETE"
            or value.get("checks") != {"c2pa": "INSPECTED", "cawg": "INSPECTED",
                                       "trustmark": "NOT_CHECKED"}):
        raise EntryFailure("LIMITED_RESULT_INVALID")
    success_envelope(value["inspection"], kernel)


def _depth(value: Any, level: int = 0) -> None:
    if level > MAX_JSON_DEPTH:
        raise EntryFailure("LIMITED_RESULT_INVALID")
    if isinstance(value, dict):
        for child in value.values():
            _depth(child, level + 1)
    elif isinstance(value, list):
        for child in value:
            _depth(child, level + 1)


def parse_envelope(raw: bytes, kernel: Any) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject(_: str) -> None:
        raise ValueError("non-finite number")

    try:
        if not raw or len(raw) > MAX_JSON_BYTES:
            raise ValueError("output bound")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=reject)
        _depth(value)
        validate_envelope(value, kernel)
        return value
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError) as exc:
        raise EntryFailure("LIMITED_RESULT_INVALID") from exc


def encode_envelope(value: dict[str, Any]) -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False,
                         separators=(",", ":")).encode("utf-8") + b"\n"
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("output bound")
        return raw
    except (TypeError, ValueError, RecursionError) as exc:
        raise EntryFailure("RESULT_SERIALIZATION_FAILED") from exc


def inspect_package(root: Path, kernel: Any) -> dict[str, Any]:
    inspection = kernel.inspect_limited(
        root / "fixtures/valid_shirushi.png", root / "tools/c2patool.exe",
        root / "config/verifier-settings.json", expected_sha256=FIXTURE_SHA256,
        expected_size=FIXTURE_SIZE,
    )
    return success_envelope(inspection, kernel)


def _load_kernel(root: Path) -> Any:
    # The native runner audits these immutable directories before this child.
    # -S prevents venv/global .pth/site initialization; -I ignores Python overrides.
    if not sys.flags.isolated or not sys.flags.no_site:
        raise EntryFailure("ENVIRONMENT_MISMATCH")
    directories = [root / "runtime/site-packages", root / "src", root / "scripts"]
    if any(not path.is_dir() or path.is_symlink() for path in directories):
        raise EntryFailure("RUNTIME_UNAVAILABLE")
    sys.path[:0] = [str(path) for path in directories]
    try:
        return importlib.import_module("inspection_metadata")
    except (ImportError, OSError):
        raise EntryFailure("RUNTIME_UNAVAILABLE") from None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if args:
            raise EntryFailure("INVALID_ARGUMENTS")
        root = Path(__file__).resolve().parents[1]
        kernel = _load_kernel(root)
        envelope = inspect_package(root, kernel)
        exit_code = 0
    except EntryFailure as exc:
        envelope, exit_code = failure_envelope(exc.code), ERROR_EXITS[exc.code]
    except Exception as exc:
        # Only declared kernel codes may cross the boundary. Never forward stderr,
        # exception text, personal paths, command lines, or partial inspection data.
        code = getattr(exc, "code", "INTERNAL_ENTRY_FAILURE")
        if not isinstance(code, str) or code not in ERROR_EXITS:
            code = "INTERNAL_ENTRY_FAILURE"
        envelope, exit_code = failure_envelope(code), ERROR_EXITS[code]
    try:
        sys.stdout.buffer.write(encode_envelope(envelope))
        sys.stdout.buffer.flush()
    except Exception:
        sys.stderr.write("RESULT_SERIALIZATION_FAILED\n")
        return 16
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
