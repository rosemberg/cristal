"""SemanticChunker — chunking baseado na estrutura do documento (Fase 4 NOVO_RAG).

Substitui o TextChunker de janela fixa por um chunker que respeita a
estrutura semântica do documento:

- HTML (main_content de páginas): parse via BeautifulSoup
  headings → nova seção, parágrafos → acumula, tabelas → chunk isolado,
  listas → mantém juntas (se < TABLE_MAX_TOKENS), senão divide por item

- Texto puro (full_text de PDFs): heurística de detecção de headings
  via linhas curtas sem pontuação final; parágrafos por \n\n

Cada chunk recebe:
- Prefixo contextual: "section_title\n\nbreadcrumb\n\n{texto}"
- Metadados: section_title, has_table, version=2
- Token count estimado (words × 1.3, padrão PT-BR)

Limites de tamanho:
- MIN_TOKENS = 100  (chunks menores são fundidos com o anterior)
- MAX_TOKENS = 800  (chunks maiores são divididos por sentença)
- TARGET    ≈ 400   (alvo central)
"""

from __future__ import annotations

import re
from dataclasses import replace as dc_replace

from app.domain.entities.chunk import DocumentChunk

# ─── Parâmetros ───────────────────────────────────────────────────────────────

_TOKENS_PER_WORD: float = 1.3
_MIN_TOKENS: int = 100
_MAX_TOKENS: int = 800
_TABLE_MAX_TOKENS: int = 300   # lista se mantém unida abaixo desse limite

# Detecta final de sentença para split de blocos muito grandes
_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+")

# Linha candidata a heading: curta, sem ponto final, não é só número
_HEADING_RE = re.compile(r"^[A-ZÁÉÍÓÚÂÊÎÔÛÀÃÕÇ\d][^.!?]{0,79}$", re.UNICODE)


# ─── Helpers de token ─────────────────────────────────────────────────────────


def _est_tokens(text: str) -> int:
    return max(1, int(len(text.split()) * _TOKENS_PER_WORD))


def _split_by_sentence(text: str) -> list[str]:
    """Divide texto em sentenças para sub-chunks de blocos grandes."""
    parts = _SENTENCE_END_RE.split(text)
    if len(parts) <= 1:
        # Sem pontuação clara → divide por palavras
        words = text.split()
        mid = len(words) // 2
        return [" ".join(words[:mid]), " ".join(words[mid:])]
    return [p.strip() for p in parts if p.strip()]


# ─── Montagem de chunks a partir de blocos ───────────────────────────────────

_Block = dict  # {text: str, is_table: bool, section_title: str | None}


def _blocks_to_chunks(
    blocks: list[_Block],
    document_url: str,
    page_number: int | None,
    start_index: int,
) -> list[DocumentChunk]:
    """Converte lista de blocos em DocumentChunk aplicando limites de tamanho."""
    chunks: list[DocumentChunk] = []
    current_text = ""
    current_section: str | None = None
    current_has_table = False
    chunk_idx = start_index

    def _emit(text: str, section: str | None, has_table: bool) -> None:
        nonlocal chunk_idx
        if not text.strip():
            return
        chunks.append(
            DocumentChunk(
                id=0,
                document_url=document_url,
                chunk_index=chunk_idx,
                text=text.strip(),
                token_count=_est_tokens(text),
                section_title=section,
                page_number=page_number,
                version=2,
                has_table=has_table,
            )
        )
        chunk_idx += 1

    for block in blocks:
        btext = block["text"].strip()
        if not btext:
            continue

        is_table = block.get("is_table", False)
        section = block.get("section_title", current_section)

        # Tabelas são sempre emitidas como chunk isolado
        if is_table:
            if current_text:
                _emit(current_text, current_section, current_has_table)
                current_text = ""
                current_has_table = False
            # Divide tabela grande por sentença (linha a linha)
            if _est_tokens(btext) > _MAX_TOKENS:
                lines = btext.splitlines()
                sub = ""
                for line in lines:
                    candidate = sub + "\n" + line if sub else line
                    if _est_tokens(candidate) > _MAX_TOKENS:
                        if sub:
                            _emit(sub, section, True)
                        sub = line
                    else:
                        sub = candidate
                if sub:
                    _emit(sub, section, True)
            else:
                _emit(btext, section, True)
            current_section = section
            continue

        # Bloco normal: tenta fundir com o atual
        candidate = current_text + "\n\n" + btext if current_text else btext

        if _est_tokens(candidate) <= _MAX_TOKENS:
            current_text = candidate
            current_section = section
            current_has_table = current_has_table or is_table
        else:
            # Emite atual e começa novo
            if current_text:
                _emit(current_text, current_section, current_has_table)
            # Bloco novo pode ser grande por si só
            if _est_tokens(btext) > _MAX_TOKENS:
                for part in _split_by_sentence(btext):
                    if part:
                        _emit(part, section, False)
                current_text = ""
                current_has_table = False
            else:
                current_text = btext
                current_has_table = False
            current_section = section

    # Emite o que restou
    if current_text:
        _emit(current_text, current_section, current_has_table)

    # Pós-processamento: funde chunks < MIN_TOKENS com o anterior
    if len(chunks) <= 1:
        return chunks

    merged: list[DocumentChunk] = [chunks[0]]
    for ch in chunks[1:]:
        prev = merged[-1]
        if ch.token_count < _MIN_TOKENS and prev.token_count + ch.token_count <= _MAX_TOKENS:
            combined = prev.text + "\n\n" + ch.text
            merged[-1] = dc_replace(
                prev,
                text=combined,
                token_count=_est_tokens(combined),
                has_table=prev.has_table or ch.has_table,
            )
        else:
            merged.append(ch)

    # Renumera índices após merge
    for i, ch in enumerate(merged):
        merged[i] = dc_replace(ch, chunk_index=start_index + i)

    return merged


# ─── SemanticChunker ──────────────────────────────────────────────────────────


class SemanticChunker:
    """Chunker semântico para HTML e texto puro (Fase 4 NOVO_RAG)."""

    # ── HTML ──────────────────────────────────────────────────────────────────

    def chunk_html(
        self,
        html: str,
        document_url: str,
        breadcrumb: str | None = None,
        page_number: int | None = None,
        start_index: int = 0,
    ) -> list[DocumentChunk]:
        """Chunka HTML usando estrutura semântica (headings, tabelas, listas)."""
        from bs4 import BeautifulSoup  # lazy import

        soup = BeautifulSoup(html, "html.parser")

        # Remove elementos não-informativos
        for tag in soup.find_all(["script", "style", "nav", "head", "footer"]):
            tag.decompose()

        blocks: list[_Block] = []
        current_section: list[str] = []  # pilha de headings para seção atual

        def _section_label() -> str | None:
            if not current_section:
                return breadcrumb
            label = current_section[-1]
            if breadcrumb:
                return f"{breadcrumb} > {label}"
            return label

        def _visit(el: object) -> None:
            name = getattr(el, "name", None)
            if name is None:
                return

            if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                heading_text = el.get_text(separator=" ", strip=True)
                if heading_text:
                    level = int(name[1])
                    # Pop headings do mesmo nível ou mais profundo
                    while current_section and len(current_section) >= level:
                        current_section.pop()
                    current_section.append(heading_text)
                return  # heading só atualiza seção, não emite bloco próprio

            if name == "p":
                text = el.get_text(separator=" ", strip=True)
                if len(text) > 20:
                    blocks.append({"text": text, "is_table": False, "section_title": _section_label()})
                return

            if name == "table":
                text = _table_to_text(el)
                if text:
                    # Tabelas herdaram a seção atual
                    blocks.append({"text": text, "is_table": True, "section_title": _section_label()})
                return  # não recursiona dentro da tabela

            if name in ("ul", "ol"):
                items = [
                    li.get_text(separator=" ", strip=True)
                    for li in el.find_all("li", recursive=False)
                ]
                items = [i for i in items if i]
                if items:
                    text = "\n".join(f"• {item}" for item in items)
                    if _est_tokens(text) <= _TABLE_MAX_TOKENS:
                        blocks.append({"text": text, "is_table": False, "section_title": _section_label()})
                    else:
                        for item in items:
                            blocks.append({"text": f"• {item}", "is_table": False, "section_title": _section_label()})
                return

            # Para div, section, article, main, body, span → recursão
            if name in ("div", "section", "article", "main", "body", "span", "td", "li"):
                for child in el.children:
                    _visit(child)
                return

        body = soup.find("body") or soup
        _visit(body)

        return _blocks_to_chunks(blocks, document_url, page_number, start_index)

    # ── Texto puro ────────────────────────────────────────────────────────────

    def chunk_plain_text(
        self,
        text: str,
        document_url: str,
        page_number: int | None = None,
        breadcrumb: str | None = None,
        start_index: int = 0,
    ) -> list[DocumentChunk]:
        """Chunka texto puro (PDFs) usando heurística de detecção de estrutura."""
        if not text.strip():
            return []

        raw_paragraphs = re.split(r"\n\s*\n", text.strip())
        blocks: list[_Block] = []
        current_section: str | None = breadcrumb

        for para in raw_paragraphs:
            para = para.strip()
            if not para:
                continue

            lines = para.splitlines()
            first_line = lines[0].strip()

            # Detecta heading: linha curta, sem ponto final, não é só número
            is_heading = (
                len(lines) == 1
                and len(first_line.split()) <= 12
                and not first_line.endswith((".", ":", ",", ";"))
                and _HEADING_RE.match(first_line)
                and len(first_line) < 100
            )

            if is_heading:
                label = first_line
                current_section = f"{breadcrumb} > {label}" if breadcrumb else label
                continue  # heading vira section_title do próximo chunk

            # Detecta tabela inline (linhas com "|")
            pipe_lines = sum(1 for l in lines if "|" in l)
            is_table = pipe_lines >= 2 and pipe_lines / max(len(lines), 1) > 0.5

            blocks.append({
                "text": para,
                "is_table": is_table,
                "section_title": current_section,
            })

        return _blocks_to_chunks(blocks, document_url, page_number, start_index)


# ─── Helpers HTML ─────────────────────────────────────────────────────────────


def _table_to_text(table_el: object) -> str:
    """Converte elemento <table> em representação textual com separador |."""
    rows_text: list[str] = []
    row_count = 0
    for tr in table_el.find_all("tr"):  # type: ignore[union-attr]
        cells = [td.get_text(separator=" ", strip=True) for td in tr.find_all(["td", "th"])]
        if cells:
            rows_text.append(" | ".join(cells))
            row_count += 1
            if row_count >= 50:  # limita tabelas muito grandes
                rows_text.append("... (tabela truncada)")
                break
    return "\n".join(rows_text)
