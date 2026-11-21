"""Demo of Claude's cursor at several speeds, on a REAL app (Notepad++), so the user can pick the speed they like.

  python tools/cursor_demo.py [speeds...] [--out DIR]      default speeds: 1 1.5 2 3
For each speed a fresh PC-Control server (PC_CONTROL_CURSOR=always, PC_CONTROL_CURSOR_SPEED=<speed>) types, clicks and drags INSIDE
the editor of a visible Notepad++ (launched through the focus guard and shown without activation: the focus stays the user's), and
the window is recorded to a GIF. Every target is computed from the editor's real rectangle, never fixed coordinates.
Choose with: setx PC_CONTROL_CURSOR_SPEED <speed>   (or the env of the MCP server).
"""
import asyncio, os, re, subprocess, sys, tempfile, threading, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import win32con, win32gui, win32process
from PIL import ImageGrab
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import realapps
from pc_control import scintilla


def record(rect, stop, frames):
    while not stop.is_set():
        t = time.time()
        frames.append((t, ImageGrab.grab(bbox=rect, all_screens=True, include_layered_windows=True)))
        time.sleep(max(0, 1 / 25 - (time.time() - t)))


async def demo(hw, sp, pointer_only=False):
    env = {**os.environ, "PC_CONTROL_HOME": tempfile.mkdtemp(), "PC_CONTROL_MODE": "bypass", "PYTHONUTF8": "1",
           "PC_CONTROL_CURSOR": "always", "PC_CONTROL_CURSOR_SPEED": str(sp)}
    out = []
    async with stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT, env=env)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            async def call(tool, **a):
                res = await s.call_tool(tool, a)
                txt = "\n".join(x.text for x in res.content if getattr(x, "type", "") == "text")
                out.append((tool, bool(getattr(res, "isError", False)), txt[:120])); return txt
            await call("show", window=str(hw), target="editor", label=f"{'puntero' if pointer_only else 'velocidad'} {sp:g}x", seconds=2)
            await asyncio.sleep(1.0)
            text = ("linea uno para seleccionar con el arrastre\nlinea dos\nlinea tres\n" if pointer_only
                    else f"Demo a velocidad {sp:g}x: el texto sale cuando la barra llega.\n")
            await call("type", window=str(hw), target="editor", text=text)
            img = await call("look", window=str(hw), mode="image")
            m = re.search(r"image (\d+)x(\d+)", img)
            wl, wt, wr, wb = win32gui.GetWindowRect(hw)
            k = (wr - wl) / int(m.group(1)) if m else 1.0
            el, et, er, eb = win32gui.GetWindowRect(scintilla.editors(hw)[0])
            def pt(fx, fy):                                     # a point INSIDE the editor, as image pixels of the last look
                return f"{int((el - wl + (er - el) * fx) / k)},{int((et - wt + (eb - et) * fy) / k)}"
            if pointer_only:                                    # long trips corner to corner, a pause at rest between them
                for fx, fy in ((0.9, 0.85), (0.1, 0.85), (0.9, 0.2), (0.5, 0.5)):
                    await call("click", window=str(hw), target=pt(fx, fy)); await asyncio.sleep(0.5)
                a, b = pt(0.02, 0.03), pt(0.4, 0.13)
                await call("drag", window=str(hw), points=f"{a};{b}", shape="line"); await asyncio.sleep(0.6)
                await call("click", window=str(hw), target=pt(0.85, 0.9))
            else:
                await call("click", window=str(hw), target=pt(0.6, 0.55))
                await call("click", window=str(hw), target=pt(0.15, 0.08))
                a, b = pt(0.05, 0.04), pt(0.45, 0.04)
                await call("drag", window=str(hw), points=f"{a};{b}", shape="line")
                await call("click", window=str(hw), target=pt(0.8, 0.8))
            await asyncio.sleep(0.8)
    return out


def main(argv):
    outdir = tempfile.gettempdir()
    if "--out" in argv:
        i = argv.index("--out"); outdir = argv[i + 1]; del argv[i:i + 2]
    pointer_only = "--pointer" in argv
    argv = [a for a in argv if a != "--pointer"]
    speeds = [float(a) for a in argv] or [1, 1.5, 2, 3]
    exe = realapps.load().get("notepadpp", {}).get("exe")
    if not exe:
        print("install it first: python tools/realapps.py install notepadpp"); return 2
    # Through PC-Control's focus guard (Notepad++ activates itself while starting: a plain launch steals the focus),
    # started minimized and then shown WITHOUT activation.
    from pc_control.focus import FocusGuard
    guard = FocusGuard("notepad++").start()
    si = subprocess.STARTUPINFO(); si.dwFlags = 1; si.wShowWindow = 7                 # SW_SHOWMINNOACTIVE
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
    win32gui.ShowWindow(hw, 4)                                                        # SW_SHOWNOACTIVATE: visible, the focus stays yours
    win32gui.SetWindowPos(hw, win32con.HWND_TOPMOST, 120, 120, 900, 560, win32con.SWP_NOACTIVATE)   # on top so the GIF is clean; never the focus
    time.sleep(0.8)
    rect = win32gui.GetWindowRect(hw)
    try:
        for sp in speeds:
            frames, stop = [], threading.Event()
            th = threading.Thread(target=record, args=(rect, stop, frames), daemon=True); th.start()
            t0 = time.time()
            res = asyncio.run(demo(hw, sp, pointer_only))
            stop.set(); th.join(2)
            path = os.path.join(outdir, f"pc-control-{'pointer' if pointer_only else 'cursor'}-{sp:g}x.gif")
            imgs = [f.convert("P", palette=1, colors=96) for _, f in frames]
            durs = [max(20, int((frames[i + 1][0] - frames[i][0]) * 1000)) for i in range(len(frames) - 1)] + [800]
            imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=durs, loop=0)
            bad = [r for r in res if r[1]]
            print(f"speed {sp:g}x: {time.time() - t0:.1f} s, {len(imgs)} frames -> {path}" + (f" | errors: {bad}" if bad else ""))
    finally:
        p.kill()
        guard.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
