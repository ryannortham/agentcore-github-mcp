FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

COPY --from=ghcr.io/github/github-mcp-server:latest /server/github-mcp-server /usr/local/bin/github-mcp-server

WORKDIR /app

COPY . .
RUN uv sync --frozen --no-dev \
    && uv cache clean \
    && find /app -name "*.pyc" -delete \
    && find /app -name "__pycache__" -type d -exec rm -rf {} + || true

# See https://github.com/github/github-mcp-server/blob/main/README.md for env options
ENV GITHUB_TOOLSETS="all" \
    GITHUB_DYNAMIC_TOOLSETS=1 \
    GITHUB_READ_ONLY=1

RUN useradd --system --shell /bin/false appuser && \
    chown -R appuser:appuser /app && \
    mkdir -p /home/appuser/.cache/uv && \
    chown -R appuser:appuser /home/appuser
USER appuser

EXPOSE 8080 8000

# Use shell form to allow conditional OpenTelemetry
CMD if [ "${ENABLE_OTEL:-true}" = "true" ]; then \
        uv run opentelemetry-instrument python -m github_mcp_agentcore; \
    else \
        uv run python -m github_mcp_agentcore; \
    fi
