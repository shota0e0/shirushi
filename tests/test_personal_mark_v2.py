from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import sys
import unittest


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from personal_mark_v2 import (
    CoordinateSpace,
    EmbeddingReadiness,
    HandwrittenPersonalMarkV2,
    MarkV2Error,
    RenderProfileRef,
    RenderProfileState,
    StrokePointV2,
    StrokeV2,
    TypedPersonalMarkV2,
    assess_embedding_readiness,
    center_fit_transform,
    confirm_typed_save,
    encode_personal_mark_v2,
    inverse_map_point,
    map_normalized_point,
    parse_personal_mark_v2,
    personal_mark_v2_from_mapping,
    prepare_typed_save,
    resolve_render_profile,
)


def typed_mapping(text: str = "森 / Mori") -> dict:
    return {
        "version": 2,
        "type": "typed",
        "text": text,
        "renderProfile": {"id": "shirushi-typed", "version": 1},
    }


def handwritten_mapping() -> dict:
    return {
        "version": 2,
        "type": "handwritten",
        "coordinateSpace": {"width": 480, "height": 220},
        "strokes": [
            {"points": [{"x": 0.12, "y": 0.43, "t": 0}, {"x": 0.14, "y": 0.42, "t": 16}]}
        ],
    }


class PersonalMarkV2ModelTests(unittest.TestCase):
    def assert_code(self, code: str, callback) -> None:
        with self.assertRaises(MarkV2Error) as caught:
            callback()
        self.assertEqual(code, caught.exception.code)

    def test_typed_and_handwritten_exact_roundtrip(self) -> None:
        for mapping in (typed_mapping(), handwritten_mapping()):
            mark = personal_mark_v2_from_mapping(mapping)
            self.assertEqual(mapping, mark.to_mapping())
            self.assertEqual(mark, parse_personal_mark_v2(encode_personal_mark_v2(mark)))

    def test_json_integer_semantics_accept_integral_float_but_reject_bool(self) -> None:
        value = typed_mapping()
        value["version"] = 2.0
        value["renderProfile"]["version"] = 1.0
        mark = personal_mark_v2_from_mapping(value)
        self.assertEqual(2, mark.version)
        self.assertEqual(1, mark.render_profile.version)
        value["version"] = True
        self.assert_code("INVALID_FIELD_TYPE", lambda: personal_mark_v2_from_mapping(value))

    def test_unknown_fields_versions_and_types_are_distinct(self) -> None:
        value = typed_mapping()
        value["extra"] = 1
        self.assert_code("UNKNOWN_FIELD", lambda: personal_mark_v2_from_mapping(value))
        value = typed_mapping()
        value["version"] = 3
        self.assert_code("UNKNOWN_SCHEMA_VERSION", lambda: personal_mark_v2_from_mapping(value))
        self.assert_code(
            "UNKNOWN_SCHEMA_VERSION",
            lambda: personal_mark_v2_from_mapping({"version": 3}),
        )
        value = typed_mapping()
        value["type"] = "future"
        self.assert_code("UNKNOWN_TYPE", lambda: personal_mark_v2_from_mapping(value))

    def test_parser_rejects_transport_and_structure_hazards(self) -> None:
        self.assert_code("TRANSPORT_BOM", lambda: parse_personal_mark_v2(b"\xef\xbb\xbf{}"))
        self.assert_code("INVALID_UTF8", lambda: parse_personal_mark_v2(b"\xff"))
        self.assert_code("MALFORMED_JSON", lambda: parse_personal_mark_v2(b"{"))
        self.assert_code(
            "DUPLICATE_KEY",
            lambda: parse_personal_mark_v2(b'{"text":"a","\\u0074ext":"b"}'),
        )
        nested = b'{"a":{"b":{"c":{"d":{"e":{"f":{"g":{"h":[]}}}}}}}}'
        self.assert_code("MAX_DEPTH_EXCEEDED", lambda: parse_personal_mark_v2(nested))
        self.assert_code("PAYLOAD_TOO_LARGE", lambda: parse_personal_mark_v2(b" " * (2 * 1024 * 1024 + 1)))
        self.assert_code(
            "MALFORMED_UNICODE",
            lambda: parse_personal_mark_v2(
                b'{"version":2,"type":"typed","text":"\\ud800","renderProfile":{"id":"shirushi-typed","version":1}}'
            ),
        )

    def test_nonfinite_overflow_and_coordinate_bounds_are_rejected(self) -> None:
        raw = json.dumps(handwritten_mapping()).replace("0.12", "1e400").encode()
        self.assert_code("INVALID_NUMBER", lambda: parse_personal_mark_v2(raw))
        value = handwritten_mapping()
        value["strokes"][0]["points"][0]["x"] = False
        self.assert_code("INVALID_FIELD_TYPE", lambda: personal_mark_v2_from_mapping(value))
        value = handwritten_mapping()
        value["strokes"][0]["points"][0]["x"] = -0.01
        self.assert_code("OUT_OF_RANGE", lambda: personal_mark_v2_from_mapping(value))
        raw = json.dumps(handwritten_mapping()).replace("0.12", "1e-4000").encode()
        self.assert_code("INVALID_NUMBER", lambda: parse_personal_mark_v2(raw))

    def test_coordinate_space_aspect_and_timing_rules(self) -> None:
        value = handwritten_mapping()
        value["coordinateSpace"] = {"width": 16384, "height": 1}
        self.assert_code("OUT_OF_RANGE", lambda: personal_mark_v2_from_mapping(value))
        value = handwritten_mapping()
        value["strokes"][0]["points"][0]["t"] = 1
        self.assert_code("TIMING_INVALID", lambda: personal_mark_v2_from_mapping(value))
        value = handwritten_mapping()
        value["strokes"][0]["points"][1]["t"] = -1
        self.assert_code("OUT_OF_RANGE", lambda: personal_mark_v2_from_mapping(value))

    def test_point_limits_are_enforced_without_simplification(self) -> None:
        value = handwritten_mapping()
        point = {"x": 0.5, "y": 0.5, "t": 0}
        value["strokes"] = [{"points": [point.copy() for _ in range(4097)]}]
        self.assert_code("LIMIT_EXCEEDED", lambda: personal_mark_v2_from_mapping(value))
        value["strokes"] = [
            {"points": [point.copy() for _ in range(4001)]}
            for _ in range(5)
        ]
        self.assert_code("LIMIT_EXCEEDED", lambda: personal_mark_v2_from_mapping(value))

    def test_unicode_preparation_and_confirmations(self) -> None:
        preparation = prepare_typed_save("Cafe\u0301")
        self.assertTrue(preparation.normalization_changed)
        self.assertEqual("Caf\u00e9", preparation.normalized_candidate)
        self.assertTrue(preparation.differences)
        self.assert_code(
            "NORMALIZATION_CONFIRMATION_REQUIRED",
            lambda: confirm_typed_save(preparation),
        )
        mark = confirm_typed_save(preparation, accept_normalization=True)
        self.assertEqual("Caf\u00e9", mark.text)

        joiner = prepare_typed_save("A\u200dB")
        self.assertEqual((0x200D,), joiner.special_code_points)
        self.assert_code(
            "SPECIAL_CHARACTER_CONFIRMATION_REQUIRED",
            lambda: confirm_typed_save(joiner),
        )
        self.assertEqual("A\u200dB", confirm_typed_save(joiner, accept_special_characters=True).text)

    def test_forged_typed_preparation_cannot_bypass_confirmation(self) -> None:
        preparation = prepare_typed_save("Cafe\u0301")
        forged = replace(preparation, normalization_changed=False, differences=())
        self.assert_code(
            "CONFIRMATION_CONTEXT_MISMATCH",
            lambda: confirm_typed_save(forged),
        )

    def test_unicode_rejections_never_repair(self) -> None:
        cases = (
            (" A", "TEXT_EDGE_WHITESPACE"),
            ("A\nB", "TEXT_CONTROL"),
            ("A\u202eB", "TEXT_BIDI_CONTROL"),
            ("A\u200bB", "TEXT_FORBIDDEN_INVISIBLE"),
            ("\u0301", "TEXT_VISIBLE_BASE_REQUIRED"),
        )
        for text, code in cases:
            with self.subTest(text=text):
                self.assert_code(code, lambda text=text: prepare_typed_save(text))

    def test_typed_draft_is_resource_bounded_before_normalization(self) -> None:
        self.assert_code(
            "PAYLOAD_TOO_LARGE",
            lambda: prepare_typed_save("A" * (2 * 1024 * 1024 + 1)),
        )

    def test_confirmation_flags_require_actual_booleans(self) -> None:
        preparation = prepare_typed_save("Cafe\u0301")
        self.assert_code(
            "INVALID_FIELD_TYPE",
            lambda: confirm_typed_save(preparation, accept_normalization="yes"),  # type: ignore[arg-type]
        )
        joiner = prepare_typed_save("A\u200dB")
        self.assert_code(
            "INVALID_FIELD_TYPE",
            lambda: confirm_typed_save(joiner, accept_special_characters=1),  # type: ignore[arg-type]
        )

    def test_render_profile_version_is_separate_and_never_falls_back(self) -> None:
        known = resolve_render_profile(RenderProfileRef("shirushi-typed", 1))
        self.assertEqual(RenderProfileState.ASSETS_UNAVAILABLE, known.state)
        self.assertEqual("RENDER_ASSETS_UNAVAILABLE", known.error_code)
        unknown = resolve_render_profile(RenderProfileRef("shirushi-typed", 2))
        self.assertEqual(RenderProfileState.UNSUPPORTED, unknown.state)
        self.assertEqual("UNSUPPORTED_RENDER_PROFILE", unknown.error_code)
        value = typed_mapping()
        value["renderProfile"]["version"] = 2
        parsed = personal_mark_v2_from_mapping(value)
        self.assertEqual(2, parsed.render_profile.version)
        self.assert_code(
            "UNSUPPORTED_RENDER_PROFILE",
            lambda: confirm_typed_save(
                prepare_typed_save("Mori"),
                render_profile=RenderProfileRef("shirushi-typed", 2),
            ),
        )

    def test_direct_render_profile_resolution_validates_before_registry_lookup(self) -> None:
        self.assert_code(
            "INVALID_FIELD_TYPE",
            lambda: resolve_render_profile(RenderProfileRef("shirushi-typed", True)),  # type: ignore[arg-type]
        )
        self.assert_code(
            "OUT_OF_RANGE",
            lambda: resolve_render_profile(RenderProfileRef("shirushi-typed", -1)),
        )
        self.assert_code(
            "OUT_OF_RANGE",
            lambda: resolve_render_profile(RenderProfileRef("shirushi-typed", 2**53)),
        )
        canonical = resolve_render_profile(RenderProfileRef("shirushi-typed", 1.0))  # type: ignore[arg-type]
        self.assertIs(type(canonical.profile.version), int)
        self.assertEqual(RenderProfileState.ASSETS_UNAVAILABLE, canonical.state)

    def test_uniform_center_fit_and_inverse_preserve_fixed_plane(self) -> None:
        transform = center_fit_transform(CoordinateSpace(480, 220), 0, 0, 300, 180)
        self.assertEqual(0.625, transform.scale)
        self.assertEqual((36.0, 80.375), map_normalized_point(transform, 0.12, 0.43))
        mapped = map_normalized_point(transform, 0.73, 0.31)
        inverse = inverse_map_point(transform, *mapped)
        self.assertIsNotNone(inverse)
        assert inverse is not None
        self.assertAlmostEqual(0.73, inverse[0])
        self.assertAlmostEqual(0.31, inverse[1])
        self.assertIsNone(inverse_map_point(transform, 10, 5))
        clamped = inverse_map_point(transform, 10, 5, clamp_active_stroke=True)
        self.assertIsNotNone(clamped)
        assert clamped is not None
        self.assertAlmostEqual(1.0 / 30.0, clamped[0])
        self.assertEqual(0.0, clamped[1])

    def test_embedding_policy_is_always_unknown(self) -> None:
        mark = personal_mark_v2_from_mapping(handwritten_mapping())
        self.assertEqual(EmbeddingReadiness.POLICY_UNKNOWN, assess_embedding_readiness(mark))
        malformed = HandwrittenPersonalMarkV2(CoordinateSpace(0, 220), ())
        self.assert_code("OUT_OF_RANGE", lambda: assess_embedding_readiness(malformed))


if __name__ == "__main__":
    unittest.main()
