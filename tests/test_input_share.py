"""The user can work in the same app at the same time: a synthetic click does not break their drag and a key does not mix with their text.

No real app is touched: the reads of the mouse/keyboard state and the sending of messages are replaced with simulated ones.
"""
import os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pc_control import core

c = {}
sent = []
core.win32gui.PostMessage = lambda h, m, w, l: sent.append((m,))
core.win32gui.SendMessage = lambda h, m, w, l: sent.append(("send", m))
core.win32gui.GetClassName = lambda h: "NetUIHWND"                       # not a button: goes through mouse messages
core.win32gui.GetWindowRect = lambda h: (0, 0, 800, 600)
core._child_at = lambda h, x, y: (h, (x, y))
core._focus_hwnd = lambda h: h

# 1) the mouse is free: the synthetic click goes out
core._buttons_down = lambda: False
sent.clear(); how = core._post_click(1, 10, 10)
c["mouse free: the click goes out"] = how == "mouse" and len(sent) >= 3

# 2) the user is pressing (dragging) and releases soon: it WAITS and then goes out
seq = iter([True, True, True, False] + [False] * 50)
core._buttons_down = lambda: next(seq)
sent.clear(); t0 = time.time(); how = core._post_click(1, 10, 10); dt = time.time() - t0
c["the user drags and releases: it waits and then presses"] = how == "mouse" and len(sent) >= 3 and dt >= 0.05
# 3) and while they do not release, NOTHING is sent (their gesture is not broken)
core._buttons_down = lambda: True; core.MOUSE_WAIT = 0.3
sent.clear(); how = core._post_click(1, 10, 10)
c["the user does not release the mouse: no message is sent"] = how == "busy" and sent == []
msg = core.click("x", (10, 10)) if False else None
# 4) click() turns that into a clear message
core.find_window = lambda q: 1; core._state[1] = {"scale": 1.0, "rect": (0, 0, 800, 600), "items": {}}
core.win32gui.IsIconic = lambda h: False
sent.clear(); out = core.click("x", (10, 10))
c["click(): explains it did not press because the user is dragging"] = "would break your gesture" in out and sent == []
core.MOUSE_WAIT = 2.0
# 5) a NATIVE click (BM_CLICK to a button) does not use the user's mouse: it does not wait even if they are pressing
core.win32gui.GetClassName = lambda h: "Button"
sent.clear(); how = core._post_click(1, 10, 10)
c["a native button (BM_CLICK) does not depend on the user's mouse"] = how == "button" and sent == [("send", 0x00F5)]

# 6) keys: they do not mix with what the user is typing
idle = iter([40, 60, 120, 400, 900] + [900] * 50)
core._idle_ms = lambda: next(idle)
sent.clear(); t0 = time.time(); out = core.key("x", "enter"); dt = time.time() - t0
c["the user types: the key waits for them to stop and then goes out"] = out.startswith("ok") and len(sent) == 2 and dt >= 0.05
core._idle_ms = lambda: 50; core.KEY_QUIET_WAIT = 0.3
sent.clear(); out = core.key("x", "enter")
c["the user never stops typing: it does not intrude on their text and explains why"] = "you are typing" in out and sent == []
core.KEY_QUIET_WAIT = 3.0

# right click: opens a REAL menu, so Claude yields if the user is working or a menu is already open
rm_open, rm_quiet = core.menu_open, core.wait_user_quiet
core.menu_open = lambda: False; core.wait_user_quiet = lambda q=0.25, timeout=None: True
c["right click with the user idle and no menus: it opens"] = core.right_click_blocked() is None
core.wait_user_quiet = lambda q=0.25, timeout=None: False
r1 = core.right_click_blocked()
c["right click while the user is working: Claude yields and explains"] = bool(r1) and "while you work" in r1
core.wait_user_quiet = lambda q=0.25, timeout=None: True; core.menu_open = lambda: True
r2 = core.right_click_blocked()
c["right click with a menu already open (e.g. the user's): does not open another on top"] = bool(r2) and "a menu is already open" in r2
core.menu_open, core.wait_user_quiet = rm_open, rm_quiet
c["the idle time required to open a menu is several seconds"] = core.RIGHT_CLICK_QUIET >= 2.0

bad = [k for k, v in c.items() if not v]
for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
print(f"input-share verification passed ({len(c)} checks)" if not bad else f"input-share verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
