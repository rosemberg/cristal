"""Testes unitários — IngestionStats e IngestionStatus (Etapa 1 Pipeline V2).

TDD RED: escrito antes da implementação.
"""

from __future__ import annotations

import pytest

from app.domain.value_objects.ingestion import IngestionStats, IngestionStatus


class TestIngestionStats:
    def test_criacao_basica(self):
        stats = IngestionStats(
            total=10,
            processed=8,
            errors=1,
            skipped=1,
            duration_seconds=12.5,
            inconsistencies_found=3,
        )
        assert stats.total == 10
        assert stats.processed == 8
        assert stats.errors == 1
        assert stats.skipped == 1
        assert stats.duration_seconds == 12.5
        assert stats.inconsistencies_found == 3

    def test_imutavel(self):
        stats = IngestionStats(
            total=5,
            processed=5,
            errors=0,
            skipped=0,
            duration_seconds=1.0,
            inconsistencies_found=0,
        )
        with pytest.raises(AttributeError):
            stats.total = 99  # type: ignore[misc]

    def test_zero_inconsistencies(self):
        stats = IngestionStats(
            total=0,
            processed=0,
            errors=0,
            skipped=0,
            duration_seconds=0.0,
            inconsistencies_found=0,
        )
        assert stats.inconsistencies_found == 0

    def test_igualdade_por_valor(self):
        a = IngestionStats(
            total=3, processed=2, errors=1, skipped=0, duration_seconds=5.0, inconsistencies_found=1
        )
        b = IngestionStats(
            total=3, processed=2, errors=1, skipped=0, duration_seconds=5.0, inconsistencies_found=1
        )
        assert a == b

    def test_desigualdade(self):
        a = IngestionStats(
            total=3, processed=2, errors=1, skipped=0, duration_seconds=5.0, inconsistencies_found=1
        )
        b = IngestionStats(
            total=3, processed=2, errors=1, skipped=0, duration_seconds=5.0, inconsistencies_found=0
        )
        assert a != b


class TestIngestionStatus:
    def test_criacao_basica(self):
        status = IngestionStatus(
            pending=50,
            processing=2,
            done=70,
            error=5,
            total_chunks=1200,
            total_tables=45,
            open_inconsistencies=7,
        )
        assert status.pending == 50
        assert status.processing == 2
        assert status.done == 70
        assert status.error == 5
        assert status.total_chunks == 1200
        assert status.total_tables == 45
        assert status.open_inconsistencies == 7

    def test_imutavel(self):
        status = IngestionStatus(
            pending=0,
            processing=0,
            done=0,
            error=0,
            total_chunks=0,
            total_tables=0,
            open_inconsistencies=0,
        )
        with pytest.raises(AttributeError):
            status.pending = 99  # type: ignore[misc]

    def test_igualdade_por_valor(self):
        a = IngestionStatus(
            pending=1, processing=0, done=10, error=0,
            total_chunks=100, total_tables=5, open_inconsistencies=0
        )
        b = IngestionStatus(
            pending=1, processing=0, done=10, error=0,
            total_chunks=100, total_tables=5, open_inconsistencies=0
        )
        assert a == b

    def test_banco_vazio(self):
        status = IngestionStatus(
            pending=0, processing=0, done=0, error=0,
            total_chunks=0, total_tables=0, open_inconsistencies=0,
        )
        assert status.pending == 0
        assert status.open_inconsistencies == 0
