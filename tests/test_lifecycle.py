"""When Claude Code closes, the PC-Control server goes with it: no orphan processes or cursors left hanging on screen."""
import os, subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import win32api, win32con, win32process

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

env = {**os.environ, "PYTHONUTF8": "1", "PC_CONTROL_HOME": os.path.join(os.environ.get("TEMP", "."), "pc-control_life")}
c = {}
# 1) the client closes the connection (EOF on stdin) without killing the process
p = subprocess.Popen([sys.executable, "-m", "pc_control.server"], cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=0x08000000)
time.sleep(2.5); c["the server starts and stays alive"] = alive(p.pid)
p.stdin.close()
t = wait_gone(p.pid, 8); c["closes the connection (EOF) -> the server exits by itself"] = t is not None
if t is None: p.kill()
print("   (exited in", t, "s)")

# 2) the process that launched it (Claude Code) dies abruptly, without closing anything cleanly
parent = subprocess.Popen([sys.executable, "-c",
    "import subprocess,sys,time;c=subprocess.Popen([sys.executable,'-m','pc_control.server'],cwd=sys.argv[1],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL);print(c.pid,flush=True);time.sleep(300)", ROOT],
    cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True, creationflags=0x08000000)
child = int(parent.stdout.readline()); time.sleep(2.5)
c["child server alive while its parent lives"] = alive(child)
subprocess.run(["taskkill", "/PID", str(parent.pid), "/F"], capture_output=True, creationflags=0x08000000)
t = wait_gone(child, 8); c["the parent dies abruptly -> the server exits by itself (no orphan left)"] = t is not None
if t is None: subprocess.run(["taskkill", "/PID", str(child), "/F"], capture_output=True, creationflags=0x08000000)
print("   (exited in", t, "s)")

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values())
print("lifecycle verification passed" if ok else "lifecycle verification FAILED")
sys.exit(0 if ok else 1)
