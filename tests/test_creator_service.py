from __future__ import annotations

from contextlib import contextmanager
import hashlib
from functools import partial
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from creator_service import (
    CreatorRequest,
    CreatorService,
    SERVICE_CONTRACT_VERSION,
    SERVICE_STATUS_ENUM,
)
from creator_e2e import run_pipeline
from creator_verify import verify_contract
from tests.trustmark_test_support import installed_model_factory


CLEAN_FIXTURE = PROJECT / "testdata/e2e/clean_fixture.png"
C2PATOOL = PROJECT / "tools/c2patool-0.26.60/c2patool/c2patool.exe"
PYTHON = PROJECT / ".venv-py312/Scripts/python.exe"
CLI = PROJECT / "scripts/creator_create.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


@contextmanager
def workdir(prefix: str):
    runtime = PROJECT / "tests/.runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    root = runtime / f"{prefix}-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root)
        if runtime.exists() and not any(runtime.iterdir()):
            runtime.rmdir()


def fake_success_core(input_path, output_path, c2patool, result_path, log_path):
    output_path.write_bytes(b"staged-test-output")
    return {"status": "PASS", "output": {"sha256": sha256_file(output_path)}}


def fake_pass_verifier(input_path, c2patool, settings):
    return {
        "contractVersion": "1.0",
        "result": "PASS",
        "input": {"path": str(input_path.resolve()), "sha256": sha256_file(input_path)},
    }


class CancelOnCall:
    def __init__(self, target: int):
        self.target = target
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.calls == self.target


class CreatorServiceTests(unittest.TestCase):
    def service(self, root: Path, **kwargs) -> CreatorService:
        return CreatorService(c2patool=C2PATOOL, log_path=root / "service.log", **kwargs)

    def test_positive_actual_e2e_is_atomic_and_verified(self) -> None:
        with workdir("positive") as root:
            output = root / "created.png"
            input_before = sha256_file(CLEAN_FIXTURE)
            result = self.service(
                root,
                core_runner=partial(run_pipeline, trustmark_factory=installed_model_factory),
                verifier=partial(verify_contract, trustmark_factory=installed_model_factory),
            ).create(CreatorRequest(CLEAN_FIXTURE, output))
            self.assertEqual("SUCCESS", result["status"])
            self.assertTrue(output.is_file())
            self.assertEqual(input_before, sha256_file(CLEAN_FIXTURE))
            self.assertEqual(input_before, result["inputSha256"])
            self.assertEqual(sha256_file(output), result["outputSha256"])
            self.assertEqual("1.0", result["verification"]["contractVersion"])
            self.assertEqual("PASS", result["verification"]["result"])
            self.assertEqual(str(output.resolve()), result["verification"]["input"]["path"])
            self.assertFalse(list(root.glob(".creator-service-*")))

    def test_existing_destination_is_rejected_and_unchanged(self) -> None:
        with workdir("existing") as root:
            output = root / "existing.png"
            original = b"existing destination"
            output.write_bytes(original)
            result = self.service(root).create(CreatorRequest(CLEAN_FIXTURE, output))
            self.assertEqual("INVALID_REQUEST", result["status"])
            self.assertEqual("OUTPUT_ALREADY_EXISTS", result["error"]["code"])
            self.assertEqual(original, output.read_bytes())
            self.assertFalse(list(root.glob(".creator-service-*")))

    def test_missing_input_is_invalid_request(self) -> None:
        with workdir("missing") as root:
            output = root / "output.png"
            result = self.service(root).create(CreatorRequest(root / "missing.png", output))
            self.assertEqual("INVALID_REQUEST", result["status"])
            self.assertEqual("INVALID_INPUT", result["error"]["code"])
            self.assertFalse(output.exists())

    def test_invalid_png_is_invalid_request(self) -> None:
        with workdir("invalid") as root:
            invalid = root / "invalid.png"
            invalid.write_text("not png", encoding="utf-8")
            output = root / "output.png"
            result = self.service(root).create(CreatorRequest(invalid, output))
            self.assertEqual("INVALID_REQUEST", result["status"])
            self.assertEqual("INVALID_INPUT", result["error"]["code"])
            self.assertFalse(output.exists())

    def test_verifier_failure_never_commits_final(self) -> None:
        with workdir("verify-fail") as root:
            output = root / "output.png"

            def fail_verifier(input_path, c2patool, settings):
                return {"contractVersion": "1.0", "result": "FAIL_SIGNATURE", "reasonCode": "SIGNATURE_INVALID"}

            service = self.service(root, core_runner=fake_success_core, verifier=fail_verifier)
            result = service.create(CreatorRequest(CLEAN_FIXTURE, output))
            self.assertEqual("VERIFICATION_FAILED", result["status"])
            self.assertEqual("POST_WRITE_VERIFICATION_FAILED", result["error"]["code"])
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob(".creator-service-*")))

    def test_generation_failure_never_commits_final(self) -> None:
        with workdir("generation-fail") as root:
            output = root / "output.png"

            def failed_core(*args, **kwargs):
                return {"status": "FAIL_WRITE", "exception": "simulated"}

            result = self.service(root, core_runner=failed_core).create(CreatorRequest(CLEAN_FIXTURE, output))
            self.assertEqual("WRITE_FAILED", result["status"])
            self.assertEqual("GENERATION_FAILED", result["error"]["code"])
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob(".creator-service-*")))

    def test_model_preparation_error_code_is_preserved(self) -> None:
        with workdir("model-fail") as root:
            output = root / "output.png"
            def failed_core(*args, **kwargs):
                return {"status": "FAIL_WRITE", "error_code": "MODEL_NETWORK_UNAVAILABLE"}
            result = self.service(root, core_runner=failed_core).create(CreatorRequest(CLEAN_FIXTURE, output))
            self.assertEqual("WRITE_FAILED", result["status"])
            self.assertEqual("MODEL_NETWORK_UNAVAILABLE", result["error"]["code"])
            self.assertFalse(output.exists())

    def test_write_failure_never_creates_final(self) -> None:
        with workdir("write-fail") as root:
            blocker = root / "not-a-directory"
            blocker.write_text("blocker", encoding="utf-8")
            output = blocker / "output.png"
            result = self.service(root).create(CreatorRequest(CLEAN_FIXTURE, output))
            self.assertEqual("WRITE_FAILED", result["status"])
            self.assertEqual("OUTPUT_PREPARATION_FAILED", result["error"]["code"])
            self.assertFalse(output.exists())

    def test_cancel_before_processing(self) -> None:
        with workdir("cancel-early") as root:
            output = root / "output.png"
            result = self.service(root).create(CreatorRequest(CLEAN_FIXTURE, output), cancel_check=lambda: True)
            self.assertEqual("CANCELLED", result["status"])
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob(".creator-service-*")))

    def test_cancel_after_generation_cleans_staging(self) -> None:
        with workdir("cancel-generated") as root:
            output = root / "output.png"
            service = self.service(root, core_runner=fake_success_core, verifier=fake_pass_verifier)
            result = service.create(CreatorRequest(CLEAN_FIXTURE, output), cancel_check=CancelOnCall(3))
            self.assertEqual("CANCELLED", result["status"])
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob(".creator-service-*")))

    def test_cancel_before_final_commit_cleans_verified_staging(self) -> None:
        with workdir("cancel-commit") as root:
            output = root / "output.png"
            service = self.service(root, core_runner=fake_success_core, verifier=fake_pass_verifier)
            result = service.create(CreatorRequest(CLEAN_FIXTURE, output), cancel_check=CancelOnCall(5))
            self.assertEqual("CANCELLED", result["status"])
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob(".creator-service-*")))

    def test_internal_exception_is_normalized(self) -> None:
        with workdir("internal") as root:
            output = root / "output.png"

            def exploding_core(*args, **kwargs):
                raise RuntimeError("developer-only diagnostic")

            result = self.service(root, core_runner=exploding_core).create(CreatorRequest(CLEAN_FIXTURE, output))
            self.assertEqual("INTERNAL_ERROR", result["status"])
            self.assertEqual("INTERNAL_ERROR", result["error"]["code"])
            self.assertNotIn("developer-only", json.dumps(result))
            self.assertIn("developer-only diagnostic", (root / "service.log").read_text(encoding="utf-8"))
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob(".creator-service-*")))

    def test_request_and_result_contracts(self) -> None:
        request = CreatorRequest.from_mapping({"inputPath": "input.png", "outputPath": "output.png"})
        self.assertEqual(Path("input.png"), request.input_path)
        self.assertEqual("1.0", SERVICE_CONTRACT_VERSION)
        self.assertEqual(
            {"SUCCESS", "CANCELLED", "INVALID_REQUEST", "WRITE_FAILED", "VERIFICATION_FAILED", "INTERNAL_ERROR"},
            set(SERVICE_STATUS_ENUM),
        )

    def test_cli_adapter_json_and_exit_code(self) -> None:
        with workdir("cli") as root:
            output = root / "output.png"
            completed = subprocess.run(
                [str(PYTHON), str(CLI), "--input", str(root / "missing.png"), "--output", str(output), "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                check=False,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(2, completed.returncode)
            self.assertEqual("INVALID_REQUEST", result["status"])
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
