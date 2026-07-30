"""Resilience primitives: graded timeouts, exponential back-off retry, and
per-tool circuit breakers.

All primitives are synchronous and thread-safe.  The module is deliberately
agnostic to the HTTP library underneath — callers pass ``httpx``-specific
retryable exception tuples at the call site, while the circuit breaker
works with *any* exception type.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from enum import Enum, auto
from threading import Lock
from typing import Any, ParamSpec, TypeVar

LOGGER = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


# ── configuration ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class TimeoutConfig:
    """Per-operation-type resilience parameters.

    All time values are in **seconds**.  Every field can be overridden via
    environment variables so operators can tune without touching code.
    """

    connect_timeout: float = 10.0
    read_timeout: float = 60.0
    write_timeout: float = 60.0

    # How long an entire agent / Crew kickoff may run before being aborted.
    agent_timeout: float = 300.0

    max_retries: int = 2
    backoff_base: float = 1.0
    backoff_max: float = 30.0

    circuit_breaker_enabled: bool = True
    circuit_breaker_threshold: int = 3
    circuit_breaker_recovery: float = 60.0


# ── circuit breaker ────────────────────────────────────────────────────


class CircuitState(Enum):
    CLOSED = auto()       # normal — requests pass through
    OPEN = auto()         # failing — requests are rejected immediately
    HALF_OPEN = auto()    # probing — one trial request allowed


class CircuitBreakerOpenError(Exception):
    """Raised when a call is rejected because the circuit is OPEN."""


class CircuitBreaker:
    """Thread-safe circuit breaker with the classic three-state model.

    * **CLOSED**:  failures increment a counter; after *failure_threshold*
      consecutive failures the breaker trips to OPEN.
    * **OPEN**:    every call is rejected immediately with
      ``CircuitBreakerOpenError`` for *recovery_timeout* seconds.
    * **HALF_OPEN**: the first call after the recovery window is allowed
      through as a probe.  Success → CLOSED; failure → OPEN again.

    Usage::

        cb = CircuitBreaker("qdrant", threshold=3, recovery=60)
        try:
            result = cb.call(risky_function, arg1, arg2)
        except CircuitBreakerOpenError:
            # fast-fail path — don't even try
            ...
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = Lock()

    # -- public API -------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def call(self, func: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        """Execute *func* subject to the circuit breaker."""
        self._pre_call()
        try:
            result = func(*args, **kwargs)
        except Exception:
            self._on_failure()
            raise
        else:
            self._on_success()
            return result

    # -- internal ---------------------------------------------------------

    def _pre_call(self) -> None:
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    LOGGER.info(
                        "Circuit breaker %r: OPEN → HALF_OPEN (probing)",
                        self.name,
                    )
                    self._state = CircuitState.HALF_OPEN
                    return
                raise CircuitBreakerOpenError(
                    f"Circuit breaker {self.name!r} is OPEN; "
                    f"retry after {self.recovery_timeout:.0f}s"
                )
            # HALF_OPEN — allow the probe through
            return

    def _on_success(self) -> None:
        with self._lock:
            if self._state != CircuitState.CLOSED:
                LOGGER.info(
                    "Circuit breaker %r: %s → CLOSED (probe succeeded)",
                    self.name,
                    self._state.name,
                )
            self._state = CircuitState.CLOSED
            self._failure_count = 0

    def _on_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if (
                self._state == CircuitState.HALF_OPEN
                or self._failure_count >= self.failure_threshold
            ):
                if self._state != CircuitState.OPEN:
                    LOGGER.warning(
                        "Circuit breaker %r: %s → OPEN (%d consecutive failures)",
                        self.name,
                        self._state.name,
                        self._failure_count,
                    )
                self._state = CircuitState.OPEN


# ── module-level breaker registry ──────────────────────────────────────

_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKERS_LOCK = Lock()


def circuit_breaker(name: str, *, config: TimeoutConfig | None = None) -> CircuitBreaker:
    """Return (or create) a module-level ``CircuitBreaker`` singleton for *name*."""
    with _BREAKERS_LOCK:
        if name not in _BREAKERS:
            threshold = 3
            recovery = 60.0
            if config is not None:
                threshold = config.circuit_breaker_threshold
                recovery = config.circuit_breaker_recovery
            _BREAKERS[name] = CircuitBreaker(
                name=name,
                failure_threshold=threshold,
                recovery_timeout=recovery,
            )
        return _BREAKERS[name]


# ── retry with exponential back-off + jitter ───────────────────────────


def with_retry(
    func: Callable[P, R],
    *args: P.args,
    config: TimeoutConfig,
    retryable: tuple[type[Exception], ...] | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    **kwargs: P.kwargs,
) -> R:
    """Call *func* with exponential back-off retry.

    Parameters
    ----------
    func:
        The callable to wrap.
    config:
        Resilience parameters (max_retries, backoff_base, backoff_max).
    retryable:
        Tuple of exception types that should trigger a retry.  If *None*,
        **all** exceptions are treated as retryable (the caller should
        normally pass a specific tuple like ``(httpx.TimeoutException,)``).
    on_retry:
        Optional callback invoked as ``on_retry(attempt_number, exception)``
        before each sleep.
    """
    delay: float = config.backoff_base
    for attempt in range(config.max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            is_retryable = (
                isinstance(exc, retryable) if retryable is not None else True
            )
            if attempt == config.max_retries or not is_retryable:
                raise

            jitter = random.uniform(0, delay * 0.5)
            total_delay = delay + jitter
            LOGGER.warning(
                "Retry %d/%d for %s after %.1fs: %s",
                attempt + 1,
                config.max_retries,
                getattr(func, "__name__", func.__class__.__name__),
                total_delay,
                exc,
            )
            if on_retry is not None:
                on_retry(attempt + 1, exc)
            time.sleep(total_delay)
            delay = min(delay * 2, config.backoff_max)

    # Unreachable — the loop always returns or raises.
    raise RuntimeError("unreachable")  # pragma: no cover


# ── combined resilience helper ─────────────────────────────────────────


def execute_with_resilience(
    tool_name: str,
    config: TimeoutConfig,
    func: Callable[P, R],
    *args: P.args,
    retryable: tuple[type[Exception], ...] | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    **kwargs: P.kwargs,
) -> R:
    """One-stop helper: circuit breaker → retry with back-off.

    If *config.circuit_breaker_enabled* is ``False`` the circuit breaker
    is skipped and only the retry logic applies.
    """
    if config.circuit_breaker_enabled:
        cb = circuit_breaker(tool_name, config=config)
        return cb.call(
            _retry_wrapper,
            func,
            *args,
            config=config,
            retryable=retryable,
            on_retry=on_retry,
            **kwargs,
        )
    return with_retry(
        func, *args, config=config, retryable=retryable, on_retry=on_retry, **kwargs
    )


def _retry_wrapper(
    func: Callable[P, R],
    *args: P.args,
    config: TimeoutConfig,
    retryable: tuple[type[Exception], ...] | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    **kwargs: P.kwargs,
) -> R:
    """Thin wrapper so ``CircuitBreaker.call`` sees retry as one atomic unit."""
    return with_retry(
        func, *args, config=config, retryable=retryable, on_retry=on_retry, **kwargs
    )


# ── agent-level wall-clock timeout ─────────────────────────────────────


class AgentTimeoutError(TimeoutError):
    """Raised when a CrewAI agent execution exceeds its time budget."""


def with_timeout(
    seconds: float,
    func: Callable[P, R],
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    """Run *func* in a thread and abort if it exceeds *seconds*.

    Returns the function result on success.  Raises ``AgentTimeoutError``
    when the deadline is exceeded.

    .. warning::

        Python cannot forcibly kill a thread.  If *func* is stuck inside
        an uninterruptible C extension or blocking I/O, the thread **will
        keep running** even after this function returns.  The executor
        uses daemon threads so the process can still exit.
    """
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-timeout") as pool:
        future = pool.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=seconds)
        except FutureTimeoutError:
            LOGGER.error(
                "Agent execution exceeded %ds deadline for %s",
                seconds,
                getattr(func, "__name__", func.__class__.__name__),
            )
            raise AgentTimeoutError(
                f"Agent execution timed out after {seconds:.0f}s"
            ) from None


def execute_agent_with_timeout(
    agent_name: str,
    config: TimeoutConfig,
    func: Callable[P, R],
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    """Convenience helper that applies wall-clock timeout from *config*.

    When ``config.agent_timeout <= 0`` the timeout guard is skipped
    entirely so that operators can disable it via environment variable.
    """
    if config.agent_timeout <= 0:
        return func(*args, **kwargs)
    try:
        return with_timeout(config.agent_timeout, func, *args, **kwargs)
    except AgentTimeoutError:
        LOGGER.warning(
            "Agent %r timed out after %.0fs",
            agent_name,
            config.agent_timeout,
        )
        raise
