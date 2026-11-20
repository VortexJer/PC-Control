"""Read-only WinEvent observer for visible input experiments, including transient focus changes."""
import ctypes
from ctypes import wintypes as W
import threading
import time

CALLBACK = ctypes.WINFUNCTYPE(None, W.HANDLE, W.DWORD, W.HWND, W.LONG, W.LONG, W.DWORD, W.DWORD)


class InputWatch:
    def __init__(self):
        self.events = []
        self.ready, self.stop = threading.Event(), threading.Event()
        self.error = None

    def _run(self):
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.SetWinEventHook.argtypes = [W.DWORD, W.DWORD, W.HMODULE, CALLBACK, W.DWORD, W.DWORD, W.DWORD]
        u.SetWinEventHook.restype = W.HANDLE
        u.UnhookWinEvent.argtypes = [W.HANDLE]
        callback = CALLBACK(lambda hook, event, hwnd, obj, child, tid, ms: self.events.append((event, hwnd, ms)))
        hooks = [u.SetWinEventHook(event, event, None, callback, 0, 0, 0) for event in (0x0003, 0x8005)]
        if not all(hooks):
            self.error = "Could not install read-only foreground/focus event observers"
        self.ready.set()
        message = W.MSG()
        try:
            while not self.stop.is_set():
                if u.PeekMessageW(ctypes.byref(message), 0, 0, 0, 1):
                    u.DispatchMessageW(ctypes.byref(message))
                else:
                    time.sleep(0.001)
        finally:
            for h in hooks:
                if h:
                    u.UnhookWinEvent(h)

    def __enter__(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.ready.wait(3) or self.error:
            raise RuntimeError(self.error or "Input observer did not start")
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(3)
