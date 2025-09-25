# AgentCore GitHub MCP

Converts the official [GitHub MCP Server](https://github.com/github/github-mcp-server) from stdio to streamable HTTP transport for [AWS Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html). 

Enables AWS Bedrock agents to access GitHub repositories, issues, PRs, and code search through MCP.

## Quick Start

```bash
# Activate uv
source .venv/bin/activate
uv sync

# Setup AWS
export AWS_ACCESS_KEY_ID=xxx
export AWS_SECRET_ACCESS_KEY=xxx
export AWS_SESSION_TOKEN=xxx

export AWS_REGION=ap-southeast-2 
export AWS_DEFAULT_REGION=ap-southeast-2


# Configure AgentCore runtime
agentcore configure --entrypoint server.py --name github_mcp_server --container-runtime docker --protocol MCP

# Set GitHub token
export GITHUB_PERSONAL_ACCESS_TOKEN=github_pat_XXXXX

# Build and run
docker build -t github-mcp-wrapper .
docker run -p 8080:8080 -e GITHUB_PERSONAL_ACCESS_TOKEN="$GITHUB_PERSONAL_ACCESS_TOKEN" github-mcp-wrapper

# Deploy to AgentCore
agentcore launch -l --env GITHUB_PERSONAL_ACCESS_TOKEN=$GITHUB_PERSONAL_ACCESS_TOKEN
```
### Important:
> Running `agentcore configure` will overwrite the Dockerfile. Make sure to discard these changes in Git to restore the required Dockerfile configuration.

## Testing

```bash
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP
# URL: http://0.0.0.0:8080/mcp
```

## Environment Variables

### Required
- `GITHUB_PERSONAL_ACCESS_TOKEN` - GitHub personal access token with repo permissions

### Optional
- `LOG_LEVEL` - Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`)
- `GITHUB_TOOLSETS` - MCP toolsets to enable (default: `"all"`)
- `GITHUB_DYNAMIC_TOOLSETS` - Enable dynamic toolset discovery (default: `1`)
- `GITHUB_READ_ONLY` - Read-only mode for security (default: `1`)

See the [GitHub MCP Server README](https://github.com/github/github-mcp-server/blob/main/README.md) for all available environment options.
