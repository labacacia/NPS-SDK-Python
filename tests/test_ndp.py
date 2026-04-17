# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for NDP frame dataclasses, InMemoryNdpRegistry, and NdpAnnounceValidator."""

import tempfile
import time

import pytest

from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.frames import EncodingTier, FrameType
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ndp.frames import (
    AnnounceFrame,
    GraphFrame,
    NdpAddress,
    NdpGraphNode,
    NdpResolveResult,
    ResolveFrame,
)
from nps_sdk.ndp.registry import InMemoryNdpRegistry
from nps_sdk.ndp.validator import NdpAnnounceResult, NdpAnnounceValidator
from nps_sdk.nip.identity import NipIdentity


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def registry() -> FrameRegistry:
    return FrameRegistry.create_full()


@pytest.fixture
def codec(registry: FrameRegistry) -> NpsFrameCodec:
    return NpsFrameCodec(registry)


@pytest.fixture
def identity(tmp_path) -> NipIdentity:
    return NipIdentity.generate(str(tmp_path / "node.key"), "test-pass")


def _make_announce(
    identity: NipIdentity,
    nid: str = "urn:nps:node:api.example.com:products",
    ttl: int = 300,
) -> AnnounceFrame:
    addresses = (NdpAddress(host="api.example.com", port=443, protocol="https"),)
    capabilities = ("nwp:query", "nwp:stream")
    timestamp = "2026-04-16T00:00:00Z"
    node_type = "memory"

    unsigned: dict = {
        "nid":          nid,
        "addresses":    [a.to_dict() for a in addresses],
        "capabilities": list(capabilities),
        "ttl":          ttl,
        "timestamp":    timestamp,
        "node_type":    node_type,
    }
    sig = identity.sign(unsigned)

    return AnnounceFrame(
        nid=nid,
        addresses=addresses,
        capabilities=capabilities,
        ttl=ttl,
        timestamp=timestamp,
        signature=sig,
        node_type=node_type,
    )


# ── NdpAddress ────────────────────────────────────────────────────────────────

class TestNdpAddress:
    def test_roundtrip(self):
        addr = NdpAddress(host="example.com", port=443, protocol="https")
        out  = NdpAddress.from_dict(addr.to_dict())
        assert out == addr

    def test_native_protocol(self):
        addr = NdpAddress(host="10.0.0.1", port=17433, protocol="nps-native")
        out  = NdpAddress.from_dict(addr.to_dict())
        assert out.protocol == "nps-native"


# ── AnnounceFrame ─────────────────────────────────────────────────────────────

class TestAnnounceFrame:
    def test_frame_type(self, identity):
        frame = _make_announce(identity)
        assert frame.frame_type == FrameType.ANNOUNCE

    def test_unsigned_dict_excludes_signature(self, identity):
        frame = _make_announce(identity)
        d = frame.unsigned_dict()
        assert "signature" not in d
        assert "nid" in d
        assert "addresses" in d

    def test_roundtrip_json(self, identity, codec):
        frame = _make_announce(identity)
        wire  = codec.encode(frame, override_tier=EncodingTier.JSON)
        out   = codec.decode(wire)
        assert isinstance(out, AnnounceFrame)
        assert out.nid          == frame.nid
        assert out.ttl          == frame.ttl
        assert out.capabilities == frame.capabilities

    def test_roundtrip_msgpack(self, identity, codec):
        frame = _make_announce(identity)
        out   = codec.decode(codec.encode(frame))
        assert isinstance(out, AnnounceFrame)
        assert out.nid       == frame.nid
        assert out.node_type == "memory"

    def test_multiple_addresses(self, codec, identity):
        frame = AnnounceFrame(
            nid="urn:nps:node:api.example.com:orders",
            addresses=(
                NdpAddress(host="api.example.com", port=443,   protocol="https"),
                NdpAddress(host="api.example.com", port=17433, protocol="nps-native"),
            ),
            capabilities=("nwp:query",),
            ttl=60,
            timestamp="2026-04-16T00:00:00Z",
            signature="ed25519:AAAA",
        )
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, AnnounceFrame)
        assert len(out.addresses) == 2


# ── ResolveFrame ──────────────────────────────────────────────────────────────

class TestResolveFrame:
    def test_frame_type(self):
        frame = ResolveFrame(target="nwp://api.example.com/products")
        assert frame.frame_type == FrameType.RESOLVE

    def test_roundtrip_request(self, codec):
        frame = ResolveFrame(
            target="nwp://api.example.com/products",
            requester_nid="urn:nps:agent:ca.example.com:agent-1",
        )
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, ResolveFrame)
        assert out.target        == frame.target
        assert out.requester_nid == frame.requester_nid
        assert out.resolved      is None

    def test_roundtrip_response(self, codec):
        frame = ResolveFrame(
            target="nwp://api.example.com/products",
            resolved=NdpResolveResult(host="api.example.com", port=443, ttl=300),
        )
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, ResolveFrame)
        assert out.resolved is not None
        assert out.resolved.host == "api.example.com"
        assert out.resolved.ttl  == 300

    def test_roundtrip_with_fingerprint(self, codec):
        frame = ResolveFrame(
            target="nwp://api.example.com/orders",
            resolved=NdpResolveResult(
                host="api.example.com", port=443, ttl=60,
                cert_fingerprint="sha256:AABB",
            ),
        )
        out = codec.decode(codec.encode(frame))
        assert out.resolved.cert_fingerprint == "sha256:AABB"


# ── GraphFrame ────────────────────────────────────────────────────────────────

class TestGraphFrame:
    def test_frame_type(self):
        frame = GraphFrame(seq=1, initial_sync=True)
        assert frame.frame_type == FrameType.GRAPH

    def test_full_sync_roundtrip(self, codec):
        frame = GraphFrame(
            seq=1,
            initial_sync=True,
            nodes=(
                NdpGraphNode(
                    nid="urn:nps:node:api.example.com:products",
                    addresses=(NdpAddress(host="api.example.com", port=443, protocol="https"),),
                    capabilities=("nwp:query",),
                    node_type="memory",
                ),
            ),
        )
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, GraphFrame)
        assert out.initial_sync is True
        assert out.seq == 1
        assert len(out.nodes) == 1
        assert out.nodes[0].nid == "urn:nps:node:api.example.com:products"

    def test_incremental_roundtrip(self, codec):
        frame = GraphFrame(
            seq=2,
            initial_sync=False,
            patch=[{"op": "add", "path": "/nodes/-", "value": {"nid": "urn:nps:node:x:y"}}],
        )
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, GraphFrame)
        assert out.initial_sync is False
        assert out.nodes is None
        assert out.patch is not None


# ── InMemoryNdpRegistry ───────────────────────────────────────────────────────

class TestInMemoryNdpRegistry:
    def test_announce_and_get_by_nid(self, identity):
        reg   = InMemoryNdpRegistry()
        frame = _make_announce(identity)
        reg.announce(frame)
        result = reg.get_by_nid(frame.nid)
        assert result is not None
        assert result.nid == frame.nid

    def test_ttl_zero_evicts(self, identity):
        reg   = InMemoryNdpRegistry()
        frame = _make_announce(identity, ttl=0)
        reg.announce(frame)
        assert reg.get_by_nid(frame.nid) is None

    def test_expired_entry_not_returned(self, identity):
        reg = InMemoryNdpRegistry()
        # Use a clock that fast-forwards
        now_val = [time.time()]
        reg.clock = lambda: now_val[0]

        frame = _make_announce(identity, ttl=10)
        reg.announce(frame)
        assert reg.get_by_nid(frame.nid) is not None

        # Advance clock past TTL
        now_val[0] += 20
        assert reg.get_by_nid(frame.nid) is None

    def test_resolve_finds_matching_nid(self, identity):
        reg   = InMemoryNdpRegistry()
        frame = _make_announce(identity, nid="urn:nps:node:api.example.com:products")
        reg.announce(frame)

        result = reg.resolve("nwp://api.example.com/products")
        assert result is not None
        assert result.host == "api.example.com"
        assert result.port == 443

    def test_resolve_subpath(self, identity):
        reg   = InMemoryNdpRegistry()
        frame = _make_announce(identity, nid="urn:nps:node:api.example.com:products")
        reg.announce(frame)

        result = reg.resolve("nwp://api.example.com/products/123")
        assert result is not None

    def test_resolve_wrong_path_returns_none(self, identity):
        reg   = InMemoryNdpRegistry()
        frame = _make_announce(identity, nid="urn:nps:node:api.example.com:products")
        reg.announce(frame)

        result = reg.resolve("nwp://api.example.com/orders")
        assert result is None

    def test_get_all_returns_live_entries(self, identity):
        reg = InMemoryNdpRegistry()
        now_val = [time.time()]
        reg.clock = lambda: now_val[0]

        f1 = _make_announce(identity, nid="urn:nps:node:api.example.com:products", ttl=100)
        f2 = _make_announce(identity, nid="urn:nps:node:api.example.com:orders",   ttl=5)
        reg.announce(f1)
        reg.announce(f2)

        assert len(reg.get_all()) == 2

        # Expire f2
        now_val[0] += 10
        live = reg.get_all()
        assert len(live) == 1
        assert live[0].nid == "urn:nps:node:api.example.com:products"

    def test_refresh_extends_ttl(self, identity):
        reg = InMemoryNdpRegistry()
        now_val = [time.time()]
        reg.clock = lambda: now_val[0]

        frame = _make_announce(identity, ttl=10)
        reg.announce(frame)

        # Advance close to expiry and refresh
        now_val[0] += 8
        reg.announce(frame)  # refreshes TTL

        # Advance another 8 seconds — still alive if TTL was refreshed
        now_val[0] += 8
        assert reg.get_by_nid(frame.nid) is not None


# ── NwpTargetMatchesNid ───────────────────────────────────────────────────────

class TestNwpTargetMatchesNid:
    def _match(self, nid: str, target: str) -> bool:
        return InMemoryNdpRegistry.nwp_target_matches_nid(nid, target)

    def test_exact_match(self):
        assert self._match(
            "urn:nps:node:api.example.com:products",
            "nwp://api.example.com/products",
        )

    def test_subpath_match(self):
        assert self._match(
            "urn:nps:node:api.example.com:products",
            "nwp://api.example.com/products/123",
        )

    def test_different_path_no_match(self):
        assert not self._match(
            "urn:nps:node:api.example.com:products",
            "nwp://api.example.com/orders",
        )

    def test_different_authority_no_match(self):
        assert not self._match(
            "urn:nps:node:api.example.com:products",
            "nwp://other.example.com/products",
        )

    def test_partial_segment_no_match(self):
        # "products" should NOT match "products-v2"
        assert not self._match(
            "urn:nps:node:api.example.com:products",
            "nwp://api.example.com/products-v2",
        )

    def test_invalid_nid_no_match(self):
        assert not self._match("not-a-nid", "nwp://api.example.com/products")

    def test_invalid_target_no_match(self):
        assert not self._match(
            "urn:nps:node:api.example.com:products",
            "https://api.example.com/products",
        )


# ── NdpAnnounceValidator ──────────────────────────────────────────────────────

class TestNdpAnnounceValidator:
    def test_valid_announce(self, identity):
        validator = NdpAnnounceValidator()
        nid = "urn:nps:node:api.example.com:products"
        validator.register_public_key(nid, identity.pub_key_string)

        frame  = _make_announce(identity, nid=nid)
        result = validator.validate(frame)
        assert result.is_valid is True
        assert result.error_code is None

    def test_unregistered_nid_fails(self, identity):
        validator = NdpAnnounceValidator()
        frame  = _make_announce(identity)
        result = validator.validate(frame)
        assert result.is_valid is False
        assert result.error_code == "NDP-ANNOUNCE-NID-MISMATCH"

    def test_invalid_signature_fails(self, identity, tmp_path):
        validator = NdpAnnounceValidator()
        nid = "urn:nps:node:api.example.com:products"
        validator.register_public_key(nid, identity.pub_key_string)

        # Frame with a bad signature
        frame = AnnounceFrame(
            nid=nid,
            addresses=(NdpAddress(host="api.example.com", port=443, protocol="https"),),
            capabilities=("nwp:query",),
            ttl=300,
            timestamp="2026-04-16T00:00:00Z",
            signature="ed25519:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )
        result = validator.validate(frame)
        assert result.is_valid is False
        assert result.error_code == "NDP-ANNOUNCE-SIG-INVALID"

    def test_remove_public_key(self, identity):
        validator = NdpAnnounceValidator()
        nid = "urn:nps:node:api.example.com:products"
        validator.register_public_key(nid, identity.pub_key_string)
        validator.remove_public_key(nid)

        frame  = _make_announce(identity, nid=nid)
        result = validator.validate(frame)
        assert result.is_valid is False

    def test_known_public_keys_snapshot(self, identity):
        validator = NdpAnnounceValidator()
        nid = "urn:nps:node:api.example.com:products"
        validator.register_public_key(nid, identity.pub_key_string)

        keys = validator.known_public_keys
        assert nid in keys
        assert keys[nid] == identity.pub_key_string

    def test_result_factory_methods(self):
        ok   = NdpAnnounceResult.ok()
        fail = NdpAnnounceResult.fail("NDP-ANNOUNCE-SIG-INVALID", "bad sig")
        assert ok.is_valid is True
        assert ok.error_code is None
        assert fail.is_valid is False
        assert fail.error_code == "NDP-ANNOUNCE-SIG-INVALID"
        assert fail.message    == "bad sig"
