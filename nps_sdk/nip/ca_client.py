# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Typed remote client for the NIP CA HTTP API."""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx


@dataclasses.dataclass(frozen=True)
class NipCaRegisterRequest:
    identifier: str
    pub_key: str
    capabilities: tuple[str, ...] = ()
    scope_json: str | None = None
    metadata_json: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "identifier": self.identifier,
            "pub_key": self.pub_key,
            "capabilities": list(self.capabilities),
        }
        if self.scope_json is not None:
            d["scope_json"] = self.scope_json
        if self.metadata_json is not None:
            d["metadata_json"] = self.metadata_json
        return d


@dataclasses.dataclass(frozen=True)
class NipCaRegisterX509Request(NipCaRegisterRequest):
    assurance_level: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        if self.assurance_level is not None:
            d["assurance_level"] = self.assurance_level
        return d


@dataclasses.dataclass(frozen=True)
class NipCaIdentFrame:
    nid: str
    pub_key: str
    capabilities: tuple[str, ...] = ()
    scope: Any | None = None
    issued_by: str | None = None
    issued_at: str | None = None
    expires_at: str | None = None
    serial: str | None = None
    signature: str | None = None
    frame: str | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    assurance_level: str | None = None
    cert_format: str | None = None
    cert_chain: tuple[str, ...] = ()
    ocsp_staple: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NipCaIdentFrame":
        return cls(
            nid=data["nid"],
            pub_key=data["pub_key"],
            capabilities=tuple(data.get("capabilities", [])),
            scope=data.get("scope"),
            issued_by=data.get("issued_by"),
            issued_at=data.get("issued_at"),
            expires_at=data.get("expires_at"),
            serial=data.get("serial"),
            signature=data.get("signature"),
            frame=data.get("frame"),
            metadata=dict(data.get("metadata", {})),
            assurance_level=data.get("assurance_level"),
            cert_format=data.get("cert_format"),
            cert_chain=tuple(data.get("cert_chain", [])),
            ocsp_staple=data.get("ocsp_staple"),
        )


@dataclasses.dataclass(frozen=True)
class NipCaCrlEntry:
    nid: str
    serial: str
    revoked_at: str | None = None
    reason: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NipCaCrlEntry":
        return cls(
            nid=data["nid"],
            serial=data["serial"],
            revoked_at=data.get("revoked_at"),
            reason=data.get("reason"),
        )


@dataclasses.dataclass(frozen=True)
class NipCaCrl:
    issued_by: str
    issued_at: str
    entries: tuple[NipCaCrlEntry, ...]
    signature: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NipCaCrl":
        return cls(
            issued_by=data["issued_by"],
            issued_at=data["issued_at"],
            entries=tuple(NipCaCrlEntry.from_dict(x) for x in data.get("entries", [])),
            signature=data["signature"],
        )


@dataclasses.dataclass(frozen=True)
class NipCaRevokeFrame:
    target_nid: str | None = None
    nid: str | None = None
    serial: str | None = None
    reason: str | None = None
    revoked_at: str | None = None
    signature: str | None = None
    frame: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NipCaRevokeFrame":
        return cls(
            target_nid=data.get("target_nid"),
            nid=data.get("nid"),
            serial=data.get("serial"),
            reason=data.get("reason"),
            revoked_at=data.get("revoked_at"),
            signature=data.get("signature"),
            frame=data.get("frame"),
        )


@dataclasses.dataclass(frozen=True)
class NipCaDiscoveryDocument:
    nps_ca: str
    issuer: str
    public_key: str
    display_name: str | None = None
    algorithms: tuple[str, ...] = ()
    endpoints: dict[str, Any] = dataclasses.field(default_factory=dict)
    capabilities: tuple[str, ...] = ()
    max_cert_validity_days: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NipCaDiscoveryDocument":
        return cls(
            nps_ca=data["nps_ca"],
            issuer=data["issuer"],
            public_key=data["public_key"],
            display_name=data.get("display_name"),
            algorithms=tuple(data.get("algorithms", [])),
            endpoints=dict(data.get("endpoints", {})),
            capabilities=tuple(data.get("capabilities", [])),
            max_cert_validity_days=data.get("max_cert_validity_days"),
        )


@dataclasses.dataclass(frozen=True)
class NipCaVerifyResponse:
    valid: bool
    nid: str | None = None
    expires_at: str | None = None
    serial: str | None = None
    error_code: str | None = None
    message: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NipCaVerifyResponse":
        return cls(
            valid=bool(data.get("valid", False)),
            nid=data.get("nid"),
            expires_at=data.get("expires_at"),
            serial=data.get("serial"),
            error_code=data.get("error_code"),
            message=data.get("message"),
        )


class NipCaClientError(RuntimeError):
    def __init__(self, error_code: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


class NipCaClient:
    """Async typed client for remote NIP CA routes."""

    def __init__(
        self,
        base_url: str,
        *,
        route_prefix: str = "",
        timeout: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._prefix = route_prefix.rstrip("/")
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> "NipCaClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def get_discovery(self) -> NipCaDiscoveryDocument:
        return NipCaDiscoveryDocument.from_dict(await self._get_json("/.well-known/nps-ca"))

    async def get_crl(self) -> NipCaCrl:
        return NipCaCrl.from_dict(await self._get_json(f"{self._prefix}/v1/crl"))

    async def register_agent(self, request: NipCaRegisterRequest, bearer_token: str | None = None) -> NipCaIdentFrame:
        data = await self._send_json("POST", f"{self._prefix}/v1/agents/register", request.to_dict(), bearer_token)
        return NipCaIdentFrame.from_dict(data)

    async def register_node(self, request: NipCaRegisterRequest, bearer_token: str | None = None) -> NipCaIdentFrame:
        data = await self._send_json("POST", f"{self._prefix}/v1/nodes/register", request.to_dict(), bearer_token)
        return NipCaIdentFrame.from_dict(data)

    async def register_agent_x509(
        self,
        request: NipCaRegisterX509Request,
        bearer_token: str | None = None,
    ) -> NipCaIdentFrame:
        data = await self._send_json("POST", f"{self._prefix}/v1/agents/register-x509", request.to_dict(), bearer_token)
        return NipCaIdentFrame.from_dict(data)

    async def register_node_x509(
        self,
        request: NipCaRegisterX509Request,
        bearer_token: str | None = None,
    ) -> NipCaIdentFrame:
        data = await self._send_json("POST", f"{self._prefix}/v1/nodes/register-x509", request.to_dict(), bearer_token)
        return NipCaIdentFrame.from_dict(data)

    async def renew_agent(self, nid: str, bearer_token: str | None = None) -> NipCaIdentFrame:
        data = await self._send_json("POST", f"{self._prefix}/v1/agents/{_escape(nid)}/renew", None, bearer_token)
        return NipCaIdentFrame.from_dict(data)

    async def renew_node(self, nid: str, bearer_token: str | None = None) -> NipCaIdentFrame:
        data = await self._send_json("POST", f"{self._prefix}/v1/nodes/{_escape(nid)}/renew", None, bearer_token)
        return NipCaIdentFrame.from_dict(data)

    async def revoke_agent(
        self,
        nid: str,
        reason: str = "cessation_of_operation",
        bearer_token: str | None = None,
    ) -> NipCaRevokeFrame:
        data = await self._send_json(
            "POST",
            f"{self._prefix}/v1/agents/{_escape(nid)}/revoke",
            {"reason": reason},
            bearer_token,
        )
        return NipCaRevokeFrame.from_dict(data)

    async def revoke_node(
        self,
        nid: str,
        reason: str = "cessation_of_operation",
        bearer_token: str | None = None,
    ) -> NipCaRevokeFrame:
        data = await self._send_json(
            "POST",
            f"{self._prefix}/v1/nodes/{_escape(nid)}/revoke",
            {"reason": reason},
            bearer_token,
        )
        return NipCaRevokeFrame.from_dict(data)

    async def verify_agent(self, nid: str) -> NipCaVerifyResponse:
        return NipCaVerifyResponse.from_dict(await self._get_json(f"{self._prefix}/v1/agents/{_escape(nid)}/verify"))

    async def verify_node(self, nid: str) -> NipCaVerifyResponse:
        return NipCaVerifyResponse.from_dict(await self._get_json(f"{self._prefix}/v1/nodes/{_escape(nid)}/verify"))

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _get_json(self, path: str) -> dict[str, Any]:
        response = await self._http.get(f"{self._base_url}{path}")
        return await _read_json_response(response)

    async def _send_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        bearer_token: str | None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else None
        response = await self._http.request(
            method,
            f"{self._base_url}{path}",
            json=body,
            headers=headers,
        )
        return await _read_json_response(response)


async def _read_json_response(response: httpx.Response) -> dict[str, Any]:
    if 200 <= response.status_code < 300:
        data = response.json()
        if not isinstance(data, dict):
            raise NipCaClientError("NIP-CA-EMPTY-RESPONSE", "NIP CA returned a non-object JSON response.")
        return data
    try:
        data = response.json()
    except ValueError:
        data = {}
    raise NipCaClientError(
        str(data.get("error_code") or data.get("error") or "NIP-CA-HTTP-ERROR"),
        str(data.get("message") or f"NIP CA returned HTTP {response.status_code}."),
        response.status_code,
    )


def _escape(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
