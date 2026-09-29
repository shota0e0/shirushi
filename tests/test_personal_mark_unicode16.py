"""Official pinned Unicode conformance; no network or host Unicode dependency."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from personal_mark_unicode16 import UNICODE_VERSION, has_property, normalize_nfc


class Unicode16Tests(unittest.TestCase):
    def test_official_normalization_vectors(self):
        fixture = json.loads((ROOT / "tests/fixtures/personal_mark_unicode16_normalization.json").read_text(encoding="utf-8"))
        self.assertEqual(fixture["unicodeVersion"], UNICODE_VERSION)
        self.assertGreater(len(fixture["vectors"]), 19000)
        for index, (source, nfc, nfd, nfkc, nfkd) in enumerate(fixture["vectors"]):
            for value, expected in ((source,nfc),(nfc,nfc),(nfd,nfc),(nfkc,nfkc),(nfkd,nfkc)):
                self.assertEqual(normalize_nfc(value), expected, f"official row {index}")

    def test_pinned_properties_and_no_compatibility_folding(self):
        self.assertTrue(has_property(0x3000, "white_space"))
        self.assertTrue(has_property(0x202E, "bidi_control"))
        self.assertTrue(has_property(0xE0100, "variation_selector"))
        self.assertTrue(has_property(0x200B, "format"))
        self.assertTrue(has_property(0x115F, "default_ignorable"))
        self.assertTrue(has_property(0x1E5D0, "visible_base"))  # Unicode 16 Ol Onal.
        self.assertFalse(has_property(0x200D, "visible_base"))
        self.assertEqual(normalize_nfc("Ａ　a\u200d森\ufe00"), "Ａ　a\u200d森\ufe00")
        self.assertEqual(normalize_nfc("e\u0301"), "é")
        self.assertEqual(normalize_nfc("\u1100\u1161\u11a8"), "각")
        with self.assertRaises(ValueError):
            normalize_nfc("\ud800")

    def test_generated_python_web_tables_are_identical(self):
        python_data = json.loads((ROOT / "src/data/personal_mark_unicode16.json").read_text(encoding="utf-8"))
        module = (ROOT / "web/personal-mark-v2/unicode16-data.js").read_text(encoding="utf-8")
        web_data = json.loads(module.split("export default ", 1)[1].removesuffix(";\n"))
        self.assertEqual(python_data, web_data)
        self.assertEqual(len(python_data["sources"]), 6)
        for record in python_data["sources"].values():
            self.assertEqual(len(record["sha256"]), 64)
            self.assertTrue(record["url"].startswith("https://www.unicode.org/"))


if __name__ == "__main__":
    unittest.main()
