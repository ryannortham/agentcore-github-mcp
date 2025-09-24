"""Streamable-HTTP wrapper around the GitHub stdio MCP server.

This adapts the GitHub MCP server (which only supports stdio transport) so it can
run inside AgentCore's runtime which requires streamable-http. We spawn the
binary, speak MCP JSON-RPC 2.0 over stdio using Content-Length framing, and
expose a single tool `github_rpc` that forwards arbitrary JSON-RPC method calls.

Binary location inside the container: /usr/local/bin/github-mcp-server

Usage (example tool invocation payload):
  method: "initialize"
  params: { "protocolVersion": "2024-11-05", ... }

The raw JSON-RPC response (including either `result` or `error`) is returned.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

GITHUB_MCP_BINARY = os.environ.get("GITHUB_MCP_BINARY", "/usr/local/bin/github-mcp-server")


class StdioMCPProxy:
    """Manage a stdio JSON-RPC (MCP) subprocess with Content-Length framing."""

    def __init__(self, binary: str):
        self.binary = binary
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._id_counter = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self.proc is not None:
            return
        try:
            self.proc = await asyncio.create_subprocess_exec(
                self.binary,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"GitHub MCP binary not found at {self.binary}") from e
        # Start background readers
        self._reader_task = asyncio.create_task(self._reader(), name="github-mcp-stdout-reader")
        asyncio.create_task(self._stderr_logger(), name="github-mcp-stderr-logger")

    async def _stderr_logger(self):
        assert self.proc is not None
        if not self.proc.stderr:
            return
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            # Forward to our stderr for visibility
            sys.stderr.write(f"[github-mcp] {line.decode(errors='replace')}")
            sys.stderr.flush()

    async def _read_headers(self) -> Optional[Dict[str, str]]:
        assert self.proc is not None
        assert self.proc.stdout is not None
        headers: Dict[str, str] = {}
        while True:
            line = await self.proc.stdout.readline()
            if not line:  # EOF
                if not headers:
                    return None
                break
            line_str = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line_str == "":  # End of headers
                break
            if ":" in line_str:
                k, v = line_str.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return headers

    async def _reader(self):
        try:
            while True:
                headers = await self._read_headers()
                if headers is None:
                    # Subprocess ended
                    break
                content_length = headers.get("content-length")
                if content_length is None:
                    continue  # Skip malformed frame
                try:
                    length = int(content_length)
                except ValueError:
                    continue
                assert self.proc and self.proc.stdout
                body = await self.proc.stdout.readexactly(length)
                try:
                    message = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                # Match requests with pending futures
                msg_id = message.get("id")
                if isinstance(msg_id, int) and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(message)
        except asyncio.IncompleteReadError:
            pass
        except Exception:
            traceback.print_exc()
        finally:
            # Fail outstanding futures if process died
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("GitHub MCP server terminated"))
            self._pending.clear()

    async def call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = 60.0) -> Dict[str, Any]:
        if self.proc is None:
            await self.start()
        assert self.proc and self.proc.stdin
        async with self._lock:
            self._id_counter += 1
            msg_id = self._id_counter
        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params
        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        frame = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
        self.proc.stdin.write(frame)
        await self.proc.stdin.drain()

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut

        try:
            response: Dict[str, Any] = await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            # Cleanup mapping if error/timeout
            self._pending.pop(msg_id, None)
            raise
        return response


# Instantiate FastMCP app & proxy
mcp = FastMCP(host="0.0.0.0", stateless_http=True)
proxy = StdioMCPProxy(GITHUB_MCP_BINARY)


@mcp.tool()
async def github_rpc(method: str, params: Optional[Dict[str, Any]] = None, timeout_seconds: int = 60) -> Dict[str, Any]:
    """Forward a JSON-RPC/MCP request to the GitHub MCP stdio server.

    Args:
        method: JSON-RPC method name (e.g. "initialize", "tools/list", etc.).
        params: Parameters object for the method (if any).
        timeout_seconds: How long to wait for a response before timing out.

    Returns:
        The raw JSON-RPC response dict (contains either `result` or `error`).
    """
    response = await proxy.call(method, params, timeout=float(timeout_seconds))
    return response


if __name__ == "__main__":
    # Expose via streamable-http so AgentCore runtime can connect.
    mcp.run(transport="streamable-http")