//! DEVELOPMENT PNG/JPEG metadata path. TrustMark and publisher trust are NOT checked.
//! Add writes ONLY an exclusive controller-owned stage; the controller publishes
//! after supervisor cleanup and generation checks. No source or final-path writes.
use crate::{
    product_contract as contract, service::ServiceFailure as Error, snapshot::InputLocator,
};
use c2pa::{Builder, Context, Reader, Settings, ValidationState};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Cursor, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
};

pub const MAX_IMAGE_PIXELS: u64 = 16 * 1024 * 1024;
/// Inspector also accepts bounded signed outputs produced by Add.
pub const MAX_INSPECTION_BYTES: usize = contract::MAX_PRODUCT_OUTPUT_BYTES;
fn unavailable<T>(_: T) -> Error {
    Error::InputUnavailable
}
fn regular(path: &Path) -> Result<(), Error> {
    InputLocator::new(path)?;
    for p in path.ancestors() {
        let m = fs::symlink_metadata(p).map_err(unavailable)?;
        if m.file_type().is_symlink() {
            return Err(Error::InputUnavailable);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if m.file_attributes() & 0x400 != 0 {
                return Err(Error::InputUnavailable);
            }
        }
    }
    Ok(())
}
fn read_pin(path: &Path, maximum: usize) -> Result<(File, Vec<u8>), Error> {
    regular(path)?;
    let mut o = OpenOptions::new();
    o.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        o.share_mode(1).custom_flags(0x00200000);
    }
    let mut f = o.open(path).map_err(unavailable)?;
    let m = f.metadata().map_err(unavailable)?;
    if !m.is_file() || m.len() > maximum as u64 {
        return Err(Error::ResourceLimitExceeded);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if m.file_attributes() & 0x400 != 0 {
            return Err(Error::InputUnavailable);
        }
    }
    let mut bytes = Vec::new();
    (&mut f)
        .take(maximum as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(unavailable)?;
    if bytes.len() > maximum {
        return Err(Error::ResourceLimitExceeded);
    }
    Ok((f, bytes))
}
fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn image_format(bytes: &[u8]) -> Result<image::ImageFormat, Error> {
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        Ok(image::ImageFormat::Png)
    } else if bytes.starts_with(b"\xff\xd8\xff") {
        Ok(image::ImageFormat::Jpeg)
    } else {
        Err(Error::UnsupportedFormat)
    }
}
fn mime(format: image::ImageFormat) -> &'static str {
    if format == image::ImageFormat::Png {
        "image/png"
    } else {
        "image/jpeg"
    }
}
fn fingerprint(bytes: &[u8], format: image::ImageFormat) -> Value {
    json!({"sha256":sha(bytes),"size":bytes.len(),"format":if format == image::ImageFormat::Png {"PNG"} else {"JPEG"}})
}
fn decoded(bytes: &[u8]) -> Result<(image::ImageFormat, image::DynamicImage), Error> {
    let format = image_format(bytes)?;
    let mut reader = image::ImageReader::with_format(Cursor::new(bytes), format);
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(16384);
    limits.max_image_height = Some(16384);
    limits.max_alloc = Some(128 * 1024 * 1024);
    reader.limits(limits);
    // Dimensions are bounded before allocating pixels, in addition to decoder limits.
    let (w, h) = image::ImageReader::with_format(Cursor::new(bytes), format)
        .into_dimensions()
        .map_err(|_| Error::UnsupportedFormat)?;
    if u64::from(w) * u64::from(h) > MAX_IMAGE_PIXELS {
        return Err(Error::ResourceLimitExceeded);
    }
    Ok((
        format,
        reader.decode().map_err(|_| Error::UnsupportedFormat)?,
    ))
}
fn context() -> Result<Context, Error> {
    let settings = Settings::new().with_json(r#"{
      "verify":{"verify_after_reading":true,"verify_trust":false,"verify_timestamp_trust":false,"ocsp_fetch":false,"remote_manifest_fetch":false},
      "core":{"decode_identity_assertions":false},"builder":{"thumbnail":{"enabled":false}}
    }"#).map_err(|_| Error::InternalSdkFailure)?;
    Context::new()
        .with_settings(settings)
        .map_err(|_| Error::InternalSdkFailure)
}
fn absent(bytes: &[u8], format: image::ImageFormat) -> Value {
    result(bytes, format, false, false, false, Value::Null)
}
fn result(
    bytes: &[u8],
    format: image::ImageFormat,
    present: bool,
    training: bool,
    inference: bool,
    mark: Value,
) -> Value {
    json!({"contractVersion":2,"operation":"limited_c2pa_cawg_inspection","result":"LIMITED_INSPECTION","completeness":"INCOMPLETE",
      "checks":{"c2pa":"INSPECTED","cawg":"INSPECTED","trustmark":"NOT_CHECKED"},
      "inspection":{"contract":"shirushi-limited-inspection","contractVersion":1,"overall":"LIMITED_INSPECTION","reasonCode":"LIMITED_SCOPE",
      "trustmark":"NOT_CHECKED","fullVerificationPerformed":false,"successMotionEligible":false,"source":fingerprint(bytes,format),
      "c2pa":{"state":"INSPECTED","presence":if present {"PRESENT"} else {"ABSENT"},"parse":present,"assertionDigestsValid":present,
      "assetBindingValid":present,"signature":if present {"PREVIEW"} else {"ABSENT"},"trustValidated":false},
      "cawg":{"state":"INSPECTED","presence":"ABSENT","aiTrainingUse":if training {"NOT_WANTED"} else {"UNKNOWN"},
      "aiInferenceUse":if inference {"NOT_WANTED"} else {"UNKNOWN"}},"personalMark":mark}})
}

/// Validates pixels and active embedded manifest integrity; never grants trust.
pub fn inspect_bytes(bytes: &[u8]) -> Result<Value, Error> {
    if bytes.len() > contract::MAX_PRODUCT_OUTPUT_BYTES {
        return Err(Error::ResourceLimitExceeded);
    }
    let (format, _) = decoded(bytes)?;
    let reader =
        match Reader::from_context(context()?).with_stream(mime(format), Cursor::new(bytes)) {
            Ok(r) => r,
            Err(c2pa::Error::JumbfNotFound) => return Ok(absent(bytes, format)),
            Err(_) => return Err(Error::C2paMalformed),
        };
    if !reader.is_embedded()
        || reader.remote_url().is_some()
        || !matches!(
            reader.validation_state(),
            ValidationState::Valid | ValidationState::Trusted
        )
    {
        return Err(Error::IntegrityFailure);
    }
    let manifest = reader.active_manifest().ok_or(Error::C2paMalformed)?;
    let label = reader.active_label().ok_or(Error::C2paMalformed)?;
    let prefix = format!("self#jumbf=/c2pa/{label}/");
    let status = reader
        .validation_results()
        .and_then(|r| r.active_manifest())
        .ok_or(Error::IntegrityFailure)?;
    if !status.failure().is_empty() {
        return Err(Error::IntegrityFailure);
    }
    let has = |code: &str, url: &str| {
        status
            .success()
            .iter()
            .any(|s| s.code() == code && s.url() == Some(url))
    };
    let signature_url = format!("{prefix}c2pa.signature");
    if !has("claimSignature.validated", &signature_url)
        || !has("claimSignature.insideValidity", &signature_url)
    {
        return Err(Error::SignatureInvalid);
    }
    let references = manifest
        .assertion_references()
        .map(|r| r.url())
        .collect::<Vec<_>>();
    let unique = references.iter().collect::<std::collections::BTreeSet<_>>();
    if references.is_empty()
        || unique.len() != references.len()
        || references.iter().any(|r| {
            !r.starts_with(&format!("{prefix}c2pa.assertions/"))
                || !has("assertion.hashedURI.match", r)
        })
        || !references
            .iter()
            .any(|r| has("assertion.dataHash.match", r) || has("assertion.bmffHash.match", r))
    {
        return Err(Error::AssetBindingFailure);
    }
    let mut rights = None;
    let mut mark = Value::Null;
    for a in manifest.assertions() {
        if a.label() == "cawg.training-mining" || a.label().starts_with("cawg.training-mining__") {
            if rights.is_some() || a.instance() != 0 || a.label() != "cawg.training-mining" {
                return Err(Error::CawgUnsupported);
            }
            let expected = format!("{prefix}c2pa.assertions/cawg.training-mining");
            if !references.contains(&expected) || !has("assertion.hashedURI.match", &expected) {
                return Err(Error::IntegrityFailure);
            }
            rights = Some(a.value().map_err(|_| Error::CawgUnsupported)?.clone());
        }
        if a.label() == contract::PERSONAL_MARK_LABEL
            || a.label()
                .starts_with(&format!("{}__", contract::PERSONAL_MARK_LABEL))
        {
            if !mark.is_null() || a.instance() != 0 || a.label() != contract::PERSONAL_MARK_LABEL {
                return Err(Error::ResultInvalid);
            }
            let expected = format!("{prefix}c2pa.assertions/{}", contract::PERSONAL_MARK_LABEL);
            if !references.contains(&expected) || !has("assertion.hashedURI.match", &expected) {
                return Err(Error::IntegrityFailure);
            }
            let value = a.value().map_err(|_| Error::ResultInvalid)?;
            contract::validate_personal_mark(value).map_err(|_| Error::ResultInvalid)?;
            mark = value.clone();
        }
    }
    // Only the exact accepted signed assertion earns NOT_WANTED. Other CAWG
    // values remain honestly UNKNOWN, never inferred from loose nested fields.
    let training = rights
        .as_ref()
        .is_some_and(|r| *r == crate::expected_rights());
    let inference = training;
    let mut value = result(bytes, format, true, training, inference, mark);
    if rights.is_some() {
        value["inspection"]["cawg"]["presence"] = json!("PRESENT");
    }
    contract::validate_product_result(&value, "limited_inspect")
        .map_err(|_| Error::ResultInvalid)?;
    Ok(value)
}
pub fn inspect_path(path: &Path) -> Result<Value, Error> {
    let (mut pin, bytes) = read_pin(path, MAX_INSPECTION_BYTES)?;
    extension_matches(path, image_format(&bytes)?)?;
    let result = inspect_bytes(&bytes)?;
    unchanged(&mut pin, &bytes, MAX_INSPECTION_BYTES)?;
    regular(path)?;
    Ok(result)
}
fn extension_matches(path: &Path, format: image::ImageFormat) -> Result<(), Error> {
    let ext = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if (format == image::ImageFormat::Png && ext != "png")
        || (format == image::ImageFormat::Jpeg && !matches!(ext.as_str(), "jpg" | "jpeg"))
    {
        return Err(Error::UnsupportedFormat);
    }
    Ok(())
}
fn unchanged(file: &mut File, bytes: &[u8], maximum: usize) -> Result<(), Error> {
    file.seek(SeekFrom::Start(0)).map_err(unavailable)?;
    let mut current = Vec::new();
    (&mut *file)
        .take(maximum as u64 + 1)
        .read_to_end(&mut current)
        .map_err(unavailable)?;
    if current != bytes {
        return Err(Error::SourceChanged);
    }
    Ok(())
}
pub fn output_path(input: &Path) -> Result<PathBuf, Error> {
    let stem = input
        .file_stem()
        .and_then(|s| s.to_str())
        .ok_or(Error::InputUnavailable)?;
    let ext = input
        .extension()
        .and_then(|s| s.to_str())
        .ok_or(Error::UnsupportedFormat)?;
    if !matches!(ext.to_ascii_lowercase().as_str(), "png" | "jpg" | "jpeg") {
        return Err(Error::UnsupportedFormat);
    }
    Ok(input.with_file_name(format!("{stem}_rights.{ext}")))
}
/// Stage only. Caller owns the stage directory and cancellation-safe final publish.
pub fn add_stage(input: &Path, output: &Path, stage: &Path, mark: &Value) -> Result<Value, Error> {
    contract::validate_personal_mark(mark).map_err(|_| Error::ResultInvalid)?;
    if output_path(input)? != output
        || input == output
        || stage == input
        || stage == output
        || stage.parent().and_then(Path::parent) != output.parent()
    {
        return Err(Error::InputUnavailable);
    }
    InputLocator::new(output)?;
    InputLocator::new(stage)?;
    let parent = output.parent().ok_or(Error::InputUnavailable)?;
    regular(parent)?;
    regular(stage.parent().ok_or(Error::InputUnavailable)?)?;
    if output.try_exists().map_err(unavailable)?
        || fs::symlink_metadata(output).is_ok()
        || fs::symlink_metadata(stage).is_ok()
    {
        return Err(Error::InputUnavailable);
    }
    let (mut source, bytes) = read_pin(input, contract::MAX_PRODUCT_INPUT_BYTES)?;
    let (format, pixels) = decoded(&bytes)?;
    extension_matches(input, format)?;
    extension_matches(stage, format)?;
    let signed = sign(&bytes, format, mark)?;
    if signed.len() > contract::MAX_PRODUCT_OUTPUT_BYTES {
        return Err(Error::ResourceLimitExceeded);
    }
    let (_, output_pixels) = decoded(&signed)?;
    if pixels.to_rgba8() != output_pixels.to_rgba8() {
        return Err(Error::IntegrityFailure);
    }
    let inspected = inspect_bytes(&signed)?;
    if inspected["inspection"]["personalMark"] != *mark
        || inspected["inspection"]["cawg"]["aiTrainingUse"] != "NOT_WANTED"
        || inspected["inspection"]["cawg"]["aiInferenceUse"] != "NOT_WANTED"
    {
        return Err(Error::IntegrityFailure);
    }
    unchanged(&mut source, &bytes, contract::MAX_PRODUCT_INPUT_BYTES)?;
    regular(input)?;
    regular(parent)?;
    let mut options = OpenOptions::new();
    options.read(true).write(true).create_new(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.share_mode(0).custom_flags(0x00200000);
    }
    let mut f = options.open(stage).map_err(unavailable)?;
    let written = (|| {
        f.write_all(&signed).map_err(unavailable)?;
        f.sync_all().map_err(unavailable)?;
        f.seek(SeekFrom::Start(0)).map_err(unavailable)?;
        let mut staged = Vec::new();
        (&mut f)
            .take(contract::MAX_PRODUCT_OUTPUT_BYTES as u64 + 1)
            .read_to_end(&mut staged)
            .map_err(unavailable)?;
        if staged != signed {
            return Err(Error::IntegrityFailure);
        }
        unchanged(&mut source, &bytes, contract::MAX_PRODUCT_INPUT_BYTES)?;
        if output.try_exists().map_err(unavailable)? {
            return Err(Error::InputUnavailable);
        }
        let v = json!({"operation":"add","result":"ADD_STAGED","developmentSigning":true,"source":fingerprint(&bytes,format),
          "output":{"path":stage.to_str().ok_or(Error::InputUnavailable)?,"finalPath":output.to_str().ok_or(Error::InputUnavailable)?,"size":signed.len(),"sha256":sha(&signed)},"personalMark":mark});
        contract::validate_product_result(&v, "add").map_err(|_| Error::ResultInvalid)?;
        Ok(v)
    })();
    drop(f);
    if written.is_err() {
        let _ = fs::remove_file(stage);
    }
    written
}

#[cfg(debug_assertions)]
fn sign(bytes: &[u8], format: image::ImageFormat, mark: &Value) -> Result<Vec<u8>, Error> {
    let signer = c2pa::create_signer::from_keys(
        include_bytes!("development-signing/es256.pub"),
        include_bytes!("development-signing/es256.pem"),
        c2pa::SigningAlg::Es256,
        None,
    )
    .map_err(|_| Error::InternalSdkFailure)?;
    let mut builder = Builder::from_context(context()?).with_definition(json!({"title":"Shirushi development metadata","claim_generator_info":[{"name":"Shirushi development","version":"0.2"}]})).map_err(|_| Error::InternalSdkFailure)?;
    // SDK v2 claims require an initial opened/created action. Metadata Add
    // edits an existing image, it does not claim to create its pixels. The
    // supported Edit intent adds the source parent ingredient and a signed
    // c2pa.opened action referring to it. Without an intent, SDK defaults add
    // neither, and the unchanged Reader integrity checks correctly reject the
    // resulting assertion.action.malformed claim.
    builder.set_intent(c2pa::BuilderIntent::Edit);
    builder
        .add_assertion("cawg.training-mining", &crate::expected_rights())
        .map_err(|_| Error::InternalSdkFailure)?;
    builder
        .add_assertion(contract::PERSONAL_MARK_LABEL, mark)
        .map_err(|_| Error::InternalSdkFailure)?;
    let mut out = Cursor::new(Vec::new());
    builder
        .sign(
            signer.as_ref(),
            mime(format),
            &mut Cursor::new(bytes),
            &mut out,
        )
        .map_err(|_| Error::InternalSdkFailure)?;
    Ok(out.into_inner())
}
#[cfg(not(debug_assertions))]
fn sign(_: &[u8], _: image::ImageFormat, _: &Value) -> Result<Vec<u8>, Error> {
    Err(Error::ServiceUnavailable)
}
