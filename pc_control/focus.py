"""Thread-specific native pre-activation guard for background app launches.

Unlike a WinEvent observer, a CBT hook can refuse activation before it happens.
Hooks are scoped to the selected executable's threads and removed after startup.
"""
import ctypes
from ctypes import wintypes as W
from functools import lru_cache
import os
from pathlib import Path
import platform
import secrets
import subprocess
import threading
import time


class ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("pid", W.DWORD),
                ("heap", ctypes.c_size_t), ("module", W.DWORD), ("threads", W.DWORD),
                ("parent", W.DWORD), ("priority", W.LONG), ("flags", W.DWORD), ("exe", W.WCHAR * 260)]


class ThreadEntry(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("usage", W.DWORD), ("tid", W.DWORD), ("pid", W.DWORD),
                ("priority", W.LONG), ("delta", W.LONG), ("flags", W.DWORD)]


def kernel():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    k.CreateToolhelp32Snapshot.restype = W.HANDLE
    k.CloseHandle.argtypes = [W.HANDLE]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [W.HANDLE, ctypes.POINTER(ProcessEntry)]
    k.Thread32First.argtypes = k.Thread32Next.argtypes = [W.HANDLE, ctypes.POINTER(ThreadEntry)]
    k.OpenThread.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenThread.restype = W.HANDLE
    k.ResumeThread.argtypes = [W.HANDLE]
    k.ResumeThread.restype = W.DWORD
    return k


def snapshot(exe):
    """Matching process ids and their threads. No command lines or document text is read."""
    k = kernel()
    h = k.CreateToolhelp32Snapshot(2 | 4, 0)
    if h == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    pids, threads = set(), {}
    try:
        p = ProcessEntry(); p.dwSize = ctypes.sizeof(p)
        more = k.Process32FirstW(h, ctypes.byref(p))
        while more:
            if p.exe.lower().removesuffix(".exe") == exe.lower().removesuffix(".exe"):
                pids.add(p.pid)
            more = k.Process32NextW(h, ctypes.byref(p))
        t = ThreadEntry(); t.dwSize = ctypes.sizeof(t)
        more = k.Thread32First(h, ctypes.byref(t))
        while more:
            threads.setdefault(t.pid, []).append(t.tid)
            more = k.Thread32Next(h, ctypes.byref(t))
    finally:
        k.CloseHandle(h)
    return pids, threads


@lru_cache(maxsize=1)
def library():
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("the native focus guard currently requires 64-bit Python")
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    path = Path(__file__).with_name("native") / f"focus_guard_{arch}.dll"
    if not path.exists():
        raise RuntimeError("the native focus guard is missing; build/package it before launching apps")
    dll = ctypes.WinDLL(str(path), use_last_error=True)
    dll.pc_focus_install.argtypes = [W.DWORD]
    dll.pc_focus_install.restype = W.HANDLE
    dll.pc_focus_remove.argtypes = [W.HANDLE]
    dll.pc_focus_remove.restype = W.BOOL
    dll.pc_focus_allow.argtypes = [ctypes.c_int] * 4
    dll.pc_focus_allow.restype = ctypes.c_int
    dll.pc_focus_owned.argtypes = [W.HWND, W.HWND]
    dll.pc_focus_owned.restype = ctypes.c_int
    dll.pc_focus_probe_install.argtypes = [W.DWORD]
    dll.pc_focus_probe_install.restype = W.HANDLE
    return dll


def process_arch(pid):
    """'native' (same machine as this server), 'x86' (a 32-bit app under WOW64) or None (unknown / not guardable)."""
    k = kernel()
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = W.HANDLE
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        if hasattr(k, "IsWow64Process2"):
            k.IsWow64Process2.argtypes = [W.HANDLE, ctypes.POINTER(W.WORD), ctypes.POINTER(W.WORD)]
            process, native = W.WORD(), W.WORD()
            if not k.IsWow64Process2(handle, ctypes.byref(process), ctypes.byref(native)):
                return None
            expected = 0xAA64 if platform.machine().lower() in ("arm64", "aarch64") else 0x8664
            if (process.value or native.value) == expected:
                return "native"
            return "x86" if process.value == 0x014C else None
        wow = W.BOOL()
        k.IsWow64Process.argtypes = [W.HANDLE, ctypes.POINTER(W.BOOL)]
        if not k.IsWow64Process(handle, ctypes.byref(wow)):
            return None
        return "x86" if wow.value else "native"
    finally:
        k.CloseHandle(handle)


class Host32:
    """32-bit hooks, installed by focus_guard_host32.exe: a hook DLL can only be set by a process of its own architecture.
    One helper per server; if it dies, Windows removes its hooks and the next call starts a new one."""
    _lock = threading.Lock()
    _proc = None

    @classmethod
    def _ask(cls, line):
        with cls._lock:
            p = cls._proc
            if p is None or p.poll() is not None:
                native = Path(__file__).with_name("native")
                exe, dll = native / "focus_guard_host32.exe", native / "focus_guard_x86.dll"
                if not exe.exists() or not dll.exists():
                    raise RuntimeError("the 32-bit focus guard is missing; this 32-bit app cannot be protected")
                p = subprocess.Popen([str(exe), str(dll)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                     creationflags=0x08000000, bufsize=1)
                if not p.stdout.readline().startswith("ready"):
                    p.kill()
                    raise RuntimeError("the 32-bit focus guard helper did not start")
                cls._proc = p
            p.stdin.write(line + "\n")
            p.stdin.flush()
            return p.stdout.readline().split()

    @classmethod
    def install(cls, tid, probe=False):
        out = cls._ask(f"{'p' if probe else 'i'} {tid}")
        h = int(out[0]) if out and out[0].isdigit() else 0
        if not h:
            ctypes.set_last_error(int(out[1]) if len(out) > 1 and out[1].isdigit() else 87)
        return h

    @classmethod
    def remove(cls, hook):
        try:
            return cls._ask(f"r {int(hook)}") == ["1"]
        except Exception:
            return False


class _Backend:
    """install / probe / remove a thread hook for one architecture."""
    def __init__(self, arch, dll):
        self.arch, self.dll = arch, dll

    def install(self, tid):
        return Host32.install(tid) if self.arch == "x86" else self.dll.pc_focus_install(tid)

    def probe(self, tid):
        return Host32.install(tid, probe=True) if self.arch == "x86" else self.dll.pc_focus_probe_install(tid)

    def remove(self, hook):
        return Host32.remove(hook) if self.arch == "x86" else self.dll.pc_focus_remove(hook)


def compatible_process(pid):
    """A thread hook must match the target's architecture: native apps directly, 32-bit apps through Host32."""
    return process_arch(pid) in ("native", "x86")


_RESTORE = {}


def publish_restore(hwnd):
    """Tell the native guard (inside the apps) which window is the user's: if an app empties the foreground, the guard gives it back to
    this window from inside the app (Windows lets that app set the foreground; PC-Control's own process often may not)."""
    if not hwnd:
        return
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileMappingW.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD, W.DWORD, W.DWORD, W.LPCWSTR]
    k.CreateFileMappingW.restype = W.HANDLE
    k.MapViewOfFile.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, ctypes.c_size_t]
    k.MapViewOfFile.restype = ctypes.c_void_p
    k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    if "map" not in _RESTORE:
        m = k.CreateFileMappingW(W.HANDLE(-1), None, 0x04, 0, 8, "Local\\PC-Control.FocusRestore")      # PAGE_READWRITE
        if not m:
            return
        _RESTORE["map"] = m                                   # kept open for the server's life: the name stays valid
    view = k.MapViewOfFile(_RESTORE["map"], 0x0002, 0, 0, 8)                                                # FILE_MAP_WRITE
    if view:
        ctypes.c_void_p.from_address(view).value = hwnd
        k.UnmapViewOfFile(view)


class FocusGuard:
    def __init__(self, exe):
        self.exe = exe
        self.dll = library()
        self.pids, self.hooks, self.errors = set(), {}, {}
        self.backends = {"native": _Backend("native", self.dll), "x86": _Backend("x86", self.dll)}
        self.arch = {}                                               # tid -> architecture of its process
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.compatible = set()
        self.processes = []
        self.started_tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        self.fg0 = ctypes.windll.user32.GetForegroundWindow()       # the user's window when the guard started
        self.handed_back = 0
        try:
            publish_restore(self.fg0)
        except Exception:
            pass

    def attach(self, tid, arch=None):
        with self.lock:
            if self.stop.is_set():
                return False
            if tid in self.hooks:
                return True
            arch = arch or self.arch.get(tid, "native")
            self.arch[tid] = arch
            try:
                hook = self.backends[arch].install(tid)
            except Exception:
                hook = 0
            if hook:
                self.hooks[tid] = hook
                self.errors.pop(tid, None)
                return True
            self.errors[tid] = ctypes.get_last_error()
            return False

    def refresh(self):
        named, threads = snapshot(self.exe)
        # Retain actual process handles for our launches, rather than following
        # a dead PID if Windows later reuses its numeric identifier.
        self.pids = named | {p.pid for p in self.processes if p.poll() is None}
        self.compatible.intersection_update(self.pids)
        for pid in list(self.pids):
            if pid not in self.compatible:
                if not compatible_process(pid):
                    continue
                self.compatible.add(pid)
            tids = threads.get(pid, [])
            arch = None if any(t in self.arch for t in tids) else process_arch(pid)
            for tid in tids:
                self.attach(tid, arch or self.arch.get(tid))

    def start(self):
        if self.stop.is_set():
            raise RuntimeError("cannot restart a closed focus guard")
        try:
            self.refresh()
        except Exception:
            self.close()
            raise
        def monitor():
            while not self.stop.wait(0.005):
                self.hand_back()
                try:
                    self.refresh()
                except OSError:
                    continue
        self.thread = threading.Thread(target=monitor, name="pc-control-focus-guard", daemon=True)
        self.thread.start()
        return self

    def launch(self, command, startupinfo, cwd=None, creationflags=0, env=None):
        """Start suspended, install on its initial thread, then resume. Never run an unguarded failed launch."""
        if self.stop.is_set():
            raise RuntimeError("cannot launch with a closed focus guard")
        p = subprocess.Popen(command, startupinfo=startupinfo, cwd=cwd, creationflags=creationflags | 0x00000004, env=env)
        self.processes.append(p)
        try:
            self.pids.add(p.pid)
            arch = process_arch(p.pid)
            if arch not in ("native", "x86"):
                raise RuntimeError("the app's architecture cannot be protected by this Python focus guard; launch stopped")
            _, threads = snapshot(self.exe)
            tids = threads.get(p.pid, [])
            if not tids:
                raise RuntimeError("the suspended process has no initial thread")
            for tid in tids:
                self.attach(tid, arch)
            k = kernel()
            for tid in tids:
                h = k.OpenThread(2, False, tid)
                if not h:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    if k.ResumeThread(h) == 0xFFFFFFFF:
                        raise ctypes.WinError(ctypes.get_last_error())
                finally:
                    k.CloseHandle(h)
            # Windows rejects a hook until the target thread has initialized its
            # GUI queue (ERROR_INVALID_PARAMETER). Retry during GUI initialization;
            # callbacks still veto activation before the operation, not afterwards.
            deadline = time.monotonic() + 3
            while not all(tid in self.hooks for tid in tids):
                if p.poll() is not None:
                    break  # app execution aliases hand off to another named process
                for tid in tids:
                    self.attach(tid)
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"the app's initial thread could not be guarded: {self.errors}")
                time.sleep(0.001)
            return p
        except Exception:
            p.terminate()
            p.wait(timeout=5)
            raise

    def hand_back(self):
        """An app that calls SetForegroundWindow while starting (Java does) EMPTIES the foreground first; the guard then refuses its
        activation, and the user would be left with no window receiving their keys. While the foreground is empty, the window the
        user had gets it back. Never anything else: not while someone owns the foreground, not another window, not if the user's
        window is gone or hidden, not while they hold a mouse button (they may be choosing a window themselves)."""
        u = ctypes.windll.user32
        try:
            if u.GetForegroundWindow() or not self.fg0 or not u.IsWindow(self.fg0) or not u.IsWindowVisible(self.fg0):
                return False
            if any(u.GetAsyncKeyState(vk) & 0x8000 for vk in (0x01, 0x02, 0x04)):
                return False
            time.sleep(0.015)                                        # a real switch in progress fills the foreground by itself
            if u.GetForegroundWindow():
                return False
            if u.SetForegroundWindow(self.fg0):
                self.handed_back += 1
                return True
        except Exception:
            pass
        return False

    def denied(self, hwnd):
        """True if the native guard refused an activation of this window since the guard started."""
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetPropW.argtypes = [W.HWND, W.LPCWSTR]
        u.GetPropW.restype = W.HANDLE
        try:
            tick = u.GetPropW(hwnd, "PC-Control.Denied")
        except Exception:
            return False
        return bool(tick and ((tick - self.started_tick) & 0xFFFFFFFF) < 0x80000000)

    def close(self):
        self.hand_back()
        self.stop.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(2)
        with self.lock:
            for tid, hook in self.hooks.items():
                self.backends[self.arch.get(tid, "native")].remove(hook)
            self.hooks.clear()

    def user_selected(self, hwnd):
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetPropW.argtypes = [W.HWND, W.LPCWSTR]
        u.GetPropW.restype = W.HANDLE
        tick = u.GetPropW(hwnd, "PC-Control.UserActivation")
        return bool(tick and ((tick - self.started_tick) & 0xFFFFFFFF) < 0x80000000)

    def verify_window(self, hwnd, timeout=1.0):
        """A kernel hook handle alone does not prove the DLL ran in the target.

        A short-lived message hook acknowledges only our tagged WM_NULL heartbeat,
        not keyboard/mouse data. It is removed as soon as the DLL is confirmed.
        """
        import win32gui
        import win32process
        tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        arch = process_arch(pid)
        if arch not in ("native", "x86") or not self.attach(tid, arch):
            raise RuntimeError("cannot install this window's focus guard; action not executed")
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetPropW.argtypes = [W.HWND, W.LPCWSTR]
        u.GetPropW.restype = W.HANDLE
        u.RemovePropW.argtypes = [W.HWND, W.LPCWSTR]
        u.RemovePropW.restype = W.HANDLE
        backend = self.backends[arch]
        probe = backend.probe(tid)
        if not probe:
            raise RuntimeError("cannot verify this window's in-process focus guard; action not executed")
        try:
            u.RemovePropW(hwnd, "PC-Control.FocusGuardReady")
            nonce = secrets.randbelow(0x7FFFFFFE) + 1
            win32gui.PostMessage(hwnd, 0, 0x50434342, nonce)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if u.GetPropW(hwnd, "PC-Control.FocusGuardReady") == nonce:
                    return
                time.sleep(0.005)
            raise RuntimeError("the app did not acknowledge the in-process focus guard; action not executed")
        finally:
            backend.remove(probe)

    @classmethod
    def for_window(cls, hwnd):
        import win32process
        guard = cls("")
        tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        arch = process_arch(pid)
        if arch not in ("native", "x86"):
            raise RuntimeError("cannot protect this app's focus across different process architectures; action not executed")
        if not guard.attach(tid, arch):
            raise RuntimeError(f"cannot protect this app's focus (thread {tid}, error {guard.errors.get(tid)}); action not executed")
        guard.pids.add(pid)
        try:
            guard.verify_window(hwnd)
        except Exception:
            guard.close()
            raise
        def monitor():                                               # an action can open a window that grabs the foreground too
            while not guard.stop.wait(0.005):
                guard.hand_back()
        guard.thread = threading.Thread(target=monitor, name="pc-control-focus-handback", daemon=True)
        guard.thread.start()
        return guard

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
