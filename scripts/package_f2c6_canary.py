"""Create and independently audit the minimal F2C.6 DEVELOPMENT CANARY.

The package is an unsigned, debug-only test artifact.  It is not an installer,
release, Python runtime, or native/manual verification result.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import struct
import tomllib
from typing import Callable


SCHEMA_VERSION = 1
ARTIFACT_TYPE = "DEVELOPMENT_CANARY"
TARGET_TRIPLE = "x86_64-pc-windows-msvc"
CRATES_IO_SOURCE = "registry+https://github.com/rust-lang/crates.io-index"
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
PACKAGE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,127}\Z")
LICENSE_EXPRESSION = re.compile(r"[A-Za-z0-9(][A-Za-z0-9 .+()/:-]{0,255}\Z")
DEPENDENCY_TEXT = re.compile(r"THIRD_PARTY_LICENSES/texts/[0-9a-f]{64}\.txt\Z")
LICENSE_NAMES = re.compile(
    r"(?:licen[cs]e|copying|copyright|notice)(?:[-_.].*)?\Z", re.IGNORECASE
)
SIDECAR_FILES = (
    "scripts/shirushi_bridge.py",
    "src/desktop_bridge.py",
    "src/personal_mark_v2_store.py",
    "src/personal_mark_v2.py",
    "src/personal_mark_unicode16.py",
    "src/personal_mark.py",
    "src/personal_mark_store.py",
    "src/runtime_paths.py",
    "src/data/personal_mark_unicode16.json",
    "src/data/Unicode-LICENSE.txt",
)
SOURCE_COPIES = {
    **{name: (name, "python-sidecar") for name in SIDECAR_FILES},
    "demo-profile/Shirushi/personal-mark/personal-mark-v2.json": (
        "scripts/canary/demo-personal-mark-v2.json",
        "synthetic-demo-profile",
    ),
    "Run-Canary.ps1": ("scripts/canary/Run-Canary.ps1", "canary-launcher"),
    "README.md": ("scripts/canary/README.md", "canary-instructions"),
    "LICENSE": ("LICENSE", "project-license"),
    "THIRD_PARTY_NOTICES.md": ("THIRD_PARTY_NOTICES.md", "project-notices"),
    "notices/BRANDING-NOTICE.md": ("assets/branding/README.md", "branding-notice"),
    "notices/Unicode-LICENSE-web.txt": (
        "web/personal-mark-v2/Unicode-LICENSE.txt",
        "unicode-license",
    ),
}
RUST_LIBRARY_NOTICE = "notices/rust-1.97.1-COPYRIGHT-library.html"
CORE_FILES = {
    "shirushi-desktop.exe", "shirushi-canary-runner.exe",
    RUST_LIBRARY_NOTICE, *SOURCE_COPIES,
}
GENERATED_FILES = {"THIRD_PARTY_LICENSES/manifest.json"}
CONTROL_FILES = {"manifest.json", "SHA256SUMS"}
FORBIDDEN_PATH_PARTS = {
    ".git", ".venv", ".venv-py312", "target", "cache", "caches", "logs",
    "models", "pdb", "signing", "credentials", "c2patool", "private",
}
FORBIDDEN_SUFFIXES = {
    ".pdb", ".msi", ".msix", ".appx", ".cer", ".crt", ".key", ".p12",
    ".pfx", ".pem", ".png", ".jpg", ".jpeg", ".webp", ".gif",
}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(rb"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
)
SECRET_TEXT_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
PERSONAL_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]Users[\\/](?!runneradmin(?:[\\/]|\b))", re.I),
    re.compile(r"/(?:home|Users)/(?!runneradmin(?:/|\b))[^\s\x00]+", re.I),
    re.compile(r"[A-Za-z]:[\\/](?:dev|src)[\\/]", re.I),
)
ALLOWED_BINARY_PATH_PREFIXES = (
    "d:\\a\\", "c:\\users\\runneradmin\\", "c:\\users\\runner~1\\",
    "c:\\hostedtoolcache\\", "c:\\program files\\",
    "c:\\programdata\\chocolatey\\", "c:\\windows\\",
)
NIKI_FIXTURE = {
    "version": 2,
    "type": "typed",
    "text": "Niki",
    "renderProfile": {"id": "shirushi-typed", "version": 1},
}
MAX_METADATA_PACKAGES = 2048
MAX_PREFLIGHT_OUTPUT_BYTES = 262_144

# Exact locked crates whose published archives omit the upstream workspace's
# license material. Native package files take precedence over these copies.
_ALLOC_LICENSE = (
    ("LICENSE", "packaging/license_sources/f2c6-rust/alloc-stdlib/LICENSE",
     "c0c56f26d9c051cac4d200c34c84e7ae9aaa853e01a982a1df08b09931e518ae",
     "https://api.github.com/repos/dropbox/rust-alloc-no-stdlib/git/blobs/cd496ba72eb37e83a44958358d1f89a8a28cbc15"),
)
_DEFMT_LICENSES = (
    ("LICENSE-APACHE", "packaging/license_sources/f2c6-rust/defmt/LICENSE-APACHE",
     "8173d5c29b4f956d532781d2b86e4e30f83e6b7878dce18c919451d6ba707c90",
     "https://api.github.com/repos/knurling-rs/defmt/git/blobs/11069edd79019f7dafbe3138841cf289209270dd"),
    ("LICENSE-MIT", "packaging/license_sources/f2c6-rust/defmt/LICENSE-MIT",
     "2710a622a896bba67356913d4d0492cab5465f61b2ecce6d880aeb483834fb50",
     "https://api.github.com/repos/knurling-rs/defmt/git/blobs/e68a7ab37c8accca023db5f1505d48e4375d4324"),
)
_SELECTORS_LICENSE = (
    ("MPL-2.0.txt", "packaging/license_sources/f2c6-rust/selectors/MPL-2.0.txt",
     "3f3d9e0024b1921b067d6f7f88deb4a60cbe7a78e76c64e3f1d7fc3b779b9d04",
     "https://www.mozilla.org/media/MPL/2.0/index.f75d2927d3c1.txt"),
)
_UNIC_LICENSES = (
    ("COPYRIGHT.md", "packaging/license_sources/f2c6-rust/rust-unic/COPYRIGHT.md",
     "f5c342c49f3ac804f3e8e7bb62a8040a44c50d47bb36902b1abd13f66a1adf8b",
     "https://api.github.com/repos/open-i18n/rust-unic/git/blobs/0322ceec0b8f15aa2d74a6173c07cc8c6f3764b3"),
    ("LICENSE-APACHE", "packaging/license_sources/f2c6-rust/rust-unic/LICENSE-APACHE",
     "a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2",
     "https://api.github.com/repos/open-i18n/rust-unic/git/blobs/16fe87b06e802f094b3fbb0894b137bca2b16ef1"),
    ("LICENSE-MIT", "packaging/license_sources/f2c6-rust/rust-unic/LICENSE-MIT",
     "23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3",
     "https://api.github.com/repos/open-i18n/rust-unic/git/blobs/31aa79387f27e730e33d871925e152e35e428031"),
)
_WEBVIEW_LICENSE = (
    ("LICENSE", "packaging/license_sources/f2c6-rust/webview2-rs/LICENSE",
     "0dcf41516e608bbcb6cdc5229feb7b86fe4a643b85e7df251133c93408fdac73",
     "https://api.github.com/repos/wravery/webview2-rs/git/blobs/de9a7e660e173136b351982a34f715af424ff812"),
)
# Key: crate name, version, Cargo.lock checksum, declared license expression.
# Value: repository, .cargo_vcs_info commit/path, copied material with source URL.
LICENSE_SUPPLEMENTS = {
    ("alloc-stdlib", "0.2.4", "0e76a019e91224d279006ff972f1e984179a6e9feb050adba6ce8274aef23195", "BSD-3-Clause"):
        ("https://github.com/dropbox/rust-alloc-no-stdlib", "ae42d22078b98549e987d2f03d12df7b984fde47", "alloc-stdlib", _ALLOC_LICENSE),
    ("defmt-parser", "1.0.0", "10d60334b3b2e7c9d91ef8150abfb6fa4c1c39ebbcf4a81c2e346aad939fee3e", "MIT OR Apache-2.0"):
        ("https://github.com/knurling-rs/defmt", "4a8cdb44891ed57b8ff5a023b6bec7137c48708f", "parser", _DEFMT_LICENSES),
    ("selectors", "0.36.1", "c5d9c0c92a92d33f08817311cf3f2c29a3538a8240e94a6a3c622ce652d7e00c", "MPL-2.0"):
        ("https://github.com/servo/stylo", "635e1a19d02960588a00e189bd4bd5bdb150ec3d", "selectors", _SELECTORS_LICENSE),
    ("unic-char-property", "0.9.0", "a8c57a407d9b6fa02b4795eb81c5b6652060a15a7903ea981f3d723e6c0be221", "MIT/Apache-2.0"):
        ("https://github.com/open-i18n/rust-unic", "5878605364af97a3358368a6eaef02104af2e016", None, _UNIC_LICENSES),
    ("unic-char-range", "0.9.0", "0398022d5f700414f6b899e10b8348231abf9173fa93144cbc1a43b9793c1fbc", "MIT/Apache-2.0"):
        ("https://github.com/open-i18n/rust-unic", "5878605364af97a3358368a6eaef02104af2e016", None, _UNIC_LICENSES),
    ("unic-common", "0.9.0", "80d7ff825a6a654ee85a63e80f92f054f904f21e7d12da4e22f9834a4aaa35bc", "MIT/Apache-2.0"):
        ("https://github.com/open-i18n/rust-unic", "5878605364af97a3358368a6eaef02104af2e016", None, _UNIC_LICENSES),
    ("unic-ucd-ident", "0.9.0", "e230a37c0381caa9219d67cf063aa3a375ffed5bf541a452db16e744bdab6987", "MIT/Apache-2.0"):
        ("https://github.com/open-i18n/rust-unic", "8a6ce83063d90b91ae2ce59eddb803edd393fca9", None, _UNIC_LICENSES),
    ("unic-ucd-version", "0.9.0", "96bd2f2237fe450fcd0a1d2f5f4e91711124f7857ba2e964247776ebeeb7b0c4", "MIT/Apache-2.0"):
        ("https://github.com/open-i18n/rust-unic", "5878605364af97a3358368a6eaef02104af2e016", None, _UNIC_LICENSES),
    ("webview2-com", "0.38.2", "7130243a7a5b33c54a444e54842e6a9e133de08b5ad7b5861cd8ed9a6a5bc96a", "MIT"):
        ("https://github.com/wravery/webview2-rs", "b74dc5e2b394044bea5191052868ce7a106c202c", "crates/webview2-com", _WEBVIEW_LICENSE),
    ("webview2-com-macros", "0.8.1", "67a921c1b6914c367b2b823cd4cde6f96beec77d30a939c8199bb377cf9b9b54", "MIT"):
        ("https://github.com/wravery/webview2-rs", "dffa41a8a46d3f5565eefbff2de57d38d399f158", "crates/callback-macros", _WEBVIEW_LICENSE),
    ("webview2-com-sys", "0.38.2", "381336cfffd772377d291702245447a5251a2ffa5bad679c99e61bc48bacbf9c", "MIT"):
        ("https://github.com/wravery/webview2-rs", "b74dc5e2b394044bea5191052868ce7a106c202c", "crates/bindings", _WEBVIEW_LICENSE),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(value: str) -> str:
    require(isinstance(value, str) and value != "", "empty package path")
    path = PurePosixPath(value)
    require(
        not path.is_absolute()
        and str(path) == value
        and ".." not in path.parts
        and "." not in path.parts
        and "\\" not in value
        and ":" not in value,
        f"unsafe package path: {value}",
    )
    require(
        not any(part.casefold() in FORBIDDEN_PATH_PARTS for part in path.parts),
        f"forbidden package path component: {value}",
    )
    require(path.suffix.casefold() not in FORBIDDEN_SUFFIXES, f"forbidden package type: {value}")
    return value


def _linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def regular_file(root: Path, name: str) -> Path:
    path = root / safe_name(name)
    require(path.is_file(), f"missing source file: {name}")
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    require(resolved.is_relative_to(resolved_root), f"source path escapes root: {name}")
    current = path
    while current != root:
        require(not _linked(current), f"linked source path: {name}")
        current = current.parent
    return path


def _require_unlinked_chain(root: Path, path: Path, label: str) -> None:
    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    require(resolved_path.is_relative_to(resolved_root), f"{label} escapes root")
    current = path
    while current != root:
        require(not _linked(current), f"linked {label}")
        current = current.parent


def _unique_names(names: list[str] | tuple[str, ...] | set[str]) -> None:
    canonical = [safe_name(name) for name in names]
    require(len(canonical) == len(set(canonical)), "duplicate package path")
    folded = [name.casefold() for name in canonical]
    require(len(folded) == len(set(folded)), "case-colliding package path")


def _json_load_exact(data: bytes, label: str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON in {label}: {error}") from error


def _scan_secrets(name: str, data: bytes, *, executable: bool = False) -> None:
    for pattern in SECRET_PATTERNS:
        require(pattern.search(data) is None, f"secret-like content in {name}")
    texts: list[str] = []
    if executable:
        texts.extend(item.decode("ascii", "ignore") for item in re.findall(rb"[ -~]{6,}", data))
        texts.extend(
            item.decode("utf-16le", "ignore")
            for item in re.findall(rb"(?:[ -~]\x00){6,}", data)
        )
    else:
        try:
            texts.append(data.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise ValueError(f"non-UTF-8 text payload: {name}") from error
    for text in texts:
        for pattern in SECRET_TEXT_PATTERNS:
            require(pattern.search(text) is None, f"secret-like content in {name}")
        for pattern in PERSONAL_PATH_PATTERNS:
            require(pattern.search(text) is None, f"personal/source path in {name}")
        if executable:
            for match in re.findall(r"[A-Za-z]:\\[^\x00\r\n]{4,260}", text):
                lowered = match.casefold()
                require(
                    lowered.startswith(ALLOWED_BINARY_PATH_PREFIXES),
                    f"undisclosed absolute path in executable: {match[:120]}",
                )


def _verify_pe_amd64(data: bytes) -> None:
    require(len(data) >= 0x40 and data[:2] == b"MZ", "executable is not PE")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    require(offset + 24 <= len(data), "truncated PE header")
    require(data[offset:offset + 4] == b"PE\0\0", "invalid PE signature")
    require(struct.unpack_from("<H", data, offset + 4)[0] == 0x8664, "PE is not AMD64")


def _verify_niki(data: bytes) -> None:
    require(_json_load_exact(data, "Niki fixture") == NIKI_FIXTURE, "unexpected Niki fixture")


def _source_audit(source_root: Path) -> dict[str, object]:
    script = source_root / "scripts/verify_f2c1_ci.py"
    require(script.is_file(), "source audit script missing")
    spec = importlib.util.spec_from_file_location("f2c1_source_audit", script)
    require(spec is not None and spec.loader is not None, "source audit cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(tuple(module.SIDECAR_FILES) == SIDECAR_FILES, "sidecar allowlist drift")
    report = module.audit(source_root)
    require(report.get("audit") == "PASS", "source audit did not pass")
    return report


def _lock_index(lock_path: Path) -> dict[tuple[str, str, str | None], dict[str, object]]:
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = lock.get("package")
    require(isinstance(packages, list), "Cargo.lock package list missing")
    require(len(packages) <= MAX_METADATA_PACKAGES, "Cargo.lock package count exceeds limit")
    result: dict[tuple[str, str, str | None], dict[str, object]] = {}
    for package in packages:
        require(isinstance(package, dict), "invalid package in Cargo.lock")
        name = package.get("name")
        version = package.get("version")
        source = package.get("source")
        require(
            isinstance(name, str) and PACKAGE_NAME.fullmatch(name) is not None
            and isinstance(version, str) and PACKAGE_VERSION.fullmatch(version) is not None
            and (source is None or (isinstance(source, str) and len(source) <= 512)),
            "invalid package identity in Cargo.lock",
        )
        key = (name, version, source)
        require(key not in result, f"duplicate package in Cargo.lock: {key[0]} {key[1]}")
        result[key] = package
    return result


def _resolved_package_ids(metadata: dict[str, object]) -> set[str]:
    resolve = metadata.get("resolve")
    require(isinstance(resolve, dict), "cargo metadata resolve graph missing")
    root_id = resolve.get("root")
    nodes = resolve.get("nodes")
    require(isinstance(root_id, str) and isinstance(nodes, list), "invalid cargo resolve graph")
    graph: dict[str, set[str]] = {}
    for node in nodes:
        require(isinstance(node, dict) and isinstance(node.get("id"), str), "invalid cargo node")
        deps = node.get("deps")
        require(isinstance(deps, list), "invalid cargo dependency list")
        graph[node["id"]] = {
            dep["pkg"] for dep in deps
            if isinstance(dep, dict) and isinstance(dep.get("pkg"), str)
        }
        require(len(graph[node["id"]]) == len(deps), "invalid cargo dependency edge")
    require(root_id in graph, "cargo metadata root node missing")
    reached: set[str] = set()
    pending = [root_id]
    while pending:
        current = pending.pop()
        if current not in reached:
            require(current in graph, "cargo dependency node missing")
            reached.add(current)
            pending.extend(graph[current] - reached)
    return reached


def _dependency_license_context(
    source_root: Path,
    package: dict[str, object],
    lock: dict[tuple[str, str, str | None], dict[str, object]],
) -> tuple[dict[str, object], dict[str, Path]]:
    name = package.get("name")
    version = package.get("version")
    source = package.get("source")
    manifest_path = package.get("manifest_path")
    license_expression = package.get("license")
    license_file = package.get("license_file")
    require(
        isinstance(name, str) and PACKAGE_NAME.fullmatch(name) is not None
        and isinstance(version, str) and PACKAGE_VERSION.fullmatch(version) is not None
        and (source is None or source == CRATES_IO_SOURCE)
        and isinstance(manifest_path, str) and manifest_path != ""
        and (license_file is None or (isinstance(license_file, str) and license_file != "")),
        "invalid cargo package provenance",
    )
    key = (name, version, source)
    require(key in lock, f"cargo metadata package is not locked: {name} {version}")
    if source is None:
        require(name == "shirushi-desktop", "unexpected unlocked path dependency")
    require(
        (isinstance(license_expression, str) and license_expression.strip() != "")
        or isinstance(license_file, str),
        f"dependency has no declared license: {name} {version}",
    )
    if isinstance(license_expression, str):
        license_expression = license_expression.strip()
        require(
            LICENSE_EXPRESSION.fullmatch(license_expression) is not None,
            f"dependency has malformed license expression: {name} {version}",
        )
        require(
            license_expression.casefold()
            not in {"unknown", "none", "proprietary", "unlicensed"},
            f"dependency has unknown license: {name} {version}",
        )
    package_root = Path(manifest_path).resolve(strict=True).parent
    require(package_root.is_dir() and not _linked(package_root), f"invalid package root: {name}")
    candidates: dict[str, Path] = {}
    if isinstance(license_file, str):
        declared = Path(license_file)
        if not declared.is_absolute():
            declared = package_root / declared
        require(declared.is_file() and not _linked(declared), f"invalid license-file: {name}")
        declared = declared.resolve(strict=True)
        require(declared.is_relative_to(package_root), f"license-file escapes package: {name}")
        _require_unlinked_chain(package_root, declared, f"license-file: {name}")
        candidates[declared.name] = declared
    for child in package_root.iterdir():
        if child.is_file() and LICENSE_NAMES.fullmatch(child.name):
            require(not _linked(child), f"linked license text: {name}")
            candidates.setdefault(child.name, child)
    locked = lock[key]
    lock_checksum = locked.get("checksum")
    require(
        source is None or (isinstance(lock_checksum, str) and HEX_SHA256.fullmatch(lock_checksum)),
        f"locked registry checksum missing or invalid: {name} {version}",
    )
    if not candidates and source == CRATES_IO_SOURCE:
        supplement = LICENSE_SUPPLEMENTS.get(
            (name, version, lock_checksum, license_expression)
        )
        if supplement is not None:
            expected_repo, expected_commit, expected_path, materials = supplement
            repository = package.get("repository")
            require(
                isinstance(repository, str) and repository.rstrip("/") == expected_repo,
                f"package repository provenance mismatch: {name} {version}",
            )
            vcs_path = package_root / ".cargo_vcs_info.json"
            require(vcs_path.is_file() and not _linked(vcs_path),
                    f"missing package VCS provenance: {name} {version}")
            vcs = _json_load_exact(vcs_path.read_bytes(), f"{name} VCS provenance")
            require(
                isinstance(vcs, dict) and isinstance(vcs.get("git"), dict)
                and vcs["git"].get("sha1") == expected_commit
                and vcs.get("path_in_vcs") == expected_path,
                f"package VCS provenance mismatch: {name} {version}",
            )
            for file_name, source_path, expected_hash, _source_url in materials:
                copied = regular_file(source_root, source_path)
                require(sha256(copied.read_bytes()) == expected_hash,
                        f"supplemental license hash mismatch: {name}/{file_name}")
                require(file_name not in candidates, "duplicate supplemental license name")
                candidates[file_name] = copied
    if name == "shirushi-desktop":
        project_license_path = source_root / "LICENSE"
        require(
            project_license_path.is_file() and not _linked(project_license_path),
            "project license missing or linked",
        )
        project_license = project_license_path.resolve(strict=True)
        require(project_license.is_relative_to(source_root.resolve(strict=True)), "project license escape")
        candidates.setdefault("LICENSE", project_license)
    return {
        "name": name,
        "version": version,
        "sourceId": source,
        "lockChecksum": lock_checksum,
        "licenseExpression": license_expression,
    }, candidates


def preflight_dependency_licenses(source_root: Path, metadata_path: Path) -> dict[str, object]:
    metadata = _json_load_exact(metadata_path.read_bytes(), "cargo metadata")
    require(isinstance(metadata, dict), "cargo metadata root is not an object")
    packages = metadata.get("packages")
    require(isinstance(packages, list), "cargo metadata packages missing")
    require(len(packages) <= MAX_METADATA_PACKAGES, "cargo metadata package count exceeds limit")
    by_id = {
        package["id"]: package
        for package in packages
        if isinstance(package, dict) and isinstance(package.get("id"), str)
    }
    require(len(by_id) == len(packages), "invalid or duplicate cargo package id")
    resolved = _resolved_package_ids(metadata)
    require(len(resolved) <= MAX_METADATA_PACKAGES, "resolved package count exceeds limit")
    lock = _lock_index(source_root / "desktop/Cargo.lock")
    missing: list[dict[str, object]] = []
    seen_missing: set[tuple[str, str, str | None]] = set()
    for package_id in sorted(resolved):
        require(package_id in by_id, "resolved cargo package missing metadata")
        identity, candidates = _dependency_license_context(source_root, by_id[package_id], lock)
        if not candidates:
            key = (identity["name"], identity["version"], identity["sourceId"])
            require(key not in seen_missing, "duplicate missing-license package identity")
            seen_missing.add(key)
            missing.append(identity)
    missing.sort(key=lambda item: (item["name"], item["version"], item["sourceId"] or ""))
    report = {
        "schemaVersion": 1,
        "diagnostic": "dependency-license-text-preflight",
        "audit": "FAIL" if missing else "PASS",
        "reason": "resolved dependencies lack native license/notice text" if missing else None,
        "target": TARGET_TRIPLE,
        "resolvedPackageCount": len(resolved),
        "missingLicenseTextCount": len(missing),
        "missingLicenseTexts": missing,
    }
    require(
        len(json.dumps(report, ensure_ascii=True, sort_keys=True).encode("ascii"))
        <= MAX_PREFLIGHT_OUTPUT_BYTES,
        "license preflight diagnostic exceeds output limit",
    )
    return report


def collect_dependency_licenses(
    source_root: Path, metadata_path: Path
) -> tuple[bytes, dict[str, bytes]]:
    metadata = _json_load_exact(metadata_path.read_bytes(), "cargo metadata")
    require(isinstance(metadata, dict), "cargo metadata root is not an object")
    packages = metadata.get("packages")
    require(isinstance(packages, list), "cargo metadata packages missing")
    by_id = {
        package["id"]: package
        for package in packages
        if isinstance(package, dict) and isinstance(package.get("id"), str)
    }
    require(len(by_id) == len(packages), "invalid or duplicate cargo package id")
    resolved = _resolved_package_ids(metadata)
    lock = _lock_index(source_root / "desktop/Cargo.lock")
    texts: dict[str, bytes] = {}
    inventory: list[dict[str, object]] = []
    for package_id in sorted(resolved):
        require(package_id in by_id, f"resolved cargo package missing metadata: {package_id}")
        identity, candidates = _dependency_license_context(source_root, by_id[package_id], lock)
        name = identity["name"]
        version = identity["version"]
        require(candidates, f"no license/notice text found: {name} {version}")
        materials: list[dict[str, str]] = []
        for file_name, path in sorted(candidates.items(), key=lambda item: item[0].casefold()):
            data = path.read_bytes()
            _scan_secrets(f"dependency {name}/{file_name}", data)
            digest = sha256(data)
            artifact_path = f"THIRD_PARTY_LICENSES/texts/{digest}.txt"
            if artifact_path in texts:
                require(texts[artifact_path] == data, "SHA-256 collision in license texts")
            else:
                texts[artifact_path] = data
            materials.append({
                "sourceFileName": file_name,
                "artifactPath": artifact_path,
                "sha256": digest,
            })
        inventory.append({
            **identity,
            "materials": materials,
        })
    inventory.sort(key=lambda item: (item["name"], item["version"], item["sourceId"] or ""))
    document = {
        "schemaVersion": 1,
        "target": TARGET_TRIPLE,
        "packageCount": len(inventory),
        "packages": inventory,
    }
    return _json_bytes(document), texts


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _file_entry(
    name: str, data: bytes, classification: str, source_kind: str, source_identifier: str,
    source_sha: str | None = None,
) -> dict[str, object]:
    return {
        "path": safe_name(name),
        "classification": classification,
        "size": len(data),
        "sha256": sha256(data),
        "source": {
            "kind": source_kind,
            "identifier": source_identifier,
            "sha256": source_sha or sha256(data),
        },
    }


def _write_new(root: Path, name: str, data: bytes) -> None:
    path = root / safe_name(name)
    require(not path.exists(), f"destination already exists: {name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not _linked(path.parent), f"linked destination parent: {name}")
    with path.open("xb") as handle:
        handle.write(data)


def create_package(
    source_root: Path,
    executable: Path,
    output: Path,
    metadata_path: Path,
    rust_library_notice: Path,
    runner_executable: Path,
    *,
    repository: str,
    event_name: str,
    tested_sha: str,
    head_sha: str,
    base_sha: str | None,
    source_audit: Callable[[Path], dict[str, object]] = _source_audit,
) -> dict[str, object]:
    require(source_root.is_dir() and not _linked(source_root), "invalid source root")
    require(executable.is_file() and not _linked(executable), "invalid executable input")
    require(runner_executable.is_file() and not _linked(runner_executable),
            "invalid runner executable input")
    require(metadata_path.is_file() and not _linked(metadata_path), "invalid cargo metadata input")
    require(rust_library_notice.is_file() and not _linked(rust_library_notice),
            "invalid Rust library notice input")
    require(output.parent.is_dir() and not _linked(output.parent), "invalid output parent")
    source_root = source_root.resolve(strict=True)
    executable = executable.resolve(strict=True)
    runner_executable = runner_executable.resolve(strict=True)
    metadata_path = metadata_path.resolve(strict=True)
    rust_library_notice = rust_library_notice.resolve(strict=True)
    output = output.resolve(strict=False)
    require(not output.exists(), "output directory already exists")
    require(not output.is_relative_to(source_root), "output directory must be outside source tree")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is not None,
            "invalid repository provenance")
    require(event_name in {"pull_request", "workflow_dispatch"}, "unexpected workflow event")
    require(GIT_SHA.fullmatch(tested_sha) is not None, "invalid tested SHA")
    require(GIT_SHA.fullmatch(head_sha) is not None, "invalid head SHA")
    require(base_sha is None or GIT_SHA.fullmatch(base_sha) is not None, "invalid base SHA")
    source_report = source_audit(source_root)
    executable_data = executable.read_bytes()
    _verify_pe_amd64(executable_data)
    _scan_secrets("shirushi-desktop.exe", executable_data, executable=True)
    runner_data = runner_executable.read_bytes()
    _verify_pe_amd64(runner_data)
    _scan_secrets("shirushi-canary-runner.exe", runner_data, executable=True)
    dependency_manifest, dependency_texts = collect_dependency_licenses(source_root, metadata_path)
    payloads: dict[str, tuple[bytes, str, str, str]] = {
        "shirushi-desktop.exe": (
            executable_data, "debug-canary-executable", "hosted-cargo-build",
            "desktop/Cargo.toml#manual-canary",
        ),
        "shirushi-canary-runner.exe": (
            runner_data, "development-canary-runner", "hosted-cargo-build",
            "desktop/Cargo.toml#manual-canary-runner",
        ),
        "THIRD_PARTY_LICENSES/manifest.json": (
            dependency_manifest, "dependency-license-inventory", "generated",
            "locked cargo metadata for x86_64-pc-windows-msvc",
        ),
        RUST_LIBRARY_NOTICE: (
            rust_library_notice.read_bytes(), "rust-standard-library-notice",
            "pinned-rust-toolchain-file",
            "rustc-1.97.1/share/doc/rust/COPYRIGHT-library.html",
        ),
    }
    for destination, (source_name, classification) in SOURCE_COPIES.items():
        data = regular_file(source_root, source_name).read_bytes()
        payloads[destination] = (data, classification, "repository-file", source_name)
    for name, data in dependency_texts.items():
        payloads[name] = (data, "dependency-license-text", "cargo-package-license", name)
    _unique_names(set(payloads) | CONTROL_FILES)
    _verify_niki(payloads["demo-profile/Shirushi/personal-mark/personal-mark-v2.json"][0])
    for name, (data, classification, _, _) in payloads.items():
        _scan_secrets(name, data, executable=name in {
            "shirushi-desktop.exe", "shirushi-canary-runner.exe",
        })
        require(classification != "", f"missing classification: {name}")
    output.mkdir(parents=False)
    entries = []
    for name in sorted(payloads):
        data, classification, source_kind, source_identifier = payloads[name]
        _write_new(output, name, data)
        entries.append(_file_entry(
            name, data, classification, source_kind, source_identifier, sha256(data)
        ))
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "artifactType": ARTIFACT_TYPE,
        "artifactStatus": "unsigned debug-only non-installer non-release",
        "target": TARGET_TRIPLE,
        "sourceProvenance": {
            "repository": repository,
            "event": event_name,
            "testedSha": tested_sha,
            "headSha": head_sha,
            "baseSha": base_sha,
        },
        "sourceAudit": {
            "audit": source_report.get("audit"),
            "webAssetCount": source_report.get("webAssetCount"),
            "sidecarResourceCount": source_report.get("sidecarResourceCount"),
        },
        "buildPathDisclosure": {
            "reason": "debug PE/PDB metadata may retain public GitHub-hosted runner paths",
            "allowedPrefixes": list(ALLOWED_BINARY_PATH_PREFIXES),
        },
        "checksumPolicy": {
            "algorithm": "SHA-256",
            "sha256SumsCovers": "all files except SHA256SUMS",
            "manifestSelfHashExcluded": True,
        },
        "fileCount": len(entries),
        "files": entries,
    }
    manifest_data = _json_bytes(manifest)
    _write_new(output, "manifest.json", manifest_data)
    sums = [f"{entry['sha256']}  {entry['path']}" for entry in entries]
    sums.append(f"{sha256(manifest_data)}  manifest.json")
    sums_data = ("\n".join(sorted(sums, key=str.casefold)) + "\n").encode("utf-8")
    _write_new(output, "SHA256SUMS", sums_data)
    result = audit_package(output)
    result["packageDir"] = str(output)
    return result


def _inventory_paths(document: object) -> set[str]:
    require(isinstance(document, dict), "dependency inventory is not an object")
    require(set(document) == {"schemaVersion", "target", "packageCount", "packages"},
            "bad dependency inventory fields")
    require(document.get("schemaVersion") == 1, "bad dependency inventory schema")
    require(document.get("target") == TARGET_TRIPLE, "bad dependency inventory target")
    packages = document.get("packages")
    require(isinstance(packages, list) and document.get("packageCount") == len(packages),
            "bad dependency inventory count")
    require(packages, "empty dependency inventory")
    paths: set[str] = set()
    identities: set[tuple[str, str, str | None]] = set()
    local_roots = 0
    for package in packages:
        require(isinstance(package, dict), "bad dependency package entry")
        require(set(package) == {
            "name", "version", "sourceId", "lockChecksum", "licenseExpression", "materials",
        }, "bad dependency package fields")
        name, version, source = package.get("name"), package.get("version"), package.get("sourceId")
        identity = (name, version, source)
        require(isinstance(name, str) and isinstance(version, str)
                and (source is None or isinstance(source, str)), "bad dependency identity")
        require(identity not in identities, "duplicate dependency identity")
        identities.add(identity)
        if source is None:
            local_roots += 1
            require(name == "shirushi-desktop" and package.get("lockChecksum") is None,
                    "bad local dependency provenance")
        else:
            require(source.startswith("registry+")
                    and isinstance(package.get("lockChecksum"), str)
                    and HEX_SHA256.fullmatch(package["lockChecksum"]),
                    "bad registry dependency provenance")
        require(package.get("licenseExpression") or package.get("materials"),
                "dependency attribution missing")
        materials = package.get("materials")
        require(isinstance(materials, list) and materials, "dependency license material missing")
        for material in materials:
            require(isinstance(material, dict), "bad dependency material")
            require(set(material) == {"sourceFileName", "artifactPath", "sha256"}
                    and isinstance(material.get("sourceFileName"), str)
                    and material["sourceFileName"] not in {"", ".", ".."}
                    and "/" not in material["sourceFileName"]
                    and "\\" not in material["sourceFileName"],
                    "bad dependency material fields")
            path = material.get("artifactPath")
            digest = material.get("sha256")
            require(isinstance(path, str) and DEPENDENCY_TEXT.fullmatch(path),
                    "bad dependency text path")
            require(isinstance(digest, str) and HEX_SHA256.fullmatch(digest),
                    "bad dependency text hash")
            require(path == f"THIRD_PARTY_LICENSES/texts/{digest}.txt",
                    "dependency text path/hash mismatch")
            paths.add(path)
    require(local_roots == 1, "dependency inventory must contain one local root")
    return paths


def audit_package(package_dir: Path) -> dict[str, object]:
    require(package_dir.is_dir() and not _linked(package_dir), "invalid package directory")
    package_dir = package_dir.resolve(strict=True)
    actual_paths: list[str] = []
    for path in package_dir.rglob("*"):
        relative = path.relative_to(package_dir).as_posix()
        safe_name(relative)
        require(not _linked(path), f"linked package path: {relative}")
        if path.is_file():
            actual_paths.append(relative)
        else:
            require(path.is_dir(), f"non-file package entry: {relative}")
    _unique_names(actual_paths)
    require(set(CONTROL_FILES).issubset(actual_paths), "package controls missing")
    manifest_data = regular_file(package_dir, "manifest.json").read_bytes()
    _scan_secrets("manifest.json", manifest_data)
    manifest = _json_load_exact(manifest_data, "manifest.json")
    require(isinstance(manifest, dict), "manifest is not an object")
    require(set(manifest) == {
        "schemaVersion", "artifactType", "artifactStatus", "target", "sourceProvenance",
        "sourceAudit", "buildPathDisclosure", "checksumPolicy", "fileCount", "files",
    }, "bad manifest fields")
    require(manifest.get("schemaVersion") == SCHEMA_VERSION, "bad manifest schema")
    require(manifest.get("artifactType") == ARTIFACT_TYPE, "bad artifact type")
    require(manifest.get("artifactStatus") == "unsigned debug-only non-installer non-release",
            "bad artifact status")
    require(manifest.get("target") == TARGET_TRIPLE, "bad target")
    entries = manifest.get("files")
    require(isinstance(entries, list) and manifest.get("fileCount") == len(entries),
            "bad manifest file count")
    names = [entry.get("path") for entry in entries if isinstance(entry, dict)]
    require(len(names) == len(entries) and all(isinstance(name, str) for name in names),
            "bad manifest file entry")
    _unique_names(names)
    dependency_data = regular_file(package_dir, "THIRD_PARTY_LICENSES/manifest.json").read_bytes()
    dependency_paths = _inventory_paths(_json_load_exact(dependency_data, "dependency inventory"))
    for name in dependency_paths:
        require(sha256(regular_file(package_dir, name).read_bytes()) == PurePosixPath(name).stem,
                f"dependency text content-address mismatch: {name}")
    permitted = CORE_FILES | GENERATED_FILES | dependency_paths
    require(set(names) == permitted, "manifest payload set mismatch")
    require(set(actual_paths) == permitted | CONTROL_FILES, "package file set mismatch")
    for entry in entries:
        require(set(entry) == {"path", "classification", "size", "sha256", "source"},
                f"bad manifest fields: {entry.get('path')}")
        name = entry["path"]
        data = regular_file(package_dir, name).read_bytes()
        require(type(entry["size"]) is int and entry["size"] == len(data), f"size mismatch: {name}")
        require(isinstance(entry["sha256"], str) and HEX_SHA256.fullmatch(entry["sha256"])
                and entry["sha256"] == sha256(data), f"hash mismatch: {name}")
        require(isinstance(entry["classification"], str) and entry["classification"],
                f"classification missing: {name}")
        source = entry["source"]
        require(isinstance(source, dict) and set(source) == {"kind", "identifier", "sha256"}
                and all(isinstance(source[key], str) and source[key] for key in ("kind", "identifier"))
                and isinstance(source["sha256"], str) and HEX_SHA256.fullmatch(source["sha256"])
                and source["sha256"] == entry["sha256"],
                f"bad source provenance: {name}")
        _scan_secrets(name, data, executable=name in {
            "shirushi-desktop.exe", "shirushi-canary-runner.exe",
        })
    exe_files = [name for name in actual_paths if name.casefold().endswith(".exe")]
    require(set(exe_files) == {"shirushi-desktop.exe", "shirushi-canary-runner.exe"}
            and len(exe_files) == 2, "unexpected executable set")
    _verify_pe_amd64(regular_file(package_dir, "shirushi-desktop.exe").read_bytes())
    _verify_pe_amd64(regular_file(package_dir, "shirushi-canary-runner.exe").read_bytes())
    _verify_niki(regular_file(
        package_dir, "demo-profile/Shirushi/personal-mark/personal-mark-v2.json"
    ).read_bytes())
    sums_data = regular_file(package_dir, "SHA256SUMS").read_bytes()
    _scan_secrets("SHA256SUMS", sums_data)
    try:
        sum_lines = sums_data.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("SHA256SUMS is not ASCII") from error
    sums: dict[str, str] = {}
    for line in sum_lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(match is not None, "malformed SHA256SUMS line")
        digest, name = match.groups()
        safe_name(name)
        require(name not in sums, "duplicate SHA256SUMS path")
        sums[name] = digest
    require(set(sums) == permitted | {"manifest.json"}, "SHA256SUMS coverage mismatch")
    for name, digest in sums.items():
        require(sha256(regular_file(package_dir, name).read_bytes()) == digest,
                f"SHA256SUMS mismatch: {name}")
    provenance = manifest.get("sourceProvenance")
    require(isinstance(provenance, dict)
            and set(provenance) == {"repository", "event", "testedSha", "headSha", "baseSha"}
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", provenance.get("repository", ""))
            and provenance.get("event") in {"pull_request", "workflow_dispatch"}
            and GIT_SHA.fullmatch(provenance.get("testedSha", ""))
            and GIT_SHA.fullmatch(provenance.get("headSha", ""))
            and (provenance.get("baseSha") is None or GIT_SHA.fullmatch(provenance["baseSha"])),
            "bad source provenance")
    source_audit = manifest.get("sourceAudit")
    require(isinstance(source_audit, dict)
            and set(source_audit) == {"audit", "webAssetCount", "sidecarResourceCount"}
            and source_audit.get("audit") == "PASS"
            and source_audit.get("webAssetCount") == 29
            and source_audit.get("sidecarResourceCount") == 10,
            "bad source audit evidence")
    disclosure = manifest.get("buildPathDisclosure")
    require(disclosure == {
        "reason": "debug PE/PDB metadata may retain public GitHub-hosted runner paths",
        "allowedPrefixes": list(ALLOWED_BINARY_PATH_PREFIXES),
    }, "bad build-path disclosure")
    policy = manifest.get("checksumPolicy")
    require(policy == {
        "algorithm": "SHA-256",
        "sha256SumsCovers": "all files except SHA256SUMS",
        "manifestSelfHashExcluded": True,
    }, "bad checksum policy")
    return {
        "audit": "PASS",
        "artifactType": ARTIFACT_TYPE,
        "fileCount": len(actual_paths),
        "manifestSha256": sha256(manifest_data),
        "sha256SumsSha256": sha256(sums_data),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--source-root", type=Path, required=True)
    create.add_argument("--executable", type=Path, required=True)
    create.add_argument("--runner-executable", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--cargo-metadata", type=Path, required=True)
    create.add_argument("--rust-library-notice", type=Path, required=True)
    create.add_argument("--repository", required=True)
    create.add_argument("--event-name", required=True)
    create.add_argument("--tested-sha", required=True)
    create.add_argument("--head-sha", required=True)
    create.add_argument("--base-sha")
    preflight = subparsers.add_parser("preflight-licenses")
    preflight.add_argument("--source-root", type=Path, required=True)
    preflight.add_argument("--cargo-metadata", type=Path, required=True)
    audit = subparsers.add_parser("audit")
    audit.add_argument("--package-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create_package(
                args.source_root, args.executable, args.output, args.cargo_metadata,
                args.rust_library_notice, args.runner_executable,
                repository=args.repository, event_name=args.event_name,
                tested_sha=args.tested_sha, head_sha=args.head_sha, base_sha=args.base_sha,
            )
        elif args.command == "audit":
            result = audit_package(args.package_dir)
        else:
            result = preflight_dependency_licenses(args.source_root, args.cargo_metadata)
    except (ValueError, OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        if args.command == "preflight-licenses":
            if isinstance(error, tomllib.TOMLDecodeError):
                reason = "invalid Cargo.lock"
            elif isinstance(error, OSError):
                reason = "dependency provenance input could not be read"
            else:
                reason = str(error)
            if len(reason) > 512 or any(pattern.search(reason) for pattern in PERSONAL_PATH_PATTERNS):
                reason = "malformed dependency metadata or lock provenance"
            print(json.dumps({
                "schemaVersion": 1,
                "diagnostic": "dependency-license-text-preflight",
                "audit": "ERROR",
                "reason": reason,
                "target": TARGET_TRIPLE,
                "resolvedPackageCount": None,
                "missingLicenseTextCount": 0,
                "missingLicenseTexts": [],
            }, ensure_ascii=True, sort_keys=True))
            return 2
        print(json.dumps({"audit": "FAIL", "reason": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 1 if args.command == "preflight-licenses" and result["audit"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
