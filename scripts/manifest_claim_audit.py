"""Local-only C2PA manifest, claim, hashed-URI, and signature audit.

No input image is modified. No network lookup is performed. The script parses
the PNG caBX/JUMBF/CBOR bytes, verifies claim assertion digests over stored
JUMBF payload bytes, and verifies the detached COSE_Sign1 claim signature with
the leaf certificate embedded in the COSE protected x5chain header.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
import traceback
from typing import Any

if not getattr(sys, "frozen", False):
    source_directory = Path(__file__).resolve().parent.parent / "src"
    if str(source_directory) not in sys.path:
        sys.path.insert(0, str(source_directory))

from c2pa_utils import CborDecoder, json_default, sha256_file
from signature_verifier import SignatureVerificationError, verify_cose_signature


def read_box_header(data: bytes, offset: int, end: int) -> tuple[int, str, int]:
    if offset + 8 > end:
        raise ValueError(f"truncated box header at {offset}")
    size = int.from_bytes(data[offset : offset + 4], "big")
    kind = data[offset + 4 : offset + 8].decode("latin-1")
    header = 8
    if size == 1:
        if offset + 16 > end:
            raise ValueError(f"truncated extended box header at {offset}")
        size = int.from_bytes(data[offset + 8 : offset + 16], "big")
        header = 16
    elif size == 0:
        size = end - offset
    if size < header or offset + size > end:
        raise ValueError(f"invalid {kind!r} box size {size} at {offset}")
    return size, kind, header


def description_label(payload: bytes) -> str | None:
    if len(payload) < 18:
        return None
    label = payload[17:].split(b"\0", 1)[0]
    return label.decode("utf-8") if label else None


def extract_png_cabx(path: Path) -> tuple[bytes, int, list[dict[str, Any]], list[str]]:
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("invalid PNG signature")
    chunks: list[dict[str, Any]] = []
    provenance: list[str] = []
    cabx = None
    cabx_file_data_offset = None
    offset = 8
    while offset < len(raw):
        length = int.from_bytes(raw[offset : offset + 4], "big")
        kind = raw[offset + 4 : offset + 8].decode("ascii")
        data = raw[offset + 8 : offset + 8 + length]
        chunks.append({"type": kind, "offset": offset, "data_length": length})
        if kind == "caBX":
            cabx = data
            cabx_file_data_offset = offset + 8
        elif kind == "iTXt":
            text = data.decode("latin-1", errors="replace")
            provenance.extend(re.findall(r"https://cai-manifests\.adobe\.com/[^\s<\"]+", text))
        offset += 12 + length
        if kind == "IEND":
            break
    if cabx is None or cabx_file_data_offset is None:
        raise ValueError("no caBX chunk")
    return cabx, cabx_file_data_offset, chunks, provenance


def extract_jpeg_cabx(path: Path) -> tuple[bytes, int, list[dict[str, Any]], list[str]]:
    """Reassemble the C2PA JUMBF store from JPEG APP11 marker segments.

    The layout follows ISO/IEC 19566-5 as implemented by c2pa-rs: every
    APP11 payload starts with CI/En/Z; continuation packets additionally
    repeat the outer JUMBF LBox/TBox, which are omitted while reassembling.
    """
    raw = path.read_bytes()
    if raw[:2] != b"\xff\xd8":
        raise ValueError("invalid JPEG signature")
    segments: list[dict[str, Any]] = []
    app11: list[tuple[int, bytes]] = []
    provenance: list[str] = []
    offset = 2
    while offset < len(raw):
        if raw[offset] != 0xFF:
            raise ValueError(f"invalid JPEG marker prefix at {offset}")
        marker_start = offset
        while offset < len(raw) and raw[offset] == 0xFF:
            offset += 1
        if offset >= len(raw):
            raise ValueError("truncated JPEG marker")
        marker = raw[offset]
        offset += 1
        if marker == 0xD9:
            segments.append({"marker": "EOI", "offset": marker_start, "data_length": 0})
            break
        if marker == 0xDA:
            if offset + 2 > len(raw):
                raise ValueError("truncated JPEG SOS")
            length = int.from_bytes(raw[offset : offset + 2], "big")
            segments.append({"marker": "SOS", "offset": marker_start, "data_length": length - 2})
            break
        if marker in {0x01, *range(0xD0, 0xD8)}:
            segments.append({"marker": f"FF{marker:02X}", "offset": marker_start, "data_length": 0})
            continue
        if offset + 2 > len(raw):
            raise ValueError("truncated JPEG segment length")
        length = int.from_bytes(raw[offset : offset + 2], "big")
        if length < 2 or offset + length > len(raw):
            raise ValueError(f"invalid JPEG segment length at {marker_start}")
        payload_start = offset + 2
        payload = raw[payload_start : offset + length]
        name = f"APP{marker - 0xE0}" if 0xE0 <= marker <= 0xEF else f"FF{marker:02X}"
        segments.append({"marker": name, "offset": marker_start, "data_length": len(payload)})
        if marker == 0xEB and len(payload) > 16 and payload[:2] == b"JP":
            app11.append((payload_start, payload))
        if marker == 0xE1:
            text = payload.decode("latin-1", errors="replace")
            provenance.extend(re.findall(r"https://cai-manifests\.adobe\.com/[^\s<\"]+", text))
        offset += length

    first_index = None
    for index, (_, payload) in enumerate(app11):
        if len(payload) > 28 and payload[24:28] == b"c2pa":
            first_index = index
            break
    if first_index is None:
        raise ValueError("no C2PA JUMBF APP11 segments")
    first_offset, first = app11[first_index]
    instance = first[2:4]
    expected_sequence = 1
    cabx = bytearray()
    for payload_offset, payload in app11[first_index:]:
        if payload[:2] != b"JP" or payload[2:4] != instance:
            break
        sequence = int.from_bytes(payload[4:8], "big")
        if sequence != expected_sequence:
            raise ValueError(f"non-contiguous C2PA APP11 sequence: {sequence}")
        cabx.extend(payload[8:] if sequence == 1 else payload[16:])
        expected_sequence += 1
    if not cabx:
        raise ValueError("empty C2PA JUMBF APP11 payload")
    # Offset is exact for the first packet and diagnostic-only for later
    # reassembled bytes, whose APP11 headers are not represented in cabx.
    return bytes(cabx), first_offset + 8, segments, provenance


def extract_c2pa_jumbf(path: Path) -> tuple[bytes, int, list[dict[str, Any]], list[str], str]:
    """Extract a C2PA JUMBF store and report its containing image format."""
    signature = path.read_bytes()[:8]
    if signature == b"\x89PNG\r\n\x1a\n":
        cabx, data_offset, structure, provenance = extract_png_cabx(path)
        return cabx, data_offset, structure, provenance, "PNG"
    if signature[:2] == b"\xff\xd8":
        cabx, data_offset, structure, provenance = extract_jpeg_cabx(path)
        return cabx, data_offset, structure, provenance, "JPEG"
    raise ValueError("unsupported image signature")


def parse_jumbf(cabx: bytes) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def parse_superbox(start: int, size: int, header: int, parents: tuple[str, ...]) -> None:
        payload_start = start + header
        end = start + size
        child_boxes: list[dict[str, Any]] = []
        offset = payload_start
        while offset < end:
            child_size, kind, child_header = read_box_header(cabx, offset, end)
            child_boxes.append(
                {
                    "start": offset,
                    "size": child_size,
                    "type": kind,
                    "header_size": child_header,
                    "payload_start": offset + child_header,
                    "end": offset + child_size,
                }
            )
            offset += child_size
        label = None
        for child in child_boxes:
            if child["type"] == "jumd":
                label = description_label(cabx[child["payload_start"] : child["end"]])
                break
        path = parents + ((label,) if label else ())
        node = {
            "label": label,
            "path": path,
            "start": start,
            "size": size,
            "header_size": header,
            "payload_start": payload_start,
            "end": end,
            "children": child_boxes,
        }
        nodes.append(node)
        for child in child_boxes:
            if child["type"] == "jumb":
                parse_superbox(child["start"], child["size"], child["header_size"], path)

    offset = 0
    while offset < len(cabx):
        size, kind, header = read_box_header(cabx, offset, len(cabx))
        if kind != "jumb":
            raise ValueError(f"unexpected top-level caBX box: {kind}")
        parse_superbox(offset, size, header, ())
        offset += size
    return nodes


def node_cbor(cabx: bytes, node: dict[str, Any]) -> tuple[Any, bytes, dict[str, Any]]:
    content = next((child for child in node["children"] if child["type"] == "cbor"), None)
    if content is None:
        raise ValueError(f"node has no CBOR content: {node['path']}")
    payload = cabx[content["payload_start"] : content["end"]]
    decoder = CborDecoder(payload)
    value = decoder.value()
    if decoder.offset != len(payload):
        raise ValueError(f"unconsumed CBOR bytes: {node['path']}")
    return value, payload, content


def public_node(node: dict[str, Any], cabx_file_data_offset: int) -> dict[str, Any]:
    return {
        "label": node["label"],
        "path": list(node["path"]),
        "caBX_offset": node["start"],
        "png_file_offset": cabx_file_data_offset + node["start"],
        "box_size": node["size"],
        "hash_payload_offset": node["payload_start"],
        "hash_payload_length": node["end"] - node["payload_start"],
    }


def cbor_head(major: int, length: int) -> bytes:
    if length < 24:
        return bytes([(major << 5) | length])
    if length < 256:
        return bytes([(major << 5) | 24, length])
    if length < 65536:
        return bytes([(major << 5) | 25]) + length.to_bytes(2, "big")
    if length < 2**32:
        return bytes([(major << 5) | 26]) + length.to_bytes(4, "big")
    return bytes([(major << 5) | 27]) + length.to_bytes(8, "big")


def cbor_bytes(value: bytes) -> bytes:
    return cbor_head(2, len(value)) + value


def cbor_text(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return cbor_head(3, len(encoded)) + encoded


def build_cose_sig_structure(protected: bytes, detached_payload: bytes) -> bytes:
    # RFC 9052 Sig_structure = [context, body_protected, external_aad, payload]
    return (
        cbor_head(4, 4)
        + cbor_text("Signature1")
        + cbor_bytes(protected)
        + cbor_bytes(b"")
        + cbor_bytes(detached_payload)
    )


def verify_ps256_with_python(cert: bytes, data: bytes, signature: bytes) -> dict[str, Any]:
    try:
        return asdict(verify_cose_signature(cert, data, signature, -37))
    except SignatureVerificationError as exc:
        raise RuntimeError(f"Python signature verification failed: {exc}") from exc


def find_active_manifest(
    nodes: list[dict[str, Any]], provenance: list[str]
) -> dict[str, Any]:
    manifests = [
        node
        for node in nodes
        if node["label"] and node["label"].startswith("urn:c2pa:") and len(node["path"]) == 2
    ]
    matches = [
        node
        for node in manifests
        if any(node["label"].replace(":", "-") in url for url in provenance)
    ]
    if len(matches) != 1:
        raise ValueError(f"unable to select one active manifest from provenance: {len(matches)}")
    return matches[0]


def resolve_uri_node(
    uri: str, active_manifest: dict[str, Any], nodes: list[dict[str, Any]]
) -> dict[str, Any]:
    prefix = "self#jumbf="
    if not uri.startswith(prefix):
        raise ValueError(f"unsupported URI: {uri}")
    target = uri[len(prefix) :]
    if target.startswith("/"):
        path = tuple(part for part in target[1:].split("/") if part)
    else:
        path = active_manifest["path"] + tuple(part for part in target.split("/") if part)
    matches = [node for node in nodes if node["path"] == path]
    if len(matches) != 1:
        raise ValueError(f"URI resolved to {len(matches)} nodes: {uri} -> {path}")
    return matches[0]


def verify_hashed_uri(
    reference: dict[str, Any],
    active_manifest: dict[str, Any],
    nodes: list[dict[str, Any]],
    cabx: bytes,
    default_alg: str,
    cabx_file_data_offset: int,
) -> dict[str, Any]:
    node = resolve_uri_node(reference["url"], active_manifest, nodes)
    algorithm = reference.get("alg", default_alg)
    stored = reference["hash"]
    if not isinstance(stored, bytes):
        raise ValueError("hashed URI digest is not a CBOR byte string")
    # C2PA hashed URI matches the stored JUMBF superbox payload bytes: the
    # superbox's outer size/type header is excluded; jumd + content boxes remain.
    hash_input = cabx[node["payload_start"] : node["end"]]
    recomputed = hashlib.new(algorithm, hash_input).digest()
    return {
        "url": reference["url"],
        "reference_group": None,
        "algorithm": algorithm,
        "stored_digest_hex": stored.hex(),
        "recomputed_digest_hex": recomputed.hex(),
        "match": stored == recomputed,
        "target": public_node(node, cabx_file_data_offset),
        "hash_input": {
            "rule": "JUMBF superbox payload bytes (outer size/type header excluded)",
            "length": len(hash_input),
            "sha256": hashlib.sha256(hash_input).hexdigest(),
            "canonicalization": "none; hash exact stored bytes",
        },
    }


def configure_logging(path: Path) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("manifest_claim_audit")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (logging.FileHandler(path, mode="w", encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--stripped", required=True, type=Path)
    parser.add_argument("--previous-audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = configure_logging(args.log)
    result: dict[str, Any] = {
        "status": "RUNNING",
        "level": None,
        "local_only": True,
        "network_lookup_performed": False,
        "inputs": {
            "baseline": {"path": str(args.baseline.resolve()), "sha256": sha256_file(args.baseline)},
            "stripped": {"path": str(args.stripped.resolve()), "sha256": sha256_file(args.stripped)},
            "previous_audit": str(args.previous_audit.resolve()),
        },
    }
    try:
        logger.info("Parsing baseline PNG caBX/JUMBF/CBOR")
        cabx, file_data_offset, png_chunks, provenance = extract_png_cabx(args.baseline)
        nodes = parse_jumbf(cabx)
        active = find_active_manifest(nodes, provenance)
        manifests = [
            public_node(node, file_data_offset)
            for node in nodes
            if node["label"] and node["label"].startswith("urn:c2pa:") and len(node["path"]) == 2
        ]
        claim_node = next(
            node
            for node in nodes
            if node["path"] == active["path"] + ("c2pa.claim.v2",)
        )
        signature_node = next(
            node
            for node in nodes
            if node["path"] == active["path"] + ("c2pa.signature",)
        )
        claim, claim_cbor_payload, claim_content_box = node_cbor(cabx, claim_node)
        signature_object, signature_cbor_payload, _ = node_cbor(cabx, signature_node)
        logger.info("Active manifest=%s", active["label"])

        claim_algorithm = claim["alg"]
        reference_results = []
        for group in ("created_assertions", "gathered_assertions"):
            for reference in claim.get(group, []):
                check = verify_hashed_uri(
                    reference, active, nodes, cabx, claim_algorithm, file_data_offset
                )
                check["reference_group"] = group
                reference_results.append(check)
        all_reference_hashes_match = bool(reference_results) and all(
            item["match"] for item in reference_results
        )
        logger.info(
            "Verified %d assertion references; all_match=%s",
            len(reference_results),
            all_reference_hashes_match,
        )

        soft_nodes = [
            node
            for node in nodes
            if node["path"]
            in (
                active["path"] + ("c2pa.assertions", "c2pa.soft-binding"),
                active["path"] + ("c2pa.assertions", "c2pa.soft-binding__1"),
            )
        ]
        soft_bindings = []
        for node in soft_nodes:
            value, _, _ = node_cbor(cabx, node)
            reference = next(
                item for item in reference_results if item["target"]["path"] == list(node["path"])
            )
            soft_bindings.append(
                {"node": public_node(node, file_data_offset), "assertion": value, "claim_reference": reference}
            )

        ingredient_node = next(
            node
            for node in nodes
            if node["path"] == active["path"] + ("c2pa.assertions", "c2pa.ingredient.v3")
        )
        ingredient, _, _ = node_cbor(cabx, ingredient_node)
        ingredient_links = []
        for name in ("activeManifest", "claimSignature"):
            if name in ingredient:
                link = verify_hashed_uri(
                    ingredient[name], active, nodes, cabx, ingredient[name].get("alg", "sha256"), file_data_offset
                )
                link["relationship"] = name
                ingredient_links.append(link)

        if not isinstance(signature_object, dict) or signature_object.get("cbor_tag") != 18:
            raise ValueError("signature CBOR is not tagged COSE_Sign1")
        cose = signature_object["value"]
        if not isinstance(cose, list) or len(cose) != 4:
            raise ValueError("invalid COSE_Sign1 array")
        protected_bytes, unprotected, detached_payload, signature_bytes = cose
        protected = CborDecoder(protected_bytes).value()
        algorithm_code = protected.get(1)
        certificates = protected.get(33)
        if algorithm_code != -37:
            raise ValueError(f"expected COSE PS256 (-37), got {algorithm_code}")
        if not isinstance(certificates, list) or not certificates:
            raise ValueError("COSE protected x5chain is missing")
        if detached_payload is not None:
            raise ValueError("COSE payload is not detached")
        sig_structure = build_cose_sig_structure(protected_bytes, claim_cbor_payload)
        signature_verification = verify_ps256_with_python(
            certificates[0], sig_structure, signature_bytes
        )
        logger.info("Detached COSE PS256 signature valid=%s", signature_verification["valid"])

        assertion_labels = [
            node["label"]
            for node in nodes
            if len(node["path"]) >= 4
            and node["path"][:3] == active["path"] + ("c2pa.assertions",)
        ]
        result.update(
            {
                "manifest_store": {
                    "png_chunks": png_chunks,
                    "caBX_file_data_offset": file_data_offset,
                    "manifest_count": len(manifests),
                    "manifests": manifests,
                    "content_credentials_provenance": provenance,
                },
                "active_manifest": {
                    **public_node(active, file_data_offset),
                    "selection_basis": "iTXt dcterms:provenance label match",
                    "claim": {
                        **public_node(claim_node, file_data_offset),
                        "instanceID": claim.get("instanceID"),
                        "dc:title": claim.get("dc:title"),
                        "claim_generator_info": claim.get("claim_generator_info"),
                        "alg": claim_algorithm,
                        "signature_uri": claim.get("signature"),
                        "created_assertions": claim.get("created_assertions", []),
                        "gathered_assertions": claim.get("gathered_assertions", []),
                        "redacted_assertions": claim.get("redacted_assertions", []),
                        "cbor_payload_length": len(claim_cbor_payload),
                        "cbor_payload_sha256": hashlib.sha256(claim_cbor_payload).hexdigest(),
                    },
                    "assertion_labels": assertion_labels,
                },
                "assertion_reference_integrity": {
                    "hash_input_rule": "exact stored JUMBF superbox payload bytes; outer 8-byte size/type header excluded",
                    "canonicalization": "none",
                    "reference_count": len(reference_results),
                    "all_match": all_reference_hashes_match,
                    "references": reference_results,
                },
                "soft_bindings": soft_bindings,
                "ingredient_relationship": {
                    "relationship": ingredient.get("relationship"),
                    "dc:title": ingredient.get("dc:title"),
                    "instanceID": ingredient.get("instanceID"),
                    "links": ingredient_links,
                    "embedded_validation_codes": {
                        status: [entry.get("code") for entry in ingredient.get("validationResults", {}).get("activeManifest", {}).get(status, [])]
                        for status in ("success", "informational", "failure")
                    },
                },
                "signature": {
                    **public_node(signature_node, file_data_offset),
                    "claim_signature_uri_matches_node": resolve_uri_node(
                        claim["signature"], active, nodes
                    )["path"]
                    == signature_node["path"],
                    "format": "COSE_Sign1",
                    "cbor_tag": 18,
                    "payload": None,
                    "payload_mode": "detached",
                    "protected_header": {
                        "algorithm_code": algorithm_code,
                        "algorithm": "PS256",
                        "x5chain_header_label": 33,
                        "certificate_count": len(certificates),
                        "certificates": [
                            {
                                "index": index,
                                "length": len(cert),
                                "sha256": hashlib.sha256(cert).hexdigest(),
                            }
                            for index, cert in enumerate(certificates)
                        ],
                    },
                    "unprotected_header_keys": [str(key) for key in unprotected],
                    "signature_length": len(signature_bytes),
                    "signed_payload": {
                        "source": "active c2pa.claim.v2 CBOR content-box payload bytes",
                        "caBX_offset": claim_content_box["payload_start"],
                        "length": len(claim_cbor_payload),
                        "sha256": hashlib.sha256(claim_cbor_payload).hexdigest(),
                        "cose_sig_structure_length": len(sig_structure),
                    },
                    "local_signature_verification": {
                        "method": "Python cryptography public-key verification using embedded leaf x5chain certificate",
                        **signature_verification,
                        "certificate_trust_validation_performed": False,
                    },
                },
            }
        )
        trustmark_soft = next(
            item for item in soft_bindings if item["node"]["label"] == "c2pa.soft-binding"
        )
        dense_soft = next(
            item for item in soft_bindings if item["node"]["label"] == "c2pa.soft-binding__1"
        )
        result["integrity_chain"] = {
            "trustmark_soft_binding": trustmark_soft["assertion"],
            "trustmark_hashed_uri_match": trustmark_soft["claim_reference"]["match"],
            "parallel_dense_soft_binding": dense_soft["assertion"],
            "parallel_dense_hashed_uri_match": dense_soft["claim_reference"]["match"],
            "same_claim": True,
            "parallel_independent_references": True,
            "claim_signature_valid": signature_verification["valid"],
            "modification_effect": (
                "Changing an assertion breaks its hashed-URI digest; changing the digest in the claim "
                "changes the signed claim CBOR and breaks the detached COSE signature unless re-signed."
            ),
        }
        structure_pass = len(soft_bindings) == 2
        integrity_pass = structure_pass and all_reference_hashes_match
        signed_chain_pass = integrity_pass and bool(signature_verification["valid"])
        result["levels"] = {
            "structure": "PASS" if structure_pass else "FAIL",
            "integrity_link": "PASS" if integrity_pass else "FAIL",
            "signed_chain": "PASS" if signed_chain_pass else "FAIL",
        }
        if signed_chain_pass:
            result["status"] = "PASS"
            result["level"] = "C"
        elif integrity_pass:
            result["status"] = "PARTIAL"
            result["level"] = "B"
        elif structure_pass:
            result["status"] = "PARTIAL"
            result["level"] = "A"
        else:
            result["status"] = "STOP"
        logger.info("Audit result status=%s level=%s", result["status"], result["level"])
    except Exception:
        result["status"] = "STOP"
        result["level"] = None
        result["exception"] = traceback.format_exc()
        logger.exception("Manifest/claim audit stopped")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=json_default) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote %s", args.output)
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
