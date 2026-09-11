"""Thin CLI adapter for CreatorService contract v1.0."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from creator_service import CreatorRequest, CreatorService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args()


def human_output(result: dict) -> str:
    if result["status"] == "SUCCESS":
        return "\n".join(
            [
                "CREATION SUCCESS",
                "",
                f"Output: {result['outputPath']}",
                f"SHA-256: {result['outputSha256']}",
                "Verification: PASS",
            ]
        )
    error = result.get("error") or {}
    return "\n".join(
        [
            "CREATION FAILED",
            "",
            f"Status: {result['status']}",
            f"Code: {error.get('code')}",
            f"Details: {result['message']}",
        ]
    )


def exit_code(result: dict) -> int:
    if result["status"] == "SUCCESS":
        return 0
    if result["status"] == "CANCELLED":
        return 4
    if result["status"] == "INVALID_REQUEST":
        return 2
    if result["status"] == "INTERNAL_ERROR":
        return 3
    return 1


def main() -> int:
    args = parse_args()
    if args.json_output and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    service = CreatorService()
    result = service.create(CreatorRequest(args.input, args.output))
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(human_output(result))
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
