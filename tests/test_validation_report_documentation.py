from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
REPORT = PROJECT / "docs/validation-report.md"


class ValidationReportDocumentationTests(unittest.TestCase):
    def test_report_exists_and_readme_links_to_it(self) -> None:
        self.assertTrue(REPORT.is_file())
        readme = (PROJECT / "README.md").read_text(encoding="utf-8")
        self.assertIn("[ローカル検証結果](docs/validation-report.md)", readme)

    def test_technical_overview_link_resolves(self) -> None:
        text = REPORT.read_text(encoding="utf-8")
        self.assertIn("[技術概要](technical-overview.md)", text)
        self.assertTrue((REPORT.parent / "technical-overview.md").is_file())

    def test_scope_formats_and_state_labels_are_fixed(self) -> None:
        text = REPORT.read_text(encoding="utf-8")
        self.assertIn("PNG／JPEGでの付与・保存・確認", text)
        self.assertIn("JPEG／JPG", text)
        self.assertNotIn("JFIF", text)
        for label in ("完全性を確認済み", "意思表示なし", "識別情報のみ", "確認できません"):
            with self.subTest(label=label):
                self.assertIn(label, text)
        self.assertIn("固定したローカル検証環境", text)
        self.assertIn("この文書の評価対象に含みません", text)

    def test_report_excludes_restricted_details_and_unapproved_claims(self) -> None:
        text = REPORT.read_text(encoding="utf-8")
        forbidden_literals = (
            "JFIF",
            "再現性",
            "統計的再現性",
            "冗長化",
            "バックアップ",
            "wrong payload",
            "schema",
            "reason code",
            "Reference Disruption",
            "ChatGPT",
            "Gemini",
            "Claude",
            "Copilot",
        )
        for value in forbidden_literals:
            with self.subTest(value=value):
                self.assertNotIn(value, text)

        forbidden_patterns = (
            r"JPEG\s*(?:Q|品質)\s*\d+",
            r"(?:crop|切り抜き)\s*\d+\s*%",
            r"(?:rotation|回転)\s*\d+(?:\.\d+)?\s*(?:°|度)",
            r"(?:resize|サイズ変更)\s*\d+\s*(?:px|ピクセル)",
            r"(?:overlay|重ね合わせ)\s*\d+\s*%",
            r"AIから(?:作品を)?守ります[。！]?",
            r"AI利用を防止します[。！]?",
            r"AI(?:学習|生成)を(?:完全に|直接)?止めます[。！]?",
            r"法的効力があります[。！]?",
            r"著作権を保証します[。！]?",
            r"作者本人であることを証明します[。！]?",
            r"Production Trust(?:済み|を確認済み|に対応しています)",
            r"完全オフライン(?:です|で動作します)",
            r"加工後も必ず残ります[。！]?",
            r"すべての画像.+動作(?:します|を保証します)",
        )
        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text))


if __name__ == "__main__":
    unittest.main()
