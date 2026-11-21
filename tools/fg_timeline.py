"""Record who holds the foreground while PC-Control runs a batch of calls (does an app steal the focus while opening?).

  python tools/fg_timeline.py calls.json
Prints every foreground change with time, hwnd, process and title, and whether the user's window got it back.
"""
import asyncio, json, os, sys, threading, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import win32gui, win32process
from mcp_batch import run_calls
from pc_control.core import psutil_name


def describe(h):
    try:
        return f"{h} {psutil_name(win32process.GetWindowThreadProcessId(h)[1])} «{win32gui.GetWindowText(h)[:40]}»"
    except Exception:
        return str(h)


def record(calls, env=None):
    fg0 = win32gui.GetForegroundWindow()
    events, stop, t0 = [(0.0, fg0)], threading.Event(), time.time()
    import ctypes
    u = ctypes.windll.user32
    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    def tick():
        li = LII(); li.cbSize = 8; u.GetLastInputInfo(ctypes.byref(li)); return li.dwTime
    tick0 = tick()
    def watch():
        last, clicked = fg0, False
        while not stop.is_set():
            h = win32gui.GetForegroundWindow()
            if any(u.GetAsyncKeyState(v) & 0x8000 for v in (1, 2, 4, 0x12, 0x5B)) and not clicked:
                events.append((time.time() - t0, "USER clicked / Alt / Win")); clicked = True
            if h != last:
                events.append((time.time() - t0, h)); last = h
            time.sleep(0.002)
    th = threading.Thread(target=watch, daemon=True); th.start()
    recs = asyncio.run(run_calls(calls, env=env))
    time.sleep(1.0); stop.set(); th.join(1)
    events.append((time.time() - t0, "user input during the run: " + ("YES" if tick() != tick0 else "no")))
    return fg0, events, recs


if __name__ == "__main__":
    calls = json.load(open(sys.argv[1], encoding="utf-8"))
    fg0, events, recs = record(calls, env={"PC_CONTROL_CURSOR": "off"})
    for r in recs:
        print(f"--- {r['tool']} [{r['secs']}s] {r['text'][:200]!r}")
    print("foreground timeline:")
    for t, h in events:
        print(f"  {t:7.3f}s  {describe(h) if isinstance(h, int) else h}")
    stolen = [h for _, h in events[1:] if isinstance(h, int) and h and h != fg0]
    print("STOLEN by:", sorted({describe(h) for h in stolen}) if stolen else "nobody", "| back to user at end:", win32gui.GetForegroundWindow() == fg0)
