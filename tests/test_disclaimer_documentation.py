from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


class DisclaimerDocumentationTests(unittest.TestCase):
    def test_disclaimer_covers_current_v01_product_boundaries(self) -> None:
        text = (PROJECT / "DISCLAIMER.md").read_text(encoding="utf-8")

        required = (
            "Shirushi v0.1 Preview",
            "すべてのAIサービスがその情報を読み取るとは限りません",
            "AI学習やAI生成を直接停止またはブロックする機能ではありません",
            "法的拘束力を持つことは保証しません",
            "必要な権利または許諾を持つ画像に使用してください。",
            "権利者本人であることを確認しません",
            "作者本人であることの検証",
            "暗号署名が数学的に正しいか",
            "Production Trust",
            "現在は正式な信頼済み署名ではありません。",
            "完全性を確認済み",
            "利用の許可も拒否も意味しません",
            "識別情報のみ",
            "識別子の検出 ≠ 権利情報の復元",
            "画像編集ソフトによる再保存",
            "形式変換",
            "SNSへのアップロード",
            "入力画像に存在するメタデータをすべて保存するものではありません",
            "既存のShirushi情報がある画像へ再付与すると",
            "[プライバシー情報](PRIVACY.md)",
            "[LICENSE](LICENSE)",
            "[第三者コンポーネントに関する表示](THIRD_PARTY_NOTICES.md)",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_disclaimer_has_no_affirmative_overclaim_phrases(self) -> None:
        text = (PROJECT / "DISCLAIMER.md").read_text(encoding="utf-8")

        # These patterns target affirmative product promises. Negative boundary
        # statements such as "保証しません" remain valid and are tested above.
        forbidden_patterns = (
            r"AIから作品を守ります[。！]?",
            r"AI利用を防止します[。！]?",
            r"AI学習を完全に止めます[。！]?",
            r"AI生成を完全に止めます[。！]?",
            r"すべてのAI(?:サービス)?がRights情報を読み取ります[。！]?",
            r"Rights情報は必ず尊重されます[。！]?",
            r"法的保護を強化します[。！]?",
            r"法的効力があります[。！]?",
            r"(?:著作権者|作者|権利者)本人であることを証明します[。！]?",
            r"Production Trust(?:済み|を確認済み)",
            r"加工後も必ず残ります[。！]?",
            r"TrustMarkからRights情報を完全復元できます[。！]?",
            r"すべてのmetadataを保持します[。！]?",
            r"完全に安全(?:です|である)",
        )
        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text))

    def test_disclaimer_links_to_existing_public_documents(self) -> None:
        text = (PROJECT / "DISCLAIMER.md").read_text(encoding="utf-8")
        links = {
            "PRIVACY.md": PROJECT / "PRIVACY.md",
            "LICENSE": PROJECT / "LICENSE",
            "THIRD_PARTY_NOTICES.md": PROJECT / "THIRD_PARTY_NOTICES.md",
        }
        for target, path in links.items():
            with self.subTest(target=target):
                self.assertIn(f"]({target})", text)
                self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
