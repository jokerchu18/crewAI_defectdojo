"""Per-operation-type ``TimeoutConfig`` presets.

Each entry composes connect / read / write timeouts, retry policy,
and circuit-breaker parameters for a class of external calls.  All
values are seeded from ``settings`` so operators can tune via
environment variables without touching code.
"""

from __future__ import annotations

from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.utils.retry import TimeoutConfig


def _timeout_config(
    *,
    connect_timeout: float,
    read_timeout: float,
    write_timeout: float = 60.0,
    agent_timeout: float | None = None,
    max_retries: int | None = None,
    backoff_base: float | None = None,
    backoff_max: float | None = None,
    circuit_breaker_enabled: bool | None = None,
    circuit_breaker_threshold: int | None = None,
    circuit_breaker_recovery: float | None = None,
) -> TimeoutConfig:
    """Build a ``TimeoutConfig``, falling back to global settings defaults."""
    return TimeoutConfig(
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        write_timeout=write_timeout,
        agent_timeout=(
            settings.agent_default_timeout
            if agent_timeout is None
            else agent_timeout
        ),
        max_retries=(
            settings.defectdojo_tool_max_retries
            if max_retries is None
            else max_retries
        ),
        backoff_base=(
            settings.defectdojo_tool_retry_backoff_base
            if backoff_base is None
            else backoff_base
        ),
        backoff_max=(
            settings.defectdojo_tool_retry_backoff_max
            if backoff_max is None
            else backoff_max
        ),
        circuit_breaker_enabled=(
            settings.defectdojo_tool_circuit_breaker_enabled
            if circuit_breaker_enabled is None
            else circuit_breaker_enabled
        ),
        circuit_breaker_threshold=(
            settings.defectdojo_tool_circuit_breaker_threshold
            if circuit_breaker_threshold is None
            else circuit_breaker_threshold
        ),
        circuit_breaker_recovery=(
            settings.defectdojo_tool_circuit_breaker_recovery
            if circuit_breaker_recovery is None
            else circuit_breaker_recovery
        ),
    )


# ── operation-type presets ─────────────────────────────────────────────

TOOL_TIMEOUTS: dict[str, TimeoutConfig] = {
    # Read-only DefectDojo queries — fast fail, 2 retries
    "defectdojo_read": _timeout_config(
        connect_timeout=settings.defectdojo_tool_connect_timeout,
        read_timeout=settings.defectdojo_tool_read_timeout,
        max_retries=2,
    ),
    # Write operations (update, verify, risk-acceptance) — 1 retry only
    # (mutation semantics — retry with caution)
    "defectdojo_write": _timeout_config(
        connect_timeout=settings.defectdojo_tool_connect_timeout,
        read_timeout=settings.defectdojo_tool_write_timeout,
        write_timeout=settings.defectdojo_tool_write_timeout,
        max_retries=1,
    ),
    # Import scan — large file upload, no retry (non-idempotent + expensive)
    "defectdojo_import": _timeout_config(
        connect_timeout=settings.defectdojo_tool_connect_timeout,
        read_timeout=settings.defectdojo_tool_import_timeout,
        write_timeout=settings.defectdojo_tool_import_timeout,
        max_retries=0,
    ),
    # Qdrant vector DB — moderate timeout, 2 retries
    "qdrant": _timeout_config(
        connect_timeout=settings.qdrant_tool_connect_timeout,
        read_timeout=settings.qdrant_timeout_seconds,
        max_retries=2,
        backoff_base=0.5,
    ),
    # PostgreSQL / chat database — fast timeout, 2 retries
    "database": _timeout_config(
        connect_timeout=5.0,
        read_timeout=settings.chat_database_timeout_seconds,
        max_retries=2,
        backoff_base=0.5,
    ),
}

# ── agent-level wall-clock timeout presets ─────────────────────────────
# Each agent type gets a different budget because their workloads differ:
#   router:   classification + parameter extraction (cheap)
#   triage / dedup / remediation / verification / query_findings: moderate
#   import_scan: file upload, can take minutes
#   risk_acceptance: pre-review + approval gating

AGENT_TIMEOUTS: dict[str, TimeoutConfig] = {
    "router": TimeoutConfig(
        agent_timeout=settings.agent_router_timeout,
    ),
    "triage": TimeoutConfig(
        agent_timeout=settings.agent_default_timeout,
    ),
    "deduplication": TimeoutConfig(
        agent_timeout=settings.agent_default_timeout,
    ),
    "remediation": TimeoutConfig(
        agent_timeout=settings.agent_default_timeout,
    ),
    "verification": TimeoutConfig(
        agent_timeout=settings.agent_default_timeout,
    ),
    "query_findings": TimeoutConfig(
        agent_timeout=settings.agent_default_timeout,
    ),
    "import_scan": TimeoutConfig(
        agent_timeout=settings.agent_import_timeout,
    ),
    "risk_acceptance": TimeoutConfig(
        agent_timeout=settings.agent_risk_acceptance_timeout,
    ),
}


def get_timeout_config(name: str) -> TimeoutConfig:
    """Return the ``TimeoutConfig`` preset for *name*.

    Raises ``KeyError`` when the name is not registered so callers notice
    misconfiguration at development time rather than silently falling back.
    """
    return TOOL_TIMEOUTS[name]
