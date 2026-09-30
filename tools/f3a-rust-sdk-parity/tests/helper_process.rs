//! Real helper on Windows CI only. Synthetic behavior lives in this test binary,
//! never in the Production/development helper command dispatcher.
use serde_json::{json, Value};
use shirushi_f3a_rust_sdk_parity::{
    canonical_success, encode_envelope,
    helper_protocol::{self as protocol, HelperOutcome},
    helper_supervisor::{self as supervisor, Control},
    service::{RequestIdentity, ServiceFailure},
};
use std::{
    io::Cursor,
    path::{Path, PathBuf},
    time::Duration,
};

const ID: RequestIdentity = RequestIdentity {
    request: 41,
    generation: 7,
};
fn success() -> Value {
    json!({"protocolVersion":1,"request":41,"generation":7,"status":"SUCCESS","result":canonical_success()})
}
fn raw(v: &Value) -> Vec<u8> {
    serde_json::to_vec(v).unwrap()
}
fn accepted(v: &Value) -> bool {
    protocol::parse_response(&raw(v), ID, 0).is_ok()
}

#[test]
fn protocol_success_preserves_exact_inner_outer_bytes() {
    match protocol::parse_response(&raw(&success()), ID, 0).unwrap() {
        HelperOutcome::Success(v) => assert_eq!(
            encode_envelope(&v, 0).unwrap(),
            encode_envelope(&canonical_success(), 0).unwrap()
        ),
        _ => panic!("unexpected typed failure"),
    }
}
#[test]
fn request_generation_round_trip() {
    let encoded = protocol::encode_request(ID, Path::new("C:/private/test.png")).unwrap();
    assert_eq!(protocol::parse_request(&encoded).unwrap().identity, ID);
    assert!(accepted(&success()));
}
#[test]
fn wrong_request_rejected() {
    let mut v = success();
    v["request"] = json!(42);
    assert!(!accepted(&v));
}
#[test]
fn wrong_generation_rejected() {
    let mut v = success();
    v["generation"] = json!(8);
    assert!(!accepted(&v));
}
#[test]
fn wrong_protocol_rejected_on_both_sides() {
    let mut v = success();
    v["protocolVersion"] = json!(2);
    assert!(!accepted(&v));
    assert!(protocol::parse_request(br#"{"protocolVersion":2,"request":41,"generation":7,"operation":"INSPECT_LIMITED","inputLocator":"x"}"#).is_err());
}
#[test]
fn malformed_request_unknown_duplicate_trailing_and_numeric_fields_rejected() {
    for r in [b"{".as_slice(),
        br#"{"protocolVersion":1,"operation":"INSPECT_LIMITED","request":1,"generation":1,"inputLocator":"x","extra":1}"#,
        br#"{"protocolVersion":1,"operation":"INSPECT_LIMITED","request":1,"request":1,"generation":1,"inputLocator":"x"}"#,
        br#"{"protocolVersion":1,"operation":"INSPECT_LIMITED","request":1.0,"generation":1,"inputLocator":"x"}"#,
        br#"{"protocolVersion":1,"operation":"INSPECT_LIMITED","request":1,"generation":1,"inputLocator":"x"} {}"#] {
        assert!(protocol::parse_request(r).is_err());
    }
}
#[test]
fn request_overflow_is_bounded_before_parse() {
    let mut c = Cursor::new(vec![b' '; protocol::MAX_REQUEST_BYTES + 32]);
    assert_eq!(
        protocol::read_bounded(&mut c, protocol::MAX_REQUEST_BYTES),
        Err(ServiceFailure::ResourceLimitExceeded)
    );
    assert_eq!(c.position(), (protocol::MAX_REQUEST_BYTES + 1) as u64);
}
#[test]
fn response_malformed_duplicate_unknown_trailing_rejected() {
    assert!(protocol::parse_response(b"{", ID, 0).is_err());
    let mut v = success();
    v["unknown"] = json!(0);
    assert!(!accepted(&v));
    let mut r = raw(&success());
    r.extend_from_slice(b" {}");
    assert!(protocol::parse_response(&r, ID, 0).is_err());
    let r = String::from_utf8(raw(&success())).unwrap().replace(
        "\"protocolVersion\":1",
        "\"protocolVersion\":1,\"protocolVersion\":1",
    );
    assert!(protocol::parse_response(r.as_bytes(), ID, 0).is_err());
    let r = String::from_utf8(raw(&success())).unwrap().replacen(
        "\"contractVersion\":1",
        "\"contractVersion\":1,\"contractVersion\":1",
        1,
    );
    assert!(protocol::parse_response(r.as_bytes(), ID, 0).is_err());
}
#[test]
fn response_overflow_rejected() {
    assert!(
        protocol::parse_response(&vec![b' '; protocol::MAX_RESPONSE_BYTES + 1], ID, 0).is_err()
    );
}
#[test]
fn nonzero_exit_cannot_publish_valid_response() {
    assert!(protocol::parse_response(&raw(&success()), ID, 9).is_err());
}
#[test]
fn typed_failure_closed_code_no_partial_inspection() {
    let mut v = json!({"protocolVersion":1,"request":41,"generation":7,"status":"FAILURE","error":"UNSUPPORTED_FORMAT"});
    assert_eq!(
        protocol::parse_response(&raw(&v), ID, 0).unwrap(),
        HelperOutcome::Failure(ServiceFailure::UnsupportedFormat)
    );
    v["inspection"] = canonical_success();
    assert!(!accepted(&v));
    v.as_object_mut().unwrap().remove("inspection");
    v["error"] = json!("private/path");
    assert!(!accepted(&v));
}
#[test]
fn success_inner_semantics_remain_strict() {
    let mut v = success();
    v["result"]["inspection"]["fullVerificationPerformed"] = json!(true);
    assert!(!accepted(&v));
    v = success();
    v["result"]["result"] = json!("INSPECTION_COMPLETED");
    assert!(!accepted(&v));
}
#[test]
fn cleanup_failure_wins_success_timeout_cancel() {
    for candidate in [
        HelperOutcome::Success(canonical_success()),
        HelperOutcome::Failure(ServiceFailure::Timeout),
        HelperOutcome::Failure(ServiceFailure::Cancelled),
    ] {
        assert_eq!(
            supervisor::accept_after_cleanup(candidate, false),
            HelperOutcome::Failure(ServiceFailure::CleanupFailed)
        );
    }
}
#[test]
fn locator_debug_is_redacted() {
    let r = protocol::parse_request(
        &protocol::encode_request(ID, Path::new("C:/private-marker/test.png")).unwrap(),
    )
    .unwrap();
    assert!(!format!("{r:?}").contains("private-marker"));
}
#[test]
fn helper_invalid_protocol_has_no_output_or_path() {
    let request = br#"{"protocolVersion":44,"inputLocator":"private-marker"}"#;
    let mut output = Vec::new();
    assert!(protocol::serve(&mut Cursor::new(request), &mut output).is_err());
    assert!(output.is_empty());
}

#[cfg(windows)]
mod process {
    use super::*;
    use std::{
        fs,
        io::Write,
        process::Command,
        sync::atomic::{AtomicU64, Ordering},
        thread,
    };
    const HELPER: &str = env!("CARGO_BIN_EXE_shirushi-inspection-helper");
    const DEADLINE: Duration = Duration::from_secs(20); // TEST only, not Production.
    const CLEANUP: Duration = Duration::from_secs(3); // TEST bound.
    const FIXTURE: &[u8] = include_bytes!("../../../tests/fixtures/inspection/valid_shirushi.png");
    struct Copy {
        dir: PathBuf,
        file: PathBuf,
    }
    impl Copy {
        fn new(data: &[u8]) -> Self {
            static SEQUENCE: AtomicU64 = AtomicU64::new(0);
            let dir = std::env::temp_dir().join(format!(
                "shirushi-helper-{}-{}",
                std::process::id(),
                SEQUENCE.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&dir).unwrap();
            let file = dir.join("private-input-marker.png");
            fs::write(&file, data).unwrap();
            Self { dir, file }
        }
    }
    impl Drop for Copy {
        fn drop(&mut self) {
            fs::remove_file(&self.file).unwrap();
            fs::remove_dir(&self.dir).unwrap();
        }
    }
    fn inspect(path: &Path, control: &Control) -> supervisor::ProcessReport {
        supervisor::inspect(Path::new(HELPER), path, ID, control, DEADLINE, CLEANUP)
    }
    fn cleaned(r: &supervisor::ProcessReport) {
        assert!(r.reaped, "{r:?}");
        assert_eq!(r.job_active_processes, Some(0), "{r:?}");
    }
    #[test]
    fn real_helper_fixed_fixture_success_reaped_job_zero() {
        let fixture = fs::canonicalize(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../tests/fixtures/inspection/valid_shirushi.png"),
        )
        .unwrap();
        let r = inspect(&fixture, &Control::new(ID));
        cleaned(&r);
        assert_eq!(r.exit_code, Some(0));
        assert_eq!(r.job_total_processes, Some(1)); // Only helper, no Python/c2patool/other child.
        assert_eq!(r.outcome, HelperOutcome::Success(canonical_success()));
        assert_eq!(r.stderr_bytes, 0);
    }
    #[test]
    fn real_helper_disposable_copy_bound_identity_and_private_paths() {
        let copy = Copy::new(FIXTURE);
        let r = inspect(&copy.file, &Control::new(ID));
        cleaned(&r);
        assert_eq!(r.outcome, HelperOutcome::Success(canonical_success()));
        assert_eq!(r.stderr_bytes, 0);
        assert!(!format!("{r:?}").contains("private-input-marker"));
        if let HelperOutcome::Success(v) = r.outcome {
            assert!(!String::from_utf8(raw(&v))
                .unwrap()
                .contains("private-input-marker"));
        }
    }
    #[test]
    fn real_helper_typed_failure_exit_zero_and_no_private_partial_result() {
        let copy = Copy::new(b"unsupported private-input-marker");
        let r = inspect(&copy.file, &Control::new(ID));
        cleaned(&r);
        assert_eq!(r.exit_code, Some(0));
        assert_eq!(r.stderr_bytes, 0);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::UnsupportedFormat)
        );
        assert!(!format!("{r:?}").contains("private-input-marker"));
    }
    #[test]
    fn real_helper_invalid_locator_typed_failure() {
        let copy = Copy::new(FIXTURE);
        let path = copy.dir.join("private-absent.png");
        let r = inspect(&path, &Control::new(ID));
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::InputUnavailable)
        );
        assert_eq!(r.exit_code, Some(0));
        assert_eq!(r.stderr_bytes, 0);
    }
    #[test]
    fn real_helper_malformed_request_transport_failure_bounded_static_stderr() {
        let r = supervisor::run(
            &mut Command::new(HELPER),
            b"{private-input-marker",
            ID,
            &Control::new(ID),
            DEADLINE,
            CLEANUP,
        );
        cleaned(&r);
        assert_eq!(r.exit_code, Some(2));
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ServiceUnavailable)
        );
        assert!(r.stderr_bytes > 0 && r.stderr_bytes <= protocol::MAX_STDERR_BYTES);
    }
    #[test]
    fn real_helper_late_success_after_cancel_discarded() {
        let copy = Copy::new(FIXTURE);
        let c = Control::new(ID);
        c.cancel();
        let r = inspect(&copy.file, &c);
        cleaned(&r);
        assert_eq!(r.outcome, HelperOutcome::Failure(ServiceFailure::Cancelled));
    }
    #[test]
    fn real_helper_stale_generation_never_published() {
        let copy = Copy::new(FIXTURE);
        let c = Control::new(ID);
        c.set_current(RequestIdentity {
            request: ID.request,
            generation: ID.generation + 1,
        });
        let r = inspect(&copy.file, &c);
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ResultInvalid)
        );
    }
    fn synthetic(mode: &str, control: &Control, deadline: Duration) -> supervisor::ProcessReport {
        let mut cmd = Command::new(std::env::current_exe().unwrap());
        cmd.args(["--exact", "process::synthetic_job_child", "--nocapture"])
            .env("SHIRUSHI_HELPER_TEST_CHILD", mode);
        supervisor::run(&mut cmd, b"{}", ID, control, deadline, CLEANUP)
    }
    #[test]
    fn synthetic_job_child() {
        let Ok(mode) = std::env::var("SHIRUSHI_HELPER_TEST_CHILD") else {
            return;
        };
        match mode.as_str() {
            "hang" => loop {
                thread::sleep(Duration::from_secs(1));
            },
            "abnormal" => std::process::exit(23),
            "malformed" => {
                print!("{{malformed private-marker");
                std::io::stdout().flush().unwrap();
            }
            "overflow" => {
                let _ =
                    std::io::stdout().write_all(&vec![b'x'; protocol::MAX_RESPONSE_BYTES + 8192]);
            }
            "stderr-overflow" => {
                let _ = std::io::stderr().write_all(&vec![b'x'; protocol::MAX_STDERR_BYTES + 8192]);
            }
            _ => panic!("unknown fixed test marker"),
        }
    }
    #[test]
    fn timeout_terminates_contained_child_and_reaps() {
        let r = synthetic("hang", &Control::new(ID), Duration::from_millis(150));
        cleaned(&r);
        assert_eq!(r.outcome, HelperOutcome::Failure(ServiceFailure::Timeout));
    }
    #[test]
    fn cancel_terminates_contained_child_and_reaps() {
        let c = Control::new(ID);
        let cancel = c.clone();
        let t = thread::spawn(move || {
            thread::sleep(Duration::from_millis(150));
            cancel.cancel();
        });
        let r = synthetic("hang", &c, DEADLINE);
        t.join().unwrap();
        cleaned(&r);
        assert_eq!(r.outcome, HelperOutcome::Failure(ServiceFailure::Cancelled));
    }
    #[test]
    fn abnormal_exit_does_not_crash_supervisor() {
        let r = synthetic("abnormal", &Control::new(ID), DEADLINE);
        cleaned(&r);
        assert_eq!(r.exit_code, Some(23));
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ServiceUnavailable)
        );
    }
    #[test]
    fn malformed_process_response_fails_closed() {
        let r = synthetic("malformed", &Control::new(ID), DEADLINE);
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ResultInvalid)
        );
    }
    #[test]
    fn oversized_process_response_kills_and_reaps() {
        let r = synthetic("overflow", &Control::new(ID), DEADLINE);
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ResourceLimitExceeded)
        );
    }
    #[test]
    fn oversized_process_stderr_kills_and_reaps_without_logging() {
        let r = synthetic("stderr-overflow", &Control::new(ID), DEADLINE);
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ResourceLimitExceeded)
        );
    }
    #[test]
    fn request_overflow_never_starts_child() {
        let r = supervisor::run(
            &mut Command::new(HELPER),
            &vec![b' '; protocol::MAX_REQUEST_BYTES + 1],
            ID,
            &Control::new(ID),
            DEADLINE,
            CLEANUP,
        );
        assert_eq!(r.exit_code, None);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ResourceLimitExceeded)
        );
        cleaned(&r);
    }
}
