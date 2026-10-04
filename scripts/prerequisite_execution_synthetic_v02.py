"""Data-only synthetic prerequisite sessions. No CLI or real execution adapter."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json

from scripts import prerequisite_orchestration_v02 as planner
from scripts import prerequisite_readiness_v02 as readiness

MAX_RESULT = 32768
MAX_DEPTH = 12


class SyntheticError(Exception):
    """Closed contract failure, never a raw path or process diagnostic."""


def _require(condition, code):
    if not condition:
        raise SyntheticError(code)


def _identifier(value):
    return (type(value) is str and 0 < len(value) <= 128
            and all(c.isascii() and (c.isalnum() or c in "_.:-") for c in value))


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def plan_digest(raw: bytes, context: planner.PlanningContext) -> str:
    """Digest the existing planner's canonical validated representation."""
    value = planner.parse_plan(raw, context)
    return hashlib.sha256(planner.encode_plan(value, context)).hexdigest()


class Outcome(str, Enum):
    SUCCEEDED = "SYNTHETIC_PROCESS_COMPLETED"
    FAILED = "SYNTHETIC_PROCESS_FAILED"
    LAUNCH_FAILED = "SYNTHETIC_LAUNCH_FAILED"
    CANCELLED = "SYNTHETIC_PROCESS_CANCELLED"
    TIMEOUT = "SYNTHETIC_TIMEOUT"
    REBOOT_REQUIRED = "SYNTHETIC_REBOOT_REQUIRED"


@dataclass(frozen=True)
class SyntheticAuthorization:
    """Explicit trusted TEST-ONLY permission; never real execution permission."""
    session_id: str
    approved: bool = False
    plan_digest: str | None = None
    record: str | None = None

    def __post_init__(self):
        _require(_identifier(self.session_id) and type(self.approved) is bool, "AUTHORIZATION_INVALID")
        _require((_digest(self.plan_digest) and _identifier(self.record)) if self.approved
                 else self.plan_digest is None and self.record is None, "AUTHORIZATION_INVALID")


@dataclass(frozen=True)
class SyntheticStep:
    prerequisite: str
    candidate_sha256: str
    elevation: planner.ElevationOutcome = planner.ElevationOutcome.APPROVED
    outcome: Outcome = Outcome.SUCCEEDED
    exit_code: int | None = 0
    reobserve: bytes | None = None

    def __post_init__(self):
        _require(type(self.prerequisite) is str and self.prerequisite in planner.ORDER
                 and _digest(self.candidate_sha256), "STEP_IDENTITY_INVALID")
        _require(type(self.elevation) is planner.ElevationOutcome and type(self.outcome) is Outcome,
                 "STEP_OUTCOME_INVALID")
        _require(self.exit_code is None or (type(self.exit_code) is int and 0 <= self.exit_code <= 0xffffffff),
                 "STEP_EXIT_CODE_INVALID")
        _require(self.reobserve is None or (type(self.reobserve) is bytes
                 and 0 < len(self.reobserve) <= readiness.MAX_RESULT), "STEP_OBSERVATION_INVALID")


@dataclass(frozen=True)
class SyntheticAdapter:
    """Sealed immutable transcript, not functions, subprocesses or callbacks."""
    steps: tuple[SyntheticStep, ...] = ()

    def __post_init__(self):
        _require(type(self.steps) is tuple and len(self.steps) <= 2, "ADAPTER_INVALID")
        for step in self.steps:
            _check(step, SyntheticStep)
        _require(len({s.prerequisite for s in self.steps}) == len(self.steps), "DUPLICATE_STEP")


def _check(value, expected):
    _require(type(value) is expected and set(vars(value)) == set(expected.__dataclass_fields__),
             "SEALED_INPUT_INVALID")
    try:
        value.__post_init__()
    except (AttributeError, TypeError):
        raise SyntheticError("SEALED_INPUT_INVALID") from None


def _statuses(report):
    return {p: report["runtimes"][k]["status"]
            for p, k in zip(planner.ORDER, ("vc", "webview2"))}


def _replay(session_id, raw, context, adapter, authorization, initial_digest, attempted_raw, input_changed=False):
    _require(_identifier(session_id), "SESSION_ID_INVALID")
    result = {"schemaVersion": 1, "sessionId": session_id, "mode": "SYNTHETIC_ONLY",
              "planDigest": initial_digest, "approvedActions": [], "candidateIdentities": [],
              "transitions": ["IDLE"], "syntheticOutcomes": [], "finalReadiness": None,
              "finalState": "IDLE", "reason": None, "syntheticAttempts": {"vc": 0, "webview2": 0},
              "realExecutionCounts": {"vc": 0, "webview2": 0}, "automaticRetry": 0,
              "automaticReboot": False, "realExecutionAllowed": False,
              "syntheticAuthorization": None}

    def transition(state):
        result["transitions"].append(state)
        result["finalState"] = state

    def stop(state, reason):
        transition(state)
        result["reason"] = reason
        return result

    if input_changed:
        return stop("BLOCKED", "CAPTURED_INPUT_CHANGED")
    try:
        value = planner.parse_plan(raw, context)
        if (type(attempted_raw) is not bytes or attempted_raw != raw or initial_digest is None
                or plan_digest(attempted_raw, context) != initial_digest):
            return stop("BLOCKED", "PLAN_BINDING_CHANGED")
        _check(adapter, SyntheticAdapter)
        _check(authorization, SyntheticAuthorization)
    except (planner.ContractError, readiness.ReadinessError, SyntheticError, AttributeError, TypeError):
        return stop("BLOCKED", "VALIDATED_INPUT_UNAVAILABLE")
    transition("PLAN_VALIDATED")
    result["syntheticAuthorization"] = {"approved": authorization.approved,
                                        "record": authorization.record, "planDigest": authorization.plan_digest}
    if value["result"] not in {"READY_NO_ACTION", "ACTION_PLAN_READY"}:
        return stop("BLOCKED", "PLANNER_ACTION_DENIED")
    actions = value["actions"]
    approval_records = {a.prerequisite: a.approval_record for a in context.approvals}
    result["approvedActions"] = [{"prerequisite": a["prerequisite"], "action": a["action"],
                                 "approvalRecord": approval_records[a["prerequisite"]]} for a in actions]
    result["candidateIdentities"] = [{"prerequisite": a["prerequisite"], "sha256": a["candidateSha256"]}
                                     for a in actions]
    report = readiness.parse_result(context.readiness_report, policy=context.policy)
    result["finalReadiness"] = _statuses(report)
    if value["result"] == "READY_NO_ACTION":
        return stop("COMPLETE", "NO_ACTION_REQUIRED")
    transition("WAITING_FOR_APPROVAL")
    if not authorization.approved:
        result["reason"] = "SYNTHETIC_APPROVAL_REQUIRED"
        return result
    if authorization.session_id != session_id or authorization.plan_digest != initial_digest:
        return stop("BLOCKED", "SYNTHETIC_APPROVAL_BINDING_CHANGED")
    if ([s.prerequisite for s in adapter.steps] != [a["prerequisite"] for a in actions]
            or any(s.candidate_sha256 != a["candidateSha256"] for s, a in zip(adapter.steps, actions))):
        return stop("BLOCKED", "SYNTHETIC_TRANSCRIPT_BINDING_CHANGED")
    transition("READY_TO_EXECUTE")
    confirmed = {p for p, state in result["finalReadiness"].items() if state == "READY"}
    for step in adapter.steps:
        record = {"prerequisite": step.prerequisite, "candidateSha256": step.candidate_sha256,
                  "elevation": step.elevation.value, "outcome": None, "exitCode": None,
                  "attempted": False, "reobserveDigest": None, "readinessAfter": None}
        result["syntheticOutcomes"].append(record)
        if step.elevation is planner.ElevationOutcome.CANCELLED:
            return stop("CANCELLED", "SYNTHETIC_UAC_CANCELLED")
        if step.elevation is planner.ElevationOutcome.FAILED:
            return stop("FAILED", "SYNTHETIC_UAC_FAILED")
        key = "vc" if step.prerequisite == planner.VC else "webview2"
        label = "VC" if key == "vc" else "WEBVIEW2"
        transition("EXECUTING_" + label)
        result["syntheticAttempts"][key] += 1
        record.update(attempted=True, outcome=step.outcome.value, exitCode=step.exit_code)
        if step.outcome is Outcome.REBOOT_REQUIRED:
            return stop("REBOOT_REQUIRED", "OWNER_REBOOT_REQUIRED_NEW_SESSION")
        if step.outcome is Outcome.CANCELLED:
            return stop("CANCELLED", "SYNTHETIC_PROCESS_CANCELLED")
        if step.outcome is not Outcome.SUCCEEDED or step.exit_code != 0:
            return stop("FAILED", step.outcome.value if step.outcome is not Outcome.SUCCEEDED
                        else "SYNTHETIC_NONZERO_EXIT")
        transition("REOBSERVING_" + label)
        if step.reobserve is None:
            return stop("BLOCKED", "REOBSERVATION_REQUIRED")
        record["reobserveDigest"] = hashlib.sha256(step.reobserve).hexdigest()
        try:
            after = readiness.parse_result(step.reobserve, policy=context.policy)
        except readiness.ReadinessError:
            return stop("BLOCKED", "REOBSERVATION_INVALID")
        states = _statuses(after)
        record["readinessAfter"] = states
        result["finalReadiness"] = states
        if (states[step.prerequisite] != "READY" or any(states[p] != "READY" for p in confirmed)
                or any(s in {"DETECTION_FAILED", "POLICY_UNSET"} for s in states.values())):
            return stop("BLOCKED", "POST_ACTION_READINESS_NOT_CONFIRMED")
        confirmed.add(step.prerequisite)
    if all(s == "READY" for s in result["finalReadiness"].values()):
        return stop("COMPLETE", "SYNTHETIC_READINESS_CONFIRMED")
    return stop("BLOCKED", "FINAL_READINESS_NOT_CONFIRMED")


def _snapshot(raw, context, adapter, authorization):
    """Copy only validated sealed data; no arbitrary deepcopy or callbacks."""
    _require(type(raw) is bytes and type(context) is planner.PlanningContext
             and set(vars(context)) == set(planner.PlanningContext.__dataclass_fields__), "BINDING_INVALID")
    _check(context.policy, readiness.Policy)
    _require(type(context.candidates) is tuple and type(context.approvals) is tuple, "BINDING_INVALID")
    def receipts(values, kind):
        _require(len(values) <= 2, "BINDING_INVALID")
        for item in values: _check(item, kind)
        return tuple(kind(**vars(item)) for item in values)
    copied = planner.PlanningContext(context.readiness_report, readiness.Policy(context.policy.vc_deployment_floor),
        receipts(context.candidates, planner.CandidateIdentity), receipts(context.approvals, planner.ExecutionApproval))
    _check(adapter, SyntheticAdapter)
    _check(authorization, SyntheticAuthorization)
    return (raw, copied, SyntheticAdapter(tuple(SyntheticStep(**vars(s)) for s in adapter.steps)),
            SyntheticAuthorization(**vars(authorization)))


class Session:
    """One in-memory session; terminal sessions cannot retry or resume a reboot."""
    __slots__ = ("_session_id", "_original", "_binding", "_initial_digest", "_used", "_run_receipt")

    def __init__(self, session_id, raw_plan, context, adapter=None, authorization=None):
        _require(_identifier(session_id), "SESSION_ID_INVALID")
        self._session_id = session_id
        self._original = (raw_plan, context, SyntheticAdapter() if adapter is None else adapter,
                          SyntheticAuthorization(session_id) if authorization is None else authorization)
        self._used = False
        self._run_receipt = None
        try:
            self._binding = _snapshot(*self._original)
            self._initial_digest = plan_digest(self._binding[0], self._binding[1])
        except (planner.ContractError, readiness.ReadinessError, SyntheticError, AttributeError, TypeError):
            self._binding = None
            self._initial_digest = None

    @property
    def raw_plan(self): return self._original[0]

    @property
    def context(self): return self._original[1]

    @property
    def adapter(self): return self._original[2]

    @property
    def authorization(self): return self._original[3]

    def run(self, current_plan=None):
        _require(not self._used, "SESSION_ALREADY_USED")
        self._used = True
        changed = False
        if self._binding is not None:
            try:
                changed = _snapshot(*self._original) != self._binding
            except (planner.ContractError, readiness.ReadinessError, SyntheticError, AttributeError, TypeError):
                changed = True
        self._run_receipt = (self.raw_plan if current_plan is None else current_plan, changed)
        return self._expected()

    def _expected(self):
        _require(self._used and self._run_receipt is not None, "SESSION_NOT_RUN")
        raw, context, adapter, authorization = self._binding or self._original
        attempted, changed = self._run_receipt
        return _replay(self._session_id, raw, context, adapter, authorization,
                       self._initial_digest, attempted, changed)

    def validate_result(self, value):
        """Replay independently from the ORIGINAL trusted inputs, not result fields."""
        expected = self._expected()
        def same(actual, wanted):
            if type(actual) is not type(wanted): return False
            if type(wanted) is dict:
                return set(actual) == set(wanted) and all(same(actual[k], v) for k, v in wanted.items())
            if type(wanted) is list:
                return len(actual) == len(wanted) and all(same(a, b) for a, b in zip(actual, wanted))
            return actual == wanted
        _require(same(value, expected), "RESULT_REPLAY_CONFLICT")

    def encode_result(self, value):
        self.validate_result(value)
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                         allow_nan=False).encode("ascii") + b"\n"
        _require(len(raw) <= MAX_RESULT, "RESULT_SIZE_INVALID")
        return raw

    def parse_result(self, raw):
        _require(type(raw) is bytes and 0 < len(raw) <= MAX_RESULT and not raw.startswith(b"\xef\xbb\xbf"),
                 "RESULT_SIZE_INVALID")
        def unique(pairs):
            value = {}
            for k, v in pairs:
                _require(k not in value, "RESULT_DUPLICATE_FIELD")
                value[k] = v
            return value
        def nonfinite(_):
            raise SyntheticError("RESULT_JSON_INVALID")
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
        except (ValueError, UnicodeError, RecursionError):
            raise SyntheticError("RESULT_JSON_INVALID") from None
        def depth(item, level=0):
            _require(level <= MAX_DEPTH, "RESULT_DEPTH_INVALID")
            if type(item) is dict:
                for nested in item.values(): depth(nested, level + 1)
            elif type(item) is list:
                for nested in item: depth(nested, level + 1)
        depth(value)
        self.validate_result(value)
        return value
