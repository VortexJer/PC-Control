"""A real app that reads the keyboard only as Raw Input (Blender), operated through the REAL MCP server with nothing app-specific:
detected by itself, keys and exact characters delivered inside the app, clicks at Claude's point, and the app never allowed to move or
confine the user's mouse - with the foreground, the real cursor and the clip rectangle untouched.

Verified independently: Blender is started by THIS test with a small observer (--python-expr) that writes its scene to a file; the
product knows nothing about it. Works with the display off. Skips (exit 0, says so) when Blender is not installed."""
import asyncio, ctypes, glob, json, os, re, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import win32api, win32gui

exe = sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe"))
if not exe:
    print("SKIP: Blender is not installed"); sys.exit(0)
tmp = tempfile.mkdtemp(prefix="pcc_raw_")
STATE = os.path.join(tmp, "scene.json").replace("\\", "/")
OBSERVER = ("import bpy, json, os\n"
            "def _w():\n"
            "    c = bpy.context; o = c.view_layer.objects.active\n"
            "    d = {'active': o.name if o else None, 'selected': [x.name for x in c.selected_objects], 'objects': len(c.scene.objects),\n"
            "         'texts': {t.name: t.as_string() for t in bpy.data.texts}}\n"
            f"    open('{STATE}.tmp', 'w', encoding='utf-8').write(json.dumps(d)); os.replace('{STATE}.tmp', '{STATE}')\n"
            "    return 0.2\n"
            "bpy.app.timers.register(_w, first_interval=0.5, persistent=True)\n")
script = os.path.join(tmp, "observer.py")
open(script, "w", encoding="utf-8").write(OBSERVER)
CMD = subprocess.list2cmdline([exe[-1], "--python", script])
checks = {}


def scene(wait=0.5):
    time.sleep(wait)
    for _ in range(20):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.1)
    return {}


def text(res):
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")


class RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]


def clip():
    r = RECT(); ctypes.windll.user32.GetClipCursor(ctypes.byref(r)); return (r.l, r.t, r.r, r.b)


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT,
                                   env={**os.environ, "PC_CONTROL_HOME": os.path.join(tmp, "home"), "PC_CONTROL_MODE": "bypass",
                                        "PYTHONUTF8": "1"})
    fg0, cur0, clip0 = win32gui.GetForegroundWindow(), win32api.GetCursorPos(), clip()
    pid = None
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            async def call(tool, **a):
                return text(await s.call_tool(tool, a))
            out = await call("open_app", command=CMD, behind=True)
            m = re.search(r"pid (\d+) \| (\d+) ", out)
            if not m:
                print(out); checks["open_app"] = False; return
            pid, hw = int(m.group(1)), m.group(2)
            for _ in range(60):
                if os.path.exists(STATE):
                    break
                await asyncio.sleep(0.25)
            await asyncio.sleep(1.5)
            await call("look", window=hw)
            P = "500,350"                                              # inside the 3D view of the default layout
            k = await call("key", window=hw, name="Escape", target=P)   # the splash screen takes keys until closed
            checks["detected as a Raw Input app (keys delivered inside it)"] = "inside the app" in k
            await call("key", window=hw, name="a", target=P)
            checks["'a' selects all (3)"] = len(scene().get("selected") or []) == 3
            await call("key", window=hw, name="alt+a", target=P)
            checks["'alt+a' deselects all"] = scene().get("selected") == []
            await call("key", window=hw, name="a", target=P)
            await call("key", window=hw, name="delete", target=P)
            checks["'delete' deletes the selection"] = scene().get("objects") == 0
            await call("key", window=hw, name="ctrl+z", target=P)
            checks["'ctrl+z' undoes it"] = scene().get("objects") == 3
            # a tool that warps/confines the mouse (grab) while the user works elsewhere: the user's cursor must not move
            await call("key", window=hw, name="g", target=P)
            await asyncio.sleep(0.6)
            checks["'g' (grab) did not move the user's cursor"] = win32api.GetCursorPos() == cur0
            checks["...nor confine it"] = clip() == clip0
            await call("key", window=hw, name="Escape", target=P)
            # rename through F2's pop-up: exact characters
            await call("key", window=hw, name="f2", target=P)
            await call("type", window=hw, text="Robot Árbol ñ€😀", replace=True)
            await call("key", window=hw, name="enter", target=P)
            checks["F2 + type renames (exact characters)"] = scene().get("active") == "Robot Árbol ñ€😀"
            # the 3D view becomes a Text Editor (shift+F11), a new text (alt+n), two lines typed into it
            await call("key", window=hw, name="shift+f11", target=P)
            await call("key", window=hw, name="alt+n", target=P)
            await call("type", window=hw, text="print('hola')\nx = 2")
            texts = scene(1.0).get("texts") or {}
            checks["Text Editor typing (two lines)"] = any("print('hola')\nx = 2" in v for v in texts.values())
            if not checks["Text Editor typing (two lines)"]:
                print("   texts:", texts)
            checks["the foreground never moved"] = win32gui.GetForegroundWindow() == fg0
            checks["the user's cursor never moved"] = win32api.GetCursorPos() == cur0
    if pid:
        try:
            os.kill(pid, 9)
        except Exception:
            pass


asyncio.run(main())
for k, v in checks.items():
    print(("ok   " if v else "FAIL ") + k)
if len(checks) < 11 or not all(checks.values()):
    sys.exit(1)
print(f"raw input live verification passed ({len(checks)} checks)")
