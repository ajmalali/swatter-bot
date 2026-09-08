FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    FASTEMBED_CACHE_PATH=/data/fastembed \
    SWATTER_DB_PATH=/data/swatter.db

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=20s --start-period=60s --retries=3 \
    CMD ["uv", "run", "swatter", "health"]

CMD ["uv", "run", "swatter", "run"]
