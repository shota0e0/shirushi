//! REAL SDK TESTS: exact c2pa0.85.0, immutable fixture, disposable in-memory copies.
use super::*;
const FIXTURE: &[u8] = include_bytes!("../../../tests/fixtures/inspection/valid_shirushi.png");

#[test]
fn real_sdk_fixed_fixture_positive_evidence_and_exact_wire_result() {
    fingerprint(FIXTURE).unwrap();
    let e = read_sdk_evidence(FIXTURE).expect("REAL_SDK_READER_FAILED");
    // Path-free diagnostics. Never emit SDK debug objects/metadata or exception text.
    println!("REAL_SDK_EVIDENCE: parsed={} embedded={} state={} refs={} rights={} success={} info={} failure={} ingredient_statuses={}",
        e.parsed,e.embedded,e.validation_state,e.references.len(),e.rights.len(),e.success.len(),e.informational.len(),e.failure.len(),e.ingredient_statuses.len());
    for s in e.success.iter().chain(&e.informational).chain(&e.failure) {
        println!("REAL_SDK_STATUS_CODE: {}", s.code);
    }
    let actual = adapt(&e).expect("REAL_SDK_POSITIVE_EVIDENCE_OR_MAPPING_FAILED");
    assert_eq!(actual, canonical_success());
    assert_eq!(inspect_fixed(FIXTURE).unwrap(), actual);
    println!(
        "REAL_SDK_FORMAL_OUTPUT: {}",
        String::from_utf8(encode_envelope(&actual, 0).unwrap())
            .unwrap()
            .trim()
    );
    fingerprint(FIXTURE).unwrap();
}

fn chunk(bytes: &[u8], kind: &[u8; 4]) -> (usize, usize) {
    let mut pos = 8;
    while pos + 12 <= bytes.len() {
        let len = u32::from_be_bytes(bytes[pos..pos + 4].try_into().unwrap()) as usize;
        assert!(pos + 12 + len <= bytes.len());
        if &bytes[pos + 4..pos + 8] == kind {
            return (pos, len);
        }
        pos += 12 + len;
    }
    panic!("TEST_CHUNK_MISSING");
}

// Test-only CRC repair keeps the disposable PNG container well-formed while
// changing signed asset bytes. No repository fixture or signing logic changes.
fn repair_crc(bytes: &mut [u8], pos: usize, len: usize) {
    let mut crc = !0u32;
    for byte in &bytes[pos + 4..pos + 8 + len] {
        crc ^= *byte as u32;
        for _ in 0..8 {
            crc = (crc >> 1) ^ (0xedb88320u32 & (0u32.wrapping_sub(crc & 1)));
        }
    }
    bytes[pos + 8 + len..pos + 12 + len].copy_from_slice(&(!crc).to_be_bytes());
}

#[test]
fn real_sdk_modified_image_bytes_fail_positive_binding() {
    fingerprint(FIXTURE).unwrap();
    let mut copy = FIXTURE.to_vec();
    let (pos, len) = chunk(&copy, b"IDAT");
    assert!(len > 4);
    copy[pos + 10] ^= 1;
    repair_crc(&mut copy, pos, len);
    assert_eq!(fingerprint(&copy), Err(Failure::FixtureChanged));
    let e =
        read_sdk_evidence(&copy).expect("MODIFIED_PNG_SDK_READER_FAILED_BEFORE_BINDING_EVIDENCE");
    assert!(e
        .failure
        .iter()
        .any(|s| s.code == "assertion.dataHash.mismatch"));
    assert!(adapt(&e).is_err());
    println!("REAL_SDK_MODIFIED_IMAGE: assertion.dataHash.mismatch; no success output");
    fingerprint(FIXTURE).unwrap();
}

#[test]
fn real_sdk_asset_binding_mismatch_on_changed_header() {
    fingerprint(FIXTURE).unwrap();
    let mut copy = FIXTURE.to_vec();
    let (pos, len) = chunk(&copy, b"IHDR");
    copy[pos + 11] ^= 1;
    repair_crc(&mut copy, pos, len);
    let e = read_sdk_evidence(&copy).expect("HEADER_PNG_SDK_READER_FAILED_BEFORE_BINDING_EVIDENCE");
    assert!(e
        .failure
        .iter()
        .any(|s| s.code == "assertion.dataHash.mismatch"));
    assert!(adapt(&e).is_err());
    println!("REAL_SDK_HEADER_TAMPER: assertion.dataHash.mismatch; no success output");
    fingerprint(FIXTURE).unwrap();
}

#[test]
fn real_sdk_malformed_c2pa_rejected() {
    fingerprint(FIXTURE).unwrap();
    let mut copy = FIXTURE.to_vec();
    let (pos, len) = chunk(&copy, b"caBX");
    copy[pos + 8..pos + 8 + len].fill(0);
    repair_crc(&mut copy, pos, len);
    match read_sdk_evidence(&copy) {
        Err(_) => (),
        Ok(e) => assert!(adapt(&e).is_err()),
    }
    println!("REAL_SDK_MALFORMED_C2PA: no success output");
    fingerprint(FIXTURE).unwrap();
}
