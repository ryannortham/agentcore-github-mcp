# Multi-stage build for GitHub MCP server on AgentCore Runtime
FROM ghcr.io/github/github-mcp-server:latest AS github-mcp-server

# Python environment stage
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy GitHub MCP server binary from first stage
COPY --from=github-mcp-server /server/github-mcp-server /usr/local/bin/github-mcp-server

# Set working directory
WORKDIR /app

# Copy Python project files
COPY pyproject.toml uv.lock ./

# Install Python dependencies
RUN uv sync --frozen --no-dev

# Copy server code
COPY server.py ./

# Set environment variables for GitHub MCP server
ARG GITHUB_PERSONAL_ACCESS_TOKEN
ENV GITHUB_TOOLSETS="all" \
    GITHUB_DYNAMIC_TOOLSETS=1 \
    GITHUB_READ_ONLY=1 \
    GITHUB_PERSONAL_ACCESS_TOKEN=${GITHUB_PERSONAL_ACCESS_TOKEN}

# Expose port for AgentCore
EXPOSE 8000

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start the GitHub MCP server
CMD ["uv", "run", "python", "server.py"]
