"""Bounded read-only NDJSON bridge for the F2C.1 desktop foundation."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import math
import re
from typing import Any, BinaryIO

from personal_mark_v2_store import PersonalMarkV2Store


BRIDGE_PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 16_384
MAX_RESPONSE_BYTES = 4_194_304
MAX_JSON_DEPTH = 16
MAX_SESSION_REQUEST_IDS = 4_096

GET_CAPABILITIES_METHOD = "get_capabilities"
LOAD_PERSONAL_MARK_METHOD = "load_personal_mark"
ALLOWED_METHODS = frozenset({GET_CAPABILITIES_METHOD, LOAD_PERSONAL_MARK_METHOD})
_REQUEST_FIELDS = frozenset({"protocolVersion", "requestId", "method", "params"})
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)


class BridgeProtocolError(ValueError):
    """Safe structured bridge failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        fatal: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id
        self.fatal = fatal


def get_capabilities() -> dict[str, Any]:
    """Return only functionality implemented in this bridge slice."""

    return {
        "bridgeProtocolVersion": 1,
        "personalMarkSchemaVersions": [1, 2],
        "renderProfiles": [
            {
                "id": "shirushi-typed",
                "version": 1,
                "state": "ASSETS_UNAVAILABLE",
            }
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


def load_personal_mark(store: PersonalMarkV2Store | None = None) -> dict[str, Any]:
    """Expose the existing F2B read envelope without bytes, paths or repair."""

    try:
        active_store = store if store is not None else PersonalMarkV2Store()
        return active_store.load().to_envelope()
    except Exception as exc:
        raise BridgeProtocolError(
            "PERSONAL_MARK_READ_FAILED",
            "Personal Mark could not be read.",
            fatal=False,
        ) from exc


def serve(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    *,
    store: PersonalMarkV2Store | None = None,
) -> int:
    """Serve one bounded session until clean EOF or a fatal protocol error."""

    used_request_ids: set[str] = set()
    while True:
        current_request_id: str | None = None
        try:
            frame = _read_request_frame(input_stream)
            if frame is None:
                return 0
            request = _parse_request(frame)
            request_id = request["requestId"]
            current_request_id = request_id

            if request_id in used_request_ids:
                error = BridgeProtocolError(
                    "DUPLICATE_REQUEST_ID",
                    "requestId was already used in this session.",
                    request_id=request_id,
                )
                _write_error(output_stream, error)
                return 1
            if len(used_request_ids) >= MAX_SESSION_REQUEST_IDS:
                error = BridgeProtocolError(
                    "SESSION_EXHAUSTED",
                    "The bridge session request budget is exhausted.",
                    request_id=request_id,
                )
                _write_error(output_stream, error)
                return 1
            used_request_ids.add(request_id)

            method = request["method"]
            if method not in ALLOWED_METHODS:
                _write_error(
                    output_stream,
                    BridgeProtocolError(
                        "UNKNOWN_METHOD",
                        "The requested method is not allowed.",
                        request_id=request_id,
                        fatal=False,
                    ),
                )
                continue
            if not isinstance(request["params"], dict) or request["params"] != {}:
                _write_error(
                    output_stream,
                    BridgeProtocolError(
                        "INVALID_PARAMS",
                        "This method requires an empty params object.",
                        request_id=request_id,
                        fatal=False,
                    ),
                )
                continue

            if method == GET_CAPABILITIES_METHOD:
                result = get_capabilities()
            elif method == LOAD_PERSONAL_MARK_METHOD:
                try:
                    result = load_personal_mark(store)
                except BridgeProtocolError as exc:
                    raise BridgeProtocolError(
                        exc.code,
                        exc.message,
                        request_id=request_id,
                        fatal=exc.fatal,
                    ) from exc
            else:  # The allowlist and explicit branches must remain in lockstep.
                raise AssertionError("unreachable bridge method")
            if not _write_success(output_stream, request_id, result):
                return 1
        except BridgeProtocolError as exc:
            try:
                _write_error(output_stream, exc)
            except (OSError, BridgeProtocolError):
                return 1
            if exc.fatal:
                return 1
        except OSError:
            return 1
        except Exception:
            try:
                _write_error(
                    output_stream,
                    BridgeProtocolError(
                        "INTERNAL_ERROR",
                        "The bridge could not complete the request.",
                        request_id=current_request_id,
                    ),
                )
            except (OSError, BridgeProtocolError):
                pass
            return 1


def _read_request_frame(stream: BinaryIO) -> bytes | None:
    try:
        raw = stream.readline(MAX_REQUEST_BYTES + 2)
    except OSError as exc:
        raise BridgeProtocolError(
            "TRANSPORT_READ_FAILED",
            "The request stream could not be read.",
        ) from exc
    if raw == b"":
        return None
    if raw.endswith(b"\n"):
        payload = raw[:-1]
        if len(payload) > MAX_REQUEST_BYTES:
            raise BridgeProtocolError(
                "REQUEST_TOO_LARGE",
                "The request exceeds the bridge byte limit.",
            )
        return payload
    if len(raw) > MAX_REQUEST_BYTES:
        raise BridgeProtocolError(
            "REQUEST_TOO_LARGE",
            "The request exceeds the bridge byte limit.",
        )
    raise BridgeProtocolError(
        "PARTIAL_REQUEST",
        "The request ended before its newline delimiter.",
    )


def _parse_request(raw: bytes) -> dict[str, Any]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise BridgeProtocolError("TRANSPORT_BOM", "A transport BOM is not permitted.")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise BridgeProtocolError("INVALID_UTF8", "The request is not strict UTF-8.") from exc
    _reject_surrogates(text)
    _preflight_json_depth(text)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_nonfinite_token,
            parse_float=_parse_finite_float,
            parse_int=_parse_json_integer,
        )
    except BridgeProtocolError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise BridgeProtocolError("MALFORMED_JSON", "The request JSON is malformed.") from exc
    _reject_surrogates_in_value(value)
    return _validate_envelope(value)


def _validate_envelope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BridgeProtocolError("INVALID_ENVELOPE", "The request root must be an object.")

    request_id: str | None = None
    if "requestId" in value:
        candidate = value["requestId"]
        if not isinstance(candidate, str) or _REQUEST_ID_PATTERN.fullmatch(candidate) is None:
            raise BridgeProtocolError("INVALID_REQUEST_ID", "requestId is invalid.")
        request_id = candidate

    if set(value) != _REQUEST_FIELDS:
        raise BridgeProtocolError(
            "INVALID_ENVELOPE",
            "The request envelope fields are invalid.",
            request_id=request_id,
        )
    protocol_version = value["protocolVersion"]
    if type(protocol_version) is not int or protocol_version != BRIDGE_PROTOCOL_VERSION:
        raise BridgeProtocolError(
            "UNSUPPORTED_PROTOCOL_VERSION",
            "The bridge protocol version is unsupported.",
            request_id=request_id,
        )
    method = value["method"]
    if not isinstance(method, str):
        raise BridgeProtocolError(
            "INVALID_ENVELOPE",
            "method must be a string.",
            request_id=request_id,
        )
    return value


def _write_success(
    stream: BinaryIO,
    request_id: str,
    result: dict[str, Any],
) -> bool:
    response = {
        "protocolVersion": BRIDGE_PROTOCOL_VERSION,
        "requestId": request_id,
        "ok": True,
        "result": result,
    }
    try:
        encoded = _encode_response(response)
    except BridgeProtocolError as exc:
        _write_error(
            stream,
            BridgeProtocolError(
                exc.code,
                exc.message,
                request_id=request_id,
            ),
        )
        return False
    _write_encoded(stream, encoded)
    return True


def _write_error(stream: BinaryIO, error: BridgeProtocolError) -> None:
    response = {
        "protocolVersion": BRIDGE_PROTOCOL_VERSION,
        "requestId": error.request_id,
        "ok": False,
        "error": {"code": error.code, "message": error.message},
    }
    _write_encoded(stream, _encode_response(response))


def _encode_response(response: dict[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            response,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BridgeProtocolError(
            "INTERNAL_ERROR",
            "The bridge could not encode its response.",
        ) from exc
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise BridgeProtocolError(
            "RESPONSE_TOO_LARGE",
            "The bridge response exceeds its byte limit.",
        )
    return encoded


def _write_encoded(stream: BinaryIO, encoded: bytes) -> None:
    stream.write(encoded + b"\n")
    stream.flush()


def _preflight_json_depth(text: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise BridgeProtocolError(
                    "MAX_DEPTH_EXCEEDED",
                    "The request exceeds the JSON depth limit.",
                )
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise BridgeProtocolError("MALFORMED_JSON", "The request JSON is malformed.")
    if in_string or depth != 0:
        raise BridgeProtocolError("MALFORMED_JSON", "The request JSON is malformed.")


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _reject_surrogates(key)
        if key in result:
            raise BridgeProtocolError("DUPLICATE_KEY", "Duplicate JSON member names are not permitted.")
        result[key] = value
    return result


def _reject_nonfinite_token(_token: str) -> float:
    raise BridgeProtocolError("INVALID_NUMBER", "Non-finite JSON numbers are not permitted.")


def _parse_finite_float(token: str) -> float:
    try:
        value = float(token)
    except (ValueError, OverflowError) as exc:
        raise BridgeProtocolError("INVALID_NUMBER", "The request contains an invalid number.") from exc
    if not math.isfinite(value):
        raise BridgeProtocolError("INVALID_NUMBER", "Non-finite JSON numbers are not permitted.")
    if value == 0.0:
        try:
            if Decimal(token) != 0:
                raise BridgeProtocolError("INVALID_NUMBER", "Underflowing JSON numbers are not permitted.")
        except InvalidOperation as exc:
            raise BridgeProtocolError("INVALID_NUMBER", "The request contains an invalid number.") from exc
    return value


def _parse_json_integer(token: str) -> int:
    digits = token[1:] if token.startswith("-") else token
    if len(digits) > 309:
        raise BridgeProtocolError("INVALID_NUMBER", "The request integer is outside the supported domain.")
    return int(token)


def _reject_surrogates(value: str) -> None:
    for character in value:
        if 0xD800 <= ord(character) <= 0xDFFF:
            raise BridgeProtocolError("MALFORMED_UNICODE", "Lone surrogates are not permitted.")


def _reject_surrogates_in_value(value: Any) -> None:
    if isinstance(value, str):
        _reject_surrogates(value)
    elif isinstance(value, dict):
        for key, child in value.items():
            _reject_surrogates(key)
            _reject_surrogates_in_value(child)
    elif isinstance(value, list):
        for child in value:
            _reject_surrogates_in_value(child)
