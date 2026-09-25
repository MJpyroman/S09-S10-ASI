# --- Etage 1 : build des dependances dans un venv isole -------------------
FROM python:3.12 AS builder
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Etage 2 : image finale, sans toolchain de build ----------------------
FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc
RUN useradd --create-home --uid 1000 appuser \
 && mkdir -p /tmp/prometheus-multiproc \
 && chown appuser:appuser /tmp/prometheus-multiproc
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appuser app.py gunicorn.conf.py ./
USER appuser
EXPOSE 5000

# Le healthcheck Docker s'appuie sur le /health reel : urlopen leve une
# HTTPError sur 503, donc le conteneur passe unhealthy si Redis est KO.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5000/health').status==200 else 1)"

CMD ["gunicorn", "--config", "gunicorn.conf.py", "--bind", "0.0.0.0:5000", "--workers", "2", "app:app"]
