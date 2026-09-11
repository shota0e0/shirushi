from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
import uuid


PROJECT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from assemble_release_candidate import (
    GENERATED_README_NOTICE,
    assemble,
    markdown_to_plain_text,
    release_markdown,
    write_release_readmes,
)


class ReleaseReadmeAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = PROJECT / "tests/.runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        self.root = runtime / f"readme-assembly-{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root)
        runtime = self.root.parent
        if runtime.exists() and not any(runtime.iterdir()):
            runtime.rmdir()

    def test_conversion_preserves_readable_markdown_structures(self) -> None:
        markdown = """# 製品名

## 見出し

- **項目**と`値`

[説明](DISCLAIMER.md)

| 種類 | 状態 |
| --- | --- |
| PNG | 対応 |

```text
sample --flag
```
"""
        plain = markdown_to_plain_text(markdown)
        self.assertTrue(plain.startswith(GENERATED_README_NOTICE))
        self.assertIn("製品名\n========", plain)
        self.assertIn("見出し\n--------", plain)
        self.assertIn("・項目と値", plain)
        self.assertIn("説明（docs/DISCLAIMER.md）", plain)
        self.assertIn("種類  |  状態", plain)
        self.assertIn("PNG  |  対応", plain)
        self.assertIn("    sample --flag", plain)
        self.assertNotIn("```", plain)

    def test_release_files_are_derived_from_the_single_markdown_source(self) -> None:
        source = self.root / "README.md"
        source.write_text("# しるし\n\n[注意](DISCLAIMER.md)\n", encoding="utf-8")
        payload = self.root / "payload"
        payload.mkdir()
        (payload / "README.md").write_text("stale hand-edited copy", encoding="utf-8")

        write_release_readmes(source, payload)

        self.assertFalse((payload / "README.md").exists())
        self.assertEqual(
            release_markdown(source.read_bytes().decode("utf-8"), "README.md").encode("utf-8"),
            (payload / "docs/README.md").read_bytes(),
        )
        self.assertEqual(
            markdown_to_plain_text(source.read_text(encoding="utf-8")),
            (payload / "README.txt").read_text(encoding="utf-8"),
        )
        self.assertIn("直接編集しないでください", (payload / "README.txt").read_text(encoding="utf-8"))

    def test_current_public_readme_converts_without_unresolved_markdown_links(self) -> None:
        source = (PROJECT / "README.md").read_text(encoding="utf-8")
        plain = markdown_to_plain_text(source)
        self.assertNotIn("](DISCLAIMER.md)", plain)
        self.assertNotIn("](PRIVACY.md)", plain)
        self.assertNotIn("](LICENSE)", plain)
        self.assertNotIn("](THIRD_PARTY_NOTICES.md)", plain)
        for target in (
            "docs/DISCLAIMER.md",
            "docs/PRIVACY.md",
            "LICENSES/Shirushi-MIT.txt",
            "LICENSES/THIRD_PARTY_NOTICES.md",
        ):
            self.assertIn(target, plain)

    def test_public_technical_overview_is_assembled_with_release_links(self) -> None:
        payload = self.root / "payload"
        assemble(PROJECT, payload)

        source = PROJECT / "docs/technical-overview.md"
        packaged = payload / "docs/technical-overview.md"
        self.assertTrue(packaged.is_file())
        self.assertEqual(
            release_markdown(source.read_bytes().decode("utf-8"), source.name).encode("utf-8"),
            packaged.read_bytes(),
        )

    def test_public_validation_report_is_assembled_unchanged(self) -> None:
        payload = self.root / "payload"
        assemble(PROJECT, payload)

        source = PROJECT / "docs/validation-report.md"
        packaged = payload / "docs/validation-report.md"
        self.assertTrue(packaged.is_file())
        self.assertEqual(
            release_markdown(source.read_bytes().decode("utf-8"), source.name).encode("utf-8"),
            packaged.read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()
