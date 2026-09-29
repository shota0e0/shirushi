"""Reproduce pinned Unicode 16 data assets; never used during application startup.

Downloads only Unicode Consortium text data, not executable code. Generated
tables and normalization vectors are mechanical derivatives under Unicode-3.0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://www.unicode.org/Public/16.0.0/ucd/"
SOURCES = {name: BASE + name for name in (
    "UnicodeData.txt", "PropList.txt", "DerivedCoreProperties.txt",
    "DerivedNormalizationProps.txt", "NormalizationTest.txt",
)}
SOURCES["license.txt"] = "https://www.unicode.org/license.txt"
# Official fixed-version text sources, verified on 2026-09-29.
EXPECTED_SHA256: dict[str, str] = {
    "UnicodeData.txt": "ff58e5823bd095166564a006e47d111130813dcf8bf234ef79fa51a870edb48f",
    "PropList.txt": "53d614508e2a0b2305a8aa21cd60d993de9326cdf65993660dfcce4503548583",
    "DerivedCoreProperties.txt": "39d35161f2954497f69e08bdb9e701493f476a3d30222de20028feda36c1dabd",
    "DerivedNormalizationProps.txt": "4d4c03892dea9146d674b686e495df2d55a28d071ac474041d73518f887abddc",
    "NormalizationTest.txt": "d811971453e7075e1ad56fb1b301eece5aa80757b81f6156e74a1bfb3ae5ceb1",
    "license.txt": "e7a93b009565cfce55919a381437ac4db883e9da2126fa28b91d12732bc53d96",
}


def ranges(points: set[int]) -> list[list[int]]:
    result: list[list[int]] = []
    for point in sorted(points):
        if result and point == result[-1][1] + 1:
            result[-1][1] = point
        else:
            result.append([point, point])
    return result


def property_points(text: str, name: str) -> set[int]:
    result: set[int] = set()
    for line in text.splitlines():
        fields = line.split("#", 1)[0].strip().split(";")
        if len(fields) < 2 or fields[1].strip() != name:
            continue
        span = fields[0].strip().split("..")
        start, end = int(span[0], 16), int(span[-1], 16)
        result.update(range(start, end + 1))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare without writing")
    args = parser.parse_args()
    sources: dict[str, str] = {}
    provenance: dict[str, dict[str, str]] = {}
    for name, url in SOURCES.items():
        with urllib.request.urlopen(url, timeout=60) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("unexpected source size")
        digest = hashlib.sha256(raw).hexdigest()
        if EXPECTED_SHA256 and EXPECTED_SHA256.get(name) != digest:
            raise ValueError(f"source hash changed: {name}")
        sources[name] = raw.decode("utf-8")
        provenance[name] = {"url": url, "sha256": digest}

    ccc: dict[str, int] = {}
    decomposition: dict[str, list[int]] = {}
    format_points: set[int] = set()
    visible: set[int] = set()
    first: int | None = None
    for line in sources["UnicodeData.txt"].splitlines():
        fields = line.split(";")
        cp, name, category = int(fields[0], 16), fields[1], fields[2]
        if name.endswith(", First>"):
            first = cp
            continue
        start = first if name.endswith(", Last>") else cp
        if start is None:
            raise ValueError("invalid UnicodeData range")
        first = None
        if category == "Cf":
            format_points.update(range(start, cp + 1))
        if category[0] in "LNPS":
            visible.update(range(start, cp + 1))
        if int(fields[3]):
            for point in range(start, cp + 1):
                ccc[str(point)] = int(fields[3])
        if fields[5] and not fields[5].startswith("<"):
            decomposition[str(cp)] = [int(value, 16) for value in fields[5].split()]
    excluded = property_points(sources["DerivedNormalizationProps.txt"], "Full_Composition_Exclusion")
    composition = {
        f"{pair[0]},{pair[1]}": int(cp)
        for cp, pair in decomposition.items() if len(pair) == 2 and int(cp) not in excluded
    }
    data = {
        "unicodeVersion": "16.0.0", "license": "Unicode-3.0",
        "sources": provenance,
        "ranges": {
            "white_space": ranges(property_points(sources["PropList.txt"], "White_Space")),
            "bidi_control": ranges(property_points(sources["PropList.txt"], "Bidi_Control")),
            "variation_selector": ranges(property_points(sources["PropList.txt"], "Variation_Selector")),
            "format": ranges(format_points),
            "default_ignorable": ranges(property_points(sources["DerivedCoreProperties.txt"], "Default_Ignorable_Code_Point")),
            "visible_base": ranges(visible),
        },
        "ccc": ccc, "decomposition": decomposition, "composition": composition,
    }
    vectors = []
    for line in sources["NormalizationTest.txt"].splitlines():
        record = line.split("#", 1)[0].strip()
        if not record or record.startswith("@"):
            continue
        columns = record.split(";")[:5]
        vectors.append(["".join(chr(int(cp, 16)) for cp in col.split()) for col in columns])
    encoded = json.dumps(data, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    outputs = {
        ROOT / "src/data/personal_mark_unicode16.json": encoded + "\n",
        ROOT / "web/personal-mark-v2/unicode16-data.js":
            "// Generated by scripts/generate_personal_mark_unicode16.py; Unicode-3.0.\n"
            "// See Unicode-LICENSE.txt. Do not edit the derived tables by hand.\nexport default " + encoded + ";\n",
        ROOT / "tests/fixtures/personal_mark_unicode16_normalization.json":
            json.dumps({"unicodeVersion": "16.0.0", "source": provenance["NormalizationTest.txt"],
                        "columns": ["source", "NFC", "NFD", "NFKC", "NFKD"], "vectors": vectors},
                       ensure_ascii=True, separators=(",", ":")) + "\n",
        ROOT / "src/data/Unicode-LICENSE.txt": sources["license.txt"],
        ROOT / "web/personal-mark-v2/Unicode-LICENSE.txt": sources["license.txt"],
        ROOT / "tests/fixtures/Unicode-LICENSE.txt": sources["license.txt"],
    }
    for path, value in outputs.items():
        expected = value.encode("utf-8")
        if args.check:
            if not path.is_file() or path.read_bytes() != expected:
                raise ValueError(f"generated file differs: {path.relative_to(ROOT)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
    print(json.dumps({"check": args.check, "outputs": len(outputs), "normalizationVectors": len(vectors),
                      "sourceHashes": {k: v["sha256"] for k, v in provenance.items()}}, indent=2))


if __name__ == "__main__":
    main()
