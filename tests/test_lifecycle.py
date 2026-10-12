"""When Claude Code closes, the PC-Control server goes with it: no orphan processes or cursors left hanging on screen.
Both ways of launching it: `python -m pc_control.server` and the `pc-control-mcp` launcher that `pc-control install` registers."""
import json, os, subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import win32api, win32con, win32process
from pc_control import cli

def alive(pid):
    try:
        h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except Exception:
        return False
    try:
        return win32process.GetExitCodeProcess(h) == 259
    finally:
        win32api.CloseHandle(h)

def wait_gone(pid, secs):
    end = time.time() + secs
    while time.time() < end:
        if not alive(pid): return round(secs - (end - time.time()), 1)
        time.sleep(0.1)
    return None

def kids(pid):
    """Direct children of a process (the launcher starts the real Python as its child)."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}').ProcessId"],
                         capture_output=True, text=True, creationflags=0x08000000).stdout.split()
    return [int(x) for x in out]

def kill_tree(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)

env = {**os.environ, "PYTHONUTF8": "1", "PC_CONTROL_HOME": os.path.join(os.environ.get("TEMP", "."), "pc-control_life")}
c = {}
launchers = [("module", [sys.executable, "-m", "pc_control.server"])]
exe = cli.server_exe()
if exe: launchers.append(("launcher", [exe]))
c["pc-control-mcp launcher exists next to this Python (pip install made it)"] = bool(exe)
for label, cmd in launchers:
    # 1) the client closes the connection (EOF on stdin) without killing the process
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    time.sleep(2.5); c[f"[{label}] the server starts and stays alive"] = alive(p.pid)
    p.stdin.close()
    t = wait_gone(p.pid, 8); c[f"[{label}] closes the connection (EOF) -> the server exits by itself"] = t is not None
    if t is None: kill_tree(p.pid)
    print(f"   [{label}] (exited in", t, "s)")

    # 2) the process that launched it (Claude Code) dies abruptly, without closing anything cleanly
    parent = subprocess.Popen([sys.executable, "-c",
        "import subprocess,sys,time,json;c=subprocess.Popen(json.loads(sys.argv[2]),cwd=sys.argv[1],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL);print(c.pid,flush=True);time.sleep(300)",
        ROOT, json.dumps(cmd)], cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True, creationflags=0x08000000)
    child = int(parent.stdout.readline()); time.sleep(2.5)
    grand = kids(child)                                           # with the launcher: the real Python is the launcher's child
    c[f"[{label}] child server alive while its parent lives"] = alive(child)
    subprocess.run(["taskkill", "/PID", str(parent.pid), "/F"], capture_output=True, creationflags=0x08000000)
    t = wait_gone(child, 8); c[f"[{label}] the parent dies abruptly -> the server exits by itself (no orphan left)"] = t is not None
    left = [g for g in grand if wait_gone(g, 8) is None]
    c[f"[{label}] ... and so does the real Python behind the launcher"] = not left
    for pid in ([child] if t is None else []) + left: kill_tree(pid)
    print(f"   [{label}] (exited in", t, "s)")

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values())
print("lifecycle verification passed" if ok else "lifecycle verification FAILED")
sys.exit(0 if ok else 1)
