"""An EditControl whose Name is empty (Chromium drops the placeholder once the field was used) must still be listed."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pc_control import core


class R:
    left, top, right, bottom = 10, 10, 210, 30
    def width(self): return 200
    def height(self): return 20


class Fake:
    def __init__(self, kind, name, kids=(), aid=""):
        self.ControlTypeName, self.Name, self.AutomationId, self.HelpText = kind, name, aid, ""
        self.BoundingRectangle, self.IsOffscreen, self._k = R(), False, list(kids)
    def GetChildren(self): return self._k


def run():
    root = Fake("WindowControl", "", [Fake("EditControl", ""), Fake("EditControl", "", aid="cmd"), Fake("CheckBoxControl", ""), Fake("ButtonControl", "", aid="btnGo")])
    core.auto.ControlFromHandle = lambda h: root
    core.field_value = lambda c, editable_only=False: ""
    items = core.walk_uia(1, (0, 0, 500, 500))
    names = sorted(i.name for i in items)
    ok = names == ["(unnamed checkbox)", "(unnamed text field)", "btnGo", "cmd"]
    print(("OK   " if ok else "FAIL ") + "unnamed edit fields are listed:", names)
    return ok


if __name__ == "__main__":
    print("unnamed field verification " + ("passed" if run() else "FAILED"))
