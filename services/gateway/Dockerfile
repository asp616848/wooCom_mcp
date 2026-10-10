# gateway image (DESIGN §10.2). Build from the repo root: docker build -f services/gateway/Dockerfile .
FROM python:3.14.7-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
RUN uv sync --locked --no-dev --no-editable --package wmg-gateway

FROM python:3.14.7-slim-trixie
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001
EXPOSE 8080
CMD ["uvicorn", "wmg_gateway.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log", "--timeout-graceful-shutdown", "10"]
