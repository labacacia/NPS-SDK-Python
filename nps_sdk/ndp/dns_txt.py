# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
DNS TXT record resolution for NDP node discovery (NPS-4 §5).

Provides helpers to look up ``_nps-node.<hostname>`` TXT records and parse
them into :class:`~nps_sdk.ndp.frames.NdpResolveResult` objects.

TXT record format::

    _nps-node.api.example.com.  IN TXT  "v=nps1 type=memory port=17434 nid=urn:nps:node:api.example.com:products fp=sha256:a3f9..."

Required keys: ``v`` (must be ``nps1``), ``nid``
Optional keys: ``port`` (default 17433), ``type``, ``fp``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    pass

from nps_sdk.ndp.frames import NdpResolveResult

DNS_TXT_DEFAULT_TTL: int = 300
"""Default TTL (seconds) applied to results resolved via DNS TXT records."""

_DEFAULT_PORT: int = 17433


def _extract_host_from_target(target: str) -> str | None:
    """
    Extract the hostname from a ``nwp://`` URL.

    Returns ``None`` if *target* is not a valid ``nwp://`` URL.

    Examples::

        >>> _extract_host_from_target("nwp://api.example.com/products")
        'api.example.com'
        >>> _extract_host_from_target("https://api.example.com/products")  # None
    """
    if not target.startswith("nwp://"):
        return None
    rest = target[len("nwp://"):]
    # Strip path component
    host = rest.split("/", 1)[0]
    # Strip port component if present (e.g. "api.example.com:8080")
    host = host.split(":")[0]
    return host if host else None


def parse_nps_txt_record(txt: str, host: str) -> NdpResolveResult | None:
    """
    Parse a single NPS DNS TXT record string into an :class:`NdpResolveResult`.

    *txt* is the raw TXT record value (space-separated ``key=value`` pairs).
    *host* is the hostname that was queried (used as the ``host`` field of the
    result, since the TXT record itself does not repeat the hostname).

    Validation rules:

    * ``v`` key MUST be present and equal to ``nps1``
    * ``nid`` key MUST be present and non-empty
    * ``port`` defaults to 17433 if absent
    * ``fp`` is mapped to ``cert_fingerprint``; absent → ``None``

    Returns ``None`` if validation fails.
    """
    pairs: dict[str, str] = {}
    for token in txt.split():
        if "=" in token:
            key, _, value = token.partition("=")
            pairs[key.strip()] = value.strip()

    # Validate required fields
    if pairs.get("v") != "nps1":
        return None
    nid = pairs.get("nid", "").strip()
    if not nid:
        return None

    # Optional fields
    raw_port = pairs.get("port", "").strip()
    try:
        port = int(raw_port) if raw_port else _DEFAULT_PORT
    except ValueError:
        return None

    fp = pairs.get("fp") or None

    return NdpResolveResult(
        host=host,
        port=port,
        ttl=DNS_TXT_DEFAULT_TTL,
        cert_fingerprint=fp,
    )


class DnsTxtLookup(Protocol):
    """
    Protocol for DNS TXT record lookup implementations.

    Each call to :meth:`lookup` returns a list of TXT record sets for the
    given hostname. Each record set is itself a list of strings (DNS allows
    a single TXT record to be split across multiple strings; callers should
    join them with a space when necessary).
    """

    async def lookup(self, hostname: str) -> list[list[str]]:
        """
        Resolve TXT records for *hostname*.

        :param hostname: Fully-qualified hostname to query.
        :returns: A list of TXT record values. Each element is a list of
                  strings that together form one TXT record.
        :raises Exception: On DNS resolution failure.
        """
        ...


class SystemDnsTxtLookup:
    """
    DNS TXT lookup implementation backed by the system resolver via
    ``dnspython`` (``dns.asyncresolver``).

    ``dnspython>=2.6`` must be installed. Install it with::

        pip install "nps-lib[dns]"
    """

    async def lookup(self, hostname: str) -> list[list[str]]:
        """
        Perform an asynchronous DNS TXT query for *hostname*.

        :raises RuntimeError: If ``dnspython`` is not installed.
        :raises dns.exception.DNSException: On resolution failure.
        """
        try:
            import dns.asyncresolver  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "dnspython is required for DNS TXT resolution. "
                "Install it with: pip install \"nps-lib[dns]\""
            ) from exc

        answer = await dns.asyncresolver.resolve(hostname, "TXT")
        result: list[list[str]] = []
        for rdata in answer:
            strings = [s.decode("utf-8") if isinstance(s, bytes) else s for s in rdata.strings]
            result.append(strings)
        return result
