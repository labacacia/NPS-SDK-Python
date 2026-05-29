# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""CGN (Cognon) token-budget estimation helpers (token-budget.md §2.2)."""
from __future__ import annotations
import json, math
from dataclasses import dataclass
from typing import Any

def estimate_cgn(value: str | bytes, /) -> int:
    """CGN = ceil(UTF-8 bytes / 4); returns 0 for empty input."""
    b = value.encode("utf-8") if isinstance(value, str) else value
    return math.ceil(len(b) / 4) if b else 0

def estimate_cgn_json(obj: Any) -> int:
    """Compact-JSON-serialize obj then call estimate_cgn."""
    return estimate_cgn(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))

def estimate_cgn_rows(rows: list[Any]) -> int:
    """Sum CGN estimates for a list of JSON-serializable rows."""
    return sum(estimate_cgn_json(r) for r in rows)

@dataclass(frozen=True)
class TokenBudgetMeta:
    """Wire-level CGN budget descriptor (NWP §5, NWM token_budget block)."""
    cgn_limit:            int
    tokenizer:            str | None           = None
    supported_tokenizers: tuple[str, ...] | None = None
    token_budget_hint:    bool                 = True
    profile:              str                  = "cgn.v1"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"cgn_limit": self.cgn_limit, "profile": self.profile}
        if self.tokenizer            is not None: d["tokenizer"]            = self.tokenizer
        if self.supported_tokenizers is not None: d["supported_tokenizers"] = list(self.supported_tokenizers)
        if self.token_budget_hint:                d["token_budget_hint"]    = True
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenBudgetMeta":
        st = data.get("supported_tokenizers")
        return cls(
            cgn_limit=int(data["cgn_limit"]),
            tokenizer=data.get("tokenizer"),
            supported_tokenizers=tuple(st) if st else None,
            token_budget_hint=bool(data.get("token_budget_hint", True)),
            profile=str(data.get("profile", "cgn.v1")),
        )

class BudgetExceededError(Exception):
    """Raised when a response payload exceeds the declared CGN budget."""
    def __init__(self, requested: int, limit: int) -> None:
        self.requested = requested
        self.limit = limit
        super().__init__(f"CGN budget exceeded: {requested} > {limit}")
