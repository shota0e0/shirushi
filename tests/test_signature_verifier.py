from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.metadata
from pathlib import Path
import sys
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
SCRIPTS = PROJECT / "scripts"
for directory in (SRC, SCRIPTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from cryptography.x509.oid import NameOID

from signature_verifier import SignatureVerificationError, verify_cose_signature
from creator_verify import VerificationFailure, _verify_cose_signature


def certificate_for(private_key) -> bytes:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Shirushi test fixture")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.DER)


class SignatureVerifierTests(unittest.TestCase):
    def test_runtime_dependency_is_pinned(self) -> None:
        self.assertEqual("50.0.1", importlib.metadata.version("cryptography"))
        requirements = (PROJECT / "requirements-release-runtime.txt").read_text(encoding="utf-8")
        self.assertIn("cryptography==50.0.1", requirements.splitlines())

    def test_es256_known_good_raw_cose_signature(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        data = b"known-good ES256 claim fixture"
        der_signature = key.sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(der_signature)
        raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        result = verify_cose_signature(certificate_for(key), data, raw_signature, -7)
        self.assertTrue(result.valid)
        self.assertEqual("ES256", result.algorithm)
        self.assertFalse(result.certificate_trust_validation_performed)

    def test_ps256_known_good_uses_sha256_mgf1_and_digest_salt(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        data = b"known-good PS256 claim fixture"
        signature = key.sign(
            data,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )
        result = verify_cose_signature(certificate_for(key), data, signature, -37)
        self.assertTrue(result.valid)
        self.assertEqual("PS256", result.algorithm)

    def test_signed_payload_and_signature_tamper_fail_closed(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        data = b"signed payload"
        der_signature = key.sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(der_signature)
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        with self.assertRaises(SignatureVerificationError) as payload_failure:
            verify_cose_signature(certificate_for(key), data + b"!", signature, -7)
        self.assertEqual("SIGNATURE_MISMATCH", payload_failure.exception.kind)
        altered = bytearray(signature)
        altered[-1] ^= 1
        with self.assertRaises(SignatureVerificationError) as signature_failure:
            verify_cose_signature(certificate_for(key), data, bytes(altered), -7)
        self.assertEqual("SIGNATURE_MISMATCH", signature_failure.exception.kind)

    def test_failure_types_are_distinct(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        certificate = certificate_for(key)
        cases = (
            (b"not a certificate", b"x" * 64, -7, "CERTIFICATE_PARSE_FAILURE"),
            (certificate, b"short", -7, "MALFORMED_SIGNATURE"),
            (certificate, b"x" * 64, -999, "UNSUPPORTED_ALGORITHM"),
        )
        for cert, signature, algorithm, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(SignatureVerificationError) as failure:
                verify_cose_signature(cert, b"data", signature, algorithm)
            self.assertEqual(expected, failure.exception.kind)

    def test_algorithm_and_certificate_key_type_mismatch_is_distinct(self) -> None:
        ec_key = ec.generate_private_key(ec.SECP256R1())
        wrong_curve_key = ec.generate_private_key(ec.SECP384R1())
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cases = (
            (certificate_for(rsa_key), b"x" * 64, -7),
            (certificate_for(wrong_curve_key), b"x" * 96, -7),
            (certificate_for(ec_key), b"x" * 256, -37),
        )
        for certificate, signature, algorithm in cases:
            with self.subTest(algorithm=algorithm), self.assertRaises(SignatureVerificationError) as failure:
                verify_cose_signature(certificate, b"data", signature, algorithm)
            self.assertEqual("CERTIFICATE_KEY_MISMATCH", failure.exception.kind)

    def test_runtime_verifier_has_no_pwsh_dependency(self) -> None:
        for relative in ("scripts/creator_verify.py", "scripts/manifest_claim_audit.py"):
            source = (PROJECT / relative).read_text(encoding="utf-8")
            with self.subTest(relative=relative):
                self.assertNotIn('which("pwsh")', source)
                self.assertNotIn("RSA.VerifyData", source)
                self.assertNotIn("E2E_CERT_B64", source)

    def test_contract_wrapper_preserves_stable_failure_codes(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        certificate = certificate_for(key)
        with self.assertRaises(VerificationFailure) as malformed:
            _verify_cose_signature(certificate, b"data", b"short", -7)
        self.assertEqual("SIGNATURE_VERIFICATION_ERROR", malformed.exception.reason_code)

        data = b"signed data"
        der_signature = key.sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(der_signature)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        with self.assertRaises(VerificationFailure) as mismatch:
            _verify_cose_signature(certificate, data + b"!", raw, -7)
        self.assertEqual("SIGNATURE_INVALID", mismatch.exception.reason_code)

    def test_contract_wrapper_fails_closed_on_internal_verifier_error(self) -> None:
        with mock.patch("creator_verify.verify_cose_signature", side_effect=RuntimeError("internal failure")):
            with self.assertRaises(VerificationFailure) as failure:
                _verify_cose_signature(b"certificate", b"data", b"signature", -7)
        self.assertEqual("FAIL_SIGNATURE", failure.exception.result)
        self.assertEqual("SIGNATURE_VERIFICATION_ERROR", failure.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
