from __future__ import annotations

import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


class PrivacyDocumentationTests(unittest.TestCase):
    def test_privacy_statement_covers_current_v01_data_boundaries(self) -> None:
        text = (PROJECT / "PRIVACY.md").read_text(encoding="utf-8")

        required = (
            "Shirushi v0.1 Preview",
            "ユーザーのPC内",
            "画像ファイルを外部サービスへアップロードする処理はありません",
            "TrustMarkモデルの準備",
            "外部C2PA参照情報の確認",
            "creator_service.log",
            r"%LOCALAPPDATA%\Shirushi\logs\creator_service.log",
            "`logs`フォルダー",
            "フルパス",
            "SHA-256",
            "ログのローテーション",
            "一時ファイル",
            r"%LOCALAPPDATA%\Shirushi\models\trustmark\0.9.0\P",
            "約62 MiB",
            "製品テレメトリ",
            "画像メタデータ",
            "[免責事項](DISCLAIMER.md)",
            "[第三者コンポーネントに関する表示](THIRD_PARTY_NOTICES.md)",
            "[ライセンス](LICENSE)",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_privacy_statement_avoids_absolute_network_claims(self) -> None:
        text = (PROJECT / "PRIVACY.md").read_text(encoding="utf-8")

        forbidden = (
            "完全オフライン",
            "一切通信しない",
            "何のデータも外へ出ない",
        )
        for phrase in forbidden:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text)
        self.assertNotIn("output/e2e/creator_service.log", text)

    def test_third_party_notice_has_finalized_holder_wording(self) -> None:
        text = (PROJECT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

        self.assertIn("Copyright (c) 2026 しょたお", text)
        self.assertNotIn("BLOCKED FOR HOLDER NAME", text)
        self.assertNotIn("copyright-holder field is finalized", text)


if __name__ == "__main__":
    unittest.main()
