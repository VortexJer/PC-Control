"""Hidden desktop: the apps PC-Control opens live on a Windows desktop the user never sees.

A window on another desktop cannot appear on your screen, flicker, steal focus, or end up
in front of or behind anything. It is the same mechanism remote-access tools use.

Everything that touches those windows must run on the dedicated thread (`DESK.run`), which is bound to that desktop.
"""
import atexit, ctypes, ctypes.wintypes as wt, os, time
from concurrent.futures import ThreadPoolExecutor
import uiautomation as auto
import win32gui, win32process

_u = ctypes.WinDLL("user32", use_last_error=True)
_k = ctypes.WinDLL("kernel32", use_last_error=True)
_u.CreateDesktopW.restype = wt.HANDLE
_u.CreateDesktopW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
_u.SetThreadDesktop.argtypes = [wt.HANDLE]
_u.CloseDesktop.argtypes = [wt.HANDLE]
_k.OpenProcess.restype = wt.HANDLE


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


# A job object that kills everything in it when the server dies, however it dies (EOF, crash, killed): the apps PC-Control starts on the
# hidden desktop can then never outlive their session.
_k.CreateJobObjectW.restype = wt.HANDLE
_k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
_k.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
_k.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
_u.EnumDesktopsW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.LPARAM]
_u.OpenDesktopW.restype = wt.HANDLE
_u.OpenDesktopW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.BOOL, wt.DWORD]
_u.EnumDesktopWindows.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.LPARAM]
_u.GetProcessWindowStation.restype = wt.HANDLE


class _JOB_BASIC(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wt.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]


class _JOB_EXTENDED(ctypes.Structure):
    _fields_ = [("Basic", _JOB_BASIC), ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _make_kill_job():
    """A job object whose processes are all killed when its last handle closes (our process dying closes it). None if it cannot be made."""
    job = _k.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _JOB_EXTENDED(); info.Basic.LimitFlags = 0x2000                       # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):      # JobObjectExtendedLimitInformation
        _k.CloseHandle(job); return None
    return job


def _terminate(pid):
    hp = _k.OpenProcess(0x0001, False, pid)                                       # PROCESS_TERMINATE
    if hp:
        _k.TerminateProcess(hp, 0); _k.CloseHandle(hp)


def _alive(pid):
    hp = _k.OpenProcess(0x00100000, False, pid)                                   # SYNCHRONIZE
    if not hp:
        return False
    try:
        return _k.WaitForSingleObject(hp, 0) == 0x102                             # WAIT_TIMEOUT: still running
    finally:
        _k.CloseHandle(hp)


def reap_orphans():
    """Desktops named pc_control_<pid> whose server (pid) is gone: whatever still has a window there is ended. They are ONLY ours. Older
    versions ended the server without closing its desktop, so apps stayed alive on a desktop nobody could see. Returns the pids ended."""
    names = []
    _ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.LPWSTR, wt.LPARAM)
    _u.EnumDesktopsW(_u.GetProcessWindowStation(), _ENUM(lambda n, _: names.append(n) or True), 0)
    ended = []
    for name in names:
        if not name.lower().startswith("pc_control_") or not name[11:].isdigit() or _alive(int(name[11:])):
            continue
        hd = _u.OpenDesktopW(name, 0, False, 0x41)                                # DESKTOP_READOBJECTS | DESKTOP_ENUMERATE
        if not hd:
            continue
        pids = set()
        _WENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        _u.EnumDesktopWindows(hd, _WENUM(lambda h, _: pids.add(win32process.GetWindowThreadProcessId(h)[1]) or True), 0)
        _u.CloseDesktop(hd)
        for pid in pids - {os.getpid(), 0}:
            _terminate(pid); ended.append(pid)
    return ended

class HiddenDesktop:
    def __init__(self):
        self.name = f"pc_control_{os.getpid()}"
        self.h = None
        self.pool = None
        self.procs = {}                 # pid -> process handle
        self.job = None                 # kills what we launched when this process ends, however it ends
        atexit.register(self.close)

    # -- lifecycle --
    def ensure(self):
        if self.h:
            return
        h = _u.CreateDesktopW(self.name, None, None, 0, 0x10000000, None)      # GENERIC_ALL
        if not h:
            raise OSError(f"could not create the hidden desktop (error {ctypes.get_last_error()})")
        self.h = h
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="pc-control-hidden", initializer=self._init_thread)

    def _init_thread(self):
        if not _u.SetThreadDesktop(self.h):
            raise OSError(f"SetThreadDesktop failed (error {ctypes.get_last_error()})")
        self._uia = auto.UIAutomationInitializerInThread()
        self._uia.__enter__()

    def run(self, fn, timeout=120):
        """Run fn on the thread bound to the hidden desktop (windows and UIA are visible here)."""
        self.ensure()
        return self.pool.submit(fn).result(timeout=timeout)

    def close(self):
        if self.h and self.pool:                         # apps that re-launch themselves (Store apps) run under another pid: end them too
            try:
                own = {p for _, _, p in self.pool.submit(self._enum).result(timeout=3)} - set(self.procs) - {os.getpid()}
            except Exception:
                own = set()
            for pid in own:
                _terminate(pid)
        for pid, hp in list(self.procs.items()):
            _k.TerminateProcess(hp, 0)
            _k.CloseHandle(hp)
        self.procs.clear()
        if self.job:                                     # kills anything that is left (children of the apps)
            _k.CloseHandle(self.job); self.job = None
        if self.pool:
            self.pool.shutdown(wait=False)
        if self.h:
            _u.CloseDesktop(self.h)
            self.h = None
    # -- processes and windows --
    def launch(self, command):
        self.ensure()
        si = _STARTUPINFOW(); si.cb = ctypes.sizeof(si); si.lpDesktop = self.name
        pi = _PROCESS_INFORMATION()
        buf = ctypes.create_unicode_buffer(command)
        from .core import app_dir
        if not _k.CreateProcessW(None, buf, None, None, False, 0x08000000, None, app_dir(command), ctypes.byref(si), ctypes.byref(pi)):
            raise OSError(f"could not launch the app (error {ctypes.get_last_error()})")
        _k.CloseHandle(pi.hThread)
        if self.job is None:
            self.job = _make_kill_job()
        if self.job:
            _k.AssignProcessToJobObject(self.job, pi.hProcess)           # best effort: if it fails, close() still ends it
        self.procs[pi.dwProcessId] = pi.hProcess
        return pi.dwProcessId

    def _enum(self):
        """Enumerate windows with a title. ONLY inside the hidden desktop's thread (it queues nothing: avoids deadlocks)."""
        out = []
        win32gui.EnumWindows(lambda h, _: out.append((h, win32gui.GetWindowText(h), win32process.GetWindowThreadProcessId(h)[1]))
                             if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) else None, None)
        return out

    def windows(self):
        """[(hwnd, title, pid)] of the windows with a title on the hidden desktop (called from outside the thread)."""
        return self.run(self._enum) if self.h else []

    def owns(self, window, main_has):
        """True if the query (hwnd or title) refers to a window on the hidden desktop.
        With a title that also exists on your desktop, yours wins (main_has=True)."""
        if not self.h:
            return False
        w = str(window)
        ws = self.windows()
        if w.isdigit():
            return int(w) in {h for h, _, _ in ws}
        return (not main_has) and any(w.lower() in t.lower() for _, t, _ in ws)

    def open(self, command, wait_s=10.0):
        """Launch the app on the hidden desktop and wait for its first window with a title."""
        pid = self.launch(command)
        t0 = time.time(); found = []
        while time.time() - t0 < wait_s:
            found = [(h, t) for h, t, p in self.windows() if p == pid]
            if found:
                time.sleep(0.3)
                found = [(h, t) for h, t, p in self.windows() if p == pid]
                break
            time.sleep(0.1)
        return {"pid": pid, "windows": [{"hwnd": h, "title": t[:50]} for h, t in found],
                "note": "" if found else "no window with a title (the app may be a Store app, which does not support another desktop, or it may take longer)"}


DESK = HiddenDesktop()


def on_hidden_thread():
    """True if the calling thread runs on PC-Control's hidden desktop (nothing done there can reach the user's screen or focus)."""
    try:
        h = _u.GetThreadDesktop(_k.GetCurrentThreadId())
        buf = ctypes.create_unicode_buffer(256); n = wt.DWORD(0)
        _u.GetUserObjectInformationW(ctypes.c_void_p(h), 2, buf, 512, ctypes.byref(n))      # UOI_NAME
        return buf.value.lower().startswith("pc_control")
    except Exception:
        return False
