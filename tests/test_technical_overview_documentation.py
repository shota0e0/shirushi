from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
OVERVIEW = PROJECT / "docs/technical-overview.md"


class TechnicalOverviewDocumentationTests(unittest.TestCase):
    def test_overview_exists_and_readme_links_to_it(self) -> None:
        self.assertTrue(OVERVIEW.is_file())
        readme = (PROJECT / "README.md").read_text(encoding="utf-8")
        self.assertIn("[技術概要](docs/technical-overview.md)", readme)

    def test_relative_document_links_resolve(self) -> None:
        text = OVERVIEW.read_text(encoding="utf-8")
        targets = re.findall(r"\[[^]]+\]\(([^)]+)\)", text)
        self.assertEqual(
            {
                "../README.md",
                "../DISCLAIMER.md",
                "../PRIVACY.md",
                "../THIRD_PARTY_NOTICES.md",
            },
            set(targets),
        )
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue((OVERVIEW.parent / target).resolve().is_file())

    def test_supported_formats_and_preview_trust_are_narrow(self) -> None:
        text = OVERVIEW.read_text(encoding="utf-8")
        formats = text[text.index("## 7. 対応形式と情報の残存性"):text.index("## 8. 配布構成")]
        self.assertIn("- PNG", formats)
        self.assertIn("- JPEG / JPG", formats)
        self.assertNotIn("JFIF", text)
        self.assertIn("`c2patool`が提供する開発・テスト用途の資格情報", text)
        self.assertIn("Production Trustには対応していません", text)

    def test_overview_has_no_affirmative_overclaim_phrases(self) -> None:
        text = OVERVIEW.read_text(encoding="utf-8")
        forbidden_patterns = (
            r"AIから(?:作品を)?守ります[。！]?",
            r"AI利用を防止します[。！]?",
            r"AI学習を完全に止めます[。！]?",
            r"AI生成を完全に止めます[。！]?",
            r"法的効力があります[。！]?",
            r"著作権を保証します[。！]?",
            r"作者本人であることを証明します[。！]?",
            r"Production Trust(?:済み|を確認済み|に対応しています)",
            r"完全オフライン(?:です|で動作します)",
            r"加工後も必ず残ります[。！]?",
        )
        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text))


if __name__ == "__main__":
    unittest.main()
