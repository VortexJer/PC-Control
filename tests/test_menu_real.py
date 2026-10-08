"""Entries of a REAL Windows MENU (ContextMenuStrip), pressed through accessibility. The test window is created off-screen:
nothing is seen, the mouse and focus are not touched, and none of the user's apps is used."""
import json, os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["PC_CONTROL_CURSOR"] = "off"
from pc_control import core, server
import uiautomation as auto
import win32gui, win32process

state = os.path.join(tempfile.gettempdir(), "pc-control_menu_state.json")
S = lambda: json.load(open(state, encoding="utf-8-sig"))
p = subprocess.Popen(["powershell.exe", "-STA", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                      "-File", os.path.join(HERE, "test_app.ps1"), state, "-X", "-6000"], creationflags=0x08000000)
c = {}
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
        mb = next(i for i, x in items.items() if x.name == "Menu")
        core.click(str(hw), mb); time.sleep(0.8)                         # BM_CLICK on the button: opens the real menu (off-screen)
        # the menu is a SEPARATE window of the same process: it is found the way the accessibility tree would see it
        popups = []
        win32gui.EnumWindows(lambda h, _: popups.append(h) if win32process.GetWindowThreadProcessId(h)[1] == p.pid and h != hw and win32gui.IsWindowVisible(h) else None, None)
        c["the real menu opened (its own window, distinct from the app's)"] = bool(popups)
        entries = {}
        for h in popups:
            for e in core.walk_uia(h, win32gui.GetWindowRect(h), offscreen_ok=True):
                entries[e.name] = e
        c["the tree sees the menu entries (Copy, Synonyms)"] = "Copy" in entries and "Synonyms" in entries
        c["... and they are menu entries"] = entries["Copy"].kind == "menuitem" and entries["Synonyms"].kind == "menuitem"
        core.look(str(hw)); looked = {x.name: x for x in core._state[hw]["items"].values()}
        c["look of the window INCLUDES the entries of the open menu (so they can be pressed by number)"] = "Copy" in looked and looked["Copy"].kind == "menuitem"
        fg0 = win32gui.GetForegroundWindow()
        r_dis = core._click_menu_item(entries["Synonyms"].ctrl, "Synonyms")
        c["disabled entry: it says so clearly and is NOT pressed"] = "is disabled" in r_dis and S().get("menu", 0) == 0
        idc = next(i for i, x in core._state[hw]["items"].items() if x.name == "Copy")
        r_ok = core.click(str(hw), idc); time.sleep(0.6)                 # by the look number, the way Claude would do it
        c["enabled entry: pressed through accessibility (the REAL menu runs its action)"] = r_ok.startswith("ok (menu entry") and S().get("menu", 0) == 1
        c["... without taking focus"] = win32gui.GetForegroundWindow() == fg0
        # text fields: look shows what they contain, and type reads the field back to confirm
        core.look(str(hw)); fid = next(i for i, x in core._state[hw]["items"].items() if x.name == "Name field")
        r_t = core.type_text(str(hw), "Hello ñ 日本", target=fid); time.sleep(0.3)
        c["type: reads the field back and says the text is there (even non-Latin characters)"] = "the field now contains the text" in r_t and "日本" in r_t
        shown = core.look(str(hw), "uia")["text"]
        c["look: shows what a text field contains"] = 'Name field="Hello ñ 日本"' in shown
        ctrl = core._state[hw]["items"][fid].ctrl
        c["read back: warns when the field does NOT contain the text"] = "WARNING: the field does NOT contain" in core._read_back(ctrl, "something else entirely", False)
        c["read back: replace compares the whole field"] = "the field now contains the text" in core._read_back(ctrl, "Hello ñ 日本", True)
finally:
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values()) and len(c) >= 11
print(f"menu-real verification passed ({len(c)} checks)" if ok else "menu-real verification FAILED")
sys.exit(0 if ok else 1)
