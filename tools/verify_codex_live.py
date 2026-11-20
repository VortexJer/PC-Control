"""Read-only smoke test using the command and environment registered in Codex."""
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pc_control import codex
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def verify():
    code, output = codex.run(["mcp", "get", "PC-Control", "--json"])
    if code:
        raise RuntimeError(output)
    registration = json.loads(output)
    transport = registration["transport"]
    assert registration["enabled"], "PC-Control is disabled in Codex"
    assert transport["env"]["PC_CONTROL_CLIENT"] == "codex"
    params = StdioServerParameters(command=transport["command"], args=transport.get("args", []),
                                  env={**os.environ, **transport.get("env", {})}, cwd=transport.get("cwd"))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {"windows", "look", "click", "type", "read", "open_app"} <= names
            result = await session.call_tool("windows", {})
            assert not getattr(result, "is_error", getattr(result, "isError", False)), str(result)
            assert result.content, "No response from windows"
            # Invalid input must refuse rather than execute an action.
            invalid = await session.call_tool("look", {"window": "PC_CONTROL_NONEXISTENT_WINDOW_3E71A9"})
            assert getattr(invalid, "is_error", getattr(invalid, "isError", False)), "Missing window was not rejected"
            print(f"Real MCP handshake, {len(names)} tools, windows query and invalid-window refusal passed")
    state = codex.status()
    assert state["registered"] and state["skill"] and state["agents_md"], state
    print("Installed Codex MCP verification passed")


if __name__ == "__main__":
    asyncio.run(verify())
