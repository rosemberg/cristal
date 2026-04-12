"""PostgreSQL-backed ContentSummarizerService.

Estende o serviço de domínio com acesso direto ao pool asyncpg para:
- Buscar páginas pendentes (sem embedding page_summary)
- Atualizar pages.content_summary com os sumários gerados
- Buscar documentos longos pendentes (sem section_summaries)
- Persistir section_summaries na tabela correspondente
"""

from __future__ import annotations

import logging

import asyncpg

from app.domain.ports.outbound.embedding_gateway import EmbeddingGateway
from app.domain.ports.outbound.embedding_repository import EmbeddingRepository
from app.domain.ports.outbound.llm_gateway import LLMGateway
from app.domain.services.content_summarizer import ContentSummarizerService

logger = logging.getLogger(__name__)


class PostgresContentSummarizerService(ContentSummarizerService):
    """Implementação concreta com acesso ao banco via asyncpg."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        llm_gateway: LLMGateway,
        embedding_gateway: EmbeddingGateway,
        embedding_repo: EmbeddingRepository,
        model_name: str = "gemini-2.5-flash-lite",
        llm_batch_size: int = 10,
        section_min_pages: int = 10,
    ) -> None:
        super().__init__(
            llm_gateway=llm_gateway,
            embedding_gateway=embedding_gateway,
            embedding_repo=embedding_repo,
            model_name=model_name,
            llm_batch_size=llm_batch_size,
            section_min_pages=section_min_pages,
        )
        self._pool = pool

    # ── Páginas ───────────────────────────────────────────────────────────────

    async def _get_covered_page_ids(self) -> set[int]:
        """IDs de páginas que já têm embedding page_summary."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT source_id FROM embeddings WHERE source_type = 'page_summary'"
            )
        return {r["source_id"] for r in rows}

    async def _fetch_pending_pages_from_db(
        self, covered: set[int], limit: int
    ) -> list[dict]:
        """Busca páginas com conteúdo que ainda não têm sumário LLM."""
        covered_list = list(covered) if covered else [-1]
        placeholders = ", ".join(f"${i+2}" for i in range(len(covered_list)))

        query = f"""
            SELECT id, title, category, subcategory, main_content
            FROM pages
            WHERE id NOT IN ({placeholders})
              AND main_content IS NOT NULL
              AND LENGTH(main_content) > 100
            ORDER BY id
            LIMIT $1
        """  # noqa: S608

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, limit, *covered_list)

        return [
            {
                "id": r["id"],
                "title": r["title"],
                "category": r["category"],
                "subcategory": r["subcategory"],
                "main_content": r["main_content"],
            }
            for r in rows
        ]

    async def _fetch_single_page_from_db(self, page_id: int) -> dict | None:
        """Busca uma única página pelo ID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, title, category, subcategory, main_content
                FROM pages
                WHERE id = $1
                """,
                page_id,
            )
        if row is None:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "category": row["category"],
            "subcategory": row["subcategory"],
            "main_content": row["main_content"],
        }

    async def _update_page_summaries(self, summaries: dict[int, str]) -> None:
        """Atualiza pages.content_summary para cada página do dicionário."""
        if not summaries:
            return

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for page_id, summary_text in summaries.items():
                    await conn.execute(
                        "UPDATE pages SET content_summary = $1, updated_at = NOW() WHERE id = $2",
                        summary_text,
                        page_id,
                    )

    async def _count_total_pages(self) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM pages WHERE main_content IS NOT NULL AND LENGTH(main_content) > 100"
            )
        return int(row["cnt"]) if row else 0

    # ── Documentos (seções) ───────────────────────────────────────────────────

    async def _get_covered_document_ids(self) -> set[int]:
        """IDs de documentos que já têm pelo menos uma section_summary."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT document_id FROM section_summaries"
            )
        return {r["document_id"] for r in rows}

    async def _fetch_pending_documents_from_db(
        self, covered: set[int], limit: int
    ) -> list[dict]:
        """Busca documentos com >N páginas que ainda não têm seções sumarizadas."""
        covered_list = list(covered) if covered else [-1]
        placeholders = ", ".join(f"${i+3}" for i in range(len(covered_list)))

        query = f"""
            SELECT id, document_url, document_title, full_text, num_pages
            FROM document_contents
            WHERE id NOT IN ({placeholders})
              AND processing_status = 'done'
              AND num_pages > $1
              AND full_text IS NOT NULL
              AND LENGTH(full_text) > 500
            ORDER BY id
            LIMIT $2
        """  # noqa: S608

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                query, self._section_min_pages, limit, *covered_list
            )

        return [
            {
                "id": r["id"],
                "document_url": r["document_url"],
                "document_title": r["document_title"],
                "full_text": r["full_text"],
                "num_pages": r["num_pages"],
            }
            for r in rows
        ]

    async def _save_section_summaries(
        self, document_id: int, sections_data: list[dict]
    ) -> list[int]:
        """Persiste seções na tabela section_summaries. Retorna IDs gerados."""
        if not sections_data:
            return []

        ids: list[int] = []
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for section in sections_data:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO section_summaries
                            (document_id, section_title, page_range, summary_text, model_used)
                        VALUES ($1, $2, $3, $4, $5)
                        RETURNING id
                        """,
                        document_id,
                        section.get("section_title"),
                        section.get("page_range"),
                        section["summary"],
                        self._model_name,
                    )
                    if row:
                        ids.append(row["id"])
        return ids

    async def _count_long_documents(self) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt
                FROM document_contents
                WHERE processing_status = 'done'
                  AND num_pages > $1
                  AND full_text IS NOT NULL
                """,
                self._section_min_pages,
            )
        return int(row["cnt"]) if row else 0
