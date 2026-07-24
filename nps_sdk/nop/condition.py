# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
CEL-subset condition evaluator for DAG node ``condition`` fields (NPS-5 §3.1.5).

Supported syntax:
  * Comparison: ``$.node.field > 0.7``, ``$.node.status == "ok"``, ``$.n.x != null``
  * Boolean logic: ``&&``, ``||``, ``!``
  * Grouping: ``( expr )``
  * Literals: numbers, quoted strings, ``true``, ``false``, ``null``
  * JSONPath access: ``$.node_id.field.sub`` (resolved via the input mapper)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping

from nps_sdk.nop.input_mapper import resolve


class NopConditionError(Exception):
    """Raised when a condition expression cannot be parsed or evaluated."""

    def __init__(self, message: str, expression: str = "") -> None:
        super().__init__(f"{message}  Expression: «{expression}»")
        self.expression = expression


class _Tok(enum.Enum):
    DOLLAR_PATH = "dollar_path"
    NUMBER = "number"
    STRING = "string"
    TRUE = "true"
    FALSE = "false"
    NULL = "null"
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    EQ = "=="
    NEQ = "!="
    AND = "&&"
    OR = "||"
    NOT = "!"
    LPAREN = "("
    RPAREN = ")"
    EOF = "eof"


@dataclass(frozen=True)
class _Token:
    kind: _Tok
    raw: str


_COMPARISON_OPS = (_Tok.GT, _Tok.GTE, _Tok.LT, _Tok.LTE, _Tok.EQ, _Tok.NEQ)


def _tokenize(s: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue

        # Dollar path: $.node.field
        if c == "$" and i + 1 < n and s[i + 1] == ".":
            start = i
            while i < n and (s[i].isalnum() or s[i] in "_.$"):
                i += 1
            tokens.append(_Token(_Tok.DOLLAR_PATH, s[start:i]))
            continue

        # String literal
        if c == '"':
            start = i
            i += 1
            while i < n and s[i] != '"':
                i += 1
            i += 1  # closing quote
            tokens.append(_Token(_Tok.STRING, s[start + 1:i - 1]))
            continue

        # Number
        if c.isdigit() or (c == "-" and i + 1 < n and s[i + 1].isdigit()):
            start = i
            if s[i] == "-":
                i += 1
            while i < n and (s[i].isdigit() or s[i] == "."):
                i += 1
            tokens.append(_Token(_Tok.NUMBER, s[start:i]))
            continue

        # Two-char operators
        two = s[i:i + 2]
        two_map = {
            ">=": _Tok.GTE, "<=": _Tok.LTE, "==": _Tok.EQ,
            "!=": _Tok.NEQ, "&&": _Tok.AND, "||": _Tok.OR,
        }
        if two in two_map:
            tokens.append(_Token(two_map[two], two))
            i += 2
            continue

        # One-char operators
        one_map = {
            ">": _Tok.GT, "<": _Tok.LT, "!": _Tok.NOT,
            "(": _Tok.LPAREN, ")": _Tok.RPAREN,
        }
        if c in one_map:
            tokens.append(_Token(one_map[c], c))
            i += 1
            continue

        # Keywords: true, false, null
        if c.isalpha():
            start = i
            while i < n and s[i].isalnum():
                i += 1
            kw = s[start:i]
            if kw == "true":
                tokens.append(_Token(_Tok.TRUE, "true"))
            elif kw == "false":
                tokens.append(_Token(_Tok.FALSE, "false"))
            elif kw == "null":
                tokens.append(_Token(_Tok.NULL, "null"))
            else:
                raise NopConditionError(f"Unknown token '{kw}'.", s)
            continue

        raise NopConditionError(f"Unexpected character '{c}' at position {i}.", s)

    tokens.append(_Token(_Tok.EOF, ""))
    return tokens


class _Parser:
    def __init__(self, tokens: list[_Token], context: Mapping[str, Any]) -> None:
        self._tokens = tokens
        self._context = context
        self._pos = 0

    @property
    def _current(self) -> _Token:
        return self._tokens[self._pos]

    def _consume(self) -> _Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    # or_expr := and_expr ('||' and_expr)*
    def parse_or_expr(self) -> bool:
        left = self._parse_and_expr()
        while self._current.kind == _Tok.OR:
            self._consume()
            right = self._parse_and_expr()
            left = left or right
        return left

    # and_expr := not_expr ('&&' not_expr)*
    def _parse_and_expr(self) -> bool:
        left = self._parse_not_expr()
        while self._current.kind == _Tok.AND:
            self._consume()
            right = self._parse_not_expr()
            left = left and right
        return left

    # not_expr := '!' not_expr | comparison
    def _parse_not_expr(self) -> bool:
        if self._current.kind == _Tok.NOT:
            self._consume()
            return not self._parse_not_expr()
        return self._parse_comparison()

    # comparison := '(' or_expr ')' | true | false | value (op value)?
    def _parse_comparison(self) -> bool:
        if self._current.kind == _Tok.LPAREN:
            self._consume()  # '('
            inner = self.parse_or_expr()
            if self._current.kind != _Tok.RPAREN:
                raise NopConditionError("Expected ')'.")
            self._consume()
            return inner

        if self._current.kind == _Tok.TRUE:
            self._consume()
            return True
        if self._current.kind == _Tok.FALSE:
            self._consume()
            return False

        lhs = self._parse_value()

        op = self._current.kind
        if op not in _COMPARISON_OPS:
            return _as_truthy(lhs)

        self._consume()  # operator
        rhs = self._parse_value()
        return _compare(lhs, op, rhs)

    # value := dollar_path | number | string | null | true | false
    def _parse_value(self) -> Any:
        tok = self._consume()
        if tok.kind == _Tok.DOLLAR_PATH:
            return self._resolve_path(tok.raw)
        if tok.kind == _Tok.NUMBER:
            return float(tok.raw)
        if tok.kind == _Tok.STRING:
            return tok.raw
        if tok.kind == _Tok.TRUE:
            return True
        if tok.kind == _Tok.FALSE:
            return False
        if tok.kind == _Tok.NULL:
            return None
        raise NopConditionError(f"Expected a value, got '{tok.raw}'.")

    def _resolve_path(self, path: str) -> Any:
        value = resolve(path, self._context)
        # Numbers stay numeric; objects/arrays fall through as-is (truthiness only).
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return float(value)
        return value


def _as_truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return len(v) > 0
    if v is None:
        return False
    return True


def _compare(lhs: Any, op: _Tok, rhs: Any) -> bool:
    if op == _Tok.EQ:
        return _eq(lhs, rhs)
    if op == _Tok.NEQ:
        return not _eq(lhs, rhs)
    if lhs is None or rhs is None:
        return False

    # Numeric comparison (bools excluded)
    lnum = isinstance(lhs, (int, float)) and not isinstance(lhs, bool)
    rnum = isinstance(rhs, (int, float)) and not isinstance(rhs, bool)
    if lnum and rnum:
        if op == _Tok.GT:
            return lhs > rhs
        if op == _Tok.GTE:
            return lhs >= rhs
        if op == _Tok.LT:
            return lhs < rhs
        if op == _Tok.LTE:
            return lhs <= rhs
        return False

    # String comparison (ordinal)
    if isinstance(lhs, str) and isinstance(rhs, str):
        if op == _Tok.GT:
            return lhs > rhs
        if op == _Tok.GTE:
            return lhs >= rhs
        if op == _Tok.LT:
            return lhs < rhs
        if op == _Tok.LTE:
            return lhs <= rhs
        return False

    return False


def _eq(a: Any, b: Any) -> bool:
    # Treat numeric equality across int/float; keep bool distinct from numbers.
    a_num = isinstance(a, (int, float)) and not isinstance(a, bool)
    b_num = isinstance(b, (int, float)) and not isinstance(b, bool)
    if a_num and b_num:
        return float(a) == float(b)
    return a == b


def evaluate(condition: str, context: Mapping[str, Any]) -> bool:
    """
    Evaluate ``condition`` against completed node results.

    Returns ``True`` if the node should execute; ``False`` if it should be
    skipped. Empty / whitespace conditions evaluate to ``True``.

    Raises:
        NopConditionError: for syntax errors or unresolvable paths.
    """
    if not condition or not condition.strip():
        return True

    try:
        tokens = _tokenize(condition.strip())
        parser = _Parser(tokens, context)
        return parser.parse_or_expr()
    except NopConditionError:
        raise
    except Exception as ex:  # noqa: BLE001
        raise NopConditionError(f"Condition evaluation error: {ex}", condition) from ex
