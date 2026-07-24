# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Transport-neutral health / readiness probes (port of ``HealthProbeRenderer`` +
``HealthEndpoints``).

Renders liveness (``/healthz``) and readiness (``/readyz``) responses as a
:class:`HealthProbeResponse` — a status code, content type, and JSON body —
which any HTTP/ASGI/WSGI host can write directly. JSON field names and status
values (``{"status":"ok"}`` / ``{"status":"error","reason":...}``) and the
200 / 503 status codes match the .NET reference exactly.
"""

from __future__ import annotations

import abc
import dataclasses
import json
from typing import Awaitable, Callable, Iterable

JSON_CONTENT_TYPE = "application/json; charset=utf-8"


@dataclasses.dataclass(frozen=True)
class HealthProbeResponse:
    status_code: int
    content_type: str
    body: str
    status: str
    reason: str | None = None


class IReadinessProbe(abc.ABC):
    """One readiness dependency. ``check`` returns None on success, or a short
    reason string on failure."""

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @abc.abstractmethod
    async def check(self) -> str | None: ...


class DelegateReadinessProbe(IReadinessProbe):
    """Wraps a callable so callers can hand a lambda instead of a class."""

    def __init__(self, name: str,
                 check: Callable[[], Awaitable[str | None]]) -> None:
        self._name = name
        self._check = check

    @property
    def name(self) -> str:
        return self._name

    async def check(self) -> str | None:
        return await self._check()


def _ok() -> HealthProbeResponse:
    body = json.dumps({"status": "ok"}, separators=(",", ":"))
    return HealthProbeResponse(200, JSON_CONTENT_TYPE, body, "ok")


def _error(reason: str) -> HealthProbeResponse:
    body = json.dumps({"status": "error", "reason": reason}, separators=(",", ":"))
    return HealthProbeResponse(503, JSON_CONTENT_TYPE, body, "error", reason)


def render_healthz() -> HealthProbeResponse:
    """Liveness — always 200 ``{"status":"ok"}``."""
    return _ok()


async def render_readyz(probes: Iterable[IReadinessProbe]) -> HealthProbeResponse:
    """Readiness — runs every probe; returns 503 with the first failure reason.
    With no probes, behaves like :func:`render_healthz`."""
    for probe in probes:
        try:
            reason = await probe.check()
        except Exception as exc:  # noqa: BLE001 — surface any probe error as a reason.
            reason = f"{probe.name}: {exc}"
        if reason is not None:
            return _error(reason)
    return _ok()
