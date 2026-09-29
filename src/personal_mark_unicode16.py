"""Pinned Unicode 16.0 properties and canonical NFC, independent of host UCD.

No NFKC, trimming, case folding, or invisible-character removal. Data are
generated from Unicode Consortium UCD files; see data/Unicode-LICENSE.txt.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

UNICODE_VERSION = "16.0.0"
_DATA = json.loads((Path(__file__).parent / "data/personal_mark_unicode16.json").read_text(encoding="utf-8"))
if _DATA["unicodeVersion"] != UNICODE_VERSION:
    raise RuntimeError("Personal Mark Unicode data version mismatch")
_CCC = {int(key): value for key, value in _DATA["ccc"].items()}
_DECOMPOSITION = {int(key): tuple(value) for key, value in _DATA["decomposition"].items()}
_COMPOSITION = {tuple(map(int, key.split(","))): value for key, value in _DATA["composition"].items()}
_RANGES = {key: tuple(map(tuple, value)) for key, value in _DATA["ranges"].items()}


def has_property(cp: int, name: str) -> bool:
    """Check one Unicode scalar against a pinned property range table."""
    if type(cp) is not int or not 0 <= cp <= 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
        raise ValueError("expected a Unicode scalar")
    spans = _RANGES[name]
    low, high = 0, len(spans)
    while low < high:
        middle = (low + high) // 2
        start, end = spans[middle]
        if cp < start:
            high = middle
        elif cp > end:
            low = middle + 1
        else:
            return True
    return False


@lru_cache(maxsize=8192)
def _decompose(cp: int) -> tuple[int, ...]:
    # Algorithmic Hangul canonical decomposition (UAX #15).
    index = cp - 0xAC00
    if 0 <= index < 11172:
        result = (0x1100 + index // 588, 0x1161 + (index % 588) // 28)
        tail = index % 28
        return result + (0x11A7 + tail,) if tail else result
    mapped = _DECOMPOSITION.get(cp)
    if mapped is None:
        return (cp,)
    return tuple(item for child in mapped for item in _decompose(child))


def _compose(first: int, second: int) -> int | None:
    leading, vowel = first - 0x1100, second - 0x1161
    if 0 <= leading < 19 and 0 <= vowel < 21:
        return 0xAC00 + (leading * 21 + vowel) * 28
    syllable, trailing = first - 0xAC00, second - 0x11A7
    if 0 <= syllable < 11172 and syllable % 28 == 0 and 0 < trailing < 28:
        return first + trailing
    return _COMPOSITION.get((first, second))


def normalize_nfc(text: str) -> str:
    """Return a candidate only; callers must enforce explicit save confirmation."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    ordered: list[int] = []
    segment: list[int] = []
    for character in text:
        cp = ord(character)
        if 0xD800 <= cp <= 0xDFFF:
            raise ValueError("malformed Unicode surrogate")
        for point in _decompose(cp):
            if _CCC.get(point, 0) == 0:
                # Stable ordering avoids quadratic insertion on hostile long runs.
                ordered.extend(sorted(segment, key=lambda value: _CCC.get(value, 0)))
                segment.clear()
                ordered.append(point)
            else:
                segment.append(point)
    ordered.extend(sorted(segment, key=lambda value: _CCC.get(value, 0)))
    if not ordered:
        return ""
    result = [ordered[0]]
    starter_index, starter = 0, ordered[0]
    last_cc = _CCC.get(starter, 0)
    for point in ordered[1:]:
        cc = _CCC.get(point, 0)
        composite = _compose(starter, point)
        if composite is not None and (last_cc == 0 or last_cc < cc):
            result[starter_index] = composite
            starter = composite
        else:
            if cc == 0:
                starter_index, starter = len(result), point
            result.append(point)
            last_cc = cc
    return "".join(map(chr, result))
