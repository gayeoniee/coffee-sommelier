# Backend API image (FastAPI + LangGraph) for Render's free plan (512 MB RAM).
# Only the API's runtime deps are installed: the data pipeline's group (pandas, bs4, ...) is skipped.
#   docker build -t coffee-api .
#   docker run -p 8000:8000 -e DATABASE_URL=... -e NVIDIA_API_KEY=... coffee-api

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-default-groups --no-install-project

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /srv
COPY --from=build /srv/.venv /srv/.venv
COPY config ./config
COPY pipeline ./pipeline
COPY app ./app
ENV PATH=/srv/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000
USER app
EXPOSE 8000
# Render injects $PORT. One worker keeps memory low; the proxy headers make request.url honour HTTPS.
CMD ["sh", "-c", "exec uvicorn app.api:get_app --factory --host 0.0.0.0 --port \"$PORT\" --workers 1 --proxy-headers --forwarded-allow-ips '*' --timeout-keep-alive 65"]
