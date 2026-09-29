# F2C verification and clean public export

## Current status: 2026-09-29 closeout

**F2C Tauri/Python bridge foundation: PASS. G2: OWNER APPROVED.**
See the [current closeout record](f2c-closeout.md) for separately attributed
Owner manual evidence, CI/artifact provenance, unresolved observations, and
the foundation-only merge boundary. The earlier pending/not-approved statements
below describe their historical gates; this dated record supersedes them.
Historical F2C.4/F2C.5 ledgers are retained unchanged. Owner merge is a separate
decision; this is not v0.2 release readiness.

## F2C.6 development-canary boundary

The materialization ledger below records the historical F2C.4 review, not the
current publication or CI state. F2C.5 created four exact commits on
`codex/f2c-public-export`, opened [PR #1](https://github.com/shota0e0/shirushi/pull/1),
and completed [Windows verification run 36537296595](https://github.com/shota0e0/shirushi/actions/runs/36537296595).
The PR remains unmerged; that CI success did not approve G2.

F2C.6 adds only an unsigned **DEVELOPMENT CANARY** artifact for Owner manual
review. It is not a release, installer, production package, or signed build.
The explicit debug-only `manual-canary` feature uses the extracted package's
fixed bridge resources and synthetic demo profile. The default repository-layout
build and its checks are retained; no actual Add/Verify/C2PA capability is added.
See the [canary launch and failure checklist](../../scripts/canary/README.md).

The default and canary builds/tests, Web/Python checks, source/resource audits,
artifact manifest/hash/privacy checks, and upload must succeed independently.
Artifact availability is not evidence of an actual Desktop window, local Python
sidecar, crash recovery, shutdown/process cleanup, or Application Control
compatibility. All native manual checklist items remain **PENDING**, and G2
remains **NOT READY / NOT APPROVED** until separately reviewed.
Do not disable, bypass, or modify a security policy. A policy rejection is
**LOCAL POLICY BLOCKED**, not automatically a product failure.

Correction to the historical whitespace entries below: the Owner accepted
exactly three frozen EOF blank lines in `web/personal-mark-v2/README.md:33`,
`embedding.js:9`, and `errors.js:46`. The correct result is
**KNOWN WHITESPACE EXCEPTIONS = 3 / Other errors = 0**; strict CRLF-aware
`diff --check` returned exit 2, not PASS. F2C.6 does not rewrite these bytes.
The two legacy Tk integration tests remain **EXCLUDED / PENDING**; the 13
motion-domain tests remain included. The bounded CI is not full Python regression.

## Historical F2C.4 scope and status

This is a source-only, local-development foundation, not an integrated product
release. The approved clean export starts from the public `shota0e0/shirushi`
`main` commit `2884b6c4da1339778ae4678ddae159a4100fa130`.
Only public repository history is retained. The local preparation branch is
`codex/f2c-public-export`; no new commit or publication has been performed.

| Evidence boundary | Status |
| --- | --- |
| F2C.1 implementation | ENVIRONMENT BLOCKED |
| Code verification | PARTIAL PASS; new-tree results recorded below |
| Hosted Windows CI | NOT RUN |
| Native manual verification | PENDING |
| G2 | NOT READY / NOT APPROVED |

Primary objective: materialize the approved allowlist, verify source and local
nonnative behavior, audit the candidate bytes, then stop for Public Export Owner
Review. No staging, commit, push, PR, workflow dispatch, merge, release, actual
Add/Verify, C2PA integration or security-policy workaround is part of this task.
The development machine's Windows Application Control block is respected:
Rust/Tauri builds, tests and window launches are not retried locally.

The existing public tree contains 103 files. Preserve 101 exactly; extend only
`.gitignore` with Desktop/test-runtime exclusions and `THIRD_PARTY_NOTICES.md`
with Unicode 16 attribution. Add 95 exact paths. The result is 198 deliverable
files and 97 changed paths, not a transfer of an entire development checkout.

## Reproducible local checks

Use CPython 3.12.10 x64 and Node 24.18.0 x64. The bounded Python suites use
the standard library and the committed Unicode/JSON fixtures; no pip package,
private baseline, model cache or credential provisioning is needed.
Create a fresh `.venv-py312` with `python -m venv --without-pip .venv-py312`
for the bridge tests' fixed repository layout. It is local, ignored test runtime,
not an exported or distributable Python environment. Never copy an existing
private environment or profile. Commands below run from the repository root.

```powershell
.venv-py312/Scripts/python.exe -B -m unittest -q tests.test_desktop_bridge tests.test_personal_mark tests.test_personal_mark_motion tests.test_personal_mark_v2 tests.test_personal_mark_v2_store tests.test_personal_mark_snapshot tests.test_personal_mark_unicode16 tests.test_personal_mark_v2_cross_language
.venv-py312/Scripts/python.exe -B scripts/verify_f2c1_ci.py
.venv-py312/Scripts/python.exe -B -m unittest -q tests.test_f2c1_ci_verification tests.test_public_fixture_governance
node web/verify.mjs
node web/desktop/verify.mjs
node web/personal-mark-v2/verify.mjs
node web/personal-mark-v2/unicode16.verify.mjs
node web-canary/verify.mjs
git diff --check
```

These are the public-source subset, not full legacy Python regression.
Earlier development-tree results are not substituted for this candidate's
results. Native-symlink privilege skips, if encountered, must be reported as skips.

## Legacy-local coverage ledger

The public motion module retains the 13 unchanged test methods in
`PersonalMarkMotionTimingTests`, `PersonalMarkMotionControllerTests` and
`PersonalMarkMotionTriggerTests`. Validation, replay/motion, v1 compatibility and
F2B v2 regressions remain included.

Two static Tk integration tests require an unpublished GUI integration and
cannot describe the preserved public v0.1 GUI. They are absent from this public
derivative only; their original module and GUI remain unchanged in the separate
development tree.

| Test ID | Public CI status |
| --- | --- |
| `tests.test_personal_mark_motion.CreatorWindowMotionIntegrationTests.test_motion_trigger_is_after_success_message_and_success_branch` | EXCLUDED / PENDING |
| `tests.test_personal_mark_motion.CreatorWindowMotionIntegrationTests.test_overlay_reuses_existing_rights_semantics_and_never_writes_image` | EXCLUDED / PENDING |

They remain legacy-local coverage. From the separate development tree's root,
the verification command is:

```powershell
python -m unittest tests.test_personal_mark_motion.CreatorWindowMotionIntegrationTests -v
```

Do not run that command against this public derivative, call exclusion PASS, or
treat historical development-tree results as execution against this export.
No public GUI source is replaced to satisfy these excluded checks.

## Hosted verification design (not activated)

The dedicated workflow uses GitHub-hosted `windows-2025`, a 60-minute timeout,
`contents: read`, immutable official action pins, no persisted checkout
credentials, no additional secrets, no caches and no uploaded artifacts.
Rust is pinned to `1.97.1-x86_64-pc-windows-msvc`; Node and Python versions are
asserted. `Cargo.lock` and exact direct dependency versions are retained.
Only the hosted runner installs the pinned Rust toolchain. It fetches locked
dependencies, then runs locked/offline Rust tests and a debug Tauri application
build. This is not Tauri CLI packaging, a release bundle or an installer.
The repository-layout sidecar source is audited; no distributable Python runtime
is bundled or proven.

Activation method B is prepared: `pull_request` to `main` for opened,
synchronize and reopened events, with a same-repository-head job gate, plus
`workflow_dispatch`. No `pull_request_target`, privileged fork execution or
user-provided shell expression is introduced. It avoids an initial workflow-only
commit to main. Manual dispatch requires the workflow on the default branch;
that requirement is not worked around.
See the [official trigger reference](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).
Publication and PR creation require a later explicit Owner authorization.

The summary records event, tested SHA (PR merge SHA for PR runs), head/base SHA,
run URL and each step outcome. Failed/skipped steps never become PASS.
Before build the source audit checks 29 Web assets, 10 sidecar resources, 52
source/resource hashes, static import closure, fixed interpreter/entry layout,
two-command ACL and bundle exclusions. After the hosted build, `--staged`
compares generated Web assets byte-for-byte; that flag refers to build assets,
not Git staging. Local negative tests use synthetic staging only.

Required CI gate: Rust tests, debug Tauri build, Desktop Web, Python bridge,
Personal Mark v2, shared Web and resource audit. Full Python regression is not
run by this bounded workflow. The two legacy Tk tests remain EXCLUDED / PENDING.

## Native/manual gate — all PENDING

- Desktop window launch and real shared UI.
- Actual Tauri-started local Python sidecar and healthy bridge.
- Personal Mark read through the actual Desktop window.
- Unavailable/crashed sidecar safe failure and crash behavior.
- Graceful shutdown and process-tree cleanup.
- Actual local Windows Application Control compatibility.

Python subprocess protocol tests and CI native unit tests do not replace this
manual evidence. G2 requires final Rust tests PASS, final Tauri application
build PASS, window launch, bridge healthy, mark read, unavailable safe failure,
shutdown/cleanup confirmation and Owner review.

## Privacy, assets and licensing

The export contains no private profiles, actual user Personal Mark records,
private baseline images, home paths, execution logs, research notes, model
caches, copied virtual environments or Cargo outputs. Test-created scratch files
and the fresh local environment are ignored runtime, not publication inputs.

Fixed Niki / Mori names and synthetic strokes are demonstration/test fixtures,
not real-user identities or profiles. Preview Verify snapshots are simulated;
no actual file C2PA readback is asserted. Browser-local records are not exported.
`web-canary/assets/demo-art.svg` is code-native synthetic demo artwork.

The governance test enumerates three product branding assets, one documentation
image, one demo SVG and 19 image test fixtures using exact paths, with no
directory wildcard. See [branding classification](../../assets/branding/README.md).
The Windows guide image is preserved from the public base, not copied from a
private development image. Existing fixture provenance, MIT attribution for
JPEG examples, project LICENSE and third-party notices remain intact.
Unicode 16 derived tables and test data retain all three Unicode-3.0 license
copies and the added attribution. No font binary is introduced.

Known exception: the existing public
`tools/c2patool-0.26.60/c2patool/c2patool.exe` contains the official sample
signing credential. It must remain identical to public-base Git blob
`915030416772df5fdc1b13ffe4a14f960810beb0`, SHA-256
`90cbcebe30250f8e8c53416d32ed86065dc04a23be86e4a2337f5cd1badfa0b7`.
This is a pre-existing public test credential, not newly introduced secret
material and never production signing authority. See the
[upstream sample signer](https://github.com/contentauth/c2pa-rs/blob/c2patool-v0.26.60/cli/src/signer.rs).
The audit must report this exception rather than claiming zero private-key
patterns. No actual credential value is reproduced here.

Commit identity is not configured by this task. No Owner-approved GitHub
noreply address has been provided; do not guess one or inherit a development
identity for a future commit. Existing public-base history is retained unchanged.

## Exact final commit preview / export allowlist

This is a preview, not staging or commits. The four groups partition all 97
paths. All unlisted development files are excluded. Base files other than the
two explicitly marked updates must not differ.

The foundation's Web README is deferred to commit 4 so its Desktop/evidence
links are introduced only after their targets exist. Product code does not
import later groups. Each test command below is for the applicable source
slice; current execution evidence covers the final assembled tree. Isolated
intermediate commit checkouts have not been created or tested. No claim of
native buildability is made without the future hosted build.

### 1. foundation

Dependency: public base only. Unrelated changes included: NO.
Public safety: exact approved paths; final audit results below. Native evidence
remains PENDING where applicable. Paths (27):

```text
web-canary/README.md
web-canary/adapters/browser-adapter.js
web-canary/app.js
web-canary/assets/demo-art.svg
web-canary/capsule.css
web-canary/editorial.css
web-canary/i18n.js
web-canary/index.html
web-canary/styles.css
web-canary/verify.mjs
web/adapters/browser-foundation-adapter.js
web/adapters/dev-preview-adapter.js
web/bootstrap.js
web/contracts.js
web/dev-preview.html
web/dev-preview.js
web/dev/fixtures.js
web/dev/preview-controls.js
web/generation-guard.js
web/i18n.js
web/index.html
web/index.js
web/mark.js
web/motion.js
web/shell.js
web/styles.css
web/verify.mjs
```

```powershell
node web/verify.mjs
node web-canary/verify.mjs
```

### 2. personal-mark-v2

Dependency: public base plus foundation (shared Web import-graph checks). Unrelated changes included: NO.
Public safety: exact approved paths; final audit results below. Native evidence
remains PENDING where applicable. Paths (40):

```text
scripts/generate_personal_mark_unicode16.py
src/data/Unicode-LICENSE.txt
src/data/personal_mark_unicode16.json
src/gui/i18n.py
src/personal_mark.py
src/personal_mark_motion.py
src/personal_mark_replay.py
src/personal_mark_snapshot.py
src/personal_mark_store.py
src/personal_mark_unicode16.py
src/personal_mark_v2.py
src/personal_mark_v2_store.py
tests/fixtures/Unicode-LICENSE.txt
tests/fixtures/personal_mark_unicode16_normalization.json
tests/fixtures/personal_mark_v2_shared.json
tests/test_personal_mark.py
tests/test_personal_mark_motion.py
tests/test_personal_mark_snapshot.py
tests/test_personal_mark_unicode16.py
tests/test_personal_mark_v2.py
tests/test_personal_mark_v2_cross_language.py
tests/test_personal_mark_v2_store.py
web/personal-mark-v2/README.md
web/personal-mark-v2/Unicode-LICENSE.txt
web/personal-mark-v2/contract.js
web/personal-mark-v2/cross-language.mjs
web/personal-mark-v2/embedding.js
web/personal-mark-v2/errors.js
web/personal-mark-v2/geometry.js
web/personal-mark-v2/index.js
web/personal-mark-v2/legacy.js
web/personal-mark-v2/parser.js
web/personal-mark-v2/profile-registry.js
web/personal-mark-v2/typed-save.js
web/personal-mark-v2/unicode16-data.js
web/personal-mark-v2/unicode16.js
web/personal-mark-v2/unicode16.verify.mjs
web/personal-mark-v2/verify.mjs
.gitignore
THIRD_PARTY_NOTICES.md
```

`.gitignore` and `THIRD_PARTY_NOTICES.md` are hunk-only updates; the other
38 paths are additions. The ignore hunk also reserves the later Desktop build
output directories; it does not copy or publish runtime files.

```powershell
.venv-py312/Scripts/python.exe -B -m unittest -q tests.test_personal_mark tests.test_personal_mark_motion tests.test_personal_mark_v2 tests.test_personal_mark_v2_store tests.test_personal_mark_snapshot tests.test_personal_mark_unicode16 tests.test_personal_mark_v2_cross_language
node web/personal-mark-v2/verify.mjs
node web/personal-mark-v2/unicode16.verify.mjs
```

### 3. tauri-bridge

Dependency: foundation and personal-mark-v2. Unrelated changes included: NO.
Public safety: exact approved paths; final audit results below. Native evidence
remains PENDING where applicable. Paths (23):

```text
desktop/Cargo.lock
desktop/Cargo.toml
desktop/build.rs
desktop/capabilities/main-window.json
desktop/src/asset_stage.rs
desktop/src/host.rs
desktop/src/lib.rs
desktop/src/main.rs
desktop/src/protocol.rs
desktop/tauri.conf.json
desktop/tests/fixtures/bridge_fixture.py
scripts/shirushi_bridge.py
src/desktop_bridge.py
tests/test_desktop_bridge.py
web/adapters/desktop-adapter.js
web/desktop.html
web/desktop.js
web/desktop/contract.js
web/desktop/controller.js
web/desktop/i18n.js
web/desktop/presentation.js
web/desktop/transport.js
web/desktop/verify.mjs
```

```powershell
.venv-py312/Scripts/python.exe -B -m unittest -q tests.test_desktop_bridge
node web/desktop/verify.mjs
```

### 4. ci-verification

Dependency: all preceding groups. Unrelated changes included: NO.
Public safety: exact approved paths; final audit results below. Native evidence
remains PENDING where applicable. Paths (7):

```text
.github/workflows/f2c1-windows-verification.yml
scripts/verify_f2c1_ci.py
tests/test_f2c1_ci_verification.py
tests/test_public_fixture_governance.py
docs/development/f2c-verification.md
assets/branding/README.md
web/README.md
```

```powershell
.venv-py312/Scripts/python.exe -B scripts/verify_f2c1_ci.py
.venv-py312/Scripts/python.exe -B -m unittest -q tests.test_f2c1_ci_verification tests.test_public_fixture_governance
git diff --check
```

## Materialized-tree verification ledger

Completed locally on the candidate tree, 2026-09-29:

| Check | Actual local result |
| --- | --- |
| Python bridge + seven Personal Mark suites | 95 tests: 93 PASS, 2 symlink-privilege SKIP |
| CI audit / asset governance | 16 PASS (11 resource audit + 5 governance) |
| Shared Web | 53 checks / 5 locales / 52 keys PASS |
| Desktop Web | 76 checks / 5 locales PASS |
| Personal Mark v2 Web | 117 checks PASS |
| Unicode 16 Web | 19965 rows / 99825 checks PASS |
| Web Canary | 5 locales / 56 keys / invariants PASS |
| JavaScript syntax | 40 JS/MJS files PASS |
| Source resource audit | PASS: 29 Web assets, 10 sidecar resources, 52 hashes |
| Workflow YAML / PowerShell syntax | PASS: 13 run blocks parsed, not executed |
| Original public file preservation | 101/101 raw checkout hashes and Git blobs identical |
| Untransformed source copies | 88/88 raw SHA-256 matches |
| Approved existing updates | Exactly two additive hunks, 19 added lines |
| Motion-domain source / AST equivalence | All 13 test bodies identical |
| Legacy Tk integration | 2 EXCLUDED / PENDING, not executed here |
| Candidate allowlist | Expected 97 / actual 97 changed paths; 198 deliverables |
| Extra publication candidates | 0 |
| Privacy/secret scan | 174 text files + 24 unchanged base binaries; no new finding |
| Known base binary exception | One official sample-key marker; unchanged c2patool blob |
| Changed/untracked whitespace scan | PASS |
| `git diff --check` | PASS (line-ending conversion warnings only) |

The two skipped tests are physical symlink-privilege checks in the v2 store
suite, not the excluded Tk tests. No privileges or security settings were
changed. The Python bridge suite uses isolated synthetic fixtures and direct
Python subprocesses; this is not an actual Tauri-started sidecar or window test.
The 13 PowerShell workflow blocks were parsed only. No Rust command was run.

The pre-existing c2patool binary also contains upstream build-environment path
strings; these remain inside the identical public-base blob. They are not
new user home paths. The known credential exception is not a blanket exemption
for additional secrets. The preserved Windows documentation image was visually
checked as a generic illustrated guide, not a private machine screenshot.

The complete non-Git filesystem inventory is 198 deliverables plus exactly
14 ignored local-test runtime files: seven files in the fresh no-pip venv and
seven bytecode files produced by isolated bridge subprocesses. Test scratch
directories are empty; no profile, model cache, native output or private image
was found. These runtime files are not publication inputs. Never publish a
directory archive or use bulk staging; the exact path plan above is the boundary.

The separate development checkout, its existing dirty files and designated
handoff were not modified; 189 saved changed-file hashes remain identical.
This public ledger records the new work without importing development notes.
The public HEAD and origin/main remain the approved base, the index is empty,
and no commit identity was configured. New commits require the Owner's verified
noreply address and a separate explicit authorization.

`git diff --name-status 2884b6c4da1339778ae4678ddae159a4100fa130` reports:

```text
M       .gitignore
M       THIRD_PARTY_NOTICES.md
```

That command excludes the 95 untracked additions; `git status --short -uall`
and the exact allowlist supply the complete 97-path comparison. No unexpected
difference was found. The exact per-path SHA-256 inventory is held with the
local review evidence, outside this deliverable tree.

Clean-export materialization and permitted local checks are complete.
STOP at **Public Export Owner Review**. Hosted CI is **NOT RUN**, native manual
verification is **PENDING**, F2C.1 is **ENVIRONMENT BLOCKED**, code verification
is **PARTIAL PASS**, and G2 is **NOT READY**. None of those gates is promoted by
the clean-export or local-source test results.
