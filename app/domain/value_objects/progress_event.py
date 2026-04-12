"""Value object: ProgressEvent — evento de progresso emitido via SSE."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProgressEvent:
    event_type: str          # "searching" | "analyzing" | "writing" | "done" | "error" | "tool_call" | "keepalive"
    message: str             # mensagem legível para o usuário
    data: dict[str, Any] = field(default_factory=dict)  # payload opcional
