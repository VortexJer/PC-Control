"""The user can move or resize the window in the middle of an action and everything keeps working.

The test window lives off-screen (it is never seen). It has a button anchored at the bottom right: when resized, it is laid out again.
"""
import json, os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["PC_CONTROL_CURSOR"] = "off"                       # this test draws nothing on screen
from pc_control import core, server
import threading
import uiautomation as auto
import win32con, win32gui, win32process

state = os.path.join(tempfile.gettempdir(), "pc-control_resize_state.json")
S = lambda: json.load(open(state, encoding="utf-8-sig"))
cmd = ["powershell.exe", "-STA", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
       "-File", os.path.join(HERE, "test_app.ps1"), state, "-X", "-6000"]
p = subprocess.Popen(cmd, creationflags=0x08000000)
c = {}
def move_resize(hw, x=None, y=None, w=None, h=None):
    l, t, r, b = win32gui.GetWindowRect(hw)
    win32gui.SetWindowPos(hw, 0, l if x is None else x, t if y is None else y, (r - l) if w is None else w, (b - t) if h is None else h,
                          win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
    time.sleep(0.5)
try:
    hw = None
    for _ in range(150):
        time.sleep(0.1)
        f = []
        win32gui.EnumWindows(lambda h, _: f.append(h) if win32gui.GetWindowText(h) == "pc-control-test" and win32process.GetWindowThreadProcessId(h)[1] == p.pid else None, None)
        if f: hw = f[0]; break
    assert hw, "the test window did not appear"
    time.sleep(0.6)
    with auto.UIAutomationInitializerInThread():
        core.look(str(hw)); items = core._state[hw]["items"]
        anc = next(i for i, x in items.items() if x.name == "Anchored")
        it = items[anc]
        rel0 = core.live_rel(hw, it); stale_cx, stale_cy = (rel0[0] + rel0[2]) // 2, (rel0[1] + rel0[3]) // 2
        size0 = core._size(win32gui.GetWindowRect(hw))

        # 1) move the window: the relative position stays valid; the screen one moves with it
        l0, t0 = win32gui.GetWindowRect(hw)[:2]
        move_resize(hw, x=-5200, y=300)
        rel1 = core.live_rel(hw, it)
        sx0 = it.ctrl.BoundingRectangle.left
        c["move: the relative position is kept"] = rel1 == rel0
        c["move: the screen one moves with the window"] = sx0 == win32gui.GetWindowRect(hw)[0] + rel0[0]
        n = S()["anchored"]; core._post_click(hw, stale_cx, stale_cy); time.sleep(0.4)
        c["move: a click by coordinates still hits"] = S()["anchored"] == n + 1

        # 2) resize: the controls are laid out again; the stored position is no longer valid, the live one is
        move_resize(hw, w=size0[0] + 300, h=size0[1] + 200)
        rel2 = core.live_rel(hw, it)
        c["resize: the anchored button really moved"] = rel2 is not None and rel2 != rel0 and rel2[0] > rel0[0] + 200 and rel2[1] > rel0[1] + 120
        n = S()["anchored"]; core._post_click(hw, stale_cx, stale_cy); time.sleep(0.4)
        c["resize: the OLD position no longer hits (control: justifies the correction)"] = S()["anchored"] == n
        n = S()["anchored"]; core._post_click(hw, (rel2[0] + rel2[2]) // 2, (rel2[1] + rel2[3]) // 2); time.sleep(0.4)
        c["resize: the LIVE position hits"] = S()["anchored"] == n + 1

        # 3) a click by coordinates from an earlier image is rejected with a clear message instead of landing somewhere else
        n = S()["anchored"]
        msg = core.click(str(hw), (stale_cx, stale_cy)); time.sleep(0.3)
        c["resize: coordinates from an old image are rejected"] = "changed size" in msg and S()["anchored"] == n
        # 4) an OCR element (no control) is only valid if the window is the same size as when it was looked at
        ocr_item = core.Item("ocr", "Anchored", rel0, None, "ocr", True)
        c["resize: stale OCR element -> None"] = core.live_rel(hw, ocr_item) is None
        core.look(str(hw))                                                       # looking again refreshes everything
        items2 = core._state[hw]["items"]; anc2 = next(i for i, x in items2.items() if x.name == "Anchored")
        c["after looking again, the OCR element is valid again"] = core.live_rel(hw, core.Item("ocr", "x", rel2, None, "ocr", True)) == rel2
        # 5) click by id still works after resizing (through the control, not by coordinates)
        n = S()["anchored"]; core.click(str(hw), anc2); time.sleep(0.4)
        c["click by id after resizing"] = S()["anchored"] == n + 1
        # 5b) the window really changes (a ribbon that collapses): the user is warned and, once they put it back, it carries on by itself
        move_resize(hw, w=size0[0] + 260, h=size0[1])
        core.look(str(hw)); its = core._state[hw]["items"]
        ext = next((i for i, x in its.items() if x.name == "Extra"), None)
        c["wide window: the Extra button exists"] = ext is not None
        big = win32gui.GetWindowRect(hw)
        move_resize(hw, w=size0[0], h=size0[1])                                  # the user shrinks it: Extra disappears
        c["narrow window: Extra is gone (the situation that must be warned about)"] = core.live_rel(hw, its[ext]) is None
        t0 = time.time(); msg = server._ensure_layout(hw, False, its[ext], timeout=1.2); dt = time.time() - t0
        ow, oh = core._size(big)
        c["warning: the message asks not to touch the app any more, to put it back as it was, and gives its size"] = bool(msg) and "not to touch" in msg and "as it was" in msg and f"{ow}x{oh}" in msg and "can no longer find 'Extra'" in msg
        c["warning: it waits the requested time before giving up"] = 1.0 <= dt < 3.0
        n = S()["extra"]
        threading.Timer(1.0, lambda: win32gui.SetWindowPos(hw, 0, 0, 0, ow, oh, win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE | win32con.SWP_NOMOVE)).start()
        msg2 = server._ensure_layout(hw, False, its[ext], timeout=8)             # the user puts it back as it was: it carries on by itself
        c["warning: once put back as it was, it carries on by itself"] = msg2 is None
        core.click(str(hw), ext); time.sleep(0.4)
        c["warning: and the action is executed (click on Extra)"] = S()["extra"] == n + 1
        c["no changes in the window: no warning and no waiting"] = server._ensure_layout(hw, False, its[ext], timeout=5) is None

        # 5c) the banner lasts 10 s, cannot be disabled and says what it should
        c["the banner lasts 10 seconds"] = server.NOTICE_SECONDS == 10.0
        c["the banner says Claude will not be able to act if it is touched"] = "Claude will not be able to act" in server.notice_text(800, 600) and "800x600" in server.notice_text(800, 600)
        c["there is no setting to disable it"] = not hasattr(server, "settings") and not os.path.exists(os.path.join(os.path.dirname(server.__file__), "settings.py"))
        c["button moved: the user is told and given the coordinates"] = "coordinates corrected" in core.moved_note((10, 70, 50, 90), (10, 60, 50, 80)) and core.moved_note((10, 70, 50, 90), (12, 71, 52, 91)) == ""
        c["a button read while the window was minimized is not reported as moved"] = core.moved_note((-31871, -31986, -31800, -31960), (400, 16, 470, 40)) == ""
        move_resize(hw, w=size0[0], h=size0[1])

        # 6) make it small: the controls that do not fit stop being accessible -> clear message, not a lost click
        move_resize(hw, w=size0[0] - 220, h=size0[1] - 120)
        rel3 = core.live_rel(hw, items2[anc2])
        c["small window: the element is resolved live or reported (never old coordinates)"] = rel3 is None or rel3 != rel0
finally:
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values()) and len(c) >= 20
print("resize verification passed" if ok else "resize verification FAILED")
sys.exit(0 if ok else 1)
