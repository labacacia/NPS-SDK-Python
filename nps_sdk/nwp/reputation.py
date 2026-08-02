# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""RFC-0005 reputation policy types and default in-process evaluator."""

from __future__ import annotations

import abc
import asyncio
import dataclasses
import datetime
import threading
import time
from enum import Enum
from typing import Any
from urllib.parse import urlencode

# ── Policy types ──────────────────────────────────────────────────────────────

@dataclasses.dataclass
class ReputationRule:
    """A single ban_on / reject_on / throttle_on rule (NPS-RFC-0005 §4.1.3)."""
    incident:    str          = "*"
    severity:    str          = ">=minor"
    within_days: int | None   = None
    count:       int          = 1


@dataclasses.dataclass
class ReputationPolicy:
    """Node-side reputation enforcement policy (NPS-RFC-0005 §4.1)."""
    enabled:             bool             = True
    log_sources:         list[str]        = dataclasses.field(default_factory=list)
    min_assurance_level: str              = "anonymous"
    cache_ttl_seconds:   int              = 300
    ban_ttl_seconds:     int              = 3600
    on_log_unavailable:  str              = "allow"
    throttle_on:         list[ReputationRule] = dataclasses.field(default_factory=list)
    reject_on:           list[ReputationRule] = dataclasses.field(default_factory=list)
    ban_on:              list[ReputationRule] = dataclasses.field(default_factory=list)


class RepOutcome(Enum):
    ACCEPT   = "accept"
    THROTTLE = "throttle"
    REJECT   = "reject"
    BAN      = "ban"


@dataclasses.dataclass
class ReputationDecision:
    outcome:      RepOutcome
    matched_rule: ReputationRule | None = None
    error_code:   str | None            = None


# ── Severity / assurance ordering ─────────────────────────────────────────────

_SEV = ["info", "minor", "moderate", "major", "critical"]
_ASS = ["anonymous", "attested", "verified"]

def _sev_idx(s: str) -> int:
    try:
        return _SEV.index(s.lower())
    except ValueError:
        return -1

def _ass_idx(s: str) -> int:
    try:
        return _ASS.index(s.lower())
    except ValueError:
        return -1


# ── Abstract interface ────────────────────────────────────────────────────────

class IReputationEvaluator(abc.ABC):
    @abc.abstractmethod
    async def evaluate(
        self,
        nid: str,
        assurance_level: str,
        policy: ReputationPolicy,
    ) -> ReputationDecision: ...

    @abc.abstractmethod
    def clear_ban(self, nid: str) -> None: ...


# ── Default evaluator ─────────────────────────────────────────────────────────

class DefaultReputationEvaluator(IReputationEvaluator):
    """In-process evaluator with thread-safe ban cache and async HTTP log query."""

    def __init__(self) -> None:
        self._lock      = threading.Lock()
        self._bans:  dict[str, float] = {}   # nid → expiry epoch
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    async def evaluate(
        self,
        nid: str,
        assurance_level: str,
        policy: ReputationPolicy,
    ) -> ReputationDecision:
        if not policy.enabled:
            return ReputationDecision(RepOutcome.ACCEPT)

        if _ass_idx(policy.min_assurance_level) > _ass_idx(assurance_level):
            return ReputationDecision(RepOutcome.REJECT, error_code="NWP-AUTH-ASSURANCE-TOO-LOW")

        with self._lock:
            exp = self._bans.get(nid)
        if exp is not None and exp > time.monotonic():
            return ReputationDecision(RepOutcome.BAN, error_code="NWP-AUTH-REPUTATION-BLOCKED")

        entries = await self._fetch_entries(nid, policy)

        for rule in policy.ban_on:
            if _rule_matches(rule, entries):
                exp = time.monotonic() + policy.ban_ttl_seconds
                with self._lock:
                    self._bans[nid] = exp
                return ReputationDecision(RepOutcome.BAN, matched_rule=rule,
                                          error_code="NWP-AUTH-REPUTATION-BLOCKED")

        for rule in policy.reject_on:
            if _rule_matches(rule, entries):
                return ReputationDecision(RepOutcome.REJECT, matched_rule=rule,
                                          error_code="NWP-AUTH-REPUTATION-BLOCKED")

        for rule in policy.throttle_on:
            if _rule_matches(rule, entries):
                return ReputationDecision(RepOutcome.THROTTLE, matched_rule=rule)

        return ReputationDecision(RepOutcome.ACCEPT)

    def clear_ban(self, nid: str) -> None:
        with self._lock:
            self._bans.pop(nid, None)

    async def _fetch_entries(
        self, nid: str, policy: ReputationPolicy
    ) -> list[dict[str, Any]]:
        now = time.monotonic()
        if policy.cache_ttl_seconds > 0:
            with self._lock:
                cached = self._cache.get(nid)
            if cached and cached[0] > now:
                return cached[1]

        entries: list[dict[str, Any]] | None = None
        for source in policy.log_sources:
            url = source.rstrip("/") + "/entries?" + urlencode({"subject_nid": nid})
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    import json
                    data = json.loads(resp.read())
                    entries = data.get("entries", [])
                    break
            except Exception:
                continue

        if entries is None:
            if policy.on_log_unavailable.lower() == "deny":
                timestamp = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
                entries = [{"incident": "*", "severity": "critical",
                             "timestamp": timestamp}]
            else:
                entries = []

        if policy.cache_ttl_seconds > 0:
            with self._lock:
                self._cache[nid] = (now + policy.cache_ttl_seconds, entries)

        return entries


# ── Package-level singleton ───────────────────────────────────────────────────

_singleton: DefaultReputationEvaluator | None = None
_singleton_lock = threading.Lock()

def default_reputation_evaluator() -> IReputationEvaluator:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = DefaultReputationEvaluator()
    return _singleton


# ── Rule matching ─────────────────────────────────────────────────────────────

def _rule_matches(rule: ReputationRule, entries: list[dict[str, Any]]) -> bool:
    cutoff: datetime.datetime | None = None
    if rule.within_days is not None:
        cutoff = (
            datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            - datetime.timedelta(days=rule.within_days)
        )

    op, threshold = _parse_sev(rule.severity)
    needed = max(rule.count, 1)
    matched = 0
    for e in entries:
        ts_raw = e.get("timestamp", "")
        if cutoff is not None and ts_raw:
            try:
                ts = datetime.datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                if ts.replace(tzinfo=None) < cutoff:
                    continue
            except ValueError:
                pass
        if not _incident_match(rule.incident, e.get("incident", "")):
            continue
        if not _sev_match(op, threshold, e.get("severity", "")):
            continue
        matched += 1
        if matched >= needed:
            return True
    return False

def _incident_match(pattern: str, incident: str) -> bool:
    return pattern == "*" or pattern.lower() == incident.lower()

def _sev_match(op: str, threshold: int, actual: str) -> bool:
    idx = _sev_idx(actual)
    if idx < 0:
        return False
    return idx >= threshold if op == ">=" else idx == threshold

def _parse_sev(s: str) -> tuple[str, int]:
    if s.startswith(">="):
        return ">=", _sev_idx(s[2:])
    return "=", _sev_idx(s)
