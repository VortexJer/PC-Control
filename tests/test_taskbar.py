"""Default mode of open_app: the app is born MINIMIZED (nothing on screen, no focus) and leaves its button in the taskbar.

While minimized it is read through its tree and operated through messages; to capture it, it would have to be restored OFF-SCREEN for
an instant and come back minimized at its original position (so that, when you press the button, it opens where it was).
"""
import json, os, subprocess, sys, tempfile, threading, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pc_control import core
import numpy as np
from PIL import Image
import uiautomation as auto
import win32gui, win32process

state = os.path.join(tempfile.gettempdir(), "pc-control_taskbar_state.json")
S = lambda: json.load(open(state, encoding="utf-8-sig"))
OURS = set()
log = {"samples": 0, "on_screen": 0, "activated": False, "by_user": 0, "foreign": set(), "events": []}
PHASE = ["start"]
stop = threading.Event()

import ctypes
class _LII(ctypes.Structure): _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
def user_idle_ms():
    """Milliseconds since the last REAL input (keyboard/mouse). The messages PC-Control sends do not count."""
    i = _LII(); i.cbSize = ctypes.sizeof(i); ctypes.windll.user32.GetLastInputInfo(ctypes.byref(i))
    return (ctypes.windll.kernel32.GetTickCount() - i.dwTime) & 0xFFFFFFFF

def sampler():
    while not stop.is_set():
        fg = win32gui.GetForegroundWindow()
        for h0 in core.top_windows():                      # "pc-control-test" windows that are NOT from this test (leftovers from another)
            if win32gui.GetWindowText(h0) == "pc-control-test" and win32process.GetWindowThreadProcessId(h0)[1] not in OURS and OURS:
                log["foreign"].add((win32process.GetWindowThreadProcessId(h0)[1], win32gui.GetClassName(h0), bool(win32gui.IsIconic(h0)), bool(win32gui.IsWindowVisible(h0))))
        if fg and (win32gui.GetWindowText(fg) == "pc-control-test" or win32process.GetWindowThreadProcessId(fg)[1] in OURS):
            if user_idle_ms() < 1500: log["by_user"] += 1          # the user activated it themselves (e.g. pressed the "Windows PowerShell" taskbar button): not PC-Control's fault
            else:
                log["activated"] = True
                if len(log["events"]) < 6: log["events"].append(("focus", PHASE[0], round(time.time() - T0, 2), win32gui.GetClassName(fg), win32gui.IsIconic(fg)))
        for h in core.top_windows():
            if win32gui.GetWindowText(h) == "pc-control-test":
                log["samples"] += 1
                if not win32gui.IsIconic(h) and not core.is_hidden(h) and user_idle_ms() >= 1500:
                    log["on_screen"] += 1
                    if len(log["events"]) < 6 and not any(e[0] == "visible" for e in log["events"]): log["events"].append(("visible", PHASE[0], round(time.time() - T0, 2), win32gui.IsWindowVisible(h), win32gui.GetWindowPlacement(h)[1]))
        time.sleep(0.003)

import ctypes.wintypes as _W
_ATTEMPTS = []                                              # (t, phase, hwnd, pid, title, real_fg): every ACTIVATION of a window
_WEPROC = ctypes.WINFUNCTYPE(None, _W.HANDLE, _W.DWORD, _W.HWND, _W.LONG, _W.LONG, _W.DWORD, _W.DWORD)
@_WEPROC
def _on_fg(hook, ev, h, obj, child, tid, t):
    """EVENT_SYSTEM_FOREGROUND also fires when the foreground lock STOPS the theft (the window is activated on its own thread).
    That makes the test deterministic: it does not depend on the lock letting the attempt through that day (1 in ~25 runs)."""
    if h and obj == 0:
        try: _ATTEMPTS.append((round(time.time() - T0, 3), PHASE[0], h, win32process.GetWindowThreadProcessId(h)[1], win32gui.GetWindowText(h), win32gui.GetForegroundWindow() == h, user_idle_ms()))
        except Exception: pass
def fg_hook():
    u = ctypes.windll.user32; hk = u.SetWinEventHook(3, 3, 0, _on_fg, 0, 0, 0x0002)   # EVENT_SYSTEM_FOREGROUND, WINEVENT_OUTOFCONTEXT
    hook_tid[0] = ctypes.windll.kernel32.GetCurrentThreadId(); hook_ready.set(); msg = _W.MSG()
    while u.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0: u.DispatchMessageW(ctypes.byref(msg))
    u.UnhookWinEvent(hk)
hook_tid, hook_ready = [0], threading.Event()

def taskbar_buttons():
    """Names of the app buttons in the taskbar (UIA). The taskbar groups by program, not by window title."""
    h = win32gui.FindWindow("Shell_TrayWnd", None); out = set()
    stack = [(auto.ControlFromHandle(h), 0)] if h else []
    while stack:
        c, d = stack.pop()
        try:
            if "TaskListButton" in (c.ClassName or "") and c.Name: out.add(c.Name)
            if d < 14: stack.extend((ch, d + 1) for ch in c.GetChildren())
        except Exception: pass
    return out

T0 = time.time()
buttons_before = taskbar_buttons()
th = threading.Thread(target=sampler, daemon=True); th.start()
threading.Thread(target=fg_hook, daemon=True).start(); hook_ready.wait(2)
cmd = (f'powershell.exe -STA -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{os.path.join(HERE, "test_app.ps1")}" "{state}" -Taskbar')
checks = {}; r = None
try:
    PHASE[0] = 'open'; r = core.open_minimized(cmd, creationflags=0x08000000); OURS.add(r["pid"])
    hw = r["windows"][0]["hwnd"] if r["windows"] else None
    assert hw, f"the window did not appear: {r}"
    PHASE[0] = 'wait'; time.sleep(0.8)
    rc0 = win32gui.GetWindowPlacement(hw)[4]
    checks["born minimized (no window on screen)"] = r["minimizada_de_origen"] and win32gui.IsIconic(hw)
    time.sleep(1.0); new_buttons = taskbar_buttons() - buttons_before
    print("new buttons in the taskbar:", sorted(new_buttons))
    checks["its button appears in the taskbar"] = bool(new_buttons)

    PHASE[0] = 'look'; lk = core.look(str(hw)); items = core._state[hw]["items"]
    checks["it is read while minimized through the tree (no capture)"] = lk["mode"] == "uia" and lk.get("minimized") and "Press" in lk["text"]
    print(f"look minimized: {lk['mode']} {lk['tokens']} tok | {lk['why']}")
    by = lambda s: next(i for i, x in items.items() if s in x.name)
    btn, edt = by("Press"), by("Name field")
    PHASE[0] = 'click'; core.click(str(hw), btn); time.sleep(0.4)
    checks["click while minimized"] = S()["clicks"] == 1
    PHASE[0] = 'type'; core.type_text(str(hw), "hello", target=edt); time.sleep(0.4)
    checks["typing while minimized"] = S()["text"] == "hello"
    ch = core.changes(str(hw)); print("change:", ch)
    checks["only the change is sent"] = "Clicks: 1" in ch

    # a minimized window is NOT captured (it would have to be shown): it warns instead of flickering
    PHASE[0] = 'image'; im = core.look(str(hw), "image")
    checks["a minimized window is not captured: it warns"] = im["mode"] == "none" and "hidden=true" in im["why"] and bool(win32gui.IsIconic(hw))
    checks["still minimized at its original place"] = bool(win32gui.IsIconic(hw)) and tuple(win32gui.GetWindowPlacement(hw)[4]) == tuple(rc0)
finally:
    PHASE[0] = 'end'; stop.set(); th.join()
    if r: subprocess.run(["taskkill", "/PID", str(r["pid"]), "/T", "/F"], capture_output=True, creationflags=0x08000000)
    time.sleep(0.05)
    if hook_tid[0]: ctypes.windll.user32.PostThreadMessageW(hook_tid[0], 0x0012, 0, 0)   # WM_QUIT to the hook's thread
_mine = [a for a in _ATTEMPTS if a[4] == "pc-control-test" or a[3] in OURS]
attempts = [a for a in _mine if a[6] >= 1500]                       # no recent user input: it was certainly not them
ambiguous = [a for a in _mine if a[6] < 1500]                       # with the user typing/clicking at that moment: inconclusive
if ambiguous: print(f"WARNING: {len(ambiguous)} activation(s) of the test window coincided with input from you (they do not count as a failure): {[(a[1], a[6]) for a in ambiguous[:3]]}")
checks["never seen on screen"] = log["on_screen"] == 0
checks["never activated focus"] = not log["activated"]
checks["did not even try to activate (even if the foreground lock stopped it)"] = not attempts
print(f"samples: {log['samples']} | times visible on screen: {log['on_screen']} | activations caused by user input (do not count): {log['by_user']} | foreign windows: {sorted(log['foreign'])} | events: {log['events']}")
diag = f"  [events={log['events']} attempts={[(t, ph, real) for t, ph, _h, _p, _ti, real, _i in attempts[:4]]}]"   # on the SAME line: gate-check trims the output with '...'
for k, v in checks.items(): print(("OK   " if v else "FAIL ") + k + ("" if v else diag))
ok = all(checks.values())
print("taskbar verification passed" if ok else "taskbar verification FAILED")
sys.exit(0 if ok else 1)
