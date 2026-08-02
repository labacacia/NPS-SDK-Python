# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for the server-side NIP CA service library + RA enrollment tiers.

Covers ``NipCaService`` (register / renew / revoke / verify / group / session),
the RA enrollment policies (allowlist / bootstrap token / pending queue), the
flattened group-JWS verifier, and the pure-ASGI ``NipCaRouterApp`` driven over
``httpx.ASGITransport``.
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import tempfile
from urllib.parse import quote

import httpx
import pytest

from nps_sdk.nip import error_codes
from nps_sdk.nip.assurance_level import AssuranceLevel
from nps_sdk.nip.ca_client import NipCaClient, NipCaClientError
from nps_sdk.nip.identity import NipIdentity
from nps_sdk.nip.verifier import (
    NipIdentVerifier,
    NipVerifierOptions,
    NipVerifyContext,
)
from nps_sdk.nip.ca import (
    AllowlistPolicy,
    BootstrapTokenPolicy,
    EnrollmentTier,
    FlattenedJws,
    InMemoryBootstrapTokenStore,
    InMemoryNipCaStore,
    InMemoryPendingStore,
    NipCaException,
    NipCaOptions,
    NipCaRouterApp,
    NipCaService,
    NipGroupJws,
    NipRaPendingException,
    PendingQueuePolicy,
    PendingStatus,
    create_enrollment_policy,
)

CA_NID = "urn:nps:org:ca.example.com"


# ── Fixtures / helpers ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def keys():
    # Ed25519 keygen is cheap; the expensive part is PBKDF2 file encryption,
    # so generate the CA + agent identities once per module.
    with tempfile.TemporaryDirectory() as d:
        ca = NipIdentity.generate(os.path.join(d, "ca.key"), "pw")
        agent = NipIdentity.generate(os.path.join(d, "a.key"), "pw")
    return ca, agent


def _opts(**kw) -> NipCaOptions:
    base = dict(ca_nid=CA_NID, base_url="http://ca")
    base.update(kw)
    return NipCaOptions(**base)


def _service(ca_id, opts=None, store=None):
    return NipCaService(opts or _opts(), store or InMemoryNipCaStore(), ca_id)


def _verifier(ca: NipCaService, opts: NipCaOptions):
    return NipIdentVerifier(
        NipVerifierOptions(trusted_issuers={opts.ca_nid: ca.get_ca_public_key()})
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_group_jws(group_signer: NipIdentity, group_nid: str, payload: dict) -> FlattenedJws:
    header = {"alg": "EdDSA", "kid": group_nid, "nps-purpose": "session-issue"}
    protected = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = (protected + "." + payload_b64).encode("ascii")
    raw_sig = group_signer._private_key.sign(signing_input)  # noqa: SLF001
    return FlattenedJws(protected=protected, payload=payload_b64, signature=_b64url(raw_sig))


# ── Register / verify round-trip ────────────────────────────────────────────────

class TestRegisterVerify:
    async def test_register_verify_roundtrip(self, keys):
        ca_id, agent = keys
        opts = _opts()
        ca = _service(ca_id, opts)
        frame = await ca.register(
            "agent", "abc123", agent.pub_key_string, ["nwp:query"], '{"nodes":["*"]}'
        )
        assert frame.nid == "urn:nps:agent:ca.example.com:abc123"
        assert frame.serial == "0x1"
        assert frame.issued_by == CA_NID
        res = await _verifier(ca, opts).verify(frame, NipVerifyContext())
        assert res.valid

    async def test_node_default_validity_longer(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        node = await ca.register("node", "n1", agent.pub_key_string, ["nwp:query"], "{}")
        rec = await ca.get_cert(node.nid)
        span = (rec.expires_at - rec.issued_at).days
        assert span == 90

    async def test_duplicate_nid_rejected(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        await ca.register("agent", "dup", agent.pub_key_string, [], "{}")
        with pytest.raises(NipCaException) as exc:
            await ca.register("agent", "dup", agent.pub_key_string, [], "{}")
        assert exc.value.error_code == error_codes.CA_NID_ALREADY_EXISTS

    async def test_capability_allowlist_enforced(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id, _opts(allowed_capabilities=frozenset({"nwp:query"})))
        with pytest.raises(NipCaException) as exc:
            await ca.register("agent", "cap", agent.pub_key_string, ["nwp:admin"], "{}")
        assert exc.value.error_code == error_codes.CERT_CAPABILITY_MISSING

    async def test_verify_missing_nid(self, keys):
        ca_id, _ = keys
        ca = _service(ca_id)
        res = await ca.verify("urn:nps:agent:ca.example.com:ghost")
        assert not res.valid
        assert res.error_code == error_codes.CA_NID_NOT_FOUND

    async def test_build_nid_uses_ca_domain(self, keys):
        ca_id, _ = keys
        ca = _service(ca_id)
        assert ca.build_nid("node", "x") == "urn:nps:node:ca.example.com:x"


# ── X.509 registration (RFC-0002) ───────────────────────────────────────────────

class TestRegisterX509:
    async def test_leaf_root_chain(self, keys):
        ca_id, agent = keys
        opts = _opts()
        ca = _service(ca_id, opts)
        frame = await ca.register_x509(
            "agent", "x1", agent.pub_key_string, ["nwp:query"], '{"nodes":["*"]}',
            assurance_level=AssuranceLevel.ATTESTED,
        )
        assert frame.cert_format == "v2-x509"
        assert len(frame.cert_chain) == 2
        assert frame.assurance_level is AssuranceLevel.ATTESTED
        # v1 Ed25519 signature still verifies (assurance_level in signed payload).
        res = await _verifier(ca, opts).verify(frame, NipVerifyContext())
        assert res.valid

    async def test_non_ed25519_pubkey_rejected(self, keys):
        ca_id, _ = keys
        ca = _service(ca_id)
        with pytest.raises(NipCaException) as exc:
            await ca.register_x509("agent", "x2", "rsa:abcd", [], "{}")
        assert exc.value.error_code == error_codes.CERT_FORMAT_INVALID


# ── Renewal window ──────────────────────────────────────────────────────────────

class TestRenewal:
    async def test_renew_too_early(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        frame = await ca.register("agent", "r1", agent.pub_key_string, [], "{}")
        with pytest.raises(NipCaException) as exc:
            await ca.renew(frame.nid)
        assert exc.value.error_code == error_codes.CA_RENEWAL_TOO_EARLY

    async def test_renew_in_window(self, keys):
        ca_id, agent = keys
        # 1-day validity with a 7-day window ⇒ always renewable.
        ca = _service(ca_id, _opts(agent_cert_validity_days=1, renewal_window_days=7))
        frame = await ca.register("agent", "r2", agent.pub_key_string, [], "{}")
        renewed = await ca.renew(frame.nid)
        assert renewed.serial != frame.serial
        assert renewed.nid == frame.nid

    async def test_renew_revoked_rejected(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id, _opts(agent_cert_validity_days=1))
        frame = await ca.register("agent", "r3", agent.pub_key_string, [], "{}")
        await ca.revoke(frame.nid, "superseded")
        with pytest.raises(NipCaException) as exc:
            await ca.renew(frame.nid)
        assert exc.value.error_code == error_codes.CERT_REVOKED

    async def test_renew_missing_nid(self, keys):
        ca_id, _ = keys
        ca = _service(ca_id)
        with pytest.raises(NipCaException) as exc:
            await ca.renew("urn:nps:agent:ca.example.com:none")
        assert exc.value.error_code == error_codes.CA_NID_NOT_FOUND


# ── Revoke + cascade ────────────────────────────────────────────────────────────

class TestRevoke:
    async def test_revoke_returns_frame(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        frame = await ca.register("agent", "rv1", agent.pub_key_string, [], "{}")
        rf = await ca.revoke(frame.nid, "key_compromise")
        assert rf.target_nid == frame.nid
        assert rf.reason == "key_compromise"
        assert rf.serial == frame.serial
        res = await ca.verify(frame.nid)
        assert res.error_code == error_codes.CERT_REVOKED

    async def test_revoke_missing_nid(self, keys):
        ca_id, _ = keys
        ca = _service(ca_id)
        with pytest.raises(NipCaException) as exc:
            await ca.revoke("urn:nps:agent:ca.example.com:none", "superseded")
        assert exc.value.error_code == error_codes.CA_NID_NOT_FOUND

    async def test_cascade_revoke_sessions(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        g = await ca.register_group("group-c", agent.pub_key_string, ["nwp:query"], "{}")
        s1 = await ca.issue_session(g.nid, agent.pub_key_string, validity_seconds=3600)
        s2 = await ca.issue_session(g.nid, agent.pub_key_string, validity_seconds=3600)
        await ca.revoke(g.nid, "ca_compromise")
        # Group + both sessions revoked.
        for nid in (g.nid, s1.nid, s2.nid):
            res = await ca.verify(nid)
            assert not res.valid
        # Sessions carry parent_revoked reason.
        sessions = await ca.list_sessions(g.nid)
        assert all(s.revoke_reason == "parent_revoked" for s in sessions)


# ── Group + session (CR-0003) ───────────────────────────────────────────────────

class TestGroupSession:
    async def test_group_lineage_role(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        g = await ca.register_group(
            "group-x", agent.pub_key_string, ["nwp:query", "nwp:stream"], "{}",
            owner_user_id="u1", owner_key_id="k1",
        )
        assert g.lineage.role == "group"
        rec = await ca.get_cert(g.nid)
        assert rec.nid_role == "group"

    async def test_group_identifier_prefix_enforced(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        with pytest.raises(NipCaException):
            await ca.register_group("notgroup", agent.pub_key_string, [], "{}")

    async def test_group_auto_identifier(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        g = await ca.register_group(None, agent.pub_key_string, [], "{}")
        assert ":group-" in g.nid

    async def test_session_validity_clamp_too_short(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        g = await ca.register_group("group-s", agent.pub_key_string, ["nwp:query"], "{}")
        with pytest.raises(NipCaException) as exc:
            await ca.issue_session(g.nid, agent.pub_key_string, validity_seconds=1)
        assert exc.value.error_code == error_codes.CA_SESSION_VALIDITY_INVALID

    async def test_session_validity_too_long(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        g = await ca.register_group("group-s2", agent.pub_key_string, ["nwp:query"], "{}")
        with pytest.raises(NipCaException) as exc:
            await ca.issue_session(g.nid, agent.pub_key_string, validity_seconds=10**9)
        assert exc.value.error_code == error_codes.CA_SESSION_VALIDITY_INVALID

    async def test_session_capability_subset(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        g = await ca.register_group("group-cap", agent.pub_key_string, ["nwp:query"], "{}")
        with pytest.raises(NipCaException) as exc:
            await ca.issue_session(
                g.nid, agent.pub_key_string, validity_seconds=3600,
                capabilities=["nwp:query", "nwp:admin"],
            )
        assert exc.value.error_code == error_codes.CA_SCOPE_EXPANSION_DENIED

    async def test_session_defaults_to_group_caps_and_scope(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        g = await ca.register_group(
            "group-d", agent.pub_key_string, ["nwp:query", "nwp:stream"], '{"nodes":["a"]}'
        )
        s = await ca.issue_session(g.nid, agent.pub_key_string, validity_seconds=3600)
        assert set(s.capabilities) == {"nwp:query", "nwp:stream"}
        assert s.scope == {"nodes": ["a"]}
        assert s.lineage.parent_nid == g.nid
        assert s.lineage.group_nid == g.nid

    async def test_session_under_non_group_rejected(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        a = await ca.register("agent", "plain", agent.pub_key_string, [], "{}")
        with pytest.raises(NipCaException) as exc:
            await ca.issue_session(a.nid, agent.pub_key_string, validity_seconds=3600)
        assert exc.value.error_code == error_codes.CA_PARENT_NOT_GROUP

    async def test_session_under_missing_group(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        with pytest.raises(NipCaException) as exc:
            await ca.issue_session(
                "urn:nps:agent:ca.example.com:group-ghost", agent.pub_key_string,
                validity_seconds=3600,
            )
        assert exc.value.error_code == error_codes.CA_PARENT_NOT_FOUND

    async def test_session_under_revoked_group(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        g = await ca.register_group("group-rev", agent.pub_key_string, ["nwp:query"], "{}")
        await ca.revoke(g.nid, "superseded")
        with pytest.raises(NipCaException) as exc:
            await ca.issue_session(g.nid, agent.pub_key_string, validity_seconds=3600)
        assert exc.value.error_code == error_codes.CA_GROUP_REVOKED


# ── RA tiers ────────────────────────────────────────────────────────────────────

class TestAllowlistPolicy:
    async def test_match(self):
        p = AllowlistPolicy(["svc-*", "worker-?"])
        await p.check("agent", "svc-1", "ed25519:x", [], "{}", None, None)
        await p.check("agent", "worker-9", "ed25519:x", [], "{}", None, None)

    async def test_deny(self):
        p = AllowlistPolicy(["svc-*"])
        with pytest.raises(NipCaException) as exc:
            await p.check("agent", "other", "ed25519:x", [], "{}", None, None)
        assert exc.value.error_code == error_codes.RA_NID_NOT_ALLOWED

    async def test_star_open(self):
        p = AllowlistPolicy(["*"])
        await p.check("agent", "anything-goes", "ed25519:x", [], "{}", None, None)


class TestBootstrapTokenPolicy:
    async def test_valid_token_consumed(self):
        store = InMemoryBootstrapTokenStore()
        raw = await store.create("label", _utc_in(3600))
        p = BootstrapTokenPolicy(store)
        await p.check("agent", "id", "ed25519:x", [], "{}", None, raw)
        # Second use fails — single-shot.
        with pytest.raises(NipCaException) as exc:
            await p.check("agent", "id", "ed25519:x", [], "{}", None, raw)
        assert exc.value.error_code == error_codes.RA_TOKEN_EXPIRED

    async def test_missing_token(self):
        p = BootstrapTokenPolicy(InMemoryBootstrapTokenStore())
        with pytest.raises(NipCaException) as exc:
            await p.check("agent", "id", "ed25519:x", [], "{}", None, None)
        assert exc.value.error_code == error_codes.RA_TOKEN_INVALID

    async def test_expired_token(self):
        store = InMemoryBootstrapTokenStore()
        raw = await store.create(None, _utc_in(-10))
        p = BootstrapTokenPolicy(store)
        with pytest.raises(NipCaException) as exc:
            await p.check("agent", "id", "ed25519:x", [], "{}", None, raw)
        assert exc.value.error_code == error_codes.RA_TOKEN_EXPIRED

    async def test_revoke_token(self):
        store = InMemoryBootstrapTokenStore()
        await store.create("l", _utc_in(3600))
        infos = await store.list()
        assert await store.revoke(infos[0].id) is True
        assert await store.revoke(infos[0].id) is False


class TestPendingQueuePolicy:
    async def test_enqueue_raises_pending(self):
        store = InMemoryPendingStore()
        p = PendingQueuePolicy(store, max_size=10)
        with pytest.raises(NipRaPendingException) as exc:
            await p.check("agent", "p1", "ed25519:x", ["nwp:query"], "{}", None, None)
        pid = exc.value.pending_id
        rec = await store.get(pid)
        assert rec.status == PendingStatus.PENDING
        assert store.pending_count == 1

    async def test_queue_full(self):
        store = InMemoryPendingStore()
        p = PendingQueuePolicy(store, max_size=1)
        with pytest.raises(NipRaPendingException):
            await p.check("agent", "p1", "ed25519:x", [], "{}", None, None)
        with pytest.raises(NipCaException) as exc:
            await p.check("agent", "p2", "ed25519:x", [], "{}", None, None)
        assert exc.value.error_code == error_codes.RA_TOKEN_INVALID

    async def test_approve_reject_transitions(self):
        store = InMemoryPendingStore()
        p = PendingQueuePolicy(store, max_size=10)
        with pytest.raises(NipRaPendingException) as exc:
            await p.check("agent", "p", "ed25519:x", [], "{}", None, None)
        pid = exc.value.pending_id
        assert await store.approve(pid) is True
        assert await store.approve(pid) is False  # no longer pending
        with pytest.raises(NipRaPendingException) as exc2:
            await p.check("agent", "q", "ed25519:x", [], "{}", None, None)
        pid2 = exc2.value.pending_id
        assert await store.reject(pid2, "nope") is True
        rec = await store.get(pid2)
        assert rec.status == PendingStatus.REJECTED and rec.reject_reason == "nope"


class TestEnrollmentFactory:
    def test_allowlist(self):
        assert isinstance(create_enrollment_policy(_opts()), AllowlistPolicy)

    def test_bootstrap_requires_store(self):
        with pytest.raises(ValueError):
            create_enrollment_policy(_opts(enrollment_tier=EnrollmentTier.BOOTSTRAP_TOKEN))

    def test_pending_requires_store(self):
        with pytest.raises(ValueError):
            create_enrollment_policy(_opts(enrollment_tier=EnrollmentTier.PENDING_QUEUE))

    def test_pending_ok(self):
        p = create_enrollment_policy(
            _opts(enrollment_tier=EnrollmentTier.PENDING_QUEUE),
            pending_store=InMemoryPendingStore(),
        )
        assert isinstance(p, PendingQueuePolicy)


# ── Group JWS ───────────────────────────────────────────────────────────────────

class TestGroupJws:
    def test_verify_ok(self, keys):
        _, agent = keys
        jws = _make_group_jws(agent, "urn:nps:agent:ca:group-1", {"session_pub_key": "x", "iat": 1})
        res = NipGroupJws.try_verify(jws, agent.public_key)
        assert res.valid
        assert res.kid == "urn:nps:agent:ca:group-1"
        assert json.loads(res.payload_json)["iat"] == 1

    def test_bad_signature(self, keys):
        ca, agent = keys
        jws = _make_group_jws(agent, "urn:nps:agent:ca:group-1", {"iat": 1})
        res = NipGroupJws.try_verify(jws, ca.public_key)  # wrong key
        assert not res.valid
        assert res.error_code == error_codes.CA_JWS_INVALID

    def test_wrong_purpose(self, keys):
        _, agent = keys
        header = {"alg": "EdDSA", "kid": "k", "nps-purpose": "wrong"}
        protected = _b64url(json.dumps(header).encode())
        payload = _b64url(b"{}")
        raw = agent._private_key.sign((protected + "." + payload).encode())  # noqa: SLF001
        jws = FlattenedJws(protected=protected, payload=payload, signature=_b64url(raw))
        res = NipGroupJws.try_verify(jws, agent.public_key)
        assert not res.valid
        assert res.error_code == error_codes.CA_JWS_INVALID

    def test_missing_parts(self, keys):
        _, agent = keys
        res = NipGroupJws.try_verify(FlattenedJws(), agent.public_key)
        assert not res.valid


# ── Router endpoints ────────────────────────────────────────────────────────────

def _router_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ca")


def _enc(nid: str) -> str:
    return quote(nid, safe="")


class TestRouter:
    async def test_well_known_and_ca_cert(self, keys):
        ca_id, _ = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.get("/.well-known/nps-ca")
            assert r.status_code == 200
            body = r.json()
            assert body["issuer"] == CA_NID
            assert "ra-tier-1" in body["capabilities"]
            r2 = await c.get("/v1/ca/cert")
            assert r2.status_code == 200
            assert r2.json()["algorithm"] == "ed25519"

    async def test_register_verify_revoke_crl(self, keys):
        ca_id, agent = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.post("/v1/agents/register", json={
                "identifier": "a1", "pub_key": agent.pub_key_string,
                "capabilities": ["nwp:query"], "scope_json": '{"nodes":["*"]}',
            })
            assert r.status_code == 201
            nid = r.json()["nid"]
            rv = await c.get(f"/v1/agents/{_enc(nid)}/verify")
            assert rv.status_code == 200 and rv.json()["valid"] is True
            rr = await c.post(f"/v1/agents/{_enc(nid)}/revoke", json={"reason": "key_compromise"})
            assert rr.status_code == 200
            rv2 = await c.get(f"/v1/agents/{_enc(nid)}/verify")
            assert rv2.status_code == 200 and rv2.json()["valid"] is False
            crl = await c.get("/v1/crl")
            assert crl.status_code == 200
            assert "signature" in crl.json()
            assert len(crl.json()["entries"]) == 1

    async def test_register_bad_pubkey(self, keys):
        ca_id, _ = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.post("/v1/agents/register", json={"identifier": "a", "pub_key": "bad"})
            assert r.status_code == 400
            assert r.json()["error_code"] == "NIP-CA-BAD-REQUEST"

    async def test_duplicate_returns_409(self, keys):
        ca_id, agent = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            body = {"identifier": "dup", "pub_key": agent.pub_key_string}
            await c.post("/v1/agents/register", json=body)
            r = await c.post("/v1/agents/register", json=body)
            assert r.status_code == 409
            assert r.json()["error_code"] == error_codes.CA_NID_ALREADY_EXISTS

    async def test_verify_unknown_404(self, keys):
        ca_id, _ = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.get(f"/v1/agents/{_enc('urn:nps:agent:ca.example.com:ghost')}/verify")
            assert r.status_code == 404
            assert r.json()["valid"] is False

    async def test_operator_auth_required(self, keys):
        ca_id, agent = keys
        opts = _opts(operator_api_key="secret")
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.post("/v1/agents/register", json={
                "identifier": "a", "pub_key": agent.pub_key_string})
            assert r.status_code == 401
            r2 = await c.post(
                "/v1/agents/register",
                headers={"Authorization": "Bearer secret"},
                json={"identifier": "a", "pub_key": agent.pub_key_string},
            )
            assert r2.status_code == 201

    async def test_certificate_list_client_and_signed_crl(self, keys):
        ca_id, agent = keys
        opts = _opts(operator_api_key="secret")
        ca = _service(ca_id, opts)
        app = NipCaRouterApp(opts, ca)
        frame = await ca.register(
            "agent", "audit-a", agent.pub_key_string, [], '{"nodes":["*"]}')
        await ca.revoke(frame.nid, "key_compromise")
        async with _router_client(app) as http:
            client = NipCaClient("http://ca", http_client=http)
            with pytest.raises(NipCaClientError) as exc:
                await client.get_certificates()
            assert exc.value.status_code == 401

            certificates = await client.get_certificates("secret")
            assert len(certificates.entries) == 1
            assert certificates.entries[0].nid == frame.nid
            assert certificates.entries[0].scope == {"nodes": ["*"]}

            crl = await client.get_crl()
            assert NipCaClient.verify_crl_signature(
                crl, ca.get_ca_public_key())

    async def test_register_x509_endpoint(self, keys):
        ca_id, agent = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.post("/v1/agents/register-x509", json={
                "identifier": "x1", "pub_key": agent.pub_key_string,
                "assurance_level": "verified",
            })
            assert r.status_code == 201
            assert r.json()["cert_format"] == "v2-x509"
            assert r.json()["assurance_level"] == "verified"

    async def test_group_register_and_session_operator(self, keys):
        ca_id, agent = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            g = await c.post("/v1/orchestrators/groups/register", json={
                "identifier": "group-r", "pub_key": agent.pub_key_string,
                "capabilities": ["nwp:query"], "scope_json": "{}",
            })
            assert g.status_code == 201
            gnid = g.json()["nid"]
            s = await c.post(f"/v1/orchestrators/groups/{_enc(gnid)}/sessions/issue", json={
                "session_pub_key": agent.pub_key_string, "validity_seconds": 3600,
            })
            assert s.status_code == 201
            assert s.json()["lineage"]["role"] == "session"
            ls = await c.get(f"/v1/orchestrators/groups/{_enc(gnid)}/sessions")
            assert ls.status_code == 200 and ls.json()["count"] == 1

    async def test_session_issue_via_group_jws(self, keys):
        ca_id, agent = keys
        opts = _opts()
        ca = _service(ca_id, opts)
        app = NipCaRouterApp(opts, ca)
        # Register group with a known group signing key (the agent identity).
        g = await ca.register_group("group-jws", agent.pub_key_string, ["nwp:query"], "{}")
        payload = {
            "session_pub_key": agent.pub_key_string,
            "validity_seconds": 3600,
            "iat": _epoch_now(),
        }
        jws = _make_group_jws(agent, g.nid, payload)
        async with _router_client(app) as c:
            r = await c.post(
                f"/v1/orchestrators/groups/{_enc(g.nid)}/sessions/issue",
                headers={"content-type": "application/jose+json"},
                json={"protected": jws.protected, "payload": jws.payload, "signature": jws.signature},
            )
            assert r.status_code == 201
            assert r.json()["lineage"]["parent_nid"] == g.nid

    async def test_session_jws_stale_iat_rejected(self, keys):
        ca_id, agent = keys
        opts = _opts()
        ca = _service(ca_id, opts)
        app = NipCaRouterApp(opts, ca)
        g = await ca.register_group("group-stale", agent.pub_key_string, ["nwp:query"], "{}")
        payload = {"session_pub_key": agent.pub_key_string, "iat": _epoch_now() - 10_000}
        jws = _make_group_jws(agent, g.nid, payload)
        async with _router_client(app) as c:
            r = await c.post(
                f"/v1/orchestrators/groups/{_enc(g.nid)}/sessions/issue",
                headers={"content-type": "application/jose+json"},
                json={"protected": jws.protected, "payload": jws.payload, "signature": jws.signature},
            )
            assert r.status_code == 401
            assert r.json()["error_code"] == error_codes.CA_JWS_EXPIRED

    async def test_bootstrap_tier_flow(self, keys):
        ca_id, agent = keys
        opts = _opts(enrollment_tier=EnrollmentTier.BOOTSTRAP_TOKEN, operator_api_key="op")
        store = InMemoryBootstrapTokenStore()
        app = NipCaRouterApp(opts, _service(ca_id, opts), bootstrap_token_store=store)
        async with _router_client(app) as c:
            tok = await c.post(
                "/v1/enrollment/tokens",
                headers={"Authorization": "Bearer op"},
                json={"ttl_seconds": 3600, "label": "worker"},
            )
            assert tok.status_code == 201
            raw = tok.json()["token"]
            assert raw.startswith("nps-bootstrap-")
            ok = await c.post(
                "/v1/agents/register",
                headers={"Authorization": "Bearer op", "X-NPS-Enrollment-Token": raw},
                json={"identifier": "boot1", "pub_key": agent.pub_key_string},
            )
            assert ok.status_code == 201
            # Reused token now consumed → unauthenticated.
            bad = await c.post(
                "/v1/agents/register",
                headers={"Authorization": "Bearer op", "X-NPS-Enrollment-Token": raw},
                json={"identifier": "boot2", "pub_key": agent.pub_key_string},
            )
            assert bad.status_code == 401
            assert bad.json()["error_code"] == error_codes.RA_TOKEN_EXPIRED

    async def test_pending_tier_flow(self, keys):
        ca_id, agent = keys
        opts = _opts(enrollment_tier=EnrollmentTier.PENDING_QUEUE)
        store = InMemoryPendingStore()
        app = NipCaRouterApp(opts, _service(ca_id, opts), pending_store=store)
        async with _router_client(app) as c:
            r = await c.post("/v1/agents/register", json={
                "identifier": "pend1", "pub_key": agent.pub_key_string})
            assert r.status_code == 202
            pid = r.json()["pending_id"]
            lst = await c.get("/v1/enrollment/pending")
            assert lst.status_code == 200 and lst.json()["count"] == 1
            appr = await c.post(f"/v1/enrollment/pending/{pid}/approve")
            assert appr.status_code == 201
            assert appr.json()["nid"].endswith("pend1")

    async def test_pending_reject(self, keys):
        ca_id, agent = keys
        opts = _opts(enrollment_tier=EnrollmentTier.PENDING_QUEUE)
        store = InMemoryPendingStore()
        app = NipCaRouterApp(opts, _service(ca_id, opts), pending_store=store)
        async with _router_client(app) as c:
            r = await c.post("/v1/agents/register", json={
                "identifier": "pend2", "pub_key": agent.pub_key_string})
            pid = r.json()["pending_id"]
            rej = await c.post(f"/v1/enrollment/pending/{pid}/reject", json={"reason": "spam"})
            assert rej.status_code == 200 and rej.json()["status"] == "rejected"
            again = await c.post(f"/v1/enrollment/pending/{pid}/approve")
            assert again.status_code == 409

    async def test_unknown_path_404(self, keys):
        ca_id, _ = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.get("/v1/nope")
            assert r.status_code == 404

    async def test_invalid_revoke_reason(self, keys):
        ca_id, agent = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            reg = await c.post("/v1/agents/register", json={
                "identifier": "rr", "pub_key": agent.pub_key_string})
            nid = reg.json()["nid"]
            r = await c.post(f"/v1/agents/{_enc(nid)}/revoke", json={"reason": "made_up"})
            assert r.status_code == 400


# ── Store + service edge coverage ───────────────────────────────────────────────

class TestStoreAndService:
    async def test_get_by_serial_and_list(self, keys):
        ca_id, agent = keys
        store = InMemoryNipCaStore()
        ca = _service(ca_id, store=store)
        f = await ca.register("agent", "s1", agent.pub_key_string, [], "{}")
        assert (await store.get_by_serial(f.serial)).nid == f.nid
        assert await store.get_by_serial("0xDEAD") is None
        certs = await ca.list_certificates()
        assert len(certs) == 1

    async def test_sign_artifact_and_ca_root(self, keys):
        ca_id, _ = keys
        ca = _service(ca_id)
        sig = ca.sign_artifact({"hello": "world"})
        assert sig.startswith("ed25519:")
        # ca_root_cert is lazy + cached.
        assert ca.ca_root_cert is ca.ca_root_cert

    async def test_verify_session_parent_revoked_chain(self, keys):
        ca_id, agent = keys
        ca = _service(ca_id)
        g = await ca.register_group("group-chain", agent.pub_key_string, ["nwp:query"], "{}")
        s = await ca.issue_session(g.nid, agent.pub_key_string, validity_seconds=3600)
        # Revoke group only via the store (no cascade) to exercise the chain check.
        await ca._store.revoke(g.nid, "superseded", _utc_in(0))  # noqa: SLF001
        res = await ca.verify(s.nid)
        assert res.error_code == error_codes.CERT_PARENT_REVOKED

    async def test_revoked_returns_false_when_absent(self, keys):
        ca_id, _ = keys
        store = InMemoryNipCaStore()
        assert await store.revoke("nope", "x", _utc_in(0)) is False
        assert await store.get_by_nid("nope") is None
        assert await store.get_revoked() == []
        assert await store.get_by_parent_nid("nope") == []


# ── Router — node + group revoke + JWS error branches ───────────────────────────

class TestRouterExtra:
    async def test_node_lifecycle(self, keys):
        ca_id, agent = keys
        opts = _opts(node_cert_validity_days=1, renewal_window_days=7)
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.post("/v1/nodes/register", json={
                "identifier": "n1", "pub_key": agent.pub_key_string})
            assert r.status_code == 201
            nid = r.json()["nid"]
            assert set(r.json()["capabilities"]) == {"nwp:query", "nwp:stream"}
            rn = await c.post(f"/v1/nodes/{_enc(nid)}/renew")
            assert rn.status_code == 200
            rv = await c.get(f"/v1/nodes/{_enc(nid)}/verify")
            assert rv.json()["valid"] is True
            rr = await c.post(f"/v1/nodes/{_enc(nid)}/revoke", json={"reason": "superseded"})
            assert rr.status_code == 200

    async def test_node_register_x509(self, keys):
        ca_id, agent = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.post("/v1/nodes/register-x509", json={
                "identifier": "nx", "pub_key": agent.pub_key_string})
            assert r.status_code == 201 and r.json()["cert_format"] == "v2-x509"

    async def test_group_revoke_endpoint_cascades(self, keys):
        ca_id, agent = keys
        opts = _opts()
        ca = _service(ca_id, opts)
        app = NipCaRouterApp(opts, ca)
        g = await ca.register_group("group-e", agent.pub_key_string, ["nwp:query"], "{}")
        await ca.issue_session(g.nid, agent.pub_key_string, validity_seconds=3600)
        async with _router_client(app) as c:
            r = await c.post(f"/v1/orchestrators/groups/{_enc(g.nid)}/revoke",
                             json={"reason": "ca_compromise"})
            assert r.status_code == 200
        sessions = await ca.list_sessions(g.nid)
        assert all(s.revoke_reason == "parent_revoked" for s in sessions)

    async def test_renew_missing_returns_404(self, keys):
        ca_id, _ = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.post(f"/v1/agents/{_enc('urn:nps:agent:ca.example.com:x')}/renew")
            assert r.status_code == 404

    async def test_register_bad_json_body(self, keys):
        ca_id, _ = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.post("/v1/agents/register", content=b"not json",
                             headers={"content-type": "application/json"})
            assert r.status_code == 400

    async def test_group_bad_identifier_and_pubkey(self, keys):
        ca_id, agent = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.post("/v1/orchestrators/groups/register", json={
                "identifier": "has space", "pub_key": agent.pub_key_string})
            assert r.status_code == 400
            r2 = await c.post("/v1/orchestrators/groups/register", json={
                "identifier": "group-ok", "pub_key": "bad"})
            assert r2.status_code == 400

    async def test_session_issue_bad_pubkey(self, keys):
        ca_id, agent = keys
        opts = _opts()
        ca = _service(ca_id, opts)
        app = NipCaRouterApp(opts, ca)
        g = await ca.register_group("group-bp", agent.pub_key_string, ["nwp:query"], "{}")
        async with _router_client(app) as c:
            r = await c.post(f"/v1/orchestrators/groups/{_enc(g.nid)}/sessions/issue",
                             json={"session_pub_key": "bad"})
            assert r.status_code == 400

    async def test_jws_group_not_found(self, keys):
        ca_id, agent = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        jws = _make_group_jws(agent, "urn:nps:agent:ca.example.com:group-ghost",
                              {"session_pub_key": agent.pub_key_string, "iat": _epoch_now()})
        async with _router_client(app) as c:
            r = await c.post(
                f"/v1/orchestrators/groups/{_enc('urn:nps:agent:ca.example.com:group-ghost')}/sessions/issue",
                headers={"content-type": "application/jose+json"},
                json={"protected": jws.protected, "payload": jws.payload, "signature": jws.signature},
            )
            assert r.status_code == 404
            assert r.json()["error_code"] == error_codes.CA_PARENT_NOT_FOUND

    async def test_jws_not_a_group(self, keys):
        ca_id, agent = keys
        opts = _opts()
        ca = _service(ca_id, opts)
        app = NipCaRouterApp(opts, ca)
        a = await ca.register("agent", "plainj", agent.pub_key_string, [], "{}")
        jws = _make_group_jws(agent, a.nid, {"session_pub_key": agent.pub_key_string, "iat": _epoch_now()})
        async with _router_client(app) as c:
            r = await c.post(
                f"/v1/orchestrators/groups/{_enc(a.nid)}/sessions/issue",
                headers={"content-type": "application/jose+json"},
                json={"protected": jws.protected, "payload": jws.payload, "signature": jws.signature},
            )
            assert r.status_code == 400
            assert r.json()["error_code"] == error_codes.CA_PARENT_NOT_GROUP

    async def test_jws_revoked_group(self, keys):
        ca_id, agent = keys
        opts = _opts()
        ca = _service(ca_id, opts)
        app = NipCaRouterApp(opts, ca)
        g = await ca.register_group("group-jr", agent.pub_key_string, ["nwp:query"], "{}")
        await ca.revoke(g.nid, "superseded")
        jws = _make_group_jws(agent, g.nid, {"session_pub_key": agent.pub_key_string, "iat": _epoch_now()})
        async with _router_client(app) as c:
            r = await c.post(
                f"/v1/orchestrators/groups/{_enc(g.nid)}/sessions/issue",
                headers={"content-type": "application/jose+json"},
                json={"protected": jws.protected, "payload": jws.payload, "signature": jws.signature},
            )
            assert r.status_code == 403
            assert r.json()["error_code"] == error_codes.CA_GROUP_REVOKED

    async def test_jws_bad_kid(self, keys):
        ca_id, agent = keys
        opts = _opts()
        ca = _service(ca_id, opts)
        app = NipCaRouterApp(opts, ca)
        g = await ca.register_group("group-kid", agent.pub_key_string, ["nwp:query"], "{}")
        jws = _make_group_jws(agent, "urn:nps:agent:ca.example.com:wrong",
                              {"session_pub_key": agent.pub_key_string, "iat": _epoch_now()})
        async with _router_client(app) as c:
            r = await c.post(
                f"/v1/orchestrators/groups/{_enc(g.nid)}/sessions/issue",
                headers={"content-type": "application/jose+json"},
                json={"protected": jws.protected, "payload": jws.payload, "signature": jws.signature},
            )
            assert r.status_code == 401
            assert r.json()["error_code"] == error_codes.CA_JWS_INVALID

    async def test_jws_bad_signature(self, keys):
        ca_id, agent = keys
        opts = _opts()
        ca = _service(ca_id, opts)
        app = NipCaRouterApp(opts, ca)
        # Group signed with a DIFFERENT pubkey than the JWS signer.
        g = await ca.register_group("group-sig", ca_id.pub_key_string, ["nwp:query"], "{}")
        jws = _make_group_jws(agent, g.nid, {"session_pub_key": agent.pub_key_string, "iat": _epoch_now()})
        async with _router_client(app) as c:
            r = await c.post(
                f"/v1/orchestrators/groups/{_enc(g.nid)}/sessions/issue",
                headers={"content-type": "application/jose+json"},
                json={"protected": jws.protected, "payload": jws.payload, "signature": jws.signature},
            )
            assert r.status_code == 401

    async def test_stores_disabled_endpoints(self, keys):
        ca_id, _ = keys
        opts = _opts()
        app = NipCaRouterApp(opts, _service(ca_id, opts))  # no stores
        async with _router_client(app) as c:
            assert (await c.post("/v1/enrollment/tokens", json={})).status_code == 400
            assert (await c.get("/v1/enrollment/pending")).status_code == 400
            assert (await c.post("/v1/enrollment/pending/x/approve")).status_code == 400
            assert (await c.post("/v1/enrollment/pending/x/reject", json={})).status_code == 400

    async def test_pending_approve_missing_and_reject_missing(self, keys):
        ca_id, _ = keys
        opts = _opts(enrollment_tier=EnrollmentTier.PENDING_QUEUE)
        app = NipCaRouterApp(opts, _service(ca_id, opts), pending_store=InMemoryPendingStore())
        async with _router_client(app) as c:
            assert (await c.post("/v1/enrollment/pending/ghost/approve")).status_code == 404
            assert (await c.post("/v1/enrollment/pending/ghost/reject", json={})).status_code == 404

    async def test_allowlist_tier_denies_via_router(self, keys):
        ca_id, agent = keys
        opts = _opts(enrollment_allowlist_patterns=("svc-*",))
        app = NipCaRouterApp(opts, _service(ca_id, opts))
        async with _router_client(app) as c:
            r = await c.post("/v1/agents/register", json={
                "identifier": "other", "pub_key": agent.pub_key_string})
            assert r.status_code == 403
            assert r.json()["error_code"] == error_codes.RA_NID_NOT_ALLOWED


# ── RA / group_jws small-branch coverage ────────────────────────────────────────

class TestRaBranches:
    async def test_bootstrap_list_and_wrong_prefix(self):
        store = InMemoryBootstrapTokenStore()
        await store.create("l", _utc_in(3600))
        assert len(await store.list()) == 1
        p = BootstrapTokenPolicy(store)
        with pytest.raises(NipCaException) as exc:
            await p.check("agent", "id", "ed25519:x", [], "{}", None, "wrong-prefix")
        assert exc.value.error_code == error_codes.RA_TOKEN_INVALID

    async def test_pending_list_and_get_missing(self):
        store = InMemoryPendingStore()
        assert await store.get("nope") is None
        assert await store.list() == []
        assert await store.approve("nope") is False
        assert await store.reject("nope", "r") is False

    def test_group_jws_malformed_b64_and_header(self, keys):
        _, agent = keys
        # Non-base64url protected header.
        bad = FlattenedJws(protected="!!!", payload="e30", signature="AA")
        assert not NipGroupJws.try_verify(bad, agent.public_key).valid
        # Valid b64 but header is not JSON.
        bad2 = FlattenedJws(protected=_b64url(b"not-json"), payload=_b64url(b"{}"),
                            signature=_b64url(b"x"))
        assert not NipGroupJws.try_verify(bad2, agent.public_key).valid

    def test_enrollment_unknown_tier(self):
        opts = _opts()
        opts.enrollment_tier = 99  # type: ignore[assignment]
        with pytest.raises(ValueError):
            create_enrollment_policy(opts)


# ── time helpers ────────────────────────────────────────────────────────────────

def _utc_in(seconds: int) -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)


def _epoch_now() -> int:
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp())
