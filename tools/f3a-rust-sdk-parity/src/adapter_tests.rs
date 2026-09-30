//! PURE ADAPTER/CONTRACT TESTS. Synthetic evidence is NOT real SDK parity proof.
use super::*;

fn good() -> SdkEvidence {
    let prefix = "self#jumbf=/c2pa/urn:c2pa:test/";
    let refs = ["c2pa.hash.data", RIGHTS_LABEL, "c2pa.actions.v2"]
        .map(|label| format!("{prefix}c2pa.assertions/{label}"));
    let mut success: Vec<Status> = refs
        .iter()
        .map(|r| Status {
            code: "assertion.hashedURI.match".into(),
            url: Some(r.clone()),
        })
        .collect();
    for code in ["claimSignature.validated", "claimSignature.insideValidity"] {
        success.push(Status {
            code: code.into(),
            url: Some(format!("{prefix}c2pa.signature")),
        });
    }
    success.push(Status {
        code: "assertion.dataHash.match".into(),
        url: Some(refs[0].clone()),
    });
    SdkEvidence {
        parsed: true,
        embedded: true,
        active_label: Some("urn:c2pa:test".into()),
        validation_state: "VALID".into(),
        references: refs.to_vec(),
        success,
        informational: vec![],
        failure: vec![],
        legacy_statuses: vec![],
        ingredient_statuses: vec![],
        rights: vec![RightsAssertion {
            label: RIGHTS_LABEL.into(),
            instance: 0,
            value: expected_rights(),
        }],
    }
}

fn rejects(mutate: impl FnOnce(&mut SdkEvidence)) {
    let mut e = good();
    mutate(&mut e);
    assert_eq!(adapt(&e), Err(Failure::LimitedResultInvalid));
}

#[test]
fn exact_inner_oracle_and_outer_contract_preserved() {
    // Independent literal copied from the approved read-only oracle/Owner RUN.
    let expected: Value=serde_json::from_str(r#"{
      "contractVersion":2,"operation":"limited_c2pa_cawg_inspection",
      "result":"LIMITED_INSPECTION","completeness":"INCOMPLETE",
      "checks":{"c2pa":"INSPECTED","cawg":"INSPECTED","trustmark":"NOT_CHECKED"},
      "inspection":{"contract":"shirushi-limited-inspection","contractVersion":1,
      "overall":"LIMITED_INSPECTION","reasonCode":"LIMITED_SCOPE","trustmark":"NOT_CHECKED",
      "fullVerificationPerformed":false,"successMotionEligible":false,
      "source":{"sha256":"558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316","size":319495,"format":"PNG"},
      "c2pa":{"state":"INSPECTED","presence":"PRESENT","parse":true,"assertionDigestsValid":true,
      "assetBindingValid":true,"signature":"PREVIEW","trustValidated":false},
      "cawg":{"state":"INSPECTED","presence":"PRESENT","aiTrainingUse":"NOT_WANTED","aiInferenceUse":"NOT_WANTED"}}
    }"#).unwrap();
    let actual = adapt(&good()).unwrap();
    assert_eq!(actual, expected);
    assert_eq!(
        parse_envelope(&encode_envelope(&actual, 0).unwrap(), 0).unwrap(),
        expected
    );
}

#[test]
fn cawg_absent_rejected() {
    rejects(|e| e.rights.clear());
}
#[test]
fn cawg_unsupported_value_rejected() {
    rejects(|e| e.rights[0].value["entries"]["cawg.ai_inference"]["use"] = json!("allowed"));
}
#[test]
fn cawg_unknown_or_extra_entry_rejected() {
    rejects(|e| e.rights[0].value["entries"]["unknown"] = json!({"use":"notAllowed"}));
}
#[test]
fn ai_training_not_equivalent_to_generative_training() {
    rejects(|e| {
        e.rights[0].value["entries"]
            .as_object_mut()
            .unwrap()
            .remove("cawg.ai_generative_training");
        e.rights[0].value["entries"]["cawg.ai_training"] = json!({"use":"notAllowed"});
    });
}
#[test]
fn duplicate_or_ambiguous_cawg_rejected() {
    rejects(|e| e.rights.push(e.rights[0].clone()));
    rejects(|e| e.rights[0].instance = 2);
    rejects(|e| e.rights[0].instance = 1);
    rejects(|e| e.rights[0].label = "cawg.training-mining__2".into());
}
#[test]
fn absent_signed_reference_rejected() {
    rejects(|e| e.references.retain(|r| !r.ends_with(RIGHTS_LABEL)));
    rejects(|e| e.references.clear());
    rejects(|e| e.references.push(e.references[0].clone()));
}
#[test]
fn absent_digest_evidence_rejected_for_every_reference() {
    rejects(|e| {
        e.success
            .retain(|s| s.url.as_deref() != Some(&e.references[2]))
    });
    rejects(|e| e.success.retain(|s| s.code != "assertion.hashedURI.match"));
}
#[test]
fn absent_asset_binding_or_other_manifest_binding_rejected() {
    rejects(|e| e.success.retain(|s| s.code != "assertion.dataHash.match"));
    rejects(|e| {
        e.success
            .iter_mut()
            .filter(|s| s.code == "assertion.dataHash.match")
            .for_each(|s| {
                s.url = Some("self#jumbf=/c2pa/other/c2pa.assertions/c2pa.hash.data".into())
            });
    });
}
#[test]
fn absent_signature_and_wrong_signature_scope_rejected() {
    rejects(|e| e.success.retain(|s| s.code != "claimSignature.validated"));
    rejects(|e| {
        e.success
            .iter_mut()
            .filter(|s| s.code == "claimSignature.validated")
            .for_each(|s| s.url = Some("self#jumbf=/c2pa/other/c2pa.signature".into()));
    });
}
#[test]
fn signature_validity_is_not_trust_validity() {
    let v = adapt(&good()).unwrap();
    assert_eq!(v["inspection"]["c2pa"]["signature"], "PREVIEW");
    assert_eq!(v["inspection"]["c2pa"]["trustValidated"], false);
    rejects(|e| e.validation_state = "TRUSTED".into());
    rejects(|e| {
        e.success.push(Status {
            code: "signingCredential.trusted".into(),
            url: None,
        })
    });
    let mut e = good();
    e.failure.push(Status {
        code: "signingCredential.untrusted".into(),
        url: None,
    });
    assert!(adapt(&e).is_err());
}
#[test]
fn trustmark_and_full_motion_verification_remain_unchecked() {
    let v = adapt(&good()).unwrap();
    assert_eq!(v["inspection"]["trustmark"], "NOT_CHECKED");
    assert_eq!(v["inspection"]["fullVerificationPerformed"], false);
    assert_eq!(v["inspection"]["successMotionEligible"], false);
}
#[test]
fn unsupported_sdk_state_and_status_rejected() {
    rejects(|e| e.validation_state = "FUTURE_STATE".into());
    rejects(|e| e.validation_state = "INVALID".into());
    rejects(|e| {
        e.success.push(Status {
            code: "future.success".into(),
            url: None,
        })
    });
    rejects(|e| {
        e.informational.push(Status {
            code: "future.info".into(),
            url: None,
        })
    });
    rejects(|e| {
        e.failure.push(Status {
            code: "assertion.dataHash.mismatch".into(),
            url: None,
        })
    });
    rejects(|e| e.parsed = false);
    rejects(|e| e.embedded = false);
    rejects(|e| e.active_label = None);
}
#[test]
fn ingredient_or_cross_claim_evidence_cannot_satisfy_active_claim() {
    rejects(|e| {
        e.ingredient_statuses.push(Status {
            code: "claimSignature.validated".into(),
            url: None,
        })
    });
    rejects(|e| e.references[0] = "self#jumbf=/c2pa/other/c2pa.assertions/c2pa.hash.data".into());
}

#[test]
fn legacy_validation_status_cannot_be_silently_accepted() {
    rejects(|e| {
        e.legacy_statuses.push(Status {
            code: "signingCredential.untrusted".into(),
            url: None,
        })
    });
    rejects(|e| {
        e.legacy_statuses.push(Status {
            code: "claim.malformed".into(),
            url: None,
        })
    });
}
#[test]
fn old_outer_versions_and_values_rejected() {
    for (key, value) in [
        ("contractVersion", json!(1)),
        ("result", json!("INSPECTION_COMPLETED")),
        ("completeness", json!("LIMITED")),
    ] {
        let mut v = canonical_success();
        v[key] = value;
        assert!(validate_envelope(&v, 0).is_err());
    }
}
#[test]
fn unknown_outer_and_inner_fields_rejected() {
    let mut v = canonical_success();
    v["extra"] = json!(true);
    assert!(validate_envelope(&v, 0).is_err());
    let mut v = canonical_success();
    v["inspection"]["extra"] = json!(true);
    assert!(validate_envelope(&v, 0).is_err());
    let mut v = canonical_success();
    v["checks"]["extra"] = json!(true);
    assert!(validate_envelope(&v, 0).is_err());
}
#[test]
fn duplicate_keys_rejected_at_outer_and_inner_levels() {
    let raw = String::from_utf8(encode_envelope(&canonical_success(), 0).unwrap()).unwrap();
    for (needle, replacement) in [
        (
            "\"contractVersion\":2",
            "\"contractVersion\":2,\"contractVersion\":2",
        ),
        (
            "\"trustValidated\":false",
            "\"trustValidated\":false,\"trustValidated\":false",
        ),
    ] {
        assert!(parse_envelope(raw.replace(needle, replacement).as_bytes(), 0).is_err());
    }
}
#[test]
fn malformed_nonfinite_and_wrong_number_types_rejected() {
    let raw = String::from_utf8(encode_envelope(&canonical_success(), 0).unwrap()).unwrap();
    for replacement in ["NaN", "Infinity", "1e999", "2.0", "true", "null"] {
        assert!(parse_envelope(
            raw.replace(
                "\"contractVersion\":2",
                &format!("\"contractVersion\":{replacement}")
            )
            .as_bytes(),
            0
        )
        .is_err());
    }
    assert!(parse_envelope(format!("{raw}{{}}").as_bytes(), 0).is_err());
    assert!(parse_envelope(b"{", 0).is_err());
    assert!(parse_envelope(&vec![b' '; MAX_JSON_BYTES + 1], 0).is_err());
    let nested = format!("{}null{}", "[".repeat(20), "]".repeat(20));
    assert!(parse_envelope(nested.as_bytes(), 0).is_err());
    assert!(Number::from_f64(f64::NAN).is_none());
    let mut v = canonical_success();
    v["inspection"]["source"]["size"] = Value::Null;
    assert!(encode_envelope(&v, 0).is_err());
}
#[test]
fn invalid_inner_semantics_rejected() {
    for (key, value) in [
        ("contractVersion", json!(2)),
        ("overall", json!("VERIFIED")),
        ("trustmark", json!("VERIFIED")),
        ("fullVerificationPerformed", json!(true)),
        ("successMotionEligible", json!(true)),
    ] {
        let mut v = canonical_success();
        v["inspection"][key] = value;
        assert!(validate_envelope(&v, 0).is_err());
    }
}
#[test]
fn success_exit_inconsistency_rejected() {
    assert!(validate_envelope(&canonical_success(), 14).is_err());
    assert!(validate_envelope(&failure_envelope(Failure::LimitedResultInvalid), 0).is_err());
}
#[test]
fn failures_have_no_partial_inspection_or_private_fields() {
    for error in [
        Failure::FixtureChanged,
        Failure::FixtureInvalid,
        Failure::LimitedResultInvalid,
        Failure::ResultSerializationFailed,
    ] {
        let v = failure_envelope(error);
        assert!(v.get("inspection").is_none());
        assert_eq!(v.as_object().unwrap().len(), 4);
        assert!(parse_envelope(
            &encode_envelope(&v, error.exit_code()).unwrap(),
            error.exit_code()
        )
        .is_ok());
        let mut partial = v.clone();
        partial["inspection"] = canonical_success()["inspection"].clone();
        assert!(validate_envelope(&partial, error.exit_code()).is_err());
    }
}
