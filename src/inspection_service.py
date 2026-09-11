"""Read-only inspection and normalization for PNG/JPEG rights signals."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Any, Callable

from PIL import Image
from trustmark import TrustMark
from runtime_paths import RuntimePaths, resolve_runtime_paths
from trustmark_model_manager import ModelPreparationError, create_trustmark


_RUNTIME_PATHS = resolve_runtime_paths()
if _RUNTIME_PATHS.development_scripts is not None and str(_RUNTIME_PATHS.development_scripts) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_PATHS.development_scripts))

from creator_verify import verify_contract


INSPECTION_CONTRACT_VERSION = "1.0"


@dataclass(frozen=True)
class TrustMarkObservation:
    present: bool | None
    schema: int | None
    payload_length: int | None
    decode_succeeded: bool
    error_code: str | None = None

    @property
    def valid_identifier(self) -> bool:
        return (
            self.present is True
            and self.decode_succeeded
            and self.schema == 2
            and self.payload_length == 68
        )


@dataclass(frozen=True)
class InspectionResult:
    contract_version: str
    source_path: str
    input_format: str | None
    ai_training_use: str
    ai_inference_use: str
    rights_status: str
    cawg_status: str
    c2pa_status: str
    trustmark_status: str
    trustmark_payload_status: str
    integrity_status: str
    signature_status: str
    durable_recovery: str
    warnings: tuple[str, ...]
    technical_details: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))


class LocalTrustMarkProbe:
    """Observe the TrustMark signal after the runtime models are prepared."""

    def __init__(
        self,
        decoder: TrustMark | None = None,
        factory: Callable[[], TrustMark] = create_trustmark,
    ) -> None:
        self._decoder = decoder
        self._factory = factory

    def _get_decoder(self) -> TrustMark:
        if self._decoder is None:
            candidate = self._factory()
            self._decoder = candidate
        return self._decoder

    def __call__(self, input_path: Path) -> TrustMarkObservation:
        try:
            with Image.open(input_path) as image:
                image.load()
                stego = image.convert("RGB")
            payload, present, schema_raw = self._get_decoder().decode(
                stego, MODE="binary", DETECTFIRST=False, ROTATION=False
            )
            if not present:
                return TrustMarkObservation(False, None, None, True)
            try:
                schema = int(schema_raw)
            except (TypeError, ValueError):
                schema = None
            payload_length = len(payload) if isinstance(payload, str) else None
            return TrustMarkObservation(True, schema, payload_length, True)
        except ModelPreparationError:
            raise
        except Exception:
            # Do not expose decoder exceptions, paths, model internals, or other
            # raw values through the user-facing inspection result.
            return TrustMarkObservation(None, None, None, False, "TRUSTMARK_DECODE_ERROR")


def _safe_technical_details(contract: dict[str, Any], observation: TrustMarkObservation) -> dict[str, Any]:
    """Whitelist technical fields; never forward arbitrary verifier output."""

    input_data = contract.get("input") if isinstance(contract.get("input"), dict) else {}
    dimensions = input_data.get("dimensions") if isinstance(input_data.get("dimensions"), dict) else {}
    diagnostics = contract.get("diagnostics") if isinstance(contract.get("diagnostics"), list) else []
    safe_diagnostics = [
        {"code": item.get("code"), "status": item.get("status")}
        for item in diagnostics
        if isinstance(item, dict)
        and isinstance(item.get("code"), str)
        and item.get("status") in {"pass", "fail", "info"}
    ]
    contract_rights = contract.get("rights") if isinstance(contract.get("rights"), dict) else {}
    contract_c2pa = contract.get("c2pa") if isinstance(contract.get("c2pa"), dict) else {}
    contract_binding = contract.get("softBinding") if isinstance(contract.get("softBinding"), dict) else {}
    contract_signature = contract.get("signature") if isinstance(contract.get("signature"), dict) else {}
    return {
        "verifierContractVersion": contract.get("contractVersion"),
        "verifierResult": contract.get("result"),
        "reasonCode": contract.get("reasonCode"),
        "input": {
            "format": input_data.get("format"),
            "sha256": input_data.get("sha256"),
            "dimensions": {"width": dimensions.get("width"), "height": dimensions.get("height")},
        },
        "rights": {
            "preset": contract_rights.get("preset"),
            "verified": contract_rights.get("verified"),
        },
        "c2pa": {
            "claimPresent": contract_c2pa.get("claimPresent"),
            "assertionDigestsMatch": contract_c2pa.get("assertionDigestsMatch"),
        },
        "trustmark": {
            "present": observation.present,
            "schema": observation.schema,
            "payloadLength": observation.payload_length,
            "decodeSucceeded": observation.decode_succeeded,
            "payloadValueExposed": False,
        },
        "softBinding": {
            "present": contract_binding.get("present"),
            "algorithm": contract_binding.get("algorithm"),
            "match": contract_binding.get("match"),
        },
        "signature": {
            "present": contract_signature.get("present"),
            "valid": contract_signature.get("valid"),
            "trustValidated": contract_signature.get("trustValidated"),
        },
        "diagnostics": safe_diagnostics,
    }


def normalize_inspection(
    contract: dict[str, Any], observation: TrustMarkObservation
) -> InspectionResult:
    """Map the strict verifier contract to user-facing, non-conflated states."""

    result = contract.get("result")
    reason = contract.get("reasonCode")
    input_data = contract.get("input") if isinstance(contract.get("input"), dict) else {}
    rights = contract.get("rights") if isinstance(contract.get("rights"), dict) else {}
    c2pa = contract.get("c2pa") if isinstance(contract.get("c2pa"), dict) else {}
    signature = contract.get("signature") if isinstance(contract.get("signature"), dict) else {}
    warnings: list[str] = []

    if result == "PASS":
        c2pa_status = "DETECTED"
    elif reason == "C2PA_CLAIM_MISSING":
        c2pa_status = "NOT_DETECTED"
    elif result == "FAIL_C2PA":
        c2pa_status = "VERIFICATION_FAILED"
    elif result in {"FAIL_TRUSTMARK", "FAIL_SOFT_BINDING", "FAIL_SIGNATURE"} or c2pa.get("claimPresent"):
        c2pa_status = "DETECTED"
    else:
        c2pa_status = "UNKNOWN"

    if result == "PASS" and rights.get("verified") is True:
        rights_status = "DETECTED"
    elif reason in {"C2PA_CLAIM_MISSING", "C2PA_RIGHTS_ASSERTION_MISSING"}:
        rights_status = "NOT_DETECTED"
    elif result in {"FAIL_SIGNATURE", "FAIL_TRUSTMARK"} or reason == "C2PA_ASSET_DATA_HASH_MISMATCH":
        rights_status = "DETECTED"
    elif c2pa_status in {"DETECTED", "VERIFICATION_FAILED"}:
        rights_status = "PARTIAL"
    else:
        rights_status = "NOT_DETECTED"

    if rights_status == "DETECTED":
        ai_training_use = ai_inference_use = "NOT_WANTED"
    elif rights_status == "NOT_DETECTED":
        ai_training_use = ai_inference_use = "NO_PERMISSION_INFO"
    else:
        ai_training_use = ai_inference_use = "INDETERMINATE"

    if result == "PASS" and rights.get("verified") is True:
        cawg_status = "DETECTED"
    elif reason == "C2PA_RIGHTS_ASSERTION_MISSING":
        cawg_status = "NOT_DETECTED"
    elif reason == "C2PA_RIGHTS_VALUE_MISMATCH":
        cawg_status = "VERIFICATION_FAILED"
    elif rights_status == "DETECTED":
        cawg_status = "DETECTED"
    elif c2pa_status == "NOT_DETECTED":
        cawg_status = "UNKNOWN"
    else:
        cawg_status = "INDETERMINATE"

    if reason in {"TRUSTMARK_SCHEMA_MISMATCH", "TRUSTMARK_PAYLOAD_LENGTH_MISMATCH"}:
        trustmark_status = "DECODE_FAILED"
        payload_status = "INVALID"
    elif reason == "TRUSTMARK_PAYLOAD_MISMATCH":
        trustmark_status = "DETECTED"
        payload_status = "MISMATCH"
    elif reason == "TRUSTMARK_NOT_DETECTED" or observation.present is False:
        trustmark_status = "NOT_DETECTED"
        payload_status = "NOT_AVAILABLE"
    elif observation.decode_succeeded is False:
        trustmark_status = "DECODE_FAILED"
        payload_status = "UNKNOWN"
    elif observation.present is True:
        trustmark_status = "DETECTED" if observation.valid_identifier else "DECODE_FAILED"
        if result == "PASS":
            payload_status = "MATCH"
        elif observation.valid_identifier:
            payload_status = "VALID_UNBOUND"
        else:
            payload_status = "INVALID"
    else:
        trustmark_status = "UNKNOWN"
        payload_status = "UNKNOWN"

    if result == "PASS":
        integrity_status = "OK"
    elif c2pa_status == "NOT_DETECTED":
        integrity_status = "UNKNOWN"
    elif result == "FAIL_WRITE":
        integrity_status = "UNKNOWN"
    else:
        integrity_status = "VERIFICATION_FAILED"

    if signature.get("valid") is True and signature.get("trustValidated") is True:
        signature_status = "TRUSTED"
    elif signature.get("valid") is True:
        signature_status = "PREVIEW"
    else:
        signature_status = "INDETERMINATE"

    if c2pa_status == "NOT_DETECTED" and observation.valid_identifier:
        durable_recovery = "IDENTIFIER_RECOVERED"
        warnings.append("C2PAメタデータは見つかりませんでしたが、TrustMarkの識別情報を検出しました。権利条件は回復できません。")
    else:
        durable_recovery = "NONE"

    if c2pa_status == "VERIFICATION_FAILED":
        warnings.append("C2PAメタデータを検出しましたが、検証に失敗しました。")
    if reason == "C2PA_RIGHTS_ASSERTION_MISSING":
        warnings.append("CAWG Rights assertionは見つかりませんでした。")
    if reason == "TRUSTMARK_PAYLOAD_MISMATCH":
        warnings.append("TrustMark payloadがC2PA内の期待値と一致しません。")
    if trustmark_status == "DECODE_FAILED":
        warnings.append("TrustMarkを検出できないか、payloadを正常にdecodeできませんでした。")

    return InspectionResult(
        contract_version=INSPECTION_CONTRACT_VERSION,
        source_path=str(input_data.get("path") or ""),
        input_format=input_data.get("format"),
        ai_training_use=ai_training_use,
        ai_inference_use=ai_inference_use,
        rights_status=rights_status,
        cawg_status=cawg_status,
        c2pa_status=c2pa_status,
        trustmark_status=trustmark_status,
        trustmark_payload_status=payload_status,
        integrity_status=integrity_status,
        signature_status=signature_status,
        durable_recovery=durable_recovery,
        warnings=tuple(dict.fromkeys(warnings)),
        technical_details=_safe_technical_details(contract, observation),
    )


class InspectionService:
    """Run the existing verifier and normalize its result for the GUI."""

    def __init__(
        self,
        *,
        c2patool: Path | None = None,
        verifier: Callable[[Path, Path, Path], dict[str, Any]] = verify_contract,
        trustmark_probe: Callable[[Path], TrustMarkObservation] | None = None,
        settings_path: Path | None = None,
        runtime_paths: RuntimePaths | None = None,
    ) -> None:
        paths = runtime_paths or resolve_runtime_paths()
        self.c2patool = Path(c2patool) if c2patool is not None else paths.c2patool
        self.verifier = verifier
        self.trustmark_probe = trustmark_probe or LocalTrustMarkProbe()
        self.settings_path = Path(settings_path) if settings_path is not None else paths.verifier_settings

    def inspect(self, input_path: Path) -> InspectionResult:
        resolved = input_path.resolve()
        contract = self.verifier(resolved, self.c2patool, self.settings_path)

        trustmark = contract.get("trustmark") if isinstance(contract.get("trustmark"), dict) else {}
        if trustmark.get("present") is None:
            observation = self.trustmark_probe(resolved)
        else:
            observation = TrustMarkObservation(
                present=trustmark.get("present"),
                schema=trustmark.get("schema"),
                payload_length=trustmark.get("payloadLength"),
                decode_succeeded=trustmark.get("present") is not None,
            )
        return normalize_inspection(contract, observation)
