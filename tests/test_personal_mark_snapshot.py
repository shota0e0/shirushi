from __future__ import annotations

from dataclasses import FrozenInstanceError
import ast
from pathlib import Path
import sys
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from personal_mark_snapshot import (
    FrozenArray,
    FrozenObject,
    SNAPSHOT_ENCODING_HEADER,
    SNAPSHOT_ENCODING_VERSION,
    PreflightSnapshot,
    SnapshotValidationError,
    SnapshotVersions,
    TargetFingerprint,
    create_preflight_snapshot,
    thaw_json_value,
)
from personal_mark_v2 import personal_mark_v2_from_mapping


def mark():
    return personal_mark_v2_from_mapping(
        {
            "version": 2,
            "type": "handwritten",
            "coordinateSpace": {"width": 480, "height": 220},
            "strokes": [{"points": [{"x": 0.12, "y": 0.43, "t": 0}]}],
        }
    )


def versions(**overrides) -> SnapshotVersions:
    values = {
        "rights_schema_version": "rights-v1",
        "preflight_protocol_version": "preflight-v1",
        "bridge_protocol_version": "bridge-v0-foundation",
        "core_service_contract_version": "1.0",
        "text_policy_version": "shirushi-typed-text-unicode-16.0.0-v1",
    }
    values.update(overrides)
    return SnapshotVersions(**values)


def snapshot(rights=None, generation: int = 7, snapshot_versions=None) -> PreflightSnapshot:
    return create_preflight_snapshot(
        target_fingerprint=TargetFingerprint("sha256", "a" * 64),
        rights_intent=rights or {"rights": "reserved", "territories": ["JP", "US"]},
        personal_mark=mark(),
        versions=snapshot_versions or versions(),
        operation_generation=generation,
    )


class PersonalMarkSnapshotTests(unittest.TestCase):
    def test_snapshot_is_deeply_immutable_and_detached(self) -> None:
        rights = {"rights": "reserved", "nested": {"allowed": ["JP"]}}
        value = snapshot(rights)
        before = value.to_mapping()
        rights["nested"]["allowed"].append("US")
        self.assertEqual(before, value.to_mapping())
        with self.assertRaises(FrozenInstanceError):
            value.operation_generation = 9  # type: ignore[misc]

    def test_mutable_mark_impostor_cannot_bypass_snapshot_detachment(self) -> None:
        class MutableMarkImpostor:
            def __init__(self) -> None:
                self.value = mark().to_mapping()

            def to_mapping(self):
                return self.value

            def __eq__(self, _other) -> bool:
                return True

        with self.assertRaises(SnapshotValidationError) as caught:
            PreflightSnapshot(
                target_fingerprint=TargetFingerprint("sha256", "a" * 64),
                rights_intent=FrozenObject((("rights", "reserved"),)),
                personal_mark=MutableMarkImpostor(),  # type: ignore[arg-type]
                versions=versions(),
                operation_generation=1,
            )
        self.assertEqual("INVALID_PERSONAL_MARK", caught.exception.code)

    def test_deterministic_encoding_is_order_independent_and_explicitly_not_jcs(self) -> None:
        first = snapshot({"b": 2, "a": 1})
        second = snapshot({"a": 1, "b": 2})
        self.assertEqual(first.deterministic_bytes(), second.deterministic_bytes())
        self.assertEqual(first.sha256_digest(), second.sha256_digest())
        self.assertTrue(first.deterministic_bytes().startswith(SNAPSHOT_ENCODING_HEADER))
        self.assertEqual("shirushi-python-deterministic-v1", SNAPSHOT_ENCODING_VERSION)
        self.assertFalse(first.deterministic_bytes().startswith(b"{"))

    def test_every_bound_context_changes_the_authoritative_digest(self) -> None:
        baseline = snapshot()
        changed_rights = snapshot({"rights": "licensed"})
        changed_generation = snapshot(generation=8)
        changed_versions = snapshot(snapshot_versions=versions(preflight_protocol_version="preflight-v2"))
        changed_target = create_preflight_snapshot(
            target_fingerprint=TargetFingerprint("sha256", "b" * 64),
            rights_intent={"rights": "reserved", "territories": ["JP", "US"]},
            personal_mark=mark(),
            versions=versions(),
            operation_generation=7,
        )
        digests = {
            baseline.sha256_digest(),
            changed_rights.sha256_digest(),
            changed_generation.sha256_digest(),
            changed_versions.sha256_digest(),
            changed_target.sha256_digest(),
        }
        self.assertEqual(5, len(digests))

    def test_framing_distinguishes_types_and_boundaries(self) -> None:
        self.assertNotEqual(snapshot({"value": 1}).sha256_digest(), snapshot({"value": 1.0}).sha256_digest())
        self.assertNotEqual(snapshot({"ab": "c"}).sha256_digest(), snapshot({"a": "bc"}).sha256_digest())
        self.assertNotEqual(snapshot({"value": ["a", "b"]}).sha256_digest(), snapshot({"value": ["ab"]}).sha256_digest())

    def test_snapshot_rejects_bad_fingerprint_versions_numbers_and_generation(self) -> None:
        with self.assertRaises(SnapshotValidationError) as fingerprint:
            TargetFingerprint("sha256", "NOT-A-DIGEST")
        self.assertEqual("INVALID_TARGET_FINGERPRINT", fingerprint.exception.code)
        with self.assertRaises(SnapshotValidationError) as whitespace:
            TargetFingerprint("sha256", "ab" * 31 + "  ")
        self.assertEqual("INVALID_TARGET_FINGERPRINT", whitespace.exception.code)
        with self.assertRaises(SnapshotValidationError) as number:
            snapshot({"value": float("nan")})
        self.assertEqual("INVALID_NUMBER", number.exception.code)
        with self.assertRaises(SnapshotValidationError) as generation:
            snapshot(generation=True)  # type: ignore[arg-type]
        self.assertEqual("INVALID_OPERATION_GENERATION", generation.exception.code)
        with self.assertRaises(SnapshotValidationError) as large_generation:
            snapshot(generation=2**53)
        self.assertEqual("INVALID_OPERATION_GENERATION", large_generation.exception.code)
        with self.assertRaises(SnapshotValidationError) as encoding:
            SnapshotVersions(
                rights_schema_version="rights-v1",
                preflight_protocol_version="preflight-v1",
                bridge_protocol_version="bridge-v0",
                core_service_contract_version="1.0",
                text_policy_version="unicode-16",
                snapshot_encoding_version="jcs",
            )
        self.assertEqual("UNKNOWN_SNAPSHOT_ENCODING", encoding.exception.code)

    def test_direct_frozen_container_construction_cannot_bypass_deep_immutability(self) -> None:
        with self.assertRaises(SnapshotValidationError) as array:
            FrozenArray(["mutable"])  # type: ignore[arg-type]
        self.assertEqual("RIGHTS_NOT_FROZEN", array.exception.code)
        with self.assertRaises(SnapshotValidationError) as nested:
            FrozenObject((("value", ["mutable"]),))  # type: ignore[arg-type]
        self.assertEqual("RIGHTS_NOT_FROZEN", nested.exception.code)
        with self.assertRaises(SnapshotValidationError) as ordering:
            FrozenObject((("z", 1), ("a", 2)))
        self.assertEqual("RIGHTS_NOT_FROZEN", ordering.exception.code)

    def test_direct_frozen_graph_cannot_bypass_aggregate_bounds(self) -> None:
        nested = FrozenArray(())
        for _ in range(16):
            nested = FrozenArray((nested,))
        rights = FrozenObject((("deep", nested),))
        with self.assertRaises(SnapshotValidationError) as depth:
            rights.to_mapping()
        self.assertEqual("SNAPSHOT_DEPTH_EXCEEDED", depth.exception.code)

        with mock.patch("personal_mark_snapshot.MAX_SNAPSHOT_ITEMS", 3):
            with self.assertRaises(SnapshotValidationError) as items:
                thaw_json_value(FrozenArray((1, 2, 3)))
        self.assertEqual("SNAPSHOT_ITEMS_EXCEEDED", items.exception.code)

        with mock.patch("personal_mark_snapshot.MAX_SNAPSHOT_INPUT_BYTES", 3):
            with self.assertRaises(SnapshotValidationError) as size:
                FrozenObject((("k", "abc"),)).to_mapping()
        self.assertEqual("SNAPSHOT_TOO_LARGE", size.exception.code)

    def test_maximum_valid_local_mark_fits_snapshot_encoding_bound(self) -> None:
        strokes = []
        for _stroke_index in range(5):
            strokes.append(
                {
                    "points": [
                        {"x": 0.5, "y": 0.5, "t": point_index}
                        for point_index in range(4000)
                    ]
                }
            )
        maximum_mark = personal_mark_v2_from_mapping(
            {
                "version": 2,
                "type": "handwritten",
                "coordinateSpace": {"width": 480, "height": 220},
                "strokes": strokes,
            }
        )
        value = create_preflight_snapshot(
            target_fingerprint=TargetFingerprint("sha256", "a" * 64),
            rights_intent={"rights": "reserved"},
            personal_mark=maximum_mark,
            versions=versions(),
            operation_generation=1,
        )
        self.assertTrue(value.deterministic_bytes().startswith(SNAPSHOT_ENCODING_HEADER))

    def test_module_is_detached_from_creator_io_and_token_services(self) -> None:
        source = (SRC / "personal_mark_snapshot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        for forbidden in ("creator_service", "pathlib", "os", "c2pa"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, imports)


if __name__ == "__main__":
    unittest.main()
