"""Unit tests for PromptBuilder — Etapa 6 (TDD RED → GREEN)."""

from __future__ import annotations

import pytest

from app.domain.value_objects.intent import QueryIntent


class TestPromptBuilder:
    @pytest.fixture
    def builder(self):
        from app.domain.services.prompt_builder import PromptBuilder

        return PromptBuilder()

    # ------------------------------------------------------------------
    # build_system_prompt
    # ------------------------------------------------------------------

    def test_system_prompt_contains_tre_pi(self, builder):
        prompt = builder.build_system_prompt()
        assert "TRE-PI" in prompt or "Tribunal Regional Eleitoral" in prompt

    def test_system_prompt_is_non_empty_string(self, builder):
        prompt = builder.build_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 50

    def test_system_prompt_instructs_json_output(self, builder):
        prompt = builder.build_system_prompt()
        assert "JSON" in prompt or "json" in prompt

    # ------------------------------------------------------------------
    # build_context
    # ------------------------------------------------------------------

    def test_build_context_with_pages(self, builder, sample_pages):
        from app.domain.value_objects.search_result import PageMatch

        matches = [PageMatch(page=p, score=1.0) for p in sample_pages[:2]]
        context = builder.build_context(pages=matches, chunks=[], tables=[])
        assert isinstance(context, str)
        assert len(context) > 0
        # deve incluir URLs ou títulos das páginas
        assert sample_pages[0].title in context or sample_pages[0].url in context

    def test_build_context_with_chunks(self, builder, sample_documents):
        from app.domain.value_objects.search_result import ChunkMatch

        chunk = sample_documents[0].chunks[0]
        match = ChunkMatch(
            chunk=chunk,
            document_title="Documento 1",
            document_url=sample_documents[0].document_url,
            score=0.9,
        )
        context = builder.build_context(pages=[], chunks=[match], tables=[])
        assert chunk.text in context

    def test_build_context_with_tables(self, builder, sample_documents):
        table = sample_documents[0].tables[0]
        context = builder.build_context(pages=[], chunks=[], tables=[table])
        assert isinstance(context, str)
        # cabeçalhos devem aparecer
        assert table.headers[0] in context

    def test_build_context_empty_returns_string(self, builder):
        context = builder.build_context(pages=[], chunks=[], tables=[])
        assert isinstance(context, str)

    # ------------------------------------------------------------------
    # classify_intent
    # ------------------------------------------------------------------

    def test_classify_intent_document_query(self, builder):
        intent = builder.classify_intent("me mostre o pdf do orçamento")
        assert intent == QueryIntent.DOCUMENT_QUERY

    def test_classify_intent_data_query(self, builder):
        intent = builder.classify_intent("quantos servidores existem no quadro?")
        assert intent == QueryIntent.DATA_QUERY

    def test_classify_intent_navigation(self, builder):
        intent = builder.classify_intent("onde fica a seção de licitações?")
        assert intent == QueryIntent.NAVIGATION

    def test_classify_intent_general_search(self, builder):
        intent = builder.classify_intent("transparência pública")
        assert intent == QueryIntent.GENERAL_SEARCH

    def test_classify_intent_returns_query_intent_enum(self, builder):
        intent = builder.classify_intent("qualquer coisa")
        assert isinstance(intent, QueryIntent)

    def test_classify_intent_followup(self, builder):
        intent = builder.classify_intent("e sobre isso que você disse?")
        assert intent in list(QueryIntent)

    # ------------------------------------------------------------------
    # format_history
    # ------------------------------------------------------------------

    def test_format_history_converts_dicts(self, builder):
        history = [
            {"role": "user", "content": "olá"},
            {"role": "assistant", "content": "olá, posso ajudar"},
        ]
        formatted = builder.format_history(history)
        assert isinstance(formatted, list)
        assert len(formatted) == 2

    def test_format_history_empty_returns_empty(self, builder):
        assert builder.format_history([]) == []

    def test_format_history_none_returns_empty(self, builder):
        assert builder.format_history(None) == []
