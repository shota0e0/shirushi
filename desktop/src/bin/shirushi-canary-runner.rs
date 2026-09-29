//! DEVELOPMENT CANARY TOOL. Native equivalent of scripts/canary/Run-Canary.ps1.

#[cfg(not(debug_assertions))]
compile_error!("shirushi-canary-runner is a development-only debug executable");

#[cfg(not(windows))]
fn main() {
    eprintln!("RUNNER_UNSUPPORTED: Windows only");
    std::process::exit(1);
}

#[cfg(windows)]
mod runner {
    use serde_json::Value;
    use sha2::{Digest, Sha256};
    use std::collections::{BTreeMap, BTreeSet, HashSet};
    use std::ffi::{OsStr, OsString};
    use std::fs::{self, File};
    use std::io::{self, Read, Write};
    use std::os::windows::{
        ffi::{OsStrExt, OsStringExt},
        fs::MetadataExt,
        io::AsRawHandle,
        process::CommandExt,
    };
    use std::path::{Path, PathBuf};
    use std::process::{Child, Command, Stdio};
    use std::thread;
    use std::time::Duration;
    use windows_sys::Win32::Foundation::{
        CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, ERROR_NO_MORE_FILES, FILETIME, HANDLE,
        INVALID_HANDLE_VALUE,
    };
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, Thread32First, Thread32Next,
        PROCESSENTRY32W, TH32CS_SNAPPROCESS, TH32CS_SNAPTHREAD, THREADENTRY32,
    };
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::{
        CreateMutexW, GetProcessTimes, OpenProcess, OpenThread, QueryFullProcessImageNameW,
        ResumeThread, PROCESS_QUERY_LIMITED_INFORMATION, THREAD_SUSPEND_RESUME,
    };

    const CREATE_SUSPENDED: u32 = 0x0000_0004;

    const DESKTOP: &str = "shirushi-desktop.exe";
    const RUNNER: &str = "shirushi-canary-runner.exe";
    const BRIDGE: &str = "scripts/shirushi_bridge.py";
    const VENV_PYTHON: &str = ".venv-py312/Scripts/python.exe";
    const PROFILE: &str = "demo-profile";
    const UNAVAILABLE_ENV: &str = "SHIRUSHI_CANARY_SIDECAR_UNAVAILABLE";
    const IDENTITY_CODE: &str = "import platform,struct,sys; print(\"%d.%d.%d|%d|%s\" % (sys.version_info[:3] + (struct.calcsize(\"P\")*8, platform.python_implementation())))";
    const EXPECTED_IDENTITY: &str = "3.12.10|64|CPython";

    #[derive(Clone, Copy, Debug)]
    struct Failure {
        code: &'static str,
        reason: &'static str,
    }
    impl Failure {
        const fn new(code: &'static str, reason: &'static str) -> Self {
            Self { code, reason }
        }
    }
    type Result<T> = std::result::Result<T, Failure>;
    fn fail<T>(code: &'static str, reason: &'static str) -> Result<T> {
        Err(Failure::new(code, reason))
    }
    fn policy_or(code: &'static str, reason: &'static str, error: &io::Error) -> Failure {
        if error.raw_os_error() == Some(1260) {
            Failure::new(
                "LOCAL_POLICY_BLOCKED",
                "Windows explicitly rejected a child executable",
            )
        } else {
            Failure::new(code, reason)
        }
    }
    fn emit(code: &str, detail: &str) {
        println!("{code}: {detail}");
        let _ = io::stdout().flush();
    }

    pub fn main() {
        if let Err(error) = run() {
            eprintln!("{}: {}", error.code, error.reason);
            std::process::exit(1);
        }
    }
    fn run() -> Result<()> {
        let args: Vec<OsString> = std::env::args_os().skip(1).collect();
        let action = match args.as_slice() {
            [a, flag, _] if a == OsStr::new("prepare") && flag == OsStr::new("--python") => {
                "prepare"
            }
            [a] if a == OsStr::new("launch") => "launch",
            [a] if a == OsStr::new("sidecar-unavailable") => "sidecar-unavailable",
            _ => {
                return fail(
                    "INVALID_ARGUMENTS",
                    "use prepare --python ABSOLUTE_PYTHON_EXE, launch, or sidecar-unavailable",
                )
            }
        };
        let runner = std::env::current_exe()
            .map_err(|_| Failure::new("PACKAGE_INVALID", "runner path is unavailable"))?;
        if !name_is(&runner, RUNNER) {
            return fail("PACKAGE_INVALID", "runner executable has unexpected name");
        }
        let root = runner.parent().ok_or(Failure::new(
            "PACKAGE_INVALID",
            "package root is unavailable",
        ))?;
        assert_immutable(root)?;
        match action {
            "prepare" => prepare(root, Path::new(&args[2])),
            "launch" => launch(root, false),
            "sidecar-unavailable" => launch(root, true),
            _ => unreachable!(),
        }
    }
    fn name_is(path: &Path, name: &str) -> bool {
        path.file_name()
            .and_then(OsStr::to_str)
            .is_some_and(|v| v.eq_ignore_ascii_case(name))
    }
    fn reparse(path: &Path) -> Result<bool> {
        let meta = fs::symlink_metadata(path)
            .map_err(|_| Failure::new("PACKAGE_INVALID", "package component unavailable"))?;
        Ok(meta.file_attributes() & 0x400 != 0)
    }
    fn reject_reparse(path: &Path) -> Result<()> {
        if reparse(path)? {
            fail(
                "PACKAGE_INVALID",
                "linked or reparse-point package component",
            )
        } else {
            Ok(())
        }
    }
    fn reject_chain(root: &Path, path: &Path) -> Result<()> {
        if !path.starts_with(root) {
            return fail("PACKAGE_INVALID", "package component escapes root");
        }
        let mut current = root.to_path_buf();
        reject_reparse(&current)?;
        for part in path
            .strip_prefix(root)
            .map_err(|_| Failure::new("PACKAGE_INVALID", "invalid package path"))?
            .components()
        {
            current.push(part);
            reject_reparse(&current)?;
        }
        Ok(())
    }
    fn safe_relative(name: &str) -> Result<PathBuf> {
        if name.is_empty()
            || name.contains('\\')
            || name.contains(':')
            || name.starts_with('/')
            || name
                .split('/')
                .any(|part| part.is_empty() || part == "." || part == "..")
        {
            return fail("PACKAGE_INVALID", "unsafe manifest path");
        }
        Ok(name.split('/').collect())
    }
    fn digest(path: &Path) -> Result<(u64, String)> {
        let mut file = File::open(path)
            .map_err(|_| Failure::new("PACKAGE_INVALID", "package file unreadable"))?;
        let mut hash = Sha256::new();
        let mut count = 0_u64;
        let mut buffer = [0_u8; 65536];
        loop {
            let n = file
                .read(&mut buffer)
                .map_err(|_| Failure::new("PACKAGE_INVALID", "package file unreadable"))?;
            if n == 0 {
                break;
            }
            count += n as u64;
            hash.update(&buffer[..n]);
        }
        Ok((count, format!("{:x}", hash.finalize())))
    }
    fn is_hex_hash(value: &str) -> bool {
        value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit())
    }
    fn runtime_root(relative: &str) -> bool {
        relative.eq_ignore_ascii_case(".venv-py312")
            || relative.eq_ignore_ascii_case("demo-profile/Shirushi/canary-webview")
    }
    fn collect_files(root: &Path, dir: &Path, found: &mut BTreeSet<String>) -> Result<()> {
        for entry in fs::read_dir(dir)
            .map_err(|_| Failure::new("PACKAGE_INVALID", "package tree unreadable"))?
        {
            let entry =
                entry.map_err(|_| Failure::new("PACKAGE_INVALID", "package tree unreadable"))?;
            let path = entry.path();
            let relative = path
                .strip_prefix(root)
                .map_err(|_| Failure::new("PACKAGE_INVALID", "package path escapes root"))?
                .to_string_lossy()
                .replace('\\', "/");
            let meta = fs::symlink_metadata(&path)
                .map_err(|_| Failure::new("PACKAGE_INVALID", "package entry unreadable"))?;
            if meta.file_attributes() & 0x400 != 0 {
                return fail("PACKAGE_INVALID", "linked package entry");
            }
            if runtime_root(&relative) {
                continue;
            }
            if meta.is_dir() {
                collect_files(root, &path, found)?;
            } else if meta.is_file() {
                found.insert(relative);
            } else {
                return fail("PACKAGE_INVALID", "non-regular package entry");
            }
        }
        Ok(())
    }

    fn assert_immutable(root: &Path) -> Result<()> {
        for name in [
            DESKTOP,
            RUNNER,
            BRIDGE,
            PROFILE,
            "manifest.json",
            "SHA256SUMS",
        ] {
            let path = root.join(safe_relative(name)?);
            if !path.exists() {
                return fail("PACKAGE_INVALID", "required package component missing");
            }
            reject_chain(root, &path)?;
        }
        for name in [".venv-py312", "demo-profile/Shirushi/canary-webview"] {
            let path = root.join(safe_relative(name)?);
            if path.exists() {
                reject_chain(root, &path)?;
            }
        }
        let raw = fs::read(root.join("manifest.json"))
            .map_err(|_| Failure::new("PACKAGE_INVALID", "manifest unreadable"))?;
        let manifest: Value = serde_json::from_slice(&raw)
            .map_err(|_| Failure::new("PACKAGE_INVALID", "manifest JSON invalid"))?;
        if manifest.get("schemaVersion").and_then(Value::as_u64) != Some(1)
            || manifest.get("artifactType").and_then(Value::as_str) != Some("DEVELOPMENT_CANARY")
        {
            return fail("PACKAGE_INVALID", "manifest schema or type invalid");
        }
        let entries = manifest
            .get("files")
            .and_then(Value::as_array)
            .ok_or(Failure::new("PACKAGE_INVALID", "manifest files invalid"))?;
        let mut declared = BTreeMap::<String, String>::new();
        for entry in entries {
            let name = entry
                .get("path")
                .and_then(Value::as_str)
                .ok_or(Failure::new("PACKAGE_INVALID", "manifest path invalid"))?;
            let size = entry
                .get("size")
                .and_then(Value::as_u64)
                .ok_or(Failure::new("PACKAGE_INVALID", "manifest size invalid"))?;
            let expected = entry
                .get("sha256")
                .and_then(Value::as_str)
                .ok_or(Failure::new("PACKAGE_INVALID", "manifest hash invalid"))?;
            if !is_hex_hash(expected) || declared.contains_key(name) {
                return fail("PACKAGE_INVALID", "manifest duplicate path or hash invalid");
            }
            let path = root.join(safe_relative(name)?);
            reject_chain(root, &path)?;
            if !path.is_file() {
                return fail("PACKAGE_INVALID", "manifest payload is not a file");
            }
            let (actual_size, actual_hash) = digest(&path)?;
            if actual_size != size || !expected.eq_ignore_ascii_case(&actual_hash) {
                return fail("PACKAGE_INVALID", "manifest size or SHA-256 mismatch");
            }
            declared.insert(name.to_owned(), actual_hash);
        }
        let mut actual = BTreeSet::new();
        collect_files(root, root, &mut actual)?;
        actual.remove("manifest.json");
        actual.remove("SHA256SUMS");
        if actual != declared.keys().cloned().collect() {
            return fail("PACKAGE_INVALID", "unexpected or missing package payload");
        }
        let sums = fs::read_to_string(root.join("SHA256SUMS"))
            .map_err(|_| Failure::new("PACKAGE_INVALID", "SHA256SUMS unreadable"))?;
        let mut covered = BTreeMap::<String, String>::new();
        for line in sums.lines() {
            let (hash, name) = line
                .split_once("  ")
                .ok_or(Failure::new("PACKAGE_INVALID", "SHA256SUMS line malformed"))?;
            if !is_hex_hash(hash)
                || hash.bytes().any(|b| b.is_ascii_uppercase())
                || name == "SHA256SUMS"
                || covered.contains_key(name)
            {
                return fail("PACKAGE_INVALID", "SHA256SUMS entry invalid");
            }
            let path = root.join(safe_relative(name)?);
            reject_chain(root, &path)?;
            let (_, actual) = digest(&path)?;
            if actual != hash {
                return fail("PACKAGE_INVALID", "SHA256SUMS hash mismatch");
            }
            covered.insert(name.to_owned(), actual);
        }
        let expected: BTreeSet<String> = declared
            .keys()
            .cloned()
            .chain(std::iter::once("manifest.json".to_owned()))
            .collect();
        if covered.keys().cloned().collect::<BTreeSet<_>>() != expected {
            return fail("PACKAGE_INVALID", "SHA256SUMS coverage mismatch");
        }
        Ok(())
    }

    fn python_identity(python: &Path) -> Result<()> {
        let output = Command::new(python)
            .args(["-I", "-B", "-c", IDENTITY_CODE])
            .output()
            .map_err(|e| {
                policy_or(
                    "PREPARE_FAILED",
                    "Python identity process could not start",
                    &e,
                )
            })?;
        if !output.status.success()
            || (output.stdout != format!("{EXPECTED_IDENTITY}\r\n").as_bytes()
                && output.stdout != format!("{EXPECTED_IDENTITY}\n").as_bytes())
        {
            return fail("PREPARE_FAILED", "interpreter must be CPython 3.12.10 x64");
        }
        Ok(())
    }
    fn prepare(root: &Path, python: &Path) -> Result<()> {
        let venv = root.join(".venv-py312");
        if !python.is_absolute()
            || !name_is(python, "python.exe")
            || !python.is_file()
            || reparse(python).unwrap_or(true)
        {
            return fail(
                "PREPARE_FAILED",
                "--python must be an explicit absolute nonlinked python.exe",
            );
        }
        if venv.exists() {
            return fail(
                "PREPARE_FAILED",
                "existing or partial .venv-py312; fresh extraction required; no automatic cleanup",
            );
        }
        python_identity(python)?;
        let status = Command::new(python)
            .args(["-I", "-B", "-m", "venv", "--without-pip"])
            .arg(&venv)
            .env("LOCALAPPDATA", root.join(PROFILE))
            .env_remove(UNAVAILABLE_ENV)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map_err(|e| policy_or("PREPARE_FAILED", "venv creation could not start", &e))?;
        if !status.success() {
            return fail(
                "PREPARE_FAILED",
                "venv creation failed; partial state retained; retry only from fresh extraction",
            );
        }
        let fixed = root.join(VENV_PYTHON);
        if !fixed.is_file() {
            return fail("PREPARE_FAILED", "venv did not produce fixed python.exe");
        }
        reject_chain(root, &fixed)?;
        python_identity(&fixed)?;
        emit(
            "PREPARE_OK",
            "fresh package-local CPython 3.12.10 x64 no-pip venv verified",
        );
        Ok(())
    }

    #[derive(Clone)]
    struct ProcessEntry {
        pid: u32,
        parent: u32,
        name: String,
    }
    struct Handle(HANDLE);
    impl Drop for Handle {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
    struct ContainedDesktop {
        child: Child,
        job: Handle,
        running: bool,
    }
    impl ContainedDesktop {
        fn start(command: &mut Command) -> Result<Self> {
            let raw = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
            if raw.is_null() {
                return fail("MONITOR_UNAVAILABLE", "Desktop containment job unavailable");
            }
            let job = Handle(raw);
            let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let configured = unsafe {
                SetInformationJobObject(
                    job.0,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const std::ffi::c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
            };
            if configured == 0 {
                return fail(
                    "MONITOR_UNAVAILABLE",
                    "Desktop containment could not be configured",
                );
            }
            command.creation_flags(CREATE_SUSPENDED);
            let mut child = command.spawn().map_err(|e| {
                policy_or(
                    "DESKTOP_START_FAILED",
                    "fixed Desktop executable could not start",
                    &e,
                )
            })?;
            if unsafe { AssignProcessToJobObject(job.0, child.as_raw_handle() as HANDLE) } == 0 {
                let _ = child.kill();
                let _ = child.wait();
                return fail("MONITOR_UNAVAILABLE", "Desktop could not be contained");
            }
            let mut result = Self {
                child,
                job,
                running: true,
            };
            resume_suspended(&result.child)?;
            Ok(result)
        }
        fn stop(&mut self) {
            if self.running {
                unsafe { TerminateJobObject(self.job.0, 1) };
                let _ = self.child.kill();
                let _ = self.child.wait();
                self.running = false;
            }
        }
    }
    impl Drop for ContainedDesktop {
        fn drop(&mut self) {
            self.stop();
        }
    }
    fn resume_suspended(child: &Child) -> Result<()> {
        let raw = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
        if raw == INVALID_HANDLE_VALUE {
            return fail("MONITOR_UNAVAILABLE", "Desktop thread snapshot unavailable");
        }
        let _snapshot = Handle(raw);
        let mut entry = THREADENTRY32 {
            dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
            ..Default::default()
        };
        let mut present = unsafe { Thread32First(raw, &mut entry) } != 0;
        while present {
            if entry.th32OwnerProcessID == child.id() {
                let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
                if thread.is_null() {
                    return fail(
                        "MONITOR_UNAVAILABLE",
                        "Desktop suspended thread unavailable",
                    );
                }
                let _thread = Handle(thread);
                if unsafe { ResumeThread(thread) } == u32::MAX {
                    return fail(
                        "MONITOR_UNAVAILABLE",
                        "Desktop suspended thread could not resume",
                    );
                }
                return Ok(());
            }
            present = unsafe { Thread32Next(raw, &mut entry) } != 0;
        }
        fail("MONITOR_UNAVAILABLE", "Desktop suspended thread not found")
    }
    fn process_entries() -> Result<Vec<ProcessEntry>> {
        let raw = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
        if raw == INVALID_HANDLE_VALUE {
            return fail(
                "MONITOR_UNAVAILABLE",
                "process snapshot could not be created",
            );
        }
        let _snapshot = Handle(raw);
        let mut row = PROCESSENTRY32W {
            dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32,
            ..Default::default()
        };
        if unsafe { Process32FirstW(raw, &mut row) } == 0 {
            return fail("MONITOR_UNAVAILABLE", "process snapshot could not be read");
        }
        let mut result = Vec::new();
        loop {
            let end = row
                .szExeFile
                .iter()
                .position(|c| *c == 0)
                .unwrap_or(row.szExeFile.len());
            result.push(ProcessEntry {
                pid: row.th32ProcessID,
                parent: row.th32ParentProcessID,
                name: String::from_utf16_lossy(&row.szExeFile[..end]),
            });
            if unsafe { Process32NextW(raw, &mut row) } == 0 {
                if unsafe { GetLastError() } != ERROR_NO_MORE_FILES {
                    return fail("MONITOR_UNAVAILABLE", "process snapshot ended unexpectedly");
                }
                break;
            }
        }
        Ok(result)
    }
    #[derive(Clone)]
    struct ProcessIdentity {
        pid: u32,
        path: PathBuf,
        created: u64,
        python: bool,
    }
    fn ticks(value: FILETIME) -> u64 {
        (u64::from(value.dwHighDateTime) << 32) | u64::from(value.dwLowDateTime)
    }
    fn identity(entry: &ProcessEntry, expected_python: &Path) -> Result<ProcessIdentity> {
        let raw = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, entry.pid) };
        if raw.is_null() {
            return fail(
                "MONITOR_UNAVAILABLE",
                "process identity could not be opened",
            );
        }
        let _handle = Handle(raw);
        let mut buffer = vec![0_u16; 32768];
        let mut size = buffer.len() as u32;
        if unsafe { QueryFullProcessImageNameW(raw, 0, buffer.as_mut_ptr(), &mut size) } == 0 {
            return fail(
                "MONITOR_UNAVAILABLE",
                "process executable path could not be read",
            );
        }
        let path = PathBuf::from(OsString::from_wide(&buffer[..size as usize]));
        let mut created = FILETIME::default();
        let mut exit = FILETIME::default();
        let mut kernel = FILETIME::default();
        let mut user = FILETIME::default();
        if unsafe { GetProcessTimes(raw, &mut created, &mut exit, &mut kernel, &mut user) } == 0 {
            return fail(
                "MONITOR_UNAVAILABLE",
                "process creation time could not be read",
            );
        }
        let python_name = name_is(&path, "python.exe") || name_is(&path, "pythonw.exe");
        Ok(ProcessIdentity {
            pid: entry.pid,
            python: python_name && same_path(&path, expected_python),
            path,
            created: ticks(created),
        })
    }
    fn same_path(left: &Path, right: &Path) -> bool {
        match (fs::canonicalize(left), fs::canonicalize(right)) {
            (Ok(a), Ok(b)) => a
                .to_string_lossy()
                .eq_ignore_ascii_case(&b.to_string_lossy()),
            _ => false,
        }
    }
    fn descendants(entries: &[ProcessEntry], root_pid: u32) -> Vec<ProcessEntry> {
        let mut ids = HashSet::from([root_pid]);
        loop {
            let before = ids.len();
            for row in entries {
                if ids.contains(&row.parent) {
                    ids.insert(row.pid);
                }
            }
            if ids.len() == before {
                break;
            }
        }
        entries
            .iter()
            .filter(|row| row.pid != root_pid && ids.contains(&row.pid))
            .cloned()
            .collect()
    }
    fn assert_not_running(exe: &Path, fixed_python: &Path) -> Result<()> {
        for row in process_entries()?
            .into_iter()
            .filter(|row| row.name.eq_ignore_ascii_case(DESKTOP))
        {
            let candidate = identity(&row, fixed_python)?;
            if same_path(&candidate.path, exe) {
                return fail(
                    "DESKTOP_ALREADY_RUNNING",
                    "this exact canary executable is already running",
                );
            }
        }
        Ok(())
    }
    fn mutex() -> Result<Handle> {
        let name: Vec<u16> = OsStr::new("Local\\Shirushi-F2C6-Development-Canary")
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        let raw = unsafe { CreateMutexW(std::ptr::null(), 1, name.as_ptr()) };
        if raw.is_null() {
            return fail(
                "MONITOR_UNAVAILABLE",
                "canary run lock could not be created",
            );
        }
        let existed = unsafe { GetLastError() } == ERROR_ALREADY_EXISTS;
        let owned = Handle(raw);
        if existed {
            return fail(
                "DESKTOP_ALREADY_RUNNING",
                "another canary runner owns the run lock",
            );
        }
        Ok(owned)
    }
    fn corroborate(pid: u32, exe: &Path, fixed_python: &Path) -> Result<u64> {
        let row = process_entries()?
            .into_iter()
            .find(|row| row.pid == pid)
            .ok_or(Failure::new(
                "MONITOR_UNAVAILABLE",
                "created Desktop process not found",
            ))?;
        let record = identity(&row, fixed_python)?;
        if !same_path(&record.path, exe) {
            return fail(
                "MONITOR_UNAVAILABLE",
                "created process is not fixed Desktop executable",
            );
        }
        Ok(record.created)
    }
    fn observe(
        child: &mut Child,
        root_created: u64,
        fixed_python: &Path,
        unavailable: bool,
    ) -> Result<()> {
        let root_pid = child.id();
        let mut seen = BTreeMap::<u32, ProcessIdentity>::new();
        let mut seen_python = false;
        let exit_code = loop {
            let entries = process_entries()?;
            for row in descendants(&entries, root_pid) {
                let record = identity(&row, fixed_python)?;
                if record.created < root_created {
                    return fail("MONITOR_UNAVAILABLE", "descendant predates Desktop process");
                }
                if record.python {
                    seen_python = true;
                }
                seen.insert(record.pid, record);
            }
            if let Some(status) = child.try_wait().map_err(|_| {
                Failure::new("MONITOR_UNAVAILABLE", "Desktop exit state unavailable")
            })? {
                break status.code().ok_or(Failure::new(
                    "APP_EXIT_NONZERO",
                    "Desktop exit code unavailable",
                ))?;
            }
            thread::sleep(Duration::from_millis(300));
        };
        emit(
            "DESKTOP_EXITED",
            &format!("pid={root_pid} exit_code={exit_code}"),
        );
        let current = process_entries()?;
        let known_ids: HashSet<u32> = seen
            .keys()
            .copied()
            .chain(std::iter::once(root_pid))
            .collect();
        let mut remaining = 0_usize;
        let mut remaining_python = 0_usize;
        for row in &current {
            let known = seen.get(&row.pid);
            let possible_late = row.parent == root_pid || known_ids.contains(&row.parent);
            let likely_python = row.name.eq_ignore_ascii_case("python.exe")
                || row.name.eq_ignore_ascii_case("pythonw.exe");
            let likely_webview = row.name.eq_ignore_ascii_case("msedgewebview2.exe");
            if known.is_none() && !possible_late && !likely_python {
                continue;
            }
            if known.is_none() && !likely_python && !likely_webview {
                continue;
            }
            let record = identity(row, fixed_python)?;
            let same_known = known.is_some_and(|old| {
                old.created == record.created && same_path(&old.path, &record.path)
            });
            let late_python = likely_python && record.python && record.created >= root_created;
            let late_child = possible_late && record.created >= root_created;
            if same_known || late_python || late_child {
                remaining += 1;
                if record.python {
                    remaining_python += 1;
                }
            }
        }
        emit(
            "CHILD_PROCESS_COUNTS",
            &format!("remaining={remaining} python={remaining_python}"),
        );
        if exit_code != 0 {
            return fail("APP_EXIT_NONZERO", "Desktop exited with nonzero code");
        }
        if unavailable && seen_python {
            return fail(
                "SIDECAR_UNAVAILABLE_SPAWNED",
                "Python sidecar seen in unavailable scenario",
            );
        }
        if !unavailable && !seen_python {
            return fail(
                "SIDECAR_NOT_OBSERVED",
                "normal launch did not establish Python sidecar observation",
            );
        }
        if seen_python {
            emit("SIDECAR_OBSERVED", "fixed package Python child observed");
        } else {
            emit(
                "SIDECAR_NOT_OBSERVED",
                "no Python child in unavailable scenario",
            );
        }
        if remaining != 0 {
            return fail(
                "ORPHAN_PROCESS_DETECTED",
                "corroborated canary child remains",
            );
        }
        emit(
            "ORPHAN_PROCESS_NONE",
            "no corroborated canary child remains",
        );
        Ok(())
    }
    fn launch(root: &Path, unavailable: bool) -> Result<()> {
        let exe = root.join(DESKTOP);
        let fixed_python = root.join(VENV_PYTHON);
        if !unavailable {
            if !fixed_python.is_file() {
                return fail(
                    "PREPARE_FAILED",
                    "normal launch requires prepared package-local venv",
                );
            }
            reject_chain(root, &fixed_python)?;
        }
        assert_not_running(&exe, &fixed_python)?;
        let _lock = mutex()?;
        let mut command = Command::new(&exe);
        command.env("LOCALAPPDATA", root.join(PROFILE));
        if unavailable {
            command.env(UNAVAILABLE_ENV, "1");
        } else {
            command.env_remove(UNAVAILABLE_ENV);
        }
        let mut desktop = ContainedDesktop::start(&mut command)?;
        let result = corroborate(desktop.child.id(), &exe, &fixed_python).and_then(|created| {
            emit("DESKTOP_STARTED", &format!("pid={}", desktop.child.id()));
            observe(&mut desktop.child, created, &fixed_python, unavailable)
        });
        if result.is_err() {
            desktop.stop();
            emit(
                "CONTAINMENT_TERMINATION_REQUESTED",
                "monitor failed; contained Desktop tree termination requested",
            );
        } else {
            desktop.running = false;
        }
        result
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        struct Fixture {
            root: PathBuf,
        }
        impl Fixture {
            fn new() -> Self {
                let nonce = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos();
                let root = std::env::temp_dir().join(format!(
                    "shirushi-f2c7-fixture-{}-{nonce}",
                    std::process::id()
                ));
                fs::create_dir(&root).unwrap();
                let mut entries = Vec::new();
                let mut sums = Vec::new();
                for (name, data) in [
                    (DESKTOP, b"desktop".as_slice()),
                    (RUNNER, b"runner".as_slice()),
                    (BRIDGE, b"bridge".as_slice()),
                ] {
                    let path = root.join(safe_relative(name).unwrap());
                    fs::create_dir_all(path.parent().unwrap()).unwrap();
                    fs::write(&path, data).unwrap();
                    let (size, hash) = digest(&path).unwrap();
                    entries.push(serde_json::json!({"path":name,"size":size,"sha256":hash}));
                    sums.push(format!("{hash}  {name}"));
                }
                fs::create_dir(root.join(PROFILE)).unwrap();
                let manifest = serde_json::to_vec(&serde_json::json!({
                    "schemaVersion":1,"artifactType":"DEVELOPMENT_CANARY","files":entries
                }))
                .unwrap();
                fs::write(root.join("manifest.json"), &manifest).unwrap();
                let (_, hash) = digest(&root.join("manifest.json")).unwrap();
                sums.push(format!("{hash}  manifest.json"));
                fs::write(root.join("SHA256SUMS"), sums.join("\n") + "\n").unwrap();
                Self { root }
            }
        }
        impl Drop for Fixture {
            fn drop(&mut self) {
                if self.root.parent() == Some(std::env::temp_dir().as_path())
                    && self
                        .root
                        .file_name()
                        .unwrap()
                        .to_string_lossy()
                        .starts_with("shirushi-f2c7-fixture-")
                {
                    let _ = fs::remove_dir_all(&self.root);
                }
            }
        }
        #[test]
        fn immutable_package_accepts_only_declared_payload_and_runtime_additions() {
            let case = Fixture::new();
            assert!(assert_immutable(&case.root).is_ok());
            let runtime = case.root.join(".venv-py312/Scripts");
            fs::create_dir_all(&runtime).unwrap();
            fs::write(runtime.join("python.exe"), b"fixture").unwrap();
            assert!(assert_immutable(&case.root).is_ok());
            fs::write(case.root.join("extra.txt"), b"extra").unwrap();
            assert_eq!(
                assert_immutable(&case.root).unwrap_err().code,
                "PACKAGE_INVALID"
            );
        }
        #[test]
        fn immutable_package_rejects_hash_and_sum_tampering() {
            let case = Fixture::new();
            fs::write(case.root.join(BRIDGE), b"tampered").unwrap();
            assert_eq!(
                assert_immutable(&case.root).unwrap_err().code,
                "PACKAGE_INVALID"
            );
            let case = Fixture::new();
            fs::write(case.root.join("SHA256SUMS"), b"bad\n").unwrap();
            assert_eq!(
                assert_immutable(&case.root).unwrap_err().code,
                "PACKAGE_INVALID"
            );
        }
        #[test]
        fn relative_path_and_runtime_allowlist() {
            for bad in ["", "../x", "/x", "a\\b", "a//b", "a/./b", "a:evil"] {
                assert!(safe_relative(bad).is_err(), "{bad}");
            }
            assert_eq!(
                safe_relative(BRIDGE).unwrap(),
                PathBuf::from("scripts/shirushi_bridge.py")
            );
            assert!(runtime_root(".venv-py312"));
            assert!(runtime_root("demo-profile/Shirushi/canary-webview"));
            assert!(!runtime_root("demo-profile/Shirushi/personal-mark"));
        }
        #[test]
        fn descendant_closure_is_recursive() {
            let rows = vec![
                ProcessEntry {
                    pid: 4,
                    parent: 1,
                    name: DESKTOP.into(),
                },
                ProcessEntry {
                    pid: 5,
                    parent: 4,
                    name: "python.exe".into(),
                },
                ProcessEntry {
                    pid: 6,
                    parent: 5,
                    name: "child.exe".into(),
                },
                ProcessEntry {
                    pid: 7,
                    parent: 1,
                    name: "other.exe".into(),
                },
            ];
            let ids: BTreeSet<u32> = descendants(&rows, 4).into_iter().map(|r| r.pid).collect();
            assert_eq!(ids, BTreeSet::from([5, 6]));
        }
        #[test]
        fn post_spawn_monitor_failure_terminates_contained_child() {
            let system_root = PathBuf::from(std::env::var_os("SystemRoot").unwrap());
            let mut command = Command::new(system_root.join("System32/ping.exe"));
            command.args(["-n", "30", "127.0.0.1"]);
            command.stdout(Stdio::null()).stderr(Stdio::null());
            let mut desktop = ContainedDesktop::start(&mut command).unwrap();
            let injected: Result<()> = fail("MONITOR_UNAVAILABLE", "injected after spawn");
            assert_eq!(injected.unwrap_err().code, "MONITOR_UNAVAILABLE");
            desktop.stop();
            assert!(desktop.child.try_wait().unwrap().is_some());
        }
    }
}

#[cfg(windows)]
fn main() {
    runner::main();
}
