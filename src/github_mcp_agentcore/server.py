"""GitHub MCP server setup and tool definitions."""

import os
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

from .proxy import StdioMCPProxy

GITHUB_MCP_BINARY = os.environ.get("GITHUB_MCP_BINARY", "/usr/local/bin/github-mcp-server")


def create_server() -> FastMCP:
    """Create and configure the GitHub MCP FastMCP server."""
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

    return mcp


def main():
    """Main entry point for the GitHub MCP wrapper."""
    server = create_server()
    # Expose via streamable-http so AgentCore runtime can connect.
    server.run(transport="streamable-http")