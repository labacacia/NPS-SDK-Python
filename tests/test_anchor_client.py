# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for AnchorNodeClient using respx to mock httpx."""

import json
import pytest
import httpx
import respx

from nps_sdk.nwp.anchor_client import (
    AnchorNodeClient,
    AnchorTopologyException,
    AnchorState,
    MemberJoined,
    MemberLeft,
    MemberUpdated,
    ResyncRequired,
    TopologyFilter,
    TopologySnapshot,
)


BASE_URL = "http://anchor.example.com"

# ── Helpers ───────────────────────────────────────────────────────────────────


def _ndjson(*dicts) -> bytes:
    """Encode a sequence of dicts as NDJSON bytes."""
    return b"".join(json.dumps(d).encode() + b"\n" for d in dicts)


def _ack() -> dict:
    return {"type": "topology.stream", "action": "subscribed"}


_SNAPSHOT_DATA = {
    "version": 3,
    "anchor_nid": "urn:nps:anchor:a1",
    "cluster_size": 2,
    "members": [
        {
            "nid": "urn:nps:agent:m1",
            "node_roles": ["memory"],
            "activation_mode": "active",
        }
    ],
}

_MEMBER_JOINED_EVENT = {
    "event_type": "member_joined",
    "seq": 1,
    "payload": {
        "nid": "urn:nps:agent:m1",
        "node_roles": ["memory"],
        "activation_mode": "active",
    },
}

_MEMBER_LEFT_EVENT = {
    "event_type": "member_left",
    "seq": 2,
    "payload": {"nid": "urn:nps:agent:m2"},
}

_MEMBER_UPDATED_EVENT = {
    "event_type": "member_updated",
    "seq": 3,
    "payload": {
        "nid": "urn:nps:agent:m1",
        "changes": {"node_roles": ["memory", "compute"]},
    },
}

_ANCHOR_STATE_EVENT = {
    "event_type": "anchor_state",
    "seq": 4,
    "payload": {"field": "quorum_status", "details": {"quorum": True}},
}

_RESYNC_REQUIRED_EVENT = {
    "event_type": "resync_required",
    "seq": 5,
    "payload": {"reason": "snapshot_expired"},
}


# ── get_snapshot ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_get_snapshot_success():
    """get_snapshot returns a fully populated TopologySnapshot on 200."""
    respx.post(f"{BASE_URL}/query").mock(
        return_value=httpx.Response(
            200,
            json={"data": [_SNAPSHOT_DATA]},
        )
    )

    async with AnchorNodeClient(BASE_URL) as client:
        snap = await client.get_snapshot()

    assert isinstance(snap, TopologySnapshot)
    assert snap.version == 3
    assert snap.anchor_nid == "urn:nps:anchor:a1"
    assert snap.cluster_size == 2
    assert len(snap.members) == 1
    assert snap.members[0].nid == "urn:nps:agent:m1"
    assert snap.members[0].node_roles == ["memory"]
    assert snap.members[0].activation_mode == "active"


@pytest.mark.anyio
@respx.mock
async def test_get_snapshot_request_body_defaults():
    """get_snapshot sends correct default type, scope, include, and depth fields."""
    route = respx.post(f"{BASE_URL}/query").mock(
        return_value=httpx.Response(200, json={"data": [_SNAPSHOT_DATA]})
    )

    async with AnchorNodeClient(BASE_URL) as client:
        await client.get_snapshot()

    body = json.loads(route.calls[0].request.content)
    assert body["type"] == "topology.snapshot"
    assert body["topology"]["scope"] == "cluster"
    assert body["topology"]["include"] == ["members"]
    assert body["topology"]["depth"] == 1


@pytest.mark.anyio
@respx.mock
async def test_get_snapshot_member_scope_and_target_nid():
    """scope='member' and target_nid are forwarded in the wire body."""
    route = respx.post(f"{BASE_URL}/query").mock(
        return_value=httpx.Response(200, json={"data": [_SNAPSHOT_DATA]})
    )

    async with AnchorNodeClient(BASE_URL) as client:
        await client.get_snapshot(scope="member", target_nid="urn:nps:agent:m1")

    body = json.loads(route.calls[0].request.content)
    assert body["topology"]["scope"] == "member"
    assert body["topology"]["target_nid"] == "urn:nps:agent:m1"


@pytest.mark.anyio
@respx.mock
async def test_get_snapshot_depth_forwarded():
    """depth parameter is reflected in the wire body."""
    route = respx.post(f"{BASE_URL}/query").mock(
        return_value=httpx.Response(200, json={"data": [_SNAPSHOT_DATA]})
    )

    async with AnchorNodeClient(BASE_URL) as client:
        await client.get_snapshot(depth=2)

    body = json.loads(route.calls[0].request.content)
    assert body["topology"]["depth"] == 2


@pytest.mark.anyio
@respx.mock
async def test_get_snapshot_non2xx_nps_error_json():
    """Non-2xx with NPS error JSON raises AnchorTopologyException with correct codes."""
    respx.post(f"{BASE_URL}/query").mock(
        return_value=httpx.Response(
            404,
            json={"error": "NWP-TOPOLOGY-NOT-FOUND", "status": "ERR-404", "message": "no anchor"},
        )
    )

    async with AnchorNodeClient(BASE_URL) as client:
        with pytest.raises(AnchorTopologyException) as exc_info:
            await client.get_snapshot()

    exc = exc_info.value
    assert exc.nwp_error_code == "NWP-TOPOLOGY-NOT-FOUND"
    assert exc.nps_status == "ERR-404"
    assert "no anchor" in str(exc)


@pytest.mark.anyio
@respx.mock
async def test_get_snapshot_non2xx_plain_body():
    """Non-2xx with a plain text body produces HTTP-<code> in nps_status."""
    respx.post(f"{BASE_URL}/query").mock(
        return_value=httpx.Response(503, content=b"Service Unavailable")
    )

    async with AnchorNodeClient(BASE_URL) as client:
        with pytest.raises(AnchorTopologyException) as exc_info:
            await client.get_snapshot()

    exc = exc_info.value
    assert exc.nps_status == "HTTP-503"
    assert exc.nwp_error_code == "UNKNOWN"


@pytest.mark.anyio
@respx.mock
async def test_get_snapshot_empty_data_raises():
    """An empty data array in the CapsFrame raises AnchorTopologyException."""
    respx.post(f"{BASE_URL}/query").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    async with AnchorNodeClient(BASE_URL) as client:
        with pytest.raises(AnchorTopologyException):
            await client.get_snapshot()


# ── subscribe ─────────────────────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_subscribe_success_sequence():
    """subscribe yields all four event types in order; ack is silently consumed."""
    ndjson = _ndjson(
        _ack(),
        _MEMBER_JOINED_EVENT,
        _MEMBER_LEFT_EVENT,
        _MEMBER_UPDATED_EVENT,
        _ANCHOR_STATE_EVENT,
    )
    respx.post(f"{BASE_URL}/subscribe").mock(
        return_value=httpx.Response(
            200,
            content=ndjson,
            headers={"content-type": "application/x-ndjson"},
        )
    )

    events = []
    async with AnchorNodeClient(BASE_URL) as client:
        async for event in client.subscribe():
            events.append(event)

    assert len(events) == 4
    assert isinstance(events[0], MemberJoined)
    assert isinstance(events[1], MemberLeft)
    assert isinstance(events[2], MemberUpdated)
    assert isinstance(events[3], AnchorState)


@pytest.mark.anyio
@respx.mock
async def test_subscribe_resync_required_terminates_generator():
    """ResyncRequired is yielded as the final event and the generator stops."""
    ndjson = _ndjson(
        _ack(),
        _MEMBER_JOINED_EVENT,
        _RESYNC_REQUIRED_EVENT,
        # This event should never be reached:
        _MEMBER_LEFT_EVENT,
    )
    respx.post(f"{BASE_URL}/subscribe").mock(
        return_value=httpx.Response(
            200,
            content=ndjson,
            headers={"content-type": "application/x-ndjson"},
        )
    )

    events = []
    async with AnchorNodeClient(BASE_URL) as client:
        async for event in client.subscribe():
            events.append(event)

    assert len(events) == 2
    assert isinstance(events[0], MemberJoined)
    assert isinstance(events[1], ResyncRequired)


@pytest.mark.anyio
@respx.mock
async def test_subscribe_mid_stream_error_raises():
    """A mid-stream error envelope raises AnchorTopologyException."""
    error_envelope = {
        "error": "NWP-TOPOLOGY-ERROR",
        "status": "ERR-STREAM-BROKEN",
        "message": "connection reset by peer",
    }
    ndjson = _ndjson(_ack(), _MEMBER_JOINED_EVENT, error_envelope)
    respx.post(f"{BASE_URL}/subscribe").mock(
        return_value=httpx.Response(
            200,
            content=ndjson,
            headers={"content-type": "application/x-ndjson"},
        )
    )

    async with AnchorNodeClient(BASE_URL) as client:
        with pytest.raises(AnchorTopologyException) as exc_info:
            async for _ in client.subscribe():
                pass

    exc = exc_info.value
    assert exc.nwp_error_code == "NWP-TOPOLOGY-ERROR"
    assert exc.nps_status == "ERR-STREAM-BROKEN"


@pytest.mark.anyio
@respx.mock
async def test_subscribe_filter_in_wire_body():
    """TopologyFilter fields are forwarded in the topology.filter wire body."""
    ndjson = _ndjson(_ack())
    route = respx.post(f"{BASE_URL}/subscribe").mock(
        return_value=httpx.Response(
            200,
            content=ndjson,
            headers={"content-type": "application/x-ndjson"},
        )
    )

    filt = TopologyFilter(node_roles=["memory"], tags_any=["gpu"])
    async with AnchorNodeClient(BASE_URL) as client:
        async for _ in client.subscribe(filter=filt):
            pass

    body = json.loads(route.calls[0].request.content)
    assert body["topology"]["filter"]["node_roles"] == ["memory"]
    assert body["topology"]["filter"]["tags_any"] == ["gpu"]


@pytest.mark.anyio
@respx.mock
async def test_subscribe_since_version_in_wire_body():
    """since_version is forwarded in the topology wire body."""
    ndjson = _ndjson(_ack())
    route = respx.post(f"{BASE_URL}/subscribe").mock(
        return_value=httpx.Response(
            200,
            content=ndjson,
            headers={"content-type": "application/x-ndjson"},
        )
    )

    async with AnchorNodeClient(BASE_URL) as client:
        async for _ in client.subscribe(since_version=42):
            pass

    body = json.loads(route.calls[0].request.content)
    assert body["topology"]["since_version"] == 42


@pytest.mark.anyio
@respx.mock
async def test_subscribe_non2xx_raises():
    """Non-2xx HTTP response from /subscribe raises AnchorTopologyException."""
    respx.post(f"{BASE_URL}/subscribe").mock(
        return_value=httpx.Response(
            503,
            content=json.dumps(
                {"error": "NWP-UNAVAILABLE", "status": "ERR-503", "message": "overloaded"}
            ).encode(),
        )
    )

    async with AnchorNodeClient(BASE_URL) as client:
        with pytest.raises(AnchorTopologyException) as exc_info:
            async for _ in client.subscribe():
                pass

    exc = exc_info.value
    assert exc.nwp_error_code == "NWP-UNAVAILABLE"
    assert exc.nps_status == "ERR-503"


# ── URL normalisation ─────────────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_url_trailing_slash_stripped():
    """base_url trailing slash is stripped; /query is appended correctly."""
    route = respx.post(f"{BASE_URL}/query").mock(
        return_value=httpx.Response(200, json={"data": [_SNAPSHOT_DATA]})
    )

    async with AnchorNodeClient(f"{BASE_URL}/") as client:
        await client.get_snapshot()

    assert route.called


@pytest.mark.anyio
@respx.mock
async def test_path_prefix_prepended_to_query_and_subscribe():
    """path_prefix is prepended to both /query and /subscribe endpoints."""
    prefix = "/v2/topology"
    query_route = respx.post(f"{BASE_URL}{prefix}/query").mock(
        return_value=httpx.Response(200, json={"data": [_SNAPSHOT_DATA]})
    )
    subscribe_route = respx.post(f"{BASE_URL}{prefix}/subscribe").mock(
        return_value=httpx.Response(
            200,
            content=_ndjson(_ack()),
            headers={"content-type": "application/x-ndjson"},
        )
    )

    async with AnchorNodeClient(BASE_URL, path_prefix=prefix) as client:
        await client.get_snapshot()
        async for _ in client.subscribe():
            pass

    assert query_route.called
    assert subscribe_route.called


@pytest.mark.anyio
@respx.mock
async def test_path_prefix_trailing_slash_stripped():
    """path_prefix trailing slash is stripped before concatenation."""
    prefix = "/v2/topology"
    route = respx.post(f"{BASE_URL}{prefix}/query").mock(
        return_value=httpx.Response(200, json={"data": [_SNAPSHOT_DATA]})
    )

    async with AnchorNodeClient(BASE_URL, path_prefix=f"{prefix}/") as client:
        await client.get_snapshot()

    assert route.called


# ── Context manager / lifecycle ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_context_manager_closes_owned_client():
    """AnchorNodeClient closes its internally-created httpx client on __aexit__."""
    client = AnchorNodeClient(BASE_URL)
    assert not client._http.is_closed
    async with client:
        pass
    assert client._http.is_closed


@pytest.mark.anyio
async def test_external_http_client_not_closed():
    """AnchorNodeClient does NOT close an externally-supplied httpx.AsyncClient."""
    http = httpx.AsyncClient()
    async with AnchorNodeClient(BASE_URL, http_client=http):
        pass
    assert not http.is_closed
    await http.aclose()


# ── AnchorTopologyException ───────────────────────────────────────────────────


def test_anchor_topology_exception_attributes():
    """AnchorTopologyException stores nwp_error_code and nps_status correctly."""
    exc = AnchorTopologyException("NWP-FOO", "ERR-BAR", "something went wrong")
    assert exc.nwp_error_code == "NWP-FOO"
    assert exc.nps_status == "ERR-BAR"
    assert str(exc) == "something went wrong"


def test_anchor_topology_exception_no_message():
    """AnchorTopologyException can be constructed without a message."""
    exc = AnchorTopologyException("NWP-UNKNOWN", "HTTP-500")
    assert exc.nwp_error_code == "NWP-UNKNOWN"
    assert exc.nps_status == "HTTP-500"


# ── Event payload detail tests ────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_member_updated_payload_fields():
    """MemberUpdated event populates nid and changes.node_roles correctly."""
    ndjson = _ndjson(_ack(), _MEMBER_UPDATED_EVENT)
    respx.post(f"{BASE_URL}/subscribe").mock(
        return_value=httpx.Response(
            200,
            content=ndjson,
            headers={"content-type": "application/x-ndjson"},
        )
    )

    events = []
    async with AnchorNodeClient(BASE_URL) as client:
        async for event in client.subscribe():
            events.append(event)

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, MemberUpdated)
    assert ev.nid == "urn:nps:agent:m1"
    assert ev.changes.node_roles == ["memory", "compute"]
    assert ev.version == 3  # seq from the event


@pytest.mark.anyio
@respx.mock
async def test_anchor_state_payload_fields():
    """AnchorState event populates field and details correctly."""
    ndjson = _ndjson(_ack(), _ANCHOR_STATE_EVENT)
    respx.post(f"{BASE_URL}/subscribe").mock(
        return_value=httpx.Response(
            200,
            content=ndjson,
            headers={"content-type": "application/x-ndjson"},
        )
    )

    events = []
    async with AnchorNodeClient(BASE_URL) as client:
        async for event in client.subscribe():
            events.append(event)

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, AnchorState)
    assert ev.field == "quorum_status"
    assert ev.details == {"quorum": True}
    assert ev.version == 4  # seq from the event


# ── Edge cases: unknown event types / blank lines ─────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_unknown_event_type_silently_skipped():
    """Unknown event_type values are silently skipped; valid events follow through."""
    unknown_event = {"event_type": "totally_unknown_event", "seq": 9, "payload": {}}
    ndjson = _ndjson(_ack(), unknown_event, _MEMBER_JOINED_EVENT)
    respx.post(f"{BASE_URL}/subscribe").mock(
        return_value=httpx.Response(
            200,
            content=ndjson,
            headers={"content-type": "application/x-ndjson"},
        )
    )

    events = []
    async with AnchorNodeClient(BASE_URL) as client:
        async for event in client.subscribe():
            events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], MemberJoined)


@pytest.mark.anyio
@respx.mock
async def test_empty_and_whitespace_ndjson_lines_skipped():
    """Empty lines and whitespace-only lines in the NDJSON stream are skipped."""
    # Build raw bytes with blank lines interspersed
    raw = (
        json.dumps(_ack()).encode() + b"\n"
        + b"\n"
        + b"   \n"
        + json.dumps(_MEMBER_JOINED_EVENT).encode() + b"\n"
        + b"\n"
        + json.dumps(_MEMBER_LEFT_EVENT).encode() + b"\n"
    )
    respx.post(f"{BASE_URL}/subscribe").mock(
        return_value=httpx.Response(
            200,
            content=raw,
            headers={"content-type": "application/x-ndjson"},
        )
    )

    events = []
    async with AnchorNodeClient(BASE_URL) as client:
        async for event in client.subscribe():
            events.append(event)

    assert len(events) == 2
    assert isinstance(events[0], MemberJoined)
    assert isinstance(events[1], MemberLeft)


@pytest.mark.anyio
@respx.mock
async def test_subscribe_wire_body_type_and_action():
    """subscribe posts the correct type and action values in the wire body."""
    ndjson = _ndjson(_ack())
    route = respx.post(f"{BASE_URL}/subscribe").mock(
        return_value=httpx.Response(
            200,
            content=ndjson,
            headers={"content-type": "application/x-ndjson"},
        )
    )

    async with AnchorNodeClient(BASE_URL) as client:
        async for _ in client.subscribe():
            pass

    body = json.loads(route.calls[0].request.content)
    assert body["type"] == "topology.stream"
    assert body["action"] == "subscribe"
    assert "stream_id" in body
    assert len(body["stream_id"]) > 0
    assert body["topology"]["scope"] == "cluster"
