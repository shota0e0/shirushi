"""Small UI-copy catalog for the Personal Mark PoC."""

from __future__ import annotations

import locale
from typing import Mapping


_MESSAGES: dict[str, dict[str, str]] = {
    "ja": {
        "open": "あなたのしるし",
        "title": "あなたのしるし",
        "description": "自分で描いた視覚的なしるしを、この端末に保存できます。",
        "clear": "書き直す",
        "save": "保存",
        "replay": "再生",
        "stop": "停止",
        "ready": "キャンバスに描いてください。",
        "loaded": "保存したしるしを読み込みました。",
        "saved": "この端末に保存しました。",
        "empty": "保存する線がありません。",
        "replaying": "描いた順番で再生しています。再生を押すと最初から再開します。",
        "stopped": "再生を停止しました。",
        "corrupted": "保存データを読み込めなかったため、新しいキャンバスを表示しています。",
        "version_mismatch": "このバージョンでは保存データを読み込めません。",
        "save_failed": "保存できませんでした。もう一度お試しください。",
        "motion_complete": "しるし済",
    },
    "en": {
        "open": "Your mark",
        "title": "Your mark",
        "description": "Draw a visual mark of your own and save it on this device.",
        "clear": "Redraw",
        "save": "Save",
        "replay": "Replay",
        "stop": "Stop",
        "ready": "Draw on the canvas.",
        "loaded": "Your saved mark was loaded.",
        "saved": "Saved on this device.",
        "empty": "There are no strokes to save.",
        "replaying": "Replaying in drawing order. Choose Replay to restart.",
        "stopped": "Replay stopped.",
        "corrupted": "Saved data could not be loaded, so a new canvas is shown.",
        "version_mismatch": "This version cannot load the saved data.",
        "save_failed": "The mark could not be saved. Please try again.",
        "motion_complete": "Marked",
    },
}


def personal_mark_messages(language: str | None = None) -> Mapping[str, str]:
    requested = language or (locale.getlocale()[0] or "ja")
    key = "ja" if requested.lower().startswith("ja") else "en"
    return _MESSAGES[key]
