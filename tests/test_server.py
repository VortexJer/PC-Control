"""El servidor MCP, de extremo a extremo por el protocolo real (stdio), con sus defensas.

Usa una ventana de prueba PROPIA lanzada desde una copia de powershell.exe con otro nombre
(el nombre real esta en la lista de bloqueo: es un terminal).
"""
import asyncio, json, os, shutil, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from pcsight import core
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

tmp = tempfile.mkdtemp(prefix="pcsight_srv_")
host = os.path.join(tmp, "pcsighthost.exe")
shutil.copy(os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), host)
state = os.path.join(tmp, "state.json")
S = lambda: json.load(open(state, encoding="utf-8-sig"))
checks = {}

def text_of(res):
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")

async def session(env_extra, fn):
    env = {**os.environ, **env_extra, "PYTHONUTF8": "1"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "pcsight.server"], cwd=ROOT, env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)

async def call(s, name, **args):
    try:
        res = await s.call_tool(name, args)
        return res.isError, text_of(res)
    except Exception as e:  # validacion de argumentos
        return True, str(e)

r = core.open_app(f'"{host}" -STA -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{os.path.join(HERE, "test_app.ps1")}" "{state}"',
                  creationflags=0x08000000)
try:
    hw = str(r["windows"][0]["hwnd"])
    time.sleep(0.5)

    async def a(s):                                     # sin permisos de accion
        tools = (await s.list_tools()).tools
        names = {t.name for t in tools}
        size = sum(len(t.name) + len(t.description or "") + len(json.dumps(t.inputSchema)) for t in tools)
        wl = text_of(await s.call_tool("windows", {}))
        lk = await call(s, "look", window=hw)
        ck = await call(s, "click", window=hw, target="1")
        op = await call(s, "open_app", command="calc.exe")
        return names, round(size / 3.6), wl, lk, ck, op
    names, tool_tok, wl, lk, ck, op = asyncio.run(session({}, a))
    checks["herramientas"] = names == {"windows", "look", "click", "type", "key", "open_app"}
    checks["definiciones < 1100 tok"] = tool_tok < 1100
    checks["windows lista la ventana"] = "pcsighthost" in wl
    checks["look permitido sin allowlist"] = (not lk[0]) and "Pulsar" in lk[1]
    checks["click bloqueado sin allowlist"] = ck[0] and "no esta permitido" in ck[1]
    checks["open_app bloqueado sin allowlist"] = op[0] and "no esta permitido" in op[1]

    async def b(s):                                     # con permiso
        lk = await call(s, "look", window=hw)
        ids = {}
        for line in lk[1].splitlines():
            for tag in line.replace("]", "]\n").splitlines():
                tag = tag.strip()
                if tag.startswith("[") and ":" in tag:
                    i, name = tag[1:-1].split(":", 1); ids[name] = i
        btn, edt = next(v for k, v in ids.items() if "Pulsar" in k), next(v for k, v in ids.items() if "Campo" in k)
        c1 = await call(s, "click", window=hw, target=btn)
        t1 = await call(s, "type", window=hw, target=edt, text="hola")
        t2 = await call(s, "type", window=hw, text="sin id")             # el id es obligatorio
        dn = await call(s, "open_app", command="cmd.exe /c exit")        # en la lista de bloqueo
        return c1, t1, t2, dn
    c1, t1, t2, dn = asyncio.run(session({"PCSIGHT_ALLOW": "pcsighthost"}, b))
    checks["click permitido + solo el cambio"] = (not c1[0]) and "cambio: + Clicks: 1" in c1[1] and S()["clicks"] == 1
    checks["type permitido"] = (not t1[0]) and S()["text"] == "hola"
    checks["type sin id rechazado"] = t2[0]
    checks["open_app de la lista de bloqueo rechazado"] = dn[0] and "no esta permitido" in dn[1]

    async def c(s):
        return await call(s, "windows"), await call(s, "look", window=hw)
    p1, p2 = asyncio.run(session({"PCSIGHT_PAUSE": "1", "PCSIGHT_ALLOW": "pcsighthost"}, c))
    checks["pausa bloquea todo"] = p1[0] and p2[0] and "pausa" in p1[1]

    from pcsight import server                           # la lista de bloqueo, por unidad
    server._proc = lambda h: "keepass"; core.find_window = lambda q: 1
    try:
        server._gate("x", False); checks["lista de bloqueo (look)"] = False
    except PermissionError:
        checks["lista de bloqueo (look)"] = True
finally:
    subprocess.run(["taskkill", "/PID", str(r["pid"]), "/T", "/F"], capture_output=True, creationflags=0x08000000)
    shutil.rmtree(tmp, ignore_errors=True)

for k, v in checks.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"tokens de las definiciones de herramientas: ~{tool_tok}")
ok = all(checks.values())
print("server verification passed" if ok else "server verification FAILED")
sys.exit(0 if ok else 1)
