"""Text chunker with configurable size and overlap.

Splits plain text into DocumentChunk entities suitable for RAG ingestion.
Token count is estimated as: words × 1.3 (heuristic for Portuguese text).
"""

from __future__ import annotations

from app.domain.entities.chunk import DocumentChunk

# Approximate tokens-per-word ratio for Portuguese text
_TOKENS_PER_WORD: float = 1.3


class TextChunker:
    """Splits text into overlapping chunks of roughly ``chunk_size`` tokens."""

    def __init__(self, chunk_size: int = 500, overlap: int = 50) -> None:
        """
        Args:
            chunk_size: Target maximum tokens per chunk.
            overlap: Number of tokens to repeat at the start of the next chunk.
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        # Convert token limits to word limits (inverse of token ratio)
        self._words_per_chunk: int = max(1, int(chunk_size / _TOKENS_PER_WORD))
        self._overlap_words: int = max(0, int(overlap / _TOKENS_PER_WORD))

    # ------------------------------------------------------------------

    def chunk(
        self,
        text: str,
        document_url: str,
        page_number: int | None = None,
        section_title: str | None = None,
        start_chunk_index: int = 0,
    ) -> list[DocumentChunk]:
        """Split *text* into DocumentChunk entities.

        Args:
            text: Raw text to split.
            document_url: URL used as identifier in each chunk.
            page_number: Source page number (propagated to all chunks).
            section_title: Source section name (propagated to all chunks).
            start_chunk_index: Starting value for chunk_index (allows callers
                to maintain a global counter across pages).

        Returns:
            List of DocumentChunk; empty list if *text* is blank.
        """
        if not text.strip():
            return []

        words = text.split()
        chunks: list[DocumentChunk] = []
        start = 0
        relative_index = 0

        while start < len(words):
            end = min(start + self._words_per_chunk, len(words))
            chunk_text = " ".join(words[start:end])
            token_count = int(len(words[start:end]) * _TOKENS_PER_WORD)

            chunks.append(
                DocumentChunk(
                    id=0,  # Sentinel: will be assigned by the DB
                    document_url=document_url,
                    chunk_index=start_chunk_index + relative_index,
                    text=chunk_text,
                    token_count=max(1, token_count),
                    section_title=section_title,
                    page_number=page_number,
                )
            )

            if end == len(words):
                break

            relative_index += 1
            # Slide forward, going back by overlap_words for context
            start = end - self._overlap_words
            if start <= 0 or start >= end:
                start = end  # Safety: avoid infinite loop

        return chunks
