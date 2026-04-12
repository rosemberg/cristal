#!/bin/bash
# run_pipeline.sh — Crawler + ingestão de documentos do portal TRE-PI
#
# Uso: bash scripts/run_pipeline.sh
#
# Pré-requisitos:
#   - Docker rodando com o container cristal-db-1
#   - .env configurado na raiz do projeto
#   - Virtualenv criado (bash scripts/setup_venv.sh)

set -euo pipefail

PYTHON=".venv/bin/python3.14"
ALEMBIC=".venv/bin/alembic"
CONCURRENCY="${CRAWL_CONCURRENCY:-3}"
PIPELINE_MODE="${1:-full}"  # full | resume | skip-known

# ── Cores ─────────────────────────────────────────────────────────────────────
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Helpers ───────────────────────────────────────────────────────────────────

step_header() {
    local step="$1" total="$2" label="$3"
    local ts
    ts=$(date '+%H:%M:%S')
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${CYAN}[$step/$total]${NC} ${BOLD}${label}${NC}  ${DIM}(${ts})${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

run_silent() {
    # Executa um comando silencioso com spinner animado
    local label="$1"
    shift
    local spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local i=0
    "$@" &
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
        printf "\r  ${RED}✗${NC}  %s (exit $exit_code)\n" "$label"
        return "$exit_code"
    fi
}

die() {
    echo -e "\n  ${RED}ERRO:${NC} $*" >&2
    exit 1
}

# ── Pré-checagens ─────────────────────────────────────────────────────────────

[ -f "$PYTHON" ] || die "Virtualenv não encontrado. Execute: bash scripts/setup_venv.sh"
docker inspect cristal-db-1 > /dev/null 2>&1 || die "Container cristal-db-1 não está rodando. Suba com: docker compose up -d"

# ── Cabeçalho ─────────────────────────────────────────────────────────────────

SECONDS=0
echo ""
echo -e "${BOLD}  ╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}  ║         Cristal — Pipeline de Ingestão           ║${NC}"
echo -e "${BOLD}  ╚══════════════════════════════════════════════════╝${NC}"
echo -e "  ${DIM}Modo: ${PIPELINE_MODE} | Iniciado em $(date '+%Y-%m-%d %H:%M:%S')${NC}"

# ── Etapa 1: Migrations (sempre) ─────────────────────────────────────────────

step_header 1 "" "Migrations Alembic"
run_silent "Aplicando migrations..." "$ALEMBIC" upgrade head

# ── Modos de execução ─────────────────────────────────────────────────────────

if [ "$PIPELINE_MODE" = "reprocess" ]; then
    # ── Modo reprocess: retenta documentos com status 'error' ────────────────

    step_header 2 2 "Reprocessamento de documentos com erro"
    echo -e "  ${DIM}Documentos com status 'error' serão reprocessados.${NC}"
    "$PYTHON" -m app.adapters.inbound.cli.document_ingester --reprocess

elif [ "$PIPELINE_MODE" = "resume" ]; then
    # ── Modo resume: retoma onde parou (sem truncar, sem crawler) ─────────────

    step_header 2 2 "Retomada do pipeline (ingestão)"
    echo -e "  ${DIM}Docs stuck em 'processing' serão resetados automaticamente.${NC}"
    echo -e "  ${DIM}Concorrência: ${CONCURRENCY}${NC}"
    "$PYTHON" -m app.adapters.inbound.cli.document_ingester --resume --concurrency "$CONCURRENCY"

elif [ "$PIPELINE_MODE" = "skip-known" ]; then
    # ── Modo skip-known: crawler pula URLs já no banco, retoma ingestão ───────

    step_header 2 3 "Crawling  (skip-known)"
    echo -e "  ${DIM}URLs já no banco serão puladas.${NC}"
    echo ""
    "$PYTHON" -m app.adapters.inbound.cli.crawler --skip-known

    step_header 3 3 "Ingestão de documentos (modo resume)"
    echo -e "  ${DIM}Concorrência: ${CONCURRENCY}${NC}"
    "$PYTHON" -m app.adapters.inbound.cli.document_ingester --resume --concurrency "$CONCURRENCY"

else
    # ── Modo full (padrão): do zero ───────────────────────────────────────────

    step_header 2 4 "Limpeza do banco"
    run_silent "Truncando tabelas..." docker exec cristal-db-1 psql -U cristal -d cristal -c "
TRUNCATE TABLE document_tables, document_chunks, document_contents,
               data_inconsistencies, page_links, navigation_tree,
               documents, pages
RESTART IDENTITY CASCADE;
"

    step_header 3 4 "Crawling do portal TRE-PI"
    echo -e "  ${DIM}Progresso em tempo real abaixo:${NC}"
    echo ""
    "$PYTHON" -m app.adapters.inbound.cli.crawler --full

    step_header 4 4 "Ingestão de documentos (PDFs e CSVs)"
    echo -e "  ${DIM}Concorrência: ${CONCURRENCY}${NC}"
    "$PYTHON" -m app.adapters.inbound.cli.document_ingester --run --concurrency "$CONCURRENCY"
fi

# ── Resumo final ──────────────────────────────────────────────────────────────

mins=$(( SECONDS / 60 ))
secs=$(( SECONDS % 60 ))

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${GREEN}${BOLD}Pipeline concluído${NC} em ${BOLD}${mins}m ${secs}s${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${DIM}Status final:${NC}"
"$PYTHON" -m app.adapters.inbound.cli.document_ingester --status
echo ""
