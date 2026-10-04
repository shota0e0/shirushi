# v0.2 prerequisite orchestration contract — development only

This Gate adds only `scripts/prerequisite_orchestration_v02.py`, its focused
tests and this document. It is a **pure future-planning contract**, not an
execution runner, installer integration, runtime dependency or Desktop startup
gate. There is no CLI and no real machine/file observation in the planner.

The accepted policy checkpoint is `e2b7b72339d551bd4519fbc757badeb4d86c1cc9`.
The existing prerequisite readiness and candidate validators, frozen evidence,
Desktop/helper code, Tauri configuration, workflows, notices and handoff are not
modified. Parent-controlled independent review determines this Gate's result.

## Layers and authority

```text
OBSERVE → APPLY_PRODUCT_POLICY → VALIDATE_OFFLINE_INPUT → PLAN
                                                 future: EXECUTE → REOBSERVE → CONFIRM
```

Only planning and pure validation of already supplied evidence exist here.
Machine observation and physical input verification remain independent existing
layers; this module does not call their collectors. Future execution and
re-observation are described and tested as contracts, not performed.

`PlanningContext` contains immutable strict readiness JSON bytes, a separately
trusted product `Policy`, immutable candidate-identity receipts and immutable
execution-approval objects. The configured policy must match the accepted
deployment floor; explicit unset `Policy()` remains blocked. Raw frontend policy,
candidate or approval dictionaries are not trusted inputs.

The preserved product policy is:

- VC architecture: **x64**.
- Shirushi deployment readiness floor: **14.51.36247.0**.
- Exact build compatibility minimum: **UNPROVEN / PARKED**.

This floor is not derived again from caller input or mutable vendor “latest”.
The existing schema-2 readiness parser applies and validates the product policy.
No runtime presence can approve an installer; no installer-file presence can
override runtime readiness. Live WebView2 registry-only architecture remains
unproven under the existing detector. Synthetic strong x64 observations used in
tests do not close that live identity gap.

### Candidate adapters are not approval

`vc_identity(output, frozen_record_bytes)` reuses the existing VC frozen-record
validator and strictly checks the existing physical verifier's output shape,
types and independent exact identity constants. It performs **no physical file
read or hash**. `webview2_identity(readiness_bytes)` reuses the strict existing
readiness/offline-input validator, preserving the known exact candidate and
historical signature-policy boundary. It does not execute or freshly verify a
signature, prove installed Runtime architecture/version, or grant publisher or
distribution approval.

The receipts distinguish `NOT_CHECKED`, `CANDIDATE_IDENTITY_VALID` and
`IDENTITY_REJECTED`. Both vendor inputs remain **CANDIDATE**. The exact identities
are independently inherited from existing validators:

- VC SHA-256:
  `843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c`.
- WebView2 SHA-256:
  `f6df8e4bc857786ff641cd01da1449169eaf8236c936ced485ea61685ba4da40`.

`ExecutionApproval` defaults to **false**. A future explicit trusted-call-site
approval requires the exact prerequisite SHA and a nonempty bounded approval
record identifier. It is never deserialized from frontend/report JSON and is
not inferred from Freeze PASS, a receipt, signature, file presence or readiness.
There is no shipped positive execution approval. Synthetic positive approvals
exercise the future planner only. Legal distribution approval remains separate.

Strict schemas and typed receipts validate consistency, **not provenance or
cryptographic authentication of arbitrary externally supplied observations**.
The trusted caller must retain the original verification/approval provenance;
an untrusted JSON object that repeats known constants is not an authentic
verification receipt. Plan validation is anchored to the original trusted
context, not fields supplied by the plan itself.

## Deterministic decision contract

| Evidence | Planned result/action |
| --- | --- |
| Both required runtimes READY | `READY_NO_ACTION`, empty actions |
| Required runtime MISSING, or VC OUTDATED, without validated approved input | `INPUT_APPROVAL_REQUIRED`; action `BLOCKED`, logical `requiredAction=INSTALL_OR_UPGRADE` |
| All required actions have exact validated inputs and trusted future approvals | `ACTION_PLAN_READY`; future `INSTALL_OR_UPGRADE` actions |
| Either detector unknown/failed/malformed or architecture ambiguous/mismatched | `READINESS_UNKNOWN_BLOCKED`; all needed actions blocked |
| Missing/unset/unaccepted product policy | `POLICY_BLOCKED` |
| A required candidate identity is rejected | `POLICY_BLOCKED`; other required actions also blocked |
| Unsupported WebView2 OUTDATED/update evidence | Blocked; no invented update policy |

The current readiness contract does not emit a valid WebView2 OUTDATED state.
A forged unsupported state is rejected by its validator, not converted into an
update plan. `REPAIR` is reserved for future explicit evidence and policy. It is
never inferred from presence, outdated version, failed installation or existence
of a candidate. READY prerequisites are omitted from the action list: mixed
READY/missing yields one action, both missing yields two.

Unknown and policy-blocked states dominate the whole plan. If one required input
is unapproved, even an independently approved other action remains blocked,
with `WAITING_FOR_OTHER_INPUT` rather than partial execution.

Future ordering is **VC Runtime, then WebView2**, sequentially. This is explicit
Shirushi product policy. The consulted official guidance requires prerequisites
before application use, but does not establish a VC↔WebView2 technical ordering
dependency. No dependency or parallel execution capability is claimed here.

## Strict plan schema version 1

Top-level fields are exactly:

| Field | Contract |
| --- | --- |
| `contractVersion` | Integer 1, not boolean |
| `operation` | `prerequisite_orchestration_plan` |
| `result` | `READY_NO_ACTION`, `ACTION_PLAN_READY`, `INPUT_APPROVAL_REQUIRED`, `READINESS_UNKNOWN_BLOCKED`, `POLICY_BLOCKED` |
| `overallReadiness` | Derived from strict readiness evidence, or `READINESS_UNKNOWN` for invalid/unavailable evidence |
| `executionAllowed` | **Always false in this Gate**, even `ACTION_PLAN_READY` |
| `actions` | Deterministically ordered needed/blocked action records |
| `rebootPolicy` | `NEVER_AUTOMATIC_OWNER_CONTROLLED` |
| `automaticRetry` | Integer 0 |
| `executionCounts` | Exactly `vc: 0`, `webview2: 0` |

Each action has exactly `prerequisite`, `reason`, `action`, `requiredAction`,
`candidateStatus`, `candidateSha256`, `executionStatus`. Prerequisites are only
`VC_RUNTIME_X64` and `WEBVIEW2_EVERGREEN_X64`. `requiredAction` preserves logical
need separately from executable permission. `FUTURE_APPROVED_NO_EXECUTOR` is
future planning readiness, not current permission to run anything.

`validate_plan`, `encode_plan` and `parse_plan` recompute the entire expected
result against the original context. Unknown/missing nested fields, wrong types,
bool-as-int values, reordered actions, coordinated status/approval/hash changes,
duplicate JSON keys, nonfinite values, BOM and trailing objects are rejected.
Maximum encoded/parsed output is 16,384 bytes, canonical ASCII JSON plus LF.
Current execution counts are contractual zeros; no process runner exists that
could increment them.

## Future execution, reboot and elevation contracts

Closed execution outcomes are `EXECUTION_SUCCEEDED`, `EXECUTION_FAILED`,
`EXECUTION_CANCELLED`, `REBOOT_REQUIRED`. These are not mappings from raw exit
codes. A later runner must establish process completion and vendor-supported
result semantics. Exit code 0 alone never establishes readiness.

- Successful future execution → **REOBSERVE_REQUIRED**, not READY.
- Failed future execution → STOP, preserve the first material failure.
- Installer cancellation → STOP_INSTALLER_CANCELLED.
- Reboot required → STOP for an Owner/user-controlled reboot, then reobserve.

No automatic reboot, retry, repair-after-failure, installer-mode substitution
or fallback elevation is permitted. A reported reboot requirement does not
prove that the reboot occurred. Future elevation distinguishes
`ELEVATION_APPROVED`, `ELEVATION_CANCELLED`, `ELEVATION_FAILED`; UAC cancellation
is not an installer failure. Elevation approval cannot override input/plan
approval. It only permits continuing an otherwise approved future plan.

`confirm_future_readiness` is a **contract-only pure function** over supplied
future outcome and already validated post-observation bytes. It neither executes
a process nor performs REOBSERVE. Without post-observation it returns
REOBSERVE_REQUIRED. After successful process completion, only a strict detector
report with overall READY produces READINESS_CONFIRMED. Missing, outdated,
unknown, invalid or untrusted-policy evidence returns
POST_INSTALL_READINESS_NOT_CONFIRMED. A full runner must still ensure the
evidence is a fresh observation after that exact process, not an old report;
freshness/provenance are not invented by this pure slice.

## Verification and next Gate

```powershell
.\.venv-py312\Scripts\python.exe -B -m unittest tests.test_prerequisite_orchestration_v02 -v
.\.venv-py312\Scripts\python.exe -B -m unittest tests.test_prerequisite_orchestration_v02 tests.test_prerequisite_readiness_v02 tests.test_verify_vc_runtime_candidate tests.test_package_v02_development -v
```

Tests use synthetic observations/approvals, strict frozen-record evidence and
pure adapters. A zero-I/O control forbids subprocess, file reads, Windows native
API loading, real observation and physical candidate verification during all
planner/adapter/confirmation API calls. Tests cover the Owner matrix, global
blocking, deterministic order, no REPAIR/retry, strict schema/tamper boundaries,
future reboot/UAC distinctions and required readiness re-confirmation. Loading
the frozen record in test setup is not physical installer verification.

VC installer execution: **0**. WebView2 installer execution: **0**. No registry
writes, UAC, elevation, reboot, repair, uninstall, download or new CI occur.
`bundle.active` remains false. No production/NSIS/clean-Windows work begins.

Next proposed Gate: **Prerequisite Execution Runner — Synthetic / No-Execute
Harness**. Do not execute real installers automatically. Actual execution
requires a later separately approved Canary.

Production adoption: UNAPPROVED. Production Signing: PARKED.
Distribution Compliance: NOT READY. Full F3A: NOT READY.
