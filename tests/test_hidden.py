"""An app opened from scratch by pc_control lives on the hidden desktop: it is NEVER seen, not even for an instant.

Measures from the user's desktop every ~3 ms during the whole startup: no window of the app must exist
there, and focus must never land on a process of ours. Inside the hidden desktop: it is read (tree and capture)
and operated (click and typing) like any other window.
"""
import json, os, sys, tempfile, threading, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pc_control import core
from pc_control.hidden import DESK
import numpy as np
from PIL import Image
import win32gui, win32process

state = os.path.join(tempfile.gettempdir(), "pc-control_hidden_state.json")
S = lambda: json.load(open(state, encoding="utf-8-sig"))
OURS = set()
log = {"samples": 0, "on_user_desktop": 0, "activated": False}
stop = threading.Event()

def sampler():
    while not stop.is_set():
        fg = win32gui.GetForegroundWindow()
        if fg and win32process.GetWindowThreadProcessId(fg)[1] in OURS: log["activated"] = True
        n = []
        win32gui.EnumWindows(lambda h, _: n.append(h) if win32gui.GetWindowText(h) == "pc-control-test" else None, None)
        log["samples"] += 1; log["on_user_desktop"] += len(n)
        time.sleep(0.003)

th = threading.Thread(target=sampler, daemon=True); th.start()
cmd = f'powershell.exe -STA -NoProfile -ExecutionPolicy Bypass -File "{os.path.join(HERE, "test_app.ps1")}" "{state}"'
checks = {}
try:
    pid = DESK.launch(cmd); OURS.add(pid)
    hw = None
    for _ in range(100):
        hw = next((h for h, t, p in DESK.windows() if p == pid), None)
        if hw: break
        time.sleep(0.1)
    time.sleep(0.5)
    stop.set(); th.join()
    assert hw, "the window did not appear on the hidden desktop"
    checks["never on the user's desktop"] = log["on_user_desktop"] == 0
    checks["never activated focus"] = not log["activated"]
    print(f"samples from the user's desktop: {log['samples']} | times the window was there: {log['on_user_desktop']}")

    lk = DESK.run(lambda: core.look(str(hw)))
    checks["it can be read (tree)"] = lk["mode"] == "uia" and "Press" in (lk.get("text") or "")
    im = DESK.run(lambda: core.look(str(hw), "image"))
    checks["it can be read (non-empty capture)"] = bool(im.get("image")) and float(np.array(Image.open(im["image"])).std()) > 5

    DESK.run(lambda: core.look(str(hw)))                       # fresh ids (the previous capture does not register them)
    items = core._state[hw]["items"]; btn = next(i for i, x in items.items() if x.name == "Press"); edt = next(i for i, x in items.items() if "Name field" in x.name)
    DESK.run(lambda: core.click(str(hw), btn)); time.sleep(0.4)
    checks["click in the hidden app"] = S()["clicks"] == 1
    DESK.run(lambda: core.type_text(str(hw), "hello", target=edt)); time.sleep(0.4)
    checks["typing in the hidden app"] = S()["text"] == "hello"
    ch = DESK.run(lambda: core.changes(str(hw)))
    checks["only the change is sent"] = "Clicks: 1" in ch
    print("change after acting:", ch)
finally:
    stop.set()
    DESK.close()

for k, v in checks.items():
    print(("OK   " if v else "FAIL ") + k)
ok = all(checks.values())
print("hidden verification passed" if ok else "hidden verification FAILED")
sys.exit(0 if ok else 1)
