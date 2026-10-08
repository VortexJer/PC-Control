"""Rules for when a new window (a dialog of one of your apps) is NOT sent down: you are using it, it was already on top...

The test window is born OUTSIDE all monitors (it is never seen) on your desktop, to test the real z-order.
"""
import os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pc_control import core
import win32con, win32gui, win32process

# 1) table for the pure rule: (is_window, is_fg, user_in_same_app, already_top, cursor_over, is_new) -> send down?
table = [
    ((True, False, False, False, False, True), True,  "normal new window: sent down"),
    ((True, False, False, True, False, True), True,  "NEW window: born on top by force and still sent down"),
    ((False, False, False, False, False, True), False, "no longer exists"),
    ((True, True, False, False, False, True), False, "it is the active window"),
    ((True, False, True, False, False, True), False, "the user is working in another window of that app"),
    ((True, False, False, True, False, False), False, "existing and already on top: not sent down"),
    ((True, False, False, False, True, False), False, "existing with the user's mouse over it"),
    ((True, False, False, False, True, True), True,  "NEW under the cursor: not the user's usage, sent down"),
]
bad = [d for args, want, d in table if core.should_tuck(*args)[0] != want]
for args, want, d in table:
    assert core.should_tuck(*args)[1], "every decision explains its reason"
if bad:
    print("FAILURES in the table:", bad); sys.exit(1)

# 2) real integration with a window of our own born off-screen
state = os.path.join(tempfile.gettempdir(), "pc-control_tuck_state.json")
cmd = ["powershell.exe", "-STA", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
       "-File", os.path.join(HERE, "test_app.ps1"), state, "-X", "-6000"]
p = subprocess.Popen(cmd, creationflags=0x08000000)
ok = False
try:
    hw = None
    for _ in range(150):
        time.sleep(0.1)
        found = []
        win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.GetWindowText(h) == "pc-control-test"
                             and win32process.GetWindowThreadProcessId(h)[1] == p.pid else None, None)
        if found: hw = found[0]; break
    assert hw, "the test window did not appear"
    time.sleep(0.5)
    assert core.is_hidden(hw), "the test window must be born outside all monitors"
    def on_top():
        ws = core.top_windows(); above = ws[:ws.index(hw)]
        return all(win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST for h in above)
    # a NEW window is born on top and is sent to the back
    win32gui.SetWindowPos(hw, win32con.HWND_TOP, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
    went_down = core.tuck(hw, is_new=True); time.sleep(0.2)
    # a window that was ALREADY on top (always visible) is not sent down
    for _ in range(5):
        win32gui.SetWindowPos(hw, win32con.HWND_TOPMOST, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        time.sleep(0.2)
        if on_top(): break
    assert on_top(), "the test could not raise the window"
    moved = core.tuck(hw, is_new=False); time.sleep(0.2)
    stayed = on_top()
    print(f"new -> sent down: {went_down} | already on top -> tuck returned {moved}, still on top: {stayed}")
    ok = went_down and (not moved) and stayed
finally:
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)
print("tuck verification passed" if ok else "tuck verification FAILED")
sys.exit(0 if ok else 1)
