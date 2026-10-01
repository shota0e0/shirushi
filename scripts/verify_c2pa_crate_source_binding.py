"""Offline binding of one approved Cargo crate to frozen MPL source evidence.

Use --crate with an explicit existing archive; no cache/index discovery occurs.
Only the public validate() performs the complete approval gate. The private
content parser is separately exercised with synthetic archives in direct tests.
No archive content is extracted, imported, executed, or used as an authority.
Requires Python 3.11+ (standard-library tomllib). This is not legal approval.
"""

import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tomllib
import zlib


# Load only the accepted, fixed repository validator, never archive/input code.
_spec = importlib.util.spec_from_file_location(
    "_c2pa_approved_mpl_evidence", Path(__file__).with_name("verify_c2pa_mpl_source_evidence.py")
)
mpl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mpl)

PACKAGE = "c2pa"
VERSION = "0.85.0"
ARCHIVE_SHA256 = "cd6fa73bf92e8ae8980779f39ad4bf2a1e575eb97f95f2accf1e73f0602c5b0b"
VCS_COMMIT = "3f40cdd22b60bf955d531b0301604e3f257e0a19"
PATH_IN_VCS = "sdk"
REGISTRY_SOURCE = "registry+https://github.com/rust-lang/crates.io-index"
PREFIX = "c2pa-0.85.0/"
DEFAULT_LOCK = Path(__file__).resolve().parents[1] / "tools/f3a-rust-sdk-parity/Cargo.lock"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_TAR_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 1024
MAX_MEMBER_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024
MAX_VCS_BYTES = 4096
MAX_LOCK_BYTES = 1024 * 1024
SOURCE_ENTRIES = tuple(
    (PREFIX + PurePosixPath(path).relative_to(PATH_IN_VCS).as_posix(), path, size, sha)
    for path, size, sha in mpl.SOURCE_FILES
)


class BindingError(ValueError):
    """A bounded, path-free failure category."""


def _read_file(path, limit):
    try:
        return mpl._read_regular(Path(path), limit)
    except mpl.EvidenceError:
        raise BindingError("INPUT_FILE_INVALID") from None


def _toml(raw, category):
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError, RecursionError):
        raise BindingError(category) from None


def _validate_lock(raw):
    record = _toml(raw, "LOCK_METADATA_INVALID")
    packages = record.get("package")
    if not isinstance(packages, list) or any(not isinstance(p, dict) for p in packages):
        raise BindingError("LOCK_METADATA_INVALID")
    candidates = [p for p in packages if p.get("name") == PACKAGE]
    if len(candidates) != 1:
        raise BindingError("LOCK_PACKAGE_INVALID")
    package = candidates[0]
    if (package.get("version") != VERSION or package.get("source") != REGISTRY_SOURCE
            or package.get("checksum") != ARCHIVE_SHA256):
        raise BindingError("LOCK_IDENTITY_MISMATCH")


def _vcs(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise BindingError("VCS_METADATA_INVALID")
            result[key] = value
        return result

    try:
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(BindingError("VCS_METADATA_INVALID")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise BindingError("VCS_METADATA_INVALID") from None
    if (type(record) is not dict or record.keys() != {"git", "path_in_vcs"}
            or type(record["git"]) is not dict or record["git"].keys() != {"sha1"}
            or type(record["git"]["sha1"]) is not str or type(record["path_in_vcs"]) is not str):
        raise BindingError("VCS_METADATA_INVALID")
    if record != {"git": {"sha1": VCS_COMMIT}, "path_in_vcs": PATH_IN_VCS}:
        raise BindingError("VCS_IDENTITY_MISMATCH")


def _safe_member(member):
    name = member.name
    parts = PurePosixPath(name).parts
    if (not name or len(name) > 1024 or "\\" in name or ":" in name or "\0" in name
            or name.startswith("/") or ".." in parts
            or str(PurePosixPath(name)) != name or not name.startswith(PREFIX)
            or any(part.endswith((".", " ")) for part in parts)):
        raise BindingError("ARCHIVE_PATH_INVALID")
    # The approved archive contains regular files only. Reject links/devices,
    # sparse data, directories, and format extensions rather than extracting.
    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.sparse is not None:
        raise BindingError("ARCHIVE_ENTRY_INVALID")
    if member.pax_headers or member.size < 0 or member.size > MAX_MEMBER_BYTES:
        raise BindingError("ARCHIVE_STRUCTURE_INVALID")


def _inspect_archive(raw):
    """Private content checks, NOT archive approval; public SHA gate precedes this.

    Synthetic unit fixtures call this directly to exercise unreachable-after-
    SHA-rejection cases without adding any production hash override.
    """
    if not raw or len(raw) > MAX_ARCHIVE_BYTES:
        raise BindingError("ARCHIVE_SIZE_INVALID")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as compressed:
            tar_bytes = compressed.read(MAX_TAR_BYTES + 1)
        if len(tar_bytes) > MAX_TAR_BYTES:
            raise BindingError("ARCHIVE_EXPANSION_LIMIT")
        if len(tar_bytes) % tarfile.BLOCKSIZE or not tar_bytes:
            raise BindingError("ARCHIVE_FORMAT_INVALID")
        cargo_path = PREFIX + "Cargo.toml"
        vcs_path = PREFIX + ".cargo_vcs_info.json"
        approved = {entry[0] for entry in SOURCE_ENTRIES}
        selected = approved | {cargo_path, vcs_path}
        seen, contents, total = set(), {}, 0
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
            for count, member in enumerate(archive, 1):
                if count > MAX_MEMBERS:
                    raise BindingError("ARCHIVE_MEMBER_LIMIT")
                _safe_member(member)
                folded = member.name.casefold()
                if folded in seen:
                    raise BindingError("ARCHIVE_DUPLICATE_ENTRY")
                seen.add(folded)
                total += member.size
                if total > MAX_TAR_BYTES:
                    raise BindingError("ARCHIVE_EXPANSION_LIMIT")
                if member.name.startswith(PREFIX + "src/crypto/asn1/") and member.name not in approved:
                    raise BindingError("ARCHIVE_SOURCE_INVENTORY_INVALID")
                if member.name in selected:
                    limit = MAX_VCS_BYTES if member.name == vcs_path else MAX_METADATA_BYTES
                    if member.name in approved:
                        limit = next(size for path, _, size, _ in SOURCE_ENTRIES if path == member.name)
                    if member.size > limit:
                        raise BindingError("ARCHIVE_SELECTED_SIZE_INVALID")
                    with archive.extractfile(member) as stream:
                        contents[member.name] = stream.read(limit + 1)
                    if len(contents[member.name]) != member.size:
                        raise BindingError("ARCHIVE_FORMAT_INVALID")
            # Reject concatenated tar payloads/nonzero trailing data. Padding
            # must include at least the two EOF blocks of this frozen format.
            if len(tar_bytes) - archive.offset < 2 * tarfile.BLOCKSIZE or any(tar_bytes[archive.offset:]):
                raise BindingError("ARCHIVE_STRUCTURE_INVALID")
        if cargo_path not in contents or vcs_path not in contents:
            raise BindingError("ARCHIVE_METADATA_MISSING")
        package = _toml(contents[cargo_path], "PACKAGE_METADATA_INVALID").get("package")
        if not isinstance(package, dict) or package.get("name") != PACKAGE or package.get("version") != VERSION:
            raise BindingError("PACKAGE_IDENTITY_MISMATCH")
        _vcs(contents[vcs_path])
        if not approved.issubset(contents):
            raise BindingError("ARCHIVE_SOURCE_MISSING")
        for path, _, size, sha in SOURCE_ENTRIES:
            data = contents[path]
            if len(data) != size:
                raise BindingError("ARCHIVE_SOURCE_SIZE_MISMATCH")
            if hashlib.sha256(data).hexdigest() != sha:
                raise BindingError("ARCHIVE_SOURCE_HASH_MISMATCH")
        return {relative: contents[path] for path, relative, _, _ in SOURCE_ENTRIES}
    except (OSError, EOFError, tarfile.TarError, zlib.error, UnicodeError, ValueError) as error:
        if isinstance(error, BindingError):
            raise
        raise BindingError("ARCHIVE_FORMAT_INVALID") from None


def validate(archive_path, evidence_root=mpl.DEFAULT_ROOT, *, lock_path=DEFAULT_LOCK):
    """Complete fixed approval gate; no discovery or configurable expected hash."""
    try:
        frozen = mpl.validate(evidence_root)
    except mpl.EvidenceError:
        raise BindingError("FROZEN_EVIDENCE_INVALID") from None
    if frozen["result"] != "MPL_SOURCE_EVIDENCE_VALID":
        raise BindingError("FROZEN_EVIDENCE_INVALID")
    _validate_lock(_read_file(lock_path, MAX_LOCK_BYTES))
    raw = _read_file(archive_path, MAX_ARCHIVE_BYTES)
    if hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA256:
        raise BindingError("ARCHIVE_HASH_MISMATCH")
    sources = _inspect_archive(raw)
    for relative, size, sha in mpl.SOURCE_FILES:
        data = _read_file(Path(evidence_root) / relative, size)
        if len(data) != size or hashlib.sha256(data).hexdigest() != sha or sources[relative] != data:
            raise BindingError("FROZEN_SOURCE_BINDING_MISMATCH")
    return {"result": "C2PA_CRATE_SOURCE_BINDING_VALID", "package": PACKAGE,
            "version": VERSION, "archiveSha256": ARCHIVE_SHA256, "archiveSize": len(raw),
            "vcsCommit": VCS_COMMIT, "pathInVcs": PATH_IN_VCS,
            "sourceCount": 5, "matchedSourceBytes": 122181}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--help"]:
        print("verify_c2pa_crate_source_binding.py --crate ARCHIVE [--evidence-root ROOT]")
        return 0
    try:
        values = {}
        if not args or len(args) % 2:
            raise BindingError("CLI_INVALID")
        for key, value in zip(args[::2], args[1::2]):
            if key not in {"--crate", "--evidence-root"} or key in values or not value or value.startswith("--"):
                raise BindingError("CLI_INVALID")
            values[key] = value
        if "--crate" not in values:
            raise BindingError("CLI_INVALID")
        result = validate(values["--crate"], values.get("--evidence-root", mpl.DEFAULT_ROOT))
    except BindingError as error:
        print(json.dumps({"result": "C2PA_CRATE_SOURCE_BINDING_INVALID", "category": str(error)}, sort_keys=True))
        return 2
    except (OSError, ValueError, RecursionError):
        print(json.dumps({"result": "C2PA_CRATE_SOURCE_BINDING_INVALID", "category": "INPUT_INVALID"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
