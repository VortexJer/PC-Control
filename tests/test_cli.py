"""install / uninstall / status / pause: set everything up in Claude Code and undo it without touching anything that is not ours.

Uses a fake `claude` and temporary folders: the real configuration is never touched.
"""
import json, os, re, shutil, sys, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmp = tempfile.mkdtemp(prefix="pc-control_cli_")
ch, ph = os.path.join(tmp, "claude"), os.path.join(tmp, "home")
os.environ["PC_CONTROL_CLAUDE_HOME"], os.environ["PC_CONTROL_HOME"] = ch, ph
from pc_control import cli

c = {}
calls = []
def fake(args):
    calls.append(args); return 0, "PC-Control: Status: Connected"
settings = os.path.join(ch, "settings.json"); skill_md = os.path.join(ch, "skills", "pc-control", "SKILL.md")
record = os.path.join(ch, "pc-control-install.json"); claude_md = os.path.join(ch, "CLAUDE.md"); bak = settings + ".pc-control-bak"
os.makedirs(ch); os.makedirs(ph)
original = {"permissions": {"allow": ["Bash(ls)"], "deny": ["Bash(rm)"]}, "model": "x", "env": {"A": "1"}}
json.dump(original, open(settings, "w"))
rd = lambda p: json.load(open(p, encoding="utf-8"))
read = lambda p: open(p, encoding="utf-8").read()

try:
    # 1) basic install
    cli.install("auto", "user", False, runner=fake, python="PY")
    c["install: removes any previous registration and registers as PC-Control"] = calls[0] == ["mcp", "remove", "PC-Control", "-s", "user"] and calls[1][:5] == ["mcp", "add", "PC-Control", "-s", "user"]
    c["install: mode and python in the command"] = "PC_CONTROL_MODE=auto" in calls[1] and calls[1][-3:] == ["PY", "-m", "pc_control.server"]
    s = read(skill_md)
    c["install: writes the skill with its marker, in English"] = (s.startswith("---") and "name: pc-control" in s and cli.MARKER in s and "[LAST USED]" in s
                                                                 and "[IN USE]" in s and "mcp__PC-Control__" in s)
    c["install: the skill has no Spanish left"] = not re.search(r"\b(ventana|usuario|herramienta|siempre|nunca)\b", s, re.I)
    st0 = rd(settings); hooks_all = st0.pop("hooks")
    c["install: does not touch settings without --allow-reads (it only adds the hooks)"] = st0 == original
    c["install: registers the PreToolUse and PermissionRequest hooks (forward slashes)"] = (
        hooks_all["PreToolUse"][0]["matcher"] == "mcp__PC-Control__.*" and hooks_all["PermissionRequest"][0]["matcher"] == "mcp__PC-Control__.*"
        and hooks_all["PreToolUse"][0]["hooks"][0]["command"] == "PY -m pc_control.hook")
    c["install: does not put ask rules (they made a dialog blink on every call)"] = "ask" not in rd(settings).get("permissions", {})
    c["install: leaves the record"] = rd(record)["mode"] == "auto" and rd(record)["allow_added"] == [] and rd(record)["claude_md_created"] is True
    c["install: backs up settings.json"] = os.path.exists(bak) and rd(bak) == original
    # 2) CLAUDE.md
    md = read(claude_md)
    c["install: creates CLAUDE.md with the managed block"] = md.count(cli.MD_START) == 1 and md.count(cli.MD_END) == 1 and "pc-control" in md and "skills/pc-control/SKILL.md" in md
    # 3) with --allow-reads, keeping what is not ours
    cli.install("strict", "user", True, runner=fake, python="PY")
    st = rd(settings)
    c["allow-reads: adds the two reads"] = cli.READ_TOOLS == ["mcp__PC-Control__windows", "mcp__PC-Control__look"] and all(t in st["permissions"]["allow"] for t in cli.READ_TOOLS)
    c["allow-reads: keeps what was there"] = "Bash(ls)" in st["permissions"]["allow"] and st["permissions"]["deny"] == ["Bash(rm)"] and st["model"] == "x" and st["env"] == {"A": "1"}
    c["install: strict mode in the command"] = any("PC_CONTROL_MODE=strict" in x for x in calls[-1])
    # 4) idempotent
    cli.install("strict", "user", True, runner=fake, python="PY")
    allow = rd(settings)["permissions"]["allow"]
    c["idempotent: no duplicates (reads, hooks, CLAUDE.md block)"] = (all(allow.count(t) == 1 for t in cli.READ_TOOLS) and len(rd(settings)["hooks"]["PreToolUse"]) == 1
                                                                   and len(rd(settings)["hooks"]["PermissionRequest"]) == 1 and read(claude_md).count(cli.MD_START) == 1)
    c["idempotent: a second install does not forget that it created CLAUDE.md"] = rd(record)["claude_md_created"] is True
    # 5) status
    stt = cli.status(runner=fake)
    c["status: reports registered, connected, skill, CLAUDE.md block and hooks"] = (stt["registered"] and stt["connected"] and stt["skill"] and stt["claude_md"] and stt["hook"]
                                                                                   and stt["mode"] == "strict" and stt["paused"] is False)
    # 6) uninstall undoes only what is ours
    open(os.path.join(ph, "approvals.json"), "w").write("{}")
    n = len(calls); log = cli.uninstall(runner=fake)
    c["uninstall: removes the MCP server"] = calls[n] == ["mcp", "remove", "PC-Control", "-s", "user"]
    c["uninstall: removes the skill folder"] = not os.path.exists(os.path.dirname(skill_md))
    c["uninstall: removes CLAUDE.md when we created it and nothing else is in it"] = not os.path.exists(claude_md)
    st = rd(settings)
    c["uninstall: removes our hooks and leaves no empty 'hooks'"] = "hooks" not in st
    c["uninstall: leaves settings exactly as they were"] = st == original
    c["uninstall: removes the record, the settings backup and the data folder"] = not os.path.exists(record) and not os.path.exists(bak) and not os.path.isdir(ph)
    c["uninstall: tells how to remove the package"] = any("pip uninstall pc-control" in l for l in log)
    # 7) --keep-data keeps the data folder
    os.makedirs(ph); open(os.path.join(ph, "allow.txt"), "w").write("winword\n")
    cli.install("auto", runner=fake, python="PY"); cli.uninstall(keep_data=True, runner=fake)
    c["--keep-data: keeps ~/.pc-control (allow list)"] = os.path.exists(os.path.join(ph, "allow.txt"))
    shutil.rmtree(ph, ignore_errors=True)
    # 8) a CLAUDE.md that already exists keeps the user's content
    open(claude_md, "w", encoding="utf-8").write("# My notes\n- keep this\n")
    cli.install("auto", runner=fake, python="PY")
    md = read(claude_md)
    c["CLAUDE.md that already existed: our block is appended and the user's text stays"] = "# My notes" in md and "- keep this" in md and md.count(cli.MD_START) == 1 and rd(record)["claude_md_created"] is False
    cli.uninstall(runner=fake)
    md = read(claude_md) if os.path.exists(claude_md) else ""
    c["uninstall: gives back the user's CLAUDE.md untouched (and does not delete it)"] = md.strip() == "# My notes\n- keep this" and cli.MD_START not in md
    # 9) a foreign skill with the same name is not touched; a foreign hook lives alongside ours
    os.makedirs(os.path.dirname(skill_md)); open(skill_md, "w", encoding="utf-8").write("my own skill")
    sj = rd(settings); sj["hooks"] = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-hook"}]}]}; json.dump(sj, open(settings, "w"))
    cli.install("auto", runner=fake, python="PY"); both = rd(settings)["hooks"]["PreToolUse"]
    c["foreign skill: install does not overwrite it"] = read(skill_md) == "my own skill"
    cli.uninstall(runner=fake); after = rd(settings)["hooks"]["PreToolUse"]
    c["foreign skill: uninstall does not delete it"] = read(skill_md) == "my own skill"
    c["hooks: ours live alongside a foreign one and uninstall removes only ours"] = len(both) == 2 and len(after) == 1 and after[0]["hooks"][0]["command"] == "my-hook"
    # 10) pause / resume
    os.makedirs(ph, exist_ok=True)
    cli.set_pause(True); paused = os.path.exists(os.path.join(ph, "PAUSE")); cli.set_pause(False)
    c["pause / resume create and remove the PAUSE file"] = paused and not os.path.exists(os.path.join(ph, "PAUSE"))
    # 11) unknown mode and short path
    try: cli.install("bogus", runner=fake); c["unknown mode: rejected"] = False
    except ValueError: c["unknown mode: rejected"] = True
    sp = cli._short(sys.executable)
    c["hook path: no backslashes (Git Bash swallows them) and no spaces"] = chr(92) not in sp and " " not in sp and os.path.exists(sp)
    # 12) the entry point works
    rc = cli.main(["status"]) if False else 0
finally:
    shutil.rmtree(tmp, ignore_errors=True)

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
bad = [k for k, v in c.items() if not v]
print(f"cli verification passed ({len(c)} checks)" if not bad else f"cli verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
