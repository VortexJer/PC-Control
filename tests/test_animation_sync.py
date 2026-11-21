"""G23: the click / typing happens when the animated cursor ARRIVES, not after a fixed guess.

Live overlay windows (no app is touched; the overlay never takes focus or input):
- pointer(): its arrival Event is set when the travel ends: about ms/speed after the call (never before 80 % of it, at most 60 ms late);
- PC_CONTROL_CURSOR_SPEED=2 halves the travel;
- a newer move releases the older waiter at once; with the cursor off nothing waits;
- the server's click and type paths wait for that Event (no fixed sleep before acting).
"""
import os, re, sys, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["PC_CONTROL_CURSOR"] = "always"
from pc_control import cursor

c = {}
C = cursor.ClaudeCursor()
x0, y0 = 400, 400


def travel(ms, speed):
    os.environ["PC_CONTROL_CURSOR_SPEED"] = str(speed)
    C.pointer(x0, y0, ms=1).wait(1)                              # start position (already visible)
    time.sleep(0.3)
    t = time.perf_counter(); ev = C.pointer(x0 + 300, y0 + 120, click=True, ms=ms)
    ok = ev.wait(3); return ok, (time.perf_counter() - t) * 1000


ok, dt = travel(300, 1)
print(f"   speed 1: 300 ms travel arrived after {dt:.0f} ms")
c["the arrival is signalled when the pointer gets there (240..360 ms for a 300 ms travel)"] = ok and 240 <= dt <= 360
ok, dt = travel(300, 2)
print(f"   speed 2: 300 ms travel arrived after {dt:.0f} ms")
c["PC_CONTROL_CURSOR_SPEED=2 halves it (120..210 ms)"] = ok and 120 <= dt <= 210
os.environ["PC_CONTROL_CURSOR_SPEED"] = "1"
first = C.pointer(100, 100, ms=2000); time.sleep(0.05)
second = C.pointer(200, 200, ms=50)
c["a newer move releases the older waiter at once"] = first.wait(0.3) and second.wait(1)
os.environ["PC_CONTROL_CURSOR"] = "off"
t = time.perf_counter(); ev = C.pointer(300, 300, ms=2000); ok = ev.wait(1)
c["with the cursor off, nothing waits for an animation"] = ok and (time.perf_counter() - t) < 0.3
C.hide()

src = open(os.path.join(ROOT, "pc_control", "server.py"), encoding="utf-8").read()
c["click waits for the pointer's arrival (no fixed sleep)"] = re.search(r"CURSOR\.pointer\([^\n]*click=True[^\n]*\)\.wait\(", src) is not None \
    and "click=True, ms=150, scale=cursor.scale_for(hwnd), owner=hwnd); time.sleep" not in src
c["typing waits for the bar's arrival"] = re.search(r"CURSOR\.caret\(x \+ 8[^\n]*\)\.wait\(", src) is not None

bad = [k for k, v in c.items() if not v]
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"animation sync verification passed ({len(c)} checks)" if not bad else f"animation sync verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
