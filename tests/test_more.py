"""Scroll, full-text read, shortcuts without keys, drop-downs/lists and find(+wait), checked against the truth the test app writes (hidden desktop)."""
import json, os, re, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from pc_control import actions, core
from pc_control.hidden import DESK

state = os.path.join(tempfile.gettempdir(), "pc-control_test2_state.json")
S = lambda: json.load(open(state, encoding="utf-8-sig"))
cmd = f'powershell.exe -STA -NoProfile -ExecutionPolicy Bypass -File "{os.path.join(HERE, "test_app2.ps1")}" "{state}"'
res = {}


def check(name, ok, detail=""):
    res[name] = bool(ok); print(("OK   " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


try:
    r = DESK.open(cmd); hw = r["windows"][0]["hwnd"] if r["windows"] else None
    assert hw, f"the window did not appear: {r}"
    time.sleep(0.8); q = str(hw)
    run = DESK.run
    lk = run(lambda: core.look(q, "uia"))
    items = core._state[hw]["items"]
    by = lambda s: next(i for i, x in items.items() if s in x.name)
    color, cities, longt = by("Color"), by("Cities"), by("Long text")

    # shortcuts without keys
    out = run(lambda: core.key(q, "ctrl+s")); time.sleep(0.4)
    check("ctrl+s runs the menu command Save", S()["saved"] == 1, out)
    out = run(lambda: core.key(q, "Ctrl+Shift+N")); time.sleep(0.4)
    check("ctrl+shift+n runs New (any spelling)", S()["newdoc"] == 1, out)
    out = run(lambda: core.key(q, "ctrl+p"))
    check("a disabled menu command is refused and says why", out.startswith("did not press") and "disabled" in out, out)
    out = run(lambda: core.key(q, "ctrl+q"))
    check("an unknown shortcut is refused, no keys pressed", out.startswith("did not press") and S()["saved"] == 1, out[:90])
    check("Spanish/German spellings normalise", actions.norm_combo("Ctrl+Mayús+S") == actions.norm_combo("strg+umschalt+s") == ("ctrl", "shift", "s"))
    out = run(lambda: core.key(q, "f5"))
    check("function keys are accepted as single keys", out.startswith("ok"), out)

    # drop-down and list
    out = run(lambda: core.type_text(q, "green", target=color)); time.sleep(0.3)
    check("type on a drop-down selects the entry (case-insensitive)", S()["color"] == "Green", out)
    out = run(lambda: core.type_text(q, "Dark", target=color)); time.sleep(0.3)
    check("a prefix selects the matching entry", S()["color"] == "Dark blue", out)
    out = run(lambda: core.type_text(q, "City 57", target=cities)); time.sleep(0.3)
    check("type on a list selects the entry, also out of view", S()["pick"] == "City 57", out)
    out = run(lambda: core.type_text(q, "Purple", target=color))
    check("a missing option is refused and lists the options", "did not select" in out and "Red" in out, out[:100])

    # scroll
    run(lambda: core.look(q, "uia")); items = core._state[hw]["items"]
    color, cities, longt = by("Color"), by("Cities"), by("Long text")
    out = run(lambda: actions.scroll(q, str(longt), "down", 2))
    m = re.search(r"(\d+)% -> (\d+)%|position (\d+) -> (\d+)", out)
    check("scroll down an element moves it", m and (m.group(1) != m.group(2) if m.group(1) else m.group(3) != m.group(4)), out)
    out = run(lambda: actions.scroll(q, str(longt), "bottom"))
    check("scroll to the bottom", out.startswith("ok"), out)
    out = run(lambda: actions.scroll(q, str(longt), "down"))
    check("scrolling past the end says so", "already at the edge" in out, out)
    out = run(lambda: actions.scroll(q, None, "down"))
    check("scroll with no target finds a scrollable area", out.startswith("ok"), out)
    out = run(lambda: actions.scroll(q, None, "sideways"))
    check("a bad direction is refused", out.startswith("did not scroll"), out)

    # full text
    out = run(lambda: actions.read_text(q, str(longt)))
    check("read gives the whole text of a field, also what is out of view", "THE END MARKER" in out and "Line 1 of" in out, out[:80])
    out = run(lambda: actions.read_text(q, str(longt), start=0, length=300))
    check("read in pieces says where to continue", "more: read(start=300)" in out, out.split("\n")[0])
    out = run(lambda: actions.read_text(q))
    check("read with no target picks the biggest text", "THE END MARKER" in out, out.split("\n")[0])

    # find (+wait) and clicking something out of view
    run(lambda: actions.scroll(q, str(cities), "top"))
    out = run(lambda: actions.find(q, "City 70"))
    check("find lists an element that is out of view", "City 70" in out and "out of view" in out, out.replace("\n", " / ")[:140])
    out = run(lambda: core.click(q, 1)); time.sleep(0.5)
    check("clicking it scrolls it into view and presses it", S()["pick"] == "City 70", out)
    out = run(lambda: actions.find(q, "Deep button"))
    out = run(lambda: core.click(q, 1)); time.sleep(0.5)
    check("find, then click by its id", S()["deep"] == 1, out)
    t0 = time.time(); out = run(lambda: actions.find(q, "Zzz-nothing", wait=1.2)); dt = time.time() - t0
    check("find with wait gives up after the time and says NOT found", out.startswith("NOT found") and 1.0 <= dt < 6, f"{dt:.1f}s {out[:60]}")
    out = run(lambda: actions.find(q, "!Zzz-nothing", wait=5));
    check("'!text' is satisfied at once when the text is absent", out.startswith("found"), out[:60])
finally:
    DESK.close()
good = len(res) >= 21 and all(res.values())
print(f"more verification {'passed' if good else 'FAILED'} ({sum(res.values())}/{len(res)} checks)")
sys.exit(0 if good else 1)
