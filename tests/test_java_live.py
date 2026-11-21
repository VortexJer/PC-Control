"""Java live, through the REAL MCP server (stdio), like Claude uses it: a Swing/AWT app (the Oracle Forms family) is opened with open_app,
read as named elements through the Java Access Bridge and operated: button, text field, checkbox, combo box, a menu entry that is not even
open, a tab, a table read, Enter on a field. The app's own event file confirms each action inside Java. Throughout, the user's
foreground window, mouse position and keyboard state are checked (changes made by the user themselves are told apart)."""
import asyncio, os, re, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import win32api, win32gui
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pc_control import core, java

tmp = tempfile.mkdtemp(); ev = os.path.join(tmp, "events.txt"); open(ev, "w").close()
title = f"PCC Swing Live {os.getpid()}"
jdk = os.path.dirname(java.find_dlls()[0])
cmd = f'"{os.path.join(jdk, "javaw.exe")}" -cp "{os.path.join(ROOT, "tests", "java_app")}" PccSwingTest "{ev}" "{title}"'
c = {}


def text_of(res):
    return "\n".join(x.text for x in res.content if getattr(x, "type", "") == "text")


async def call(s, tool, **args):
    try:
        res = await s.call_tool(tool, args)
        return bool(getattr(res, "isError", False)), text_of(res)
    except Exception as e:
        return True, str(e)


def events():
    return open(ev, encoding="utf-8").read().splitlines()


def ids(look_text):
    return {m.group(2).strip(): m.group(1) for m in re.finditer(r"\[(\d+):([^\]=]+)", look_text)}


async def run(s):
    out = {}
    out["open"] = await call(s, "open_app", command=cmd)
    m = re.search(r"pid \d+ \| (\d+) ", out["open"][1]); hw = m.group(1) if m else title
    time.sleep(1.2)
    out["windows"] = await call(s, "windows", filter="pcc swing live")
    out["look"] = await call(s, "look", window=hw)
    i = ids(out["look"][1])
    out["type"] = await call(s, "type", window=hw, target=i.get("Nombre del cliente", "0"), text="Ana Pérez")
    out["click"] = await call(s, "click", window=hw, target="Saludar")
    out["check"] = await call(s, "click", window=hw, target="Acepto las condiciones")
    out["combo"] = await call(s, "type", window=hw, target="Color favorito", text="Verde")
    out["menu"] = await call(s, "click", window=hw, target="Nuevo documento")          # a menu entry of a CLOSED menu: found and pressed
    out["enter"] = await call(s, "key", window=hw, name="enter")                        # Enter on the field Claude typed into
    out["tab"] = await call(s, "click", window=hw, target="Avanzado")
    time.sleep(0.4)
    out["read"] = await call(s, "read", window=hw)
    out["find"] = await call(s, "look", window=hw, find="Pedido 1002")
    out["close"] = await call(s, "key", window=hw, name="alt+f4")
    out["hw"] = hw
    return out


fg0, cur0, tick0 = win32gui.GetForegroundWindow(), win32api.GetCursorPos(), core._input_tick()
params = StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT,
                               env={**os.environ, "PC_CONTROL_HOME": os.path.join(tmp, "home"), "PC_CONTROL_MODE": "bypass", "PYTHONUTF8": "1",
                                    "PC_CONTROL_CURSOR": "off"})


async def main():
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await run(s)

out = asyncio.run(main())
for k in ("open", "look", "type", "click", "check", "combo", "menu", "enter", "tab", "find"):
    print(f"   {k}: {out[k][1][:230]!r}")
evs = events()
c["open_app opens the Java app with the bridge, without taking the focus"] = not out["open"][0] and "accessibility bridge" in out["open"][1] and "took the keyboard focus" not in out["open"][1]
c["windows() marks it as a Java window read through the bridge"] = "[java]" in out["windows"][1]
c["look lists Java controls by name with ids"] = all(n in out["look"][1] for n in ("Saludar", "Nombre del cliente", "Acepto las condiciones", "Color favorito", "Avanzado"))
c["type writes into the Java text field (read back)"] = "the field now contains the text" in out["type"][1] and "text:Ana Pérez" in evs
c["click by name presses the Java button (the app saw it, with the typed text)"] = "button:Saludar:Ana Pérez" in evs and not out["click"][0]
c["checkbox ticked inside Java"] = "check:true" in evs
c["combo entry selected and read back"] = "combo:Verde" in evs and "the control now shows it" in out["combo"][1]
c["an entry of a CLOSED menu is found and pressed (no menu opened on screen)"] = "menu:Nuevo documento" in evs
c["Enter on the field runs the field's own action through the bridge"] = "notify-field-accept" in out["enter"][1]
c["a tab is selected"] = "tab:Avanzado" in evs
c["read gives the Java texts, including the table"] = "Pedido 1002" in out["read"][1] and "Enviado" in out["read"][1]
c["find locates a table cell"] = "Pedido 1002" in out["find"][1] and not out["find"][0]
c["the app closed through the window it was opened in"] = not out["close"][0]
user = core._input_tick() != tick0
c["the user's foreground window was never taken (or the user changed it themselves)"] = win32gui.GetForegroundWindow() == fg0 or user
c["the user's mouse never moved (or the user moved it)"] = win32api.GetCursorPos() == cur0 or user
c["no loop guard or false success in the run"] = not any("REPEATED FAILURE" in v[1] for k, v in out.items() if isinstance(v, tuple))

bad = [k for k, v in c.items() if not v]
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
print("events:", evs)
print(f"java live verification passed ({len(c)} checks)" if not bad else f"java live verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
