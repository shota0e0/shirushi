"""Offline, raw-byte validation of the Owner-approved c2pa MPL evidence only.

The constants below are the independent approval contract. The adjacent JSON
documents that contract; it cannot authorize different source or license bytes.
This verifier does not establish legal sufficiency or distribution approval.
"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "packaging/license_sources/inspection-helper/c2pa-mpl"
SOURCE_FILES = (
    ("sdk/src/crypto/asn1/mod.rs", 52740, "90d0f896297b63b44986d04214bfdd896bd9f02fd1df47e8abdf86ddc9f4b5b2"),
    ("sdk/src/crypto/asn1/rfc3161.rs", 15510, "1a3974bee08c695c556b897b4381d697bf094bc304e8628881feaa47a55719de"),
    ("sdk/src/crypto/asn1/rfc3281.rs", 7345, "916728217ff7b38d3e6cc4cda1289ab060aad4ee7ed6ee73c06a85b2580c9830"),
    ("sdk/src/crypto/asn1/rfc4210.rs", 1153, "a5206504a011198c59a30fcb7ebf2f85d89d1c4325047808109b383ff308558f"),
    ("sdk/src/crypto/asn1/rfc5652.rs", 45433, "6ed39ddf6e037b2816d5d9a0b154cfc415367f0f82524e2043cb40b2964afc95"),
)
LICENSE_SIZE = 16726
LICENSE_SHA256 = "3f3d9e0024b1921b067d6f7f88deb4a60cbe7a78e76c64e3f1d7fc3b779b9d04"
MPL_HEADER = (
    b"// This Source Code Form is subject to the terms of the Mozilla Public\n"
    b"// License, v. 2.0. If a copy of the MPL was not distributed with this\n"
    b"// file, You can obtain one at https://mozilla.org/MPL/2.0/.\n"
)
MAX_RECORD_BYTES = 16384
MAX_README_BYTES = 16384
EXPECTED_FILES = frozenset(path for path, _, _ in SOURCE_FILES) | {
    "README.md", "approved-source-set.json", "MPL-2.0.txt"
}
EXPECTED_DIRECTORIES = frozenset({"sdk", "sdk/src", "sdk/src/crypto", "sdk/src/crypto/asn1"})


class EvidenceError(ValueError):
    """A closed, path-free failure category."""


def approved_contract():
    """Return a fresh contract from reviewed constants, never from input JSON."""
    return {
        "schemaVersion": 1,
        "c2pa": {
            "name": "c2pa", "version": "0.85.0", "tag": "c2pa-v0.85.0",
            "commit": "3f40cdd22b60bf955d531b0301604e3f257e0a19",
            "repository": "https://github.com/contentauth/c2pa-rs",
        },
        "originalUpstream": {
            "project": "cryptography-rs/cryptographic-message-syntax",
            "version": "0.22.0", "tag": "cryptographic-message-syntax/0.22.0",
            "commit": "c09b693c8a6f9cf7475f1310405632931f9ad12b",
            "repository": "https://github.com/indygreg/cryptography-rs",
        },
        "sourceScope": {"license": "MPL-2.0", "classification": "MODIFIED_COPY"},
        "files": [{"relativePath": path, "size": size, "sha256": sha} for path, size, sha in SOURCE_FILES],
        "licenseText": {
            "relativePath": "MPL-2.0.txt", "size": LICENSE_SIZE,
            "sha256": LICENSE_SHA256,
            "authoritativeSource": "https://www.mozilla.org/media/MPL/2.0/index.txt",
        },
    }


def _duplicate_free(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _schema(value, template):
    # Exact type checks intentionally reject bool as an integer.
    if type(value) is not type(template):
        raise EvidenceError("RECORD_TYPE_INVALID")
    if isinstance(template, dict):
        if value.keys() != template.keys():
            raise EvidenceError("RECORD_FIELDS_INVALID")
        for key in template:
            _schema(value[key], template[key])
    elif isinstance(template, list):
        if len(value) != len(template):
            raise EvidenceError("RECORD_INVENTORY_INVALID")
        for actual, expected in zip(value, template):
            _schema(actual, expected)


def validate_record(raw):
    if not raw or len(raw) > MAX_RECORD_BYTES:
        raise EvidenceError("JSON_SIZE_INVALID")
    try:
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_free,
                            parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError("JSON_INVALID")))
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        if isinstance(error, EvidenceError):
            raise
        raise EvidenceError("JSON_INVALID") from None
    expected = approved_contract()
    _schema(record, expected)
    entries = record["files"] + [record["licenseText"]]
    paths = []
    for entry in entries:
        path = entry["relativePath"]
        parts = PurePosixPath(path).parts
        if not path or "\\" in path or ":" in path or "\0" in path or path.startswith("/") or ".." in parts or str(PurePosixPath(path)) != path:
            raise EvidenceError("RECORD_PATH_INVALID")
        if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise EvidenceError("RECORD_HASH_INVALID")
        paths.append(path.casefold())
    if len(set(paths)) != len(paths):
        raise EvidenceError("RECORD_PATH_COLLISION")
    if record != expected:
        raise EvidenceError("APPROVAL_CONTRACT_MISMATCH")
    return record


def _is_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _checked_stat(path):
    try:
        info = path.lstat()
    except OSError:
        raise EvidenceError("FILE_UNAVAILABLE") from None
    if _is_reparse(info):
        raise EvidenceError("LINK_OR_REPARSE_REJECTED")
    return info


def _inventory(root):
    # Check ancestors without resolving links, including an injected root.
    for parent in reversed((root,) + tuple(root.parents)):
        if not stat.S_ISDIR(_checked_stat(parent).st_mode):
            raise EvidenceError("DIRECTORY_INVALID")
    files, directories, folded = set(), set(), set()
    pending = [(root, "")]
    try:
        while pending:
            directory, prefix = pending.pop()
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda item: item.name)
            for child in children:
                relative = prefix + child.name
                if relative.casefold() in folded:
                    raise EvidenceError("INVENTORY_PATH_COLLISION")
                folded.add(relative.casefold())
                info = _checked_stat(Path(child.path))
                if stat.S_ISDIR(info.st_mode):
                    if relative not in EXPECTED_DIRECTORIES:
                        raise EvidenceError("INVENTORY_UNEXPECTED")
                    directories.add(relative)
                    pending.append((Path(child.path), relative + "/"))
                elif stat.S_ISREG(info.st_mode):
                    if relative not in EXPECTED_FILES:
                        raise EvidenceError("INVENTORY_UNEXPECTED")
                    files.add(relative)
                else:
                    raise EvidenceError("NON_REGULAR_FILE")
    except OSError:
        raise EvidenceError("INVENTORY_UNAVAILABLE") from None
    if files != EXPECTED_FILES or directories != EXPECTED_DIRECTORIES:
        raise EvidenceError("INVENTORY_MISSING")


def _read_regular(path, limit):
    before = _checked_stat(path)
    if not stat.S_ISREG(before.st_mode):
        raise EvidenceError("NON_REGULAR_FILE")
    if before.st_size > limit:
        raise EvidenceError("FILE_SIZE_INVALID")
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or _is_reparse(opened) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise EvidenceError("FILE_CHANGED_DURING_READ")
            data = stream.read(limit + 1)
        after = _checked_stat(path)
    except OSError:
        raise EvidenceError("FILE_UNAVAILABLE") from None
    if len(data) > limit or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
        raise EvidenceError("FILE_CHANGED_DURING_READ")
    return data


def validate(root=DEFAULT_ROOT):
    root = Path(os.path.abspath(root))  # No symlink resolution.
    _inventory(root)
    validate_record(_read_regular(root / "approved-source-set.json", MAX_RECORD_BYTES))
    _read_regular(root / "README.md", MAX_README_BYTES)
    for path, size, sha in SOURCE_FILES:
        data = _read_regular(root / path, size)
        if not data.startswith(MPL_HEADER):
            raise EvidenceError("MPL_HEADER_MISMATCH")
        if len(data) != size:
            raise EvidenceError("SOURCE_SIZE_MISMATCH")
        if hashlib.sha256(data).hexdigest() != sha:
            raise EvidenceError("SOURCE_HASH_MISMATCH")
    license_data = _read_regular(root / "MPL-2.0.txt", LICENSE_SIZE)
    if len(license_data) != LICENSE_SIZE:
        raise EvidenceError("LICENSE_SIZE_MISMATCH")
    if hashlib.sha256(license_data).hexdigest() != LICENSE_SHA256:
        raise EvidenceError("LICENSE_HASH_MISMATCH")
    return {"result": "MPL_SOURCE_EVIDENCE_VALID", "schemaVersion": 1,
            "sourceCount": 5, "totalSourceBytes": 122181, "licenseSha256": LICENSE_SHA256}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--help"]:
        print("verify_c2pa_mpl_source_evidence.py [--root EVIDENCE_DIRECTORY]")
        return 0
    try:
        if not args:
            root = DEFAULT_ROOT
        elif len(args) == 2 and args[0] == "--root":
            root = Path(args[1])
        else:
            raise EvidenceError("CLI_INVALID")
        result = validate(root)
    except EvidenceError as error:
        print(json.dumps({"result": "MPL_SOURCE_EVIDENCE_INVALID", "category": str(error)}, sort_keys=True))
        return 2
    except (OSError, ValueError, RecursionError):
        print(json.dumps({"result": "MPL_SOURCE_EVIDENCE_INVALID", "category": "INPUT_UNAVAILABLE"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
