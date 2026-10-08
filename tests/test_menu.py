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


# ---- apps that ignore mouse messages (modern Store/XAML): the element is pressed through its UI Automation pattern ----
class Tog:
    def __init__(self, log): self.log = log
    def Toggle(self): self.log.append("Toggle")
class Ctrl2:
    def __init__(self, invoke=False, toggle=False): self.log = []; self._i, self._t = invoke, toggle
    def GetInvokePattern(self): return Inv(self.log, "i") if self._i else None
    def GetTogglePattern(self): return Tog(self.log) if self._t else None
    def GetSelectionItemPattern(self): return None
    def GetExpandCollapsePattern(self): return None
cx = Ctrl2(invoke=True); item(cx, "Seven", kind="button")
c["invoke: a button of an app that ignores mouse messages is pressed through UI Automation Invoke"] = core.invoke(str(H), 1).startswith("ok (UI Automation Invoke") and cx.log == ["Invoke"]
ct = Ctrl2(toggle=True); item(ct, "Option", kind="checkbox")
c["invoke: a checkbox falls back to Toggle"] = core.invoke(str(H), 1).startswith("ok (UI Automation Toggle") and ct.log == ["Toggle"]
cn = Ctrl2(); item(cn, "Plain", kind="text")
c["invoke: an element with no pattern returns None (the caller keeps the plain click)"] = core.invoke(str(H), 1) is None
c["invoke: an unknown id returns None"] = core.invoke(str(H), 99) is None


# ---- title-bar buttons (minimize / maximize / close) are not in the client area: they are pressed through Invoke ----
class Par:
    ControlTypeName = "TitleBarControl"
class CtrlTB(Ctrl2):
    def GetParentControl(self): return Par()
class CtrlOther(Ctrl2):
    def GetParentControl(self):
        class P: ControlTypeName = "PaneControl"
        return P()
ctb = CtrlTB(invoke=True); item(ctb, "Close", kind="button"); posted.clear()
r_tb = core.click(str(H), 1)
c["title-bar button: pressed through Invoke, no mouse message sent"] = r_tb.startswith("ok (UI Automation Invoke") and "title-bar" in r_tb and ctb.log == ["Invoke"] and not posted
c["in_title_bar: true only for a title-bar child"] = core.in_title_bar(CtrlTB()) and not core.in_title_bar(CtrlOther()) and not core.in_title_bar(object())


# ---- fields that are not native edit windows (web pages, WPF, XAML): filled through the UI Automation value ----
class ValPat:
    def __init__(self, value="", ro=False): self.Value, self.IsReadOnly = value, ro
    def SetValue(self, v): self.Value = v
class CtrlV:
    IsPassword = False
    def __init__(self, value="", ro=False): self.pat = ValPat(value, ro)
    def GetValuePattern(self): return self.pat
cv = CtrlV("Ana"); item(cv, "Your name", kind="edit")
r_v = core.type_text(str(H), " y Luis", target=1)
c["web/WPF field: typed through UI Automation SetValue (append) and read back"] = r_v.startswith("ok (UI Automation SetValue") and cv.pat.Value == "Ana y Luis" and "the field now contains the text" in r_v
r_r = core.type_text(str(H), "Nuevo", target=1, replace=True)
c["web/WPF field: replace sets the whole value"] = cv.pat.Value == "Nuevo" and "the field now contains the text" in r_r
ro = CtrlV("fijo", ro=True); item(ro, "Read only", kind="edit")
posted_chars = []
real_pm = core.win32gui.PostMessage; core.win32gui.PostMessage = lambda *a: posted_chars.append(a)
core._focus_hwnd = lambda h: 1234
r_ro = core.type_text(str(H), "x", target=1)
core.win32gui.PostMessage = real_pm
c["a read-only field is not overwritten (falls back, and says it is unconfirmed)"] = ro.pat.Value == "fijo" and "unconfirmed" in r_ro

# ---- a click that closes the window must not turn into an error ----
real_isw = core.win32gui.IsWindow
core.win32gui.IsWindow = lambda h: False
c["changes(): the window closed after the click -> a clear message, not an exception"] = core.changes(str(H)) == "the window is gone (it was closed)"
c["... and the verdict says the window closed"] = server.verdict("ok (BM_CLICK, no focus)", "the window is gone (it was closed)") == "the window closed"
def _gone(q): raise LookupError("no window")
real_fw = core.find_window; core.find_window = _gone
c["changes(): a title that no longer matches any window -> the same clear message"] = core.changes("Calculator") == "the window is gone (it was closed)"
core.find_window = real_fw; core.win32gui.IsWindow = real_isw

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values())
print(f"menu verification passed ({len(c)} checks)" if ok else "menu verification FAILED")
sys.exit(0 if ok else 1)
