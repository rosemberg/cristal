"""Input port: SyntheticQueryGenerationUseCase Protocol.

Define a interface para geração de perguntas sintéticas (Query Augmentation).
"""

from __future__ import annotations

from typing import Protocol

from app.domain.value_objects.synthetic_query import GenerationResult


class SyntheticQueryGenerationUseCase(Protocol):
    """Use case para geração e gestão de perguntas sintéticas."""

    async def generate_for_pending_chunks(
        self,
        batch_size: int = 50,
        source_types: list[str] | None = None,
    ) -> GenerationResult:
        """Gera perguntas para chunks que ainda não têm perguntas sintéticas.

        Args:
            batch_size: Número de chunks processados por chamada ao LLM.
            source_types: Lista de tipos a processar. None = todos
                ('page_chunk', 'chunk').
        """
        ...

    async def regenerate_for_chunk(
        self, source_type: str, source_id: int
    ) -> int:
        """Regenera perguntas para um chunk específico.

        Remove as perguntas e embeddings existentes antes de gerar novos.
        Retorna a quantidade de perguntas geradas.
        """
        ...

    async def get_status(self) -> dict[str, object]:
        """Retorna status da geração: contagens por source_type e pendentes."""
        ...
