//! Core/state tests only; no helper/process/IPC/OS isolation claim.
use shirushi_f3a_rust_sdk_parity::{
    canonical_success, encode_envelope, parse_envelope,
    service::{
        self, CommitResult, InspectionRequest, RequestIdentity, ServiceFailure, ServiceOutcome,
        SourceObservation, TerminalState,
    },
    snapshot::{DetectedFormat, InputLocator, Snapshot, MAX_SNAPSHOT_BYTES},
    FIXTURE_SHA256, FIXTURE_SIZE,
};
use std::{
    fs,
    io::{self, Cursor, Read},
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

const FIXTURE: &[u8] = include_bytes!("../../../tests/fixtures/inspection/valid_shirushi.png");
const A: RequestIdentity = RequestIdentity {
    request: 1,
    generation: 1,
};
const B: RequestIdentity = RequestIdentity {
    request: 2,
    generation: 2,
};

fn request(identity: RequestIdentity) -> InspectionRequest {
    InspectionRequest::new(identity, Snapshot::from_bytes(FIXTURE).unwrap())
}
fn publish(request: &InspectionRequest) -> TerminalState {
    let mut state = TerminalState::new(request.identity());
    assert_eq!(
        state.complete(
            request.identity(),
            service::inspect(request),
            SourceObservation::Captured(*request.fingerprint())
        ),
        CommitResult::Committed
    );
    state
}
fn success(state: &TerminalState) -> &serde_json::Value {
    match state.outcome().unwrap() {
        ServiceOutcome::Success(result) => result.envelope(),
        ServiceOutcome::Failure(failure) => panic!("{}", failure.code()),
    }
}

struct DisposableCopy {
    directory: PathBuf,
    file: PathBuf,
}
impl DisposableCopy {
    fn new(name: &str, bytes: &[u8]) -> Self {
        static SEQUENCE: AtomicU64 = AtomicU64::new(0);
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let directory = std::env::temp_dir().join(format!(
            "shirushi-skeleton-{nonce}-{}",
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&directory).unwrap();
        let file = directory.join(name);
        fs::write(&file, bytes).unwrap();
        Self { directory, file }
    }
}
impl Drop for DisposableCopy {
    fn drop(&mut self) {
        // Exact disposable file and empty directory only; no recursive cleanup.
        let _ = fs::remove_file(&self.file);
        let _ = fs::remove_dir(&self.directory);
    }
}

#[test]
fn fixture_snapshot_matches_existing_wire_bytes() {
    let state = publish(&request(A));
    let actual = encode_envelope(success(&state), 0).unwrap();
    assert_eq!(actual, encode_envelope(&canonical_success(), 0).unwrap());
    assert_eq!(parse_envelope(&actual, 0).unwrap(), canonical_success());
}
#[test]
fn snapshot_has_exact_fixture_hash_size_and_byte_detected_format() {
    let snapshot = Snapshot::from_reader(&mut Cursor::new(FIXTURE)).unwrap();
    assert_eq!(snapshot.bytes(), FIXTURE);
    assert_eq!(snapshot.fingerprint().sha256_hex(), FIXTURE_SHA256);
    assert_eq!(snapshot.fingerprint().size(), FIXTURE_SIZE);
    assert_eq!(snapshot.fingerprint().format(), DetectedFormat::Png);
}
#[test]
fn png_bytes_named_jpeg_still_succeed_as_png() {
    let copy = DisposableCopy::new("synthetic.jpg", FIXTURE);
    let snapshot = InputLocator::new(&copy.file).unwrap().capture().unwrap();
    assert_eq!(snapshot.fingerprint().format(), DetectedFormat::Png);
    let state = publish(&InspectionRequest::new(A, snapshot));
    assert_eq!(success(&state)["inspection"]["source"]["format"], "PNG");
}
#[test]
fn jpeg_bytes_named_png_are_detected_but_not_success_eligible() {
    let copy = DisposableCopy::new("synthetic.png", b"\xff\xd8\xffsynthetic-jpeg");
    let snapshot = InputLocator::new(&copy.file).unwrap().capture().unwrap();
    assert_eq!(snapshot.fingerprint().format(), DetectedFormat::Jpeg);
    let state = publish(&InspectionRequest::new(A, snapshot));
    assert_eq!(
        state.outcome(),
        Some(&ServiceOutcome::Failure(ServiceFailure::UnsupportedFormat))
    );
}

struct CountingReader {
    count: usize,
    remaining: usize,
}
impl Read for CountingReader {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        let size = buffer.len().min(self.remaining);
        buffer[..size].fill(0);
        self.remaining -= size;
        self.count += size;
        Ok(size)
    }
}
#[test]
fn oversize_reader_stops_at_ceiling_plus_one_before_request_creation() {
    let mut reader = CountingReader {
        count: 0,
        remaining: MAX_SNAPSHOT_BYTES * 4,
    };
    assert_eq!(
        Snapshot::from_reader(&mut reader).unwrap_err(),
        ServiceFailure::ResourceLimitExceeded
    );
    assert_eq!(reader.count, MAX_SNAPSHOT_BYTES + 1);
}
#[test]
fn oversize_slice_is_rejected_before_sdk_inspection() {
    assert_eq!(
        Snapshot::from_bytes(&vec![0; MAX_SNAPSHOT_BYTES + 1]).unwrap_err(),
        ServiceFailure::ResourceLimitExceeded
    );
}
#[test]
fn oversize_file_is_rejected_by_metadata_ceiling() {
    let copy = DisposableCopy::new("large.bin", &vec![0; MAX_SNAPSHOT_BYTES + 1]);
    assert_eq!(
        InputLocator::new(&copy.file)
            .unwrap()
            .capture()
            .unwrap_err(),
        ServiceFailure::ResourceLimitExceeded
    );
}
#[test]
fn exact_ceiling_is_allowed_for_capture_without_expanding_success_scope() {
    let mut reader = CountingReader {
        count: 0,
        remaining: MAX_SNAPSHOT_BYTES,
    };
    let snapshot = Snapshot::from_reader(&mut reader).unwrap();
    assert_eq!(reader.count, MAX_SNAPSHOT_BYTES);
    assert_eq!(snapshot.fingerprint().size(), MAX_SNAPSHOT_BYTES);
    let state = publish(&InspectionRequest::new(A, snapshot));
    assert_eq!(
        state.outcome(),
        Some(&ServiceOutcome::Failure(ServiceFailure::UnsupportedFormat))
    );
}
#[test]
fn empty_and_unknown_readable_inputs_are_unsupported() {
    for bytes in [b"".as_slice(), b"synthetic text".as_slice()] {
        let state = publish(&InspectionRequest::new(
            A,
            Snapshot::from_bytes(bytes).unwrap(),
        ));
        assert_eq!(
            state.outcome(),
            Some(&ServiceOutcome::Failure(ServiceFailure::UnsupportedFormat))
        );
    }
}
#[test]
fn relative_missing_and_directory_inputs_are_unavailable() {
    assert!(matches!(
        InputLocator::new(std::path::Path::new("relative.png")),
        Err(ServiceFailure::InputUnavailable)
    ));
    let copy = DisposableCopy::new("synthetic.png", FIXTURE);
    assert_eq!(
        InputLocator::new(&copy.directory)
            .unwrap()
            .capture()
            .unwrap_err(),
        ServiceFailure::InputUnavailable
    );
    assert_eq!(
        InputLocator::new(&copy.directory.join("missing.png"))
            .unwrap()
            .capture()
            .unwrap_err(),
        ServiceFailure::InputUnavailable
    );
}
#[test]
fn reader_failure_redacts_error_message() {
    struct Failed;
    impl Read for Failed {
        fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
            Err(io::Error::other("SYNTHETIC_PRIVATE_PATH_AND_PAYLOAD"))
        }
    }
    let failure = Snapshot::from_reader(&mut Failed).unwrap_err();
    assert_eq!(failure.code(), "INPUT_UNAVAILABLE");
    assert!(!format!("{failure:?}").contains("PRIVATE"));
}
#[test]
fn interrupted_read_preserves_snapshot_identity() {
    struct InterruptedOnce {
        interrupted: bool,
        bytes: Cursor<&'static [u8]>,
    }
    impl Read for InterruptedOnce {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            if !self.interrupted {
                self.interrupted = true;
                return Err(io::Error::from(io::ErrorKind::Interrupted));
            }
            self.bytes.read(buffer)
        }
    }
    let snapshot = Snapshot::from_reader(&mut InterruptedOnce {
        interrupted: false,
        bytes: Cursor::new(FIXTURE),
    })
    .unwrap();
    assert_eq!(snapshot.fingerprint().sha256_hex(), FIXTURE_SHA256);
}
#[test]
fn changed_source_observation_blocks_original_snapshot_success() {
    let request = request(A);
    let completion = service::inspect(&request);
    assert_eq!(completion.fingerprint().sha256_hex(), FIXTURE_SHA256);
    let mut changed = FIXTURE.to_vec();
    let last = changed.len() - 1;
    changed[last] ^= 1;
    let observed = Snapshot::from_bytes(&changed).unwrap();
    let mut state = TerminalState::new(A);
    assert_eq!(
        state.complete(
            A,
            completion,
            SourceObservation::Captured(*observed.fingerprint())
        ),
        CommitResult::Committed
    );
    assert_eq!(
        state.outcome(),
        Some(&ServiceOutcome::Failure(ServiceFailure::SourceChanged))
    );
}
#[test]
fn unavailable_source_observation_blocks_success() {
    let mut state = TerminalState::new(A);
    state.complete(
        A,
        service::inspect(&request(A)),
        SourceObservation::Unavailable,
    );
    assert_eq!(
        state.outcome(),
        Some(&ServiceOutcome::Failure(ServiceFailure::InputUnavailable))
    );
}
#[test]
fn snapshot_is_independent_of_mutated_original_buffer() {
    let mut original = FIXTURE.to_vec();
    let snapshot = Snapshot::from_bytes(&original).unwrap();
    original.fill(0);
    let request = InspectionRequest::new(A, snapshot);
    let completion = service::inspect(&request);
    assert_eq!(completion.fingerprint().sha256_hex(), FIXTURE_SHA256);
    let mut state = TerminalState::new(A);
    // Separate observation models snapshot binding, not continuous source immutability.
    state.complete(
        A,
        completion,
        SourceObservation::Captured(*request.fingerprint()),
    );
    assert_eq!(
        success(&state)["inspection"]["source"]["sha256"],
        FIXTURE_SHA256
    );
}
#[test]
fn semantic_core_does_not_reopen_locator_after_acquisition() {
    let copy = DisposableCopy::new("synthetic.png", FIXTURE);
    let snapshot = InputLocator::new(&copy.file).unwrap().capture().unwrap();
    let request = InspectionRequest::new(A, snapshot);
    fs::remove_file(&copy.file).unwrap();
    // Core still uses acquired bytes; unavailable current source prevents publication.
    let completion = service::inspect(&request);
    assert_eq!(completion.fingerprint().sha256_hex(), FIXTURE_SHA256);
    let mut state = TerminalState::new(A);
    state.complete(A, completion, SourceObservation::Unavailable);
    assert_eq!(
        state.outcome(),
        Some(&ServiceOutcome::Failure(ServiceFailure::InputUnavailable))
    );
}
#[test]
fn non_golden_png_is_unsupported_not_source_changed() {
    let mut other = FIXTURE.to_vec();
    let last = other.len() - 1;
    other[last] ^= 1;
    let state = publish(&InspectionRequest::new(
        A,
        Snapshot::from_bytes(&other).unwrap(),
    ));
    assert_eq!(
        state.outcome(),
        Some(&ServiceOutcome::Failure(ServiceFailure::UnsupportedFormat))
    );
}
#[test]
fn request_a_result_cannot_be_accepted_by_request_b() {
    let request = request(A);
    let mut state = TerminalState::new(B);
    assert_eq!(
        state.complete(
            B,
            service::inspect(&request),
            SourceObservation::Captured(*request.fingerprint())
        ),
        CommitResult::StaleRequest
    );
    assert!(state.outcome().is_none());
}
#[test]
fn same_request_number_with_new_generation_is_stale() {
    let request = request(A);
    let current = RequestIdentity {
        request: A.request,
        generation: A.generation + 1,
    };
    let mut state = TerminalState::new(A);
    assert_eq!(
        state.complete(
            current,
            service::inspect(&request),
            SourceObservation::Captured(*request.fingerprint())
        ),
        CommitResult::StaleRequest
    );
    assert!(state.outcome().is_none());
}
#[test]
fn same_generation_with_new_request_number_is_stale() {
    let request = request(A);
    let current = RequestIdentity {
        request: A.request + 1,
        generation: A.generation,
    };
    let mut state = TerminalState::new(A);
    assert_eq!(
        state.complete(
            current,
            service::inspect(&request),
            SourceObservation::Captured(*request.fingerprint())
        ),
        CommitResult::StaleRequest
    );
    assert!(state.outcome().is_none());
}
#[test]
fn cancelled_terminal_prevents_later_success_publication() {
    let request = request(A);
    let mut state = TerminalState::new(A);
    assert_eq!(state.cancel(A), CommitResult::Committed);
    assert_eq!(
        state.complete(
            A,
            service::inspect(&request),
            SourceObservation::Captured(*request.fingerprint())
        ),
        CommitResult::AlreadyTerminal
    );
    assert_eq!(
        state.outcome(),
        Some(&ServiceOutcome::Failure(ServiceFailure::Cancelled))
    );
}
#[test]
fn cancel_after_success_cannot_rewrite_success() {
    let mut state = publish(&request(A));
    assert_eq!(state.cancel(A), CommitResult::AlreadyTerminal);
    assert_eq!(success(&state), &canonical_success());
}
#[test]
fn failure_after_success_cannot_replace_success() {
    let mut state = publish(&request(A));
    assert_eq!(
        state.fail(A, ServiceFailure::InternalSdkFailure),
        CommitResult::AlreadyTerminal
    );
    assert_eq!(success(&state), &canonical_success());
}
#[test]
fn prior_failure_blocks_success_and_contains_no_partial_inspection() {
    let request = request(A);
    let mut state = TerminalState::new(A);
    assert_eq!(
        state.fail(A, ServiceFailure::CleanupFailed),
        CommitResult::Committed
    );
    assert_eq!(
        state.complete(
            A,
            service::inspect(&request),
            SourceObservation::Captured(*request.fingerprint())
        ),
        CommitResult::AlreadyTerminal
    );
    assert_eq!(
        state.outcome(),
        Some(&ServiceOutcome::Failure(ServiceFailure::CleanupFailed))
    );
}
#[test]
fn first_failure_wins_and_stale_cancel_cannot_change_state() {
    let mut state = TerminalState::new(A);
    assert_eq!(state.cancel(B), CommitResult::StaleRequest);
    assert!(state.outcome().is_none());
    assert_eq!(
        state.fail(A, ServiceFailure::InputUnavailable),
        CommitResult::Committed
    );
    assert_eq!(state.cancel(A), CommitResult::AlreadyTerminal);
    assert_eq!(
        state.fail(A, ServiceFailure::Timeout),
        CommitResult::AlreadyTerminal
    );
    assert_eq!(
        state.outcome(),
        Some(&ServiceOutcome::Failure(ServiceFailure::InputUnavailable))
    );
}
#[test]
fn all_service_failures_are_bounded_without_partial_inspection_or_free_text() {
    use ServiceFailure::*;
    let failures = [
        SourceChanged,
        UnsupportedFormat,
        InputUnavailable,
        C2paAbsent,
        C2paMalformed,
        IntegrityFailure,
        AssetBindingFailure,
        SignatureInvalid,
        CawgAbsent,
        CawgUnsupported,
        InternalSdkFailure,
        ResourceLimitExceeded,
        Timeout,
        Cancelled,
        ServiceUnavailable,
        ResultInvalid,
        CleanupFailed,
    ];
    for failure in failures {
        let mut state = TerminalState::new(A);
        state.fail(A, failure);
        assert_eq!(state.outcome(), Some(&ServiceOutcome::Failure(failure)));
        assert!(failure.code().len() <= 64);
        assert!(failure
            .code()
            .bytes()
            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_'));
        assert!(!format!("{:?}", state.outcome().unwrap()).contains("inspection"));
    }
}
#[test]
fn debug_output_redacts_bytes_locator_and_formal_result() {
    let snapshot = Snapshot::from_bytes(b"SYNTHETIC_PRIVATE_IMAGE_PAYLOAD").unwrap();
    assert!(!format!("{snapshot:?}").contains("PRIVATE_IMAGE_PAYLOAD"));
    let copy = DisposableCopy::new("synthetic-private-name.png", FIXTURE);
    assert_eq!(
        format!("{:?}", InputLocator::new(&copy.file).unwrap()),
        "InputLocator(REDACTED)"
    );
    let state = publish(&request(A));
    assert_eq!(
        format!("{:?}", state.outcome().unwrap()),
        "Success(LimitedResult(VALIDATED_INNER1_OUTER2))"
    );
}
#[test]
fn inner_outer_versions_and_limited_values_remain_exact() {
    let state = publish(&request(A));
    let outer = success(&state);
    assert_eq!(outer["contractVersion"], 2);
    assert_eq!(outer["result"], "LIMITED_INSPECTION");
    assert_eq!(outer["completeness"], "INCOMPLETE");
    assert_eq!(outer["checks"]["trustmark"], "NOT_CHECKED");
    let inner = &outer["inspection"];
    assert_eq!(inner, &canonical_success()["inspection"]);
    assert_eq!(inner["contractVersion"], 1);
    assert_eq!(inner["trustmark"], "NOT_CHECKED");
    assert_eq!(inner["c2pa"]["trustValidated"], false);
    assert_eq!(inner["fullVerificationPerformed"], false);
    assert_eq!(inner["successMotionEligible"], false);
    assert_eq!(inner["cawg"]["aiTrainingUse"], "NOT_WANTED");
    assert_eq!(inner["cawg"]["aiInferenceUse"], "NOT_WANTED");
    for field in ["request", "generation", "serviceFailure"] {
        assert!(outer.get(field).is_none());
    }
}
#[test]
fn service_sources_have_zero_python_c2patool_and_process_spawn_paths() {
    // Source audit complements real SDK fixture execution; no OS monitoring claim.
    for source in [
        include_str!("../src/service.rs"),
        include_str!("../src/snapshot.rs"),
    ] {
        for forbidden in ["std::process", "Command::", ".spawn(", "python", "c2patool"] {
            assert!(!source.contains(forbidden));
        }
    }
}
