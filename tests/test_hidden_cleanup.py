"""The apps PC-Control starts on its hidden desktop must never outlive the session: they end when the server shuts down (EOF), when it
is killed or crashes (kill-on-close job), and the ones an older version left behind are reaped. Uses real processes on a real hidden
desktop (never the user's)."""
import ctypes, os, subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, ROOT)
from pc_control import hidden

PY = sys.executable
SLEEP = f'"{PY}" -c "import time; time.sleep(120)"'
c = {}


def child(code):
    """Run `code` in a fresh python with PC-Control importable; returns the Popen (stdout piped)."""
    return subprocess.Popen([PY, "-c", "import sys; sys.path.insert(0, r'%s'); %s" % (ROOT, code)], stdout=subprocess.PIPE, text=True,
                            creationflags=0x08000000)


def wait_dead(pid, secs=6.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        if not hidden._alive(pid):
            return True
        time.sleep(0.1)
    return False


launch = ("from pc_control import hidden; from pc_control.hidden import DESK; "
          "pid = DESK.launch(r'''%s'''); print(pid, flush=True); " % SLEEP)

# 1) orderly end of the session: server._shutdown() (it ends with os._exit, which skips atexit)
p = child(launch + "from pc_control import server; server._shutdown()")
sleeper = int(p.stdout.readline()); p.wait(timeout=20)
c["an orderly shutdown ends what ran on the hidden desktop"] = wait_dead(sleeper)

# 2) the server is killed (crash, taskkill, Claude Code force-closed): the job object kills it
p = child(launch + "import time; time.sleep(120)")
sleeper = int(p.stdout.readline()); alive_before = hidden._alive(sleeper)
p.kill(); p.wait(timeout=10)
c["the app was really running before the kill"] = alive_before
c["a killed server takes its hidden-desktop apps with it"] = wait_dead(sleeper)

# 3) leftovers of an older version: a desktop of a server that no longer exists, with an app that has a window
dead = subprocess.Popen([PY, "-c", "pass"], creationflags=0x08000000); dead.wait()
name = f"pc_control_{dead.pid}"
u, k = hidden._u, hidden._k
hd = u.CreateDesktopW(name, None, None, 0, 0x10000000, None)
win_code = ("import ctypes, time; u = ctypes.windll.user32; "
            "u.CreateWindowExW(0, 'STATIC', 'pc-control-orphan-test', 0x10000000, 0, 0, 10, 10, 0, 0, 0, 0); time.sleep(120)")
si = hidden._STARTUPINFOW(); si.cb = ctypes.sizeof(si); si.lpDesktop = name
pi = hidden._PROCESS_INFORMATION()
buf = ctypes.create_unicode_buffer(f'"{PY}" -c "{win_code}"')
ok = k.CreateProcessW(None, buf, None, None, False, 0x08000000, None, None, ctypes.byref(si), ctypes.byref(pi))
time.sleep(1.5)
try:
    c["test setup: an orphan app is alive on a dead server's desktop"] = bool(ok) and hidden._alive(pi.dwProcessId)
    ended = hidden.reap_orphans()
    c["the reaper ends the orphan app"] = pi.dwProcessId in ended and wait_dead(pi.dwProcessId)
    # a LIVE server's desktop must never be touched
    live = f"pc_control_{os.getpid()}"
    hl = u.CreateDesktopW(live, None, None, 0, 0x10000000, None)
    si2 = hidden._STARTUPINFOW(); si2.cb = ctypes.sizeof(si2); si2.lpDesktop = live
    pi2 = hidden._PROCESS_INFORMATION(); buf2 = ctypes.create_unicode_buffer(f'"{PY}" -c "{win_code}"')
    k.CreateProcessW(None, buf2, None, None, False, 0x08000000, None, None, ctypes.byref(si2), ctypes.byref(pi2)); time.sleep(1.5)
    hidden.reap_orphans()
    c["a desktop whose server is still alive is left alone"] = hidden._alive(pi2.dwProcessId)
    hidden._terminate(pi2.dwProcessId); u.CloseDesktop(hl)
finally:
    if pi.dwProcessId and hidden._alive(pi.dwProcessId):
        hidden._terminate(pi.dwProcessId)
    u.CloseDesktop(hd)
for kx, v in c.items():
    print(("OK   " if v else "FAIL ") + kx)
ok_all = len(c) == 6 and all(c.values())
print(f"hidden-cleanup verification {'passed' if ok_all else 'FAILED'}")
sys.exit(0 if ok_all else 1)