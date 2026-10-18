FROM ghcr.io/astral-sh/uv:0.13.1 AS uv
FROM python:3.12.14-slim-trixie

COPY --from=uv /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./
COPY LICENSE README.md ./
RUN useradd --uid 10001 --create-home miroven && mkdir -p /data/blobs && chown miroven:miroven /data/blobs
USER miroven
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["python", "-m", "app.server"]
