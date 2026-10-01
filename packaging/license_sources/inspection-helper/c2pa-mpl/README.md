# c2pa MPL source evidence

These five files are the current c2pa-rs 0.85.0 sources from tag
`c2pa-v0.85.0`, commit `3f40cdd22b60bf955d531b0301604e3f257e0a19`:

- `sdk/src/crypto/asn1/mod.rs`
- `sdk/src/crypto/asn1/rfc3161.rs`
- `sdk/src/crypto/asn1/rfc3281.rs`
- `sdk/src/crypto/asn1/rfc4210.rs`
- `sdk/src/crypto/asn1/rfc5652.rs`

All five are classified `MODIFIED_COPY`. Their origin is the
cryptography-rs / cryptographic-message-syntax 0.22.0 project, tag
`cryptographic-message-syntax/0.22.0`, commit
`c09b693c8a6f9cf7475f1310405632931f9ad12b`. Redistribution does not imply
that Shirushi authored the upstream modifications. Original copies are not
included here.

The source files are frozen as exact raw bytes, with their existing MPL 2.0
headers retained. The authoritative MPL 2.0 plaintext is included locally as
`MPL-2.0.txt`. `approved-source-set.json` records the approved identities;
the offline validator independently contains the same reviewed constants.
No line-ending normalization or formatting is permitted.

This subset is not represented as a standalone buildable crate. Its types
and module imports belong to the surrounding c2pa SDK. Consult the wider
[exact c2pa source tree](https://github.com/contentauth/c2pa-rs/tree/3f40cdd22b60bf955d531b0301604e3f257e0a19)
and the
[exact original source tree](https://github.com/indygreg/cryptography-rs/tree/c09b693c8a6f9cf7475f1310405632931f9ad12b/cryptographic-message-syntax)
for context.

This is repository approval evidence, not release provenance or a final
distribution notice. This freeze does not assert final legal sufficiency.
Final package contents, notice wording, source-availability decisions and
Production dependency adoption remain separate Owner review decisions.

Run from the repository root:

```text
python -B scripts/verify_c2pa_mpl_source_evidence.py
python -B -m unittest discover -s tests -p test_c2pa_mpl_source_evidence.py -v
```
