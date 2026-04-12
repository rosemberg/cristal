"""Unit tests — SyntheticQueryGeneratorService (Fase 1 NOVO_RAG).

TDD RED → GREEN para o serviço de geração de perguntas sintéticas.

Cobre:
- _parse_llm_response: JSON válido, markdown fences, fallback regex, JSON inválido
- _call_llm: chamada ao gateway com prompt correto, temperatura 0.7
- _save_questions: persistência em batch e retorno de IDs
- _generate_embeddings: batch de embeddings + EmbeddingRecord correto
- generate_for_pending_chunks: fluxo completo, pula cobertos, processa pendentes
- regenerate_for_chunk: deleta existentes e regera
- get_status: delega ao repo
- Tratamento de erros: LLM falha → conta erro, continua; JSON malformado → fallback regex
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.services.synthetic_query_generator import (
    SyntheticQueryGeneratorService,
    _parse_llm_response,
)
from app.domain.value_objects.synthetic_query import GenerationResult, SyntheticQuery


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_service(
    llm=None,
    embedding_gw=None,
    sq_repo=None,
    emb_repo=None,
    pending_chunks=None,
    single_chunk=None,
):
    """Cria instância de SyntheticQueryGeneratorService com mocks."""
    llm = llm or AsyncMock()
    embedding_gw = embedding_gw or AsyncMock()
    sq_repo = sq_repo or AsyncMock()
    emb_repo = emb_repo or AsyncMock()

    svc = SyntheticQueryGeneratorService(
        llm_gateway=llm,
        embedding_gateway=embedding_gw,
        synthetic_query_repo=sq_repo,
        embedding_repo=emb_repo,
        model_name="gemini-test",
        llm_batch_size=2,
    )

    # Sobrescreve métodos de acesso a banco com mocks
    svc._fetch_pending_from_db = AsyncMock(return_value=pending_chunks or [])
    svc._fetch_single_chunk = AsyncMock(return_value=single_chunk)

    return svc, llm, embedding_gw, sq_repo, emb_repo


def make_llm_response(items: list[dict]) -> str:
    """Gera resposta JSON simulada do LLM."""
    return json.dumps(items, ensure_ascii=False)


# ── _parse_llm_response ───────────────────────────────────────────────────────


class TestParseLlmResponse:
    def test_valid_json(self):
        raw = '[{"chunk_id": 1, "questions": ["Pergunta A?", "Pergunta B?"]}]'
        result = _parse_llm_response(raw)
        assert result == [{"chunk_id": 1, "questions": ["Pergunta A?", "Pergunta B?"]}]

    def test_markdown_code_fence_json(self):
        raw = '```json\n[{"chunk_id": 2, "questions": ["X?"]}]\n```'
        result = _parse_llm_response(raw)
        assert result == [{"chunk_id": 2, "questions": ["X?"]}]

    def test_markdown_code_fence_no_lang(self):
        raw = '```\n[{"chunk_id": 3, "questions": ["Y?"]}]\n```'
        result = _parse_llm_response(raw)
        assert result == [{"chunk_id": 3, "questions": ["Y?"]}]

    def test_regex_fallback_on_invalid_json(self):
        # JSON com vírgula extra no final (inválido)
        raw = '[{"chunk_id": 4, "questions": ["Q1?", "Q2?"]},]'
        result = _parse_llm_response(raw)
        # Regex deve extrair pelo menos um item
        assert any(item.get("chunk_id") == 4 for item in result)

    def test_completely_invalid_returns_empty(self):
        raw = "Desculpe, não consegui gerar perguntas."
        result = _parse_llm_response(raw)
        assert result == []

    def test_multiple_chunks(self):
        raw = make_llm_response([
            {"chunk_id": 1, "questions": ["Q1?"]},
            {"chunk_id": 2, "questions": ["Q2?", "Q3?"]},
        ])
        result = _parse_llm_response(raw)
        assert len(result) == 2
        assert result[0]["chunk_id"] == 1
        assert result[1]["chunk_id"] == 2


# ── _call_llm ─────────────────────────────────────────────────────────────────


class TestCallLlm:
    @pytest.mark.asyncio
    async def test_call_llm_uses_correct_temperature(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value='[{"chunk_id": 1, "questions": ["Pergunta?"]}]'
        )
        svc, _, _, _, _ = make_service(llm=llm)

        chunks = [{"id": 1, "text": "Texto do chunk"}]
        result = await svc._call_llm(chunks)

        assert 1 in result
        assert "Pergunta?" in result[1]
        # Verifica temperatura 0.7 (criatividade)
        call_kwargs = llm.generate.call_args
        assert call_kwargs.kwargs.get("temperature") == 0.7

    @pytest.mark.asyncio
    async def test_call_llm_returns_empty_map_on_empty_response(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="[]")
        svc, _, _, _, _ = make_service(llm=llm)

        result = await svc._call_llm([{"id": 1, "text": "Texto"}])
        assert result == {}

    @pytest.mark.asyncio
    async def test_call_llm_filters_empty_questions(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value='[{"chunk_id": 1, "questions": ["Boa?", "", "   "]}]'
        )
        svc, _, _, _, _ = make_service(llm=llm)

        result = await svc._call_llm([{"id": 1, "text": "Texto"}])
        assert result[1] == ["Boa?"]


# ── _save_questions ───────────────────────────────────────────────────────────


class TestSaveQuestions:
    @pytest.mark.asyncio
    async def test_save_questions_persists_all_and_returns_ids(self):
        sq_repo = AsyncMock()
        sq_repo.save_batch = AsyncMock(return_value=[10, 11, 12])

        svc, _, _, _, _ = make_service(sq_repo=sq_repo)

        questions_map = {1: ["Q1?", "Q2?"], 2: ["Q3?"]}
        saved_ids = await svc._save_questions(questions_map, "page_chunk")

        saved_queries = sq_repo.save_batch.call_args[0][0]
        assert len(saved_queries) == 3
        assert all(q.source_type == "page_chunk" for q in saved_queries)
        assert saved_queries[0].source_id == 1
        assert saved_queries[2].source_id == 2

        # IDs devem ser distribuídos corretamente
        assert saved_ids[1] == [10, 11]
        assert saved_ids[2] == [12]

    @pytest.mark.asyncio
    async def test_save_questions_empty_map_returns_empty(self):
        sq_repo = AsyncMock()
        svc, _, _, _, _ = make_service(sq_repo=sq_repo)

        result = await svc._save_questions({}, "page_chunk")
        assert result == {}
        sq_repo.save_batch.assert_not_called()


# ── _generate_embeddings ──────────────────────────────────────────────────────


class TestGenerateEmbeddings:
    @pytest.mark.asyncio
    async def test_generate_embeddings_calls_embed_batch(self):
        embedding_gw = AsyncMock()
        embedding_gw.embed_batch = AsyncMock(
            return_value=[[0.1, 0.2], [0.3, 0.4]]
        )
        emb_repo = AsyncMock()
        svc, _, _, _, _ = make_service(embedding_gw=embedding_gw, emb_repo=emb_repo)

        questions_map = {1: ["Q1?", "Q2?"]}
        saved_ids = {1: [100, 101]}

        count = await svc._generate_embeddings(questions_map, saved_ids)

        assert count == 2
        emb_repo.save_batch.assert_called_once()
        records = emb_repo.save_batch.call_args[0][0]
        assert records[0].source_type == "synthetic_query"
        assert records[0].source_id == 100
        assert records[1].source_id == 101

    @pytest.mark.asyncio
    async def test_generate_embeddings_returns_zero_on_empty(self):
        svc, _, _, _, _ = make_service()
        count = await svc._generate_embeddings({}, {})
        assert count == 0


# ── generate_for_pending_chunks ───────────────────────────────────────────────


class TestGenerateForPendingChunks:
    @pytest.mark.asyncio
    async def test_skips_covered_and_processes_pending(self):
        sq_repo = AsyncMock()
        # page_chunk: 1 coberto, retorna 1 pendente
        sq_repo.get_covered_source_ids = AsyncMock(side_effect=[
            {1},   # page_chunk: id 1 já coberto
            set(), # chunk: nenhum coberto
        ])
        sq_repo.save_batch = AsyncMock(return_value=[10, 11])

        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value='[{"chunk_id": 2, "questions": ["P1?", "P2?"]}]'
        )

        embedding_gw = AsyncMock()
        embedding_gw.embed_batch = AsyncMock(return_value=[[0.1] * 768, [0.2] * 768])

        emb_repo = AsyncMock()

        svc, _, _, _, _ = make_service(
            llm=llm,
            embedding_gw=embedding_gw,
            sq_repo=sq_repo,
            emb_repo=emb_repo,
        )
        # page_chunk tem 1 pendente
        svc._fetch_pending_from_db = AsyncMock(side_effect=[
            [{"id": 2, "text": "Tabela de diárias 2024"}],  # page_chunk pendente
            [],  # chunk: nenhum pendente
        ])

        result = await svc.generate_for_pending_chunks(batch_size=50)

        assert result.chunks_processed == 1
        assert result.questions_generated == 2
        assert result.embeddings_created == 2
        assert result.errors == 0
        assert result.skipped == 1  # {1} coberto em page_chunk

    @pytest.mark.asyncio
    async def test_returns_empty_result_when_no_pending(self):
        sq_repo = AsyncMock()
        sq_repo.get_covered_source_ids = AsyncMock(return_value=set())

        svc, _, _, _, _ = make_service(sq_repo=sq_repo)
        svc._fetch_pending_from_db = AsyncMock(return_value=[])

        result = await svc.generate_for_pending_chunks()

        assert result.chunks_processed == 0
        assert result.questions_generated == 0

    @pytest.mark.asyncio
    async def test_filters_source_types_when_specified(self):
        sq_repo = AsyncMock()
        sq_repo.get_covered_source_ids = AsyncMock(return_value=set())

        svc, _, _, _, _ = make_service(sq_repo=sq_repo)
        svc._fetch_pending_from_db = AsyncMock(return_value=[])

        await svc.generate_for_pending_chunks(source_types=["page_chunk"])

        # Deve ter consultado apenas page_chunk
        assert sq_repo.get_covered_source_ids.call_count == 1
        sq_repo.get_covered_source_ids.assert_called_with("page_chunk")

    @pytest.mark.asyncio
    async def test_error_in_batch_increments_error_count(self):
        sq_repo = AsyncMock()
        sq_repo.get_covered_source_ids = AsyncMock(return_value=set())

        llm = AsyncMock()
        llm.generate = AsyncMock(side_effect=Exception("Vertex AI timeout"))

        svc, _, _, _, _ = make_service(llm=llm, sq_repo=sq_repo)
        svc._fetch_pending_from_db = AsyncMock(side_effect=[
            [{"id": 1, "text": "Chunk A"}, {"id": 2, "text": "Chunk B"}],
            [],
        ])

        result = await svc.generate_for_pending_chunks()

        # Ambos os chunks do batch falharam
        assert result.errors == 2
        assert result.chunks_processed == 0


# ── regenerate_for_chunk ──────────────────────────────────────────────────────


class TestRegenerateForChunk:
    @pytest.mark.asyncio
    async def test_regenerate_deletes_existing_and_generates_new(self):
        sq_repo = AsyncMock()
        sq_repo.get_covered_source_ids = AsyncMock(return_value={42})
        sq_repo.delete_by_source = AsyncMock()
        sq_repo.save_batch = AsyncMock(return_value=[200, 201, 202])

        emb_repo = AsyncMock()
        emb_repo.delete_by_source = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value='[{"chunk_id": 42, "questions": ["P1?", "P2?", "P3?"]}]'
        )

        embedding_gw = AsyncMock()
        embedding_gw.embed_batch = AsyncMock(
            return_value=[[0.1] * 768, [0.2] * 768, [0.3] * 768]
        )

        svc, _, _, _, _ = make_service(
            llm=llm,
            embedding_gw=embedding_gw,
            sq_repo=sq_repo,
            emb_repo=emb_repo,
            single_chunk={"id": 42, "text": "Texto do chunk 42"},
        )

        count = await svc.regenerate_for_chunk("page_chunk", 42)

        assert count == 3
        sq_repo.delete_by_source.assert_called_once_with("page_chunk", 42)
        emb_repo.delete_by_source.assert_called_once_with(42, "synthetic_query")

    @pytest.mark.asyncio
    async def test_regenerate_chunk_not_found_returns_zero(self):
        sq_repo = AsyncMock()
        sq_repo.get_covered_source_ids = AsyncMock(return_value=set())

        svc, _, _, _, _ = make_service(sq_repo=sq_repo, single_chunk=None)

        count = await svc.regenerate_for_chunk("page_chunk", 999)

        assert count == 0

    @pytest.mark.asyncio
    async def test_regenerate_skips_delete_if_not_covered(self):
        sq_repo = AsyncMock()
        sq_repo.get_covered_source_ids = AsyncMock(return_value=set())  # 42 não está coberto
        sq_repo.save_batch = AsyncMock(return_value=[300])

        emb_repo = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value='[{"chunk_id": 42, "questions": ["P?"]}]'
        )
        embedding_gw = AsyncMock()
        embedding_gw.embed_batch = AsyncMock(return_value=[[0.1] * 768])

        svc, _, _, _, _ = make_service(
            llm=llm,
            embedding_gw=embedding_gw,
            sq_repo=sq_repo,
            emb_repo=emb_repo,
            single_chunk={"id": 42, "text": "Texto"},
        )

        count = await svc.regenerate_for_chunk("page_chunk", 42)

        assert count == 1
        sq_repo.delete_by_source.assert_not_called()
        emb_repo.delete_by_source.assert_not_called()


# ── get_status ────────────────────────────────────────────────────────────────


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_get_status_aggregates_counts(self):
        sq_repo = AsyncMock()
        sq_repo.get_status = AsyncMock(return_value={"page_chunk": 3000, "chunk": 500})

        svc, _, _, _, _ = make_service(sq_repo=sq_repo)
        info = await svc.get_status()

        assert info["total_questions"] == 3500
        assert info["questions_by_source_type"]["page_chunk"] == 3000

    @pytest.mark.asyncio
    async def test_get_status_empty(self):
        sq_repo = AsyncMock()
        sq_repo.get_status = AsyncMock(return_value={})

        svc, _, _, _, _ = make_service(sq_repo=sq_repo)
        info = await svc.get_status()

        assert info["total_questions"] == 0
