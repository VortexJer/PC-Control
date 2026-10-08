"""PC-Control command line: install / uninstall in Claude Code, status, pause.

    pc-control install [--mode follow|auto|ask|strict|bypass] [--scope user|project|local] [--allow-reads]
    pc-control uninstall [--keep-data]
    pc-control status
    pc-control pause | resume
    pc-control serve                     (the MCP server, what Claude Code launches)

`install` sets everything up: it registers the MCP server in Claude Code, writes the skill with the usage instructions, adds a short
managed block to ~/.claude/CLAUDE.md and registers the permission hooks.
`uninstall` removes exactly what `install` added (it keeps a record in ~/.claude/pc-control-install.json) and, by default, the
~/.pc-control data folder too. Nothing that was not put there by `install` is touched.
"""
import argparse, json, os, shutil, subprocess, sys
from . import envvars

MARKER = "<!-- installed-by: pc-control -->"
SERVER_NAME = "PC-Control"                    # the name Claude Code shows: mcp__PC-Control__look
SKILL_DIR = "pc-control"
READ_TOOLS = [f"mcp__{SERVER_NAME}__windows", f"mcp__{SERVER_NAME}__look"]
HOOK_MATCHER = f"mcp__{SERVER_NAME}__.*"
HOOK_MARK = "pc_control.hook"
HOOK_EVENTS = ("PreToolUse", "PermissionRequest")     # PreToolUse asks with a clear text; PermissionRequest answers by itself when asking is pointless
MD_START, MD_END = "<!-- pc-control:begin (managed by the pc-control package) -->", "<!-- pc-control:end -->"

SKILL = f"""---
name: pc-control
description: Use when the user asks you to see, read or operate an app or window on their Windows PC (Word, File Explorer, Paint...), or asks how to use the app they have open. Controls apps without bringing them to the front and without moving the user's mouse or keyboard.
---
{MARKER}

# PC-Control: Claude on the PC

Tools `mcp__PC-Control__*`: `windows`, `look`, `click`, `type`, `drag`, `key`, `open_app`.

## Flow
1. `windows` lists the windows, most recent first. `[IN USE]` = where the user is working right now;
   `[LAST USED]` = the one they had in front before talking to you. If they say "this app" or "how do I use this", it is that one.
2. `look(window)` returns the cheapest thing that works (UI-tree text, OCR, or an image with numbered marks). Elements appear
   as `[id:name]`.
3. `click(window, id)` and `type(window, id, text)` act by id. They return only what changed: no need to look again.

## Rules
- Nothing is focused; the user's mouse and keyboard are never touched. Do not ask for shortcuts (`ctrl+s`): they are not supported.
- Delicate actions (pay, delete, send, install) and protected windows are asked by PC-Control itself (you cannot answer for the user).
  PC-Control never types into password fields.
- `open_app` opens the app minimized with its taskbar button; with `hidden=true`, on a hidden desktop. A minimized app is read as text, not as an image.
- Word: Claude has its OWN insertion point (a hidden bookmark). `type` writes there through COM, without touching the user's selection or
  caret, and the answer says in which paragraph and after which words the text landed (check that it is what you expected). The formatting
  buttons (bold, italic, alignment...) apply to what Claude writes, not to the user's selection, so the user can click anywhere without
  diverting anything. Press the button BEFORE typing the text that takes that format; press it again to turn it off.
  In Word, `key` only offers `enter`, `space` and `backspace`, and they act at Claude's own insertion point.
- If the user resizes a window and what you were about to press disappears, PC-Control shows a 10 s notice on that window
  ("If you touch this application, Claude will not be able to act on it...") and waits for the user to put it back; if the button merely moved,
  it finds it again by itself and the answer says so. Every action says whether it had a visible effect: if it says NO VISIBLE EFFECT, check with `look`.
- Windows marked [PROTECTED: ...] (terminal, password manager, banking...) are NOT blocked but need the user's permission.
  If the tool answers that it needs the user's permission and that you must repeat EXACTLY the same call, do it: Claude Code will show its
  permission dialog to the user (you cannot answer it). If they say no, do not insist and do not work around it.
  A "yes" from the user is valid ONLY for that call: the next one asks again. If the user picks "don't ask again" in the dialog, PC-Control
  applies it to that one window only (not to the others); you cannot and must not grant that permission.
- `drag` presses the button and drags through points (freehand, line, rectangle, ellipse), without moving the user's mouse. Not every app
  accepts it (modern canvases may ignore it): check the result with `look`.
- Right click opens the app's real context menu only when the user has been idle for a few seconds; the entries are pressed by id like any button.
- "unconfirmed" in an answer = the program could not check the effect. Check with `look` before taking anything for granted.
- If the user asks how an app works, explain what you see with `look`; do not act unless they ask.
"""

MD_BLOCK = f"""{MD_START}
# PC-Control
- **pc-control** (`~/.claude/skills/pc-control/SKILL.md`) - see, read and operate Windows apps and windows (Word, File Explorer, Paint...)
  without taking focus or moving the user's mouse. When the user asks to look at, read or operate an app or window on their PC, or how to
  use the app they have open, use the installed pc-control skill first.
{MD_END}
"""


def _ours(text):
    """True if a SKILL.md was written by this installer."""
    return MARKER in text


def data_home():
    return envvars.get("HOME") or os.path.join(os.path.expanduser("~"), ".pc-control")


def claude_home():
    return envvars.get("CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")


def _paths():
    ch = claude_home()
    return {"settings": os.path.join(ch, "settings.json"), "skill": os.path.join(ch, "skills", SKILL_DIR), "skills_dir": os.path.join(ch, "skills"),
            "record": os.path.join(ch, "pc-control-install.json"), "claude_md": os.path.join(ch, "CLAUDE.md")}


def _short(path):
    """Short 8.3 path with no spaces and forward slashes: works the same in Git Bash and cmd (Claude Code runs hooks with Git Bash,
    which swallows backslashes)."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(520)
        out = buf.value if ctypes.windll.kernel32.GetShortPathNameW(path, buf, 520) else path
    except Exception:
        out = path
    return out.replace(chr(92), "/")


def _load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _is_our_hook(entry):
    return any(HOOK_MARK in h.get("command", "") for h in entry.get("hooks", []))


def _hook_add(settings_path, py):
    """Register the hooks that make Claude Code ask the user for PC-Control's permissions. Idempotent."""
    s = _load(settings_path, {})
    for ev in HOOK_EVENTS:
        lst = s.setdefault("hooks", {}).setdefault(ev, [])
        lst[:] = [e for e in lst if not _is_our_hook(e)]
        lst.append({"matcher": HOOK_MATCHER, "hooks": [{"type": "command", "command": f"{_short(py)} -m {HOOK_MARK}"}]})
    _save(settings_path, s)


def _hook_remove(settings_path):
    s = _load(settings_path, {})
    removed = False
    for ev in HOOK_EVENTS:
        lst = s.get("hooks", {}).get(ev)
        if not lst:
            continue
        keep = [e for e in lst if not _is_our_hook(e)]
        if len(keep) != len(lst):
            removed = True
            if keep:
                s["hooks"][ev] = keep
            else:
                s["hooks"].pop(ev)
    if removed:
        if not s.get("hooks"):
            s.pop("hooks", None)
        _save(settings_path, s)
    return removed


def hook_installed(settings_path):
    h = _load(settings_path, {}).get("hooks", {})
    return all(any(_is_our_hook(e) for e in h.get(ev, [])) for ev in HOOK_EVENTS)


def _md_add(path):
    """Add (or refresh) the managed PC-Control block in CLAUDE.md. Returns True if the file did not exist before (so uninstall can delete it)."""
    created = not os.path.exists(path)
    text = "" if created else open(path, encoding="utf-8").read()
    if MD_START in text and MD_END in text:
        a, b = text.index(MD_START), text.index(MD_END) + len(MD_END)
        text = text[:a] + MD_BLOCK.rstrip("\n") + text[b:]
    else:
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + MD_BLOCK
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return created


def _md_remove(path, created):
    """Remove our block from CLAUDE.md, and the file itself if we created it and nothing else is left. Returns True if something was removed."""
    if not os.path.exists(path):
        return False
    text = open(path, encoding="utf-8").read()
    if MD_START not in text or MD_END not in text:
        return False
    a, b = text.index(MD_START), text.index(MD_END) + len(MD_END)
    rest = (text[:a].rstrip("\n") + "\n\n" + text[b:].lstrip("\n")).strip("\n")
    if not rest and created:
        os.remove(path)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(rest + "\n" if rest else "")
    return True


def run_claude(args):
    """Run `claude <args>`; returns (code, output)."""
    exe = shutil.which("claude")
    if not exe:
        return 127, "the 'claude' command was not found in PATH (install Claude Code first)"
    p = subprocess.run([exe] + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


def install(mode="follow", scope="user", allow_reads=False, runner=run_claude, python=None):
    """Register PC-Control in Claude Code and leave its skill. Returns a list of lines to show."""
    if mode not in ("follow", "auto", "ask", "strict", "bypass"):
        raise ValueError(f"unknown mode: {mode}")
    py = python or sys.executable
    pth = _paths(); log = []
    record = _load(pth["record"], {})
    runner(["mcp", "remove", SERVER_NAME, "-s", scope])                    # idempotent: if it was there, it is redone
    code, out = runner(["mcp", "add", SERVER_NAME, "-s", scope, "-e", f"PC_CONTROL_MODE={mode}", "-e", "PYTHONUTF8=1", "--", py, "-m", "pc_control.server"])
    if code != 0:
        raise RuntimeError(f"claude mcp add failed: {out}")
    log.append(f"MCP server registered ({scope}, mode {mode})")

    skill_md = os.path.join(pth["skill"], "SKILL.md")
    if os.path.exists(skill_md) and not _ours(open(skill_md, encoding="utf-8").read()):
        log.append(f"a '{SKILL_DIR}' skill that is not ours already exists: left untouched")
        wrote_skill = False
    else:
        os.makedirs(pth["skill"], exist_ok=True)
        open(skill_md, "w", encoding="utf-8").write(SKILL)
        log.append(f"skill written to {skill_md}")
        wrote_skill = True

    had_md = os.path.exists(pth["claude_md"])
    _md_add(pth["claude_md"])
    md_created = record.get("claude_md_created", False) or not had_md        # if install created the file, uninstall may delete it
    log.append(f"instructions block added to {pth['claude_md']}")

    bak = pth["settings"] + ".pc-control-bak"
    made_backup = record.get("settings_backup", False)
    if not os.path.exists(bak) and os.path.exists(pth["settings"]):
        shutil.copy(pth["settings"], bak); made_backup = True
    _hook_add(pth["settings"], py)
    log.append("permission hooks registered: Claude Code will ask the user before PC-Control uses a protected window")
    added = set(record.get("allow_added", []))
    if allow_reads:
        s = _load(pth["settings"], {})
        allow = s.setdefault("permissions", {}).setdefault("allow", [])
        for tool in READ_TOOLS:
            if tool not in allow:
                allow.append(tool); added.add(tool)
        _save(pth["settings"], s)
        log.append("reads (windows, look) allowed without asking")
    _save(pth["record"], {"scope": scope, "mode": mode, "skill": wrote_skill or record.get("skill", False), "allow_added": sorted(added),
                          "claude_md_created": bool(md_created), "settings_backup": bool(made_backup)})
    log.append(f"done: open a new Claude Code session; the tools appear as {SERVER_NAME} (mcp__{SERVER_NAME}__*)")
    return log


def uninstall(keep_data=False, scope="user", runner=run_claude):
    """Undo everything `install` did, and only that: MCP server, skill, CLAUDE.md block, hooks, permissions, record, backup and data folder."""
    pth = _paths(); log = []
    record = _load(pth["record"], {})
    scope = record.get("scope", scope)
    code, out = runner(["mcp", "remove", SERVER_NAME, "-s", scope])
    log.append("MCP server removed" if code == 0 else f"MCP server: nothing to remove ({out[:60]})")

    skill_md = os.path.join(pth["skills_dir"], SKILL_DIR, "SKILL.md")
    if os.path.exists(skill_md):
        if _ours(open(skill_md, encoding="utf-8").read()):
            shutil.rmtree(os.path.dirname(skill_md), ignore_errors=True); log.append(f"skill '{SKILL_DIR}' removed")
        else:
            log.append(f"skill '{SKILL_DIR}' is not ours: left untouched")
    if _md_remove(pth["claude_md"], record.get("claude_md_created", False)):
        log.append("instructions block removed from CLAUDE.md")
    if os.path.exists(pth["settings"]) and _hook_remove(pth["settings"]):
        log.append("permission hooks removed")
    added = record.get("allow_added", [])
    if added and os.path.exists(pth["settings"]):
        s = _load(pth["settings"], {})
        allow = s.get("permissions", {}).get("allow", [])
        s.setdefault("permissions", {})["allow"] = [a for a in allow if a not in added]
        if not s["permissions"]["allow"]:
            s["permissions"].pop("allow")
        if not s["permissions"]:
            s.pop("permissions")
        _save(pth["settings"], s); log.append("read permissions removed")
    bak = pth["settings"] + ".pc-control-bak"
    if record.get("settings_backup") and os.path.exists(bak):
        os.remove(bak); log.append("settings backup removed")
    if os.path.exists(pth["record"]):
        os.remove(pth["record"])
    if not keep_data and os.path.isdir(data_home()):
        shutil.rmtree(data_home(), ignore_errors=True); log.append(f"data folder {data_home()} removed")
    log.append("done. To remove the package itself: pip uninstall pc-control")
    return log


def status(runner=run_claude):
    pth = _paths(); record = _load(pth["record"], {})
    code, out = runner(["mcp", "get", SERVER_NAME])
    skill_md = os.path.join(pth["skill"], "SKILL.md")
    md = os.path.exists(pth["claude_md"]) and MD_START in open(pth["claude_md"], encoding="utf-8").read()
    return {"registered": code == 0, "connected": code == 0 and "Connected" in out, "mode": record.get("mode"),
            "skill": os.path.exists(skill_md) and _ours(open(skill_md, encoding="utf-8").read()),
            "claude_md": md, "hook": hook_installed(pth["settings"]),
            "paused": os.path.exists(os.path.join(data_home(), "PAUSE"))}


def set_pause(on):
    f = os.path.join(data_home(), "PAUSE")
    if on:
        os.makedirs(data_home(), exist_ok=True); open(f, "w").close()
    elif os.path.exists(f):
        os.remove(f)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pc-control", description="PC-Control: let an AI see and operate Windows apps. Install, uninstall and control.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install", help="register in Claude Code and leave the skill")
    i.add_argument("--mode", choices=["follow", "auto", "ask", "strict", "bypass"], default="follow")
    i.add_argument("--scope", choices=["user", "project", "local"], default="user")
    i.add_argument("--allow-reads", action="store_true", help="allow windows and look without asking")
    u = sub.add_parser("uninstall", help="remove everything install added (and the data folder)")
    u.add_argument("--keep-data", action="store_true", help="keep the ~/.pc-control data folder (allow list, approvals)")
    sub.add_parser("status", help="show whether it is installed and connected")
    sub.add_parser("pause", help="kill switch: every tool refuses")
    sub.add_parser("resume", help="remove the pause")
    sub.add_parser("serve", help="launch the MCP server (what Claude Code runs)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "install":
            print("\n".join(install(a.mode, a.scope, a.allow_reads)))
        elif a.cmd == "uninstall":
            print("\n".join(uninstall(a.keep_data)))
        elif a.cmd == "status":
            for k, v in status().items(): print(f"{k}: {v}")
        elif a.cmd in ("pause", "resume"):
            set_pause(a.cmd == "pause"); print("paused" if a.cmd == "pause" else "resumed")
        elif a.cmd == "serve":
            from .server import main as serve
            serve()
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
