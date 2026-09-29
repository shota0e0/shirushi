# F2C closeout / G2 Owner approval

Date: 2026-09-29. Scope: the shared Web / Personal Mark v2 / Tauri-Python
bridge **foundation**, not a completed v0.2 product or release.

**F2C Tauri/Python bridge foundation: PASS. G2: OWNER APPROVED.**
The Owner explicitly approved this gate after the manual observations below.
This is an Owner attestation recorded from the project review, not an assertion
that a formal GitHub approving review exists. Merge remains the Owner's separate
decision. No merge, release, tag, deployment, or F3 implementation is performed
by this closeout.

This current record supersedes the earlier native-manual PENDING / G2 NOT READY
status in the [historical verification ledger](f2c-verification.md). Historical
F2C.4/F2C.5 counts, decisions, and evidence are retained, not rewritten. CI-only
summaries and the per-run canary checklist cannot themselves grant Owner approval.

## Reviewed implementation snapshot

Repository: [shota0e0/shirushi](https://github.com/shota0e0/shirushi).
PR: [#1](https://github.com/shota0e0/shirushi/pull/1), base `main`, head
`codex/f2c-public-export`.

- Approved public base: `2884b6c4da1339778ae4678ddae159a4100fa130`.
- Implementation head: `16f3e00528fd970acc161e2fdd9a67d99bc53760`.
- At this snapshot: 12 commits, 115 changed files; clean index/worktree.
- PR at review: OPEN, not draft, MERGEABLE; no auto-merge requested.
- The two closeout documents are documentation-only changes after this snapshot;
  they do not change any Desktop, runner, bridge, Web, package, or workflow input.
- Local fetch configuration stores only `origin/main`; the feature upstream
  remote-tracking ref is unavailable locally. PR head identity is checked using
  GitHub, not an invented local ahead/behind result.

## Automated CI evidence

[Windows verification run 36568301438](https://github.com/shota0e0/shirushi/actions/runs/36568301438)
completed successfully for the implementation head above. Tested synthetic merge:
`7e0b75a34ce519db26e6d61c2c6fde8e4962a0b1`; base is the approved public base above.

| Check | Result |
| --- | --- |
| Rust bridge tests | PASS, 15 tests |
| Ordinary Tauri debug build | PASS |
| Manual-canary Desktop tests / runner tests | PASS, 20 / 10 tests |
| Dedicated runner tests and build; manual-canary executable build | PASS |
| Bounded Python bridge / Personal Mark regression | PASS, 95 tests |
| Audit, governance, and package tests | PASS, 33 tests |
| Shared Web, Desktop Web, Personal Mark v2, Unicode, Canary checks | PASS |
| PowerShell launcher checks; PowerShell/native Prepare equivalence | PASS |
| Locked dependency/license preflight | PASS |
| Source/staged resource audit; package creation and independent audit | PASS |
| Development artifact upload | PASS |

These are bounded checks, not a claim that the full historical Python regression
or a native Windows GUI session ran in CI. Any later documentation-only CI run
will be recorded on the PR separately; it does not retroactively change the identity of
the Owner-tested artifact.

### Owner-tested development artifact

[Artifact 11033306004](https://github.com/shota0e0/shirushi/actions/runs/36568301438/artifacts/11033306004):
`shirushi-DEVELOPMENT-CANARY-windows-x64-7e0b75a34ce519db26e6d61c2c6fde8e4962a0b1`.
This is an unsigned DEVELOPMENT CANARY, not a release, installer, signed build,
or production distribution. It contains 179 files. GitHub reported expiration:
2026-10-06 12:34:22 UTC; retention is finite.

| Object | SHA-256 |
| --- | --- |
| GitHub artifact ZIP digest | `7391ab979f3c481873e575f652b86a73fd6b97aee65807901381b2cddabb5cfb` |
| Artifact manifest | `32167805f22014cea9332e89ab6ba3be30809c727f0aa4d5c489c6b38fd92ab6` |
| SHA256SUMS file | `9cfca52fa6262ba97220138c4cedd792a631d082891711d888e9859c1c1a81be` |
| Native runner | `c9a301850a8f38142af2fb7ad4a32a028abd556cba7c5cb0b755d0dc89a8cc72` |

## Owner-reported native manual evidence

Source: explicit Owner observation and approval in the project review. These are
not agent-executed manual tests. No personal paths, environment dumps, private
profiles, or raw local logs are imported into this public record.

| Scenario | Owner-observed result |
| --- | --- |
| Normal Prepare and launch | `PREPARE_OK`; Tauri window and Shared UI visible; package-local CPython 3.12.10 x64; Python bridge healthy; Typed Personal Mark v2 loaded; `SIDECAR_OBSERVED` |
| Normal shutdown | Desktop exit code 0; remaining children 0 / Python 0; `ORPHAN_PROCESS_NONE` |
| Sidecar-unavailable | Unavailable UI; Personal Mark unread; Add / Verify disabled; no false success; `SIDECAR_NOT_OBSERVED` (expected) |
| Sidecar-unavailable shutdown | Desktop exit code 0; remaining children 0 / Python 0; `ORPHAN_PROCESS_NONE` |
| Subsequent normal recovery/relaunch | Owner prepared the same latest artifact version again; healthy bridge, Typed v2 read, `SIDECAR_OBSERVED`, exit code 0, remaining 0 / Python 0, `ORPHAN_PROCESS_NONE` |

The recovery report does not claim overwrite of an existing venv: Prepare still
rejects an existing `.venv-py312`, and no exact re-extraction details were supplied.
This demonstrates the reported unavailable-to-normal recovery path; it does not
establish arbitrary injected-crash recovery or universal process-cleanup reliability.

### Historical failures and security boundary

- Direct execution of the Desktop executable before Prepare showed bridge
  unavailable and safe UI failure. Prepare not having run was a possible factor,
  not a proven root cause.
- The PowerShell launcher was rejected consistently with `RemoteSigned`, an
  Internet-zone MOTW (`ZoneId=3`), and an unsigned script. This was not established
  as a Windows-10-specific or PowerShell-version-specific defect.
- The native development runner became the formal manual route. No
  ExecutionPolicy change, Bypass, automatic unblock, administrator requirement,
  or Windows security-policy change was used as a remedy.
- Earlier runs reported `MONITOR_UNAVAILABLE`, and later an orphan count of 2
  non-Python children. These historical observations have **not been root-caused
  or fixed** by this closeout. They failed visibly and requested contained-tree
  termination; the request alone is not proof of cleanup in those failed runs.
- Latest Owner-reported normal, unavailable, and recovery runs all completed with
  zero remaining children. The prior intermittent observation is retained as a
  residual development-tool reliability risk, not erased by those passes. If it
  recurs, capture the existing privacy-safe diagnostics in a separate bounded task.
- Local Cargo build-helper Application Control rejection remains a historical
  local build limitation; GitHub Windows CI supplied build evidence. Successful
  manual execution is evidence for the reported environment/run only, not general
  Application Control compatibility or production trust.

## Public safety and deferred scope

The public-base clean-export boundary remains in force. No internal history,
private profiles, real Personal Marks, personal paths, local logs, private test
images, or caches are added by this closeout. Demo identities and strokes are
synthetic test data, not real user profiles. Existing dependency license provenance
and package audits are unchanged.

The existing public `c2patool` sample credential remains the reviewed, unchanged
base exception (Git blob `915030416772df5fdc1b13ffe4a14f960810beb0`), not a new secret
or a production signing authority. Whitespace status remains **KNOWN WHITESPACE
EXCEPTIONS = 3 / Other errors = 0**, not an unqualified base-to-head diff-check PASS.

| Item | Status / boundary |
| --- | --- |
| Two old Tk integration tests | EXCLUDED / PENDING; exact IDs and local coverage command remain in the historical ledger; 13 motion-domain tests retained |
| Full historical Python regression | Not claimed; only named bounded suites were run |
| Production signing / trust | PARKED |
| Actual Core Add / Verify from the new Desktop UI | NOT IMPLEMENTED IN F2C |
| Personal Mark C2PA embedding / readback | NOT IMPLEMENTED |
| Explorer / Batch integration | NOT IMPLEMENTED |
| v0.2 product icon / branding refresh | PENDING before release; current icons remain valid temporary product branding, not fixtures |
| Production release readiness | NOT APPROVED by this gate |

## Merge boundary and next work

F2C foundation merge-readiness is supported by the bounded CI checks, separately
reported native evidence, explicit G2 Owner approval, and the preserved limitations
above. Final readiness also requires the PR body to reflect this record, the
documentation-only publication to pass its existing checks, and a clean final
branch state. The Owner decides whether to merge PR #1; the agent does not merge.

Only after an Owner merge and synchronization to updated public `main`, propose a
new branch `codex/f3a-inspection-bridge` for a separately authorized F3A slice:

1. **F3A: read-only actual Inspection / Verify.** Shared Web UI to a Tauri-owned
   opaque target, Python application service, then existing `InspectionService`.
2. **F3B: actual Add.** Preflight, bound token, confirmation, existing
   `CreatorService`, staging, source recheck, output verification, commit, then
   success motion. The token binds the target fingerprint, Rights Intent snapshot,
   Personal Mark snapshot, and relevant protocol/schema versions; changed inputs
   invalidate confirmation.
3. Preserve all v0.1 Core semantics. Personal Mark C2PA embedding/readback is
   explicitly outside F3 and belongs to a separate later F4 PoC.

This is a proposed sequence only. No F3/F4 code, new branch, or implementation
authorization is created by this document.
