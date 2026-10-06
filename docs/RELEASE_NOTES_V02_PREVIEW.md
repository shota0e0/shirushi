# Shirushi v0.2 Preview — release notes draft

Status: PUBLIC RELEASE NOT CREATED / RC VALIDATION INCOMPLETE.
These notes describe the frozen product and approved release direction, not an
available installer or proof of final Windows install/launch behavior.

## Product scope

- Windows x64 Desktop; single PNG/JPEG; five locales (ja/en/zh-CN/zh-TW/ko).
- Add the fixed Rights Intent 「AI学習・生成利用を希望しない」 in C2PA/CAWG metadata.
- Publish a separate `_rights` output; never overwrite the source or an existing
  output. A collision stops. Keep a separate backup of important artwork.
- Limited Inspection reads supported embedded C2PA/CAWG information. Result is
  `LIMITED_INSPECTION / INCOMPLETE`; TrustMark `NOT_CHECKED`. An unmarked image
  must not falsely display Rights Intent. Missing intent is not consent/refusal.
- Handwritten/Typed Personal Mark and approved motion remain UI presentation;
  visible signature pixels/glow/motion are not burned into output. Mark metadata
  is included; current selection is session-only, not persistent author identity.
- Single-image Explorer Add/Verify dispatches into the same Desktop/helper flow;
  no image processing runs inside explorer.exe. Batch is deferred to v0.3.

## Preview trust boundary — important

v0.2 Preview uses existing **public test C2PA credentials**. Those test credentials
can be used by others. They are NOT production-grade identity, Production Trust,
author identity verification, copyright ownership proof or complete provenance
verification. Production Trust remains PARKED / post-v0.2. Rights Intent behavior
is unchanged. AI platforms may ignore or remove the metadata; no AI-compliance,
usage prevention or survival-after-editing/SNS guarantee is made.

C2PA image credentials and Windows Authenticode executable signing are separate.
If Windows blocks launch, stop: no policy disabling, alternate launcher/unblock
trick, self-signed-root installation or security workaround is supported.

## Installer baseline and current limits

Windows x64 / Tauri NSIS / per-user is approved. Per-user is Shirushi installation
ownership, NOT a promise that all Microsoft prerequisites require no admin.
VC x64 deployment floor remains `14.51.36247.0`; theoretical build minimum remains
UNPROVEN. Compatible WebView2 availability must be observed, not inferred from a
registry version alone. Ready prerequisites are skipped; unknown/not-ready stops.

The current preparation does not launch or redistribute frozen vendor candidates.
Automated elevated vendor execution, public redistribution acceptance and missing-
prerequisite installation are not proven by these notes. Uninstall must remove
only exact Shirushi-owned files/registration, not artwork or shared runtimes.

Current Development PASS remains accepted, including native CI Add/Verify and
Explorer contracts. Owner-local native UI smoke was blocked by access denial;
the policy cause is unproven and this is not a product crash. Public release-
profile build, NSIS compilation, ordinary supported Windows launch, actual
install/uninstall/Explorer UI, clean Windows and final artifact notices/hashes
still require release-candidate validation. No public download/tag/release exists
for this v0.2 preparation.
