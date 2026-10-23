# Hosted mode image for Cloud Run: `linkedin-mcp serve-http` on $PORT. See deploy.md.
FROM python:3.13-slim

# Fonts for the title cards and time-zone data so TZ (e.g. Asia/Kolkata) is honoured.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Dependencies first so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra hosted --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra hosted

RUN useradd --create-home --uid 10001 app
USER app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8080

CMD ["linkedin-mcp", "serve-http"]
