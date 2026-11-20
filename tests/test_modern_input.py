"""Deterministic safety/capability tests; no real user windows are changed."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pc_control import core


class ModernInputTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = Mock()
        self.ctrl.HasKeyboardFocus = False
        self.ctrl.IsPassword = False
        self.ctrl.GetValuePattern.return_value = None
        self.ctrl.GetLegacyIAccessiblePattern.return_value = None
        self.ctrl.GetSelectionItemPattern.return_value = None
        item = NS(ctrl=self.ctrl)
        self.patches = [patch.object(core, "find_window", return_value=1),
            patch.object(core.win32gui, "GetClassName", return_value="Chrome_WidgetWin_1"),
            patch.object(core.win32gui, "GetForegroundWindow", return_value=2),
            patch.object(core, "_native", return_value=(0, "")),
            patch.dict(core._state, {1: {"items": {7: item}, "rect": (0, 0, 800, 600)}}),
            patch("pc_control.actions.select_option", return_value=None),
            patch.object(core, "popup_windows", return_value=[]),
            patch("pc_control.richedit.type_text", return_value=None),
            patch.object(core.win32gui, "PostMessage"), patch.object(core.win32gui, "SendMessage")]
        self.mocks = [p.start() for p in self.patches]
        core.win32gui.SendMessage.return_value = 12

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def type(self, text="new", replace=False):
        return core.type_text("test", text, 7, replace)

    def pattern(self, **kwargs):
        return NS(IsReadOnly=False, State=0, SetValue=Mock(return_value=True), **kwargs)

    def test_value_pattern_without_focus(self):
        pat = self.pattern(Value="old")
        self.ctrl.GetValuePattern.return_value = pat
        self.assertIn("UI Automation SetValue", self.type())
        pat.SetValue.assert_called_once_with("oldnew")
        self.ctrl.SetFocus.assert_not_called()
        core.win32gui.PostMessage.assert_not_called()

    def test_native_nonedit_also_uses_value(self):
        pat = self.pattern(Value="old")
        self.ctrl.GetValuePattern.return_value = pat
        with patch.object(core, "_native", return_value=(9, "xamlhost")):
            self.type(replace=True)
        pat.SetValue.assert_called_once_with("new")

    def test_legacy_fallback(self):
        self.ctrl.GetValuePattern.side_effect = RuntimeError("no ValuePattern")
        pat = self.pattern(Value="old")
        self.ctrl.GetLegacyIAccessiblePattern.return_value = pat
        self.assertIn("LegacyIAccessible.SetValue", self.type())
        pat.SetValue.assert_called_once_with("oldnew")
        self.assertEqual(core.field_value(self.ctrl), "old")

    def test_missing_value_never_erased_by_append(self):
        pat = self.pattern(Value=None)
        self.ctrl.GetValuePattern.return_value = pat
        self.assertTrue(self.type().startswith("did not type"))
        pat.SetValue.assert_not_called()
        self.assertIn("SetValue", self.type(replace=True))
        pat.SetValue.assert_called_once_with("new")

    def test_readonly_and_secret_legacy_not_written(self):
        for state in (0x40, 0x20000000):
            pat = self.pattern(Value="keep")
            pat.State = state
            self.ctrl.GetLegacyIAccessiblePattern.return_value = pat
            self.assertTrue(self.type().startswith("did not type"))
            pat.SetValue.assert_not_called()

    def test_setter_failure_never_routes_to_other_field(self):
        pat = self.pattern(Value="old")
        pat.SetValue.return_value = False
        self.ctrl.GetValuePattern.return_value = pat
        self.assertTrue(self.type().startswith("did not type"))
        self.ctrl.SetFocus.assert_not_called()
        core.win32gui.PostMessage.assert_not_called()

    def test_invalid_target_no_blind_fallback(self):
        self.assertTrue(core.type_text("test", "x", 55).startswith("did not type"))
        core.win32gui.PostMessage.assert_not_called()

    def test_foreground_field_yields_to_typing(self):
        self.ctrl.HasKeyboardFocus = True
        with patch.object(core.win32gui, "GetForegroundWindow", return_value=1), patch.object(core, "_idle_ms", return_value=0):
            self.assertIn("editing this field", self.type())
        self.ctrl.GetValuePattern.assert_not_called()

    def test_click_and_shortcut_yield_to_active_window_input(self):
        from pc_control import actions
        with patch.object(core.win32gui, "GetForegroundWindow", return_value=1), patch.object(core, "_idle_ms", return_value=0):
            self.assertIn("did not press", core.click("test", (10, 10)))
            self.assertIn("did not press", core.invoke("test", 7))
            self.assertIn("did not press", actions.shortcut("test", "ctrl+a"))
        core.win32gui.PostMessage.assert_not_called()
        core.win32gui.SendMessage.assert_not_called()

    def test_native_selection_preserved(self):
        with patch.object(core, "_native", return_value=(9, "richeditd2dpt")), \
             patch.object(core, "_edit_selection", return_value=(70000, 70004)), \
             patch.object(core, "_input_tick", return_value=123):
            self.type()
        self.assertEqual(core.win32gui.SendMessage.call_args_list[-1].args, (9, 0x00B1, 70000, 70004))
        core.win32gui.SendMessage.assert_any_call(9, 0x00B1, 12, 12)

    def test_rich_edit_range_precedes_selection_messages(self):
        with patch.object(core, "_native", return_value=(9, "richeditd2dpt")), \
             patch("pc_control.richedit.type_text", return_value="ok (RichEdit TOM range)"):
            self.assertIn("TOM range", self.type())
        core.win32gui.SendMessage.assert_not_called()

    def test_changed_rich_edit_document_is_not_silently_typed_into(self):
        core._state[1]["title"] = "intended.txt - Notepad"
        with patch.object(core, "_native", return_value=(9, "richeditd2dpt")), \
             patch.object(core.win32gui, "GetWindowText", return_value="different.txt - Notepad"), \
             patch("pc_control.richedit.type_text") as write:
            self.assertIn("document/tab title changed", self.type())
            write.assert_not_called()

    def test_unsaved_marker_alone_does_not_change_document_identity(self):
        core._state[1]["title"] = "intended.txt - Notepad"
        with patch.object(core, "_native", return_value=(9, "richeditd2dpt")), \
             patch.object(core.win32gui, "GetWindowText", return_value="*intended.txt - Notepad"), \
             patch("pc_control.richedit.type_text", return_value="ok (RichEdit TOM range)"):
            self.assertTrue(self.type().startswith("ok"))

    def test_new_user_selection_is_not_undone(self):
        with patch.object(core, "_native", return_value=(9, "edit")), \
             patch.object(core, "_edit_selection", return_value=(2, 4)), \
             patch.object(core, "_input_tick", side_effect=[123, 124]):
            self.type()
        self.assertNotEqual(core.win32gui.SendMessage.call_args_list[-1].args, (9, 0x00B1, 2, 4))

    def test_unknown_user_input_state_refuses_native_write(self):
        with patch.object(core, "_native", return_value=(9, "edit")), \
             patch.object(core, "_edit_selection", return_value=(2, 4)), \
             patch.object(core, "_input_tick", return_value=None):
            self.assertIn("did not type", self.type())
        core.win32gui.SendMessage.assert_not_called()

    def test_held_modifier_is_busy_even_without_recent_input(self):
        with patch.object(core, "_idle_ms", return_value=5000), patch.object(core, "_buttons_down", return_value=False), \
             patch.object(core, "_keys_down", return_value=True):
            self.assertFalse(core.wait_user_quiet(timeout=0))

    def test_side_mouse_buttons_are_input(self):
        with patch.object(core.win32api, "GetAsyncKeyState", side_effect=lambda vk: 0x8000 if vk == 5 else 0):
            self.assertTrue(core._buttons_down())

    def test_shared_gui_thread_focus_does_not_target_another_window(self):
        with patch.object(core, "_thread_focus", return_value=25), patch.object(core, "_root_window", return_value=2), \
             patch.object(core.win32gui, "GetWindow", return_value=0), patch.object(core, "chromium_input", return_value=0):
            self.assertEqual(core._focus_hwnd(1), 0)

    def test_focus_inside_owned_dialog_is_in_scope(self):
        with patch.object(core, "_thread_focus", return_value=25), patch.object(core, "_root_window", return_value=3), \
             patch.object(core.win32gui, "GetWindow", side_effect=lambda h, flag: 1 if h == 3 else 0):
            self.assertEqual(core._focus_hwnd(1), 25)

    def test_unresolved_key_and_blind_text_never_post_to_zero_or_other_window(self):
        with patch.object(core, "_focus_hwnd", return_value=0), patch.object(core, "wait_user_quiet", return_value=True):
            self.assertIn("did not press", core.key("test", "enter"))
            self.assertIn("did not type", core.type_text("test", "text"))
        core.win32gui.PostMessage.assert_not_called()

    def test_rich_edit_enter_space_use_ranges_and_delete_yields(self):
        item = core._state[1]["items"][7]
        item.kind, item.id, item.name, item.aid = "document", 7, "Editor", "editor"
        with patch.object(core, "_native", return_value=(9, "richeditd2dpt")), patch.object(core, "is_secret_field", return_value=False), \
             patch("pc_control.richedit.type_text", return_value="ok (RichEdit TOM range)") as write:
            self.assertIn("TOM range", core.key("test", "enter"))
            self.assertEqual(write.call_args.args[1], "\r")
            self.assertIn("TOM range", core.key("test", "space"))
            self.assertEqual(write.call_args.args[1], " ")
            self.assertIn("did not press", core.key("test", "backspace"))
        core.win32gui.PostMessage.assert_not_called()

    def test_rich_edit_keys_do_not_edit_behind_a_dialog(self):
        item = core._state[1]["items"][7]
        item.kind, item.id = "document", 7
        with patch.object(core, "_native", return_value=(9, "richeditd2dpt")), patch.object(core, "is_secret_field", return_value=False), \
             patch.object(core, "popup_windows", return_value=[3]):
            self.assertIn("dialog/menu", core.key("test", "enter"))
        core.win32gui.PostMessage.assert_not_called()

    def test_drag_rejects_outside_before_any_message(self):
        with patch.object(core.win32gui, "IsIconic", return_value=False), \
             patch.object(core.win32gui, "GetWindowRect", return_value=(0, 0, 800, 600)):
            for points in ([(-1, 20), (10, 20)], [(10, 20), (900, 20)]):
                self.assertIn("outside", core.drag("test", points))
        core.win32gui.PostMessage.assert_not_called()

    def test_modern_paint_refuses_unsupported_drag(self):
        def enum(h, callback, arg):
            callback(2, arg)
        with patch.object(core.win32gui, "IsIconic", return_value=False), \
             patch.object(core.win32gui, "GetWindowRect", return_value=(0, 0, 800, 600)), \
             patch.object(core.win32gui, "GetClassName", side_effect=lambda h: "MSPaintApp" if h == 1 else "Microsoft.UI.Content.DesktopChildSiteBridge"), \
             patch.object(core.win32gui, "EnumChildWindows", side_effect=enum):
            self.assertIn("did not drag: this modern Paint", core.drag("test", [(10, 20), (100, 120)]))
        core.win32gui.PostMessage.assert_not_called()

    def test_interrupted_drag_releases_at_last_delivered_point(self):
        calls = []
        def post(h, message, w, point):
            calls.append((h, message, w, point))
            if len(calls) == 3:
                raise RuntimeError("window changed during drag")
        with patch.object(core.win32gui, "IsIconic", return_value=False), \
             patch.object(core.win32gui, "GetWindowRect", return_value=(0, 0, 800, 600)), \
             patch.object(core.win32gui, "ScreenToClient", side_effect=lambda h, p: p), \
             patch.object(core, "wait_user_mouse_idle", return_value=True), \
             patch.object(core, "_child_at", return_value=(9, (10, 20))), \
             patch.object(core.win32gui, "PostMessage", side_effect=post):
            with self.assertRaises(RuntimeError):
                core.drag("test", [(10, 20), (100, 120)])
        self.assertEqual(calls[-1][1], core.win32con.WM_LBUTTONUP)
        self.assertEqual(calls[-1][3], core.win32api.MAKELONG(10, 20))


if __name__ == "__main__":
    result = unittest.main(exit=False)
    if result.result.wasSuccessful():
        print("modern input verification passed")
    sys.exit(not result.result.wasSuccessful())
