#!/bin/bash
# setup_venv.sh — Cria e inicializa o ambiente virtual Python do Cristal
#
# Uso:
#   bash scripts/setup_venv.sh          # instala dependências de produção
#   bash scripts/setup_venv.sh --dev    # instala também dependências de dev/test
#   bash scripts/setup_venv.sh --reset  # recria o venv do zero antes de instalar

set -euo pipefail

# ─── Configuração ─────────────────────────────────────────────────────────────

VENV_DIR=".venv"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11
DEV_MODE=false
RESET_MODE=false

# ─── Argumentos ───────────────────────────────────────────────────────────────

for arg in "$@"; do
    case "$arg" in
        --dev)   DEV_MODE=true ;;
        --reset) RESET_MODE=true ;;
        --help|-h)
            echo "Uso: bash scripts/setup_venv.sh [--dev] [--reset]"
            echo "  --dev    Instala dependências de desenvolvimento e teste"
            echo "  --reset  Recria o venv do zero antes de instalar"
            exit 0
            ;;
        *)
            echo "Opção desconhecida: $arg  (use --help)"
            exit 1
            ;;
    esac
done

# ─── Cabeçalho ────────────────────────────────────────────────────────────────

echo
echo "╔══════════════════════════════════════════╗"
echo "║   Cristal — Setup do Ambiente Virtual    ║"
echo "╚══════════════════════════════════════════╝"
echo

# ─── Localiza Python 3.11+ ────────────────────────────────────────────────────

find_python() {
    # Candidatos em ordem de preferência
    local candidates=(
        python3.14 python3.13 python3.12 python3.11
        /opt/homebrew/opt/python@3.14/bin/python3.14
        /opt/homebrew/opt/python@3.13/bin/python3.13
        /opt/homebrew/opt/python@3.12/bin/python3.12
        /opt/homebrew/opt/python@3.11/bin/python3.11
        /usr/local/bin/python3
        python3
    )
    for candidate in "${candidates[@]}"; do
        if command -v "$candidate" &>/dev/null; then
            local version
            version=$("$candidate" -c "import sys; print(sys.version_info.minor + sys.version_info.major * 100)" 2>/dev/null || echo 0)
            local required=$(( MIN_PYTHON_MAJOR * 100 + MIN_PYTHON_MINOR ))
            if [ "$version" -ge "$required" ] 2>/dev/null; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_python || true)

if [ -z "$PYTHON" ]; then
    echo "ERRO: Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ não encontrado."
    echo "Instale com: brew install python@3.14"
    exit 1
fi

PYTHON_VERSION=$("$PYTHON" --version 2>&1)
echo "Python:       $PYTHON_VERSION  ($PYTHON)"

# ─── Venv ─────────────────────────────────────────────────────────────────────

if [ "$RESET_MODE" = true ] && [ -d "$VENV_DIR" ]; then
    echo "Removendo venv anterior ($VENV_DIR)..."
    rm -rf "$VENV_DIR"
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Criando virtualenv em $VENV_DIR..."
    "$PYTHON" -m venv "$VENV_DIR"
else
    echo "Venv existente: $VENV_DIR  (use --reset para recriar)"
fi

PIP="$VENV_DIR/bin/pip"

# ─── Dependências ─────────────────────────────────────────────────────────────

echo "Atualizando pip..."
"$PIP" install --upgrade pip --quiet

if [ "$DEV_MODE" = true ]; then
    echo "Instalando dependências de dev (requirements-dev.txt)..."
    "$PIP" install -r requirements-dev.txt
else
    echo "Instalando dependências de produção (requirements.txt)..."
    "$PIP" install -r requirements.txt
fi

# ─── Resumo ───────────────────────────────────────────────────────────────────

echo
echo "══════════════════════════════════════════════"
echo "  Concluído!"
echo
echo "  Para ativar o ambiente neste terminal:"
echo "    source $VENV_DIR/bin/activate"
echo
echo "  Ou execute diretamente sem ativar:"
echo "    $VENV_DIR/bin/python -m <modulo>"
echo "    $VENV_DIR/bin/pytest tests/"
echo
echo "  Para iniciar a aplicação:"
echo "    source $VENV_DIR/bin/activate && uvicorn app.main:app --reload"
echo "══════════════════════════════════════════════"
echo
