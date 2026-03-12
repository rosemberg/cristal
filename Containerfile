FROM python:3.12-slim

WORKDIR /app

# Install deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app/ ./app/
COPY static/ ./static/

# GCP credentials mounted as Secret in OpenShift
ENV GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-sa-key.json
ENV VERTEX_PROJECT_ID=tre-pi-project
ENV VERTEX_LOCATION=us-central1
ENV VERTEX_MODEL=gemini-2.5-flash-lite
ENV PORT=8080

EXPOSE 8080

# Non-root user for OpenShift compatibility
RUN useradd -u 1001 -r -g 0 -s /sbin/nologin appuser && \
    chown -R 1001:0 /app && \
    chmod -R g=u /app
USER 1001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
