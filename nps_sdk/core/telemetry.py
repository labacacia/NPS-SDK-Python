# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Lightweight, dependency-free telemetry primitives for the NPS SDK.

Port of the .NET reference instrumentation (``NwpTelemetry`` / ``NopTelemetry``
built on ``System.Diagnostics.Metrics``). The .NET SDK exposes counters,
histograms, and ``ActivitySource`` spans that are no-ops until a listener is
attached; this module provides the same shapes with an *always-on* in-memory
reader so callers (and tests) can inspect the recorded values without wiring an
external collector.

If the OpenTelemetry Python API is installed it is **not** required — this
module is intentionally standalone so the SDK carries no telemetry dependency.
Instrument *names* match the .NET reference exactly (``nps.frames.processed``,
``nps.nop.task.duration_ms``, ...) so a host that later bridges these to an OTEL
exporter keeps the same metric identities across languages.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping


# ── Metric snapshots (immutable views returned to callers) ─────────────────────

Labels = tuple[tuple[str, str], ...]


def _freeze_labels(labels: Mapping[str, Any] | None) -> Labels:
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


@dataclass(frozen=True)
class CounterSnapshot:
    name: str
    unit: str
    description: str
    #: Per-labelset totals, keyed by the frozen (sorted) label tuple.
    cells: dict[Labels, float]

    def total(self) -> float:
        """Sum across all label cells (matches a Prometheus counter total)."""
        return sum(self.cells.values())

    def value(self, **labels: Any) -> float:
        return self.cells.get(_freeze_labels(labels), 0.0)


@dataclass(frozen=True)
class HistogramSnapshot:
    name: str
    unit: str
    description: str
    #: Per-labelset recorded samples.
    cells: dict[Labels, list[float]]

    def count(self, **labels: Any) -> int:
        if labels:
            return len(self.cells.get(_freeze_labels(labels), []))
        return sum(len(v) for v in self.cells.values())

    def sum(self, **labels: Any) -> float:
        if labels:
            return sum(self.cells.get(_freeze_labels(labels), []))
        return sum(sum(v) for v in self.cells.values())

    def samples(self, **labels: Any) -> list[float]:
        return list(self.cells.get(_freeze_labels(labels), []))


# ── Instruments ────────────────────────────────────────────────────────────────

class Counter:
    """Monotonic counter with per-labelset cells (mirrors .NET ``Counter<long>``)."""

    def __init__(self, name: str, unit: str = "", description: str = "") -> None:
        self.name = name
        self.unit = unit
        self.description = description
        self._cells: dict[Labels, float] = {}
        self._gate = threading.Lock()

    def add(self, value: float = 1, /, **labels: Any) -> None:
        key = _freeze_labels(labels)
        with self._gate:
            self._cells[key] = self._cells.get(key, 0.0) + value

    def snapshot(self) -> CounterSnapshot:
        with self._gate:
            return CounterSnapshot(self.name, self.unit, self.description, dict(self._cells))

    def reset(self) -> None:
        with self._gate:
            self._cells.clear()


class Histogram:
    """Records a distribution of values (mirrors .NET ``Histogram<double>``)."""

    def __init__(self, name: str, unit: str = "", description: str = "") -> None:
        self.name = name
        self.unit = unit
        self.description = description
        self._cells: dict[Labels, list[float]] = {}
        self._gate = threading.Lock()

    def record(self, value: float, /, **labels: Any) -> None:
        key = _freeze_labels(labels)
        with self._gate:
            self._cells.setdefault(key, []).append(float(value))

    def snapshot(self) -> HistogramSnapshot:
        with self._gate:
            return HistogramSnapshot(
                self.name, self.unit, self.description,
                {k: list(v) for k, v in self._cells.items()},
            )

    def reset(self) -> None:
        with self._gate:
            self._cells.clear()


# ── Spans ────────────────────────────────────────────────────────────────────

@dataclass
class Span:
    """A single unit of traced work (mirrors a .NET ``Activity``).

    Spans are recorded in the owning :class:`Tracer` when they end, so tests
    can assert on span names, attributes, status, and duration.
    """

    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "unset"          # "unset" | "ok" | "error"
    status_description: str | None = None
    start_ns: int = field(default_factory=time.perf_counter_ns)
    end_ns: int | None = None

    def set_attribute(self, key: str, value: Any) -> "Span":
        self.attributes[key] = value
        return self

    def set_status(self, status: str, description: str | None = None) -> "Span":
        self.status = status
        self.status_description = description
        return self

    @property
    def duration_ms(self) -> float:
        end = self.end_ns if self.end_ns is not None else time.perf_counter_ns()
        return (end - self.start_ns) / 1_000_000.0


class Tracer:
    """Creates and records :class:`Span` objects for a named source."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._spans: list[Span] = []
        self._gate = threading.Lock()

    def start_span(self, name: str, **attributes: Any) -> "_SpanContext":
        span = Span(name=name, attributes=dict(attributes))
        return _SpanContext(self, span)

    def _finish(self, span: Span) -> None:
        span.end_ns = time.perf_counter_ns()
        if span.status == "unset":
            span.status = "ok"
        with self._gate:
            self._spans.append(span)

    def recorded_spans(self) -> list[Span]:
        with self._gate:
            return list(self._spans)

    def reset(self) -> None:
        with self._gate:
            self._spans.clear()


class _SpanContext:
    """Context manager wrapping a live :class:`Span`; records it on exit."""

    def __init__(self, tracer: Tracer, span: Span) -> None:
        self._tracer = tracer
        self.span = span

    def __enter__(self) -> Span:
        return self.span

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None and self.span.status == "unset":
            self.span.set_status("error", str(exc) if exc else exc_type.__name__)
        self._tracer._finish(self.span)
        return False


# ── Meter (a named group of instruments) ───────────────────────────────────────

class Meter:
    """A named factory for counters and histograms (mirrors .NET ``Meter``)."""

    def __init__(self, name: str, version: str = "") -> None:
        self.name = name
        self.version = version
        self._instruments: dict[str, Counter | Histogram] = {}
        self._gate = threading.Lock()

    def create_counter(self, name: str, unit: str = "", description: str = "") -> Counter:
        with self._gate:
            c = Counter(name, unit, description)
            self._instruments[name] = c
            return c

    def create_histogram(self, name: str, unit: str = "", description: str = "") -> Histogram:
        with self._gate:
            h = Histogram(name, unit, description)
            self._instruments[name] = h
            return h

    def instruments(self) -> Iterator[Counter | Histogram]:
        with self._gate:
            return iter(list(self._instruments.values()))

    def reset(self) -> None:
        for inst in self.instruments():
            inst.reset()
