"""Hook PreToolUse de Claude Code para PC-Control: hace que sea CLAUDE CODE quien le pregunte al usuario.

Se registra con `pc-control install` (matcher mcp__PC-Control__.*). Recibe la llamada por stdin y, si hace falta el permiso del
usuario (ventana protegida, o una accion que el servidor ya pidio confirmar), contesta permissionDecision="ask": Claude Code
muestra su cuadro de permiso normal y el modelo no puede contestarlo. Es ligero a proposito (no importa OCR ni vision).
"""
import ctypes, ctypes.wintypes as wt, json, os, sys
import win32gui, win32process
from . import perm, policy


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


def _find(query):
    q = str(query).strip()
    if q.isdigit() and win32gui.IsWindow(int(q)):
        return int(q)
    found = []
    win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.IsWindowVisible(h) and q.lower() in win32gui.GetWindowText(h).lower() else None, None)
    return found[0] if found else 0


def decide(data):
    """Devuelve el motivo si hay que preguntar al usuario, o None."""
    tool = str(data.get("tool_name", "")).rsplit("__", 1)[-1]
    args = data.get("tool_input") or {}
    if tool in ("windows",) or policy.mode() == "bypass":
        return None
    s = perm.sig(tool, args)
    if tool == "open_app":
        try:
            import shlex
            proc = os.path.splitext(os.path.basename(shlex.split(args.get("command", ""), posix=False)[0].strip('"')))[0].lower()
        except (ValueError, IndexError):
            return None
        title = ""
    else:
        hwnd = _find(args.get("window", ""))
        if not hwnd:
            return None
        proc, title = _proc(hwnd), win32gui.GetWindowText(hwnd)
    cat = policy.blocked_category(proc, title)
    if cat:
        key = f"{cat}|{proc}"
        if perm.approved(key):
            return None
        perm.mark("asked", s)
        return f"Claude quiere usar una ventana PROTEGIDA con PC-Control ({cat}: '{proc}'). Lo permites?"
    if perm.take("pending", s):                      # el servidor pidio confirmar esta accion (p. ej. una accion delicada)
        perm.mark("asked", s)
        return f"PC-Control pide tu permiso para una accion delicada en '{proc}' ({tool}). Lo permites?"
    return None


def main():
    try:
        data = json.load(sys.stdin)
        reason = decide(data)
    except Exception:
        return 0                                     # un fallo del hook nunca debe romper la herramienta: el servidor sigue protegiendo
    if reason:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                            "permissionDecisionReason": reason}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
