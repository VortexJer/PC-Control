"""Live demo of PC-Control on YOUR screen: a small test window (it never takes the focus) and Claude's orange cursor, with a caption for
each step. Run: python tools/demo.py   (about 40 seconds; the window closes at the end)."""
import os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import uiautomation as auto
import win32gui
from pc_control import actions, core, cursor

PAUSE = 2.6


def main():
    state = os.path.join(tempfile.gettempdir(), "pc-control_demo_state.json")
    app = subprocess.Popen(["powershell.exe", "-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            os.path.join(ROOT, "tests", "test_app2.ps1"), state], creationflags=0x08000000)
    hw = None
    for _ in range(60):
        time.sleep(0.25)
        hits = [h for h, t in core.list_windows() if t.startswith("pc-control-test2")]
        if hits:
            hw = hits[0]; break
    if not hw:
        print("the demo window did not appear"); app.kill(); return 1
    q = str(hw); s = cursor.scale_for(hw)
    time.sleep(0.8)

    def say(text):
        l, t, r, _ = win32gui.GetWindowRect(hw)
        cursor.CURSOR.note(text, (l + r) // 2, t - 40 if t > 80 else t + 400, hold=PAUSE + 0.5, scale=s, owner=hw)
        print("·", text)

    def point(item_id):
        it = core._state[hw]["items"].get(item_id)
        if it is None:
            return
        rel = core.live_rel(hw, it) or it.rect
        l, t = win32gui.GetWindowRect(hw)[:2]
        cursor.CURSOR.pointer(l + (rel[0] + rel[2]) // 2, t + (rel[1] + rel[3]) // 2, click=True, ms=380, scale=s, owner=hw)
        time.sleep(0.5)

    def find_id(name):
        return next(i for i, x in core._state[hw]["items"].items() if name in x.name)

    try:
        with auto.UIAutomationInitializerInThread():
            r = core.look(q)
            say(f"1/6 look: the window as text, {r.get('tokens')} tokens (an image would cost {r.get('image_cost')})"); time.sleep(PAUSE)

            color = find_id("Color"); point(color)
            say("2/6 type on a drop-down: picks 'Dark blue' by its text, no focus, no real mouse")
            core.type_text(q, "dark", target=color); time.sleep(PAUSE)

            core.look(q); longt = find_id("Long text"); point(longt)
            say("3/6 scroll: the long text, 3 pages down")
            print("  ", actions.scroll(q, str(longt), "down", 3)); time.sleep(PAUSE)

            out = actions.find(q, "City 70")
            say("4/6 look(find='City 70'): found although it is out of view; clicking it scrolls the list to it")
            print("  ", out.replace("\n", " / ")); time.sleep(PAUSE * 0.6)
            print("  ", core.click(q, 1)); time.sleep(PAUSE)

            say("5/6 key ctrl+s: runs the app's own File > Save command (look at the title), no keys pressed")
            print("  ", core.key(q, "ctrl+s")); time.sleep(PAUSE)

            core.look(q)
            out = actions.read_text(q, str(find_id("Long text")), 0, 200)
            say("6/6 read: the whole text of the field, also what is out of view: " + out.split("\n")[0].split(": ", 1)[-1])
            print("  ", out.split("\n")[0]); time.sleep(PAUSE + 1)
    finally:
        cursor.CURSOR.hide(); time.sleep(0.2)
        try:
            win32gui.PostMessage(hw, 0x0010, 0, 0)        # WM_CLOSE: the demo window only
        except Exception:
            pass
        time.sleep(0.5)
        if app.poll() is None:
            app.kill()
    print("demo finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
