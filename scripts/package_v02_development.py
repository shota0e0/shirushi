"""Bounded, non-executing Windows v0.2 DEVELOPMENT_PACKAGE byte assembler.

This is not runtime discovery, an installer, publisher verification, or release
adoption. Source snapshots and the trusted audit record live outside the package.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import struct
import sys
import tempfile

DESKTOP = "shirushi-desktop.exe"
HELPER = "shirushi-inspection-helper.exe"
MANIFEST = "inspection-helper.manifest.json"
FILES = frozenset((DESKTOP, HELPER, MANIFEST))
MAX_EXECUTABLE = 256 * 1024 * 1024
MAX_MANIFEST = 4096
MAX_RECORD = 8192
HEADER_BOUND = 64 * 1024
CHUNK = 1024 * 1024
REPARSE = 0x400
HASH = re.compile(r"[0-9a-f]{64}\Z")
RESERVED = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|\Z)", re.I)


class PackageError(Exception):
    """Closed public category; no private path or exception text."""


def _fail(code: str) -> None:
    raise PackageError(code)


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _unique(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def _json(raw: bytes, bound: int) -> dict:
    if not raw or len(raw) > bound:
        _fail("JSON_SIZE_INVALID")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError):
        _fail("JSON_INVALID")
    if not isinstance(value, dict):
        _fail("JSON_SCHEMA_INVALID")
    return value


def _keys(value: dict, expected: set) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        _fail("JSON_SCHEMA_INVALID")


def _file_fields(value: dict, maximum: int) -> None:
    _keys(value, {"size", "sha256"})
    if type(value["size"]) is not int or not 0 < value["size"] <= maximum:
        _fail("FILE_SIZE_INVALID")
    if not isinstance(value["sha256"], str) or not HASH.fullmatch(value["sha256"]):
        _fail("HASH_INVALID")


def parse_manifest(raw: bytes) -> dict:
    value = _json(raw, MAX_MANIFEST)
    _keys(value, {"schemaVersion", "relativePath", "size", "sha256", "protocolVersion"})
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        _fail("MANIFEST_VERSION_INVALID")
    if type(value["protocolVersion"]) is not int or value["protocolVersion"] != 1:
        _fail("MANIFEST_PROTOCOL_INVALID")
    if value["relativePath"] != HELPER:
        _fail("MANIFEST_HELPER_PATH_INVALID")
    _file_fields({"size": value["size"], "sha256": value["sha256"]}, MAX_EXECUTABLE)
    return value


def parse_record(raw: bytes) -> dict:
    value = _json(raw, MAX_RECORD)
    _keys(value, {"schemaVersion", "artifactType", "target", "runtimeBinding", "files"})
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        _fail("RECORD_VERSION_INVALID")
    if value["artifactType"] != "DEVELOPMENT_PACKAGE" or value["target"] != "x86_64-pc-windows-msvc" or value["runtimeBinding"] != "UNPROVEN":
        _fail("RECORD_SCOPE_INVALID")
    _keys(value["files"], set(FILES))
    for name in FILES:
        _file_fields(value["files"][name], MAX_MANIFEST if name == MANIFEST else MAX_EXECUTABLE)
    return value


def _reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & REPARSE)


def _drive_type(root: str) -> int:
    # No executable/PATH search. Query the Windows volume before opening paths.
    if os.name != "nt":
        _fail("WINDOWS_REQUIRED")
    function = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    function.argtypes = [ctypes.c_wchar_p]
    function.restype = ctypes.c_uint32
    return int(function(root))


def _path(value: str | Path) -> Path:
    raw = str(value)
    if len(raw) > 4096 or not re.match(r"\A[A-Za-z]:[\\/]", raw):
        _fail("ABSOLUTE_LOCAL_PATH_REQUIRED")
    parts = re.split(r"[\\/]", raw[3:])
    if not parts or len(parts) > 32:
        _fail("PATH_INVALID")
    for part in parts:
        if not part or part in (".", "..") or part.endswith((" ", ".")) or RESERVED.match(part):
            _fail("PATH_INVALID")
        if any(ord(c) < 32 or c in '<>:"|?*' for c in part):
            _fail("PATH_INVALID")
    windows = PureWindowsPath(raw)
    if not windows.is_absolute() or _drive_type(windows.drive + "\\") != 3:
        _fail("LOCAL_FIXED_DRIVE_REQUIRED")
    return Path(raw)


def _metadata(path: Path, directory: bool = False) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError:
        _fail("PATH_UNAVAILABLE")
    if _reparse(metadata):
        _fail("REPARSE_PATH_REJECTED")
    if not (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)):
        _fail("PATH_TYPE_INVALID")
    return metadata


def _ancestors(path: Path) -> None:
    for parent in reversed(path.parents):
        _metadata(parent, directory=True)


def _stamp(value: os.stat_result) -> tuple:
    # Windows Python 3.12 lstat/fstat can expose different ctime semantics.
    # Compare common identity/size/mtime and independently verify byte hashes.
    return *_identity(value), value.st_size, value.st_mtime_ns


def _identity(value: os.stat_result) -> tuple:
    device, inode = value.st_dev, value.st_ino
    if type(device) is not int or device < 0 or type(inode) is not int or inode <= 0:
        _fail("FILE_IDENTITY_UNAVAILABLE")
    return device, inode


def _pe(header: bytes, size: int, subsystem: int) -> None:
    # Header identity only, not exhaustive PE validation or publisher proof.
    try:
        if len(header) < 64 or header[:2] != b"MZ":
            _fail("PE_HEADER_INVALID")
        offset = struct.unpack_from("<I", header, 60)[0]
        if offset < 64 or offset + 24 > len(header) or header[offset:offset + 4] != b"PE\0\0":
            _fail("PE_HEADER_INVALID")
        machine, sections = struct.unpack_from("<HH", header, offset + 4)
        optional_size, characteristics = struct.unpack_from("<HH", header, offset + 20)
        optional = offset + 24
        table = optional + optional_size
        if machine != 0x8664 or not 1 <= sections <= 96 or not 112 <= optional_size <= 4096:
            _fail("PE_IDENTITY_INVALID")
        if table + 40 * sections > len(header) or table + 40 * sections > size:
            _fail("PE_SECTION_BOUNDS_INVALID")
        if not characteristics & 2 or characteristics & (0x2000 | 0x100):
            _fail("PE_EXECUTABLE_INVALID")
        if struct.unpack_from("<H", header, optional)[0] != 0x20B or struct.unpack_from("<H", header, optional + 68)[0] != subsystem:
            _fail("PE_IDENTITY_INVALID")
        for i in range(sections):
            count, pointer = struct.unpack_from("<II", header, table + 40 * i + 16)
            if count and (pointer < table + 40 * sections or pointer + count > size):
                _fail("PE_SECTION_BOUNDS_INVALID")
    except struct.error:
        _fail("PE_HEADER_INVALID")


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    identity: tuple
    size: int
    sha256: str


@dataclass(frozen=True)
class InputSnapshot:
    desktop: FileSnapshot
    helper: FileSnapshot


def _read_file(path: Path, maximum: int, subsystem: int | None = None) -> tuple[os.stat_result, int, str]:
    _ancestors(path)
    before = _metadata(path)
    if not 0 < before.st_size <= maximum:
        _fail("FILE_SIZE_INVALID")
    digest, count, header = hashlib.sha256(), 0, bytearray()
    try:
        with path.open("rb") as source:
            if _stamp(os.fstat(source.fileno())) != _stamp(before):
                _fail("SOURCE_CHANGED")
            while block := source.read(CHUNK):
                count += len(block)
                if count > before.st_size or count > maximum:
                    _fail("SOURCE_CHANGED")
                digest.update(block)
                if len(header) < HEADER_BOUND:
                    header.extend(block[:HEADER_BOUND - len(header)])
            after_open = os.fstat(source.fileno())
    except OSError:
        _fail("FILE_READ_FAILED")
    after = _metadata(path)
    _ancestors(path)
    if count != before.st_size or _stamp(before) != _stamp(after_open) or _stamp(before) != _stamp(after):
        _fail("SOURCE_CHANGED")
    if subsystem is not None:
        _pe(bytes(header), count, subsystem)
    return before, count, digest.hexdigest()


def _read_small(path: Path, maximum: int) -> bytes:
    _ancestors(path)
    before = _metadata(path)
    if not 0 < before.st_size <= maximum:
        _fail("FILE_SIZE_INVALID")
    try:
        with path.open("rb") as source:
            if _stamp(os.fstat(source.fileno())) != _stamp(before):
                _fail("SOURCE_CHANGED")
            raw = source.read(maximum + 1)
            after_open = os.fstat(source.fileno())
    except OSError:
        _fail("FILE_READ_FAILED")
    if len(raw) != before.st_size or _stamp(after_open) != _stamp(before) or _stamp(_metadata(path)) != _stamp(before):
        _fail("SOURCE_CHANGED")
    return raw


def capture_inputs(desktop: str | Path, helper: str | Path) -> InputSnapshot:
    paths = (_path(desktop), _path(helper))
    if paths[0].name != DESKTOP or paths[1].name != HELPER:
        _fail("SOURCE_BASENAME_INVALID")
    result = []
    for path, subsystem in zip(paths, (2, 3)):
        metadata, size, sha = _read_file(path, MAX_EXECUTABLE, subsystem)
        result.append(FileSnapshot(path, _stamp(metadata), size, sha))
    if result[0].identity[:2] == result[1].identity[:2]:
        _fail("SOURCE_IDENTITY_COLLISION")
    return InputSnapshot(*result)


def _unchanged(snapshot: FileSnapshot) -> None:
    _path(snapshot.path)
    subsystem = 2 if snapshot.path.name == DESKTOP else 3
    metadata, size, sha = _read_file(snapshot.path, MAX_EXECUTABLE, subsystem)
    if _stamp(metadata) != snapshot.identity or size != snapshot.size or sha != snapshot.sha256:
        _fail("SOURCE_CHANGED")


def _manifest(snapshot: InputSnapshot) -> bytes:
    raw = _canonical({"schemaVersion": 1, "relativePath": HELPER, "size": snapshot.helper.size,
                      "sha256": snapshot.helper.sha256, "protocolVersion": 1})
    parse_manifest(raw)
    return raw


def _record(snapshot: InputSnapshot, manifest: bytes) -> dict:
    value = {"schemaVersion": 1, "artifactType": "DEVELOPMENT_PACKAGE",
             "target": "x86_64-pc-windows-msvc", "runtimeBinding": "UNPROVEN", "files": {
                 DESKTOP: {"size": snapshot.desktop.size, "sha256": snapshot.desktop.sha256},
                 HELPER: {"size": snapshot.helper.size, "sha256": snapshot.helper.sha256},
                 MANIFEST: {"size": len(manifest), "sha256": hashlib.sha256(manifest).hexdigest()}}}
    parse_record(_canonical(value))
    return value


def _inventory(root: Path) -> None:
    _ancestors(root)
    _metadata(root, directory=True)
    try:
        names = []
        with os.scandir(root) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names) > 3:
                    _fail("PACKAGE_INVENTORY_INVALID")
    except OSError:
        _fail("INVENTORY_UNAVAILABLE")
    if len(names) != 3 or len({name.casefold() for name in names}) != 3 or set(names) != FILES:
        _fail("PACKAGE_INVENTORY_INVALID")
    for name in names:
        _metadata(root / name)


def audit_package(root: str | Path, trusted_record: dict) -> None:
    """The caller must supply independently frozen evidence, not adjacent hashes."""
    record = parse_record(_canonical(trusted_record))
    package = _path(root)
    _inventory(package)
    for name in FILES:
        maximum = MAX_MANIFEST if name == MANIFEST else MAX_EXECUTABLE
        _, size, sha = _read_file(package / name, maximum, 2 if name == DESKTOP else 3 if name == HELPER else None)
        if record["files"][name] != {"size": size, "sha256": sha}:
            _fail("PACKAGE_IDENTITY_MISMATCH")
    # Read only the bounded manifest, then compare helper fields with independent
    # source evidence as well as the actual staged helper. It cannot authorize itself.
    raw = _read_small(package / MANIFEST, MAX_MANIFEST)
    if hashlib.sha256(raw).hexdigest() != record["files"][MANIFEST]["sha256"]:
        _fail("PACKAGE_IDENTITY_MISMATCH")
    manifest = parse_manifest(raw)
    if raw != _canonical(manifest):
        _fail("MANIFEST_NOT_CANONICAL")
    if manifest["size"] != record["files"][HELPER]["size"] or manifest["sha256"] != record["files"][HELPER]["sha256"]:
        _fail("MANIFEST_IDENTITY_MISMATCH")
    _inventory(package)


def _stage_destination(destination: Path, owner: tuple) -> None:
    _ancestors(destination)
    stage = destination.parent
    if destination.name not in FILES or not stage.name.startswith(".shirushi-v02-stage-"):
        _fail("STAGE_IDENTITY_CHANGED")
    if _identity(_metadata(stage, directory=True)) != owner:
        _fail("STAGE_IDENTITY_CHANGED")
    if os.path.lexists(destination):
        _fail("STAGE_DESTINATION_ALREADY_EXISTS")


def _copy(snapshot: FileSnapshot, destination: Path, owner: tuple) -> None:
    _unchanged(snapshot)
    _stage_destination(destination, owner)
    digest, count = hashlib.sha256(), 0
    try:
        with snapshot.path.open("rb") as source, destination.open("xb") as target:
            if _stamp(os.fstat(source.fileno())) != snapshot.identity:
                _fail("SOURCE_CHANGED")
            while block := source.read(CHUNK):
                count += len(block)
                if count > snapshot.size:
                    _fail("SOURCE_CHANGED")
                target.write(block)
                digest.update(block)
            target.flush()
            os.fsync(target.fileno())
    except OSError:
        _fail("STAGING_COPY_FAILED")
    if count != snapshot.size or digest.hexdigest() != snapshot.sha256:
        _fail("SOURCE_CHANGED")
    _unchanged(snapshot)


def _output(root: str | Path, snapshot: InputSnapshot) -> tuple[Path, tuple]:
    output = _path(root)
    _ancestors(output)
    if os.path.lexists(output):
        _fail("OUTPUT_ALREADY_EXISTS")
    case_key = str(output).replace("/", "\\").casefold().rstrip("\\")
    for source in (snapshot.desktop.path, snapshot.helper.path):
        source_key = str(source).replace("/", "\\").casefold()
        if source_key == case_key or source_key.startswith(case_key + "\\"):
            _fail("SOURCE_OUTPUT_OVERLAP")
    with os.scandir(output.parent) as entries:
        for count, entry in enumerate(entries):
            if count >= 4096:
                _fail("OUTPUT_PARENT_INVENTORY_LIMIT")
            if entry.name.casefold() == output.name.casefold():
                _fail("OUTPUT_CASE_COLLISION")
    return output, _identity(_metadata(output.parent, directory=True))


def _cleanup(stage: Path, owner: tuple) -> None:
    # Never rmtree, follow a reparse point, or delete a published output. Only
    # this exclusively allocated, identity-matching, flat staging directory.
    _ancestors(stage)
    if not stage.name.startswith(".shirushi-v02-stage-") or _identity(_metadata(stage, directory=True)) != owner:
        _fail("STAGE_CLEANUP_REFUSED")
    entries = []
    with os.scandir(stage) as listing:
        for entry in listing:
            if len(entries) >= 3 or entry.name not in FILES:
                _fail("STAGE_CLEANUP_REFUSED")
            entries.append(Path(entry.path))
    for entry in entries:
        _metadata(entry)
    for entry in entries:
        entry.unlink()
    stage.rmdir()


def assemble(desktop: str | Path, helper: str | Path, output_root: str | Path,
             *, snapshot: InputSnapshot | None = None) -> dict:
    """Snapshot internally unless the caller supplies an earlier immutable one."""
    if os.name != "nt":
        _fail("WINDOWS_NO_CLOBBER_PUBLISH_REQUIRED")
    frozen = snapshot or capture_inputs(desktop, helper)
    if frozen.desktop.path != _path(desktop) or frozen.helper.path != _path(helper):
        _fail("SNAPSHOT_PATH_MISMATCH")
    if frozen.desktop.path.name != DESKTOP or frozen.helper.path.name != HELPER:
        _fail("SOURCE_BASENAME_INVALID")
    _unchanged(frozen.desktop)
    _unchanged(frozen.helper)
    output, parent_identity = _output(output_root, frozen)
    stage = Path(tempfile.mkdtemp(prefix=".shirushi-v02-stage-", dir=output.parent))
    owner = _identity(_metadata(stage, directory=True))
    published = False
    try:
        _copy(frozen.desktop, stage / DESKTOP, owner)
        _copy(frozen.helper, stage / HELPER, owner)
        # Derive from actual copied helper only AFTER it matches frozen identity.
        _, size, sha = _read_file(stage / HELPER, MAX_EXECUTABLE, 3)
        if size != frozen.helper.size or sha != frozen.helper.sha256:
            _fail("STAGED_HELPER_CHANGED")
        manifest = _canonical({"schemaVersion": 1, "relativePath": HELPER, "size": size,
                               "sha256": sha, "protocolVersion": 1})
        if manifest != _manifest(frozen):
            _fail("MANIFEST_IDENTITY_MISMATCH")
        _stage_destination(stage / MANIFEST, owner)
        with (stage / MANIFEST).open("xb") as target:
            target.write(manifest)
        record = _record(frozen, manifest)
        audit_package(stage, record)
        _unchanged(frozen.desktop)
        _unchanged(frozen.helper)
        _, now = _output(output, frozen)
        if now != parent_identity or _identity(_metadata(stage, directory=True)) != owner:
            _fail("OUTPUT_PARENT_CHANGED")
        # Windows os.rename fails if destination exists, including an empty
        # directory. No replace(), Force, overwrite, or POSIX clobber fallback.
        try:
            os.rename(stage, output)
        except OSError:
            _fail("NO_CLOBBER_PUBLISH_FAILED")
        published = True
        audit_package(output, record)
        return record
    finally:
        if not published and os.path.lexists(stage):
            try:
                _cleanup(stage, owner)
            except (PackageError, OSError):
                _fail("ASSEMBLY_FAILED_CLEANUP_REFUSED")


class _Arguments(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _fail("CLI_ARGUMENTS_INVALID")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _Arguments(description=__doc__)
        commands = parser.add_subparsers(dest="command", required=True, parser_class=_Arguments)
        make = commands.add_parser("assemble")
        make.add_argument("--desktop", required=True)
        make.add_argument("--helper", required=True)
        make.add_argument("--output-root", required=True)
        check = commands.add_parser("audit")
        check.add_argument("--package-root", required=True)
        check.add_argument("--record", required=True)
        check.add_argument("--expected-record-sha256", required=True)
        arguments = parser.parse_args(argv)
        if arguments.command == "assemble":
            value = assemble(arguments.desktop, arguments.helper, arguments.output_root)
            raw = _canonical(value) + b"\n"
            # Stdout only: capture this OUTSIDE the package and independently
            # freeze its digest. No fourth package file is created.
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()
            print("DEVELOPMENT_RECORD_SHA256: " + hashlib.sha256(raw).hexdigest(), file=sys.stderr)
        else:
            record_path, package = _path(arguments.record), _path(arguments.package_root)
            if record_path == package or package in record_path.parents:
                _fail("AUDIT_RECORD_MUST_BE_EXTERNAL")
            if not HASH.fullmatch(arguments.expected_record_sha256):
                _fail("HASH_INVALID")
            raw = _read_small(record_path, MAX_RECORD)
            # Digest includes the exact emitted stdout bytes, including its LF.
            if hashlib.sha256(raw).hexdigest() != arguments.expected_record_sha256:
                _fail("TRUSTED_RECORD_CHANGED")
            audit_package(package, parse_record(raw))
            print('{"classification":"DEVELOPMENT_PACKAGE_AUDIT_OK","runtimeBinding":"UNPROVEN"}')
        return 0
    except (PackageError, OSError) as error:
        code = str(error) if isinstance(error, PackageError) else "FILESYSTEM_OPERATION_FAILED"
        print(json.dumps({"classification": "DEVELOPMENT_PACKAGE_FAILED", "errorCode": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
