"""Token benchmark: a fixed scenario through the real MCP protocol (test app on the hidden desktop), counting the tokens of the tool
definitions and of every answer. Usage: python tools/token_bench.py [repo_dir]   (needs `pip install tiktoken`; cl100k is a proxy
for Claude's tokenizer: use it to COMPARE versions, not as an absolute price)."""
import asyncio, json, os, shutil, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import tiktoken
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

enc = tiktoken.get_encoding("cl100k_base")
tok = lambda s: len(enc.encode(s))
tmp = tempfile.mkdtemp(prefix="pc-control_bench_")
host = os.path.join(tmp, "pc_control_host.exe")
shutil.copy(os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), host)
app = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "test_app2.ps1")
rows = []


def text_of(res):
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")


async def call(s, tool, **a):
    out = text_of(await s.call_tool(tool, a)); rows.append((tool, tok(out), out)); return out


async def scenario(s):
    tools = (await s.list_tools()).tools
    deftok = tok("".join(t.name + (t.description or "") + json.dumps(getattr(t, "inputSchema", None) or getattr(t, "input_schema", None)) for t in tools))
    await call(s, "open_app", command=f'"{host}" -STA -NoProfile -ExecutionPolicy Bypass -File "{app}" "{os.path.join(tmp, "s.json")}"', hidden=True)
    w = "pc-control-test2"
    for _ in range(30):
        if w in text_of(await s.call_tool("windows", {})):
            break
        await asyncio.sleep(0.5)
    lk = await call(s, "look", window=w)
    import re
    ids = {m.group(2): m.group(1) for m in re.finditer(r"\[(\d+):([^\]=]+)", lk)}
    await call(s, "type", window=w, target=ids.get("Color", "1"), text="Blue")
    await call(s, "scroll", window=w, target=ids.get("Long text", "1"), direction="down", amount=2)
    await call(s, "look", window=w, find="City 70")
    await call(s, "click", window=w, target="1")
    await call(s, "key", window=w, name="ctrl+s")
    await call(s, "key", window=w, name="ctrl+q")
    await call(s, "read", window=w, length=300)
    await call(s, "key", window=w, name="alt+f4")
    return deftok


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT,
                                   env={**os.environ, "PYTHONPATH": ROOT, "PC_CONTROL_HOME": os.path.join(tmp, "home"), "PC_CONTROL_MODE": "bypass", "PYTHONUTF8": "1"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await scenario(s)

deftok = asyncio.run(main())
for tool, n, out in rows:
    print(f"{n:5}  {tool:8} {out[:110]!r}")
print(f"definitions {deftok} | answers {sum(n for _, n, _ in rows)} tokens over {len(rows)} calls")
shutil.rmtree(tmp, ignore_errors=True)
