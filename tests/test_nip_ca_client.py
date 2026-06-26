# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

import json

import httpx
import pytest
import respx

from nps_sdk.nip import NipCaClient, NipCaClientError, NipCaRegisterRequest


BASE_URL = "https://ca.example.test"


def ident_payload() -> dict[str, object]:
    return {
        "frame": "0x20",
        "nid": "urn:nps:agent:example.test:a",
        "pub_key": "ed25519:a",
        "capabilities": ["nwp:query"],
        "scope": {},
        "issued_by": "urn:nps:org:example.test",
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-01-02T00:00:00Z",
        "serial": "0x1",
        "signature": "ed25519:sig",
    }


@pytest.mark.asyncio
@respx.mock
async def test_register_agent_sends_typed_request_with_bearer() -> None:
    route = respx.post(f"{BASE_URL}/nip/v1/agents/register").mock(
        return_value=httpx.Response(201, json=ident_payload())
    )

    async with NipCaClient(BASE_URL, route_prefix="/nip") as client:
        frame = await client.register_agent(
            NipCaRegisterRequest("a", "ed25519:a", ("nwp:query",), "{}"),
            bearer_token="secret",
        )

    assert frame.nid == "urn:nps:agent:example.test:a"
    assert route.calls.last.request.headers["Authorization"] == "Bearer secret"
    assert json.loads(route.calls.last.request.content)["identifier"] == "a"


@pytest.mark.asyncio
@respx.mock
async def test_error_response_throws_typed_exception() -> None:
    respx.post(f"{BASE_URL}/v1/agents/urn%3Anps%3Aagent%3Aexample.test%3Aa/renew").mock(
        return_value=httpx.Response(
            401,
            json={"error_code": "NIP-CA-UNAUTHORIZED", "message": "nope"},
        )
    )

    async with NipCaClient(BASE_URL) as client:
        with pytest.raises(NipCaClientError) as exc_info:
            await client.renew_agent("urn:nps:agent:example.test:a")

    assert exc_info.value.error_code == "NIP-CA-UNAUTHORIZED"
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@respx.mock
async def test_revoke_agent_returns_typed_revoke_frame() -> None:
    route = respx.post(f"{BASE_URL}/v1/agents/urn%3Anps%3Aagent%3Aexample.test%3Aa/revoke").mock(
        return_value=httpx.Response(
            200,
            json={
                "frame": "0x22",
                "target_nid": "urn:nps:agent:example.test:a",
                "serial": "0x1",
                "reason": "key_compromise",
                "revoked_at": "2026-01-03T00:00:00Z",
                "signature": "ed25519:sig",
            },
        )
    )

    async with NipCaClient(BASE_URL) as client:
        frame = await client.revoke_agent("urn:nps:agent:example.test:a", reason="key_compromise")

    assert frame.target_nid == "urn:nps:agent:example.test:a"
    assert frame.reason == "key_compromise"
    assert json.loads(route.calls.last.request.content)["reason"] == "key_compromise"
