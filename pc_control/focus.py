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


def compatible_process(pid):
    """A thread hook must have the same machine architecture as the target."""
    k = kernel()
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = W.HANDLE
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        if hasattr(k, "IsWow64Process2"):
            k.IsWow64Process2.argtypes = [W.HANDLE, ctypes.POINTER(W.WORD), ctypes.POINTER(W.WORD)]
            process, native = W.WORD(), W.WORD()
            if not k.IsWow64Process2(handle, ctypes.byref(process), ctypes.byref(native)):
                return False
            expected = 0xAA64 if platform.machine().lower() in ("arm64", "aarch64") else 0x8664
            return (process.value or native.value) == expected
        wow = W.BOOL()
        k.IsWow64Process.argtypes = [W.HANDLE, ctypes.POINTER(W.BOOL)]
        return bool(k.IsWow64Process(handle, ctypes.byref(wow))) and not wow.value
    finally:
        k.CloseHandle(handle)


class FocusGuard:
    def __init__(self, exe):
        self.exe = exe
        self.dll = library()
        self.pids, self.hooks, self.errors = set(), {}, {}
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.compatible = set()
        self.processes = []
        self.started_tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF

    def attach(self, tid):
        with self.lock:
            if self.stop.is_set():
                return False
            if tid in self.hooks:
                return True
            hook = self.dll.pc_focus_install(tid)
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
            for tid in threads.get(pid, []):
                self.attach(tid)

    def start(self):
        if self.stop.is_set():
            raise RuntimeError("cannot restart a closed focus guard")
        try:
            self.refresh()
        except Exception:
            self.close()
            raise
        def monitor():
            while not self.stop.wait(0.01):
                try:
                    self.refresh()
                except OSError:
                    continue
        self.thread = threading.Thread(target=monitor, name="pc-control-focus-guard", daemon=True)
        self.thread.start()
        return self

    def launch(self, command, startupinfo, cwd=None, creationflags=0):
        """Start suspended, install on its initial thread, then resume. Never run an unguarded failed launch."""
        if self.stop.is_set():
            raise RuntimeError("cannot launch with a closed focus guard")
        p = subprocess.Popen(command, startupinfo=startupinfo, cwd=cwd, creationflags=creationflags | 0x00000004)
        self.processes.append(p)
        try:
            self.pids.add(p.pid)
            if not compatible_process(p.pid):
                raise RuntimeError("the app's architecture cannot be protected by this Python focus guard; launch stopped")
            _, threads = snapshot(self.exe)
            tids = threads.get(p.pid, [])
            if not tids:
                raise RuntimeError("the suspended process has no initial thread")
            for tid in tids:
                self.attach(tid)
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

    def close(self):
        self.stop.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(2)
        with self.lock:
            for hook in self.hooks.values():
                self.dll.pc_focus_remove(hook)
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
        if not compatible_process(pid) or not self.attach(tid):
            raise RuntimeError("cannot install this window's focus guard; action not executed")
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetPropW.argtypes = [W.HWND, W.LPCWSTR]
        u.GetPropW.restype = W.HANDLE
        u.RemovePropW.argtypes = [W.HWND, W.LPCWSTR]
        u.RemovePropW.restype = W.HANDLE
        probe = self.dll.pc_focus_probe_install(tid)
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
            self.dll.pc_focus_remove(probe)

    @classmethod
    def for_window(cls, hwnd):
        import win32process
        guard = cls("")
        tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not compatible_process(pid):
            raise RuntimeError("cannot protect this app's focus across different process architectures; action not executed")
        if not guard.attach(tid):
            raise RuntimeError(f"cannot protect this app's focus (thread {tid}, error {guard.errors.get(tid)}); action not executed")
        guard.pids.add(pid)
        try:
            guard.verify_window(hwnd)
        except Exception:
            guard.close()
            raise
        return guard

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
