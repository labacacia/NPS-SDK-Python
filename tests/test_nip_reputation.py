# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for NPS-RFC-0004 reputation log types, signing, and Merkle proofs."""

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nps_sdk.nip.reputation import (
    IncidentType,
    InclusionProof,
    ReputationLogClient,
    ReputationLogEntry,
    Severity,
    SignedTreeHead,
    canonical_json_for_leaf,
    sign_entry,
    verify_entry,
)


# ── Shared helpers ────────────────────────────────────────────────────────────

def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def leaf_hash(entry: ReputationLogEntry) -> bytes:
    canonical = canonical_json_for_leaf(entry)
    return hashlib.sha256(b"\x00" + canonical).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def make_signed_entry(subject_nid: str = "urn:nps:agent:test:subject") -> ReputationLogEntry:
    privkey = Ed25519PrivateKey.generate()
    unsigned = ReputationLogEntry(
        v=1,
        log_id="urn:nps:org:log.test",
        seq=1,
        timestamp="2026-01-01T00:00:00Z",
        subject_nid=subject_nid,
        incident=IncidentType.CERT_REVOKED,
        severity=Severity.INFO,
        issuer_nid="urn:nps:org:issuer.test",
        signature="",
    )
    return sign_entry(privkey, unsigned)


def make_sth(root: bytes, tree_size: int = 1) -> SignedTreeHead:
    return SignedTreeHead(
        log_id="urn:nps:org:log.test",
        tree_size=tree_size,
        timestamp="2026-01-01T00:00:00Z",
        sha256_root_hash=b64url(root),
        signature="ed25519:placeholder",
    )


# ── Part 1 — IncidentType ─────────────────────────────────────────────────────

class TestIncidentType:
    KNOWN_WIRE_STRINGS = [
        ("cert-revoked",         IncidentType.CERT_REVOKED),
        ("rate-limit-violation", IncidentType.RATE_LIMIT_VIOLATION),
        ("tos-violation",        IncidentType.TOS_VIOLATION),
        ("scraping-pattern",     IncidentType.SCRAPING_PATTERN),
        ("payment-default",      IncidentType.PAYMENT_DEFAULT),
        ("contract-dispute",     IncidentType.CONTRACT_DISPUTE),
        ("impersonation-claim",  IncidentType.IMPERSONATION_CLAIM),
        ("positive-attestation", IncidentType.POSITIVE_ATTESTATION),
    ]

    @pytest.mark.parametrize("wire_str,expected", KNOWN_WIRE_STRINGS)
    def test_from_wire_known(self, wire_str: str, expected: IncidentType):
        assert IncidentType.from_wire(wire_str) is expected

    @pytest.mark.parametrize("wire_str,expected", KNOWN_WIRE_STRINGS)
    def test_wire_property_round_trips(self, wire_str: str, expected: IncidentType):
        assert expected.wire == wire_str

    def test_unknown_wire_returns_other(self):
        result = IncidentType.from_wire("totally-unknown-incident")
        assert result is IncidentType.OTHER

    def test_other_wire_attribute(self):
        # IncidentType.OTHER.wire must be defined and not raise
        wire = IncidentType.OTHER.wire
        # The wire value is the internal sentinel — verify it is a non-empty string
        assert isinstance(wire, str)
        assert wire  # not empty


# ── Part 2 — Severity ─────────────────────────────────────────────────────────

class TestSeverity:
    KNOWN_WIRE_STRINGS = [
        ("info",     Severity.INFO),
        ("minor",    Severity.MINOR),
        ("moderate", Severity.MODERATE),
        ("major",    Severity.MAJOR),
        ("critical", Severity.CRITICAL),
    ]

    @pytest.mark.parametrize("wire_str,expected", KNOWN_WIRE_STRINGS)
    def test_from_wire_known(self, wire_str: str, expected: Severity):
        assert Severity.from_wire(wire_str) is expected

    @pytest.mark.parametrize("wire_str,expected", KNOWN_WIRE_STRINGS)
    def test_wire_property_round_trips(self, wire_str: str, expected: Severity):
        assert expected.wire == wire_str

    def test_severity_ordering(self):
        assert Severity.INFO     < Severity.MINOR
        assert Severity.MINOR    < Severity.MODERATE
        assert Severity.MODERATE < Severity.MAJOR
        assert Severity.MAJOR    < Severity.CRITICAL

    def test_severity_le_and_ge(self):
        assert Severity.INFO     <= Severity.INFO
        assert Severity.CRITICAL >= Severity.MAJOR

    def test_unknown_wire_raises_value_error(self):
        with pytest.raises(ValueError):
            Severity.from_wire("catastrophic")

    def test_unknown_wire_case_insensitive_known(self):
        # from_wire is case-insensitive for known values
        assert Severity.from_wire("INFO") is Severity.INFO
        assert Severity.from_wire("Critical") is Severity.CRITICAL


# ── Part 3 — ReputationLogEntry serialization ─────────────────────────────────

class TestReputationLogEntrySerialization:
    def _make_minimal_entry(self) -> ReputationLogEntry:
        return ReputationLogEntry(
            v=1,
            log_id="urn:nps:org:log.test",
            seq=5,
            timestamp="2026-03-01T12:00:00Z",
            subject_nid="urn:nps:agent:test:subject",
            incident=IncidentType.TOS_VIOLATION,
            severity=Severity.MODERATE,
            issuer_nid="urn:nps:org:issuer.test",
        )

    def test_to_dict_snake_case_keys(self):
        entry = self._make_minimal_entry()
        d = entry.to_dict()
        for expected_key in (
            "v", "log_id", "seq", "timestamp", "subject_nid",
            "incident", "severity", "issuer_nid",
        ):
            assert expected_key in d

    def test_to_dict_wire_values(self):
        entry = self._make_minimal_entry()
        d = entry.to_dict()
        assert d["incident"] == "tos-violation"
        assert d["severity"] == "moderate"
        assert d["v"] == 1

    def test_to_dict_omits_none_fields(self):
        entry = self._make_minimal_entry()
        d = entry.to_dict()
        for optional_key in ("window", "observation", "evidence_ref", "evidence_sha256", "signature"):
            assert optional_key not in d

    def test_to_dict_includes_optional_fields_when_set(self):
        entry = ReputationLogEntry(
            v=1,
            log_id="urn:nps:org:log.test",
            seq=1,
            timestamp="2026-01-01T00:00:00Z",
            subject_nid="urn:nps:agent:test:subject",
            incident=IncidentType.PAYMENT_DEFAULT,
            severity=Severity.MAJOR,
            issuer_nid="urn:nps:org:issuer.test",
            observation={"note": "manual review"},
            evidence_ref="ipfs://Qm...",
            evidence_sha256="abc123",
            signature="ed25519:AAAA",
        )
        d = entry.to_dict()
        assert "observation" in d
        assert "evidence_ref" in d
        assert "evidence_sha256" in d
        assert "signature" in d

    def test_from_dict_round_trips_fully_populated(self):
        from nps_sdk.nip.reputation import ObservationWindow

        original = ReputationLogEntry(
            v=1,
            log_id="urn:nps:org:log.test",
            seq=42,
            timestamp="2026-06-01T00:00:00Z",
            subject_nid="urn:nps:agent:test:fullsubject",
            incident=IncidentType.IMPERSONATION_CLAIM,
            severity=Severity.CRITICAL,
            issuer_nid="urn:nps:org:issuer.test",
            window=ObservationWindow(start="2026-05-01T00:00:00Z", end="2026-05-31T23:59:59Z"),
            observation={"count": 7, "detail": "details here"},
            evidence_ref="https://example.com/evidence/1",
            evidence_sha256="deadbeef",
            signature="ed25519:ZZZZ",
        )
        d = original.to_dict()
        restored = ReputationLogEntry.from_dict(d)

        assert restored.v == original.v
        assert restored.log_id == original.log_id
        assert restored.seq == original.seq
        assert restored.timestamp == original.timestamp
        assert restored.subject_nid == original.subject_nid
        assert restored.incident is original.incident
        assert restored.severity is original.severity
        assert restored.issuer_nid == original.issuer_nid
        assert restored.window is not None
        assert restored.window.start == original.window.start
        assert restored.window.end == original.window.end
        assert restored.observation == original.observation
        assert restored.evidence_ref == original.evidence_ref
        assert restored.evidence_sha256 == original.evidence_sha256
        assert restored.signature == original.signature

    def test_from_dict_unknown_incident_becomes_other(self):
        d = {
            "v": 1,
            "log_id": "urn:nps:org:log.test",
            "seq": 1,
            "timestamp": "2026-01-01T00:00:00Z",
            "subject_nid": "urn:nps:agent:test:subject",
            "incident": "brand-new-unknown-incident",
            "severity": "info",
            "issuer_nid": "urn:nps:org:issuer.test",
        }
        entry = ReputationLogEntry.from_dict(d)
        assert entry.incident is IncidentType.OTHER
        # incident_raw should preserve the original string
        assert entry.incident_raw == "brand-new-unknown-incident"

    def test_from_dict_unknown_severity_raises(self):
        d = {
            "v": 1,
            "log_id": "urn:nps:org:log.test",
            "seq": 1,
            "timestamp": "2026-01-01T00:00:00Z",
            "subject_nid": "urn:nps:agent:test:subject",
            "incident": "cert-revoked",
            "severity": "apocalyptic",
            "issuer_nid": "urn:nps:org:issuer.test",
        }
        with pytest.raises(ValueError):
            ReputationLogEntry.from_dict(d)


# ── Part 4 — sign_entry / verify_entry ────────────────────────────────────────

class TestSignAndVerify:
    def _make_keypair(self):
        privkey = Ed25519PrivateKey.generate()
        pubkey = privkey.public_key()
        return privkey, pubkey

    def _make_unsigned_entry(self, subject_nid: str = "urn:nps:agent:test:subject") -> ReputationLogEntry:
        return ReputationLogEntry(
            v=1,
            log_id="urn:nps:org:log.test",
            seq=1,
            timestamp="2026-01-01T00:00:00Z",
            subject_nid=subject_nid,
            incident=IncidentType.RATE_LIMIT_VIOLATION,
            severity=Severity.MINOR,
            issuer_nid="urn:nps:org:issuer.test",
            signature="",
        )

    def test_sign_and_verify_passes(self):
        privkey, pubkey = self._make_keypair()
        entry = sign_entry(privkey, self._make_unsigned_entry())
        assert verify_entry(pubkey, entry) is True

    def test_verify_false_when_subject_nid_tampered(self):
        privkey, pubkey = self._make_keypair()
        entry = sign_entry(privkey, self._make_unsigned_entry())
        tampered = ReputationLogEntry(
            v=entry.v,
            log_id=entry.log_id,
            seq=entry.seq,
            timestamp=entry.timestamp,
            subject_nid="urn:nps:agent:test:HACKED",
            incident=entry.incident,
            severity=entry.severity,
            issuer_nid=entry.issuer_nid,
            signature=entry.signature,
        )
        assert verify_entry(pubkey, tampered) is False

    def test_verify_false_when_severity_changed(self):
        privkey, pubkey = self._make_keypair()
        entry = sign_entry(privkey, self._make_unsigned_entry())
        tampered = ReputationLogEntry(
            v=entry.v,
            log_id=entry.log_id,
            seq=entry.seq,
            timestamp=entry.timestamp,
            subject_nid=entry.subject_nid,
            incident=entry.incident,
            severity=Severity.CRITICAL,  # changed
            issuer_nid=entry.issuer_nid,
            signature=entry.signature,
        )
        assert verify_entry(pubkey, tampered) is False

    def test_verify_false_with_wrong_public_key(self):
        privkey, pubkey = self._make_keypair()
        entry = sign_entry(privkey, self._make_unsigned_entry())
        # Generate a completely different keypair
        _, wrong_pubkey = self._make_keypair()
        assert verify_entry(wrong_pubkey, entry) is False

    def test_signing_is_canonical_key_order_does_not_matter(self):
        """Two sign calls on equivalent entries must produce the same signed payload."""
        privkey = Ed25519PrivateKey.generate()
        pubkey = privkey.public_key()
        entry = self._make_unsigned_entry()
        signed1 = sign_entry(privkey, entry)
        signed2 = sign_entry(privkey, entry)
        # Deterministic: both signatures are identical
        assert signed1.signature == signed2.signature
        # Both verify
        assert verify_entry(pubkey, signed1) is True
        assert verify_entry(pubkey, signed2) is True


# ── Part 5 — Merkle VerifyInclusion ──────────────────────────────────────────

class TestVerifyInclusion:
    """Phase 2 Merkle inclusion-proof verification tests."""

    # ── 1-leaf tree ──────────────────────────────────────────────────────────

    def test_verify_inclusion_single_leaf(self):
        entry = make_signed_entry()
        lh = leaf_hash(entry)
        proof = InclusionProof(
            seq=1,
            leaf_index=0,
            tree_size=1,
            leaf_hash=b64url(lh),
            audit_path=(),
        )
        sth = make_sth(lh, tree_size=1)
        assert ReputationLogClient.verify_inclusion(proof, sth, entry) is True

    # ── 2-leaf tree ──────────────────────────────────────────────────────────

    def test_verify_inclusion_two_leaf_tree(self):
        entry_a = make_signed_entry(subject_nid="urn:nps:agent:test:A")
        entry_b = make_signed_entry(subject_nid="urn:nps:agent:test:B")

        lh_a = leaf_hash(entry_a)
        lh_b = leaf_hash(entry_b)
        root = node_hash(lh_a, lh_b)

        sth = make_sth(root, tree_size=2)

        # Proof for leaf A (index 0): sibling is lhB
        proof_a = InclusionProof(
            seq=1,
            leaf_index=0,
            tree_size=2,
            leaf_hash=b64url(lh_a),
            audit_path=(b64url(lh_b),),
        )
        assert ReputationLogClient.verify_inclusion(proof_a, sth, entry_a) is True

        # Proof for leaf B (index 1): sibling is lhA
        proof_b = InclusionProof(
            seq=2,
            leaf_index=1,
            tree_size=2,
            leaf_hash=b64url(lh_b),
            audit_path=(b64url(lh_a),),
        )
        assert ReputationLogClient.verify_inclusion(proof_b, sth, entry_b) is True

    # ── 4-leaf tree ──────────────────────────────────────────────────────────

    def test_verify_inclusion_four_leaf_tree(self):
        entries = [
            make_signed_entry(subject_nid=f"urn:nps:agent:test:{i}")
            for i in range(4)
        ]
        hashes = [leaf_hash(e) for e in entries]

        n01  = node_hash(hashes[0], hashes[1])
        n23  = node_hash(hashes[2], hashes[3])
        root = node_hash(n01, n23)

        sth = make_sth(root, tree_size=4)

        # index 0: path = [h1, n23]
        proof0 = InclusionProof(seq=1, leaf_index=0, tree_size=4, leaf_hash=b64url(hashes[0]),
                                audit_path=(b64url(hashes[1]), b64url(n23)))
        # index 1: path = [h0, n23]
        proof1 = InclusionProof(seq=2, leaf_index=1, tree_size=4, leaf_hash=b64url(hashes[1]),
                                audit_path=(b64url(hashes[0]), b64url(n23)))
        # index 2: path = [h3, n01]
        proof2 = InclusionProof(seq=3, leaf_index=2, tree_size=4, leaf_hash=b64url(hashes[2]),
                                audit_path=(b64url(hashes[3]), b64url(n01)))
        # index 3: path = [h2, n01]
        proof3 = InclusionProof(seq=4, leaf_index=3, tree_size=4, leaf_hash=b64url(hashes[3]),
                                audit_path=(b64url(hashes[2]), b64url(n01)))

        for proof, entry in zip((proof0, proof1, proof2, proof3), entries):
            assert ReputationLogClient.verify_inclusion(proof, sth, entry) is True

    # ── Tampered entry ───────────────────────────────────────────────────────

    def test_verify_inclusion_false_on_tamper(self):
        entry = make_signed_entry()
        lh = leaf_hash(entry)
        proof = InclusionProof(seq=1, leaf_index=0, tree_size=1,
                               leaf_hash=b64url(lh), audit_path=())
        sth = make_sth(lh, tree_size=1)

        tampered = ReputationLogEntry(
            v=entry.v,
            log_id=entry.log_id,
            seq=entry.seq,
            timestamp=entry.timestamp,
            subject_nid="urn:nps:agent:test:TAMPERED",
            incident=entry.incident,
            severity=entry.severity,
            issuer_nid=entry.issuer_nid,
            signature=entry.signature,
        )
        assert ReputationLogClient.verify_inclusion(proof, sth, tampered) is False

    # ── Wrong root ───────────────────────────────────────────────────────────

    def test_verify_inclusion_false_on_wrong_root(self):
        entry = make_signed_entry()
        lh = leaf_hash(entry)
        proof = InclusionProof(seq=1, leaf_index=0, tree_size=1,
                               leaf_hash=b64url(lh), audit_path=())
        # STH with all-zero root hash
        sth = make_sth(bytes(32), tree_size=1)
        assert ReputationLogClient.verify_inclusion(proof, sth, entry) is False

    # ── Wrong leaf_hash in proof ─────────────────────────────────────────────

    def test_verify_inclusion_false_on_wrong_leaf_hash(self):
        entry = make_signed_entry()
        lh = leaf_hash(entry)
        # proof.leaf_hash is all zeros — but the algorithm uses the entry directly,
        # not proof.leaf_hash for computation. Verify behaviour: the sth root is
        # derived from the real leaf hash; a proof with wrong leaf_hash but correct
        # entry still recomputes the correct hash. What matters is the STH root.
        # Use a wrong STH root derived from all-zero leaf_hash to force False.
        bad_lh = bytes(32)
        proof = InclusionProof(seq=1, leaf_index=0, tree_size=1,
                               leaf_hash=b64url(bad_lh), audit_path=())
        # Make STH match the bad proof (wrong data) so entry recompute won't match
        sth = make_sth(bad_lh, tree_size=1)
        assert ReputationLogClient.verify_inclusion(proof, sth, entry) is False

    # ── Corrupted audit path ──────────────────────────────────────────────────

    def test_verify_inclusion_false_on_corrupted_path(self):
        entry_a = make_signed_entry(subject_nid="urn:nps:agent:test:A")
        entry_b = make_signed_entry(subject_nid="urn:nps:agent:test:B")

        lh_a = leaf_hash(entry_a)
        lh_b = leaf_hash(entry_b)
        root = node_hash(lh_a, lh_b)

        sth = make_sth(root, tree_size=2)

        # Proof for A but with zero-bytes sibling instead of lhB
        corrupt_path = (b64url(bytes(32)),)
        proof_a_bad = InclusionProof(
            seq=1,
            leaf_index=0,
            tree_size=2,
            leaf_hash=b64url(lh_a),
            audit_path=corrupt_path,
        )
        assert ReputationLogClient.verify_inclusion(proof_a_bad, sth, entry_a) is False
