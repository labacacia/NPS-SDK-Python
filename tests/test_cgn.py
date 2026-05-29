# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for CGN token-budget estimation helpers (token-budget.md §2.2)."""

import pytest

from nps_sdk.nwp.cgn import (
    estimate_cgn,
    estimate_cgn_json,
    estimate_cgn_rows,
    TokenBudgetMeta,
    BudgetExceededError,
)
from nps_sdk.nwp.frames import QueryFrame
from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.frames import EncodingTier
from nps_sdk.core.registry import FrameRegistry


# ── estimate_cgn ──────────────────────────────────────────────────────────────

class TestEstimateCgn:
    def test_empty_string_returns_zero(self):
        assert estimate_cgn("") == 0

    def test_empty_bytes_returns_zero(self):
        assert estimate_cgn(b"") == 0

    def test_hello_5_bytes(self):
        # "hello" = 5 bytes → ceil(5/4) = 2
        assert estimate_cgn("hello") == 2

    def test_test_4_bytes(self):
        # "test" = 4 bytes → ceil(4/4) = 1
        assert estimate_cgn("test") == 1

    def test_abcde_5_bytes(self):
        # "abcde" = 5 bytes → ceil(5/4) = 2
        assert estimate_cgn("abcde") == 2

    def test_8_bytes_aligned(self):
        # "abcdefgh" = 8 bytes → ceil(8/4) = 2
        assert estimate_cgn("abcdefgh") == 2

    def test_bytes_input_same_as_string(self):
        assert estimate_cgn(b"hello") == estimate_cgn("hello")

    def test_chinese_text_multibyte(self):
        # "你好" = 6 UTF-8 bytes → ceil(6/4) = 2
        assert estimate_cgn("你好") == 2

    def test_single_ascii_char(self):
        # "a" = 1 byte → ceil(1/4) = 1
        assert estimate_cgn("a") == 1

    def test_12_bytes(self):
        # "abcdefghijkl" = 12 bytes → ceil(12/4) = 3
        assert estimate_cgn("abcdefghijkl") == 3


# ── estimate_cgn_json ─────────────────────────────────────────────────────────

class TestEstimateCgnJson:
    def test_simple_dict(self):
        obj = {"key": "val"}
        # compact JSON: {"key":"val"} = 13 bytes → ceil(13/4) = 4
        import json
        raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        expected = estimate_cgn(raw)
        assert estimate_cgn_json(obj) == expected

    def test_empty_dict(self):
        assert estimate_cgn_json({}) == estimate_cgn("{}")

    def test_list(self):
        lst = [1, 2, 3]
        assert estimate_cgn_json(lst) == estimate_cgn("[1,2,3]")

    def test_string_value(self):
        assert estimate_cgn_json("hello") == estimate_cgn('"hello"')

    def test_integer(self):
        assert estimate_cgn_json(42) == estimate_cgn("42")


# ── estimate_cgn_rows ─────────────────────────────────────────────────────────

class TestEstimateCgnRows:
    def test_empty_rows(self):
        assert estimate_cgn_rows([]) == 0

    def test_single_row(self):
        row = {"id": 1, "name": "Alice"}
        assert estimate_cgn_rows([row]) == estimate_cgn_json(row)

    def test_multiple_rows_sum(self):
        rows = [{"id": 1}, {"id": 2}, {"id": 3}]
        expected = sum(estimate_cgn_json(r) for r in rows)
        assert estimate_cgn_rows(rows) == expected

    def test_rows_with_unicode(self):
        rows = [{"name": "你好"}, {"name": "world"}]
        expected = sum(estimate_cgn_json(r) for r in rows)
        assert estimate_cgn_rows(rows) == expected


# ── TokenBudgetMeta ───────────────────────────────────────────────────────────

class TestTokenBudgetMeta:
    def test_defaults(self):
        meta = TokenBudgetMeta(cgn_limit=1000)
        assert meta.profile == "cgn.v1"
        assert meta.token_budget_hint is True
        assert meta.tokenizer is None
        assert meta.supported_tokenizers is None

    def test_to_dict_minimal(self):
        meta = TokenBudgetMeta(cgn_limit=500)
        d = meta.to_dict()
        assert d["cgn_limit"] == 500
        assert d["profile"] == "cgn.v1"
        assert d["token_budget_hint"] is True
        assert "tokenizer" not in d
        assert "supported_tokenizers" not in d

    def test_to_dict_full(self):
        meta = TokenBudgetMeta(
            cgn_limit=2000,
            tokenizer="gpt4",
            supported_tokenizers=("gpt4", "cl100k"),
            token_budget_hint=True,
            profile="cgn.v1",
        )
        d = meta.to_dict()
        assert d["cgn_limit"] == 2000
        assert d["tokenizer"] == "gpt4"
        assert d["supported_tokenizers"] == ["gpt4", "cl100k"]

    def test_roundtrip(self):
        meta = TokenBudgetMeta(
            cgn_limit=1024,
            tokenizer="cl100k",
            supported_tokenizers=("cl100k", "p50k"),
            token_budget_hint=True,
            profile="cgn.v1",
        )
        back = TokenBudgetMeta.from_dict(meta.to_dict())
        assert back.cgn_limit == meta.cgn_limit
        assert back.tokenizer == meta.tokenizer
        assert back.supported_tokenizers == meta.supported_tokenizers
        assert back.token_budget_hint == meta.token_budget_hint
        assert back.profile == meta.profile

    def test_from_dict_minimal(self):
        meta = TokenBudgetMeta.from_dict({"cgn_limit": 100})
        assert meta.cgn_limit == 100
        assert meta.profile == "cgn.v1"
        assert meta.token_budget_hint is True

    def test_token_budget_hint_false_not_in_dict(self):
        # token_budget_hint=False should NOT appear in to_dict()
        meta = TokenBudgetMeta(cgn_limit=100, token_budget_hint=False)
        d = meta.to_dict()
        assert "token_budget_hint" not in d


# ── BudgetExceededError ───────────────────────────────────────────────────────

class TestBudgetExceededError:
    def test_carries_requested_and_limit(self):
        err = BudgetExceededError(requested=1500, limit=1000)
        assert err.requested == 1500
        assert err.limit == 1000

    def test_message_format(self):
        err = BudgetExceededError(requested=200, limit=100)
        assert "200" in str(err)
        assert "100" in str(err)

    def test_is_exception(self):
        with pytest.raises(BudgetExceededError):
            raise BudgetExceededError(10, 5)


# ── QueryFrame with token_budget and tokenizer ────────────────────────────────

class TestQueryFrameTokenBudget:
    @pytest.fixture
    def codec(self) -> NpsFrameCodec:
        return NpsFrameCodec(FrameRegistry.create_full())

    def test_token_budget_roundtrip_json(self, codec: NpsFrameCodec):
        frame = QueryFrame(limit=10, token_budget=512, tokenizer="cl100k")
        wire = codec.encode(frame, override_tier=EncodingTier.JSON)
        out = codec.decode(wire)
        assert isinstance(out, QueryFrame)
        assert out.token_budget == 512
        assert out.tokenizer == "cl100k"

    def test_token_budget_roundtrip_msgpack(self, codec: NpsFrameCodec):
        frame = QueryFrame(limit=5, token_budget=1024, tokenizer="gpt4")
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, QueryFrame)
        assert out.token_budget == 1024
        assert out.tokenizer == "gpt4"

    def test_token_budget_none_by_default(self, codec: NpsFrameCodec):
        frame = QueryFrame(limit=10)
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, QueryFrame)
        assert out.token_budget is None
        assert out.tokenizer is None

    def test_to_dict_omits_none_fields(self):
        frame = QueryFrame(limit=10)
        d = frame.to_dict()
        assert "token_budget" not in d
        assert "tokenizer" not in d

    def test_to_dict_includes_when_set(self):
        frame = QueryFrame(limit=10, token_budget=256, tokenizer="p50k")
        d = frame.to_dict()
        assert d["token_budget"] == 256
        assert d["tokenizer"] == "p50k"

    def test_from_dict_parses_token_budget(self):
        frame = QueryFrame.from_dict({"limit": 10, "token_budget": 128, "tokenizer": "cl100k"})
        assert frame.token_budget == 128
        assert frame.tokenizer == "cl100k"
