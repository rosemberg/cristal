"""Vertex AI adapter — VertexEmbeddingGateway.

Implementa EmbeddingGateway usando o modelo `text-embedding-005` (768 dims)
via Vertex AI SDK (TextEmbeddingModel).

Recursos:
- **LRU cache** (OrderedDict): embeddings de queries são cacheados em memória
  para evitar chamadas repetidas à API (queries de busca costumam repetir).
- **Circuit breaker**: após `circuit_breaker_threshold` falhas consecutivas,
  levanta EmbeddingUnavailableError por `circuit_breaker_timeout` segundos.
  O chamador (HybridSearchService) faz fallback para FTS nesse período.
- **Exponential backoff**: retries com delay 0.5 × 2^attempt antes de contar
  como falha definitiva no circuit breaker.
- **Batching**: textos são enviados em lotes de até `max_batch_size` (padrão 100)
  para respeitar a quota do Vertex AI.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict

from app.domain.ports.outbound.embedding_gateway import (
    EmbeddingGateway,
    EmbeddingUnavailableError,
)

logger = logging.getLogger(__name__)

_RETRY_BASE_DELAY = 0.5  # segundos — multiplicado por 2^attempt

# Limite de tokens por chamada à API (text-embedding-005 suporta 20.000).
# Usamos 15.000 como margem segura. Estimativa: 1 token ≈ 4 caracteres.
_MAX_TOKENS_PER_CALL = 15_000
_CHARS_PER_TOKEN = 4


def _split_by_tokens(
    texts: list[str], max_tokens: int = _MAX_TOKENS_PER_CALL
) -> list[list[str]]:
    """Divide lista de textos em sub-lotes respeitando o limite de tokens.

    Estimativa simples: len(text) / 4 ≈ tokens. Cada texto entra sozinho
    no pior caso (texto individual > max_tokens é enviado assim mesmo —
    a API retornará erro 400 nesse caso extremo, capturado pelo retry).
    """
    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0

    for text in texts:
        estimated = max(1, len(text) // _CHARS_PER_TOKEN)
        if current and current_tokens + estimated > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(text)
        current_tokens += estimated

    if current:
        batches.append(current)

    return batches


class VertexEmbeddingGateway(EmbeddingGateway):
    """Adapter que conecta EmbeddingGateway ao Vertex AI text-embedding-005."""

    DIMENSIONS = 768
    DEFAULT_MAX_BATCH_SIZE = 100

    def __init__(
        self,
        project_id: str,
        location: str,
        model_name: str = "text-embedding-005",
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        cache_max_size: int = 256,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        """
        Args:
            project_id: GCP project ID.
            location: GCP region (e.g. "us-central1").
            model_name: Vertex AI embedding model (default: "text-embedding-005").
            max_batch_size: Máximo de textos por chamada à API (≤ 250 para quota segura).
            cache_max_size: Capacidade máxima do LRU cache de queries.
            circuit_breaker_threshold: Falhas consecutivas para abrir o circuit breaker.
            circuit_breaker_timeout: Segundos que o circuit breaker permanece aberto.
            max_retries: Tentativas com backoff antes de considerar como falha.
        """
        import vertexai  # noqa: PLC0415 — lazy import para reduzir memória em testes
        vertexai.init(project=project_id, location=location)
        self._model_name = model_name
        self._max_batch_size = max_batch_size
        self._max_retries = max_retries

        # LRU cache: OrderedDict mantém ordem de inserção; ao atingir limite,
        # remove o item mais antigo (LRU por aproximação de FIFO sem acesso recente).
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_max_size = cache_max_size

        # Circuit breaker state
        self._circuit_breaker_threshold = circuit_breaker_threshold
        self._circuit_breaker_timeout = circuit_breaker_timeout
        self._consecutive_failures = 0
        self._open_until: float = 0.0  # monotonic timestamp

    # ── Cache helpers ────────────────────────────────────────────────────────

    def _cache_get(self, key: str) -> list[float] | None:
        """Busca no cache LRU, movendo o item para o fim (mais recente)."""
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def _cache_put(self, key: str, value: list[float]) -> None:
        """Insere no cache LRU, evictando o item mais antigo se necessário."""
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._cache_max_size:
                self._cache.popitem(last=False)  # remove LRU (primeiro)
        self._cache[key] = value

    # ── Circuit breaker helpers ──────────────────────────────────────────────

    def _assert_circuit_closed(self) -> None:
        """Levanta EmbeddingUnavailableError se o circuit breaker estiver aberto."""
        now = time.monotonic()
        if now < self._open_until:
            remaining = round(self._open_until - now, 1)
            raise EmbeddingUnavailableError(
                f"Circuit breaker OPEN — serviço de embeddings indisponível por "
                f"mais {remaining}s. Usando apenas FTS."
            )
        # Timeout expirou → half-open: zeramos para permitir nova tentativa
        if self._open_until > 0:
            logger.info("EmbeddingGateway: circuit breaker half-open — tentando reconectar")
            self._open_until = 0.0

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._open_until = 0.0

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._circuit_breaker_threshold:
            self._open_until = time.monotonic() + self._circuit_breaker_timeout
            logger.warning(
                "EmbeddingGateway: circuit breaker ABERTO após %d falhas consecutivas. "
                "Busca semântica desabilitada por %.0fs.",
                self._consecutive_failures,
                self._circuit_breaker_timeout,
            )

    # ── Vertex AI call (síncrono, executado em thread) ───────────────────────

    def _call_vertex_sync(self, texts: list[str], task_type: str) -> list[list[float]]:
        """Chamada síncrona ao Vertex AI SDK — executa em asyncio.to_thread."""
        from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel  # noqa: PLC0415
        model = TextEmbeddingModel.from_pretrained(self._model_name)
        inputs = [TextEmbeddingInput(text, task_type=task_type) for text in texts]
        embeddings = model.get_embeddings(inputs)
        return [list(e.values) for e in embeddings]

    # ── Retry com exponential backoff ────────────────────────────────────────

    async def _embed_with_retry(
        self, texts: list[str], task_type: str
    ) -> list[list[float]]:
        """Executa a chamada ao Vertex AI com retries e exponential backoff."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                result = await asyncio.to_thread(
                    self._call_vertex_sync, texts, task_type
                )
                self._record_success()
                return result
            except Exception as exc:
                last_exc = exc
                delay = _RETRY_BASE_DELAY * (2**attempt)
                if attempt < self._max_retries - 1:
                    logger.warning(
                        "EmbeddingGateway: tentativa %d/%d falhou (%s). "
                        "Aguardando %.1fs antes de retry.",
                        attempt + 1,
                        self._max_retries,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "EmbeddingGateway: todas as %d tentativas falharam: %s",
                        self._max_retries,
                        exc,
                    )
        self._record_failure()
        raise last_exc  # type: ignore[misc]

    # ── Public API ────────────────────────────────────────────────────────────

    async def embed_text(
        self,
        text: str,
        task_type: str = "RETRIEVAL_QUERY",
    ) -> list[float]:
        """Gera o embedding de um único texto com suporte a cache LRU.

        Queries (RETRIEVAL_QUERY) são cacheadas; documentos (RETRIEVAL_DOCUMENT)
        não são cacheados pois são persistidos na tabela `embeddings`.
        """
        # Cache somente para queries (task_type == RETRIEVAL_QUERY)
        if task_type == "RETRIEVAL_QUERY":
            cache_key = f"q:{text}"
            cached = self._cache_get(cache_key)
            if cached is not None:
                logger.debug("EmbeddingGateway: cache HIT para query (len=%d)", len(text))
                return cached

        self._assert_circuit_closed()
        results = await self._embed_with_retry([text], task_type)
        embedding = results[0]

        if task_type == "RETRIEVAL_QUERY":
            self._cache_put(f"q:{text}", embedding)

        return embedding

    async def embed_batch(
        self,
        texts: list[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> list[list[float]]:
        """Gera embeddings em batch com dois níveis de divisão:

        1. Divide em sub-lotes de até `max_batch_size` itens (quota de itens).
        2. Dentro de cada sub-lote, divide por tokens estimados para respeitar
           o limite de 20.000 tokens/chamada do text-embedding-005.
        """
        if not texts:
            return []

        self._assert_circuit_closed()

        all_results: list[list[float]] = []

        # Primeiro nível: divide por número de itens
        for i in range(0, len(texts), self._max_batch_size):
            item_batch = texts[i : i + self._max_batch_size]

            # Segundo nível: divide por tokens estimados
            token_batches = _split_by_tokens(item_batch)
            for token_batch in token_batches:
                logger.debug(
                    "EmbeddingGateway: batch %d textos / ~%d tokens estimados",
                    len(token_batch),
                    sum(len(t) // _CHARS_PER_TOKEN for t in token_batch),
                )
                batch_result = await self._embed_with_retry(token_batch, task_type)
                all_results.extend(batch_result)

        return all_results

    # ── Observability ─────────────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """True se o circuit breaker estiver fechado (serviço disponível)."""
        return time.monotonic() >= self._open_until

    @property
    def cache_size(self) -> int:
        """Número atual de entradas no LRU cache."""
        return len(self._cache)
