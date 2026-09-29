from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock
import uuid


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import desktop_bridge
from desktop_bridge import (
    BRIDGE_PROTOCOL_VERSION,
    MAX_JSON_DEPTH,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_SESSION_REQUEST_IDS,
    get_capabilities,
    serve,
)
from personal_mark_v2_store import PersonalMarkV2Store


def request(
    request_id: str,
    method: str = "get_capabilities",
    params=None,
    *,
    version=1,
) -> bytes:
    return json.dumps(
        {
            "protocolVersion": version,
            "requestId": request_id,
            "method": method,
            "params": {} if params is None else params,
        },
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def run_session(payload: bytes, *, store=None):
    output = io.BytesIO()
    exit_code = serve(io.BytesIO(payload), output, store=store)
    raw_lines = output.getvalue().splitlines()
    return exit_code, raw_lines, [json.loads(line) for line in raw_lines]


def expected_capabilities() -> dict:
    return {
        "bridgeProtocolVersion": 1,
        "personalMarkSchemaVersions": [1, 2],
        "renderProfiles": [
            {"id": "shirushi-typed", "version": 1, "state": "ASSETS_UNAVAILABLE"}
        ],
        "capabilities": {
            "personalMarkRead": True,
            "personalMarkWrite": False,
            "nativeTargetSelection": False,
            "coreAdd": False,
            "coreVerify": False,
            "coreReadback": False,
            "c2paPersonalMarkEmbedding": False,
            "explorerIntegration": False,
        },
    }


class DesktopBridgeProtocolTests(unittest.TestCase):
    def assert_error(self, payload: bytes, code: str, request_id=None, exit_code=1) -> dict:
        actual_exit, _raw, responses = run_session(payload)
        self.assertEqual(exit_code, actual_exit)
        self.assertEqual(1, len(responses))
        response = responses[0]
        self.assertEqual(BRIDGE_PROTOCOL_VERSION, response["protocolVersion"])
        self.assertEqual(request_id, response["requestId"])
        self.assertFalse(response["ok"])
        self.assertEqual(code, response["error"]["code"])
        self.assertEqual({"code", "message"}, set(response["error"]))
        return response

    def test_constants_and_capabilities_are_exact(self) -> None:
        self.assertEqual(16_384, MAX_REQUEST_BYTES)
        self.assertEqual(4_194_304, MAX_RESPONSE_BYTES)
        self.assertEqual(16, MAX_JSON_DEPTH)
        self.assertEqual(4_096, MAX_SESSION_REQUEST_IDS)
        self.assertEqual(expected_capabilities(), get_capabilities())

    def test_valid_request_is_correlated_and_eof_is_graceful(self) -> None:
        exit_code, raw, responses = run_session(request("req_1"))
        self.assertEqual(0, exit_code)
        self.assertEqual(1, len(responses))
        self.assertEqual(
            {
                "protocolVersion": 1,
                "requestId": "req_1",
                "ok": True,
                "result": expected_capabilities(),
            },
            responses[0],
        )
        self.assertTrue(raw[0].startswith(b'{"protocolVersion":1,"requestId":"req_1","ok":true,"result":'))

    def test_unknown_method_and_invalid_params_are_correlated_nonfatal(self) -> None:
        payload = request("unknown", "not_allowed") + request("bad-params", params={"path": "forbidden"}) + request("ok")
        exit_code, _raw, responses = run_session(payload)
        self.assertEqual(0, exit_code)
        self.assertEqual(["UNKNOWN_METHOD", "INVALID_PARAMS"], [item["error"]["code"] for item in responses[:2]])
        self.assertEqual(["unknown", "bad-params", "ok"], [item["requestId"] for item in responses])
        self.assertTrue(responses[2]["ok"])

    def test_invalid_params_consumes_request_id(self) -> None:
        payload = request("same", params=[]) + request("same")
        exit_code, _raw, responses = run_session(payload)
        self.assertEqual(1, exit_code)
        self.assertEqual("INVALID_PARAMS", responses[0]["error"]["code"])
        self.assertEqual("DUPLICATE_REQUEST_ID", responses[1]["error"]["code"])

    def test_duplicate_request_id_is_correlated_then_terminates(self) -> None:
        payload = request("same") + request("same") + request("never")
        exit_code, _raw, responses = run_session(payload)
        self.assertEqual(1, exit_code)
        self.assertEqual(2, len(responses))
        self.assertTrue(responses[0]["ok"])
        self.assertEqual("same", responses[1]["requestId"])
        self.assertEqual("DUPLICATE_REQUEST_ID", responses[1]["error"]["code"])

    def test_session_budget_is_bounded_and_ids_are_never_reused(self) -> None:
        with mock.patch("desktop_bridge.MAX_SESSION_REQUEST_IDS", 1):
            exit_code, _raw, responses = run_session(request("one") + request("two"))
        self.assertEqual(1, exit_code)
        self.assertTrue(responses[0]["ok"])
        self.assertEqual("SESSION_EXHAUSTED", responses[1]["error"]["code"])
        self.assertEqual("two", responses[1]["requestId"])

    def test_wrong_protocol_and_envelope_fail_with_safe_correlation(self) -> None:
        self.assert_error(request("v2", version=2), "UNSUPPORTED_PROTOCOL_VERSION", "v2")
        self.assert_error(request("bool", version=True), "UNSUPPORTED_PROTOCOL_VERSION", "bool")
        raw = b'{"protocolVersion":1,"requestId":"known","method":"get_capabilities","params":{},"extra":1}\n'
        self.assert_error(raw, "INVALID_ENVELOPE", "known")
        self.assert_error(b'{"protocolVersion":1,"method":"get_capabilities","params":{}}\n', "INVALID_ENVELOPE")

    def test_invalid_request_ids_are_fatal_and_uncorrelated(self) -> None:
        for invalid in ("", "space bad", "x" * 65, "\u68ee", True):
            with self.subTest(invalid=invalid):
                self.assert_error(request(invalid), "INVALID_REQUEST_ID")  # type: ignore[arg-type]

    def test_malformed_transport_and_json_fail_once(self) -> None:
        cases = (
            (b"{not json\n", "MALFORMED_JSON"),
            (b"\xef\xbb\xbf{}\n", "TRANSPORT_BOM"),
            (b"\xff\n", "INVALID_UTF8"),
            (b'{"protocolVersion":1,"requestId":"a","requestId":"b","method":"get_capabilities","params":{}}\n', "DUPLICATE_KEY"),
            (b'{"protocolVersion":1,"requestId":"a","method":"get_capabilities","params":{"value":NaN}}\n', "INVALID_NUMBER"),
            (b'{"protocolVersion":1,"requestId":"\\ud800","method":"get_capabilities","params":{}}\n', "MALFORMED_UNICODE"),
            (b"\n", "MALFORMED_JSON"),
        )
        for payload, code in cases:
            with self.subTest(code=code):
                self.assert_error(payload, code)

    def test_decoded_duplicate_keys_and_depth_limit_are_rejected(self) -> None:
        duplicate = b'{"protocolVersion":1,"requestId":"a","method":"get_capabilities","params":{"text":1,"\\u0074ext":2}}\n'
        self.assert_error(duplicate, "DUPLICATE_KEY")
        nested = "[" * 16 + "]" * 16
        payload = (
            '{"protocolVersion":1,"requestId":"deep","method":"get_capabilities","params":'
            + nested
            + "}\n"
        ).encode()
        self.assert_error(payload, "MAX_DEPTH_EXCEEDED")

    def test_oversized_and_partial_frames_are_bounded(self) -> None:
        self.assert_error(b" " * (MAX_REQUEST_BYTES + 1) + b"\n", "REQUEST_TOO_LARGE")
        self.assert_error(request("partial")[:-1], "PARTIAL_REQUEST")

    def test_response_limit_failure_is_correlated_and_fatal(self) -> None:
        with mock.patch("desktop_bridge.MAX_RESPONSE_BYTES", 300):
            exit_code, _raw, responses = run_session(request("large"))
        self.assertEqual(1, exit_code)
        self.assertEqual("large", responses[0]["requestId"])
        self.assertEqual("RESPONSE_TOO_LARGE", responses[0]["error"]["code"])


class DesktopBridgeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = PROJECT / "tests" / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.root = runtime / f"bridge-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.v2 = self.root / "personal-mark-v2.json"
        self.v1 = self.root / "personal-mark-v1.json"
        self.store = PersonalMarkV2Store(self.v2, self.v1)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def load_response(self) -> dict:
        exit_code, _raw, responses = run_session(
            request("load", "load_personal_mark"),
            store=self.store,
        )
        self.assertEqual(0, exit_code)
        self.assertEqual(1, len(responses))
        self.assertTrue(responses[0]["ok"])
        encoded = json.dumps(responses[0], ensure_ascii=False)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("raw_bytes", encoded)
        return responses[0]["result"]

    def test_absent_mark(self) -> None:
        self.assertEqual(
            {"contract": "shirushi-personal-mark-read", "contractVersion": 1, "state": "absent"},
            self.load_response(),
        )

    def test_valid_v2_and_known_unavailable_profile(self) -> None:
        value = {
            "version": 2,
            "type": "typed",
            "text": "Mori",
            "renderProfile": {"id": "shirushi-typed", "version": 1},
        }
        self.v2.write_text(json.dumps(value), encoding="utf-8")
        before = self.v2.read_bytes()
        result = self.load_response()
        self.assertEqual("v2", result["state"])
        self.assertEqual(value, result["mark"])
        self.assertEqual(
            {"state": "ASSETS_UNAVAILABLE", "errorCode": "RENDER_ASSETS_UNAVAILABLE"},
            result["renderProfileSupport"],
        )
        self.assertEqual(before, self.v2.read_bytes())

    def test_unknown_profile_state_is_preserved_without_fallback(self) -> None:
        value = {
            "version": 2,
            "type": "typed",
            "text": "Mori",
            "renderProfile": {"id": "future", "version": 9},
        }
        self.v2.write_text(json.dumps(value), encoding="utf-8")
        result = self.load_response()
        self.assertEqual("v2", result["state"])
        self.assertEqual(
            {"state": "UNSUPPORTED", "errorCode": "UNSUPPORTED_RENDER_PROFILE"},
            result["renderProfileSupport"],
        )

    def test_legacy_provenance_is_preserved_without_migration(self) -> None:
        value = {
            "version": 1,
            "type": "handwritten",
            "strokes": [{"points": [{"x": 0.2, "y": 0.3, "t": 0}]}],
        }
        self.v1.write_text(json.dumps(value), encoding="utf-8")
        before = self.v1.read_bytes()
        result = self.load_response()
        self.assertEqual("legacy_v1", result["state"])
        self.assertEqual("legacy-unknown", result["geometryProvenance"])
        self.assertNotIn("coordinateSpace", result["payload"])
        self.assertFalse(self.v2.exists())
        self.assertEqual(before, self.v1.read_bytes())

    def test_corrupt_and_unsupported_v2_remain_distinct_and_never_fallback(self) -> None:
        self.v1.write_text(
            json.dumps({"version": 1, "type": "handwritten", "strokes": []}),
            encoding="utf-8",
        )
        self.v2.write_text("{bad", encoding="utf-8")
        malformed = self.load_response()
        self.assertEqual("malformed", malformed["state"])
        self.assertEqual("v2", malformed["source"])
        self.v2.write_text(json.dumps({"version": 9}), encoding="utf-8")
        unsupported = self.load_response()
        self.assertEqual("unsupported", unsupported["state"])
        self.assertEqual("UNKNOWN_SCHEMA_VERSION", unsupported["errorCode"])

    def test_io_error_is_a_successful_transport_read_state(self) -> None:
        self.v2.mkdir()
        result = self.load_response()
        self.assertEqual("io_error", result["state"])
        self.assertEqual("v2", result["source"])

    def test_store_exception_is_generic_nonfatal_bridge_error(self) -> None:
        class FailingStore:
            def load(self):
                raise RuntimeError(f"secret path {self}")

        payload = request("load", "load_personal_mark") + request("after")
        exit_code, raw, responses = run_session(payload, store=FailingStore())
        self.assertEqual(0, exit_code)
        self.assertEqual("PERSONAL_MARK_READ_FAILED", responses[0]["error"]["code"])
        self.assertEqual("load", responses[0]["requestId"])
        self.assertNotIn(b"secret", b"\n".join(raw))
        self.assertTrue(responses[1]["ok"])


class DesktopBridgeSubprocessTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = PROJECT / "tests" / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.root = runtime / f"bridge-process-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.python = PROJECT / ".venv-py312" / "Scripts" / "python.exe"
        self.entry = PROJECT / "scripts" / "shirushi_bridge.py"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def run_bridge(self, payload: bytes, *extra_args: str):
        return subprocess.run(
            [str(self.python), "-I", "-u", str(self.entry), *extra_args],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            cwd=self.root,
            check=False,
        )

    def test_real_isolated_subprocess_valid_framing_and_graceful_shutdown(self) -> None:
        completed = self.run_bridge(request("subprocess"))
        self.assertEqual(0, completed.returncode)
        self.assertEqual(b"", completed.stderr)
        lines = completed.stdout.splitlines()
        self.assertEqual(1, len(lines))
        response = json.loads(lines[0])
        self.assertEqual("subprocess", response["requestId"])
        self.assertTrue(response["ok"])
        self.assertEqual(expected_capabilities(), response["result"])

    def test_real_subprocess_duplicate_id_terminates_and_emits_no_ready_noise(self) -> None:
        completed = self.run_bridge(request("same") + request("same") + request("never"))
        self.assertEqual(1, completed.returncode)
        self.assertEqual(b"", completed.stderr)
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(2, len(responses))
        self.assertEqual("DUPLICATE_REQUEST_ID", responses[1]["error"]["code"])

    def test_real_subprocess_partial_eof_is_structured_and_fatal(self) -> None:
        completed = self.run_bridge(request("partial")[:-1])
        self.assertEqual(1, completed.returncode)
        self.assertEqual(b"", completed.stderr)
        response = json.loads(completed.stdout)
        self.assertEqual("PARTIAL_REQUEST", response["error"]["code"])

    def test_isolated_entry_ignores_pythonpath_and_rejects_arguments(self) -> None:
        malicious = self.root / "malicious"
        malicious.mkdir()
        marker = self.root / "marker.txt"
        (malicious / "desktop_bridge.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(malicious)
        completed = subprocess.run(
            [str(self.python), "-I", "-u", str(self.entry)],
            input=request("isolated"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            cwd=self.root,
            env=environment,
            check=False,
        )
        self.assertEqual(0, completed.returncode)
        self.assertFalse(marker.exists())
        rejected = self.run_bridge(b"", "--path", "forbidden")
        self.assertEqual(2, rejected.returncode)
        self.assertEqual(b"", rejected.stdout)
        self.assertEqual(b"", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
