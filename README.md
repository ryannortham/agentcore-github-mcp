# AgentCore GitHub MCP

Converts the official [GitHub MCP Server](https://github.com/github/github-mcp-server) from stdio to streamable HTTP transport for [AWS Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html). 

Enables AWS Bedrock agents to access GitHub repositories, issues, PRs, and code search through MCP.

## Quick Start

### Setup venv
```bash
source .venv/bin/activate
```  

### Setup GitHub Auth
```bash
export GITHUB_PERSONAL_ACCESS_TOKEN=github_pat_XXXXX
```

### Setup AWS Bedrock AgentCore

Configure AWS credentials:
```bash
export AWS_ACCESS_KEY_ID=xxx
export AWS_SECRET_ACCESS_KEY=xxx
export AWS_SESSION_TOKEN=xxx
```
Configure AWS region:
```bash
export AWS_REGION=ap-southeast-2 
export AWS_DEFAULT_REGION=ap-southeast-2
```

Configure AgentCore runtime:
```bash
agentcore configure \
  --entrypoint src/github_mcp_agentcore \
  --name github_mcp_server \
  --container-runtime docker \
  --protocol MCP
```

> [!WARNING]  
> `agentcore configure` will overwrite the Dockerfile. Discard these changes in Git to restore the required configuration.

## Local Development

### Option A: Run locally with Docker
```bash
docker build --tag github-mcp-wrapper .

docker run \
  --publish 8080:8080 \
  --env GITHUB_PERSONAL_ACCESS_TOKEN="$GITHUB_PERSONAL_ACCESS_TOKEN" \
  --env ENABLE_OTEL=false \
  github-mcp-wrapper
```
### Option B: Run locally in AgentCore
```bash
agentcore launch \
  --local \
  --env GITHUB_PERSONAL_ACCESS_TOKEN=$GITHUB_PERSONAL_ACCESS_TOKEN \
  --env ENABLE_OTEL=false
```

> [!NOTE]  
> Set `ENABLE_OTEL=false` for local development to avoid OpenTelemetry configuration errors.

## Testing

Use MCP Inspector to test the connection:
```bash
npx @modelcontextprotocol/inspector
```
Connection Settings:
- Transport: Streamable HTTP
- URL: http://0.0.0.0:8080/mcp

## Deploy to AWS
```bash
agentcore launch --env GITHUB_PERSONAL_ACCESS_TOKEN=$GITHUB_PERSONAL_ACCESS_TOKEN
```

## Environment Variables

### Required
- `GITHUB_PERSONAL_ACCESS_TOKEN` - GitHub personal access token with repo permissions

### Optional
- `ENABLE_OTEL` - Enable OpenTelemetry instrumentation (default: `true`)
- `LOG_LEVEL` - Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`)
- `GITHUB_TOOLSETS` - MCP toolsets to enable (default: `"all"`)
- `GITHUB_DYNAMIC_TOOLSETS` - Enable dynamic toolset discovery (default: `1`)
- `GITHUB_READ_ONLY` - Read-only mode for security (default: `1`)

See the [GitHub MCP Server README](https://github.com/github/github-mcp-server/blob/main/README.md) for all available environment options.
