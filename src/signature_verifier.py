"""Independent COSE signature verification for Shirushi Preview credentials."""

from __future__ import annotations

from dataclasses import dataclass

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils


@dataclass(frozen=True)
class SignatureDetails:
    valid: bool
    algorithm: str
    subject: str
    issuer: str
    serial: str
    not_before_utc: str
    not_after_utc: str
    certificate_trust_validation_performed: bool = False
    method: str = "Python cryptography public-key verification using embedded leaf x5chain certificate"


class SignatureVerificationError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def verify_cose_signature(
    cert_der: bytes,
    data: bytes,
    signature: bytes,
    algorithm_code: int,
) -> SignatureDetails:
    """Verify ES256 or PS256 without performing certificate-chain trust validation."""

    try:
        certificate = x509.load_der_x509_certificate(cert_der)
    except (TypeError, ValueError) as exc:
        raise SignatureVerificationError("CERTIFICATE_PARSE_FAILURE", "embedded certificate could not be parsed") from exc

    try:
        public_key = certificate.public_key()
    except Exception as exc:
        raise SignatureVerificationError("CERTIFICATE_PARSE_FAILURE", "certificate public key could not be loaded") from exc
    try:
        if algorithm_code == -7:
            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                raise SignatureVerificationError("CERTIFICATE_KEY_MISMATCH", "ES256 requires an EC public key")
            if not isinstance(public_key.curve, ec.SECP256R1):
                raise SignatureVerificationError("CERTIFICATE_KEY_MISMATCH", "ES256 requires the P-256 curve")
            coordinate_size = (public_key.curve.key_size + 7) // 8
            if len(signature) != coordinate_size * 2:
                raise SignatureVerificationError("MALFORMED_SIGNATURE", "ES256 signature has an invalid length")
            r = int.from_bytes(signature[:coordinate_size], "big")
            s = int.from_bytes(signature[coordinate_size:], "big")
            if r == 0 or s == 0:
                raise SignatureVerificationError("MALFORMED_SIGNATURE", "ES256 signature contains an invalid integer")
            public_key.verify(utils.encode_dss_signature(r, s), data, ec.ECDSA(hashes.SHA256()))
            algorithm = "ES256"
        elif algorithm_code == -37:
            if not isinstance(public_key, rsa.RSAPublicKey):
                raise SignatureVerificationError("CERTIFICATE_KEY_MISMATCH", "PS256 requires an RSA public key")
            expected_size = (public_key.key_size + 7) // 8
            if len(signature) != expected_size:
                raise SignatureVerificationError("MALFORMED_SIGNATURE", "PS256 signature has an invalid length")
            public_key.verify(
                signature,
                data,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
                hashes.SHA256(),
            )
            algorithm = "PS256"
        else:
            raise SignatureVerificationError("UNSUPPORTED_ALGORITHM", f"unsupported COSE algorithm: {algorithm_code}")
    except InvalidSignature as exc:
        raise SignatureVerificationError("SIGNATURE_MISMATCH", "COSE claim signature is invalid") from exc
    except ValueError as exc:
        raise SignatureVerificationError("MALFORMED_SIGNATURE", "COSE signature is malformed") from exc

    return SignatureDetails(
        valid=True,
        algorithm=algorithm,
        subject=certificate.subject.rfc4514_string(),
        issuer=certificate.issuer.rfc4514_string(),
        serial=format(certificate.serial_number, "X"),
        not_before_utc=certificate.not_valid_before_utc.isoformat(),
        not_after_utc=certificate.not_valid_after_utc.isoformat(),
    )
