#!/bin/bash
set -e

echo "=== Cristal Bootstrap ==="

# 1. Aguardar PostgreSQL (com timeout de 60s)
echo "[1/4] Aguardando PostgreSQL..."
RETRIES=30
until python -c "
import asyncio, asyncpg, os, sys
async def check():
    dsn = os.environ.get('CRISTAL_DATABASE_URL', '')
    # Suporta postgresql+asyncpg://, postgresql://, postgres://
    for prefix in ['postgresql+asyncpg://', 'postgres://']:
        dsn = dsn.replace(prefix, 'postgresql://')
    await asyncpg.connect(dsn, timeout=5)
asyncio.run(check())
" 2>/dev/null; do
    RETRIES=$((RETRIES - 1))
    if [ "$RETRIES" -le 0 ]; then
        echo "ERRO: PostgreSQL não disponível após 60s. Abortando."
        exit 1
    fi
    echo "  PostgreSQL indisponível, tentando novamente em 2s... ($RETRIES restantes)"
    sleep 2
done
echo "  PostgreSQL disponível."

# 2. Aplicar migrations
echo "[2/4] Aplicando migrations..."
alembic upgrade head
echo "  Migrations aplicadas."

# 3. Verificar se banco tem dados (crawler)
PAGES_COUNT=$(python -c "
import asyncio, asyncpg, os
async def count():
    dsn = os.environ.get('CRISTAL_DATABASE_URL', '')
    for prefix in ['postgresql+asyncpg://', 'postgres://']:
        dsn = dsn.replace(prefix, 'postgresql://')
    conn = await asyncpg.connect(dsn)
    try:
        n = await conn.fetchval('SELECT COUNT(*) FROM pages')
    except Exception:
        n = 0
    await conn.close()
    print(n)
asyncio.run(count())
")

if [ "$PAGES_COUNT" -eq 0 ]; then
    echo "[3/4] Banco vazio — executando crawler..."
    python -m app.adapters.inbound.cli.crawler --full
else
    echo "[3/4] Banco já possui $PAGES_COUNT páginas — crawler ignorado."
fi

# 4. Ingestão de documentos (se pipeline implementado)
if python -c "import app.adapters.inbound.cli.document_ingester" 2>/dev/null; then
    echo "[4/4] Verificando documentos pendentes..."
    python -m app.adapters.inbound.cli.document_ingester --run
else
    echo "[4/4] Pipeline de ingestão não disponível — ignorado."
fi

echo "=== Bootstrap concluído ==="

# Iniciar app
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
