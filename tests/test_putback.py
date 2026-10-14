"""An app that raises ITSELF above the user's windows when something is pressed in it (Firefox does when invoked through UI Automation) must go back where
it was. Uses the throw-away test window, sent behind the others, and an action that raises it on purpose."""
import os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import win32gui
from pc_control import core, server

st = os.path.join(tempfile.gettempdir(), "pc-control_putback_state.json")
app = subprocess.Popen(["powershell.exe", "-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, "test_app2.ps1"), st], creationflags=0x08000000)
c, hw = {}, None
try:
    for _ in range(80):
        time.sleep(0.25)
        h = [h for h, t in core.list_windows() if t.startswith("pc-control-test2")]
        if h:
            hw = h[0]; break
    time.sleep(1.0)
    win32gui.SetWindowPos(hw, 1, 0, 0, 0, 0, 0x0013)                    # to the very back, without activating it
    time.sleep(0.2)
    z0 = server._zinfo(hw)
    c["the test window is behind others, and its place is known"] = z0 is not None and z0[0] > 0 and z0[1] is not None
    raise_it = lambda: (win32gui.SetWindowPos(hw, 0, 0, 0, 0, 0, 0x0013), "ok (it raised itself)")[1]            # HWND_TOP | NOSIZE | NOMOVE | NOACTIVATE
    r = server._act(hw, False, raise_it)
    z1 = server._zinfo(hw)
    c["an action that raises the window over the user's windows leaves it where it was"] = z1 is not None and z1[0] == z0[0]
    # a window the user has in front is left alone (they are using it)
    win32gui.SetWindowPos(hw, 0, 0, 0, 0, 0, 0x0013); time.sleep(0.2)
    zf = server._zinfo(hw)
    server._put_back(hw, (0, win32gui.GetDesktopWindow()))             # position 0 = it was in front: the user may be using it
    c["a window that was in front is never pushed back"] = server._zinfo(hw)[0] == zf[0]
    # a window that was opened/raised BEFORE the action and is not moved by it stays put
    win32gui.SetWindowPos(hw, 1, 0, 0, 0, 0, 0x0013); time.sleep(0.2); z2 = server._zinfo(hw)
    server._act(hw, False, lambda: "ok (nothing moves)")
    c["an action that moves nothing moves nothing"] = server._zinfo(hw)[0] == z2[0]
    c["_put_back does nothing when the window that was in front of it is gone"] = server._put_back(hw, (3, 999999999)) is None
finally:
    try: win32gui.PostMessage(hw, 0x0010, 0, 0)
    except Exception: pass
    time.sleep(0.4)
    if app.poll() is None:
        app.kill()
for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = len(c) == 5 and all(c.values())
print("put-back verification " + ("passed" if ok else "FAILED"))
sys.exit(0 if ok else 1)