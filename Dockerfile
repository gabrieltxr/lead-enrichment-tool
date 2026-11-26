FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install .

COPY alembic.ini ./

RUN useradd --system --uid 10001 --no-create-home appuser
USER appuser

EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

# Imagem usada pelo docker compose para rodar os testes.
FROM base AS dev
USER root
RUN pip install ".[dev]"
COPY tests ./tests
USER appuser
