from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


class ReadmeDocumentationTests(unittest.TestCase):
    def test_readme_covers_public_v01_entry_points(self) -> None:
        text = (PROJECT / "README.md").read_text(encoding="utf-8")

        required = (
            "# しるし / Shirushi",
            "作品に利用意思を残す",
            "Shirushi v0.1 Preview",
            "C2PA",
            "CAWG Rights",
            "TrustMark",
            "## Release版を起動する",
            "shirushi-v0.1-preview-windows-x64.zip",
            "Shirushi.exe",
            "ZIPファイルを右クリックして`すべて展開`を選び",
            "`Shirushi.exe`だけを別の場所へ移動せず",
            "## 対応画像形式",
            "PNG",
            "JPEG / JPG",
            "すべてのAIサービスがこの権利情報を読み取り、その意思表示を尊重するとは限りません",
            "AI学習やAI生成を直接停止する機能ではありません",
            "著作権者本人であることを確認せず",
            "開発・テスト用の資格情報",
            "基本的にローカル",
            "公式の配布元から自動取得",
            "初回のみ時間がかかる場合があります。しばらくお待ちください。",
            "photo_rights.jpg",
            "既存ファイルは上書きしません",
            "明示的に続行した場合だけ再付与します",
            "権利情報やメタデータの一部または全部が失われる可能性",
            "MIT License",
            "Originally created by しょたお (Shota0).",
            "## 開発への参加",
            "[製品上の注意事項](DISCLAIMER.md)",
            "[プライバシー情報](PRIVACY.md)",
            "[第三者コンポーネントに関する表示](THIRD_PARTY_NOTICES.md)",
            "## 開発者向けの起動方法",
            "以下は開発者向けです。通常利用では不要です",
            "一般ユーザーは、前述の配布パッケージに含まれる`Shirushi.exe`を使用してください",
            "識別情報だけが残っている状態か、または情報を確認できない状態かを確認できます",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_readme_has_no_affirmative_overclaim_phrases(self) -> None:
        text = (PROJECT / "README.md").read_text(encoding="utf-8")

        # Match affirmative promises only. Explicit non-guarantee statements
        # remain valid and are required by the public product boundary.
        forbidden_patterns = (
            r"AIから(?:作品を)?守ります[。！]?",
            r"AI利用を防止します[。！]?",
            r"AI学習を完全に止めます[。！]?",
            r"AI生成を完全に止めます[。！]?",
            r"法的効力があります[。！]?",
            r"著作権を保証します[。！]?",
            r"Production Trust(?:済み|を確認済み)",
            r"完全オフライン(?:です|で動作します)",
        )
        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text))

    def test_readme_links_only_to_existing_public_documents(self) -> None:
        text = (PROJECT / "README.md").read_text(encoding="utf-8")
        for target in ("DISCLAIMER.md", "PRIVACY.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
            with self.subTest(target=target):
                self.assertIn(f"]({target})", text)
                self.assertTrue((PROJECT / target).is_file())

        self.assertNotIn("TODO", text)
        self.assertNotIn("TBD", text)
        self.assertNotIn("example.com", text)

    def test_release_and_developer_launch_instructions_are_separate(self) -> None:
        text = (PROJECT / "README.md").read_text(encoding="utf-8")
        release = text.index("## Release版を起動する")
        usage = text.index("## 基本的な使い方")
        contributions = text.index("## 開発への参加")
        developer = text.index("## 開発者向けの起動方法")

        self.assertLess(release, usage)
        self.assertLess(contributions, developer)
        self.assertNotIn(".venv-py312", text[release:usage])
        self.assertNotIn("Shirushi.vbs", text[release:usage])
        self.assertIn(".venv-py312", text[developer:])
        self.assertIn("Shirushi.vbs", text[developer:])

    def test_user_guide_presents_formats_and_fixed_setting_clearly(self) -> None:
        text = (PROJECT / "README.md").read_text(encoding="utf-8")
        formats = text[text.index("## 対応画像形式"):text.index("## 基本的な使い方")]
        create = text[text.index("### しるしを付ける"):text.index("### しるしを確認する")]

        self.assertIn("| PNG |", formats)
        self.assertIn("| JPEG / JPG |", formats)
        self.assertNotIn("| JFIF |", formats)
        self.assertIn("意思表示が固定で設定されています", create)
        self.assertNotIn("固定の利用意思設定を確認します", create)


if __name__ == "__main__":
    unittest.main()
