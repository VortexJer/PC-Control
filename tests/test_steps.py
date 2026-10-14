"""steps(): several actions in one call. Uses fake tool functions: it checks the order, the stops (failure, no effect, permission, unknown
action) and the short answer for the middle steps. No window is touched."""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from pc_control import server, policy

calls, c = [], {}
def fake(name, reply):
    def f(window, **kw):
        calls.append((name, window, kw))
        r = reply(kw) if callable(reply) else reply
        if isinstance(r, Exception):
            raise r
        return r
    return f

steps = server.RAW["steps"]
real = dict(server.RAW)
try:
    server.RAW.update(click=fake("click", "pressed Save | changes: a dialog | did its job"), type=fake("type", "typed | changes: x | ok"),
                      key=fake("key", "sent enter | changes: closed | ok"))
    out = steps("w", [{"do": "click", "target": "Save"}, {"do": "type", "target": "Name", "text": "hi", "replace": True}, {"do": "key", "name": "enter"}])
    c["runs in order with its arguments"] = [x[0] for x in calls] == ["click", "type", "key"] and calls[1][2] == {"target": "Name", "text": "hi", "replace": True}
    lines = out.splitlines()
    c["middle steps are short, the last one is complete"] = "changes" not in lines[0] and "changes" in lines[2]
    calls.clear()
    server.RAW["click"] = fake("click", "did not press: gone | NOT EXECUTED")
    out = steps("w", [{"do": "click", "target": "A"}, {"do": "key", "name": "enter"}])
    c["stops at a step that was not executed"] = len(calls) == 1 and "STOPPED" in out
    calls.clear()
    server.RAW["click"] = fake("click", policy.NeedsPermission("x", "q"))
    out = steps("w", [{"do": "click", "target": "A"}, {"do": "key", "name": "enter"}])
    c["stops when the user's permission is needed (never asks for the whole batch)"] = len(calls) == 1 and "permission" in out
    out = steps("w", [{"do": "format", "target": "A"}])
    c["unknown action stops"] = "unknown action" in out
    try:
        steps("w", [])
        c["empty list is refused"] = False
    except server.ToolError:
        c["empty list is refused"] = True
finally:
    server.RAW.clear(); server.RAW.update(real)
# typing into a classic edit box: a bare LF must reach it as CR LF (and as Enter through WM_CHAR)
from types import SimpleNamespace
from pc_control import core
sent, posted = [], []
saved = (core.win32gui.SendMessage, core.win32gui.PostMessage, core._native, core._read_back, core.find_window, core._state.copy(), core.win32gui.GetClassName)
try:
    core.win32gui.SendMessage = lambda h, m, w, l: sent.append((m, l))
    core.win32gui.PostMessage = lambda h, m, w, l: posted.append((m, w))
    core._native = lambda ctrl: (555, "edit")
    core._read_back = lambda *a: ""
    core.find_window = lambda q: 1
    core.win32gui.GetClassName = lambda h: "Notepad"
    core._state[1] = {"items": {7: SimpleNamespace(ctrl=object())}}
    core.type_text("w", "uno\ndos\r\ntres\rcuatro", target=7)
    c["LF, CR LF and CR all arrive as CR LF in an edit box"] = sent[-1][1] == "uno\r\ndos\r\ntres\r\ncuatro"
    core.type_text("w", "a\nb", target=7, replace=True)
    c["same with replace"] = sent[-1][1] == "a\r\nb"
    core._native = lambda ctrl: (0, "")
    core.type_text("w", "a\nb")
    c["WM_CHAR path sends Enter (CR) for a line break"] = [w for m, w in posted] == [97, 13, 98]
finally:
    core.win32gui.SendMessage, core.win32gui.PostMessage, core._native, core._read_back, core.find_window = saved[:5]
    core.win32gui.GetClassName = saved[6]
    core._state.clear(); core._state.update(saved[5])
# select_text: the first match is selected (EM_SETSEL), a missing one is reported, a password field is refused
from pc_control import actions
sel = []
sv = (actions.core.find_window, actions.core._native, actions._wm_text, actions.win32gui.SendMessage, actions.core._state.copy())
class _Ctrl:
    IsPassword = False; Name = "Notes"; AutomationId = "notes"; HelpText = ""
    def GetTextPattern(self): return None
try:
    actions.core.find_window = lambda q: 1
    actions.core._state[1] = {"items": {3: SimpleNamespace(ctrl=_Ctrl())}}
    actions.core._native = lambda ctrl: (9, "edit")
    actions._wm_text = lambda nh: "uno\r\ndos\r\ntres"
    actions.win32gui.SendMessage = lambda h, m, w, l: sel.append((m, w, l))
    r = actions.select_text("w", 3, "DOS")
    c["select_text selects the first match, ignoring case"] = sel == [(0xB1, 5, 8)] and r.startswith("selected")
    c["a missing text is reported, not selected"] = "NOT EXECUTED" in actions.select_text("w", 3, "cuatro") and len(sel) == 1
    _Ctrl.IsPassword = True
    c["a password field is never selected into"] = "secret" in actions.select_text("w", 3, "uno") and len(sel) == 1
finally:
    actions.core.find_window, actions.core._native, actions._wm_text, actions.win32gui.SendMessage = sv[:4]
    actions.core._state.clear(); actions.core._state.update(sv[4])
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
ok = len(c) == 12 and all(c.values())
print(f"steps verification {'passed' if ok else 'FAILED'}")
sys.exit(0 if ok else 1)