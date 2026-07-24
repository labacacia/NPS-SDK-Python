# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Prometheus-compatible metrics registry (port of ``MetricsRegistry`` +
``MetricsEndpoint``).

A small counter/gauge registry that renders the Prometheus/OpenMetrics text
exposition format. The exposition ``Content-Type`` matches the .NET reference:
``text/plain; version=0.0.4; charset=utf-8``.
"""

from __future__ import annotations

import hmac
import threading

METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: ASCII Unit Separator used to key labelled counter cells (matches .NET).
_CELL_SEPARATOR = "\x1f"


def _format_value(v: float) -> str:
    # Integer-valued floats print without a trailing ".0" (matches .NET "0.####").
    if v == int(v):
        return str(int(v))
    return repr(v)


def _escape_label(v: str) -> str:
    if not any(c in v for c in ("\\", '"', "\n")):
        return v
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Counter:
    """Monotonic counter with one cell per label-value tuple."""

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...]) -> None:
        self._name = name
        self._help = help_text
        self._labels = label_names
        self._cells: dict[str, float] = {}
        self._gate = threading.Lock()
        if not label_names:
            self._cells[""] = 0.0

    def inc(self, by: float = 1.0, *label_values: str) -> None:
        key = self._cell_key(label_values)
        with self._gate:
            self._cells[key] = self._cells.get(key, 0.0) + by

    def _cell_key(self, label_values: tuple[str, ...]) -> str:
        if not self._labels:
            return ""
        parts = [label_values[i] if i < len(label_values) else ""
                 for i in range(len(self._labels))]
        return _CELL_SEPARATOR.join(parts)

    def write_to(self, out: list[str]) -> None:
        out.append(f"# HELP {self._name} {self._help}\n")
        out.append(f"# TYPE {self._name} counter\n")
        with self._gate:
            cells = list(self._cells.items())
        for key, val in cells:
            if self._labels:
                parts = key.split(_CELL_SEPARATOR)
                label_str = ",".join(
                    f'{self._labels[i]}="{_escape_label(parts[i] if i < len(parts) else "")}"'
                    for i in range(len(self._labels)))
                out.append(f"{self._name}{{{label_str}}} {_format_value(val)}\n")
            else:
                out.append(f"{self._name} {_format_value(val)}\n")


class Gauge:
    """Single-valued gauge; thread-safe."""

    def __init__(self, name: str, help_text: str) -> None:
        self._name = name
        self._help = help_text
        self._value = 0.0
        self._gate = threading.Lock()

    def set(self, value: float) -> None:
        with self._gate:
            self._value = value

    def inc(self, by: float = 1.0) -> None:
        self.add(by)

    def dec(self, by: float = 1.0) -> None:
        self.add(-by)

    def add(self, by: float) -> None:
        with self._gate:
            self._value += by

    @property
    def value(self) -> float:
        with self._gate:
            return self._value

    def write_to(self, out: list[str]) -> None:
        out.append(f"# HELP {self._name} {self._help}\n")
        out.append(f"# TYPE {self._name} gauge\n")
        out.append(f"{self._name} {_format_value(self.value)}\n")


class MetricsRegistry:
    """Registers counters/gauges and renders Prometheus exposition text."""

    def __init__(self) -> None:
        self._entries: list[Counter | Gauge] = []
        self._gate = threading.Lock()

    def register_counter(self, name: str, help_text: str,
                         *label_names: str) -> Counter:
        c = Counter(name, help_text, tuple(label_names))
        with self._gate:
            self._entries.append(c)
        return c

    def register_gauge(self, name: str, help_text: str) -> Gauge:
        g = Gauge(name, help_text)
        with self._gate:
            self._entries.append(g)
        return g

    def render(self) -> str:
        with self._gate:
            snap = list(self._entries)
        out: list[str] = []
        for e in snap:
            e.write_to(out)
        return "".join(out)


def authorize_metrics(auth_header: str | None, bearer_token: str | None,
                      require_bearer_token: bool = False) -> tuple[bool, int]:
    """Mirror .NET ``MetricsEndpoint.Authorize``.

    Returns ``(allowed, failure_status)``. When allowed, ``failure_status`` is 0.
    An unconfigured token allows access unless ``require_bearer_token`` is set,
    in which case the endpoint is hidden (404).
    """
    if not bearer_token:
        if not require_bearer_token:
            return True, 0
        return False, 404

    header = auth_header or ""
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False, 401
    supplied = header[len(prefix):]
    if hmac.compare_digest(supplied, bearer_token):
        return True, 0
    return False, 401
