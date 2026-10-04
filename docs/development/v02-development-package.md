# v0.2 flat development package

This is a bounded byte-assembly mechanism for `DEVELOPMENT_PACKAGE`, not an
installer or Production package. It does not build or execute either binary.
The mechanism's synthetic tests alone do not prove a real native assembly or a
working relocated Desktop. Assembly was accepted at feature commit
`9d10a20f277c941b2a9dda18410e6de6ddfae21e`, validation commit
`99d951e4b6bd2d1f386325d769fa31fd9d71819f`, CI run `37175939344`.
The separate executable-relative runtime-binding validation remains pending until
the current native CI proves compiled-digest and moved-package preflight agreement.

## Exact layout and manifest

The output root contains exactly these three regular files, with exact casing:

```text
development-package/
  shirushi-desktop.exe
  shirushi-inspection-helper.exe
  inspection-helper.manifest.json
```

No PDB, source, Python environment, cache, secret, certificate, executable fixture,
or fourth evidence file is included. The manifest uses the existing Rust package
schema, not a new helper protocol. It is canonical ASCII JSON (sorted keys,
compact separators, no BOM or trailing newline), bounded to 4,096 bytes:

```json
{"protocolVersion":1,"relativePath":"shirushi-inspection-helper.exe","schemaVersion":1,"sha256":"<actual copied helper SHA-256: 64 lowercase hex digits>","size":123456}
```

The example size/hash are placeholders, not accepted input constants. The actual
manifest is generated only after the staged helper matches the independently
captured source identity, size, and SHA-256. The helper protocol stays version 1.

## Explicit inputs and assembly

Use `scripts/package_v02_development.py assemble` on Windows with three explicit
absolute paths: `--desktop`, `--helper`, and `--output-root`. Both sources must
already exist and have the exact names above; the output must not exist and its
parent must already exist. There is no build, download, PATH search, environment
override, runtime discovery, or caller-supplied helper SHA that authorizes bytes.

Paths must use a local fixed drive. UNC, device, relative, traversal, ADS,
reserved-name, trailing-dot/space aliases, links, and reparse points are rejected.
All existing ancestors are inspected. Source/output overlap and case collisions
are rejected. Each executable is bounded to 256 MiB. Bounded header checks require
x64 PE32+, an executable rather than a DLL, valid section file bounds, Windows GUI
subsystem for Desktop, and console subsystem for helper. These checks are not
exhaustive PE validity, publisher authentication, or runtime-compatibility proof.

The assembler captures source metadata and hashes before copying, rechecks them
around copying, and copies in chunks of at most 1 MiB. It allocates a private
sibling stage on the same volume. Before every destination write it rechecks the
stage's ancestor/reparse safety and allocated directory identity. Full staged
inventory, copied hashes, and canonical manifest are audited before Windows
no-clobber rename publication; the published package is then audited again.
An existing output is never overwritten. No POSIX overwrite fallback is provided.

Failed assembly cleans only its identity-matching, flat, exclusively allocated
stage when every contained entry is an expected regular file. Unexpected entries,
reparse observations, or changed ownership cause cleanup refusal, not recursive
deletion. A package already published before a final audit failure is retained
for Owner inspection; it is not silently approved or automatically removed.

## Independent evidence and recurring audit

Successful assembly emits one bounded JSON evidence record to stdout, outside
the package. Its exact fields are `schemaVersion`, `artifactType`, `target`,
`runtimeBinding`, and `files`. Values are schema 1, `DEVELOPMENT_PACKAGE`,
`x86_64-pc-windows-msvc`, and `UNPROVEN`; `files` contains exactly the three
basenames, each with only actual `size` and lowercase `sha256`. It contains no
local source paths. This record is at most 8,192 bytes.

Stdout is canonical ASCII JSON followed by one LF byte. Stderr prints
`DEVELOPMENT_RECORD_SHA256`, covering those exact stdout bytes **including LF**.
Capture stdout with a byte-preserving writer outside the package, and freeze the
digest separately in the Owner-approved evidence record. Windows PowerShell 5.1
`>` must not be used: it can decode/re-encode native stdout as UTF-16. For example,
an explicit Python controller can open the external record with `open(path,
'xb')` and use `subprocess.run([...assembler arguments...], stdout=record_file,
check=True)`; this launches only the assembler, not either native payload. Do not
overwrite an existing evidence file. Never add this record as a fourth package
file. If capture fails, the already-published package needs separate review.

For a later independent audit, invoke:

```text
python scripts/package_v02_development.py audit --package-root C:\DevPackages\development-package --record C:\DevEvidence\development-record.json --expected-record-sha256 <separately frozen exact record SHA-256>
```

The auditor verifies the external record's exact byte digest before accepting its
strict schema, rejects unknown/duplicate fields, hashes every actual package file,
and checks canonical manifest bytes and helper identity against the external
record. Adjacent manifest/hash files cannot authorize coordinated helper/manifest
tampering. A digest newly calculated from an untrusted replacement record is not
an independent approval; protect the frozen record/digest outside the package.

The Python API `capture_inputs()` returns an immutable input snapshot, allowing a
caller to freeze source identity before a later `assemble(..., snapshot=...)`.
Arbitrary bytes altered before the first trusted snapshot cannot be identified as
tampering by this assembler. It establishes byte continuity from the selected
inputs, not that the inputs were built from an accepted commit, signed, safe, or
approved. Input provenance and review remain separate requirements.

## Canonical preparation before Desktop compilation

The single build-time canonical generator is:

```text
python scripts/package_v02_development.py prepare-manifest --helper C:\DevBuild\shirushi-inspection-helper.exe
```

It requires an explicit actual helper with the same local/regular/reparse/identity
and x64 console PE checks as assembly. It internally captures size/SHA/identity,
serializes through the same helper-manifest function used for the actual staged
helper, and rechecks the captured helper before returning. No caller-provided
expected helper hash authorizes replacement. Preparation does not need Desktop
bytes and does not build, execute, or approve the helper.

Capture its stdout externally as raw bytes: canonical ASCII JSON with **no LF or
BOM**. Stderr's `DEVELOPMENT_MANIFEST_SHA256` covers those exact manifest bytes.
This differs intentionally from the assembly evidence record, which ends in LF.
Use byte-preserving capture, never Windows PowerShell 5.1 text redirection.

The build order is helper build → canonical manifest preparation → independently
freeze SHA-256 of those raw bytes → set compile-time
`SHIRUSHI_DEV_INSPECTION_MANIFEST_SHA256` → Desktop build → assemble with the same
helper → require packaged and prebuild manifest bytes to match exactly. The
previous insertion-order PowerShell JSON was logically equivalent but had a
different raw digest; it must not authorize canonical package bytes. No expected
digest is read or derived from the package at runtime.

## Runtime and authority limits

The assembler evidence record's `runtimeBinding` intentionally remains `UNPROVEN`:
byte assembly cannot certify how Desktop was compiled. The Windows debug-only
development application now obtains its actual path using `current_exe()`, takes
that executable's parent, and reuses unchanged `CanaryPackage` preflight with the
independently embedded compile-time digest. No compile-time absolute root is
required. CWD, argv, PATH, registry, runtime environment root/digest values, and
frontend input cannot select the root or authorize its contents.

The resolver checks an absolute regular non-reparse executable-path leaf. Existing
package preflight retains fixed helper/manifest basenames, local fixed-drive and
ancestor/root reparse checks, raw manifest digest, helper size/SHA, retained guards,
pre-resume process identity, and supervisor cleanup. Moving the unchanged three
files is expected to preserve that development contract without rebuilding.
Focused tests may inject a controlled executable path only through a Windows
debug/explicit-native-test cfg surface; it still uses the compiled digest. The
normal dispatch tests use the actual current test executable's sibling directory.
An ignored post-assembly test is explicitly executed in CI against actual package
A and its copied package B, not the full UI. These tests do not add application
runtime discovery or environment overrides.

Release execution remains development-boundary limited. No Tauri configuration
is changed (`bundle.active` remains false).
Assembly and moved-package preflight do not launch Desktop or helper. Separately,
the accepted native CI regression floor still executes the real Rust helper and
limited-inspection fixture through the normal application dispatch. No full UI
launch, Python inspection fallback, WebView2/VC prerequisite installation,
signing, installer generation, or zero-setup claim is included. Any further
runtime-binding authority change requires a separate Owner-approved Gate.

Filesystem checks detect observable pre/post-copy substitutions and changed
reparse/identity state. They are not a kernel-level defense against a hostile
concurrent filesystem actor. Use controlled local inputs/output parents; do not
treat this as an adversarial multi-user publishing service. Windows Python 3.12
`lstat`/`fstat` can expose different ctime semantics, so common file identity,
size, and mtime checks are paired with exact byte hashes rather than comparing
cross-API ctime values. Missing/zero file IDs or invalid identity types fail closed
instead of treating an unknown identity as allocated stage ownership. Signature
verification is not faked or supplied here.

Tests use synthetic, non-executed PE byte fixtures in exclusively owned temporary
directories. Actual Windows symlink tests may be skipped when privileges are
unavailable; synthetic reparse and stage-substitution rejection remain exercised.

Production adoption: UNAPPROVED. Production Signing: PARKED.
Distribution Compliance: NOT READY. Full F3A: NOT READY.
