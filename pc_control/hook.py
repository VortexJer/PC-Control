"""Claude Code PreToolUse hook for PC-Control: makes CLAUDE CODE itself ask the user.

It is registered with `pc-control install` (matcher mcp__PC-Control__.*). It receives the call on stdin and, if the user's
permission is needed (protected window, or an action the server already asked to confirm), answers permissionDecision="ask": Claude Code
shows its normal permission dialog and the model cannot answer it. It is lightweight on purpose (it imports no OCR or vision).
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


_VERBS = {"drag": "DRAG the mouse (draw, select or move) in", "look": "VIEW the contents of", "click": "CLICK on", "type": "TYPE into", "key": "PRESS a key in"}


def _action(tool, args, what):
    """What Claude wants to do, in a sentence a human understands."""
    if tool == "type":
        txt = str(args.get("text", ""))
        return f"TYPE «{txt[:60]}{'…' if len(txt) > 60 else ''}» into {what}"
    if tool == "key":
        return f"PRESS the key «{args.get('name', '?')}» in {what}"
    if tool == "click":
        return f"CLICK (element {args.get('target', '?')}) on {what}"
    return f"{_VERBS.get(tool, 'USE')} {what}"


PENDING = []          # (signature, tool, window key): "asked" marks that are written ONLY after the question has been delivered (see main)


def _decide(data):
    """Return the reason (in plain language) if the user must be asked, or None. Records in PENDING the marks still to be written."""
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
        title = ""; what = f"the program «{proc}»"
    else:
        hwnd = _find(args.get("window", ""))
        if not hwnd:
            return None
        proc, title = _proc(hwnd), win32gui.GetWindowText(hwnd)
        what = f"the window «{title[:60] or proc}» (program: {proc})"
    cat = policy.needs_ask(policy.mode(), proc, title)
    if cat:
        key = policy.window_key(cat, proc, 0 if tool == "open_app" else hwnd)
        if perm.approved(key):
            return None
        PENDING.append((s, tool, key))
        if tool == "open_app":
            action = f"OPEN {what}"
        else:
            action = _action(tool, args, what)
        note = (' A plain "yes" only counts for THIS time. If you choose "don\'t ask again", PC-Control applies it only to THIS window '
                "(not to the others) for as long as this session lasts.")
        if cat == policy.ASK_ALL:
            return f"Claude wants to {action}.\n\nPC-Control asks you about every window Claude accesses.{note}\n\nAllow it?"
        return (f"Claude wants to {action}.\n\nThis is a PROTECTED window ({cat}): it could show or change sensitive things "
                f"(commands, passwords, private data).{note}\n\nAllow it?")
    detail = perm.take("pending", s)                # the server asked to confirm this action (e.g. a sensitive action)
    if detail:
        PENDING.append((s, tool, None))
        det = detail if isinstance(detail, str) else tool
        return f"Claude wants to {det} in {what}.\n\nPC-Control considers it sensitive and needs your permission every time.\n\nAllow it?"
    return None


def _need(data):
    """True if THIS call needs the user's answer (without consuming any mark). Used by the PermissionRequest hook."""
    tool = str(data.get("tool_name", "")).rsplit("__", 1)[-1]
    args = data.get("tool_input") or {}
    if tool == "windows" or policy.mode() == "bypass":
        return False
    if perm.fresh("asked", perm.sig(tool, args)):          # PreToolUse already asked the user: let them answer it
        return True
    if tool == "open_app":
        try:
            import shlex
            proc = os.path.splitext(os.path.basename(shlex.split(args.get("command", ""), posix=False)[0].strip('"')))[0].lower()
        except (ValueError, IndexError):
            return False
        return bool(policy.needs_ask(policy.mode(), proc, "")) and not perm.approved(policy.window_key(policy.needs_ask(policy.mode(), proc, ""), proc, 0))
    hwnd = _find(args.get("window", ""))
    if not hwnd:
        return False
    proc, title = _proc(hwnd), win32gui.GetWindowText(hwnd)
    cat = policy.needs_ask(policy.mode(), proc, title)
    return bool(cat) and not perm.approved(policy.window_key(cat, proc, hwnd))


def commit_marks():
    """The "asked" mark tells the server the user saw the question. It is written only once the question HAS been delivered:
    if the hook fails earlier (error, encoding, Claude Code does not receive it), there is no mark and the server does NOT execute (fails closed).
    The window asked about with each tool is also recorded (see convert_rules)."""
    for s, tool, key in PENDING:
        perm.mark("asked", s)
        if key:
            perm.set_last(tool, key)
    PENDING.clear()


def _settings_candidates(cwd):
    home = os.path.expanduser("~")
    out, seen = [], set()
    for base in (cwd or "", home):
        for name in ("settings.local.json", "settings.json"):
            p = os.path.normpath(os.path.join(base, ".claude", name))
            if base and os.path.normcase(p) not in seen and os.path.exists(p):
                seen.add(os.path.normcase(p)); out.append(p)
    return out


def _rules_by_installer():
    """Permissions that `pc-control install --allow-reads` set on purpose: they are not a "don't ask again" from the user."""
    try:
        rec = json.load(open(os.path.join(os.path.expanduser("~"), ".claude", "pc-control-install.json"), encoding="utf-8"))
        return set(rec.get("allow_added", []))
    except (OSError, ValueError):
        return set()


def convert_rules(data):
    """Claude Code's «Yes, and don't ask again for PC-Control - Look commands» option saves a rule per TOOL
    (mcp__PC-Control__look): it would apply to all windows. Here the newly saved rule is detected, applied ONLY to the last
    window asked about with that tool (session memory), and the rule is removed so it does not apply to the others."""
    mine = _rules_by_installer()
    for path in _settings_candidates(data.get("cwd")):
        try:
            s = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        allow = s.get("permissions", {}).get("allow", [])
        remove = []
        for rule in allow:
            if not isinstance(rule, str) or not rule.startswith("mcp__PC-Control__") or rule in mine:
                continue
            tool = rule.rsplit("__", 1)[-1]
            last = perm.get_last(tool)
            if tool in ("windows", "*") or not last or os.path.getmtime(path) < last["t"] - 2:
                continue
            perm.approve(last["key"]); remove.append(rule)
        if remove:
            s["permissions"]["allow"] = [r for r in allow if r not in remove]
            if not s["permissions"]["allow"]:
                s["permissions"].pop("allow")
            if not s["permissions"]:
                s.pop("permissions")
            json.dump(s, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)


def decide(data, commit=True):
    """Like _decide; with commit=True it writes the "asked" mark right away (tests and direct use). main() uses commit=False and writes it after delivering the question."""
    reason = _decide(data)
    if commit:
        commit_marks()
    return reason


def main():
    try:
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")        # Claude Code sends UTF-8; the Windows console uses cp1252 unless forced
        data = json.load(sys.stdin)
        perm.set_cc_mode(data.get("permission_mode"))              # Claude Code's mode rules (PC_CONTROL_MODE=follow)
        convert_rules(data)                                        # Claude Code's "don't ask again" -> that window only
        if data.get("hook_event_name") == "PermissionRequest":
            # PC-Control's `ask` rules make Claude Code show a dialog on EVERY call. Here we answer by ourselves only
            # when there is no need to bother the user (normal window in auto, bypass, window already remembered); if it is needed, nothing is said
            # and the dialog appears for the user.
            perm.set_flag("pr_seen")                                   # this Claude Code DOES announce dialogs: the server can require it
            if not _need(data):
                sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}))
                sys.stdout.flush()
            else:
                perm.mark("shown", perm.sig(str(data.get("tool_name", "")).rsplit("__", 1)[-1], data.get("tool_input") or {}))   # the dialog IS GOING to be shown
            return 0
        reason = decide(data, commit=False)
        if reason:
            # ensure_ascii: ASCII-only output (accents and symbols travel as \uXXXX) so NO console encoding can break it
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                                "permissionDecisionReason": reason}}, ensure_ascii=True))
            sys.stdout.flush()
            commit_marks()                                          # only after delivering the question
    except Exception:
        return 0                                           # a hook failure must never break the tool: the server keeps protecting
    return 0


if __name__ == "__main__":
    sys.exit(main())
