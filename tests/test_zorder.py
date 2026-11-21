"""Where a window that came up without the user asking ends: a NEW window behind ALL windows; one that already existed back at its own
place in the stacking order (right below the same window as before), as the user had left it. Probe windows (they never take the
focus); FocusGuard.tuck and core.soft_minimize are the code under test."""
import ctypes, json, os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import win32con, win32gui
from pc_control import core, focus

c = {}
u = ctypes.windll.user32
F = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
tmp = tempfile.mkdtemp()
procs = []


def probe(name, x):
    log = os.path.join(tmp, name + ".log")
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "input_probe_app.py"), name, log, str(x), "300", "300", "200"])
    procs.append(p)
    for _ in range(100):
        try:
            return next(json.loads(l)["hwnd"] for l in open(log) if '"ready"' in l)
        except Exception:
            time.sleep(0.05)


def above(h):
    """The first VISIBLE window right above h (invisible helper windows, such as an app's IME window, sit glued above their owner)."""
    prev = u.GetWindow(h, 3)                                           # GW_HWNDPREV
    while prev and not win32gui.IsWindowVisible(prev):
        prev = u.GetWindow(prev, 3)
    return prev


def is_bottom(h):
    nxt = u.GetWindow(h, 2)                                            # GW_HWNDNEXT: nothing visible with a title below it
    while nxt:
        if win32gui.IsWindowVisible(nxt) and win32gui.GetWindowText(nxt) and win32gui.GetClassName(nxt) not in ("Progman", "WorkerW"):
            return False
        nxt = u.GetWindow(nxt, 2)
    return True


fg0 = win32gui.GetForegroundWindow()
try:
    X, W = probe("PCC-Z X", 100), probe("PCC-Z W", 450)
    T = probe("PCC-Z T", 800)
    time.sleep(0.3)
    win32gui.SetWindowPos(W, X, 0, 0, 0, 0, F)                         # the user's arrangement: W right below X
    time.sleep(0.1)
    g = focus.FocusGuard.__new__(focus.FocusGuard)
    order = []
    win32gui.EnumWindows(lambda h, _: order.append(h), None)
    g.windows0, g.fg0, g.above0 = set(order), fg0, {}
    last = 0
    for h in order:
        g.above0[h] = last
        if win32gui.IsWindowVisible(h):
            last = h
    # 2) an existing window comes up: back right below the same window as before
    win32gui.SetWindowPos(W, win32con.HWND_TOP, 0, 0, 0, 0, F); time.sleep(0.1)
    g.tuck(W); time.sleep(0.1)
    if above(W) != X:
        print(f"   W={W} X={X} snapshot above={g.above0.get(W)} ({win32gui.GetClassName(g.above0.get(W) or 0) if g.above0.get(W) else '-'}) now above={above(W)}")
    c["existing window that came up goes back right below the same window as before"] = above(W) == X
    # 1) a NEW window (created after the guard started) comes up: behind ALL windows
    N = probe("PCC-Z N", 1150); time.sleep(0.3)
    win32gui.SetWindowPos(N, win32con.HWND_TOP, 0, 0, 0, 0, F); time.sleep(0.1)
    g.tuck(N); time.sleep(0.1)
    c["a NEW window that came up goes behind ALL windows"] = is_bottom(N)
    # 3) an existing window that was the very top: right below the user's window, never above it
    g.above0[T] = 0
    win32gui.SetWindowPos(T, win32con.HWND_TOP, 0, 0, 0, 0, F); time.sleep(0.1)
    g.tuck(T); time.sleep(0.1)
    c["an existing window that was at the very top ends right below the user's window"] = (above(T) == fg0) if fg0 else True
    # 4) soft minimize: new -> bottom then minimized; existing -> its place then minimized
    win32gui.SetWindowPos(W, X, 0, 0, 0, 0, F); time.sleep(0.1)
    prev_w = core.visible_above(W)
    win32gui.SetWindowPos(W, win32con.HWND_TOP, 0, 0, 0, 0, F)
    core._SOFT.clear()
    core.soft_minimize(W, fg0, prev_w); core.soft_minimize(N, fg0)
    time.sleep(0.1)
    if above(W) != X:
        print(f"   soft: X={X} prev_w={prev_w} ({win32gui.GetClassName(prev_w)} vis={win32gui.IsWindowVisible(prev_w)}) above(W)={above(W)} ({win32gui.GetClassName(above(W)) if above(W) else '-'})")
    c["soft minimize: the existing window first goes back to its place"] = above(W) == X
    c["soft minimize: the new window first goes behind all windows"] = is_bottom(N)
    time.sleep(core.SOFT_MINIMIZE_S + 0.4)
    c["soft minimize: both end minimized, out of sight"] = bool(win32gui.IsIconic(W) and win32gui.IsIconic(N))
    c["the user's window kept the foreground throughout"] = win32gui.GetForegroundWindow() == fg0
finally:
    for p in procs:
        p.kill()

bad = [k for k, v in c.items() if not v]
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"z-order verification passed ({len(c)} checks)" if not bad else f"z-order verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
