"""Minimal streamable-http wrapper around the stdio-only GitHub MCP server.

Uses newline-delimited JSON (NDJSON) protocol to communicate with the GitHub MCP server.
Provides simplified tools for interacting with GitHub toolsets.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict 

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
        self.proc: asyncio.subprocess.Process | None = None
        self.counter = 0
        self.pending: Dict[int, asyncio.Future] = {}
        self.reader_task: asyncio.Task | None = None
        self.initialized = False
        self.stderr_task: asyncio.Task | None = None
        # Check if logging is enabled via environment variables
        log_level = os.environ.get("LOG_LEVEL", "").lower()
        self.log_enabled = (os.environ.get("GITHUB_MCP_LOG") == "1" or 
                           log_level in {"debug", "trace"})

    def _log(self, direction: str, message: str, data: dict | None = None):
        if not self.log_enabled:
            return
        
        payload = ""
        if data is not None:
            try:
                payload = json.dumps(data)[:800]
            except Exception:
                payload = str(data)[:800]
        
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

    async def _read_frame(self) -> dict | None:
        assert self.proc and self.proc.stdout
        line = await self.proc.stdout.readline()
        if not line:
            return None
        
        line_s = line.decode(errors="replace").rstrip("\r\n")
        self._log("raw", line_s)
        
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
        
        init_params = {
            "protocolVersion": os.environ.get("GITHUB_MCP_PROTOCOL_VERSION", "2024-11-05"),
            "capabilities": {},
            "clientInfo": {"name": "agentcore-github-wrapper", "version": "0.1.0"},
        }
        
        resp = await self.call("initialize", init_params, auto_init=False)
        if "result" in resp:
            self.initialized = True
            tool_count = len(resp.get("result", {}).get("tools", []))
            self._log("info", "initialized", {"tools": tool_count})

    async def call(self, method: str, params: Dict[str, Any] | None = None, *, timeout: float = 30.0, auto_init: bool = True) -> Dict[str, Any]:
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
            return self._create_error_response(msg_id, -32001, "Request timed out")
        except Exception as e:
            self.pending.pop(msg_id, None)
            self._log("error", "exception", {"id": msg_id, "method": method, "error": str(e)})
            return self._create_error_response(msg_id, -32002, str(e))
    
    def _create_error_response(self, msg_id: int, code: int, message: str) -> Dict[str, Any]:
        """Create a standard JSON-RPC error response."""
        return {
            "jsonrpc": "2.0", 
            "id": msg_id, 
            "error": {"code": code, "message": message}
        }


# Instantiate FastMCP app & proxy
mcp = FastMCP(host="0.0.0.0", port=8080, stateless_http=True)
proxy = StdioMCPProxy(GITHUB_MCP_BINARY)


async def _call_github_tool(tool_name: str, arguments: Dict[str, Any], timeout_seconds: int = 30) -> Dict[str, Any]:
    """Helper function to call GitHub MCP tools."""
    params = {"name": tool_name, "arguments": arguments}
    return await proxy.call("tools/call", params, timeout=float(timeout_seconds), auto_init=True)


@mcp.tool()
async def list_available_toolsets(timeout_seconds: int = 30) -> Dict[str, Any]:
    """List all available toolsets this GitHub MCP server can offer, providing the enabled status of each.
    
    Use this when a task could be achieved with a GitHub tool and the currently available tools aren't enough.
    Call get_toolset_tools with these toolset names to discover specific tools you can call.
    """
    return await _call_github_tool("list_available_toolsets", {}, timeout_seconds)


@mcp.tool()
async def get_toolset_tools(toolset: str, timeout_seconds: int = 30) -> Dict[str, Any]:
    """List all the capabilities that are enabled with the specified toolset.
    
    Use this to get clarity on whether enabling a toolset would help you to complete a task.
    
    Args:
        toolset: The name of the toolset you want to get the tools for. Must be one of:
                context, issues, orgs, users, discussions, repos, pull_requests, actions,
                code_security, secret_protection, dependabot, experiments, notifications,
                gists, security_advisories
    """
    return await _call_github_tool("get_toolset_tools", {"toolset": toolset}, timeout_seconds)


@mcp.tool()
async def enable_toolset(toolset: str, timeout_seconds: int = 30) -> Dict[str, Any]:
    """Enable one of the sets of tools the GitHub MCP server provides.
    
    Use get_toolset_tools and list_available_toolsets first to see what this will enable.
    
    Args:
        toolset: The name of the toolset to enable. Must be one of:
                context, issues, orgs, users, discussions, repos, pull_requests, actions,
                code_security, secret_protection, dependabot, experiments, notifications,
                gists, security_advisories
    """
    return await _call_github_tool("enable_toolset", {"toolset": toolset}, timeout_seconds)


@mcp.tool()
async def call_github_tool(tool_name: str, arguments: dict | None = None, timeout_seconds: int = 30) -> Dict[str, Any]:
    """Execute any GitHub MCP tool by name with the provided arguments.
    
    This is the main interface for calling GitHub tools once you've enabled the appropriate toolsets.
    Use list_available_toolsets and get_toolset_tools first to discover what tools are available.
    
    Args:
        tool_name: The name of the GitHub tool to call (e.g., "get_repository", "create_issue", etc.)
        arguments: Dictionary of arguments to pass to the tool (tool-specific)
        timeout_seconds: How long to wait for the tool to complete
    """
    if arguments is None:
        arguments = {}
    return await _call_github_tool(tool_name, arguments, timeout_seconds)


@mcp.tool()
async def list_enabled_tools(timeout_seconds: int = 30) -> Dict[str, Any]:
    """List all currently enabled/available GitHub tools that can be called.
    
    This shows you the actual tools you can call with call_github_tool, not just the toolsets.
    """
    return await proxy.call("tools/list", {}, timeout=float(timeout_seconds), auto_init=True)


if __name__ == "__main__":
    # Expose via streamable-http so AgentCore runtime can connect.
    mcp.run(transport="streamable-http")