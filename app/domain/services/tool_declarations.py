"""Declarações das tools de function calling para o DataAgent.

Formato compatível com Vertex AI (functionDeclarations).
Constante reutilizável — importada pelo DataAgent e pelo gateway.
"""

from __future__ import annotations

TOOL_DECLARATIONS: list[dict] = [
    {
        "name": "filter_rows",
        "description": (
            "Filtra linhas de uma tabela onde o valor de uma coluna "
            "atende a uma condição. Retorna a tabela filtrada."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_index": {
                    "type": "integer",
                    "description": "Índice da tabela no contexto (0-based).",
                },
                "column": {
                    "type": "string",
                    "description": "Nome exato do cabeçalho da coluna.",
                },
                "operator": {
                    "type": "string",
                    "enum": ["eq", "neq", "gt", "gte", "lt", "lte", "contains", "startswith"],
                    "description": "Operador de comparação.",
                },
                "value": {
                    "type": "string",
                    "description": "Valor para comparação (sempre string — conversão interna).",
                },
            },
            "required": ["table_index", "column", "operator", "value"],
        },
    },
    {
        "name": "count_rows",
        "description": (
            "Conta o número de linhas de uma tabela. "
            "Opcionalmente filtra por coluna/valor antes de contar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_index": {
                    "type": "integer",
                    "description": "Índice da tabela no contexto (0-based).",
                },
                "column": {
                    "type": "string",
                    "description": "Coluna para filtrar antes de contar (opcional).",
                },
                "value": {
                    "type": "string",
                    "description": "Valor para filtrar na coluna (opcional).",
                },
            },
            "required": ["table_index"],
        },
    },
    {
        "name": "sum_column",
        "description": (
            "Soma todos os valores numéricos de uma coluna. "
            "Aceita formatos BRL (R$ 1.234,56) e decimais. "
            "Retorna o total exato como Decimal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_index": {
                    "type": "integer",
                    "description": "Índice da tabela no contexto (0-based).",
                },
                "column": {
                    "type": "string",
                    "description": "Nome exato do cabeçalho da coluna numérica.",
                },
            },
            "required": ["table_index", "column"],
        },
    },
    {
        "name": "sort_rows",
        "description": (
            "Ordena as linhas de uma tabela por uma coluna. "
            "Valores numéricos e monetários são ordenados numericamente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_index": {
                    "type": "integer",
                    "description": "Índice da tabela no contexto (0-based).",
                },
                "column": {
                    "type": "string",
                    "description": "Nome exato do cabeçalho da coluna.",
                },
                "order": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "description": "Direção da ordenação. Default: desc.",
                },
            },
            "required": ["table_index", "column"],
        },
    },
    {
        "name": "extract_value",
        "description": (
            "Extrai o valor de uma célula específica. "
            "Localiza a linha pelo filtro e retorna o valor da coluna alvo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_index": {
                    "type": "integer",
                    "description": "Índice da tabela no contexto (0-based).",
                },
                "filter_column": {
                    "type": "string",
                    "description": "Coluna usada para localizar a linha.",
                },
                "filter_value": {
                    "type": "string",
                    "description": "Valor para localizar a linha (match parcial case-insensitive).",
                },
                "target_column": {
                    "type": "string",
                    "description": "Coluna da qual extrair o valor.",
                },
            },
            "required": ["table_index", "filter_column", "filter_value", "target_column"],
        },
    },
]
