"""StdioMCPProxy - A JSON-RPC proxy for stdio-based MCP servers."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict


class StdioMCPProxy:
    """JSON-RPC proxy for stdio-based MCP servers.
    
    Communicates using newline-delimited JSON (NDJSON) protocol - one JSON object per line.
    Handles process lifecycle, message routing, and error recovery for MCP server interactions.
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