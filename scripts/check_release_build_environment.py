"""Validate the pinned Shirushi v0.1 Windows release build environment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import struct
import subprocess
import sys


EXPECTED_PYTHON = (3, 12, 10)
EXPECTED_PIP = "25.0.1"
EXPECTED_ARCHITECTURE = "AMD64"
EXPECTED_TORCH_RUNTIME = "2.14.0+cpu"
EXPECTED_TORCHVISION_RUNTIME = "0.29.0+cpu"
EXPECTED_C2PATOOL_VERSION = "c2patool 0.26.60"
EXPECTED_C2PATOOL_SHA256 = (
    "90CBCEBE30250F8E8C53416D32ED86065DC04A23BE86E4A2337F5CD1BADFA0B7"
)


def parse_requirements(path: Path) -> dict[str, str]:
    """Read exact package pins while ignoring comments and pip options."""

    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "--", "-r ")):
            continue
        if "==" not in line:
            raise ValueError(f"requirement is not exactly pinned: {path.name}: {line}")
        name, version = line.split("==", 1)
        normalized = name.strip().lower().replace("_", "-")
        if not normalized or not version.strip() or normalized in pins:
            raise ValueError(f"invalid requirement: {path.name}: {line}")
        pins[normalized] = version.strip()
    return pins


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def check_environment(project_root: Path) -> dict[str, object]:
    failures: list[str] = []
    checks: dict[str, object] = {}

    python_version = tuple(sys.version_info[:3])
    operating_system = platform.system()
    architecture = platform.machine().upper()
    pointer_bits = struct.calcsize("P") * 8
    checks["python"] = platform.python_version()
    checks["operatingSystem"] = operating_system
    checks["architecture"] = architecture
    checks["pointerBits"] = pointer_bits
    if python_version != EXPECTED_PYTHON:
        found_python = ".".join(str(item) for item in python_version)
        failures.append(f"Python must be 3.12.10, found {found_python}")
    if operating_system != "Windows":
        failures.append(f"operating system must be Windows, found {operating_system}")
    if architecture != EXPECTED_ARCHITECTURE or pointer_bits != 64:
        failures.append(f"Python must be Windows AMD64, found {architecture}/{pointer_bits}")

    try:
        pip_version = importlib.metadata.version("pip")
        checks["pip"] = pip_version
        if pip_version != EXPECTED_PIP:
            failures.append(f"pip must be {EXPECTED_PIP}, found {pip_version}")
    except importlib.metadata.PackageNotFoundError:
        failures.append(f"missing package: pip=={EXPECTED_PIP}")

    expected: dict[str, str] = {}
    for requirement_path in (
        project_root / "requirements-release-runtime.txt",
        project_root / "requirements-release-build.txt",
    ):
        for name, version in parse_requirements(requirement_path).items():
            if name in expected and expected[name] != version:
                failures.append(f"conflicting pins for {name}")
            expected[name] = version

    installed: dict[str, str] = {}
    for name, expected_version in sorted(expected.items()):
        try:
            actual_version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            failures.append(f"missing package: {name}=={expected_version}")
            continue
        installed[name] = actual_version
        accepted_versions = {expected_version, expected_version.removesuffix("+cpu")}
        if actual_version not in accepted_versions:
            failures.append(
                f"package version mismatch: {name}: expected {expected_version}, "
                f"found {actual_version}"
            )
    checks["packages"] = installed

    try:
        import torch
        import torchvision

        checks["torchRuntimeVersion"] = torch.__version__
        checks["torchvisionRuntimeVersion"] = torchvision.__version__
        checks["torchCudaVersion"] = torch.version.cuda
        if torch.__version__ != EXPECTED_TORCH_RUNTIME:
            failures.append(
                f"torch CPU build mismatch: expected {EXPECTED_TORCH_RUNTIME}, "
                f"found {torch.__version__}"
            )
        if torchvision.__version__ != EXPECTED_TORCHVISION_RUNTIME:
            failures.append(
                "torchvision CPU build mismatch: expected "
                f"{EXPECTED_TORCHVISION_RUNTIME}, found {torchvision.__version__}"
            )
        if torch.version.cuda is not None:
            failures.append(f"CUDA-enabled torch is not allowed: {torch.version.cuda}")
    except Exception as exc:
        failures.append(f"could not inspect PyTorch runtime: {type(exc).__name__}: {exc}")

    c2patool = (
        project_root
        / "tools"
        / "c2patool-0.26.60"
        / "c2patool"
        / "c2patool.exe"
    )
    checks["c2patoolPath"] = str(c2patool)
    if not c2patool.is_file():
        failures.append(f"c2patool is missing: {c2patool}")
    else:
        digest = sha256_file(c2patool)
        checks["c2patoolSha256"] = digest
        if digest != EXPECTED_C2PATOOL_SHA256:
            failures.append(
                f"c2patool SHA-256 mismatch: expected {EXPECTED_C2PATOOL_SHA256}, "
                f"found {digest}"
            )
        completed = subprocess.run(
            [str(c2patool), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
        )
        version_text = completed.stdout.strip()
        checks["c2patoolVersion"] = version_text
        if completed.returncode != 0 or version_text != EXPECTED_C2PATOOL_VERSION:
            failures.append(
                f"c2patool version mismatch: expected {EXPECTED_C2PATOOL_VERSION}, "
                f"found {version_text or completed.stderr.strip()}"
            )

    return {
        "status": "PASS" if not failures else "FAIL",
        "checks": checks,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()
    result = check_environment(args.project_root.resolve())
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["status"])
        for failure in result["failures"]:
            print(f"- {failure}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
