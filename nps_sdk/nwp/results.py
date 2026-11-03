# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Explicit experimental result-profile client for a pinned public HTTPS route.

The host configures actual TLS/mTLS identity. Models, descriptors and routing hints
supply no authority. This public-leg client requires data_results=1; a gateway owns
any independent downstream invocation_context projection. No native/result-stream
support, default advertisement, retries or alternate-route fallback is installed.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import math
import re
import uuid
from typing import Any, Sequence
from urllib.parse import urljoin, urlsplit

import httpx

_REQUIRED = "nps.data_results=1"
_OPERATIONS = {"result.create", "result.read", "result.aggregate", "result.release"}
_DESCRIPTOR_FIELDS = {"result_ref", "issuer_nid", "snapshot_id", "schema_ref", "row_count", "created_at", "expires_at", "retention"}
_MAX_SAFE_INTEGER = 9007199254740991


class ResultHttpError(RuntimeError):
    """Bounded failure without response bodies or credentials.

    outcome_unknown means this attempt may have allocated/released a resource;
    false makes no statement about an earlier attempt. Recover creation only with
    the same logical UUID/parameters and fresh current authorization. Cancellation
    propagates and does not imply rollback. No operation is automatically retried.
    """
    def __init__(self, message: str, *, outcome_unknown: bool, http_status: int | None = None,
                 nps_status: str | None = None, error_code: str | None = None) -> None:
        super().__init__(message)
        self.outcome_unknown = outcome_unknown
        self.http_status = http_status
        self.nps_status = nps_status
        self.error_code = error_code


@dataclasses.dataclass(frozen=True)
class ResultDescriptor:
    """Immutable issuer/snapshot/schema fence, not a bearer credential.

    Retention is process-local. Endpoint replacement/restart does not imply state
    migration; ownership/source policy is checked by the serving host on every use.
    """
    result_ref: str
    issuer_nid: str
    snapshot_id: str
    schema_ref: str
    row_count: int
    created_at: int
    expires_at: int
    retention: str


@dataclasses.dataclass(frozen=True)
class ResultPage:
    """One explicitly bounded inspected page; no automatic pagination."""
    descriptor: ResultDescriptor
    rows: tuple[dict[str, Any], ...]
    next_cursor: str | None


@dataclasses.dataclass(frozen=True)
class ResultAggregate:
    """Exact COUNT/SUM groups bound to the captured descriptor."""
    descriptor: ResultDescriptor
    groups: tuple[dict[str, Any], ...]


def _text(value: Any, maximum: int) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        return len(value.encode('utf-8', errors='strict')) <= maximum
    except UnicodeError:
        return False


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _protocol(status: int | None = None) -> ResultHttpError:
    return ResultHttpError("Invalid result protocol response; outcome may be unknown.", outcome_unknown=True, http_status=status)


def _fields(values: Sequence[str]) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("A field sequence is required.")
    result = list(values)
    if not 1 <= len(result) <= 256 or any(not _text(value, 128) for value in result) or len(set(result)) != len(result):
        raise ValueError("Invalid result fields.")
    return result


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise ValueError("Duplicate JSON member.")
        result[key] = value
    return result


def _validate_json(value: Any, *, integer_only: bool, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError("JSON depth exceeds bound.")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings.")
            key.encode('utf-8', errors='strict')
            _validate_json(child, integer_only=integer_only, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_json(child, integer_only=integer_only, depth=depth + 1)
    elif isinstance(value, str):
        value.encode('utf-8', errors='strict')
    elif value is None or type(value) is bool:
        pass
    elif type(value) is int:
        if integer_only and abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError("Integer exceeds profile range.")
    elif type(value) is float and not integer_only and math.isfinite(value):
        pass
    else:
        raise ValueError("Unsupported profile JSON value.")


def _payload_size(raw: bytes) -> int:
    # Preserve the exact payload span rather than normalizing away whitespace
    # before enforcing the profile's 64KiB read/aggregate response bound.
    text = raw.decode('utf-8')
    decoder = json.JSONDecoder()
    def whitespace(position: int) -> int:
        while position < len(text) and text[position] in ' \t\r\n':
            position += 1
        return position
    position = whitespace(0) + 1
    while True:
        name, position = decoder.raw_decode(text, whitespace(position))
        position = whitespace(position)
        if text[position] != ':':
            raise _protocol()
        position = whitespace(position + 1)
        if name == 'data':
            start = whitespace(position + 1)
            _, end = decoder.raw_decode(text, start)
            return len(text[start:end].encode('utf-8'))
        _, position = decoder.raw_decode(text, position)
        position = whitespace(position)
        if text[position] != ',':
            raise _protocol()
        position += 1


class ResultHttpClient:
    """Opt-in public result route with affirmative discovery before every POST.

    base_url and expected_issuer are host-owned pins. Configure HTTPS/mTLS through
    the supplied httpx client; redirects are always disabled per request. A supplied
    client remains caller-owned. Closing this client does not release server results.
    Discovery, exact echo, schema/snapshot binding and decompressed response bounds
    fail closed. No model-supplied tenant, key, issuer or target is accepted.
    """
    def __init__(self, base_url: str, expected_issuer: str, *, http_client: httpx.AsyncClient | None = None) -> None:
        route = urlsplit(base_url)
        if route.scheme != 'https' or not route.hostname or route.username is not None or route.password is not None or route.query or route.fragment:
            raise ValueError("A pinned HTTPS node route is required.")
        if not _text(expected_issuer, 512):
            raise ValueError("Expected issuer is required.")
        _ = route.port  # Reject an invalid/out-of-range port before any transport use.
        self._base = str(httpx.URL(base_url)).rstrip('/')
        self._manifest = self._base + '/.nwm'
        self._invoke = self._base + '/invoke'
        self._issuer = expected_issuer
        self._http = http_client or httpx.AsyncClient(timeout=120, follow_redirects=False, trust_env=False)
        self._owns_http = http_client is None
        self._manifest_version = 0
        self._manifest_hash: str | None = None

    async def __aenter__(self) -> ResultHttpClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close owned transport only; callers explicitly release known resources."""
        if self._owns_http:
            await self._http.aclose()

    async def create(self, source_id: str, idempotency_key: uuid.UUID, *, query: dict[str, Any] | None = None,
                     ttl_seconds: int | None = None) -> ResultDescriptor:
        """Capture one authorized immutable source without default preview rows.

        Keep the same UUID-v4 and parameters for explicit recovery after an uncertain
        outcome. A new key can allocate another result. Query accepts only the
        advertised source filter/fields/order subset; no URL, SQL or code is accepted.
        """
        if not _text(source_id, 128) or not isinstance(idempotency_key, uuid.UUID) or idempotency_key.version != 4:
            raise ValueError("A source and UUID-v4 logical key are required.")
        if ttl_seconds is not None and not _integer(ttl_seconds, 1, 900):
            raise ValueError("Invalid result TTL.")
        parameters: dict[str, Any] = {"source_id": source_id}
        if ttl_seconds is not None:
            parameters['ttl_seconds'] = ttl_seconds
        if query is not None:
            if type(query) is not dict or any(key not in {'filter', 'fields', 'order'} for key in query):
                raise ValueError("Unsupported result query.")
            parameters.update(query)
        value = await self._invoke_action('result.create', parameters, str(idempotency_key))
        return self._descriptor(value)

    async def read(self, descriptor: ResultDescriptor, limit: int, *, fields: Sequence[str] | None = None,
                   cursor: str | None = None) -> ResultPage:
        """Read one page from this exact descriptor; a reference never grants access."""
        self._check_descriptor(descriptor)
        if not _integer(limit, 1, 1000) or cursor is not None and not _text(cursor, 4096):
            raise ValueError("Invalid page bounds.")
        parameters: dict[str, Any] = dict(result_ref=descriptor.result_ref, snapshot_id=descriptor.snapshot_id,
                                         schema_ref=descriptor.schema_ref, limit=limit)
        if fields is not None:
            parameters['fields'] = _fields(fields)
        if cursor is not None:
            parameters['cursor'] = cursor
        value = await self._invoke_action('result.read', parameters)
        if type(value) is not dict or set(value) != {'descriptor', 'rows', 'next_cursor'}:
            raise _protocol()
        actual = self._descriptor(value['descriptor'])
        rows = value['rows']
        if actual != descriptor or type(rows) is not list or len(rows) > limit or any(type(row) is not dict for row in rows):
            raise _protocol()
        if value['next_cursor'] is not None and not _text(value['next_cursor'], 4096):
            raise _protocol()
        if len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()) > 65536:
            raise _protocol()
        return ResultPage(actual, tuple(rows), value['next_cursor'])

    async def aggregate(self, descriptor: ResultDescriptor, group_by: Sequence[str], *, sum_field: str | None = None) -> ResultAggregate:
        """Compute the reference COUNT and optional exact integer SUM subset."""
        self._check_descriptor(descriptor)
        if isinstance(group_by, (str, bytes)):
            raise ValueError("A grouping sequence is required.")
        groups = list(group_by)
        if groups:
            groups = _fields(groups)
        if len(groups) > 4 or any(field in {'count', 'sum'} for field in groups) or sum_field is not None and not _text(sum_field, 128):
            raise ValueError("Invalid aggregation.")
        operations: list[dict[str, Any]] = [dict(func='COUNT', alias='count')]
        if sum_field is not None:
            operations.append(dict(func='SUM', field=sum_field, alias='sum'))
        value = await self._invoke_action('result.aggregate', dict(result_ref=descriptor.result_ref,
            snapshot_id=descriptor.snapshot_id, schema_ref=descriptor.schema_ref,
            aggregate=dict(group_by=groups, operations=operations)))
        if type(value) is not dict or set(value) != {'descriptor', 'groups'}:
            raise _protocol()
        actual = self._descriptor(value['descriptor'])
        result = value['groups']
        if actual != descriptor or type(result) is not list or len(result) > 1000 or any(type(row) is not dict for row in result):
            raise _protocol()
        if len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()) > 65536:
            raise _protocol()
        return ResultAggregate(actual, tuple(result))

    async def release(self, descriptor: ResultDescriptor) -> None:
        """Owner-authorized idempotent release; an uncertain attempt is never retried."""
        self._check_descriptor(descriptor)
        value = await self._invoke_action('result.release', dict(result_ref=descriptor.result_ref))
        if type(value) is not dict or set(value) != {'result_ref', 'released'} or value['result_ref'] != descriptor.result_ref or value['released'] is not True:
            raise _protocol()

    def _descriptor(self, value: Any) -> ResultDescriptor:
        if type(value) is not dict or set(value) != _DESCRIPTOR_FIELDS:
            raise _protocol()
        descriptor = ResultDescriptor(**value)
        self._check_descriptor(descriptor)
        return descriptor

    def _check_descriptor(self, value: ResultDescriptor) -> None:
        if not isinstance(value, ResultDescriptor) or value.issuer_nid != self._issuer or not _text(value.result_ref, 512) or not _text(value.snapshot_id, 512):
            raise _protocol()
        if not isinstance(value.schema_ref, str) or re.fullmatch(r'sha256:[0-9a-f]{64}', value.schema_ref) is None:
            raise _protocol()
        if not _integer(value.row_count, 0, 100000) or not _integer(value.created_at, 0, _MAX_SAFE_INTEGER) or not _integer(value.expires_at, 0, _MAX_SAFE_INTEGER):
            raise _protocol()
        if value.retention != 'process' or not 0 < value.expires_at - value.created_at <= 900:
            raise _protocol()

    async def _invoke_action(self, operation: str, parameters: dict[str, Any], key: str | None = None) -> Any:
        if operation not in _OPERATIONS:
            raise ValueError("Unsupported result operation.")
        frame = dict(action_id=operation, params=parameters)
        if key is not None:
            frame['idempotency_key'] = key
        _validate_json(frame, integer_only=True)
        raw = json.dumps(frame, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > 65536:
            raise ValueError("Result request exceeds its bound.")
        await self._discover(operation)
        try:
            async with self._http.stream('POST', self._invoke, content=raw,
                headers={'Content-Type': 'application/nwp-frame', 'Accept': 'application/nwp-capsule',
                         'X-NPS-Required-Features': _REQUIRED}, follow_redirects=False) as response:
                if str(response.url) != self._invoke:
                    raise _protocol(response.status_code)
                if not response.is_success:
                    if response.headers.get_list('X-NPS-Selected-Features') or response.headers.get('Content-Type', '').split(';')[0] != 'application/nwp-error+json':
                        raise _protocol(response.status_code)
                    error, _ = await self._body(response, 131072, True, integer_only=True)
                    if type(error) is not dict or not _text(error.get('status'), 128) or not _text(error.get('error'), 128):
                        raise _protocol(response.status_code)
                    raise ResultHttpError("NWP result request rejected.", outcome_unknown=response.status_code >= 500,
                        http_status=response.status_code, nps_status=error['status'], error_code=error['error'])
                if response.headers.get('Content-Type', '').split(';')[0] != 'application/nwp-capsule' or response.headers.get_list('X-NPS-Selected-Features') != [_REQUIRED]:
                    raise _protocol(response.status_code)
                capsule, raw_body = await self._body(response, 131072, True, integer_only=True)
                if type(capsule) is not dict or not _integer(capsule.get('count'), 1, 1) or type(capsule.get('data')) is not list or len(capsule['data']) != 1:
                    raise _protocol(response.status_code)
                if operation in {'result.read', 'result.aggregate'} and _payload_size(raw_body) > 65536:
                    raise _protocol(response.status_code)
                return capsule['data'][0]
        except httpx.HTTPError:
            raise ResultHttpError("Result transport failed; outcome may be unknown.", outcome_unknown=True) from None

    async def _discover(self, operation: str) -> None:
        try:
            async with self._http.stream('GET', self._manifest, headers={'Accept': 'application/nwp-manifest+json'}, follow_redirects=False) as response:
                if response.status_code != 200 or str(response.url) != self._manifest or response.headers.get('Content-Type', '').split(';')[0] != 'application/nwp-manifest+json':
                    raise ValueError("No affirmative manifest.")
                value, raw_body = await self._body(response, 65536, False, integer_only=False)
                if type(value) is not dict or value.get('node_id') != self._issuer or value.get('node_type') not in {'action', 'complex', 'bridge'}:
                    raise ValueError("Wrong issuer/role.")
                version = value.get('manifest_version')
                if not _integer(version, 1, 4294967295) or response.headers.get_list('X-NWM-Version') != [str(version)]:
                    raise ValueError("Invalid manifest version.")
                auth = value.get('auth', {})
                if type(auth) is not dict or auth.get('required') is not True or auth.get('identity_type') not in {'nip-cert', 'bearer'}:
                    raise ValueError("No authenticated capability requirement.")
                capabilities = auth.get('required_capabilities')
                if type(capabilities) is not list or any(type(item) is not str for item in capabilities) or 'nwp:action' not in capabilities:
                    raise ValueError("No exact action capability.")
                if 'manifest_updated_at' in value:
                    timestamp = value['manifest_updated_at']
                    if not _text(timestamp, 64):
                        raise ValueError("Invalid manifest timestamp.")
                    datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                action = value['actions'][operation]
                if type(action) is not dict or action.get('required_capability') != 'nwp:action' or urljoin(self._manifest, value['endpoints']['invoke']) != self._invoke:
                    raise ValueError("Unsupported pinned action route.")
                profile = value['profiles']['feature_negotiation']
                if type(profile) is not dict or set(profile) != {'version', 'feature_offer'} or type(profile['version']) is not int or profile['version'] != 1:
                    raise ValueError("Unsupported feature negotiation.")
                if len(json.dumps(profile, ensure_ascii=False).encode()) > 16384 or not self._result_offer(profile['feature_offer']):
                    raise ValueError("Result revision unavailable.")
                digest = hashlib.sha256(raw_body.strip(b' \t\r\n')).hexdigest()
                if version < self._manifest_version or version == self._manifest_version and digest != self._manifest_hash:
                    raise ValueError("Manifest rollback/content drift.")
                self._manifest_version, self._manifest_hash = version, digest
        except (ResultHttpError, httpx.HTTPError, KeyError, TypeError, ValueError):
            raise ResultHttpError("Affirmative authenticated result-route support unavailable; no operation was sent.", outcome_unknown=False) from None

    @staticmethod
    def _result_offer(offer: Any) -> bool:
        if type(offer) is not list or not 1 <= len(offer) <= 32:
            return False
        ids: set[str] = set()
        found = False
        for feature in offer:
            if type(feature) is not dict or set(feature) != {'id', 'revisions'} or not _text(feature['id'], 96) or re.fullmatch(r'[a-z][a-z0-9]*(\.[a-z][a-z0-9_-]*)+', feature['id']) is None or feature['id'] in ids:
                return False
            ids.add(feature['id'])
            revisions = feature['revisions']
            if type(revisions) is not list or not 1 <= len(revisions) <= 16:
                return False
            last = 0
            for revision in revisions:
                if type(revision) is not dict or set(revision) != {'revision', 'lifecycle'} or not _integer(revision['revision'], last + 1, 65535) or revision['lifecycle'] not in {'experimental', 'stable', 'deprecated'}:
                    return False
                last = revision['revision']
                found |= feature['id'] == 'nps.data_results' and revision['revision'] == 1 and revision['lifecycle'] in {'experimental', 'stable'}
        return found

    @staticmethod
    async def _body(response: httpx.Response, maximum: int, uncertain: bool, *, integer_only: bool) -> tuple[Any, bytes]:
        try:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > maximum:
                    raise ValueError("Response exceeds bound.")
                data.extend(chunk)
            value = json.loads(data.decode('utf-8', errors='strict'), object_pairs_hook=_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON.")))
            _validate_json(value, integer_only=integer_only)
            return value, bytes(data)
        except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
            raise ResultHttpError("Invalid or incomplete bounded response.", outcome_unknown=uncertain) from None
