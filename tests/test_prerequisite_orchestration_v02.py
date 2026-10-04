"""Synthetic contract evidence only; no installer, observation or runner."""
import builtins
import copy
import ctypes
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from scripts import prerequisite_orchestration_v02 as contract
from scripts import prerequisite_readiness_v02 as readiness
from scripts import verify_vc_runtime_candidate as vc_candidate


def report(vc="READY", webview="READY", *, policy=readiness.BUILD_POLICY, offline=None):
    vc_values = {"Version": ("14.51.36247.0" if vc != "OUTDATED" else "14.51.36246.0", "SZ"),
                 "Installed": (1, "DWORD")}
    v = readiness.Registration(readiness.VC_SOURCE, "OK", vc_values, "X64_REGISTERED")
    if vc in ("MISSING", "FAILED"):
        v = readiness.Registration(readiness.VC_SOURCE, vc, {})
    if vc == "UNKNOWN_ARCHITECTURE":
        v = replace(v, architecture="UNPROVEN")
    w = readiness.Registration(readiness.WV_SOURCES[0], "OK", {"pv": ("140.0.1.2", "SZ")}, "X64_VERIFIED")
    if webview in ("MISSING", "FAILED"):
        w = readiness.Registration(readiness.WV_SOURCES[0], webview, {})
    if webview == "UNKNOWN_ARCHITECTURE":
        w = replace(w, architecture="UNPROVEN")
    rows = [v, readiness.Registration(readiness.VC_SOURCES[1], "MISSING", {}),
            w, readiness.Registration(readiness.WV_SOURCES[1], "MISSING", {})]
    return readiness.encode_result(readiness.evaluate(rows, policy=policy, offline_input=offline), policy=policy)


def identity(prerequisite):
    return contract.CandidateIdentity(prerequisite, contract.IdentityStatus.VALID,
                                      contract.EXPECTED_SHA[prerequisite])


def approval(prerequisite):
    # Synthetic trusted-call-site exercise, not actual Owner execution approval.
    return contract.ExecutionApproval(prerequisite, True, contract.EXPECTED_SHA[prerequisite],
                                      "SYNTHETIC_TEST_ONLY")


def context(vc="READY", webview="READY", *, candidates=(), approvals=(), policy=readiness.BUILD_POLICY):
    return contract.PlanningContext(report(vc, webview, policy=policy), policy, candidates, approvals)


class PlanningTests(unittest.TestCase):
    def checked(self, ctx, result):
        value = contract.plan(ctx)
        self.assertEqual(value["result"], result)
        self.assertIs(value["executionAllowed"], False)
        self.assertEqual(value["automaticRetry"], 0)
        self.assertEqual(value["executionCounts"], {"vc": 0, "webview2": 0})
        self.assertEqual(contract.parse_plan(contract.encode_plan(value, ctx), ctx), value)
        self.assertNotIn("REPAIR", [a["action"] for a in value["actions"]])
        return value

    def test_both_ready_no_action_or_execution(self):
        self.assertEqual(self.checked(context(), "READY_NO_ACTION")["actions"], [])

    def test_vc_missing_identity_without_approval_requires_approval(self):
        value = self.checked(context("MISSING", candidates=(identity(contract.VC),)), "INPUT_APPROVAL_REQUIRED")
        self.assertEqual(len(value["actions"]), 1)
        action = value["actions"][0]
        self.assertEqual((action["action"], action["requiredAction"]), ("BLOCKED", "INSTALL_OR_UPGRADE"))
        self.assertEqual(action["candidateStatus"], "CANDIDATE_IDENTITY_VALID")

    def test_unchecked_offline_identity_cannot_be_approved_by_approval_alone(self):
        self.checked(context("MISSING", approvals=(approval(contract.VC),)), "INPUT_APPROVAL_REQUIRED")

    def test_vc_outdated_approved_future_plan_no_repair_or_current_execution(self):
        value = self.checked(context("OUTDATED", candidates=(identity(contract.VC),),
                                     approvals=(approval(contract.VC),)), "ACTION_PLAN_READY")
        self.assertEqual(value["actions"][0]["action"], "INSTALL_OR_UPGRADE")
        self.assertEqual(value["actions"][0]["executionStatus"], "FUTURE_APPROVED_NO_EXECUTOR")

    def test_webview_missing_unapproved_correct_single_action(self):
        value = self.checked(context(webview="MISSING", candidates=(identity(contract.WEBVIEW2),)),
                             "INPUT_APPROVAL_REQUIRED")
        self.assertEqual([a["prerequisite"] for a in value["actions"]], [contract.WEBVIEW2])

    def test_both_missing_order_is_product_policy_and_sequential(self):
        ids = tuple(identity(p) for p in reversed(contract.ORDER))
        approvals = tuple(approval(p) for p in reversed(contract.ORDER))
        value = self.checked(context("MISSING", "MISSING", candidates=ids, approvals=approvals), "ACTION_PLAN_READY")
        self.assertEqual([a["prerequisite"] for a in value["actions"]], list(contract.ORDER))

    def test_unknown_globally_blocks_other_approved_required_action(self):
        for vc, wv in (("FAILED", "MISSING"), ("MISSING", "FAILED"),
                       ("UNKNOWN_ARCHITECTURE", "MISSING"), ("MISSING", "UNKNOWN_ARCHITECTURE")):
            with self.subTest(vc=vc, webview=wv):
                value = self.checked(context(vc, wv, candidates=tuple(identity(p) for p in contract.ORDER),
                                             approvals=tuple(approval(p) for p in contract.ORDER)), "READINESS_UNKNOWN_BLOCKED")
                self.assertTrue(all(a["action"] == "BLOCKED" for a in value["actions"]))

    def test_missing_unset_wrong_product_policy_blocked(self):
        self.checked(replace(context(), policy=None), "POLICY_BLOCKED")
        self.checked(context(policy=readiness.Policy()), "POLICY_BLOCKED")
        self.checked(context(policy=readiness.Policy("14.40.1.0")), "POLICY_BLOCKED")
        self.checked(replace(context(), policy={"vcDeploymentFloorVersion": "14.51.36247.0"}), "POLICY_BLOCKED")

    def test_rejected_candidate_blocks_and_suppresses_other_approved_input(self):
        ids = (contract.CandidateIdentity(contract.VC, contract.IdentityStatus.REJECTED), identity(contract.WEBVIEW2))
        value = self.checked(context("MISSING", "MISSING", candidates=ids,
                                     approvals=(approval(contract.WEBVIEW2),)), "POLICY_BLOCKED")
        self.assertTrue(all(a["action"] == "BLOCKED" for a in value["actions"]))

    def test_one_unapproved_input_suppresses_partial_future_execution(self):
        value = self.checked(context("MISSING", "MISSING", candidates=tuple(identity(p) for p in contract.ORDER),
                                     approvals=(approval(contract.VC),)), "INPUT_APPROVAL_REQUIRED")
        self.assertTrue(all(a["action"] == "BLOCKED" for a in value["actions"]))
        self.assertEqual(value["actions"][0]["executionStatus"], "WAITING_FOR_OTHER_INPUT")

    def test_invalid_duplicate_or_raw_receipts_are_not_trusted(self):
        for ctx in (replace(context("MISSING"), candidates=(identity(contract.VC), identity(contract.VC))),
                    replace(context("MISSING"), candidates=({"prerequisite": contract.VC},)),
                    replace(context("MISSING"), approvals=({"approved": True},))):
            self.checked(ctx, "POLICY_BLOCKED")

    def test_same_type_tampered_receipt_and_missing_policy_fields_fail_closed(self):
        self.checked(object.__new__(contract.PlanningContext), "POLICY_BLOCKED")
        incomplete = object.__new__(contract.CandidateIdentity)
        self.checked(context("MISSING", candidates=(incomplete,)), "POLICY_BLOCKED")
        altered = identity(contract.VC)
        object.__setattr__(altered, "prerequisite", [])
        self.checked(context("MISSING", candidates=(altered,)), "POLICY_BLOCKED")
        approval_value = approval(contract.VC)
        object.__delattr__(approval_value, "sha256")
        self.checked(context("MISSING", approvals=(approval_value,)), "POLICY_BLOCKED")
        policy = readiness.Policy(readiness.VC_DEPLOYMENT_FLOOR)
        object.__delattr__(policy, "vc_deployment_floor")
        self.checked(replace(context(), policy=policy), "POLICY_BLOCKED")

    def test_approval_is_explicit_boolean_sha_bound_and_not_json_authority(self):
        self.assertFalse(contract.ExecutionApproval(contract.VC).approved)
        for args in ((contract.VC, 1), (contract.VC, True, "0" * 64, "test"),
                     (contract.VC, True, contract.EXPECTED_SHA[contract.VC], ""),
                     (contract.VC, True, contract.EXPECTED_SHA[contract.VC], "C:\\private")):
            with self.assertRaises(contract.ContractError):
                contract.ExecutionApproval(*args)
        with self.assertRaises(contract.ContractError):
            contract.CandidateIdentity(contract.VC, contract.IdentityStatus.VALID, "0" * 64)

    def test_malformed_stale_or_forged_readiness_is_blocked(self):
        good = json.loads(report())
        bad = copy.deepcopy(good); bad["runtimes"]["vc"]["status"] = "OUTDATED"
        stale = copy.deepcopy(good); stale["schemaVersion"] = 1
        unsupported = copy.deepcopy(good); unsupported["runtimes"]["webview2"]["status"] = "OUTDATED"
        for raw in (b"not-json", b"[]", b"{}", None, b"x" * (readiness.MAX_RESULT + 1),
                    json.dumps(bad).encode(), json.dumps(stale).encode(), json.dumps(unsupported).encode()):
            self.checked(replace(context(), readiness_report=raw), "READINESS_UNKNOWN_BLOCKED")

    def test_plan_schema_types_unknown_fields_and_coordinated_tamper_rejected(self):
        ctx = context("MISSING"); baseline = contract.plan(ctx)
        mutations = (lambda v: v.update(extra=1), lambda v: v.pop("operation"),
                     lambda v: v.update(contractVersion=True), lambda v: v.update(contractVersion=1.0),
                     lambda v: v.update(executionAllowed=0),
                     lambda v: v.update(automaticRetry=True), lambda v: v.update(executionAllowed=True),
                     lambda v: v["actions"][0].update(extra=1),
                     lambda v: v.update(result="ACTION_PLAN_READY", executionAllowed=True),
                     lambda v: v["actions"][0].update(action="INSTALL_OR_UPGRADE", executionStatus="FUTURE_APPROVED_NO_EXECUTOR"),
                     lambda v: v["executionCounts"].update(vc=1))
        for change in mutations:
            value = copy.deepcopy(baseline); change(value)
            with self.assertRaises(contract.ContractError):
                contract.parse_plan(json.dumps(value).encode(), ctx)

    def test_plan_duplicate_nonfinite_bom_trailing_and_bounds_rejected(self):
        ctx = context(); raw = contract.encode_plan(contract.plan(ctx), ctx)
        invalid = (raw.replace(b'"contractVersion":1', b'"contractVersion":1,"contractVersion":1'),
                   raw.replace(b'"contractVersion":1', b'"contractVersion":NaN'),
                   b"\xef\xbb\xbf" + raw, raw + b"{}", b"", b"x" * (contract.MAX_PLAN + 1))
        for value in invalid:
            with self.assertRaises(contract.ContractError): contract.parse_plan(value, ctx)


class AdapterAndFutureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = (Path(__file__).resolve().parents[1] / "docs/development/v02-vc-runtime-offline-candidate.json").read_bytes()
        record = vc_candidate.parse_record(cls.frozen)
        cls.vc_output = {"schemaVersion": 1, "classification": "VC_RUNTIME_CANDIDATE_IDENTITY_MATCH",
                         "filename": vc_candidate.EXPECTED_FILENAME, "size": vc_candidate.EXPECTED_SIZE,
                         "sha256": vc_candidate.EXPECTED_SHA256, "freezeRecordSha256": vc_candidate.EXPECTED_RECORD_SHA256,
                         "state": "CANDIDATE", "signatureVerification": "FROZEN_ACQUISITION_EVIDENCE_ONLY",
                         "compatibilityClassification": record["compatibilityClassification"],
                         "minimumPolicyClassification": record["minimumPolicyClassification"],
                         "legalClassification": "LEGAL_REVIEW_REQUIRED", "executionCount": 0}

    def test_vc_adapter_reuses_frozen_record_without_promoting_candidate(self):
        receipt = contract.vc_identity(self.vc_output, self.frozen)
        self.assertEqual(receipt, identity(contract.VC))
        for key, value in (("state", "OWNER_APPROVED"), ("sha256", "0" * 64),
                           ("size", True), ("executionCount", False), ("extra", 1)):
            changed = dict(self.vc_output); changed[key] = value
            with self.assertRaises(contract.ContractError): contract.vc_identity(changed, self.frozen)
        with self.assertRaises(contract.ContractError): contract.vc_identity(self.vc_output, self.frozen + b" ")

    def test_webview_adapter_reuses_existing_strict_offline_evidence_only(self):
        for status, expected in (("NOT_CHECKED", contract.IdentityStatus.NOT_CHECKED),
                                 ("CANDIDATE_IDENTITY_MATCH", contract.IdentityStatus.VALID),
                                 ("IDENTITY_REJECTED", contract.IdentityStatus.REJECTED)):
            offline = readiness._offline(status, "HASH_MISMATCH" if status == "IDENTITY_REJECTED" else None)
            self.assertEqual(contract.webview2_identity(report(offline=offline)).status, expected)
        bad = json.loads(report()); bad["offlineInputs"]["webview2"]["approval"] = "OWNER_APPROVED"
        with self.assertRaises(contract.ContractError): contract.webview2_identity(json.dumps(bad).encode())

    def test_success_requires_reobserve_and_confirmed_readiness_not_exit_code(self):
        success = contract.ExecutionOutcome.SUCCEEDED
        self.assertEqual(contract.confirm_future_readiness(success, None), "REOBSERVE_REQUIRED")
        for raw in (report("MISSING"), report("OUTDATED"), report(webview="FAILED"), b"bad-json"):
            self.assertEqual(contract.confirm_future_readiness(success, raw), "POST_INSTALL_READINESS_NOT_CONFIRMED")
        self.assertEqual(contract.confirm_future_readiness(success, report()), "READINESS_CONFIRMED")
        with self.assertRaises(contract.ContractError): contract.confirm_future_readiness(0, report())

    def test_reboot_failure_cancel_stop_no_retry_and_uac_cancellation_distinct(self):
        self.assertEqual(contract.confirm_future_readiness(contract.ExecutionOutcome.REBOOT_REQUIRED, report()),
                         "STOP_OWNER_REBOOT_THEN_REOBSERVE")
        self.assertEqual(contract.confirm_future_readiness(contract.ExecutionOutcome.FAILED, report()), "STOP_PRESERVE_FIRST_FAILURE")
        self.assertEqual(contract.confirm_future_readiness(contract.ExecutionOutcome.CANCELLED, report()), "STOP_INSTALLER_CANCELLED")
        self.assertEqual(contract.ELEVATION_TRANSITIONS[contract.ElevationOutcome.CANCELLED], "STOP_ELEVATION_CANCELLED")
        self.assertNotEqual(contract.ELEVATION_TRANSITIONS[contract.ElevationOutcome.CANCELLED],
                            contract.EXECUTION_TRANSITIONS[contract.ExecutionOutcome.FAILED])

    def test_pure_api_never_calls_io_observation_verification_or_process_adapters(self):
        raw = report(); webview = report(offline=readiness._offline("CANDIDATE_IDENTITY_MATCH"))
        ctx = context("MISSING", candidates=(identity(contract.VC),), approvals=(approval(contract.VC),))
        forbidden = AssertionError("PURE_CONTRACT_ATTEMPTED_IO")
        with mock.patch.object(builtins, "open", side_effect=forbidden), \
             mock.patch.object(Path, "read_bytes", side_effect=forbidden), \
             mock.patch.object(subprocess, "run", side_effect=forbidden), \
             mock.patch.object(subprocess, "Popen", side_effect=forbidden), \
             mock.patch.object(ctypes, "WinDLL", side_effect=forbidden), \
             mock.patch.object(readiness, "observe", side_effect=forbidden), \
             mock.patch.object(readiness.WindowsRegistryReader, "read", side_effect=forbidden), \
             mock.patch.object(readiness, "verify_candidate", side_effect=forbidden), \
             mock.patch.object(vc_candidate, "verify_candidate", side_effect=forbidden):
            value = contract.plan(ctx)
            contract.parse_plan(contract.encode_plan(value, ctx), ctx)
            contract.vc_identity(self.vc_output, self.frozen)
            contract.webview2_identity(webview)
            contract.confirm_future_readiness(contract.ExecutionOutcome.SUCCEEDED, raw)
        self.assertIs(value["executionAllowed"], False)
        self.assertEqual(value["executionCounts"], {"vc": 0, "webview2": 0})


if __name__ == "__main__":
    unittest.main()
