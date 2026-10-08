"""The MCP server end to end through the real protocol (stdio), with its defences.

The test apps are launched with a copy of powershell.exe under another name (the real one is on the block list: it is a
terminal) and through the open_app tool: minimized (taskbar) and on the hidden desktop. Nothing shows on screen.
"""
import asyncio, json, os, re, shutil, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

tmp = tempfile.mkdtemp(prefix="pc-control_srv_")
host = os.path.join(tmp, "pc_control_host.exe")
shutil.copy(os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), host)
st1, st2 = os.path.join(tmp, "s1.json"), os.path.join(tmp, "s2.json")
S = lambda p: json.load(open(p, encoding="utf-8-sig"))
ps1 = os.path.join(HERE, "test_app.ps1")
checks, pids, SCHEMAS = {}, [], []

def text_of(res):
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")

ASKED = []
ANS = {"v": None}                                      # None = client WITHOUT question support; "accept"/"decline"

async def _elicit(ctx, params):
    from mcp import types
    ASKED.append(params.message)
    if ANS["v"] == "accept":
        return types.ElicitResult(action="accept", content={"allow": True})
    return types.ElicitResult(action=ANS["v"])

async def session(env_extra, fn):
    params = StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT,
                                   env={**os.environ, "PC_CONTROL_HOME": os.path.join(tmp, "home"), **env_extra, "PYTHONUTF8": "1"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w, elicitation_callback=_elicit if ANS['v'] else None) as s:
            await s.initialize()
            return await fn(s)

async def call(s, _tool, **args):
    try:
        res = await s.call_tool(_tool, args)
        return bool(getattr(res, "isError", getattr(res, "is_error", False))), text_of(res)
    except Exception as e:
        return True, str(e)

def ids_of(look_text):
    return {m.group(2): m.group(1) for m in re.finditer(r"\[(\d+):([^\]]+)\]", look_text)}

try:
    async def a(s):                                     # without action permissions
        tools = (await s.list_tools()).tools
        SCHEMAS.extend(getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) for t in tools)
        size = sum(len(t.name) + len(t.description or "") + len(json.dumps(getattr(t, "inputSchema", None) or getattr(t, "input_schema", None))) for t in tools)
        return ({t.name for t in tools}, round(size / 3.6),
                await call(s, "click", window="Program Manager", target="1"), await call(s, "type", window="Program Manager", target="1", text="x"),
                await call(s, "key", window="Program Manager", name="enter"), await call(s, "open_app", command="calc.exe"))
    names, tool_tok, ck, ty, ky, op = asyncio.run(session({"PC_CONTROL_MODE": "strict"}, a))
    checks["tools"] = names == {"windows", "look", "click", "type", "key", "open_app", "drag"}
    checks["the model has NO confirm parameter (it cannot grant itself permissions)"] = "confirm" not in json.dumps(SCHEMAS)
    checks["definitions < 1350 tok"] = tool_tok < 1350
    checks["click blocked without an allowlist"] = ck[0] and "is not allowed" in ck[1]
    checks["type blocked without an allowlist"] = ty[0] and "is not allowed" in ty[1]
    checks["key blocked without an allowlist"] = ky[0] and "is not allowed" in ky[1]
    checks["open_app blocked without an allowlist"] = op[0] and "is not allowed" in op[1]

    async def b(s):                                     # with permission
        o1 = await call(s, "open_app", command=f'"{host}" -STA -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{ps1}" "{st1}" -Taskbar')
        o2 = await call(s, "open_app", hidden=True, command=f'"{host}" -STA -NoProfile -ExecutionPolicy Bypass -File "{ps1}" "{st2}"')
        for o in (o1, o2):
            m = re.search(r"pid (\d+)", o[1])
            if m: pids.append(int(m.group(1)))
        m1 = re.search(r"pid \d+ \| (\d+) ", o1[1]); m2 = re.search(r"pid \d+ \| (\d+) ", o2[1])
        h1, h2 = (m1 and m1.group(1)), (m2 and m2.group(1))
        await asyncio.sleep(0.8)
        out = {"o1": o1, "o2": o2, "windows": await call(s, "windows")}
        for tag, hw, st in (("min", h1, st1), ("hidden", h2, st2)):
            if not hw: out[tag] = None; continue
            lk = await call(s, "look", window=hw); ids = ids_of(lk[1])
            btn = next((v for k, v in ids.items() if "Press" in k), "0"); edt = next((v for k, v in ids.items() if "Name field" in k), "0")
            c1 = await call(s, "click", window=hw, target=btn)
            t1 = await call(s, "type", window=hw, target=edt, text="hello")
            t2 = await call(s, "type", window=hw, text="no id")
            await asyncio.sleep(0.4)
            out[tag] = (lk, c1, t1, t2, S(st))
        out["denied"] = await call(s, "open_app", command="cmd.exe /c exit")
        out["img_min"] = await call(s, "look", window=h1, mode="image") if h1 else None
        return out
    out = asyncio.run(session({"PC_CONTROL_MODE": "strict", "PC_CONTROL_ALLOW": "pc_control_host"}, b))
    checks["open_app minimized"] = (not out["o1"][0]) and "minimized" in out["o1"][1]
    checks["open_app hidden"] = (not out["o2"][0]) and "hidden desktop" in out["o2"][1]
    checks["windows lists the hidden one"] = "(hidden)" in out["windows"][1]
    for tag in ("min", "hidden"):
        v = out.get(tag)
        checks[f"look+click+type ({tag})"] = bool(v) and (not v[1][0]) and "changes: + Clicks: 1" in v[1][1] and v[4]["clicks"] == 1 and v[4]["text"] == "hello"
        checks[f"type without an id rejected ({tag})"] = bool(v) and v[3][0]
        if v and not checks[f"look+click+type ({tag})"]: print("DEBUG", tag, "| look:", v[0][1][:200].replace(chr(10), " / "), "| click:", v[1], "| type:", v[2], "| state:", v[4])
    checks["protected without question support: NOT executed and it explains why"] = out["denied"][0] and "same call" in out["denied"][1]
    # Claude Code does not support questions from the server: the hook makes CLAUDE CODE ask (the model cannot answer)
    hook_in = json.dumps({"tool_name": "mcp__PC-Control__open_app", "tool_input": {"command": "cmd.exe /c exit"}})
    async def hk(s):
        e1 = await call(s, "open_app", command="cmd.exe /c exit")                      # without the hook: asks to repeat and is not executed
        e2 = await call(s, "open_app", command="cmd.exe /c exit")                      # repeating without the user having said anything: still not executed
        h = subprocess.run([sys.executable, "-m", "pc_control.hook"], input=hook_in, capture_output=True, text=True, encoding="utf-8",
                           cwd=ROOT, env={**os.environ, "PC_CONTROL_HOME": os.path.join(tmp, "home"), "PC_CONTROL_MODE": "auto", "PYTHONUTF8": "1"})
        e3 = await call(s, "open_app", command="cmd.exe /c exit")                      # the hook asked and the user said yes
        e4 = await call(s, "open_app", command="cmd.exe /c exit")                      # the permission was spent: no hook again
        return e1, e2, h, e3, e4
    e1, e2, h, e3, e4 = asyncio.run(session({"PC_CONTROL_MODE": "auto"}, hk))
    checks["hook: without the hook the call is NOT executed and it asks to repeat it"] = e1[0] and "same call" in e1[1] and e2[0]
    checks["hook: Claude Code receives permissionDecision=ask"] = '"permissionDecision": "ask"' in h.stdout
    checks["hook: after the user's yes the same call is executed"] = not e3[0]
    checks["hook: a yes counts ONLY that once (the next call asks for permission again)"] = e4[0] and "same call" in e4[1]
    # Claude Code announces (PermissionRequest) every time it is about to show a dialog: then the server REQUIRES that it was shown
    shutil.rmtree(os.path.join(tmp, "home", "perm"), ignore_errors=True)
    hook_env = {**os.environ, "PC_CONTROL_HOME": os.path.join(tmp, "home"), "PC_CONTROL_MODE": "auto", "PYTHONUTF8": "1"}
    def run_hook(payload):
        return subprocess.run([sys.executable, "-m", "pc_control.hook"], input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", cwd=ROOT, env=hook_env)
    run_hook({"hook_event_name": "PermissionRequest", "tool_name": "mcp__PC-Control__windows", "tool_input": {}})      # first time this event is seen: sets pr_seen
    pre = {"hook_event_name": "PreToolUse", "tool_name": "mcp__PC-Control__open_app", "tool_input": {"command": "cmd.exe /c exit"}}
    async def sh(s):
        run_hook(pre)                                                                  # the hook "asked" (PreToolUse)...
        f1 = await call(s, "open_app", command="cmd.exe /c exit")                      # ...but the dialog was NOT shown (a rule skipped it)
        run_hook(pre); run_hook({**pre, "hook_event_name": "PermissionRequest"})       # now yes: PermissionRequest announces that the dialog is shown
        f2 = await call(s, "open_app", command="cmd.exe /c exit")
        return f1, f2
    f1, f2 = asyncio.run(session({"PC_CONTROL_MODE": "auto"}, sh))
    checks["if the dialog was NOT shown (a rule skips it), the server does NOT execute and explains why"] = f1[0] and "was NOT shown" in f1[1]
    checks["if the dialog WAS shown and the user accepted, it is executed"] = not f2[0]
    shutil.rmtree(os.path.join(tmp, "home", "perm"), ignore_errors=True)               # new user/session: no previous permissions
    ANS["v"] = "decline"; ASKED.clear()
    async def dn(s): return await call(s, "open_app", command="cmd.exe /c exit"), await call(s, "open_app", command="cmd.exe /c exit", confirm=True)
    d1, d2 = asyncio.run(session({"PC_CONTROL_MODE": "auto"}, dn))
    checks["protected: the server asks the USER (PROTECTED in the question)"] = bool(ASKED) and "PROTECTED" in ASKED[0]
    checks["protected: if the user says no -> not executed"] = d1[0] and "did NOT give permission" in d1[1]
    checks["protected: the model cannot skip it with confirm=true"] = d2[0] and "did NOT give permission" in d2[1]
    shutil.rmtree(os.path.join(tmp, "home", "perm"), ignore_errors=True)
    ANS["v"] = "accept"; ASKED.clear()
    async def ac(s): return await call(s, "open_app", command="cmd.exe /c exit"), await call(s, "open_app", command="cmd.exe /c exit")
    a1, a2 = asyncio.run(session({"PC_CONTROL_MODE": "auto"}, ac))
    checks["protected: if the user says yes -> executed"] = not a1[0]
    checks["protected: a yes counts only once (the second call asks again)"] = len(ASKED) == 2
    # ask mode: a NORMAL app also asks
    shutil.rmtree(os.path.join(tmp, "home", "perm"), ignore_errors=True)
    ANS["v"] = "decline"; ASKED.clear()
    async def ak(s): return await call(s, "open_app", command="calc.exe")
    k1 = asyncio.run(session({"PC_CONTROL_MODE": "ask"}, ak))
    checks["ask mode: a normal app (calc) also asks the user"] = bool(ASKED) and "ask mode" in ASKED[0] and k1[0] and "did NOT give permission" in k1[1]
    ASKED.clear()
    async def ak2(s): return await call(s, "open_app", command="pc_control_does_not_exist.exe")      # opens NOTHING real: fails on launch, but first decides whether to ask
    k2 = asyncio.run(session({"PC_CONTROL_MODE": "auto"}, ak2))
    checks["auto mode: a normal app does NOT ask (no real app is opened in the test)"] = not ASKED
    ANS["v"] = None
    checks["minimized is not captured (it warns)"] = bool(out["img_min"]) and "hidden=true" in out["img_min"][1]

    async def c(s):
        return await call(s, "windows"), await call(s, "open_app", command="calc.exe")
    p1, p2 = asyncio.run(session({"PC_CONTROL_PAUSE": "1", "PC_CONTROL_ALLOW": "calc"}, c))
    checks["pause blocks everything"] = p1[0] and p2[0] and "paused" in p1[1]

    st3, st4 = os.path.join(tmp, "s3.json"), os.path.join(tmp, "s4.json")
    async def modes(s, st):
        o = await call(s, "open_app", command=f'"{host}" -STA -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{ps1}" "{st}" -Taskbar')
        m = re.search(r"pid (\d+)", o[1])
        if m: pids.append(int(m.group(1)))
        hw = re.search(r"pid \d+ \| (\d+) ", o[1]); hw = hw and hw.group(1)
        await asyncio.sleep(0.8)
        ids = ids_of((await call(s, "look", window=hw))[1])
        g = lambda k: next((v for n, v in ids.items() if k in n), "0")
        r = {"open": o, "push": await call(s, "click", window=hw, target=g("Press")),
             "del_no": await call(s, "click", window=hw, target=g("Delete"))}
        await asyncio.sleep(0.3); r["d1"] = S(st)["deletes"]
        ANS["v"] = "accept"; r["del_yes"] = await call(s, "click", window=hw, target=g("Delete"))
        r["badid"] = await call(s, "type", window=hw, target="9999", text="blind")      # an id that does not come from look(): no blind typing
        r["pw"] = await call(s, "type", window=hw, target=g("Secret"), text="abc")
        await asyncio.sleep(0.4); r["state"] = S(st)
        return r
    ANS["v"] = "decline"
    ra = asyncio.run(session({"PC_CONTROL_MODE": "auto"}, lambda s: modes(s, st3)))
    checks["auto: opens and acts without an allowlist"] = (not ra["open"][0]) and (not ra["push"][0])
    checks["auto: a sensitive action requires confirm"] = ra["del_no"][0] and "did NOT give permission" in ra["del_no"][1] and ra["d1"] == 0
    checks["auto: with the user's yes it is executed"] = (not ra["del_yes"][0]) and ra["state"]["deletes"] == 1
    checks["type with an id that does not come from look() is refused (no blind typing)"] = ra["badid"][0] and "unknown id" in ra["badid"][1] and ra["state"]["text"] == ""
    checks["auto: does not type into a password field"] = ra["pw"][0] and "password" in ra["pw"][1] and ra["state"]["secret"] == ""
    rb = asyncio.run(session({"PC_CONTROL_MODE": "bypass"}, lambda s: modes(s, st4)))
    checks["bypass: no confirmations"] = (not rb["del_no"][0]) and rb["d1"] == 1 and rb["state"]["deletes"] == 2
    checks["bypass: types into a password field"] = (not rb["pw"][0]) and rb["state"]["secret"] == "abc"

    from pc_control import server, core                     # the block list, by unit
    server._proc = lambda h: "keepass"; core.find_window = lambda q: 1
    os.environ["PC_CONTROL_MODE"] = "auto"                  # pinned: "follow" would depend on the Claude Code session running the test
    try:
        server._gate("x", False); checks["protected category (look): asks for permission"] = False
    except PermissionError:
        checks["protected category (look): asks for permission"] = True
    try:
        server._gate("x", False, confirm=True); checks["protected category: allowed with confirm=true"] = True
    except PermissionError:
        checks["protected category: allowed with confirm=true"] = False
finally:
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)
    time.sleep(0.5); shutil.rmtree(tmp, ignore_errors=True)

for k, v in checks.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"tool definition tokens: ~{tool_tok}")
ok = all(checks.values())
print("server verification passed" if ok else "server verification FAILED")
sys.exit(0 if ok else 1)
