FROM python:3.11-alpine

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY --from=ghcr.io/github/github-mcp-server:latest /server/github-mcp-server /usr/local/bin/github-mcp-server

WORKDIR /app

COPY pyproject.toml uv.lock server.py ./
RUN uv sync --frozen --no-dev \
    && uv cache clean \
    && find /app -name "*.pyc" -delete \
    && find /app -name "__pycache__" -type d -exec rm -rf {} + || true

ARG GITHUB_PERSONAL_ACCESS_TOKEN
ENV GITHUB_TOOLSETS="all" \
    GITHUB_DYNAMIC_TOOLSETS=1 \
    GITHUB_READ_ONLY=1 \
    GITHUB_PERSONAL_ACCESS_TOKEN=${GITHUB_PERSONAL_ACCESS_TOKEN}

RUN adduser -D -s /bin/false appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

CMD ["uv", "run", "python", "server.py"]
