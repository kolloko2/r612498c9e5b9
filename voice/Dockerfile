FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/voice
COPY pyproject.toml ./
ARG INSTALL_LOCAL_PROVIDERS=false
ARG INSTALL_SILERO=false
RUN if [ "$INSTALL_SILERO" = "true" ]; then \
      pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu 'torch>=2.1,<3'; \
    fi && \
    if [ "$INSTALL_LOCAL_PROVIDERS" = "true" ] && [ "$INSTALL_SILERO" = "true" ]; then \
      pip install --no-cache-dir '.[local,silero]'; \
    elif [ "$INSTALL_LOCAL_PROVIDERS" = "true" ]; then \
      pip install --no-cache-dir '.[local]'; \
    elif [ "$INSTALL_SILERO" = "true" ]; then \
      pip install --no-cache-dir '.[silero]'; \
    else \
      pip install --no-cache-dir .; \
    fi
COPY app ./app
COPY tools ./tools
RUN groupadd --gid 10001 trainer-voice && useradd --uid 10001 --gid trainer-voice --create-home voice && mkdir -p /data/recordings /data/outbox && chown -R voice:trainer-voice /data
USER voice
ENV RECORDING_DIR=/data/recordings OUTBOX_DIR=/data/outbox
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/api/v1/health', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--ws-max-size", "65500"]
