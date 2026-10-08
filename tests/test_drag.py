"""Drag and draw: the drag is sent as mouse messages to the window (without touching the user's mouse or focus).
The test window is created off-screen and has a canvas that records what it receives."""
import ctypes, json, os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["PC_CONTROL_CURSOR"] = "off"
from pc_control import core
import uiautomation as auto
import win32gui, win32process

class _LII(ctypes.Structure): _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
def last_input():
    """Timestamp of the user's last REAL input (keyboard/mouse). The messages PC-Control sends do not change it."""
    i = _LII(); i.cbSize = ctypes.sizeof(i); ctypes.windll.user32.GetLastInputInfo(ctypes.byref(i)); return i.dwTime

c = {}
# ---- shapes: pure ----
rect = core.shape_points("rect", [(10, 10), (50, 40)])
c["rect: 4 closed sides (5 points, back to the start)"] = rect[0] == rect[-1] and len(rect) == 5 and {p for p in rect} == {(10, 10), (50, 10), (50, 40), (10, 40)}
el = core.shape_points("ellipse", [(0, 0), (100, 60)])
xs, ys = [p[0] for p in el], [p[1] for p in el]
c["ellipse: inside its box, closed and with many points"] = min(xs) >= 0 and max(xs) <= 100 and min(ys) >= 0 and max(ys) <= 60 and el[0] == el[-1] and len(el) >= 40 and max(xs) - min(xs) >= 98
c["line: only the start and the end even if more are given"] = core.shape_points("line", [(1, 1), (5, 5), (9, 9)]) == [(1, 1), (9, 9)]
c["path: freehand through all the points"] = core.shape_points("path", [(1, 1), (5, 9), (9, 2)]) == [(1, 1), (5, 9), (9, 2)]
dense = core._densify([(0, 0), (60, 0)], 6)
c["the drag is smoothed: one move every 6 px at most"] = len(dense) == 11 and dense[-1] == (60, 0) and all(b[0] - a[0] <= 6 for a, b in zip(dense, dense[1:]))

# ---- against a real window (off-screen) ----
state = os.path.join(tempfile.gettempdir(), "pc-control_drag_state.json")
S = lambda: json.load(open(state, encoding="utf-8-sig"))
p = subprocess.Popen(["powershell.exe", "-STA", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                      "-File", os.path.join(HERE, "test_app.ps1"), state, "-X", "-6000"], creationflags=0x08000000)
try:
    hw = None
    for _ in range(150):
        time.sleep(0.1); f = []
        win32gui.EnumWindows(lambda h, _: f.append(h) if win32gui.GetWindowText(h) == "pc-control-test" and win32process.GetWindowThreadProcessId(h)[1] == p.pid else None, None)
        if f: hw = f[0]; break
    assert hw, "the test window did not appear"
    time.sleep(0.7)
    with auto.UIAutomationInitializerInThread():
        core.look(str(hw))
        wl, wt = win32gui.GetWindowRect(hw)[:2]
        px, py = win32gui.ClientToScreen(hw, (20, 230))                 # corner of the canvas on screen
        ox, oy = px - wl, py - wt                                       # ... and in window pixels (like an image's coordinates)
        fg0 = win32gui.GetForegroundWindow(); mouse0 = win32gui.GetCursorPos(); in0 = last_input()
        r = core.drag(str(hw), [(ox + 30, oy + 20), (ox + 330, oy + 70)], "line"); time.sleep(0.8)
        s = S()
        c["line: reply ok"] = r.startswith("ok (drag")
        c["line: the canvas received ONE press, many moves with the button and ONE release"] = s["down"] == 1 and s["up"] == 1 and s["moves"] >= 40 and s["held"] == 0
        c["line: the path covers end to end (30..330 in x, 20..70 in y)"] = s["minx"] <= 40 and s["maxx"] >= 326 and s["miny"] <= 26 and s["maxy"] >= 66
        user_moved = last_input() != in0                                 # the user touched something during the drag: this check proves nothing
        c["the user's mouse did NOT move and focus did not change (if the user did not touch anything during the test)"] = user_moved or (win32gui.GetCursorPos() == mouse0 and win32gui.GetForegroundWindow() == fg0)
        if user_moved: print("WARNING: you touched the mouse or keyboard during the drag; the mouse/focus check does not count this time")
        before = S()["moves"]
        r2 = core.drag(str(hw), [(ox + 50, oy + 10), (ox + 200, oy + 60)], "rect"); time.sleep(0.8)
        s2 = S()
        c["rectangle: another full drag (4 sides) on the same canvas"] = r2.startswith("ok") and s2["down"] == 2 and s2["up"] == 2 and s2["moves"] - before >= 40
        # the user is pressing the mouse: Claude yields and sends NOTHING
        real = core.wait_user_mouse_idle; core.wait_user_mouse_idle = lambda timeout=None: False
        r3 = core.drag(str(hw), [(ox + 10, oy + 10), (ox + 100, oy + 50)], "line"); time.sleep(0.4)
        core.wait_user_mouse_idle = real
        c["if the user is dragging/pressing: it yields and sends nothing"] = r3.startswith("did not drag") and S()["down"] == 2
        r4 = core.drag(str(hw), [(1, 1)], "path")
        c["with a single point: it warns instead of failing"] = "at least 2 points" in r4
        # if it fails midway, the button is released anyway
        c["at the end the button is never left 'pressed' in the app"] = S()["held"] == 0
finally:
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values()) and len(c) >= 12
print(f"drag verification passed ({len(c)} checks)" if ok else "drag verification FAILED")
sys.exit(0 if ok else 1)
