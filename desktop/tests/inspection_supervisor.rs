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
    // Test-only observation. These separate anonymous pipes never carry an
    // inspection response and never reference the target stdout/stderr objects.
    mod target_handles {
        use super::*;
        use std::{
            ffi::c_void,
            fs::File,
            os::windows::io::{AsRawHandle, FromRawHandle, IntoRawHandle, OwnedHandle},
            ptr::{null, null_mut},
            time::Instant,
        };
        use windows_sys::Win32::{
            Foundation::{
                CloseHandle, CompareObjectHandles, DuplicateHandle, GetHandleInformation,
                DUPLICATE_SAME_ACCESS, HANDLE, HANDLE_FLAG_INHERIT,
            },
            System::Threading::GetCurrentProcess,
        };
        const REPORT: &str = "SHIRUSHI_TEST_TARGET_HANDLE_REPORT";
        const PROBE: &str = "SHIRUSHI_TEST_TARGET_HANDLE_PROBE";
        #[link(name = "kernel32")]
        extern "system" {
            fn GetStdHandle(kind: u32) -> HANDLE;
            fn GetFileType(handle: HANDLE) -> u32;
            fn CreatePipe(
                read: *mut HANDLE,
                write: *mut HANDLE,
                security: *const c_void,
                size: u32,
            ) -> i32;
            fn PeekNamedPipe(
                pipe: HANDLE,
                buffer: *mut c_void,
                size: u32,
                read: *mut u32,
                available: *mut u32,
                left: *mut u32,
            ) -> i32;
        }
        struct Pipe {
            read: File,
            write: Option<OwnedHandle>,
        }
        fn duplicate(process: HANDLE, handle: HANDLE, inherit: bool) -> Option<OwnedHandle> {
            let mut copy = null_mut();
            // SAFETY: source is the current process or our owned Child handle;
            // returned duplicate belongs to this process. Never CLOSE_SOURCE.
            if unsafe {
                DuplicateHandle(
                    process,
                    handle,
                    GetCurrentProcess(),
                    &mut copy,
                    0,
                    i32::from(inherit),
                    DUPLICATE_SAME_ACCESS,
                )
            } == 0
            {
                return None;
            }
            Some(unsafe { OwnedHandle::from_raw_handle(copy) })
        }
        fn pipe() -> Pipe {
            let mut read = null_mut();
            let mut write = null_mut();
            // SAFETY: fresh outputs; null security makes both handles noninheritable.
            assert!(
                unsafe { CreatePipe(&mut read, &mut write, null(), 1024) } != 0,
                "diagnostic pipe unavailable"
            );
            let read = unsafe { File::from_raw_handle(read) };
            let write = unsafe { OwnedHandle::from_raw_handle(write) };
            // Only a new diagnostic write handle is inheritable. Target flags
            // and all target handles remain untouched.
            let inherited = duplicate(unsafe { GetCurrentProcess() }, write.as_raw_handle(), true)
                .expect("diagnostic write handle unavailable");
            Pipe {
                read,
                write: Some(inherited),
            }
        }
        fn record<const N: usize>(read: &mut File, bound: Duration) -> Option<[u8; N]> {
            let start = Instant::now();
            loop {
                let mut available = 0;
                // SAFETY: one reader, no pending IO on this private diagnostic
                // pipe. No target pipe is peeked, read, written or closed here.
                if unsafe {
                    PeekNamedPipe(
                        read.as_raw_handle(),
                        null_mut(),
                        0,
                        null_mut(),
                        &mut available,
                        null_mut(),
                    )
                } == 0
                {
                    return None;
                }
                if available == N as u32 {
                    let mut bytes = [0; N];
                    read.read_exact(&mut bytes).ok()?;
                    return bytes.iter().all(|b| *b <= 1).then_some(bytes);
                }
                if available > N as u32 || start.elapsed() >= bound {
                    return None;
                }
                thread::sleep(Duration::from_millis(1));
            }
        }
        fn send(handle: HANDLE, bytes: &[u8]) {
            let copy = duplicate(unsafe { GetCurrentProcess() }, handle, false)
                .expect("diagnostic channel unavailable");
            // Fixed records <=14 bytes fit in the fresh 1024-byte channel.
            assert!(
                File::from(copy).write_all(bytes).is_ok(),
                "diagnostic record failed"
            );
        }
        #[derive(Debug, Clone, Copy)]
        pub struct Facts {
            valid: bool,
            inheritable: bool,
            pipe: bool,
        }
        fn facts(handle: HANDLE) -> Facts {
            let mut flags = 0;
            // Borrowed handle only: these APIs do not mutate or close it.
            let valid = unsafe { GetHandleInformation(handle, &mut flags) } != 0;
            Facts {
                valid,
                inheritable: valid && flags & HANDLE_FLAG_INHERIT != 0,
                pipe: valid && unsafe { GetFileType(handle) } == 3,
            }
        }
        #[derive(Debug)]
        pub struct Proof {
            parent_stdout: Facts,
            parent_stderr: Facts,
            descendant_stdout_valid: bool,
            descendant_stdout_pipe: bool,
            descendant_stderr_valid: bool,
            descendant_stderr_pipe: bool,
            stdout_remote_duplicate: bool,
            stderr_remote_duplicate: bool,
            stdout_same_object: bool,
            stderr_same_object: bool,
        }
        impl Proof {
            pub fn classification(&self) -> &'static str {
                let p = self.parent_stdout;
                let e = self.parent_stderr;
                if !p.valid || !e.valid || !p.pipe || !e.pipe {
                    "OTHER_HANDLE_STATE"
                } else if !p.inheritable || !e.inheritable {
                    "TARGET_HANDLES_NOT_INHERITABLE"
                } else if self.descendant_stdout_valid
                    && self.descendant_stdout_pipe
                    && self.descendant_stderr_valid
                    && self.descendant_stderr_pipe
                    && self.stdout_same_object
                    && self.stderr_same_object
                {
                    "TARGET_PIPE_HANDLES_INHERITED"
                } else if self.stdout_same_object != self.stderr_same_object {
                    "OTHER_HANDLE_STATE"
                } else if !self.descendant_stdout_valid
                    && !self.descendant_stderr_valid
                    && !self.stdout_remote_duplicate
                    && !self.stderr_remote_duplicate
                {
                    // Failed queries alone cannot distinguish absence from an
                    // API failure. Do not turn them into a negative proof.
                    "DIAGNOSTIC_INSUFFICIENT"
                } else if self.descendant_stdout_valid
                    && self.descendant_stderr_valid
                    && (!self.stdout_remote_duplicate || !self.stderr_remote_duplicate)
                {
                    "HANDLE_VALUE_VALID_BUT_OBJECT_IDENTITY_UNPROVEN"
                } else {
                    "OTHER_HANDLE_STATE"
                }
            }
        }
        pub struct Channel(Pipe);
        impl Channel {
            pub fn new() -> Self {
                assert!(
                    std::env::var_os(REPORT).is_none(),
                    "unexpected diagnostic configuration"
                );
                let channel = Self(pipe());
                // Private test-to-parent communication; never formatted into Debug.
                std::env::set_var(
                    REPORT,
                    (channel.0.write.as_ref().unwrap().as_raw_handle() as usize).to_string(),
                );
                channel
            }
            pub fn receive(&mut self) -> Option<Proof> {
                drop(self.0.write.take());
                let v = record::<14>(&mut self.0.read, Duration::ZERO)?;
                let f = |i| Facts {
                    valid: v[i] != 0,
                    inheritable: v[i + 1] != 0,
                    pipe: v[i + 2] != 0,
                };
                Some(Proof {
                    parent_stdout: f(0),
                    parent_stderr: f(3),
                    descendant_stdout_valid: v[6] != 0,
                    descendant_stdout_pipe: v[7] != 0,
                    descendant_stderr_valid: v[8] != 0,
                    descendant_stderr_pipe: v[9] != 0,
                    stdout_remote_duplicate: v[10] != 0,
                    stderr_remote_duplicate: v[11] != 0,
                    stdout_same_object: v[12] != 0,
                    stderr_same_object: v[13] != 0,
                })
            }
        }
        impl Drop for Channel {
            fn drop(&mut self) {
                std::env::remove_var(REPORT);
            }
        }
        pub struct ParentProbe {
            stdout: HANDLE,
            stderr: HANDLE,
            stdout_facts: Facts,
            stderr_facts: Facts,
            report: HANDLE,
            channel: Pipe,
        }
        impl ParentProbe {
            pub fn capture() -> Option<Self> {
                let value = std::env::var(REPORT).ok()?;
                let report = value.parse::<usize>().ok()? as HANDLE;
                let stdout = unsafe { GetStdHandle(-11i32 as u32) };
                let stderr = unsafe { GetStdHandle(-12i32 as u32) };
                Some(Self {
                    stdout,
                    stderr,
                    stdout_facts: facts(stdout),
                    stderr_facts: facts(stderr),
                    report,
                    channel: pipe(),
                })
            }
            pub fn configure(&self, command: &mut std::process::Command) {
                command.env(
                    PROBE,
                    format!(
                        "{}:{}:{}",
                        self.stdout as usize,
                        self.stderr as usize,
                        self.channel.write.as_ref().unwrap().as_raw_handle() as usize
                    ),
                );
            }
            pub fn finish(mut self, child: &std::process::Child) {
                let v = record::<4>(&mut self.channel.read, Duration::from_secs(5))
                    .expect("descendant diagnostic record unavailable");
                // Source values stay open in the hanging descendant. Hold the
                // returned duplicates only across the object comparison, never
                // across parent exit or supervisor cleanup.
                let compare = |target| {
                    let copy = duplicate(child.as_raw_handle(), target, false);
                    let same = copy
                        .as_ref()
                        .map(|c| unsafe { CompareObjectHandles(target, c.as_raw_handle()) } != 0)
                        .unwrap_or(false);
                    let duplicated = copy.is_some();
                    if let Some(copy) = copy {
                        // Close this diagnostic duplicate, never the original
                        // parent/descendant target, before any later wait/exit.
                        assert!(
                            unsafe { CloseHandle(copy.into_raw_handle()) } != 0,
                            "diagnostic duplicate close failed"
                        );
                    }
                    (duplicated, same)
                };
                let out = compare(self.stdout);
                let err = compare(self.stderr);
                let p = self.stdout_facts;
                let e = self.stderr_facts;
                send(
                    self.report,
                    &[
                        p.valid as u8,
                        p.inheritable as u8,
                        p.pipe as u8,
                        e.valid as u8,
                        e.inheritable as u8,
                        e.pipe as u8,
                        v[0],
                        v[1],
                        v[2],
                        v[3],
                        out.0 as u8,
                        err.0 as u8,
                        out.1 as u8,
                        err.1 as u8,
                    ],
                );
            }
        }
        pub fn descendant_probe() {
            let Ok(value) = std::env::var(PROBE) else {
                return;
            };
            // Private raw values are never asserted/formatted/logged.
            let fields: Option<Vec<usize>> = value.split(':').map(|s| s.parse().ok()).collect();
            let fields = fields
                .filter(|v| v.len() == 3)
                .expect("invalid diagnostic request");
            let out = facts(fields[0] as HANDLE);
            let err = facts(fields[1] as HANDLE);
            send(
                fields[2] as HANDLE,
                &[
                    out.valid as u8,
                    out.pipe as u8,
                    err.valid as u8,
                    err.pipe as u8,
                ],
            );
            // Original hang/retention behavior continues after this observation.
        }
    }
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
            "hang" => {
                target_handles::descendant_probe();
                loop {
                    thread::sleep(Duration::from_secs(1));
                }
            }
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
                let probe = target_handles::ParentProbe::capture();
                let mut descendant = std::process::Command::new(std::env::current_exe().unwrap());
                descendant
                    .env("SHIRUSHI_DESKTOP_SYNTHETIC_CHILD", "hang")
                    .stdin(std::process::Stdio::piped())
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null());
                if let Some(probe) = &probe {
                    probe.configure(&mut descendant);
                }
                let mut child = descendant.spawn().unwrap();
                child.stdin.take().unwrap().write_all(&input).unwrap();
                if let Some(probe) = probe {
                    probe.finish(&child);
                }
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
        let mut diagnostic = target_handles::Channel::new();
        let r = run(SyntheticBehavior::LeakyChild, &Control::new(ID), DEADLINE);
        if let Some(proof) = diagnostic.receive() {
            println!("TARGET_HANDLE_REPORT: {proof:?}");
            println!("TARGET_HANDLE_CLASSIFICATION: {}", proof.classification());
        } else {
            println!("TARGET_HANDLE_CLASSIFICATION: DIAGNOSTIC_INSUFFICIENT");
        }
        cleaned(&r);
        if r.outcome == HelperOutcome::Failure(ServiceFailure::Timeout) {
            assert!(r.pre_cleanup_wait_state.is_some(), "{r:?}");
            if let Some(state) = r.pre_cleanup_wait_state {
                assert!(!state.stdout_done || state.stdout_reader_finished, "{r:?}");
                assert!(!state.stderr_done || state.stderr_reader_finished, "{r:?}");
            }
        }
        assert_eq!(
            r.outcome,
            HelperOutcome::Failure(ServiceFailure::CleanupFailed),
            "{r:?}"
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
