"""Assemble and inventory the allowlisted Shirushi one-dir release candidate."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import mimetypes
from pathlib import Path
import re
import shutil


FORBIDDEN_MODEL_NAMES = {
    "trustmark_P.yaml",
    "decoder_P.ckpt",
    "encoder_P.ckpt",
}
FORBIDDEN_MODEL_SHA256 = {
    "43f37103f92efa8bd6b1c5902bb537cc12a981dc699fca19d9bb7de8c62d03d9",
    "f5f1d570c889c5908c6e4a28c07dc90cf33990ebaf24037254ebe0b8849b1bdc",
    "659d427f72d16eea4e8fbda175ae72e3a78830dc6cf44b63642a5a4b28a2b4e2",
}
FORBIDDEN_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".ckpt",
    ".onnx",
    ".pt",
    ".pth",
    ".safetensors",
}
FORBIDDEN_DIRECTORY_NAMES = {
    ".git",
    ".venv",
    ".venv-py312",
    "input",
    "output",
    "testdata",
    "tests",
    "logs",
    "cache",
    "temp",
    "tmp",
}

RELEASE_README_LINK_TARGETS = {
    "DISCLAIMER.md": "docs/DISCLAIMER.md",
    "PRIVACY.md": "docs/PRIVACY.md",
    "LICENSE": "LICENSES/Shirushi-MIT.txt",
    "THIRD_PARTY_NOTICES.md": "LICENSES/THIRD_PARTY_NOTICES.md",
}
GENERATED_README_NOTICE = (
    "このファイルはREADME.mdから自動生成されています。直接編集しないでください。"
)

RUNTIME_PYTHON_DISTRIBUTIONS = {
    "aiohappyeyeballs": "2.7.1",
    "aiohttp": "3.14.3",
    "aiosignal": "1.4.0",
    "antlr4-python3-runtime": "4.9.3",
    "attrs": "26.1.0",
    "cffi": "2.1.1",
    "colorama": "0.4.6",
    "cryptography": "50.0.1",
    "einops": "0.8.2",
    "filelock": "3.32.5",
    "frozenlist": "1.8.0",
    "fsspec": "2026.7.0",
    "idna": "3.19",
    "Jinja2": "3.1.6",
    "lightning": "2.6.5",
    "lightning-utilities": "0.15.3",
    "MarkupSafe": "3.0.3",
    "mpmath": "1.3.0",
    "multidict": "6.7.1",
    "networkx": "3.6.1",
    "numpy": "1.26.4",
    "omegaconf": "2.3.1",
    "packaging": "26.3",
    "pillow": "12.3.0",
    "propcache": "0.5.2",
    "pycparser": "3.0",
    "pytorch-lightning": "2.6.5",
    "PyYAML": "6.0.3",
    "setuptools": "84.0.0",
    "six": "1.17.0",
    "sympy": "1.14.0",
    "torch": "2.14.0",
    "torchmetrics": "1.9.0",
    "torchvision": "0.29.0",
    "tqdm": "4.70.0",
    "trustmark": "0.9.0",
    "typing_extensions": "4.16.0",
    "yarl": "1.24.5",
}

LICENSE_DIRECTORY_NAMES = {
    "cryptography": "cryptography",
    "cffi": "cffi",
    "numpy": "NumPy",
    "pillow": "Pillow",
    "pycparser": "pycparser",
    "torch": "PyTorch",
    "torchvision": "torchvision",
    "trustmark": "TrustMark",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().lower()


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _license_files(distribution: importlib.metadata.Distribution) -> list[Path]:
    selected: list[Path] = []
    for item in distribution.files or []:
        relative = Path(str(item))
        lowered = relative.name.lower()
        in_metadata = bool(relative.parts and relative.parts[0].endswith(".dist-info"))
        if not in_metadata:
            continue
        if (
            lowered.startswith(("license", "licence", "copying", "notice", "authors", "copyright"))
            or "licenses" in {part.lower() for part in relative.parts}
            or "sboms" in {part.lower() for part in relative.parts}
        ):
            selected.append(relative)
    return sorted(set(selected))


def assemble_licenses(project_root: Path, payload_root: Path) -> None:
    """Copy upstream license bytes for every audited runtime component."""

    license_root = payload_root / "LICENSES"
    static_root = project_root / "packaging" / "license_sources"
    if not static_root.is_dir():
        raise FileNotFoundError(static_root)
    shutil.copytree(static_root, license_root, dirs_exist_ok=True)

    components: list[dict[str, object]] = []
    for name, expected_version in RUNTIME_PYTHON_DISTRIBUTIONS.items():
        distribution = importlib.metadata.distribution(name)
        accepted_versions = {expected_version}
        if name in {"torch", "torchvision"}:
            accepted_versions.add(f"{expected_version}+cpu")
        if distribution.version not in accepted_versions:
            raise RuntimeError(
                f"runtime license version mismatch for {name}: "
                f"expected {expected_version}, found {distribution.version}"
            )
        directory_name = LICENSE_DIRECTORY_NAMES.get(
            name.lower(), f"Python-Packages/{name}-{expected_version}"
        )
        destination = license_root / directory_name
        copied: list[str] = []
        for relative in _license_files(distribution):
            source = Path(distribution.locate_file(relative))
            if not source.is_file():
                raise FileNotFoundError(source)
            metadata_root = relative.parts[0]
            target_relative = Path(*relative.parts[1:]) if relative.parts[0] == metadata_root else relative
            copy_file(source, destination / target_relative)
            copied.append(target_relative.as_posix())

        if name == "antlr4-python3-runtime":
            copy_file(static_root / "ANTLR" / "LICENSE.txt", destination / "LICENSE.txt")
            copied.append("LICENSE.txt")
        if not copied:
            raise RuntimeError(f"no upstream license material found for {name}")
        components.append(
            {
                "component": name,
                "version": expected_version,
                "licenseExpression": distribution.metadata.get("License-Expression")
                or distribution.metadata.get("License")
                or "See included upstream license",
                "includedFiles": sorted(set(copied)),
            }
        )

    # The bundled OpenSSL 3 DLLs come from cryptography; retain the exact
    # Apache-2.0 text shipped by that wheel under an explicit component folder.
    copy_file(
        license_root / "cryptography" / "licenses" / "LICENSE.APACHE",
        license_root / "OpenSSL" / "LICENSE-APACHE-2.0.txt",
    )
    microsoft_notice = license_root / "Microsoft-Runtime" / "NOTICE.txt"
    microsoft_notice.parent.mkdir(parents=True, exist_ok=True)
    microsoft_notice.write_text(
        "Microsoft runtime files included in runtime/ are unmodified files "
        "collected by PyInstaller from the official CPython 3.12.10 Windows "
        "installation. Redistribution is subject to the applicable Microsoft "
        "Software License Terms.\n\n"
        "References:\n"
        "https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files\n"
        "https://learn.microsoft.com/cpp/windows/universal-crt-deployment\n",
        encoding="utf-8",
    )
    component_manifest = license_root / "PYTHON_COMPONENTS.json"
    component_manifest.write_text(
        json.dumps(
            {"schemaVersion": "1.0", "components": components},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    component_markdown = [
        "# Bundled Python component licenses",
        "",
        "This inventory is generated from the exact Python distributions used to build this release candidate.",
        "Upstream license and notice files are stored in the listed component directories.",
        "",
        "| Component | Version | License metadata |",
        "| --- | --- | --- |",
    ]
    for item in components:
        expression = str(item["licenseExpression"]).replace("|", "\\|").replace("\n", " ")
        component_markdown.append(
            f"| `{item['component']}` | `{item['version']}` | {expression} |"
        )
    (license_root / "PYTHON_COMPONENTS.md").write_text(
        "\n".join(component_markdown) + "\n", encoding="utf-8"
    )


def _plain_inline(text: str) -> str:
    def replace_image(match: re.Match[str]) -> str:
        alt, target = match.groups()
        return f"{alt}（画像: {target}）" if alt else f"画像: {target}"

    def replace_link(match: re.Match[str]) -> str:
        label, target = match.groups()
        release_target = RELEASE_README_LINK_TARGETS.get(target, target)
        return f"{label}（{release_target}）"

    text = re.sub(r"!\[([^]]*)\]\(([^)]+)\)", replace_image, text)
    text = re.sub(r"\[([^]]+)\]\(([^)]+)\)", replace_link, text)
    text = re.sub(r"<((?:https?|mailto):[^>]+)>", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def markdown_to_plain_text(markdown: str) -> str:
    """Render release README Markdown as deterministic, readable plain text."""

    output = [GENERATED_README_NOTICE, ""]
    in_code_block = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if re.match(r"^\s*```", line):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            output.append(f"    {line}" if line else "")
            continue

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            level = len(heading.group(1))
            title = _plain_inline(heading.group(2))
            if level == 1:
                output.extend((title, "=" * max(8, len(title))))
            elif level == 2:
                output.extend((title, "-" * max(8, len(title))))
            else:
                output.append(f"■ {title}")
            continue

        if re.match(r"^\s*\|?(?:\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$", line):
            continue
        if line.strip().startswith("|") and line.strip().endswith("|"):
            cells = [
                _plain_inline(cell.strip())
                for cell in line.strip().strip("|").split("|")
            ]
            output.append("  |  ".join(cells))
            continue

        bullet = re.match(r"^(\s*)[-*+]\s+(.+)$", line)
        if bullet:
            output.append(f"{bullet.group(1)}・{_plain_inline(bullet.group(2))}")
            continue
        quote = re.match(r"^\s*>\s?(.*)$", line)
        if quote:
            output.append(f"引用: {_plain_inline(quote.group(1))}")
            continue
        output.append(_plain_inline(line))

    while output and output[-1] == "":
        output.pop()
    return "\n".join(output) + "\n"


RELEASE_MARKDOWN_LINKS = {
    "README.md": {
        "docs/technical-overview.md": "technical-overview.md",
        "docs/validation-report.md": "validation-report.md",
        "LICENSE": "../LICENSES/Shirushi-MIT.txt",
        "THIRD_PARTY_NOTICES.md": "../LICENSES/THIRD_PARTY_NOTICES.md",
    },
    "technical-overview.md": {
        "../README.md": "README.md",
        "../DISCLAIMER.md": "DISCLAIMER.md",
        "../PRIVACY.md": "PRIVACY.md",
        "../THIRD_PARTY_NOTICES.md": "../LICENSES/THIRD_PARTY_NOTICES.md",
    },
    "DISCLAIMER.md": {
        "LICENSE": "../LICENSES/Shirushi-MIT.txt",
        "THIRD_PARTY_NOTICES.md": "../LICENSES/THIRD_PARTY_NOTICES.md",
    },
    "PRIVACY.md": {
        "LICENSE": "../LICENSES/Shirushi-MIT.txt",
        "THIRD_PARTY_NOTICES.md": "../LICENSES/THIRD_PARTY_NOTICES.md",
    },
}


def release_markdown(markdown: str, source_name: str) -> str:
    """Change only allowlisted inline link destinations in public documents."""
    targets = RELEASE_MARKDOWN_LINKS.get(source_name, {})
    # Keep code samples and inline code verbatim, including link-like text.
    pattern = r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)|(!?\[[^\]\n]*\]\()([^\s)]+)(\))"

    def replace(match: re.Match[str]) -> str:
        if match.group(1) or match.group(2).startswith("!"):
            return match.group(0)
        target, separator, fragment = match.group(3).partition("#")
        return match.group(2) + targets.get(target, target) + separator + fragment + match.group(4)

    return re.sub(pattern, replace, markdown)


def write_release_markdown(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Decode bytes directly to preserve source line endings as well as prose.
    original = source.read_bytes().decode("utf-8")
    destination.write_bytes(release_markdown(original, source.name).encode("utf-8"))


def write_release_readmes(source: Path, payload_root: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    markdown = source.read_text(encoding="utf-8")
    write_release_markdown(source, payload_root / "docs" / "README.md")
    plain_path = payload_root / "README.txt"
    plain_path.parent.mkdir(parents=True, exist_ok=True)
    plain_path.write_text(
        markdown_to_plain_text(markdown), encoding="utf-8", newline="\r\n"
    )
    stale_root_markdown = payload_root / "README.md"
    if stale_root_markdown.is_file():
        stale_root_markdown.unlink()


def assemble(project_root: Path, payload_root: Path) -> None:
    required = {
        project_root / "docs" / "technical-overview.md": (
            payload_root / "docs" / "technical-overview.md"
        ),
        project_root / "docs" / "validation-report.md": (
            payload_root / "docs" / "validation-report.md"
        ),
        project_root / "DISCLAIMER.md": payload_root / "docs" / "DISCLAIMER.md",
        project_root / "PRIVACY.md": payload_root / "docs" / "PRIVACY.md",
        project_root / "LICENSE": payload_root / "LICENSES" / "Shirushi-MIT.txt",
        project_root / "THIRD_PARTY_NOTICES.md": (
            payload_root / "LICENSES" / "THIRD_PARTY_NOTICES.md"
        ),
    }
    for source, destination in required.items():
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.suffix == ".md":
            write_release_markdown(source, destination)
        else:
            copy_file(source, destination)
    write_release_readmes(project_root / "README.md", payload_root)
    assemble_licenses(project_root, payload_root)


def classify_component(relative: Path) -> str:
    path = relative.as_posix()
    name = relative.name.lower()
    if path.startswith("LICENSES/"):
        return "licenses"
    if path.startswith("docs/") or name == "readme.txt":
        return "documentation"
    if path == "Shirushi.exe":
        return "Shirushi application / PyInstaller bootloader"
    if name == "c2patool.exe":
        return "c2patool 0.26.60"
    mappings = (
        ("runtime/torchvision", "torchvision 0.29.0"),
        ("runtime/torch", "PyTorch 2.14.0"),
        ("runtime/numpy", "NumPy 1.26.4"),
        ("runtime/PIL", "Pillow 12.3.0"),
        ("runtime/cryptography", "cryptography 50.0.1"),
        ("runtime/_tcl_data", "Tcl 8.6"),
        ("runtime/_tk_data", "Tk 8.6"),
    )
    for prefix, component in mappings:
        if path.startswith(prefix):
            return component
    if path.startswith("runtime/"):
        return "Python runtime or bundled dependency"
    return "Shirushi application"


def classify_file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    fixed = {
        ".dll": "Windows DLL",
        ".exe": "Windows executable",
        ".pyd": "Python extension module",
        ".pyc": "Python bytecode",
        ".json": "JSON",
        ".md": "Markdown",
        ".txt": "text",
        ".ico": "Windows icon",
        ".png": "PNG image",
        ".zip": "ZIP archive",
    }
    return fixed.get(suffix, mimetypes.guess_type(path.name)[0] or "binary/data")


def inventory(payload_root: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(item for item in payload_root.rglob("*") if item.is_file()):
        records.append(
            {
                "path": path.relative_to(payload_root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
                "fileType": classify_file_type(path),
                "component": classify_component(path.relative_to(payload_root)),
            }
        )
    return records


def validate_payload(payload_root: Path, records: list[dict[str, object]]) -> None:
    required = {
        "Shirushi.exe",
        "README.txt",
        "docs/README.md",
        "docs/technical-overview.md",
        "docs/validation-report.md",
        "docs/DISCLAIMER.md",
        "docs/PRIVACY.md",
        "LICENSES/Shirushi-MIT.txt",
        "LICENSES/THIRD_PARTY_NOTICES.md",
        "LICENSES/PYTHON_COMPONENTS.json",
        "LICENSES/PYTHON_COMPONENTS.md",
        "LICENSES/c2patool/LICENSE-MIT",
        "LICENSES/c2patool/LICENSE-APACHE",
        "LICENSES/c2patool/c2patool-v0.26.60-x86_64-pc-windows-msvc-sbom.json",
        "LICENSES/Python/LICENSE.txt",
        "LICENSES/Tcl-Tk/Tcl-license.terms",
        "LICENSES/Tcl-Tk/Tk-license.terms",
        "LICENSES/PyInstaller/COPYING.txt",
        "LICENSES/TrustMark/licenses/LICENSE",
        "LICENSES/PyTorch/licenses/LICENSE",
        "LICENSES/torchvision/LICENSE",
        "LICENSES/NumPy/LICENSE.txt",
        "LICENSES/Pillow/licenses/LICENSE",
        "LICENSES/cryptography/licenses/LICENSE",
        "LICENSES/cffi/licenses/LICENSE",
        "LICENSES/pycparser/licenses/LICENSE",
        "runtime/c2patool.exe",
        "runtime/config/trustmark-models-v0.9.0.json",
        "runtime/config/verifier-settings.json",
        "runtime/gui/app_icon.png",
        "runtime/gui/app_icon.ico",
    }
    present = {str(record["path"]) for record in records}
    missing = sorted(required - present)
    if missing:
        raise RuntimeError(f"required payload files missing: {missing}")

    violations: list[str] = []
    for record in records:
        relative = Path(str(record["path"]))
        if relative.name in FORBIDDEN_MODEL_NAMES:
            violations.append(f"forbidden TrustMark model name: {relative.as_posix()}")
        if str(record["sha256"]).lower() in FORBIDDEN_MODEL_SHA256:
            violations.append(f"forbidden TrustMark model SHA: {relative.as_posix()}")
        if relative.suffix.lower() in FORBIDDEN_SUFFIXES:
            violations.append(f"forbidden private/model suffix: {relative.as_posix()}")
        # Dependency packages can legitimately contain modules named ``tests`` or
        # ``cache``. Reject project-artifact directories at the payload root and
        # repository metadata at any depth.
        root_name = relative.parts[0].lower()
        if root_name in FORBIDDEN_DIRECTORY_NAMES or ".git" in {
            part.lower() for part in relative.parts
        }:
            violations.append(f"forbidden directory: {relative.as_posix()}")
    if violations:
        raise RuntimeError("; ".join(violations))


def write_inventory(
    records: list[dict[str, object]], json_path: Path, csv_path: Path
) -> None:
    total_size = sum(int(record["size"]) for record in records)
    document = {
        "schemaVersion": "1.0",
        "fileCount": len(records),
        "totalSize": total_size,
        "files": records,
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["path", "size", "sha256", "fileType", "component"],
        )
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--payload-root", type=Path, required=True)
    parser.add_argument("--inventory-json", type=Path, required=True)
    parser.add_argument("--inventory-csv", type=Path, required=True)
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    payload_root = args.payload_root.resolve()
    if not (payload_root / "Shirushi.exe").is_file():
        raise FileNotFoundError(payload_root / "Shirushi.exe")
    assemble(project_root, payload_root)
    records = inventory(payload_root)
    validate_payload(payload_root, records)
    write_inventory(records, args.inventory_json.resolve(), args.inventory_csv.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
