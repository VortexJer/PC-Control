"""Claude's orange cursor (pointer + bar + banner): always visible, at rest, isolated to its app, without taking focus.

For a few seconds an orange pointer and bar (and a banner) really appear on the screen.
"""
import os, sys, threading, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from pc_control import cursor
import win32api, win32con, win32gui, win32process

c = {}
# ---------- 1) pure rules ----------
for args, want, d in [
    (("always", False, True, False), True, "always: visible even if the user is in another app"),
    (("user", False, True, True), True, "user: the user is on the window"),
    (("user", False, True, False), False, "user: the user is on another window"),
    (("always", True, True, True), False, "minimized: nothing to show"),
    (("always", False, False, True), False, "off-screen or hidden"),
    (("off", False, True, True), False, "disabled")]:
    c[f"rule: {d}"] = cursor.should_show(*args) == want
for val, want in (("", "always"), ("always", "always"), ("USER", "user"), ("off", "off"), ("zzz", "always")):
    if val: os.environ["PC_CONTROL_CURSOR"] = val
    else: os.environ.pop("PC_CONTROL_CURSOR", None)
    c[f"mode '{val or '(unset)'}' -> {want}"] = cursor.mode() == want
os.environ.pop("PC_CONTROL_CURSOR", None)
for args, want, d in [
    (("always", True, False, True, False), False, "always: stays even with the user in another app"),
    (("user", True, False, True, False), True, "user: the user left -> it hides"),
    (("always", True, True, True, True), True, "minimized -> it hides (comes back on restore)"),
    (("always", False, False, False, False), True, "closed -> it hides"),
    (("always", True, False, False, True), True, "hidden -> it hides")]:
    c[f"isolated: {d}"] = cursor.should_hide(*args) == want

# sizes: larger than before, adapted to the text line and the DPI
c["pointer: larger than before (0.78) and no bigger than a large Windows pointer"] = 0.78 < cursor.ARROW_K <= 1.1 and max(y for _, y in cursor.ARROW) * cursor.ARROW_K <= 24
c["bar: huge page (Word) -> a normal 22 px line"] = cursor.caret_height(900, 1.0) == 22
c["bar: one-line field -> ~70% of it"] = cursor.caret_height(24, 1.0) == 17 and cursor.caret_height(40, 1.0) == 28
c["bar: never giant or tiny"] = 15 <= cursor.caret_height(1, 1.0) <= 40 and cursor.caret_height(100000, 1.0) <= 40
c["bar: scales with the DPI"] = cursor.caret_height(900, 1.5) > cursor.caret_height(900, 1.0) and cursor.caret_height(900, 2.0) > cursor.caret_height(900, 1.5)
c["system scale between 0.75 and 3"] = 0.75 <= cursor.scale_for(0) <= 3.0

# colour that adapts to the background
pal = {name: cursor.palette_for(bg) for name, bg in (("dark", (20, 20, 25)), ("medium", (120, 120, 130)), ("light", (245, 245, 245)), ("orange", (250, 130, 30)), ("unknown", None))}
c["colour: dark background -> light edge"] = pal["dark"][1] == cursor.WHITE
c["colour: light background -> dark edge (visible on white)"] = pal["light"][1] == cursor.DARK and pal["light"][0] != pal["dark"][0]
c["colour: orange background -> changes hue so it does not get lost"] = pal["orange"][0] != cursor.ORANGE and pal["orange"][0][2] > 200
c["colour: no data -> the usual orange"] = pal["unknown"] == (cursor.ORANGE, cursor.WHITE)
c["the drawing really changes with the colour"] = cursor.render_arrow(1.0, 0, pal["light"]) != cursor.render_arrow(1.0, 0, pal["dark"])
c["the bar is steady: always the same drawing (does not blink)"] = cursor.render_caret(22, 1.0) == cursor.render_caret(22, 1.0)

# everything fits in its canvas at any system scale (100% to 300%), with no clipping
for s in (0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0):
    bad_clip = []
    for name, data in (("pointer+ring", cursor.render_arrow(s, 6)), ("pointer", cursor.render_arrow(s, 0)),
                       ("bar", cursor.render_caret(cursor.caret_height(900, s), s)), ("field bar", cursor.render_caret(cursor.caret_height(30, s), s))):
        a = np.frombuffer(data, dtype=np.uint8).reshape(cursor.SIZE, cursor.SIZE, 4)[..., 3]
        if a[0].any() or a[-1].any() or a[:, 0].any() or a[:, -1].any(): bad_clip.append(name)
    c[f"no clipping at scale {int(s * 100)}%"] = not bad_clip
msg = "If you touch this application, Claude will not be able to act on it. Put it back as it was (1928x1168) and do not touch it any more."
sizes = {}
for s in (1.0, 1.5, 2.0):
    data, nw, nh = cursor.render_note(msg, s); sizes[s] = (nw, nh)
    a = np.frombuffer(data, dtype=np.uint8).reshape(nh, nw, 4)[..., 3]
    c[f"banner at scale {int(s * 100)}%: consistent and with content"] = len(data) == nw * nh * 4 and a.max() >= 240 and a[0, 0] <= 20
c["banner: grows with the scale and wraps the text into lines"] = sizes[1.5][1] > sizes[1.0][1] and sizes[1.0][0] <= 520 and sizes[1.0][1] >= 40

# ---------- 2) the real windows ----------
activated = []
stop = threading.Event()
def sampler():
    while not stop.is_set():
        fg = win32gui.GetForegroundWindow()
        if fg and win32process.GetWindowThreadProcessId(fg)[1] == os.getpid(): activated.append(win32gui.GetClassName(fg))
        time.sleep(0.004)
threading.Thread(target=sampler, daemon=True).start()

wc = win32gui.WNDCLASS(); wc.lpszClassName = "PC-Control-TestOwner"; wc.hInstance = win32api.GetModuleHandle(None)
wc.lpfnWndProc = lambda hh, m, w, l: win32gui.DefWindowProc(hh, m, w, l)
try: win32gui.RegisterClass(wc)
except Exception: pass
def new_owner(): return win32gui.CreateWindowEx(0x08000080, "PC-Control-TestOwner", "pc-control-owner", 0x90000000, -4000, -4000, 200, 120, 0, 0, wc.hInstance, None)
def wait(sec):
    t_end = time.time() + sec
    while time.time() < t_end:
        win32gui.PumpWaitingMessages(); time.sleep(0.01)
H = lambda k: cursor.CURSOR.hwnds[k]
def vis(k): return bool(win32gui.IsWindowVisible(H(k)))
def rect(k): return win32gui.GetWindowRect(H(k))

owner = new_owner()
try:
    os.environ["PC_CONTROL_CURSOR"] = "always"
    cursor.CURSOR.pointer(420, 320, click=True, ms=300, scale=1.0, owner=owner)
    cursor.CURSOR.caret(640, 320, h=cursor.caret_height(900, 1.0), scale=1.0, owner=owner)
    wait(0.5)
    c["the pointer does NOT vanish at once when the bar arrives: it stays a while (typing is fast)"] = vis("pointer") and vis("caret")
    wait(2.2)
    c["only ONE is visible at a time: the one from the last action (the bar)"] = vis("caret") and not vis("pointer")
    for k in ("pointer", "caret"):
        ex = win32gui.GetWindowLong(H(k), win32con.GWL_EXSTYLE)
        c[f"{k}: click-through, no focus, no taskbar button, transparent, NOT topmost"] = bool(ex & 0x20) and bool(ex & 0x08000000) and bool(ex & 0x80) and bool(ex & 0x80000) and not (ex & 0x8)
        c[f"{k}: owned by the window it acts on (isolated)"] = win32gui.GetWindow(H(k), 4) == owner       # GW_OWNER
    r = rect("caret"); c["the bar is where it was asked to be"] = abs(r[0] + cursor.HOT - 640) <= 2 and abs(r[1] + cursor.HOT - 320) <= 2
    c["clicks go through (the pointer does not receive the mouse)"] = win32gui.WindowFromPoint((420 + 8, 320 + 8)) != H("pointer")

    # AT REST: still visible long after the action ends (before, they left after ~1.3 s)
    wait(3.5)
    c["AT REST: the indicator is still visible after 4 s without acting"] = vis("caret")
    cursor.CURSOR.caret(640, 320, h=22, scale=1.0, owner=owner); wait(0.2)                        # the bar has just been used...
    cursor.CURSOR.pointer(420, 320, click=False, ms=200, scale=1.0, owner=owner); wait(0.5)       # ...and a click arrives: it does not vanish at once
    c["the bar does not vanish at once when the pointer arrives either"] = vis("caret") and vis("pointer")
    wait(2.2)
    c["a click afterwards: the pointer shows and the bar disappears"] = vis("pointer") and not vis("caret")
    r = rect("pointer"); c["at rest the pointer stays still at its last position"] = abs(r[0] + cursor.HOT - 420) <= 2
    # FOLLOWS ITS WINDOW: if the app moves, the cursor goes with it (position relative to the app, not to the screen)
    r0 = rect("pointer"); o0 = win32gui.GetWindowRect(owner)
    win32gui.SetWindowPos(owner, 0, o0[0] + 137, o0[1] + 61, 0, 0, 0x0001 | 0x0004 | 0x0010); wait(0.5)
    r1 = rect("pointer")
    c["the window moves: the pointer goes with it (same offset)"] = (r1[0] - r0[0], r1[1] - r0[1]) == (137, 61)
    win32gui.SetWindowPos(owner, 0, o0[0], o0[1], 0, 0, 0x0001 | 0x0004 | 0x0010); wait(0.4)
    # the bar slides to another position and stays at rest there
    cursor.CURSOR.caret(700, 360, h=22, scale=1.0, owner=owner); wait(2.6)
    r = rect("caret"); c["the bar moves to its new position and stays there"] = abs(r[0] + cursor.HOT - 700) <= 2 and abs(r[1] + cursor.HOT - 360) <= 2 and vis("caret") and not vis("pointer")

    # hiding/minimizing the app ONLY hides them; when it comes back they reappear where they were
    win32gui.ShowWindow(owner, 0); wait(0.8)
    c["app hidden/minimized: pointer and bar hide"] = not vis("pointer") and not vis("caret")
    win32gui.ShowWindow(owner, 8); wait(0.8)                                       # SW_SHOWNA: comes back without activating
    c["when the app comes back, it REAPPEARS where it was"] = vis("caret") and abs(rect("caret")[0] + cursor.HOT - 700) <= 2

    # CLOSING the app makes them disappear completely (and they do not come back)
    win32gui.DestroyWindow(owner); wait(1.0)
    c["app closed: they disappear"] = not vis("pointer") and not vis("caret")
    wait(1.2)
    c["app closed: they do not reappear"] = not vis("pointer") and not vis("caret")

    # CONTINUOUS visibility in user mode: the user enters and leaves (simulated without activating any window)
    owner = new_owner()
    presence = {"on": False}
    real, cursor.user_is_on = cursor.user_is_on, (lambda o: presence["on"])
    os.environ["PC_CONTROL_CURSOR"] = "user"
    try:
        cursor.CURSOR.pointer(420, 320, click=False, ms=200, owner=owner); cursor.CURSOR.caret(640, 320, h=22, owner=owner); wait(0.6)
        c["user: the action starts and the user is NOT there -> not visible"] = not vis("pointer") and not vis("caret")
        presence["on"] = True; wait(0.6)
        c["user: the user enters AFTER it started -> it appears"] = vis("caret")
        presence["on"] = False; wait(0.6)
        c["user: the user goes to another app -> it disappears"] = not vis("pointer") and not vis("caret")
        presence["on"] = True; wait(0.6)
        c["user: comes back -> it reappears"] = vis("caret")
    finally:
        cursor.user_is_on = real
    os.environ["PC_CONTROL_CURSOR"] = "always"

    # the banner: appears centred, lasts as long as requested and goes away; isolated the same way
    cursor.CURSOR.note("If you touch this application, Claude will not be able to act on it.", 700, 400, hold=1.6, scale=1.0, owner=owner); wait(0.7)
    r = rect("note"); c["banner: visible, banner-sized and centred where requested"] = vis("note") and (r[2] - r[0]) > 150 and abs((r[0] + r[2]) // 2 - 700) <= 3 and r[1] == 400
    c["banner: owned by the app (isolated)"] = win32gui.GetWindow(H("note"), 4) == owner
    wait(1.6)
    c["banner: goes away by itself when its time is up"] = not vis("note")
    c["... but the indicator stays at rest"] = vis("caret")
    cursor.CURSOR.hide(); wait(0.3)
    c["hide(): removes everything instantly"] = not vis("pointer") and not vis("caret") and not vis("note")
finally:
    os.environ.pop("PC_CONTROL_CURSOR", None)
    try: win32gui.DestroyWindow(owner)
    except Exception: pass
stop.set()
c["NO test window was ever activated (neither the cursor's nor the helper ones)"] = not activated
if activated: print("windows that took focus:", sorted(set(activated)))

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values())
print("cursor verification passed" if ok else "cursor verification FAILED")
sys.exit(0 if ok else 1)
