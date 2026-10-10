"""What look() lists from the UI tree: nameless fields still get an id (Chromium drops a used field's name), internal parts and
unlabelled icon buttons do not, scroll bars are left out, and a list row made only of cells reads as ONE line with the cells' values."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pc_control import core


class R:
    left, top, right, bottom = 10, 10, 210, 30
    def width(self): return 200
    def height(self): return 20


class Fake:
    def __init__(self, kind, name, kids=(), aid="", value=None):
        self.ControlTypeName, self.Name, self.AutomationId, self.HelpText = kind, name, aid, ""
        self.BoundingRectangle, self.IsOffscreen, self._k, self.value = R(), False, list(kids), value
    def GetChildren(self): return self._k


def run():
    row = Fake("ListItemControl", "report.txt", [Fake("TextControl", "report.txt"), Fake("EditControl", "Date modified", value="08/10/2026 21:55"),
                                                  Fake("EditControl", "Type", value="Text document"), Fake("EditControl", "Size", value="‎2 KB")])
    row_btn = Fake("ListItemControl", "Wi-Fi", [Fake("TextControl", "Wi-Fi"), Fake("ButtonControl", "On")])
    sb = Fake("ScrollBarControl", "Vertical", [Fake("ButtonControl", "Line down")])
    root = Fake("WindowControl", "", [Fake("EditControl", ""), Fake("EditControl", "", aid="cmd"), Fake("CheckBoxControl", ""),
                                      Fake("ButtonControl", "", aid="btnGo"), Fake("ButtonControl", "", aid="PART_ChevronButton"),
                                      Fake("ButtonControl", ""), sb, row, row_btn])
    core.auto.ControlFromHandle = lambda h: root
    core.field_value = lambda c, editable_only=False: (None if editable_only else c.value)
    items = core.walk_uia(1, (0, 0, 500, 500))
    names = [i.name for i in items]
    checks = {
        "nameless fields get an id": {"(unnamed checkbox)", "(unnamed text field)", "cmd", "btnGo"} <= set(names),
        "internal parts and unlabelled icon buttons are left out": not any(n.startswith("PART_") or n == "(unnamed button)" for n in names),
        "scroll bars are left out": "Line down" not in names and "Vertical" not in names,
        "a row of cells is one line with the values": [i.value for i in items if i.name == "report.txt" and i.kind == "listitem"] == ["08/10/2026 21:55 · Text document · 2 KB"]
                                                      and "Date modified" not in names,
        "a row with a button is walked as usual": "On" in names,
    }
    for k, v in checks.items():
        print(("OK   " if v else "FAIL ") + k)
    if not all(checks.values()):
        print("   names:", names)
    return all(checks.values())


if __name__ == "__main__":
    print("unnamed field verification " + ("passed" if run() else "FAILED"))
