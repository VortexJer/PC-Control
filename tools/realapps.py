"""Real third-party apps for testing PC-Control on many UI frameworks, installed PORTABLE in one folder so they can all be removed.

  python tools/realapps.py install [name ...]   download + unpack (default: all); records each one in the manifest
  python tools/realapps.py list                 what is installed, its framework and its executable
  python tools/realapps.py path <name>          the executable of one app (for tests)
  python tools/realapps.py uninstall            close them and delete EVERYTHING this script installed (manifest-driven)

Everything lives under %USERPROFILE%\\.pc-control-testenv\\realapps (nothing goes to Program Files, the registry or PATH).
The uninstall also removes the per-user settings folders these portable apps may create (listed per app below).
The WSL distro used for Remote Desktop is separate: `python tools/rdp_testenv.py --remove`.
"""
import glob, json, os, shutil, subprocess, sys, urllib.request, zipfile

BASE = os.path.join(os.path.expanduser("~"), ".pc-control-testenv", "realapps")
MANIFEST = os.path.join(BASE, "manifest.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) pc-control-tests"}
ROAMING, LOCAL = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")

# name: (framework, url, how, executable glob inside its folder, extra per-user folders the app creates)
APPS = {
    "notepadpp": ("Win32 + Scintilla", "https://github.com/notepad-plus-plus/notepad-plus-plus/releases/download/v8.9.9.1/npp.8.9.9.1.portable.zip",
                  "zip", "notepad++.exe", []),
    "sumatra": ("custom-drawn Win32", "https://files.sumatrapdfreader.org/software/sumatrapdf/rel/3.5.2/SumatraPDF-3.5.2-64.zip",
                "zip", "SumatraPDF*.exe", [os.path.join(LOCAL, "SumatraPDF")]),
    "putty": ("Win32 terminal (custom-drawn)", "https://the.earth.li/~sgtatham/putty/0.85/w64/putty.exe", "exe", "putty.exe", []),
    "keepass": ("WinForms (.NET)", "https://downloads.sourceforge.net/project/keepass/KeePass%202.x/2.62/KeePass-2.62.zip",
                "zip", "KeePass.exe", [os.path.join(ROAMING, "KeePass")]),
    "vlc": ("Qt 5", "https://get.videolan.org/vlc/3.0.21/win64/vlc-3.0.21-win64.zip", "zip", "*/vlc.exe", [os.path.join(ROAMING, "vlc")]),
    "jmeter": ("Java Swing", "https://dlcdn.apache.org/jmeter/binaries/apache-jmeter-5.6.3.zip", "zip", "*/bin/ApacheJMeter.jar", []),
    "sqldeveloper": ("Java Swing (Oracle)", "https://download.oracle.com/otn_software/java/sqldeveloper/sqldeveloper-24.3.1.347.1826-no-jre.zip",
                     "zip", "sqldeveloper/sqldeveloper/bin/sqldeveloper64W.exe", [os.path.join(ROAMING, "sqldeveloper")]),
    "firefox": ("Gecko (Firefox)", "https://download.mozilla.org/?product=firefox-latest-ssl&os=win64&lang=es-ES", "7z-sfx", "core/firefox.exe",
                []),
    # Chromium family (asked for on Reddit: Brave; Electron like VS Code / Discord). Both portable, nothing installed system-wide.
    "brave": ("Chromium (Brave)", "github-latest:brave/brave-browser:brave-v*-win32-x64.zip", "zip", "brave.exe",
              [os.path.join(LOCAL, "BraveSoftware")]),
    "vscode": ("Electron (VS Code)", "https://update.code.visualstudio.com/latest/win32-x64-archive/stable", "zip", "Code.exe", []),
}


def resolve(url):
    """github-latest:owner/repo:asset-glob -> the download URL of the newest release asset matching the glob."""
    if not url.startswith("github-latest:"):
        return url
    import fnmatch
    _, repo, pattern = url.split(":", 2)
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases?per_page=15", headers=UA)
    for rel in json.load(urllib.request.urlopen(req, timeout=60)):
        if rel.get("prerelease") or rel.get("draft"):
            continue
        for a in rel.get("assets", []):
            if fnmatch.fnmatch(a["name"], pattern):
                return a["browser_download_url"]
    raise RuntimeError(f"no release asset {pattern} in {repo}")


def load():
    try:
        return json.load(open(MANIFEST, encoding="utf-8"))
    except Exception:
        return {}


def save(m):
    os.makedirs(BASE, exist_ok=True)
    json.dump(m, open(MANIFEST, "w", encoding="utf-8"), indent=1)


def fetch(url, dest):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)


def seven_zip():
    for p in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe", shutil.which("7z") or ""):
        if p and os.path.exists(p):
            return p
    return None


def install(name):
    fw, url, how, exe, extra = APPS[name]
    m = load()
    folder = os.path.join(BASE, name)
    m[name] = {"framework": fw, "folder": folder, "extra": extra, "url": url, "state": "installing"}
    save(m)                                              # recorded BEFORE downloading: a cut-off install is still uninstalled
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(BASE, name + ".download")
    url = resolve(url)
    print(f"{name}: downloading {url}")
    fetch(url, tmp)
    if how == "zip":
        zipfile.ZipFile(tmp).extractall(folder)
    elif how == "exe":
        shutil.copy(tmp, os.path.join(folder, os.path.basename(exe)))
    elif how == "7z-sfx":
        z = seven_zip()
        if not z:
            raise RuntimeError("7-Zip is needed to unpack this installer without running it")
        subprocess.run([z, "x", "-y", f"-o{folder}", tmp], check=True, capture_output=True)
    os.remove(tmp)
    if name == "notepadpp":                              # portable mode: settings stay in its folder
        open(os.path.join(folder, "doLocalConf.xml"), "a").close()
    hits = glob.glob(os.path.join(folder, exe))
    m[name].update(state="installed", exe=hits[0] if hits else None)
    save(m)
    print(f"{name}: {fw} -> {m[name]['exe']}")


def close():
    """Close every running copy of the test apps (only processes started from this folder; never the user's own apps)."""
    n = 0
    for pid, exe in _processes():
        if exe.lower().startswith(BASE.lower()):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True); n += 1
    print(f"closed {n} test app process(es)")


def _processes():
    """(pid, executable path) of every process this user can query."""
    import win32api, win32con, win32process
    out = []
    for pid in win32process.EnumProcesses():
        try:
            h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            try:
                out.append((pid, win32process.GetModuleFileNameEx(h, 0)))
            finally:
                win32api.CloseHandle(h)
        except Exception:
            pass
    return out


def uninstall():
    m = load()
    exes = {os.path.basename(v.get("exe") or "").lower() for v in m.values() if v.get("exe")}
    exes |= {"java.exe", "javaw.exe"} if any(k in m for k in ("jmeter", "sqldeveloper")) else set()
    close()
    for pid, exe in _processes():                       # Java apps run in java.exe from another folder: closed by their window title
        if os.path.basename(exe).lower() in exes and not exe.lower().startswith(BASE.lower()):
            pass                                        # never kill a java.exe that may be the user's own
    for name, v in m.items():
        for d in [v.get("folder")] + list(v.get("extra") or []):
            if d and os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        print(f"removed {name}")
    shutil.rmtree(BASE, ignore_errors=True)
    print("all real test apps removed" if not os.path.exists(BASE) else f"could not remove everything in {BASE}")


def main(argv):
    cmd = argv[0] if argv else "list"
    if cmd == "install":
        bad = []
        for n in argv[1:] or list(APPS):
            if load().get(n, {}).get("state") == "installed":
                print(f"{n}: already installed"); continue
            try:
                install(n)
            except Exception as e:
                bad.append(n); print(f"{n}: FAILED {e.__class__.__name__}: {e}")
        return 1 if bad else 0
    if cmd == "list":
        for n, v in load().items():
            print(f"{n:13} {v.get('state'):10} {v.get('framework'):28} {v.get('exe')}")
        return 0
    if cmd == "path":
        print(load().get(argv[1], {}).get("exe") or ""); return 0
    if cmd == "uninstall":
        uninstall(); return 0
    if cmd == "close":
        close(); return 0
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
