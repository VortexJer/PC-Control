"""Click and typing without focus or mouse, checked against the truth written by the test app itself (hidden desktop)."""
import json, os, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from pc_control import core
from pc_control.hidden import DESK

state = os.path.join(tempfile.gettempdir(), "pc-control_test_state.json")
S = lambda: json.load(open(state, encoding="utf-8-sig"))
cmd = f'powershell.exe -STA -NoProfile -ExecutionPolicy Bypass -File "{os.path.join(HERE, "test_app.ps1")}" "{state}"'
res = {}
try:
    r = DESK.open(cmd); hw = r["windows"][0]["hwnd"] if r["windows"] else None
    assert hw, f"the window did not appear: {r}"
    time.sleep(0.5)
    lk = DESK.run(lambda: core.look(str(hw))); print("LOOK:", lk["mode"], lk["tokens"], "tok |", lk["why"])
    items = core._state[hw]["items"]; by = lambda s: next(i for i, x in items.items() if s in x.name)
    btn, edt, chk = by("Press"), by("Name field"), by("option")
    def case(name, fn, key, cond):
        before = S()[key]; out = DESK.run(fn); time.sleep(0.5); after = S()[key]
        res[name] = bool(cond(before, after)); print(f"{name:22} {key}: {before!r} -> {after!r} | {out}")
    case("click button (id)", lambda: core.click(str(hw), btn), "clicks", lambda b, a: a == b + 1)
    case("click checkbox (id)", lambda: core.click(str(hw), chk), "checked", lambda b, a: a is True)
    case("type (id)", lambda: core.type_text(str(hw), "hello", target=edt), "text", lambda b, a: a == "hello")
    case("type more (id)", lambda: core.type_text(str(hw), " world", target=edt), "text", lambda b, a: a == "hello world")
    case("replace (id)", lambda: core.type_text(str(hw), "new", target=edt, replace=True), "text", lambda b, a: a == "new")
    x0, y0, x1, y1 = items[btn].rect
    case("click by coordinates", lambda: core.click(str(hw), ((x0 + x1) // 2, (y0 + y1) // 2)), "clicks", lambda b, a: a == b + 1)
finally:
    DESK.close()
good = len(res) == 6 and all(res.values())
print("background verification passed" if good else "background verification FAILED")
sys.exit(0 if good else 1)
