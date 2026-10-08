"""Menu entries (e.g. Word's context menu): they are pressed through accessibility, not through mouse messages to the app window."""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pc_control import core, server

class P:
    def __init__(self, log, name): self.log, self.name = log, name
class Inv(P):
    def Invoke(self): self.log.append("Invoke")
class Exp(P):
    def Expand(self): self.log.append("Expand")
class Ctrl:
    def __init__(self, enabled=True, invoke=True, expand=False):
        self.IsEnabled, self.log, self._i, self._e = enabled, [], invoke, expand
    def GetInvokePattern(self): return Inv(self.log, "i") if self._i else None
    def GetExpandCollapsePattern(self): return Exp(self.log, "e") if self._e else None
    def GetSelectionItemPattern(self): return None
    def GetLegacyIAccessiblePattern(self): return None

H = 9001
core.find_window = lambda q: H
core.win32gui.GetClassName = lambda h: "OpusApp" if h == H else ""
core.win32gui.GetWindowRect = lambda h: (0, 0, 800, 600)
core._native = lambda ctrl: (0, "")
core.win32gui.IsIconic = lambda h: False
posted = []
core._post_click = lambda *a, **k: posted.append(a) or "mouse"

def item(ctrl, name, kind="menuitem"):
    it = core.Item(kind, name, (700, 500, 790, 520), ctrl, "uia", True); it.id = 1
    core._state[H] = {"items": {1: it}, "rect": (0, 0, 800, 600), "scale": 1.0, "mode": "uia", "lines": set()}
    return it

c = {}
cp = Ctrl(); item(cp, "Copy")
r = core.click(str(H), 1)
c["menu entry: pressed through accessibility (Invoke)"] = cp.log == ["Invoke"] and r.startswith("ok (menu entry")
c["... and NO mouse message is sent to the app window (it would be lost)"] = not posted
cs = Ctrl(invoke=False, expand=True); item(cs, "Synonyms")
r2 = core.click(str(H), 1)
c["entry with a submenu: it expands (Expand)"] = cs.log == ["Expand"] and "submenu" in r2
cd = Ctrl(enabled=False); item(cd, "Synonyms")
r3 = core.click(str(H), 1)
c["disabled (greyed out) entry: says so clearly instead of 'no visible effect'"] = "is disabled" in r3 and not cd.log
c["... and the verdict is NOT EXECUTED"] = server.verdict(r3, "no changes") == "NOT EXECUTED"
c["the verdict of a pressed entry does not say 'no visible effect' even if the menu closes"] = "NO VISIBLE EFFECT" not in server.verdict(r, "no changes")
# what is NOT a menu entry follows its usual path
cb = Ctrl(); item(cb, "Press", kind="button")
core.live_rel = lambda h, it: it.rect
core.click(str(H), 1)
c["a normal button does NOT use the menu path"] = cb.log == [] and bool(posted)

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values())
print(f"menu verification passed ({len(c)} checks)" if ok else "menu verification FAILED")
sys.exit(0 if ok else 1)
