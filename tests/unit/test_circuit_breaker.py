"""Testes unitários: CircuitBreaker — transições de estado."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.domain.services.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        assert cb.state == CircuitState.CLOSED

    def test_no_fallback_when_closed(self):
        cb = CircuitBreaker()
        assert cb.should_use_fallback() is False

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_fallback_when_open(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        assert cb.should_use_fallback() is True

    def test_success_resets_to_closed(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.should_use_fallback() is False

    def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=1.0)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Simula passagem do tempo além do timeout
        future_time = time.monotonic() + 2.0
        with patch("app.domain.services.circuit_breaker.time.monotonic", return_value=future_time):
            assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_allows_test_call(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=1.0)
        cb.record_failure()

        future_time = time.monotonic() + 2.0
        with patch("app.domain.services.circuit_breaker.time.monotonic", return_value=future_time):
            assert cb.state == CircuitState.HALF_OPEN
            assert cb.should_use_fallback() is False  # permite uma chamada de teste

    def test_success_in_half_open_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=1.0)
        cb.record_failure()

        future_time = time.monotonic() + 2.0
        with patch("app.domain.services.circuit_breaker.time.monotonic", return_value=future_time):
            cb.state  # trigger HALF_OPEN
            cb.record_success()

        assert cb.state == CircuitState.CLOSED

    def test_failure_count_resets_on_success(self):
        cb = CircuitBreaker(failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        # Após success, contador zerou — 1 falha não deve abrir
        assert cb.state == CircuitState.CLOSED

    def test_no_fallback_with_zero_failures(self):
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.should_use_fallback() is False

    def test_custom_thresholds(self):
        cb = CircuitBreaker(failure_threshold=10, recovery_timeout=120.0)
        for _ in range(9):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
