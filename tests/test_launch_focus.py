"""Launch focus hole (found 2026-11-20 with Java): an app that calls SetForegroundWindow while starting first EMPTIES the foreground and
then activates itself. The guard used to allow that second step ("no foreground window" counted as permission) and the app got the
user's keyboard. Now the empty foreground is not permission, and the user's window gets the foreground back while it is empty.

Measured live, polling the foreground every millisecond while the Swing test app starts (behind and minimized). The user's own input
during the test is told apart (it makes the run inconclusive, not passed)."""
import os, sys, time, tempfile, threading
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import win32gui, win32process
from pc_control import core, java
from pc_control.focus import library

c = {}
dll = library()
c["policy: an empty foreground is NOT permission to activate"] = not dll.pc_focus_allow(0, 0, 0, 1)
c["policy: a real click or the user's own switch still is"] = dll.pc_focus_allow(0, 1, 0, 0) and dll.pc_focus_allow(0, 0, 1, 0)

jdk = os.path.dirname(java.find_dlls()[0])
tmp = tempfile.mkdtemp()


def watch(stop, log):
    last = None
    while not stop.is_set():
        fg = win32gui.GetForegroundWindow()
        if fg != last:
            log.append((time.perf_counter(), fg)); last = fg
        time.sleep(0.001)


def launch(how):
    title = f"PCC Launch {how} {os.getpid()}"
    cmd = f'"{os.path.join(jdk, "javaw.exe")}" -cp "{os.path.join(ROOT, "tests", "java_app")}" PccSwingTest "{os.path.join(tmp, how)}.txt" "{title}"'
    fg0, tick0 = win32gui.GetForegroundWindow(), core._input_tick()
    stop, log = threading.Event(), []
    t = threading.Thread(target=watch, args=(stop, log), daemon=True); t.start()
    t0 = time.perf_counter()
    r = core.open_behind(cmd, "javaw", env=java.launch_env(cmd)) if how == "behind" else core.open_minimized(cmd, env=java.launch_env(cmd))
    time.sleep(2.5)                                           # the app finishes starting (Java shows and activates its frame here)
    stop.set(); t.join()
    pid = r["pid"]
    app_had_fg = any(fg and win32process.GetWindowThreadProcessId(fg)[1] == pid for _, fg in log)
    empty = [(a, b) for (a, fa), (b, _) in zip(log, log[1:] + [(time.perf_counter(), None)]) if fa == 0]
    longest_empty = max((b - a for a, b in empty), default=0.0)
    user = core._input_tick() != tick0
    for w in r["windows"]:
        if win32gui.IsWindow(w["hwnd"]):
            win32gui.PostMessage(w["hwnd"], 0x0010, 0, 0)
    return {"app_had_fg": app_had_fg, "final_ok": win32gui.GetForegroundWindow() == fg0, "user": user,
            "empty_ms": round(longest_empty * 1000), "windows": r["windows"], "log": [(round((a - t0) * 1000), fg) for a, fg in log]}


for how in ("behind", "minimized"):
    r = launch(how)
    print(f"   {how}: foreground timeline (ms, hwnd) = {r['log'][:8]} | longest empty = {r['empty_ms']} ms | user input = {r['user']}")
    if r["user"]:
        c[f"{how}: inconclusive because the user used the keyboard/mouse during the run (rerun)"] = False
        continue
    c[f"{how}: the Java app opened"] = bool(r["windows"])
    c[f"{how}: the app NEVER held the foreground (so never the user's keyboard)"] = not r["app_had_fg"]
    c[f"{how}: the user's window has the foreground at the end"] = r["final_ok"]
    c[f"{how}: an emptied foreground went back to the user within 250 ms"] = r["empty_ms"] <= 250

bad = [k for k, v in c.items() if not v]
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"launch focus verification passed ({len(c)} checks)" if not bad else f"launch focus verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
