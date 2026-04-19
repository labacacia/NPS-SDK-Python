# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for NIP frame dataclasses and NipIdentity."""

import os
import tempfile
import pytest

from nps_sdk.nip.frames import IdentFrame, IdentMetadata, RevokeFrame, TrustFrame
from nps_sdk.nip.identity import NipIdentity
from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.frames import EncodingTier, FrameType
from nps_sdk.core.registry import FrameRegistry


@pytest.fixture
def full_registry() -> FrameRegistry:
    return FrameRegistry.create_full()


@pytest.fixture
def codec(full_registry: FrameRegistry) -> NpsFrameCodec:
    return NpsFrameCodec(full_registry)


# ── NipIdentity ───────────────────────────────────────────────────────────────

class TestNipIdentity:
    def test_generate_and_load(self, tmp_path):
        key_file = str(tmp_path / "ca.key")
        passphrase = "test-pass-1234"

        identity = NipIdentity.generate(key_file, passphrase)
        assert identity.is_loaded
        assert identity.pub_key_string.startswith("ed25519:")

        # Re-load from file
        identity2 = NipIdentity()
        identity2.load(key_file, passphrase)
        assert identity2.is_loaded
        assert identity2.pub_key_string == identity.pub_key_string

    def test_wrong_passphrase_raises(self, tmp_path):
        key_file = str(tmp_path / "ca.key")
        NipIdentity.generate(key_file, "correct-pass")

        identity = NipIdentity()
        with pytest.raises(ValueError):
            identity.load(key_file, "wrong-pass")

    def test_file_not_found(self, tmp_path):
        identity = NipIdentity()
        with pytest.raises(FileNotFoundError):
            identity.load(str(tmp_path / "nonexistent.key"), "pass")

    def test_sign_and_verify(self, tmp_path):
        key_file = str(tmp_path / "ca.key")
        identity = NipIdentity.generate(key_file, "sign-pass")

        payload = {"nid": "urn:nps:agent:example.com:abc", "action": "test"}
        sig     = identity.sign(payload)

        assert sig.startswith("ed25519:")
        assert NipIdentity.verify_signature(identity.pub_key_string, payload, sig) is True

    def test_verify_tampered_payload(self, tmp_path):
        key_file = str(tmp_path / "ca.key")
        identity = NipIdentity.generate(key_file, "sign-pass")

        payload = {"value": 42}
        sig     = identity.sign(payload)

        tampered = {"value": 43}
        assert NipIdentity.verify_signature(identity.pub_key_string, tampered, sig) is False

    def test_verify_bad_signature(self, tmp_path):
        key_file = str(tmp_path / "ca.key")
        identity = NipIdentity.generate(key_file, "sign-pass")

        payload = {"data": "hello"}
        bad_sig = "ed25519:" + "AAAA" * 16  # 64 bytes of zeros, base64
        assert NipIdentity.verify_signature(identity.pub_key_string, payload, bad_sig) is False

    def test_sign_raises_when_not_loaded(self):
        identity = NipIdentity()
        with pytest.raises(RuntimeError):
            identity.sign({"test": 1})

    def test_pub_key_raises_when_not_loaded(self):
        identity = NipIdentity()
        with pytest.raises(RuntimeError):
            _ = identity.public_key

    def test_different_keypairs_produce_different_pub_keys(self, tmp_path):
        f1 = str(tmp_path / "key1.key")
        f2 = str(tmp_path / "key2.key")
        id1 = NipIdentity.generate(f1, "pass")
        id2 = NipIdentity.generate(f2, "pass")
        assert id1.pub_key_string != id2.pub_key_string

    def test_corrupt_key_file_raises(self, tmp_path):
        key_file = str(tmp_path / "corrupt.key")
        with open(key_file, "wb") as f:
            f.write(b"\x00" * 10)  # too short
        identity = NipIdentity()
        with pytest.raises(ValueError):
            identity.load(key_file, "pass")


# ── IdentFrame ────────────────────────────────────────────────────────────────

class TestIdentFrame:
    def _make_frame(self, sig: str = "ed25519:AAAA") -> IdentFrame:
        return IdentFrame(
            nid="urn:nps:agent:ca.example.com:abc123",
            pub_key="ed25519:MCow...",
            capabilities=("nwp:query", "nwp:stream"),
            scope={"nodes": ["nwp://example.com/data"]},
            issued_by="urn:nps:org:ca.example.com",
            issued_at="2026-04-16T00:00:00Z",
            expires_at="2027-04-16T00:00:00Z",
            serial="0x0001",
            signature=sig,
        )

    def test_frame_type(self):
        assert self._make_frame().frame_type == FrameType.IDENT

    def test_unsigned_dict_excludes_signature(self):
        frame = self._make_frame()
        d     = frame.unsigned_dict()
        assert "signature" not in d
        assert "nid" in d

    def test_roundtrip_json(self, codec: NpsFrameCodec):
        frame = self._make_frame()
        wire  = codec.encode(frame, override_tier=EncodingTier.JSON)
        out   = codec.decode(wire)
        assert isinstance(out, IdentFrame)
        assert out.nid          == frame.nid
        assert out.capabilities == frame.capabilities
        assert out.serial       == frame.serial

    def test_roundtrip_msgpack(self, codec: NpsFrameCodec):
        frame = self._make_frame()
        out   = codec.decode(codec.encode(frame))
        assert isinstance(out, IdentFrame)
        assert out.nid == frame.nid

    def test_with_metadata(self, codec: NpsFrameCodec):
        frame = IdentFrame(
            nid="urn:nps:agent:example.com:xyz",
            pub_key="ed25519:MCow...",
            capabilities=("nwp:query",),
            scope={},
            issued_by="urn:nps:org:example.com",
            issued_at="2026-04-16T00:00:00Z",
            expires_at="2027-04-16T00:00:00Z",
            serial="0x0002",
            signature="ed25519:BBBB",
            metadata=IdentMetadata(model_family="anthropic/claude-4", tokenizer="cl100k_base"),
        )
        out = codec.decode(codec.encode(frame))
        assert out.metadata is not None
        assert out.metadata.model_family == "anthropic/claude-4"
        assert out.metadata.tokenizer    == "cl100k_base"


# ── RevokeFrame ───────────────────────────────────────────────────────────────

class TestRevokeFrame:
    def _make_frame(self) -> RevokeFrame:
        return RevokeFrame(
            target_nid="urn:nps:agent:ca.example.com:abc123",
            serial="0x0001",
            reason="key_compromise",
            revoked_at="2026-04-16T12:00:00Z",
            signature="ed25519:CCCC",
        )

    def test_frame_type(self):
        assert self._make_frame().frame_type == FrameType.REVOKE

    def test_unsigned_dict_excludes_signature(self):
        frame = self._make_frame()
        d     = frame.unsigned_dict()
        assert "signature" not in d
        assert "target_nid" in d

    def test_roundtrip(self, codec: NpsFrameCodec):
        frame = self._make_frame()
        out   = codec.decode(codec.encode(frame))
        assert isinstance(out, RevokeFrame)
        assert out.target_nid == frame.target_nid
        assert out.reason     == "key_compromise"
        assert out.revoked_at == frame.revoked_at


# ── TrustFrame ────────────────────────────────────────────────────────────────

class TestTrustFrame:
    def _make_frame(self, sig: str = "ed25519:DDDD") -> TrustFrame:
        return TrustFrame(
            grantor_nid="urn:nps:org:ca.example.com",
            grantee_ca="urn:nps:org:ca.partner.com",
            trust_scope=("nwp:query", "nwp:stream"),
            nodes=("nwp://example.com/data", "nwp://example.com/actions"),
            expires_at="2027-04-16T00:00:00Z",
            signature=sig,
        )

    def test_frame_type(self):
        assert self._make_frame().frame_type == FrameType.TRUST

    def test_registry_resolves_trust(self, full_registry: FrameRegistry):
        assert full_registry.resolve(FrameType.TRUST) is TrustFrame

    def test_unsigned_dict_excludes_signature(self):
        frame = self._make_frame()
        d     = frame.unsigned_dict()
        assert "signature"   not in d
        assert "grantor_nid" in d
        assert "grantee_ca"  in d

    def test_roundtrip_json(self, codec: NpsFrameCodec):
        frame = self._make_frame()
        wire  = codec.encode(frame, override_tier=EncodingTier.JSON)
        out   = codec.decode(wire)
        assert isinstance(out, TrustFrame)
        assert out.grantor_nid == frame.grantor_nid
        assert out.grantee_ca  == frame.grantee_ca
        assert out.trust_scope == frame.trust_scope
        assert out.nodes       == frame.nodes
        assert out.expires_at  == frame.expires_at

    def test_roundtrip_msgpack(self, codec: NpsFrameCodec):
        frame = self._make_frame()
        out   = codec.decode(codec.encode(frame))  # preferred_tier is MSGPACK
        assert isinstance(out, TrustFrame)
        assert out.grantor_nid == frame.grantor_nid
        assert out.trust_scope == frame.trust_scope

    def test_signed_trust_verify(self, tmp_path):
        """Integration: generate keypair, sign TrustFrame, verify."""
        key_file = str(tmp_path / "ca-grantor.key")
        identity = NipIdentity.generate(key_file, "grantor-pass")

        frame = TrustFrame(
            grantor_nid="urn:nps:org:ca.example.com",
            grantee_ca="urn:nps:org:ca.partner.com",
            trust_scope=("nwp:query",),
            nodes=("nwp://example.com/data",),
            expires_at="2027-04-16T00:00:00Z",
            signature="",  # placeholder
        )
        unsigned = frame.unsigned_dict()
        sig      = identity.sign(unsigned)
        assert NipIdentity.verify_signature(identity.pub_key_string, unsigned, sig) is True


# ── IdentMetadata ─────────────────────────────────────────────────────────────

class TestIdentMetadata:
    def test_empty_metadata(self):
        m   = IdentMetadata()
        out = IdentMetadata.from_dict(m.to_dict())
        assert out.model_family is None
        assert out.tokenizer    is None
        assert out.runtime      is None

    def test_full_metadata(self):
        m   = IdentMetadata(model_family="gpt-4", tokenizer="cl100k", runtime="langchain/0.2")
        out = IdentMetadata.from_dict(m.to_dict())
        assert out.model_family == "gpt-4"
        assert out.runtime      == "langchain/0.2"

    def test_signed_ident_verify(self, tmp_path):
        """Integration: generate keypair, sign IdentFrame, verify."""
        key_file = str(tmp_path / "agent.key")
        identity = NipIdentity.generate(key_file, "agent-pass")

        # Build the unsigned frame dict
        unsigned = {
            "nid":          "urn:nps:agent:example.com:agent-1",
            "pub_key":      identity.pub_key_string,
            "capabilities": ["nwp:query"],
            "scope":        {"nodes": []},
            "issued_by":    "urn:nps:org:example.com",
            "issued_at":    "2026-04-16T00:00:00Z",
            "expires_at":   "2027-04-16T00:00:00Z",
            "serial":       "0x0001",
            "frame":        "0x20",
        }
        sig = identity.sign(unsigned)

        # Verify the signature
        assert NipIdentity.verify_signature(identity.pub_key_string, unsigned, sig) is True
