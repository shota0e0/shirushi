"""Fixed isolated entrypoint for the read-only Shirushi desktop bridge."""

from __future__ import annotations

from pathlib import Path
import sys


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
sys.path.insert(0, str(_SOURCE_ROOT))

from desktop_bridge import serve  # noqa: E402


def main() -> int:
    if len(sys.argv) != 1:
        return 2
    return serve(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
