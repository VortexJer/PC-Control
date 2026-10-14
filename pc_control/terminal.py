"""Terminals: which shell lives in a window, and talking to it WITHOUT touching the user's keyboard, mouse or focus.

A terminal window is not a place where mouse messages and WM_CHAR work (Windows Terminal draws its own text and ignores them), but every shell
reads its keys from a console input buffer, and that buffer can be written to directly. See _conhelper.py.

The hard part is "which shell is this window?": Windows Terminal is ONE process that hosts every window and every tab. So the shells of a
window are found as the children of the pseudo console hosts (OpenConsole.exe) under that process, and the right tab is the one whose
console title is the window's title. If that is not unique the answer lists the choices instead of guessing: typing into the wrong
shell is worse than not typing. The terminal Claude Code itself runs in is never a target (it would type into its own prompt).
"""
import ctypes, ctypes.wintypes as wt, json, os, re, subprocess, sys
import win32gui, win32process

TERMINAL_CLASSES = ("CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass")


class TerminalError(RuntimeError):
    """Something the user or the model must be told in plain words (it is shown as the answer of the tool)."""


class _PE(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD), ("heap", ctypes.c_size_t), ("module", wt.DWORD), ("threads", wt.DWORD),
                ("ppid", wt.DWORD), ("prio", wt.LONG), ("flags", wt.DWORD), ("exe", wt.WCHAR * 260)]


def processes():
    """{pid: (parent pid, lower-case image name)} of every running process."""
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k.CreateToolhelp32Snapshot(2, 0)                              # TH32CS_SNAPPROCESS
    out = {}
    try:
        e = _PE(); e.size = ctypes.sizeof(_PE)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[e.pid] = (e.ppid, e.exe.lower())
            ok = k.Process32NextW(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    return out


def ancestors(pid, table=None):
    table = table or processes()
    seen = set()
    while pid in table and pid not in seen:
        seen.add(pid); pid = table[pid][0]
    return seen


def is_terminal(hwnd):
    try:
        return win32gui.GetClassName(hwnd) in TERMINAL_CLASSES
    except Exception:
        return False


def _norm(title):
    """A title without the status symbols some programs put in front of it, and without case or spacing differences."""
    return re.sub(r"\s+", " ", re.sub(r"^[^\w]+", "", title or "")).strip().lower()


def helper(req, timeout=20):
    p = subprocess.run([sys.executable, "-m", "pc_control._conhelper"], input=json.dumps(req), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=0x08000000, timeout=timeout)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        raise TerminalError(f"the console helper gave no answer ({(p.stderr or p.stdout).strip()[:200]})")
    if "error" in out:
        raise TerminalError(out["error"])
    return out


def _descendants_of(pid, table):
    kids = {}
    for p, (pp, _) in table.items():
        kids.setdefault(pp, []).append(p)
    res, stack = set(), [pid]
    while stack:
        for c in kids.get(stack.pop(), []):
            if c not in res:
                res.add(c); stack.append(c)
    return res


SHELLISH = {"cmd.exe", "powershell.exe", "pwsh.exe", "powershell_ise.exe", "bash.exe", "wsl.exe", "zsh.exe", "fish.exe", "nu.exe", "sh.exe", "python.exe", "py.exe",
            "node.exe", "ssh.exe", "claude.exe", "ipython.exe", "irb.exe", "ghci.exe", "psql.exe", "mysql.exe", "sqlite3.exe"}


def candidates(hwnd, table=None):
    """Processes that could have their console in this window. Windows Terminal hosts every window and tab in ONE process, and since the default
    terminal handoff of Windows 11 a shell no longer has to be its child, so: its children, plus every program that usually lives in a console."""
    table = table or processes()
    cls = win32gui.GetClassName(hwnd)
    owner = win32process.GetWindowThreadProcessId(hwnd)[1]
    if cls == "CASCADIA_HOSTING_WINDOW_CLASS":
        pids = {p for p, (pp, n) in table.items() if (pp == owner and n not in ("openconsole.exe", "conhost.exe")) or n in SHELLISH}
    else:                                                                # a classic console window: its owner (conhost) and its neighbours
        pids = {owner} | {p for p, (pp, n) in table.items() if pp == owner} | ({table[owner][0]} if owner in table else set())
    pids.discard(os.getpid())
    return sorted(pids)


def _owner_window(w):
    try:
        return win32gui.GetWindow(w, 4) or win32gui.GetParent(w)             # the pseudo console of a Windows Terminal tab is owned by the terminal window
    except Exception:
        return 0


def tabs(hwnd, table=None):
    """The consoles of this window: [(representative pid, console title, is it the terminal Claude Code runs in)]. One entry per console (tab): a shell and
    the programs it started share one."""
    table = table or processes()
    mine = ancestors(os.getpid(), table)
    pids = candidates(hwnd, table)
    if not pids:
        return []
    infos = helper({"op": "infos", "pids": pids})["infos"]
    cls = win32gui.GetClassName(hwnd)
    groups = {}
    for p in pids:
        inf = infos.get(str(p))
        if not inf or not inf["window"]:
            continue
        w = inf["window"]
        if (w == hwnd) if cls == "ConsoleWindowClass" else (_owner_window(w) == hwnd):
            groups.setdefault(w, []).append((p, inf["title"]))
    out = []
    for w, members in groups.items():
        top = min(members, key=lambda m: len(ancestors(m[0], table)))            # the outermost one (the shell, not what it started)
        out.append((top[0], top[1], any(m[0] in mine for m in members)))
    return out


def resolve(hwnd):
    """The pid of the shell in this terminal window (the active tab), or TerminalError saying why not."""
    t = tabs(hwnd)
    if not t:
        raise TerminalError("no shell found in that terminal window (a program running as administrator cannot be reached from here)")
    wanted = _norm(win32gui.GetWindowText(hwnd))                         # the window's title is the active tab's title
    others = [x for x in t if not x[2]]
    same = [x for x in others if _norm(x[1]) == wanted]
    self_active = any(x[2] and _norm(x[1]) == wanted for x in t)
    if len(same) == 1 and not self_active:
        return same[0][0]
    if self_active or not others:
        raise TerminalError("that is the terminal Claude Code itself runs in: typing there would type into its own prompt")
    if len(t) == 1:
        return others[0][0]
    pool = same or others
    raise TerminalError("several shells could be the one in that window, and typing into the wrong one is worse than not typing. Tell the user which "
                        "tab, or give the window a clear title. Candidates: " + "; ".join(f"pid {p} «{(ti or '')[:40]}»" for p, ti, _ in pool[:8]))

def write(pid, text):
    """Type text into the shell's console. Returns what the console shows right after (rows, cursor before and after, visible window): one call."""
    return helper({"op": "type", "pid": pid, "text": text})


def press(pid, name):
    return helper({"op": "key", "pid": pid, "name": name})


def screen(pid, rows=200):
    return helper({"op": "read", "pid": pid, "rows": rows})


def text_of(pid, rows=200):
    return "\n".join(screen(pid, rows)["rows"])

def text_area(hwnd):
    """Screen rectangle where the console text is drawn: the client area of a classic console; in Windows Terminal its TermControl (the client
    area also holds the tab bar), found through UI Automation, or the client area minus a tab bar's height if that fails."""
    l, t = win32gui.ClientToScreen(hwnd, (0, 0))
    cr = win32gui.GetClientRect(hwnd)
    area = (l, t, l + cr[2], t + cr[3])
    if win32gui.GetClassName(hwnd) == "CASCADIA_HOSTING_WINDOW_CLASS":
        try:
            import uiautomation as auto
            c = auto.Control(searchFromControl=auto.ControlFromHandle(hwnd), ClassName="TermControl")
            if c.Exists(0.6, 0.1):
                r = c.BoundingRectangle
                if r.width() > 50 and r.height() > 30:
                    return (r.left, r.top, r.right, r.bottom)
        except Exception:
            pass
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd) / 96 if hasattr(ctypes.windll.user32, "GetDpiForWindow") else 1.0
        area = (area[0], area[1] + int(44 * dpi), area[2], area[3])
    return area


def cursor_points(hwnd, res):
    """From the answer of write/press: screen points of the console cursor before and after, and the height of one text line."""
    l, t, r, b = text_area(hwnd)
    wl, wt_, wr, wb = res["win"]
    cols, rows = max(1, wr - wl + 1), max(1, wb - wt_ + 1)
    cw, ch = (r - l) / cols, (b - t) / rows
    pt = lambda c: (int(l + (c[0] - wl) * cw + 2), int(t + (c[1] - wt_ + 0.5) * ch))
    return pt(res["before"]), pt(res["cursor"]), ch

def uia_text(hwnd, visible_only=False):
    """The text of a Windows Terminal tab through UI Automation: its whole history, or only what is in view right now. None if it is not available.
    (The console of the shell only holds one screen; the history lives in the terminal, and so does the scrolled view.)"""
    if win32gui.GetClassName(hwnd) != "CASCADIA_HOSTING_WINDOW_CLASS":
        return None
    try:
        import uiautomation as auto
        c = auto.Control(searchFromControl=auto.ControlFromHandle(hwnd), ClassName="TermControl")
        if not c.Exists(0.6, 0.1):
            return None
        tp = c.GetPattern(auto.PatternId.TextPattern)
        if not tp:
            return None
        if visible_only:
            rs = tp.GetVisibleRanges()
            txt = "\n".join(r.GetText(-1) for r in rs) if rs else ""
        else:
            txt = tp.DocumentRange.GetText(-1)
    except Exception:
        return None
    lines = [ln.rstrip() for ln in txt.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)
