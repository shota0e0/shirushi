"""Minimal shared helpers for local C2PA parsing and verification."""

from __future__ import annotations

import hashlib
from pathlib import Path
import struct
from typing import Any


class CborDecoder:
    """Strict decoder for the definite-length CBOR used by the verifier."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def take(self, count: int) -> bytes:
        end = self.offset + count
        if end > len(self.data):
            raise ValueError("truncated CBOR")
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def length(self, additional: int) -> int:
        if additional < 24:
            return additional
        sizes = {24: 1, 25: 2, 26: 4, 27: 8}
        if additional not in sizes:
            raise ValueError(f"unsupported CBOR length encoding: {additional}")
        return int.from_bytes(self.take(sizes[additional]), "big")

    def value(self) -> Any:
        initial = self.take(1)[0]
        major, additional = initial >> 5, initial & 31
        if additional == 31:
            if major == 2:
                parts = []
                while self.data[self.offset] != 0xFF:
                    part = self.value()
                    if not isinstance(part, bytes):
                        raise ValueError("invalid indefinite byte string")
                    parts.append(part)
                self.offset += 1
                return b"".join(parts)
            if major == 3:
                parts = []
                while self.data[self.offset] != 0xFF:
                    part = self.value()
                    if not isinstance(part, str):
                        raise ValueError("invalid indefinite text string")
                    parts.append(part)
                self.offset += 1
                return "".join(parts)
            if major == 4:
                items = []
                while self.data[self.offset] != 0xFF:
                    items.append(self.value())
                self.offset += 1
                return items
            if major == 5:
                items = {}
                while self.data[self.offset] != 0xFF:
                    key = self.value()
                    items[key] = self.value()
                self.offset += 1
                return items
            raise ValueError(f"unsupported indefinite CBOR major type: {major}")
        if major == 0:
            return self.length(additional)
        if major == 1:
            return -1 - self.length(additional)
        if major == 2:
            return self.take(self.length(additional))
        if major == 3:
            return self.take(self.length(additional)).decode("utf-8")
        if major == 4:
            return [self.value() for _ in range(self.length(additional))]
        if major == 5:
            return {self.value(): self.value() for _ in range(self.length(additional))}
        if major == 6:
            return {"cbor_tag": self.length(additional), "value": self.value()}
        if major == 7:
            simple = {20: False, 21: True, 22: None, 23: None}
            if additional in simple:
                return simple[additional]
            if additional == 24:
                return self.take(1)[0]
            if additional == 25:
                return struct.unpack(">e", self.take(2))[0]
            if additional == 26:
                return struct.unpack(">f", self.take(4))[0]
            if additional == 27:
                return struct.unpack(">d", self.take(8))[0]
        raise ValueError(f"unsupported CBOR item: major={major} additional={additional}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"type": "bytes", "hex": value.hex(), "length": len(value)}
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
