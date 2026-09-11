"""Human-facing labels for normalized inspection results."""

from __future__ import annotations

from dataclasses import dataclass
import json

from inspection_service import InspectionResult


USE_LABELS = {
    "NOT_WANTED": "希望しない",
    "NO_PERMISSION_INFO": "意思表示なし",
    "INDETERMINATE": "判定不能",
}
RIGHTS_LABELS = {"DETECTED": "検出", "NOT_DETECTED": "未検出", "PARTIAL": "一部のみ検出"}
CAWG_LABELS = {
    "DETECTED": "検出",
    "NOT_DETECTED": "未検出",
    "VERIFICATION_FAILED": "検証失敗",
    "INDETERMINATE": "判定不能",
    "UNKNOWN": "不明",
}
C2PA_LABELS = {"DETECTED": "検出", "NOT_DETECTED": "未検出", "VERIFICATION_FAILED": "検証失敗", "UNKNOWN": "不明"}
TRUSTMARK_LABELS = {"DETECTED": "検出", "NOT_DETECTED": "未検出", "DECODE_FAILED": "decode失敗", "UNKNOWN": "不明"}
INTEGRITY_LABELS = {"OK": "完全性を確認済み", "VERIFICATION_FAILED": "検証失敗", "UNKNOWN": "不明"}
SIGNATURE_LABELS = {
    "PREVIEW": "プレビュー",
    "TRUSTED": "署名の信頼検証済み",
    "INDETERMINATE": "判定不能",
}


@dataclass(frozen=True)
class PreflightWarning:
    heading: str
    detail: str


def preflight_warning(result: InspectionResult) -> PreflightWarning | None:
    """Return GUI flow copy for normalized existing-signal states."""
    if result.integrity_status == "OK":
        return PreflightWarning(
            "この画像にはすでにしるしが付いています。",
            "もう一度しるしを付けると、既存情報に影響する可能性があります。",
        )
    if result.c2pa_status == "NOT_DETECTED" and result.trustmark_status == "NOT_DETECTED":
        return None
    if result.c2pa_status == "NOT_DETECTED" and result.trustmark_status == "DETECTED":
        return PreflightWarning(
            "この画像にはしるしの識別情報が残っています。",
            "AI利用条件そのものは確認できません。もう一度しるしを付けると、既存情報に影響する可能性があります。",
        )
    return PreflightWarning(
        "既存のしるし情報を検出しましたが、状態を完全に確認できません。",
        "もう一度しるしを付けると、既存情報に影響する可能性があります。",
    )


def information_state(result: InspectionResult) -> str:
    if result.integrity_status == "OK":
        return "完全性を確認済み"
    if result.c2pa_status == "NOT_DETECTED" and result.trustmark_status == "NOT_DETECTED":
        return "意思表示なし"
    if result.c2pa_status == "NOT_DETECTED" and result.trustmark_status == "DETECTED":
        return "識別情報のみ"
    return "確認できません"


def inspection_primary_rows(result: InspectionResult) -> tuple[tuple[str, str], ...]:
    return (
        ("AI学習利用", USE_LABELS[result.ai_training_use]),
        ("AI生成 / 推論利用", USE_LABELS[result.ai_inference_use]),
        ("情報の状態", information_state(result)),
    )


def inspection_technical_rows(result: InspectionResult) -> tuple[tuple[str, str], ...]:
    return (
        ("権利情報", RIGHTS_LABELS[result.rights_status]),
        ("CAWG Rights", CAWG_LABELS[result.cawg_status]),
        ("C2PA", C2PA_LABELS[result.c2pa_status]),
        ("TrustMark", TRUSTMARK_LABELS[result.trustmark_status]),
        ("完全性", INTEGRITY_LABELS[result.integrity_status]),
        ("署名", SIGNATURE_LABELS[result.signature_status]),
    )


def inspection_summary_rows(result: InspectionResult) -> tuple[tuple[str, str], ...]:
    """Compatibility view for callers that still need one combined list."""

    return (*inspection_primary_rows(result), *inspection_technical_rows(result))


def inspection_heading(result: InspectionResult) -> str:
    if result.integrity_status == "OK":
        return "しるしを確認しました"
    if result.c2pa_status == "NOT_DETECTED" and result.trustmark_status == "NOT_DETECTED":
        return "しるしは見つかりませんでした"
    if result.c2pa_status == "NOT_DETECTED" and result.trustmark_status == "DETECTED":
        return "しるしの識別情報を確認しました"
    if result.integrity_status == "VERIFICATION_FAILED":
        return "しるしの情報は見つかりましたが、完全性を確認できませんでした"
    return "しるしを十分に確認できませんでした"


def inspection_explanation(result: InspectionResult) -> str:
    if result.integrity_status == "OK":
        return "この画像には、AIによる学習・生成利用を希望しない意思が記録されています。"
    if result.c2pa_status == "NOT_DETECTED" and result.trustmark_status == "NOT_DETECTED":
        return (
            "この画像から、AI利用に関する意思表示は確認できませんでした。\n"
            "これは、利用を許可または拒否していることを意味しません。"
        )
    if result.c2pa_status == "NOT_DETECTED" and result.trustmark_status == "DETECTED":
        return (
            "画像本体の権利情報は確認できませんでしたが、しるしの識別情報は残っています。\n"
            "この状態ではAI利用条件そのものは確認できません。"
        )
    if result.integrity_status == "VERIFICATION_FAILED":
        return "しるしの情報は検出されましたが、内容が変更されていないことを確認できませんでした。"
    return "画像内の情報を十分に確認できませんでした。技術情報を確認してください。"


def signature_explanation(result: InspectionResult) -> str | None:
    if result.signature_status == "PREVIEW":
        return "現在は正式な信頼済み署名ではありません。"
    return None


def technical_details_text(result: InspectionResult) -> str:
    details = {
        "inspectionContractVersion": result.contract_version,
        "trustmarkPayloadStatus": result.trustmark_payload_status,
        "durableRecovery": result.durable_recovery,
        **result.technical_details,
    }
    return json.dumps(details, ensure_ascii=False, indent=2)
