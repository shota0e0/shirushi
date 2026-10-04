//! Development-only application connection to the accepted Rust supervisor.
//! No runtime helper discovery, Python fallback, or Production package authority.
use crate::inspection_protocol::{HelperOutcome, RequestIdentity, ServiceFailure};
use crate::inspection_supervisor::{Control, ProcessReport};
use crate::protocol::BridgeError;
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

pub const OPERATION: &str = "limited_c2pa_cawg_inspection";
const SUPERVISOR_CLEANUP_SECONDS: u64 = 5;
const SUPERVISOR_CLEANUP_BOUND: Duration = Duration::from_secs(SUPERVISOR_CLEANUP_SECONDS);
// The accepted supervisor may use two cleanup windows and one shared worker-join
// window. Preserve its 5s budget; allow 3 * 5s plus 1s dispatch/scheduling margin.
pub const SHUTDOWN_WAIT_BOUND: Duration = Duration::from_secs(3 * SUPERVISOR_CLEANUP_SECONDS + 1);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShutdownWaitResult {
    Complete,
    TimedOut,
    Unavailable,
}
impl ShutdownWaitResult {
    pub fn code(self) -> &'static str {
        match self {
            Self::Complete => "WORKER_OPERATION_COMPLETED",
            Self::TimedOut => "WORKER_TERMINATION_TIMEOUT",
            Self::Unavailable => "WORKER_TERMINATION_UNAVAILABLE",
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct InspectionRequest {
    pub operation: String,
    pub input_path: String,
}
impl InspectionRequest {
    fn validate(self) -> Result<PathBuf, BridgeError> {
        if self.operation != OPERATION
            || self.input_path.is_empty()
            || self.input_path.len() > 4096
            || self.input_path.contains('\0')
            || !Path::new(&self.input_path).is_absolute()
        {
            return Err(invocation_error("INSPECTION_REQUEST_INVALID"));
        }
        Ok(PathBuf::from(self.input_path))
    }
}
fn invocation_error(code: &'static str) -> BridgeError {
    BridgeError::new(code, "development inspection invocation unavailable")
}

struct RuntimeState {
    next: u64,
    active: Option<Control>,
    shutdown: bool,
    shutdown_deadline: Option<Instant>,
}
struct RuntimeCompletion {
    state: Mutex<RuntimeState>,
    completed: Condvar,
}
#[derive(Clone)]
pub struct LimitedInspectionRuntime(Arc<RuntimeCompletion>);
impl Default for LimitedInspectionRuntime {
    fn default() -> Self {
        Self(Arc::new(RuntimeCompletion {
            state: Mutex::new(RuntimeState {
                next: 0,
                active: None,
                shutdown: false,
                shutdown_deadline: None,
            }),
            completed: Condvar::new(),
        }))
    }
}
impl LimitedInspectionRuntime {
    /// Reserve before scheduling work: concurrent IPC cannot launch two children.
    /// The reservation lives in the blocking worker until cleanup has completed.
    pub(crate) fn reserve(
        &self,
        request: InspectionRequest,
    ) -> Result<InspectionInvocation, BridgeError> {
        #[cfg(not(all(windows, debug_assertions)))]
        {
            let _ = request;
            return Err(invocation_error("DEV_CANARY_ONLY"));
        }
        #[cfg(all(windows, debug_assertions))]
        {
            let input = request.validate()?;
            let mut state = self
                .0
                .state
                .lock()
                .map_err(|_| invocation_error("INSPECTION_UNAVAILABLE"))?;
            if state.shutdown {
                return Err(invocation_error("INSPECTION_UNAVAILABLE"));
            }
            if state.active.is_some() {
                return Err(invocation_error("INSPECTION_BUSY"));
            }
            state.next = state
                .next
                .checked_add(1)
                .ok_or_else(|| invocation_error("INSPECTION_UNAVAILABLE"))?;
            let id = RequestIdentity {
                request: state.next,
                generation: 0,
            };
            let control = Control::new(id);
            state.active = Some(control.clone());
            Ok(InspectionInvocation {
                runtime: self.clone(),
                input,
                id,
                control,
            })
        }
    }
    pub fn shutdown(&self) {
        if let Ok(mut state) = self.0.state.lock() {
            state.shutdown = true;
            if let Some(control) = &state.active {
                control.cancel();
            }
        }
    }
    /// Wait for the owned invocation's operation/cleanup to finish, not for a
    /// reusable Tauri pool thread to terminate. Timeout never implies cleanup.
    /// First wait fixes one deadline shared by repeated ExitRequested/Exit calls.
    pub fn shutdown_and_wait(&self, timeout: Duration) -> ShutdownWaitResult {
        self.shutdown_and_wait_inner(timeout, || {})
    }
    fn shutdown_and_wait_inner(
        &self,
        timeout: Duration,
        mut before_wait: impl FnMut(),
    ) -> ShutdownWaitResult {
        let mut state = match self.0.state.lock() {
            Ok(state) => state,
            Err(_) => return ShutdownWaitResult::Unavailable,
        };
        state.shutdown = true;
        if let Some(control) = &state.active {
            control.cancel();
        }
        if state.active.is_none() {
            return ShutdownWaitResult::Complete;
        }
        let deadline = match state.shutdown_deadline {
            Some(deadline) => deadline,
            None => {
                let Some(deadline) = Instant::now().checked_add(timeout) else {
                    return ShutdownWaitResult::Unavailable;
                };
                state.shutdown_deadline = Some(deadline);
                deadline
            }
        };
        loop {
            if state.active.is_none() {
                return ShutdownWaitResult::Complete;
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return ShutdownWaitResult::TimedOut;
            }
            // Test-only callers may observe this wait boundary without sleeps.
            // Production passes a no-op; wait atomically releases the state lock
            // so the invocation can finish its cleanup, clear active and notify.
            before_wait();
            state = match self.0.completed.wait_timeout(state, remaining) {
                Ok((state, _)) => state,
                Err(_) => return ShutdownWaitResult::Unavailable,
            };
        }
    }
    /// Same application dispatch used by the Tauri command, without WebView/UI.
    pub fn inspect(&self, request: InspectionRequest) -> Result<Value, BridgeError> {
        Ok(self.reserve(request)?.run())
    }
}
pub(crate) struct InspectionInvocation {
    runtime: LimitedInspectionRuntime,
    input: PathBuf,
    id: RequestIdentity,
    control: Control,
}
impl Drop for InspectionInvocation {
    fn drop(&mut self) {
        self.control.cancel();
        if let Ok(mut state) = self.runtime.0.state.lock() {
            state.active = None;
            self.runtime.0.completed.notify_all();
        }
    }
}
fn failed(error: ServiceFailure) -> Value {
    json!({
        "contractVersion": 2, "operation": OPERATION,
        "result": "INSPECTION_FAILED", "completeness": "INCOMPLETE",
        "checks": {"c2pa":"NOT_CHECKED", "cawg":"NOT_CHECKED", "trustmark":"NOT_CHECKED"},
        "errorCode": error.code()
    })
}
fn transport_report(report: ProcessReport) -> Value {
    // Supervisor outcome is already fail-closed on cleanup. Check independently
    // before exposing even a validated success to the application boundary.
    let outcome = if matches!(report.outcome, HelperOutcome::Success(_))
        && (!report.reaped || report.job_active_processes != Some(0))
    {
        HelperOutcome::Failure(ServiceFailure::CleanupFailed)
    } else {
        report.outcome
    };
    match outcome {
        HelperOutcome::Success(value) => value,
        HelperOutcome::Failure(error) => failed(error),
    }
}
impl InspectionInvocation {
    pub(crate) fn run(self) -> Value {
        #[cfg(all(windows, debug_assertions))]
        {
            self.run_with(|input, id, control| {
                use crate::inspection_package::CanaryPackage;
                // Build-authorized inputs only. The browser and process runtime
                // environment cannot override either helper root or digest.
                let root = option_env!("SHIRUSHI_DEV_INSPECTION_ROOT");
                let digest = option_env!("SHIRUSHI_DEV_INSPECTION_MANIFEST_SHA256");
                let (Some(root), Some(digest)) = (root, digest) else {
                    return Err(ServiceFailure::ServiceUnavailable);
                };
                let package = CanaryPackage::preflight(Path::new(root), digest)
                    .map_err(|_| ServiceFailure::ServiceUnavailable)?;
                let mut report = crate::inspection_supervisor::inspect(
                    &package.configuration(),
                    input,
                    id,
                    control,
                    Duration::from_secs(30),
                    SUPERVISOR_CLEANUP_BOUND,
                );
                if report.outcome != HelperOutcome::Failure(ServiceFailure::CleanupFailed)
                    && (package.process_identity_failure().is_some()
                        || (matches!(report.outcome, HelperOutcome::Success(_))
                            && !package.process_identity_verified()))
                {
                    report.outcome = HelperOutcome::Failure(ServiceFailure::ServiceUnavailable);
                }
                Ok(report)
            })
        }
        #[cfg(not(all(windows, debug_assertions)))]
        {
            failed(ServiceFailure::ServiceUnavailable)
        }
    }
    #[cfg(all(windows, debug_assertions))]
    fn run_with(
        self,
        runner: impl FnOnce(&Path, RequestIdentity, &Control) -> Result<ProcessReport, ServiceFailure>,
    ) -> Value {
        let pinned_input = match pin_input(&self.input) {
            Ok(file) => file,
            Err(error) => return failed(error),
        };
        let result = match runner(&self.input, self.id, &self.control) {
            Ok(report) => transport_report(report),
            Err(error) => failed(error),
        };
        // SHARE_READ only for the complete operation. No writes to source bytes.
        drop(pinned_input);
        result
    }
}

#[cfg(all(windows, debug_assertions))]
struct InputGuard {
    _files: Vec<std::fs::File>,
}
#[cfg(all(windows, debug_assertions))]
fn pin_input(input: &Path) -> Result<InputGuard, ServiceFailure> {
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
    use std::path::{Component, Prefix};
    #[link(name = "kernel32")]
    extern "system" {
        fn GetDriveTypeW(root: *const u16) -> u32;
    }
    if input
        .extension()
        .and_then(|v| v.to_str())
        .is_none_or(|v| !v.eq_ignore_ascii_case("png"))
    {
        return Err(ServiceFailure::UnsupportedFormat);
    }
    // Development input stays local-drive/no-ADS/no-reparse; the helper retains
    // authority for approved fixture bytes and Limited Inspection semantics.
    if input.components().count() > 32
        || !matches!(input.components().next(), Some(Component::Prefix(p)) if matches!(p.kind(), Prefix::Disk(_)))
    {
        return Err(ServiceFailure::InputUnavailable);
    }
    let mut drive = PathBuf::from(input.components().next().unwrap().as_os_str());
    drive.push("\\");
    let terminated: Vec<u16> = drive.as_os_str().encode_wide().chain(Some(0)).collect();
    // Query the drive root before opening ancestors. A mapped drive uses a Disk
    // prefix too, but is not an approved local fixed-drive source.
    if unsafe { GetDriveTypeW(terminated.as_ptr()) } != 3 {
        return Err(ServiceFailure::InputUnavailable);
    }
    let mut ancestor = PathBuf::new();
    let mut guards = Vec::new();
    for part in input.components() {
        match part {
            Component::Prefix(_) | Component::RootDir => ancestor.push(part.as_os_str()),
            Component::Normal(name) if !name.to_string_lossy().contains(':') => ancestor.push(name),
            _ => return Err(ServiceFailure::InputUnavailable),
        }
        if ancestor == input {
            break;
        }
        if matches!(part, Component::Prefix(_)) {
            continue;
        }
        let directory = std::fs::OpenOptions::new()
            .read(true)
            .share_mode(3)
            .custom_flags(0x02200000)
            .open(&ancestor)
            .map_err(|_| ServiceFailure::InputUnavailable)?;
        let metadata = directory
            .metadata()
            .map_err(|_| ServiceFailure::InputUnavailable)?;
        if !metadata.is_dir() || metadata.file_attributes() & 0x400 != 0 {
            return Err(ServiceFailure::InputUnavailable);
        }
        guards.push(directory);
    }
    let file = std::fs::OpenOptions::new()
        .read(true)
        .share_mode(1)
        .custom_flags(0x00200000)
        .open(input)
        .map_err(|_| ServiceFailure::InputUnavailable)?;
    let metadata = file
        .metadata()
        .map_err(|_| ServiceFailure::InputUnavailable)?;
    if !metadata.is_file() || metadata.file_attributes() & 0x400 != 0 {
        return Err(ServiceFailure::InputUnavailable);
    }
    guards.push(file);
    Ok(InputGuard { _files: guards })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn request_schema_and_operation_are_closed() {
        for raw in [
            r#"{}"#,
            r#"{"operation":"limited_c2pa_cawg_inspection","inputPath":"x","helperPath":"evil"}"#,
            r#"{"operation":"x","operation":"x","inputPath":"x"}"#,
            r#"{"operation":7,"inputPath":"x"}"#,
        ] {
            assert!(serde_json::from_str::<InspectionRequest>(raw).is_err());
        }
        for path in ["", "relative.png", "x\0.png"] {
            assert!(InspectionRequest {
                operation: OPERATION.into(),
                input_path: path.into()
            }
            .validate()
            .is_err());
        }
        assert!(InspectionRequest {
            operation: "verify".into(),
            input_path: "C:/x.png".into()
        }
        .validate()
        .is_err());
    }
    #[test]
    fn failures_never_upgrade_limited_scope_or_disclose_paths() {
        for error in [
            ServiceFailure::Timeout,
            ServiceFailure::CleanupFailed,
            ServiceFailure::ResultInvalid,
            ServiceFailure::ServiceUnavailable,
            ServiceFailure::InputUnavailable,
            ServiceFailure::UnsupportedFormat,
        ] {
            let value = failed(error);
            assert_eq!(value["result"], "INSPECTION_FAILED");
            assert_eq!(value["completeness"], "INCOMPLETE");
            assert_eq!(value["checks"]["trustmark"], "NOT_CHECKED");
            assert_eq!(value["errorCode"], error.code());
            assert_eq!(value.as_object().unwrap().len(), 6);
        }
    }
    fn report(outcome: HelperOutcome, reaped: bool, active: Option<u32>) -> ProcessReport {
        ProcessReport {
            outcome,
            reaped,
            job_active_processes: active,
            job_total_processes: Some(1),
            exit_code: Some(0),
            stderr_bytes: 0,
            pre_cleanup_wait_state: None,
        }
    }
    #[test]
    fn cleanup_failure_wins_over_success_and_closed_failures_are_preserved() {
        // These values stand for a supervisor-validated value; the accepted
        // protocol tests and real-helper dispatch test prove its full schema.
        let accepted = json!({"result":"LIMITED_INSPECTION","completeness":"INCOMPLETE"});
        assert_eq!(
            transport_report(report(
                HelperOutcome::Success(accepted.clone()),
                true,
                Some(0)
            )),
            accepted
        );
        for (reaped, active) in [(false, Some(0)), (true, Some(1)), (true, None)] {
            assert_eq!(
                transport_report(report(
                    HelperOutcome::Success(accepted.clone()),
                    reaped,
                    active
                ))["errorCode"],
                "CLEANUP_FAILED"
            );
        }
        for error in [
            ServiceFailure::ResultInvalid,
            ServiceFailure::ServiceUnavailable,
            ServiceFailure::Timeout,
        ] {
            assert_eq!(
                transport_report(report(HelperOutcome::Failure(error), true, Some(0))),
                failed(error)
            );
        }
    }
    #[test]
    fn command_registration_and_local_capability_match() {
        let source = include_str!("lib.rs");
        let handlers = source
            .split(".invoke_handler(tauri::generate_handler![")
            .nth(1)
            .unwrap()
            .split("])")
            .next()
            .unwrap();
        assert!(handlers.contains("bridge_inspect_limited"));
        assert!(source.contains("shutdown_and_wait(limited_inspection::SHUTDOWN_WAIT_BOUND)"));
        assert!(include_str!("../build.rs").contains("\"bridge_inspect_limited\""));
        let capability: Value =
            serde_json::from_str(include_str!("../capabilities/main-window.json")).unwrap();
        assert_eq!(capability["local"], true);
        assert_eq!(capability["windows"], json!(["main"]));
        assert!(capability["permissions"]
            .as_array()
            .unwrap()
            .contains(&json!("allow-bridge-inspect-limited")));
        assert!(capability.get("remote").is_none());
    }
    #[cfg(all(windows, debug_assertions))]
    fn request() -> InspectionRequest {
        InspectionRequest {
            operation: OPERATION.into(),
            input_path: Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .join("tests/fixtures/inspection/valid_shirushi.png")
                .to_string_lossy()
                .into_owned(),
        }
    }
    #[cfg(all(windows, debug_assertions))]
    #[test]
    fn one_shared_lease_released_after_failures_and_sequential_calls() {
        let runtime = LimitedInspectionRuntime::default();
        let first = runtime.reserve(request()).unwrap();
        assert_eq!(
            runtime.clone().reserve(request()).err().unwrap().code,
            "INSPECTION_BUSY"
        );
        assert_eq!(
            first.run_with(|_, _, _| Err(ServiceFailure::Timeout))["errorCode"],
            "TIMEOUT"
        );
        assert!(runtime.reserve(request()).is_ok());
        let second = runtime.reserve(request()).unwrap();
        assert_eq!(
            second.run_with(|_, _, _| Err(ServiceFailure::ResultInvalid))["errorCode"],
            "RESULT_INVALID"
        );
        assert!(runtime.reserve(request()).is_ok());
    }
    #[cfg(all(windows, debug_assertions))]
    #[test]
    fn shutdown_cancels_active_lease_and_blocks_new_dispatch() {
        let runtime = LimitedInspectionRuntime::default();
        let lease = runtime.reserve(request()).unwrap();
        runtime.shutdown();
        assert_eq!(
            runtime.reserve(request()).err().unwrap().code,
            "INSPECTION_UNAVAILABLE"
        );
        let report = crate::inspection_supervisor::inspect(
            &crate::inspection_supervisor::FixedExecutable::new(PathBuf::from(
                "C:/never-launched.exe",
            )),
            &lease.input,
            lease.id,
            &lease.control,
            std::time::Duration::from_millis(1),
            std::time::Duration::from_millis(1),
        );
        assert_eq!(
            report.outcome,
            HelperOutcome::Failure(ServiceFailure::Cancelled)
        );
        assert_eq!(report.job_total_processes, Some(0));
        drop(lease);
        assert!(runtime.0.state.lock().unwrap().shutdown);
        assert!(runtime.0.state.lock().unwrap().active.is_none());
    }
    #[cfg(all(windows, debug_assertions))]
    #[test]
    fn request_counter_overflow_never_reuses_identity_or_dispatches() {
        let runtime = LimitedInspectionRuntime::default();
        runtime.0.state.lock().unwrap().next = u64::MAX;
        assert_eq!(
            runtime.reserve(request()).err().unwrap().code,
            "INSPECTION_UNAVAILABLE"
        );
        assert!(runtime.0.state.lock().unwrap().active.is_none());
    }
    #[cfg(all(windows, debug_assertions))]
    #[test]
    fn missing_and_unsupported_inputs_do_not_dispatch() {
        let runtime = LimitedInspectionRuntime::default();
        for (path, error) in [
            (
                "C:/shirushi-definitely-absent-9b0c239c.png",
                "INPUT_UNAVAILABLE",
            ),
            ("C:/unsupported.jpg", "UNSUPPORTED_FORMAT"),
        ] {
            let lease = runtime
                .reserve(InspectionRequest {
                    operation: OPERATION.into(),
                    input_path: path.into(),
                })
                .unwrap();
            let value = lease.run_with(|_, _, _| panic!("invalid input must not launch helper"));
            assert_eq!(value["errorCode"], error);
        }
    }
    #[cfg(all(windows, debug_assertions))]
    #[test]
    fn directory_disguised_as_png_is_rejected_without_dispatch() {
        let path = std::env::temp_dir().join(format!(
            "shirushi-input-directory-{}.png",
            std::process::id()
        ));
        // Exclusive private test leaf; never reuse/overwrite a pre-existing path.
        std::fs::create_dir(&path).unwrap();
        let result = pin_input(&path);
        std::fs::remove_dir(&path).unwrap();
        assert!(matches!(result, Err(ServiceFailure::InputUnavailable)));
    }
    #[test]
    fn shutdown_wait_idle_completes_without_waiting() {
        let runtime = LimitedInspectionRuntime::default();
        assert_eq!(
            runtime.shutdown_and_wait(Duration::ZERO),
            ShutdownWaitResult::Complete
        );
        assert!(runtime.0.state.lock().unwrap().shutdown);
        assert_eq!(SHUTDOWN_WAIT_BOUND, Duration::from_secs(16));
        assert_eq!(SUPERVISOR_CLEANUP_BOUND, Duration::from_secs(5));
    }
    #[test]
    fn shutdown_wait_second_completed_call_is_immediate() {
        let runtime = LimitedInspectionRuntime::default();
        assert_eq!(
            runtime.shutdown_and_wait(SHUTDOWN_WAIT_BOUND),
            ShutdownWaitResult::Complete
        );
        assert_eq!(
            runtime.shutdown_and_wait(Duration::ZERO),
            ShutdownWaitResult::Complete
        );
    }
    #[test]
    fn shutdown_wait_poison_is_unavailable_not_complete() {
        let runtime = LimitedInspectionRuntime::default();
        let poison = runtime.clone();
        assert!(std::thread::spawn(move || {
            let _state = poison.0.state.lock().unwrap();
            panic!("test-only mutex poison");
        })
        .join()
        .is_err());
        assert_eq!(
            runtime.shutdown_and_wait(Duration::ZERO),
            ShutdownWaitResult::Unavailable
        );
    }
    #[cfg(all(windows, debug_assertions))]
    #[test]
    fn shutdown_wait_cancels_owned_control_and_allows_worker_drop() {
        use std::sync::mpsc;
        let runtime = LimitedInspectionRuntime::default();
        let lease = runtime.reserve(request()).unwrap();
        let (entered_tx, entered_rx) = mpsc::channel();
        let worker = std::thread::spawn(move || {
            entered_rx.recv_timeout(Duration::from_secs(5)).unwrap();
            // Cancellation is observed by the existing supervisor claim before
            // any child can launch; owned worker then completes/drops the lease.
            let report = crate::inspection_supervisor::inspect(
                &crate::inspection_supervisor::FixedExecutable::new(PathBuf::from(
                    "C:/never-launched.exe",
                )),
                &lease.input,
                lease.id,
                &lease.control,
                Duration::from_millis(1),
                Duration::from_millis(1),
            );
            assert_eq!(
                report.outcome,
                HelperOutcome::Failure(ServiceFailure::Cancelled)
            );
            assert_eq!(report.job_total_processes, Some(0));
            drop(lease);
        });
        let mut entered_tx = Some(entered_tx);
        assert_eq!(
            runtime.shutdown_and_wait_inner(Duration::from_secs(5), || {
                if let Some(sender) = entered_tx.take() {
                    sender.send(()).unwrap();
                }
            }),
            ShutdownWaitResult::Complete
        );
        worker.join().unwrap();
        assert!(runtime.0.state.lock().unwrap().active.is_none());
    }
    #[cfg(all(windows, debug_assertions))]
    #[test]
    fn shutdown_wait_does_not_complete_before_controlled_cleanup() {
        use std::sync::mpsc;
        let runtime = LimitedInspectionRuntime::default();
        let lease = runtime.reserve(request()).unwrap();
        let (running_tx, running_rx) = mpsc::channel();
        let (cleanup_tx, cleanup_rx) = mpsc::channel();
        let worker = std::thread::spawn(move || {
            lease.run_with(|_, _, _| {
                running_tx.send(()).unwrap();
                cleanup_rx.recv_timeout(Duration::from_secs(5)).unwrap();
                Ok(report(
                    HelperOutcome::Failure(ServiceFailure::Cancelled),
                    true,
                    Some(0),
                ))
            })
        });
        running_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        let (waiting_tx, waiting_rx) = mpsc::channel();
        let (completed_tx, completed_rx) = mpsc::channel();
        let waiter_runtime = runtime.clone();
        let waiter = std::thread::spawn(move || {
            let mut waiting_tx = Some(waiting_tx);
            let result = waiter_runtime.shutdown_and_wait_inner(Duration::from_secs(5), || {
                if let Some(sender) = waiting_tx.take() {
                    sender.send(()).unwrap();
                }
            });
            completed_tx.send(result).unwrap();
        });
        waiting_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        assert!(matches!(
            completed_rx.try_recv(),
            Err(mpsc::TryRecvError::Empty)
        ));
        assert_eq!(
            runtime.reserve(request()).err().unwrap().code,
            "INSPECTION_UNAVAILABLE"
        );
        cleanup_tx.send(()).unwrap();
        assert_eq!(worker.join().unwrap()["errorCode"], "CANCELLED");
        assert_eq!(
            completed_rx.recv_timeout(Duration::from_secs(5)).unwrap(),
            ShutdownWaitResult::Complete
        );
        waiter.join().unwrap();
    }
    #[cfg(all(windows, debug_assertions))]
    #[test]
    fn shutdown_wait_timeout_preserves_active_lease_and_cannot_restart_budget() {
        let runtime = LimitedInspectionRuntime::default();
        let lease = runtime.reserve(request()).unwrap();
        assert_eq!(
            runtime.shutdown_and_wait(Duration::from_millis(10)),
            ShutdownWaitResult::TimedOut
        );
        assert!(runtime.0.state.lock().unwrap().active.is_some());
        let deadline = runtime.0.state.lock().unwrap().shutdown_deadline;
        // A second exit event must not acquire another 16s wait after timeout.
        assert_eq!(
            runtime.shutdown_and_wait(SHUTDOWN_WAIT_BOUND),
            ShutdownWaitResult::TimedOut
        );
        assert_eq!(runtime.0.state.lock().unwrap().shutdown_deadline, deadline);
        drop(lease);
        assert_eq!(
            runtime.shutdown_and_wait(Duration::ZERO),
            ShutdownWaitResult::Complete
        );
    }
    #[cfg(all(windows, debug_assertions))]
    #[test]
    fn shutdown_wait_rejects_new_reservation_after_idle_completion() {
        let runtime = LimitedInspectionRuntime::default();
        assert_eq!(
            runtime.shutdown_and_wait(Duration::ZERO),
            ShutdownWaitResult::Complete
        );
        assert_eq!(
            runtime.reserve(request()).err().unwrap().code,
            "INSPECTION_UNAVAILABLE"
        );
    }
    #[cfg(not(all(windows, debug_assertions)))]
    #[test]
    fn production_and_non_windows_dispatch_have_no_helper_authority() {
        let request = InspectionRequest {
            operation: OPERATION.into(),
            input_path: "C:/fixture.png".into(),
        };
        assert_eq!(
            LimitedInspectionRuntime::default()
                .inspect(request)
                .unwrap_err()
                .code,
            "DEV_CANARY_ONLY"
        );
    }
}
