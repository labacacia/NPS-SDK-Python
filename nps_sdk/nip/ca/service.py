# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Core CA business logic: issue, renew, revoke, and verify NID certificates
(NPS-3 §6–8, NPS-CR-0003 groups/sessions, NPS-CR-0005 RA, NPS-RFC-0002 X.509).

Port of the .NET ``NPS.NIP.Ca.NipCaService``. All signing is done with the CA's
Ed25519 private key via an injected :class:`~nps_sdk.nip.identity.NipIdentity`.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime
import json
import secrets
from typing import Any, Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from nps_sdk.nip import error_codes
from nps_sdk.nip.assurance_level import AssuranceLevel
from nps_sdk.nip.frames import IdentFrame, IdentMetadata, RevokeFrame
from nps_sdk.nip.identity import NipIdentity
from nps_sdk.nip.x509.builder import LeafRole, NipX509Builder

from nps_sdk.nip.ca.errors import NipCaException
from nps_sdk.nip.ca.lineage import IdentLineage, IdentLineageRole
from nps_sdk.nip.ca.options import NipCaOptions
from nps_sdk.nip.ca.ra import (
    IBootstrapTokenStore,
    IEnrollmentPolicy,
    IPendingStore,
    create_enrollment_policy,
)
from nps_sdk.nip.ca.store import INipCaStore, NipCaCertRecord


# ── Result ──────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class NipVerifyResult:
    """Result of a NIP certificate verification check."""

    valid: bool
    error_code: str | None = None
    message: str | None = None
    record: NipCaCertRecord | None = None

    @classmethod
    def ok(cls, record: NipCaCertRecord) -> "NipVerifyResult":
        return cls(valid=True, record=record)

    @classmethod
    def fail(cls, error_code: str, message: str) -> "NipVerifyResult":
        return cls(valid=False, error_code=error_code, message=message)


# ── Service ─────────────────────────────────────────────────────────────────────

class NipCaService:
    """CA service. Construct with options, a store, and a loaded CA identity."""

    def __init__(
        self,
        opts: NipCaOptions,
        store: INipCaStore,
        identity: NipIdentity,
    ) -> None:
        self._opts = opts
        self._store = store
        self._identity = identity
        self._root_cert = None  # lazily created

    # ── Root cert (NPS-RFC-0002) ────────────────────────────────────────────────

    @property
    def ca_root_cert(self):
        """Self-signed CA root certificate (generated once per process)."""
        if self._root_cert is None:
            self._root_cert = self._create_root_cert()
        return self._root_cert

    def _create_root_cert(self):
        serial = int.from_bytes(secrets.token_bytes(16), "big") & ((1 << 127) - 1)
        if serial == 0:
            serial = 1
        now = _utcnow()
        return NipX509Builder.issue_root(
            ca_nid=self._opts.ca_nid,
            ca_priv_key=self._priv_key(),
            not_before=now,
            not_after=now + datetime.timedelta(days=3650),
            serial_number=serial,
        )

    # ── Register (Agent / Node) ─────────────────────────────────────────────────

    async def register(
        self,
        entity_type: str,
        identifier: str,
        pub_key: str,
        capabilities: Sequence[str],
        scope_json: str,
        metadata_json: str | None = None,
    ) -> IdentFrame:
        """Register a new Agent or Node, issue an IdentFrame, and persist it."""
        nid = self.build_nid(entity_type, identifier)
        if await self._store.get_by_nid(nid) is not None:
            raise NipCaException(f"NID already exists: {nid}", error_codes.CA_NID_ALREADY_EXISTS)

        self._check_capabilities(capabilities)

        valid_days = (
            self._opts.node_cert_validity_days
            if entity_type == "node"
            else self._opts.agent_cert_validity_days
        )
        now = _utcnow()
        expires_at = now + datetime.timedelta(days=valid_days)
        serial = await self._store.next_serial()

        frame = self._issue_frame(
            nid, pub_key, capabilities, scope_json, now, expires_at, serial, metadata_json
        )
        await self._store.save(
            NipCaCertRecord(
                nid=nid,
                entity_type=entity_type,
                serial=serial,
                pub_key=pub_key,
                capabilities=tuple(capabilities),
                scope_json=scope_json,
                issued_by=self._opts.ca_nid,
                issued_at=now,
                expires_at=expires_at,
                metadata_json=metadata_json,
            )
        )
        return frame

    # ── Register with RA gate (NPS-CR-0005) ─────────────────────────────────────

    async def register_with_ra(
        self,
        entity_type: str,
        identifier: str,
        pub_key: str,
        capabilities: Sequence[str],
        scope_json: str,
        metadata_json: str | None = None,
        enrollment_token: str | None = None,
        enrollment_policy: IEnrollmentPolicy | None = None,
    ) -> IdentFrame:
        """RA-gated registration: run *enrollment_policy* then :meth:`register`."""
        if enrollment_policy is not None:
            await enrollment_policy.check(
                entity_type, identifier, pub_key,
                capabilities, scope_json, metadata_json, enrollment_token,
            )
        return await self.register(
            entity_type, identifier, pub_key, capabilities, scope_json, metadata_json
        )

    @staticmethod
    def create_enrollment_policy(
        opts: NipCaOptions,
        bootstrap_token_store: IBootstrapTokenStore | None = None,
        pending_store: IPendingStore | None = None,
    ) -> IEnrollmentPolicy:
        """Construct the policy selected by ``opts.enrollment_tier``."""
        return create_enrollment_policy(opts, bootstrap_token_store, pending_store)

    # ── Register X.509 (NPS-RFC-0002 prototype) ─────────────────────────────────

    async def register_x509(
        self,
        entity_type: str,
        identifier: str,
        pub_key: str,
        capabilities: Sequence[str],
        scope_json: str,
        root_cert=None,
        assurance_level: AssuranceLevel = AssuranceLevel.ANONYMOUS,
        metadata_json: str | None = None,
    ) -> IdentFrame:
        """Register + issue an IdentFrame carrying a DER X.509 leaf+root chain
        (NPS-RFC-0002 §4.1), alongside the v1 CA-signed JSON proof.
        """
        if root_cert is None:
            root_cert = self.ca_root_cert

        nid = self.build_nid(entity_type, identifier)
        if await self._store.get_by_nid(nid) is not None:
            raise NipCaException(f"NID already exists: {nid}", error_codes.CA_NID_ALREADY_EXISTS)

        self._check_capabilities(capabilities)

        valid_days = (
            self._opts.node_cert_validity_days
            if entity_type == "node"
            else self._opts.agent_cert_validity_days
        )
        now = _utcnow()
        expires_at = now + datetime.timedelta(days=valid_days)
        serial = await self._store.next_serial()

        v1_frame = self._issue_frame(
            nid, pub_key, capabilities, scope_json, now, expires_at, serial,
            metadata_json, assurance_level=assurance_level,
        )

        subject_pub = _extract_ed25519_pubkey(pub_key)
        leaf_serial = _parse_serial_int(serial)
        role = LeafRole.NODE if entity_type == "node" else LeafRole.AGENT

        leaf_cert = NipX509Builder.issue_leaf(
            subject_nid=nid,
            subject_pub_key=subject_pub,
            ca_priv_key=self._priv_key(),
            issuer_nid=self._opts.ca_nid,
            role=role,
            assurance_level=assurance_level,
            not_before=now,
            not_after=expires_at,
            serial_number=leaf_serial,
        )

        from cryptography.hazmat.primitives.serialization import Encoding

        chain = (
            _b64url(leaf_cert.public_bytes(Encoding.DER)),
            _b64url(root_cert.public_bytes(Encoding.DER)),
        )

        await self._store.save(
            NipCaCertRecord(
                nid=nid,
                entity_type=entity_type,
                serial=serial,
                pub_key=pub_key,
                capabilities=tuple(capabilities),
                scope_json=scope_json,
                issued_by=self._opts.ca_nid,
                issued_at=now,
                expires_at=expires_at,
                metadata_json=metadata_json,
            )
        )

        return dataclasses.replace(v1_frame, cert_format="v2-x509", cert_chain=chain)

    # ── Register Group (NPS-CR-0003) ────────────────────────────────────────────

    async def register_group(
        self,
        identifier: str | None,
        pub_key: str,
        capabilities: Sequence[str],
        scope_json: str,
        owner_user_id: str | None = None,
        owner_key_id: str | None = None,
        metadata_json: str | None = None,
    ) -> IdentFrame:
        """Register an orchestrator group NID with ``lineage.role = "group"``
        (NPS-CR-0003 §5.1.3).
        """
        if not identifier:
            identifier = "group-" + secrets.token_hex(16)
        elif not identifier.startswith("group-"):
            raise NipCaException(
                f"Group identifier MUST start with reserved prefix 'group-' "
                f"(got '{identifier}'). NPS-3 §3.1.",
                error_codes.CA_NID_ALREADY_EXISTS,
            )

        nid = self.build_nid("agent", identifier)
        if await self._store.get_by_nid(nid) is not None:
            raise NipCaException(f"NID already exists: {nid}", error_codes.CA_NID_ALREADY_EXISTS)

        self._check_capabilities(capabilities)

        now = _utcnow()
        expires_at = now + datetime.timedelta(days=self._opts.group_cert_validity_days)
        serial = await self._store.next_serial()

        lineage = IdentLineage(
            role=IdentLineageRole.GROUP,
            owner_user_id=owner_user_id,
            owner_key_id=owner_key_id,
        )
        lineage_json = _canonical_json(lineage.to_dict())

        frame = self._issue_frame(
            nid, pub_key, capabilities, scope_json, now, expires_at, serial,
            metadata_json, lineage=lineage,
        )
        await self._store.save(
            NipCaCertRecord(
                nid=nid,
                entity_type="agent",
                serial=serial,
                pub_key=pub_key,
                capabilities=tuple(capabilities),
                scope_json=scope_json,
                issued_by=self._opts.ca_nid,
                issued_at=now,
                expires_at=expires_at,
                metadata_json=metadata_json,
                nid_role=IdentLineageRole.GROUP,
                parent_nid=None,
                lineage_json=lineage_json,
            )
        )
        return frame

    # ── Issue Session (NPS-CR-0003) ─────────────────────────────────────────────

    async def issue_session(
        self,
        group_nid: str,
        session_pub_key: str,
        validity_seconds: int | None = None,
        purpose: str | None = None,
        capabilities: Sequence[str] | None = None,
        scope_json: str | None = None,
        metadata_json: str | None = None,
    ) -> IdentFrame:
        """Issue a short-lived session NID under *group_nid* (NPS-CR-0003 §5.1.3).

        Validity is clamped to ``[session_min, session_max]``; requests outside
        the window raise ``NIP-CA-SESSION-VALIDITY-INVALID``. Session
        capabilities MUST be a subset of the group's.
        """
        group = await self._store.get_by_nid(group_nid)
        if group is None:
            raise NipCaException(
                f"Group NID not found: {group_nid}.", error_codes.CA_PARENT_NOT_FOUND
            )
        if group.nid_role != IdentLineageRole.GROUP:
            raise NipCaException(
                f"NID '{group_nid}' is not registered as a group "
                f"(role='{group.nid_role or '<null>'}').",
                error_codes.CA_PARENT_NOT_GROUP,
            )
        if group.revoked_at is not None:
            raise NipCaException(
                f"Group {group_nid} was revoked at {_iso(group.revoked_at)}; "
                f"cannot issue new sessions.",
                error_codes.CA_GROUP_REVOKED,
            )
        if _utcnow() > group.expires_at:
            raise NipCaException(
                f"Group {group_nid} expired at {_iso(group.expires_at)}; "
                f"cannot issue new sessions.",
                error_codes.CERT_EXPIRED,
            )

        v = validity_seconds if validity_seconds is not None else self._opts.session_default_validity_seconds
        if v < self._opts.session_min_validity_seconds or v > self._opts.session_max_validity_seconds:
            raise NipCaException(
                f"Session validity must be in "
                f"[{self._opts.session_min_validity_seconds}, "
                f"{self._opts.session_max_validity_seconds}] seconds; got {v}.",
                error_codes.CA_SESSION_VALIDITY_INVALID,
            )

        session_caps = tuple(capabilities) if capabilities is not None else group.capabilities
        if capabilities is not None:
            group_cap_set = set(group.capabilities)
            expansion = [c for c in session_caps if c not in group_cap_set]
            if expansion:
                raise NipCaException(
                    f"Session capabilities not in parent group: {', '.join(expansion)}.",
                    error_codes.CA_SCOPE_EXPANSION_DENIED,
                )
        session_scope_json = scope_json if scope_json is not None else group.scope_json

        unix_seconds = int(_utcnow().timestamp())
        rand_hex = secrets.token_bytes(8).hex()
        session_id = f"session-{unix_seconds}-{rand_hex}"
        session_nid = self.build_nid("agent", session_id)

        now = _utcnow()
        expires_at = now + datetime.timedelta(seconds=v)
        serial = await self._store.next_serial()

        lineage = IdentLineage(
            role=IdentLineageRole.SESSION,
            parent_nid=group_nid,
            group_nid=group_nid,
            session_id=session_id,
            purpose=purpose,
            owner_user_id=_extract_lineage_field(group.lineage_json, "owner_user_id"),
            owner_key_id=_extract_lineage_field(group.lineage_json, "owner_key_id"),
        )
        lineage_json = _canonical_json(lineage.to_dict())

        frame = self._issue_frame(
            session_nid, session_pub_key, session_caps, session_scope_json,
            now, expires_at, serial, metadata_json, lineage=lineage,
        )
        await self._store.save(
            NipCaCertRecord(
                nid=session_nid,
                entity_type="agent",
                serial=serial,
                pub_key=session_pub_key,
                capabilities=session_caps,
                scope_json=session_scope_json,
                issued_by=self._opts.ca_nid,
                issued_at=now,
                expires_at=expires_at,
                metadata_json=metadata_json,
                nid_role=IdentLineageRole.SESSION,
                parent_nid=group_nid,
                lineage_json=lineage_json,
            )
        )
        return frame

    async def list_sessions(self, group_nid: str) -> list[NipCaCertRecord]:
        """List every session NID issued under *group_nid* (live + revoked)."""
        return await self._store.get_by_parent_nid(group_nid)

    async def get_cert(self, nid: str) -> NipCaCertRecord | None:
        """Return the persisted record for *nid*, or ``None``."""
        return await self._store.get_by_nid(nid)

    # ── Renew ────────────────────────────────────────────────────────────────────

    async def renew(self, nid: str) -> IdentFrame:
        """Renew a certificate — only within the renewal window."""
        record = await self._store.get_by_nid(nid)
        if record is None:
            raise NipCaException(f"NID not found: {nid}", error_codes.CA_NID_NOT_FOUND)
        if record.revoked_at is not None:
            raise NipCaException(f"NID is revoked: {nid}", error_codes.CERT_REVOKED)

        now = _utcnow()
        renew_window_start = record.expires_at - datetime.timedelta(
            days=self._opts.renewal_window_days
        )
        if now < renew_window_start:
            raise NipCaException(
                f"Renewal window opens {_iso(renew_window_start)}. Too early to renew.",
                error_codes.CA_RENEWAL_TOO_EARLY,
            )

        valid_days = (
            self._opts.node_cert_validity_days
            if record.entity_type == "node"
            else self._opts.agent_cert_validity_days
        )
        expires_at = now + datetime.timedelta(days=valid_days)
        serial = await self._store.next_serial()

        frame = self._issue_frame(
            nid, record.pub_key, record.capabilities, record.scope_json,
            now, expires_at, serial, record.metadata_json,
        )
        await self._store.save(
            NipCaCertRecord(
                nid=nid,
                entity_type=record.entity_type,
                serial=serial,
                pub_key=record.pub_key,
                capabilities=record.capabilities,
                scope_json=record.scope_json,
                issued_by=self._opts.ca_nid,
                issued_at=now,
                expires_at=expires_at,
                metadata_json=record.metadata_json,
            )
        )
        return frame

    # ── Revoke ───────────────────────────────────────────────────────────────────

    async def revoke(self, nid: str, reason: str) -> RevokeFrame:
        """Revoke a certificate and return the signed RevokeFrame.

        When the target is a group, every live session NID under it is also
        revoked with reason ``parent_revoked`` (NPS-CR-0003 §5.3).
        """
        record = await self._store.get_by_nid(nid)
        if record is None:
            raise NipCaException(f"NID not found: {nid}", error_codes.CA_NID_NOT_FOUND)

        now = _utcnow()
        revoked = await self._store.revoke(nid, reason, now)
        if not revoked:
            raise NipCaException(f"Failed to revoke {nid}.", error_codes.CA_NID_NOT_FOUND)

        cascade_parent: str | None = None
        if record.nid_role == IdentLineageRole.GROUP:
            for child in await self._store.get_by_parent_nid(nid):
                if child.revoked_at is not None:
                    continue
                await self._store.revoke(child.nid, "parent_revoked", now)

        payload = {
            "frame": "0x22",
            "target_nid": nid,
            "serial": record.serial,
            "reason": reason,
            "revoked_at": _iso(now),
            "signer_nid": self._opts.ca_nid,
        }
        signature = self._identity.sign(payload)

        # RevokeFrame's parent-rule requires parent_nid iff reason is
        # parent_revoked. Group revocation carries the operator reason, not
        # parent_revoked, so parent_nid stays absent (matches .NET wire).
        if reason == "parent_revoked":
            cascade_parent = record.parent_nid

        return RevokeFrame(
            target_nid=nid,
            reason=reason,
            revoked_at=_iso(now),
            signer_nid=self._opts.ca_nid,
            signature=signature,
            serial=record.serial,
            parent_nid=cascade_parent,
        )

    # ── Verify (OCSP) ────────────────────────────────────────────────────────────

    async def verify(self, nid: str) -> NipVerifyResult:
        """Verify a NID: existence, expiry, revocation, and — for sessions —
        chain up to the group (NPS-3 §7 step 3a).
        """
        record = await self._store.get_by_nid(nid)
        if record is None:
            return NipVerifyResult.fail(error_codes.CA_NID_NOT_FOUND, "NID not found.")
        if record.revoked_at is not None:
            return NipVerifyResult.fail(
                error_codes.CERT_REVOKED,
                f"Revoked at {_iso(record.revoked_at)}: {record.revoke_reason}",
            )
        if _utcnow() > record.expires_at:
            return NipVerifyResult.fail(
                error_codes.CERT_EXPIRED, f"Expired at {_iso(record.expires_at)}."
            )

        if record.parent_nid:
            parent = await self._store.get_by_nid(record.parent_nid)
            if parent is None:
                return NipVerifyResult.fail(
                    error_codes.CERT_PARENT_REVOKED,
                    f"Parent NID {record.parent_nid} not found.",
                )
            if parent.revoked_at is not None:
                return NipVerifyResult.fail(
                    error_codes.CERT_PARENT_REVOKED,
                    f"Parent {record.parent_nid} revoked at {_iso(parent.revoked_at)}: "
                    f"{parent.revoke_reason}",
                )
            if _utcnow() > parent.expires_at:
                return NipVerifyResult.fail(
                    error_codes.CERT_PARENT_REVOKED,
                    f"Parent {record.parent_nid} expired at {_iso(parent.expires_at)}.",
                )

        return NipVerifyResult.ok(record)

    # ── CRL / listing / signing ──────────────────────────────────────────────────

    async def get_crl(self) -> list[NipCaCertRecord]:
        """Return the current Certificate Revocation List (NPS-3 §8)."""
        return await self._store.get_revoked()

    async def list_certificates(self) -> list[NipCaCertRecord]:
        """Return all certificate records from the backing store."""
        return await self._store.list()

    def sign_artifact(self, artifact: Any) -> str:
        """Sign an arbitrary CA-owned JSON artifact with the CA Ed25519 key."""
        return self._identity.sign(artifact)

    def get_ca_public_key(self) -> str:
        """Return the CA public key in ``ed25519:<base64url>`` format."""
        return self._identity.pub_key_string

    # ── NID builder ──────────────────────────────────────────────────────────────

    def build_nid(self, entity_type: str, identifier: str) -> str:
        """Build a NID: ``urn:nps:{type}:{domain}:{identifier}``.

        The domain is extracted from the CA NID (4th ``:``-delimited segment).
        """
        parts = self._opts.ca_nid.split(":")
        domain = parts[3] if len(parts) >= 4 else self._opts.ca_nid
        return f"urn:nps:{entity_type}:{domain}:{identifier}"

    # ── Private ──────────────────────────────────────────────────────────────────

    def _issue_frame(
        self,
        nid: str,
        pub_key: str,
        capabilities: Sequence[str],
        scope_json: str,
        issued_at: datetime.datetime,
        expires_at: datetime.datetime,
        serial: str,
        metadata_json: str | None,
        assurance_level: AssuranceLevel | None = None,
        lineage: IdentLineage | None = None,
    ) -> IdentFrame:
        scope = json.loads(scope_json)
        issued_at_str = _iso(issued_at)
        expires_at_str = _iso(expires_at)

        # Canonical signed payload — alphabetical (sort_keys) via NipIdentity.sign.
        # assurance_level / lineage are included only when set so pre-RFC-0003 /
        # pre-CR-0003 frames stay bit-compatible.
        payload: dict[str, Any] = {
            "capabilities": list(capabilities),
            "expires_at": expires_at_str,
            "frame": "0x20",
            "issued_at": issued_at_str,
            "issued_by": self._opts.ca_nid,
            "nid": nid,
            "pub_key": pub_key,
            "scope": scope,
            "serial": serial,
        }
        if assurance_level is not None:
            payload["assurance_level"] = assurance_level.wire
        if lineage is not None:
            payload["lineage"] = lineage.to_dict()
        signature = self._identity.sign(payload)

        metadata = None
        if metadata_json is not None:
            metadata = IdentMetadata.from_dict(json.loads(metadata_json))

        return IdentFrame(
            nid=nid,
            pub_key=pub_key,
            capabilities=tuple(capabilities),
            scope=scope,
            issued_by=self._opts.ca_nid,
            issued_at=issued_at_str,
            expires_at=expires_at_str,
            serial=serial,
            signature=signature,
            metadata=metadata,
            assurance_level=assurance_level,
            lineage=lineage,
        )

    def _check_capabilities(self, capabilities: Sequence[str]) -> None:
        if self._opts.allowed_capabilities is not None:
            disallowed = [
                c for c in capabilities if c not in self._opts.allowed_capabilities
            ]
            if disallowed:
                raise NipCaException(
                    f"Capabilities not permitted by this CA: {', '.join(disallowed)}",
                    error_codes.CERT_CAPABILITY_MISSING,
                )

    def _priv_key(self) -> Ed25519PrivateKey:
        key = self._identity._private_key  # noqa: SLF001 — CA owns its identity
        if key is None:
            raise RuntimeError("CA identity not loaded.")
        return key


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    """Round-trip ISO-8601 with microseconds + trailing Z (matches .NET "O")."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="microseconds") + "Z"


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _extract_ed25519_pubkey(encoded: str) -> Ed25519PublicKey:
    prefix = "ed25519:"
    if not encoded.startswith(prefix):
        raise NipCaException(
            f"X.509 issuance requires an ed25519:* pubkey; got '{encoded}'.",
            error_codes.CERT_FORMAT_INVALID,
        )
    return NipIdentity._parse_pub_key(encoded)  # noqa: SLF001


def _parse_serial_int(serial: str) -> int:
    hex_str = serial[2:] if serial.lower().startswith("0x") else serial
    value = int(hex_str, 16) if hex_str else 1
    return value if value > 0 else 1


def _extract_lineage_field(lineage_json: str | None, field: str) -> str | None:
    if not lineage_json:
        return None
    try:
        doc = json.loads(lineage_json)
        v = doc.get(field)
        return v if isinstance(v, str) else None
    except Exception:
        return None
