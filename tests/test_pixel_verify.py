"""Pixel verification: on surfaces with no UI tree (canvas, Tk, OCR, Java without bridge, remote screens) the answer of an action says
whether the screen really changed, and WHERE. A click that changed nothing is never reported as done.

1) the pure rule on synthetic pictures (caret blink and stray pixels are not changes; inside/outside the element)
2) live: a WinForms canvas whose drawing is not in the UI tree: a click that draws is SEEN at the element, a click on empty space is
   reported as NO VISIBLE EFFECT; a Tk app, which takes its button state from the real mouse and ignores message clicks, must be
   reported as NO VISIBLE EFFECT, never "ok". Each app's own file confirms what really happened. Focus and mouse
   are checked to stay the user's.
"""
import os, sys, time, tempfile
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["PC_CONTROL_MODE"] = "bypass"          # the test app runs in pythonw, which the policy protects (it can run code): not what is tested here
os.environ["PC_CONTROL_CURSOR"] = "off"
import numpy as np
import win32api, win32gui
from pc_control import core, server

c = {}
# ---- 1) the rule ----
a = np.full((300, 400), 200, np.uint8)
snapA = (a, (0, 0, 400, 300))
same = core.pixel_change(snapA, (a.copy(), (0, 0, 400, 300)), (10, 10, 50, 50))
c["identical pictures: no change"] = same == ""
b = a.copy(); b[100:118, 50:52] = 0
c["a blinking caret (thin vertical line) is not a change"] = core.pixel_change(snapA, (b, (0, 0, 400, 300)), (10, 10, 50, 50)) == ""
b = a.copy(); b[5, 5] = 0; b[200, 300] = 0
c["a few stray pixels are not a change"] = core.pixel_change(snapA, (b, (0, 0, 400, 300))) == ""
b = a.copy(); b[20:60, 20:60] = 0
r = core.pixel_change(snapA, (b, (0, 0, 400, 300)), (10, 10, 50, 50))
c["a change at the element is located there"] = r.startswith("pixels changed at the element")
r = core.pixel_change(snapA, (b, (0, 0, 400, 300)), (300, 200, 350, 250))
c["a change far from the element says so"] = "elsewhere" in r
c["no capture: not measurable (None), never 'no change'"] = core.pixel_change(None, snapA) is None

# ---- 2) live, through the real tool path ----
import re
tmp = tempfile.mkdtemp()
fg0, cur0, tick0 = win32gui.GetForegroundWindow(), win32api.GetCursorPos(), core._input_tick()


def count(path):
    try:
        return open(path, encoding="utf-8-sig").read().strip().split("\n")[0].strip()
    except OSError:
        return "0"


# a) a canvas whose drawing is NOT in the UI tree (WinForms): a click that draws must be SEEN, at the element
out_c = os.path.join(tmp, "canvas.txt"); title_c = f"PCC Canvas {os.getpid()}"
ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
r = core.open_behind(f'"{ps}" -STA -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{os.path.join(ROOT, "tests", "pixel_canvas_app.ps1")}" "{out_c}" "{title_c}"', "powershell")
hw = None
for _ in range(40):
    hw = next((h for h in core.top_windows() if win32gui.GetWindowText(h) == title_c), None)
    if hw:
        break
    time.sleep(0.25)
c["the canvas app opened behind"] = hw is not None
try:
    time.sleep(0.8)
    server.RAW["look"](str(hw))
    res = server.RAW["click"](str(hw), "120,140")                    # inside the canvas (window pixels of the last look)
    time.sleep(0.4)
    c["canvas: the click really drew (the app's own count)"] = count(out_c) == "1"
    c["canvas: the answer says the effect was SEEN on screen, where it clicked"] = "effect observed on screen" in res and "NO VISIBLE EFFECT" not in res
    print("   canvas click ->", res[:220])
    wr = win32gui.GetWindowRect(hw)
    k = core._state.get(hw, {}).get("scale", 1.0) or 1.0
    empty = f"{int((wr[2] - wr[0]) * 0.92 / k)},{int((wr[3] - wr[1]) * 0.88 / k)}"     # bottom-right of the form, away from the canvas, at any DPI
    res2 = server.RAW["click"](str(hw), empty)                       # empty form area: nothing happens
    time.sleep(0.4)
    c["canvas: a click on empty space did nothing in the app"] = count(out_c) == "1"
    c["canvas: ... and is NOT reported as done (NO VISIBLE EFFECT)"] = "NO VISIBLE EFFECT" in res2
    print("   empty click  ->", res2[:220])
finally:
    if hw and win32gui.IsWindow(hw):
        win32gui.PostMessage(hw, 0x0010, 0, 0)

# b) a Tk app: Tk takes the button state from the REAL mouse, so a message click cannot press it. The answer must say so, never "ok".
out_t = os.path.join(tmp, "tk.txt"); title_t = f"PCC Pixel {os.getpid()}"
pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
r = core.open_behind(f'"{pyw}" "{os.path.join(ROOT, "tests", "pixel_test_app.py")}" "{out_t}" "{title_t}"', "pythonw")
hwnd = next((w["hwnd"] for w in r["windows"] if w["title"] == title_t), None)
c["the Tk app opened behind, without the focus"] = hwnd is not None and r["foco_intacto"]
try:
    time.sleep(0.8)
    found = server.RAW["look"](str(hwnd), find="Pintar")               # what the model does to aim at a label: find gives it an id
    m = re.search(r"\[(\d+):Pintar", found)
    c["Tk: find locates the button by its text (OCR)"] = m is not None
    res = server.RAW["click"](str(hwnd), m.group(1) if m else "0")
    time.sleep(0.4)
    print("   Tk click     ->", res[:220])
    pressed = count(out_t) not in ("0", "")
    c["Tk: the answer matches reality (pressed -> effect seen; ignored -> NO VISIBLE EFFECT), never a false 'ok'"] = (
        ("effect observed" in res) if pressed else ("NO VISIBLE EFFECT" in res))
finally:
    if hwnd and win32gui.IsWindow(hwnd):
        win32gui.PostMessage(hwnd, 0x0010, 0, 0)
user = core._input_tick() != tick0
c["the user's foreground window stayed (or the user changed it themselves)"] = win32gui.GetForegroundWindow() == fg0 or user
c["the user's mouse never moved (or the user moved it)"] = win32api.GetCursorPos() == cur0 or user

bad = [k for k, v in c.items() if not v]
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"pixel verification passed ({len(c)} checks)" if not bad else f"pixel verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
