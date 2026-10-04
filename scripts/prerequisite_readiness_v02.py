"""Read-only development prerequisite facts; never install or approve an input.

Only the shipped BUILD_POLICY is used by the CLI. Injected policies and readers
are for independently controlled tests/build callers, not runtime user policy.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import stat
import sys

MAX_RESULT = 16384
CHUNK = 1024 * 1024
REPARSE = 0x400
CANDIDATE_NAME = "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
CANDIDATE_SIZE = 212272848
CANDIDATE_SHA256 = "f6df8e4bc857786ff641cd01da1449169eaf8236c936ced485ea61685ba4da40"
SIGNATURE_POLICY = "ACCEPT_PRIMARY_MICROSOFT_WITH_KNOWN_NESTED_EDGEBUILD"
VC_SOURCES = ("HKLM64:VC14:Runtimes:x64", "HKLM32:VC14:Runtimes:x64")
VC_SOURCE = VC_SOURCES[0]
WV_SOURCES = ("HKLM32:EdgeUpdate:StableWebView2", "HKCU:EdgeUpdate:StableWebView2")
VC_KEY = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
WV_KEY = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
VERSION = re.compile(r"(?:0|[1-9][0-9]{0,4})(?:\.(?:0|[1-9][0-9]{0,4})){3}\Z")
VC_OBSERVED_VERSION = re.compile(r"[0-9]{1,5}(?:\.[0-9]{1,5}){3}\Z")
RESERVED = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|\Z)", re.I)
STATES = {"READY", "MISSING", "OUTDATED", "POLICY_UNSET", "DETECTION_FAILED"}
INPUT_ERRORS = {"PATH_INVALID", "WINDOWS_REQUIRED", "LOCAL_FIXED_DRIVE_REQUIRED",
                "BASENAME_INVALID", "PATH_UNAVAILABLE", "REPARSE_REJECTED",
                "FILE_TYPE_INVALID", "SIZE_MISMATCH", "HASH_MISMATCH",
                "FILE_IDENTITY_UNAVAILABLE", "HARDLINK_REJECTED", "SOURCE_CHANGED", "READ_FAILED"}


class ReadinessError(Exception):
    """Closed, path-free public failure category."""


def _fail(code: str) -> None:
    raise ReadinessError(code)


def version(value: object, *, vc: bool = False) -> tuple[int, int, int, int]:
    if not isinstance(value, str) or len(value) > 32:
        _fail("VERSION_INVALID")
    raw = value[1:] if vc and value.startswith("v") else value
    if not (VC_OBSERVED_VERSION if vc else VERSION).fullmatch(raw):
        _fail("VERSION_INVALID")
    parts = tuple(int(piece) for piece in raw.split("."))
    if any(piece > 65535 for piece in parts):
        _fail("VERSION_INVALID")
    return parts


def _version_string(parts: tuple) -> str:
    return ".".join(str(piece) for piece in parts)


@dataclass(frozen=True)
class Policy:
    vc_minimum: str | None = None

    def __post_init__(self) -> None:
        if self.vc_minimum is not None:
            if _version_string(version(self.vc_minimum)) != self.vc_minimum:
                _fail("POLICY_INVALID")


# No approved VC minimum exists. Changing this requires an Owner-reviewed build
# policy change, not command-line/environment/observed registry input.
BUILD_POLICY = Policy()


@dataclass(frozen=True)
class Registration:
    source: str
    query: str
    values: dict
    architecture: str = "UNPROVEN"


class WindowsRegistryReader:
    """Allowlisted queries, KEY_READ only. No enumeration or executable lookup."""

    def read(self, source: str) -> Registration:
        if source not in (*VC_SOURCES, *WV_SOURCES):
            _fail("SOURCE_INVALID")
        if os.name != "nt" or platform.machine().upper() not in ("AMD64", "X86_64") or sys.maxsize <= 2**32:
            return Registration(source, "FAILED", {})
        import winreg
        if source in VC_SOURCES:
            hive, key = winreg.HKEY_LOCAL_MACHINE, VC_KEY
            view = winreg.KEY_WOW64_64KEY if source == VC_SOURCE else winreg.KEY_WOW64_32KEY
            names = ("Version", "Installed", "Major", "Minor", "Bld", "Rbld")
            architecture = "X64_REGISTERED"
        else:
            hive = winreg.HKEY_LOCAL_MACHINE if source == WV_SOURCES[0] else winreg.HKEY_CURRENT_USER
            key, view, names = WV_KEY, winreg.KEY_WOW64_32KEY, ("pv",)
            architecture = "UNPROVEN"  # Registry pv does not establish Runtime PE architecture.
        try:
            with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                values = {}
                for name in names:
                    try:
                        value, kind = winreg.QueryValueEx(handle, name)
                        # No raw registry value is emitted in the report.
                        values[name] = (value, "SZ" if kind == winreg.REG_SZ else "DWORD" if kind == winreg.REG_DWORD else "OTHER")
                    except FileNotFoundError:
                        pass
            return Registration(source, "OK", values, architecture)
        except FileNotFoundError:
            return Registration(source, "MISSING", {}, architecture)
        except OSError:
            return Registration(source, "FAILED", {}, architecture)


def normalize(raw: Registration) -> dict:
    if not isinstance(raw, Registration) or raw.source not in (*VC_SOURCES, *WV_SOURCES):
        _fail("OBSERVATION_INVALID")
    out = {"source": raw.source, "query": raw.query, "version": None,
           "installed": None, "architecture": raw.architecture, "issue": "NONE"}
    if not isinstance(raw.query, str) or raw.query not in {"OK", "MISSING", "FAILED"} or not isinstance(raw.values, dict):
        _fail("OBSERVATION_INVALID")
    if not isinstance(raw.architecture, str) or raw.architecture not in {"X64_REGISTERED", "X64_VERIFIED", "UNPROVEN", "WRONG_ARCHITECTURE"}:
        _fail("OBSERVATION_INVALID")
    if raw.query != "OK":
        out["issue"] = "REGISTRATION_MISSING" if raw.query == "MISSING" else "QUERY_FAILED"
        return out
    vc = raw.source in VC_SOURCES
    allowed = {"Version", "Installed", "Major", "Minor", "Bld", "Rbld"} if vc else {"pv"}
    try:
        if not set(raw.values) <= allowed:
            _fail("FIELD_TYPE_INVALID")
        for pair in raw.values.values():
            if not isinstance(pair, tuple) or len(pair) != 2 or not isinstance(pair[1], str) or pair[1] not in {"SZ", "DWORD", "OTHER"}:
                _fail("FIELD_TYPE_INVALID")
        pair = raw.values.get("Version" if vc else "pv")
        if not vc and (pair is None or pair == (None, "SZ") or pair == ("", "SZ") or pair == ("0.0.0.0", "SZ")):
            out.update(query="MISSING", issue="REGISTRATION_MISSING")
            return out
        if pair is None or pair[1] != "SZ":
            _fail("FIELD_TYPE_INVALID")
        parts = version(pair[0], vc=vc)
        if parts == (0, 0, 0, 0):
            _fail("VERSION_INVALID")
        if vc:
            installed = raw.values.get("Installed")
            if installed is not None:
                if installed[1] != "DWORD" or type(installed[0]) is not int or installed[0] not in (0, 1):
                    _fail("FIELD_TYPE_INVALID")
                out["installed"] = bool(installed[0])
            fields = [raw.values.get(name) for name in ("Major", "Minor", "Bld", "Rbld")]
            if any(field is not None for field in fields):
                if any(field is None or field[1] != "DWORD" or type(field[0]) is not int or not 0 <= field[0] <= 65535 for field in fields):
                    _fail("FIELD_TYPE_INVALID")
                if tuple(field[0] for field in fields) != parts:
                    _fail("VERSION_FIELDS_CONFLICT")
        else:
            out["installed"] = True
        out["version"] = _version_string(parts)
    except ReadinessError as error:
        out.update(query="MALFORMED", version=None, installed=None, issue=str(error))
    return out


def _state(observations: list, *, vc: bool, policy: Policy) -> tuple[str, str]:
    if any(item["query"] in {"FAILED", "MALFORMED"} for item in observations):
        return "DETECTION_FAILED", "OBSERVATION_FAILED"
    present = [item for item in observations if item["query"] == "OK"]
    if vc and policy.vc_minimum is None:
        return "POLICY_UNSET", "VC_MINIMUM_UNAPPROVED"
    if not present:
        return "MISSING", "REGISTRATION_MISSING"
    if len({item["version"] for item in present}) != 1:
        return "DETECTION_FAILED", "SOURCE_VERSION_CONFLICT"
    if any(item["architecture"] not in ({"X64_REGISTERED", "X64_VERIFIED"} if vc else {"X64_VERIFIED"}) for item in present):
        return "DETECTION_FAILED", "X64_READINESS_UNPROVEN"
    if vc and any(item["installed"] is None for item in present):
        return "DETECTION_FAILED", "INSTALLED_FLAG_UNPROVEN"
    if vc and len({item["installed"] for item in present}) != 1:
        return "DETECTION_FAILED", "SOURCE_INSTALLED_CONFLICT"
    if vc and any(item["installed"] is False for item in present):
        return "MISSING", "INSTALLED_FLAG_FALSE"
    if vc and version(present[0]["version"]) < version(policy.vc_minimum):
        return "OUTDATED", "BELOW_TRUSTED_MINIMUM"
    return "READY", "X64_REGISTERED_POLICY_SATISFIED" if vc else "X64_RUNTIME_EVIDENCE_VERIFIED"


def _overall(states: list[str]) -> str:
    if any(state in {"POLICY_UNSET", "DETECTION_FAILED"} for state in states):
        return "READINESS_UNKNOWN"
    return "READY" if all(state == "READY" for state in states) else "PREREQUISITES_REQUIRED"


def _offline(status: str = "NOT_CHECKED", error: str | None = None) -> dict:
    return {"vc": {"status": "VC_OFFLINE_INPUT_UNCONFIGURED"}, "webview2": {
        "status": status, "filename": CANDIDATE_NAME, "expectedSize": CANDIDATE_SIZE,
        "expectedSha256": CANDIDATE_SHA256, "approval": "CANDIDATE",
        "signaturePolicy": SIGNATURE_POLICY, "currentSignatureVerification": "NOT_PERFORMED",
        "runtimeIdentity": "UNPROVEN", "error": error}}


def evaluate(registrations: list[Registration], *, policy: Policy = BUILD_POLICY,
             offline_input: dict | None = None) -> dict:
    if not isinstance(policy, Policy):
        _fail("POLICY_INVALID")
    if not isinstance(registrations, list) or len(registrations) != 4 or any(not isinstance(item, Registration) for item in registrations) or [item.source for item in registrations] != [*VC_SOURCES, *WV_SOURCES]:
        _fail("OBSERVATION_INVALID")
    observations = [normalize(item) for item in registrations]
    runtimes = {}
    for name, items, vc in (("vc", observations[:2], True), ("webview2", observations[2:], False)):
        state, reason = _state(items, vc=vc, policy=policy)
        runtimes[name] = {"status": state, "reason": reason, "observations": items}
    result = {"schemaVersion": 1, "purpose": "READ_ONLY_DEVELOPMENT_READINESS",
              "policy": {"vcMinimumVersion": policy.vc_minimum},
              "overall": _overall([item["status"] for item in runtimes.values()]),
              "runtimes": runtimes, "offlineInputs": offline_input if offline_input is not None else _offline()}
    validate_result(result, policy=policy)
    return result


def _keys(value: object, expected: set) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        _fail("RESULT_SCHEMA_INVALID")


def validate_result(value: dict, *, policy: Policy = BUILD_POLICY) -> None:
    """Recompute state; a status/manifest cannot grant itself policy or approval."""
    _keys(value, {"schemaVersion", "purpose", "policy", "overall", "runtimes", "offlineInputs"})
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1 or value["purpose"] != "READ_ONLY_DEVELOPMENT_READINESS":
        _fail("RESULT_SCHEMA_INVALID")
    _keys(value["policy"], {"vcMinimumVersion"})
    if value["policy"]["vcMinimumVersion"] != policy.vc_minimum:
        _fail("POLICY_MISMATCH")
    _keys(value["runtimes"], {"vc", "webview2"})
    for name, sources, vc in (("vc", list(VC_SOURCES), True), ("webview2", list(WV_SOURCES), False)):
        item = value["runtimes"][name]
        _keys(item, {"status", "reason", "observations"})
        rows = item["observations"]
        if not isinstance(rows, list) or len(rows) != len(sources):
            _fail("RESULT_SCHEMA_INVALID")
        for row, source in zip(rows, sources):
            _keys(row, {"source", "query", "version", "installed", "architecture", "issue"})
            if row["source"] != source or not isinstance(row["query"], str) or row["query"] not in {"OK", "MISSING", "FAILED", "MALFORMED"} or not isinstance(row["architecture"], str) or row["architecture"] not in {"X64_REGISTERED", "X64_VERIFIED", "UNPROVEN", "WRONG_ARCHITECTURE"}:
                _fail("RESULT_SCHEMA_INVALID")
            if row["installed"] is not None and type(row["installed"]) is not bool:
                _fail("RESULT_SCHEMA_INVALID")
            if row["query"] == "OK":
                if version(row["version"]) == (0, 0, 0, 0) or row["issue"] != "NONE" or (not vc and row["installed"] is not True):
                    _fail("RESULT_SCHEMA_INVALID")
            else:
                issues = {"MISSING": {"REGISTRATION_MISSING"}, "FAILED": {"QUERY_FAILED"}, "MALFORMED": {"VERSION_INVALID", "FIELD_TYPE_INVALID", "VERSION_FIELDS_CONFLICT"}}
                if row["version"] is not None or row["installed"] is not None or not isinstance(row["issue"], str) or row["issue"] not in issues[row["query"]]:
                    _fail("RESULT_SCHEMA_INVALID")
        state, reason = _state(rows, vc=vc, policy=policy)
        if item["status"] != state or item["reason"] != reason:
            _fail("RESULT_STATE_CONFLICT")
    if value["overall"] != _overall([item["status"] for item in value["runtimes"].values()]):
        _fail("RESULT_STATE_CONFLICT")
    offline = value["offlineInputs"]
    _keys(offline, {"vc", "webview2"})
    if offline["vc"] != {"status": "VC_OFFLINE_INPUT_UNCONFIGURED"}:
        _fail("RESULT_SCHEMA_INVALID")
    wv = offline["webview2"]
    _keys(wv, set(_offline()["webview2"]))
    status, error = wv["status"], wv["error"]
    if not isinstance(status, str) or status not in {"NOT_CHECKED", "CANDIDATE_IDENTITY_MATCH", "IDENTITY_REJECTED"} or ((not isinstance(error, str) or error not in INPUT_ERRORS) if status == "IDENTITY_REJECTED" else error is not None):
        _fail("RESULT_SCHEMA_INVALID")
    if type(wv["expectedSize"]) is not int or wv != _offline(status, error)["webview2"]:
        _fail("RESULT_SCHEMA_INVALID")


def encode_result(value: dict, *, policy: Policy = BUILD_POLICY) -> bytes:
    validate_result(value, policy=policy)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"
    if len(raw) > MAX_RESULT:
        _fail("RESULT_SIZE_INVALID")
    return raw


def parse_result(raw: bytes, *, policy: Policy = BUILD_POLICY) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_RESULT or raw.startswith(b"\xef\xbb\xbf"):
        _fail("RESULT_SIZE_INVALID")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("DUPLICATE_JSON_FIELD")
            result[key] = value
        return result
    def nonfinite(_):
        _fail("JSON_INVALID")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        _fail("JSON_INVALID")
    validate_result(result, policy=policy)
    return result


def _drive_type(root: str) -> int:
    if os.name != "nt":
        _fail("WINDOWS_REQUIRED")
    function = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    function.argtypes, function.restype = [ctypes.c_wchar_p], ctypes.c_uint32
    return int(function(root))


def _candidate_path(raw: str | Path) -> Path:
    text = str(raw)
    if len(text) > 4096 or not re.match(r"\A[A-Za-z]:[\\/]", text):
        _fail("PATH_INVALID")
    parts = re.split(r"[\\/]", text[3:])
    if not parts or len(parts) > 32:
        _fail("PATH_INVALID")
    for part in parts:
        if not part or part in (".", "..") or part.endswith((" ", ".")) or RESERVED.match(part) or any(ord(c) < 32 or c in '<>:"|?*' for c in part):
            _fail("PATH_INVALID")
    windows = PureWindowsPath(text)
    if _drive_type(windows.drive + "\\") != 3:
        _fail("LOCAL_FIXED_DRIVE_REQUIRED")
    if windows.name != CANDIDATE_NAME:
        _fail("BASENAME_INVALID")
    return Path(text)


def _metadata(path: Path, *, directory: bool = False):
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


def _stamp(value) -> tuple:
    if type(value.st_dev) is not int or value.st_dev < 0 or type(value.st_ino) is not int or value.st_ino <= 0:
        _fail("FILE_IDENTITY_UNAVAILABLE")
    if type(getattr(value, "st_nlink", None)) is not int or value.st_nlink <= 0:
        _fail("FILE_IDENTITY_UNAVAILABLE")
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_nlink


def _ancestors(path: Path) -> tuple:
    return tuple((parent, _stamp(_metadata(parent, directory=True))) for parent in reversed(path.parents))


def _hash_candidate(path: Path) -> str:
    ancestors = _ancestors(path)
    before = _metadata(path)
    if before.st_size != CANDIDATE_SIZE:
        _fail("SIZE_MISMATCH")
    count, digest = 0, hashlib.sha256()
    try:
        with path.open("rb") as source:
            if _stamp(os.fstat(source.fileno())) != _stamp(before):
                _fail("SOURCE_CHANGED")
            while block := source.read(CHUNK):
                count += len(block)
                if count > CANDIDATE_SIZE:
                    _fail("SOURCE_CHANGED")
                digest.update(block)
            after_open = os.fstat(source.fileno())
    except OSError:
        _fail("READ_FAILED")
    if count != CANDIDATE_SIZE or _stamp(after_open) != _stamp(before) or _stamp(_metadata(path)) != _stamp(before) or _ancestors(path) != ancestors:
        _fail("SOURCE_CHANGED")
    return digest.hexdigest()


def verify_candidate(path: str | Path) -> dict:
    try:
        candidate = _candidate_path(path)
        if _hash_candidate(candidate) != CANDIDATE_SHA256:
            _fail("HASH_MISMATCH")
        return _offline("CANDIDATE_IDENTITY_MATCH")
    except ReadinessError as error:
        return _offline("IDENTITY_REJECTED", str(error))


def observe(reader=None, *, candidate: str | Path | None = None) -> dict:
    registry = reader if reader is not None else WindowsRegistryReader()
    rows = [registry.read(source) for source in (*VC_SOURCES, *WV_SOURCES)]
    return evaluate(rows, offline_input=verify_candidate(candidate) if candidate is not None else None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("observe",))
    parser.add_argument("--webview2-candidate", help="Explicit absolute local file only; identity is not approval")
    args = parser.parse_args(argv)
    try:
        result = observe(candidate=args.webview2_candidate)
        sys.stdout.buffer.write(encode_result(result))
        sys.stdout.buffer.flush()
        return 2 if result["offlineInputs"]["webview2"]["status"] == "IDENTITY_REJECTED" else 0
    except ReadinessError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
