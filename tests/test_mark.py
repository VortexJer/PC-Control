"""show(): an orange oval around an element, ABOVE its window (not behind it), that goes away by itself when what is inside it
changes (the user did what was shown) and stays while nothing happens. Uses a test window on screen that never takes the focus."""
import os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import win32gui
import uiautomation as auto
import threading
from pc_control import actions, core, cursor, server

st = os.path.join(tempfile.gettempdir(), "pc-control_mark_state.json")
app = subprocess.Popen(["powershell.exe", "-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, "test_app2.ps1"), st],
                       creationflags=0x08000000)
c = {}
try:
    hw = None
    for _ in range(80):
        time.sleep(0.25)
        h = [h for h, t in core.list_windows() if t.startswith("pc-control-test2")]
        if h:
            hw = h[0]; break
    assert hw, "the test window did not appear"
    time.sleep(0.8)
    with auto.UIAutomationInitializerInThread():
        core.look(str(hw), "uia")
        it = next(x for x in core._state[hw]["items"].values() if x.name == "Long text")
        l, t = win32gui.GetWindowRect(hw)[:2]; r = core.live_rel(hw, it)
        token = cursor.CURSOR.mark((l + r[0], t + r[1], l + r[2], t + r[3]), "here", 20, 1.0, owner=hw)
        mk = cursor.CURSOR.hwnds["mark"]
        time.sleep(1.5)
        c["the oval is shown"] = bool(win32gui.IsWindowVisible(mk))
        above, w = False, win32gui.GetWindow(hw, 3)                     # windows above the test window in the stacking order
        while w:
            if w == mk:
                above = True; break
            w = win32gui.GetWindow(w, 3)
        c["it is ABOVE its window, not hidden behind it"] = above
        time.sleep(1.5)
        c["while nothing happens it stays"] = bool(win32gui.IsWindowVisible(mk))
        actions.scroll(str(hw), str(it.id), "down", 3)                  # what is inside the marked box changes
        time.sleep(3.0)                                                  # a few window captures, 0.35 s of steady change, and the fade
        c["when what is inside it changes, it goes away by itself"] = not win32gui.IsWindowVisible(mk)
        c["wait_for is told it changed (token + reason)"] = cursor.CURSOR.mark_result == (token, "changed")
        c["wait_for gives up with 'timeout' when nobody does the step"] = server.RAW["wait_for"](str(hw), str(it.id), "go", 3, confirm=True).startswith("timeout")
        pt = server._drag_point(hw, str(it.id)); w0, h0 = win32gui.GetWindowRect(hw)[2] - l, win32gui.GetWindowRect(hw)[3] - t
        c["drag accepts an element (id) as a point, and x,y as before"] = 0 < pt[0] < w0 and 0 < pt[1] < h0 and server._drag_point(hw, "5, 7") == (5, 7)
        threading.Timer(3.0, lambda: actions.scroll(str(hw), str(it.id), "up", 3)).start()          # the user "does" the step
        c["wait_for returns 'changed' as soon as they do it"] = server.RAW["wait_for"](str(hw), str(it.id), "go", 15, confirm=True).startswith("changed")
finally:
    try:
        win32gui.PostMessage(hw, 0x0010, 0, 0)
    except Exception:
        pass
    time.sleep(0.5)
    if app.poll() is None:
        app.kill()
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
ok = len(c) == 8 and all(c.values())
print(f"mark verification {'passed' if ok else 'FAILED'}")
sys.exit(0 if ok else 1)
