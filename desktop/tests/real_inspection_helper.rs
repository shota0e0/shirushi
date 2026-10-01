//! Explicit CI-only target; compile-time trusted inputs, never runtime discovery.
#![allow(unexpected_cfgs)]
#![cfg(all(windows, debug_assertions, shirushi_real_helper_canary))]
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use shirushi_desktop::{
    inspection_package::{
        parse_manifest, CanaryPackage, PackageFailure, HELPER_BASENAME, MANIFEST_BASENAME,
    },
    inspection_protocol::{HelperOutcome, RequestIdentity, ServiceFailure},
    inspection_supervisor::{inspect, Control},
};
use std::{
    fs,
    io::Write,
    os::windows::fs::OpenOptionsExt,
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
    time::Duration,
};

// CI freezes these build inputs OUTSIDE every tested package tree, before compiling
// this target. No runtime environment variable can override them or helper discovery.
const TRUSTED_MANIFEST: &[u8] = include_bytes!(env!("SHIRUSHI_CANARY_TRUSTED_MANIFEST"));
const TRUSTED_DIGEST: &str = env!("SHIRUSHI_CANARY_MANIFEST_SHA256");
const BUILT_HELPER: &str = env!("SHIRUSHI_CANARY_BUILT_HELPER");
const HELPER_SHA256: &str = env!("SHIRUSHI_CANARY_HELPER_SHA256");
const FIXTURE_SHA256: &str = "558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316";
static NEXT: AtomicU64 = AtomicU64::new(0);
fn sha(raw: &[u8]) -> String {
    format!("{:x}", Sha256::digest(raw))
}
struct Tree(PathBuf);
impl Tree {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "shirushi-real-helper-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::SeqCst)
        ));
        fs::create_dir(&path).unwrap(); // exclusive; never overwrite/reuse a tree
        assert!(path.is_absolute());
        assert_eq!(sha(TRUSTED_MANIFEST), TRUSTED_DIGEST);
        let source = fs::read(BUILT_HELPER).unwrap();
        assert_eq!(sha(&source), HELPER_SHA256);
        fs::write(path.join(HELPER_BASENAME), source).unwrap();
        fs::write(path.join(MANIFEST_BASENAME), TRUSTED_MANIFEST).unwrap();
        Self(path)
    }
    fn preflight(&self) -> Result<CanaryPackage, PackageFailure> {
        CanaryPackage::preflight(&self.0, TRUSTED_DIGEST)
    }
    fn helper(&self) -> PathBuf {
        self.0.join(HELPER_BASENAME)
    }
    fn manifest(&self) -> PathBuf {
        self.0.join(MANIFEST_BASENAME)
    }
    fn revised_manifest(&self, change: impl FnOnce(&mut Value)) -> String {
        let mut m: Value = serde_json::from_slice(TRUSTED_MANIFEST).unwrap();
        change(&mut m);
        let raw = serde_json::to_vec(&m).unwrap();
        // Deliberately independent trusted negative-test configuration, not automatic
        // adoption from the adjacent manifest under test.
        let digest = sha(&raw);
        fs::write(self.manifest(), raw).unwrap();
        digest
    }
}
impl Drop for Tree {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).expect("private test tree cleanup failed");
    }
}
fn rejected(tree: &Tree, expected: &str) {
    assert_eq!(
        CanaryPackage::preflight(&tree.0, expected).unwrap_err(),
        PackageFailure::PackageIntegrityFailure
    );
}
#[test]
fn strict_manifest_closed_fields_types_versions_and_canonical_hash() {
    assert!(parse_manifest(TRUSTED_MANIFEST).is_ok());
    let original: Value = serde_json::from_slice(TRUSTED_MANIFEST).unwrap();
    for field in [
        "schemaVersion",
        "relativePath",
        "size",
        "sha256",
        "protocolVersion",
    ] {
        let mut v = original.clone();
        v.as_object_mut().unwrap().remove(field);
        assert!(
            parse_manifest(&serde_json::to_vec(&v).unwrap()).is_err(),
            "missing {field}"
        );
        let mut v = original.clone();
        v[field] = json!(null);
        assert!(
            parse_manifest(&serde_json::to_vec(&v).unwrap()).is_err(),
            "type {field}"
        );
        let raw = format!(
            "{{\"{field}\":{},{}",
            original[field],
            &String::from_utf8(serde_json::to_vec(&original).unwrap()).unwrap()[1..]
        );
        assert!(parse_manifest(raw.as_bytes()).is_err(), "duplicate {field}");
    }
    for (field, value) in [
        ("schemaVersion", json!(2)),
        ("protocolVersion", json!(2)),
        ("relativePath", json!("../shirushi-inspection-helper.exe")),
        ("relativePath", json!("other.exe")),
        ("size", json!(0)),
        ("size", json!(-1)),
        ("size", json!(1.0)),
        ("size", json!("1")),
        ("sha256", json!(HELPER_SHA256.to_uppercase())),
        ("sha256", json!("x".repeat(64))),
        ("extra", json!(1)),
    ] {
        let mut v = original.clone();
        v[field] = value;
        assert!(
            parse_manifest(&serde_json::to_vec(&v).unwrap()).is_err(),
            "invalid {field}"
        );
    }
    for raw in [b"{}{}".as_slice(), b"null", b"[]", b"{", &vec![b' '; 4097]] {
        assert!(parse_manifest(raw).is_err());
    }
    let mut trailing = TRUSTED_MANIFEST.to_vec();
    trailing.extend_from_slice(b"{}");
    assert!(parse_manifest(&trailing).is_err());
}
#[test]
fn helper_missing() {
    let t = Tree::new();
    fs::remove_file(t.helper()).unwrap();
    rejected(&t, TRUSTED_DIGEST);
}
#[test]
fn manifest_missing() {
    let t = Tree::new();
    fs::remove_file(t.manifest()).unwrap();
    rejected(&t, TRUSTED_DIGEST);
}
#[test]
fn wrong_trusted_manifest_digest() {
    let t = Tree::new();
    rejected(&t, &"0".repeat(64));
}
#[test]
fn manifest_tampered() {
    let t = Tree::new();
    fs::OpenOptions::new()
        .append(true)
        .open(t.manifest())
        .unwrap()
        .write_all(b" ")
        .unwrap();
    rejected(&t, TRUSTED_DIGEST);
}
#[test]
fn helper_tampered_same_size() {
    let t = Tree::new();
    let mut raw = fs::read(t.helper()).unwrap();
    raw[0] ^= 1;
    fs::write(t.helper(), raw).unwrap();
    rejected(&t, TRUSTED_DIGEST);
}
#[test]
fn helper_size_mismatch() {
    let t = Tree::new();
    let digest = t.revised_manifest(|m| m["size"] = json!(m["size"].as_u64().unwrap() + 1));
    rejected(&t, &digest);
}
#[test]
fn malformed_manifest_with_independently_expected_digest() {
    let t = Tree::new();
    let raw = b"{}{}";
    fs::write(t.manifest(), raw).unwrap();
    rejected(&t, &sha(raw));
}
#[test]
fn relative_path_escape_and_wrong_basename() {
    for path in [
        "../shirushi-inspection-helper.exe",
        "fixture/shirushi-inspection-helper.exe",
        "C:/other.exe",
        "other.exe",
    ] {
        let t = Tree::new();
        let digest = t.revised_manifest(|m| m["relativePath"] = json!(path));
        rejected(&t, &digest);
    }
}
#[test]
fn unsupported_protocol() {
    let t = Tree::new();
    let digest = t.revised_manifest(|m| m["protocolVersion"] = json!(2));
    rejected(&t, &digest);
}
#[test]
fn non_regular_helper() {
    let t = Tree::new();
    fs::remove_file(t.helper()).unwrap();
    fs::create_dir(t.helper()).unwrap();
    rejected(&t, TRUSTED_DIGEST);
}
#[test]
fn relative_root_rejected() {
    assert!(
        CanaryPackage::preflight(std::path::Path::new("relative-root"), TRUSTED_DIGEST).is_err()
    );
}
#[test]
fn helper_and_manifest_symlinks_rejected_without_policy_changes() {
    for basename in [HELPER_BASENAME, MANIFEST_BASENAME] {
        let t = Tree::new();
        let leaf = t.0.join(basename);
        let original = t.0.join("original-file");
        fs::rename(&leaf, &original).unwrap();
        // Uses only privileges already granted on the hosted runner. No elevation,
        // Developer Mode, policy mutation, or fallback to an untested PASS.
        std::os::windows::fs::symlink_file(&original, &leaf)
            .expect("hosted runner cannot create the bounded symlink test fixture");
        rejected(&t, TRUSTED_DIGEST);
    }
}
#[test]
fn helper_replaced_before_preflight() {
    let t = Tree::new();
    fs::rename(t.helper(), t.0.join("old.exe")).unwrap();
    fs::write(t.helper(), b"replacement").unwrap();
    rejected(&t, TRUSTED_DIGEST);
}
#[test]
fn verified_handle_denies_write_delete_rename_and_is_retained_by_configuration() {
    let t = Tree::new();
    let package = t.preflight().unwrap();
    let configuration = package.configuration();
    drop(package);
    assert!(fs::OpenOptions::new()
        .write(true)
        .share_mode(3)
        .open(t.helper())
        .is_err());
    assert!(fs::remove_file(t.helper()).is_err());
    assert!(fs::rename(t.helper(), t.0.join("renamed.exe")).is_err());
    assert!(fs::rename(&t.0, t.0.with_extension("renamed")).is_err());
    assert!(fs::OpenOptions::new()
        .write(true)
        .share_mode(3)
        .open(t.manifest())
        .is_err());
    println!(
        "SHARING: write/delete/rename denied; noninheritable guards retained by configuration"
    );
    assert!(!format!("{configuration:?}").contains(t.0.to_str().unwrap()));
    drop(configuration);
    // Windows permits replacement once all verified guards are deliberately released.
    fs::rename(t.helper(), t.0.join("old.exe")).unwrap();
    fs::write(t.helper(), b"changed").unwrap();
    rejected(&t, TRUSTED_DIGEST);
}
fn id() -> RequestIdentity {
    RequestIdentity {
        request: 41,
        generation: 7,
    }
}
#[test]
fn pre_resume_identity_mismatch_never_resumes_and_drains_job() {
    let a = Tree::new();
    let b = Tree::new();
    let package = a.preflight().unwrap();
    let other = b.preflight().unwrap();
    let configuration = package.mismatched_identity_configuration_for_canary(&other);
    let control = Control::new(id());
    let report = inspect(
        &configuration,
        &a.0.join("never-read.png"),
        id(),
        &control,
        Duration::from_secs(30),
        Duration::from_secs(5),
    );
    assert_eq!(
        other.process_identity_failure(),
        Some(PackageFailure::ProcessIdentityUnverified)
    );
    assert!(!other.process_identity_verified());
    assert!(!control.has_started());
    assert_eq!(
        report.outcome,
        HelperOutcome::Failure(ServiceFailure::ServiceUnavailable)
    );
    assert!(report.reaped);
    assert_eq!(report.job_active_processes, Some(0));
    assert_eq!(report.job_total_processes, Some(1));
    println!("PRE_RESUME_MISMATCH: no resume; reaped; Job active=0; no partial result");
}
#[test]
fn real_helper_fixed_fixture_end_to_end() {
    let t = Tree::new();
    fs::create_dir(t.0.join("fixture")).unwrap();
    let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("tests/fixtures/inspection/valid_shirushi.png");
    let raw = fs::read(fixture).unwrap();
    assert_eq!(raw.len(), 319495);
    assert_eq!(sha(&raw), FIXTURE_SHA256);
    let input = t.0.join("fixture/valid_shirushi.png");
    fs::write(&input, raw).unwrap();
    let package = t.preflight().unwrap();
    let configuration = package.configuration();
    let control = Control::new(id());
    // Explicit test-only budget; Production deadline remains BENCHMARK_REQUIRED.
    let report = inspect(
        &configuration,
        &input,
        id(),
        &control,
        Duration::from_secs(30),
        Duration::from_secs(5),
    );
    assert!(package.process_identity_verified());
    assert!(package.process_identity_failure().is_none());
    assert!(report.reaped);
    assert_eq!(report.exit_code, Some(0));
    assert_eq!(report.job_total_processes, Some(1));
    assert_eq!(report.job_active_processes, Some(0));
    let HelperOutcome::Success(ref outer) = report.outcome else {
        panic!("{report:?}")
    };
    assert_eq!(outer["contractVersion"], 2);
    assert_eq!(outer["result"], "LIMITED_INSPECTION");
    assert_eq!(outer["completeness"], "INCOMPLETE");
    let inner = &outer["inspection"];
    assert_eq!(inner["contractVersion"], 1);
    assert_eq!(inner["trustmark"], "NOT_CHECKED");
    assert_eq!(inner["c2pa"]["trustValidated"], false);
    assert_eq!(inner["fullVerificationPerformed"], false);
    assert_eq!(inner["successMotionEligible"], false);
    assert!(!format!("{report:?}").contains(input.to_str().unwrap()));
    println!("REAL_HELPER_FIXED_PATH_CANARY: protocol1/inner1/outer2; LIMITED_INSPECTION/INCOMPLETE; file identity verified; exit0; reaped; Job total1/active0; Python0/c2patool0 (no descendants)");
}
