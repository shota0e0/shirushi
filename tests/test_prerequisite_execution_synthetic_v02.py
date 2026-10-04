"""Synthetic runner A–R matrix; no live process, Windows or prerequisite tests."""
import ast
import builtins
import copy
import ctypes
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock
import winreg

from scripts import prerequisite_execution_synthetic_v02 as runner
from scripts import prerequisite_orchestration_v02 as planner
from scripts import prerequisite_readiness_v02 as readiness
from scripts import verify_vc_runtime_candidate as candidate
from tests.test_prerequisite_orchestration_v02 import report, context, identity, approval

SID = "SYNTHETIC_SESSION_1"


def step(prerequisite, after=None, **kwargs):
    return runner.SyntheticStep(prerequisite, planner.EXPECTED_SHA[prerequisite],
                                reobserve=after, **kwargs)


def session(vc="READY", webview="READY", *, steps=(), authorized=True, ctx=None):
    if ctx is None:
        ctx = context(vc, webview, candidates=tuple(identity(p) for p in planner.ORDER),
                      approvals=tuple(approval(p) for p in planner.ORDER))
    raw = planner.encode_plan(planner.plan(ctx), ctx)
    auth = (runner.SyntheticAuthorization(SID, True, runner.plan_digest(raw, ctx), "TEST_ONLY")
            if authorized else runner.SyntheticAuthorization(SID))
    return runner.Session(SID, raw, ctx, runner.SyntheticAdapter(steps), auth)


class RunnerMatrixTests(unittest.TestCase):
    def checked(self, s, state, reason=None):
        result = s.run()
        self.assertEqual(result["finalState"], state)
        if reason is not None: self.assertEqual(result["reason"], reason)
        self.assertEqual(s.parse_result(s.encode_result(result)), result)
        self.assertIs(result["realExecutionAllowed"], False)
        self.assertEqual(result["realExecutionCounts"], {"vc": 0, "webview2": 0})
        self.assertEqual(result["automaticRetry"], 0)
        self.assertIs(result["automaticReboot"], False)
        self.assertTrue(all(n <= 1 for n in result["syntheticAttempts"].values()))
        return result

    def test_a_both_ready_complete_without_authorization_zero_actions(self):
        result = self.checked(session(authorized=False), "COMPLETE", "NO_ACTION_REQUIRED")
        self.assertEqual(result["syntheticAttempts"], {"vc": 0, "webview2": 0})
        self.assertEqual(result["approvedActions"], [])

    def test_b_vc_missing_webview_ready_reobserve_complete(self):
        result = self.checked(session("MISSING", steps=(step(planner.VC, report()),)), "COMPLETE")
        self.assertIn("REOBSERVING_VC", result["transitions"])
        self.assertNotIn("EXECUTING_WEBVIEW2", result["transitions"])

    def test_c_vc_ready_webview_missing_reobserve_complete(self):
        result = self.checked(session(webview="MISSING", steps=(step(planner.WEBVIEW2, report()),)), "COMPLETE")
        self.assertNotIn("EXECUTING_VC", result["transitions"])

    def test_d_both_missing_vc_target_ready_before_webview_then_complete(self):
        result = self.checked(session("MISSING", "MISSING", steps=(
            step(planner.VC, report(webview="MISSING")), step(planner.WEBVIEW2, report()))), "COMPLETE")
        sequence = result["transitions"]
        self.assertLess(sequence.index("REOBSERVING_VC"), sequence.index("EXECUTING_WEBVIEW2"))
        self.assertEqual(result["syntheticOutcomes"][0]["readinessAfter"][planner.WEBVIEW2], "MISSING")

    def test_e_exit_zero_vc_reobserve_outdated_blocks_webview(self):
        result = self.checked(session("MISSING", "MISSING", steps=(
            step(planner.VC, report("OUTDATED", "MISSING")), step(planner.WEBVIEW2, report()))),
            "BLOCKED", "POST_ACTION_READINESS_NOT_CONFIRMED")
        self.assertEqual(result["syntheticAttempts"]["webview2"], 0)

    def test_f_exit_zero_unknown_or_missing_blocks(self):
        for after in (report("FAILED"), report("MISSING"), report("UNKNOWN_ARCHITECTURE")):
            self.checked(session("MISSING", steps=(step(planner.VC, after),)), "BLOCKED")

    def test_g_uac_cancel_distinct_cancelled_no_attempt_retry(self):
        result = self.checked(session("MISSING", steps=(step(planner.VC,
            elevation=planner.ElevationOutcome.CANCELLED),)), "CANCELLED", "SYNTHETIC_UAC_CANCELLED")
        self.assertEqual(result["syntheticAttempts"]["vc"], 0)

    def test_h_uac_failed_not_generic_installer_failure(self):
        self.checked(session("MISSING", steps=(step(planner.VC,
            elevation=planner.ElevationOutcome.FAILED),)), "FAILED", "SYNTHETIC_UAC_FAILED")

    def test_i_launch_failure_stops(self):
        self.checked(session("MISSING", steps=(step(planner.VC,
            outcome=runner.Outcome.LAUNCH_FAILED, exit_code=None),)), "FAILED", "SYNTHETIC_LAUNCH_FAILED")

    def test_j_nonzero_exit_not_ready_even_with_ready_report(self):
        self.checked(session("MISSING", steps=(step(planner.VC, report(), exit_code=1),)),
                     "FAILED", "SYNTHETIC_NONZERO_EXIT")

    def test_k_reboot_required_stops_no_next_no_resume(self):
        s = session("MISSING", "MISSING", steps=(step(planner.VC, report(),
            outcome=runner.Outcome.REBOOT_REQUIRED, exit_code=3010), step(planner.WEBVIEW2, report())))
        result = self.checked(s, "REBOOT_REQUIRED", "OWNER_REBOOT_REQUIRED_NEW_SESSION")
        self.assertEqual(result["syntheticAttempts"]["webview2"], 0)
        with self.assertRaisesRegex(runner.SyntheticError, "SESSION_ALREADY_USED"): s.run()
        # An unexplained 3010 is not borrowed from MSI as reboot success.
        self.checked(session("MISSING", steps=(step(planner.VC, report(), exit_code=3010),)), "FAILED")

    def test_l_partial_vc_success_webview_failure_never_complete_or_rollback(self):
        result = self.checked(session("MISSING", "MISSING", steps=(
            step(planner.VC, report(webview="MISSING")),
            step(planner.WEBVIEW2, outcome=runner.Outcome.FAILED, exit_code=9))), "FAILED")
        self.assertEqual(result["finalReadiness"], {planner.VC: "READY", planner.WEBVIEW2: "MISSING"})
        self.assertEqual(len(result["syntheticOutcomes"]), 2)

    def test_m_sha_or_approval_mismatch_blocked_before_launch(self):
        wrong = replace(step(planner.VC, report()), candidate_sha256="0" * 64)
        self.checked(session("MISSING", steps=(wrong,)), "BLOCKED", "SYNTHETIC_TRANSCRIPT_BINDING_CHANGED")
        ctx = context("MISSING", candidates=(identity(planner.VC),))
        result = self.checked(session(ctx=ctx), "BLOCKED", "PLANNER_ACTION_DENIED")
        self.assertEqual(result["syntheticAttempts"]["vc"], 0)
        s = session("MISSING", steps=(step(planner.VC, report()),))
        object.__setattr__(s.authorization, "plan_digest", "0" * 64)
        self.checked(s, "BLOCKED", "CAPTURED_INPUT_CHANGED")

    def test_n_mutated_plan_after_validation_blocked_and_auditable(self):
        s = session("MISSING", steps=(step(planner.VC, report()),))
        changed = s.raw_plan + b" "
        value = s.run(changed)
        self.assertEqual((value["finalState"], value["reason"]), ("BLOCKED", "PLAN_BINDING_CHANGED"))
        self.assertEqual(s.parse_result(s.encode_result(value)), value)
        with self.assertRaises(TypeError): s.validate_result(value, attempted_plan=s.raw_plan)

    def test_o_invalid_transition_and_fabricated_complete_strictly_rejected(self):
        s = session("MISSING", authorized=False)
        baseline = self.checked(s, "WAITING_FOR_APPROVAL")
        mutations = (lambda v: v["transitions"].append("COMPLETE"),
                     lambda v: v.update(finalState="COMPLETE", reason="SYNTHETIC_READINESS_CONFIRMED"),
                     lambda v: v.update(planDigest="0" * 64),
                     lambda v: v.update(syntheticAttempts={"vc": 1, "webview2": 0}),
                     lambda v: v.update(realExecutionAllowed=True),
                     lambda v: v.update(extra=True), lambda v: v.pop("mode"),
                     lambda v: v.update(schemaVersion=True), lambda v: v.update(schemaVersion=1.0),
                     lambda v: v.update(automaticRetry=False))
        for change in mutations:
            altered = copy.deepcopy(baseline); change(altered)
            with self.assertRaises(runner.SyntheticError): s.parse_result(json.dumps(altered).encode())

    def test_p_duplicate_session_execution_and_duplicate_transcript_denied(self):
        s = session("MISSING", steps=(step(planner.VC, report()),))
        self.checked(s, "COMPLETE")
        with self.assertRaisesRegex(runner.SyntheticError, "SESSION_ALREADY_USED"): s.run()
        with self.assertRaises(runner.SyntheticError):
            runner.SyntheticAdapter((step(planner.VC), step(planner.VC)))

    def test_q_timeout_explicit_failed_no_retry_or_reobserve(self):
        result = self.checked(session("MISSING", steps=(step(planner.VC, report(),
            outcome=runner.Outcome.TIMEOUT, exit_code=None),)), "FAILED", "SYNTHETIC_TIMEOUT")
        self.assertNotIn("REOBSERVING_VC", result["transitions"])

    def test_process_cancellation_stops_without_reobserve_or_retry(self):
        result = self.checked(session("MISSING", steps=(step(planner.VC, report(),
            outcome=runner.Outcome.CANCELLED, exit_code=None),)), "CANCELLED", "SYNTHETIC_PROCESS_CANCELLED")
        self.assertEqual(result["syntheticAttempts"]["vc"], 1)
        self.assertNotIn("REOBSERVING_VC", result["transitions"])

    def test_r_pure_runner_no_real_process_native_registry_or_observation_calls(self):
        s = session("MISSING", "MISSING", steps=(step(planner.VC, report(webview="MISSING")),
                                                  step(planner.WEBVIEW2, report())))
        failure = AssertionError("REAL_OPERATION_FORBIDDEN")
        with mock.patch.object(builtins, "open", side_effect=failure), \
             mock.patch.object(subprocess, "Popen", side_effect=failure), \
             mock.patch.object(subprocess, "run", side_effect=failure), \
             mock.patch.object(os, "system", side_effect=failure), \
             mock.patch.object(ctypes, "WinDLL", side_effect=failure), \
             mock.patch.object(winreg, "OpenKey", side_effect=failure), \
             mock.patch.object(winreg, "SetValueEx", side_effect=failure), \
             mock.patch.object(readiness, "observe", side_effect=failure), \
             mock.patch.object(readiness.WindowsRegistryReader, "read", side_effect=failure), \
             mock.patch.object(readiness, "verify_candidate", side_effect=failure), \
             mock.patch.object(candidate, "verify_candidate", side_effect=failure):
            self.checked(s, "COMPLETE")

    def test_no_process_imports_callbacks_or_cli_static_guard(self):
        tree = ast.parse(Path(runner.__file__).read_text(encoding="utf-8"))
        imports = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
        self.assertEqual(imports, {"hashlib", "json"})
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertTrue(names.isdisjoint({"subprocess", "ctypes", "os", "winreg", "eval", "exec", "open"}))
        with self.assertRaises(runner.SyntheticError): runner.SyntheticAdapter((lambda: 0,))

    def test_missing_or_malformed_reobserve_and_regressed_prior_ready_block(self):
        for after, reason in ((None, "REOBSERVATION_REQUIRED"), (b"invalid", "REOBSERVATION_INVALID"),
                              (report(webview="MISSING"), "POST_ACTION_READINESS_NOT_CONFIRMED")):
            self.checked(session("MISSING", steps=(step(planner.VC, after),)), "BLOCKED", reason)
        self.checked(session("MISSING", "MISSING", steps=(step(planner.VC, report(webview="MISSING")),
            step(planner.WEBVIEW2, report("OUTDATED")))), "BLOCKED", "POST_ACTION_READINESS_NOT_CONFIRMED")

    def test_raw_or_tampered_authority_adapter_context_fail_closed(self):
        good = session("MISSING")
        for adapter, auth, ctx in (({"execute": lambda: self.fail("CALLBACK")}, good.authorization, good.context),
                                   (good.adapter, {"approved": True}, good.context),
                                   (good.adapter, good.authorization, object.__new__(planner.PlanningContext)),
                                   (object.__new__(runner.SyntheticAdapter), good.authorization, good.context)):
            self.checked(runner.Session(SID, good.raw_plan, ctx, adapter, auth), "BLOCKED")
        s = session("MISSING", steps=(step(planner.VC),))
        object.__setattr__(s.adapter.steps[0], "prerequisite", [])
        self.checked(s, "BLOCKED", "CAPTURED_INPUT_CHANGED")

    def test_bound_original_inputs_cannot_be_replaced_or_reauthorized_and_pre_run_complete_rejected(self):
        s = session("MISSING", steps=(step(planner.VC, report()),))
        with self.assertRaises(AttributeError): s.context = context()
        with self.assertRaises(AttributeError): s.raw_plan = b"replacement"
        with self.assertRaisesRegex(runner.SyntheticError, "SESSION_NOT_RUN"): s.validate_result({"finalState": "COMPLETE"})
        object.__setattr__(s.context.candidates[0], "sha256", "0" * 64)
        self.checked(s, "BLOCKED", "CAPTURED_INPUT_CHANGED")
        s = session("MISSING", steps=(step(planner.VC, report()),))
        object.__setattr__(s.context, "readiness_report", report())
        self.checked(s, "BLOCKED", "CAPTURED_INPUT_CHANGED")

    def test_planner_approval_record_and_simulation_authority_are_not_conflated(self):
        s = session("MISSING", steps=(step(planner.VC, report()),))
        result = self.checked(s, "COMPLETE")
        self.assertEqual(result["approvedActions"][0]["approvalRecord"], "SYNTHETIC_TEST_ONLY")
        self.assertNotEqual(result["approvedActions"][0]["approvalRecord"], s.authorization.record)

    def test_session_and_authorization_bounds_exact_types(self):
        for sid in ("", "x" * 129, "C:\\private", None):
            with self.assertRaises(runner.SyntheticError): runner.Session(sid, b"", None)
        for approved in (1, "true"):
            with self.assertRaises(runner.SyntheticError): runner.SyntheticAuthorization(SID, approved)
        with self.assertRaises(runner.SyntheticError): runner.SyntheticAuthorization(SID, True, "0" * 64, "")
        with self.assertRaises(runner.SyntheticError): step(planner.VC, exit_code=True)

    def test_order_missing_extra_unrequested_transcript_rejected(self):
        for steps in ((step(planner.WEBVIEW2), step(planner.VC)), (step(planner.VC),), ()):
            self.checked(session("MISSING", "MISSING", steps=steps), "BLOCKED")
        self.checked(session("MISSING", steps=(step(planner.VC), step(planner.WEBVIEW2))), "BLOCKED")

    def test_strict_json_duplicate_nonfinite_bom_trailing_depth_and_size(self):
        s = session(); result = self.checked(s, "COMPLETE"); raw = s.encode_result(result)
        for bad in (b"", b"x" * (runner.MAX_RESULT + 1), raw + b"{}", b"\xef\xbb\xbf" + raw,
                    raw.replace(b'"schemaVersion":1', b'"schemaVersion":1,"schemaVersion":1'),
                    raw.replace(b'"schemaVersion":1', b'"schemaVersion":NaN'),
                    b"[" * 20 + b"0" + b"]" * 20):
            with self.assertRaises(runner.SyntheticError): s.parse_result(bad)

    def test_coordinated_outcome_identity_and_readiness_tamper_rejected_by_original_transcript(self):
        s = session("MISSING", steps=(step(planner.VC, report("OUTDATED")),))
        result = self.checked(s, "BLOCKED")
        altered = copy.deepcopy(result)
        altered.update(finalState="COMPLETE", reason="SYNTHETIC_READINESS_CONFIRMED")
        altered["transitions"][-1] = "COMPLETE"
        altered["finalReadiness"][planner.VC] = "READY"
        altered["syntheticOutcomes"][0]["readinessAfter"][planner.VC] = "READY"
        altered["syntheticOutcomes"][0]["candidateSha256"] = "0" * 64
        with self.assertRaises(runner.SyntheticError): s.validate_result(altered)


if __name__ == "__main__":
    unittest.main()
