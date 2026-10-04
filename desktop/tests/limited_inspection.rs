//! Opt-in development application dispatch proof with independently frozen inputs.
//! No Production/GUI launch; controlled paths are test inputs, not discovery.
#![allow(unexpected_cfgs)]
#![cfg(all(windows, debug_assertions, shirushi_dev_limited_inspection_canary))]
use sha2::{Digest, Sha256};
use shirushi_desktop::limited_inspection::{
    development_package_preflight_for_canary, InspectionRequest, LimitedInspectionRuntime,
    ShutdownWaitResult, OPERATION, SHUTDOWN_WAIT_BOUND,
};
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    os::windows::fs::MetadataExt,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex,
    },
};

const FIXTURE_SHA256: &str = "558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316";
// Explicit build fixture paths are TEST inputs only, not application discovery.
const DIGEST: &str = env!("SHIRUSHI_DEV_INSPECTION_MANIFEST_SHA256");
const TRUSTED: &str = env!("SHIRUSHI_CANARY_TRUSTED_MANIFEST");
const BUILT_HELPER: &str = env!("SHIRUSHI_CANARY_BUILT_HELPER");
const HELPER_SHA: &str = env!("SHIRUSHI_CANARY_HELPER_SHA256");
const HELPER: &str = "shirushi-inspection-helper.exe";
const MANIFEST: &str = "inspection-helper.manifest.json";
const DESKTOP: &str = "shirushi-desktop.exe";
static TEST_LOCK: Mutex<()> = Mutex::new(());
static NEXT: AtomicU64 = AtomicU64::new(0);

fn bounded_read(path: &Path, bound: u64) -> Vec<u8> {
    let m = fs::symlink_metadata(path).unwrap();
    assert!(m.is_file() && m.file_attributes() & 0x400 == 0 && m.len() <= bound);
    let mut raw = Vec::new();
    fs::File::open(path)
        .unwrap()
        .take(bound + 1)
        .read_to_end(&mut raw)
        .unwrap();
    assert_eq!(raw.len() as u64, m.len());
    raw
}
fn trusted_inputs() -> (Vec<u8>, Vec<u8>) {
    let manifest = bounded_read(Path::new(TRUSTED), 4096);
    let helper = bounded_read(Path::new(BUILT_HELPER), 268435456);
    assert_eq!(hash(&manifest), DIGEST);
    assert_eq!(hash(&helper), HELPER_SHA);
    let value: serde_json::Value = serde_json::from_slice(&manifest).unwrap();
    assert_eq!(value["relativePath"], HELPER);
    assert_eq!(value["size"], helper.len() as u64);
    assert_eq!(value["sha256"], HELPER_SHA);
    (helper, manifest)
}
fn create_file(path: &Path, bytes: &[u8]) {
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .unwrap()
        .write_all(bytes)
        .unwrap();
}
struct OwnedInputs {
    root: PathBuf,
    files: Vec<PathBuf>,
    owned_root: bool,
}
impl OwnedInputs {
    fn siblings() -> Self {
        let root = std::env::current_exe()
            .unwrap()
            .parent()
            .unwrap()
            .to_path_buf();
        let (helper, manifest) = trusted_inputs();
        let mut owned = Self {
            root,
            files: Vec::new(),
            owned_root: false,
        };
        for (name, raw) in [(HELPER, helper), (MANIFEST, manifest)] {
            let path = owned.root.join(name);
            create_file(&path, &raw);
            owned.files.push(path);
        }
        assert!(
            development_package_preflight_for_canary(&std::env::current_exe().unwrap()).is_ok()
        );
        owned
    }
    fn flat() -> Self {
        let root = std::env::temp_dir().join(format!(
            "shirushi-exe-binding-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::SeqCst)
        ));
        fs::create_dir(&root).unwrap();
        let (helper, manifest) = trusted_inputs();
        let mut owned = Self {
            root,
            files: Vec::new(),
            owned_root: true,
        };
        // Controlled executable-path leaf; never executed. CI opt-in below uses
        // the actual assembled Desktop bytes instead of this resolver fixture.
        for (name, raw) in [
            (DESKTOP, b"controlled test executable leaf".to_vec()),
            (HELPER, helper),
            (MANIFEST, manifest),
        ] {
            let path = owned.root.join(name);
            create_file(&path, &raw);
            owned.files.push(path);
        }
        owned
    }
    fn executable(&self) -> PathBuf {
        self.root.join(DESKTOP)
    }
}
impl Drop for OwnedInputs {
    fn drop(&mut self) {
        let m = fs::symlink_metadata(&self.root).unwrap();
        assert!(m.is_dir() && m.file_attributes() & 0x400 == 0);
        for path in &self.files {
            match fs::symlink_metadata(path) {
                Ok(m) => {
                    assert!(m.is_file() && m.file_attributes() & 0x400 == 0);
                    fs::remove_file(path).unwrap();
                }
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => panic!("owned test cleanup failed: {:?}", e.kind()),
            }
        }
        if self.owned_root {
            fs::remove_dir(&self.root).unwrap();
        }
    }
}
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
    let _lock = TEST_LOCK.lock().unwrap();
    let _siblings = OwnedInputs::siblings();
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
    assert_eq!(
        runtime.shutdown_and_wait(SHUTDOWN_WAIT_BOUND),
        ShutdownWaitResult::Complete
    );
    assert_eq!(
        runtime.shutdown_and_wait(SHUTDOWN_WAIT_BOUND),
        ShutdownWaitResult::Complete
    );
    assert!(runtime.inspect(request(input)).is_err());
}

#[test]
fn application_dispatch_failure_then_success_preserves_source() {
    let _lock = TEST_LOCK.lock().unwrap();
    let _siblings = OwnedInputs::siblings();
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
    assert_eq!(
        runtime.shutdown_and_wait(SHUTDOWN_WAIT_BOUND),
        ShutdownWaitResult::Complete
    );
}

#[test]
fn development_resolver_flat_move_and_missing_inputs() {
    let _lock = TEST_LOCK.lock().unwrap();
    let a = OwnedInputs::flat();
    let b = OwnedInputs::flat();
    for package in [&a, &b] {
        assert!(development_package_preflight_for_canary(&package.executable()).is_ok());
        assert_eq!(
            hash(&bounded_read(&package.root.join(MANIFEST), 4096)),
            DIGEST
        );
        assert_eq!(
            hash(&bounded_read(&package.root.join(HELPER), 268435456)),
            HELPER_SHA
        );
    }
    for executable in [
        PathBuf::new(),
        PathBuf::from("relative.exe"),
        a.root.join("missing.exe"),
        a.root.join("absent-parent").join(DESKTOP),
    ] {
        assert!(development_package_preflight_for_canary(&executable).is_err());
    }
    fs::remove_file(a.root.join(HELPER)).unwrap();
    assert!(development_package_preflight_for_canary(&a.executable()).is_err());
}

#[test]
fn development_resolver_ignores_cwd_runtime_environment_and_path() {
    let _lock = TEST_LOCK.lock().unwrap();
    let package = OwnedInputs::flat();
    let elsewhere = OwnedInputs::flat();
    let cwd = std::env::current_dir().unwrap();
    let keys = [
        "SHIRUSHI_DEV_INSPECTION_ROOT",
        "SHIRUSHI_DEV_INSPECTION_MANIFEST_SHA256",
        "PATH",
    ];
    let prior: Vec<_> = keys.iter().map(|key| std::env::var_os(key)).collect();
    struct Restore {
        cwd: PathBuf,
        keys: [&'static str; 3],
        values: Vec<Option<std::ffi::OsString>>,
    }
    impl Drop for Restore {
        fn drop(&mut self) {
            std::env::set_current_dir(&self.cwd).unwrap();
            for (key, value) in self.keys.iter().zip(&self.values) {
                if let Some(value) = value {
                    std::env::set_var(key, value);
                } else {
                    std::env::remove_var(key);
                }
            }
        }
    }
    let _restore = Restore {
        cwd,
        keys,
        values: prior,
    };
    std::env::set_current_dir(&elsewhere.root).unwrap();
    std::env::set_var(keys[0], &elsewhere.root);
    std::env::set_var(keys[1], "0".repeat(64));
    std::env::set_var(keys[2], &elsewhere.root);
    fs::write(elsewhere.root.join(HELPER), b"fake PATH helper").unwrap();
    assert!(development_package_preflight_for_canary(&package.executable()).is_ok());
    assert_eq!(
        hash(&bounded_read(&package.root.join(MANIFEST), 4096)),
        DIGEST
    );
}

#[test]
fn development_resolver_raw_manifest_and_helper_tamper_fail_closed() {
    let _lock = TEST_LOCK.lock().unwrap();
    for change in 0..5 {
        let mut package = OwnedInputs::flat();
        assert!(development_package_preflight_for_canary(&package.executable()).is_ok());
        match change {
            0 => fs::write(package.root.join(MANIFEST), b"{}").unwrap(),
            1 => fs::write(package.root.join(HELPER), b"tampered helper").unwrap(),
            2 => {
                let raw = bounded_read(&package.root.join(MANIFEST), 4096);
                let value: serde_json::Value = serde_json::from_slice(&raw).unwrap();
                let equivalent = serde_json::to_vec_pretty(&value).unwrap();
                assert_ne!(equivalent, raw);
                fs::write(package.root.join(MANIFEST), equivalent).unwrap();
            }
            3 => {
                let raw = bounded_read(&package.root.join(MANIFEST), 4096);
                let mut value: serde_json::Value = serde_json::from_slice(&raw).unwrap();
                value["relativePath"] = serde_json::json!("../other-helper.exe");
                fs::write(
                    package.root.join(MANIFEST),
                    serde_json::to_vec(&value).unwrap(),
                )
                .unwrap();
            }
            _ => {
                let other = package.root.join("other-helper.exe");
                fs::rename(package.root.join(HELPER), &other).unwrap();
                package.files.push(other);
            }
        }
        assert!(development_package_preflight_for_canary(&package.executable()).is_err());
    }
}

#[test]
fn development_resolver_reparse_root_rejected_when_testable() {
    let _lock = TEST_LOCK.lock().unwrap();
    let package = OwnedInputs::flat();
    let alias = package.root.with_file_name(format!(
        "shirushi-binding-link-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::SeqCst)
    ));
    match std::os::windows::fs::symlink_dir(&package.root, &alias) {
        Ok(()) => {
            assert!(development_package_preflight_for_canary(&alias.join(DESKTOP)).is_err());
            fs::remove_dir(alias).unwrap(); // Owned link only; no recursive target cleanup.
        }
        Err(e) if e.raw_os_error() == Some(1314) => {
            println!("REPARSE_ROOT_TEST: UNPROVEN_PRIVILEGE_UNAVAILABLE")
        }
        Err(e) => panic!("link fixture unavailable: {:?}", e.kind()),
    }
}

#[test]
#[ignore = "Explicit CI-only post-assembly proof; no runtime application override"]
fn real_assembled_package_move_preflight() {
    let _lock = TEST_LOCK.lock().unwrap();
    let a = PathBuf::from(
        std::env::var_os("SHIRUSHI_CANARY_PACKAGE_A").expect("explicit package A test input"),
    );
    let b = PathBuf::from(
        std::env::var_os("SHIRUSHI_CANARY_PACKAGE_B").expect("explicit package B test input"),
    );
    assert!(a.is_absolute() && b.is_absolute());
    assert_ne!(a, b);
    for root in [&a, &b] {
        assert!(development_package_preflight_for_canary(&root.join(DESKTOP)).is_ok());
        assert_eq!(
            bounded_read(&root.join(MANIFEST), 4096),
            bounded_read(Path::new(TRUSTED), 4096)
        );
        assert_eq!(hash(&bounded_read(&root.join(MANIFEST), 4096)), DIGEST);
        assert_eq!(
            hash(&bounded_read(&root.join(HELPER), 268435456)),
            HELPER_SHA
        );
    }
    for name in [DESKTOP, HELPER, MANIFEST] {
        assert_eq!(
            bounded_read(&a.join(name), 268435456),
            bounded_read(&b.join(name), 268435456)
        );
    }
    println!("REAL_MOVED_PACKAGE_PREFLIGHT: BOTH_ROOTS_PASS; COMPILED_RAW_DIGEST_MATCH; HELPER_IDENTITY_MATCH; NO_UI_LAUNCH");
}
