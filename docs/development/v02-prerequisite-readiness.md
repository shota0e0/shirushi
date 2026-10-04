# v0.2 development prerequisite readiness foundation

This independent, Python-standard-library tool is **read-only development
evidence**, not a Desktop runtime dependency, startup gate, installer, repair
or approval mechanism. The accepted development runtime-binding checkpoint is
`cf25049057ab81b35126c46c87502c37cd44b169`, validated by native CI `37180157563`
at validation commit `961d0ebb12527831bcd6de1a27c27a22a027e339`.

The only new files are the tool, its tests and this document. Existing Desktop
IPC, helper protocol, package binding, Tauri configuration, workflows, notices
and `CURRENT_HANDOFF` are unchanged. Parent-controlled independent review and
verification determine the Gate result; this document does not self-approve it.

## Two independent branches

Installed-runtime readiness observes registration facts, normalizes them and
applies a separately trusted build policy. Offline-input identity measures an
explicit file against independently frozen candidate constants. File presence
does not establish runtime installation. Runtime installation does not approve
a distribution input. Neither branch installs or repairs anything.

```powershell
.\.venv-py312\Scripts\python.exe -B scripts\prerequisite_readiness_v02.py observe
.\.venv-py312\Scripts\python.exe -B scripts\prerequisite_readiness_v02.py observe --webview2-candidate 'C:\explicit\MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
```

There is no download, discovery, directory scan, PATH lookup, external program,
registry write, service change, elevation, reboot or Windows servicing command.
Candidate location is explicit; no candidate is searched for automatically.
CLI policy, version, expected-hash and registry-source overrides are absent.
Process exit `0` means the observation/report completed, **not overall READY**.
Consumers must parse `overall`. An explicitly rejected candidate or a contract
error returns `2`. Malformed/unreadable registrations remain structured unknown
results, not successful readiness. Stdout is one bounded UTF-8/ASCII JSON object
plus LF. Windows PowerShell 5.1 redirection may re-encode text; use byte-preserving
capture if applying the strict JSON parser to a saved report.

## Registry observations and limits

Microsoft documents VC registration under
`SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64`. The tool reads only
`Version` (`REG_SZ`), `Installed` where exposed, and the optional
`Major`, `Minor`, `Bld`, `Rbld` (`REG_DWORD`) version fields. It explicitly queries
both HKLM 64-bit and 32-bit views of that **same x64 key**, never an x86/ARM64
runtime key. Source labels preserve the two views. This handles the published
WOW6432Node/native-view qualification conservatively: one valid registration
and one missing view can be evidence; contradictory versions or installed flags
are `DETECTION_FAILED`. Any failed/malformed source prevents READY. Optional
numeric version fields must be complete and agree with the Version string when
present. Missing Installed is retained as unknown and cannot establish readiness
under a configured policy. No claim of binary/module-load closure follows.

The shipped immutable `BUILD_POLICY.vc_minimum` is **None**. No approved minimum
or VC redistributable input exists. Normalized VC evidence therefore produces
`POLICY_UNSET` (or `DETECTION_FAILED` for malformed/unreadable evidence), never
READY. Synthetic tests pass an explicit `Policy` to pure functions; that is not
an Owner-approved product minimum. Result validation requires its policy to
equal an independently supplied trusted policy, defaulting to the shipped None.
An observed version or report field cannot grant itself policy authority.

WebView2 queries only the stable Evergreen GUID
`{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}` and `pv` (`REG_SZ`):

- HKLM 32-bit view of `SOFTWARE\Microsoft\EdgeUpdate\Clients\{GUID}` corresponds
  to the documented `HKLM\SOFTWARE\WOW6432Node\...` on x64 Windows.
- HKCU 32-bit view of `SOFTWARE\Microsoft\EdgeUpdate\Clients\{GUID}` is the
  explicit view selected for the documented per-user `HKCU\Software\...` key.
  The report retains a separate per-user source; it is not merged with HKLM.

Missing pv, null/empty `REG_SZ`, or `0.0.0.0` is missing evidence. Wrong types,
malformed versions and query failures are not missing. Different positive hive
versions are conservatively conflicting rather than silently choosing a newer
one. Browser or preview-channel registrations are never queried or substituted.
The live collector is supported only on a 64-bit Python/x64 Windows host;
unsupported hosts produce failed observations, not a manufactured baseline.

**Positive pv establishes registration presence/version, not Runtime x64.**
The live collector records WebView2 architecture `UNPROVEN` and its readiness is
`DETECTION_FAILED` / `READINESS_UNKNOWN` until a later approved strong binary/API
identity mechanism exists. It does not locate binaries, load a DLL or call the
version API in this slice. Synthetic test observations explicitly marked
`X64_VERIFIED` exercise the READY state engine only, not live architecture proof.
No product WebView2 minimum is invented; a valid positive version plus future
strong x64 evidence is the foundation's WebView2 condition, not full usability
or zero-setup proof.

Official references, reviewed for this Gate:

- [Microsoft VC redistribution and registration/version evidence](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170).
- [Microsoft WebView2 distribution and stable Runtime detection](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution).

## Strict report schema version 1

No unknown/missing fields, duplicate JSON keys, non-finite numbers, BOM,
trailing object, bool-as-int versions/sizes or oversized report are accepted.
The maximum raw report is 16,384 bytes. Nested keys are closed as well.

| Object | Exact fields / conditions |
| --- | --- |
| Root | `schemaVersion` integer 1; `purpose` = `READ_ONLY_DEVELOPMENT_READINESS`; `policy`, `overall`, `runtimes`, `offlineInputs` |
| policy | `vcMinimumVersion`: null in the shipped policy, otherwise independently trusted canonical four-part version |
| runtimes | Exactly `vc`, `webview2` |
| Each runtime | `status`, `reason`, `observations` |
| Each observation | `source`, `query`, `version`, `installed`, `architecture`, `issue` |
| offlineInputs | Exactly `vc`, `webview2` |
| offlineInputs.vc | `status` = `VC_OFFLINE_INPUT_UNCONFIGURED` |
| offlineInputs.webview2 | `status`, `filename`, `expectedSize`, `expectedSha256`, `approval`, `signaturePolicy`, `currentSignatureVerification`, `runtimeIdentity`, `error` |

Observation order and source labels are fixed: `HKLM64:VC14:Runtimes:x64`,
`HKLM32:VC14:Runtimes:x64`, `HKLM32:EdgeUpdate:StableWebView2`,
`HKCU:EdgeUpdate:StableWebView2`. VC has two rows and WebView2 has two rows.
`query` is `OK`, `MISSING`, `FAILED` or `MALFORMED`. `version` is null outside OK;
OK versions are canonical four unsigned decimal parts, each 0..65535, without
leading zeros, and not all zero. VC's observed lowercase `v` prefix and decimal
component zero padding (for example `v14.44.35211.00` → `14.44.35211.0`) are
normalized before comparing numeric DWORD fields. Trusted policy/result versions
remain canonical; this does not relax WebView2 syntax or approve a minimum.
`installed` is boolean or null, never numeric.
Architecture is `X64_REGISTERED`, `X64_VERIFIED`, `UNPROVEN` or
`WRONG_ARCHITECTURE`; live WebView2 never emits X64_VERIFIED.

Observation `issue` is `NONE` for OK, `REGISTRATION_MISSING` for MISSING,
`QUERY_FAILED` for FAILED, or `VERSION_INVALID`, `FIELD_TYPE_INVALID`,
`VERSION_FIELDS_CONFLICT` for MALFORMED. Raw values, exception messages, private
paths and environment dumps are not published.

Per-runtime states are closed: `READY`, `MISSING`, `OUTDATED`, `POLICY_UNSET`,
`DETECTION_FAILED`. Reasons are derived, not caller-authorizing flags:
`OBSERVATION_FAILED`, `VC_MINIMUM_UNAPPROVED`, `REGISTRATION_MISSING`,
`SOURCE_VERSION_CONFLICT`, `X64_READINESS_UNPROVEN`, `INSTALLED_FLAG_UNPROVEN`,
`SOURCE_INSTALLED_CONFLICT`, `INSTALLED_FLAG_FALSE`, `BELOW_TRUSTED_MINIMUM`,
`X64_REGISTERED_POLICY_SATISFIED`, `X64_RUNTIME_EVIDENCE_VERIFIED`.

`overall` is recomputed independently:

- all required runtime states READY → `READY`;
- any POLICY_UNSET or DETECTION_FAILED → `READINESS_UNKNOWN`;
- otherwise missing/outdated runtime → `PREREQUISITES_REQUIRED`.

`validate_result`/`parse_result` recompute statuses and reasons from normalized
facts and the external trusted policy. Editing status and policy together does
not approve an input. This is schema/policy consistency, **not cryptographic
authentication of an arbitrary externally supplied observation report**.
Collector provenance still matters; tests inject observations intentionally.

## Explicit offline candidate identity

Only this independently frozen candidate may match:

- Basename: `MicrosoftEdgeWebView2RuntimeInstallerX64.exe`
- Size: `212272848`
- SHA-256: `f6df8e4bc857786ff641cd01da1449169eaf8236c936ced485ea61685ba4da40`

The validator requires an explicit absolute local fixed-drive path, exact case
basename, no traversal/ADS/device/UNC syntax, regular file and non-linked/non-
reparse ancestors, and a candidate leaf with exactly one hard link (`st_nlink`
equals 1). Unknown/zero/noninteger link count fails closed; multiple leaf links
are `HARDLINK_REJECTED`. It hashes bounded chunks, compares lstat/fstat identities,
size, modification time and link count before/after, and rechecks ancestors.
Zero/unknown file identity fails closed. Detectable replacement/mutation is rejected; this is
not a kernel-guaranteed hostile concurrent filesystem security proof.

Offline status is `NOT_CHECKED`, `CANDIDATE_IDENTITY_MATCH` or
`IDENTITY_REJECTED`. A rejection has one closed error code from the implementation
allowlist; otherwise error is null. Expected constants cannot be overridden by
JSON, CLI or environment. Exact-byte match means only candidate identity:

```text
approval = CANDIDATE
signaturePolicy = ACCEPT_PRIMARY_MICROSOFT_WITH_KNOWN_NESTED_EDGEBUILD
currentSignatureVerification = NOT_PERFORMED
runtimeIdentity = UNPROVEN
```

This records the historical exact-candidate Owner exception, not fresh primary
or nested signature verification, Microsoft publisher proof, Runtime x64 proof,
installer execution or distribution approval. New bytes never inherit that
exception. VC offline input is always unconfigured. No arbitrary
`vc_redist.x64.exe` can become approved here.

## Verification and remaining Gates

```powershell
.\.venv-py312\Scripts\python.exe -B -m unittest tests.test_prerequisite_readiness_v02 -v
.\.venv-py312\Scripts\python.exe -B -m unittest tests.test_prerequisite_readiness_v02 tests.test_package_v02_development -v
```

Tests cover configured synthetic VC thresholds, missing/unset/type/query/
architecture evidence, both registry views and conflicts; stable WebView2
missing/valid/malformed/conflicting evidence and unproven live x64; closed JSON,
no false READY, coordinated policy/status edits; candidate size/hash/name/path/
reparse/handle-identity/tamper rejection and explicit VC unconfigured state.
The exact candidate positive branch mocks the measurement boundary and is
clearly **synthetic**, not validation of the historical 212 MB candidate.
Small synthetic files separately exercise real bounded hashing and mutation
checks. Actual symlink creation may be skipped for lack of privilege; synthetic
reparse rejection remains tested. Tests do not use the developer's installed
state as policy proof. No candidate location is implicitly searched.

Native CI is not added. A separately reported non-elevated local read-only
smoke, if performed, is observation evidence only. No Windows runner/developer
state defines the supported consumer baseline. Installer execution remains 0.

Next proposed Gate: **VC Runtime Offline Candidate Freeze**. Strong WebView2
x64/version identity, approved runtime minimums, installer input trust/legal
review and later install/repair orchestration remain separate Owner-reviewed
Gates. Do not start them automatically or block Desktop startup here.

Production adoption: UNAPPROVED. Production Signing: PARKED.
Distribution Compliance: NOT READY. Full F3A: NOT READY.
