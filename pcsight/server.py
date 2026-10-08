"""pcsight MCP server: lets an AI look at and operate Windows apps without stealing focus or moving the mouse.

Safety model
  * look()    works on any window except the deny-list (password managers, terminals, ...).
  * click/type/key only work on apps you explicitly allow:
        env PCSIGHT_ALLOW="notepad,winword"     or     ~/.pcsight/allow.txt (one process name per line)
  * Create the file ~/.pcsight/PAUSE (or set PCSIGHT_PAUSE=1) to make every tool refuse instantly.
"""
import ctypes, os, ctypes.wintypes as wt
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


def _gate(window, act):
    if os.environ.get("PCSIGHT_PAUSE") or os.path.exists(os.path.join(HOME, "PAUSE")):
        raise PermissionError("pcsight en pausa (PAUSE).")
    hwnd = core.find_window(window); proc = _proc(hwnd)
    if proc in DENY:
        raise PermissionError(f"'{proc}' esta en la lista de bloqueo.")
    if act and proc not in _allowed():
        raise PermissionError(f"actuar sobre '{proc}' no esta permitido. El usuario debe anadirlo a PCSIGHT_ALLOW o ~/.pcsight/allow.txt.")
    return hwnd


@mcp.tool()
def windows() -> str:
    """List open windows as 'hwnd | process | title'."""
    out = []
    for h, t in core.list_windows():
        p = _proc(h)
        if p not in DENY:
            out.append(f"{h} | {p} | {t[:50]}")
    return "\n".join(out)


@mcp.tool()
def look(window: str, mode: str = "auto"):
    """See a window WITHOUT focusing it. Picks the cheapest of: UI-tree text, OCR text, or an image with numbered marks. Elements are [id:name]; use the id with click/type. window = title substring or hwnd. mode: auto|image|uia."""
    hwnd = _gate(window, act=False)
    r = core.look(str(hwnd), mode)
    head = f"{r['mode']} {r.get('tokens', 0)}tok | {r['why']}" + (f" | blind {r['blind']}" if "blind" in r else "")
    if r["mode"] == "image":
        return [head + f" | image {r['image_size']}: numbered marks = ids; coordinates = image pixels", Image(path=r["image"])]
    return head + "\n" + (r.get("text") or "")


@mcp.tool()
def click(window: str, target: str, right: bool = False, double: bool = False) -> str:
    """Click an element by id from look(), or 'x,y' in the pixels of the last image. Does not move the mouse or change focus."""
    hwnd = _gate(window, act=True)
    t = tuple(int(float(v)) for v in target.split(",")) if "," in target else int(target)
    return core.click(str(hwnd), t, right, double)


@mcp.tool()
def type(window: str, text: str, target: str = "", replace: bool = False) -> str:
    """Type text into an element id from look() (append, or replace=true). Does not change focus."""
    hwnd = _gate(window, act=True)
    return core.type_text(str(hwnd), text, int(target) if target else None, replace)


@mcp.tool()
def key(window: str, name: str) -> str:
    """Press a single key (enter, tab, esc, up, down, left, right, backspace, delete, home, end, pgup, pgdn, space). Shortcuts are unsupported."""
    hwnd = _gate(window, act=True)
    return core.key(str(hwnd), name)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
