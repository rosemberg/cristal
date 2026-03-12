import logging
import json
from typing import AsyncIterator

import vertexai
from vertexai.generative_models import (
    GenerativeModel,
    Content,
    Part,
    GenerationConfig,
)

from app.config import VERTEX_PROJECT_ID, VERTEX_LOCATION, VERTEX_MODEL

logger = logging.getLogger(__name__)


class VertexClient:
    def __init__(self) -> None:
        vertexai.init(project=VERTEX_PROJECT_ID, location=VERTEX_LOCATION)
        self.model = GenerativeModel(VERTEX_MODEL)
        logger.info("VertexClient initialized with model %s", VERTEX_MODEL)

    def _build_contents(self, messages: list[dict]) -> list[Content]:
        contents: list[Content] = []
        for msg in messages:
            role = msg.get("role", "user")
            # Vertex AI uses "model" instead of "assistant"
            if role == "assistant":
                role = "model"
            contents.append(
                Content(role=role, parts=[Part.from_text(msg["content"])])
            )
        return contents

    async def generate(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        generation_config = GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            top_p=0.8,
            top_k=40,
        )

        model = GenerativeModel(
            VERTEX_MODEL,
            system_instruction=system_prompt,
            generation_config=generation_config,
        )

        contents = self._build_contents(messages)

        try:
            response = model.generate_content(contents)
            return response.text
        except Exception as exc:
            logger.error("Vertex AI generation error: %s", exc)
            raise

    async def generate_stream(
        self,
        system_prompt: str,
        messages: list[dict],
    ) -> AsyncIterator[str]:
        model = GenerativeModel(
            VERTEX_MODEL,
            system_instruction=system_prompt,
            generation_config=GenerationConfig(
                temperature=0.3,
                max_output_tokens=2048,
                top_p=0.8,
                top_k=40,
            ),
        )

        contents = self._build_contents(messages)

        try:
            responses = model.generate_content(contents, stream=True)
            for chunk in responses:
                if chunk.text:
                    yield chunk.text
        except Exception as exc:
            logger.error("Vertex AI stream error: %s", exc)
            raise
