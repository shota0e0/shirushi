use crate::protocol::{decode_response, encode_request, BridgeError, GET_CAPABILITIES, MAX_RESPONSE_BYTES};
use crossbeam_channel::{bounded, select, Receiver, Sender};
use serde_json::Value;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

#[cfg(windows)]
use std::os::windows::{io::AsRawHandle, process::CommandExt};
#[cfg(windows)]
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE},
    System::{
        Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, Thread32First, Thread32Next, THREADENTRY32,
            TH32CS_SNAPTHREAD,
        },
        JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        },
        Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME},
    },
};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(5);
const SHUTDOWN_GRACE: Duration = Duration::from_millis(750);
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
#[cfg(windows)]
const CREATE_SUSPENDED: u32 = 0x0000_0004;

#[derive(Clone, Debug)]
struct LaunchConfig {
    executable: PathBuf,
    arguments: Vec<String>,
    #[cfg(test)]
    environment: Vec<(String, String)>,
}

impl LaunchConfig {
    fn production() -> Result<Self, BridgeError> {
        if !cfg!(debug_assertions) {
            return Err(BridgeError::new(
                "DEV_CANARY_ONLY",
                "the repository-layout Python bridge is enabled only in local development builds",
            ));
        }
        let manifest = fs::canonicalize(env!("CARGO_MANIFEST_DIR")).map_err(|_| {
            BridgeError::new("BRIDGE_UNAVAILABLE", "could not resolve desktop manifest directory")
        })?;
        let repository = canonical_parent(&manifest)?;
        let executable = canonical_fixed_file(&repository, Path::new(".venv-py312/Scripts/python.exe"))?;
        let script = canonical_fixed_file(&repository, Path::new("scripts/shirushi_bridge.py"))?;
        Ok(Self {
            executable,
            arguments: vec!["-I".into(), "-u".into(), script.to_string_lossy().into_owned()],
            #[cfg(test)]
            environment: Vec::new(),
        })
    }

    #[cfg(test)]
    fn fixture(mode: &str) -> Self {
        let repository = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        Self {
            executable: repository.join(".venv-py312/Scripts/python.exe"),
            arguments: vec![
                "-I".into(),
                "-u".into(),
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("tests/fixtures/bridge_fixture.py")
                    .to_string_lossy()
                    .into_owned(),
                mode.into(),
            ],
            environment: Vec::new(),
        }
    }

    #[cfg(test)]
    fn fixture_with_argument(mode: &str, argument: &Path) -> Self {
        let mut config = Self::fixture(mode);
        config.arguments.push(argument.to_string_lossy().into_owned());
        config
    }

    #[cfg(test)]
    fn production_fixture(local_app_data: &Path) -> Result<Self, BridgeError> {
        let mut config = Self::production()?;
        config.environment.push((
            "LOCALAPPDATA".into(),
            local_app_data.to_string_lossy().into_owned(),
        ));
        Ok(config)
    }
}

fn canonical_parent(manifest: &Path) -> Result<PathBuf, BridgeError> {
    let candidate = manifest.parent().ok_or_else(|| {
        BridgeError::new("BRIDGE_UNAVAILABLE", "desktop manifest has no repository ancestor")
    })?;
    fs::canonicalize(candidate).map_err(|_| {
        BridgeError::new("BRIDGE_UNAVAILABLE", "could not resolve repository directory")
    })
}

fn canonical_fixed_file(repository: &Path, relative: &Path) -> Result<PathBuf, BridgeError> {
    let path = fs::canonicalize(repository.join(relative)).map_err(|_| {
        BridgeError::new(
            "BRIDGE_UNAVAILABLE",
            format!("required fixed bridge component is unavailable: {}", relative.display()),
        )
    })?;
    if !path.starts_with(repository) || !path.is_file() {
        return Err(BridgeError::new(
            "BRIDGE_UNAVAILABLE",
            "fixed bridge component resolved outside the repository or is not a file",
        ));
    }
    Ok(path)
}

#[derive(Debug)]
pub struct BridgeHost {
    commands: Sender<WorkerCommand>,
    in_flight: AtomicBool,
    next_request: AtomicU64,
    health: Arc<Mutex<Health>>,
    containment: Arc<Mutex<Option<Arc<JobObject>>>>,
    worker: Mutex<Option<JoinHandle<()>>>,
}

impl BridgeHost {
    pub fn start() -> Result<Self, BridgeError> {
        Self::start_with_config(LaunchConfig::production()?)
    }

    fn start_with_config(config: LaunchConfig) -> Result<Self, BridgeError> {
        let started = Instant::now();
        let (command_tx, command_rx) = bounded(1);
        let health = Arc::new(Mutex::new(Health::Starting));
        let containment = Arc::new(Mutex::new(None));
        let worker_health = Arc::clone(&health);
        let worker_containment = Arc::clone(&containment);
        let worker = thread::Builder::new()
            .name("shirushi-python-bridge".into())
            .spawn(move || worker_main(config, command_rx, worker_health, worker_containment))
            .map_err(|_| BridgeError::new("BRIDGE_UNAVAILABLE", "could not start bridge supervisor"))?;
        let host = Self {
            commands: command_tx,
            in_flight: AtomicBool::new(false),
            next_request: AtomicU64::new(1),
            health,
            containment,
            worker: Mutex::new(Some(worker)),
        };
        let deadline = started + REQUEST_TIMEOUT;
        match host.request_until(GET_CAPABILITIES, deadline) {
            Ok(_) => {
                let startup_fault = {
                    let mut health = host.health.lock().expect("bridge health mutex poisoned");
                    match &*health {
                        Health::Starting => {
                            *health = Health::Ready;
                            None
                        }
                        Health::Poisoned(error) => Some(error.clone()),
                        Health::Stopped => Some(BridgeError::new(
                            "BRIDGE_UNAVAILABLE",
                            "bridge stopped during startup",
                        )),
                        Health::Ready => None,
                    }
                };
                if let Some(error) = startup_fault {
                    host.shutdown();
                    Err(error)
                } else {
                    Ok(host)
                }
            }
            Err(error) => {
                host.shutdown();
                Err(error)
            }
        }
    }

    pub fn request(&self, method: &'static str) -> Result<Value, BridgeError> {
        self.request_until(method, Instant::now() + REQUEST_TIMEOUT)
    }

    fn request_until(&self, method: &'static str, deadline: Instant) -> Result<Value, BridgeError> {
        let guard = BusyGuard::acquire(&self.in_flight)?;
        match &*self.health.lock().expect("bridge health mutex poisoned") {
            Health::Poisoned(error) => return Err(error.clone()),
            Health::Stopped => {
                return Err(BridgeError::new("BRIDGE_UNAVAILABLE", "bridge is stopped"));
            }
            Health::Starting | Health::Ready => {}
        }
        let request_id = format!("r{}", self.next_request.fetch_add(1, Ordering::Relaxed));
        let (reply_tx, reply_rx) = bounded(1);
        let command = WorkerCommand::Request {
                request_id,
                method,
                deadline,
                reply: reply_tx,
            };
        match self.commands.try_send(command) {
            Ok(()) => {}
            Err(crossbeam_channel::TrySendError::Full(_)) => {
                return Err(BridgeError::new("BUSY", "one bridge request is already outstanding"));
            }
            Err(crossbeam_channel::TrySendError::Disconnected(_)) => {
                let error = BridgeError::new("SIDECAR_CRASHED", "bridge supervisor is unavailable");
                self.poison_and_terminate(error.clone());
                return Err(error);
            }
        }
        let remaining = deadline.saturating_duration_since(Instant::now()) + Duration::from_millis(100);
        let result = match reply_rx.recv_timeout(remaining) {
            Ok(result) => result,
            Err(crossbeam_channel::RecvTimeoutError::Timeout) => {
                let error = BridgeError::new("REQUEST_TIMEOUT", "bridge request timed out");
                self.poison_and_terminate(error.clone());
                Err(error)
            }
            Err(crossbeam_channel::RecvTimeoutError::Disconnected) => {
                let error = BridgeError::new("SIDECAR_CRASHED", "bridge supervisor closed the response channel");
                self.poison_and_terminate(error.clone());
                Err(error)
            }
        };
        drop(guard);
        result
    }

    fn poison_and_terminate(&self, error: BridgeError) {
        poison_health(&self.health, error);
        if let Some(containment) = self
            .containment
            .lock()
            .expect("bridge containment mutex poisoned")
            .as_ref()
        {
            containment.terminate();
        }
    }

    pub fn health_error(&self) -> Option<BridgeError> {
        match &*self.health.lock().expect("bridge health mutex poisoned") {
            Health::Poisoned(error) => Some(error.clone()),
            Health::Stopped => Some(BridgeError::new("BRIDGE_UNAVAILABLE", "bridge is stopped")),
            Health::Starting | Health::Ready => None,
        }
    }

    pub(crate) fn shutdown(&self) {
        let _ = self
            .commands
            .send_timeout(WorkerCommand::Shutdown, REQUEST_TIMEOUT + Duration::from_secs(1));
        if let Some(worker) = self.worker.lock().expect("bridge worker mutex poisoned").take() {
            let _ = worker.join();
        }
        *self.health.lock().expect("bridge health mutex poisoned") = Health::Stopped;
    }
}

impl Drop for BridgeHost {
    fn drop(&mut self) {
        self.shutdown();
    }
}

struct BusyGuard<'a>(&'a AtomicBool);

impl<'a> BusyGuard<'a> {
    fn acquire(flag: &'a AtomicBool) -> Result<Self, BridgeError> {
        flag.compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| Self(flag))
            .map_err(|_| BridgeError::new("BUSY", "one bridge request is already outstanding"))
    }
}

impl Drop for BusyGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

#[derive(Clone, Debug)]
enum Health {
    Starting,
    Ready,
    Poisoned(BridgeError),
    Stopped,
}

#[derive(Debug)]
enum WorkerCommand {
    Request {
        request_id: String,
        method: &'static str,
        deadline: Instant,
        reply: Sender<Result<Value, BridgeError>>,
    },
    Shutdown,
}

enum OutputEvent {
    Frame(Vec<u8>),
    StdoutTooLarge,
    StdoutIo,
    StdoutEof,
    UnexpectedStderr,
    StderrIo,
}

fn worker_main(
    config: LaunchConfig,
    commands: Receiver<WorkerCommand>,
    health: Arc<Mutex<Health>>,
    containment: Arc<Mutex<Option<Arc<JobObject>>>>,
) {
    let (mut child, mut stdin, output_rx) = match spawn_child(&config, &containment) {
        Ok(parts) => parts,
        Err(error) => {
            poison_health(&health, error);
            drain_commands_with_health(&commands, &health);
            return;
        }
    };

    let mut poisoned = None;
    loop {
        select! {
            recv(commands) -> command => match command {
                Ok(WorkerCommand::Request { request_id, method, deadline, reply }) => {
                    let result = if let Some(error) = &poisoned {
                        Err(error.clone())
                    } else {
                        execute_request(&mut child, &mut stdin, &output_rx, &request_id, method, deadline)
                    };
                    if let Err(error) = &result {
                        if fatal_error(error) {
                            poison_child(&mut child, &mut stdin, &health, error.clone());
                            poisoned = Some(error.clone());
                        }
                    }
                    let _ = reply.send(result);
                }
                Ok(WorkerCommand::Shutdown) | Err(_) => break,
            },
            recv(output_rx) -> event => {
                let error = match event {
                    Ok(event) => event_error(event),
                    Err(_) => BridgeError::new("SIDECAR_CRASHED", "sidecar output channel closed"),
                };
                poison_child(&mut child, &mut stdin, &health, error.clone());
                poisoned = Some(error);
            },
            default(Duration::from_millis(15)) => {
                if poisoned.is_none() {
                    match child.try_wait() {
                        Ok(Some(_)) => {
                            let error = BridgeError::new("SIDECAR_CRASHED", "Python sidecar exited unexpectedly");
                            poison_child(&mut child, &mut stdin, &health, error.clone());
                            poisoned = Some(error);
                        }
                        Err(_) => {
                            let error = BridgeError::new("SIDECAR_CRASHED", "could not query Python sidecar state");
                            poison_child(&mut child, &mut stdin, &health, error.clone());
                            poisoned = Some(error);
                        }
                        Ok(None) => {}
                    }
                }
            }
        }
    }
    graceful_stop(&mut child, &mut stdin);
}

fn execute_request(
    child: &mut ContainedChild,
    stdin: &mut Option<ChildStdin>,
    output_rx: &Receiver<OutputEvent>,
    request_id: &str,
    method: &str,
    deadline: Instant,
) -> Result<Value, BridgeError> {
    if Instant::now() >= deadline {
        return Err(BridgeError::new("REQUEST_TIMEOUT", "bridge request timed out"));
    }
    if child.try_wait().map_err(|_| BridgeError::new("SIDECAR_CRASHED", "could not query Python sidecar state"))?.is_some() {
        return Err(BridgeError::new("SIDECAR_CRASHED", "Python sidecar exited unexpectedly"));
    }
    if let Ok(event) = output_rx.try_recv() {
        return Err(event_error(event));
    }
    let request = encode_request(request_id, method)?;
    let stream = stdin.as_mut().ok_or_else(|| BridgeError::new("SIDECAR_CRASHED", "sidecar stdin is closed"))?;
    stream
        .write_all(&request)
        .and_then(|_| stream.flush())
        .map_err(|_| BridgeError::new("SIDECAR_CRASHED", "failed to write to Python sidecar"))?;

    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(BridgeError::new("REQUEST_TIMEOUT", "bridge request timed out"));
        }
        match output_rx.recv_timeout(remaining) {
            Ok(OutputEvent::Frame(frame)) => {
                let decoded = decode_response(&frame, request_id, method);
                if decoded.is_ok() {
                    let settle = deadline
                        .saturating_duration_since(Instant::now())
                        .min(Duration::from_millis(15));
                    if !settle.is_zero() {
                        match output_rx.recv_timeout(settle) {
                            Ok(event) => return Err(event_error(event)),
                            Err(crossbeam_channel::RecvTimeoutError::Disconnected) => {
                                return Err(BridgeError::new("SIDECAR_CRASHED", "sidecar output channel closed"));
                            }
                            Err(crossbeam_channel::RecvTimeoutError::Timeout) => {}
                        }
                    }
                }
                return decoded;
            }
            Ok(event) => return Err(event_error(event)),
            Err(crossbeam_channel::RecvTimeoutError::Timeout) => {
                return Err(BridgeError::new("REQUEST_TIMEOUT", "bridge request timed out"));
            }
            Err(crossbeam_channel::RecvTimeoutError::Disconnected) => {
                return Err(BridgeError::new("SIDECAR_CRASHED", "sidecar output channel closed"));
            }
        }
    }
}

fn spawn_child(
    config: &LaunchConfig,
    containment: &Arc<Mutex<Option<Arc<JobObject>>>>,
) -> Result<(ContainedChild, Option<ChildStdin>, Receiver<OutputEvent>), BridgeError> {
    let job = Arc::new(JobObject::new()?);
    *containment.lock().expect("bridge containment mutex poisoned") = Some(Arc::clone(&job));
    let mut command = Command::new(&config.executable);
    command
        .args(&config.arguments)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(test)]
    for (name, value) in &config.environment {
        command.env(name, value);
    }
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW | CREATE_SUSPENDED);
    let mut child = command
        .spawn()
        .map_err(|_| BridgeError::new("BRIDGE_UNAVAILABLE", "could not start the fixed Python sidecar"))?;
    if let Err(error) = job.assign(&child) {
        let _ = child.kill();
        let _ = child.wait();
        return Err(error);
    }
    #[cfg(windows)]
    if let Err(error) = resume_suspended_child(&child) {
        job.terminate();
        let _ = child.kill();
        let _ = child.wait();
        return Err(error);
    }
    let mut child = ContainedChild { process: child, job };
    let stdin = child.process.stdin.take();
    let stdout = child.process.stdout.take().ok_or_else(|| BridgeError::new("BRIDGE_UNAVAILABLE", "sidecar stdout is unavailable"))?;
    let stderr = child.process.stderr.take().ok_or_else(|| BridgeError::new("BRIDGE_UNAVAILABLE", "sidecar stderr is unavailable"))?;
    let (sender, receiver) = bounded(2);
    let stdout_sender = sender.clone();
    thread::Builder::new()
        .name("shirushi-bridge-stdout".into())
        .spawn(move || read_stdout(stdout, stdout_sender))
        .map_err(|_| BridgeError::new("BRIDGE_UNAVAILABLE", "could not monitor sidecar stdout"))?;
    thread::Builder::new()
        .name("shirushi-bridge-stderr".into())
        .spawn(move || read_stderr(stderr, sender))
        .map_err(|_| BridgeError::new("BRIDGE_UNAVAILABLE", "could not monitor sidecar stderr"))?;
    Ok((child, stdin, receiver))
}

#[cfg(windows)]
fn resume_suspended_child(child: &Child) -> Result<(), BridgeError> {
    // SAFETY: this creates an owned snapshot handle, checked before use and closed on every path.
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(BridgeError::new(
            "BRIDGE_UNAVAILABLE",
            "could not enumerate the suspended sidecar thread",
        ));
    }
    let mut entry = THREADENTRY32 {
        dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
        ..Default::default()
    };
    let mut found = false;
    // SAFETY: snapshot is valid and entry points to initialized writable storage of the required size.
    let mut has_entry = unsafe { Thread32First(snapshot, &mut entry) } != 0;
    while has_entry {
        if entry.th32OwnerProcessID == child.id() {
            found = true;
            // SAFETY: the thread id came from the live snapshot; the handle is closed below.
            let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
            if thread.is_null() {
                // SAFETY: snapshot is the owned handle created above.
                unsafe { CloseHandle(snapshot) };
                return Err(BridgeError::new(
                    "BRIDGE_UNAVAILABLE",
                    "could not open the suspended sidecar thread",
                ));
            }
            // SAFETY: thread is a live THREAD_SUSPEND_RESUME handle.
            let previous_count = unsafe { ResumeThread(thread) };
            // SAFETY: this is the single close for the OpenThread handle.
            unsafe { CloseHandle(thread) };
            if previous_count == u32::MAX {
                // SAFETY: snapshot is the owned handle created above.
                unsafe { CloseHandle(snapshot) };
                return Err(BridgeError::new(
                    "BRIDGE_UNAVAILABLE",
                    "could not resume the contained Python sidecar",
                ));
            }
            break;
        }
        // SAFETY: snapshot remains live and entry remains valid writable storage.
        has_entry = unsafe { Thread32Next(snapshot, &mut entry) } != 0;
    }
    // SAFETY: this is the single close for the snapshot handle.
    unsafe { CloseHandle(snapshot) };
    if !found {
        return Err(BridgeError::new(
            "BRIDGE_UNAVAILABLE",
            "suspended sidecar thread was not found",
        ));
    }
    Ok(())
}

fn read_stdout(mut stdout: impl Read, sender: Sender<OutputEvent>) {
    let mut frame = Vec::with_capacity(4096);
    let mut chunk = [0_u8; 8192];
    loop {
        match stdout.read(&mut chunk) {
            Ok(0) => {
                if !frame.is_empty() {
                    let _ = sender.send(OutputEvent::StdoutIo);
                } else {
                    let _ = sender.send(OutputEvent::StdoutEof);
                }
                return;
            }
            Ok(count) => {
                for byte in &chunk[..count] {
                    if *byte == b'\n' {
                        if frame.last() == Some(&b'\r') {
                            frame.pop();
                        }
                        if sender.send(OutputEvent::Frame(std::mem::take(&mut frame))).is_err() {
                            return;
                        }
                    } else {
                        frame.push(*byte);
                        if frame.len() > MAX_RESPONSE_BYTES {
                            let _ = sender.send(OutputEvent::StdoutTooLarge);
                            return;
                        }
                    }
                }
            }
            Err(_) => {
                let _ = sender.send(OutputEvent::StdoutIo);
                return;
            }
        }
    }
}

fn read_stderr(mut stderr: impl Read, sender: Sender<OutputEvent>) {
    let mut byte = [0_u8; 1];
    match stderr.read(&mut byte) {
        Ok(0) => {}
        Ok(_) => { let _ = sender.send(OutputEvent::UnexpectedStderr); }
        Err(_) => { let _ = sender.send(OutputEvent::StderrIo); }
    }
}

fn event_error(event: OutputEvent) -> BridgeError {
    match event {
        OutputEvent::Frame(_) => BridgeError::new("UNEXPECTED_STDOUT", "sidecar emitted an unexpected stdout frame"),
        OutputEvent::StdoutTooLarge => BridgeError::new("RESPONSE_TOO_LARGE", "sidecar response exceeds its byte limit"),
        OutputEvent::StdoutIo => BridgeError::new("MALFORMED_RESPONSE", "sidecar stdout ended with a partial or unreadable frame"),
        OutputEvent::StdoutEof => BridgeError::new("SIDECAR_CRASHED", "Python sidecar stdout closed unexpectedly"),
        OutputEvent::UnexpectedStderr | OutputEvent::StderrIo => BridgeError::new("UNEXPECTED_STDERR", "Python sidecar emitted or failed on stderr"),
    }
}

fn fatal_error(error: &BridgeError) -> bool {
    matches!(
        error.code.as_str(),
        "BRIDGE_UNAVAILABLE"
            | "REQUEST_TIMEOUT"
            | "SIDECAR_CRASHED"
            | "UNEXPECTED_STDOUT"
            | "UNEXPECTED_STDERR"
            | "RESPONSE_TOO_LARGE"
            | "MALFORMED_RESPONSE"
            | "RESPONSE_ID_MISMATCH"
            | "PROTOCOL_VERSION_MISMATCH"
            | "TRANSPORT_READ_FAILED"
            | "REQUEST_TOO_LARGE"
            | "PARTIAL_REQUEST"
            | "TRANSPORT_BOM"
            | "INVALID_UTF8"
            | "MALFORMED_UNICODE"
            | "MAX_DEPTH_EXCEEDED"
            | "MALFORMED_JSON"
            | "DUPLICATE_KEY"
            | "INVALID_NUMBER"
            | "INVALID_ENVELOPE"
            | "INVALID_REQUEST_ID"
            | "UNSUPPORTED_PROTOCOL_VERSION"
            | "DUPLICATE_REQUEST_ID"
            | "SESSION_EXHAUSTED"
            | "INTERNAL_ERROR"
    )
}

fn poison_child(child: &mut ContainedChild, stdin: &mut Option<ChildStdin>, health: &Arc<Mutex<Health>>, error: BridgeError) {
    poison_health(health, error);
    force_stop(child, stdin);
}

fn poison_health(health: &Arc<Mutex<Health>>, error: BridgeError) {
    let mut health = health.lock().expect("bridge health mutex poisoned");
    if matches!(&*health, Health::Starting | Health::Ready) {
        *health = Health::Poisoned(error);
    }
}

fn drain_commands_with_health(commands: &Receiver<WorkerCommand>, health: &Arc<Mutex<Health>>) {
    while let Ok(command) = commands.recv() {
        match command {
            WorkerCommand::Request { reply, .. } => {
                let error = match &*health.lock().expect("bridge health mutex poisoned") {
                    Health::Poisoned(error) => error.clone(),
                    _ => BridgeError::new("BRIDGE_UNAVAILABLE", "bridge is unavailable"),
                };
                let _ = reply.send(Err(error));
            }
            WorkerCommand::Shutdown => break,
        }
    }
}

fn graceful_stop(child: &mut ContainedChild, stdin: &mut Option<ChildStdin>) {
    stdin.take();
    let deadline = Instant::now() + SHUTDOWN_GRACE;
    while Instant::now() < deadline {
        match child.try_wait() {
            Ok(Some(_)) => return,
            Ok(None) => thread::sleep(Duration::from_millis(10)),
            Err(_) => break,
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn force_stop(child: &mut ContainedChild, stdin: &mut Option<ChildStdin>) {
    stdin.take();
    child.terminate_tree();
    let _ = child.wait();
}

struct ContainedChild {
    process: Child,
    job: Arc<JobObject>,
}

impl Drop for ContainedChild {
    fn drop(&mut self) {
        self.terminate_tree();
        let _ = self.process.wait();
    }
}

impl ContainedChild {
    fn try_wait(&mut self) -> std::io::Result<Option<std::process::ExitStatus>> {
        self.process.try_wait()
    }

    fn kill(&mut self) -> std::io::Result<()> {
        self.process.kill()
    }

    fn wait(&mut self) -> std::io::Result<std::process::ExitStatus> {
        self.process.wait()
    }

    fn terminate_tree(&mut self) {
        self.job.terminate();
        let _ = self.process.kill();
    }
}

#[cfg(windows)]
#[derive(Debug)]
struct JobObject(HANDLE);

#[cfg(windows)]
unsafe impl Send for JobObject {}
#[cfg(windows)]
unsafe impl Sync for JobObject {}

#[cfg(windows)]
impl JobObject {
    fn new() -> Result<Self, BridgeError> {
        // SAFETY: null security/name pointers create a private job; the returned handle is owned here.
        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err(BridgeError::new("BRIDGE_UNAVAILABLE", "could not create sidecar containment job"));
        }
        let mut information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: the pointer and size describe the initialized information value for this API call.
        let configured = unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &information as *const _ as *const std::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if configured == 0 {
            // SAFETY: handle was returned by CreateJobObjectW and has not been closed.
            unsafe { CloseHandle(handle) };
            return Err(BridgeError::new("BRIDGE_UNAVAILABLE", "could not configure sidecar containment job"));
        }
        Ok(Self(handle))
    }

    fn assign(&self, child: &Child) -> Result<(), BridgeError> {
        let process = child.as_raw_handle() as HANDLE;
        // SAFETY: both handles are live for the duration of the call.
        if unsafe { AssignProcessToJobObject(self.0, process) } == 0 {
            return Err(BridgeError::new("BRIDGE_UNAVAILABLE", "could not contain the Python sidecar process tree"));
        }
        Ok(())
    }

    fn terminate(&self) {
        // SAFETY: the job handle remains owned by this value.
        let _ = unsafe { TerminateJobObject(self.0, 1) };
    }
}

#[cfg(windows)]
impl Drop for JobObject {
    fn drop(&mut self) {
        // SAFETY: this is the single close of the owned job handle; kill-on-close contains descendants.
        unsafe { CloseHandle(self.0) };
    }
}

#[cfg(not(windows))]
#[derive(Debug)]
struct JobObject;

#[cfg(not(windows))]
impl JobObject {
    fn new() -> Result<Self, BridgeError> { Ok(Self) }
    fn assign(&self, _child: &Child) -> Result<(), BridgeError> { Ok(()) }
    fn terminate(&self) {}
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn host_for_test_channel(
        commands: Sender<WorkerCommand>,
        worker: Option<JoinHandle<()>>,
    ) -> BridgeHost {
        BridgeHost {
            commands,
            in_flight: AtomicBool::new(false),
            next_request: AtomicU64::new(1),
            health: Arc::new(Mutex::new(Health::Ready)),
            containment: Arc::new(Mutex::new(None)),
            worker: Mutex::new(worker),
        }
    }

    #[test]
    fn normal_framing_and_graceful_shutdown() {
        let host = BridgeHost::start_with_config(LaunchConfig::fixture("normal")).unwrap();
        let result = host.request(crate::protocol::LOAD_PERSONAL_MARK).unwrap();
        assert_eq!(result.get("state").and_then(Value::as_str), Some("absent"));
        drop(host);
    }

    #[test]
    fn startup_rejects_unavailable_crash_stderr_and_bad_frames() {
        let mut unavailable = LaunchConfig::fixture("normal");
        unavailable.executable = PathBuf::from("Z:/definitely/not/python.exe");
        assert_eq!(BridgeHost::start_with_config(unavailable).unwrap_err().code, "BRIDGE_UNAVAILABLE");
        for (mode, expected) in [
            ("crash", "SIDECAR_CRASHED"),
            ("stderr", "UNEXPECTED_STDERR"),
            ("wrong_id", "RESPONSE_ID_MISMATCH"),
            ("wrong_version", "PROTOCOL_VERSION_MISMATCH"),
            ("duplicate_key", "MALFORMED_RESPONSE"),
            ("depth", "MALFORMED_RESPONSE"),
            ("oversize", "RESPONSE_TOO_LARGE"),
            ("extra_stdout", "UNEXPECTED_STDOUT"),
        ] {
            assert_eq!(BridgeHost::start_with_config(LaunchConfig::fixture(mode)).unwrap_err().code, expected, "mode {mode}");
        }
    }

    #[test]
    fn timeout_poisoning_has_no_restart_or_replay() {
        let started = Instant::now();
        let error = BridgeHost::start_with_config(LaunchConfig::fixture("timeout")).unwrap_err();
        assert_eq!(error.code, "REQUEST_TIMEOUT");
        assert!(started.elapsed() >= REQUEST_TIMEOUT);
    }

    #[test]
    fn real_python_typed_mark_roundtrip_preserves_profile_state() {
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let local = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join(format!("real-python-{nonce}"));
        let mark_dir = local.join("Shirushi/personal-mark");
        fs::create_dir_all(&mark_dir).unwrap();
        fs::write(
            mark_dir.join("personal-mark-v2.json"),
            br#"{"version":2,"type":"typed","text":"Mori","renderProfile":{"id":"shirushi-typed","version":1}}"#,
        )
        .unwrap();
        let host = BridgeHost::start_with_config(LaunchConfig::production_fixture(&local).unwrap()).unwrap();
        let result = host.request(crate::protocol::LOAD_PERSONAL_MARK).unwrap();
        assert_eq!(result.get("state").and_then(Value::as_str), Some("v2"));
        assert_eq!(result["mark"]["text"], "Mori");
        assert_eq!(result["renderProfileSupport"]["state"], "ASSETS_UNAVAILABLE");
        assert_eq!(result["renderProfileSupport"]["errorCode"], "RENDER_ASSETS_UNAVAILABLE");
    }

    #[cfg(windows)]
    #[test]
    fn timeout_terminates_sidecar_descendants() {
        use windows_sys::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
        use windows_sys::Win32::System::Threading::{OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE};

        let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let pid_path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join(format!("bridge-descendant-{nonce}.pid"));
        let error = BridgeHost::start_with_config(LaunchConfig::fixture_with_argument("timeout_descendant", &pid_path)).unwrap_err();
        assert_eq!(error.code, "REQUEST_TIMEOUT");
        let pid: u32 = fs::read_to_string(&pid_path).unwrap().parse().unwrap();
        // SAFETY: OpenProcess receives a PID created by the fixture and the returned handle is closed below.
        let handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, pid) };
        if !handle.is_null() {
            // SAFETY: handle is live and synchronization-only.
            assert_eq!(unsafe { WaitForSingleObject(handle, 2000) }, WAIT_OBJECT_0);
            // SAFETY: this is the single close for the OpenProcess handle.
            unsafe { CloseHandle(handle) };
        }
    }

    #[test]
    fn one_outstanding_request_returns_busy() {
        let host = Arc::new(BridgeHost::start_with_config(LaunchConfig::fixture("slow_after_handshake")).unwrap());
        let first = Arc::clone(&host);
        let active = thread::spawn(move || first.request(crate::protocol::LOAD_PERSONAL_MARK));
        thread::sleep(Duration::from_millis(100));
        assert_eq!(host.request(crate::protocol::LOAD_PERSONAL_MARK).unwrap_err().code, "BUSY");
        assert_eq!(active.join().unwrap().unwrap().get("state").and_then(Value::as_str), Some("absent"));
    }

    #[test]
    fn command_channel_full_and_disconnected_are_distinct() {
        let (full_tx, full_rx) = bounded(1);
        full_tx.send(WorkerCommand::Shutdown).unwrap();
        let full_host = host_for_test_channel(full_tx, None);
        assert_eq!(full_host.request(crate::protocol::LOAD_PERSONAL_MARK).unwrap_err().code, "BUSY");
        assert!(matches!(full_rx.try_recv(), Ok(WorkerCommand::Shutdown)));
        drop(full_host);

        let (closed_tx, closed_rx) = bounded(1);
        drop(closed_rx);
        let closed_host = host_for_test_channel(closed_tx, None);
        assert_eq!(closed_host.request(crate::protocol::LOAD_PERSONAL_MARK).unwrap_err().code, "SIDECAR_CRASHED");
        assert_eq!(closed_host.health_error().unwrap().code, "SIDECAR_CRASHED");
    }

    #[test]
    fn disconnected_reply_channel_poisoning_is_not_reported_as_timeout() {
        let (command_tx, command_rx) = bounded(1);
        let worker = thread::spawn(move || {
            if let Ok(WorkerCommand::Request { reply, .. }) = command_rx.recv() {
                drop(reply);
            }
        });
        let host = host_for_test_channel(command_tx, Some(worker));
        assert_eq!(host.request(crate::protocol::LOAD_PERSONAL_MARK).unwrap_err().code, "SIDECAR_CRASHED");
        assert_eq!(host.health_error().unwrap().code, "SIDECAR_CRASHED");
    }
}
