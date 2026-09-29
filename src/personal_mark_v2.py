"""Strict, additive Personal Mark v2 domain model.

This module deliberately does not alter or reinterpret the v1 model.  It owns
the bounded JSON parser, the explicit Typed new-save confirmation boundary and
the fixed-plane Handwritten geometry used by the v2 foundation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
import math
from typing import Any, Mapping, TypeAlias

from personal_mark_unicode16 import UNICODE_VERSION, has_property, normalize_nfc


PERSONAL_MARK_V2_VERSION = 2
TYPED_MARK_TYPE = "typed"
HANDWRITTEN_MARK_TYPE = "handwritten"
MAX_LOCAL_RECORD_BYTES = 2 * 1024 * 1024
MAX_CONTAINER_DEPTH = 8
MAX_TEXT_SCALARS = 128
MAX_TEXT_UTF8_BYTES = 512
MAX_COORDINATE_DIMENSION = 16_384
MIN_COORDINATE_ASPECT = 1.0 / 16.0
MAX_COORDINATE_ASPECT = 16.0
MAX_STROKES = 128
MAX_POINTS_PER_STROKE = 4096
MAX_TOTAL_POINTS = 20_000
MAX_STROKE_DURATION_MS = 120_000
TEXT_POLICY_VERSION = f"shirushi-typed-text-unicode-{UNICODE_VERSION}-v1"

SUPPORTED_RENDER_PROFILE_ID = "shirushi-typed"
SUPPORTED_RENDER_PROFILE_VERSION = 1


class MarkV2Error(ValueError):
    """A validation error with a stable cross-language code."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


class RenderProfileState(str, Enum):
    ASSETS_UNAVAILABLE = "ASSETS_UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"


class EmbeddingReadiness(str, Enum):
    POLICY_UNKNOWN = "POLICY_UNKNOWN"


@dataclass(frozen=True)
class RenderProfileRef:
    id: str
    version: int

    def to_mapping(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version}


@dataclass(frozen=True)
class CoordinateSpace:
    """The one logical capture plane frozen for an entire Handwritten mark."""

    width: int
    height: int

    def to_mapping(self) -> dict[str, int]:
        return {"width": self.width, "height": self.height}


@dataclass(frozen=True)
class StrokePointV2:
    x: float
    y: float
    t: int

    def to_mapping(self) -> dict[str, float | int]:
        return {"x": self.x, "y": self.y, "t": self.t}


@dataclass(frozen=True)
class StrokeV2:
    points: tuple[StrokePointV2, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {"points": [point.to_mapping() for point in self.points]}


@dataclass(frozen=True)
class TypedPersonalMarkV2:
    text: str
    render_profile: RenderProfileRef
    version: int = PERSONAL_MARK_V2_VERSION
    type: str = TYPED_MARK_TYPE

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": self.type,
            "text": self.text,
            "renderProfile": self.render_profile.to_mapping(),
        }


@dataclass(frozen=True)
class HandwrittenPersonalMarkV2:
    coordinate_space: CoordinateSpace
    strokes: tuple[StrokeV2, ...]
    version: int = PERSONAL_MARK_V2_VERSION
    type: str = HANDWRITTEN_MARK_TYPE

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": self.type,
            "coordinateSpace": self.coordinate_space.to_mapping(),
            "strokes": [stroke.to_mapping() for stroke in self.strokes],
        }


PersonalMarkV2: TypeAlias = TypedPersonalMarkV2 | HandwrittenPersonalMarkV2


@dataclass(frozen=True)
class CodePointDifference:
    index: int
    original: tuple[int, ...]
    normalized: tuple[int, ...]


@dataclass(frozen=True)
class TypedSavePreparation:
    original: str
    normalized_candidate: str
    normalization_changed: bool
    differences: tuple[CodePointDifference, ...]
    special_code_points: tuple[int, ...]

    @property
    def requires_special_character_confirmation(self) -> bool:
        return bool(self.special_code_points)


@dataclass(frozen=True)
class RenderProfileResolution:
    profile: RenderProfileRef
    state: RenderProfileState
    error_code: str | None


@dataclass(frozen=True)
class CenterFitTransform:
    scale: float
    offset_x: float
    offset_y: float
    source_width: float
    source_height: float
    container_x: float
    container_y: float
    container_width: float
    container_height: float

    @property
    def displayed_width(self) -> float:
        return self.source_width * self.scale

    @property
    def displayed_height(self) -> float:
        return self.source_height * self.scale


def parse_personal_mark_v2(raw: bytes) -> PersonalMarkV2:
    """Parse one strict UTF-8 v2 record through the bounded parser boundary."""

    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_LOCAL_RECORD_BYTES:
        _fail("PAYLOAD_TOO_LARGE", "Personal Mark record exceeds the local byte limit")
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("TRANSPORT_BOM", "UTF-8 BOM is not permitted")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail("INVALID_UTF8", "Personal Mark record is not strict UTF-8", offset=exc.start)
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
    except MarkV2Error:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        _fail("MALFORMED_JSON", "Personal Mark record is not valid JSON", detail=str(exc))
    _reject_surrogates_in_value(value)
    return personal_mark_v2_from_mapping(value)


def encode_personal_mark_v2(mark: PersonalMarkV2) -> bytes:
    """Encode a validated mark for local storage; this is not JCS or C2PA."""

    validated = personal_mark_v2_from_mapping(mark.to_mapping())
    encoded = json.dumps(
        validated.to_mapping(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_LOCAL_RECORD_BYTES:
        _fail("PAYLOAD_TOO_LARGE", "Personal Mark record exceeds the local byte limit")
    return encoded


def personal_mark_v2_from_mapping(value: Any) -> PersonalMarkV2:
    if not isinstance(value, Mapping):
        _fail("ROOT_NOT_OBJECT", "Personal Mark root must be an object")
    _require_fields(value, {"version"}, "mark")
    version = _bounded_integer(value["version"], "version", PERSONAL_MARK_V2_VERSION, PERSONAL_MARK_V2_VERSION)
    if version != PERSONAL_MARK_V2_VERSION:  # defensive; bounded helper has already raised
        _fail("UNKNOWN_SCHEMA_VERSION", "unsupported Personal Mark schema version", version=version)
    _require_fields(value, {"type"}, "mark")
    mark_type = value["type"]
    if not isinstance(mark_type, str):
        _fail("INVALID_FIELD_TYPE", "type must be a string", field="type")
    if mark_type == TYPED_MARK_TYPE:
        return _typed_from_mapping(value)
    if mark_type == HANDWRITTEN_MARK_TYPE:
        return _handwritten_from_mapping(value)
    _fail("UNKNOWN_TYPE", "unsupported Personal Mark type", type=mark_type)


def prepare_typed_save(text: str) -> TypedSavePreparation:
    """Validate a draft without rewriting it and expose its NFC candidate."""

    _preflight_typed_draft_size(text)
    _validate_text_shape(text, require_nfc=False, enforce_limits=False)
    candidate = normalize_nfc(text)
    _validate_text_shape(candidate, require_nfc=True)
    return TypedSavePreparation(
        original=text,
        normalized_candidate=candidate,
        normalization_changed=text != candidate,
        differences=_code_point_differences(text, candidate),
        special_code_points=_special_code_points(candidate),
    )


def confirm_typed_save(
    preparation: TypedSavePreparation,
    *,
    accept_normalization: bool = False,
    accept_special_characters: bool = False,
    render_profile: RenderProfileRef | None = None,
) -> TypedPersonalMarkV2:
    """Construct a Typed mark only after every required confirmation."""

    if not isinstance(preparation, TypedSavePreparation):
        _fail("INVALID_FIELD_TYPE", "Typed save preparation has the wrong type")
    if type(accept_normalization) is not bool or type(accept_special_characters) is not bool:
        _fail("INVALID_FIELD_TYPE", "Typed save confirmation flags must be booleans")
    recomputed = prepare_typed_save(preparation.original)
    if recomputed != preparation:
        _fail(
            "CONFIRMATION_CONTEXT_MISMATCH",
            "Typed save preparation does not match its original text",
        )
    preparation = recomputed
    if preparation.normalization_changed and not accept_normalization:
        _fail(
            "NORMALIZATION_CONFIRMATION_REQUIRED",
            "NFC-changing text requires explicit confirmation",
        )
    if preparation.special_code_points and not accept_special_characters:
        _fail(
            "SPECIAL_CHARACTER_CONFIRMATION_REQUIRED",
            "joiners or variation selectors require explicit confirmation",
            code_points=preparation.special_code_points,
        )
    profile = render_profile or RenderProfileRef(
        SUPPORTED_RENDER_PROFILE_ID,
        SUPPORTED_RENDER_PROFILE_VERSION,
    )
    if resolve_render_profile(profile).state is RenderProfileState.UNSUPPORTED:
        _fail(
            "UNSUPPORTED_RENDER_PROFILE",
            "new Typed saves require a recognized render profile generation",
            profile_id=profile.id,
            profile_version=profile.version,
        )
    mark = TypedPersonalMarkV2(preparation.normalized_candidate, profile)
    return personal_mark_v2_from_mapping(mark.to_mapping())  # type: ignore[return-value]


def validate_typed_save_boundary(
    mark: TypedPersonalMarkV2,
    *,
    draft_text: str,
    accept_normalization: bool = False,
    accept_special_characters: bool = False,
) -> TypedPersonalMarkV2:
    """Recheck confirmations at the persistence boundary, bound to this text."""

    preparation = prepare_typed_save(draft_text)
    confirmed = confirm_typed_save(
        preparation,
        accept_normalization=accept_normalization,
        accept_special_characters=accept_special_characters,
        render_profile=mark.render_profile,
    )
    if confirmed.text != mark.text or confirmed.to_mapping() != mark.to_mapping():
        _fail("CONFIRMATION_CONTEXT_MISMATCH", "confirmation does not match the mark being saved")
    return confirmed


def resolve_render_profile(profile: RenderProfileRef) -> RenderProfileResolution:
    """Resolve rendering readiness without a font/system fallback."""

    if not isinstance(profile, RenderProfileRef):
        _fail("INVALID_FIELD_TYPE", "render profile reference has the wrong type")
    if not isinstance(profile.id, str):
        _fail("INVALID_FIELD_TYPE", "renderProfile.id must be a string", field="renderProfile.id")
    _reject_surrogates(profile.id)
    canonical_profile = RenderProfileRef(
        profile.id,
        _bounded_integer(profile.version, "renderProfile.version", 0, 2**53 - 1),
    )
    if (
        canonical_profile.id == SUPPORTED_RENDER_PROFILE_ID
        and canonical_profile.version == SUPPORTED_RENDER_PROFILE_VERSION
    ):
        return RenderProfileResolution(
            canonical_profile,
            RenderProfileState.ASSETS_UNAVAILABLE,
            "RENDER_ASSETS_UNAVAILABLE",
        )
    return RenderProfileResolution(
        canonical_profile,
        RenderProfileState.UNSUPPORTED,
        "UNSUPPORTED_RENDER_PROFILE",
    )


def assess_embedding_readiness(mark: PersonalMarkV2) -> EmbeddingReadiness:
    """F2B has no approved C2PA embedding policy."""

    if not isinstance(mark, (TypedPersonalMarkV2, HandwrittenPersonalMarkV2)):
        _fail("INVALID_FIELD_TYPE", "embedding assessment requires a Personal Mark v2 value")
    personal_mark_v2_from_mapping(mark.to_mapping())
    return EmbeddingReadiness.POLICY_UNKNOWN


def center_fit_transform(
    coordinate_space: CoordinateSpace,
    container_x: float,
    container_y: float,
    container_width: float,
    container_height: float,
) -> CenterFitTransform:
    if not isinstance(coordinate_space, CoordinateSpace):
        _fail("INVALID_FIELD_TYPE", "coordinateSpace has the wrong type")
    width = _bounded_integer(
        coordinate_space.width,
        "coordinateSpace.width",
        1,
        MAX_COORDINATE_DIMENSION,
    )
    height = _bounded_integer(
        coordinate_space.height,
        "coordinateSpace.height",
        1,
        MAX_COORDINATE_DIMENSION,
    )
    aspect = width / height
    if not MIN_COORDINATE_ASPECT <= aspect <= MAX_COORDINATE_ASPECT:
        _fail("OUT_OF_RANGE", "coordinateSpace aspect ratio is outside the supported range")
    for field, number in (
        ("container_x", container_x),
        ("container_y", container_y),
        ("container_width", container_width),
        ("container_height", container_height),
    ):
        if not _is_finite_number(number):
            _fail("INVALID_NUMBER", f"{field} must be finite", field=field)
    if container_width <= 0 or container_height <= 0:
        _fail("OUT_OF_RANGE", "container dimensions must be positive")
    source_width = float(width)
    source_height = float(height)
    scale = min(container_width / source_width, container_height / source_height)
    return CenterFitTransform(
        scale=scale,
        offset_x=container_x + (container_width - scale * source_width) / 2.0,
        offset_y=container_y + (container_height - scale * source_height) / 2.0,
        source_width=source_width,
        source_height=source_height,
        container_x=float(container_x),
        container_y=float(container_y),
        container_width=float(container_width),
        container_height=float(container_height),
    )


def map_normalized_point(
    transform: CenterFitTransform,
    x: float,
    y: float,
) -> tuple[float, float]:
    _validate_normalized_coordinate(x, "x")
    _validate_normalized_coordinate(y, "y")
    return (
        transform.offset_x + float(x) * transform.source_width * transform.scale,
        transform.offset_y + float(y) * transform.source_height * transform.scale,
    )


def inverse_map_point(
    transform: CenterFitTransform,
    pointer_x: float,
    pointer_y: float,
    *,
    clamp_active_stroke: bool = False,
) -> tuple[float, float] | None:
    if not _is_finite_number(pointer_x) or not _is_finite_number(pointer_y):
        _fail("INVALID_NUMBER", "pointer coordinates must be finite")
    x = (float(pointer_x) - transform.offset_x) / (transform.scale * transform.source_width)
    y = (float(pointer_y) - transform.offset_y) / (transform.scale * transform.source_height)
    if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
        return x, y
    if not clamp_active_stroke:
        return None
    return min(1.0, max(0.0, x)), min(1.0, max(0.0, y))


def _typed_from_mapping(value: Mapping[str, Any]) -> TypedPersonalMarkV2:
    _exact_fields(value, {"version", "type", "text", "renderProfile"}, "typed mark")
    text = value["text"]
    _validate_text_shape(text, require_nfc=True)
    profile_value = value["renderProfile"]
    if not isinstance(profile_value, Mapping):
        _fail("INVALID_FIELD_TYPE", "renderProfile must be an object", field="renderProfile")
    _exact_fields(profile_value, {"id", "version"}, "renderProfile")
    profile_id = profile_value["id"]
    if not isinstance(profile_id, str):
        _fail("INVALID_FIELD_TYPE", "renderProfile.id must be a string", field="renderProfile.id")
    _reject_surrogates(profile_id)
    profile_version = _bounded_integer(profile_value["version"], "renderProfile.version", 0, 2**53 - 1)
    return TypedPersonalMarkV2(text, RenderProfileRef(profile_id, profile_version))


def _handwritten_from_mapping(value: Mapping[str, Any]) -> HandwrittenPersonalMarkV2:
    _exact_fields(value, {"version", "type", "coordinateSpace", "strokes"}, "handwritten mark")
    raw_space = value["coordinateSpace"]
    if not isinstance(raw_space, Mapping):
        _fail("INVALID_FIELD_TYPE", "coordinateSpace must be an object", field="coordinateSpace")
    _exact_fields(raw_space, {"width", "height"}, "coordinateSpace")
    width = _bounded_integer(raw_space["width"], "coordinateSpace.width", 1, MAX_COORDINATE_DIMENSION)
    height = _bounded_integer(raw_space["height"], "coordinateSpace.height", 1, MAX_COORDINATE_DIMENSION)
    aspect = width / height
    if not MIN_COORDINATE_ASPECT <= aspect <= MAX_COORDINATE_ASPECT:
        _fail("OUT_OF_RANGE", "coordinateSpace aspect ratio is outside the supported range")

    raw_strokes = value["strokes"]
    if not isinstance(raw_strokes, list):
        _fail("INVALID_FIELD_TYPE", "strokes must be an array", field="strokes")
    if not raw_strokes:
        _fail("LIMIT_EXCEEDED", "saved Handwritten marks require at least one stroke", field="strokes")
    if len(raw_strokes) > MAX_STROKES:
        _fail("LIMIT_EXCEEDED", "too many strokes", limit=MAX_STROKES)
    strokes: list[StrokeV2] = []
    total_points = 0
    for stroke_index, raw_stroke in enumerate(raw_strokes):
        if not isinstance(raw_stroke, Mapping):
            _fail("INVALID_FIELD_TYPE", "stroke must be an object", stroke=stroke_index)
        _exact_fields(raw_stroke, {"points"}, "stroke")
        raw_points = raw_stroke["points"]
        if not isinstance(raw_points, list):
            _fail("INVALID_FIELD_TYPE", "points must be an array", stroke=stroke_index)
        if not raw_points:
            _fail("LIMIT_EXCEEDED", "saved strokes require at least one point", stroke=stroke_index)
        if len(raw_points) > MAX_POINTS_PER_STROKE:
            _fail("LIMIT_EXCEEDED", "too many points in stroke", stroke=stroke_index)
        total_points += len(raw_points)
        if total_points > MAX_TOTAL_POINTS:
            _fail("LIMIT_EXCEEDED", "too many points in Personal Mark", limit=MAX_TOTAL_POINTS)
        points: list[StrokePointV2] = []
        previous_t = -1
        for point_index, raw_point in enumerate(raw_points):
            if not isinstance(raw_point, Mapping):
                _fail("INVALID_FIELD_TYPE", "point must be an object", stroke=stroke_index, point=point_index)
            _exact_fields(raw_point, {"x", "y", "t"}, "point")
            x = _validate_normalized_coordinate(raw_point["x"], "x")
            y = _validate_normalized_coordinate(raw_point["y"], "y")
            t = _bounded_integer(raw_point["t"], "t", 0, MAX_STROKE_DURATION_MS)
            if point_index == 0 and t != 0:
                _fail("TIMING_INVALID", "first point timestamp must be zero", stroke=stroke_index)
            if t < previous_t:
                _fail("TIMING_INVALID", "point timestamps must be nondecreasing", stroke=stroke_index)
            previous_t = t
            points.append(StrokePointV2(x, y, t))
        strokes.append(StrokeV2(tuple(points)))
    return HandwrittenPersonalMarkV2(CoordinateSpace(width, height), tuple(strokes))


def _validate_text_shape(value: Any, *, require_nfc: bool, enforce_limits: bool = True) -> None:
    if not isinstance(value, str):
        _fail("INVALID_FIELD_TYPE", "text must be a string", field="text")
    _reject_surrogates(value)
    if not value:
        _fail("TEXT_EMPTY", "Typed Personal Mark text must not be empty")
    if enforce_limits and len(value) > MAX_TEXT_SCALARS:
        _fail("TEXT_TOO_LONG", "Typed text exceeds the scalar limit", limit=MAX_TEXT_SCALARS)
    encoded_length = len(value.encode("utf-8"))
    if enforce_limits and encoded_length > MAX_TEXT_UTF8_BYTES:
        _fail("TEXT_UTF8_TOO_LONG", "Typed text exceeds the UTF-8 byte limit", limit=MAX_TEXT_UTF8_BYTES)
    if require_nfc and normalize_nfc(value) != value:
        _fail("TEXT_NOT_NFC", "Typed text must already be NFC")
    first = ord(value[0])
    last = ord(value[-1])
    if has_property(first, "white_space") or has_property(last, "white_space"):
        _fail("TEXT_EDGE_WHITESPACE", "leading or trailing Unicode White_Space is not permitted")
    has_visible_base = False
    for index, character in enumerate(value):
        code_point = ord(character)
        if code_point <= 0x1F or 0x7F <= code_point <= 0x9F or code_point in (0x2028, 0x2029):
            _fail("TEXT_CONTROL", "control or line-separator character is not permitted", index=index, code_point=code_point)
        if has_property(code_point, "bidi_control"):
            _fail("TEXT_BIDI_CONTROL", "Bidi_Control is not permitted", index=index, code_point=code_point)
        special = code_point in (0x200C, 0x200D) or has_property(code_point, "variation_selector")
        if (
            has_property(code_point, "format")
            or has_property(code_point, "default_ignorable")
        ) and not special:
            _fail(
                "TEXT_FORBIDDEN_INVISIBLE",
                "format/default-ignorable character is not permitted",
                index=index,
                code_point=code_point,
            )
        if has_property(code_point, "visible_base") and not has_property(code_point, "default_ignorable"):
            has_visible_base = True
    if not has_visible_base:
        _fail("TEXT_VISIBLE_BASE_REQUIRED", "Typed text requires a permitted visible-base character")


def _preflight_typed_draft_size(value: Any) -> None:
    """Bound draft work before Unicode property scans or normalization."""

    if not isinstance(value, str):
        _fail("INVALID_FIELD_TYPE", "text must be a string", field="text")
    if len(value) > MAX_LOCAL_RECORD_BYTES:
        _fail("PAYLOAD_TOO_LARGE", "Typed draft exceeds the pre-normalization resource limit")
    utf8_bytes = 0
    for index, character in enumerate(value):
        code_point = ord(character)
        if 0xD800 <= code_point <= 0xDFFF:
            _fail("MALFORMED_UNICODE", "lone surrogate is not permitted", index=index)
        if code_point <= 0x7F:
            utf8_bytes += 1
        elif code_point <= 0x7FF:
            utf8_bytes += 2
        elif code_point <= 0xFFFF:
            utf8_bytes += 3
        else:
            utf8_bytes += 4
        if utf8_bytes > MAX_LOCAL_RECORD_BYTES:
            _fail("PAYLOAD_TOO_LARGE", "Typed draft exceeds the pre-normalization resource limit")


def _special_code_points(value: str) -> tuple[int, ...]:
    return tuple(
        ord(character)
        for character in value
        if ord(character) in (0x200C, 0x200D)
        or has_property(ord(character), "variation_selector")
    )


def _code_point_differences(original: str, candidate: str) -> tuple[CodePointDifference, ...]:
    # One stable prefix/suffix diff is sufficient for an explicit UI code-point
    # disclosure and avoids pretending normalization is a simple edit script.
    if original == candidate:
        return ()
    prefix = 0
    while prefix < min(len(original), len(candidate)) and original[prefix] == candidate[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < len(original) - prefix
        and suffix < len(candidate) - prefix
        and original[len(original) - suffix - 1] == candidate[len(candidate) - suffix - 1]
    ):
        suffix += 1
    original_end = len(original) - suffix if suffix else len(original)
    candidate_end = len(candidate) - suffix if suffix else len(candidate)
    return (
        CodePointDifference(
            prefix,
            tuple(ord(character) for character in original[prefix:original_end]),
            tuple(ord(character) for character in candidate[prefix:candidate_end]),
        ),
    )


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
            if depth > MAX_CONTAINER_DEPTH:
                _fail("MAX_DEPTH_EXCEEDED", "Personal Mark JSON exceeds the maximum container depth")
        elif character in "]}":
            depth -= 1
            if depth < 0:
                _fail("MALFORMED_JSON", "Personal Mark JSON has unbalanced containers")
    if in_string or depth != 0:
        _fail("MALFORMED_JSON", "Personal Mark JSON is incomplete")


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _reject_surrogates(key)
        if key in result:
            _fail("DUPLICATE_KEY", "duplicate decoded JSON member name", key=key)
        result[key] = value
    return result


def _reject_nonfinite_token(token: str) -> float:
    _fail("INVALID_NUMBER", "non-finite JSON number is not permitted", token=token)


def _parse_finite_float(token: str) -> float:
    try:
        value = float(token)
    except (ValueError, OverflowError):
        _fail("INVALID_NUMBER", "invalid JSON number", token=token)
    if not math.isfinite(value):
        _fail("INVALID_NUMBER", "non-finite or overflowing JSON number is not permitted", token=token)
    if value == 0.0:
        try:
            if Decimal(token) != 0:
                _fail("INVALID_NUMBER", "underflowing JSON number is not permitted", token=token)
        except InvalidOperation:
            _fail("INVALID_NUMBER", "invalid JSON number", token=token)
    return value


def _parse_json_integer(token: str) -> int:
    # Reject absurd integer tokens before arbitrary-precision conversion.  All
    # domain integer limits are far below this defensive lexical ceiling.
    digits = token[1:] if token.startswith("-") else token
    if len(digits) > 309:
        _fail("INVALID_NUMBER", "JSON integer exceeds finite binary64 range")
    return int(token)


def _reject_surrogates(value: str) -> None:
    for index, character in enumerate(value):
        if 0xD800 <= ord(character) <= 0xDFFF:
            _fail("MALFORMED_UNICODE", "lone surrogate is not permitted", index=index)


def _reject_surrogates_in_value(value: Any) -> None:
    if isinstance(value, str):
        _reject_surrogates(value)
    elif isinstance(value, Mapping):
        for key, child in value.items():
            _reject_surrogates(key)
            _reject_surrogates_in_value(child)
    elif isinstance(value, list):
        for child in value:
            _reject_surrogates_in_value(child)


def _require_fields(value: Mapping[str, Any], required: set[str], context: str) -> None:
    missing = required - set(value)
    if missing:
        _fail("MISSING_FIELD", f"{context} is missing required fields", fields=tuple(sorted(missing)))


def _exact_fields(value: Mapping[str, Any], expected: set[str], context: str) -> None:
    _require_fields(value, expected, context)
    unknown = set(value) - expected
    if unknown:
        _fail(
            "UNKNOWN_FIELD",
            f"{context} contains unknown fields",
            fields=tuple(sorted((str(field) for field in unknown))),
        )


def _bounded_integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("INVALID_FIELD_TYPE", f"{field} must be an integer", field=field)
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("INVALID_NUMBER", f"{field} must be finite", field=field)
        if not value.is_integer():
            _fail("INVALID_FIELD_TYPE", f"{field} must be an integer", field=field)
    integer = int(value)
    if not minimum <= integer <= maximum:
        code = "UNKNOWN_SCHEMA_VERSION" if field == "version" else "OUT_OF_RANGE"
        _fail(code, f"{field} is outside the supported range", field=field, value=integer)
    return integer


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _validate_normalized_coordinate(value: Any, field: str) -> float:
    if not _is_finite_number(value):
        code = "INVALID_FIELD_TYPE" if isinstance(value, bool) or not isinstance(value, (int, float)) else "INVALID_NUMBER"
        _fail(code, f"{field} must be a finite number", field=field)
    number = float(value)
    if not 0.0 <= number <= 1.0:
        _fail("OUT_OF_RANGE", f"{field} must be within 0..1", field=field)
    return number


def _fail(code: str, message: str, **details: Any) -> None:
    raise MarkV2Error(code, message, **details)
