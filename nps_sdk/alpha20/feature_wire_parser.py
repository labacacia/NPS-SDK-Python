"""Bounded feature-only parsers, not a negotiated/admitted session or policy engine."""
from dataclasses import dataclass
import json
import re
from types import MappingProxyType
from typing import Mapping, Sequence

NATIVE_INVALID = "NCP-FEATURE-NEGOTIATION-INVALID"
HTTP_INVALID = "NWP-FEATURE-REQUIREMENTS-INVALID"
_ID = re.compile(r"[a-z][a-z0-9]*(\.[a-z][a-z0-9_-]*)+")


class FeatureFormatError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FeatureRevision:
    revision: int
    lifecycle: str


@dataclass(frozen=True)
class FeatureSelection:
    id: str
    revision: int


@dataclass(frozen=True)
class FeatureOffer:
    features: Mapping[str, tuple[FeatureRevision, ...]]
    required: tuple[str, ...]


def _need(condition):
    if not condition:
        raise ValueError("invalid feature syntax")


def _integer(raw):
    _need(re.fullmatch(r"[1-9][0-9]{0,4}", raw) and int(raw) <= 65535)
    return int(raw)


def _forbid_number(_):
    raise ValueError("only unsigned decimal integer tokens are allowed")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result)
        result[key] = value
    return result


def _members(value, allowed):
    _need(type(value) is dict and set(value) <= set(allowed))
    return value


def _id(value):
    _need(type(value) is str and len(value) <= 96 and _ID.fullmatch(value))
    return value


def parse_feature_offer(raw: str) -> FeatureOffer:
    """Read a standalone feature block, preserving raw duplicate/numeric rejection."""
    try:
        _need(type(raw) is str and len(raw.encode("utf-8")) <= 16384)
        block = _members(json.loads(raw, object_pairs_hook=_unique, parse_int=_integer,
            parse_float=_forbid_number, parse_constant=_forbid_number),
            ("feature_negotiation", "feature_offer", "required_features"))
        if block:
            _need(type(block.get("feature_negotiation")) is int and block["feature_negotiation"] == 1)
        features = {}
        if "feature_offer" in block:
            offers = block["feature_offer"]
            _need(type(offers) is list and len(offers) <= 32)
            for value in offers:
                item = _members(value, ("id", "revisions"))
                name = _id(item.get("id"))
                _need(name not in features)
                revisions = item.get("revisions")
                _need(type(revisions) is list and 1 <= len(revisions) <= 16)
                parsed = []
                last = 0
                for value in revisions:
                    entry = _members(value, ("revision", "lifecycle"))
                    revision, lifecycle = entry.get("revision"), entry.get("lifecycle")
                    _need(type(revision) is int and last < revision <= 65535)
                    _need(type(lifecycle) is str and lifecycle in ("experimental", "stable", "deprecated"))
                    parsed.append(FeatureRevision(revision, lifecycle))
                    last = revision
                features[name] = tuple(parsed)
        required = []
        if "required_features" in block:
            names = block["required_features"]
            _need(type(names) is list and len(names) <= 32)
            for value in names:
                name = _id(value)
                _need(name in features and name not in required)
                required.append(name)
        return FeatureOffer(MappingProxyType(features), tuple(required))
    except (ValueError, TypeError, RecursionError, OverflowError) as error:
        raise FeatureFormatError(NATIVE_INVALID) from error


def parse_required_feature_header(instances: Sequence[str]) -> tuple[FeatureSelection, ...]:
    """Raw instances before framework joining. Missing is allowed; empty is invalid."""
    try:
        _need(not isinstance(instances, (str, bytes)) and len(instances) <= 1)
        if not instances:
            return ()
        raw = instances[0]
        _need(type(raw) is str and len(raw) <= 4096 and raw.isascii())
        pairs = raw.split(",")
        _need(1 <= len(pairs) <= 32)
        parsed = []
        last = ""
        for pair in pairs:
            parts = pair.split("=")
            _need(len(parts) == 2)
            name = _id(parts[0])
            _need(name > last)
            parsed.append(FeatureSelection(name, _integer(parts[1])))
            last = name
        return tuple(parsed)
    except (ValueError, TypeError) as error:
        raise FeatureFormatError(HTTP_INVALID) from error
