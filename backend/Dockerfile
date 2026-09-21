# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm AS backend-build
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY backend/api/ ./api/
COPY backend/mcp_server/ ./mcp_server/
COPY backend/pipeline/ ./pipeline/
COPY backend/reads/ ./reads/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHON_DOTENV_DISABLED=1
RUN useradd --create-home --uid 10001 app
WORKDIR /app/backend
COPY --from=backend-build /app/backend /app/backend
COPY LICENSE NOTICE /app/
COPY --chmod=755 backend/docker-entrypoint.sh /usr/local/bin/continuum-entrypoint
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
ENTRYPOINT ["continuum-entrypoint"]
CMD ["novel-webapp", "--host", "0.0.0.0", "--port", "8000"]
