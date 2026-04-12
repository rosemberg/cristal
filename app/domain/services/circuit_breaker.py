"""Domain service: CircuitBreaker — proteção para chamadas ao LLM com fallback.

Implementa o padrão Circuit Breaker com três estados:
- CLOSED: operação normal, chamadas permitidas
- OPEN: muitas falhas consecutivas, chamadas bloqueadas (usa fallback)
- HALF_OPEN: período de recuperação, uma chamada de teste permitida
"""

from __future__ import annotations

import time
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker para LLM gateway com fallback automático.

    Usage:
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        if cb.should_use_fallback():
            result = await fallback_llm.generate(...)
        else:
            try:
                result = await primary_llm.generate(...)
                cb.record_success()
            except Exception:
                cb.record_failure()
                raise
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._state = CircuitState.CLOSED

    @property
    def state(self) -> CircuitState:
        """Estado atual do circuit breaker."""
        if self._state == CircuitState.OPEN:
            # Verifica se já passou o timeout de recuperação
            if (
                self._last_failure_time is not None
                and time.monotonic() - self._last_failure_time >= self._recovery_timeout
            ):
                self._state = CircuitState.HALF_OPEN
        return self._state

    def record_success(self) -> None:
        """Registra chamada bem-sucedida — reseta o circuit breaker."""
        self._failure_count = 0
        self._last_failure_time = None
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Registra falha — pode abrir o circuit breaker."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN

    def should_use_fallback(self) -> bool:
        """Retorna True quando o circuito está aberto (deve usar fallback)."""
        current_state = self.state  # lê via property (atualiza OPEN→HALF_OPEN se necessário)
        if current_state == CircuitState.OPEN:
            return True
        if current_state == CircuitState.HALF_OPEN:
            # Permite uma chamada de teste — não usa fallback
            return False
        return False
