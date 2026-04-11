-- ============================================================================
-- Limpeza de tabelas corrompidas no banco do Cristal 2.0
--
-- Causa: CsvProcessor detectava separador errado, produzindo tabelas com
-- 1 coluna (dados concatenados com ;) ou headers contendo separadores brutos.
--
-- Uso:
--   1. Rodar SELECT primeiro para inspecionar os registros afetados
--   2. Confirmar que são realmente corrompidos
--   3. Rodar os DELETEs dentro de uma transação
--   4. Re-ingerir os documentos CSV afetados com o CsvProcessor corrigido
-- ============================================================================

-- ─── Passo 1: Diagnosticar ──────────────────────────────────────────────────

-- Tabelas com 1 coluna ou menos (parse com separador errado)
SELECT id, document_url, num_cols, num_rows, caption,
       LEFT(headers::text, 120) AS headers_preview
FROM document_tables
WHERE num_cols <= 1
ORDER BY id;

-- Tabelas cujos headers contêm separadores brutos (; ou |||)
SELECT id, document_url, num_cols, num_rows, caption,
       LEFT(headers::text, 120) AS headers_preview
FROM document_tables
WHERE headers::text LIKE '%;%'
   OR headers::text LIKE '%|||%'
ORDER BY id;

-- Resumo: total de tabelas corrompidas vs total
SELECT
    COUNT(*) FILTER (WHERE num_cols <= 1 OR headers::text LIKE '%;%' OR headers::text LIKE '%|||%')
        AS corrupted,
    COUNT(*) AS total
FROM document_tables;

-- ─── Passo 2: Listar documentos afetados (para re-ingestão posterior) ───────

SELECT DISTINCT document_url
FROM document_tables
WHERE num_cols <= 1
   OR headers::text LIKE '%;%'
   OR headers::text LIKE '%|||%'
ORDER BY document_url;

-- ─── Passo 3: Limpeza (rodar dentro de transação) ──────────────────────────

BEGIN;

-- Deletar chunks dos documentos que tinham tabelas corrompidas
DELETE FROM document_chunks
WHERE document_url IN (
    SELECT DISTINCT document_url
    FROM document_tables
    WHERE num_cols <= 1
       OR headers::text LIKE '%;%'
       OR headers::text LIKE '%|||%'
);

-- Deletar as tabelas corrompidas
DELETE FROM document_tables
WHERE num_cols <= 1
   OR headers::text LIKE '%;%'
   OR headers::text LIKE '%|||%';

-- Deletar os registros de document_contents dos documentos afetados
-- para permitir re-ingestão limpa
DELETE FROM document_contents
WHERE document_url IN (
    SELECT DISTINCT document_url
    FROM document_tables
    WHERE num_cols <= 1
       OR headers::text LIKE '%;%'
       OR headers::text LIKE '%|||%'
);

COMMIT;

-- ─── Passo 4: Verificar ────────────────────────────────────────────────────

SELECT COUNT(*) AS remaining_corrupt
FROM document_tables
WHERE num_cols <= 1
   OR headers::text LIKE '%;%'
   OR headers::text LIKE '%|||%';
