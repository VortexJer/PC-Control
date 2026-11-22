"""A real web page in Microsoft Edge (guest profile, hidden desktop): read the whole page, find, pick in a <select> (its onchange
must fire), press a button that is out of view, scroll. Skipped if Edge is not installed."""
import os, re, sys, tempfile, time, shutil
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from pc_control import actions, core
from pc_control.hidden import DESK

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not os.path.exists(EDGE):
    print("web verification passed (skipped: Edge not installed)"); sys.exit(0)
work = os.path.join(tempfile.gettempdir(), "pc-control_web_test"); shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
rows = "".join(f"<p>Paragraph {i}: filler text to make the page long.</p>" for i in range(1, 150))
page = os.path.join(work, "long.html")
open(page, "w", encoding="utf-8").write(f"""<!doctype html><html><head><meta charset=utf-8><title>pcc-web-test</title></head><body>
<h1>Long test page</h1>
<label>Country <select onchange="document.getElementById('out').textContent='chosen:'+this.value"><option>Spain</option><option>France</option><option>Portugal</option></select></label>
<p id=out>nothing</p>
<label>Search <input role=combobox aria-haspopup=listbox aria-expanded=false aria-label="What do you want to play" oninput="document.getElementById('q').textContent='typed:'+this.value"></label>
<p id=q>none</p>
<label>Pick <input list=dl aria-label="With suggestions"><datalist id=dl><option>Alpha</option><option>Beta</option></datalist></label>{rows}
<button onclick="this.textContent='PRESSED'">Bottom button</button><p>END OF THE PAGE</p></body></html>""")
res = {}


def check(name, ok, detail=""):
    res[name] = bool(ok); print(("OK   " if ok else "FAIL ") + name + (f" | {detail[:150]}" if detail else ""))


try:
    DESK.open(f'"{EDGE}" --guest --user-data-dir="{os.path.join(work, "profile")}" --no-first-run --force-renderer-accessibility "file:///{page}"')
    R = DESK.run; hw = None
    for _ in range(40):
        time.sleep(1)
        ws = [h for h, t, _ in R(lambda: DESK._enum()) if "pcc-web-test" in t]
        if ws:
            hw = ws[0]; break
    assert hw, "Edge did not show the page"
    time.sleep(2); q = str(hw)
    R(lambda: core.look(q))
    out = R(lambda: actions.read_text(q, None, 0, 40000))
    check("read gives the whole page, also what is out of view", "Long test page" in out and "END OF THE PAGE" in out and "\ufffc" not in out, out.split("\n")[0])
    out = R(lambda: actions.find(q, "Country|Bottom button"))
    check("find lists the drop-down and the button out of view", "combobox" in out and "out of view" in out, out.replace("\n", " / "))
    items = core._state[hw]["items"]
    sel = next(i for i, x in items.items() if x.kind == "combobox"); btn = next(i for i, x in items.items() if "Bottom" in x.name)
    out = R(lambda: core.type_text(q, "portugal", target=sel)); time.sleep(0.5)
    check("type on a <select> picks the option and its onchange fires", R(lambda: actions.find(q, "chosen:Portugal")).startswith("found"), out)
    # a search box with suggestions (Spotify's kind: role=combobox, a listbox pops up) and an <input list> take TEXT; only a <select>
    # (whose popup is a menu) is a choice among options
    srch = next(i for i, x in items.items() if x.kind == "combobox" and "play" in x.name.lower())
    out = R(lambda: core.type_text(q, "blue monday", target=srch)); time.sleep(0.5)
    check("a search combobox (listbox popup) takes the typed text, its input event fires",
          R(lambda: actions.find(q, "typed:blue monday")).startswith("found"), out)
    dl = next(i for i, x in items.items() if x.kind == "combobox" and "suggestions" in x.name.lower())
    out = R(lambda: core.type_text(q, "Gamma", target=dl)); time.sleep(0.3)
    check("an <input list> takes a value that is not one of its suggestions", "Gamma" in out or "read back" in out and "WARNING" not in out, out)
    btn = int(re.search(r"\[(\d+):Bottom", R(lambda: actions.find(q, "Bottom button"))).group(1))
    out = R(lambda: core.click(q, btn)); time.sleep(0.6)
    check("a button out of view is pressed", R(lambda: actions.find(q, "PRESSED")).startswith("found"), out)
    out = R(lambda: actions.scroll(q, None, "top"))
    check("scroll the page to the top", out.startswith("ok") and "-> 0%" in out, out)
    out = R(lambda: actions.scroll(q, None, "down", 2))
    check("scroll the page down", out.startswith("ok") and "0% ->" in out and "0% -> 0%" not in out, out)
finally:
    DESK.close()
    time.sleep(1); shutil.rmtree(work, ignore_errors=True)
good = len(res) == 8 and all(res.values())
print(f"web verification {'passed' if good else 'FAILED'} ({sum(res.values())}/{len(res)} checks)")
sys.exit(0 if good else 1)
