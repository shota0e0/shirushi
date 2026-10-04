# v0.2 flat development package

This is a bounded byte-assembly mechanism for `DEVELOPMENT_PACKAGE`, not an
installer or Production package. It does not build or execute either binary.
The mechanism's synthetic tests do not prove a real native assembly or a working
relocated Desktop. Real native assembly evidence is currently
`NATIVE_ASSEMBLY_NOT_RUN`; the overall delivery Gate remains PARTIAL.

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

## Runtime and authority limits

`runtimeBinding` intentionally remains `UNPROVEN`. The current development Rust
runner uses independently frozen compile-time development helper root/manifest
inputs; copying siblings does not introduce directory discovery or bind the
relocated package to that runner. Release execution remains development-boundary
limited. No Tauri configuration is changed (`bundle.active` remains false).
No Desktop/helper launch, inspection fixture, UI integration, Python fallback,
WebView2/VC prerequisite installation, signing, installer generation, or zero-setup
claim is included in this mechanism Gate. Any package runtime-binding change
requires a separate Owner-approved Gate.

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
