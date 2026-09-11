"""Test-only process seam for CreatorService failure and cancellation fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid


PROJECT = Path(__file__).resolve().parents[2]
SRC = PROJECT / "src"
SCRIPTS = PROJECT / "scripts"
for search_path in (SRC, SCRIPTS):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

from creator_create import exit_code
from creator_service import CreatorRequest, CreatorService


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def controlled_core(input_path, output_path, c2patool, result_path, log_path):
    shutil.copyfile(input_path, output_path)
    return {"status": "PASS", "output": {"sha256": sha256_file(output_path)}}


def verifier_result(input_path: Path, *, passed: bool) -> dict:
    return {
        "contractVersion": "1.0",
        "result": "PASS" if passed else "FAIL_SIGNATURE",
        "reasonCode": None if passed else "SIGNATURE_INVALID",
        "message": "Controlled verifier result.",
        "input": {
            "path": str(input_path.resolve()),
            "sha256": sha256_file(input_path),
            "format": "png",
            "dimensions": {"width": 1024, "height": 1024},
        },
        "rights": {"preset": "AI利用拒否", "verified": passed},
        "trustmark": {"present": True, "schema": 2, "payloadLength": 68, "payloadMatch": passed},
        "softBinding": {"present": True, "algorithm": "com.adobe.trustmark", "match": passed},
        "c2pa": {"claimPresent": True, "assertionDigestsMatch": passed},
        "signature": {"present": True, "valid": passed, "trustValidated": False},
        "diagnostics": [
            {"code": "FIXTURE_VERIFICATION", "status": "pass" if passed else "fail"}
        ],
    }


class CancelOnCall:
    def __init__(self, target: int) -> None:
        self.target = target
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.calls == self.target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("verification-failure", "cancel", "internal-error"), required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log_path = args.output.parent / f".creator-service-fixture-{uuid.uuid4().hex}.log"

    def controlled_verifier(input_path, c2patool, settings):
        return verifier_result(input_path, passed=args.scenario != "verification-failure")

    def exploding_core(*unused_args, **unused_kwargs):
        raise RuntimeError("controlled internal fixture diagnostic")

    service = CreatorService(
        log_path=log_path,
        core_runner=exploding_core if args.scenario == "internal-error" else controlled_core,
        verifier=controlled_verifier,
    )
    cancel_check = CancelOnCall(5) if args.scenario == "cancel" else None
    try:
        result = service.create(CreatorRequest(args.input, args.output), cancel_check=cancel_check)
    finally:
        log_path.unlink(missing_ok=True)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
