"""An app whose UI Automation answers in seconds per element (reported: Premiere Pro freezes while a UIA client reads its tree) is
detected while reading, the walk stops at once, and from then on it is read from pixels only - while normal apps keep their tree.
The slow app is simulated by a fake element tree (each element takes 1.2 s to say its type); the control is the real Notepad-free
desktop tree of this test's own console host, which must not be marked."""
import os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pc_control import core
import win32gui

checks = {}


class SlowEl:
    def __init__(self, depth=0):
        self.depth = depth
    @property
    def ControlTypeName(self):
        time.sleep(1.2); return "ButtonControl"
    Name = "slow"
    def GetChildren(self):
        return [SlowEl(self.depth + 1) for _ in range(3)] if self.depth < 3 else []


hwnd = win32gui.GetForegroundWindow() or core.top_windows()[0]
proc = core.psutil_name(core.window_pid(hwnd))
real_from = core.auto.ControlFromHandle
core.auto.ControlFromHandle = lambda h: SlowEl()
t0 = time.time()
items = core.walk_uia(hwnd, win32gui.GetWindowRect(hwnd), budget_s=6.0)
took = time.time() - t0
core.auto.ControlFromHandle = real_from
checks["the slow app is detected while reading"] = proc in core.SLOW_UIA
checks["the walk stopped at the first slow element (not after 6 s)"] = took < 3.0
checks["from then on its tree is not read (pixels only)"] = core.tree(hwnd, win32gui.GetWindowRect(hwnd)) == []
core.SLOW_UIA.discard(proc)
normal = core.walk_uia(hwnd, win32gui.GetWindowRect(hwnd), budget_s=1.5)
checks["control: a normal app is not marked and keeps its tree"] = proc not in core.SLOW_UIA and len(normal) > 0

for k, v in checks.items():
    print(("ok   " if v else "FAIL ") + k)
if not all(checks.values()):
    sys.exit(1)
print(f"slow tree verification passed ({len(checks)} checks)")
