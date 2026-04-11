"""Router: Documents — GET /api/documents/..."""

from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query

from app.adapters.inbound.fastapi.dependencies import get_document_use_case
from app.adapters.inbound.fastapi.schemas import (
    DocumentContentResponse,
    DocumentListResponse,
    DocumentOut,
    DocumentTablesResponse,
    TableOut,
)
from app.domain.ports.inbound.document_use_case import DocumentUseCase

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    category: str | None = Query(default=None),
    doc_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    doc_uc: DocumentUseCase = Depends(get_document_use_case),
) -> DocumentListResponse:
    """Lista documentos com filtros opcionais."""
    docs = await doc_uc.list_documents(
        category=category, doc_type=doc_type, page=page, size=size
    )
    return DocumentListResponse(
        documents=[
            DocumentOut(
                id=d.id,
                page_url=d.page_url,
                document_url=d.document_url,
                type=d.type,
                is_processed=d.is_processed,
                title=d.title,
                num_pages=d.num_pages,
            )
            for d in docs
        ]
    )


@router.get("/{url:path}/content", response_model=DocumentContentResponse)
async def get_document_content(
    url: str,
    doc_uc: DocumentUseCase = Depends(get_document_use_case),
) -> DocumentContentResponse:
    """Retorna o conteúdo textual extraído de um documento."""
    decoded_url = unquote(url)
    content = await doc_uc.get_content(decoded_url)
    if content is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return DocumentContentResponse(content=content)


@router.get("/{url:path}/tables", response_model=DocumentTablesResponse)
async def get_document_tables(
    url: str,
    doc_uc: DocumentUseCase = Depends(get_document_use_case),
) -> DocumentTablesResponse:
    """Retorna as tabelas extraídas de um documento."""
    decoded_url = unquote(url)
    tables = await doc_uc.get_tables(decoded_url)
    return DocumentTablesResponse(
        tables=[
            TableOut(
                id=t.id,
                table_index=t.table_index,
                headers=t.headers,
                rows=t.rows,
                caption=t.caption,
                page_number=t.page_number,
            )
            for t in tables
        ]
    )


@router.get("/{url:path}", response_model=DocumentOut)
async def get_document(
    url: str,
    doc_uc: DocumentUseCase = Depends(get_document_use_case),
) -> DocumentOut:
    """Retorna metadados de um documento pelo URL."""
    decoded_url = unquote(url)
    doc = await doc_uc.get(decoded_url)
    if doc is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return DocumentOut(
        id=doc.id,
        page_url=doc.page_url,
        document_url=doc.document_url,
        type=doc.type,
        is_processed=doc.is_processed,
        title=doc.title,
        num_pages=doc.num_pages,
    )
