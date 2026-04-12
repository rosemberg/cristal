"""Vertex AI adapter — VertexAIFunctionCallingGateway.

Implementa FunctionCallingGateway usando o SDK google-cloud-aiplatform.
Suporta o loop de function calling do DataAgent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import vertexai
from vertexai.generative_models import (
    Content,
    FunctionDeclaration,
    GenerationConfig,
    GenerativeModel,
    Part,
    Tool,
)

from app.domain.ports.outbound.function_calling_gateway import (
    FunctionCall,
    FunctionCallingGateway,
    FunctionCallingResponse,
)

logger = logging.getLogger(__name__)


class VertexAIFunctionCallingGateway(FunctionCallingGateway):
    """Adapter que conecta FunctionCallingGateway ao Vertex AI / Gemini."""

    def __init__(self, project_id: str, location: str, model_name: str) -> None:
        vertexai.init(project=project_id, location=location)
        self._model_name = model_name

    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float = 0.1,
    ) -> FunctionCallingResponse:
        """Gera resposta com suporte a invocação de tools via Vertex AI."""
        vertex_tools = self._build_tools(tools)
        model = GenerativeModel(
            self._model_name,
            system_instruction=system_prompt,
            tools=vertex_tools,
        )
        contents = self._build_contents(messages)
        config = GenerationConfig(temperature=temperature)

        response = await asyncio.to_thread(
            model.generate_content, contents, generation_config=config
        )

        return self._parse_response(response)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_tools(self, tool_declarations: list[dict[str, Any]]) -> list[Tool]:
        """Converte declarações do domínio para objetos Tool do Vertex AI."""
        declarations = []
        for decl in tool_declarations:
            params = decl.get("parameters", {})
            declarations.append(
                FunctionDeclaration(
                    name=decl["name"],
                    description=decl.get("description", ""),
                    parameters=params,
                )
            )
        return [Tool(function_declarations=declarations)]

    def _build_contents(self, messages: list[dict[str, Any]]) -> list[Content]:
        """Converte mensagens do domínio para o formato Content do Vertex AI."""
        contents: list[Content] = []
        for msg in messages:
            role = str(msg.get("role", "user"))
            vertex_role = "model" if role == "assistant" else "user"
            content_data = msg.get("content", "")

            if isinstance(content_data, str):
                contents.append(
                    Content(role=vertex_role, parts=[Part.from_text(content_data)])
                )
            elif isinstance(content_data, list):
                # Suporta mensagens mistas (text + function_response)
                parts: list[Part] = []
                for item in content_data:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            parts.append(Part.from_text(str(item.get("text", ""))))
                        elif item.get("type") == "function_response":
                            parts.append(
                                Part.from_function_response(
                                    name=str(item["name"]),
                                    response={"result": item.get("response")},
                                )
                            )
                if parts:
                    contents.append(Content(role=vertex_role, parts=parts))

        return contents

    def _parse_response(self, response: Any) -> FunctionCallingResponse:
        """Extrai function calls ou texto final da resposta do Vertex AI."""
        candidate = response.candidates[0] if response.candidates else None
        if candidate is None:
            return FunctionCallingResponse(
                text="",
                function_calls=[],
                finish_reason="stop",
            )

        function_calls: list[FunctionCall] = []
        text_parts: list[str] = []

        for part in candidate.content.parts:
            if hasattr(part, "function_call") and part.function_call is not None and part.function_call.name:
                fc = part.function_call
                function_calls.append(
                    FunctionCall(
                        name=fc.name,
                        args=dict(fc.args) if fc.args else {},
                    )
                )
            elif hasattr(part, "text") and part.text:
                text_parts.append(part.text)

        if function_calls:
            return FunctionCallingResponse(
                text=None,
                function_calls=function_calls,
                finish_reason="function_call",
            )

        finish_reason = str(getattr(candidate, "finish_reason", "stop")).lower()
        return FunctionCallingResponse(
            text="".join(text_parts),
            function_calls=[],
            finish_reason=finish_reason,
        )
