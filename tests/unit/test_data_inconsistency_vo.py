"""Testes unitários — DataInconsistency e HealthCheckReport (Etapa 1 Pipeline V2).

TDD RED: escrito antes da implementação.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.value_objects.data_inconsistency import DataInconsistency, HealthCheckReport


NOW = datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc)


class TestDataInconsistency:
    def _make(self, **kwargs) -> DataInconsistency:
        defaults = dict(
            id=1,
            resource_type="document",
            severity="warning",
            inconsistency_type="broken_link",
            resource_url="https://www.tre-pi.jus.br/doc/test.pdf",
            resource_title="Documento de Teste",
            parent_page_url="https://www.tre-pi.jus.br/transparencia",
            detail="Retornou 404",
            http_status=404,
            error_message=None,
            detected_at=NOW,
            detected_by="ingestion_pipeline",
            status="open",
            resolved_at=None,
            resolved_by=None,
            resolution_note=None,
            retry_count=0,
            last_checked_at=NOW,
        )
        defaults.update(kwargs)
        return DataInconsistency(**defaults)

    def test_criacao_completa(self):
        di = self._make()
        assert di.resource_type == "document"
        assert di.severity == "warning"
        assert di.inconsistency_type == "broken_link"
        assert di.http_status == 404
        assert di.status == "open"
        assert di.retry_count == 0
        assert di.id == 1

    def test_imutavel(self):
        di = self._make()
        with pytest.raises(AttributeError):
            di.status = "resolved"  # type: ignore[misc]

    def test_id_none_para_nao_persistido(self):
        di = self._make(id=None)
        assert di.id is None

    def test_campos_opcionais_none(self):
        di = self._make(
            resource_title=None,
            parent_page_url=None,
            http_status=None,
            error_message=None,
            resolved_at=None,
            resolved_by=None,
            resolution_note=None,
        )
        assert di.resource_title is None
        assert di.parent_page_url is None
        assert di.http_status is None

    def test_tipos_de_recurso_validos(self):
        for rtype in ("page", "document", "link", "chunk"):
            di = self._make(resource_type=rtype)
            assert di.resource_type == rtype

    def test_severidades_validas(self):
        for sev in ("critical", "warning", "info"):
            di = self._make(severity=sev)
            assert di.severity == sev

    def test_status_validos(self):
        for st in ("open", "acknowledged", "resolved", "ignored"):
            di = self._make(status=st)
            assert di.status == st

    def test_detected_by_validos(self):
        for det in ("ingestion_pipeline", "health_check", "crawler", "manual"):
            di = self._make(detected_by=det)
            assert di.detected_by == det

    def test_igualdade_por_valor(self):
        a = self._make()
        b = self._make()
        assert a == b

    def test_desigualdade_por_status(self):
        a = self._make(status="open")
        b = self._make(status="resolved")
        assert a != b

    def test_tipos_de_inconsistencia(self):
        tipos = [
            "broken_link",
            "page_not_accessible",
            "document_not_found",
            "document_corrupted",
            "empty_content",
            "encoding_error",
            "oversized",
            "orphan_chunks",
            "duplicate_content",
            "category_mismatch",
            "missing_metadata",
        ]
        for tipo in tipos:
            di = self._make(inconsistency_type=tipo)
            assert di.inconsistency_type == tipo

    def test_retry_count_positivo(self):
        di = self._make(retry_count=5)
        assert di.retry_count == 5


class TestHealthCheckReport:
    def _make(self, **kwargs) -> HealthCheckReport:
        defaults = dict(
            total_checked=100,
            healthy=90,
            issues_found=10,
            new_inconsistencies=7,
            updated_inconsistencies=3,
            auto_resolved=2,
            duration_seconds=45.2,
            by_type={"broken_link": 5, "page_not_accessible": 3, "empty_content": 2},
        )
        defaults.update(kwargs)
        return HealthCheckReport(**defaults)

    def test_criacao_basica(self):
        report = self._make()
        assert report.total_checked == 100
        assert report.healthy == 90
        assert report.issues_found == 10
        assert report.new_inconsistencies == 7
        assert report.updated_inconsistencies == 3
        assert report.auto_resolved == 2
        assert report.duration_seconds == 45.2
        assert report.by_type["broken_link"] == 5

    def test_imutavel(self):
        report = self._make()
        with pytest.raises(AttributeError):
            report.total_checked = 999  # type: ignore[misc]

    def test_by_type_vazio(self):
        report = self._make(
            total_checked=0, healthy=0, issues_found=0,
            new_inconsistencies=0, updated_inconsistencies=0,
            duration_seconds=0.1, by_type={}
        )
        assert report.by_type == {}

    def test_igualdade_por_valor(self):
        a = self._make()
        b = self._make()
        assert a == b

    def test_sem_problemas(self):
        report = self._make(
            total_checked=50, healthy=50, issues_found=0,
            new_inconsistencies=0, updated_inconsistencies=0,
            by_type={}
        )
        assert report.issues_found == 0
        assert report.healthy == 50
