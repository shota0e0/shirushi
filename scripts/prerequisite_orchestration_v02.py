"""Pure development prerequisite planning contract; no execution or observation.

Trusted callers supply independently validated facts and explicit approval.
There is deliberately no CLI, process adapter, filesystem or registry collector.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from types import MappingProxyType

from scripts import prerequisite_readiness_v02 as readiness
from scripts import verify_vc_runtime_candidate as vc_candidate

MAX_PLAN = 16384
VC = "VC_RUNTIME_X64"
WEBVIEW2 = "WEBVIEW2_EVERGREEN_X64"
ORDER = (VC, WEBVIEW2)  # Shirushi product policy, not a technical dependency.
EXPECTED_SHA = MappingProxyType({VC: vc_candidate.EXPECTED_SHA256,
                                 WEBVIEW2: readiness.CANDIDATE_SHA256})


class ContractError(Exception):
    """Closed, public contract failure without paths or raw exception text."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ContractError(code)


def _trusted_policy(policy: object) -> bool:
    return type(policy) is readiness.Policy and "vc_deployment_floor" in vars(policy) and policy.vc_deployment_floor in (
        None, readiness.VC_DEPLOYMENT_FLOOR)


class IdentityStatus(str, Enum):
    NOT_CHECKED = "NOT_CHECKED"
    VALID = "CANDIDATE_IDENTITY_VALID"
    REJECTED = "IDENTITY_REJECTED"


@dataclass(frozen=True)
class CandidateIdentity:
    prerequisite: str
    status: IdentityStatus = IdentityStatus.NOT_CHECKED
    sha256: str | None = None

    def __post_init__(self) -> None:
        _require(self.prerequisite in ORDER and type(self.status) is IdentityStatus,
                 "IDENTITY_CONTRACT_INVALID")
        _require(self.sha256 == EXPECTED_SHA[self.prerequisite] if self.status is IdentityStatus.VALID
                 else self.sha256 is None, "IDENTITY_CONTRACT_INVALID")


@dataclass(frozen=True)
class ExecutionApproval:
    """Trusted in-memory policy only; never deserialize this from frontend JSON."""
    prerequisite: str
    approved: bool = False
    sha256: str | None = None
    approval_record: str | None = None

    def __post_init__(self) -> None:
        _require(self.prerequisite in ORDER and type(self.approved) is bool,
                 "APPROVAL_CONTRACT_INVALID")
        if self.approved:
            _require(self.sha256 == EXPECTED_SHA[self.prerequisite], "APPROVAL_IDENTITY_MISMATCH")
            _require(type(self.approval_record) is str and 0 < len(self.approval_record) <= 128
                     and all(c.isascii() and (c.isalnum() or c in "_.:-") for c in self.approval_record),
                     "APPROVAL_RECORD_INVALID")
        else:
            _require(self.sha256 is None and self.approval_record is None, "APPROVAL_CONTRACT_INVALID")


@dataclass(frozen=True)
class PlanningContext:
    """Immutable report bytes plus separately trusted policy/receipts/approval."""
    readiness_report: bytes | None
    policy: readiness.Policy | None = readiness.BUILD_POLICY
    candidates: tuple[CandidateIdentity, ...] = ()
    approvals: tuple[ExecutionApproval, ...] = ()


def vc_identity(output: dict, frozen_record: bytes) -> CandidateIdentity:
    """Pure adapter for the existing verifier's result; never reads its file."""
    try:
        record = vc_candidate.parse_record(frozen_record)
    except vc_candidate.CandidateError:
        raise ContractError("VC_IDENTITY_EVIDENCE_INVALID") from None
    expected = {"schemaVersion": 1, "classification": "VC_RUNTIME_CANDIDATE_IDENTITY_MATCH",
                "filename": vc_candidate.EXPECTED_FILENAME, "size": vc_candidate.EXPECTED_SIZE,
                "sha256": vc_candidate.EXPECTED_SHA256,
                "freezeRecordSha256": vc_candidate.EXPECTED_RECORD_SHA256, "state": "CANDIDATE",
                "signatureVerification": "FROZEN_ACQUISITION_EVIDENCE_ONLY",
                "compatibilityClassification": record["compatibilityClassification"],
                "minimumPolicyClassification": record["minimumPolicyClassification"],
                "legalClassification": "LEGAL_REVIEW_REQUIRED", "executionCount": 0}
    _require(type(output) is dict and set(output) == set(expected)
             and all(type(output[k]) is type(v) and output[k] == v for k, v in expected.items()),
             "VC_IDENTITY_EVIDENCE_INVALID")
    return CandidateIdentity(VC, IdentityStatus.VALID, vc_candidate.EXPECTED_SHA256)


def webview2_identity(report: bytes, *, policy: readiness.Policy = readiness.BUILD_POLICY) -> CandidateIdentity:
    """Reuse the strict readiness/offline-input validator, not a second verifier."""
    _require(_trusted_policy(policy), "TRUSTED_POLICY_UNAVAILABLE")
    try:
        value = readiness.parse_result(report, policy=policy)["offlineInputs"]["webview2"]
    except readiness.ReadinessError:
        raise ContractError("WEBVIEW2_IDENTITY_EVIDENCE_INVALID") from None
    status = {"NOT_CHECKED": IdentityStatus.NOT_CHECKED,
              "CANDIDATE_IDENTITY_MATCH": IdentityStatus.VALID,
              "IDENTITY_REJECTED": IdentityStatus.REJECTED}[value["status"]]
    return CandidateIdentity(WEBVIEW2, status,
                             readiness.CANDIDATE_SHA256 if status is IdentityStatus.VALID else None)


def _root(result: str, overall: str, actions: list[dict]) -> dict:
    return {"contractVersion": 1, "operation": "prerequisite_orchestration_plan",
            "result": result, "overallReadiness": overall, "executionAllowed": False,
            "actions": actions, "rebootPolicy": "NEVER_AUTOMATIC_OWNER_CONTROLLED",
            "automaticRetry": 0, "executionCounts": {"vc": 0, "webview2": 0}}


def _action(prerequisite: str, reason: str, identity: CandidateIdentity,
            *, action: str = "BLOCKED", required: str | None = None,
            execution: str = "BLOCKED") -> dict:
    return {"prerequisite": prerequisite, "reason": reason, "action": action,
            "requiredAction": required, "candidateStatus": identity.status.value,
            "candidateSha256": identity.sha256, "executionStatus": execution}


def _unavailable(result: str, reason: str) -> dict:
    return _root(result, "READINESS_UNKNOWN",
                 [_action(p, reason, CandidateIdentity(p)) for p in ORDER])


def _receipts(values: tuple, kind: type) -> dict:
    _require(type(values) is tuple and len(values) <= 2 and all(type(v) is kind for v in values),
             "TRUSTED_CONTEXT_INVALID")
    try:
        for value in values:
            value.__post_init__()
    except (AttributeError, TypeError):
        raise ContractError("TRUSTED_CONTEXT_INVALID") from None
    _require(len({v.prerequisite for v in values}) == len(values), "TRUSTED_CONTEXT_INVALID")
    return {p: next((v for v in values if v.prerequisite == p), kind(p)) for p in ORDER}


def plan(context: PlanningContext) -> dict:
    """Return a future action plan; current-Gate execution is always forbidden."""
    if (type(context) is not PlanningContext
            or not {"readiness_report", "policy", "candidates", "approvals"} <= vars(context).keys()
            or not _trusted_policy(context.policy)):
        return _unavailable("POLICY_BLOCKED", "TRUSTED_POLICY_UNAVAILABLE")
    try:
        identities = _receipts(context.candidates, CandidateIdentity)
        approvals = _receipts(context.approvals, ExecutionApproval)
    except ContractError:
        return _unavailable("POLICY_BLOCKED", "TRUSTED_CONTEXT_INVALID")
    try:
        report = readiness.parse_result(context.readiness_report, policy=context.policy)
    except readiness.ReadinessError:
        return _unavailable("READINESS_UNKNOWN_BLOCKED", "READINESS_EVIDENCE_INVALID")
    runtimes = {VC: report["runtimes"]["vc"], WEBVIEW2: report["runtimes"]["webview2"]}
    needed = [p for p in ORDER if runtimes[p]["status"] != "READY"]
    if not needed:
        return _root("READY_NO_ACTION", report["overall"], [])
    if any(runtimes[p]["status"] == "POLICY_UNSET" for p in needed):
        blocked, reason = "POLICY_BLOCKED", "PRODUCT_POLICY_UNSET"
    elif any(runtimes[p]["status"] == "DETECTION_FAILED" for p in needed):
        blocked, reason = "READINESS_UNKNOWN_BLOCKED", "READINESS_UNKNOWN"
    elif runtimes[WEBVIEW2]["status"] == "OUTDATED":
        blocked, reason = "POLICY_BLOCKED", "WEBVIEW2_UPDATE_POLICY_UNAPPROVED"
    elif any(identities[p].status is IdentityStatus.REJECTED for p in needed):
        blocked, reason = "POLICY_BLOCKED", "CANDIDATE_IDENTITY_REJECTED"
    else:
        blocked, reason = None, None
    if blocked:
        return _root(blocked, report["overall"], [
            _action(p, reason, identities[p], required="INSTALL_OR_UPGRADE"
                    if runtimes[p]["status"] in {"MISSING", "OUTDATED"} else None)
            for p in needed])
    permitted = {p: identities[p].status is IdentityStatus.VALID and approvals[p].approved
                 and identities[p].sha256 == approvals[p].sha256 for p in needed}
    complete = all(permitted.values())
    actions = [_action(p, "RUNTIME_" + runtimes[p]["status"], identities[p],
                       action="INSTALL_OR_UPGRADE" if complete else "BLOCKED",
                       required="INSTALL_OR_UPGRADE",
                       execution="FUTURE_APPROVED_NO_EXECUTOR" if complete else
                       "INPUT_APPROVAL_REQUIRED" if not permitted[p] else "WAITING_FOR_OTHER_INPUT")
               for p in needed]
    return _root("ACTION_PLAN_READY" if complete else "INPUT_APPROVAL_REQUIRED", report["overall"], actions)


def validate_plan(value: dict, context: PlanningContext) -> None:
    """Recompute against ORIGINAL trusted context; a plan cannot approve itself."""
    expected = plan(context)
    def same(actual, wanted):
        if type(actual) is not type(wanted): return False
        if isinstance(wanted, dict):
            return set(actual) == set(wanted) and all(same(actual[k], v) for k, v in wanted.items())
        if isinstance(wanted, list):
            return len(actual) == len(wanted) and all(same(a, b) for a, b in zip(actual, wanted))
        return actual == wanted
    _require(same(value, expected), "PLAN_CONTRACT_CONFLICT")


def encode_plan(value: dict, context: PlanningContext) -> bytes:
    validate_plan(value, context)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                     allow_nan=False).encode("ascii") + b"\n"
    _require(0 < len(raw) <= MAX_PLAN, "PLAN_SIZE_INVALID")
    return raw


def parse_plan(raw: bytes, context: PlanningContext) -> dict:
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_PLAN and not raw.startswith(b"\xef\xbb\xbf"),
             "PLAN_SIZE_INVALID")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "DUPLICATE_PLAN_FIELD")
            result[key] = value
        return result
    def nonfinite(_):
        raise ContractError("PLAN_JSON_INVALID")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise ContractError("PLAN_JSON_INVALID") from None
    validate_plan(value, context)
    return value


class ExecutionOutcome(str, Enum):
    SUCCEEDED = "EXECUTION_SUCCEEDED"
    FAILED = "EXECUTION_FAILED"
    CANCELLED = "EXECUTION_CANCELLED"
    REBOOT_REQUIRED = "REBOOT_REQUIRED"


class ElevationOutcome(str, Enum):
    APPROVED = "ELEVATION_APPROVED"
    CANCELLED = "ELEVATION_CANCELLED"
    FAILED = "ELEVATION_FAILED"


EXECUTION_TRANSITIONS = MappingProxyType({
    ExecutionOutcome.SUCCEEDED: "REOBSERVE_REQUIRED",
    ExecutionOutcome.FAILED: "STOP_PRESERVE_FIRST_FAILURE",
    ExecutionOutcome.CANCELLED: "STOP_INSTALLER_CANCELLED",
    ExecutionOutcome.REBOOT_REQUIRED: "STOP_OWNER_REBOOT_THEN_REOBSERVE"})
ELEVATION_TRANSITIONS = MappingProxyType({
    ElevationOutcome.APPROVED: "CONTINUE_ONLY_WITH_APPROVED_FUTURE_PLAN",
    ElevationOutcome.CANCELLED: "STOP_ELEVATION_CANCELLED",
    ElevationOutcome.FAILED: "STOP_ELEVATION_FAILED"})


def confirm_future_readiness(outcome: ExecutionOutcome, after: bytes | None,
                             *, policy: readiness.Policy = readiness.BUILD_POLICY) -> str:
    """Contract-only evaluation of supplied future evidence; performs no reobserve.

    SUCCEEDED denotes a completed process, not an interpretation of exit code 0.
    A later runner must establish that outcome using vendor-supported semantics.
    """
    _require(type(outcome) is ExecutionOutcome, "EXECUTION_OUTCOME_INVALID")
    if outcome is not ExecutionOutcome.SUCCEEDED:
        return EXECUTION_TRANSITIONS[outcome]
    if not _trusted_policy(policy):
        return "POST_INSTALL_READINESS_NOT_CONFIRMED"
    if after is None:
        return "REOBSERVE_REQUIRED"
    try:
        result = readiness.parse_result(after, policy=policy)
    except readiness.ReadinessError:
        return "POST_INSTALL_READINESS_NOT_CONFIRMED"
    return "READINESS_CONFIRMED" if result["overall"] == "READY" else "POST_INSTALL_READINESS_NOT_CONFIRMED"
