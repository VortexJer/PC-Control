"""Live, on-screen demo for the user: Claude's pointer clicks REAL buttons of a real app (Notepad++ toolbar), slowly, so the user can
see where the pointer ends on each button. Launched through the focus guard, shown without taking the focus, kept on top while it runs.

  python tools/click_demo.py [--speed 1] [--list]
--list only prints the toolbar buttons the app exposes.
"""
import asyncio, ctypes, os, re, subprocess, sys, tempfile, time
ctypes.windll.shcore.SetProcessDpiAwareness(2)
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import win32con, win32gui, win32process
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import realapps
from pc_control.focus import FocusGuard

BUTTONS = ["＋", "new 1", "new 2", "Search", "@esc", "new 1", "＋", "new 2", "✕"]


async def run(hw, speed, list_only):
    env = {**os.environ, "PC_CONTROL_HOME": tempfile.mkdtemp(), "PC_CONTROL_MODE": "bypass", "PYTHONUTF8": "1",
           "PC_CONTROL_CURSOR": "always", "PC_CONTROL_CURSOR_SPEED": str(speed)}
    async with stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT, env=env)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            async def call(tool, **a):
                res = await s.call_tool(tool, a)
                return "\n".join(x.text for x in res.content if getattr(x, "type", "") == "text")
            full = await call("look", window=str(hw), mode="text")
            if list_only:
                print(full[:3000]); return
            await call("look", window=str(hw), find="editor")
            await call("type", window=str(hw), target="editor", text="Mira la punta del puntero naranja en cada boton que pulso.\n\tlinea con tabulador\n")
            await asyncio.sleep(1.0)
            for name in BUTTONS:
                if name == "@esc":
                    print("esc:", (await call("key", window=str(hw), name="esc"))[:80]); await asyncio.sleep(1.0); continue
                found = await call("look", window=str(hw), find=name)
                m = re.search(r"\[(\d+):" + re.escape(name) + r"\]", found)
                if not m:
                    print(f"{name}: not found ({found[:80]!r})"); continue
                out = await call("click", window=str(hw), target=m.group(1))
                print(f"{name}: {out[:110]}")
                await asyncio.sleep(1.6)                      # time to see where the pointer ended


def main(argv):
    speed = float(argv[argv.index("--speed") + 1]) if "--speed" in argv else 1.0
    exe = realapps.load().get("notepadpp", {}).get("exe")
    guard = FocusGuard("notepad++").start()
    si = subprocess.STARTUPINFO(); si.dwFlags = 1; si.wShowWindow = 7
    p = guard.launch([exe, "-multiInst", "-nosession"], si)
    hw = 0
    for _ in range(100):
        hs = []
        win32gui.EnumWindows(lambda h, _: hs.append(h) if win32process.GetWindowThreadProcessId(h)[1] == p.pid and win32gui.IsWindowVisible(h)
                             and "Notepad++" in win32gui.GetWindowText(h) else None, None)
        if hs:
            hw = hs[0]; break
        time.sleep(0.1)
    time.sleep(0.5)
    win32gui.ShowWindow(hw, 4)                                                         # visible, the focus stays the user's
    win32gui.SetWindowPos(hw, win32con.HWND_TOPMOST, 200, 120, 1100, 700, win32con.SWP_NOACTIVATE)
    time.sleep(0.6)
    try:
        asyncio.run(run(hw, speed, "--list" in argv))
    finally:
        p.kill(); guard.close()


if __name__ == "__main__":
    main(sys.argv[1:])
