"""Measure where Claude's cursor really ENDS versus where it should: the pointer's tip on the clicked point, the typing bar right after
the last character written. Real Notepad++, real server, screen grabs with the overlay (layered windows included).

  python tools/cursor_accuracy.py
"""
import asyncio, ctypes, os, re, subprocess, sys, tempfile, time
ctypes.windll.shcore.SetProcessDpiAwareness(2)
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import numpy as np, win32con, win32gui, win32process
from PIL import ImageGrab
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import realapps
from pc_control import scintilla
from pc_control.focus import FocusGuard


def orange(img):
    a = np.asarray(img).astype(int)
    return (a[:, :, 0] > 200) & (a[:, :, 1] > 70) & (a[:, :, 1] < 175) & (a[:, :, 2] < 100)


def tip_near(x, y, r=70):
    """The arrow tip: top-most orange pixel around (x, y). The visible apex is the arrow EDGE, 3x7 px above the orange fill at 150 %."""
    img = ImageGrab.grab(bbox=(x - r, y - r, x + r, y + r), include_layered_windows=True, all_screens=True)
    ys, xs = np.nonzero(orange(img))
    if not len(ys):
        return None
    i = np.argmin(ys * 1000 + xs)
    return x - r + int(xs[i]), y - r + int(ys[i])


def bar_near(x, y, r=30):
    """The typing bar: centre of the orange pixels around (x, y)."""
    img = ImageGrab.grab(bbox=(x - r, y - r, x + r, y + r), include_layered_windows=True, all_screens=True)
    ys, xs = np.nonzero(orange(img))
    if not len(ys):
        return None
    return x - r + int(np.median(xs)), y - r + int(np.median(ys))


async def run(hw):
    env = {**os.environ, "PC_CONTROL_HOME": tempfile.mkdtemp(), "PC_CONTROL_MODE": "bypass", "PYTHONUTF8": "1", "PC_CONTROL_CURSOR": "always"}
    rows = []
    async with stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT, env=env)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            async def call(tool, **a):
                res = await s.call_tool(tool, a)
                return "\n".join(x.text for x in res.content if getattr(x, "type", "") == "text")
            await call("look", window=str(hw), find="editor")
            await call("type", window=str(hw), target="editor", text="abc def ghi")
            await asyncio.sleep(0.6)
            ed = scintilla.editors(hw)[0]
            want = scintilla.end_point(ed)
            got = bar_near(want[0], want[1])
            rows.append(("typing bar after the last character", want[:2], got))
            img = await call("look", window=str(hw), mode="image")
            m = re.search(r"image (\d+)x(\d+)", img)
            wl, wt, wr, wb = win32gui.GetWindowRect(hw)
            k = (wr - wl) / int(m.group(1))
            el, et, er, eb = win32gui.GetWindowRect(ed)
            for fx, fy in ((0.7, 0.6), (0.3, 0.8)):
                ix, iy = int((el - wl + (er - el) * fx) / k), int((et - wt + (eb - et) * fy) / k)
                await call("click", window=str(hw), target=f"{ix},{iy}")
                await asyncio.sleep(0.7)                     # the ring is gone: only the arrow is orange
                want = (wl + int(ix * k), wt + int(iy * k))
                rows.append((f"pointer tip on the click at image {ix},{iy}", want, tip_near(*want)))
    return rows


def main():
    exe = realapps.load().get("notepadpp", {}).get("exe")
    guard = FocusGuard("notepad++").start()
    si = subprocess.STARTUPINFO(); si.dwFlags = 1; si.wShowWindow = 7
    p = guard.launch([exe, "-multiInst", "-nosession", "-notabbar"], si)
    hw = 0
    for _ in range(100):
        hs = []
        win32gui.EnumWindows(lambda h, _: hs.append(h) if win32process.GetWindowThreadProcessId(h)[1] == p.pid and win32gui.IsWindowVisible(h)
                             and "Notepad++" in win32gui.GetWindowText(h) else None, None)
        if hs:
            hw = hs[0]; break
        time.sleep(0.1)
    time.sleep(0.5)
    win32gui.ShowWindow(hw, 4)
    win32gui.SetWindowPos(hw, win32con.HWND_TOP, 200, 150, 1000, 650, win32con.SWP_NOACTIVATE)
    time.sleep(0.6)
    try:
        rows = asyncio.run(run(hw))
    finally:
        p.kill(); guard.close()
    worst = 0
    for what, want, got in rows:
        err = None if got is None else ((got[0] - want[0]) ** 2 + (got[1] - want[1]) ** 2) ** 0.5
        worst = max(worst, 999 if err is None else err)
        print(f"{what}: should be {want}, is {got}" + ("" if err is None else f" -> off by {err:.1f} px"))
    print("cursor accuracy:", "OK" if worst <= 6 else f"OFF (worst {worst:.0f} px)")
    return 0 if worst <= 6 else 1


if __name__ == "__main__":
    sys.exit(main())
