"""open_app(behind=true) / core.open_behind: an app is opened NOT minimized but at the very back of the user's windows, quickly, without
taking the keyboard focus. Uses a real classic Windows program (winver) and closes only its own window."""
import os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import win32gui
from pc_control import core

OVER = {"pc-control-cursor", "pc-control-caret", "pc-control-note", "pc-control-mark"}
tops = lambda: [h for h in core.top_windows() if win32gui.GetWindowText(h) not in OVER]
import threading
core.BACK_GRACE_S = 0.5                                                   # the hook outlives open_behind by this long (8 s in real use)
c, hw = {}, None
fg0 = win32gui.GetForegroundWindow()
before = set(core.top_windows())
try:
    t0 = time.time()
    r = core.open_behind("winver.exe", "winver")
    dt = time.time() - t0
    hw = r["windows"][0]["hwnd"] if r["windows"] else None
    c["the app opened and its window was found"] = hw is not None and hw not in before
    o = tops()
    c["it is at the very back of the user's windows"] = hw in o and o.index(hw) == len(o) - 1
    c["it is not minimized (apps that fail minimized can be read)"] = hw is not None and not win32gui.IsIconic(hw) and win32gui.IsWindowVisible(hw)
    c["it returns as soon as the window is up (under 3 s, it used to take about 4)"] = dt < 3.0
    c["the answer is truthful about the focus (foco_intacto matches who really has it)"] = r["foco_intacto"] == (win32gui.GetForegroundWindow() == fg0)
    c["a window that was already open is never moved"] = all(h in tops() for h in before if win32gui.IsWindow(h) and win32gui.GetWindowText(h) not in OVER and h != hw)
finally:
    if hw:
        try: win32gui.PostMessage(hw, 0x0010, 0, 0)
        except Exception: pass
time.sleep(1.8)
alive = [t.name for t in threading.enumerate() if t.name == "pc-control-back-on-create" and t.is_alive()]
c["the hook stops by itself once the grace period is over (no thread left)"] = alive == []
for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = len(c) == 7 and all(c.values())
print("back-on-create verification " + ("passed" if ok else "FAILED"))
sys.exit(0 if ok else 1)