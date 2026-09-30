"""Mocked CI orchestration tests: no network, install, native or Cargo execution."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_f3a_ci_runtime", ROOT / "scripts/verify_f3a_ci_runtime.py")
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


class FakeResponse(io.BytesIO):
    status = 200
    def __init__(self, content, url):
        super().__init__(content)
        self.url = url
    def geturl(self):
        return self.url


class CIRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="f3a-ci-mocked-test-")
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.python = self.root / "python.exe"
        self.python.write_bytes(b"synthetic never executed")

    def test_approved_real_lock_url_hash_version_identity(self):
        selected = ci.approved_inputs()
        self.assertEqual(set(selected), set(ci.helper.APPROVED))
        for name, item in selected.items():
            self.assertEqual(item["url"], ci.URLS[name])
            self.assertEqual((item["filename"], item["sha256"], item["size"]), ci.helper.ARTIFACTS[name])

    def acquisition_item(self):
        item = copy.deepcopy(ci.approved_inputs()["cffi"])
        content = b"synthetic non-wheel download response"
        item["size"] = len(content)
        item["sha256"] = hashlib.sha256(content).hexdigest()
        return item, content

    def test_mocked_official_acquisition_exclusive_hash_match(self):
        item, content = self.acquisition_item()
        opener = Mock()
        opener.open.return_value = FakeResponse(content, item["url"])
        output = self.root / item["filename"]
        with patch.dict(ci.helper.ARTIFACTS, {"cffi": (item["filename"], item["sha256"], item["size"])}):
            result = ci.acquire_one("cffi", item, output, opener)
            self.assertEqual(result["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            with self.assertRaisesRegex(ci.ProofError, "EXISTING_OUTPUT_REFUSED"):
                ci.acquire_one("cffi", item, output, opener)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.args[0].full_url, ci.URLS["cffi"])

    def test_unapproved_url_hash_and_redirect_refused(self):
        item, content = self.acquisition_item()
        opener = Mock()
        bad_url = {**item, "url": "https://example.invalid/private"}
        with self.assertRaisesRegex(ci.ProofError, "UNAPPROVED_URL"):
            ci.acquire_one("cffi", bad_url, self.root / item["filename"], opener)
        with self.assertRaisesRegex(ci.ProofError, "UNAPPROVED_ARTIFACT"):
            ci.acquire_one("cffi", item, self.root / item["filename"], opener)
        opener.open.assert_not_called()
        with self.assertRaisesRegex(ci.ProofError, "REDIRECT_REFUSED"):
            ci.RejectRedirects().redirect_request(None, None, 302, "redirect", {}, "https://example.invalid")
        opener.open.return_value = FakeResponse(content, "https://example.invalid/changed")
        with patch.dict(ci.helper.ARTIFACTS, {"cffi": (item["filename"], item["sha256"], item["size"])}):
            with self.assertRaisesRegex(ci.ProofError, "UNAPPROVED_RESPONSE"):
                ci.acquire_one("cffi", item, self.root / item["filename"], opener)

    def test_mocked_acquisition_hash_or_size_mismatch_keeps_failure(self):
        item, content = self.acquisition_item()
        with patch.dict(ci.helper.ARTIFACTS, {"cffi": (item["filename"], item["sha256"], item["size"])}):
            for suffix, response in (("short", content[:-1]), ("changed", b"x" * len(content)), ("large", content + b"x")):
                directory = self.root / suffix
                directory.mkdir()
                opener = Mock()
                opener.open.return_value = FakeResponse(response, item["url"])
                with self.assertRaisesRegex(ci.ProofError, "ARTIFACT_(HASH_OR_SIZE|SIZE)_MISMATCH"):
                    ci.acquire_one("cffi", item, directory / item["filename"], opener)

    def test_opener_disables_proxies_and_uses_verified_tls(self):
        with patch.object(ci.urllib.request, "build_opener") as build, patch.object(ci.ssl, "create_default_context", return_value="verified-context") as tls:
            ci.official_opener()
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], ci.RejectRedirects)
        tls.assert_called_once_with()
        self.assertEqual(build.call_args.args[2]._context, "verified-context")

    def test_inventory_hash_changes_and_unsafe_paths_reject(self):
        root = self.root / "inventory"
        root.mkdir()
        path = root / "synthetic.py"
        path.write_bytes(b"original")
        before = ci.digest_inventory(ci.inventory(root))
        path.write_bytes(b"changed")
        self.assertNotEqual(ci.digest_inventory(ci.inventory(root)), before)
        for name in ("../outside", "C:/private", "a\\b", "/absolute", "a//b"):
            with self.assertRaises(ci.ProofError):
                ci.relative(name)

    def assembly_inputs(self):
        runtime = self.root / "mocked-runtime"
        site = runtime / "site-packages"
        (site / "bin").mkdir(parents=True)
        (site / "bin/cffi-gen-src.exe").write_bytes(b"synthetic never executed")
        (site / "mocked.py").write_bytes(b"synthetic package fixture")
        files = ci.inventory(site)
        selected = ci.approved_inputs()
        evidence = self.root / "mocked-runtime-evidence.json"
        evidence.write_text(json.dumps({"status": "CANDIDATE_RUNTIME_STAGED_FOR_AUDIT", "buildIdentity": ci.helper.IDENTITY,
            "lockSha256": ci.LOCK_SHA, "requirementsSha256": ci.helper.sha256(ci.REQUIREMENTS),
            "dependencies": [{"name": n, "version": d["version"], "filename": d["filename"], "sha256": d["sha256"], "size": d["size"]} for n, d in sorted(selected.items())],
            "files": files}), encoding="utf-8")
        runner = self.root / ci.RUNNER
        runner.write_bytes(b"synthetic never executed")
        return runtime, evidence, runner, files

    def test_mocked_assembly_exact_sources_inventory_and_original_preserved(self):
        runtime, evidence, runner, files = self.assembly_inputs()
        before = ci.inventory(runtime)
        output = self.root / "ephemeral-test-package"
        # Installation metadata is mocked; copied runtime is synthetic only.
        with patch.object(ci.helper, "validate_installed", return_value=files):
            result = ci.assemble(runtime, evidence, runner, output)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        expected = {f"runtime/site-packages/{r['path']}" for r in files} | set(ci.SOURCE_FILES) | {
            ci.RUNNER, "runtime-lock.json", "fixtures/valid_shirushi.png", "fixtures/manifest.json", "tools/c2patool.exe", "config/verifier-settings.json"}
        self.assertEqual({r["path"] for r in manifest["files"]}, expected)
        self.assertEqual({r["path"] for r in ci.inventory(output)}, expected | {"manifest.json", "SHA256SUMS"})
        sums = (output / "SHA256SUMS").read_text(encoding="utf-8")
        self.assertEqual({line.split("  ", 1)[1] for line in sums.splitlines()}, expected | {"manifest.json"})
        self.assertEqual(ci.inventory(runtime), before)
        self.assertFalse((output / ".venv-py312").exists())
        self.assertFalse(result["cffiLauncher"]["executed"])
        self.assertEqual(result["cffiLauncher"]["classification"], "PRESENT_UNUSED_PENDING_DISTRIBUTION_REVIEW")
        self.assertEqual(result["pyo3Ffi"], "PARTIAL / ATTRIBUTION_UNRESOLVED")
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_runtime_inventory_mismatch_refuses_before_assembly(self):
        runtime, evidence, runner, files = self.assembly_inputs()
        output = self.root / "refused"
        with patch.object(ci.helper, "validate_installed", return_value=files[:-1]):
            with self.assertRaisesRegex(ci.ProofError, "RUNTIME_INVENTORY_MISMATCH"):
                ci.assemble(runtime, evidence, runner, output)
        self.assertFalse(output.exists())

    def test_synthetic_seal_validator_exact_binding_and_rejections(self):
        # A static JSON unit fixture, NOT a native-generated/approved seal.
        runtime, evidence, runner, files = self.assembly_inputs()
        package = self.root / "synthetic-seal-validation"
        with patch.object(ci.helper, "validate_installed", return_value=files):
            ci.assemble(runtime, evidence, runner, package)
        scripts = package / ".venv-py312/Scripts"
        scripts.mkdir(parents=True)
        for name in ("python.exe", "pythonw.exe"):
            (scripts / name).write_bytes(b"synthetic interpreter not executable")
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        artifacts = [r for r in manifest["files"] if r["path"].startswith(("runtime/", "src/", "scripts/", "tools/", "config/")) or r["path"] == ci.RUNNER]
        for name in ("python.exe", "pythonw.exe"):
            path = scripts / name
            artifacts.append({"path": path.relative_to(package).as_posix(), "size": path.stat().st_size, "sha256": ci.helper.sha256(path)})
        selected = ci.approved_inputs()
        seal = {"sealFormatVersion": 1, "runnerContractVersion": 1,
            "packageManifestSha256": ci.helper.sha256(package / "manifest.json"), "runtimeLockSha256": ci.LOCK_SHA,
            "fixtureManifestSha256": ci.helper.sha256(package / "fixtures/manifest.json"),
            "identity": {"version": "3.12.10", "bits": 64, "implementation": "CPython", "pythonExecutableSha256": ci.helper.sha256(scripts / "python.exe"), "basePythonExecutableSha256": ci.helper.sha256(self.python)},
            "runtimeArtifacts": sorted(artifacts, key=lambda r: r["path"]),
            "wheelArtifacts": sorted([{"path": d["filename"], "size": d["size"], "sha256": d["sha256"]} for d in selected.values()], key=lambda r: r["path"])}
        seal_path = package / ci.SEAL
        seal_path.write_text(json.dumps(seal), encoding="utf-8")
        self.assertEqual(ci.read_and_validate_seal(package, self.python), seal)
        for field, value in (("runtimeLockSha256", "0" * 64), ("runnerContractVersion", 2), ("sealFormatVersion", True), ("extra", "unexpected")):
            changed = {**seal, field: value}
            seal_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ci.ProofError, "NATIVE_SEAL_BINDING_MISMATCH"):
                ci.read_and_validate_seal(package, self.python)

    def package_stub(self):
        package = self.root / "package"
        (package / "runtime/site-packages/pycparser").mkdir(parents=True)
        (package / "fixtures").mkdir()
        for name in (ci.RUNNER, "runtime-lock.json", "runtime/site-packages/pycparser/__init__.py", "fixtures/valid_shirushi.png"):
            (package / name).write_bytes(b"synthetic mock orchestration only")
        return package

    def test_fixed_native_command_environment_and_bounded_process_output(self):
        package = self.package_stub()
        self.assertEqual(ci.native_command(package, "prepare", self.python), [str(package / ci.RUNNER), "prepare", "--python", str(self.python)])
        with self.assertRaises(ci.ProofError):
            ci.native_command(package, "shell", self.python)
        with patch.dict(ci.os.environ, {"PYTHONPATH": "private", "PYTHONHOME": "private", "PIP_INDEX_URL": "private", "PATH": "private"}), \
                patch.object(ci.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            ci.run_native(package, "prepare", self.python)
        self.assertFalse(run.call_args.kwargs["shell"])
        for key in ("PYTHONPATH", "PYTHONHOME", "PIP_INDEX_URL", "PATH"):
            self.assertNotIn(key, run.call_args.kwargs["env"])
        self.assertNotEqual(run.call_args.kwargs["cwd"], package)
        with patch.object(ci.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"x" * 65537, b"")):
            with self.assertRaisesRegex(ci.ProofError, "NATIVE_OUTPUT_INVALID"):
                ci.run_native(package, "inspect-limited-fixture")

    def test_native_failure_strictness_exit_no_partial_duplicate(self):
        valid = {"contractVersion": 2, "operation": ci.entry.OPERATION, "result": "INSPECTION_FAILED", "error": {"code": "SEAL_INVALID"}}
        result = subprocess.CompletedProcess([], 11, json.dumps(valid).encode(), b"safe diagnostics")
        self.assertFalse(ci.validate_failure(result, "SEAL_INVALID", 11)["partialInspection"])
        for value in ({**valid, "inspection": {}}, {**valid, "contractVersion": 1}, {**valid, "unknown": 1}):
            result.stdout = json.dumps(value).encode()
            with self.assertRaises(ci.ProofError):
                ci.validate_failure(result, "SEAL_INVALID", 11)
        result.stdout = json.dumps(valid).encode()
        result.returncode = 0
        with self.assertRaisesRegex(ci.ProofError, "FAILURE_EXIT_MISMATCH"):
            ci.validate_failure(result, "SEAL_INVALID", 11)
        for raw in (b'{}{}', b'{"x":NaN}', b'{"x":1,"x":2}', b'\xff', b'x' * 65537):
            with self.assertRaises(ci.ProofError):
                ci.strict_native_json(raw)

    def mocked_native(self, package, action, python=None):
        if package.name == "package":
            if action == "prepare":
                venv = package / ".venv-py312"
                venv.mkdir(exist_ok=True)
                if not (package / ci.SEAL).exists():
                    (package / ci.SEAL).write_text(json.dumps(self.mocked_seal), encoding="utf-8")
                return subprocess.CompletedProcess([], 0, b"", b"PREPARE_OK: package-local runtime seal validated\n")
            return subprocess.CompletedProcess([], 0, json.dumps(self.mocked_success).encode(), b"")
        code, exit_code = ("PACKAGE_INVALID", 10) if package.name in {"runtime", "fixture", "lock"} else ("SEAL_INVALID", 11)
        return subprocess.CompletedProcess([], exit_code, json.dumps({"contractVersion": 2, "operation": ci.entry.OPERATION,
            "result": "INSPECTION_FAILED", "error": {"code": code}}).encode(), b"safe fixed diagnosis")

    def mock_native_contract(self):
        self.mocked_seal = {"packageManifestSha256": "a" * 64, "identity": {"bits": 64}}
        inner = {"source": {"sha256": ci.FIXTURE_SHA, "size": 319495, "format": "PNG"}, "contractVersion": 1,
                 "overall": "LIMITED_INSPECTION", "trustmark": "NOT_CHECKED", "fullVerificationPerformed": False, "successMotionEligible": False}
        def validate(value, require_expected=False):
            if value != inner:
                raise ValueError("mocked kernel contract mismatch")
        self.mocked_kernel = types.SimpleNamespace(validate_result=validate)
        self.mocked_success = ci.entry.success_envelope(inner, self.mocked_kernel)

    def test_mocked_native_positive_and_six_disposable_negatives_original_unchanged(self):
        package = self.package_stub()
        self.mock_native_contract()
        negatives = self.root / "disposable-negatives"
        with patch.object(ci, "run_native", side_effect=self.mocked_native) as run, \
                patch.object(ci, "read_and_validate_seal", return_value=self.mocked_seal), \
                patch.object(ci.entry, "_load_kernel", return_value=self.mocked_kernel):
            result = ci.prove(package, self.python, negatives)
        self.assertEqual(run.call_count, 9)
        self.assertEqual(result["inspection"]["result"], "LIMITED_INSPECTION")
        self.assertEqual(result["inspection"]["completeness"], "INCOMPLETE")
        self.assertEqual(len(result["negativeCases"]), 6)
        self.assertTrue(result["candidateBUnchanged"])
        self.assertFalse(result["candidateAReadOrUploaded"])
        self.assertFalse(result["cffiLauncherExecuted"])
        self.assertEqual(ci.digest_inventory(ci.inventory(package)), result["sealedPackageInventorySha256"])
        self.assertFalse((negatives / "unsealed" / ci.SEAL).exists())
        self.assertTrue((package / ci.SEAL).exists())
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_inspection_exit_failure_blocks_negative_tests_and_success(self):
        package = self.package_stub()
        self.mock_native_contract()
        original = self.mocked_native
        def failing_native(path, action, python=None):
            if action == "inspect-limited-fixture":
                return subprocess.CompletedProcess([], 15, json.dumps(self.mocked_success).encode(), b"")
            return original(path, action, python)
        negatives = self.root / "negative-not-started"
        with patch.object(ci, "run_native", side_effect=failing_native), patch.object(ci, "read_and_validate_seal", return_value=self.mocked_seal):
            with self.assertRaisesRegex(ci.ProofError, "INSPECTION_EXIT_MISMATCH"):
                ci.prove(package, self.python, negatives)
        self.assertFalse(negatives.exists())

    def test_malformed_or_full_success_result_never_accepted(self):
        package = self.package_stub()
        self.mock_native_contract()
        original = self.mocked_native
        self.mocked_success["result"] = "PASS"
        with patch.object(ci, "run_native", side_effect=original), patch.object(ci, "read_and_validate_seal", return_value=self.mocked_seal), \
                patch.object(ci.entry, "_load_kernel", return_value=self.mocked_kernel):
            with self.assertRaisesRegex(ci.ProofError, "FORMAL_INSPECTION_INVALID"):
                ci.prove(package, self.python, self.root / "negatives")

    def test_cli_failure_report_is_path_free_and_no_raw_stderr(self):
        output = self.root / "unused-wheelhouse"
        evidence = self.root / "failure.json"
        stderr = io.StringIO()
        with patch.object(ci, "fetch", side_effect=RuntimeError("private stderr/path")), patch.object(ci.sys, "stderr", stderr):
            self.assertEqual(ci.main(["fetch", "--wheelhouse", str(output), "--evidence", str(evidence)]), 1)
        report = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["error"], "CI_PROOF_INTERNAL_FAILURE")
        self.assertNotIn("private", stderr.getvalue())
        self.assertNotIn(str(self.root), json.dumps(report))


if __name__ == "__main__":
    unittest.main()
