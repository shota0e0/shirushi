"""CI-only independent Candidate B proof, never a release/archive/upload tool.

Candidate A is not an input. Acquisition uses only four exact approved URLs and
hashes; offline installation remains the existing helper's responsibility.
Negative cases mutate disposable copies only. Distribution stays NOT READY.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "packaging/f3a-limited-runtime-lock.json"
REQUIREMENTS = ROOT / "packaging/f3a-limited-requirements.txt"
LOCK_SHA = "38f51adb89f89bdf488de3371cd6c36b78e8ab326571f458c028cb4f122b3d69"
RUNNER = "shirushi-limited-canary.exe"
SEAL = ".venv-py312/.shirushi-f3a-seal.json"
FIXTURE_SHA = "558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316"
TOOL_SHA = "90cbcebe30250f8e8c53416d32ed86065dc04a23be86e4a2337f5cd1badfa0b7"
URLS = {
    "cffi": "https://files.pythonhosted.org/packages/d9/79/615cc094e2fb508cade7de88d3b4f6c4ec2bab695c97bce9153dc65aadf5/cffi-2.1.1-cp312-cp312-win_amd64.whl",
    "cryptography": "https://files.pythonhosted.org/packages/42/8b/cb12b1b60c91b074ca6bf0fdd59aa8f10d8bc5f73af8faece86ef0421b37/cryptography-50.0.1-cp311-abi3-win_amd64.whl",
    "pillow": "https://files.pythonhosted.org/packages/45/89/da2f7971a317f83d807fdd4065c0af40208e59e692cc43d315a71a0e96d1/pillow-12.3.0-cp312-cp312-win_amd64.whl",
    "pycparser": "https://files.pythonhosted.org/packages/0c/c3/44f3fbbfa403ea2a7c779186dc20772604442dde72947e7d01069cbe98e3/pycparser-3.0-py3-none-any.whl",
}
SOURCE_FILES = (
    "src/inspection_metadata.py", "src/runtime_paths.py", "src/signature_verifier.py", "src/c2pa_utils.py",
    "scripts/inspect_limited_fixture.py", "scripts/manifest_claim_audit.py",
)


def load_fixed(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load_fixed("prepare_f3a_limited_runtime")
entry = load_fixed("inspect_limited_fixture")


class ProofError(ValueError):
    """Fixed diagnostic; remote/child exception strings never cross boundary."""


def require(condition, code):
    if not condition:
        raise ProofError(code)


def outside_source(path):
    helper.absolute(path)
    require(path != ROOT and ROOT not in path.parents, "ISOLATION_REQUIRED")
    return path


def fresh(path):
    outside_source(path)
    require(not path.exists(), "EXISTING_OUTPUT_REFUSED")


def relative(path):
    require(isinstance(path, str) and path and path.isascii() and not path.startswith("/"), "UNSAFE_RELATIVE_PATH")
    require(not any(c in path for c in "\\:\x00") and all(p not in {"", ".", ".."} for p in path.split("/")), "UNSAFE_RELATIVE_PATH")
    return Path(*path.split("/"))


def inventory(root):
    helper.reject_links(root)
    require(root.is_dir(), "INVENTORY_ROOT_INVALID")
    records, folded = [], set()
    for path in sorted(root.rglob("*")):
        helper.reject_links(path)
        name = path.relative_to(root).as_posix()
        relative(name)
        require(name.lower() not in folded, "CASE_COLLIDING_FILE")
        folded.add(name.lower())
        if path.is_dir():
            continue
        require(path.is_file(), "NONREGULAR_FILE")
        records.append({"path": name, "size": path.stat().st_size, "sha256": helper.sha256(path)})
    return records


def digest_inventory(records):
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def approved_inputs():
    require(helper.sha256(LOCK) == LOCK_SHA, "APPROVED_LOCK_CHANGED")
    selected = helper.load_lock(LOCK)
    helper.validate_requirements(REQUIREMENTS, selected)
    for name, item in selected.items():
        require(item.get("url") == URLS[name], "UNAPPROVED_URL")
    return selected


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProofError("REDIRECT_REFUSED")


def official_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), RejectRedirects(),
                                      urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def acquire_one(name, item, destination, opener):
    require(name in URLS and item.get("url") == URLS[name], "UNAPPROVED_URL")
    parsed = urllib.parse.urlsplit(item["url"])
    require(parsed.scheme == "https" and parsed.hostname == "files.pythonhosted.org" and
            parsed.username is None and parsed.password is None and parsed.port is None and not parsed.query and not parsed.fragment, "UNAPPROVED_URL")
    require((item["filename"], item["sha256"], item["size"]) == helper.ARTIFACTS[name], "UNAPPROVED_ARTIFACT")
    helper.reject_links(destination)
    require(not destination.exists() and destination.name == item["filename"], "EXISTING_OUTPUT_REFUSED")
    try:
        with opener.open(urllib.request.Request(item["url"], headers={"User-Agent": "Shirushi-CI-fixed-input-proof"}), timeout=60) as response:
            require(response.geturl() == item["url"] and response.status == 200, "UNAPPROVED_RESPONSE")
            with destination.open("xb") as output:
                size = 0
                while block := response.read(65536):
                    size += len(block)
                    require(size <= item["size"], "ARTIFACT_SIZE_MISMATCH")
                    output.write(block)
        require(size == item["size"] and helper.sha256(destination) == item["sha256"], "ARTIFACT_HASH_OR_SIZE_MISMATCH")
    except (urllib.error.URLError, OSError) as exc:
        raise ProofError("OFFICIAL_ARTIFACT_UNAVAILABLE") from exc
    return {"name": name, "version": item["version"], "filename": item["filename"], "url": item["url"], "size": size, "sha256": item["sha256"]}


def fetch(wheelhouse):
    fresh(wheelhouse)
    selected = approved_inputs()
    wheelhouse.mkdir(parents=True)
    opener = official_opener()
    inputs = [acquire_one(name, selected[name], wheelhouse / selected[name]["filename"], opener) for name in sorted(selected)]
    helper.validate_wheels(wheelhouse, selected)
    return {"status": "EXACT_CI_INPUTS_HASH_VERIFIED", "inputs": inputs, "runtimeLockSha256": LOCK_SHA}


def copy_checked(source, destination):
    helper.reject_links(source)
    require(source.is_file() and not destination.exists(), "COPY_SOURCE_OR_TARGET_INVALID")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    require(helper.sha256(source) == helper.sha256(destination) and source.stat().st_size == destination.stat().st_size, "COPY_HASH_MISMATCH")


def assemble(runtime, runtime_evidence, runner, output):
    for path in (runtime, runtime_evidence):
        outside_source(path)
    # Compiled runner is a fixed explicit CI build output (may be in Cargo target).
    helper.absolute(runner)
    require(runner.name == RUNNER, "RUNNER_IDENTITY_INVALID")
    fresh(output)
    require(runtime != output and runtime not in output.parents and output not in runtime.parents, "ISOLATION_REQUIRED")
    selected = approved_inputs()
    recorded = helper.strict_json(runtime_evidence.read_bytes())
    require(isinstance(recorded, dict) and recorded.get("status") == "CANDIDATE_RUNTIME_STAGED_FOR_AUDIT" and
            recorded.get("buildIdentity") == helper.IDENTITY and recorded.get("lockSha256") == LOCK_SHA and
            recorded.get("requirementsSha256") == helper.sha256(REQUIREMENTS), "RUNTIME_EVIDENCE_INVALID")
    expected_dependencies = [{"name": name, "version": item["version"], "filename": item["filename"],
                              "sha256": item["sha256"], "size": item["size"]} for name, item in sorted(selected.items())]
    require(recorded.get("dependencies") == expected_dependencies, "RUNTIME_CLOSURE_MISMATCH")
    site = runtime / "site-packages"
    original_inventory = helper.validate_installed(site, selected)
    require(original_inventory == recorded.get("files"), "RUNTIME_INVENTORY_MISMATCH")
    fixture = ROOT / "tests/fixtures/inspection/valid_shirushi.png"
    tool = ROOT / "tools/c2patool-0.26.60/c2patool/c2patool.exe"
    require(fixture.stat().st_size == 319495 and helper.sha256(fixture) == FIXTURE_SHA, "FIXTURE_CHANGED")
    require(helper.sha256(tool) == TOOL_SHA, "C2PATOOL_CHANGED")
    output.mkdir(parents=True)
    for record in original_inventory:
        copy_checked(site / relative(record["path"]), output / "runtime/site-packages" / relative(record["path"]))
    fixed_files = [(ROOT / path, output / path) for path in SOURCE_FILES]
    fixed_files += [(runner, output / RUNNER), (LOCK, output / "runtime-lock.json"),
                    (fixture, output / "fixtures/valid_shirushi.png"), (tool, output / "tools/c2patool.exe"),
                    (ROOT / "tests/fixtures/inspection/manifest.json", output / "fixtures/manifest.json")]
    for source, destination in fixed_files:
        copy_checked(source, destination)
    settings = output / "config/verifier-settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_bytes(b"{}\n")
    files = inventory(output)
    (output / "manifest.json").write_text(json.dumps({"schemaVersion": 1, "artifactType": "F3A_LIMITED_DEVELOPMENT_CANARY", "files": files},
                                                  sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    sums = "".join(f"{item['sha256']}  {item['path']}\n" for item in files)
    sums += f"{helper.sha256(output / 'manifest.json')}  manifest.json\n"
    (output / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")
    require(helper.validate_installed(site, selected) == original_inventory, "CANDIDATE_B_MUTATED")
    all_files = inventory(output)
    cffi = next((r for r in original_inventory if r["path"] == "bin/cffi-gen-src.exe"), None)
    require(cffi is not None, "EXPECTED_CFFI_LAUNCHER_MISSING")
    native_files = [r for r in original_inventory if Path(r["path"]).suffix.lower() in {".pyd", ".dll", ".exe"}]
    return {"status": "EPHEMERAL_CANDIDATE_B_ASSEMBLED", "runtimeLockSha256": LOCK_SHA,
            "inputs": recorded["dependencies"], "packageVersions": helper.APPROVED, "runtimeFiles": original_inventory,
            "runtimeFileCount": len(original_inventory), "runtimeBytes": sum(r["size"] for r in original_inventory),
            "nativeFileCount": len(native_files), "nativeFiles": native_files,
            "packageFiles": all_files, "packageInventorySha256": digest_inventory(all_files),
            "fixtureSha256": FIXTURE_SHA, "c2patoolSha256": TOOL_SHA,
            "cffiLauncher": {**cffi, "classification": "PRESENT_UNUSED_PENDING_DISTRIBUTION_REVIEW",
                             "provenance": "cffi2.1.1 console_scripts cffi-gen-src=cffi._cffi_gen_src:run; pinned pip-generated launcher", "executed": False},
            "pyo3Ffi": "PARTIAL / ATTRIBUTION_UNRESOLVED", "distributionCompliance": "NOT_READY", "fullF3A": "NOT_READY"}


def strict_native_json(raw):
    require(isinstance(raw, bytes) and 0 < len(raw) <= 65536, "NATIVE_OUTPUT_INVALID")
    try:
        value = helper.strict_json(raw.decode("utf-8"))
        def depth(item, level=0):
            require(level <= 8, "NATIVE_OUTPUT_INVALID")
            if isinstance(item, dict):
                for child in item.values():
                    depth(child, level + 1)
            elif isinstance(item, list):
                for child in item:
                    depth(child, level + 1)
        depth(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProofError("NATIVE_OUTPUT_INVALID") from exc


def native_command(package, action, python=None):
    outside_source(package)
    require(action in {"prepare", "inspect-limited-fixture"}, "INVALID_NATIVE_ACTION")
    runner = package / RUNNER
    helper.reject_links(runner)
    require(runner.is_file(), "RUNNER_MISSING")
    command = [str(runner), action]
    if action == "prepare":
        helper.absolute(python)
        require(python.is_file() and python.name.lower() == "python.exe", "PYTHON_PATH_INVALID")
        command += ["--python", str(python)]
    else:
        require(python is None, "INVALID_NATIVE_ACTION")
    return command


def run_native(package, action, python=None):
    command = native_command(package, action, python)
    with tempfile.TemporaryDirectory(prefix="f3a-ci-native-", dir=package.parent) as temporary:
        working = Path(temporary)
        environment = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR"}}
        environment.update({"TEMP": str(working), "TMP": str(working)})
        try:
            result = subprocess.run(command, cwd=working, env=environment, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, timeout=300, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProofError("NATIVE_EXECUTION_FAILED") from exc
    require(len(result.stdout) <= 65536 and len(result.stderr) <= 16384, "NATIVE_OUTPUT_INVALID")
    return result


def validate_failure(result, code, exit_code):
    require(result.returncode == exit_code, "FAILURE_EXIT_MISMATCH")
    value = strict_native_json(result.stdout)
    require(value == {"contractVersion": 2, "operation": entry.OPERATION, "result": "INSPECTION_FAILED", "error": {"code": code}}, "FAILURE_ENVELOPE_INVALID")
    return {"code": code, "exit": exit_code, "partialInspection": False}


def read_and_validate_seal(package, python):
    raw = (package / SEAL).read_bytes()
    seal = strict_native_json(raw)
    manifest = helper.strict_json((package / "manifest.json").read_bytes())
    files = [r for r in manifest["files"] if r["path"].startswith(("runtime/", "src/", "scripts/", "tools/", "config/")) or r["path"] == RUNNER]
    for relative_path in (".venv-py312/Scripts/python.exe", ".venv-py312/Scripts/pythonw.exe"):
        path = package / relative_path
        files.append({"path": relative_path, "sha256": helper.sha256(path), "size": path.stat().st_size})
    selected = approved_inputs()
    expected = {"sealFormatVersion": 1, "runnerContractVersion": 1,
                "packageManifestSha256": helper.sha256(package / "manifest.json"),
                "runtimeLockSha256": LOCK_SHA, "fixtureManifestSha256": helper.sha256(package / "fixtures/manifest.json"),
                "identity": {"version": "3.12.10", "bits": 64, "implementation": "CPython",
                             "pythonExecutableSha256": helper.sha256(package / ".venv-py312/Scripts/python.exe"),
                             "basePythonExecutableSha256": helper.sha256(python)},
                "runtimeArtifacts": sorted(files, key=lambda r: r["path"]),
                "wheelArtifacts": sorted([{"path": item["filename"], "sha256": item["sha256"], "size": item["size"]} for item in selected.values()], key=lambda r: r["path"])}
    require(isinstance(seal, dict) and type(seal.get("sealFormatVersion")) is int and
            type(seal.get("runnerContractVersion")) is int, "NATIVE_SEAL_BINDING_MISMATCH")
    require(seal == expected, "NATIVE_SEAL_BINDING_MISMATCH")
    return seal


def prove(package, python, negatives_root):
    outside_source(package)
    helper.absolute(python)
    fresh(negatives_root)
    require(package != negatives_root and package not in negatives_root.parents and negatives_root not in package.parents, "ISOLATION_REQUIRED")
    require(not (package / ".venv-py312").exists(), "EXISTING_VENV_REFUSED")
    before_prepare = inventory(package)
    result = run_native(package, "prepare", python)
    require(result.returncode == 0 and not result.stdout and result.stderr.strip() == b"PREPARE_OK: package-local runtime seal validated", "PREPARE_FAILED")
    seal = read_and_validate_seal(package, python)
    sealed_files = inventory(package)
    require([r for r in sealed_files if not r["path"].startswith(".venv-py312/")] == before_prepare, "IMMUTABLE_PACKAGE_MUTATED")
    sealed_digest = digest_inventory(sealed_files)
    reuse = run_native(package, "prepare", python)
    require(reuse.returncode == 0 and not reuse.stdout and reuse.stderr.strip() == b"PREPARE_OK: package-local runtime seal validated", "REUSE_FAILED")
    require(inventory(package) == sealed_files, "CANDIDATE_B_MUTATED")
    inspection = run_native(package, "inspect-limited-fixture")
    require(inspection.returncode == 0, "INSPECTION_EXIT_MISMATCH")
    # Existing adapter/kernel own inspection semantics; no parallel inspection.
    kernel = entry._load_kernel(package)
    try:
        envelope = entry.parse_envelope(inspection.stdout, kernel)
    except Exception as exc:
        raise ProofError("FORMAL_INSPECTION_INVALID") from exc
    require(envelope.get("result") == "LIMITED_INSPECTION" and not inspection.stderr, "FORMAL_INSPECTION_INVALID")
    require(read_and_validate_seal(package, python) == seal and inventory(package) == sealed_files, "CANDIDATE_B_MUTATED")
    negative_results = []
    negatives_root.mkdir(parents=True)
    cases = (("seal", "SEAL_INVALID", 11), ("runtime", "PACKAGE_INVALID", 10), ("fixture", "PACKAGE_INVALID", 10),
             ("lock", "PACKAGE_INVALID", 10), ("identity", "SEAL_INVALID", 11), ("unsealed", "SEAL_INVALID", 11))
    for name, code, expected_exit in cases:
        copy = negatives_root / name
        shutil.copytree(package, copy, symlinks=False)
        require(inventory(copy) == sealed_files, "NEGATIVE_COPY_MISMATCH")
        if name in {"seal", "identity"}:
            changed = json.loads(json.dumps(seal))
            if name == "seal":
                changed["packageManifestSha256"] = "0" * 64
            else:
                changed["identity"]["bits"] = 32
            (copy / SEAL).write_text(json.dumps(changed), encoding="utf-8")
        elif name == "unsealed":
            (copy / SEAL).unlink()  # expressly disposable copy, never A/original B
        else:
            target = {"runtime": "runtime/site-packages/pycparser/__init__.py",
                      "fixture": "fixtures/valid_shirushi.png", "lock": "runtime-lock.json"}[name]
            with (copy / target).open("ab") as stream:
                stream.write(b"\nsynthetic disposable negative-case mutation\n")
        mutated_inventory = inventory(copy)
        outcome = run_native(copy, "prepare", python) if name == "unsealed" else run_native(copy, "inspect-limited-fixture")
        negative_results.append({"case": name, **validate_failure(outcome, code, expected_exit)})
        require(inventory(copy) == mutated_inventory, "NEGATIVE_REFUSAL_MUTATED_OR_REPAIRED_COPY")
        require(inventory(package) == sealed_files, "CANDIDATE_B_MUTATED")
    return {"status": "NATIVE_LIMITED_INSPECTION_EXECUTED", "seal": seal, "inspection": envelope,
            "prepare": "COMPLETED", "reuse": "EXACT_READ_ONLY_MATCH", "childExit": 0,
            "jobCleanup": "Native success requires Job empty/cleanup; separate actual-Job Rust test retained",
            "negativeCases": negative_results, "sealedPackageInventorySha256": sealed_digest,
            "packageFiles": sealed_files, "candidateBUnchanged": True, "candidateAReadOrUploaded": False,
            "cffiLauncherExecuted": False, "pyo3Ffi": "PARTIAL / ATTRIBUTION_UNRESOLVED",
            "distributionCompliance": "NOT_READY", "fullF3A": "NOT_READY"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    fetch_parser = commands.add_parser("fetch")
    fetch_parser.add_argument("--wheelhouse", required=True, type=Path)
    assembly_parser = commands.add_parser("assemble")
    for name in ("runtime", "runtime-evidence", "runner", "output"):
        assembly_parser.add_argument(f"--{name}", required=True, type=Path)
    proof_parser = commands.add_parser("prove")
    for name in ("package", "python", "negatives-root"):
        proof_parser.add_argument(f"--{name}", required=True, type=Path)
    for command in (fetch_parser, assembly_parser, proof_parser):
        command.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        fresh(args.evidence)
        target = args.wheelhouse if args.action == "fetch" else args.output if args.action == "assemble" else args.package
        require(args.evidence != target and target not in args.evidence.parents, "EVIDENCE_ISOLATION_REQUIRED")
    except (ProofError, helper.PreparationError) as exc:
        print(f"F3A_CI_PROOF_FAILED: {exc}", file=sys.stderr)
        return 1
    report = {"schemaVersion": 1, "operation": args.action, "status": "INCOMPLETE", "fullF3A": "NOT_READY", "distributionCompliance": "NOT_READY"}
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    with args.evidence.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report) + "\n")
    try:
        if args.action == "fetch":
            result = fetch(args.wheelhouse)
        elif args.action == "assemble":
            result = assemble(args.runtime, args.runtime_evidence, args.runner, args.output)
        else:
            result = prove(args.package, args.python, args.negatives_root)
        report.update(result)
    except Exception as exc:
        report["status"] = "INCOMPLETE"
        report["error"] = str(exc) if isinstance(exc, (ProofError, helper.PreparationError)) else "CI_PROOF_INTERNAL_FAILURE"
        print(f"F3A_CI_PROOF_FAILED: {report['error']}", file=sys.stderr)
        return 1
    finally:
        args.evidence.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    summary = {k: v for k, v in report.items() if k not in {"runtimeFiles", "packageFiles", "seal"}}
    if "seal" in report:
        summary["sealBindings"] = {key: value for key, value in report["seal"].items() if key != "runtimeArtifacts"}
        summary["sealedRuntimeArtifactCount"] = len(report["seal"]["runtimeArtifacts"])
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
