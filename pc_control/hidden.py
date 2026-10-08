"""Escritorio oculto: las apps que abre pcsight viven en un escritorio de Windows que el usuario nunca ve.

Una ventana de otro escritorio no puede aparecer en tu pantalla, ni parpadear, ni robar el foco, ni quedar
por delante o por detras de nada. Es el mismo mecanismo que usan las herramientas de acceso remoto.

Todo lo que toque esas ventanas debe ejecutarse en el hilo dedicado (`DESK.run`), que esta atado a ese escritorio.
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
        self.name = f"pcsight_{os.getpid()}"
        self.h = None
        self.pool = None
        self.procs = {}                 # pid -> handle del proceso
        atexit.register(self.close)

    # -- ciclo de vida --
    def ensure(self):
        if self.h:
            return
        h = _u.CreateDesktopW(self.name, None, None, 0, 0x10000000, None)      # GENERIC_ALL
        if not h:
            raise OSError(f"no se pudo crear el escritorio oculto (error {ctypes.get_last_error()})")
        self.h = h
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="pcsight-hidden", initializer=self._init_thread)

    def _init_thread(self):
        if not _u.SetThreadDesktop(self.h):
            raise OSError(f"SetThreadDesktop fallo (error {ctypes.get_last_error()})")
        self._uia = auto.UIAutomationInitializerInThread()
        self._uia.__enter__()

    def run(self, fn, timeout=120):
        """Ejecuta fn en el hilo atado al escritorio oculto (aqui las ventanas y UIA son visibles)."""
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

    # -- procesos y ventanas --
    def launch(self, command):
        self.ensure()
        si = _STARTUPINFOW(); si.cb = ctypes.sizeof(si); si.lpDesktop = self.name
        pi = _PROCESS_INFORMATION()
        buf = ctypes.create_unicode_buffer(command)
        if not _k.CreateProcessW(None, buf, None, None, False, 0x08000000, None, None, ctypes.byref(si), ctypes.byref(pi)):
            raise OSError(f"no se pudo lanzar la app (error {ctypes.get_last_error()})")
        _k.CloseHandle(pi.hThread)
        self.procs[pi.dwProcessId] = pi.hProcess
        return pi.dwProcessId

    def windows(self):
        """[(hwnd, titulo, pid)] de las ventanas con titulo del escritorio oculto."""
        if not self.h:
            return []
        def enum():
            out = []
            win32gui.EnumWindows(lambda h, _: out.append((h, win32gui.GetWindowText(h), win32process.GetWindowThreadProcessId(h)[1]))
                                 if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) else None, None)
            return out
        return self.run(enum)

    def owns(self, window, main_has):
        """True si la consulta (hwnd o titulo) se refiere a una ventana del escritorio oculto.
        Con un titulo que tambien existe en tu escritorio, gana el tuyo (main_has=True)."""
        if not self.h:
            return False
        w = str(window)
        ws = self.windows()
        if w.isdigit():
            return int(w) in {h for h, _, _ in ws}
        return (not main_has) and any(w.lower() in t.lower() for _, t, _ in ws)

    def open(self, command, wait_s=10.0):
        """Lanza la app en el escritorio oculto y espera a su primera ventana con titulo."""
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
                "note": "" if found else "ninguna ventana con titulo (la app puede ser de la Tienda, que no admite otro escritorio, o tardar mas)"}


DESK = HiddenDesktop()
