"""Inspect a disposable Notepad/Paint window without moving the user's input.

Print capabilities only; never type, click or select the user's existing documents.
"""
import argparse
import contextlib
import os
from pathlib import Path
import sys
import tempfile
import time
sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import win32gui
from pc_control import core
from input_watch import InputWatch


def probe(app, exercise=False):
    documents = Path(__file__).resolve().parents[1] / ".goldentokens" / "probe-documents"
    documents.mkdir(parents=True, exist_ok=True)
    # Keep disposable files if the user chooses to work in a test window.
    with contextlib.nullcontext(tempfile.mkdtemp(prefix="probe-", dir=documents)) as tmp:
        file = Path(tmp) / ("pc-control-probe.txt" if app == "notepad" else "pc-control-probe.bmp")
        if app == "notepad":
            file.write_text("PC-Control disposable input probe\nLinea: ñ 漢字 🙂\n", encoding="utf-8")
        else:
            from PIL import Image
            Image.new("RGB", (320, 240), "white").save(file)
        before = set(core.top_windows())
        fg = win32gui.GetForegroundWindow()
        mouse = __import__("win32api").GetCursorPos()
        watch = InputWatch().__enter__()
        result = core.open_behind(f'{app}.exe "{file}"', app)
        launch_events_end = len(watch.events)
        print("LAUNCH foreground events", [(event, hwnd) for event, hwnd, _ in watch.events if event == 3])
        owned = [w["hwnd"] for w in result["windows"] if "pc-control-probe" in w["title"]]
        if not owned:
            watch.__exit__()
            raise RuntimeError("The disposable probe window did not appear")
        leave_open = set()
        writing_events_start = writing_events_end = None
        native = None
        try:
            for h in owned:
                win32gui.ShowWindow(h, 4)  # SW_SHOWNOACTIVATE: visible on the real desktop
                win32gui.SetWindowPos(h, 0, 50, 80, 1100, 800, 0x0010)  # normal visible test window, without activation
                time.sleep(1)
                rect = win32gui.GetWindowRect(h)
                items = core.walk_uia(h, rect, budget_s=8)
                print("APP", app, "class", win32gui.GetClassName(h), "items", len(items))
                if app == "mspaint":
                    for it in items:
                        if any(s in it.name.lower() for s in ("lienzo", "canvas", "imagen", "image", "dibujo")):
                            print("CANVAS CANDIDATE", it.kind, repr(it.name)[:90], it.rect)
                    children = []
                    win32gui.EnumChildWindows(h, lambda c, _: children.append((win32gui.GetClassName(c), win32gui.GetWindowText(c)[:50], win32gui.GetWindowRect(c))), None)
                    print("CHILD WINDOWS", children[:12])
                for it in items:
                    if it.kind not in ("edit", "document", "custom", "pane"):
                        continue
                    ctrl = it.ctrl
                    native, cls = core._native(ctrl)
                    patterns = []
                    for name in ("GetValuePattern", "GetTextPattern", "GetLegacyIAccessiblePattern"):
                        try:
                            pat = getattr(ctrl, name)()
                            if pat:
                                value = getattr(pat, "Value", None)
                                patterns.append(f"{name}:present,readonly={getattr(pat, 'IsReadOnly', '?')},value={repr(value)[:70]}")
                        except Exception as e:
                            patterns.append(f"{name}:{e.__class__.__name__}")
                    print(it.kind, repr(it.name)[:100], "native", native, cls, "; ".join(patterns))
                output = Path(__file__).resolve().parents[1] / ".goldentokens" / "modern-probes"
                output.mkdir(parents=True, exist_ok=True)
                from PIL import Image
                before_img = core.grab(h, win32gui.GetWindowRect(h))
                if before_img is not None:
                    Image.fromarray(before_img[:, :, ::-1]).save(output / f"{app}-before.png")
                if exercise and app == "notepad":
                    if "pc-control-probe" not in win32gui.GetWindowText(h) or win32gui.GetForegroundWindow() == h:
                        leave_open.add(h)
                        raise RuntimeError("the probe became active or changed documents; no automated typing attempted")
                    core.look(str(h))
                    item = next(it for it in core._state[h]["items"].values() if it.kind == "document")
                    native, _ = core._native(item.ctrl)
                    native_model = win32gui.SendMessage(native, 0x003D, 0, -16)
                    document = None
                    print("RichEdit native object model exposed", bool(native_model))
                    if native_model:
                        try:
                            import pythoncom
                            import win32com.client
                            pythoncom.CoInitialize()
                            document = win32com.client.Dispatch(pythoncom.ObjectFromLresult(native_model, pythoncom.IID_IDispatch, 0))
                            print("TOM range accessible", repr(document.Range(0, 2).Text))
                            print("TOM story", repr(document.Range(0, 0x7FFFFFFF).Text), "length", document.Range(0, 0).StoryLength,
                                  "WM length", win32gui.SendMessage(native, 0x000E, 0, 0))
                        except Exception as error:
                            print("TOM object query failed", error.__class__.__name__, str(error)[:120])
                    old = core.field_value(item.ctrl)
                    old_story = document.Range(0, 0x7FFFFFFF).Text if document is not None else None
                    win32gui.SendMessage(native, 0x00B1, 2, 6)
                    writing_events_start = len(watch.events)
                    out = core.type_text(str(h), "VISIBLE_PC_CONTROL_APPEND", item.id)
                    time.sleep(0.1)
                    writing_events_end = len(watch.events)
                    print("WRITE foreground events", [(event, hwnd) for event, hwnd, _ in watch.events[writing_events_start:] if event == 3])
                    value = core.field_value(item.ctrl)
                    print("TOM resulting value", repr(value))
                    assert value.rstrip("\r\n").endswith("VISIBLE_PC_CONTROL_APPEND"), out
                    if old_story is not None:
                        new_story = document.Range(0, 0x7FFFFFFF).Text
                        assert new_story == old_story + "VISIBLE_PC_CONTROL_APPEND", (repr(old_story), repr(new_story), out)
                    assert core._edit_selection(native) == (2, 6), "user selection moved"
                    print("NOTEPAD REAL: append confirmed; selection (2,6) preserved;", out)
                    entered = core.key(str(h), "enter")
                    spaced = core.key(str(h), "space")
                    assert entered.startswith("ok") and spaced.startswith("ok"), (entered, spaced)
                    assert core._edit_selection(native) == (2, 6), "range keys changed selection"
                    if old_story is not None:
                        assert document.Range(0, 0x7FFFFFFF).Text == old_story + "VISIBLE_PC_CONTROL_APPEND\r ", "range key text differs"
                    value = core.field_value(item.ctrl)
                    writing_events_end = len(watch.events)
                    print("RichEdit enter/space confirmed through independent ranges; selection preserved")
                    time.sleep(6)
                    if core.field_value(item.ctrl) != value or win32gui.GetForegroundWindow() == h or "pc-control-probe" not in win32gui.GetWindowText(h):
                        leave_open.add(h)
                    else:
                        core.type_text(str(h), old, item.id, replace=True)
                        win32gui.SendMessage(native, 0x00B9, 0, 0)  # only this untouched disposable document
                if exercise and app == "mspaint" and before_img is not None:
                    # This disposable 320x240 bitmap is centered in the workspace, below the ribbon.
                    import numpy as np
                    mask = (before_img.min(axis=2) > 248).astype("uint8") * 255
                    mask[:300] = 0
                    import cv2
                    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask)
                    choices = [(i, st) for i, st in enumerate(stats[1:], 1)
                               if 200 <= st[2] <= 700 and 150 <= st[3] <= 500 and st[4] > 30000]
                    if not choices:
                        print("PAINT: canvas region not identified; no drawing attempted")
                    else:
                        _, st = max(choices, key=lambda pair: pair[1][4])
                        x, y, w, hh = map(int, st[:4])
                        time.sleep(6)
                        # No prior core.look scale: these are raw window pixels.
                        core._state.pop(h, None)
                        out = core.drag(str(h), [(x+30, y+30), (x+w-30, y+hh-30)], "rect", delay=0.01)
                        time.sleep(1)
                        after = core.grab(h, win32gui.GetWindowRect(h))
                        changed = int(np.count_nonzero(np.any(before_img[y:y+hh, x:x+w] != after[y:y+hh, x:x+w], axis=2)))
                        Image.fromarray(after[:, :, ::-1]).save(output / "mspaint-after.png")
                        print("PAINT REAL: canvas pixel changes", changed, "|", out)
                after_img = core.grab(h, win32gui.GetWindowRect(h))
                if after_img is not None:
                    Image.fromarray(after_img[:, :, ::-1]).save(output / f"{app}-after.png")
                time.sleep(8)
            print("foreground unchanged", fg == win32gui.GetForegroundWindow(), "mouse unchanged", mouse == __import__("win32api").GetCursorPos())
            print("owned probe windows", len(owned))
            time.sleep(0.1)
            print("WinEvent foreground activations of probe", sum(event == 3 and hwnd in owned for event, hwnd, _ in watch.events))
            print("WinEvent activations during launch", sum(event == 3 and hwnd in owned for event, hwnd, _ in watch.events[:launch_events_end]))
            if exercise and app == "notepad":
                assert not any(hwnd in (*owned, native) for _, hwnd, _ in watch.events), "focus verification inconclusive: probe activation/focus observed"
                assert not any(event == 3 and hwnd in owned for event, hwnd, _ in watch.events[:launch_events_end]), "probe activated during launch"
                assert not any(hwnd in (*owned, native) for _, hwnd, _ in watch.events[writing_events_start:writing_events_end]), "probe acquired focus during writing"
                print("Visible modern focus/input verification passed")
        finally:
            watch.__exit__()
            for h in owned:
                if h not in before and h not in leave_open and win32gui.IsWindow(h) and win32gui.GetForegroundWindow() != h and "pc-control-probe" in win32gui.GetWindowText(h):
                    win32gui.PostMessage(h, 0x0010, 0, 0)
            time.sleep(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("app", choices=("notepad", "mspaint"))
    ap.add_argument("--exercise", action="store_true", help="write/draw only in the disposable probe document")
    args = ap.parse_args()
    probe(args.app, args.exercise)
