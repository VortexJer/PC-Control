"""Permissions granted by the USER when the client (Claude Code) does not support questions from the server.

Claude Code does let a PreToolUse hook return "ask": Claude Code itself shows the user its permission dialog and the
model cannot answer it. The hook (hook.py) and the server (server.py) talk to each other through marks in ~/.pc-control/perm:

  asked/<signature>    the hook asked for permission and the call arrived (= the user said yes). The server consumes it once.
  pending/<signature>  the server needed permission and could not ask: the hook, on seeing the SAME call repeated, asks.
  approved.json        protected windows already approved by the user (key "category|process"), valid for several hours.
"""
import hashlib, json, os, time

ASK_TTL = 180.0                    # seconds an asked/pending mark stays valid
APPROVED_TTL = 4 * 3600.0          # how long the user's yes to a protected window lasts
SIG_KEYS = ("window", "target", "name", "command", "text", "points", "shape")


def home():
    from . import envvars
    return envvars.get("HOME") or os.path.join(os.path.expanduser("~"), ".pc-control")


def sig(tool, args):
    """Stable signature of a call: the same for the hook (which sees the arguments as given) and for the server (which also sees the defaults)."""
    d = {k: str(args[k]) for k in SIG_KEYS if args.get(k) not in (None, "")}
    return hashlib.sha1(json.dumps([tool, d], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]


def _dir(kind):
    d = os.path.join(home(), "perm", kind)
    os.makedirs(d, exist_ok=True)
    return d


def mark(kind, s, text=""):
    with open(os.path.join(_dir(kind), s), "w", encoding="utf-8") as f:
        f.write(text)                                     # e.g. what the server knows about the action ("press «Delete all»")


def fresh(kind, s):
    f = os.path.join(_dir(kind), s)
    return os.path.exists(f) and time.time() - os.path.getmtime(f) < ASK_TTL


def take(kind, s):
    """If there is a recent mark, delete it and return its text (or True if it had none); otherwise False. Each permission is spent only once."""
    f = os.path.join(_dir(kind), s)
    ok = fresh(kind, s)
    text = ""
    try:
        text = open(f, encoding="utf-8").read()
        os.remove(f)
    except OSError:
        pass
    return (text or True) if ok else False


def _approved_file():
    return os.path.join(home(), "perm", "approved.json")


def _procs():
    """pid -> (ppid, lowercase name) of all processes (Toolhelp)."""
    import ctypes
    from ctypes import wintypes as wt
    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k.CreateToolhelp32Snapshot(2, 0); out = {}
    pe = PE(); pe.dwSize = ctypes.sizeof(PE)
    ok = k.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(pe))
    while ok:
        out[pe.th32ProcessID] = (pe.th32ParentProcessID, pe.szExeFile.lower())
        ok = k.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(pe))
    k.CloseHandle(ctypes.c_void_p(snap))
    return out


def _started(pid):
    import ctypes
    from ctypes import wintypes as wt
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return 0
    try:
        c, e, kt, ut = (wt.FILETIME() for _ in range(4))
        k.GetProcessTimes(ctypes.c_void_p(h), ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut))
        return (c.dwHighDateTime << 32) | c.dwLowDateTime
    finally:
        k.CloseHandle(ctypes.c_void_p(h))


def session_id():
    """Identify the Claude Code SESSION that is using PC-Control (the server and the hook hang off the same claude process):
    the user's yes holds for that session and not for later ones. With no 'claude' process in the chain, the direct parent."""
    try:
        procs = _procs(); pid = os.getpid(); first_parent = procs.get(pid, (os.getppid(), ""))[0]
        cur = pid
        for _ in range(12):
            ppid, _name = procs.get(cur, (0, ""))
            if not ppid or ppid not in procs:
                break
            cur = ppid
            if procs[cur][1].startswith("claude"):
                return f"{cur}-{_started(cur)}"
        return f"{first_parent}-{_started(first_parent)}"
    except Exception:
        return str(os.getppid())


def _load_approved():
    try:
        return json.load(open(_approved_file(), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_flag(name):
    os.makedirs(os.path.join(home(), "perm", "flags"), exist_ok=True)
    open(os.path.join(home(), "perm", "flags", name), "w").close()


def flag(name):
    return os.path.exists(os.path.join(home(), "perm", "flags", name))


def approved(key):
    return time.time() - _load_approved().get(session_id(), {}).get(key, 0) < APPROVED_TTL


def approve(key):
    _dir("asked")
    data = _load_approved(); sid = session_id()
    data = {sid: {k: v for k, v in data.get(sid, {}).items() if time.time() - v < APPROVED_TTL}}     # this session only; the others no longer count
    data[sid][key] = time.time()
    json.dump(data, open(_approved_file(), "w", encoding="utf-8"))


# ---- CLAUDE CODE's permission mode (default / plan / acceptEdits / auto / bypassPermissions) ----
# The hook receives it on every call (permission_mode field) and records it; the server reads it to follow it (PC_CONTROL_MODE=follow).
def _cc_file():
    return os.path.join(home(), "perm", "cc_mode.json")


def set_cc_mode(pm):
    if not pm:
        return
    _dir("asked")
    try:
        data = json.load(open(_cc_file(), encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    sid = session_id()
    data = {k: v for k, v in data.items() if time.time() - v.get("t", 0) < 24 * 3600}      # old sessions dropped
    data[sid] = {"mode": str(pm), "t": time.time()}
    json.dump(data, open(_cc_file(), "w", encoding="utf-8"))


def cc_mode():
    """The permission mode Claude Code has right now in THIS session, or '' if unknown (e.g. without the hook)."""
    try:
        return str(json.load(open(_cc_file(), encoding="utf-8")).get(session_id(), {}).get("mode", ""))
    except (OSError, ValueError):
        return ""


# ---- Claude Code's "don't ask again", converted to ONE window ----
def _last_file():
    return os.path.join(home(), "perm", "lastask.json")


def set_last(tool, key):
    """The last window the hook asked the user about with this tool (per session)."""
    _dir("asked")
    try:
        data = json.load(open(_last_file(), encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    sid = session_id()
    data = {k: v for k, v in data.items() if any(time.time() - e.get("t", 0) < 24 * 3600 for e in v.values())}
    data.setdefault(sid, {})[tool] = {"key": key, "t": time.time()}
    json.dump(data, open(_last_file(), "w", encoding="utf-8"))


def get_last(tool):
    try:
        return json.load(open(_last_file(), encoding="utf-8")).get(session_id(), {}).get(tool)
    except (OSError, ValueError):
        return None
