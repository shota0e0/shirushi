# F3A Rust SDK Limited Inspection parity: dependency-freeze checkpoint

## Current execution checkpoint: footprint accepted, tests pending

Owner explicitly approved the frozen240Windows-package/26build-script graph for
ISOLATED POC ONLY. No Production/Desktop/redistribution/license-completeness/
release/final-runtime-composition approval follows. Frozen lock/features remain
unchanged. Earlier STOP/dependency-freeze records below are historical.

Implemented in the isolated crate only: explicit offline SDK context, normalized
active-claim evidence, per-reference positive digest/signature/asset checks, exact
signed CAWG preset mapping, inner1/outer2 serializer/strict JSON parser, failure
envelopes with no partial inspection. All status/URI/debug data remain outside
formal output. Unknown states/statuses and unsupported inputs fail closed.
The read-only independent review identified and corrected legacy-status parity:
any nonempty Reader legacy validation_status/failure vector rejects success,
including signingCredential.untrusted, just as the existing oracle does.

Tests are separated into real_sdk_tests.rs (actual fixed fixture + actual SDK;
disposable in-memory image/header/C2PA mutations) and adapter_tests.rs (synthetic
evidence, not real parity proof). Source formatter/parser check passed. Actual
compilation/test results are PENDING until isolated GitHub-hosted Windows CI.

CI: new independent .github/workflows/f3a-rust-sdk-parity.yml, push trigger only
for codex/f3a-rust-sdk-parity and relevant PoC paths; manual entry guarded to that
same branch. Existing F2C workflow bytes/modes unchanged. Pinned1.97.1 MSVC toolchain,
canonical raw Git lock/checkout SHA verification, frozen240/26 footprint checks,
locked offline cargo tests after build-time fetch. No upload steps/artifacts.
No Owner-local executable build/run or policy changes.

CI execution checkpoints (not parity PASS): run36706641971 had zero jobs because
runner context was used at job-env scope; moving that unchanged target directory
to step-env fixed the isolated workflow only. Runs36706866527/36707805785 compiled
the frozen SDK and passed25/26 tests. Positive mapping stopped on an adapter
over-restriction: ingredient deltas had exactly informational
ingredient.unknownProvenance (success0/failure0; legacy0), while the oracle checks
ingredient failures only. SDK0.85.0 store.rs ingredient_checks and
validation_results.rs LogKind classify it as an informational no-manifest notice.
The narrow correction requires its exact category/code and signed active
c2pa.ingredient.v3 reference/digest; it cannot satisfy active evidence. Unknown
codes, wrong categories, ingredient failures and unsigned/cross-claim notices
still reject. Positive parity and two added regression cases remain pending CI.

SDK runtime source audit: PoC implements no subprocess or network calls; exact
SDK src process-command occurrences are in cfg(test) ephemeral_cert tests, not
the dependency-library Reader path. Rust-native crypto only; no HTTP backend.
This is call-path/feature evidence, not OS-level network/process monitoring.

## Task contract

Approved specification: Owner RUN, Rust SDK Limited Inspection Semantic Parity,
2026-09-30, attachment 1c9403c3-9954-454e-ac16-87dd22ae6eeb.

Primary objective: prove real exact SDK inspection of the immutable fixed PNG
with inner v1 / outer v2 unchanged, zero Python/c2patool processes, no required
runtime network. This is not Production integration or a redistribution audit.

In scope: separate branch/crate, exact lock/feature/provenance evidence, strict
adapter + real SDK/adapter tests, bounded isolated CI only if the preimplementation
gates permit it. Out of scope: Desktop/Tauri/IPC/Add/Personal Mark/TrustMark/ML/
Explorer implementation, parked runner mutation, release/transport/signing/policy
changes, main/merge/PR, binary/runtime uploads.

Completion criteria: all 15 Owner parity criteria must pass before PARITY_PROVEN.
At this checkpoint actual SDK inspection, implementation and all tests are NOT_RUN.
No pure adapter tests are being presented as real SDK validation.

Required sources: CURRENT_HANDOFF, src/inspection_metadata.py,
scripts/inspect_limited_fixture.py (read-only oracle, neither executed),
fixed fixture and Owner RUN. Existing source remains unchanged.

## Work instructions

Recommended model: GPT-6.1 Sol
Recommended reasoning effort: high
Selection reason: bounded SDK evidence-to-existing-contract parity slice.
Automatic escalation conditions: missing exact SDK APIs; inner/outer/CAWG
semantic change; fallback/network/fixture/production/policy requirement;
unexpected dependency footprint requiring Owner review.

Objective: the one semantic parity question above.
Required source files: Owner RUN; existing kernel/entry; fixed fixture; exact SDK source.
In-scope work: dependency freeze/evidence before functional implementation.
Out-of-scope work: all Production/runtime distribution actions above.
Completion criteria: frozen preimplementation evidence and explicit Owner footprint review.
Verification commands:
- cargo metadata --offline --locked --format-version 1 --manifest-path tools/f3a-rust-sdk-parity/Cargo.toml --filter-platform x86_64-pc-windows-msvc
- cargo tree --offline --locked --manifest-path tools/f3a-rust-sdk-parity/Cargo.toml --target x86_64-pc-windows-msvc
- Git/hash baseline comparison; immutable fixture size/SHA check.

## Result: PARITY_PARTIAL / STOP FOR OWNER REVIEW

No functional PoC has been implemented, built or run. The only Rust source is an
explicit dependency-freeze placeholder. PARITY_PROVEN/PASS is not claimed.
No missing SDK API was established; exact source investigation found candidate
positive-evidence APIs, not actual runtime proof.

The dependency review checkpoint is the substantial footprint: 304 registry
packages in the all-target lock (305 with root); Windows cargo tree 240 packages
including root, 26 build scripts. Metadata target filtering reports 267 packages,
which is not the same as the active target cargo tree. The evidence JSON retains
the target-tree inventory and feature lines separately.

This size is not an SDK defect and no numeric threshold was invented. Because
Owner section17 explicitly requires review for unexpected large/native/restricted
closures, obtain explicit acceptance of this newly resolved footprint before
functional implementation. Read-only fallback guard recommends this checkpoint.
No native/restricted dependency or production license approval is asserted.

## Freeze

Branch: codex/f3a-rust-sdk-parity, unchanged starting HEAD
2ab7f97f8b75ca9b491308e21d9398d6ccef140c.

Cargo.lock SHA-256:
a40f49659e9329689de6aa1bf581875a9e2b542e5d0faca6c33f4964fe03f3a8

Exact c2pa 0.85.0 package SHA-256:
cd6fa73bf92e8ae8980779f39ad4bf2a1e575eb97f95f2accf1e73f0602c5b0b
matches lock checksum. Packaged .cargo_vcs_info.json binds sdk to upstream commit
3f40cdd22b60bf955d531b0301604e3f257e0a19, matching official c2pa-v0.85.0 release.

SDK features: only rust_native_crypto, default-features=false. All HTTP backends,
remote manifest feature, OpenSSL, file_io, thumbnails, PDF/schema/diagnostics disabled.
Rust-native is a candidate implementation detail, not proven parity with the pinned
CLI/OpenSSL backend. It avoids bringing a vendored OpenSSL C build into this PoC.

Actual Windows tree: no OpenSSL/openssl-sys, reqwest/ureq/wstd, ring/aws-lc, cc or
sha2-asm. The registry metadata includes nonactive/cross-target entries such as
sha2-asm/cc/defmt; these are not asserted as Windows build inputs.
Active packages have no Cargo links declarations. Windows native API bindings
windows-sys0.61.2/windows-link0.2.1 are present; no actual binary/native composition
has been built or audited. Build-script read-only screening
found Rust compiler probes/config/codegen, no direct C/CMake/pkg-config/vcpkg/network
download invocation; this is a preliminary screen, not a binary composition audit.
All external target-tree packages declare license expressions; no full license/
notice completeness or distribution approval was performed.

SDK license expression: MIT OR Apache-2.0. Exact official root LICENSE-MIT and
LICENSE-APACHE were read. The downloaded SDK crate archive does not contain those
text files; upstream sources remain the evidence, not an assertion of packaged
license completeness. Do not turn this observation into a full license audit here.

## Candidate positive evidence mapping (SOURCE ONLY)

| Required fact | Exact 0.85.0 API/status | Remaining runtime proof |
| --- | --- | --- |
| Parse + active manifest | Reader::from_context().with_stream; active_manifest/active_label | Reader on exact fixed PNG |
| Signed reference set | Manifest::assertion_references, sourced from Claim::assertions | Nonempty exact refs to active claim |
| Every digest | activeManifest.success assertion.hashedURI.match, URL per ref | Require match for each ref, not any-one or absence of failure |
| Asset binding | active success assertion.dataHash.match | Must apply to PNG data hash |
| Signature mathematics | claimSignature.validated after CertificateInfo.validated | Positive exact active signature status |
| Trust separation | signingCredential.trusted/untrusted distinct statuses | Never promote test certificate; trustValidated=false |
| Signed CAWG | assertions + value + reference/digest membership | Exactly one applicable cawg.training-mining and exact two entries |

Reader creation or ValidationState alone must not mean parity success.
Unknown statuses/states must fail closed. CAWG inference / generative_training
must remain exact; ai_training is not a synonym. Inner1/outer2 and limited flags
remain unchanged and are not yet emitted by this PoC.

Exact upstream sources:
- https://github.com/contentauth/c2pa-rs/releases/tag/c2pa-v0.85.0
- https://github.com/contentauth/c2pa-rs/blob/3f40cdd22b60bf955d531b0301604e3f257e0a19/Cargo.toml
- https://github.com/contentauth/c2pa-rs/blob/3f40cdd22b60bf955d531b0301604e3f257e0a19/sdk/Cargo.toml
- https://github.com/contentauth/c2pa-rs/blob/3f40cdd22b60bf955d531b0301604e3f257e0a19/sdk/src/reader.rs
- https://github.com/contentauth/c2pa-rs/blob/3f40cdd22b60bf955d531b0301604e3f257e0a19/sdk/src/manifest.rs
- https://github.com/contentauth/c2pa-rs/blob/3f40cdd22b60bf955d531b0301604e3f257e0a19/sdk/src/claim.rs
- https://github.com/contentauth/c2pa-rs/blob/3f40cdd22b60bf955d531b0301604e3f257e0a19/sdk/src/validation_results.rs
- https://github.com/contentauth/c2pa-rs/blob/3f40cdd22b60bf955d531b0301604e3f257e0a19/sdk/src/settings/mod.rs
- https://github.com/contentauth/c2pa-rs/blob/3f40cdd22b60bf955d531b0301604e3f257e0a19/LICENSE-MIT
- https://github.com/contentauth/c2pa-rs/blob/3f40cdd22b60bf955d531b0301604e3f257e0a19/LICENSE-APACHE

## Verification / gaps

Fixture remains 319495 bytes, SHA-256
558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316.
No fixture changes/copies, Python or c2patool invocation, Rust build/test, CI,
artifact upload, commit/push/PR. Cargo generate-lockfile/metadata/tree performed
build-time registry acquisition only; no local Cargo build helper was executed.
Tool identities: rustc1.97.1 (8bab26f4f 2026-07-14);
cargo1.97.1 (c980f4866 2026-06-30). A +1.97.1 lookup was unavailable locally;
existing stable reports these versions. No toolchain install was performed.

Actual real-SDK validation / CAWG mapping / inner output / outer output /
real negatives / adapter negatives / runtime no-network evidence: all NOT_RUN.
Python/c2patool process counts 0 apply only to this investigation, not proof of
an unimplemented executable. Artifact upload0, CI0.

Next task only after Owner footprint acceptance: implement strict isolated SDK
evidence adapter and real fixture / safe disposable-copy negative tests plus
pure contract negatives, then isolated authoritative CI with artifact upload0.
A dedicated branch-restricted CI path may be proposed; none was changed/created.

Final repository audit: 254 preexisting tracked/nonignored files compared by
size/SHA-256; 253 unchanged, only CURRENT_HANDOFF updated; no removals. Six new
allowlisted files only (four isolated crate files, two development evidence files).
Historical handoff content is byte-preserved: reconstructing it by removing only
this checkpoint and restoring the branch/upstream header matches the prior
SHA-2561812cd20cade8aa3fdca24a29bed66f32fc401bf3454835caf228ad461880477.
Index/tracked diff remain empty. New-file no-index whitespace checks report no
errors (exit1 is expected for an added file); sensitive pattern scan found no
new personal paths/keys/tokens. No target output directory exists.

Production integration remains UNAPPROVED and premature before parity proof.
Distribution Compliance NOT READY; Full F3A NOT READY.
