"""Synthetic/mocked preparation checks: never download or invoke pip."""
import copy
import hashlib
import importlib.util
import json
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_f3a_limited_runtime", ROOT / "scripts/prepare_f3a_limited_runtime.py")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
ORIGINAL_ARTIFACTS = copy.deepcopy(helper.ARTIFACTS)


class OfflineRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="f3a-helper-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.wheels = self.root / "wheels"
        self.wheels.mkdir()
        self.lock = json.loads((ROOT / "packaging/f3a-limited-runtime-lock.json").read_text(encoding="utf-8"))
        for item in self.lock["dependencies"]:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(f"{item['name']}-{item['version']}.dist-info/METADATA",
                                 f"Metadata-Version: 2.4\nName: {item['name']}\nVersion: {item['version']}\n")
            content = buffer.getvalue()
            (self.wheels / item["filename"]).write_bytes(content)
            item["sha256"] = hashlib.sha256(content).hexdigest()
            item["size"] = len(content)
            item["licenses"] = []
        self.lockfile = self.root / "lock.json"
        self.lockfile.write_text(json.dumps(self.lock), encoding="utf-8")
        artifact_patch = patch.object(helper, "ARTIFACTS", {item["name"]: (item["filename"], item["sha256"], item["size"]) for item in self.lock["dependencies"]})
        artifact_patch.start()
        self.addCleanup(artifact_patch.stop)
        self.selected = helper.load_lock(self.lockfile)
        self.requirements = self.root / "requirements.txt"
        self.requirements.write_text("\n".join(f"{x['name']}=={x['version']} --hash=sha256:{x['sha256']}" for x in self.lock["dependencies"]), encoding="utf-8")
        self.python = self.root / "python.exe"
        self.python.write_bytes(b"synthetic-not-an-executable")
        self.output = self.root / "runtime"
        self.evidence = self.root / "evidence.json"

    def report(self):
        return {"version": "1", "pip_version": "25.0.1", "install": [
            {"metadata": {"name": item["name"], "version": item["version"]},
             "download_info": {"url": (self.wheels / item["filename"]).as_uri(),
                               "archive_info": {"hashes": {"sha256": item["sha256"]}}}}
            for item in self.lock["dependencies"]]}

    def prepare(self):
        return helper.prepare(self.python, self.wheels, self.output, self.evidence, self.lockfile, self.requirements)

    def test_existing_real_candidate_specification_is_consistent(self):
        with patch.object(helper, "ARTIFACTS", ORIGINAL_ARTIFACTS):
            selected = helper.load_lock(ROOT / "packaging/f3a-limited-runtime-lock.json")
        helper.validate_requirements(ROOT / "packaging/f3a-limited-requirements.txt", selected)
        self.assertEqual({k: x["version"] for k, x in selected.items()}, helper.APPROVED)

    def test_four_verified_inputs_and_report_accept(self):
        helper.validate_wheels(self.wheels, self.selected)
        helper.validate_wheel_metadata(self.wheels, self.selected)
        helper.validate_requirements(self.requirements, self.selected)
        self.assertEqual(len(helper.validate_report(self.report(), self.selected, self.wheels)), 4)

    def test_extra_or_changed_wheel_rejects(self):
        extra = self.wheels / "unexpected.whl"
        extra.write_bytes(b"unexpected")
        with self.assertRaisesRegex(helper.PreparationError, "UNEXPECTED_WHEELHOUSE_FILE"):
            helper.validate_wheels(self.wheels, self.selected)
        extra.unlink()
        item = next(iter(self.selected.values()))
        (self.wheels / item["filename"]).write_bytes(b"changed")
        with self.assertRaisesRegex(helper.PreparationError, "WHEEL_HASH_OR_SIZE_MISMATCH"):
            helper.validate_wheels(self.wheels, self.selected)

    def test_missing_extra_duplicate_or_wrong_version_report_rejects(self):
        for change in ("missing", "extra", "duplicate", "version", "hash", "source"):
            with self.subTest(change=change):
                report = self.report()
                installs = report["install"]
                if change == "missing":
                    installs.pop()
                elif change == "extra":
                    installs.append(copy.deepcopy(installs[0]))
                elif change == "duplicate":
                    installs[1] = copy.deepcopy(installs[0])
                elif change == "version":
                    installs[0]["metadata"]["version"] = "latest"
                elif change == "hash":
                    installs[0]["download_info"]["archive_info"]["hashes"]["sha256"] = "0" * 64
                else:
                    installs[0]["download_info"]["url"] = "https://example.invalid/unapproved.whl"
                with self.assertRaises(helper.PreparationError):
                    helper.validate_report(report, self.selected, self.wheels)

    def test_lock_version_extra_and_duplicate_reject(self):
        for kind in ("version", "extra", "duplicate"):
            lock = copy.deepcopy(self.lock)
            if kind == "version":
                lock["dependencies"][0]["version"] = "latest"
            elif kind == "extra":
                lock["dependencies"].append(copy.deepcopy(lock["dependencies"][0]))
            else:
                lock["dependencies"][1] = copy.deepcopy(lock["dependencies"][0])
            self.lockfile.write_text(json.dumps(lock), encoding="utf-8")
            with self.assertRaisesRegex(helper.PreparationError, "DEPENDENCY_CLOSURE_MISMATCH"):
                helper.load_lock(self.lockfile)

    def test_duplicate_nonfinite_json_reject(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{'):
            with self.assertRaises(helper.PreparationError):
                helper.strict_json(raw)

    def test_same_version_unapproved_artifact_hash_rejects(self):
        self.lock["dependencies"][0]["sha256"] = "0" * 64
        self.lockfile.write_text(json.dumps(self.lock), encoding="utf-8")
        with self.assertRaisesRegex(helper.PreparationError, "UNAPPROVED_ARTIFACT"):
            helper.load_lock(self.lockfile)

    def test_evidence_in_source_tree_rejected(self):
        self.evidence = ROOT / "unwanted-runtime-evidence.json"
        with patch.object(helper, "run_checked") as run:
            with self.assertRaisesRegex(helper.PreparationError, "OUTPUT_ISOLATION_REQUIRED"):
                self.prepare()
            run.assert_not_called()

    def test_requirements_extra_duplicate_reject(self):
        with self.requirements.open("a", encoding="utf-8") as stream:
            stream.write("\n" + self.requirements.read_text(encoding="utf-8").splitlines()[0])
        with self.assertRaisesRegex(helper.PreparationError, "REQUIREMENTS_MISMATCH"):
            helper.validate_requirements(self.requirements, self.selected)

    def test_standard_offline_command_and_sanitized_environment(self):
        with patch.dict(helper.os.environ, {"PYTHONPATH": "private", "PYTHONHOME": "private", "PYTHONSTARTUP": "private", "PIP_INDEX_URL": "private", "PATH": "private"}):
            environment = helper.clean_environment(self.root)
        for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PIP_INDEX_URL", "PATH"):
            self.assertNotIn(key, environment)
        command = helper.pip_command(self.python, self.wheels, self.requirements, self.output, self.evidence, dry_run=True)
        for flag in ("-I", "-B", "--isolated", "--no-index", "--only-binary=:all:", "--require-hashes", "--no-cache-dir", "--ignore-installed", "--no-compile", "--dry-run"):
            self.assertIn(flag, command)
        self.assertEqual(command[0], str(self.python))
        self.assertNotIn("--no-deps", command)

    def test_existing_output_refused_without_subprocess(self):
        self.output.mkdir()
        with patch.object(helper, "run_checked") as run:
            with self.assertRaisesRegex(helper.PreparationError, "EXISTING_OUTPUT_REFUSED"):
                self.prepare()
            run.assert_not_called()

    def test_python_mismatch_leaves_incomplete_evidence_no_runtime(self):
        with patch.object(helper, "run_checked", return_value=json.dumps({**helper.IDENTITY, "version": "3.13.0"})):
            with self.assertRaisesRegex(helper.PreparationError, "BUILD_PYTHON_OR_PIP_MISMATCH"):
                self.prepare()
        self.assertFalse(self.output.exists())
        state = json.loads(self.evidence.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "PREPARATION_INCOMPLETE")
        self.assertNotIn(str(self.root), json.dumps(state))

    def test_mocked_success_closure_before_install_path_free_evidence(self):
        calls = []
        def fake_run(command, workspace, environment, *, capture=False):
            calls.append(command)
            self.assertNotEqual(workspace, ROOT)
            if capture:
                return json.dumps(helper.IDENTITY)
            report = Path(command[command.index("--report") + 1])
            report.write_text(json.dumps(self.report()), encoding="utf-8")
            if "--dry-run" not in command:
                self.assertTrue(self.output.is_dir())
        with patch.object(helper, "run_checked", side_effect=fake_run), patch.object(helper, "validate_installed", return_value=[{"path": "synthetic.py", "sha256": "a" * 64, "size": 3}]):
            state = self.prepare()
        self.assertEqual(state["status"], "CANDIDATE_RUNTIME_STAGED_FOR_AUDIT")
        self.assertEqual(len(calls), 3)
        self.assertIn("--dry-run", calls[1])
        self.assertNotIn("--dry-run", calls[2])
        self.assertNotIn(str(self.root), json.dumps(state))
        self.assertFalse(any(self.root.glob("f3a-build-*")))

    def test_resolver_mismatch_stops_before_output_and_install(self):
        calls = []
        def fake_run(command, workspace, environment, *, capture=False):
            calls.append(command)
            if capture:
                return json.dumps(helper.IDENTITY)
            report = self.report()
            report["install"].pop()
            Path(command[command.index("--report") + 1]).write_text(json.dumps(report), encoding="utf-8")
        with patch.object(helper, "run_checked", side_effect=fake_run):
            with self.assertRaisesRegex(helper.PreparationError, "DEPENDENCY_CLOSURE_MISMATCH"):
                self.prepare()
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.output.exists())
        self.assertEqual(json.loads(self.evidence.read_text(encoding="utf-8"))["status"], "PREPARATION_INCOMPLETE")

    def test_install_failure_retains_partial_output_and_refuses_retry(self):
        calls = []
        def fake_run(command, workspace, environment, *, capture=False):
            calls.append(command)
            if capture:
                return json.dumps(helper.IDENTITY)
            if "--dry-run" not in command:
                (self.output / "partial.txt").write_text("synthetic", encoding="utf-8")
                raise helper.PreparationError("BUILD_PROCESS_FAILED")
            Path(command[command.index("--report") + 1]).write_text(json.dumps(self.report()), encoding="utf-8")
        with patch.object(helper, "run_checked", side_effect=fake_run):
            with self.assertRaisesRegex(helper.PreparationError, "BUILD_PROCESS_FAILED"):
                self.prepare()
        self.assertTrue((self.output / "partial.txt").is_file())
        self.assertEqual(json.loads(self.evidence.read_text(encoding="utf-8"))["status"], "PREPARATION_INCOMPLETE")
        with self.assertRaisesRegex(helper.PreparationError, "EXISTING_OUTPUT_REFUSED"):
            self.prepare()

    def test_subprocess_error_is_bounded_and_does_not_leak(self):
        with patch.object(helper.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "private path", "private stderr")):
            with self.assertRaisesRegex(helper.PreparationError, "^BUILD_PROCESS_FAILED$"):
                helper.run_checked([str(self.python)], self.root, {})

    def test_installed_metadata_extra_rejects(self):
        class Distribution:
            def __init__(self, name, version):
                self.metadata, self.version = {"Name": name}, version
        distributions = [Distribution(k, v) for k, v in helper.APPROVED.items()] + [Distribution("unapproved", "1")]
        with patch.object(helper.importlib.metadata, "distributions", return_value=distributions):
            with self.assertRaisesRegex(helper.PreparationError, "INSTALLED_CLOSURE_MISMATCH"):
                helper.validate_installed(self.root, self.selected)

    def test_direct_url_and_unapproved_dependency_refused_before_pip(self):
        item = self.selected["cffi"]
        for requirement, code in (("pycparser @ https://example.invalid/unapproved.whl", "DIRECT_URL_DEPENDENCY_REFUSED"),
                                  ("unapproved>=1", "DEPENDENCY_CLOSURE_MISMATCH"),
                                  ("pycparser>999", "DEPENDENCY_CLOSURE_MISMATCH")):
            with self.subTest(requirement=requirement):
                with zipfile.ZipFile(self.wheels / item["filename"], "w") as archive:
                    archive.writestr(f"cffi-{item['version']}.dist-info/METADATA",
                                     f"Metadata-Version: 2.4\nName: cffi\nVersion: {item['version']}\nRequires-Dist: {requirement}\n")
                with self.assertRaisesRegex(helper.PreparationError, code):
                    helper.validate_wheel_metadata(self.wheels, self.selected)

    def test_optional_extra_is_not_added(self):
        item = self.selected["cffi"]
        with zipfile.ZipFile(self.wheels / item["filename"], "w") as archive:
            archive.writestr(f"cffi-{item['version']}.dist-info/METADATA",
                             f'Metadata-Version: 2.4\nName: cffi\nVersion: {item["version"]}\nRequires-Dist: unapproved; extra == "test"\n')
        helper.validate_wheel_metadata(self.wheels, self.selected)

    def test_installed_hook_and_bytecode_refused(self):
        class Distribution:
            def __init__(self, name, version):
                self.metadata, self.version = {"Name": name}, version
        distributions = [Distribution(k, v) for k, v in helper.APPROVED.items()]
        target = self.root / "installed"
        target.mkdir()
        for filename in ("evil.pth", "sitecustomize.py", "cached.pyc"):
            path = target / filename
            path.write_bytes(b"synthetic")
            with patch.object(helper.importlib.metadata, "distributions", return_value=distributions):
                with self.assertRaisesRegex(helper.PreparationError, "UNEXPECTED_INSTALLED_FILE"):
                    helper.validate_installed(target, self.selected)
            path.unlink()


if __name__ == "__main__":
    unittest.main()
