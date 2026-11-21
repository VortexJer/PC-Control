"""A missing acknowledgement never permits an unprotected action; partial hooks are removed."""
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pc_control import focus, server


class FocusSafetyTests(unittest.TestCase):
    def test_native_global_hooks_rejected(self):
        dll = focus.library()
        self.assertFalse(dll.pc_focus_install(0))
        self.assertFalse(dll.pc_focus_probe_install(0))

    def test_no_ack_removes_temporary_probe(self):
        dll = MagicMock()
        dll.pc_focus_probe_install.return_value = 123
        with patch.object(focus, "library", return_value=dll):
            guard = focus.FocusGuard("test")
        user32 = MagicMock()
        user32.GetPropW.return_value = None
        with patch.object(focus, "process_arch", return_value="native"), \
             patch.object(guard, "attach", return_value=True), \
             patch("win32process.GetWindowThreadProcessId", return_value=(10, 20)), \
             patch("win32gui.PostMessage") as posted, patch.object(focus.ctypes, "WinDLL", return_value=user32):
            with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
                guard.verify_window(1, timeout=0)
            self.assertEqual(posted.call_args.args[:3], (1, 0, 0x50434342))
        dll.pc_focus_remove.assert_called_once_with(123)

    def test_failed_window_verification_removes_cbt_hook(self):
        with patch.object(focus, "library", return_value=MagicMock()), \
             patch.object(focus, "process_arch", return_value="native"), \
             patch("win32process.GetWindowThreadProcessId", return_value=(10, 20)), \
             patch.object(focus.FocusGuard, "attach", return_value=True), \
             patch.object(focus.FocusGuard, "verify_window", side_effect=RuntimeError("no ack")), \
             patch.object(focus.FocusGuard, "close") as close:
            with self.assertRaisesRegex(RuntimeError, "no ack"):
                focus.FocusGuard.for_window(1)
            close.assert_called_once()

    def test_mcp_mutation_not_called_without_verified_guard(self):
        action = Mock()
        with patch.object(focus.FocusGuard, "for_window", side_effect=RuntimeError("cannot protect")):
            with self.assertRaisesRegex(RuntimeError, "cannot protect"):
                server._act(1, False, action)
        action.assert_not_called()


if __name__ == "__main__":
    result = unittest.main(exit=False)
    if result.result.wasSuccessful():
        print("focus safety verification passed")
    sys.exit(not result.result.wasSuccessful())
