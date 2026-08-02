# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Shared NCP v0.11 and NIP v0.13 conformance vectors."""

from __future__ import annotations

import base64
import dataclasses
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nps_sdk.core.frames import (
    EncodingTier,
    FrameFlags,
    FrameHeader,
    FrameType,
)
from nps_sdk.ncp.frames import HelloFrame
from nps_sdk.ncp.handshake_profile import (
    NcpHandshakeAction,
    NcpHandshakeProfile,
    evaluate_hello_header,
    evaluate_preamble,
    negotiate_handshake,
)
from nps_sdk.nip.ca_client import NipCaClient, NipCaCrl
from nps_sdk.nip.revocation_policy import (
    NipRevocationEvaluation,
    NipRevocationMode,
    NipRevocationOutcome,
    NipRevocationSource,
)


def _repo_file(relative: str) -> Path:
    for root in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        candidate = root / relative
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Unable to locate repository file: {relative}")


def _vectors(relative: str) -> list[dict]:
    data = json.loads(_repo_file(relative).read_text(encoding="utf-8"))
    return data["vectors"]


def test_ncp_native_server_handshake_vectors() -> None:
    tier = {
        "json": EncodingTier.JSON,
        "msgpack": EncodingTier.MSGPACK,
        "binary_vector": EncodingTier.BINARY_VECTOR,
    }
    for vector in _vectors(
        "spec/conformance/ncp/native_server_handshake_vectors.json"
    ):
        server_data = vector["input"]["server"]
        transport = vector["input"]["transport"]
        expected = vector["expected"]
        decision = evaluate_preamble(
            bytes.fromhex(transport["preamble_hex"]),
            transport["preamble_elapsed_ms"],
            server_data["preamble_timeout_ms"],
        )
        if decision.action == NcpHandshakeAction.CONTINUE and "first_frame_type" in transport:
            flags = FrameFlags(int(tier[transport["first_frame_tier"]]))
            if transport["first_frame_encrypted"]:
                flags |= FrameFlags.ENCRYPTED
            if transport["first_frame_extended"]:
                flags |= FrameFlags.EXT
            decision = evaluate_hello_header(
                FrameHeader(
                    FrameType(int(transport["first_frame_type"], 16)),
                    flags,
                    transport["hello_payload_length"],
                ),
                transport["hello_elapsed_ms"],
                server_data["hello_timeout_ms"],
                server_data["max_hello_payload"],
            )
        if decision.action == NcpHandshakeAction.CONTINUE and "hello" in vector["input"]:
            profile_fields = {
                field.name for field in dataclasses.fields(NcpHandshakeProfile)
            }
            profile = NcpHandshakeProfile(**{
                key: tuple(value) if isinstance(value, list) else value
                for key, value in server_data.items()
                if key in profile_fields
            })
            hello_data = dict(vector["input"]["hello"])
            for key in ("supported_encodings", "supported_protocols"):
                hello_data[key] = tuple(hello_data[key])
            decision = negotiate_handshake(profile, HelloFrame(**hello_data))

        assert decision.action.value == expected["action"], vector["id"]
        assert (decision.action == NcpHandshakeAction.ERROR_CLOSE) == expected["emit_error"]
        for field in (
            "diagnostic_error",
            "status",
            "error",
            "session_version",
            "negotiated_encoding",
            "max_frame_payload",
            "ext_support",
            "max_concurrent_streams",
        ):
            if field in expected:
                assert getattr(decision, field) == expected[field], vector["id"]
        for field in ("enabled_encodings", "supported_protocols"):
            if field in expected:
                assert list(getattr(decision, field) or ()) == expected[field], vector["id"]


def test_nip_revocation_policy_vectors() -> None:
    for vector in _vectors(
        "spec/conformance/nip/revocation_policy_vectors.json"
    ):
        source_input = vector["input"]
        expected = vector["expected"]
        evaluation = NipRevocationEvaluation(
            NipRevocationMode(source_input["revocation_mode"]),
            source_input["ocsp_fail_open"],
        )
        decision = None
        for observation in source_input["sources"]:
            decision = evaluation.observe(
                NipRevocationSource(observation["source"]),
                NipRevocationOutcome(observation["outcome"]),
            )
            if decision is not None:
                break
        decision = decision or evaluation.complete()
        assert decision.valid == expected["valid"], vector["id"]
        assert [x.value for x in evaluation.consulted_sources] == (
            expected["consulted_sources"]
        ), vector["id"]
        if not expected["valid"]:
            assert decision.failed_step == expected["failed_step"], vector["id"]
            assert decision.error_code == expected["error"], vector["id"]


def test_nip_signed_crl_vectors() -> None:
    for vector in _vectors("spec/conformance/nip/signed_crl_vectors.json"):
        input_data = vector["input"]
        canonical = json.dumps(
            input_data["body"], separators=(",", ":"), sort_keys=True)
        if "canonical_for_signing" in vector["expected"]:
            assert canonical == vector["expected"]["canonical_for_signing"]
        crl = NipCaCrl.from_dict({
            **input_data["body"],
            "signature": input_data["signature"],
        })
        assert NipCaClient.verify_crl_signature(
            crl, input_data["public_key"]
        ) == vector["expected"]["signature_valid"], vector["id"]

        if "private_seed_hex" in input_data:
            private_key = Ed25519PrivateKey.from_private_bytes(
                bytes.fromhex(input_data["private_seed_hex"]))
            signature = private_key.sign(canonical.encode("utf-8"))
            encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
            assert f"ed25519:{encoded}" == input_data["signature"], vector["id"]
