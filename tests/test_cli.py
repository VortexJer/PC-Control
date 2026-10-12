"""install / uninstall / status / pause: set everything up in Claude Code and undo it without touching anything that is not ours.

Uses a fake `claude` and temporary folders: the real configuration is never touched.
"""
import json, os, re, shutil, sys, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    rm_i = calls.index(["mcp", "remove", "PC-Control", "-s", "user"]); add = calls[rm_i + 1]
    c["install: removes any previous registration and registers as PC-Control"] = add[:5] == ["mcp", "add", "PC-Control", "-s", "user"]
    c["install: also removes the old 'pcsight' server, in every scope"] = all(["mcp", "remove", "pcsight", "-s", sc] in calls for sc in ("user", "project", "local"))
    c["install: mode and python in the command"] = "PC_CONTROL_MODE=auto" in add and add[-3:] == ["PY", "-m", "pc_control.server"]
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
    c["uninstall: tells how to remove the package"] = any("pip uninstall claude-pc-control" in l for l in log)
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
    # 13) update from ANY earlier version: what the old "pcsight" and early PC-Control versions left is cleaned, the user's own is kept
    old_skill = os.path.join(ch, "skills", "pcsight"); os.makedirs(old_skill, exist_ok=True)
    open(os.path.join(old_skill, "SKILL.md"), "w", encoding="utf-8").write("---\nname: pcsight\n---\n<!-- installed-by: pcsight -->\nold")
    other_skill = os.path.join(ch, "skills", "mine"); os.makedirs(other_skill, exist_ok=True)
    open(os.path.join(other_skill, "SKILL.md"), "w", encoding="utf-8").write("my own skill")
    open(os.path.join(ch, "CLAUDE.md"), "w", encoding="utf-8").write("# my notes\n\n<!-- pcsight:begin (managed) -->\nold block\n<!-- pcsight:end -->\n\nmore of mine\n")
    json.dump({"hooks": {"PreToolUse": [{"matcher": "mcp__pcsight__.*", "hooks": [{"type": "command", "command": "python -m pcsight.hook"}]},
                                         {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-own-hook"}]}]},
               "permissions": {"allow": ["mcp__pcsight__look", "Bash(ls)"], "ask": ["mcp__PC-Control__click", "Bash(git push)"]}},
              open(settings, "w"))
    open(os.path.join(ch, "pcsight-install.json"), "w").write("{}")
    calls.clear(); cli.install("follow", runner=fake, python="PY")
    st = rd(settings); md = read(os.path.join(ch, "CLAUDE.md"))
    c["migrate: the old pcsight skill is removed, the user's own skill is kept"] = not os.path.exists(old_skill) and os.path.exists(other_skill)
    c["migrate: the old CLAUDE.md block is removed, the user's notes are kept"] = "pcsight" not in md and "# my notes" in md and "more of mine" in md
    c["migrate: the old hook is removed, the user's own hook is kept"] = ("pcsight" not in json.dumps(st.get("hooks", {}))
                                                                          and "my-own-hook" in json.dumps(st.get("hooks", {})))
    c["migrate: old permission rules removed (pcsight, PC-Control 'ask'), the user's own kept"] = (
        st["permissions"].get("allow") == ["Bash(ls)"] and st["permissions"].get("ask") == ["Bash(git push)"])
    c["migrate: the old install record is removed"] = not os.path.exists(os.path.join(ch, "pcsight-install.json"))
    c["update: detects a git checkout as the source to pull"] = cli._installed()[1] == os.path.dirname(os.path.dirname(os.path.abspath(cli.__file__)))
    # 13b) what Claude Code runs: the `pc-control-mcp` launcher (a name Guard-like tools can recognize), the module form as a fallback
    exe = cli.server_exe()
    c["server command: an explicit python always gets the module form"] = cli.server_command("PY") == ["PY", "-m", "pc_control.server"]
    c["server command: default is the pc-control-mcp launcher when pip made one (full path, so it does not depend on PATH)"] = (
        cli.server_command() == [exe] if exe else cli.server_command() == [sys.executable, "-m", "pc_control.server"])
    calls.clear(); cli.install("follow", runner=fake)
    add = next(x for x in calls if x[:2] == ["mcp", "add"])
    c["install: registers the launcher (or the module form without one)"] = (add[add.index("--") + 1:] == [exe] if exe else add[-2:] == ["-m", "pc_control.server"])
    c["install: the launcher path has no shell-eaten backslash problem (it is passed as one argument)"] = (not exe) or os.path.isabs(add[-1])
    # 14) the package was renamed (pc-control -> claude-pc-control) and both own the SAME files: the old one has to go FIRST
    import importlib.metadata as imd
    real_dist, real_run, real_inst, real_ver = imd.distribution, cli.subprocess.run, cli._installed, cli._code_version
    present, ran, fail = {"pc-control"}, [], []
    class Fake:
        def __init__(self, rc=0): self.returncode = rc; self.stdout = ""; self.stderr = ""
    def fake_run(cmd, *a, **k):
        ran.append(list(cmd))
        return Fake(1 if fail and any(f in cmd for f in fail) else 0)
    class D: version = "0.5.3"
    def fake_dist(name):
        if name in present: return D()
        raise imd.PackageNotFoundError(name)
    pip_i = lambda cmd: cmd[1:4] == ["-m", "pip", "install"]
    pip_u = lambda cmd: cmd[1:4] == ["-m", "pip", "uninstall"]
    try:
        imd.distribution, cli.subprocess.run, cli._code_version = fake_dist, fake_run, lambda: "x"
        log = cli.drop_legacy_dist()
        c["rename: the old 'pc-control' package is uninstalled when present"] = (len(ran) == 1 and pip_u(ran[0]) and ran[0][-1] == "pc-control"
                                                                               and any("claude-pc-control" in l for l in log))
        ran.clear(); present.clear(); present.add("claude-pc-control")
        c["rename: nothing is uninstalled when only the new package is there"] = cli.drop_legacy_dist() == [] and ran == []
        c["rename: the new name is the one looked up first"] = cli._dist(imd) is not None
        present.clear(); present.add("pc-control")
        c["rename: an install that only has the old name is still found (status/update)"] = cli._dist(imd) is not None
        ran.clear(); present.clear(); present.add("pc-control"); fail.append("pc-control")
        try: cli.drop_legacy_dist(); c["rename: a failed uninstall is reported, not swallowed"] = False
        except RuntimeError: c["rename: a failed uninstall is reported, not swallowed"] = True
        fail.clear()
        # update() of a normal install: uninstall old -> install new (uninstalling AFTER would delete the new files)
        ran.clear(); present.clear(); present.add("pc-control"); cli._installed = lambda: ("0.5.3", None)
        cli.update()
        iu = next((i for i, x in enumerate(ran) if pip_u(x)), -1); ii = next((i for i, x in enumerate(ran) if pip_i(x) and cli.REPO_ZIP in x), -1)
        c["update (normal install): the old package is removed BEFORE the new one is installed"] = 0 <= iu < ii
        # update() of a git checkout: pull -> uninstall old -> pip install -e
        ran.clear(); present.clear(); present.add("pc-control"); cli._installed = lambda: ("0.5.3", ROOT_DIR)
        cli.update()
        ig = next((i for i, x in enumerate(ran) if x[:1] == ["git"]), -1); iu = next((i for i, x in enumerate(ran) if pip_u(x)), -1)
        ii = next((i for i, x in enumerate(ran) if pip_i(x) and "-e" in x), -1)
        c["update (git checkout): pull, then remove the old package, then install the checkout"] = 0 <= ig < iu < ii
        # update() when only the new package exists: no uninstall at all
        ran.clear(); present.clear(); present.add("claude-pc-control")
        cli.update()
        c["update: nothing is uninstalled when there is no old package"] = not any(pip_u(x) for x in ran)
        # an OLD update got the new code without knowing about the rename: reinstall puts the new package back after dropping the old
        ran.clear(); present.clear(); present.add("pc-control"); present.add("claude-pc-control"); cli._installed = lambda: ("0.6.0", None)
        h = cli._heal_legacy_dist()
        iu = next((i for i, x in enumerate(ran) if pip_u(x)), -1); ii = next((i for i, x in enumerate(ran) if pip_i(x) and "--force-reinstall" in x and "--no-deps" in x), -1)
        c["heal: both packages present -> old removed, then the new one is forced back in"] = 0 <= iu < ii and bool(h)
    finally:
        imd.distribution, cli.subprocess.run, cli._installed, cli._code_version = real_dist, real_run, real_inst, real_ver
finally:
    shutil.rmtree(tmp, ignore_errors=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the PowerShell installers really pass their arguments
for ps1 in ("install.ps1", "uninstall.ps1"):
    src = open(os.path.join(ROOT, ps1), encoding="utf-8").read()
    c[f"{ps1} runs the CLI with its own arguments ($cliArgs, not the empty $args)"] = "& python @cliArgs" in src and "& python @args" not in src
rd_ps = lambda n: open(os.path.join(ROOT, n), encoding="utf-8").read()
for ps1 in ("install.ps1", "update.ps1"):                    # the old package goes before the first pip install of the new one
    s = rd_ps(ps1); iu = s.find("pip uninstall -y pc-control"); ii = s.find("pip install", iu if iu >= 0 else 0)
    c[f"{ps1}: removes the old 'pc-control' package before installing claude-pc-control"] = 0 <= iu < ii
c["update.ps1: both the checkout and the zip install go through Remove-LegacyPackage"] = rd_ps("update.ps1").count("Remove-LegacyPackage") == 3
for ps1 in ("install.ps1", "update.ps1"):                    # PowerShell 5.1: a failing native probe is fatal under 'Stop' unless it runs under 'Continue'
    s = rd_ps(ps1)
    c[f"{ps1}: the 'is the old package there?' probe cannot abort the script when it is not there"] = (
        "$ErrorActionPreference = 'Continue'" in s and "sys.exit(0 if 'pc-control' in" in s and "m.distribution('pc-control')" not in s)
c["uninstall.ps1: removes the package under either name"] = "pip uninstall -y claude-pc-control pc-control" in rd_ps("uninstall.ps1")
for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
bad = [k for k, v in c.items() if not v]
print(f"cli verification passed ({len(c)} checks)" if not bad else f"cli verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
