"""A call stuck inside another program (Firefox on a huge page, a web mail client) must not freeze the server: it is cut with a clear message and
the NEXT call answers at once. Before, every later call waited behind the stuck one for minutes."""
import os, sys, threading, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from pc_control import core, server
c = {}
server.CALL_TIMEOUT = 1.5                                       # 45 s in real use
release = threading.Event()
old_main = server.MAIN
core._state[999999] = {"items": {1: object()}}                  # an element table whose UI objects belong to the thread that is about to be abandoned
t0 = time.time(); msg = ""
try:
    server._on("no such window", lambda hidden: release.wait(30))      # stands for a UI Automation call that never comes back
except server.ToolError as e:
    msg = str(e)
dt = time.time() - t0
c["a stuck call is cut after the deadline, not after minutes"] = 1.2 < dt < 5
c["the message says what happened and what to try instead (image mode, find, a smaller view)"] = "timed out" in msg and "mode=\"image\"" in msg and "ids were reset" in msg
c["the stuck worker was replaced by a fresh one"] = server.MAIN is not old_main
c["the element table was cleared (its objects belonged to the abandoned thread)"] = 999999 not in core._state
t1 = time.time(); r = server._on("no such window", lambda hidden: "alive"); dt2 = time.time() - t1
c["the NEXT call answers at once instead of waiting behind the stuck one"] = r == "alive" and dt2 < 1.0
release.set()
real = server.MAIN.submit(lambda: __import__("threading").current_thread().name).result(timeout=5)
c["the new worker is a normal UI thread"] = real.startswith("pc-control-main")
c["every question to another program is limited (not the 20 s default)"] = server._uia_limited == [True] and server.UIA_CALL_MS <= 5000
for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values())
print("hang verification passed" if ok else "hang verification FAILED")
sys.exit(0 if ok else 1)