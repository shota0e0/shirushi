"""Build-only standard-pip offline staging, never a manual runtime installer.

Only the existing candidate lock's four exact wheel inputs are accepted. This
does not create a venv or a native seal. A failed output remains incomplete;
there is no automatic repair, overwrite, download or destructive cleanup.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlparse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APPROVED = {"cffi": "2.1.1", "cryptography": "50.0.1", "pillow": "12.3.0", "pycparser": "3.0"}
# Approved exact artifacts, not merely arbitrary wheels with matching versions.
ARTIFACTS = {
    "cffi": ("cffi-2.1.1-cp312-cp312-win_amd64.whl", "f53e442b08449d42821fa4a4fba000095af9f62742a500f978a9f557ec44339a", 185919),
    "cryptography": ("cryptography-50.0.1-cp311-abi3-win_amd64.whl", "aed8db4f6d71c51efb89530e12d9464e7bf2923d46c3205dc794a2a93f8c0648", 3842826),
    "pillow": ("pillow-12.3.0-cp312-cp312-win_amd64.whl", "a2b55dd6b2a4c4b7d87ffa56bdb33fdc5fdb9a462173861a7bc097f17d91cb09", 7227137),
    "pycparser": ("pycparser-3.0-py3-none-any.whl", "b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992", 48172),
}
IDENTITY = {"implementation": "CPython", "version": "3.12.10", "bits": 64, "platform": "win32", "pip": "25.0.1"}
PROBE = (
    "import json,platform,struct,sys,pip;"
    "print(json.dumps({'implementation':platform.python_implementation(),"
    "'version':platform.python_version(),'bits':struct.calcsize('P')*8,"
    "'platform':sys.platform,'pip':pip.__version__}))"
)


class PreparationError(ValueError):
    """Safe fixed diagnostic without subprocess output or external paths."""


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PreparationError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def reject_constant(_):
    raise PreparationError("NONFINITE_JSON")


def strict_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except (TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise PreparationError("MALFORMED_JSON") from exc


def reject_links(path):
    for component in (path, *path.parents):
        if component.exists() or component.is_symlink():
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise PreparationError("LINK_OR_REPARSE_POINT")


def absolute(path):
    if not path.is_absolute():
        raise PreparationError("ABSOLUTE_PATH_REQUIRED")
    reject_links(path)
    return path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_lock(path):
    lock = strict_json(path.read_bytes())
    expected = {"schemaVersion": 1, "purpose": "F3A_LIMITED_DEVELOPMENT_CANARY", "python": "3.12.10",
                "implementation": "CPython", "bits": 64, "platform": "win_amd64"}
    if not isinstance(lock, dict) or any(type(lock.get(k)) is not type(v) or lock.get(k) != v for k, v in expected.items()):
        raise PreparationError("LOCK_IDENTITY_MISMATCH")
    installer = lock.get("installer")
    if not isinstance(installer, dict) or installer.get("name") != "pip" or installer.get("version") != "25.0.1":
        raise PreparationError("BUILD_INSTALLER_MISMATCH")
    dependencies = lock.get("dependencies")
    if not isinstance(dependencies, list) or len(dependencies) != 4:
        raise PreparationError("DEPENDENCY_CLOSURE_MISMATCH")
    selected = {}
    for item in dependencies:
        if not isinstance(item, dict):
            raise PreparationError("DEPENDENCY_CLOSURE_MISMATCH")
        name = item.get("name")
        if not isinstance(name, str) or name not in APPROVED or name in selected or item.get("version") != APPROVED[name]:
            raise PreparationError("DEPENDENCY_CLOSURE_MISMATCH")
        filename = item.get("filename")
        if not isinstance(filename, str) or any(c in filename for c in ":\\/") or not filename.endswith(".whl") or not filename.startswith(f"{name}-{APPROVED[name]}-"):
            raise PreparationError("WHEEL_FILENAME_INVALID")
        if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise PreparationError("WHEEL_HASH_INVALID")
        if type(item.get("size")) is not int or item["size"] <= 0:
            raise PreparationError("WHEEL_SIZE_INVALID")
        if (filename, item["sha256"], item["size"]) != ARTIFACTS[name]:
            raise PreparationError("UNAPPROVED_ARTIFACT")
        selected[name] = item
    return selected


def validate_requirements(path, selected):
    actual = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            actual.append(line)
    expected = {f"{name}=={item['version']} --hash=sha256:{item['sha256']}" for name, item in selected.items()}
    if len(actual) != 4 or set(actual) != expected:
        raise PreparationError("REQUIREMENTS_MISMATCH")


def validate_wheels(wheelhouse, selected):
    reject_links(wheelhouse)
    if not wheelhouse.is_dir() or {p.name for p in wheelhouse.iterdir()} != {item["filename"] for item in selected.values()}:
        raise PreparationError("UNEXPECTED_WHEELHOUSE_FILE")
    for item in selected.values():
        path = wheelhouse / item["filename"]
        reject_links(path)
        if not path.is_file() or path.stat().st_size != item["size"] or sha256(path) != item["sha256"]:
            raise PreparationError("WHEEL_HASH_OR_SIZE_MISMATCH")


def validate_wheel_metadata(wheelhouse, selected):
    # Reuse the pinned build tool's standard requirement/marker parser. This is
    # not an installer; parsing precedes pip so even direct-URL dependencies
    # cannot trigger network access despite --no-index.
    from pip._vendor.packaging.requirements import InvalidRequirement, Requirement
    from pip._vendor.packaging.markers import default_environment
    environment = default_environment()
    environment["extra"] = ""
    for name, item in selected.items():
        try:
            with zipfile.ZipFile(wheelhouse / item["filename"]) as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                if len(set(names)) != len(names):
                    raise PreparationError("WHEEL_MEMBER_INVALID")
                for info in infos:
                    member = info.filename
                    if member.startswith("/") or any(c in member for c in "\\:") or ".." in member.split("/") or stat.S_ISLNK(info.external_attr >> 16):
                        raise PreparationError("WHEEL_MEMBER_INVALID")
                metadata_names = [member for member in names if member.endswith(".dist-info/METADATA")]
                if len(metadata_names) != 1:
                    raise PreparationError("WHEEL_METADATA_INVALID")
                metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
                if str(metadata.get("Name", "")).lower().replace("_", "-") != name or metadata.get("Version") != item["version"]:
                    raise PreparationError("WHEEL_METADATA_INVALID")
                for declared in metadata.get_all("Requires-Dist", []):
                    requirement = Requirement(declared)
                    if requirement.url is not None:
                        raise PreparationError("DIRECT_URL_DEPENDENCY_REFUSED")
                    if requirement.marker is not None and not requirement.marker.evaluate(environment):
                        continue
                    dependency = requirement.name.lower().replace("_", "-")
                    if dependency not in APPROVED or requirement.extras or APPROVED[dependency] not in requirement.specifier:
                        raise PreparationError("DEPENDENCY_CLOSURE_MISMATCH")
        except (zipfile.BadZipFile, InvalidRequirement, KeyError) as exc:
            raise PreparationError("WHEEL_METADATA_INVALID") from exc


def validate_report(report, selected, wheelhouse):
    if not isinstance(report, dict) or report.get("version") != "1" or report.get("pip_version") != "25.0.1":
        raise PreparationError("RESOLVER_REPORT_INVALID")
    installs = report.get("install")
    if not isinstance(installs, list) or len(installs) != 4:
        raise PreparationError("DEPENDENCY_CLOSURE_MISMATCH")
    seen = set()
    for item in installs:
        if not isinstance(item, dict) or not isinstance(item.get("metadata"), dict):
            raise PreparationError("RESOLVER_REPORT_INVALID")
        name = str(item["metadata"].get("name", "")).lower().replace("_", "-")
        if name not in selected or name in seen or item["metadata"].get("version") != selected[name]["version"]:
            raise PreparationError("DEPENDENCY_CLOSURE_MISMATCH")
        seen.add(name)
        download = item.get("download_info", {})
        if not isinstance(download, dict) or not isinstance(download.get("url"), str):
            raise PreparationError("RESOLVER_REPORT_INVALID")
        url = urlparse(download["url"])
        wanted = (wheelhouse / selected[name]["filename"]).as_uri()
        if url.scheme != "file" or unquote(download["url"]) != unquote(wanted):
            raise PreparationError("RESOLVER_SOURCE_MISMATCH")
        archive = download.get("archive_info", {})
        hashes = archive.get("hashes", {}) if isinstance(archive, dict) else {}
        if not isinstance(hashes, dict) or hashes.get("sha256") != selected[name]["sha256"]:
            raise PreparationError("RESOLVER_HASH_MISMATCH")
    return [{"name": name, "version": selected[name]["version"], "filename": selected[name]["filename"],
             "sha256": selected[name]["sha256"], "size": selected[name]["size"]} for name in sorted(seen)]


def clean_environment(workspace):
    environment = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR"}}
    environment.update({"TEMP": str(workspace), "TMP": str(workspace), "PIP_CONFIG_FILE": os.devnull,
                        "PIP_NO_INPUT": "1", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return environment


def run_checked(command, workspace, environment, *, capture=False):
    try:
        process = subprocess.run(command, cwd=workspace, env=environment, capture_output=True,
                                 text=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreparationError("BUILD_PROCESS_FAILED") from exc
    if process.returncode:
        raise PreparationError("BUILD_PROCESS_FAILED")
    return process.stdout if capture else None


def pip_command(python, wheelhouse, requirements, target, report, *, dry_run):
    command = [str(python), "-I", "-B", "-m", "pip", "--isolated", "install", "--no-index",
               "--find-links", str(wheelhouse), "--only-binary=:all:", "--require-hashes",
               "--no-cache-dir", "--disable-pip-version-check", "--no-compile", "--ignore-installed",
               "--target", str(target), "--report", str(report), "-r", str(requirements)]
    if dry_run:
        command.append("--dry-run")
    return command


def validate_installed(target, selected):
    actual = {}
    for distribution in importlib.metadata.distributions(path=[str(target)]):
        name = distribution.metadata["Name"].lower().replace("_", "-")
        if name in actual:
            raise PreparationError("INSTALLED_CLOSURE_MISMATCH")
        actual[name] = distribution.version
    if actual != APPROVED:
        raise PreparationError("INSTALLED_CLOSURE_MISMATCH")
    files = []
    for path in sorted(target.rglob("*")):
        reject_links(path)
        if path.is_dir():
            continue
        if not path.is_file() or path.suffix in {".pyc", ".pyo", ".pth"} or "__pycache__" in path.parts or path.name in {"sitecustomize.py", "usercustomize.py"}:
            raise PreparationError("UNEXPECTED_INSTALLED_FILE")
        files.append({"path": path.relative_to(target).as_posix(), "sha256": sha256(path), "size": path.stat().st_size})
    for item in selected.values():
        for license_file in item.get("licenses", []):
            relative = license_file["path"]
            if Path(relative).is_absolute() or ".." in Path(relative).parts or any(c in relative for c in "\\:"):
                raise PreparationError("LICENSE_PATH_INVALID")
            path = target / relative
            if not path.is_file() or path.stat().st_size != license_file["size"] or sha256(path) != license_file["sha256"]:
                raise PreparationError("INSTALLED_LICENSE_MISMATCH")
    return files


def prepare(python, wheelhouse, output, evidence, lock, requirements):
    for path in (python, wheelhouse, output, evidence, lock, requirements):
        absolute(path)
    if python.name.lower() != "python.exe" or not python.is_file():
        raise PreparationError("BUILD_PYTHON_INVALID")
    if output == ROOT or ROOT in output.parents or evidence == ROOT or ROOT in evidence.parents or evidence == output or output in evidence.parents:
        raise PreparationError("OUTPUT_ISOLATION_REQUIRED")
    if output.exists() or evidence.exists():
        raise PreparationError("EXISTING_OUTPUT_REFUSED")
    selected = load_lock(lock)
    validate_requirements(requirements, selected)
    validate_wheels(wheelhouse, selected)
    lock_hash, requirements_hash = sha256(lock), sha256(requirements)
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence.parent.mkdir(parents=True, exist_ok=True)
    state = {"schemaVersion": 1, "purpose": "F3A_LIMITED_RUNTIME_BUILD_EVIDENCE",
             "status": "PREPARATION_INCOMPLETE", "lockSha256": lock_hash,
             "requirementsSha256": requirements_hash, "dependencies": [], "files": []}
    with evidence.open("x", encoding="utf-8") as stream:
        json.dump(state, stream, sort_keys=True, indent=2)
        stream.write("\n")
    try:
        with tempfile.TemporaryDirectory(prefix="f3a-build-", dir=output.parent) as temporary:
            workspace = Path(temporary)
            environment = clean_environment(workspace)
            identity = strict_json(run_checked([str(python), "-I", "-B", "-c", PROBE], workspace, environment, capture=True))
            if identity != IDENTITY:
                raise PreparationError("BUILD_PYTHON_OR_PIP_MISMATCH")
            state["buildIdentity"] = identity
            validate_wheel_metadata(wheelhouse, selected)
            target = output / "site-packages"
            resolver_report = workspace / "resolver-report.json"
            run_checked(pip_command(python, wheelhouse, requirements, target, resolver_report, dry_run=True), workspace, environment)
            state["dependencies"] = validate_report(strict_json(resolver_report.read_bytes()), selected, wheelhouse)
            validate_wheels(wheelhouse, selected)
            if sha256(lock) != lock_hash or sha256(requirements) != requirements_hash:
                raise PreparationError("IMMUTABLE_INPUT_CHANGED")
            output.mkdir()  # exclusive fresh reservation only after closure match
            install_report = workspace / "install-report.json"
            run_checked(pip_command(python, wheelhouse, requirements, target, install_report, dry_run=False), workspace, environment)
            if validate_report(strict_json(install_report.read_bytes()), selected, wheelhouse) != state["dependencies"]:
                raise PreparationError("DEPENDENCY_CLOSURE_MISMATCH")
            state["files"] = validate_installed(target, selected)
            validate_wheels(wheelhouse, selected)
            if sha256(lock) != lock_hash or sha256(requirements) != requirements_hash:
                raise PreparationError("IMMUTABLE_INPUT_CHANGED")
            state["status"] = "CANDIDATE_RUNTIME_STAGED_FOR_AUDIT"
    except (PreparationError, OSError) as exc:
        state["status"] = "PREPARATION_INCOMPLETE"
        state["error"] = str(exc) if isinstance(exc, PreparationError) else "FILESYSTEM_FAILURE"
        raise PreparationError(state["error"]) from exc
    finally:
        evidence.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("python", "wheelhouse", "output", "evidence"):
        parser.add_argument(f"--{option}", required=True, type=Path)
    parser.add_argument("--lock", type=Path, default=ROOT / "packaging/f3a-limited-runtime-lock.json")
    parser.add_argument("--requirements", type=Path, default=ROOT / "packaging/f3a-limited-requirements.txt")
    arguments = parser.parse_args(argv)
    try:
        prepare(arguments.python, arguments.wheelhouse, arguments.output, arguments.evidence, arguments.lock, arguments.requirements)
    except (PreparationError, OSError) as exc:
        code = str(exc) if isinstance(exc, PreparationError) else "FILESYSTEM_FAILURE"
        print(f"PREPARATION_FAILED: {code}", file=sys.stderr)
        return 11
    print("CANDIDATE_RUNTIME_STAGED_FOR_AUDIT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
