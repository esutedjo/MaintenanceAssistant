# Judge-facing image. Default SAMPLE_MODE=true so it runs with no Zetaris, Nightshift or network.
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_SYSTEM_CERTS=1
ENV SAMPLE_MODE=true RUN_HOST=0.0.0.0 RUN_PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN mkdir -p /app/logs /app/.state

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD curl -fsS http://127.0.0.1:8000/health || exit 1
CMD ["uv", "run", "--no-dev", "python", "run.py", "--serve"]
