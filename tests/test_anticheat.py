"""A program protected by anti-cheat is never entered: open_app refuses to start it, and an action on its window (the user started
it) is refused without the guard DLL ever being loaded into it - a foreign DLL in a game can get the user's account banned.
The "game" is a copy of powershell.exe running the WinForms test window, next to an EasyAntiCheat folder; the control is the same copy without that folder."""
import asyncio, ctypes, os, shutil, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import win32gui, win32process
from pc_control import core

tmp = tempfile.mkdtemp(prefix="pcc_ac_")
game_dir, plain_dir = os.path.join(tmp, "Game"), os.path.join(tmp, "Plain")
os.makedirs(os.path.join(game_dir, "EasyAntiCheat")); os.makedirs(plain_dir)
game, plain = os.path.join(game_dir, "pccgame.exe"), os.path.join(plain_dir, "pccplain.exe")
PS = os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
APP = os.path.join(ROOT, "tests", "test_app2.ps1")                  # a WinForms test window, as tools/token_bench.py does
shutil.copy(PS, game); shutil.copy(PS, plain)
ARGS = f'-NoProfile -ExecutionPolicy Bypass -File "{APP}"'
checks = {}


def text(res):
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")


def modules(pid):
    out = []
    class ME(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong), ("a", ctypes.c_ulong), ("pid", ctypes.c_ulong), ("b", ctypes.c_ulong), ("c", ctypes.c_ulong),
                    ("base", ctypes.c_void_p), ("size", ctypes.c_ulong), ("h", ctypes.c_void_p), ("name", ctypes.c_wchar * 256),
                    ("path", ctypes.c_wchar * 260)]
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    s = k.CreateToolhelp32Snapshot(0x18, pid)
    me = ME(); me.dwSize = ctypes.sizeof(ME)
    ok = k.Module32FirstW(ctypes.c_void_p(s), ctypes.byref(me))
    while ok:
        out.append(me.name.lower()); ok = k.Module32NextW(ctypes.c_void_p(s), ctypes.byref(me))
    k.CloseHandle(ctypes.c_void_p(s))
    return out


def running(name):
    return subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}"], capture_output=True, text=True, encoding="mbcs", errors="replace").stdout.lower().count(name)


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT,
                                   env={**os.environ, "PC_CONTROL_HOME": os.path.join(tmp, "home"), "PC_CONTROL_MODE": "bypass",
                                        "PYTHONUTF8": "1"})
    user_game = None
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            async def call(tool, **a):
                res = await s.call_tool(tool, a)
                return text(res)
            t = await call("open_app", command=f'"{game}" {ARGS}')
            checks["open_app refuses a program shipped with anti-cheat"] = "anti-cheat" in t
            await asyncio.sleep(1)
            checks["...and it was not started"] = running("pccgame.exe") == 0
            # the user starts it themselves (minimized, not activated): an action on it is refused, nothing goes inside it
            si = subprocess.STARTUPINFO(); si.dwFlags |= 1; si.wShowWindow = 7
            user_game = subprocess.Popen(f'"{game}" {ARGS}', startupinfo=si)
            await asyncio.sleep(2.5)
            await call("look", window="pccgame")
            t = await call("key", window="pccgame", name="enter")
            checks["an action on its window is refused (anti-cheat)"] = "anti-cheat" in t
            if not checks["an action on its window is refused (anti-cheat)"]:
                print("   key said:", t[:200])
            gpids = [p for p in [user_game.pid] + [win32process.GetWindowThreadProcessId(h)[1] for h in core.top_windows()
                                                   if core.psutil_name(win32process.GetWindowThreadProcessId(h)[1]) == "pccgame"]]
            loaded = [(p, m) for p in set(gpids) for m in modules(p) if "focus_guard" in m]
            checks["...and no PC-Control DLL was loaded into it"] = not loaded
            if loaded:
                print("   loaded:", loaded, "| game pid", user_game.pid)
            # control: the same program without anti-cheat opens normally
            t = await call("open_app", command=f'"{plain}" {ARGS}')
            checks["control: the same program without anti-cheat opens"] = "pid " in t and "anti-cheat" not in t
    for p in (user_game,):
        try:
            p.kill()
        except Exception:
            pass
    subprocess.run(["taskkill", "/IM", "pccplain.exe", "/F"], capture_output=True)


asyncio.run(main())
for k, v in checks.items():
    print(("ok   " if v else "FAIL ") + k)
if len(checks) < 5 or not all(checks.values()):
    sys.exit(1)
print(f"anticheat verification passed ({len(checks)} checks)")
