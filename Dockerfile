# syntax=docker/dockerfile:1
#
# AZ-700 exam trainer - web application image.
#
# Only api/ and web/ are copied in: the offline PDF-to-image build
# (api/modules/ingest, tools/build_dataset.py) needs pymupdf, runs once on
# Windows against the source PDFs, and never runs inside this container. The
# dataset it produces (SQLite catalog + PNGs) is copied onto the k3s host's
# volume separately - see docs/deploy-k3s.md.
FROM python:3.14-slim

LABEL org.opencontainers.image.source="https://github.com/divlv/exam700" \
      org.opencontainers.image.description="AZ-700 exam trainer (web)" \
      org.opencontainers.image.licenses="MIT"

# Fixed uid/gid so it matches the ownership the deployment docs ask for on
# the k3s host's bind-mounted data volume.
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Installed before the application code so this layer is cached across code
# changes that do not touch requirements.txt.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY web ./web
RUN rm -rf ./api/modules/ingest

USER appuser

ENV AZ700_DATA_DIR=/data/az700 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1

# --forwarded-allow-ips '*': the pod is only ever reached through Traefik's
# ClusterIP hop, whose pod IP is not predictable, so uvicorn's default trust
# of just 127.0.0.1 would never match and X-Forwarded-Proto would be ignored
# - silently breaking the Secure session cookie in production. There is no
# other path into this pod for a request to arrive by.
CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
