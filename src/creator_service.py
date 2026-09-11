"""Minimal application-service boundary for the PNG/JPEG rights-signal creator."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import traceback
import uuid
from typing import Any, Callable

from runtime_paths import C2PATOOL_ERROR_CODES, RuntimePaths, resolve_runtime_paths


_RUNTIME_PATHS = resolve_runtime_paths()
if _RUNTIME_PATHS.development_scripts is not None and str(_RUNTIME_PATHS.development_scripts) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_PATHS.development_scripts))

from creator_e2e import PipelineFailure, SUPPORTED_EXTENSIONS, run_pipeline, validate_input_image
from creator_verify import CONTRACT_VERSION as VERIFIER_CONTRACT_VERSION
from creator_verify import verify_contract
from trustmark_model_manager import MODEL_ERROR_CODES


SERVICE_CONTRACT_VERSION = "1.0"
SERVICE_STATUS_ENUM = (
    "SUCCESS",
    "CANCELLED",
    "INVALID_REQUEST",
    "WRITE_FAILED",
    "VERIFICATION_FAILED",
    "INTERNAL_ERROR",
)
SERVICE_ERROR_CODE_ENUM = (
    "INVALID_INPUT",
    "OUTPUT_ALREADY_EXISTS",
    "OUTPUT_EQUALS_INPUT",
    "OUTPUT_NOT_PNG",
    "OUTPUT_PREPARATION_FAILED",
    "GENERATION_FAILED",
    "ATOMIC_COMMIT_FAILED",
    "POST_WRITE_VERIFICATION_FAILED",
    "VERIFIER_CONTRACT_MISMATCH",
    "INPUT_CHANGED",
    "TEMP_CLEANUP_FAILED",
    "INTERNAL_ERROR",
    "CANCELLED_BY_REQUEST",
    *MODEL_ERROR_CODES,
    *C2PATOOL_ERROR_CODES,
)


@dataclass(frozen=True)
class CreatorRequest:
    input_path: Path
    output_path: Path

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "CreatorRequest":
        if set(value) != {"inputPath", "outputPath"}:
            raise ValueError("request must contain exactly inputPath and outputPath")
        if not isinstance(value["inputPath"], str) or not isinstance(value["outputPath"], str):
            raise ValueError("inputPath and outputPath must be strings")
        return cls(Path(value["inputPath"]), Path(value["outputPath"]))


class ServiceFailure(RuntimeError):
    def __init__(self, status: str, code: str, message: str):
        super().__init__(message)
        if status not in SERVICE_STATUS_ENUM or status == "SUCCESS":
            raise ValueError(f"invalid service failure status: {status}")
        if code not in SERVICE_ERROR_CODE_ENUM:
            raise ValueError(f"invalid service error code: {code}")
        self.status = status
        self.code = code
        self.user_message = message


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _service_result(
    request: CreatorRequest,
    status: str,
    message: str,
    *,
    input_sha256: str | None = None,
    output_sha256: str | None = None,
    verification: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    if status not in SERVICE_STATUS_ENUM:
        raise ValueError(f"invalid service status: {status}")
    if error_code is not None and error_code not in SERVICE_ERROR_CODE_ENUM:
        raise ValueError(f"invalid service error code: {error_code}")
    input_path = str(request.input_path.resolve())
    output_path = str(request.output_path.resolve())
    canonical_output = (
        {"path": output_path, "sha256": output_sha256}
        if status == "SUCCESS" and output_sha256 is not None
        else None
    )
    return {
        "serviceContractVersion": SERVICE_CONTRACT_VERSION,
        "status": status,
        "errorCode": error_code,
        "message": message,
        "input": {"path": input_path, "sha256": input_sha256},
        "output": canonical_output,
        "outputPath": output_path,
        "inputSha256": input_sha256,
        "outputSha256": output_sha256,
        "verification": verification,
        "error": None if error_code is None else {"code": error_code, "message": message},
    }


class CreatorService:
    """Synchronous, cooperative-cancellation application service."""

    def __init__(
        self,
        *,
        c2patool: Path | None = None,
        log_path: Path | None = None,
        runtime_paths: RuntimePaths | None = None,
        core_runner: Callable[..., dict[str, Any]] = run_pipeline,
        verifier: Callable[..., dict[str, Any]] = verify_contract,
    ) -> None:
        paths = runtime_paths or resolve_runtime_paths()
        self.c2patool = Path(c2patool) if c2patool is not None else paths.c2patool
        self.log_path = Path(log_path) if log_path is not None else paths.creator_log
        self.core_runner = core_runner
        self.verifier = verifier

    def _logger(self) -> logging.Logger:
        logger = logging.getLogger(f"creator_service.{uuid.uuid4().hex}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = logging.FileHandler(self.log_path, mode="a", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        except OSError:
            # Logging is best-effort. Never fall back to CWD or the read-only
            # application resource directory.
            handler = logging.NullHandler()
        logger.addHandler(handler)
        return logger

    @staticmethod
    def _close_logger(logger: logging.Logger) -> None:
        for handler in list(logger.handlers):
            handler.flush()
            handler.close()
            logger.removeHandler(handler)

    @staticmethod
    def _cancel_if_requested(cancel_check: Callable[[], bool] | None, checkpoint: str) -> None:
        if cancel_check is not None and bool(cancel_check()):
            raise ServiceFailure("CANCELLED", "CANCELLED_BY_REQUEST", f"Creation cancelled at checkpoint: {checkpoint}.")

    @staticmethod
    def _require_input_unchanged(input_path: Path, expected_sha256: str) -> None:
        if not input_path.is_file() or sha256_file(input_path) != expected_sha256:
            raise ServiceFailure("INTERNAL_ERROR", "INPUT_CHANGED", "Input changed during processing.")

    def create(
        self,
        request: CreatorRequest,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        input_path = request.input_path.resolve()
        output_path = request.output_path.resolve()
        normalized_request = CreatorRequest(input_path, output_path)
        input_sha256: str | None = None
        verification: dict[str, Any] | None = None
        staging_dir: Path | None = None
        committed = False
        result: dict[str, Any] | None = None
        logger = self._logger()
        logger.info("request start input=%s output=%s", input_path, output_path)
        try:
            self._cancel_if_requested(cancel_check, "before_processing")
            if input_path == output_path:
                raise ServiceFailure("INVALID_REQUEST", "OUTPUT_EQUALS_INPUT", "Output path must differ from input path.")
            if output_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                raise ServiceFailure("INVALID_REQUEST", "OUTPUT_NOT_PNG", "Output path must use .png, .jpg, or .jpeg.")
            if output_path.suffix.lower() != input_path.suffix.lower():
                raise ServiceFailure("INVALID_REQUEST", "OUTPUT_NOT_PNG", "Output extension must match input extension.")
            if output_path.exists():
                raise ServiceFailure("INVALID_REQUEST", "OUTPUT_ALREADY_EXISTS", "Output already exists; overwrite is not allowed.")
            try:
                _, input_sha256, _ = validate_input_image(input_path)
            except PipelineFailure as exc:
                raise ServiceFailure("INVALID_REQUEST", "INVALID_INPUT", "Input must be an existing valid PNG or JPEG.") from exc
            logger.info("input validated sha256=%s", input_sha256)
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                if not output_path.parent.is_dir():
                    raise OSError("output parent is not a directory")
                staging_dir = output_path.parent / f".creator-service-{uuid.uuid4().hex}"
                staging_dir.mkdir(mode=0o755)
            except Exception as exc:
                raise ServiceFailure("WRITE_FAILED", "OUTPUT_PREPARATION_FAILED", "Unable to prepare the output location.") from exc

            staged_output = staging_dir / f"staged-output{output_path.suffix}"
            core_result_path = staging_dir / "core-result.json"
            core_log_path = staging_dir / "core.log"
            verifier_settings = staging_dir / "verifier-settings.json"
            verifier_settings.write_text("{}\n", encoding="utf-8")
            logger.info("staging prepared path=%s", staging_dir)

            self._cancel_if_requested(cancel_check, "before_generation")
            core_result = self.core_runner(
                input_path,
                staged_output,
                self.c2patool,
                core_result_path,
                core_log_path,
            )
            self._require_input_unchanged(input_path, input_sha256)
            self._cancel_if_requested(cancel_check, "after_generation")
            if core_result.get("status") != "PASS" or not staged_output.is_file():
                core_status = core_result.get("status")
                status = "WRITE_FAILED" if core_status == "FAIL_WRITE" else "VERIFICATION_FAILED"
                model_error = core_result.get("error_code")
                error_code = (
                    model_error
                    if model_error in (*MODEL_ERROR_CODES, *C2PATOOL_ERROR_CODES)
                    else "GENERATION_FAILED"
                )
                raise ServiceFailure(status, error_code, "Rights-signal generation did not complete successfully.")
            logger.info("generation stage complete core_status=PASS")

            self._cancel_if_requested(cancel_check, "before_verification")
            verification = self.verifier(staged_output, self.c2patool, verifier_settings)
            if verification.get("contractVersion") != VERIFIER_CONTRACT_VERSION:
                raise ServiceFailure("INTERNAL_ERROR", "VERIFIER_CONTRACT_MISMATCH", "Verifier contract version mismatch.")
            if verification.get("result") != "PASS":
                raise ServiceFailure("VERIFICATION_FAILED", "POST_WRITE_VERIFICATION_FAILED", "Generated output failed verification.")
            logger.info("post-write verification result=PASS contractVersion=%s", verification["contractVersion"])

            self._require_input_unchanged(input_path, input_sha256)
            self._cancel_if_requested(cancel_check, "before_final_commit")
            if output_path.exists():
                raise ServiceFailure("INVALID_REQUEST", "OUTPUT_ALREADY_EXISTS", "Output appeared during processing; overwrite is not allowed.")
            output_sha256 = sha256_file(staged_output)
            try:
                os.rename(staged_output, output_path)
                committed = True
            except FileExistsError as exc:
                raise ServiceFailure("INVALID_REQUEST", "OUTPUT_ALREADY_EXISTS", "Output appeared during processing; overwrite is not allowed.") from exc
            except OSError as exc:
                raise ServiceFailure("WRITE_FAILED", "ATOMIC_COMMIT_FAILED", "Unable to commit the verified output.") from exc
            # Atomic rename preserves verified bytes; expose the durable final
            # path rather than the now-transient staging path in the contract.
            verification["input"]["path"] = str(output_path)
            logger.info("final commit complete output=%s sha256=%s", output_path, output_sha256)
            result = _service_result(
                normalized_request,
                "SUCCESS",
                "Creation completed successfully.",
                input_sha256=input_sha256,
                output_sha256=output_sha256,
                verification=verification,
            )
            logger.info("request result=SUCCESS")
        except ServiceFailure as exc:
            logger.warning("request result=%s code=%s message=%s", exc.status, exc.code, exc.user_message)
            result = _service_result(
                normalized_request,
                exc.status,
                exc.user_message,
                input_sha256=input_sha256,
                verification=verification,
                error_code=exc.code,
            )
        except Exception as exc:
            logger.error("request result=INTERNAL_ERROR\n%s", traceback.format_exc())
            result = _service_result(
                normalized_request,
                "INTERNAL_ERROR",
                "An unexpected internal error occurred.",
                input_sha256=input_sha256,
                verification=verification,
                error_code="INTERNAL_ERROR",
            )
        finally:
            if staging_dir is not None and staging_dir.exists():
                try:
                    shutil.rmtree(staging_dir)
                except Exception:
                    logger.error("staging cleanup failed\n%s", traceback.format_exc())
                    # A cleanup failure invalidates service SUCCESS. Remove the
                    # just-created final output so a failed operation never
                    # leaves a file presented as a successful product.
                    if committed and output_path.exists():
                        try:
                            output_path.unlink()
                            committed = False
                        except Exception:
                            logger.error("rollback of committed output failed\n%s", traceback.format_exc())
                    result = _service_result(
                        normalized_request,
                        "INTERNAL_ERROR",
                        "Temporary output cleanup failed.",
                        input_sha256=input_sha256,
                        verification=verification,
                        error_code="TEMP_CLEANUP_FAILED",
                    )
            self._close_logger(logger)
        if result is None:
            raise RuntimeError("service did not produce a result")
        return result


def result_to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
