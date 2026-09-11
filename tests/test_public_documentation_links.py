from pathlib import Path
import re
import sys
import shutil
import uuid
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
from assemble_release_candidate import assemble, release_markdown


class PublicDocumentationLinksTests(unittest.TestCase):
    documents = {
        "README.md": "docs/README.md",
        "docs/technical-overview.md": "docs/technical-overview.md",
        "docs/validation-report.md": "docs/validation-report.md",
        "DISCLAIMER.md": "docs/DISCLAIMER.md",
        "PRIVACY.md": "docs/PRIVACY.md",
    }

    def assert_links(self, document):
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" not in target and not target.startswith("#"):
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / target.split("#")[0]).is_file())

    def test_repository_links(self):
        for source in self.documents:
            self.assert_links(PROJECT / source)

    def test_release_links_and_only_link_destinations_change(self):
        runtime = PROJECT / "tests/.runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        payload = runtime / ("public-links-" + uuid.uuid4().hex)
        payload.mkdir()
        try:
            assemble(PROJECT, payload)
            for source, destination in self.documents.items():
                self.assert_links(payload / destination)
                before = (PROJECT / source).read_bytes().decode("utf-8")
                after = (payload / destination).read_bytes().decode("utf-8")
                # Independently remove destinations: labels, prose and whitespace must match.
                strip = lambda text: re.sub(r"(\[[^\]]*\]\()[^)]+(\))", r"\1\2", text)
                self.assertEqual(strip(before), strip(after))
            self.assertTrue((payload / "LICENSES/Shirushi-MIT.txt").is_file())
            self.assertTrue((payload / "LICENSES/THIRD_PARTY_NOTICES.md").is_file())
        finally:
            shutil.rmtree(payload)

    def test_conversion_is_limited_and_preserves_fragments_and_code(self):
        text = '[MIT](LICENSE#terms) [web](https://example.org) [other](other.md)\n`[MIT](LICENSE)`\n```md\n[MIT](LICENSE)\n```\n![image](LICENSE)'
        expected = text.replace('[MIT](LICENSE#terms)', '[MIT](../LICENSES/Shirushi-MIT.txt#terms)')
        self.assertEqual(expected, release_markdown(text, "README.md"))

    def test_public_copy_scope_and_supported_formats(self):
        readme = (PROJECT / "README.md").read_text(encoding="utf-8")
        overview = (PROJECT / "docs/technical-overview.md").read_text(encoding="utf-8")
        definition = overview.split("### 完全性を確認済み\n\n")[1].split("\n\n")[0]
        self.assertIn(definition, readme)
        for name in self.documents:
            text = (PROJECT / name).read_text(encoding="utf-8")
            self.assertNotIn("JFIF", text)
            self.assertNotIn("rotation", text)
        report = (PROJECT / "docs/validation-report.md").read_text(encoding="utf-8")
        self.assertIn("Shirushi本体の基本動作確認とは別に", report)
        self.assertIn("固定した検証用画像と確認環境", report)


if __name__ == "__main__":
    unittest.main()
