# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""SQL-backed NIP CA store tests (sqlite round-trip + INipCaStore conformance)."""

from __future__ import annotations

import datetime

import pytest

from nps_sdk.nip.ca.store import INipCaStore, NipCaCertRecord
from nps_sdk.nip.ca.sql_store import SqliteNipCaStore


def _record(nid="nid:agent:abc", serial="0x1", **kw) -> NipCaCertRecord:
    now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    base = dict(
        nid=nid,
        entity_type="agent",
        serial=serial,
        pub_key="PUBKEY",
        capabilities=("read", "write"),
        scope_json='{"org":"acme"}',
        issued_by="nid:ca:root",
        issued_at=now,
        expires_at=now + datetime.timedelta(days=365),
    )
    base.update(kw)
    return NipCaCertRecord(**base)


@pytest.fixture()
async def store():
    s = await SqliteNipCaStore.open(":memory:")
    yield s


@pytest.mark.asyncio
async def test_store_satisfies_protocol():
    s = await SqliteNipCaStore.open(":memory:")
    assert isinstance(s, INipCaStore)


@pytest.mark.asyncio
async def test_save_and_get_by_nid_round_trip(store):
    rec = _record()
    await store.save(rec)
    got = await store.get_by_nid(rec.nid)
    assert got is not None
    assert got.nid == rec.nid
    assert got.capabilities == ("read", "write")
    assert got.scope_json == '{"org":"acme"}'
    assert got.issued_at == rec.issued_at
    assert got.expires_at == rec.expires_at
    assert got.revoked_at is None


@pytest.mark.asyncio
async def test_get_by_serial(store):
    await store.save(_record(serial="0xABC"))
    got = await store.get_by_serial("0xABC")
    assert got is not None and got.serial == "0xABC"
    assert await store.get_by_serial("0xZZZ") is None


@pytest.mark.asyncio
async def test_next_serial_is_atomic_and_hex(store):
    s1 = await store.next_serial()
    s2 = await store.next_serial()
    assert s1 == "0x1"
    assert s2 == "0x2"


@pytest.mark.asyncio
async def test_duplicate_serial_rejected(store):
    await store.save(_record(serial="0x5"))
    with pytest.raises(Exception):
        await store.save(_record(nid="nid:agent:other", serial="0x5"))


@pytest.mark.asyncio
async def test_revoke_and_get_revoked(store):
    rec = _record()
    await store.save(rec)
    revoked_at = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)
    ok = await store.revoke(rec.nid, "compromised", revoked_at)
    assert ok is True
    revoked = await store.get_revoked()
    assert len(revoked) == 1
    assert revoked[0].revoke_reason == "compromised"
    assert revoked[0].revoked_at == revoked_at
    # Revoking again returns False (already revoked).
    assert await store.revoke(rec.nid, "again", revoked_at) is False


@pytest.mark.asyncio
async def test_revoke_unknown_nid_returns_false(store):
    assert await store.revoke("nid:agent:ghost", "x", datetime.datetime.now(
        datetime.timezone.utc)) is False


@pytest.mark.asyncio
async def test_list_returns_all(store):
    await store.save(_record(nid="nid:a", serial="0x1"))
    await store.save(_record(nid="nid:b", serial="0x2"))
    rows = await store.list()
    assert {r.nid for r in rows} == {"nid:a", "nid:b"}


@pytest.mark.asyncio
async def test_get_by_parent_nid_lineage(store):
    group = _record(nid="nid:group:g", serial="0x1", nid_role="group")
    session = _record(nid="nid:session:s", serial="0x2",
                      nid_role="session", parent_nid="nid:group:g",
                      lineage_json='{"role":"session"}')
    await store.save(group)
    await store.save(session)
    children = await store.get_by_parent_nid("nid:group:g")
    assert len(children) == 1
    assert children[0].nid == "nid:session:s"
    assert children[0].lineage_json == '{"role":"session"}'
