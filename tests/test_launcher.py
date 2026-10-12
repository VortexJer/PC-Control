"""A launcher (a program whose window belongs to a CHILD process it starts, like wmplayer -> setup_wm or git-gui -> wish): open_app
must still find that window, minimize it and report it, instead of "no window with a title"."""
import json, os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import win32gui
from pc_control import core

state = os.path.join(tempfile.gettempdir(), "pc-control_launcher_state.json")
app = os.path.join(HERE, "test_app.ps1")
# pythonw is the launcher (no console, like a real GUI launcher): it starts powershell WITHOUT a console window (its child owns the
# test window) and exits
pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
child = ["powershell.exe", "-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", app, state, "-Taskbar"]
cmd = f'"{pyw}" -c "import subprocess; subprocess.Popen({child!r}, creationflags=0x08000000)"'
c = {}
fg0 = win32gui.GetForegroundWindow()
r = core.open_minimized(cmd, wait_s=20)
wins = [w for w in r["windows"] if w["title"].startswith("pc-control-test")]
c["the child's window is found and reported (not 'no window with a title')"] = bool(wins)
if wins:
    h = wins[0]["hwnd"]
    fg = win32gui.GetForegroundWindow()
    print("foreground now:", fg, win32gui.GetWindowText(fg)[:40], win32gui.GetClassName(fg))
    c["it is left minimized, and the focus is not on anything it opened"] = bool(win32gui.IsIconic(h)) and fg == fg0
    win32gui.PostMessage(h, 0x0010, 0, 0)
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
ok = len(c) == 2 and all(c.values())
print(f"launcher verification {'passed' if ok else 'FAILED'}" + ("" if ok else f" | {r}"))
sys.exit(0 if ok else 1)
