# v0.2 synthetic prerequisite execution runner — no execution

## Bounded Task contract

Owner source: **IMPLEMENT+VERIFY Synthetic Prerequisite Execution Runner**,
the current Owner instruction and its A–R test matrix. Accepted prior checkpoint:
`4a0773dcb6ad10ed0fc98c775574f6f670eef3eb`, branch
`codex/f3a-rust-sdk-parity`, repository `C:/dev/shirushi-public-export`.

Objective: test one sequential session's prerequisite transitions using only
synthetic data. Scope is exactly three new files:

- `scripts/prerequisite_execution_synthetic_v02.py`
- `tests/test_prerequisite_execution_synthetic_v02.py`
- this document

Existing planner/readiness/candidate validators, Desktop/helper/package/runtime
binding, Tauri, workflows, frozen evidence and unrelated files are unchanged.
`docs/CURRENT_HANDOFF.md` is explicitly preserved by Owner instruction. This
document records the current bounded slice; its earlier handoff does not grant
new execution authority. Independent review and parent-controlled commit remain
the completion boundary.

Recommended model: GPT-6.1 Sol. Recommended reasoning effort: High. Selection
reason: bounded authority-sensitive transition verification. Escalate/STOP for
conflicting approval, real-execution authority, extra paths, or Production change.

No CLI, real process adapter or installer command exists. The module imports
only dataclasses, enums, hashing/JSON and existing pure validation contracts.
It never downloads, reads candidate files, discovers executables, invokes
PowerShell/msiexec/installer processes, loads native APIs, elevates, writes
registry, observes the host, repairs, uninstalls or reboots. Loading tests and
repository source for tests is not prerequisite execution.

## Authority remains layered

```text
existing trusted readiness/input facts → strict existing planner
                                      → explicit TEST-ONLY authorization
                                      → sealed synthetic transcript
                                      → target-specific supplied reobservation
```

The existing planner is the sole source of need, approval, identity and ordering.
`planner.parse_plan` / `encode_plan` recompute its strict contract against the
original `PlanningContext`. The runner does not duplicate those decisions.
The planner's `executionAllowed=false` is unchanged and is **never** interpreted
as permission for a real process. Runner `realExecutionAllowed` is always false.

`SyntheticAuthorization` is separate explicit trusted in-memory **test-only**
permission. It defaults to denied; a positive object binds the ASCII session ID,
canonical validated-plan SHA-256 and bounded approval-record identifier. It is
not read from JSON, environment, a filename/path, publisher name, “latest”, or
candidate presence. A positive synthetic authorization cannot authorize a real
process or promote either candidate. `approvedActions.approvalRecord` comes from
the existing planner context's execution approval; `syntheticAuthorization`
records simulation permission separately. Neither receipt authenticates arbitrary
external JSON. The trusted caller is responsible for their original provenance.

The accepted VC floor is still **14.51.36247.0 / x64**. The exact build
compatibility minimum remains **UNPROVEN / PARKED**. VC and WebView2 inputs remain
**CANDIDATE**. Their exact approved identity constants are inherited from the
existing planner, not introduced again or overridden here. Live registry-only
WebView2 architecture uncertainty remains unchanged. Strong x64 observations
in these tests are synthetic, not a clean-machine validation result.

`SyntheticAdapter` is an exact-class frozen tuple of at most two exact-class
`SyntheticStep` data records. There are no injectable callbacks, executable
paths, command strings or I/O methods. Each step contains prerequisite, expected
candidate SHA, closed simulated elevation/outcome, optional numeric exit code
and optional immutable readiness-report bytes. Unknown/subclass/raw objects,
duplicate steps, wrong types and nonbounded inputs are rejected or BLOCKED.

## Session identity and one-shot lifetime

`Session` captures sealed data copies and the planner's canonical digest at
construction. Public input properties are read-only. Before running it checks
the original supplied objects still match the captured copies. Mutation of a
trusted receipt/context/transcript/authorization after validation blocks with
zero attempts rather than reauthorizing new input.

`run(current_plan)` optionally supplies the attempted bytes for the mutation
negative control. Those bytes must equal the original validated plan **exactly**,
and its canonical digest must still match. Even a formatting-only change is
BLOCKED. The original canonical digest remains in the audit result.

`run` consumes the session once, including denied, blocked, cancelled and failed
sessions. A second call raises `SESSION_ALREADY_USED`; there is no automatic
retry, fallback, alternative elevation, scheduled task, mode switch, repair,
parallel execution or rollback. Synthetic launch attempts are at most one per
step. Counts are in-memory and not a durable cross-process execution ledger.
Sessions are synchronous, serialized test objects, not a concurrency interface.
Python-private fields are not a security sandbox against hostile code that
rewrites internals; the trusted test/controller boundary must remain intact.

## Closed transitions

States are exactly `IDLE`, `PLAN_VALIDATED`, `WAITING_FOR_APPROVAL`,
`READY_TO_EXECUTE`, `EXECUTING_VC`, `REOBSERVING_VC`, `EXECUTING_WEBVIEW2`,
`REOBSERVING_WEBVIEW2`, `REBOOT_REQUIRED`, `CANCELLED`, `FAILED`, `BLOCKED`,
`COMPLETE`. All transitions are replay-derived, not caller-submitted instructions.

- Both prerequisites READY: COMPLETE with no actions/attempts, without simulation
  authorization. No runtime installation is inferred.
- A denied planner: BLOCKED before any adapter step. Default denied simulation
  authorization: WAITING_FOR_APPROVAL with no attempts; this session is consumed.
- An approved synthetic plan: VC, then WebView2, skipping READY prerequisites.
  This is existing Shirushi product policy, not a claimed VC↔WebView2 dependency.
- Each synthetic successful process goes to its target's REOBSERVING state.
  Exit 0 alone is never COMPLETE or READY.
- The supplied report is parsed by the existing strict readiness validator.
  The target must be READY; every previously READY prerequisite must remain
  READY. Detection failure, policy uncertainty, missing/outdated target or invalid
  report stops. An intermediate VC READY / WebView2 MISSING report may proceed
  to the already approved WebView2 action; overall READY is not required early.
- COMPLETE after actions requires final readiness of **both** prerequisites.
- UAC cancellation → CANCELLED / `SYNTHETIC_UAC_CANCELLED`, not installer failure.
  UAC failure → FAILED / `SYNTHETIC_UAC_FAILED`. Neither consumes an installer
  launch attempt or permits fallback elevation.
- Launch failure, nonzero exit, simulated failure or timeout → FAILED with an
  explicit reason. Process cancellation → CANCELLED. No retry/reobserve follows.
- Explicit simulated reboot outcome → REBOOT_REQUIRED and STOP. No reboot,
  next action, automatic retry or assumed READY follows. Future work requires
  fresh readiness, a newly approved plan and a new session. No reboot resume
  token exists. Numeric 3010 alone is not borrowed from MSI as this EXE's semantics.

VC success followed by WebView2 failure retains VC's successful synthetic
outcome/reobservation and the later failure. It does not claim COMPLETE, perform
rollback, or treat the unobserved failed action as installed. A future action
must start from fresh independently obtained readiness.

All elevation, process, timeout, reboot and observation behavior here is
**synthetic data**, not vendor return-code semantics or measured machine state.
A later process harness must establish its own supported semantics and freshness.

## Strict bounded result schema 1

Top-level fields are exactly:

| Field | Contract |
| --- | --- |
| `schemaVersion`, `sessionId`, `mode` | Integer 1 (not bool/float); bounded ASCII ID; `SYNTHETIC_ONLY` |
| `planDigest` | Captured canonical validated-plan SHA, or null when unavailable |
| `approvedActions` | Ordered prerequisite/action and planner-context approval record |
| `candidateIdentities` | Ordered prerequisite/exact SHA from validated plan |
| `syntheticAuthorization` | Validated test-only approved/record/planDigest, or null if validation fails |
| `transitions`, `syntheticOutcomes` | Replay-derived closed transitions and bounded per-step observations |
| `finalReadiness`, `finalState`, `reason` | Last valid supplied statuses, terminal state/reason; no fabricated readiness |
| `syntheticAttempts` | Exactly vc/webview2 attempt integers, each 0 or 1 |
| `realExecutionCounts` | Exactly vc/webview2 integers, both **0** |
| `automaticRetry`, `automaticReboot`, `realExecutionAllowed` | Integer 0, false, false |

Each synthetic outcome has exactly `prerequisite`, `candidateSha256`, `elevation`,
`outcome`, `exitCode`, `attempted`, `reobserveDigest`, `readinessAfter`. Process
outcome/code are null before a simulated launch. Reobserve SHA binds supplied
report bytes, not a claim of fresh machine observation. No raw paths, command
output, credentials, secret data or installer contents are emitted.

Output is canonical compact sorted ASCII JSON plus LF, maximum **32,768 bytes**.
Parser rejects BOM, duplicates, nonfinite values, trailing JSON, unknown/missing
fields, bool/float substitutions and depth beyond **12**. Strict comparison
rejects invalid transitions, changed hashes/identities/digest, forged final states,
or coordinated fabricated COMPLETE/readiness/outcome fields.

Result validation is allowed only **after this Session actually ran**. It replays
the frozen original context/plan/transcript/authorization and recorded attempted
bytes; it never trusts fields in the result as authority. Callers cannot supply
an alternate attempted-plan argument to validate a trace that did not run.
This is independent consistency replay, not cryptographic authentication of a
foreign session. Keep the original trusted session and inputs for validation.

## Verification and next bounded slice

```powershell
.\.venv-py312\Scripts\python.exe -B -m unittest tests.test_prerequisite_execution_synthetic_v02 -v
.\.venv-py312\Scripts\python.exe -B -m unittest tests.test_prerequisite_execution_synthetic_v02 tests.test_prerequisite_orchestration_v02 tests.test_prerequisite_readiness_v02 tests.test_verify_vc_runtime_candidate tests.test_package_v02_development
```

Tests cover A–R, both READY/no action, mixed and sequential prerequisite cases,
partial success, default denial, UAC/cancellation/failure, timeout/reboot, readiness
reconfirmation, single-use/duplicate rejection, plan/input tamper, strict result
replay and schema boundaries. Mock guards forbid subprocess, shell invocation,
native API loading, registry writes/reads and real observers/verifiers. Static
guards prohibit real process imports and callback adapters. Existing pure tests
are regressed separately; this Gate makes no new native CI claim.

Real VC execution: **0**. Real WebView2 execution: **0**. Reboot/elevation/registry
mutation: **0**. No new CI, downloads, host changes or installer integration.
`bundle.active` remains false. The accepted Limited result/protocol, package and
runtime-binding products are not changed.

Next proposed Gate: **Real Process Harness — No Installer**. Do not start it in
this Gate. Real prerequisite installation remains a separately approved future
Canary; synthetic PASS is not a runtime/zero-setup/Production validation result.

Production adoption: UNAPPROVED. Production Signing: PARKED.
Distribution Compliance: NOT READY. Full F3A: NOT READY.
