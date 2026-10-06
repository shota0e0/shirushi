//! Development Add/PNG/JPEG tests only. No vendor installer, network or trust import.
use serde_json::{json, Value};
use shirushi_f3a_rust_sdk_parity::{
    helper_protocol as wire, product, product_contract as contract,
    service::{RequestIdentity, ServiceFailure},
};
use std::{
    fs,
    io::Cursor,
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};

static NEXT: AtomicU64 = AtomicU64::new(0);
#[test]
fn inspection_resource_ceiling_accepts_the_full_add_output_bound() {
    assert_eq!(
        product::MAX_INSPECTION_BYTES,
        contract::MAX_PRODUCT_OUTPUT_BYTES
    );
    assert_eq!(product::MAX_INSPECTION_BYTES, 64 * 1024 * 1024);
    assert_eq!(contract::MAX_PRODUCT_INPUT_BYTES, 32 * 1024 * 1024);
    assert!(product::MAX_INSPECTION_BYTES > contract::MAX_PRODUCT_INPUT_BYTES);
}
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "shirushi-product-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        Self(p)
    }
    fn input(&self, ext: &str) -> PathBuf {
        self.0.join(format!("日本語 sample.{ext}"))
    }
    fn stage(&self, ext: &str) -> PathBuf {
        let p = self.0.join("owned-stage");
        fs::create_dir(&p).unwrap();
        p.join(format!("staged.{ext}"))
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn mark() -> Value {
    json!({"version":1,"mode":"typed","typed":"作者","handwritten":{"coordinateSpace":{"width":480,"height":220},"strokes":[]}})
}
fn image_bytes(format: image::ImageFormat) -> Vec<u8> {
    let image =
        image::DynamicImage::ImageRgb8(image::RgbImage::from_pixel(3, 2, image::Rgb([21, 40, 80])));
    let mut bytes = Cursor::new(Vec::new());
    image.write_to(&mut bytes, format).unwrap();
    bytes.into_inner()
}
fn staged(format: image::ImageFormat, ext: &str, m: Value) {
    let temp = Temp::new();
    let input = temp.input(ext);
    let output = product::output_path(&input).unwrap();
    let stage = temp.stage(ext);
    let original = image_bytes(format);
    fs::write(&input, &original).unwrap();
    let receipt = product::add_stage(&input, &output, &stage, &m).unwrap();
    assert_eq!(receipt["result"], "ADD_STAGED");
    assert_eq!(receipt["developmentSigning"], true);
    assert_eq!(fs::read(&input).unwrap(), original);
    assert!(!output.exists());
    let signed = fs::read(&stage).unwrap();
    assert_eq!(
        receipt["output"]["size"].as_u64(),
        Some(signed.len() as u64)
    );
    let parsed = product::inspect_path(&stage).unwrap();
    assert_eq!(parsed["inspection"]["personalMark"], m);
    assert_eq!(parsed["inspection"]["cawg"]["aiTrainingUse"], "NOT_WANTED");
    assert_eq!(parsed["inspection"]["cawg"]["aiInferenceUse"], "NOT_WANTED");
    assert_eq!(parsed["inspection"]["trustmark"], "NOT_CHECKED");
    assert_eq!(parsed["inspection"]["fullVerificationPerformed"], false);
    assert_eq!(
        image::load_from_memory(&original).unwrap().to_rgba8(),
        image::load_from_memory(&signed).unwrap().to_rgba8()
    );
    contract::validate_product_result(&receipt, "add").unwrap();
    // Verify the actual SDK assertion, not merely the adapted policy booleans.
    let settings = c2pa::Settings::new()
        .with_json(
            r#"{"verify":{"verify_trust":false,"ocsp_fetch":false,"remote_manifest_fetch":false}}"#,
        )
        .unwrap();
    let reader = c2pa::Reader::from_context(c2pa::Context::new().with_settings(settings).unwrap())
        .with_stream(
            if ext == "png" {
                "image/png"
            } else {
                "image/jpeg"
            },
            Cursor::new(signed),
        )
        .unwrap();
    // Metadata Add edits an existing image. The SDK must emit a valid opened
    // action, not claim that Shirushi created the source artwork.
    assert!(matches!(
        reader.validation_state(),
        c2pa::ValidationState::Valid
    ));
    let actions = reader
        .active_manifest()
        .unwrap()
        .assertions()
        .iter()
        .filter(|a| a.label().starts_with("c2pa.actions"))
        .collect::<Vec<_>>();
    assert_eq!(actions.len(), 1);
    assert_eq!(
        actions[0].value().unwrap()["actions"][0]["action"],
        "c2pa.opened"
    );
    let assertion = reader
        .active_manifest()
        .unwrap()
        .assertions()
        .iter()
        .find(|a| a.label() == "cawg.training-mining")
        .unwrap();
    assert_eq!(
        assertion.value().unwrap(),
        &json!({"entries":{"cawg.ai_inference":{"use":"notAllowed"},"cawg.ai_generative_training":{"use":"notAllowed"}}})
    );
}
#[test]
fn png_add_embeds_exact_rights_and_metadata_without_changing_pixels_or_source() {
    staged(image::ImageFormat::Png, "png", mark());
}
#[test]
fn jpeg_add_embeds_exact_rights_and_metadata_without_changing_pixels_or_source() {
    staged(image::ImageFormat::Jpeg, "jpeg", mark());
}
#[test]
fn handwritten_metadata_roundtrips_without_visible_pixel_changes() {
    let mut m = mark();
    m["mode"] = json!("handwritten");
    m["handwritten"]["strokes"] = json!([[{"x":0.0,"y":0.1},{"x":1.0,"y":0.9}]]);
    staged(image::ImageFormat::Png, "png", m);
}
#[test]
fn arbitrary_unmarked_png_and_jpeg_are_honestly_absent_and_incomplete() {
    for format in [image::ImageFormat::Png, image::ImageFormat::Jpeg] {
        let v = product::inspect_bytes(&image_bytes(format)).unwrap();
        assert_eq!(v["result"], "LIMITED_INSPECTION");
        assert_eq!(v["completeness"], "INCOMPLETE");
        assert_eq!(v["inspection"]["c2pa"]["presence"], "ABSENT");
        assert_eq!(v["inspection"]["cawg"]["presence"], "ABSENT");
        assert_eq!(v["inspection"]["cawg"]["aiInferenceUse"], "UNKNOWN");
        assert!(v["inspection"]["personalMark"].is_null());
        contract::validate_product_result(&v, "limited_inspect").unwrap();
    }
}
#[test]
fn malformed_and_unsupported_images_never_publish_success() {
    for bytes in [
        b"not an image".as_slice(),
        b"\x89PNG\r\n\x1a\n",
        b"\xff\xd8\xff\xe0",
    ] {
        assert!(product::inspect_bytes(bytes).is_err());
    }
}
#[test]
fn unsupported_extension_and_magic_mismatch_are_rejected() {
    let t = Temp::new();
    for name in ["unsupported.gif", "mismatch.jpg"] {
        let p = t.0.join(name);
        fs::write(&p, image_bytes(image::ImageFormat::Png)).unwrap();
        assert_eq!(
            product::inspect_path(&p),
            Err(ServiceFailure::UnsupportedFormat)
        );
    }
}
#[test]
fn same_path_and_wrong_output_name_rejected_without_source_write() {
    let t = Temp::new();
    let p = t.input("png");
    let s = t.stage("png");
    let b = image_bytes(image::ImageFormat::Png);
    fs::write(&p, &b).unwrap();
    assert!(product::add_stage(&p, &p, &s, &mark()).is_err());
    assert!(product::add_stage(&p, &t.0.join("wrong.png"), &s, &mark()).is_err());
    assert_eq!(fs::read(&p).unwrap(), b);
    assert!(!s.exists());
}
#[test]
fn existing_final_or_stage_is_never_overwritten() {
    let t = Temp::new();
    let p = t.input("png");
    let s = t.stage("png");
    let o = product::output_path(&p).unwrap();
    fs::write(&p, image_bytes(image::ImageFormat::Png)).unwrap();
    fs::write(&o, b"keep-final").unwrap();
    assert!(product::add_stage(&p, &o, &s, &mark()).is_err());
    assert_eq!(fs::read(&o).unwrap(), b"keep-final");
    fs::remove_file(&o).unwrap();
    fs::write(&s, b"keep-stage").unwrap();
    assert!(product::add_stage(&p, &o, &s, &mark()).is_err());
    assert_eq!(fs::read(&s).unwrap(), b"keep-stage");
}
#[test]
fn stage_outside_owned_sibling_directory_is_rejected() {
    let t = Temp::new();
    let p = t.input("png");
    fs::write(&p, image_bytes(image::ImageFormat::Png)).unwrap();
    assert!(product::add_stage(
        &p,
        &product::output_path(&p).unwrap(),
        &t.0.join("unsafe.png"),
        &mark()
    )
    .is_err());
}
#[test]
fn strict_marks_reject_unknown_keys_malformed_and_unbounded_coordinates() {
    let mut m = mark();
    contract::validate_personal_mark(&m).unwrap();
    m["extra"] = json!(true);
    assert!(contract::validate_personal_mark(&m).is_err());
    let mut m = mark();
    m["version"] = json!(2);
    assert!(contract::validate_personal_mark(&m).is_err());
    let mut m = mark();
    m["mode"] = json!("handwritten");
    m["handwritten"]["strokes"] = json!([[{"x":2.0,"y":0.0}]]);
    assert!(contract::validate_personal_mark(&m).is_err());
    let mut m = mark();
    m["typed"] = json!("x".repeat(49));
    assert!(contract::validate_personal_mark(&m).is_err());
}
#[test]
fn explicit_operations_and_duplicate_keys_are_fail_closed() {
    let id = RequestIdentity {
        request: 8,
        generation: 2,
    };
    let t = Temp::new();
    let p = t.input("png");
    assert!(wire::encode_product_request(id, "unknown", &p, None, None, None).is_err());
    let raw = wire::encode_product_request(id, "limited_inspect", &p, None, None, None).unwrap();
    wire::parse_request(&raw).unwrap();
    let bad=br#"{"protocolVersion":1,"operation":"limited_inspect","operation":"add","request":8,"generation":2,"inputLocator":"a"}"#;
    assert!(wire::parse_request(bad).is_err());
    assert!(wire::encode_product_request(
        id,
        "add",
        &p,
        Some(&product::output_path(&p).unwrap()),
        Some(&t.stage("png")),
        Some(&mark())
    )
    .is_ok());
}
#[test]
fn modern_wire_binds_identity_operation_and_result_schema() {
    let id = RequestIdentity {
        request: 1,
        generation: 2,
    };
    let t = Temp::new();
    let p = t.input("png");
    fs::write(&p, image_bytes(image::ImageFormat::Png)).unwrap();
    let request =
        wire::encode_product_request(id, "limited_inspect", &p, None, None, None).unwrap();
    let mut response = Vec::new();
    wire::serve(&mut Cursor::new(request), &mut response).unwrap();
    assert!(matches!(
        wire::parse_product_response(&response, id, "limited_inspect", 0).unwrap(),
        wire::HelperOutcome::Success(_)
    ));
    assert!(wire::parse_product_response(&response, id, "add", 0).is_err());
    assert!(wire::parse_product_response(&response, id, "limited_inspect", 2).is_err());
    assert!(wire::parse_product_response(
        &response,
        RequestIdentity {
            request: 2,
            generation: 2
        },
        "limited_inspect",
        0
    )
    .is_err());
}
#[test]
fn golden_fixture_regression_remains_exact_and_modern_inspection_is_dynamic() {
    let fixture = include_bytes!("../../../tests/fixtures/inspection/valid_shirushi.png");
    assert_eq!(
        shirushi_f3a_rust_sdk_parity::inspect_fixed(fixture).unwrap(),
        shirushi_f3a_rust_sdk_parity::canonical_success()
    );
    let v = product::inspect_bytes(fixture).unwrap();
    assert_eq!(
        v["inspection"]["source"]["sha256"],
        shirushi_f3a_rust_sdk_parity::FIXTURE_SHA256
    );
    assert!(v["inspection"]["personalMark"].is_null());
    contract::validate_product_result(&v, "limited_inspect").unwrap();
}
#[test]
fn malformed_signed_payload_does_not_become_absent() {
    let mut fixture =
        include_bytes!("../../../tests/fixtures/inspection/valid_shirushi.png").to_vec();
    let pos = fixture.windows(4).position(|w| w == b"caBX").unwrap();
    fixture[pos + 16] ^= 1;
    assert!(product::inspect_bytes(&fixture).is_err());
}
#[cfg(unix)]
#[test]
fn symlink_sources_and_output_parents_are_rejected() {
    let t = Temp::new();
    let p = t.input("png");
    fs::write(&p, image_bytes(image::ImageFormat::Png)).unwrap();
    let alias = t.0.join("alias.png");
    std::os::unix::fs::symlink(&p, &alias).unwrap();
    assert_eq!(
        product::inspect_path(&alias),
        Err(ServiceFailure::InputUnavailable)
    );
    let dir = t.0.join("link-dir");
    std::os::unix::fs::symlink(&t.0, &dir).unwrap();
    assert!(product::add_stage(
        &alias,
        &product::output_path(&alias).unwrap(),
        &dir.join("owned/staged.png"),
        &mark()
    )
    .is_err());
}
