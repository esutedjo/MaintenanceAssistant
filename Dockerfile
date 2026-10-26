# Judge-facing image. Default SAMPLE_MODE=true so it runs with no Zetaris, Nightshift or network.
# Publish on loopback only: docker run --rm -p 127.0.0.1:8000:8000 cleris
FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377
# uv from its official image at a pinned version and digest (no curl | sh).
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /uvx /bin/

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_SYSTEM_CERTS=1
ENV SAMPLE_MODE=true RUN_HOST=0.0.0.0 RUN_PORT=8000 OMP_NUM_THREADS=4

RUN apt-get update && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=app:app . .
RUN mkdir -p /app/logs /app/.state && chown -R app:app /app/logs /app/.state

# Not root at run time.
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD curl -fsS "http://127.0.0.1:${RUN_PORT}/health" || exit 1
CMD ["/app/.venv/bin/python", "run.py", "--serve"]
