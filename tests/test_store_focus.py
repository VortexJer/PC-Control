"""G21, LIVE with real Store / packaged apps through the REAL MCP server: opening them never leaves the foreground with them.

Cases (each records the foreground every 2 ms):
- Calculator (calc.exe hands off to the Store frame): any steal is given back within 150 ms; open_app names the frame, not a helper.
- Photos (Store app through shell:AppsFolder): same.
- Settings already open in the background, opened again on another page (single-instance: it brings its EXISTING window up):
  the foreground is given back within 150 ms.
- The user's own switches (a click ON the window, Alt/Win) are never undone; an older interaction does not protect a later steal
  (simulated, so the test itself never brings a window in front of the user).
- No answer names a helper window (PopupHost) as the app.
Do not touch the PC during the ~40 s run: a click right when an app comes up counts as YOUR choice and makes that case inconclusive."""
import ctypes, os, subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import win32gui, win32process
from fg_timeline import record, describe

c = {}
u = ctypes.windll.user32


def steal_ms(fg0, events):
    """Longest time (ms) the foreground was held by a window that is not the user's (0 = never)."""
    worst, since = 0, None
    for t, h in events[1:]:
        if isinstance(h, str) and h.startswith("USER"):
            break                                                  # from the user's first click on, switches are theirs
        if not isinstance(h, int):
            continue
        if h and h != fg0 and since is None:
            since = t
        elif (h == fg0 or not h) and since is not None:
            worst = max(worst, (t - since) * 1000); since = None
    if since is not None:
        worst = 10 ** 6                                            # never given back
    return worst


def kill(name):
    subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)


def tap_f24():
    class KI(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("extra", ctypes.c_size_t)]
    class INP(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("ki", KI), ("pad", ctypes.c_byte * 8)]
    for up in (0, 2):
        i = INP(1, KI(0x87, 0, up, 0, 0)); u.SendInput(1, ctypes.byref(i), ctypes.sizeof(INP))


def run_case(name, calls):
    fg0, events, recs = record(calls, env={"PC_CONTROL_CURSOR": "off"})
    ms = steal_ms(fg0, events)
    user = any(isinstance(h, str) and h.startswith("USER") for _, h in events)
    print(f"   {name}: longest steal {ms:.0f} ms | {'INCONCLUSIVE: you clicked/pressed Alt during it, rerun' if user else 'no user input'} | {recs[0]['text'][:140]!r}")
    for t, h in events[1:]:
        print(f"      {t:7.3f}s {describe(h) if isinstance(h, int) else h}")
    return fg0, ms, user, recs


def topmost_user_windows():
    out = []
    win32gui.EnumWindows(lambda h, _: out.append(h) if win32gui.IsWindowVisible(h) and win32gui.GetWindowLong(h, -20) & 8
                         and win32gui.GetWindowText(h) else None, None)
    return set(out)


top0 = topmost_user_windows()
kill("CalculatorApp.exe")
fg0, ms, user, recs = run_case("calculator", [["open_app", {"command": "Calculadora"}], ["sleep", {"s": 2}]])
c["Calculator: the foreground is back with the user within 150 ms (or never taken)"] = ms <= 150
c["Calculator: open_app names the Calculator frame"] = "Calculadora" in recs[0]["text"] and "PopupHost" not in recs[0]["text"]
kill("CalculatorApp.exe")

fg0, ms, user, recs = run_case("photos", [["open_app", {"command": "Fotos"}], ["sleep", {"s": 3}]])
c["Photos: the foreground is back with the user within 150 ms (or never taken)"] = ms <= 150
c["Photos: no helper window named as the app"] = "PopupHost" not in recs[0]["text"]

run_case("settings first open", [["open_app", {"command": "ms-settings:about"}], ["sleep", {"s": 2}]])   # Settings open (guarded too)
fg0, ms, user, recs = run_case("settings reused", [["open_app", {"command": "ms-settings:sound"}], ["sleep", {"s": 2}]])
c["Settings already open: the foreground is back with the user within 150 ms (or never taken)"] = ms <= 150
c["Settings: no helper window named as the app"] = "PopupHost" not in recs[0]["text"]

# The user's own choices (simulated: a live version would have to bring a window to the front of the user's screen)
from pc_control import focus
g = focus.FocusGuard.__new__(focus.FocusGuard)
g.last_user_action = g.last_switch_key = 0.0; g.last_click_root = 0
now = time.monotonic()
g.last_user_action, g.last_click_root = now, 111
c["a click ON the window that came up a moment ago: it is the user's choice"] = g.user_recent(111)
c["a click on ANOTHER window at that moment: the window that came up is still a steal"] = not g.user_recent(222)
g.last_user_action = now - 5
c["the user used the app earlier and went elsewhere: the app coming up again is a steal"] = not g.user_recent(111)
g.last_switch_key = time.monotonic()
c["Alt/Win (the user's switcher) a moment ago: the switch is theirs"] = g.user_recent(222)

new_top = topmost_user_windows() - top0
c["no window of the user was left 'always on top' (seen once in a session; never again)"] = not new_top
if new_top:
    print("   became topmost:", [(h, win32gui.GetClassName(h)) for h in new_top])
bad = [k for k, v in c.items() if not v]
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"store focus verification passed ({len(c)} checks)" if not bad else f"store focus verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
