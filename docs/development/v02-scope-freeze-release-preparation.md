# Shirushi v0.2 — Frozen Scope / Release Closure

2026-10-07 / Release Closure — approved Preview implementation, native validation pending.
Status: `V02_RELEASE_CLOSURE_PARTIAL` — public RC is NOT yet built.
Target remains `V02_RELEASE_CANDIDATE_READY_FOR_OWNER_REVIEW`; do not claim it
from Development CI. Owner now authorizes one isolated validation commit/push
and existing-workflow Preview build/NSIS/artifact validation. No tag/release,
purchase, installer execution or host mutation is authorized.
This current record supersedes the earlier scope proposal and Explorer task plan.
Local resumable truth is [CURRENT_HANDOFF.md](../CURRENT_HANDOFF.md).

Owner has approved existing public/test C2PA credentials for **v0.2 Preview**
and **Windows x64 / Tauri NSIS / per-user** installation. These decisions are
closed, not pending. Production Trust remains PARKED. Per-user installation
does not imply no-admin Microsoft prerequisites. The later artifact-validation
approval authorizes one isolated commit/push and CI; it does NOT authorize
installer execution, paid signing or publication.

## 1. Frozen product scope

Owner-approved and frozen; not new feature requests:

| Classification | Scope |
| --- | --- |
| IN V0.2 | Current Desktop UI; Handwritten / Typed Personal Mark; approved motion and Reduced Motion; UI-only visible mark (not baked into pixels); existing mark metadata |
| IN V0.2 | Single PNG/JPEG Add; Limited Inspection / Verify; fixed Rights Intent; local processing; source overwrite protection; separate output |
| IN V0.2 | Five existing locales: ja, en, zh-CN, zh-TW, ko; exact helper/manifest integrity; existing Rust helper/Desktop/supervisor architecture |
| IN V0.2 / DESKTOP CORE | Explorer right-click Add and Verify; one image; dispatch only outside explorer.exe; owned registration and safe removal |
| RELEASE ENGINEERING | Public build/package; installer/prerequisites; Windows executable trust; clean Windows evidence; final docs/notices/artifact |
| DEFER TO v0.3 | Batch; public Web version; optional UX expansion; deeper migration not needed for current product |
| PARKED / RESEARCH | Production C2PA Trust/author identity; Full Inspection/TrustMark; Reference Disruption and new research |
| KNOWN LIMITATION | Owner-local native UI NOT_RUN after launch access denial; cause unproven; Development non-blocking |
| KNOWN LIMITATION | Current Personal Mark selection is session-only; metadata may be removed by editing/re-saving/SNS; no universal survival guarantee |

Development Preview is a development asset, not a public Web product.
Prerequisite release work does not reopen the accepted VC floor or theoretical
minimum investigation. No new branding, UI redesign, batch or research lane.

Accepted Development:
`DEVELOPMENT_UI_BASELINE_ACCEPTED`,
`V02_RUST_PRODUCT_HELPER_EXTENSION_PASS`,
`V02_PRODUCT_FLOW_INTEGRATION_PASS`,
`V02_EXPLORER_CONTEXT_INTEGRATION_PASS`.
Latest Windows CI [37540938881](https://github.com/shota0e0/shirushi/actions/runs/37540938881),
validation commit `6d5f093d2ad5ab1f67f298278c78cceef5a757ff`, SUCCESS.
Helper103, Desktop lib47, supervisor39+39, helper integration17, dispatch6,
controller1, moved-package1 PASS; focused Explorer12 overlaps lib47.
Explorer/bridge native Python36 PASS including nonce-only registry ownership.
Web719 checks PASS. Existing skip/ignore predicates unchanged.
These are NOT public release-build, native human UI, install or uninstall proof.

Current Preview implementation regression (2026-10-07): nonnative Web733 checks
PASS; bounded pure Python115 total /111 PASS /4 existing skips (2.747s), including
12 Preview preparation tests, README conversion and public-document contracts.
No skip policy change.
Native formatter launch remains blocked by Application Control4551; no bypass
or alternate native execution. Native Preview build/probe/NSIS evidence is absent.

## 2. Smallest accurate v0.2 promise / draft release notes

Shirushi v0.2 is a Windows x64 local image tool. It records the fixed intention
「AI学習・生成利用を希望しない」 as C2PA/CAWG metadata in a separate PNG/JPEG
output and reads the supported embedded information through Limited Inspection.
Handwritten/Typed Personal Mark is displayed in the UI and represented in
metadata; visible signature pixels, glow and motion are not burned into output.
Five UI locales and single-image Explorer Add/Verify entry are in the product.

Verify means `LIMITED_INSPECTION / INCOMPLETE`; C2PA and CAWG `INSPECTED`,
TrustMark `NOT_CHECKED`. It does not prove copyright, author identity,
AI-platform recognition/compliance, complete provenance or Production Trust.
No intent found means neither permission nor refusal.
「しるしを見る」 replays presentation; 「しるしを確認する」 inspects the image.
Existing Preview signatures are public test credentials, not a trusted identity.

### Public-facing usage draft — not current install availability

1. Install the eventual exact approved Windows x64 candidate using its approved
   installer. No v0.2 public installer/download is available from this preparation.
2. Choose a local PNG/JPEG. Add uses the current mark; first-time/unconfigured
   entry opens Handwritten setup and waits for explicit save.
3. Add publishes `stem_rights.ext` beside the source only on validated success.
   Source and existing output are not overwritten; collisions stop.
4. Verify the separate output or an existing image. Unmarked images must not
   display a false Rights Intent. Limited/incomplete results remain explicit.
5. After owned registration is installed, use Explorer → Shirushi →
   しるしを付ける / しるしを確認する. This is a classic static menu; Windows 11
   may require its extended/classic menu. Actual UI placement is NOT_RUN.
6. Uninstall must remove only the recorded Shirushi-owned exact menu trees.
   It must not remove images, unrelated handlers, shared VC Runtime or WebView2.

No user should disable SAC/SmartScreen/policy, unblock files, use an alternate
launcher or install self-signed roots to make this candidate run.
Normal documented UAC for an approved Microsoft prerequisite is a distinct,
consent-based action, not an execution-policy bypass.

## 3. Five release workstreams / true public blockers

Only these product-relevant items are on the public-release path.

| Stream | Current concrete evidence / gap | Minimum closure | Owner dependency |
| --- | --- | --- | --- |
| A. Public build / package | Explicit preview-release source path, compiled manifest digest and native-only bridge now implemented. Latest actual binary is still debug Development; release-profile build/test remains unproven | Native release helper→manifest→Desktop and Add/Verify/Explorer/package checks on existing Windows CI | Approve exact isolated commit/push and existing workflow augmentation; channel decision is already approved |
| B. Installer / prerequisites | Per-user NSIS overlay/preparation and owned hooks implemented, not compiled. Base bundle.active=false. Ready-only VC floor and native WebView2 probe; no vendor execution/download. Frozen assets remain CANDIDATE | Compile/audit actual installer, validate owned install/remove; close missing-prerequisite UX through an explicitly accepted manual path or separately approved vendor integration | Actual install tests; acceptance of ready-only/manual prerequisite limitation or vendor redistribution/execution authority; format decision already approved |
| C. Windows trust / launch | Owner launch was access denied; cause remains UNPROVEN. No Authenticode signing path/identity accepted | Agree trust policy and exact signing identity/process, then normal candidate install/launch on supported Windows without policy workarounds; document remaining SmartScreen warnings | Publisher identity/service choice; any account/paid/sign action separately approved |
| D. Clean Windows validation | No clean Windows 11 x64 target supplied/verified. Windows Server CI is supplementary, not consumer equivalence | One available clean target/snapshot; exact candidate install→first launch→Add/Verify→Explorer→uninstall, source identity and prerequisite failure/reboot handling | Owner supplies/authorizes environment and human UI smoke |
| E. Docs / notices / final artifact | Existing root docs describe v0.1 Python/TrustMark; accepted Development artifact contains only3 runtime files. Final native composition/notice and vendor redistribution are not audited | v0.2-specific docs; final shipped-component/license/notice inventory incl embedded native components and Unicode; exact immutable artifact/source/locks/hash; no private material; Owner final release decision | Input/notice decisions and final public approval |

Development history remains PASS. A public-release blocker is not a retroactive
Development failure. Full F3A research is not required, but applicable notice/
redistribution obligations for the actual v0.2 artifact cannot be waived.

## 4. Public-build changes — implemented, native validation pending

Do not merely delete `debug_assertions` or reuse the v0.1 PyInstaller assembler.

- Preserve flat `shirushi-desktop.exe / shirushi-inspection-helper.exe /
  inspection-helper.manifest.json`. Reuse current assembler identity/no-clobber/
  stage/audit logic; add only approved release documents/assets explicitly.
- Explicit `preview-release` opt-in enables the approved public Preview channel;
  normal release without it still cannot sign Add. Public test private keys are
  not Production authority. Preview retains `developmentSigning=true` and
  `trustValidated=false`; no schema/result upgrade to COMPLETE/VERIFIED.
- Reuse the existing pinned package verification implementation behind a public
  constructor with the independently compiled manifest digest. Restrict
  canary mismatch injection/native observers to their existing test boundaries.
  No PATH, runtime env override or frontend-selected helper root.
- Make normal PNG/JPEG read, Add, Limited Inspection and Explorer dispatch
  available in that channel; retain supervisor cleanup, cancellation,
  bounded shutdown, request identity, source and output protections.
- Keep public startup independent of repository .venv/scripts. Product Flow
  already uses native operations and session mark. Remove only obsolete bridge
  startup dependency for the selected public channel, not product behavior.
- Embedded Tauri assets/custom-protocol and build identity must be validated in
  release profile. Do not use manual-canary (explicitly debug-only).
- Test release-profile PNG/JPEG Add→inspection, unmarked absence, both mark
  metadata forms/pixels unchanged, Explorer intents/duplicate/input rejection,
  lifecycle, five locales and raw package move/tamper on existing Windows CI.
  No commit/push or CI start authorized until Owner reviews those actual changes.

Implemented files are enumerated in the current handoff. Preview compiles out
the repository/Python host module, negotiates a strict
`NATIVE_PREVIEW_SESSION_ONLY` capability response and uses the existing browser
adapter for session-only Personal Mark. It does not invent persistent identity.
The existing package verifier is reused; arbitrary canary roots/digest overrides
remain debug-only. Windows x64 MSVC and separation from manual-canary are enforced.

## 5. Installer path — approved implementation, not a generated installer

Approved baseline: existing Tauri NSIS, per-user Windows x64, standard
user-consent UI, with a narrow prerequisite/Explorer hook; not a template rewrite.
Base `bundle.active=false` and static Preview overlay false remain unchanged.
Only the explicit audited preparation tool emits bundle.active=true in its
owned temporary generated config, for a later authorized bundle invocation.
Tauri built-in mutable WebView2 download/standard offlineInstaller must not
replace the accepted frozen offline trust boundary; configure skip only with
an actually functioning controlled prerequisite check.

`scripts/prepare_v02_preview.py` reuses the existing three-file assembler and
audit, checks the supplied build-time manifest digest against actual helper
bytes, and stages bounded documents plus hooks/config before no-clobber publish.
It generates no installer and starts no subprocess. CLI:

```text
python -B -m scripts.prepare_v02_preview --desktop <absolute-built-Desktop>
  --helper <absolute-built-helper> --output-root <absolute-new-owned-Temp-root>
  --compiled-manifest-sha256 <exact-build-time-digest>
```

The current hook is deliberately **ready-only**: VC registry x64 Installed=1
with numeric version>=14.51.36247.0 before copying; after copying, exact Desktop
`--shirushi-prerequisite-check` exits before UI/helper startup and queries the
statically linked x64 WebView2 loader API. Unknown/nonzero aborts before Explorer
registration/ordinary launch. Probe rejects WEBVIEW2_* environment overrides,
any WebView2 policy root, unstable/noncanonical version or API error. Success is
**X64_API_AVAILABILITY_ONLY**, not frozen Runtime identity, actual browser/UI or
all enterprise-policy compatibility. No vendor installer/download/UAC/reboot.
Missing prerequisites require separate approved Microsoft installation and a
fresh installer session. This does not yet close fresh-machine prerequisite UX.

VC deployment floor is exactly x64 `14.51.36247.0`; build compatibility minimum
remains UNPROVEN. Existing VC≥floor and compatible WebView2 READY → SKIP.
Missing/outdated/unknown are distinct. No installer exit0→READY shortcut.
Normal Microsoft UAC must be explicit; do not assume the fixture harness owns
elevated vendor/service work. Do not add a second runner or hide this boundary.
For required action, retain exact frozen identity and accepted signature policy.
Fresh numeric exit/reboot contracts and adequate timeout must be evidenced for
the interface actually used; provisional VC900s/WebView2600s are not guarantees.
No reboot, fallback, retry, warning-only continuation or app launch on failure.

Frozen VC:18731856 bytes, SHA843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c.
Frozen WebView2:212272848 bytes, SHAf6df8e4bc857786ff641cd01da1449169eaf8236c936ced485ea61685ba4da40.
Candidates stay CANDIDATE; this document does not approve public redistribution.

Registration uses current per-user owned three PNG/JPG/JPEG trees, fixed exact
Desktop/SHA descriptor, no global associations, no processing in Explorer.
An installed NSIS seam needs the same ownership contract without requiring a
user-installed Python runtime. Keep the original descriptor for removal; absent
is safe, foreign/mismatched state stops. Do not overwrite foreign entries.
Uninstall must not erase user images/profile or shared vendor runtimes.

Generated native NSIS derives the exact existing Explorer descriptor: counts,
REG_SZ types, values/path/compiled Desktop SHA and link checks; exclusive missing
key creation, exact-existing no-op, checked writes and all-roots postcondition.
Unregister rechecks each leaf, removes exact values and only empty keys, checks
errors and final absence; no wildcard/recursive removal. Shared prerequisites
and user profile cleanup are excluded. Source conformance reviewed PASS;
actual NSIS compile/registration/unregister is still UNPROVEN.

## 6. Windows trust decision (official guidance checked 2026-10-07)

[Microsoft SmartScreen guidance](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation):
signing identifies a publisher and supports reputation continuity; a new signed
binary may still warn. Unsigned new files do not inherit publisher reputation;
enterprise policy may disallow continuation. EV is not a guaranteed bypass.

[Smart App Control overview](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/overview)
and [signing guidance](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control):
unknown unsigned code can be blocked; trusted code signing is the supported
developer path. This does not identify the cause of this Owner host's refusal.

Recommendation for normal public Windows 11 support: trusted Authenticode for
Desktop, helper and installer; do not promise warning-free behavior.
An unsigned artifact can remain a private Development candidate, but cannot yet
meet this release's normal-user launch promise. An unsigned public exception
would need explicit narrowed support and normal clean-target evidence, not
instructions to turn security off. Signing is therefore a proposed requirement
for this release promise, NOT a purchased/selected certificate or universal law.
C2PA Preview image signing and Windows executable Authenticode are separate.
No provider application, certificate import, purchase, sign action or policy
change occurred; verify a selected path before producing final signed hashes.

## 7. Minimal clean Windows matrix — NOT_RUN

Target: clean Windows 11 x64 consumer edition, current supported build recorded;
ordinary standard user, existing security controls unchanged, snapshot/revert or
dedicated safe test target; no source repository/Python/developer toolchain.
Server2025 CI is supplementary only. Environment is currently NOT_PROVIDED.
Do not create VM/enable Hyper-V or buy cloud capacity automatically.

| Case | Required bounded evidence |
| --- | --- |
| Install / first launch | Exact installer/hash; native UI shown normally; no missing DLL/sidecar; trust/warning behavior recorded without bypass |
| Prerequisites | Ready→skip; missing/outdated→accepted action or clear stop; decline/failure/unknown→no Shirushi launch; approved reboot-required→stop |
| PNG + JPEG | Add via UI; separate output; source SHA unchanged; output inspection returns fixed intent; unmarked remains absent |
| Personal Mark / locales | Handwritten first-time setup, Typed selection, visible pixels unchanged, current UI/motion/reduced-motion and five existing locales |
| Explorer | Single PNG/JPEG Add and Verify incl Japanese/spaces path; existing flow, no double execution; classic menu placement recorded |
| Remove / uninstall | Only exact owned entries removed; repeat/missing safe; unrelated handler/user images/shared runtimes unchanged |

One clean target and exact final candidate are sufficient for the initial
release decision; do not multiply platforms or research lanes. A release-byte
change invalidates only affected evidence and requires the corresponding retest.

## 8. Coherent commit closure proposal — stage/commit/push NOT_RUN

Original branch `codex/f3a-rust-sdk-parity`,
HEAD `182d4f079b0a9bdf0765be6f3dcbd7c6ac64901e`; index empty, raw SHA
`714bdf71a238c646543cead2700838a57fd37d456d982eddff8edb80863b0767`.
Configured origin/same branch, tracking ref unavailable, ahead/behind UNPROVEN;
no fetch. Starting31 tracked dirty /44 concrete untracked,341 files.
Validation branch `codex/limited-inspection-native-validation`,
HEAD `6d5f093d2ad5ab1f67f298278c78cceef5a757ff`, clean.

Minimum current closure: TWO commits after Owner approval, not dozens.

1. **Product implementation**:45 exact currently uncommitted product/test paths
   below. All45 raw SHA match the validated isolated working files at6d5f093.
   Includes both canonical Cargo locks and public-test-credentials NOTICE.
   Those45 were validated Development bytes at the previous checkpoint.
   Current Preview changes overlap that slice; byte equality with6d5f093 is
   therefore NOT claimed now. Include only the current handoff's exact additional
   Preview files/deltas after native validation; no branch-wide merge.
2. **Public docs / release metadata**: README, DISCLAIMER, PRIVACY,
   THIRD_PARTY_NOTICES, this closure record, public Explorer contract and
   web/README after public-content review. Final artifact hashes/notices/release
   notes belong here only when their actual artifacts exist.

Exact product allowlist (not a glob or staging instruction):

```text
desktop/build.rs
desktop/capabilities/main-window.json
desktop/Cargo.lock
desktop/Cargo.toml
desktop/src/asset_stage.rs
desktop/src/explorer_entry.rs
desktop/src/inspection_protocol.rs
desktop/src/inspection_supervisor.rs
desktop/src/lib.rs
desktop/src/limited_inspection.rs
desktop/src/product_image.rs
desktop/src/product_operation.rs
scripts/explorer_context_v02.py
tests/test_explorer_context_v02.py
tools/f3a-rust-sdk-parity/Cargo.lock
tools/f3a-rust-sdk-parity/Cargo.toml
tools/f3a-rust-sdk-parity/src/development-signing/es256.pem
tools/f3a-rust-sdk-parity/src/development-signing/es256.pub
tools/f3a-rust-sdk-parity/src/development-signing/NOTICE.txt
tools/f3a-rust-sdk-parity/src/helper_protocol.rs
tools/f3a-rust-sdk-parity/src/lib.rs
tools/f3a-rust-sdk-parity/src/product_contract.rs
tools/f3a-rust-sdk-parity/src/product.rs
tools/f3a-rust-sdk-parity/tests/product_helper.rs
web/adapters/desktop-adapter.js
web/adapters/dev-preview-adapter.js
web/bootstrap.js
web/contracts.js
web/desktop.js
web/desktop/inspection-contract.js
web/desktop/inspection.verify.mjs
web/desktop/limited-inspection.verify.mjs
web/desktop/product-flow.js
web/desktop/product-flow.verify.mjs
web/desktop/transport.js
web/dev-preview.js
web/dev/fixtures.js
web/dev/preview-controls.js
web/i18n.js
web/mark.js
web/motion.js
web/shell.js
web/styles.css
web/ui-flow.verify.mjs
web/verify.mjs
```

Exclude: temporary validation workflow and workflow-only historical commits;
local CURRENT_HANDOFF (private paths/history); untracked legacy F3A evidence/
schemas/investigations; old Python inspection_application/acquisition scripts/
tests; unused DotGothic16 font/OFL; unrelated f3a-rust-sdk-parity doc changes;
vendor EXEs, build outputs, logs/cache and CI artifacts. Preserve all, do not
delete. No bulk `git add`, branch-wide merge/cherry-pick or index reset.
No secret Production material is authorized; es256 fixtures are PUBLIC TEST
CREDENTIALS only, not publisher/author credentials.

## 9. Docs / notices / final artifact preparation

Root README, DISCLAIMER, PRIVACY and THIRD_PARTY_NOTICES gain explicitly scoped
v0.2 preparation sections; v0.1 published behavior remains labeled legacy.
No v0.2 download/install availability or complete validation claim.
Remove security-bypass instructions as a release-doc defect, not UI redesign.
Final notices must reconcile Desktop/Tauri, Rust helper/c2pa0.85.0, embedded native
crypto and other linked/generated code, Unicode16 text, applicable MPL source
availability and actually bundled Microsoft assets. Do not relabel the v0.1
Python/c2patool/TrustMark inventory as v0.2 proof; do not re-investigate excluded
legacy components. Existing MPL exact-source evidence stays unchanged.

Final public package has not been generated. Only identifiable current input is
Development artifact `shirushi-v02-development-package-37540938881`:
three runtime files, GitHub ZIP SHA
`93d0bd58d37d63de3359711e082ccc0b5397a7d6996d6083eebe0aa49a8ce6c3`.
Desktop SHA`c0b6e9beacc1789131ee26d16807309c755306954e647245d3c633041e4eb77f`,
helper SHA`faa46c51d693249fe6574097357e1c361f24957a8420b24209db1d8a021fafe7`,
manifest SHA`2aa586ce69705bdc8296a5e515e0a9bb324bd4fd033f0d8d94d44c39bab7411e`.
Do not label these final release hashes. Any signing/installer changes create
new bytes; final files/source revision/locks/notices/signatures must be inventoried.

## 10. Shortest release sequence / current decision point

1. Freeze scope and Preview public-test/per-user NSIS decisions (DONE).
   Trusted Windows signing is separate and not selected/purchased.
2. Public build/installer source implementation (prepared, pure regressions PASS);
   review exact coherent isolated commit and existing-workflow CI diff.
3. Owner-approved commit/push → existing Windows release-profile build/tests/
   package/audit; candidate hashes and actual component notices.
4. Sign through selected approved process and run one clean Windows install/
   UI/Explorer/uninstall validation with exact candidate, no policy bypass.
5. Synchronize docs/notices/release notes with that final artifact and residual
   limitations; Owner approves public release/tag/publication separately.

Current blockers are short and concrete: native release build+working installer,
missing-prerequisite handling acceptance/closure, trusted ordinary launch+clean
target, artifact-bound applicable notices. Public channel/format choices are no
longer blockers. WorkstreamsA–E above own them; no micro-Gates.
`V02_RELEASE_CANDIDATE_READY_FOR_OWNER_REVIEW` is NOT claimed now.
Production C2PA Trust remains out of scope; release code signing is separate.
Recommended GPT-6.1 Sol / High; Astra escalationNO. Skill-mandated pinned guard
review preserves approval and public-authority boundaries.

## 11. Existing-CI artifact validation — APPROVED / preparation in progress

Owner approved one coherent isolated validation commit/push plus necessary
existing-workflow changes; original feature branch is not pushed/committed.
Preserve existing debug/native regression and four local skips. No new framework.
The existing job hard-pins Desktop Cargo.lock in three checks (currently
f704b02621c9325d32735d35204438ae91a54ac3662376d709b9a99eb47977e7).
Those three expected hashes must be updated together to the reviewed canonical
lock0ff501939a8e7a77be3e261c4f10f64b3c85a1f40362958b186e42b9a8491a94;
do not remove/weaken the checks or change other diagnostic jobs. Helper lock
and immutable fixture remain unchanged.

1. Build helper release with `preview-release`, Rust1.88/x64, canonical lock.
2. Generate canonical manifest from that exact helper; compile its SHA through
   `SHIRUSHI_PREVIEW_INSPECTION_MANIFEST_SHA256` (build-only, no runtime override).
3. Pin Tauri CLI2.11.3 (tagged source MSRV1.77.2, same Tauri2.11 family;
   actual locked build compatibility still to validate). Build Desktop with Cargo
   using static `desktop/tauri.preview.conf.json` through build-time `TAURI_CONFIG`,
   explicit Preview feature and locked dependencies, without invoking bundling.
   Run release-profile contracts/round-trips.
4. Prepare actual binaries using the new tool; independently audit/hash inputs,
   manifest, runtime package and notice/config inventory.
5. Use `cargo tauri bundle` on **already-built** Desktop with generated config,
   same feature/target. Do not rebuild Desktop after freezing its registration
   hash. Verify Desktop bytes before/after bundling; verify payload placement.
6. Identify/download candidate NSIS artifact and hashes. Build/NSIS generation
   is not installer execution, clean Windows or normal-launch proof. No install,
   vendor launch, registry action, signing, tag or release in that CI proposal.

Actual final notice inventory/license payload (including applicable MIT/MPL/
Unicode/native dependencies) remains to be reconciled against the native build.
The current four staged public docs alone are not a final compliance bundle.
CLI2.12.1 proposal was rejected before CI: its tagged source requires Rust1.90,
not the accepted Rust1.88. No toolchain/policy bypass or version-check suppression.
