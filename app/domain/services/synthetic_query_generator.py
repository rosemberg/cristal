"""Domain service: SyntheticQueryGeneratorService.

Gera perguntas sintéticas (Query Augmentation) para chunks existentes,
persistindo-as em `synthetic_queries` e seus embeddings em `embeddings`
com source_type='synthetic_query'.

Estratégia:
1. Consulta chunks sem cobertura de perguntas sintéticas (via SyntheticQueryRepository)
2. Envia ao LLM em batches (padrão 10 chunks/chamada)
3. Salva perguntas no banco e gera embeddings via EmbeddingGateway
4. Retorna GenerationResult com contagens

Fallback:
- JSON malformado → tenta regex antes de descartar o batch
- Erros LLM → registra erro, continua para próximo batch (sem retry aqui;
  a CLI pode re-executar)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from app.domain.ports.outbound.embedding_gateway import EmbeddingGateway
from app.domain.ports.outbound.embedding_repository import EmbeddingRecord, EmbeddingRepository
from app.domain.ports.outbound.llm_gateway import LLMGateway
from app.domain.ports.outbound.synthetic_query_repository import SyntheticQueryRepository
from app.domain.value_objects.synthetic_query import GenerationResult, SyntheticQuery

logger = logging.getLogger(__name__)

# ─── Prompt ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "Você é um especialista em transparência pública brasileira. "
    "Sua tarefa é gerar perguntas que cidadãos fariam ao consultar dados do "
    "Tribunal Regional Eleitoral do Piauí (TRE-PI). "
    "Responda APENAS com JSON válido, sem texto adicional."
)

_USER_PROMPT_TEMPLATE = """\
Para cada trecho de documento abaixo, gere de 3 a 5 perguntas em linguagem \
natural que um cidadão faria e que esse trecho é capaz de responder.

Regras:
- Perguntas em português brasileiro, linguagem simples
- Inclua variações (formal/informal, com/sem siglas)
- Se houver dados numéricos, inclua perguntas sobre valores e totais
- Se houver nomes, inclua perguntas sobre pessoas/órgãos específicos
- Retorne JSON no formato: [{{"chunk_id": N, "questions": ["...", "..."]}}]

Trechos:
{chunks_json}
"""

# Regex fallback: extrai arrays de perguntas do JSON mesmo que malformado
_JSON_ARRAY_RE = re.compile(r'\{[^{}]*"chunk_id"\s*:\s*(\d+)[^{}]*"questions"\s*:\s*(\[[^\]]+\])')


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _parse_llm_response(raw: str) -> list[dict]:
    """Tenta parsear resposta JSON do LLM. Fallback para regex."""
    # Limpa markdown code fences se presentes
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed  # type: ignore[return-value]
    except json.JSONDecodeError:
        pass

    # Regex fallback
    results = []
    for m in _JSON_ARRAY_RE.finditer(raw):
        try:
            chunk_id = int(m.group(1))
            questions = json.loads(m.group(2))
            results.append({"chunk_id": chunk_id, "questions": questions})
        except (ValueError, json.JSONDecodeError):
            continue
    return results


def _build_chunks_json(chunks: list[dict]) -> str:
    """Serializa lista de {id, text} para o prompt."""
    return json.dumps(
        [{"chunk_id": c["id"], "text": c["text"][:1500]} for c in chunks],
        ensure_ascii=False,
        indent=2,
    )


# ─── Service ──────────────────────────────────────────────────────────────────


class SyntheticQueryGeneratorService:
    """Orquestra a geração de perguntas sintéticas para chunks existentes."""

    def __init__(
        self,
        llm_gateway: LLMGateway,
        embedding_gateway: EmbeddingGateway,
        synthetic_query_repo: SyntheticQueryRepository,
        embedding_repo: EmbeddingRepository,
        model_name: str = "gemini-2.5-flash-lite",
        llm_batch_size: int = 10,
    ) -> None:
        self._llm = llm_gateway
        self._embedding_gw = embedding_gateway
        self._sq_repo = synthetic_query_repo
        self._emb_repo = embedding_repo
        self._model_name = model_name
        self._llm_batch_size = llm_batch_size

    # ── generate_for_pending_chunks ───────────────────────────────────────────

    async def generate_for_pending_chunks(
        self,
        batch_size: int = 50,
        source_types: list[str] | None = None,
    ) -> GenerationResult:
        """Gera perguntas para chunks que ainda não têm cobertura."""
        types = source_types or ["page_chunk", "chunk"]
        result = GenerationResult()

        for source_type in types:
            covered = await self._sq_repo.get_covered_source_ids(source_type)
            pending = await self._get_pending_chunks(source_type, covered, batch_size)

            if not pending:
                logger.info("SQGenerator: nenhum chunk pendente para '%s'", source_type)
                continue

            result.skipped += len(covered)
            logger.info(
                "SQGenerator: %d chunks pendentes para '%s'", len(pending), source_type
            )
            sub = await self._process_chunks(pending, source_type)
            result.chunks_processed += sub.chunks_processed
            result.questions_generated += sub.questions_generated
            result.embeddings_created += sub.embeddings_created
            result.errors += sub.errors

        return result

    # ── regenerate_for_chunk ──────────────────────────────────────────────────

    async def regenerate_for_chunk(
        self, source_type: str, source_id: int
    ) -> int:
        """Remove perguntas/embeddings existentes e regenera para um chunk."""
        # 1. Busca perguntas existentes para obter seus IDs (para deletar embeddings)
        existing_ids = await self._sq_repo.get_covered_source_ids(source_type)
        if source_id in existing_ids:
            await self._sq_repo.delete_by_source(source_type, source_id)
            # Embeddings de synthetic_query com source_id entre os IDs deletados
            # são removidos pelo CASCADE na tabela, ou manualmente:
            await self._emb_repo.delete_by_source(source_id, "synthetic_query")

        chunk = await self._fetch_single_chunk(source_type, source_id)
        if chunk is None:
            logger.warning("SQGenerator: chunk não encontrado: %s/%d", source_type, source_id)
            return 0

        sub = await self._process_chunks([chunk], source_type)
        return sub.questions_generated

    # ── get_status ────────────────────────────────────────────────────────────

    async def get_status(self) -> dict[str, object]:
        """Retorna status atual da geração."""
        counts = await self._sq_repo.get_status()
        return {
            "questions_by_source_type": counts,
            "total_questions": sum(counts.values()),
        }

    # ── Helpers internos ──────────────────────────────────────────────────────

    async def _process_chunks(
        self, chunks: list[dict], source_type: str
    ) -> GenerationResult:
        """Processa um batch de chunks: LLM → save queries → embeddings."""
        result = GenerationResult()

        # Divide em sub-batches para o LLM
        for i in range(0, len(chunks), self._llm_batch_size):
            sub = chunks[i : i + self._llm_batch_size]
            try:
                questions_map = await self._call_llm(sub)
                saved_ids = await self._save_questions(questions_map, source_type)
                emb_count = await self._generate_embeddings(questions_map, saved_ids)

                result.chunks_processed += len(sub)
                result.questions_generated += sum(len(qs) for qs in questions_map.values())
                result.embeddings_created += emb_count
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "SQGenerator: erro no batch [%d:%d] de '%s': %s",
                    i, i + len(sub), source_type, exc,
                )
                result.errors += len(sub)

        return result

    async def _call_llm(self, chunks: list[dict]) -> dict[int, list[str]]:
        """Chama LLM e retorna {chunk_id: [question, ...]}."""
        prompt = _USER_PROMPT_TEMPLATE.format(chunks_json=_build_chunks_json(chunks))
        raw = await self._llm.generate(
            system_prompt=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,  # criatividade para perguntas variadas
        )
        parsed = _parse_llm_response(raw)
        return {
            item["chunk_id"]: [q for q in item.get("questions", []) if isinstance(q, str) and q.strip()]
            for item in parsed
            if isinstance(item.get("chunk_id"), int)
        }

    async def _save_questions(
        self, questions_map: dict[int, list[str]], source_type: str
    ) -> dict[int, list[int]]:
        """Salva perguntas no banco. Retorna {chunk_id: [sq_id, ...]}."""
        all_queries: list[SyntheticQuery] = []
        chunk_to_questions: dict[int, list[str]] = {}

        for chunk_id, questions in questions_map.items():
            chunk_to_questions[chunk_id] = questions
            for question in questions:
                all_queries.append(
                    SyntheticQuery(
                        source_type=source_type,
                        source_id=chunk_id,
                        question=question,
                        model_used=self._model_name,
                    )
                )

        if not all_queries:
            return {}

        ids = await self._sq_repo.save_batch(all_queries)

        # Reconstrói {chunk_id: [sq_id, ...]} na mesma ordem
        result: dict[int, list[int]] = {}
        idx = 0
        for chunk_id, questions in chunk_to_questions.items():
            result[chunk_id] = ids[idx : idx + len(questions)]
            idx += len(questions)
        return result

    async def _generate_embeddings(
        self,
        questions_map: dict[int, list[str]],
        saved_ids: dict[int, list[int]],
    ) -> int:
        """Gera embeddings para cada pergunta e salva com source_type='synthetic_query'."""
        flat_questions: list[str] = []
        flat_ids: list[int] = []

        for chunk_id, questions in questions_map.items():
            sq_ids = saved_ids.get(chunk_id, [])
            for i, question in enumerate(questions):
                if i < len(sq_ids):
                    flat_questions.append(question)
                    flat_ids.append(sq_ids[i])

        if not flat_questions:
            return 0

        embeddings = await self._embedding_gw.embed_batch(
            flat_questions, task_type="RETRIEVAL_DOCUMENT"
        )

        records = [
            EmbeddingRecord(
                source_type="synthetic_query",
                source_id=sq_id,
                embedding=emb,
                source_text_hash="",
                model_name=self._model_name,
                dimensions=len(emb),
            )
            for sq_id, emb in zip(flat_ids, embeddings)
        ]
        await self._emb_repo.save_batch(records)
        return len(records)

    async def _get_pending_chunks(
        self, source_type: str, covered: set[int], limit: int
    ) -> list[dict]:
        """Busca chunks sem cobertura no banco. Retorna [{id, text}]."""
        # Esta implementação usa o repo diretamente via SQL raw.
        # O repo injeta o pool asyncpg — delegamos para um método virtual.
        return await self._fetch_pending_from_db(source_type, covered, limit)

    async def _fetch_pending_from_db(
        self, source_type: str, covered: set[int], limit: int
    ) -> list[dict]:
        """Template method: subclasses ou adapters fornecem implementação real.

        Para testes, este método pode ser sobrescrito com mock.
        Para produção, o CLI injeta um adapter que implementa consulta SQL.
        """
        raise NotImplementedError(
            "SyntheticQueryGeneratorService._fetch_pending_from_db deve ser "
            "implementado pelo adapter concreto (PostgresSyntheticQueryGeneratorService)."
        )

    async def _fetch_single_chunk(
        self, source_type: str, source_id: int
    ) -> dict | None:
        """Busca um único chunk pelo ID."""
        raise NotImplementedError(
            "SyntheticQueryGeneratorService._fetch_single_chunk deve ser "
            "implementado pelo adapter concreto."
        )
