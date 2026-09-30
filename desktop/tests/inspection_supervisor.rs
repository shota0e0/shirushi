//! Synthetic Desktop process fixtures only; no c2pa helper, Python or real images.
use serde_json::{json, Value};
use shirushi_desktop::{
    inspection_protocol::{self as protocol, HelperOutcome, RequestIdentity, ServiceFailure},
    inspection_supervisor::{self as supervisor, Control, FixedExecutable, SyntheticBehavior},
};
use std::{path::Path, time::Duration};
const ID: RequestIdentity = RequestIdentity {
    request: 41,
    generation: 7,
};
const PRIVATE: &str = "C:/private-marker/input.png";
fn success() -> Value {
    json!({"protocolVersion":1,"request":41,"generation":7,"status":"SUCCESS","result":golden()})
}
fn raw(v: &Value) -> Vec<u8> {
    serde_json::to_vec(v).unwrap()
}
fn accepted(v: &Value) -> bool {
    protocol::parse_response(&raw(v), ID, 0).is_ok()
}
fn golden() -> Value {
    json!({
        "contractVersion": 2, "operation": "limited_c2pa_cawg_inspection",
        "result": "LIMITED_INSPECTION", "completeness": "INCOMPLETE",
        "checks": {"c2pa":"INSPECTED", "cawg":"INSPECTED", "trustmark":"NOT_CHECKED"},
        "inspection": {
            "contract":"shirushi-limited-inspection", "contractVersion":1,
            "overall":"LIMITED_INSPECTION", "reasonCode":"LIMITED_SCOPE",
            "trustmark":"NOT_CHECKED", "fullVerificationPerformed":false,
            "successMotionEligible":false,
            "source":{"sha256":"558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316", "size":319495, "format":"PNG"},
            "c2pa":{"state":"INSPECTED", "presence":"PRESENT", "parse":true,
                "assertionDigestsValid":true, "assetBindingValid":true,
                "signature":"PREVIEW", "trustValidated":false},
            "cawg":{"state":"INSPECTED", "presence":"PRESENT",
                "aiTrainingUse":"NOT_WANTED", "aiInferenceUse":"NOT_WANTED"}
        }
    })
}

#[test]
fn request_encoding_and_private_debug() {
    let bytes = protocol::encode_request(ID, Path::new(PRIVATE)).unwrap();
    let value: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(
        value,
        json!({"protocolVersion":1,"operation":"INSPECT_LIMITED","request":41,"generation":7,"inputLocator":PRIVATE})
    );
    let request = protocol::parse_request(&bytes).unwrap();
    assert_eq!(request.identity, ID);
    assert!(!format!("{request:?}").contains("private-marker"));
}
#[test]
fn success_decode_outer_v2_exact_value_and_serialization_preserved() {
    let parsed = protocol::parse_response(&raw(&success()), ID, 0).unwrap();
    match parsed {
        HelperOutcome::Success(v) => {
            assert_eq!(v, golden());
            assert_eq!(raw(&v), raw(&golden()));
        }
        _ => panic!("failure"),
    }
}
#[test]
fn failure_decode_closed_code_and_private_debug() {
    let failure = json!({"protocolVersion":1,"request":41,"generation":7,"status":"FAILURE","error":"UNSUPPORTED_FORMAT"});
    let parsed = protocol::parse_response(&raw(&failure), ID, 0).unwrap();
    assert_eq!(
        parsed,
        HelperOutcome::Failure(ServiceFailure::UnsupportedFormat)
    );
    assert!(!format!("{parsed:?}").contains("private-marker"));
}
#[test]
fn duplicate_response_field_rejected() {
    let r = String::from_utf8(raw(&success())).unwrap().replace(
        "\"protocolVersion\":1",
        "\"protocolVersion\":1,\"protocolVersion\":1",
    );
    assert!(protocol::parse_response(r.as_bytes(), ID, 0).is_err());
}
#[test]
fn nested_duplicate_rejected() {
    let r = String::from_utf8(raw(&success())).unwrap().replace(
        "\"trustValidated\":false",
        "\"trustValidated\":false,\"trustValidated\":false",
    );
    assert!(protocol::parse_response(r.as_bytes(), ID, 0).is_err());
}
#[test]
fn unknown_response_and_nested_field_rejected() {
    let mut v = success();
    v["unknown"] = json!("private-marker");
    assert!(!accepted(&v));
    v = success();
    v["result"]["inspection"]["unknown"] = json!(0);
    assert!(!accepted(&v));
}
#[test]
fn request_duplicate_unknown_float_trailing_rejected() {
    for r in [
 br#"{"protocolVersion":1,"operation":"INSPECT_LIMITED","request":1,"request":1,"generation":1,"inputLocator":"x"}"#.as_slice(),
 br#"{"protocolVersion":1,"operation":"INSPECT_LIMITED","request":1,"generation":1,"inputLocator":"x","unknown":0}"#,
 br#"{"protocolVersion":1,"operation":"INSPECT_LIMITED","request":1.0,"generation":1,"inputLocator":"x"}"#,
 br#"{"protocolVersion":1,"operation":"INSPECT_LIMITED","request":1,"generation":1,"inputLocator":"x"} {}"#
 ] { assert!(protocol::parse_request(r).is_err()); }
}
#[test]
fn malformed_trailing_nonfinite_rejected() {
    for r in [b"{".as_slice(), b"NaN", b"{} {}"] {
        assert!(protocol::parse_response(r, ID, 0).is_err());
    }
}
#[test]
fn overflow_rejected_at_limit() {
    assert!(
        protocol::parse_response(&vec![b' '; protocol::MAX_RESPONSE_BYTES + 1], ID, 0).is_err()
    );
    let mut reader = std::io::Cursor::new(vec![0; protocol::MAX_RESPONSE_BYTES + 32]);
    assert_eq!(
        protocol::read_bounded(&mut reader, protocol::MAX_RESPONSE_BYTES),
        Err(ServiceFailure::ResourceLimitExceeded)
    );
    assert_eq!(reader.position(), (protocol::MAX_RESPONSE_BYTES + 1) as u64);
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
fn wrong_protocol_and_unknown_status_rejected() {
    let mut v = success();
    v["protocolVersion"] = json!(2);
    assert!(!accepted(&v));
    v = success();
    v["status"] = json!("COMPLETE");
    assert!(!accepted(&v));
}
#[test]
fn failure_unknown_code_partial_result_rejected() {
    let mut v = json!({"protocolVersion":1,"request":41,"generation":7,"status":"FAILURE","error":"private-marker"});
    assert!(!accepted(&v));
    v["error"] = json!("TIMEOUT");
    v["result"] = golden();
    assert!(!accepted(&v));
    v.as_object_mut().unwrap().remove("result");
    v["inspection"] = golden();
    assert!(!accepted(&v));
}
#[test]
fn success_inner_semantics_and_old_outer_rejected() {
    for (key, value) in [
        ("fullVerificationPerformed", json!(true)),
        ("successMotionEligible", json!(true)),
        ("trustmark", json!("CHECKED")),
        ("contractVersion", json!(2)),
    ] {
        let mut v = success();
        v["result"]["inspection"][key] = value;
        assert!(!accepted(&v));
    }
    let mut v = success();
    v["result"]["inspection"]["c2pa"]["trustValidated"] = json!(true);
    assert!(!accepted(&v));
    for (key, value) in [
        ("result", json!("INSPECTION_COMPLETED")),
        ("completeness", json!("LIMITED")),
        ("contractVersion", json!(1)),
    ] {
        let mut v = success();
        v["result"][key] = value;
        assert!(!accepted(&v));
    }
}
#[test]
fn success_requires_accepted_exit() {
    assert!(protocol::parse_response(&raw(&success()), ID, 23).is_err());
}
#[test]
fn cleanup_precedence_over_success_timeout_cancel() {
    for candidate in [
        HelperOutcome::Success(golden()),
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
fn result_and_formal_errors_do_not_leak_input_path() {
    let result = protocol::parse_response(&raw(&success()), ID, 0).unwrap();
    assert!(!format!("{result:?}").contains("private-marker"));
    if let HelperOutcome::Success(value) = result {
        assert!(!String::from_utf8(raw(&value))
            .unwrap()
            .contains("private-marker"));
    }
    let configuration = FixedExecutable::new(std::path::PathBuf::from(PRIVATE));
    assert!(!format!("{configuration:?}").contains("private-marker"));
}

#[cfg(windows)]
mod process {
    use super::*;
    use std::{
        io::{Read, Write},
        thread,
    };
    const DEADLINE: Duration = Duration::from_secs(20);
    const CLEANUP: Duration = Duration::from_secs(3);
    fn run(mode: SyntheticBehavior, c: &Control, deadline: Duration) -> supervisor::ProcessReport {
        let config = FixedExecutable::synthetic_test_child(std::env::current_exe().unwrap(), mode);
        supervisor::inspect(&config, Path::new(PRIVATE), ID, c, deadline, CLEANUP)
    }
    fn cleaned(r: &supervisor::ProcessReport) {
        assert!(r.reaped, "{r:?}");
        assert_eq!(r.job_active_processes, Some(0), "{r:?}");
    }
    fn wait_started(c: &Control) {
        let start = std::time::Instant::now();
        while !c.has_started() {
            if start.elapsed() >= Duration::from_secs(5) {
                c.cancel();
                panic!("synthetic contained child did not start");
            }
            thread::sleep(Duration::from_millis(1));
        }
    }
    // Runs before libtest writes framing bytes. Test-only CRT hook, no Production
    // entry or helper synthetic behavior. Normal tests have no marker and continue.
    #[used]
    #[link_section = ".CRT$XCU"]
    static SYNTHETIC_ENTRY: extern "C" fn() = synthetic_entry;
    extern "C" fn synthetic_entry() {
        let Ok(mode) = std::env::var("SHIRUSHI_DESKTOP_SYNTHETIC_CHILD") else {
            return;
        };
        let execution = std::panic::catch_unwind(|| child(&mode));
        std::process::exit(if execution.is_ok() { 0 } else { 99 });
    }
    fn child(mode: &str) {
        let mut input = Vec::new();
        std::io::stdin().read_to_end(&mut input).unwrap();
        let request: Value = serde_json::from_slice(&input).unwrap();
        assert_eq!(request["operation"], "INSPECT_LIMITED");
        let mut value = success();
        match mode {
            "hang" => loop {
                thread::sleep(Duration::from_secs(1));
            },
            "abnormal" => std::process::exit(23),
            "malformed" => {
                let _ = std::io::stdout().write_all(b"{private-marker");
                return;
            }
            "overflow" => {
                let _ =
                    std::io::stdout().write_all(&vec![b'x'; protocol::MAX_RESPONSE_BYTES + 8192]);
                return;
            }
            "stderr-overflow" => {
                let _ = std::io::stderr().write_all(&vec![b'x'; protocol::MAX_STDERR_BYTES + 8192]);
                return;
            }
            "failure" => {
                value = json!({"protocolVersion":1,"request":41,"generation":7,"status":"FAILURE","error":"UNSUPPORTED_FORMAT"})
            }
            "wrong-request" => value["request"] = json!(42),
            "wrong-generation" => value["generation"] = json!(8),
            "delayed-success" => {
                let _ = std::io::stderr().write_all(b"READY_PRIVATE_MARKER");
                std::io::stderr().flush().unwrap();
                thread::sleep(Duration::from_millis(400));
            }
            "leaky-child" => {
                // Retain a contained descendant beyond parent exit. It receives EOF on stdin.
                let mut descendant = std::process::Command::new(std::env::current_exe().unwrap());
                descendant
                    .env("SHIRUSHI_DESKTOP_SYNTHETIC_CHILD", "hang")
                    .stdin(std::process::Stdio::piped())
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null());
                let mut child = descendant.spawn().unwrap();
                child.stdin.take().unwrap().write_all(&input).unwrap();
            }
            "success" => {
                let _ = std::io::stderr().write_all(b"private-marker stderr");
            }
            _ => panic!("unknown fixed synthetic behavior"),
        }
        std::io::stdout().write_all(&raw(&value)).unwrap();
        std::io::stdout().flush().unwrap();
    }
    #[test]
    fn valid_success_reaped_job_empty() {
        let r = run(SyntheticBehavior::Success, &Control::new(ID), DEADLINE);
        cleaned(&r);
        assert_eq!(r.exit_code, Some(0));
        assert_eq!(r.job_total_processes, Some(1));
        assert_eq!(r.outcome, HelperOutcome::Success(golden()));
    }
    #[test]
    fn typed_failure_reaped_job_empty() {
        let r = run(SyntheticBehavior::Failure, &Control::new(ID), DEADLINE);
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::UnsupportedFormat)
        );
    }
    #[test]
    fn malformed_process_response_rejected() {
        let r = run(SyntheticBehavior::Malformed, &Control::new(ID), DEADLINE);
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ResultInvalid)
        );
        assert!(!format!("{r:?}").contains("private-marker"));
    }
    #[test]
    fn wrong_request_process_response_rejected() {
        let r = run(SyntheticBehavior::WrongRequest, &Control::new(ID), DEADLINE);
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ResultInvalid)
        );
    }
    #[test]
    fn wrong_generation_process_response_rejected() {
        let r = run(
            SyntheticBehavior::WrongGeneration,
            &Control::new(ID),
            DEADLINE,
        );
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ResultInvalid)
        );
    }
    #[test]
    fn oversized_process_output_killed_reaped_job_empty() {
        let r = run(SyntheticBehavior::Overflow, &Control::new(ID), DEADLINE);
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ResourceLimitExceeded)
        );
    }
    #[test]
    fn stderr_not_forwarded_on_success() {
        let r = run(SyntheticBehavior::Success, &Control::new(ID), DEADLINE);
        cleaned(&r);
        assert!(r.stderr_bytes > 0);
        assert_eq!(r.outcome, HelperOutcome::Success(golden()));
        assert!(!format!("{r:?}").contains("private-marker"));
    }
    #[test]
    fn stderr_overflow_bounded_killed_without_forwarding() {
        let r = run(
            SyntheticBehavior::StderrOverflow,
            &Control::new(ID),
            DEADLINE,
        );
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ResourceLimitExceeded)
        );
    }
    #[test]
    fn timeout_cleanup_reaps_job_zero() {
        let r = run(
            SyntheticBehavior::Hang,
            &Control::new(ID),
            Duration::from_millis(150),
        );
        cleaned(&r);
        assert_eq!(r.outcome, HelperOutcome::Failure(ServiceFailure::Timeout));
    }
    #[test]
    fn abnormal_child_exit_rejected() {
        let r = run(SyntheticBehavior::Abnormal, &Control::new(ID), DEADLINE);
        cleaned(&r);
        assert_eq!(r.exit_code, Some(23));
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ServiceUnavailable)
        );
    }
    #[test]
    fn stale_completion_discarded() {
        let c = Control::new(ID);
        c.set_current(RequestIdentity {
            request: 42,
            generation: 8,
        });
        let r = run(SyntheticBehavior::Success, &c, DEADLINE);
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ResultInvalid)
        );
        assert_eq!(r.exit_code, None);
    }
    #[test]
    fn cancel_then_late_success_discarded() {
        let c = Control::new(ID);
        c.cancel();
        let r = run(SyntheticBehavior::DelayedSuccess, &c, DEADLINE);
        cleaned(&r);
        assert_eq!(r.outcome, HelperOutcome::Failure(ServiceFailure::Cancelled));
        assert_eq!(r.exit_code, None);
    }
    #[test]
    fn running_cancel_terminates_and_reaps() {
        let c = Control::new(ID);
        let cancel = c.clone();
        let t = thread::spawn(move || {
            wait_started(&cancel);
            cancel.cancel();
        });
        let r = run(SyntheticBehavior::Hang, &c, DEADLINE);
        t.join().unwrap();
        cleaned(&r);
        assert_eq!(r.outcome, HelperOutcome::Failure(ServiceFailure::Cancelled));
    }
    #[test]
    fn running_generation_change_discards_completion() {
        let c = Control::new(ID);
        let stale = c.clone();
        let t = thread::spawn(move || {
            wait_started(&stale);
            stale.set_current(RequestIdentity {
                request: 42,
                generation: 8,
            });
        });
        let r = run(SyntheticBehavior::DelayedSuccess, &c, DEADLINE);
        t.join().unwrap();
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::ResultInvalid)
        );
    }
    #[test]
    fn descendant_cleanup_failure_supersedes_valid_response() {
        let r = run(SyntheticBehavior::LeakyChild, &Control::new(ID), DEADLINE);
        cleaned(&r);
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::CleanupFailed)
        );
        assert_eq!(r.job_total_processes, Some(2));
    }
    #[test]
    fn completed_identity_cannot_be_published_twice() {
        let c = Control::new(ID);
        let first = run(SyntheticBehavior::Success, &c, DEADLINE);
        cleaned(&first);
        assert_eq!(first.outcome, HelperOutcome::Success(golden()));
        let late = run(SyntheticBehavior::Success, &c, DEADLINE);
        cleaned(&late);
        assert_eq!(
            late.outcome,
            HelperOutcome::Failure(ServiceFailure::ResultInvalid)
        );
        assert_eq!(late.exit_code, None);
    }
    #[test]
    fn running_cancel_then_delayed_success_discarded() {
        let c = Control::new(ID);
        let cancel = c.clone();
        let t = thread::spawn(move || {
            wait_started(&cancel);
            cancel.cancel();
        });
        let r = run(SyntheticBehavior::DelayedSuccess, &c, DEADLINE);
        t.join().unwrap();
        cleaned(&r);
        assert_eq!(r.outcome, HelperOutcome::Failure(ServiceFailure::Cancelled));
    }
    #[test]
    fn timeout_invalidates_request_against_late_success() {
        let c = Control::new(ID);
        let first = run(SyntheticBehavior::Hang, &c, Duration::from_millis(150));
        cleaned(&first);
        assert_eq!(
            first.outcome,
            HelperOutcome::Failure(ServiceFailure::Timeout)
        );
        let late = run(SyntheticBehavior::Success, &c, DEADLINE);
        cleaned(&late);
        assert_eq!(
            late.outcome,
            HelperOutcome::Failure(ServiceFailure::ResultInvalid)
        );
        assert_eq!(late.exit_code, None);
    }
    #[test]
    fn simultaneous_duplicate_identity_is_rejected() {
        let c = Control::new(ID);
        let running = c.clone();
        let worker = thread::spawn(move || run(SyntheticBehavior::Hang, &running, DEADLINE));
        wait_started(&c);
        let duplicate = run(SyntheticBehavior::Success, &c, DEADLINE);
        assert_eq!(
            duplicate.outcome,
            HelperOutcome::Failure(ServiceFailure::ResultInvalid)
        );
        assert_eq!(duplicate.exit_code, None);
        c.cancel();
        let first = worker.join().unwrap();
        cleaned(&first);
        assert_eq!(first.job_total_processes, Some(1));
        assert_eq!(
            first.outcome,
            HelperOutcome::Failure(ServiceFailure::Cancelled)
        );
    }
    #[test]
    fn aba_identity_change_never_revives_old_completion_or_invalidates_new_lease() {
        let c = Control::new(ID);
        let old_control = c.clone();
        let old =
            thread::spawn(move || run(SyntheticBehavior::DelayedSuccess, &old_control, DEADLINE));
        wait_started(&c);
        c.set_current(RequestIdentity {
            request: 42,
            generation: 8,
        });
        c.set_current(ID);
        let newer_control = c.clone();
        let newer = thread::spawn(move || run(SyntheticBehavior::Hang, &newer_control, DEADLINE));
        wait_started(&c);
        let stale = old.join().unwrap();
        cleaned(&stale);
        assert_eq!(
            stale.outcome,
            HelperOutcome::Failure(ServiceFailure::ResultInvalid)
        );
        assert!(
            !newer.is_finished(),
            "old lease invalidated newer contained operation"
        );
        c.cancel();
        let current = newer.join().unwrap();
        cleaned(&current);
        assert_eq!(
            current.outcome,
            HelperOutcome::Failure(ServiceFailure::Cancelled)
        );
    }
    #[test]
    fn preflight_failure_invalidates_identity_before_any_child_start() {
        for invalid_locator in [false, true] {
            let c = Control::new(ID);
            let configuration = if invalid_locator {
                FixedExecutable::synthetic_test_child(
                    std::env::current_exe().unwrap(),
                    SyntheticBehavior::Success,
                )
            } else {
                FixedExecutable::new(std::path::PathBuf::from("relative-helper.exe"))
            };
            let path = if invalid_locator {
                Path::new("")
            } else {
                Path::new(PRIVATE)
            };
            let first = supervisor::inspect(&configuration, path, ID, &c, DEADLINE, CLEANUP);
            assert!(matches!(first.outcome, HelperOutcome::Failure(_)));
            assert_eq!(first.exit_code, None);
            assert!(!c.has_started());
            let retry = run(SyntheticBehavior::Success, &c, DEADLINE);
            cleaned(&retry);
            assert_eq!(
                retry.outcome,
                HelperOutcome::Failure(ServiceFailure::ResultInvalid)
            );
            assert_eq!(retry.exit_code, None);
            assert!(!c.has_started());
        }
    }
}
#[test]
fn synthetic_job_child() { /* CRT hook intercepts only fixed child marker. */
}
