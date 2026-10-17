# Keep this in sync with .python-version
ARG PYTHON_VERSION=3.12

# ---- build stage: install dependencies into a virtualenv ----
FROM python:${PYTHON_VERSION}-slim AS build
# Pin this to the uv version your team uses (check with: uv --version)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Copy only the dependency manifests first so this layer is cached
# until pyproject.toml or uv.lock actually change.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---- runtime stage: only the venv and the code ----
FROM python:${PYTHON_VERSION}-slim AS prod
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src
WORKDIR /app

RUN useradd --create-home appuser

COPY --from=build /app/.venv /app/.venv

# Application code and Alembic (migrations must ship in the image)
COPY alembic.ini .
COPY ./alembic ./alembic
COPY ./src ./src

USER appuser
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]