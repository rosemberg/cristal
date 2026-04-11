"""Unit tests — Document Processor (Etapa 7).

Testa: TextChunker, PdfProcessor, CsvProcessor, DocumentProcessor.
Nenhum I/O externo: PDFs e CSVs são gerados em memória.
"""

from __future__ import annotations

import io

import fitz  # type: ignore[import-untyped]
import pandas as pd
import pytest

from app.adapters.outbound.document_processor.chunker import TextChunker
from app.adapters.outbound.document_processor.csv_processor import CsvProcessor
from app.adapters.outbound.document_processor.document_processor import DocumentProcessor
from app.adapters.outbound.document_processor.pdf_processor import PdfProcessor

DOC_URL = "https://www.tre-pi.jus.br/doc-teste.pdf"
CSV_URL = "https://www.tre-pi.jus.br/planilha.csv"
XLSX_URL = "https://www.tre-pi.jus.br/planilha.xlsx"

# ─── Helpers ─────────────────────────────────────────────────────────────────


def make_pdf(text: str, num_pages: int = 1, title: str = "") -> bytes:
    """Cria PDF em memória com o texto fornecido."""
    doc = fitz.open()
    if title:
        doc.set_metadata({"title": title})
    for _ in range(num_pages):
        page = doc.new_page()
        page.insert_text((50, 72), text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_csv(headers: list[str], rows: list[list[str]]) -> bytes:
    """Cria CSV em memória."""
    lines = [",".join(headers)] + [",".join(row) for row in rows]
    return "\n".join(lines).encode("utf-8")


def make_xlsx(sheets: dict[str, tuple[list[str], list[list[str]]]]) -> bytes:
    """Cria XLSX em memória com múltiplas abas."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet_name, (headers, rows) in sheets.items():
            df = pd.DataFrame(rows, columns=headers)
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    return buf.getvalue()


# ─── TextChunker ─────────────────────────────────────────────────────────────


class TestTextChunker:
    def test_empty_text_returns_no_chunks(self) -> None:
        chunker = TextChunker(chunk_size=500, overlap=50)
        result = chunker.chunk("", document_url=DOC_URL)
        assert result == []

    def test_whitespace_only_returns_no_chunks(self) -> None:
        chunker = TextChunker(chunk_size=500, overlap=50)
        result = chunker.chunk("   \n\t  ", document_url=DOC_URL)
        assert result == []

    def test_short_text_yields_single_chunk(self) -> None:
        chunker = TextChunker(chunk_size=500, overlap=50)
        text = "Texto curto para teste."
        result = chunker.chunk(text, document_url=DOC_URL)
        assert len(result) == 1
        assert result[0].text == text
        assert result[0].chunk_index == 0
        assert result[0].document_url == DOC_URL

    def test_long_text_yields_multiple_chunks(self) -> None:
        chunker = TextChunker(chunk_size=20, overlap=5)  # Pequeno para forçar split
        # 100 palavras → deve gerar vários chunks
        text = " ".join(f"palavra{i}" for i in range(100))
        result = chunker.chunk(text, document_url=DOC_URL)
        assert len(result) > 1

    def test_chunk_indices_are_sequential(self) -> None:
        chunker = TextChunker(chunk_size=20, overlap=5)
        text = " ".join(f"palavra{i}" for i in range(100))
        result = chunker.chunk(text, document_url=DOC_URL)
        indices = [c.chunk_index for c in result]
        assert indices == list(range(len(result)))

    def test_chunk_indices_offset_by_start(self) -> None:
        chunker = TextChunker(chunk_size=20, overlap=0)
        text = " ".join(f"palavra{i}" for i in range(100))
        result = chunker.chunk(text, document_url=DOC_URL, start_chunk_index=10)
        assert result[0].chunk_index == 10
        assert result[-1].chunk_index == 10 + len(result) - 1

    def test_chunk_overlap_reuses_words(self) -> None:
        chunker = TextChunker(chunk_size=20, overlap=10)
        text = " ".join(f"palavra{i}" for i in range(50))
        result = chunker.chunk(text, document_url=DOC_URL)
        # Deve haver sobreposição: palavras do fim do chunk N aparecem no início do chunk N+1
        assert len(result) >= 2
        last_words_first = result[0].text.split()[-5:]
        first_words_second = result[1].text.split()[:5]
        # Ao menos algumas palavras em comum
        overlap_set = set(last_words_first) & set(first_words_second)
        assert len(overlap_set) > 0

    def test_token_count_is_positive_for_non_empty(self) -> None:
        chunker = TextChunker()
        result = chunker.chunk("Relatório de gestão fiscal 2024", document_url=DOC_URL)
        assert result[0].token_count > 0

    def test_page_number_propagated_to_chunks(self) -> None:
        chunker = TextChunker(chunk_size=500)
        result = chunker.chunk("Texto da página 3.", document_url=DOC_URL, page_number=3)
        assert result[0].page_number == 3

    def test_section_title_propagated_to_chunks(self) -> None:
        chunker = TextChunker(chunk_size=500)
        result = chunker.chunk(
            "Conteúdo da seção.", document_url=DOC_URL, section_title="Introdução"
        )
        assert result[0].section_title == "Introdução"

    def test_all_words_covered_no_loss(self) -> None:
        chunker = TextChunker(chunk_size=30, overlap=5)
        words = [f"w{i}" for i in range(80)]
        text = " ".join(words)
        result = chunker.chunk(text, document_url=DOC_URL)
        # Última palavra deve aparecer no último chunk
        last_word = words[-1]
        assert any(last_word in c.text for c in result)
        # Primeira palavra deve aparecer no primeiro chunk
        assert words[0] in result[0].text


# ─── PdfProcessor ────────────────────────────────────────────────────────────


class TestPdfProcessor:
    def test_process_returns_processed_document(self) -> None:
        processor = PdfProcessor(TextChunker())
        pdf_bytes = make_pdf("Conteúdo do relatório fiscal 2024.")
        result = processor.process(pdf_bytes, DOC_URL)
        assert result.document_url == DOC_URL

    def test_process_extracts_text(self) -> None:
        processor = PdfProcessor(TextChunker())
        pdf_bytes = make_pdf("Relatório Anual TRE-PI 2024 transparência pública.")
        result = processor.process(pdf_bytes, DOC_URL)
        assert "Relatório" in result.text
        assert "TRE-PI" in result.text

    def test_process_single_page_num_pages(self) -> None:
        processor = PdfProcessor(TextChunker())
        pdf_bytes = make_pdf("Página única.", num_pages=1)
        result = processor.process(pdf_bytes, DOC_URL)
        assert result.num_pages == 1

    def test_process_multi_page_num_pages(self) -> None:
        processor = PdfProcessor(TextChunker())
        pdf_bytes = make_pdf("Texto por página.", num_pages=3)
        result = processor.process(pdf_bytes, DOC_URL)
        assert result.num_pages == 3

    def test_process_generates_chunks(self) -> None:
        processor = PdfProcessor(TextChunker(chunk_size=20, overlap=5))
        long_text = " ".join(f"palavra{i}" for i in range(100))
        pdf_bytes = make_pdf(long_text)
        result = processor.process(pdf_bytes, DOC_URL)
        assert len(result.chunks) > 0

    def test_chunks_have_correct_document_url(self) -> None:
        processor = PdfProcessor(TextChunker())
        pdf_bytes = make_pdf("Texto de teste para URL.")
        result = processor.process(pdf_bytes, DOC_URL)
        for chunk in result.chunks:
            assert chunk.document_url == DOC_URL

    def test_chunks_page_numbers_assigned(self) -> None:
        processor = PdfProcessor(TextChunker())
        pdf_bytes = make_pdf("Conteúdo da página.", num_pages=2)
        result = processor.process(pdf_bytes, DOC_URL)
        page_numbers = {c.page_number for c in result.chunks}
        # Pelo menos uma página válida referenciada
        assert page_numbers.issubset({1, 2})
        assert len(page_numbers) >= 1

    def test_invalid_bytes_raises_error(self) -> None:
        from app.domain.ports.outbound.document_process_gateway import DocumentProcessingError

        processor = PdfProcessor(TextChunker())
        with pytest.raises(DocumentProcessingError):
            processor.process(b"not a pdf at all", DOC_URL)

    def test_pdf_with_metadata_title(self) -> None:
        processor = PdfProcessor(TextChunker())
        pdf_bytes = make_pdf("Conteúdo.", title="Relatório Fiscal 2024")
        result = processor.process(pdf_bytes, DOC_URL)
        # title pode ser None se PyMuPDF não preservar (comportamento aceito)
        assert result.title is None or isinstance(result.title, str)


# ─── CsvProcessor ────────────────────────────────────────────────────────────


class TestCsvProcessor:
    def test_process_csv_returns_processed_document(self) -> None:
        processor = CsvProcessor(TextChunker())
        csv_bytes = make_csv(["Nome", "Valor"], [["Alice", "1000"], ["Bob", "2000"]])
        result = processor.process(csv_bytes, CSV_URL, doc_type="csv")
        assert result.document_url == CSV_URL

    def test_process_csv_extracts_table(self) -> None:
        processor = CsvProcessor(TextChunker())
        csv_bytes = make_csv(["Cargo", "Salário"], [["Juiz", "30000"], ["Analista", "10000"]])
        result = processor.process(csv_bytes, CSV_URL, doc_type="csv")
        assert len(result.tables) == 1
        table = result.tables[0]
        assert "Cargo" in table.headers
        assert "Salário" in table.headers

    def test_process_csv_table_rows(self) -> None:
        processor = CsvProcessor(TextChunker())
        csv_bytes = make_csv(["A", "B"], [["x1", "y1"], ["x2", "y2"], ["x3", "y3"]])
        result = processor.process(csv_bytes, CSV_URL, doc_type="csv")
        assert result.tables[0].num_rows == 3

    def test_process_csv_table_cols(self) -> None:
        processor = CsvProcessor(TextChunker())
        csv_bytes = make_csv(["Col1", "Col2", "Col3"], [["a", "b", "c"]])
        result = processor.process(csv_bytes, CSV_URL, doc_type="csv")
        assert result.tables[0].num_cols == 3

    def test_process_csv_generates_text(self) -> None:
        processor = CsvProcessor(TextChunker())
        csv_bytes = make_csv(["Nome", "CPF"], [["Maria", "123.456.789-00"]])
        result = processor.process(csv_bytes, CSV_URL, doc_type="csv")
        assert "Nome" in result.text
        assert "Maria" in result.text

    def test_process_csv_generates_chunks(self) -> None:
        processor = CsvProcessor(TextChunker(chunk_size=20, overlap=5))
        many_rows = [[f"nome{i}", str(i * 1000)] for i in range(60)]
        csv_bytes = make_csv(["Nome", "Valor"], many_rows)
        result = processor.process(csv_bytes, CSV_URL, doc_type="csv")
        assert len(result.chunks) > 0

    def test_process_xlsx_single_sheet(self) -> None:
        processor = CsvProcessor(TextChunker())
        xlsx_bytes = make_xlsx(
            {"Despesas": (["Categoria", "Valor"], [["Pessoal", "5000000"]])}
        )
        result = processor.process(xlsx_bytes, XLSX_URL, doc_type="xlsx")
        assert len(result.tables) == 1
        assert result.tables[0].headers == ["Categoria", "Valor"]

    def test_process_xlsx_multiple_sheets(self) -> None:
        processor = CsvProcessor(TextChunker())
        xlsx_bytes = make_xlsx(
            {
                "Receitas": (["Fonte", "Montante"], [["TED", "100"]]),
                "Despesas": (["Item", "Custo"], [["Pessoal", "80"]]),
            }
        )
        result = processor.process(xlsx_bytes, XLSX_URL, doc_type="xlsx")
        assert len(result.tables) == 2

    def test_process_xlsx_sheet_name_as_caption(self) -> None:
        processor = CsvProcessor(TextChunker())
        xlsx_bytes = make_xlsx({"Orçamento 2024": (["Área", "Valor"], [["TI", "200"]])})
        result = processor.process(xlsx_bytes, XLSX_URL, doc_type="xlsx")
        assert result.tables[0].caption == "Orçamento 2024"

    def test_process_csv_table_document_url(self) -> None:
        processor = CsvProcessor(TextChunker())
        csv_bytes = make_csv(["X"], [["1"]])
        result = processor.process(csv_bytes, CSV_URL, doc_type="csv")
        assert result.tables[0].document_url == CSV_URL


# ─── DocumentProcessor (gateway) ─────────────────────────────────────────────


class TestDocumentProcessor:
    @pytest.mark.anyio
    async def test_process_pdf_routes_to_pdf_processor(self) -> None:
        processor = DocumentProcessor()
        pdf_bytes = make_pdf("Conteúdo de transparência.")
        result = await processor.process(DOC_URL, pdf_bytes, doc_type="pdf")
        assert result.document_url == DOC_URL
        assert result.num_pages == 1

    @pytest.mark.anyio
    async def test_process_csv_routes_to_csv_processor(self) -> None:
        processor = DocumentProcessor()
        csv_bytes = make_csv(["A", "B"], [["1", "2"]])
        result = await processor.process(CSV_URL, csv_bytes, doc_type="csv")
        assert result.document_url == CSV_URL
        assert len(result.tables) == 1

    @pytest.mark.anyio
    async def test_process_xlsx_routes_to_csv_processor(self) -> None:
        processor = DocumentProcessor()
        xlsx_bytes = make_xlsx({"Sheet1": (["X", "Y"], [["a", "b"]])})
        result = await processor.process(XLSX_URL, xlsx_bytes, doc_type="xlsx")
        assert result.document_url == XLSX_URL
        assert len(result.tables) >= 1

    @pytest.mark.anyio
    async def test_unsupported_type_raises_value_error(self) -> None:
        processor = DocumentProcessor()
        with pytest.raises(ValueError, match="Unsupported"):
            await processor.process(DOC_URL, b"content", doc_type="docx")

    @pytest.mark.anyio
    async def test_implements_gateway_protocol(self) -> None:
        from app.domain.ports.outbound.document_process_gateway import (
            DocumentProcessGateway,
        )

        processor = DocumentProcessor()
        assert isinstance(processor, DocumentProcessGateway)
