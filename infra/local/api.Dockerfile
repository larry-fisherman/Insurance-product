FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.13-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1
WORKDIR /build
COPY services/backend/pyproject.toml services/backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY services/backend/app ./app
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr libglib2.0-0 libnss3 && rm -rf /var/lib/apt/lists/* && useradd --create-home --uid 10001 app
RUN mkdir /data && chown app:app /data
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --chown=app:app services/backend/alembic.ini ./alembic.ini
COPY --chown=app:app services/backend/migrations ./migrations
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=2)"
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
