# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS daemon observability utilities (port of ``NPS.Daemon.Observability``).

Portable, framework-agnostic building blocks for the NPS daemons:

  * :mod:`~nps_sdk.daemon.health`   — ``/healthz`` / ``/readyz`` probe rendering.
  * :mod:`~nps_sdk.daemon.metrics`  — Prometheus-format ``/metrics`` registry.
  * :mod:`~nps_sdk.daemon.logging`  — single-line JSON structured logging.
  * :mod:`~nps_sdk.daemon.shutdown` — graceful drain coordinator.
"""

from nps_sdk.daemon.health import (
    JSON_CONTENT_TYPE,
    DelegateReadinessProbe,
    HealthProbeResponse,
    IReadinessProbe,
    render_healthz,
    render_readyz,
)
from nps_sdk.daemon.metrics import (
    METRICS_CONTENT_TYPE,
    Counter,
    Gauge,
    MetricsRegistry,
    authorize_metrics,
)
from nps_sdk.daemon.logging import (
    LOG_LEVEL_ENV_VAR,
    NpsJsonFormatter,
    configure_json_logging,
    resolve_log_level,
)
from nps_sdk.daemon.shutdown import (
    DEFAULT_DRAIN_TIMEOUT_SECONDS,
    GracefulShutdown,
    ShutdownState,
)

__all__ = [
    # health
    "JSON_CONTENT_TYPE",
    "HealthProbeResponse",
    "IReadinessProbe",
    "DelegateReadinessProbe",
    "render_healthz",
    "render_readyz",
    # metrics
    "METRICS_CONTENT_TYPE",
    "MetricsRegistry",
    "Counter",
    "Gauge",
    "authorize_metrics",
    # logging
    "LOG_LEVEL_ENV_VAR",
    "NpsJsonFormatter",
    "configure_json_logging",
    "resolve_log_level",
    # shutdown
    "DEFAULT_DRAIN_TIMEOUT_SECONDS",
    "GracefulShutdown",
    "ShutdownState",
]
