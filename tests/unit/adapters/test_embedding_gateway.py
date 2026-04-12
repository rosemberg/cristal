"""Unit tests — VertexEmbeddingGateway (Fase 2b).

TDD RED → GREEN para o adapter de embeddings com:
- Port ABC (EmbeddingGateway + EmbeddingUnavailableError)
- Happy path: embed_text, embed_batch
- LRU cache (queries cacheadas, documentos não)
- Circuit breaker (abre após N falhas, rejeita chamadas, reseta após timeout)
- Exponential backoff (retries com sleep crescente)
- Batching automático (chunks de max_batch_size)
- Observability: is_available, cache_size

Mocks:
- vertexai.init — evita inicialização real do SDK
- TextEmbeddingModel.from_pretrained + get_embeddings — evita chamada à API
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.domain.ports.outbound.embedding_gateway import (
    EmbeddingGateway,
    EmbeddingUnavailableError,
)

# ── Constantes ────────────────────────────────────────────────────────────────

PROJECT = "test-project"
LOCATION = "us-central1"
MODEL = "text-embedding-005"
FAKE_EMBEDDING = [0.1] * 768
FAKE_EMBEDDING_2 = [0.2] * 768


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_mock_vertex_module():
    """Cria mocks para vertexai e TextEmbeddingModel."""
    mock_vertexai = MagicMock()
    mock_model_cls = MagicMock()
    mock_model_instance = MagicMock()
    mock_model_cls.from_pretrained.return_value = mock_model_instance
    return mock_vertexai, mock_model_cls, mock_model_instance


def make_embedding_result(values: list[float]) -> MagicMock:
    e = MagicMock()
    e.values = values
    return e


def make_gateway(
    circuit_breaker_threshold: int = 3,
    circuit_breaker_timeout: float = 60.0,
    cache_max_size: int = 10,
    max_batch_size: int = 100,
    max_retries: int = 3,
):
    """Instancia VertexEmbeddingGateway com vertexai.init mockado.

    Com import lazy no gateway, o alvo correto é ``vertexai.init`` em vez do
    atributo de módulo ``embedding_gateway.vertexai`` (que não existe mais).
    """
    from app.adapters.outbound.vertex_ai.embedding_gateway import VertexEmbeddingGateway

    with patch("vertexai.init"):
        gw = VertexEmbeddingGateway(
            project_id=PROJECT,
            location=LOCATION,
            model_name=MODEL,
            circuit_breaker_threshold=circuit_breaker_threshold,
            circuit_breaker_timeout=circuit_breaker_timeout,
            cache_max_size=cache_max_size,
            max_batch_size=max_batch_size,
            max_retries=max_retries,
        )
    return gw


# ── Port ABC ──────────────────────────────────────────────────────────────────


class TestEmbeddingGatewayPort:
    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            EmbeddingGateway()  # type: ignore[abstract]

    def test_embedding_unavailable_error_is_exception(self) -> None:
        err = EmbeddingUnavailableError("circuit open")
        assert isinstance(err, Exception)
        assert "circuit open" in str(err)

    def test_port_has_embed_text_method(self) -> None:
        assert hasattr(EmbeddingGateway, "embed_text")

    def test_port_has_embed_batch_method(self) -> None:
        assert hasattr(EmbeddingGateway, "embed_batch")


# ── Init ──────────────────────────────────────────────────────────────────────


class TestVertexEmbeddingGatewayInit:
    def test_init_calls_vertexai_init(self) -> None:
        from app.adapters.outbound.vertex_ai.embedding_gateway import VertexEmbeddingGateway
        with patch("vertexai.init") as mock_init:
            VertexEmbeddingGateway(project_id=PROJECT, location=LOCATION)
            mock_init.assert_called_once_with(project=PROJECT, location=LOCATION)

    def test_init_stores_model_name(self) -> None:
        gw = make_gateway()
        assert gw._model_name == MODEL

    def test_init_circuit_breaker_starts_closed(self) -> None:
        gw = make_gateway()
        assert gw.is_available is True
        assert gw._consecutive_failures == 0
        assert gw._open_until == 0.0

    def test_init_cache_starts_empty(self) -> None:
        gw = make_gateway()
        assert gw.cache_size == 0


# ── embed_text — happy path ───────────────────────────────────────────────────


class TestEmbedText:
    async def test_embed_text_returns_vector(self) -> None:
        gw = make_gateway()
        mock_result = [make_embedding_result(FAKE_EMBEDDING)]

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.return_value = mock_result
            result = await gw.embed_text("quanto foi gasto com diárias")

        assert result == FAKE_EMBEDDING

    async def test_embed_text_uses_retrieval_query_by_default(self) -> None:
        gw = make_gateway()
        mock_result = [make_embedding_result(FAKE_EMBEDDING)]

        with patch(
            "vertexai.language_models.TextEmbeddingInput"
        ) as mock_input_cls, patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.return_value = mock_result
            await gw.embed_text("query de teste")
            mock_input_cls.assert_called_once_with("query de teste", task_type="RETRIEVAL_QUERY")

    async def test_embed_text_with_retrieval_document_task_type(self) -> None:
        gw = make_gateway()
        mock_result = [make_embedding_result(FAKE_EMBEDDING)]

        with patch(
            "vertexai.language_models.TextEmbeddingInput"
        ) as mock_input_cls, patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.return_value = mock_result
            await gw.embed_text("texto do documento", task_type="RETRIEVAL_DOCUMENT")
            mock_input_cls.assert_called_once_with(
                "texto do documento", task_type="RETRIEVAL_DOCUMENT"
            )

    async def test_embed_text_returns_list_of_floats(self) -> None:
        gw = make_gateway()
        embedding_values = [0.01 * i for i in range(768)]
        mock_result = [make_embedding_result(embedding_values)]

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.return_value = mock_result
            result = await gw.embed_text("texto qualquer")

        assert len(result) == 768
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.01)


# ── embed_batch — happy path ──────────────────────────────────────────────────


class TestEmbedBatch:
    async def test_embed_batch_empty_returns_empty_list(self) -> None:
        gw = make_gateway()
        result = await gw.embed_batch([])
        assert result == []

    async def test_embed_batch_single_text(self) -> None:
        gw = make_gateway()
        mock_result = [make_embedding_result(FAKE_EMBEDDING)]

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.return_value = mock_result
            result = await gw.embed_batch(["documento único"])

        assert result == [FAKE_EMBEDDING]

    async def test_embed_batch_multiple_texts(self) -> None:
        gw = make_gateway()
        mock_results = [
            make_embedding_result(FAKE_EMBEDDING),
            make_embedding_result(FAKE_EMBEDDING_2),
        ]

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.return_value = mock_results
            result = await gw.embed_batch(["doc 1", "doc 2"])

        assert len(result) == 2
        assert result[0] == FAKE_EMBEDDING
        assert result[1] == FAKE_EMBEDDING_2

    async def test_embed_batch_uses_retrieval_document_by_default(self) -> None:
        gw = make_gateway()
        mock_result = [make_embedding_result(FAKE_EMBEDDING)]

        with patch(
            "vertexai.language_models.TextEmbeddingInput"
        ) as mock_input_cls, patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.return_value = mock_result
            await gw.embed_batch(["texto"])
            mock_input_cls.assert_called_once_with("texto", task_type="RETRIEVAL_DOCUMENT")

    async def test_embed_batch_splits_into_chunks_of_max_batch_size(self) -> None:
        """5 textos com max_batch_size=2 deve resultar em 3 chamadas à API."""
        gw = make_gateway(max_batch_size=2, max_retries=1)
        texts = ["t1", "t2", "t3", "t4", "t5"]

        # Cada chamada retorna N embeddings (N = tamanho do batch)
        def side_effect(inputs):
            return [make_embedding_result(FAKE_EMBEDDING) for _ in inputs]

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = side_effect
            result = await gw.embed_batch(texts)

        assert len(result) == 5
        assert mock_cls.from_pretrained.return_value.get_embeddings.call_count == 3

    async def test_embed_batch_order_preserved_across_batches(self) -> None:
        """Ordem dos embeddings deve corresponder à ordem dos textos."""
        gw = make_gateway(max_batch_size=2, max_retries=1)
        embeddings_per_text = {
            "a": [1.0] * 768,
            "b": [2.0] * 768,
            "c": [3.0] * 768,
        }
        texts = ["a", "b", "c"]

        def side_effect(inputs):
            # inputs são TextEmbeddingInput; aqui são mocks com __init__ args
            results = []
            for inp in inputs:
                text = inp.text if hasattr(inp, "text") else str(inp)
                results.append(make_embedding_result(embeddings_per_text.get(text, FAKE_EMBEDDING)))
            return results

        with patch(
            "vertexai.language_models.TextEmbeddingInput"
        ) as mock_input_cls, patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            # Simula TextEmbeddingInput armazenando o texto
            def make_input(text, task_type):
                m = MagicMock()
                m.text = text
                return m

            mock_input_cls.side_effect = make_input
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = side_effect
            result = await gw.embed_batch(texts)

        assert result[0] == [1.0] * 768
        assert result[1] == [2.0] * 768
        assert result[2] == [3.0] * 768


# ── LRU Cache ─────────────────────────────────────────────────────────────────


class TestLRUCache:
    async def test_query_result_is_cached_after_first_call(self) -> None:
        gw = make_gateway()
        mock_result = [make_embedding_result(FAKE_EMBEDDING)]

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.return_value = mock_result
            r1 = await gw.embed_text("query repetida")
            r2 = await gw.embed_text("query repetida")

        # Segunda chamada deve usar o cache — API chamada apenas 1x
        assert mock_cls.from_pretrained.return_value.get_embeddings.call_count == 1
        assert r1 == r2

    async def test_document_embedding_is_not_cached(self) -> None:
        gw = make_gateway()
        mock_result = [make_embedding_result(FAKE_EMBEDDING)]

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.return_value = mock_result
            await gw.embed_text("documento", task_type="RETRIEVAL_DOCUMENT")
            await gw.embed_text("documento", task_type="RETRIEVAL_DOCUMENT")

        # Documentos não cacheados → 2 chamadas à API
        assert mock_cls.from_pretrained.return_value.get_embeddings.call_count == 2

    async def test_different_queries_have_separate_cache_entries(self) -> None:
        gw = make_gateway()
        results = [
            [make_embedding_result(FAKE_EMBEDDING)],
            [make_embedding_result(FAKE_EMBEDDING_2)],
        ]

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = results
            r1 = await gw.embed_text("query A")
            r2 = await gw.embed_text("query B")

        assert r1 == FAKE_EMBEDDING
        assert r2 == FAKE_EMBEDDING_2
        assert gw.cache_size == 2

    async def test_cache_evicts_lru_when_full(self) -> None:
        """Com cache_max_size=2, o terceiro item expulsa o primeiro."""
        gw = make_gateway(cache_max_size=2)

        embeddings = [
            [make_embedding_result([float(i)] * 768)]
            for i in range(3)
        ]

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = embeddings
            await gw.embed_text("q1")
            await gw.embed_text("q2")
            await gw.embed_text("q3")  # deve expulsar q1

        assert gw.cache_size == 2
        assert "q:q1" not in gw._cache
        assert "q:q2" in gw._cache
        assert "q:q3" in gw._cache

    async def test_cache_hit_updates_lru_order(self) -> None:
        """Acessar q1 depois de q2 mantém q1 como mais recente (não expulso primeiro)."""
        gw = make_gateway(cache_max_size=2)

        embs = [
            [make_embedding_result(FAKE_EMBEDDING)],
            [make_embedding_result(FAKE_EMBEDDING_2)],
            [make_embedding_result([0.9] * 768)],
        ]

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = embs
            await gw.embed_text("q1")  # cache: [q1]
            await gw.embed_text("q2")  # cache: [q1, q2]
            await gw.embed_text("q1")  # HIT — q1 vira mais recente; cache: [q2, q1]
            await gw.embed_text("q3")  # INSERT — expulsa q2 (LRU); cache: [q1, q3]

        assert "q:q2" not in gw._cache  # q2 foi expulso
        assert "q:q1" in gw._cache
        assert "q:q3" in gw._cache


# ── Circuit Breaker ───────────────────────────────────────────────────────────


class TestCircuitBreaker:
    async def test_circuit_opens_after_threshold_failures(self) -> None:
        """Após 3 falhas consecutivas, o circuit breaker abre."""
        gw = make_gateway(circuit_breaker_threshold=3, max_retries=1)

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = (
                Exception("API error")
            )
            for _ in range(3):
                with pytest.raises(Exception):
                    await gw.embed_text("q")

        assert gw.is_available is False
        assert gw._open_until > time.monotonic()

    async def test_circuit_open_raises_embedding_unavailable(self) -> None:
        """Após circuit aberto, novas chamadas levantam EmbeddingUnavailableError."""
        gw = make_gateway(circuit_breaker_threshold=3, max_retries=1)

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = (
                Exception("fail")
            )
            for _ in range(3):
                with pytest.raises(Exception):
                    await gw.embed_text("q")

        # A partir daqui, EmbeddingUnavailableError deve ser levantado
        with pytest.raises(EmbeddingUnavailableError, match="OPEN"):
            await gw.embed_text("q nova")

    async def test_circuit_open_also_blocks_embed_batch(self) -> None:
        gw = make_gateway(circuit_breaker_threshold=1, max_retries=1)

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = (
                Exception("fail")
            )
            with pytest.raises(Exception):
                await gw.embed_batch(["texto"])

        with pytest.raises(EmbeddingUnavailableError):
            await gw.embed_batch(["outro texto"])

    async def test_circuit_resets_after_timeout(self) -> None:
        """Após o timeout expirar, o circuit breaker fecha e permite nova tentativa."""
        gw = make_gateway(
            circuit_breaker_threshold=1,
            circuit_breaker_timeout=0.001,  # 1ms para facilitar teste
            max_retries=1,
        )

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = [
                Exception("fail"),
                [make_embedding_result(FAKE_EMBEDDING)],
            ]
            with pytest.raises(Exception):
                await gw.embed_text("q")

            # Aguarda timeout do circuit breaker (1ms)
            await asyncio.sleep(0.05)

            # Agora deve funcionar novamente (half-open → sucesso → closed)
            result = await gw.embed_text("q nova")

        assert result == FAKE_EMBEDDING
        assert gw.is_available is True

    async def test_successful_call_resets_failure_count(self) -> None:
        """2 falhas + 1 sucesso deve zerar o contador (não abre o circuit)."""
        gw = make_gateway(circuit_breaker_threshold=3, max_retries=1)

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = [
                Exception("fail 1"),
                Exception("fail 2"),
                [make_embedding_result(FAKE_EMBEDDING)],  # sucesso
            ]
            # 2 falhas
            for _ in range(2):
                with pytest.raises(Exception):
                    await gw.embed_text(f"q{_}")

            # 1 sucesso → reseta contador
            await gw.embed_text("q ok")

        assert gw._consecutive_failures == 0
        assert gw.is_available is True

    async def test_is_available_false_when_circuit_open(self) -> None:
        gw = make_gateway(circuit_breaker_threshold=1, max_retries=1)

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = (
                Exception("fail")
            )
            with pytest.raises(Exception):
                await gw.embed_text("q")

        assert gw.is_available is False

    async def test_is_available_true_when_circuit_closed(self) -> None:
        gw = make_gateway()
        assert gw.is_available is True


# ── Exponential Backoff ───────────────────────────────────────────────────────


class TestExponentialBackoff:
    async def test_retry_succeeds_on_second_attempt(self) -> None:
        """1 falha + 1 sucesso deve retornar o embedding sem levantar exceção."""
        gw = make_gateway(max_retries=3)

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls, patch(
            "app.adapters.outbound.vertex_ai.embedding_gateway.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = [
                Exception("transient error"),
                [make_embedding_result(FAKE_EMBEDDING)],
            ]
            result = await gw.embed_text("q")

        assert result == FAKE_EMBEDDING
        mock_sleep.assert_called_once()  # 1 sleep antes do retry

    async def test_sleep_uses_exponential_delay(self) -> None:
        """Primeiro retry deve dormir 0.5s, segundo 1.0s."""
        gw = make_gateway(max_retries=3)

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls, patch(
            "app.adapters.outbound.vertex_ai.embedding_gateway.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = [
                Exception("fail 1"),
                Exception("fail 2"),
                [make_embedding_result(FAKE_EMBEDDING)],
            ]
            result = await gw.embed_text("q")

        assert result == FAKE_EMBEDDING
        delays = [c.args[0] for c in mock_sleep.call_args_list]
        assert delays[0] == pytest.approx(0.5)   # 0.5 * 2^0
        assert delays[1] == pytest.approx(1.0)   # 0.5 * 2^1

    async def test_all_retries_exhausted_raises_original_exception(self) -> None:
        """Após esgotar todas as tentativas, deve propagar a exceção original."""
        gw = make_gateway(max_retries=2)

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls, patch(
            "app.adapters.outbound.vertex_ai.embedding_gateway.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = RuntimeError(
                "persistent error"
            )
            with pytest.raises(RuntimeError, match="persistent error"):
                await gw.embed_text("q")

    async def test_no_sleep_on_last_attempt(self) -> None:
        """Na última tentativa (sem mais retries), não deve chamar sleep."""
        gw = make_gateway(max_retries=1)  # zero retries — nenhum sleep

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls, patch(
            "app.adapters.outbound.vertex_ai.embedding_gateway.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            mock_cls.from_pretrained.return_value.get_embeddings.side_effect = (
                Exception("fail")
            )
            with pytest.raises(Exception):
                await gw.embed_text("q")

        mock_sleep.assert_not_called()


# ── Observability ─────────────────────────────────────────────────────────────


class TestObservability:
    def test_cache_size_reflects_cached_entries(self) -> None:
        gw = make_gateway(cache_max_size=100)
        assert gw.cache_size == 0
        gw._cache_put("q:a", FAKE_EMBEDDING)
        gw._cache_put("q:b", FAKE_EMBEDDING_2)
        assert gw.cache_size == 2

    async def test_cache_size_after_queries(self) -> None:
        gw = make_gateway()
        mock_result = [make_embedding_result(FAKE_EMBEDDING)]

        with patch(
            "vertexai.language_models.TextEmbeddingModel"
        ) as mock_cls:
            mock_cls.from_pretrained.return_value.get_embeddings.return_value = mock_result
            await gw.embed_text("query 1")
            await gw.embed_text("query 2")

        assert gw.cache_size == 2

    def test_is_available_initially_true(self) -> None:
        gw = make_gateway()
        assert gw.is_available is True
