"""Unit tests — VertexAIGateway (Etapa 8).

Testa o adapter de IA sem acionar a API real.
vertexai e GenerativeModel são mockados via unittest.mock.patch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.adapters.outbound.vertex_ai.gateway import VertexAIGateway

PROJECT = "test-project"
LOCATION = "us-central1"
MODEL = "gemini-2.5-flash-lite"

SYSTEM_PROMPT = "Você é um assistente de transparência pública do TRE-PI."
MESSAGES = [{"role": "user", "content": "Quais são os salários dos servidores?"}]


def make_gateway() -> VertexAIGateway:
    return VertexAIGateway(project_id=PROJECT, location=LOCATION, model_name=MODEL)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_vertexai():
    with patch("app.adapters.outbound.vertex_ai.gateway.vertexai") as m:
        yield m


@pytest.fixture
def mock_generative_model():
    with patch("app.adapters.outbound.vertex_ai.gateway.GenerativeModel") as cls:
        yield cls


@pytest.fixture
def mock_generation_config():
    with patch("app.adapters.outbound.vertex_ai.gateway.GenerationConfig") as cls:
        yield cls


# ─── Init ─────────────────────────────────────────────────────────────────────


class TestVertexAIGatewayInit:
    def test_init_calls_vertexai_init(self, mock_vertexai, mock_generative_model) -> None:
        make_gateway()
        mock_vertexai.init.assert_called_once_with(project=PROJECT, location=LOCATION)

    def test_init_stores_model_name(self, mock_vertexai, mock_generative_model) -> None:
        gw = make_gateway()
        assert gw._model_name == MODEL

    def test_init_different_location(self, mock_vertexai, mock_generative_model) -> None:
        VertexAIGateway(project_id="proj", location="southamerica-east1", model_name="gemini")
        mock_vertexai.init.assert_called_once_with(
            project="proj", location="southamerica-east1"
        )


# ─── generate() ──────────────────────────────────────────────────────────────


class TestGenerate:
    async def test_generate_returns_response_text(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        mock_response = MagicMock()
        mock_response.text = "Os salários estão disponíveis no portal de transparência."
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = mock_response

        gw = make_gateway()
        result = await gw.generate(SYSTEM_PROMPT, MESSAGES)

        assert result == "Os salários estão disponíveis no portal de transparência."

    async def test_generate_creates_model_with_system_instruction(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = MagicMock(text="ok")

        gw = make_gateway()
        await gw.generate(SYSTEM_PROMPT, MESSAGES)

        mock_generative_model.assert_called_once_with(
            MODEL, system_instruction=SYSTEM_PROMPT
        )

    async def test_generate_passes_temperature_07(
        self, mock_vertexai, mock_generative_model, mock_generation_config
    ) -> None:
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = MagicMock(text="ok")

        gw = make_gateway()
        await gw.generate(SYSTEM_PROMPT, MESSAGES, temperature=0.7)

        mock_generation_config.assert_called_once_with(temperature=0.7)

    async def test_generate_default_temperature_is_0_3(
        self, mock_vertexai, mock_generative_model, mock_generation_config
    ) -> None:
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = MagicMock(text="ok")

        gw = make_gateway()
        await gw.generate(SYSTEM_PROMPT, MESSAGES)

        mock_generation_config.assert_called_once_with(temperature=0.3)

    async def test_generate_converts_assistant_to_model_role(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = MagicMock(text="ok")

        messages = [
            {"role": "user", "content": "Olá"},
            {"role": "assistant", "content": "Como posso ajudar?"},
            {"role": "user", "content": "Quero saber sobre licitações"},
        ]
        gw = make_gateway()
        await gw.generate(SYSTEM_PROMPT, messages)

        contents_arg = instance.generate_content.call_args.args[0]
        roles = [c.role for c in contents_arg]
        assert roles == ["user", "model", "user"]

    async def test_generate_with_multiple_messages(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = MagicMock(text="resposta multi")

        messages = [
            {"role": "user", "content": "Pergunta 1"},
            {"role": "assistant", "content": "Resposta 1"},
            {"role": "user", "content": "Pergunta 2"},
        ]
        gw = make_gateway()
        result = await gw.generate(SYSTEM_PROMPT, messages)

        assert result == "resposta multi"
        contents_arg = instance.generate_content.call_args.args[0]
        assert len(contents_arg) == 3

    async def test_generate_raises_on_api_error(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        instance = mock_generative_model.return_value
        instance.generate_content.side_effect = Exception("API quota exceeded")

        gw = make_gateway()
        with pytest.raises(Exception, match="API quota exceeded"):
            await gw.generate(SYSTEM_PROMPT, MESSAGES)

    async def test_generate_passes_contents_to_model(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = MagicMock(text="ok")

        gw = make_gateway()
        await gw.generate(SYSTEM_PROMPT, MESSAGES)

        # generate_content deve ter sido chamado com uma lista de Contents
        call_args = instance.generate_content.call_args
        contents = call_args.args[0]
        assert isinstance(contents, list)
        assert len(contents) == 1


# ─── generate_stream() ───────────────────────────────────────────────────────


class TestGenerateStream:
    async def test_generate_stream_yields_chunks(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        chunk1 = MagicMock(text="Os salários ")
        chunk2 = MagicMock(text="estão no portal.")
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = iter([chunk1, chunk2])

        gw = make_gateway()
        stream = await gw.generate_stream(SYSTEM_PROMPT, MESSAGES)
        tokens = [t async for t in stream]

        assert tokens == ["Os salários ", "estão no portal."]

    async def test_generate_stream_skips_empty_text_chunks(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        chunk1 = MagicMock(text="Início. ")
        chunk2 = MagicMock(text="")
        chunk3 = MagicMock(text="Fim.")
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = iter([chunk1, chunk2, chunk3])

        gw = make_gateway()
        stream = await gw.generate_stream(SYSTEM_PROMPT, MESSAGES)
        tokens = [t async for t in stream]

        assert tokens == ["Início. ", "Fim."]

    async def test_generate_stream_skips_none_text_chunks(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        chunk1 = MagicMock(text="Conteúdo válido.")
        chunk2 = MagicMock()
        chunk2.text = None
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = iter([chunk1, chunk2])

        gw = make_gateway()
        stream = await gw.generate_stream(SYSTEM_PROMPT, MESSAGES)
        tokens = [t async for t in stream]

        assert tokens == ["Conteúdo válido."]

    async def test_generate_stream_returns_async_iterator(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = iter([MagicMock(text="ok")])

        gw = make_gateway()
        stream = await gw.generate_stream(SYSTEM_PROMPT, MESSAGES)

        assert hasattr(stream, "__aiter__")
        assert hasattr(stream, "__anext__")

    async def test_generate_stream_empty_response(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = iter([])

        gw = make_gateway()
        stream = await gw.generate_stream(SYSTEM_PROMPT, MESSAGES)
        tokens = [t async for t in stream]

        assert tokens == []

    async def test_generate_stream_single_chunk(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = iter([MagicMock(text="resultado completo")])

        gw = make_gateway()
        stream = await gw.generate_stream(SYSTEM_PROMPT, [{"role": "user", "content": "query"}])
        tokens = [t async for t in stream]

        assert tokens == ["resultado completo"]

    async def test_generate_stream_uses_streaming_mode(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        instance = mock_generative_model.return_value
        instance.generate_content.return_value = iter([MagicMock(text="ok")])

        gw = make_gateway()
        stream = await gw.generate_stream(SYSTEM_PROMPT, MESSAGES)
        # Consome o stream para acionar a chamada ao SDK
        _ = [t async for t in stream]

        # generate_content deve ter sido chamado com stream=True
        call_kwargs = instance.generate_content.call_args
        assert call_kwargs.kwargs.get("stream") is True


# ─── _build_contents() ───────────────────────────────────────────────────────


class TestBuildContents:
    def test_build_contents_user_role_preserved(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        gw = make_gateway()
        contents = gw._build_contents([{"role": "user", "content": "Olá"}])
        assert len(contents) == 1
        assert contents[0].role == "user"

    def test_build_contents_assistant_mapped_to_model(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        gw = make_gateway()
        contents = gw._build_contents([{"role": "assistant", "content": "Resposta"}])
        assert contents[0].role == "model"

    def test_build_contents_empty_messages_returns_empty_list(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        gw = make_gateway()
        contents = gw._build_contents([])
        assert contents == []

    def test_build_contents_multiple_messages(
        self, mock_vertexai, mock_generative_model
    ) -> None:
        gw = make_gateway()
        messages = [
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
            {"role": "user", "content": "C"},
        ]
        contents = gw._build_contents(messages)
        assert len(contents) == 3
        assert [c.role for c in contents] == ["user", "model", "user"]
