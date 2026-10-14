"""Terminals: typing, keys, reading and looking go through the console of the shell (no keyboard, mouse or focus), the right shell is chosen among
several, and the terminal Claude Code itself runs in is never typed into. Real consoles and Windows Terminal windows of our own; the user's windows are
never touched (the shells we start carry a marker in their command line, and only those are closed)."""
import os, shutil, subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import win32gui
from pc_control import core, cursor, server, terminal as T

server.policy.mode = lambda: "bypass"              # the permission question is tested elsewhere; here the console itself
R = server.RAW
c = {}
MARK = "PCC_TERM_TEST"

def close_mine():
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'cmd.exe','powershell.exe' -and $_.CommandLine -like '*" + MARK + "*' } "
          "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, creationflags=0x08000000)

def find(title, cls=None):
    out = []
    win32gui.EnumWindows(lambda h, _: out.append(h) if title in win32gui.GetWindowText(h) and (cls is None or win32gui.GetClassName(h) == cls) else None, None)
    return out[0] if out else None

# ---- pure logic: choosing the shell among several tabs (fake process table, fake consoles) ----
class Fake:
    # one Windows Terminal (pid 100, window 1) with three tabs, a delegated shell that is NOT its child, and a console of another window
    table = {1: (0, "explorer.exe"), 100: (1, "windowsterminal.exe"), 101: (100, "openconsole.exe"), 102: (100, "powershell.exe"), 105: (102, "claude.exe"),
             104: (100, "cmd.exe"), 107: (3000, "pwsh.exe"), 3000: (1, "python.exe"), 200: (1, "python.exe")}
    consoles = {102: ("✳ trabajando", 11), 105: ("✳ trabajando", 11), 104: ("build server", 12), 107: ("build server", 13), 200: ("otro", 99), 3000: ("otro", 99)}
    owners = {11: 1, 12: 1, 13: 1, 99: 2}
    title = "build server"
def fake_helper(req, timeout=20):
    assert req["op"] == "infos"
    return {"infos": {str(p): ({"title": Fake.consoles[p][0], "window": Fake.consoles[p][1]} if p in Fake.consoles else None) for p in req["pids"]}}
saved = (T.processes, T.helper, T.win32gui.GetClassName, T.win32process.GetWindowThreadProcessId, T.win32gui.GetWindowText, T.win32gui.GetWindow,
         T.win32gui.GetParent, T.os.getpid)
try:
    T.processes = lambda: Fake.table
    T.helper = fake_helper
    T.win32gui.GetClassName = lambda h: "CASCADIA_HOSTING_WINDOW_CLASS"
    T.win32process.GetWindowThreadProcessId = lambda h: (0, 100)
    T.win32gui.GetWindowText = lambda h: Fake.title
    T.win32gui.GetWindow = lambda w, rel: Fake.owners.get(w, 0)
    T.win32gui.GetParent = lambda w: Fake.owners.get(w, 0)
    T.os.getpid = lambda: 105                                                     # this server runs under shell 102 (its claude.exe is 105)
    c["candidates: the children of Windows Terminal AND the programs that usually live in a console, even if not its children (Windows 11 handoff)"] = (
        set(T.candidates(1, Fake.table)) == {102, 104, 107, 200, 3000})            # (not this server itself: here pid 105)
    t = T.tabs(1, Fake.table)
    c["one entry per console (tab): a shell and what it started share one, and consoles of other windows are left out"] = sorted(x[0] for x in t) == [102, 104, 107]
    c["the tab Claude Code runs in is recognised (its shell and its claude.exe share the console)"] = [x[2] for x in sorted(t)] == [True, False, False]
    Fake.title = "build server"
    try: T.resolve(1); picked = "no error"
    except T.TerminalError as e: picked = str(e)
    c["two tabs with the window's title: it refuses and lists them instead of guessing"] = "several shells" in picked and "pid 104" in picked and "pid 107" in picked
    Fake.consoles[107] = ("otra cosa", 13)
    c["a unique title picks that tab, even if its shell is not a child of Windows Terminal"] = T.resolve(1) == 104
    Fake.consoles[104] = ("otra", 12); Fake.consoles[107] = ("build server", 13)
    c["...whichever tab it is"] = T.resolve(1) == 107
    Fake.title = "◑ trabajando"
    try: T.resolve(1); got = "no error"
    except T.TerminalError as e: got = str(e)
    c["when the active tab is the one Claude Code runs in, it refuses (it would type into its own prompt)"] = "itself" in got
    Fake.title = "nada que ver"; Fake.consoles = {102: ("✳ trabajando", 11), 105: ("✳ trabajando", 11)}
    try: T.resolve(1); alone = "no error"
    except T.TerminalError as e: alone = str(e)
    c["when the only console is the one Claude Code runs in, it says so"] = "itself" in alone
finally:
    (T.processes, T.helper, T.win32gui.GetClassName, T.win32process.GetWindowThreadProcessId, T.win32gui.GetWindowText, T.win32gui.GetWindow,
     T.win32gui.GetParent, T.os.getpid) = saved
c["a title with status symbols in front matches the same title without them"] = T._norm("✳  Build   Server") == T._norm("build server") == "build server"
# ---- a real console of our own, started by open_app (it used to run inside the server's hidden console and show no window) ----
try:
    out = R["open_app"](f"cmd.exe /k title {MARK}_A", confirm=True)
    hw = find(MARK + "_A")
    c["open_app starts a console program in a console window of its own"] = hw is not None and win32gui.GetClassName(hw) in T.TERMINAL_CLASSES and "console window" in out
    w = str(hw)
    r = R["look"](w, confirm=True); r = r if isinstance(r, str) else r[0]
    c["look on a terminal gives its exact console text (no OCR) and names the shell"] = r.startswith("console") and "exact text" in r and "shell pid" in r
    r = R["type"](w, "", "echo HOLA_TERMINAL_PCC\n", confirm=True)
    c["type writes AND presses Enter, and reads the console back to confirm"] = "console shows it" in r and "HOLA_TERMINAL_PCC" in r and "pressed Enter" in r
    r = R["read"](w, find="HOLA_TERMINAL", confirm=True)
    c["read finds text of the console screen"] = "lines match" in r and "HOLA_TERMINAL_PCC" in r
    R["type"](w, "", "ping -n 40 127.0.0.1\n", confirm=True); time.sleep(1.2)
    r = R["key"](w, "ctrl+c", confirm=True)
    c["ctrl+c reaches what runs in the console (the ping is interrupted)"] = "^C" in r or "Control-C" in r
    r = R["type"](w, "", "echo SIN_ENTER", confirm=True)
    c["typing without a line break leaves the text on the prompt, unsent"] = "console shows it" in r and "pressed Enter" not in r
    r = R["key"](w, "enter", confirm=True)
    c["key enter then runs it"] = "SIN_ENTER" in r
    r = R["key"](w, "ctrl+z", confirm=True)
    c["a key a console cannot take is reported, not faked"] = "ok" in r or "did not press" in r
finally:
    close_mine()
    time.sleep(0.5)

# ---- the typing bar is drawn where the console cursor is (a visible console window: started behind everything) ----
try:
    si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW; si.wShowWindow = 4
    p = subprocess.Popen(["cmd.exe", "/k", f"title {MARK}_B"], creationflags=0x10, startupinfo=si)
    time.sleep(2.0); hb = find(MARK + "_B")
    if hb:
        win32gui.SetWindowPos(hb, 1, 0, 0, 0, 0, 0x0013)
    r = R["type"](str(hb), "", "echo CARET\n", confirm=True) if hb else ""
    time.sleep(0.6)
    caret = cursor.CURSOR.hwnds.get("caret", 0)
    c["the orange typing bar is shown while typing into a terminal"] = bool(caret) and bool(win32gui.IsWindowVisible(caret))
    res = T.screen(T.resolve(hb)) if hb else None
    (x0, y0), (x1, y1), ch = T.cursor_points(hb, {**res, "before": res["cursor"]}) if hb else ((0, 0), (0, 0), 0)
    l, t, rr, b = T.text_area(hb) if hb else (0, 0, 0, 0)
    c["its position is inside the console's text area (so it lands on the cursor, not elsewhere)"] = hb is not None and l <= x1 <= rr and t <= y1 <= b and ch > 4
finally:
    close_mine()

# ---- Windows Terminal (a tab of ours; the user's tabs are in the same process and are never touched) ----
if shutil.which("wt.exe"):
    try:
        subprocess.Popen(["wt.exe", "-w", "new", "new-tab", "--title", MARK + "_C", "cmd.exe", "/k", f"title {MARK}_C"])
        time.sleep(4.0); hc = find(MARK + "_C", "CASCADIA_HOSTING_WINDOW_CLASS")
        c["a Windows Terminal window of ours is found"] = hc is not None
        if hc:
            r = R["type"](str(hc), "", "echo HOLA_WT_PCC\n", confirm=True)
            c["typing into a Windows Terminal tab works and is confirmed from its console"] = "console shows it" in r and "HOLA_WT_PCC" in r
            r = R["read"](str(hc), confirm=True)
            c["read of a Windows Terminal tab returns its console, not an OCR guess"] = "console of shell" in r and "HOLA_WT_PCC" in r
            R["type"](str(hc), "", "for /L %i in (1,1,150) do @echo linea_%i\n", confirm=True); time.sleep(1.5)
            whole = R["read"](str(hc), length=40000, confirm=True)
            c["read returns the whole HISTORY of a Windows Terminal tab (lines that already scrolled away)"] = "linea_1 " in whole.replace("\n", " ") + " " and "linea_150" in whole
            view0 = R["look"](str(hc), confirm=True)
            nums0 = [int(x) for x in __import__("re").findall(r"linea_(\d+)", view0)]
            c["look shows what is in view now (the end of the output)"] = bool(nums0) and max(nums0) == 150 and min(nums0) > 50
            r = R["scroll"](str(hc), "up", "", 3, confirm=True)
            nums1 = [int(x) for x in __import__("re").findall(r"linea_(\d+)", R["look"](str(hc), confirm=True))]
            c["scroll up really moves the view and the answer confirms it from the terminal"] = "confirmed from the terminal" in r and bool(nums1) and max(nums1) < max(nums0)
            r = R["scroll"](str(hc), "bottom", "", 1, confirm=True)
            nums2 = [int(x) for x in __import__("re").findall(r"linea_(\d+)", R["look"](str(hc), confirm=True))]
            c["scroll to the bottom brings the view back to the end"] = bool(nums2) and max(nums2) == 150
    finally:
        close_mine()
else:
    print("(Windows Terminal not installed: its checks are skipped)")

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values()) and len(c) >= 23
print(f"terminal verification {'passed' if ok else 'FAILED'} ({len(c)} checks)")
sys.exit(0 if ok else 1)