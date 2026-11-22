"""The taskbar is a target: listed by windows(), read by look() (its buttons with ids), the volume set by type and scroll on the volume
button through Core Audio - verified against the audio endpoint itself, restored at the end - with no flyout and no focus change; other
taskbar buttons (they open Windows UI in front of the user) are not pressed without the user's yes."""
import asyncio, os, re, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import win32gui
from pc_control import audio, core

tmp = tempfile.mkdtemp(prefix="pcc_tb_")
checks = {}


def text(res):
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")


def visible_tops():
    return {h for h in core.top_windows() if win32gui.IsWindowVisible(h)}


async def session(mode, fn):
    params = StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT,
                                   env={**os.environ, "PC_CONTROL_HOME": os.path.join(tmp, mode), "PC_CONTROL_MODE": mode, "PYTHONUTF8": "1"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            async def call(tool, **a):
                res = await s.call_tool(tool, a)
                return bool(getattr(res, "isError", False)), text(res)
            return await fn(call)


level0, mute0 = audio.get()
fg0, tops0 = win32gui.GetForegroundWindow(), visible_tops()


async def main(call):
    _, w = await call("windows", filter="taskbar")
    checks["windows() lists the taskbar"] = "Taskbar" in w
    _, lk = await call("look", window="taskbar")
    checks["look reads the taskbar and says how to change the volume"] = "volume" in lk.lower() and "scroll" in lk
    _, f = await call("look", window="taskbar", find="Volum|Speaker|Altavo|Auricul")
    m = re.search(r"\[(\d+):", f)
    checks["the volume button has an id"] = bool(m)
    if not m:
        print(f); return
    vid = m.group(1)
    target = 37 if level0 != 37 else 41
    _, t = await call("type", window="taskbar", target=vid, text=str(target))
    checks["type sets the level (read from the endpoint)"] = audio.get()[0] == target and f"-> {target}%" in t
    _, t = await call("scroll", window="taskbar", target=vid, direction="up", amount=2)
    checks["scroll up raises it like the wheel (2 per step)"] = audio.get()[0] == target + 4
    _, t = await call("scroll", window="taskbar", target=vid, direction="down")
    checks["scroll down lowers it"] = audio.get()[0] == target + 2
    _, t = await call("type", window="taskbar", target=vid, text=str(level0))
    checks["the user's level is restored"] = audio.get()[0] == level0
    checks["no flyout or window appeared"] = visible_tops() - tops0 == set()
    checks["the foreground never moved"] = win32gui.GetForegroundWindow() == fg0


async def strict(call):
    _, f = await call("look", window="taskbar", find="Inicio|Start")
    m = re.search(r"\[(\d+):", f)
    err, t = await call("click", window="taskbar", target=m.group(1) if m else "Start")
    checks["a taskbar button is not pressed without the user (strict)"] = "not executed" in t.lower() or "not allowed" in t.lower()
    checks["...and nothing opened"] = visible_tops() - tops0 == set() and win32gui.GetForegroundWindow() == fg0


try:
    asyncio.run(session("bypass", main))
    asyncio.run(session("strict", strict))
finally:
    audio.set_level(level0); audio.set_mute(mute0)
for k, v in checks.items():
    print(("ok   " if v else "FAIL ") + k)
if len(checks) < 10 or not all(checks.values()):
    sys.exit(1)
print(f"taskbar target verification passed ({len(checks)} checks)")
