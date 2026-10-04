//! Opt-in development application dispatch proof with independently frozen inputs.
//! Not a Production/GUI/package test; no runtime discovery or environment opt-in.
#![allow(unexpected_cfgs)]
#![cfg(all(windows, debug_assertions, shirushi_dev_limited_inspection_canary))]
use sha2::{Digest, Sha256};
use shirushi_desktop::limited_inspection::{
    InspectionRequest, LimitedInspectionRuntime, OPERATION,
};
use std::{fs, path::PathBuf};

const FIXTURE_SHA256: &str = "558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316";
// The test is not meaningful unless the normal application dispatch is compiled
// with an explicit helper root and an independently frozen manifest digest.
const ROOT: &str = env!("SHIRUSHI_DEV_INSPECTION_ROOT");
const DIGEST: &str = env!("SHIRUSHI_DEV_INSPECTION_MANIFEST_SHA256");
fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("tests/fixtures/inspection")
        .join(name)
}
fn request(path: PathBuf) -> InspectionRequest {
    InspectionRequest {
        operation: OPERATION.into(),
        input_path: path.to_str().unwrap().into(),
    }
}
fn hash(raw: &[u8]) -> String {
    format!("{:x}", Sha256::digest(raw))
}

#[test]
fn application_dispatch_fixture_success_cleanup_and_sequential_repetition() {
    assert!(PathBuf::from(ROOT).is_absolute());
    assert_eq!(DIGEST.len(), 64);
    let input = fixture("valid_shirushi.png");
    let before = fs::read(&input).unwrap();
    assert_eq!(before.len(), 319495);
    assert_eq!(hash(&before), FIXTURE_SHA256);
    let runtime = LimitedInspectionRuntime::default();
    for _ in 0..2 {
        let outer = runtime.inspect(request(input.clone())).unwrap();
        assert_eq!(outer["contractVersion"], 2);
        assert_eq!(outer["operation"], OPERATION);
        assert_eq!(outer["result"], "LIMITED_INSPECTION");
        assert_eq!(outer["completeness"], "INCOMPLETE");
        assert_eq!(outer["checks"]["c2pa"], "INSPECTED");
        assert_eq!(outer["checks"]["cawg"], "INSPECTED");
        assert_eq!(outer["checks"]["trustmark"], "NOT_CHECKED");
        assert_eq!(outer["inspection"]["source"]["sha256"], FIXTURE_SHA256);
        assert_eq!(outer["inspection"]["fullVerificationPerformed"], false);
        assert_eq!(outer["inspection"]["successMotionEligible"], false);
        assert_eq!(outer["inspection"]["c2pa"]["trustValidated"], false);
        // Application success is only published after supervisor reap/Job-zero.
        assert_eq!(fs::read(&input).unwrap(), before);
    }
    runtime.shutdown();
    assert!(runtime.inspect(request(input)).is_err());
}

#[test]
fn application_dispatch_failure_then_success_preserves_source() {
    let runtime = LimitedInspectionRuntime::default();
    for path in [
        fixture("missing-dev-canary-input.png"),
        fixture("valid_shirushi.jpg"),
        fixture("source_synthetic.png"),
    ] {
        let before = fs::read(&path).ok();
        let value = runtime.inspect(request(path.clone())).unwrap();
        assert_eq!(value["result"], "INSPECTION_FAILED");
        assert_eq!(value["completeness"], "INCOMPLETE");
        assert_eq!(value["checks"]["trustmark"], "NOT_CHECKED");
        assert!(value["errorCode"].as_str().is_some());
        assert!(!value.to_string().contains(path.to_str().unwrap()));
        assert_eq!(fs::read(path).ok(), before);
    }
    assert_eq!(
        runtime
            .inspect(request(fixture("valid_shirushi.png")))
            .unwrap()["result"],
        "LIMITED_INSPECTION"
    );
}
