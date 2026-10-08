"""The permission hook: makes Claude Code (not the model) ask the user before using protected windows."""
import io, json, os, sys, tempfile, time, types
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp(prefix="pc-control_hook_"); os.environ["PC_CONTROL_HOME"] = tmp; os.environ.pop("PC_CONTROL_MODE", None)
from pc_control import hook, perm

c = {}
hook._find = lambda q: 1234
hook._proc = lambda h: "windowsterminal"
hook.win32gui = types.SimpleNamespace(GetWindowText=lambda h: "PowerShell")
look = {"tool_name": "mcp__PC-Control__look", "tool_input": {"window": "PowerShell"}}

r = hook.decide(look)
c["protected window: the hook asks for permission"] = bool(r) and "PROTECTED" in r
s = perm.sig("look", {"window": "PowerShell"})
c["the dialog says WHICH app, WHICH window and WHAT it will do (not a window number)"] = "PowerShell" in r and "windowsterminal" in r and "VIEW the contents" in r and "9701696" not in r
rt = hook.decide({"tool_name": "mcp__PC-Control__type", "tool_input": {"window": "9701696", "target": "3", "text": "rm -rf /tmp/x"}})
c["typing: shows the text it will type"] = bool(rt) and "TYPE «rm -rf /tmp/x»" in rt
perm.take("asked", perm.sig("type", {"window": "9701696", "target": "3", "text": "rm -rf /tmp/x"}))
c["... and leaves the 'asked' mark so the server knows the user saw it"] = perm.fresh("asked", s)
c["the mark is spent only once"] = perm.take("asked", s) and not perm.take("asked", s)
c["windows (list only) never asks"] = hook.decide({"tool_name": "mcp__PC-Control__windows", "tool_input": {}}) is None
perm.approve("terminal or console|windowsterminal|1234")
c["after the user's yes it does not ask again for that window"] = hook.decide(look) is None
# the yes holds ONLY for the Claude Code session that gave it: another session (another claude process) asks again
real_sid = perm.session_id
perm.session_id = lambda: "9999-1"
c["a NEW Claude Code session does not inherit the previous one's permission"] = hook.decide(look) is not None and not perm.approved("terminal or console|windowsterminal|1234")
perm.approve("terminal or console|windowsterminal|1234")
c["... and its own yes only counts for it"] = hook.decide(look) is None
perm.session_id = real_sid
c["the previous session no longer counts as approved (cleaned up when another is approved)"] = not perm.approved("terminal or console|windowsterminal|1234")
c["the session id is stable within the same session"] = perm.session_id() == perm.session_id()
os.remove(os.path.join(tmp, "perm", "approved.json"))

hook.policy.blocked_category = lambda p, t="": None             # normal window
c["normal window: does not ask"] = hook.decide({"tool_name": "mcp__PC-Control__click", "tool_input": {"window": "Word", "target": "5"}}) is None
perm.mark("pending", perm.sig("click", {"window": "Word", "target": "5"}), "press the button «Delete all»")
r2 = hook.decide({"tool_name": "mcp__PC-Control__click", "tool_input": {"window": "Word", "target": "5"}})
c["action the server asked to confirm (same call repeated): asks and says which button"] = bool(r2) and "sensitive" in r2 and "«Delete all»" in r2
c["... and does not ask again the next time"] = hook.decide({"tool_name": "mcp__PC-Control__click", "tool_input": {"window": "Word", "target": "5"}}) is None

# the signature is the same whether the hook sees the arguments as given or the server sees them with their default values
c["stable signature (with and without default values)"] = perm.sig("click", {"window": "W", "target": "5"}) == perm.sig("click", {"window": "W", "target": "5", "right": False, "double": False})
c["different signature if the action changes"] = perm.sig("click", {"window": "W", "target": "5"}) != perm.sig("click", {"window": "W", "target": "6"})

# FAILS CLOSED: the "asked" mark (= the user saw the question) only exists if the question was really delivered
hook.policy.blocked_category = lambda p, t="": "terminal or console"
sg = perm.sig("look", {"window": "PowerShell"}); perm.take("asked", sg); hook.PENDING.clear()
class _Broken(io.StringIO):
    def write(self, x): raise OSError("broken pipe")
sys.stdin = io.StringIO(json.dumps(look)); _o = sys.stdout; sys.stdout = _Broken(); hook.main(); sys.stdout = _o
c["if the hook cannot deliver the question, it leaves NO mark (the server does not execute without the user seeing it)"] = not perm.fresh("asked", sg)
hook.PENDING.clear(); sys.stdin = io.StringIO(json.dumps(look)); sys.stdout = io.StringIO(); hook.main(); sys.stdout = _o
c["if delivery goes well, it leaves the mark afterwards"] = perm.take("asked", sg) is not False
hook.PENDING.clear()

# IN CLAUDE CODE'S ENVIRONMENT (no PYTHONUTF8, cp1252 console) and with a title containing symbols cp1252 lacks (real case: "✳")
import subprocess
hook.win32gui = types.SimpleNamespace(GetWindowText=lambda h: "✳ Permissions")
env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}; env["PC_CONTROL_HOME"] = tmp; env.pop("PC_CONTROL_MODE", None)
code = ("import sys,types;from pc_control import hook;hook._find=lambda q:1;hook._proc=lambda h:'windowsterminal';"
        "hook.win32gui=types.SimpleNamespace(GetWindowText=lambda h:'✳ Permissions');hook.policy.blocked_category=lambda p,t='':'terminal or console';sys.exit(hook.main())")
pr = subprocess.run([sys.executable, "-c", code], input=json.dumps(look, ensure_ascii=False).encode("utf-8"), capture_output=True, env=env,
                    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
out_real = pr.stdout.decode("ascii", "replace")
c["cp1252 console and a title with odd symbols: the hook does NOT break (before: UnicodeEncodeError)"] = pr.returncode == 0 and not pr.stderr and all(b < 128 for b in pr.stdout) and '"permissionDecision": "ask"' in out_real
c["... and the symbol arrives intact in the dialog text"] = "✳" in out_real or "✳" in json.loads(out_real)["hookSpecificOutput"]["permissionDecisionReason"]
perm.take("asked", perm.sig("look", look["tool_input"]))

# ASK MODE: asks about EVERY window (once per window and session); in auto only about the protected ones
import importlib
os.environ["PC_CONTROL_MODE"] = "ask"; hook.policy.blocked_category = importlib.import_module("pc_control.policy").blocked_category
hook.policy.blocked_category = lambda p, t="": None
hook._proc = lambda h: "notepad"
ask_in = {"tool_name": "mcp__PC-Control__click", "tool_input": {"window": "Notepad", "target": "2"}}
ra = hook.decide(ask_in)
c["ask mode: a normal window ALSO asks, with the mode's notice"] = bool(ra) and "every window" in ra and "PROTECTED" not in ra and "only counts for THIS time" in ra
perm.take("asked", perm.sig("click", ask_in["tool_input"]))
perm.approve(hook.policy.window_key(hook.policy.ASK_ALL, "notepad", 1234))
c["ask mode: after the yes to THAT window it no longer asks in the session"] = hook.decide(ask_in) is None
hook._find = lambda q: 777
c["ask mode: a different window (even of the same program) asks again"] = hook.decide(ask_in) is not None
perm.take("asked", perm.sig("click", ask_in["tool_input"]))
c["ask mode: windows (list only) still does not ask"] = hook.decide({"tool_name": "mcp__PC-Control__windows", "tool_input": {}}) is None
os.environ["PC_CONTROL_MODE"] = "auto"
c["auto mode: the same normal window does NOT ask"] = hook.decide(ask_in) is None
os.environ.pop("PC_CONTROL_MODE", None)
hook._find = lambda q: 1234; hook._proc = lambda h: "windowsterminal"


# the hook records the Claude Code permission mode it receives and behaves accordingly
os.environ.pop("PC_CONTROL_MODE", None)
hook._find = lambda q: 1234; hook._proc = lambda h: "notepad"; hook.policy.blocked_category = lambda p, t="": None
pm = lambda mode: {"tool_name": "mcp__PC-Control__click", "tool_input": {"window": "Notepad", "target": "9"}, "permission_mode": mode}
def run_main(d):
    sys.stdin = io.StringIO(json.dumps(d)); o = sys.stdout; sys.stdout = io.StringIO(); hook.PENDING.clear(); hook.main(); out = sys.stdout.getvalue(); sys.stdout = o; return out
sg2 = perm.sig("click", {"window": "Notepad", "target": "9"})
try: os.remove(os.path.join(tmp, "perm", "approved.json"))
except OSError: pass
o1 = run_main(pm("default")); perm.take("asked", sg2)
c["Claude Code in normal mode: a window that is not in the filter does NOT ask"] = o1 == "" and perm.cc_mode() == "default"
o2 = run_main(pm("bypassPermissions"))
c["Claude Code in bypass: the hook asks nothing"] = o2 == "" and perm.cc_mode() == "bypassPermissions"
o3 = run_main(pm("auto"))
c["Claude Code in auto: a normal window does not ask"] = o3 == "" and perm.cc_mode() == "auto"
hook.policy.blocked_category = lambda p, t="": "terminal or console"
o4 = run_main(pm("auto")); perm.take("asked", sg2)
c["Claude Code in auto: a protected window DOES ask"] = '"permissionDecision": "ask"' in o4
o5 = run_main(pm("bypassPermissions"))
c["Claude Code in bypass: not even the protected ones ask"] = o5 == ""
perm.set_cc_mode("auto"); hook._proc = lambda h: "windowsterminal"


# Claude Code's "DON'T ASK AGAIN": it saves one rule per TOOL; the hook trims it to the LAST window it asked about
import shutil
os.environ["PC_CONTROL_MODE"] = "ask"; hook._proc = lambda h: "notepad"; hook.policy.blocked_category = lambda p, t="": None
try: os.remove(os.path.join(tmp, "perm", "approved.json"))
except OSError: pass
proj = tempfile.mkdtemp(prefix="pc-control_proj_"); os.makedirs(os.path.join(proj, ".claude"))
sl = os.path.join(proj, ".claude", "settings.local.json")
hook._find = lambda q: 111                                           # window A
a_in = {"tool_name": "mcp__PC-Control__look", "tool_input": {"window": "A"}, "cwd": proj}
ra = hook.decide(a_in)                                                # asks about A; the hook records that A was the last one for `look`
c["new window A: asks"] = bool(ra) and "THIS window" in ra
perm.take("asked", perm.sig("look", a_in["tool_input"]))
time.sleep(0.05)
json.dump({"permissions": {"allow": ["Bash(ls)", "mcp__PC-Control__look"]}, "model": "x"}, open(sl, "w"))   # the user chooses "don't ask again"
hook.convert_rules(a_in)
c["the tool-wide rule is trimmed to window A (remembered in the session)"] = perm.approved(hook.policy.window_key(hook.policy.ASK_ALL, "notepad", 111))
left = json.load(open(sl))
c["... and the tool-wide rule is REMOVED from the file (it does not apply to the others), leaving unrelated entries alone"] = left["permissions"]["allow"] == ["Bash(ls)"] and left["model"] == "x"
c["window A no longer asks"] = hook.decide(a_in) is None
hook._find = lambda q: 222                                           # window B, same tool
b_in = {"tool_name": "mcp__PC-Control__look", "tool_input": {"window": "B"}, "cwd": proj}
rb = hook.decide(b_in)
c["window B (same tool) STILL asks"] = bool(rb)
perm.take("asked", perm.sig("look", b_in["tool_input"]))
# rules that are NOT a "don't ask again": the installer's and those that predate the question
json.dump({"permissions": {"allow": ["mcp__PC-Control__windows"]}}, open(sl, "w")); hook.convert_rules(a_in)
c["the windows rule (list only) is left alone"] = json.load(open(sl))["permissions"]["allow"] == ["mcp__PC-Control__windows"]
perm.set_last("click", policy_key := hook.policy.window_key(hook.policy.ASK_ALL, "notepad", 333))
time.sleep(0.05); os.utime(sl, (time.time() - 100, time.time() - 100))
json.dump({"permissions": {"allow": ["mcp__PC-Control__click"]}}, open(sl, "w")); os.utime(sl, (time.time() - 100, time.time() - 100))
hook.convert_rules(a_in)
c["a rule that PREDATES the question (not from the dialog) is not converted"] = json.load(open(sl))["permissions"]["allow"] == ["mcp__PC-Control__click"] and not perm.approved(policy_key)
shutil.rmtree(proj, ignore_errors=True)
os.environ.pop("PC_CONTROL_MODE", None); hook._find = lambda q: 1234; hook._proc = lambda h: "windowsterminal"
hook.policy.blocked_category = lambda p, t="": "terminal or console"


# ---- PermissionRequest hook: answers by itself only when there is NO need to bother the user ----
os.environ.pop("PC_CONTROL_MODE", None)
try: os.remove(os.path.join(tmp, "perm", "approved.json"))
except OSError: pass
def pr(tool_input, mode="auto", name="look", proc="notepad", cat=None):
    hook._find = lambda q: 555; hook._proc = lambda h: proc; hook.policy.blocked_category = lambda p, t="": cat
    d = {"hook_event_name": "PermissionRequest", "tool_name": "mcp__PC-Control__" + name, "tool_input": tool_input, "permission_mode": mode}
    sys.stdin = io.StringIO(json.dumps(d)); o = sys.stdout; sys.stdout = io.StringIO(); hook.PENDING.clear(); hook.main(); out = sys.stdout.getvalue(); sys.stdout = o
    return out
ALLOW = '"behavior": "allow"'
c["PermissionRequest, Claude Code in auto, normal window: answers 'allow' (no need to bother)"] = ALLOW in pr({"window": "Notepad"}, "auto")
c["PermissionRequest, Claude Code in bypass: answers 'allow'"] = ALLOW in pr({"window": "Notepad"}, "bypassPermissions", cat="terminal or console")
c["PermissionRequest, PROTECTED window: does NOT answer (the dialog appears for the user)"] = pr({"window": "PowerShell"}, "auto", proc="windowsterminal", cat="terminal or console") == ""
c["PermissionRequest, Claude Code normal mode and window outside the filter: answers allow (no flicker)"] = ALLOW in pr({"window": "Notepad"}, "default")
perm.approve(hook.policy.window_key(hook.policy.ASK_ALL, "notepad", 555))
c["PermissionRequest, window already remembered: answers 'allow'"] = ALLOW in pr({"window": "Notepad"}, "default")
c["PermissionRequest, windows (list only): answers 'allow'"] = ALLOW in pr({}, "default", name="windows")
perm.mark("asked", perm.sig("click", {"window": "Notepad", "target": "3"}))           # PreToolUse already asked the user (e.g. a sensitive action)
c["PermissionRequest, question already asked by PreToolUse: does NOT answer (the user does)"] = pr({"window": "Notepad", "target": "3"}, "auto", name="click") == ""
perm.take("asked", perm.sig("click", {"window": "Notepad", "target": "3"}))
hook._find = lambda q: 1234; hook._proc = lambda h: "windowsterminal"; hook.policy.blocked_category = lambda p, t="": "terminal or console"
try: os.remove(os.path.join(tmp, "perm", "approved.json"))
except OSError: pass
perm.set_cc_mode("auto")

# through stdin/stdout the way Claude Code calls it
hook.policy.blocked_category = lambda p, t="": "terminal or console"
sys.stdin = io.StringIO(json.dumps(look)); buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
code = hook.main(); sys.stdout = old
out = json.loads(buf.getvalue())["hookSpecificOutput"]
c["output: permissionDecision=ask with a reason (Claude Code shows its dialog)"] = code == 0 and out["permissionDecision"] == "ask" and out["hookEventName"] == "PreToolUse" and "PROTECTED" in out["permissionDecisionReason"]
sys.stdin = io.StringIO("this is not json"); buf = io.StringIO(); sys.stdout = buf
code = hook.main(); sys.stdout = old
c["a hook failure never breaks the tool (exits 0 and silently)"] = code == 0 and buf.getvalue() == ""
os.environ["PC_CONTROL_MODE"] = "bypass"
c["bypass mode: does not ask"] = hook.decide(look) is None

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values())
print(f"hook verification passed ({len(c)} checks)" if ok else "hook verification FAILED")
sys.exit(0 if ok else 1)
