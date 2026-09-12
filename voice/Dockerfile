FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/voice
COPY pyproject.toml ./
COPY app ./app
COPY tools ./tools
ARG INSTALL_LOCAL_PROVIDERS=false
RUN if [ "$INSTALL_LOCAL_PROVIDERS" = "true" ]; then pip install --no-cache-dir '.[local]'; else pip install --no-cache-dir .; fi
RUN useradd --uid 10001 --create-home voice && mkdir -p /data/recordings /data/outbox && chown -R voice:voice /data
USER voice
ENV RECORDING_DIR=/data/recordings OUTBOX_DIR=/data/outbox
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/api/v1/health', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--ws-max-size", "65500"]
