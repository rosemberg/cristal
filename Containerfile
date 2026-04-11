FROM python:3.12-slim

WORKDIR /app

# Deps do sistema para PyMuPDF e processamento de documentos
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

# Deps Python (layer cache separado)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código da aplicação
COPY alembic.ini .
COPY migrations/ ./migrations/
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY static/ ./static/

# Variáveis de ambiente com prefixo CRISTAL_
ENV GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-sa-key.json
ENV CRISTAL_VERTEX_PROJECT_ID=tre-pi-project
ENV CRISTAL_VERTEX_LOCATION=us-central1
ENV CRISTAL_VERTEX_MODEL=gemini-2.5-flash-lite
ENV PORT=8080

EXPOSE 8080

# Usuário não-root para compatibilidade com OpenShift (UID 1001)
RUN useradd -u 1001 -r -g 0 -s /sbin/nologin appuser && \
    chmod +x scripts/docker-entrypoint.sh && \
    chown -R 1001:0 /app && \
    chmod -R g=u /app
USER 1001

ENTRYPOINT ["scripts/docker-entrypoint.sh"]
