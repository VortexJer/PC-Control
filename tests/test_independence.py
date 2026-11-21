"""G18 independence, LIVE with real apps: a simulated user types (with capitals, so Shift is held) and holds the mouse button in THEIR
Notepad++ (real app, foreground), while Claude, through the REAL MCP server, types, presses Enter, scrolls and reads in ANOTHER real
Notepad++. Checks: Claude's actions go out at once (no waiting for the user's typing), neither text gets a single character of the other,
the foreground never leaves the user's window.

Needs the real apps: python tools/realapps.py install notepadpp. The user of this PC should not type during the ~15 s run (the test
plays the user; real typing in between makes it inconclusive, and it says so)."""
import asyncio, ctypes, os, subprocess, sys, threading, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import win32api, win32con, win32gui, win32process
from pc_control import scintilla
import realapps
from mcp_batch import run_calls

c = {}
exe = realapps.load().get("notepadpp", {}).get("exe")
c["real Notepad++ installed (tools/realapps.py install notepadpp)"] = bool(exe and os.path.exists(exe))
u = ctypes.windll.user32


class KI(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("extra", ctypes.c_size_t)]
class MI(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("data", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("extra", ctypes.c_size_t)]
class UN(ctypes.Union):
    _fields_ = [("ki", KI), ("mi", MI), ("pad", ctypes.c_byte * 32)]
class INP(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", UN)]


def key(vk, up=False):
    i = INP(1); i.u.ki = KI(vk, 0, 2 if up else 0, 0, 0); u.SendInput(1, ctypes.byref(i), ctypes.sizeof(INP))


def button(up=False):
    i = INP(0); i.u.mi = MI(0, 0, 0, 4 if up else 2, 0, 0); u.SendInput(1, ctypes.byref(i), ctypes.sizeof(INP))


def user_types(text, stop):
    """The user's typing through the keyboard input path: capitals hold Shift for real."""
    for ch in text:
        if stop.is_set():
            return
        r = u.VkKeyScanW(ord(ch)); vk, shift = r & 0xFF, (r >> 8) & 1
        if shift: key(0x10)
        key(vk); time.sleep(0.02); key(vk, True)
        if shift: key(0x10, True)
        time.sleep(0.045)


def top_of(pid, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        hs = []
        win32gui.EnumWindows(lambda h, _: hs.append(h) if win32process.GetWindowThreadProcessId(h)[1] == pid and win32gui.IsWindowVisible(h)
                             and "Notepad++" in win32gui.GetWindowText(h) else None, None)
        if hs:
            return hs[0]
        time.sleep(0.1)


USER_TEXT = "El Usuario Escribe Esto Con Mayusculas Mientras Claude Trabaja Al Lado. "
CLAUDE = ["primera linea de claude", "segunda linea de claude", "tercera linea de claude"]
out = {}
if c["real Notepad++ installed (tools/realapps.py install notepadpp)"]:
    fg0, cur0 = win32gui.GetForegroundWindow(), win32api.GetCursorPos()
    user = subprocess.Popen([exe, "-multiInst", "-nosession", "-notabbar"])
    hu = top_of(user.pid)
    try:
        key(0x87); key(0x87, True); win32gui.SetForegroundWindow(hu); time.sleep(0.6)     # the test IS the user (F24: a key no app uses, unlike Alt)
        c["the user's Notepad++ is in front (the test plays the user)"] = win32gui.GetForegroundWindow() == hu
        ed_u = scintilla.editors(hu)[0]
        stop, fg_samples = threading.Event(), []
        def watch():
            while not stop.is_set():
                fg_samples.append(win32gui.GetForegroundWindow()); time.sleep(0.02)
        def user_work():
            user_types(USER_TEXT, stop)
            l, t, r, b = win32gui.GetWindowRect(ed_u); win32api.SetCursorPos(((l + r) // 2, (t + b) // 2))
            button(); time.sleep(1.5); button(True)                                     # the user holds the mouse in THEIR editor
            user_types(USER_TEXT, stop)
        th_w, th_u = threading.Thread(target=watch, daemon=True), threading.Thread(target=user_work, daemon=True)
        th_w.start(); th_u.start()
        calls = [["open_app", {"command": exe + " -multiInst -nosession -notabbar"}, {"save": "w"}], ["sleep", {"s": 1.0}],
                 ["look", {"window": "$w", "find": "editor"}]]
        for line in CLAUDE:
            calls += [["type", {"window": "$w", "text": line}], ["key", {"window": "$w", "name": "enter"}]]
        calls += [["scroll", {"window": "$w", "direction": "down"}], ["read", {"window": "$w"}]]
        t_start = time.time()
        recs = asyncio.run(run_calls(calls, env={"PC_CONTROL_CURSOR": "off"}))
        th_u.join(30); stop.set(); th_w.join(2)
        out["recs"] = recs
        hw = int(recs[0]["text"].split("|")[1].split()[0]) if "|" in recs[0]["text"] else 0
        acts = [r for r in recs if r["tool"] in ("type", "key", "scroll")]
        for r in recs:
            print(f"   {r['tool']:8} {r['secs']:5.2f}s  {r['text'][:150]!r}")
        c["every Claude action succeeded while the user worked"] = acts and all(not r["error"] and r["text"].startswith(("ok", "(typed"))
                                                                               for r in acts)
        c["no action waited for the user's typing (each under 1.5 s)"] = acts and max(r["secs"] for r in acts) < 1.5
        text_u = scintilla.get_text(ed_u) or ""
        claude_text = "".join(line + "\n" for line in CLAUDE)
        read = next((r["text"] for r in recs if r["tool"] == "read"), "")
        c["Claude's editor holds EXACTLY Claude's text (no user character)"] = all(l in read for l in CLAUDE) and not any(
            w in read for w in ("Usuario", "Mayusculas", "Escribe"))
        c["the user's editor holds EXACTLY the user's text (no Claude character)"] = text_u == USER_TEXT * 2 and "claude" not in text_u
        if text_u != USER_TEXT * 2:
            print("   user's editor:", repr(text_u[:200]))
        c["the foreground was the user's window the whole time"] = fg_samples and all(h == hu for h in fg_samples)
    finally:
        try:
            win32api.SetCursorPos(cur0)
        except Exception:
            pass
        user.kill()
        try:
            key(0x87); key(0x87, True); win32gui.SetForegroundWindow(fg0)
        except Exception:
            pass

bad = [k for k, v in c.items() if not v]
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"independence verification passed ({len(c)} checks)" if not bad else f"independence verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
