from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
SCRIPTS = PROJECT / "scripts"
for directory in (SRC, SCRIPTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from creator_e2e import run_pipeline
from creator_service import CreatorRequest, CreatorService
from creator_verify import verify_contract
from runtime_paths import (
    C2PATOOL_SHA256,
    RuntimeComponentError,
    RuntimePaths,
    require_c2patool,
    resolve_runtime_paths,
    sha256_file,
)
from trustmark_model_manager import TrustMarkModelManager


C2PATOOL = PROJECT / "tools/c2patool-0.26.60/c2patool/c2patool.exe"
CLEAN_FIXTURE = PROJECT / "testdata/e2e/clean_fixture.png"


class RuntimePortabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = PROJECT / "tests/.runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        self.root = runtime / f"portability-{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root)
        runtime = self.root.parent
        if runtime.exists() and not any(runtime.iterdir()):
            runtime.rmdir()

    def test_development_paths_do_not_depend_on_cwd(self) -> None:
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            paths = resolve_runtime_paths(local_app_data=self.root / "日本語ユーザー")
        finally:
            os.chdir(previous)
        self.assertEqual(PROJECT, paths.resource_root)
        self.assertEqual(C2PATOOL, paths.c2patool)
        self.assertEqual(PROJECT / "config/trustmark-models-v0.9.0.json", paths.trustmark_manifest)
        self.assertEqual(PROJECT / "config/verifier-settings.json", paths.verifier_settings)
        self.assertEqual(self.root / "日本語ユーザー/Shirushi/logs/creator_service.log", paths.creator_log)

    def test_future_frozen_paths_use_meipass_and_executable(self) -> None:
        install = self.root / "配布 フォルダー"
        resources = install / "runtime"
        paths = resolve_runtime_paths(
            frozen=True,
            executable=install / "Shirushi.exe",
            resource_root=resources,
            local_app_data=self.root / "Local App Data",
        )
        self.assertEqual(install.resolve(), paths.installation_root)
        self.assertEqual(resources.resolve(), paths.resource_root)
        self.assertEqual(resources.resolve() / "c2patool.exe", paths.c2patool)
        self.assertEqual(resources.resolve() / "gui/app_icon.png", paths.app_icon)
        self.assertEqual(resources.resolve() / "gui/app_icon.ico", paths.app_icon_ico)
        self.assertIsNone(paths.development_scripts)

    def test_frozen_defaults_read_sys_executable_and_meipass(self) -> None:
        install = self.root / "Installed App"
        resources = install / "runtime"
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "_MEIPASS", str(resources), create=True),
            mock.patch.object(sys, "executable", str(install / "Shirushi.exe")),
        ):
            paths = resolve_runtime_paths(local_app_data=self.root / "Local App Data")
        self.assertTrue(paths.frozen)
        self.assertEqual(install.resolve(), paths.installation_root)
        self.assertEqual(resources.resolve(), paths.resource_root)

    def test_known_good_c2patool_matches_pinned_sha(self) -> None:
        self.assertEqual(C2PATOOL_SHA256, sha256_file(C2PATOOL))
        self.assertEqual(C2PATOOL.resolve(), require_c2patool(C2PATOOL))

    def test_c2patool_executes_from_unicode_path_with_spaces(self) -> None:
        copied = self.root / "日本語 component folder" / "c2patool.exe"
        copied.parent.mkdir(parents=True)
        shutil.copyfile(C2PATOOL, copied)
        executable = require_c2patool(copied)
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
        )
        self.assertEqual(0, completed.returncode)
        self.assertEqual("c2patool 0.26.60", completed.stdout.strip())

    def test_missing_and_mismatched_c2patool_fail_closed(self) -> None:
        with self.assertRaises(RuntimeComponentError) as missing:
            require_c2patool(self.root / "missing.exe")
        self.assertEqual("C2PATOOL_MISSING", missing.exception.code)
        mismatch = self.root / "c2patool.exe"
        mismatch.write_bytes(b"not the pinned component")
        with self.assertRaises(RuntimeComponentError) as altered:
            require_c2patool(mismatch)
        self.assertEqual("C2PATOOL_INTEGRITY_FAILED", altered.exception.code)

    def test_pipeline_maps_missing_c2patool_to_stable_error(self) -> None:
        result_path = self.root / "result.json"
        result = run_pipeline(
            CLEAN_FIXTURE,
            self.root / "output.png",
            self.root / "missing.exe",
            result_path,
            self.root / "pipeline.log",
        )
        self.assertEqual("FAIL_C2PA", result["status"])
        self.assertEqual("C2PATOOL_MISSING", result["error_code"])
        self.assertEqual(result, json.loads(result_path.read_text(encoding="utf-8")))

    def test_verifier_maps_missing_and_mismatched_c2patool_to_stable_errors(self) -> None:
        settings = self.root / "settings.json"
        settings.write_text("{}\n", encoding="utf-8")
        missing = verify_contract(CLEAN_FIXTURE, self.root / "missing.exe", settings)
        self.assertEqual("FAIL_C2PA", missing["result"])
        self.assertEqual("C2PATOOL_MISSING", missing["reasonCode"])
        mismatch_path = self.root / "c2patool.exe"
        mismatch_path.write_bytes(b"altered")
        mismatch = verify_contract(CLEAN_FIXTURE, mismatch_path, settings)
        self.assertEqual("FAIL_C2PA", mismatch["result"])
        self.assertEqual("C2PATOOL_INTEGRITY_FAILED", mismatch["reasonCode"])

    def test_logging_failure_does_not_stop_creation_or_fall_back(self) -> None:
        blocker = self.root / "not-a-directory"
        blocker.write_text("block", encoding="utf-8")
        requested_log = blocker / "creator_service.log"
        output = self.root / "output.png"

        def core(_input, staged, *_args):
            staged.write_bytes(b"created")
            return {"status": "PASS"}

        def verifier(path, *_args):
            return {"contractVersion": "1.0", "result": "PASS", "input": {"path": str(path)}}

        service = CreatorService(
            c2patool=C2PATOOL,
            log_path=requested_log,
            core_runner=core,
            verifier=verifier,
        )
        result = service.create(CreatorRequest(CLEAN_FIXTURE, output))
        self.assertEqual("SUCCESS", result["status"])
        self.assertTrue(output.is_file())
        self.assertFalse(requested_log.exists())

    def test_default_paths_are_injectable_without_real_localappdata_writes(self) -> None:
        paths = RuntimePaths(PROJECT, PROJECT, self.root / "LocalAppData/Shirushi", False)
        service = CreatorService(runtime_paths=paths)
        self.assertEqual(paths.creator_log, service.log_path)
        manager = TrustMarkModelManager(runtime_paths=paths)
        self.assertEqual(paths.trustmark_manifest, manager.manifest_path)
        self.assertEqual(paths.user_data_root / "models/trustmark/0.9.0/P", manager.cache_directory)


if __name__ == "__main__":
    unittest.main()
