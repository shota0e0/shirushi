# v0.2 VC Runtime offline candidate freeze

This Gate freezes **one candidate**, not an approved distribution input or an
installed runtime. The accepted prerequisite-readiness checkpoint is
`ceee564e6057b5711d8cafbc2f54f87d8453daee`. Its accepted feature-branch push is
handled separately from this narrow, not-yet-pushed freeze change.

## Exact acquisition

Requested Microsoft permalink: `https://aka.ms/vc14/vc_redist.x64.exe`.
One isolated payload download was performed, without retries, using ordinary
HTTPS validation. The complete observed chain is frozen in
`v02-vc-runtime-offline-candidate.json`:

1. `aka.ms/vc14/vc_redist.x64.exe`: HTTP 301.
2. `aka.ms/vs/18/release/vc_redist.x64.exe`: HTTP 301.
3. `download.visualstudio.microsoft.com`: HTTP 200.

Retrieved UTC: `2026-10-04T07:15:28.457280Z`.
Content-Length and independently measured size: **18,731,856 bytes**.
Local basename: `vc_redist.x64.exe`.
Raw file SHA-256:
`843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c`.
The final public HTTPS URL is recorded, but is not a permanent policy or an
automatic future-input selector. Size/SHA were recorded by independent raw
reads before any metadata/PE/signature parser opened the candidate.

The executable remains outside Git in a dedicated temporary evidence directory.
No binary, extracted payload, installer resource or certificate material is
committed. Installer execution count is **0**.

## Wrapper, version and package architecture

Passive metadata from those exact bytes:

- FileVersion/ProductVersion: `14.51.36247.0` / `14.51.36247.0`.
- ProductName: `Microsoft Visual C++ v14 Redistributable (x64) - 14.51.36247`.
- CompanyName: `Microsoft Corporation`.
- OriginalFilename: `VC_redist.x64.exe` (case preserved, unlike local basename).
- Wrapper PE machine `0x014c`, PE32, Windows GUI subsystem 2, six sections,
  characteristics 3330, one WIN_CERTIFICATE table entry.

The x86 wrapper is **not** the architecture of every contained Runtime binary.
Package x64 delivery identity is supported by the official x64 channel plus
trusted signed product metadata, not filename alone. Microsoft documents that
the current X64 redistributable package contains both x64 and ARM64 binaries.
Neither inner-binary architecture nor installed Runtime architecture was proven
by this non-executing Gate; both remain UNPROVEN in the freeze record.

## Signature inventory and trust

There is exactly one embedded primary Authenticode CMS signer, one certificate
table entry, no secondary/nested signature and no legacy countersigner. The
sole unsigned attribute is the RFC3161 timestamp attribute. No VC-specific
exception for an untrusted auxiliary signature was needed or introduced.

Primary signer: Microsoft Corporation; issuer Microsoft Code Signing PCA 2024.
Digest: SHA-256. Its mathematical CMS signature is valid. Signer certificate DER
SHA-256 is
`c30b441672c82883d92eddac6d24cb57e9960bda4486c7fb5865e74157f35850`.
Subject, issuer, serial, validity interval and EKUs are frozen without private
paths. Chain order is leaf → Code Signing PCA 2024 → Microsoft Root CA 2011.

RFC3161 timestamp: `2026-05-27T05:56:14.262Z`, Microsoft Time-Stamp Service,
issued by Microsoft Time-Stamp PCA 2010. Its CMS mathematical signature is
valid. Timestamp leaf DER SHA-256 is
`2cad33a99aef874ead5a5c5c2f9618c5f29da750234d0845ff3962c7343326b7`.
Timestamp chain order is leaf → Time-Stamp PCA 2010 → Microsoft Root CA 2010.

Independent diagnostic dimensions:

- Windows WinVerifyTrust `WINTRUST_ACTION_GENERIC_VERIFY_V2` enumeration:
  secondary signature count 0; specific index 0 returned `0x00000000`.
  No UI, whole-chain/exclude-root revocation policy, MD2/MD4 disabled. State
  handles were closed. No hash-only, no-policy, or no-revocation weakening.
- SignTool `verify /pa /v /tw /ds 0`: exit 0, zero warnings/errors.
- SignTool `verify /pa /v /tw /all`: exit 0, zero warnings/errors; timestamp
  verified under Windows Authenticode policy.
- Signer and timestamp independent X509 chain diagnostics: complete/pass,
  no error statuses, Online/ExcludeRoot/NoFlag. The signer chain uses the current
  verification time; timestamp-chain VerificationTime is explicitly the RFC3161
  timestamp. These support the attribution;
  generic chain checking is not substituted for Authenticode file verification.
- Online-capable revocation validation succeeded, but this does **not** claim a
  forced fresh HTTP/CRL response or perpetual offline revocation freshness.

The exact Windows SDK verifier was `signtool.exe`, ProductVersion
`10.0.26100.7705`, SHA-256
`431ee314c83988cacda86606356fd321b75ae0093481b97e3b738e99c412f2a0`.
No trust roots were imported, policy changed, elevation requested or candidate
executed. Temp diagnostics use only Windows trust APIs, passive PE parsing,
.NET CMS/X509 inspection and SignTool's **verify** command.

SignTool's Authenticode **image digest**
`e9a3ba3c1fe68f4d736ebcfedd3307202f624f909d7fc90ef44021d7497067f2`
is deliberately distinct from the whole-file raw SHA above. Do not substitute
one for the other. The freeze JSON contains structured semantics only, no raw
SignTool output or user-specific paths.

## Build compatibility and readiness policy are not closed

Accepted historical Desktop native CI `36962270599`, source
`20a492f77cd82211f706ce53a8a3d0b45845cac5`, established an actual observed Rust
link invocation with Microsoft `link.exe`:

- Path-derived MSVC toolset `14.51.36231`.
- Linker FileVersion/ProductVersion `14.51.36260.0`.
- Linker SHA-256
  `a8c42e25c91ca2453e1110540ac08f0fc12d7539454a11b9af8baeecb469837d`.

The candidate is numerically newer than that path-toolset version but older than
that linker file version. These different evidence types are not silently
equated. The current development native CI `37180157563` does not capture an
exact current MSVC Build Tools version; exact helper Build Tools version is also
UNPROVEN. Consequently the complete actual-build compatibility classification
is **VC_CANDIDATE_COMPATIBILITY_UNPROVEN**, not a blanket supported/too-old claim.

Microsoft requires the installed redistributable to be at least as recent as
the Build Tools used to build the app. The historical mixed evidence and missing
current/helper capture do not establish one exact Shirushi runtime minimum.
Minimum classification is **VC_MINIMUM_POLICY_UNPROVEN**, minimum value null.
The shipped readiness policy remains POLICY_UNSET and its VC offline input
remains UNCONFIGURED; this Gate does not edit or integrate that tool.

Official sources:

- [Latest supported VC redistributables, architecture and build-version rule](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170).
- [Redistribution and license boundary](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170).
- [MSVC binary compatibility restrictions](https://learn.microsoft.com/en-us/cpp/porting/binary-compat-2015-2017?view=msvc-170).
- [Windows signature-selection settings](https://learn.microsoft.com/en-us/windows/win32/api/wintrust/ns-wintrust-wintrust_signature_settings).
- [SignTool verification semantics](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool).

Redistribution is restricted by applicable Visual Studio licensing terms;
engineering evidence is not legal approval. Status: **LEGAL_REVIEW_REQUIRED**.

## Strict independent freeze record and validator

Freeze schema 1 records exact identity, source chain/timestamp, passive metadata,
signature/certificate/chain mappings, verifier identity, historical build facts,
separate compatibility/minimum/legal classifications and executionCount 0.
`scripts/verify_vc_runtime_candidate.py` defines the complete closed nested
schema, rejecting unknown/missing fields, duplicates, non-finite numbers,
bool-as-int, BOM, oversized/trailing JSON and unexpected signature structure.

The validator independently freezes expected file size/raw SHA and exact
record size/raw SHA. A coordinated file+record edit cannot authorize new bytes.
Any record byte change, even logically equivalent JSON formatting, is rejected.
The frozen record identity is 7,362 bytes / SHA-256
`6d00952879411aff1f2f18cc3936d18233c7a0d3abdd94159a1643b5b84345e6`.
No CLI/environment expected-value, minimum-version, source or policy override
exists. New bytes/signers/records require a separate reviewed Gate.

```powershell
.\.venv-py312\Scripts\python.exe -B scripts\verify_vc_runtime_candidate.py --record 'C:\explicit\v02-vc-runtime-offline-candidate.json' --candidate 'C:\explicit\vc_redist.x64.exe'
.\.venv-py312\Scripts\python.exe -B -m unittest tests.test_verify_vc_runtime_candidate -v
```

Both paths must be explicit absolute local fixed-drive paths, with exact
basenames, safe path syntax and regular non-reparse/non-symlink/non-hardlinked
files. Every ancestor is checked. Bounded streaming reads compare path and open
handle identity/size/mtime/link count before and after, then recheck ancestor
identity and non-reparse/type constraints. Unrelated ancestor-directory size or
mtime changes do not replace identity and are not treated as candidate mutation.
Unavailable file identity fails closed. This is ordinary detectable filesystem
replacement protection, not a hostile concurrent kernel-level race guarantee.

The validator performs **offline byte/record validation only**, never a download
or fresh signature/certificate verification. On match it explicitly reports
`FROZEN_ACQUISITION_EVIDENCE_ONLY`, candidate state and executionCount 0. A match
does not grant distribution approval, installed-runtime readiness, repaired
state, or a minimum version. The actual once-downloaded candidate has separately
passed the validator; synthetic tests supplement rather than replace that proof.
No candidate binary is committed as a test fixture.

Next proposed Gate: **VC Runtime Minimum Readiness Policy Closure**.
No install/repair orchestration, Desktop startup integration, bundling, NSIS,
Explorer registration or Clean Windows validation starts automatically.

Production adoption: UNAPPROVED. Production Signing: PARKED.
Distribution Compliance: NOT READY. Full F3A: NOT READY.
VC redistributable execution: 0. WebView2 installer execution: 0.
