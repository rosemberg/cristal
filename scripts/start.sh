#!/bin/bash
# start.sh — Configura e sobe o servidor Cristal 2.0
#
# Pré-requisitos:
#   - Docker rodando com cristal-db-1 (já ativo)
#   - .env configurado na raiz do projeto
#   - Virtualenv criado (bash scripts/setup_venv.sh)
#
# Uso:
#   bash scripts/start.sh              # sobe o servidor normalmente
#   bash scripts/start.sh --backfill   # roda backfill de embeddings antes de subir
#   bash scripts/start.sh --skip-migrations  # pula migrations (já aplicadas)

set -euo pipefail

# ── Configuração ──────────────────────────────────────────────────────────────

PYTHON=".venv/bin/python3.14"
ALEMBIC=".venv/bin/alembic"
HOST="${CRISTAL_HOST:-0.0.0.0}"
PORT="${CRISTAL_PORT:-8080}"
WORKERS="${CRISTAL_WORKERS:-1}"
LOG_LEVEL="${CRISTAL_LOG_LEVEL:-info}"

# Flags
DO_BACKFILL=false
SKIP_MIGRATIONS=false

for arg in "$@"; do
    case "$arg" in
        --backfill)          DO_BACKFILL=true ;;
        --skip-migrations)   SKIP_MIGRATIONS=true ;;
    esac
done

# ── Cores ─────────────────────────────────────────────────────────────────────

BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

step() { echo -e "\n${BOLD}${CYAN}▶ $*${NC}"; }
ok()   { echo -e "  ${GREEN}✓${NC}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "\n  ${RED}✗ ERRO:${NC} $*" >&2; exit 1; }

run_spinner() {
    local label="$1"; shift
    local spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local i=0
    "$@" > /tmp/cristal_cmd_out.txt 2>&1 &
    local pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        i=$(( (i + 1) % ${#spin} ))
        printf "\r  ${DIM}${spin:$i:1}${NC}  %s" "$label"
        sleep 0.1
    done
    wait "$pid"
    local exit_code=$?
    if [ "$exit_code" -eq 0 ]; then
        printf "\r  ${GREEN}✓${NC}  %s\n" "$label"
    else
        printf "\r  ${RED}✗${NC}  %s\n" "$label"
        echo ""
        cat /tmp/cristal_cmd_out.txt
        return "$exit_code"
    fi
}

# ── Cabeçalho ─────────────────────────────────────────────────────────────────

SECONDS=0
echo ""
echo -e "${BOLD}  ╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}  ║          Cristal 2.0 — Startup Script            ║${NC}"
echo -e "${BOLD}  ╚══════════════════════════════════════════════════╝${NC}"
echo -e "  ${DIM}Iniciado em $(date '+%Y-%m-%d %H:%M:%S')${NC}"

# ── 1. Pré-checagens ──────────────────────────────────────────────────────────

step "Verificando pré-requisitos"

[ -f "$PYTHON" ] \
    || die "Virtualenv não encontrado em .venv/\n     Execute primeiro: bash scripts/setup_venv.sh"

[ -f ".env" ] \
    || die ".env não encontrado na raiz do projeto.\n     Copie o exemplo: cp .env.example .env  e preencha os valores."

docker inspect cristal-db-1 > /dev/null 2>&1 \
    || die "Container cristal-db-1 não está rodando.\n     Suba com: docker compose up -d"

ok "Python: $($PYTHON --version 2>&1)"
ok "Docker: cristal-db-1 rodando"
ok ".env: encontrado"

# Checa se DB aceita conexões
step "Testando conexão com o banco"
DB_OK=$("$PYTHON" -c "
import asyncio, asyncpg, os
from dotenv import load_dotenv
load_dotenv()

async def check():
    url = os.getenv('CRISTAL_DATABASE_URL', 'postgresql://cristal:cristal@localhost:5432/cristal')
    dsn = url.replace('postgresql+asyncpg://', 'postgresql://')
    try:
        conn = await asyncpg.connect(dsn=dsn, timeout=5)
        v = await conn.fetchval('SELECT version()')
        await conn.close()
        print('OK:' + v[:40])
    except Exception as e:
        print('FAIL:' + str(e))

asyncio.run(check())
" 2>/dev/null)

if [[ "$DB_OK" == OK:* ]]; then
    ok "PostgreSQL: ${DB_OK#OK:}"
else
    die "Não foi possível conectar ao banco: ${DB_OK#FAIL:}"
fi

# ── 2. Credenciais GCP ────────────────────────────────────────────────────────

step "Verificando credenciais GCP (Vertex AI)"

GCP_CREDS_FILE=$(grep -E '^GOOGLE_APPLICATION_CREDENTIALS' .env | cut -d= -f2 | tr -d '"' | tr -d "'" | xargs 2>/dev/null || true)

if [ -n "$GCP_CREDS_FILE" ] && [ -f "$GCP_CREDS_FILE" ]; then
    ok "Service account: $GCP_CREDS_FILE"
elif "$PYTHON" -c "import google.auth; google.auth.default()" > /dev/null 2>&1; then
    ok "Credenciais padrão do gcloud (application-default)"
else
    warn "Credenciais GCP não encontradas."
    warn "Embeddings semânticos serão desabilitados (apenas FTS)."
    warn "Para ativar: gcloud auth application-default login"
fi

# ── 3. Migrations ─────────────────────────────────────────────────────────────

if [ "$SKIP_MIGRATIONS" = false ]; then
    step "Aplicando migrations Alembic"
    run_spinner "alembic upgrade head..." "$ALEMBIC" upgrade head

    CURRENT=$("$ALEMBIC" current 2>/dev/null | grep -oE '[a-f0-9]{12}' | head -1 || echo "?")
    ok "Schema atualizado (revision: $CURRENT)"
else
    warn "Migrations puladas (--skip-migrations)"
fi

# ── 4. Verificar dados no banco ───────────────────────────────────────────────

step "Checando dados no banco"

STATS=$("$PYTHON" -c "
import asyncio, asyncpg, os
from dotenv import load_dotenv
load_dotenv()

async def check():
    url = os.getenv('CRISTAL_DATABASE_URL', 'postgresql://cristal:cristal@localhost:5432/cristal')
    dsn = url.replace('postgresql+asyncpg://', 'postgresql://')
    conn = await asyncpg.connect(dsn=dsn)
    pages   = await conn.fetchval('SELECT COUNT(*) FROM pages')
    chunks  = await conn.fetchval('SELECT COUNT(*) FROM document_chunks')
    tables  = await conn.fetchval('SELECT COUNT(*) FROM document_tables')
    embs    = await conn.fetchval('SELECT COUNT(*) FROM embeddings')
    await conn.close()
    print(f'{pages},{chunks},{tables},{embs}')

asyncio.run(check())
" 2>/dev/null || echo "0,0,0,0")

IFS=',' read -r PAGES CHUNKS TABLES EMBS <<< "$STATS"
ok "pages=$PAGES  chunks=$CHUNKS  tables=$TABLES  embeddings=$EMBS"

if [ "$EMBS" -eq 0 ] && [ "$CHUNKS" -gt 0 ]; then
    warn "Há $CHUNKS chunks sem embeddings."
    warn "Use --backfill para gerar antes de subir, ou execute depois:"
    warn "  $PYTHON scripts/backfill_embeddings.py"
fi

# ── 5. Backfill de embeddings (opcional) ──────────────────────────────────────

if [ "$DO_BACKFILL" = true ]; then
    step "Backfill de embeddings (chunk + page + table)"

    if [ "$CHUNKS" -eq 0 ] && [ "$PAGES" -eq 0 ]; then
        warn "Sem dados para embedar. Pulando backfill."
    else
        echo -e "  ${DIM}Isso pode levar alguns minutos dependendo do volume...${NC}"
        echo ""
        "$PYTHON" scripts/backfill_embeddings.py --batch-size 100
    fi
fi

# ── 6. Liberar porta ──────────────────────────────────────────────────────────

step "Verificando porta $PORT"

PORT_PID=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
if [ -n "$PORT_PID" ]; then
    warn "Porta $PORT em uso pelo processo $PORT_PID — encerrando..."
    kill -TERM "$PORT_PID" 2>/dev/null || true
    sleep 1
    # Força se ainda estiver rodando
    if lsof -ti tcp:"$PORT" > /dev/null 2>&1; then
        kill -KILL "$PORT_PID" 2>/dev/null || true
        sleep 1
    fi
    ok "Processo anterior encerrado"
else
    ok "Porta $PORT disponível"
fi

# ── 7. Subir o servidor ───────────────────────────────────────────────────────

step "Iniciando servidor uvicorn"

mins=$(( SECONDS / 60 ))
secs=$(( SECONDS % 60 ))

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${GREEN}${BOLD}Setup concluído${NC} em ${BOLD}${mins}m ${secs}s${NC}"
echo -e "  Servidor: ${CYAN}http://${HOST}:${PORT}${NC}"
echo -e "  Workers:  ${WORKERS}"
echo -e "  Log:      ${LOG_LEVEL}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

exec "$PYTHON" -m uvicorn \
    app.adapters.inbound.fastapi.app:create_app \
    --factory \
    --host "$HOST" \
    --port "$PORT" \
    --workers "$WORKERS" \
    --log-level "$LOG_LEVEL"
