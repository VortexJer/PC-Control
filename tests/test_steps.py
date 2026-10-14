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
# a multi-line field read back with its line breaks dropped is NOT a failed typing
real_fv = core.field_value
try:
    core.field_value = lambda ctrl, editable_only=False: "Primera lineaSegunda lineaTercera linea"
    c["multi-line typing is confirmed even if the app reports the field without line breaks"] = "does NOT contain" not in core._read_back(object(), "Primera linea\nSegunda linea\nTercera linea", False)
    c["a really different text is still flagged"] = "does NOT contain" in core._read_back(object(), "otra cosa\ndistinta", False)
finally:
    core.field_value = real_fv
# UI Automation selection (modern apps): the selection is read back from the app; a different one is reported as failed
class _Rng:
    def __init__(self, text): self.text = text; self.selected = False
    def Select(self): self.selected = True
    def GetText(self, n): return self.text
class _Doc:
    def __init__(self, found, held): self.found, self.held = found, held
    def FindText(self, needle, back, ignore): return self.found
class _TP:
    def __init__(self, found, held): self.DocumentRange = _Doc(found, held); self._held = held
    def GetSelection(self): return [_Rng(self._held)]
class _UCtrl:
    IsPassword = False; Name = "Editor"; AutomationId = ""; HelpText = ""
    def __init__(self, tp): self.tp = tp
    def GetTextPattern(self): return self.tp
sv2 = (actions.core.find_window, actions.core._state.copy())
try:
    actions.core.find_window = lambda q: 1
    actions.core._state[1] = {"items": {3: SimpleNamespace(ctrl=_UCtrl(_TP(_Rng("Segunda linea"), "Segunda linea")))}}
    r = actions.select_text("w", 3, "Segunda linea")
    c["a UI Automation selection is confirmed by reading it back"] = r.startswith("selected") and "confirmed by reading it back" in r
    actions.core._state[1] = {"items": {3: SimpleNamespace(ctrl=_UCtrl(_TP(_Rng("Segunda linea"), "otra cosa")))}}
    r = actions.select_text("w", 3, "Segunda linea")
    c["if the app holds another selection it is reported as NOT selected"] = r.startswith("did not select")
finally:
    actions.core.find_window = sv2[0]
    actions.core._state.clear(); actions.core._state.update(sv2[1])
# programs that could not be read minimized are remembered, and open_app then starts them behind the user's windows
import tempfile
from pc_control import server as _srv
_home = tempfile.mkdtemp(prefix="pc-control_behind_")
real_home, real_behind, real_min, real_mode = _srv.HOME, _srv.core.open_behind, _srv.core.open_minimized, _srv.policy.mode
calls3 = []
try:
    _srv.HOME = _home
    c["nothing is remembered at first"] = _srv._learned_behind() == set()
    _srv._learn_behind("Spotify"); _srv._learn_behind("spotify"); _srv._learn_behind("")
    c["a program is remembered once, in lower case, and an empty name is ignored"] = _srv._learned_behind() == {"spotify"} and len(open(_srv._behind_file(), encoding="utf-8").read().split()) == 1
    _srv.policy.mode = lambda: "bypass"
    _srv.core.open_behind = lambda command, exe, **k: calls3.append(("behind", exe)) or {"pid": 1, "windows": [], "note": "x", "foco_intacto": True}
    _srv.core.open_minimized = lambda command, **k: calls3.append(("minimized", command)) or {"pid": 1, "windows": [], "note": "x"}
    _srv.RAW["open_app"]("spotify.exe", confirm=True)
    _srv.RAW["open_app"]("someotherapp.exe", confirm=True)
    _srv.RAW["open_app"]("someotherapp.exe", behind=True, confirm=True)
    c["open_app starts a remembered program behind, an unknown one minimized, and behind=true always behind"] = calls3 == [("behind", "spotify"), ("minimized", "someotherapp.exe"), ("behind", "someotherapp")]
    r = _srv.core.open_behind  # noqa
finally:
    _srv.HOME, _srv.core.open_behind, _srv.core.open_minimized, _srv.policy.mode = real_home, real_behind, real_min, real_mode
    import shutil as _sh; _sh.rmtree(_home, ignore_errors=True)
# closing and reopening an app is only for one that open_app opened a moment ago, never for one the user may be using
import time as _tm
from pc_control import server as _s2
_s2.OPENED.add(11111); _s2.OPENED_AT[11111] = _tm.time()
_s2.OPENED.add(22222); _s2.OPENED_AT[22222] = _tm.time() - 600
try:
    c["an app opened a moment ago counts as fresh"] = _s2._fresh(11111) is True
    c["an app opened long ago (the user may have taken it over) is not fresh"] = _s2._fresh(22222) is False
    c["a window open_app never opened (the user's) is never fresh"] = _s2._fresh(33333) is False
finally:
    for _h in (11111, 22222):
        _s2.OPENED.discard(_h); _s2.OPENED_AT.pop(_h, None)
# a click nobody checks that does nothing is the worst answer: a batch skips the check ONLY where clicks are known to work
from pc_control import server as _s3
seen = []
real_proven, real_raw = _s3._clicks_proven, dict(_s3.RAW)
try:
    _s3.RAW["click"] = lambda window, **kw: seen.append(_s3._FAST["skip"]) or "ok | changes: x | effect observed"
    _s3.RAW["key"] = lambda window, **kw: seen.append(_s3._FAST["skip"]) or "ok | changes: x | effect observed"
    _s3._clicks_proven = lambda w: False
    _s3.RAW["steps"]("w", [{"do": "click", "target": "A"}, {"do": "click", "target": "B"}, {"do": "click", "target": "C"}])
    c["on a program whose clicks were never seen to work, no click of a batch skips its check"] = seen == [False, False, False]
    seen.clear(); _s3._clicks_proven = lambda w: True
    _s3.RAW["steps"]("w", [{"do": "click", "target": "A"}, {"do": "click", "target": "B"}, {"do": "click", "target": "C"}])
    c["where clicks are proven, the middle ones skip the slow check and the last one keeps it"] = seen == [True, True, False]
    seen.clear(); _s3._clicks_proven = lambda w: False
    _s3.RAW["steps"]("w", [{"do": "key", "name": "tab"}, {"do": "key", "name": "tab"}, {"do": "click", "target": "C"}])
    c["keys are not the risk: they skip the check even on an unknown program"] = seen[:2] == [True, True]
finally:
    _s3._clicks_proven = real_proven; _s3.RAW.clear(); _s3.RAW.update(real_raw)
import win32gui as _wg
real_cls, real_proc = _wg.GetClassName, _s3._proc
try:
    _wg.GetClassName = lambda h: "ApplicationFrameWindow"
    c["a Store app (ApplicationFrameWindow) is pressed through UI Automation straight away"] = _s3._press_directly(1) is True
    _wg.GetClassName = lambda h: "Notepad"; _s3._proc = lambda h: "someprog"
    c["an ordinary program is not, until a plain click is seen to do nothing"] = _s3._press_directly(1) is False
    _s3._MOUSE["ignored"].add(("Notepad", "someprog"))
    c["a program that ignored a plain click is pressed directly from then on"] = _s3._press_directly(1) is True
finally:
    _wg.GetClassName, _s3._proc = real_cls, real_proc
    _s3._MOUSE["ignored"].discard(("Notepad", "someprog"))
# a Store app has two windows (content + frame): whichever one is named, an app PC-Control opened is recognised as its own
real_fam, real_gtp = _s3._family_roots, _s3.win32process.GetWindowThreadProcessId
try:
    _s3._family_roots = lambda h: {h, 6}
    _s3.win32process.GetWindowThreadProcessId = lambda h: (0, 4242)
    _s3.OPENED.add(6); _s3.OPENED_AT[6] = _tm.time()
    c["the frame is recognised as ours when only its content window was recorded (and the other way round)"] = _s3._mine(5) is True and _s3._fresh(5) is True
    _s3.OPENED_AT[6] = _tm.time() - 600
    c["...but only fresh for two minutes"] = _s3._mine(5) is True and _s3._fresh(5) is False
finally:
    _s3._family_roots, _s3.win32process.GetWindowThreadProcessId = real_fam, real_gtp
    _s3.OPENED.discard(6); _s3.OPENED_AT.pop(6, None)
# found on a real machine: a click on "Close" ends the window, and asking about it afterwards crashed the answer; typing that could not be read back said ok
c["a window that no longer exists (a click on Close ended it) gives no key and no crash"] = _s3._mouse_key(999999999) is None and _s3._press_directly(999999999) is False
c["typing that cannot be read back is reported as NOT CONFIRMED, not as ok"] = "NOT CONFIRMED" in _s3.verdict("ok (3 characters through WM_CHAR; unconfirmed)", "no changes")
c["typing that WAS read back still says so"] = "confirmed by reading" in _s3.verdict("ok (WM_SETTEXT) | read back: the field now contains the text (\"x\")", "no changes")
c["typing that could not be read back but made the window change like typing does is reported as probably arrived"] = "probably arrived" in _s3.verdict("ok (11 characters through WM_CHAR to the window; unconfirmed)", "+ Ln 1, Col 12 | - Ln 1, Col 1")
c["a window that is not a Chromium/Electron one has no page widget to type into"] = _s3.core.chromium_input(0) == 0
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
ok = len(c) == 35 and all(c.values())
print(f"steps verification {'passed' if ok else 'FAILED'}")
sys.exit(0 if ok else 1)