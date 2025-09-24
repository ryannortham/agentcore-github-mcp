# GitHub MCP Server Adapter

Streamable-HTTP wrapper around the upstream `github-mcp-server` (which only supports stdio) so it can run inside AgentCore runtimes requiring `streamable-http` transport.

## How it works

`server.py` launches the stdio-only GitHub MCP binary (`/usr/local/bin/github-mcp-server` by default) and forwards JSON-RPC 2.0 / MCP messages using a single FastMCP tool: `github_rpc`.

Framing uses `Content-Length` headers (standard MCP / LSP style). Responses are returned verbatim (including either `result` or `error`).

## Environment

- Python >= 3.10
- Binary path can be overridden with env var `GITHUB_MCP_BINARY` (default `/usr/local/bin/github-mcp-server`).

## Install deps

```bash
pip install -e .
```

(If using uv or pipx adjust accordingly.)

## Run server

```bash
python server.py
```
This starts FastMCP on `0.0.0.0` with `streamable-http` transport (stateless_http mode).

## Example tool invocation (pseudo JSON)

```json
{
  "method": "github_rpc",
  "params": {
    "method": "initialize",
    "params": {"protocolVersion": "2024-11-05"},
    "timeout_seconds": 30
  }
}
```

The returned value will be the raw JSON-RPC response from the GitHub MCP server.

Subsequent calls might include methods like `tools/list`, `tools/call`, etc., based on the GitHub MCP server's API surface.

## Notes

- STDERR from the underlying binary is forwarded with a `[github-mcp]` prefix for observability.
- Timeouts default to 60s; adjust via `timeout_seconds` parameter.
- Outstanding pending requests are failed if the subprocess exits.

## License

MIT (follow upstream licensing as applicable).
