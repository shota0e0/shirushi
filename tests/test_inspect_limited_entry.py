"""Outer transport tests; no new image fixture or verification semantics."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT / "tests")]
import inspect_limited_fixture as entry
import inspection_metadata as kernel
import test_inspection_metadata as kernel_tests


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.inner = kernel_tests.LimitedTests().inspect()

    def test_success_wraps_inner_without_rewriting(self):
        before = copy.deepcopy(self.inner)
        before_bytes = json.dumps(self.inner, ensure_ascii=True, allow_nan=False,
                                  separators=(',', ':')).encode('utf-8')
        envelope = entry.success_envelope(self.inner, kernel)
        self.assertEqual(set(envelope), {'contractVersion', 'operation', 'result',
                                        'completeness', 'checks', 'inspection'})
        self.assertEqual(envelope['contractVersion'], 2)
        self.assertEqual(envelope['inspection']['contractVersion'], 1)
        self.assertIs(envelope['inspection'], self.inner)
        self.assertEqual(envelope["inspection"], before)
        self.assertEqual(envelope["result"], "LIMITED_INSPECTION")
        self.assertEqual(envelope["completeness"], "INCOMPLETE")
        self.assertEqual(envelope["checks"]["trustmark"], "NOT_CHECKED")
        self.assertFalse(envelope["inspection"]["fullVerificationPerformed"])
        self.assertFalse(envelope["inspection"]["successMotionEligible"])
        self.assertEqual(entry.parse_envelope(entry.encode_envelope(envelope), kernel), envelope)
        after_bytes = json.dumps(envelope['inspection'], ensure_ascii=True, allow_nan=False,
                                 separators=(',', ':')).encode('utf-8')
        self.assertEqual(after_bytes, before_bytes)
        self.assertEqual(self.inner, before)

    def test_old_outer_and_hybrid_contracts_are_rejected(self):
        valid = entry.success_envelope(self.inner, kernel)
        for version, result, completeness in [
                (1, 'INSPECTION_COMPLETED', 'LIMITED'),
                (1, 'LIMITED_INSPECTION', 'INCOMPLETE'),
                (2, 'INSPECTION_COMPLETED', 'LIMITED'),
                (2, 'INSPECTION_COMPLETED', 'INCOMPLETE'),
                (2, 'LIMITED_INSPECTION', 'LIMITED'),
                (3, 'LIMITED_INSPECTION', 'INCOMPLETE')]:
            candidate = copy.deepcopy(valid)
            candidate.update(contractVersion=version, result=result, completeness=completeness)
            with self.subTest(version=version, result=result, completeness=completeness), \
                    self.assertRaises(entry.EntryFailure):
                entry.parse_envelope(entry.encode_envelope(candidate), kernel)

    def test_outer_mutations_rejected(self):
        valid = entry.success_envelope(self.inner, kernel)
        mutations = [("result", "PASS"), ("result", "VERIFIED"),
                     ("completeness", "COMPLETE"), ("contractVersion", True),
                     ("contractVersion", 2.0),
                     ("operation", "full_verification"), ("extra", 1)]
        for key, value in mutations:
            candidate = copy.deepcopy(valid)
            candidate[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(entry.EntryFailure):
                entry.validate_envelope(candidate, kernel)

    def test_check_and_inner_mutations_rejected(self):
        for path, value in [(('checks', 'trustmark'), 'INSPECTED'),
                            (('inspection', 'contractVersion'), 2),
                            (('inspection', 'fullVerificationPerformed'), True),
                            (('inspection', 'successMotionEligible'), True),
                            (('inspection', 'overall'), 'COMPLETE')]:
            candidate = entry.success_envelope(copy.deepcopy(self.inner), kernel)
            candidate[path[0]][path[1]] = value
            with self.assertRaises((entry.EntryFailure, kernel.LimitedInspectionError)):
                entry.validate_envelope(candidate, kernel)

    def test_negative_inner_is_not_completed_fixed_fixture(self):
        negative = kernel_tests.LimitedTests().inspect({})
        with self.assertRaises(kernel.LimitedInspectionError):
            entry.success_envelope(negative, kernel)

    def test_source_fingerprint_must_match(self):
        candidate = copy.deepcopy(self.inner)
        candidate['source']['sha256'] = '0' * 64
        with self.assertRaises(entry.EntryFailure):
            entry.success_envelope(candidate, kernel)

    def test_failure_never_contains_partial_result(self):
        envelope = entry.failure_envelope('C2PATOOL_TIMEOUT')
        entry.validate_envelope(envelope, kernel)
        self.assertEqual(envelope['contractVersion'], 2)
        self.assertEqual(set(envelope), {'contractVersion', 'operation', 'result', 'error'})
        envelope['inspection'] = self.inner
        with self.assertRaises(entry.EntryFailure):
            entry.validate_envelope(envelope, kernel)

    def test_old_failure_version_is_rejected(self):
        envelope = entry.failure_envelope('C2PATOOL_TIMEOUT')
        envelope['contractVersion'] = 1
        with self.assertRaises(entry.EntryFailure):
            entry.parse_envelope(entry.encode_envelope(envelope), kernel)

    def test_nested_unknown_fields_are_rejected(self):
        for path in [('checks',), ('inspection',), ('inspection', 'source'),
                     ('inspection', 'c2pa'), ('inspection', 'cawg')]:
            candidate = entry.success_envelope(copy.deepcopy(self.inner), kernel)
            child = candidate
            for key in path:
                child = child[key]
            child['unexpected'] = True
            with self.subTest(path=path), \
                    self.assertRaises((entry.EntryFailure, kernel.LimitedInspectionError)):
                entry.parse_envelope(entry.encode_envelope(candidate), kernel)

    def test_duplicates_in_valid_v2_and_failure_are_rejected(self):
        valid = entry.encode_envelope(entry.success_envelope(self.inner, kernel))
        failure = entry.encode_envelope(entry.failure_envelope('C2PATOOL_TIMEOUT'))
        for raw in [
                valid.replace(b'"contractVersion":2',
                              b'"contractVersion":2,"contractVersion":2', 1),
                valid.replace(b'"contractVersion":1',
                              b'"contractVersion":1,"contractVersion":1', 1),
                valid.replace(b'"trustmark":"NOT_CHECKED"',
                              b'"trustmark":"NOT_CHECKED","trustmark":"NOT_CHECKED"', 1),
                failure.replace(b'"code":"C2PATOOL_TIMEOUT"',
                                b'"code":"C2PATOOL_TIMEOUT","code":"C2PATOOL_TIMEOUT"', 1)]:
            with self.assertRaises(entry.EntryFailure):
                entry.parse_envelope(raw, kernel)

    def test_unknown_error_is_sanitized(self):
        self.assertEqual(entry.failure_envelope('private diagnostic')['error']['code'],
                         'INTERNAL_ENTRY_FAILURE')

    def test_json_parser_rejects_duplicate_nonfinite_extra_documents_and_depth(self):
        raw_values = [b'{"contractVersion":1,"contractVersion":1}',
                      b'{"x":NaN}', b'{"x":Infinity}', b'{}{}', b'[]',
                      b'\xff', b'x' * (entry.MAX_JSON_BYTES + 1),
                      b'{"x":' * 20 + b'1' + b'}' * 20]
        for raw in raw_values:
            with self.subTest(raw=raw[:40]), self.assertRaises(entry.EntryFailure):
                entry.parse_envelope(raw, kernel)

    def test_nonserializable_result_has_fixed_error(self):
        with self.assertRaisesRegex(entry.EntryFailure, 'RESULT_SERIALIZATION_FAILED'):
            entry.encode_envelope({'x': object()})

    def test_dispatch_uses_only_fixed_package_paths(self):
        with patch.object(kernel, 'inspect_limited', return_value=self.inner) as call:
            entry.inspect_package(ROOT, kernel)
        self.assertEqual(call.call_args.args, (ROOT / 'fixtures/valid_shirushi.png',
                                              ROOT / 'tools/c2patool.exe',
                                              ROOT / 'config/verifier-settings.json'))

    def test_dispatch_never_selects_the_present_cffi_generated_launcher(self):
        # This verifies entry dispatch responsibility, not process monitoring.
        # Presence/unused provenance and candidate bytes stay separate evidence.
        with patch.object(kernel, 'inspect_limited', return_value=self.inner) as call:
            envelope = entry.inspect_package(ROOT, kernel)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[1], ROOT / 'tools/c2patool.exe')
        self.assertNotIn('cffi-gen-src.exe', repr(call.call_args))
        self.assertEqual(envelope['result'], 'LIMITED_INSPECTION')
        self.assertEqual(envelope['completeness'], 'INCOMPLETE')
        self.assertEqual(envelope['checks']['trustmark'], 'NOT_CHECKED')

    def test_main_sanitizes_kernel_failure_and_emits_one_document(self):
        output = io.BytesIO()
        class Stream:
            buffer = output
        with patch.object(entry, '_load_kernel', return_value=kernel), \
             patch.object(entry, 'inspect_package', side_effect=kernel.LimitedInspectionError('C2PATOOL_TIMEOUT')), \
             patch.object(sys, 'stdout', Stream()):
            self.assertEqual(entry.main([]), 13)
        envelope = entry.parse_envelope(output.getvalue(), kernel)
        self.assertEqual(envelope, entry.failure_envelope('C2PATOOL_TIMEOUT'))
        self.assertEqual(output.getvalue().count(b'\n'), 1)

    def test_main_success_exit_zero_matches_v2_success(self):
        output = io.BytesIO()
        class Stream:
            buffer = output
        with patch.object(entry, '_load_kernel', return_value=kernel), \
             patch.object(entry, 'inspect_package',
                          return_value=entry.success_envelope(self.inner, kernel)), \
             patch.object(sys, 'stdout', Stream()):
            self.assertEqual(entry.main([]), 0)
        envelope = entry.parse_envelope(output.getvalue(), kernel)
        self.assertEqual(envelope['contractVersion'], 2)
        self.assertEqual(envelope['result'], 'LIMITED_INSPECTION')
        self.assertEqual(envelope['completeness'], 'INCOMPLETE')
        self.assertEqual(envelope['inspection'], self.inner)
        self.assertEqual(output.getvalue().count(b'\n'), 1)

    def test_failure_exit_codes_match_failure_only_v2(self):
        for code, expected_exit in entry.ERROR_EXITS.items():
            output = io.BytesIO()
            class Stream:
                buffer = output
            with self.subTest(code=code), \
                 patch.object(entry, '_load_kernel', side_effect=entry.EntryFailure(code)), \
                 patch.object(sys, 'stdout', Stream()):
                self.assertEqual(entry.main([]), expected_exit)
            envelope = entry.parse_envelope(output.getvalue(), kernel)
            self.assertEqual(envelope, entry.failure_envelope(code))
            self.assertNotIn('inspection', envelope)

    def test_arbitrary_target_argument_rejected_before_import(self):
        output = io.BytesIO()
        class Stream:
            buffer = output
        with patch.object(entry, '_load_kernel') as load, patch.object(sys, 'stdout', Stream()):
            self.assertEqual(entry.main(['unapproved-target']), 2)
            load.assert_not_called()

    def test_output_failure_returns_internal_exit(self):
        class BrokenStream:
            def write(self, _):
                raise OSError('private path')
        class Stream:
            buffer = BrokenStream()
        stderr = io.StringIO()
        with patch.object(sys, 'stdout', Stream()), patch.object(sys, 'stderr', stderr):
            self.assertEqual(entry.main(['invalid']), 16)
        self.assertEqual(stderr.getvalue(), 'RESULT_SERIALIZATION_FAILED\n')


if __name__ == '__main__':
    unittest.main()
