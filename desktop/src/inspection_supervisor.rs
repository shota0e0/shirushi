//! Desktop-owned helper lifecycle skeleton; no SDK parsing or frontend command.
//! Explicit deadlines are caller/test values; Production remains BENCHMARK_REQUIRED.
use crate::inspection_protocol::{
    self as helper_protocol, HelperOutcome, RequestIdentity, ServiceFailure, MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES, MAX_STDERR_BYTES,
};
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};

#[derive(Clone)]
pub struct Control(Arc<Mutex<ControlState>>);
struct ControlState {
    current: RequestIdentity,
    cancelled: bool,
    terminal: bool,
    running: bool,
    started: bool,
    epoch: u64,
    unavailable: bool,
}
impl Control {
    pub fn new(id: RequestIdentity) -> Self {
        Self(Arc::new(Mutex::new(ControlState {
            current: id,
            cancelled: false,
            terminal: false,
            running: false,
            started: false,
            epoch: 0,
            unavailable: false,
        })))
    }
    pub fn cancel(&self) {
        if let Ok(mut c) = self.0.lock() {
            c.cancelled = true;
        }
    }
    pub fn set_current(&self, id: RequestIdentity) {
        if let Ok(mut c) = self.0.lock() {
            if c.current != id {
                let Some(epoch) = c.epoch.checked_add(1) else {
                    c.unavailable = true;
                    return;
                };
                c.epoch = epoch;
                c.current = id;
                c.cancelled = false;
                c.terminal = false;
                c.running = false;
                c.started = false;
            }
        }
    }
    fn failure(&self, id: RequestIdentity, epoch: u64) -> Option<ServiceFailure> {
        match self.0.lock() {
            Ok(c) if c.unavailable => Some(ServiceFailure::ServiceUnavailable),
            Ok(c) if c.cancelled => Some(ServiceFailure::Cancelled),
            Ok(c) if c.terminal => Some(ServiceFailure::ResultInvalid),
            Ok(c) if c.current != id || c.epoch != epoch => Some(ServiceFailure::ResultInvalid),
            Ok(_) => None,
            Err(_) => Some(ServiceFailure::ServiceUnavailable),
        }
    }
    fn claim(&self, id: RequestIdentity) -> Result<RequestLease<'_>, ServiceFailure> {
        let mut c = self
            .0
            .lock()
            .map_err(|_| ServiceFailure::ServiceUnavailable)?;
        if c.unavailable {
            return Err(ServiceFailure::ServiceUnavailable);
        }
        if c.cancelled {
            return Err(ServiceFailure::Cancelled);
        }
        if c.current != id || c.terminal || c.running {
            return Err(ServiceFailure::ResultInvalid);
        }
        c.running = true;
        Ok(RequestLease {
            control: self,
            identity: id,
            epoch: c.epoch,
        })
    }
    /// Path-free lifecycle observation for deterministic development tests.
    pub fn has_started(&self) -> bool {
        self.0.lock().map(|c| c.started).unwrap_or(false)
    }
    /// A cancelled/stale controller may not publish a helper's staged Add.
    /// Hold the existing control lock through the no-clobber publication.
    pub(crate) fn publish_if_current<T>(
        &self,
        id: RequestIdentity,
        publish: impl FnOnce() -> Result<T, ServiceFailure>,
    ) -> Result<T, ServiceFailure> {
        let state = self
            .0
            .lock()
            .map_err(|_| ServiceFailure::ServiceUnavailable)?;
        if state.unavailable {
            return Err(ServiceFailure::ServiceUnavailable);
        }
        if state.cancelled {
            return Err(ServiceFailure::Cancelled);
        }
        if state.current != id || !state.terminal || state.running {
            return Err(ServiceFailure::ResultInvalid);
        }
        publish()
    }
}
struct RequestLease<'a> {
    control: &'a Control,
    identity: RequestIdentity,
    epoch: u64,
}
#[cfg(test)]
mod publication_tests {
    use super::*;
    use std::cell::Cell;

    #[test]
    fn publication_requires_completed_current_uncancelled_lease() {
        let id = RequestIdentity {
            request: 1,
            generation: 1,
        };
        let control = Control::new(id);
        let called = Cell::new(false);
        assert_eq!(
            control.publish_if_current(id, || {
                called.set(true);
                Ok(())
            }),
            Err(ServiceFailure::ResultInvalid)
        );
        let lease = control.claim(id).unwrap();
        assert_eq!(
            control.publish_if_current(id, || {
                called.set(true);
                Ok(())
            }),
            Err(ServiceFailure::ResultInvalid)
        );
        assert!(!called.get());
        drop(lease);
        assert_eq!(
            control.publish_if_current(id, || {
                called.set(true);
                Ok(())
            }),
            Ok(())
        );
        assert!(called.get());
        called.set(false);
        control.cancel();
        assert_eq!(
            control.publish_if_current(id, || {
                called.set(true);
                Ok(())
            }),
            Err(ServiceFailure::Cancelled)
        );
        assert!(!called.get());
        control.set_current(RequestIdentity {
            request: 2,
            generation: 1,
        });
        assert_eq!(
            control.publish_if_current(id, || {
                called.set(true);
                Ok(())
            }),
            Err(ServiceFailure::ResultInvalid)
        );
        assert!(!called.get());
    }
}
impl Drop for RequestLease<'_> {
    fn drop(&mut self) {
        if let Ok(mut c) = self.control.0.lock() {
            if c.current == self.identity && c.epoch == self.epoch {
                c.running = false;
                c.terminal = true;
            }
        }
    }
}
/// Supervisor-observed completion flags at a main-loop timeout, before cleanup.
/// Diagnostic evidence only: not serialized into any helper or UI contract.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PreCleanupWaitState {
    pub stdout_done: bool,
    pub stderr_done: bool,
    pub stdin_done: bool,
    pub parent_exit_seen: bool,
    /// Bounded read returned and reached its pre-send marker; not proof of EOF.
    pub stdout_reader_finished: bool,
    pub stderr_reader_finished: bool,
}

#[derive(Debug)]
pub struct ProcessReport {
    pub outcome: HelperOutcome,
    pub reaped: bool,
    pub job_active_processes: Option<u32>,
    pub job_total_processes: Option<u32>,
    pub exit_code: Option<i32>,
    /// Raw stderr is bounded then discarded, never exposed in diagnostics.
    pub stderr_bytes: usize,
    /// None unless the main wait loop itself selected its deadline timeout.
    pub pre_cleanup_wait_state: Option<PreCleanupWaitState>,
}
/// Trusted host/test injection only. No frontend-supplied executable or env override.
/// This slice deliberately does not establish Production packaging/discovery.
pub struct FixedExecutable {
    executable: std::path::PathBuf,
    arguments: Vec<std::ffi::OsString>,
    test_marker: Option<String>,
    #[cfg(all(windows, debug_assertions))]
    verified_helper: Option<Arc<crate::inspection_package::VerifiedHelper>>,
}
impl std::fmt::Debug for FixedExecutable {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("FixedExecutable(REDACTED)")
    }
}
impl FixedExecutable {
    pub fn new(executable: std::path::PathBuf) -> Self {
        Self {
            executable,
            arguments: Vec::new(),
            test_marker: None,
            #[cfg(all(windows, debug_assertions))]
            verified_helper: None,
        }
    }
    /// Explicit development-only synthetic process fixture configuration.
    /// Fixed test harness arguments/marker, never shell command passthrough.
    pub fn synthetic_test_child(executable: std::path::PathBuf, marker: SyntheticBehavior) -> Self {
        Self {
            executable,
            arguments: vec![
                "--exact".into(),
                "synthetic_job_child".into(),
                "--nocapture".into(),
            ],
            test_marker: Some(marker.code().to_owned()),
            #[cfg(all(windows, debug_assertions))]
            verified_helper: None,
        }
    }
    #[cfg(all(windows, debug_assertions))]
    pub(crate) fn verified_canary(
        executable: std::path::PathBuf,
        guard: Arc<crate::inspection_package::VerifiedHelper>,
    ) -> Self {
        Self {
            executable,
            arguments: Vec::new(),
            test_marker: None,
            verified_helper: Some(guard),
        }
    }
    #[cfg(windows)]
    fn command(&self) -> std::process::Command {
        let mut c = std::process::Command::new(&self.executable);
        c.args(&self.arguments);
        c.env_remove("SHIRUSHI_DESKTOP_SYNTHETIC_CHILD");
        if let Some(marker) = &self.test_marker {
            c.env("SHIRUSHI_DESKTOP_SYNTHETIC_CHILD", marker);
        }
        c
    }
}
/// Closed development fixture behavior; not a Production runtime override.
#[derive(Clone, Copy)]
pub enum SyntheticBehavior {
    Success,
    Failure,
    Malformed,
    Hang,
    Abnormal,
    WrongRequest,
    WrongGeneration,
    Overflow,
    StderrOverflow,
    DelayedSuccess,
    LeakyChild,
}
impl SyntheticBehavior {
    fn code(self) -> &'static str {
        match self {
            Self::Success => "success",
            Self::Failure => "failure",
            Self::Malformed => "malformed",
            Self::Hang => "hang",
            Self::Abnormal => "abnormal",
            Self::WrongRequest => "wrong-request",
            Self::WrongGeneration => "wrong-generation",
            Self::Overflow => "overflow",
            Self::StderrOverflow => "stderr-overflow",
            Self::DelayedSuccess => "delayed-success",
            Self::LeakyChild => "leaky-child",
        }
    }
}
/// Cleanup failure always wins over any candidate success, timeout or cancellation.
pub fn accept_after_cleanup(candidate: HelperOutcome, cleanup_proven: bool) -> HelperOutcome {
    if cleanup_proven {
        candidate
    } else {
        HelperOutcome::Failure(ServiceFailure::CleanupFailed)
    }
}

#[cfg(windows)]
mod windows {
    use super::*;
    use std::{
        ffi::c_void,
        io::Write,
        mem::{size_of, zeroed},
        os::windows::{io::AsRawHandle, process::CommandExt},
        path::Path,
        process::{Child, Command, Stdio},
        ptr::{null, null_mut},
        sync::{
            atomic::{AtomicBool, Ordering},
            mpsc,
        },
        thread,
        time::Instant,
    };

    // Minimal kernel32 ABI, checked against the already-cached windows-sys0.61.2
    // generated definitions. No dependency edge, lock change or new build script.
    // These structs deliberately mirror Win32, not any crate's private ABI.
    type HandleRaw = *mut c_void;
    #[repr(C)]
    #[derive(Default)]
    struct BasicLimits {
        process_time: i64,
        job_time: i64,
        flags: u32,
        min_working_set: usize,
        max_working_set: usize,
        active_limit: u32,
        affinity: usize,
        priority: u32,
        scheduling: u32,
    }
    #[repr(C)]
    #[derive(Default)]
    struct ExtendedLimits {
        basic: BasicLimits,
        io_counters: [u64; 6],
        process_memory: usize,
        job_memory: usize,
        peak_process_memory: usize,
        peak_job_memory: usize,
    }
    #[repr(C)]
    #[derive(Default)]
    struct Accounting {
        user: i64,
        kernel: i64,
        period_user: i64,
        period_kernel: i64,
        faults: u32,
        total: u32,
        active: u32,
        terminated: u32,
    }
    #[repr(C)]
    #[derive(Default)]
    struct ThreadEntry {
        size: u32,
        usage: u32,
        thread_id: u32,
        process_id: u32,
        base_priority: i32,
        delta_priority: i32,
        flags: u32,
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn CreateJobObjectW(attributes: *const c_void, name: *const u16) -> HandleRaw;
        fn SetInformationJobObject(
            job: HandleRaw,
            class: i32,
            info: *const c_void,
            size: u32,
        ) -> i32;
        fn QueryInformationJobObject(
            job: HandleRaw,
            class: i32,
            info: *mut c_void,
            size: u32,
            returned: *mut u32,
        ) -> i32;
        fn AssignProcessToJobObject(job: HandleRaw, process: HandleRaw) -> i32;
        fn TerminateJobObject(job: HandleRaw, exit: u32) -> i32;
        fn CloseHandle(handle: HandleRaw) -> i32;
        fn CreateToolhelp32Snapshot(flags: u32, process: u32) -> HandleRaw;
        fn Thread32First(snapshot: HandleRaw, entry: *mut ThreadEntry) -> i32;
        fn Thread32Next(snapshot: HandleRaw, entry: *mut ThreadEntry) -> i32;
        fn OpenThread(access: u32, inherit: i32, id: u32) -> HandleRaw;
        fn ResumeThread(thread: HandleRaw) -> u32;
    }
    struct Handle(HandleRaw);
    impl Handle {
        fn close(mut self) -> bool {
            let h = self.0;
            self.0 = null_mut();
            // SAFETY: uniquely owned valid kernel handle, closed at most once.
            unsafe { CloseHandle(h) != 0 }
        }
    }
    impl Drop for Handle {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe {
                    CloseHandle(self.0);
                }
            }
        }
    }
    fn job() -> Result<Handle, ServiceFailure> {
        // SAFETY: null means default noninheritable security and unnamed Job.
        let raw = unsafe { CreateJobObjectW(null(), null()) };
        if raw.is_null() {
            return Err(ServiceFailure::ServiceUnavailable);
        }
        let job = Handle(raw);
        let mut limits = ExtendedLimits::default();
        limits.basic.flags = 0x2000;
        // SAFETY: exact Win32 extended-limit layout/size, borrowed during call.
        if unsafe {
            SetInformationJobObject(
                raw,
                9,
                &limits as *const _ as *const c_void,
                size_of::<ExtendedLimits>() as u32,
            )
        } == 0
        {
            return Err(ServiceFailure::ServiceUnavailable);
        }
        Ok(job)
    }
    fn accounting(job: &Handle) -> Result<Accounting, ServiceFailure> {
        let mut accounting = Accounting::default();
        // SAFETY: valid Job, exact writable Win32 accounting buffer.
        if unsafe {
            QueryInformationJobObject(
                job.0,
                1,
                &mut accounting as *mut _ as *mut c_void,
                size_of::<Accounting>() as u32,
                null_mut(),
            )
        } == 0
        {
            return Err(ServiceFailure::CleanupFailed);
        }
        Ok(accounting)
    }
    fn active(job: &Handle) -> Result<u32, ServiceFailure> {
        accounting(job).map(|a| a.active)
    }
    fn resume(child: &Child) -> Result<(), ServiceFailure> {
        // std::process does not expose the initial thread handle. Enumerate only
        // the not-yet-running process's single initial thread, never arbitrary PIDs.
        let raw = unsafe { CreateToolhelp32Snapshot(4, 0) };
        if raw == -1isize as HandleRaw || raw.is_null() {
            return Err(ServiceFailure::ServiceUnavailable);
        }
        let snapshot = Handle(raw);
        let mut row: ThreadEntry = unsafe { zeroed() };
        row.size = size_of::<ThreadEntry>() as u32;
        let mut present = unsafe { Thread32First(raw, &mut row) } != 0;
        let mut found = None;
        while present {
            if row.process_id == child.id() {
                found = Some(row.thread_id);
                break;
            }
            present = unsafe { Thread32Next(raw, &mut row) } != 0;
        }
        if !snapshot.close() {
            return Err(ServiceFailure::CleanupFailed);
        }
        let raw = unsafe { OpenThread(2, 0, found.ok_or(ServiceFailure::ServiceUnavailable)?) };
        if raw.is_null() {
            return Err(ServiceFailure::ServiceUnavailable);
        }
        let thread = Handle(raw);
        let resumed = unsafe { ResumeThread(raw) };
        if !thread.close() {
            return Err(ServiceFailure::CleanupFailed);
        }
        // Newly created suspended initial thread must have suspend count exactly1.
        if resumed != 1 {
            return Err(ServiceFailure::ServiceUnavailable);
        }
        Ok(())
    }

    enum Io {
        Out(Result<Vec<u8>, ServiceFailure>),
        Err(Result<usize, ServiceFailure>),
        In(bool),
    }
    fn join_workers(threads: Vec<thread::JoinHandle<()>>, bound: Duration) -> bool {
        let start = Instant::now();
        let mut clean = true;
        for t in threads {
            while !t.is_finished() && start.elapsed() < bound {
                thread::sleep(POLL);
            }
            if t.is_finished() {
                clean &= t.join().is_ok();
            } else {
                clean = false;
            }
        }
        clean
    }
    const POLL: Duration = Duration::from_millis(5);
    fn failed(e: ServiceFailure) -> ProcessReport {
        ProcessReport {
            outcome: HelperOutcome::Failure(e),
            reaped: true,
            job_active_processes: Some(0),
            job_total_processes: Some(0),
            exit_code: None,
            stderr_bytes: 0,
            pre_cleanup_wait_state: None,
        }
    }
    fn cleanup(
        child: &mut Child,
        job: &Handle,
        terminate: bool,
        bound: Duration,
    ) -> (bool, Option<u32>, Option<i32>) {
        let start = Instant::now();
        let mut ok = true;
        if terminate {
            // SAFETY: contained process tree only; never global process enumeration/kill.
            ok = unsafe { TerminateJobObject(job.0, 1) } != 0;
            // A failed assignment leaves a suspended, never-executed child outside
            // the Job. Child::kill targets its owned handle, no PID reuse lookup.
            if child.try_wait().ok().flatten().is_none() {
                let _ = child.kill();
            }
        }
        let mut reaped = false;
        let mut exit = None;
        let mut count = None;
        loop {
            match child.try_wait() {
                Ok(Some(s)) => {
                    reaped = true;
                    exit = s.code();
                }
                Ok(None) => {}
                Err(_) => {
                    ok = false;
                }
            }
            match active(job) {
                Ok(n) => count = Some(n),
                Err(_) => {
                    ok = false;
                }
            }
            if reaped && count == Some(0) {
                return (ok, count, exit);
            }
            if start.elapsed() >= bound {
                return (false, count, exit);
            }
            thread::sleep(POLL);
        }
    }
    /// Exact executable supplied by trusted host/test code, not an IPC argument.
    /// No shell/CLI passthrough is exposed by the helper itself.
    fn run(
        command: &mut Command,
        configuration: &FixedExecutable,
        raw: &[u8],
        id: RequestIdentity,
        control: &Control,
        _lease: &RequestLease<'_>,
        deadline: Duration,
        cleanup_bound: Duration,
    ) -> ProcessReport {
        let start = Instant::now();
        if raw.len() > MAX_REQUEST_BYTES {
            return failed(ServiceFailure::ResourceLimitExceeded);
        }
        let job = match job() {
            Ok(j) => j,
            Err(e) => return failed(e),
        };
        command
            .creation_flags(0x00000004)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = match command.spawn() {
            Ok(c) => c,
            Err(_) => {
                return failed(if job.close() {
                    ServiceFailure::ServiceUnavailable
                } else {
                    ServiceFailure::CleanupFailed
                })
            }
        };
        // SAFETY: live owned child and Job handles; child cannot execute before assignment.
        let assigned = unsafe { AssignProcessToJobObject(job.0, child.as_raw_handle()) } != 0;
        let ready = if assigned {
            #[cfg(debug_assertions)]
            if let Some(guard) = &configuration.verified_helper {
                if !guard.verify_child(&child) {
                    Err(ServiceFailure::ServiceUnavailable)
                } else {
                    resume(&child)
                }
            } else {
                resume(&child)
            }
            #[cfg(not(debug_assertions))]
            resume(&child)
        } else {
            Err(ServiceFailure::ServiceUnavailable)
        };
        if let Err(e) = ready {
            drop(child.stdin.take());
            drop(child.stdout.take());
            drop(child.stderr.take());
            let (clean, count, exit) = cleanup(&mut child, &job, true, cleanup_bound);
            let total = accounting(&job).ok().map(|a| a.total);
            let clean = job.close() && clean;
            return ProcessReport {
                outcome: accept_after_cleanup(HelperOutcome::Failure(e), clean),
                reaped: child.try_wait().ok().flatten().is_some(),
                job_active_processes: count,
                job_total_processes: total,
                exit_code: exit,
                stderr_bytes: 0,
                pre_cleanup_wait_state: None,
            };
        }
        if let Ok(mut c) = control.0.lock() {
            if c.current == id && c.epoch == _lease.epoch {
                c.started = true;
            }
        }
        let (tx, rx) = mpsc::channel();
        // Piped handles are guaranteed by the std spawn contract above.
        let mut stdout = child.stdout.take().unwrap();
        let mut stderr = child.stderr.take().unwrap();
        let mut stdin = child.stdin.take().unwrap();
        let out_tx = tx.clone();
        let err_tx = tx.clone();
        let stdout_reader_finished = Arc::new(AtomicBool::new(false));
        let stderr_reader_finished = Arc::new(AtomicBool::new(false));
        let out_finished = Arc::clone(&stdout_reader_finished);
        let err_finished = Arc::clone(&stderr_reader_finished);
        let bytes = raw.to_vec();
        let mut threads = Vec::with_capacity(3);
        // Fallible OS worker creation: an error drops the unused captured pipe
        // handles and routes partial startup through the same cleanup proof.
        let workers = (|| {
            threads.push(thread::Builder::new().spawn(move || {
                let result = helper_protocol::read_bounded(&mut stdout, MAX_RESPONSE_BYTES);
                out_finished.store(true, Ordering::Release);
                let _ = out_tx.send(Io::Out(result));
            })?);
            threads.push(thread::Builder::new().spawn(move || {
                let result =
                    helper_protocol::read_bounded(&mut stderr, MAX_STDERR_BYTES).map(|v| v.len());
                err_finished.store(true, Ordering::Release);
                let _ = err_tx.send(Io::Err(result));
            })?);
            threads.push(thread::Builder::new().spawn(move || {
                let _ = tx.send(Io::In(stdin.write_all(&bytes).is_ok()));
            })?);
            Ok::<(), std::io::Error>(())
        })();
        if workers.is_err() {
            let (clean, count, exit) = cleanup(&mut child, &job, true, cleanup_bound);
            let joined = join_workers(threads, cleanup_bound);
            let total = accounting(&job).ok().map(|a| a.total);
            let closed = job.close();
            return ProcessReport {
                outcome: accept_after_cleanup(
                    HelperOutcome::Failure(ServiceFailure::ServiceUnavailable),
                    clean && joined && total.is_some() && closed,
                ),
                reaped: child.try_wait().ok().flatten().is_some(),
                job_active_processes: count,
                job_total_processes: total,
                exit_code: exit,
                stderr_bytes: 0,
                pre_cleanup_wait_state: None,
            };
        }
        let mut terminal: Option<ServiceFailure> = None;
        let mut output = None;
        let mut stderr_bytes = 0;
        let mut err_done = false;
        let mut in_done = false;
        let mut exit = None;
        let mut pre_cleanup_wait_state = None;
        loop {
            // Invalidate before considering any queued/late successful response.
            let stopped = control.failure(id, _lease.epoch).or_else(|| {
                if start.elapsed() >= deadline {
                    // Capture only already-received worker completions and
                    // try_wait observations, never later cleanup/reap values.
                    pre_cleanup_wait_state = Some(PreCleanupWaitState {
                        stdout_done: output.is_some(),
                        stderr_done: err_done,
                        stdin_done: in_done,
                        parent_exit_seen: exit.is_some(),
                        stdout_reader_finished: stdout_reader_finished.load(Ordering::Acquire),
                        stderr_reader_finished: stderr_reader_finished.load(Ordering::Acquire),
                    });
                    Some(ServiceFailure::Timeout)
                } else {
                    None
                }
            });
            if let Some(e) = stopped {
                terminal = Some(e);
                break;
            }
            match rx.recv_timeout(POLL) {
                Ok(Io::Out(Ok(v))) => output = Some(v),
                Ok(Io::Err(Ok(n))) => {
                    stderr_bytes = n;
                    err_done = true;
                }
                Ok(Io::In(true)) => in_done = true,
                Ok(Io::In(false)) => {
                    terminal = Some(ServiceFailure::ServiceUnavailable);
                    break;
                }
                Ok(Io::Out(Err(e))) | Ok(Io::Err(Err(e))) => {
                    terminal = Some(e);
                    break;
                }
                Err(mpsc::RecvTimeoutError::Disconnected)
                    if output.is_some() && err_done && in_done =>
                {
                    thread::sleep(POLL);
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    terminal = Some(ServiceFailure::ServiceUnavailable);
                    break;
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
            match child.try_wait() {
                Ok(Some(s)) => {
                    exit = Some(s.code().unwrap_or(-1));
                    if !s.success() {
                        terminal = Some(ServiceFailure::ServiceUnavailable);
                        break;
                    }
                    // One request owns one helper and one dedicated Job. A
                    // normally exited parent must not leave runtime children.
                    // Detect this before awaiting EOF: a residual descendant
                    // may still hold the stdout/stderr write handles open.
                    let remaining = active(&job);
                    // Cancellation/deadline reached while observing exit/Job
                    // state keeps its existing terminal classification. Return
                    // to the top-level check (including its timeout snapshot).
                    if control.failure(id, _lease.epoch).is_some() || start.elapsed() >= deadline {
                        continue;
                    }
                    if !matches!(remaining, Ok(0)) {
                        // Positive residual count or an unprovable Job state:
                        // terminate/drain, reap and join via bounded cleanup.
                        // Successful draining must not erase this failure.
                        terminal = Some(ServiceFailure::CleanupFailed);
                        break;
                    }
                }
                Ok(None) => {}
                Err(_) => {
                    terminal = Some(ServiceFailure::ServiceUnavailable);
                    break;
                }
            }
            if exit.is_some() && output.is_some() && err_done && in_done {
                break;
            }
        }
        let stop = terminal;
        let (mut clean, mut count, mut observed_exit) =
            cleanup(&mut child, &job, stop.is_some(), cleanup_bound);
        if !clean {
            // Preserve CLEANUP_FAILED even if fallback termination later succeeds.
            let (_, final_count, final_exit) = cleanup(&mut child, &job, true, cleanup_bound);
            count = final_count;
            observed_exit = final_exit;
        }
        clean &= join_workers(threads, cleanup_bound);
        // Readers may finish just after process exit was observed. Retain only
        // bounded diagnostics, never publish late output or lose proven overflow.
        let mut late_overflow = false;
        for message in rx.try_iter() {
            match message {
                Io::Err(Ok(n)) => stderr_bytes = n,
                Io::Out(Err(ServiceFailure::ResourceLimitExceeded))
                | Io::Err(Err(ServiceFailure::ResourceLimitExceeded)) => late_overflow = true,
                _ => {}
            }
        }
        let total = accounting(&job).ok().map(|a| a.total);
        clean &= total.is_some();
        clean &= job.close();
        // Hold the caller-owned control lock across final decision; no late
        // output can undo cancel/generation invalidation observed before publication.
        let mut current = control.0.lock();
        let invalidated = match &current {
            Ok(c) if c.unavailable => Some(ServiceFailure::ServiceUnavailable),
            Ok(c) if c.cancelled => Some(ServiceFailure::Cancelled),
            Ok(c) if c.terminal => Some(ServiceFailure::ResultInvalid),
            Ok(c) if c.current != id || c.epoch != _lease.epoch => {
                Some(ServiceFailure::ResultInvalid)
            }
            Ok(_) if start.elapsed() >= deadline => Some(ServiceFailure::Timeout),
            Ok(_) => None,
            Err(_) => Some(ServiceFailure::ServiceUnavailable),
        };
        let stop = match stop {
            Some(ServiceFailure::ServiceUnavailable) if late_overflow => {
                Some(ServiceFailure::ResourceLimitExceeded)
            }
            other => other,
        };
        let candidate = if let Some(e) = stop.or(invalidated) {
            HelperOutcome::Failure(e)
        } else {
            let outcome = helper_protocol::parse_response(
                output.as_deref().unwrap_or(&[]),
                id,
                exit.unwrap_or(-1),
            )
            .unwrap_or_else(HelperOutcome::Failure);
            match outcome {
                HelperOutcome::Success(ref value)
                    if !helper_protocol::response_matches_request(raw, value) =>
                {
                    HelperOutcome::Failure(ServiceFailure::ResultInvalid)
                }
                other => other,
            }
        };
        if let Ok(c) = &mut current {
            if c.current == id && c.epoch == _lease.epoch {
                // Completion (including timeout) invalidates this identity before
                // returning. Only a new identity may start another operation.
                c.terminal = true;
            }
        }
        ProcessReport {
            outcome: accept_after_cleanup(candidate, clean),
            reaped: child.try_wait().ok().flatten().is_some(),
            job_active_processes: count,
            job_total_processes: total,
            exit_code: observed_exit,
            stderr_bytes,
            pre_cleanup_wait_state,
        }
    }
    pub fn inspect(
        configuration: &FixedExecutable,
        input: &Path,
        id: RequestIdentity,
        control: &Control,
        deadline: Duration,
        cleanup_bound: Duration,
    ) -> ProcessReport {
        let raw = match helper_protocol::encode_request(id, input) {
            Ok(v) => v,
            Err(e) => return failed(e),
        };
        invoke(configuration, &raw, id, control, deadline, cleanup_bound)
    }
    /// Same accepted one-shot runner, with a trusted strictly validated product
    /// request. No alternate executable discovery or second process owner.
    pub fn invoke(
        configuration: &FixedExecutable,
        raw: &[u8],
        id: RequestIdentity,
        control: &Control,
        deadline: Duration,
        cleanup_bound: Duration,
    ) -> ProcessReport {
        let lease = match control.claim(id) {
            Ok(lease) => lease,
            Err(e) => return failed(e),
        };
        if !configuration.executable.is_absolute() {
            return failed(ServiceFailure::ServiceUnavailable);
        }
        #[cfg(debug_assertions)]
        if let Some(guard) = &configuration.verified_helper {
            guard.begin();
        }
        run(
            &mut configuration.command(),
            configuration,
            raw,
            id,
            control,
            &lease,
            deadline,
            cleanup_bound,
        )
    }
}
#[cfg(windows)]
pub use windows::inspect;
#[cfg(windows)]
pub use windows::invoke;
