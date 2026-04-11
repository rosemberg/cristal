"""Vertex AI adapter — VertexAIGateway.

Implementa LLMGateway usando o SDK Google Cloud Vertex AI
(google-cloud-aiplatform >= 1.72).

Chamadas ao SDK são executadas via asyncio.to_thread para não bloquear
o event loop do FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import vertexai
from vertexai.generative_models import (
    Content,
    GenerationConfig,
    GenerativeModel,
    Part,
)

from app.domain.ports.outbound.llm_gateway import LLMGateway

logger = logging.getLogger(__name__)


class VertexAIGateway(LLMGateway):
    """Adapter que conecta LLMGateway ao Vertex AI / Gemini."""

    def __init__(self, project_id: str, location: str, model_name: str) -> None:
        vertexai.init(project=project_id, location=location)
        self._model_name = model_name

    def _build_contents(self, messages: list[dict[str, object]]) -> list[Content]:
        """Converte mensagens do domínio para o formato Content do Vertex AI.

        O domínio usa roles "user" e "assistant"; o Vertex AI usa "user" e "model".
        """
        contents: list[Content] = []
        for msg in messages:
            role = str(msg.get("role", "user"))
            vertex_role = "model" if role == "assistant" else "user"
            text = str(msg.get("content", ""))
            contents.append(Content(role=vertex_role, parts=[Part.from_text(text)]))
        return contents

    async def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, object]],
        temperature: float = 0.3,
    ) -> str:
        """Gera resposta completa (não-streaming) via Gemini."""
        model = GenerativeModel(self._model_name, system_instruction=system_prompt)
        contents = self._build_contents(messages)
        config = GenerationConfig(temperature=temperature)
        response = await asyncio.to_thread(
            model.generate_content, contents, generation_config=config
        )
        return response.text  # type: ignore[no-any-return]

    async def generate_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, object]],
    ) -> AsyncIterator[str]:
        """Gera resposta em streaming via Gemini.

        O SDK síncrono é executado em thread pool; os chunks são coletados
        e emitidos como async generator.
        """
        model = GenerativeModel(self._model_name, system_instruction=system_prompt)
        contents = self._build_contents(messages)

        async def _stream() -> AsyncIterator[str]:
            chunks = await asyncio.to_thread(
                lambda: list(model.generate_content(contents, stream=True))
            )
            for chunk in chunks:
                if chunk.text:
                    yield chunk.text

        return _stream()
