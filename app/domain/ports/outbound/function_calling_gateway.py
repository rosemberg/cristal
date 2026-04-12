"""Output port: FunctionCallingGateway — LLM com suporte a function calling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FunctionCall:
    """Uma invocação de tool retornada pelo LLM."""

    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class FunctionCallingResponse:
    """Resposta do LLM que pode conter function calls ou texto final."""

    text: str | None                     # None se houver function calls pendentes
    function_calls: list[FunctionCall]   # vazio se for texto final
    finish_reason: str                   # "stop" | "function_call"


class FunctionCallingGateway(ABC):
    """Gateway LLM com suporte a function calling (Vertex AI / Gemini)."""

    @abstractmethod
    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float = 0.1,
    ) -> FunctionCallingResponse:
        """Gera resposta com suporte a invocação de tools.

        Args:
            system_prompt: Instrução de sistema do agente.
            messages: Histórico de mensagens (inclui tool results quando em loop).
            tools: Declarações das tools no formato Vertex AI functionDeclarations.
            temperature: Temperatura de geração (baixa para DataAgent).

        Returns:
            FunctionCallingResponse com text=None e function_calls preenchidas,
            ou text preenchido e function_calls=[] quando o modelo parar.
        """
        ...
