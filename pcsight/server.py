"""pcsight MCP server: lets an AI look at and operate Windows apps without stealing focus or moving the mouse.

Safety model
  * look()      works on any window except the deny-list (password managers, terminals, ...).
  * click/type/key/open_app only work on apps you explicitly allow:
        env PCSIGHT_ALLOW="notepad,winword"     or     ~/.pcsight/allow.txt (one process name per line)
  * Create the file ~/.pcsight/PAUSE (or set PCSIGHT_PAUSE=1) to make every tool refuse instantly.
  * Nothing here ever focuses a window, moves your mouse or injects keystrokes; new windows go to the back.
"""
import ctypes, ctypes.wintypes as wt, os, shlex, time
import win32gui, win32process
from mcp.server.fastmcp import FastMCP, Image
from . import core

HOME = os.path.join(os.path.expanduser("~"), ".pcsight")
DENY = {"keepass", "keepassxc", "bitwarden", "1password", "lastpass", "dashlane", "windowsterminal", "cmd",
        "powershell", "pwsh", "conhost", "mmc", "regedit", "taskmgr", "claude"}
mcp = FastMCP("pcsight")


def _proc(hwnd):
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520); n = wt.DWORD(520)
        k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        return os.path.splitext(os.path.basename(buf.value))[0].lower()
    finally:
        k.CloseHandle(h)


def _allowed():
    names = {x.strip().lower() for x in os.environ.get("PCSIGHT_ALLOW", "").split(",") if x.strip()}
    f = os.path.join(HOME, "allow.txt")
    if os.path.exists(f):
        names |= {l.strip().lower() for l in open(f, encoding="utf-8") if l.strip() and not l.startswith("#")}
    return names


def _paused():
    if os.environ.get("PCSIGHT_PAUSE") or os.path.exists(os.path.join(HOME, "PAUSE")):
        raise PermissionError("pcsight en pausa (PAUSE).")


def _gate(window, act):
    _paused()
    hwnd = core.find_window(window); proc = _proc(hwnd)
    if proc in DENY:
        raise PermissionError(f"'{proc}' esta en la lista de bloqueo.")
    if act and proc not in _allowed():
        raise PermissionError(f"actuar sobre '{proc}' no esta permitido. El usuario debe anadirlo a PCSIGHT_ALLOW o ~/.pcsight/allow.txt.")
    return hwnd


def _after(hwnd, result):
    """Respuesta barata tras actuar: resultado + solo lo que cambio. Los dialogos nuevos van al fondo."""
    time.sleep(0.25)
    core.tuck_process(win32process.GetWindowThreadProcessId(hwnd)[1])
    return f"{result} | cambio: {core.changes(str(hwnd))}"


@mcp.tool()
def windows() -> str:
    """List open windows as 'hwnd | process | title'."""
    _paused()
    out = []
    for h in core.top_windows():
        p = _proc(h)
        if p not in DENY:
            out.append(f"{h} | {p} | {win32gui.GetWindowText(h)[:50]}")
    return "\n".join(out)


@mcp.tool()
def look(window: str, mode: str = "auto"):
    """See a window WITHOUT focusing it. Picks the cheapest of: UI-tree text, OCR text, or an image with numbered marks. Elements are [id:name]; use the id with click/type. window = title substring or hwnd. mode: auto|image|uia."""
    hwnd = _gate(window, act=False)
    r = core.look(str(hwnd), mode)
    head = f"{r['mode']} {r.get('tokens', 0)}tok | {r['why']}" + (f" | blind {r['blind']}" if "blind" in r else "")
    if r["mode"] == "image":
        return [head + f" | image {r['image_size']}: numbered marks = ids; x,y = image pixels", Image(path=r["image"])]
    return head + "\n" + (r.get("text") or "")


@mcp.tool()
def click(window: str, target: str, right: bool = False, double: bool = False) -> str:
    """Click an element id from look(), or 'x,y' in the pixels of the last image. Does not move the mouse or change focus. Returns what changed."""
    hwnd = _gate(window, act=True)
    t = tuple(int(float(v)) for v in target.split(",")) if "," in target else int(target)
    return _after(hwnd, core.click(str(hwnd), t, right, double))


@mcp.tool()
def type(window: str, target: str, text: str, replace: bool = False) -> str:
    """Type text into the element id (from look()). Appends, or replaces with replace=true. Id is required: no blind typing. Returns what changed."""
    hwnd = _gate(window, act=True)
    return _after(hwnd, core.type_text(str(hwnd), text, int(target), replace))


@mcp.tool()
def key(window: str, name: str) -> str:
    """Press a single key (enter, tab, esc, up, down, left, right, backspace, delete, home, end, pgup, pgdn, space). Shortcuts are unsupported."""
    hwnd = _gate(window, act=True)
    return _after(hwnd, core.key(str(hwnd), name))


@mcp.tool()
def open_app(command: str) -> str:
    """Launch an allowed app minimized, un-focused and at the back of every window. Then use windows()/look()."""
    _paused()
    exe = os.path.splitext(os.path.basename(shlex.split(command, posix=False)[0].strip('"')))[0].lower()
    if exe in DENY or exe not in _allowed():
        raise PermissionError(f"abrir '{exe}' no esta permitido (PCSIGHT_ALLOW o ~/.pcsight/allow.txt).")
    r = core.open_app(command)
    ws = "; ".join(f"{w['hwnd']} {w['title']}" for w in r["windows"]) or r["note"]
    return f"pid {r['pid']} | {ws} | foco_intacto={r['foco_intacto']}"


@mcp.tool()
def reveal(window: str) -> str:
    """Bring a window that pcsight parked off-screen back to where it was (without focusing it). Only on the user's request."""
    hwnd = _gate(window, act=True)
    return "ok (vuelve a su sitio)" if core.unpark(hwnd) else "esa ventana no estaba aparcada por pcsight"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
