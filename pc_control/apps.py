"""Turn what the model writes in open_app ("winword", "word", "excel.exe /e", "chrome https://...", "Calculadora") into a command Windows can
start. Real sessions failed with FileNotFoundError for programs that ARE installed but not on PATH (Office, Chrome, WordPad's absence...).

Order: an existing path -> PATH (with PATHEXT) -> App Paths in the registry (how Win+R finds winword) -> a Start-menu shortcut with that
name -> a Store app with that name (started through shell:AppsFolder). Nothing is started here.
"""
import glob, os, shlex, shutil, winreg

ALIASES = {"word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook", "paint": "mspaint",
           "calculadora": "calc", "calculator": "calc", "bloc de notas": "notepad", "edge": "msedge", "explorador": "explorer",
           "explorador de archivos": "explorer", "file explorer": "explorer", "chrome": "chrome", "firefox": "firefox",
           "visual studio code": "code", "vscode": "code", "vs code": "code", "terminal": "wt", "configuracion": "ms-settings:",
           "configuración": "ms-settings:", "settings": "ms-settings:"}
URI_SCHEMES = ("ms-settings:", "ms-", "shell:", "http://", "https://", "mailto:")


def split(command):
    """(program, rest of the command line) as written; quotes around the program are removed."""
    command = command.strip()
    if not command.startswith('"'):                  # an unquoted path with spaces (C:/Program Files/App/app.exe arg): the longest
        low = command.lower()                        # prefix ending in .exe that exists is the program
        for i in [j + 4 for j in range(len(low)) if low.startswith(".exe", j)][::-1]:
            if (i == len(command) or command[i] == " ") and " " in command[:i] and os.path.isfile(command[:i]):
                return command[:i], command[i:].strip()
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split(" ", 1)
    if not parts:
        return "", ""
    exe = parts[0].strip('"')
    rest = command[len(parts[0]):].strip()
    return exe, rest


def _app_paths(name):
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (0, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}", 0, winreg.KEY_READ | view) as k:
                    path = os.path.expandvars(winreg.QueryValueEx(k, "")[0]).strip('"')
                    if os.path.isfile(path):
                        return path
            except OSError:
                continue
    return None


def start_menu_shortcuts():
    dirs = []
    try:                                                     # the Start menu folders as the shell knows them (all users, this user)
        from win32com.shell import shell, shellcon
        dirs = [shell.SHGetFolderPath(0, shellcon.CSIDL_COMMON_PROGRAMS, None, 0), shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAMS, None, 0)]
    except Exception:
        pass
    out = {}
    for d in dirs:
        for f in glob.glob(os.path.join(d, "**", "*.lnk"), recursive=True):
            out.setdefault(os.path.splitext(os.path.basename(f))[0].casefold(), f)
    return out


def _shortcut_target(lnk):
    try:
        import win32com.client
        sc = win32com.client.Dispatch("WScript.Shell").CreateShortcut(lnk)
        tgt = sc.TargetPath
        if tgt and os.path.isfile(tgt):
            return tgt, sc.Arguments or ""
    except Exception:
        pass
    return None


def store_apps():
    """Store / packaged apps: display name -> AppUserModelID (what the Start menu shows), read from shell:AppsFolder."""
    try:
        import win32com.client
        folder = win32com.client.Dispatch("Shell.Application").NameSpace("shell:::{4234d49b-0245-4df3-b780-3893943456e1}")
        return {it.Name.casefold(): it.Path for it in folder.Items() if it.Path and "!" in it.Path}
    except Exception:
        return {}


def resolve(command):
    """(command to run, how it was found, exe name for the focus guard). exe name None if nothing was found."""
    exe, rest = split(command)
    low = exe.casefold()
    if low.startswith(URI_SCHEMES) and not os.path.isfile(os.path.expandvars(exe)):
        return f'explorer.exe "{command.strip()}"', "URI through the shell", "explorer"      # a URI is not a program: the shell opens it
    if os.path.isfile(os.path.expandvars(exe)):
        return command, "as written", os.path.splitext(os.path.basename(exe))[0].lower()
    alias = ALIASES.get(command.strip().casefold()) or ALIASES.get(low)
    if alias and alias.endswith(":"):
        return f'explorer.exe "{alias}"', f"settings page {alias}", "explorer"
    names = [low, alias] if alias else [low]
    for n in names:
        for cand in (n, n if n.endswith(".exe") else n + ".exe"):
            w = shutil.which(cand)
            if w:
                return (f'"{w}" {rest}'.strip() if n != low or " " in w else command), "PATH", os.path.splitext(os.path.basename(w))[0].lower()
            ap = _app_paths(cand)
            if ap:
                return f'"{ap}" {rest}'.strip(), "App Paths (registry)", os.path.splitext(os.path.basename(ap))[0].lower()
    full = command.strip().casefold()
    lnks = start_menu_shortcuts()
    for key in (full, low, alias or ""):
        if key and key in lnks:
            t = _shortcut_target(lnks[key])
            if t:
                return f'"{t[0]}" {t[1]} {rest if key != full else ""}'.strip(), "Start menu shortcut", os.path.splitext(os.path.basename(t[0]))[0].lower()
    apps = store_apps()
    for key in (full, low):
        if key in apps:
            return f'explorer.exe "shell:AppsFolder\\{apps[key]}"', "Store app", "explorer"
    # a word of the name: "virtualbox" -> "Oracle VirtualBox", "blender" -> "Blender 4.2" (only when exactly ONE program matches)
    word = low.removesuffix(".exe")
    if len(word) >= 3:
        lnk_hits = [k for k in lnks if word in k.replace("-", " ").split() or k.startswith(word)]
        if len(lnk_hits) == 1:
            t = _shortcut_target(lnks[lnk_hits[0]])
            if t:
                return f'"{t[0]}" {t[1]} {rest}'.strip(), f"Start menu shortcut «{lnk_hits[0]}»", os.path.splitext(os.path.basename(t[0]))[0].lower()
        app_hits = [k for k in apps if word in k.split() or k.startswith(word)]
        if not lnk_hits and len(app_hits) == 1:
            return f'explorer.exe "shell:AppsFolder\\{apps[app_hits[0]]}"', f"Store app «{app_hits[0]}»", "explorer"
    return command, "", None


def suggestions(command, n=6):
    """Installed programs whose name resembles what was asked (for a clear 'not found' answer)."""
    import difflib
    exe, _ = split(command)
    names = list(start_menu_shortcuts()) + list(store_apps())
    return difflib.get_close_matches(exe.casefold().removesuffix(".exe"), names, n=n, cutoff=0.5)
CHROMIUM_FILES = ("icudtl.dat", "libcef.dll", "chrome_elf.dll", "v8_context_snapshot.bin", "chrome_100_percent.pak")


def exec_alias_target(path):
    """The real program behind a Store "app execution alias" (WindowsApps\\Spotify.exe is a 0-byte link to the package's exe), or None.
    Read from the alias' reparse point (IO_REPARSE_TAG_APPEXECLINK): version, then package id, app id and the target path."""
    import ctypes, ctypes.wintypes as W
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = W.HANDLE
    k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
    k.DeviceIoControl.argtypes = [W.HANDLE, W.DWORD, ctypes.c_void_p, W.DWORD, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p]
    h = k.CreateFileW(path, 0, 7, None, 3, 0x00200000 | 0x02000000, None)   # OPEN_EXISTING, OPEN_REPARSE_POINT | BACKUP_SEMANTICS
    if not h or h == W.HANDLE(-1).value:
        return None
    try:
        buf = ctypes.create_string_buffer(16 * 1024)
        n = W.DWORD()
        if not k.DeviceIoControl(h, 0x000900A8, None, 0, buf, len(buf), ctypes.byref(n), None):    # FSCTL_GET_REPARSE_POINT
            return None
        raw = buf.raw[:n.value]
        if int.from_bytes(raw[:4], "little") != 0x8000001B:                                         # IO_REPARSE_TAG_APPEXECLINK
            return None
        strings = raw[12:].decode("utf-16-le", "ignore").split("\x00")
        return strings[2] if len(strings) > 2 and strings[2].lower().endswith(".exe") else None
    finally:
        k.CloseHandle(h)


def chromium_like(exe):
    """A Chromium-based program (Chrome/Edge family, CEF apps like Spotify, Electron apps like Discord or VS Code): it ships Chromium's
    own files next to its exe. Those hide their page from UI Automation unless started with accessibility on, and stop painting when
    covered unless told not to."""
    import os
    exe = exec_alias_target(exe) or exe
    d = os.path.dirname(exe)
    if not d or not os.path.isdir(d):
        return False
    dirs = [d]
    try:                                               # Chrome and newer VS Code keep them in a version folder next to the exe
        dirs += [e.path for e in os.scandir(d) if e.is_dir() and any(ch.isdigit() for ch in e.name)][:6]
    except OSError:
        pass
    return any(os.path.exists(os.path.join(x, f)) for x in dirs for f in CHROMIUM_FILES)
