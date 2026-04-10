"""Value object: QueryIntent enum."""

from __future__ import annotations

from enum import StrEnum


class QueryIntent(StrEnum):
    GENERAL_SEARCH = "busca_geral"
    DOCUMENT_QUERY = "consulta_documento"
    DATA_QUERY = "consulta_dados"
    NAVIGATION = "navegacao"
    FOLLOWUP = "followup"
