//! Shared development wire validation. No SDK, path lookup, signing or execution.
//! UI v1 mark is metadata only, not the formal Production personal-mark schema.
use serde_json::Value;

pub const MAX_PRODUCT_INPUT_BYTES: usize = 32 * 1024 * 1024;
pub const MAX_PRODUCT_OUTPUT_BYTES: usize = 64 * 1024 * 1024;
pub const MAX_PRODUCT_REQUEST_BYTES: usize = 64 * 1024;
pub const PERSONAL_MARK_LABEL: &str = "org.shirushi.development.personal-mark";

fn keys(v: &Value, names: &[&str]) -> bool {
    v.as_object()
        .is_some_and(|o| o.len() == names.len() && names.iter().all(|k| o.contains_key(*k)))
}
fn sha(v: &Value) -> bool {
    v.as_str().is_some_and(|s| {
        s.len() == 64
            && s.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}
fn source(v: &Value) -> bool {
    keys(v, &["sha256", "size", "format"])
        && sha(&v["sha256"])
        && v["size"]
            .as_u64()
            .is_some_and(|n| n > 0 && n <= MAX_PRODUCT_OUTPUT_BYTES as u64)
        && matches!(v["format"].as_str(), Some("PNG" | "JPEG"))
}
fn path(v: &Value) -> bool {
    v.as_str()
        .is_some_and(|s| !s.is_empty() && s.len() <= 4096 && !s.contains('\0'))
}

pub fn validate_personal_mark(v: &Value) -> Result<(), &'static str> {
    if !keys(v, &["version", "mode", "typed", "handwritten"])
        || v["version"].as_u64() != Some(1)
        || !matches!(v["mode"].as_str(), Some("typed" | "handwritten"))
    {
        return Err("INVALID_PERSONAL_MARK");
    }
    let text = v["typed"].as_str().ok_or("INVALID_PERSONAL_MARK")?;
    if text.chars().count() > 48 || text.contains('\0') || text.trim() != text {
        return Err("INVALID_PERSONAL_MARK");
    }
    let h = &v["handwritten"];
    if !keys(h, &["coordinateSpace", "strokes"])
        || !keys(&h["coordinateSpace"], &["width", "height"])
    {
        return Err("INVALID_PERSONAL_MARK");
    }
    for axis in ["width", "height"] {
        if !h["coordinateSpace"][axis]
            .as_f64()
            .is_some_and(|n| n.is_finite() && n > 0.0 && n <= 16384.0)
        {
            return Err("INVALID_PERSONAL_MARK");
        }
    }
    let strokes = h["strokes"].as_array().ok_or("INVALID_PERSONAL_MARK")?;
    if strokes.len() > 128 {
        return Err("INVALID_PERSONAL_MARK");
    }
    let mut total = 0;
    for stroke in strokes {
        let points = stroke.as_array().ok_or("INVALID_PERSONAL_MARK")?;
        total += points.len();
        if points.is_empty() || points.len() > 4096 || total > 4096 {
            return Err("INVALID_PERSONAL_MARK");
        }
        for point in points {
            if !keys(point, &["x", "y"])
                || ["x", "y"].iter().any(|axis| {
                    !point[axis]
                        .as_f64()
                        .is_some_and(|n| n.is_finite() && (0.0..=1.0).contains(&n))
                })
            {
                return Err("INVALID_PERSONAL_MARK");
            }
        }
    }
    if (v["mode"] == "typed" && text.is_empty())
        || (v["mode"] == "handwritten" && strokes.is_empty())
    {
        return Err("INVALID_PERSONAL_MARK");
    }
    Ok(())
}

pub fn validate_product_result(v: &Value, expected_operation: &str) -> Result<(), &'static str> {
    let invalid = "INVALID_PRODUCT_RESULT";
    if expected_operation == "add" {
        if !keys(
            v,
            &[
                "operation",
                "result",
                "developmentSigning",
                "source",
                "output",
                "personalMark",
            ],
        ) || v["operation"] != "add"
            || v["result"] != "ADD_STAGED"
            || v["developmentSigning"] != true
            || !source(&v["source"])
            || !keys(&v["output"], &["path", "finalPath", "size", "sha256"])
            || !path(&v["output"]["path"])
            || !path(&v["output"]["finalPath"])
            || v["output"]["path"] == v["output"]["finalPath"]
            || !sha(&v["output"]["sha256"])
            || !v["output"]["size"]
                .as_u64()
                .is_some_and(|n| n > 0 && n <= MAX_PRODUCT_OUTPUT_BYTES as u64)
        {
            return Err(invalid);
        }
        return validate_personal_mark(&v["personalMark"]);
    }
    if expected_operation != "limited_inspect"
        || !keys(
            v,
            &[
                "contractVersion",
                "operation",
                "result",
                "completeness",
                "checks",
                "inspection",
            ],
        )
        || v["contractVersion"].as_u64() != Some(2)
        || v["operation"] != "limited_c2pa_cawg_inspection"
        || v["result"] != "LIMITED_INSPECTION"
        || v["completeness"] != "INCOMPLETE"
        || v["checks"]
            != serde_json::json!({"c2pa":"INSPECTED","cawg":"INSPECTED","trustmark":"NOT_CHECKED"})
    {
        return Err(invalid);
    }
    let i = &v["inspection"];
    if !keys(
        i,
        &[
            "contract",
            "contractVersion",
            "overall",
            "reasonCode",
            "trustmark",
            "fullVerificationPerformed",
            "successMotionEligible",
            "source",
            "c2pa",
            "cawg",
            "personalMark",
        ],
    ) || i["contract"] != "shirushi-limited-inspection"
        || i["contractVersion"].as_u64() != Some(1)
        || i["overall"] != "LIMITED_INSPECTION"
        || i["reasonCode"] != "LIMITED_SCOPE"
        || i["trustmark"] != "NOT_CHECKED"
        || i["fullVerificationPerformed"] != false
        || i["successMotionEligible"] != false
        || !source(&i["source"])
    {
        return Err(invalid);
    }
    let c = &i["c2pa"];
    if !keys(
        c,
        &[
            "state",
            "presence",
            "parse",
            "assertionDigestsValid",
            "assetBindingValid",
            "signature",
            "trustValidated",
        ],
    ) || c["state"] != "INSPECTED"
        || c["trustValidated"] != false
    {
        return Err(invalid);
    }
    let present = c["presence"] == "PRESENT";
    if present {
        if c["parse"] != true
            || c["assertionDigestsValid"] != true
            || c["assetBindingValid"] != true
            || c["signature"] != "PREVIEW"
        {
            return Err(invalid);
        }
    } else if c["presence"] != "ABSENT"
        || c["parse"] != false
        || c["assertionDigestsValid"] != false
        || c["assetBindingValid"] != false
        || c["signature"] != "ABSENT"
    {
        return Err(invalid);
    }
    let w = &i["cawg"];
    if !keys(w, &["state", "presence", "aiTrainingUse", "aiInferenceUse"])
        || w["state"] != "INSPECTED"
    {
        return Err(invalid);
    }
    if w["presence"] == "PRESENT" {
        if !present
            || ["aiTrainingUse", "aiInferenceUse"]
                .iter()
                .any(|k| !matches!(w[k].as_str(), Some("NOT_WANTED" | "UNKNOWN")))
        {
            return Err(invalid);
        }
    } else if w["presence"] != "ABSENT"
        || w["aiTrainingUse"] != "UNKNOWN"
        || w["aiInferenceUse"] != "UNKNOWN"
    {
        return Err(invalid);
    }
    if !i["personalMark"].is_null() {
        if !present {
            return Err(invalid);
        }
        validate_personal_mark(&i["personalMark"])?;
    }
    Ok(())
}
