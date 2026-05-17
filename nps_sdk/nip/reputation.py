# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS-RFC-0004 — ReputationLogClient

Provides data types, signing helpers, HTTP client, and Merkle inclusion-proof
verification for the NPS reputation log.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
from enum import Enum
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _b64u_encode(b: bytes) -> str:
    """Base64url encode (no padding)."""
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    """Base64url decode (tolerates missing padding)."""
    # Add padding as needed
    padding = (-len(s)) % 4
    return base64.urlsafe_b64decode(s + "=" * padding)


def _sort_keys_recursive(obj: Any) -> Any:
    """Recursively sort dict keys for canonical JSON."""
    if isinstance(obj, dict):
        return {key: _sort_keys_recursive(val) for key, val in sorted(obj.items())}
    if isinstance(obj, list):
        return [_sort_keys_recursive(item) for item in obj]
    return obj


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


# ── Enums ─────────────────────────────────────────────────────────────────────

class IncidentType(Enum):
    """
    NPS-RFC-0004 incident type.

    Unknown wire values map to ``OTHER`` (forward-compatible).
    The original raw string is preserved in the entry's ``incident_raw`` attribute
    so round-trips through OTHER are lossless.
    """

    CERT_REVOKED         = "cert-revoked"
    RATE_LIMIT_VIOLATION = "rate-limit-violation"
    TOS_VIOLATION        = "tos-violation"
    SCRAPING_PATTERN     = "scraping-pattern"
    PAYMENT_DEFAULT      = "payment-default"
    CONTRACT_DISPUTE     = "contract-dispute"
    IMPERSONATION_CLAIM  = "impersonation-claim"
    POSITIVE_ATTESTATION = "positive-attestation"
    OTHER                = "__other__"

    @classmethod
    def from_wire(cls, value: str) -> "IncidentType":
        """Parse a wire string; unknown values return ``OTHER``."""
        for member in cls:
            if member.value == value:
                return member
        return cls.OTHER

    @property
    def wire(self) -> str:
        """Return the kebab-case wire string."""
        return self.value


class Severity(Enum):
    """
    NPS-RFC-0004 severity level (ordered enum).

    Unknown wire values raise ``ValueError`` (no forward-compat).
    """

    INFO     = 0
    MINOR    = 1
    MODERATE = 2
    MAJOR    = 3
    CRITICAL = 4

    @classmethod
    def from_wire(cls, value: str) -> "Severity":
        """Parse a wire string; unknown values raise ``ValueError``."""
        for member in cls:
            if member.name.lower() == value.lower():
                return member
        raise ValueError(f"Unknown severity: {value!r}")

    @property
    def wire(self) -> str:
        return self.name.lower()

    # Make severity levels comparable by their integer value.
    def __lt__(self, other: "Severity") -> bool:
        return self.value < other.value

    def __le__(self, other: "Severity") -> bool:
        return self.value <= other.value

    def __gt__(self, other: "Severity") -> bool:
        return self.value > other.value

    def __ge__(self, other: "Severity") -> bool:
        return self.value >= other.value


# ── Data types ────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class ObservationWindow:
    """Optional time window for the observation in a reputation log entry."""

    start: str
    end:   str

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ObservationWindow":
        return cls(start=data["start"], end=data["end"])


@dataclasses.dataclass(frozen=True)
class ReputationLogEntry:
    """
    NPS-RFC-0004 reputation log entry (all 12 wire fields).

    ``incident_raw`` is an extension field — not part of the wire format —
    used to preserve the original wire string when ``incident == IncidentType.OTHER``.
    """

    # Required fields
    v:           int
    log_id:      str
    seq:         int
    timestamp:   str
    subject_nid: str
    incident:    IncidentType
    severity:    Severity
    issuer_nid:  str

    # Optional fields
    window:          ObservationWindow | None = None
    observation:     Any                      = None  # arbitrary JSON
    evidence_ref:    str | None               = None
    evidence_sha256: str | None               = None
    signature:       str | None               = None

    # Non-wire field: preserves raw incident string for IncidentType.OTHER
    incident_raw: str | None = dataclasses.field(default=None, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Return the full wire dict (all non-None fields)."""
        d: dict[str, Any] = {
            "v":           self.v,
            "log_id":      self.log_id,
            "seq":         self.seq,
            "timestamp":   self.timestamp,
            "subject_nid": self.subject_nid,
            "incident":    self.incident_raw if self.incident is IncidentType.OTHER and self.incident_raw is not None else self.incident.wire,
            "severity":    self.severity.wire,
            "issuer_nid":  self.issuer_nid,
        }
        if self.window is not None:
            d["window"] = self.window.to_dict()
        if self.observation is not None:
            d["observation"] = self.observation
        if self.evidence_ref is not None:
            d["evidence_ref"] = self.evidence_ref
        if self.evidence_sha256 is not None:
            d["evidence_sha256"] = self.evidence_sha256
        if self.signature is not None:
            d["signature"] = self.signature
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReputationLogEntry":
        incident_str = data["incident"]
        incident     = IncidentType.from_wire(incident_str)
        incident_raw = incident_str if incident is IncidentType.OTHER else None

        window_raw = data.get("window")
        window = ObservationWindow.from_dict(window_raw) if window_raw is not None else None

        return cls(
            v=data.get("v", 1),
            log_id=data["log_id"],
            seq=data.get("seq", 0),
            timestamp=data.get("timestamp", ""),
            subject_nid=data["subject_nid"],
            incident=incident,
            incident_raw=incident_raw,
            severity=Severity.from_wire(data["severity"]),
            issuer_nid=data["issuer_nid"],
            window=window,
            observation=data.get("observation"),
            evidence_ref=data.get("evidence_ref"),
            evidence_sha256=data.get("evidence_sha256"),
            signature=data.get("signature"),
        )


@dataclasses.dataclass(frozen=True)
class SignedTreeHead:
    """NPS-RFC-0004 signed tree head."""

    log_id:          str
    tree_size:       int
    timestamp:       str
    sha256_root_hash: str  # base64url
    signature:       str   # ed25519:<base64url>

    def to_dict(self) -> dict[str, Any]:
        return {
            "log_id":           self.log_id,
            "tree_size":        self.tree_size,
            "timestamp":        self.timestamp,
            "sha256_root_hash": self.sha256_root_hash,
            "signature":        self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SignedTreeHead":
        return cls(
            log_id=data["log_id"],
            tree_size=data["tree_size"],
            timestamp=data["timestamp"],
            sha256_root_hash=data["sha256_root_hash"],
            signature=data["signature"],
        )


@dataclasses.dataclass(frozen=True)
class InclusionProof:
    """NPS-RFC-0004 Merkle inclusion proof."""

    seq:        int
    leaf_index: int
    tree_size:  int
    leaf_hash:  str         # base64url
    audit_path: tuple[str, ...]  # base64url steps

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq":        self.seq,
            "leaf_index": self.leaf_index,
            "tree_size":  self.tree_size,
            "leaf_hash":  self.leaf_hash,
            "audit_path": list(self.audit_path),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InclusionProof":
        return cls(
            seq=data["seq"],
            leaf_index=data["leaf_index"],
            tree_size=data["tree_size"],
            leaf_hash=data["leaf_hash"],
            audit_path=tuple(data.get("audit_path", [])),
        )


# ── Exception ─────────────────────────────────────────────────────────────────

class ReputationLogException(Exception):
    """
    Raised when the reputation log API returns an error response.

    Attributes:
        nip_error_code: The NIP error code from the response (e.g. "NIP-4001").
        nps_status:     The NPS status string from the response.
    """

    def __init__(
        self,
        nip_error_code: str,
        nps_status: str,
        message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.nip_error_code = nip_error_code
        self.nps_status     = nps_status


# ── Canonical JSON helpers ────────────────────────────────────────────────────

def canonical_json_for_signing(entry: ReputationLogEntry) -> bytes:
    """
    Return the canonical JSON bytes for signing (excludes ``signature`` field).

    Per spec:
    - All entry fields using wire names, EXCLUDING ``signature`` and any None fields.
    - If ``incident == IncidentType.OTHER`` and ``incident_raw`` is set, emit the
      raw string; otherwise emit the kebab-case wire string.
    - Keys are sorted recursively; serialized with ``separators=(",", ":")``.
    """
    d = entry.to_dict()
    d.pop("signature", None)
    sorted_d = _sort_keys_recursive(d)
    return json.dumps(sorted_d, separators=(",", ":"), sort_keys=False).encode("utf-8")


def canonical_json_for_leaf(entry: ReputationLogEntry) -> bytes:
    """
    Return the canonical JSON bytes for Merkle leaf hashing (INCLUDES ``signature``).

    Per spec the full entry including the signature field is used for leaf hashing.
    """
    d = entry.to_dict()
    sorted_d = _sort_keys_recursive(d)
    return json.dumps(sorted_d, separators=(",", ":"), sort_keys=False).encode("utf-8")


# ── Signing helpers ───────────────────────────────────────────────────────────

def sign_entry(
    private_key: Ed25519PrivateKey,
    entry: ReputationLogEntry,
) -> ReputationLogEntry:
    """
    Sign *entry* with *private_key* and return a new entry with ``.signature``
    populated as ``"ed25519:<base64url>"``.
    """
    payload  = canonical_json_for_signing(entry)
    raw_sig  = private_key.sign(payload)
    sig_str  = f"ed25519:{_b64u_encode(raw_sig)}"
    return dataclasses.replace(entry, signature=sig_str)


def verify_entry(
    public_key: Ed25519PublicKey,
    entry: ReputationLogEntry,
) -> bool:
    """
    Verify the issuer-side Ed25519 signature on *entry*.

    Returns ``True`` if valid, ``False`` otherwise (never raises on a bad sig).
    """
    if entry.signature is None:
        return False
    if not entry.signature.startswith("ed25519:"):
        return False
    try:
        raw_sig = _b64u_decode(entry.signature[len("ed25519:"):])
        payload = canonical_json_for_signing(entry)
        public_key.verify(raw_sig, payload)
        return True
    except Exception:
        return False


# ── HTTP client ───────────────────────────────────────────────────────────────

class ReputationLogClient:
    """
    Async HTTP client for the NPS-RFC-0004 reputation log API.

    Usage::

        async with ReputationLogClient("https://log.example.com") as client:
            entry = await client.submit(my_entry)
            sth   = await client.get_sth()
            proof = await client.get_proof(entry.seq)
            ok    = ReputationLogClient.verify_inclusion(proof, sth, entry)
    """

    def __init__(
        self,
        base_url: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url    = base_url.rstrip("/")
        self._http        = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> "ReputationLogClient":
        if self._owns_client:
            self._http = httpx.AsyncClient()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_client and self._http is not None:
            await self._http.aclose()
            self._http = None

    # ── API methods ──────────────────────────────────────────────────────────

    async def submit(self, entry: ReputationLogEntry) -> ReputationLogEntry:
        """
        POST /v1/log/entries — submit a reputation log entry.

        Returns the entry as echoed back by the server (with ``seq``,
        ``timestamp``, and ``log_id`` filled in by the server).
        """
        resp = await self._post("/v1/log/entries", entry.to_dict())
        return ReputationLogEntry.from_dict(resp)

    async def query(
        self,
        nid: str | None = None,
        since_seq: int | None = None,
    ) -> list[ReputationLogEntry]:
        """
        GET /v1/log/entries — query reputation log entries.

        Args:
            nid:       Filter by subject NID (optional).
            since_seq: Return only entries with seq > this value (optional).

        Returns a list of ``ReputationLogEntry`` objects.
        """
        params: dict[str, Any] = {}
        if nid is not None:
            params["nid"] = nid
        if since_seq is not None:
            params["since"] = since_seq
        resp = await self._get("/v1/log/entries", params=params)
        return [ReputationLogEntry.from_dict(e) for e in resp.get("entries", [])]

    async def get_sth(self) -> SignedTreeHead:
        """GET /v1/log/sth — fetch the current signed tree head."""
        resp = await self._get("/v1/log/sth")
        return SignedTreeHead.from_dict(resp)

    async def get_proof(self, seq: int) -> InclusionProof:
        """GET /v1/log/proof?seq=<seq> — fetch a Merkle inclusion proof."""
        resp = await self._get("/v1/log/proof", params={"seq": seq})
        return InclusionProof.from_dict(resp)

    async def get_gossip_sth(self) -> SignedTreeHead:
        """GET /v1/log/gossip/sth — fetch a gossiped signed tree head."""
        resp = await self._get("/v1/log/gossip/sth")
        return SignedTreeHead.from_dict(resp)

    # ── Merkle verification ──────────────────────────────────────────────────

    @staticmethod
    def verify_inclusion(
        proof: InclusionProof,
        sth: SignedTreeHead,
        entry: ReputationLogEntry,
    ) -> bool:
        """
        Verify a Merkle inclusion proof (RFC 9162 fold algorithm).

        Returns ``True`` if *entry* is included in the tree described by *sth*
        at position *proof.leaf_index*, ``False`` otherwise.

        Leaf hash:  SHA256(0x00 || canonical_json_bytes_of_full_entry_including_signature)
        Node hash:  SHA256(0x01 || left_hash || right_hash)
        """
        try:
            leaf_bytes  = canonical_json_for_leaf(entry)
            node_hash   = _sha256(b"\x00" + leaf_bytes)

            for i, step_b64 in enumerate(proof.audit_path):
                sibling = _b64u_decode(step_b64)
                if (proof.leaf_index >> i) & 1 == 0:
                    # current node is left child
                    node_hash = _sha256(b"\x01" + node_hash + sibling)
                else:
                    # current node is right child
                    node_hash = _sha256(b"\x01" + sibling + node_hash)

            root_bytes = _b64u_decode(sth.sha256_root_hash)
            return node_hash == root_bytes
        except Exception:
            return False

    # ── Private helpers ──────────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError(
                "ReputationLogClient must be used as an async context manager "
                "or an httpx.AsyncClient must be supplied at construction time."
            )
        return self._http

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url  = self._base_url + path
        resp = await self._client().get(url, params=params)
        return self._parse(resp)

    async def _post(
        self,
        path: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        url  = self._base_url + path
        resp = await self._client().post(url, json=body)
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except Exception as exc:
            resp.raise_for_status()
            raise ReputationLogException(
                nip_error_code="NIP-0000",
                nps_status="parse-error",
                message=f"Could not parse response JSON: {exc}",
            ) from exc

        if resp.is_error:
            raise ReputationLogException(
                nip_error_code=data.get("error", "NIP-0000"),
                nps_status=data.get("status", "unknown"),
                message=data.get("message"),
            )

        return data
