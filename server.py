"""Minimal streamable-http wrapper around the stdio-only GitHub MCP server.

Goal: stay as close in spirit to the simple example while still working with
the GitHub server's MCP (Content-Length framed JSON-RPC) output.

We expose a single tool `github_rpc(method, params)` that forwards the request
and returns the raw JSON-RPC response dict.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

GITHUB_MCP_BINARY = os.environ.get("GITHUB_MCP_BINARY", "/usr/local/bin/github-mcp-server")


class StdioMCPProxy:
    """Very small stdio JSON-RPC proxy.

    Uses newline-delimited JSON (NDJSON) protocol (one JSON object per line).
    Earlier versions tried LSP-style Content-Length framing; the upstream
    github-mcp-server responds with JSON parse errors to framed messages, so we
    switched to plain newline JSON which matches the simple example pattern.
    """

    def __init__(self, binary: str):
        self.binary = binary
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.counter = 0
        self.pending: Dict[int, asyncio.Future] = {}
        self.reader_task: Optional[asyncio.Task] = None
        self.initialized = False
        self.init_response: Optional[Dict[str, Any]] = None
        log_level_env = os.environ.get("LOG_LEVEL", "").lower()
        self.log_enabled = os.environ.get("GITHUB_MCP_LOG") == "1" or log_level_env in {"debug", "trace"}
        self.stderr_task: Optional[asyncio.Task] = None
        # Ring buffer for raw stdout lines (for debugging)
        self._stdout_lines: list[str] = []
        self._stdout_max = 200

    def _log(self, direction: str, message: str, data: Optional[dict] = None):
        if not self.log_enabled:
            return
        payload = ""
        if data is not None:
            try:
                payload = json.dumps(data)[:800]
            except Exception:
                payload = str(data)
        sys.stderr.write(f"[github-mcp-wrapper {direction}] {message} {payload}\n")

    async def start(self):
        if self.proc:
            return
        # Per upstream docs the local binary must be invoked with the 'stdio' arg.
        self.proc = await asyncio.create_subprocess_exec(
            self.binary,
            "stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._log("proc", "spawned binary")
        token_set = bool(os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN"))
        self._log("info", "token status", {"token_present": token_set})
        self.reader_task = asyncio.create_task(self._reader())
        if self.proc.stderr:
            self.stderr_task = asyncio.create_task(self._stderr_reader())

    async def _stderr_reader(self):
        assert self.proc and self.proc.stderr
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            if self.log_enabled:
                sys.stderr.write(f"[github-mcp-wrapper stderr] {line.decode(errors='replace').rstrip()}\n")

    async def _read_frame(self) -> Optional[dict]:
        assert self.proc and self.proc.stdout
        line = await self.proc.stdout.readline()
        if not line:
            return None
        line_s = line.decode(errors="replace").rstrip("\r\n")
        self._stdout_lines.append(line_s)
        if len(self._stdout_lines) > self._stdout_max:
            self._stdout_lines.pop(0)
        if self.log_enabled:
            sys.stderr.write(f"[github-mcp-wrapper raw] {line_s}\n")
        if not line_s:
            return {}
        try:
            return json.loads(line_s)
        except Exception:
            # Return a synthetic parse error wrapper so caller can ignore
            return {"_unparsed": line_s}

    async def _reader(self):
        while True:
            msg = await self._read_frame()
            if msg is None:
                break
            msg_id = msg.get('id')
            self._log("<=", "response", msg)
            if isinstance(msg_id, int) and msg_id in self.pending:
                fut = self.pending.pop(msg_id)
                if not fut.done():
                    fut.set_result(msg)

    async def _initialize(self):
        if self.initialized:
            return
        protocol_version = os.environ.get("GITHUB_MCP_PROTOCOL_VERSION", "2024-11-05")
        init_params = {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "agentcore-github-wrapper", "version": "0.1.0"},
        }
        resp = await self.call("initialize", init_params, auto_init=False)
        # We consider success if there's a result
        if "result" in resp:
            self.initialized = True
            self.init_response = resp
            self._log("info", "initialized", {"tools": len(resp.get("result", {}).get("tools", []))})

    async def call(self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = 30.0, auto_init: bool = True) -> Dict[str, Any]:
        if not self.proc:
            await self.start()
        assert self.proc and self.proc.stdin
        # Auto-initialize if needed
        if auto_init and method != "initialize" and not self.initialized:
            await self._initialize()

        # Some server methods expect an object, not null
        if params is None:
            params = {}

        self.counter += 1
        msg_id = self.counter
        req = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        data = (json.dumps(req, separators=(",", ":")) + "\n").encode()
        self._log("=>", "request", req)
        self.proc.stdin.write(data)
        await self.proc.stdin.drain()
        fut = asyncio.get_event_loop().create_future()
        self.pending[msg_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self.pending.pop(msg_id, None)
            self._log("error", "timeout", {"id": msg_id, "method": method})
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32001, "message": "Request timed out"}}
        except Exception as e:
            self.pending.pop(msg_id, None)
            self._log("error", "exception", {"id": msg_id, "method": method, "error": str(e)})
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32002, "message": str(e)}}


# Instantiate FastMCP app & proxy
mcp = FastMCP(host="0.0.0.0", stateless_http=True)
proxy = StdioMCPProxy(GITHUB_MCP_BINARY)


@mcp.tool()
async def github_rpc(method: str, params: Optional[Dict[str, Any]] = None, timeout_seconds: int = 30) -> Dict[str, Any]:
    """Forward a JSON-RPC/MCP request to the GitHub MCP stdio server.

    Automatically performs `initialize` once, if you call any method other than
    `initialize` first. Pass `method="initialize"` explicitly to force a new
    initialize (will update cached result).

    Set env `GITHUB_MCP_LOG=1` for verbose frame logging.
    """
    # If user explicitly calls initialize we force it (ignore cached state)
    if method == "initialize":
        proxy.initialized = False
    proxy._log("tool", "github_rpc invoked", {"method": method})
    return await proxy.call(method, params, timeout=float(timeout_seconds), auto_init=True)


@mcp.tool()
async def list_tools() -> Dict[str, Any]:
    """Initialize (if needed) then return a simplified list of tool names.

    This calls tools/list under the hood and extracts the tool names for quick
    inspection/debugging.
    """
    resp = await proxy.call("tools/list", {}, timeout=30.0, auto_init=True)
    tools = []
    try:
        tools = [t["name"] for t in resp.get("result", {}).get("tools", [])]
    except Exception:
        pass
    return {"tools": tools, "raw": resp}


@mcp.tool()
async def raw_stdout() -> Dict[str, Any]:
    """Return recent raw stdout lines from the underlying GitHub MCP process.

    Useful for debugging when frames are not being parsed.
    """
    return {"lines": proxy._stdout_lines[-50:]}


if __name__ == "__main__":
    # Expose via streamable-http so AgentCore runtime can connect.
    mcp.run(transport="streamable-http")