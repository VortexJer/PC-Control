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


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


class HiddenDesktop:
    def __init__(self):
        self.name = f"pc_control_{os.getpid()}"
        self.h = None
        self.pool = None
        self.procs = {}                 # pid -> process handle
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
        for pid, hp in list(self.procs.items()):
            _k.TerminateProcess(hp, 0)
            _k.CloseHandle(hp)
        self.procs.clear()
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
        if not _k.CreateProcessW(None, buf, None, None, False, 0x08000000, None, None, ctypes.byref(si), ctypes.byref(pi)):
            raise OSError(f"could not launch the app (error {ctypes.get_last_error()})")
        _k.CloseHandle(pi.hThread)
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
