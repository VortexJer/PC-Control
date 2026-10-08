"""El servidor MCP de extremo a extremo por el protocolo real (stdio), con sus defensas.

Las apps de prueba se lanzan con una copia de powershell.exe con otro nombre (el real esta en la lista de bloqueo: es un
terminal) y mediante la herramienta open_app: minimizada (barra de tareas) y en el escritorio oculto. Nada se ve en pantalla.
"""
import asyncio, json, os, re, shutil, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

tmp = tempfile.mkdtemp(prefix="pcsight_srv_")
host = os.path.join(tmp, "pcsighthost.exe")
shutil.copy(os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), host)
st1, st2 = os.path.join(tmp, "s1.json"), os.path.join(tmp, "s2.json")
S = lambda p: json.load(open(p, encoding="utf-8-sig"))
ps1 = os.path.join(HERE, "test_app.ps1")
checks, pids = {}, []

def text_of(res):
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")

async def session(env_extra, fn):
    params = StdioServerParameters(command=sys.executable, args=["-m", "pcsight.server"], cwd=ROOT,
                                   env={**os.environ, **env_extra, "PYTHONUTF8": "1"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)

async def call(s, name, **args):
    try:
        res = await s.call_tool(name, args)
        return bool(getattr(res, "isError", getattr(res, "is_error", False))), text_of(res)
    except Exception as e:
        return True, str(e)

def ids_of(look_text):
    return {m.group(2): m.group(1) for m in re.finditer(r"\[(\d+):([^\]]+)\]", look_text)}

try:
    async def a(s):                                     # sin permisos de accion
        tools = (await s.list_tools()).tools
        size = sum(len(t.name) + len(t.description or "") + len(json.dumps(getattr(t, "inputSchema", None) or getattr(t, "input_schema", None))) for t in tools)
        return ({t.name for t in tools}, round(size / 3.6),
                await call(s, "click", window="Program Manager", target="1"), await call(s, "type", window="Program Manager", target="1", text="x"),
                await call(s, "key", window="Program Manager", name="enter"), await call(s, "open_app", command="calc.exe"))
    names, tool_tok, ck, ty, ky, op = asyncio.run(session({}, a))
    checks["herramientas"] = names == {"windows", "look", "click", "type", "key", "open_app"}
    checks["definiciones < 1100 tok"] = tool_tok < 1100
    checks["click bloqueado sin allowlist"] = ck[0] and "no esta permitido" in ck[1]
    checks["type bloqueado sin allowlist"] = ty[0] and "no esta permitido" in ty[1]
    checks["key bloqueado sin allowlist"] = ky[0] and "no esta permitido" in ky[1]
    checks["open_app bloqueado sin allowlist"] = op[0] and "no esta permitido" in op[1]

    async def b(s):                                     # con permiso
        o1 = await call(s, "open_app", command=f'"{host}" -STA -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{ps1}" "{st1}" -Taskbar')
        o2 = await call(s, "open_app", hidden=True, command=f'"{host}" -STA -NoProfile -ExecutionPolicy Bypass -File "{ps1}" "{st2}"')
        for o in (o1, o2):
            m = re.search(r"pid (\d+)", o[1])
            if m: pids.append(int(m.group(1)))
        m1 = re.search(r"pid \d+ \| (\d+) ", o1[1]); m2 = re.search(r"pid \d+ \| (\d+) ", o2[1])
        h1, h2 = (m1 and m1.group(1)), (m2 and m2.group(1))
        await asyncio.sleep(0.8)
        out = {"o1": o1, "o2": o2, "windows": await call(s, "windows")}
        for tag, hw, st in (("min", h1, st1), ("oculta", h2, st2)):
            if not hw: out[tag] = None; continue
            lk = await call(s, "look", window=hw); ids = ids_of(lk[1])
            btn = next((v for k, v in ids.items() if "Pulsar" in k), "0"); edt = next((v for k, v in ids.items() if "Campo" in k), "0")
            c1 = await call(s, "click", window=hw, target=btn)
            t1 = await call(s, "type", window=hw, target=edt, text="hola")
            t2 = await call(s, "type", window=hw, text="sin id")
            await asyncio.sleep(0.4)
            out[tag] = (lk, c1, t1, t2, S(st))
        out["denied"] = await call(s, "open_app", command="cmd.exe /c exit")
        out["img_min"] = await call(s, "look", window=h1, mode="image") if h1 else None
        return out
    out = asyncio.run(session({"PCSIGHT_ALLOW": "pcsighthost"}, b))
    checks["open_app minimizada"] = (not out["o1"][0]) and "minimizada" in out["o1"][1]
    checks["open_app oculta"] = (not out["o2"][0]) and "oculto" in out["o2"][1]
    checks["windows lista la oculta"] = "(oculta)" in out["windows"][1]
    for tag in ("min", "oculta"):
        v = out.get(tag)
        checks[f"look+click+type ({tag})"] = bool(v) and (not v[1][0]) and "cambio: + Clicks: 1" in v[1][1] and v[4]["clicks"] == 1 and v[4]["text"] == "hola"
        checks[f"type sin id rechazado ({tag})"] = bool(v) and v[3][0]
    checks["open_app de la lista de bloqueo rechazado"] = out["denied"][0] and "no esta permitido" in out["denied"][1]
    checks["minimizada no se captura (avisa)"] = bool(out["img_min"]) and "hidden=true" in out["img_min"][1]

    async def c(s):
        return await call(s, "windows"), await call(s, "open_app", command="calc.exe")
    p1, p2 = asyncio.run(session({"PCSIGHT_PAUSE": "1", "PCSIGHT_ALLOW": "calc"}, c))
    checks["pausa bloquea todo"] = p1[0] and p2[0] and "pausa" in p1[1]

    from pcsight import server, core                     # la lista de bloqueo, por unidad
    server._proc = lambda h: "keepass"; core.find_window = lambda q: 1
    try:
        server._gate("x", False); checks["lista de bloqueo (look)"] = False
    except PermissionError:
        checks["lista de bloqueo (look)"] = True
finally:
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)
    time.sleep(0.5); shutil.rmtree(tmp, ignore_errors=True)

for k, v in checks.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"tokens de las definiciones de herramientas: ~{tool_tok}")
ok = all(checks.values())
print("server verification passed" if ok else "server verification FAILED")
sys.exit(0 if ok else 1)
