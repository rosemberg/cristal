"""Output port: EmbeddingGateway ABC.

Abstração para geração de embeddings vetoriais de texto.

Task types suportados:
- "RETRIEVAL_QUERY"    — para embeddings de queries de busca (menor dimensão efetiva)
- "RETRIEVAL_DOCUMENT" — para embeddings de documentos/chunks a indexar
- "SEMANTIC_SIMILARITY" — para comparação semântica genérica
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingUnavailableError(Exception):
    """Raised when the embedding service is unavailable (circuit breaker open).

    O chamador deve fazer fallback para busca FTS quando receber esta exceção.
    """


class EmbeddingGateway(ABC):
    """Port for generating text embeddings via a vector model."""

    @abstractmethod
    async def embed_text(
        self,
        text: str,
        task_type: str = "RETRIEVAL_QUERY",
    ) -> list[float]:
        """Gera o embedding de um único texto.

        Args:
            text: Texto a ser vetorizado.
            task_type: Tipo de tarefa para otimização do modelo.
                Use "RETRIEVAL_QUERY" para buscas,
                "RETRIEVAL_DOCUMENT" para indexação.

        Returns:
            Vetor de floats (dimensão depende do modelo, tipicamente 768).

        Raises:
            EmbeddingUnavailableError: Se o serviço estiver indisponível
                (circuit breaker aberto).
        """
        ...

    @abstractmethod
    async def embed_batch(
        self,
        texts: list[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> list[list[float]]:
        """Gera embeddings de múltiplos textos em batch.

        Args:
            texts: Lista de textos a serem vetorizados.
            task_type: Tipo de tarefa (mesmo valor para todos os textos).

        Returns:
            Lista de vetores, na mesma ordem dos textos de entrada.
            Len(resultado) == len(texts).

        Raises:
            EmbeddingUnavailableError: Se o serviço estiver indisponível.
        """
        ...
