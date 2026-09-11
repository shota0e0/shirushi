"""Human-facing messages for CreatorService contract v1.0 results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResultMessage:
    heading: str
    detail: str
    developer_code: str | None
    successful: bool = False


INVALID_REQUEST_MESSAGES = {
    "INVALID_INPUT": "有効なPNGまたはJPEG画像を選択してください。",
    "OUTPUT_ALREADY_EXISTS": "同じ名前の保存先がすでに存在します。既存ファイルは上書きしていません。",
    "OUTPUT_EQUALS_INPUT": "入力画像と異なる保存先が必要です。",
    "OUTPUT_NOT_PNG": "保存先は入力と同じPNGまたはJPEG形式である必要があります。",
}

MODEL_PREPARATION_MESSAGES = {
    "MODEL_NETWORK_UNAVAILABLE": (
        "初回準備を完了できませんでした",
        "インターネット接続を確認し、同じ操作をもう一度お試しください。必要なコンポーネントの取得時に画像ファイル自体は送信していません。",
    ),
    "MODEL_CACHE_NOT_WRITABLE": (
        "初回準備を完了できませんでした",
        "保存領域へ書き込めませんでした。同じ操作をもう一度お試しください。",
    ),
    "MODEL_STORAGE_FULL": (
        "初回準備を完了できませんでした",
        "空き容量を確認し、同じ操作をもう一度お試しください。",
    ),
    "MODEL_INTEGRITY_FAILED": (
        "初回準備を完了できませんでした",
        "必要なコンポーネントを確認できませんでした。同じ操作をもう一度お試しください。",
    ),
    "MODEL_LOAD_FAILED": (
        "初回準備を完了できませんでした",
        "必要なコンポーネントを読み込めませんでした。同じ操作をもう一度お試しください。",
    ),
    "MODEL_PREPARATION_FAILED": (
        "初回準備を完了できませんでした",
        "同じ操作をもう一度お試しください。",
    ),
}


def message_for_result(result: dict[str, Any]) -> ResultMessage:
    """Map only stable CreatorService v1.0 fields to user-facing copy."""
    status = result.get("status")
    error_code = result.get("errorCode")
    if error_code in MODEL_PREPARATION_MESSAGES:
        heading, detail = MODEL_PREPARATION_MESSAGES[error_code]
        return ResultMessage(heading, detail, error_code)
    if status == "SUCCESS":
        return ResultMessage(
            "✔ しるしを付けました",
            "保存と確認が正常に完了しました。",
            None,
            True,
        )
    if status == "INVALID_REQUEST":
        detail = INVALID_REQUEST_MESSAGES.get(error_code, "入力画像または保存先を確認してください。")
        return ResultMessage("処理を開始できませんでした", detail, error_code)
    if status == "WRITE_FAILED":
        return ResultMessage("画像を保存できませんでした", "保存先を確認してください。", error_code)
    if status == "VERIFICATION_FAILED":
        return ResultMessage(
            "権利情報の検証に失敗しました",
            "検証に失敗した画像は、保存先には出力していません。",
            error_code,
        )
    if status == "CANCELLED":
        return ResultMessage("処理をキャンセルしました", "未完了の画像は保存していません。", error_code)
    return ResultMessage("予期しないエラーが発生しました", "時間をおいて、もう一度お試しください。", error_code or "INTERNAL_ERROR")
