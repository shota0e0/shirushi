# Isolated Limited Inspection semantic-parity PoC

DEVELOPMENT ONLY. Production adoption UNAPPROVED. Distribution Compliance and
Full F3A NOT READY. No Desktop/Tauri dependencies, release binary or Owner transport.

Exact SDK c2pa0.85.0, default-features=false, rust_native_crypto only. Frozen
Cargo.lock SHA-256:
a40f49659e9329689de6aa1bf581875a9e2b542e5d0faca6c33f4964fe03f3a8
Owner approved240Windows packages/26build scripts for this isolated PoC only.

This crate has no executable/CLI. The library Reader path is in-process and
explicitly disables trust promotion, remote fetching, OCSP and identity decoding.
It requires affirmative active-claim signature/hash/reference/asset evidence,
then maps only the exact signed cawg.training-mining preset into inner1/outer2.
It does not call Python/c2patool, fetch runtime network data, inspect TrustMark,
run Full Verification or make success motion eligible.

Authoritative tests run in the branch-restricted Windows PoC workflow, upload0:

    cargo test --locked --offline --target x86_64-pc-windows-msvc --manifest-path tools/f3a-rust-sdk-parity/Cargo.toml -- --nocapture

Run after build-time locked dependency acquisition. Real SDK tests independently
fingerprint the unchanged fixed PNG and inspect it using exact SDK; negatives
mutate private in-memory copies only. Pure adapter tests use synthetic evidence
and never substitute for real SDK proof. No OS-level network isolation is claimed.

See docs/development/f3a-rust-sdk-parity.md and the frozen inventory JSON for
authority, exact source/license provenance, feature/dependency and evidence mapping.
