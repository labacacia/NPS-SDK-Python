# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Daemon observability tests — health/readiness, metrics, structured logging,
and graceful shutdown coordination."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from nps_sdk.daemon import (
    DEFAULT_DRAIN_TIMEOUT_SECONDS,
    DelegateReadinessProbe,
    GracefulShutdown,
    MetricsRegistry,
    authorize_metrics,
    configure_json_logging,
    render_healthz,
    render_readyz,
)
from nps_sdk.daemon.health import JSON_CONTENT_TYPE
from nps_sdk.daemon.logging import LOG_LEVEL_ENV_VAR, NpsJsonFormatter, resolve_log_level
from nps_sdk.daemon.metrics import METRICS_CONTENT_TYPE


# ── Health / readiness ──────────────────────────────────────────────────────

def test_healthz_is_ok():
    r = render_healthz()
    assert r.status_code == 200
    assert r.content_type == JSON_CONTENT_TYPE
    assert json.loads(r.body) == {"status": "ok"}


@pytest.mark.asyncio
async def test_readyz_ok_with_no_probes():
    r = await render_readyz([])
    assert r.status_code == 200
    assert json.loads(r.body) == {"status": "ok"}


@pytest.mark.asyncio
async def test_readyz_ok_when_all_probes_pass():
    async def ok():
        return None
    r = await render_readyz([DelegateReadinessProbe("storage", ok)])
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_readyz_returns_503_with_first_failure_reason():
    async def bad():
        return "db down"
    async def ok():
        return None
    r = await render_readyz([
        DelegateReadinessProbe("ok", ok),
        DelegateReadinessProbe("storage", bad),
    ])
    assert r.status_code == 503
    body = json.loads(r.body)
    assert body == {"status": "error", "reason": "db down"}


@pytest.mark.asyncio
async def test_readyz_probe_exception_becomes_reason():
    async def boom():
        raise RuntimeError("kaboom")
    r = await render_readyz([DelegateReadinessProbe("keys", boom)])
    assert r.status_code == 503
    assert "keys: kaboom" in json.loads(r.body)["reason"]


# ── Metrics registry ────────────────────────────────────────────────────────

def test_counter_prometheus_exposition():
    reg = MetricsRegistry()
    c = reg.register_counter("nps_frames_total", "Total frames", "frame_type")
    c.inc(1, "query")
    c.inc(2, "query")
    c.inc(1, "action")
    out = reg.render()
    assert "# HELP nps_frames_total Total frames" in out
    assert "# TYPE nps_frames_total counter" in out
    assert 'nps_frames_total{frame_type="query"} 3' in out
    assert 'nps_frames_total{frame_type="action"} 1' in out


def test_counter_no_labels_and_gauge():
    reg = MetricsRegistry()
    c = reg.register_counter("nps_events_total", "Events")
    c.inc()
    g = reg.register_gauge("nps_inflight", "In-flight requests")
    g.set(5)
    g.inc()
    g.dec(2)
    out = reg.render()
    assert "nps_events_total 1" in out
    assert "# TYPE nps_inflight gauge" in out
    assert "nps_inflight 4" in out


def test_metrics_content_type():
    assert METRICS_CONTENT_TYPE == "text/plain; version=0.0.4; charset=utf-8"


def test_authorize_metrics_open_when_no_token():
    assert authorize_metrics(None, None) == (True, 0)


def test_authorize_metrics_hidden_when_required_but_unconfigured():
    assert authorize_metrics(None, None, require_bearer_token=True) == (False, 404)


def test_authorize_metrics_bearer_token():
    assert authorize_metrics("Bearer secret", "secret") == (True, 0)
    assert authorize_metrics("Bearer wrong", "secret") == (False, 401)
    assert authorize_metrics("secret", "secret") == (False, 401)


# ── Structured JSON logging ─────────────────────────────────────────────────

def test_json_formatter_fields():
    fmt = NpsJsonFormatter()
    rec = logging.LogRecord("nps.daemon", logging.WARNING, __file__, 1,
                            "hello %s", ("world",), None)
    line = fmt.format(rec)
    obj = json.loads(line)
    assert obj["level"] == "warn"
    assert obj["msg"] == "hello world"
    assert obj["logger"] == "nps.daemon"
    assert "timestamp" in obj


def test_json_formatter_includes_trace_id_and_exception():
    fmt = NpsJsonFormatter()
    try:
        raise ValueError("bad")
    except ValueError:
        import sys
        rec = logging.LogRecord("nps.daemon", logging.ERROR, __file__, 1,
                                "oops", (), sys.exc_info())
    rec.trace_id = "abcd1234"
    obj = json.loads(fmt.format(rec))
    assert obj["trace_id"] == "abcd1234"
    assert "ValueError" in obj["exception"]


def test_resolve_log_level_from_env(monkeypatch):
    monkeypatch.setenv(LOG_LEVEL_ENV_VAR, "warning")
    assert resolve_log_level() == logging.WARNING
    monkeypatch.setenv(LOG_LEVEL_ENV_VAR, "bogus")
    assert resolve_log_level(logging.INFO) == logging.INFO
    monkeypatch.delenv(LOG_LEVEL_ENV_VAR, raising=False)
    assert resolve_log_level(logging.DEBUG) == logging.DEBUG


def test_configure_json_logging_installs_formatter():
    handler = configure_json_logging(default_level=logging.INFO)
    try:
        assert isinstance(handler.formatter, NpsJsonFormatter)
        assert logging.getLogger().level == logging.INFO
    finally:
        logging.getLogger().removeHandler(handler)


# ── Graceful shutdown ───────────────────────────────────────────────────────

def test_default_drain_timeout():
    assert DEFAULT_DRAIN_TIMEOUT_SECONDS == 30.0


@pytest.mark.asyncio
async def test_shutdown_gate_flips_and_runs_hooks_in_order():
    gs = GracefulShutdown(drain_timeout=5.0)
    order = []
    gs.on_shutdown(lambda: _append(order, "a"))
    gs.on_shutdown(lambda: _append(order, "b"))
    assert gs.state.is_stopping is False
    await gs.trigger()
    assert gs.state.is_stopping is True
    assert order == ["a", "b"]


@pytest.mark.asyncio
async def test_shutdown_is_idempotent():
    gs = GracefulShutdown(drain_timeout=5.0)
    calls = []
    gs.on_shutdown(lambda: _append(calls, "x"))
    await gs.trigger()
    await gs.trigger()
    assert calls == ["x"]


@pytest.mark.asyncio
async def test_shutdown_bounded_by_timeout():
    gs = GracefulShutdown(drain_timeout=0.05)

    async def slow():
        await asyncio.sleep(10)

    gs.on_shutdown(slow)
    # Must return promptly despite the slow hook.
    await asyncio.wait_for(gs.trigger(), timeout=2.0)
    assert gs.state.is_stopping is True


@pytest.mark.asyncio
async def test_wait_for_shutdown_unblocks_after_trigger():
    gs = GracefulShutdown(drain_timeout=5.0)
    waiter = asyncio.ensure_future(gs.wait_for_shutdown())
    await asyncio.sleep(0)
    assert not waiter.done()
    await gs.trigger()
    await asyncio.wait_for(waiter, timeout=1.0)


@pytest.mark.asyncio
async def test_install_signal_handlers_does_not_raise():
    gs = GracefulShutdown()
    # Should install (or silently no-op on unsupported platforms) without error.
    gs.install_signal_handlers(asyncio.get_event_loop())


def test_level_name_folds_custom_levels():
    fmt = NpsJsonFormatter()
    rec = logging.LogRecord("x", 25, __file__, 1, "m", (), None)  # between INFO/WARNING
    obj = json.loads(fmt.format(rec))
    assert obj["level"] == "info"


async def _append(lst, val):
    lst.append(val)
