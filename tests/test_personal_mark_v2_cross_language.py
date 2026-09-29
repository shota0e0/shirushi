"""Shared JSON boundary fixtures; Python remains the authoritative save side."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from personal_mark_v2 import (MarkV2Error, TypedPersonalMarkV2, parse_personal_mark_v2, resolve_render_profile)


class SharedV2ContractTests(unittest.TestCase):
    def test_shared_python_web_fixtures(self):
        fixture = json.loads((ROOT / "tests/fixtures/personal_mark_v2_shared.json").read_text(encoding="utf-8"))
        completed = subprocess.run(["node", "web/personal-mark-v2/cross-language.mjs"], cwd=ROOT,
                                   capture_output=True, check=True, encoding="utf-8", timeout=30)
        web = {outcome["id"]: outcome for outcome in json.loads(completed.stdout)}
        self.assertEqual(len(web), len(fixture["cases"]))
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                try:
                    mark = parse_personal_mark_v2(case["raw"].encode("utf-8"))
                    python = {"id":case["id"], "code":"VALID", "mark":mark.to_mapping(), "profile":None}
                    if isinstance(mark, TypedPersonalMarkV2):
                        python["profile"] = resolve_render_profile(mark.render_profile).state.value
                except MarkV2Error as error:
                    python = {"id":case["id"], "code":error.code}
                self.assertEqual(python["code"], case["expect"])
                if "profile" in case:
                    self.assertEqual(python["profile"], case["profile"])
                self.assertEqual(web[case["id"]], python)


if __name__ == "__main__":
    unittest.main()
