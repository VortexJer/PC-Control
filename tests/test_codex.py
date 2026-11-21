"""Isolated installation lifecycle, CLI routing and permission regressions."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pc_control import cli, codex, policy


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(root / "codex"),
            "PC_CONTROL_CODEX_SKILLS_HOME": str(root / "skills"),
            "PC_CONTROL_CLAUDE_HOME": str(root / "claude"), "PC_CONTROL_HOME": str(root / "data")})
        self.env.start()
        self.calls = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def runner(self, args):
        self.calls.append(args)
        return 0, "ok"

    def install(self, **kwargs):
        return codex.install(runner=self.runner, python="PY", **kwargs)

    def test_registration(self):
        self.install()
        self.assertEqual(self.calls, [["mcp", "add", "PC-Control", "--env", "PC_CONTROL_MODE=follow",
            "--env", "PC_CONTROL_CLIENT=codex", "--env", "PYTHONUTF8=1", "--", "PY", "-m", "pc_control.server"]])
        self.assertFalse(Path(cli.claude_home()).exists())

    def test_idempotent_and_exact_uninstall(self):
        p = codex.paths()
        md = Path(p["md"])
        md.parent.mkdir(parents=True)
        md.write_text("# Existing notes\n", encoding="utf-8")
        config = md.parent / "config.toml"
        config.write_text('[mcp_servers.other]\ncommand = "other"\n', encoding="utf-8")
        self.install(mode="ask")
        self.install(mode="strict")
        self.assertEqual(md.read_text(encoding="utf-8").count(cli.MD_START), 1)
        self.assertEqual(codex.status(self.runner)["mode"], "strict")
        skill = Path(p["skill"])
        neighbor = skill.parent / "user-notes.txt"
        neighbor.write_text("keep", encoding="utf-8")
        Path(cli.data_home()).mkdir()
        codex.uninstall(runner=self.runner)
        self.assertEqual(md.read_text(encoding="utf-8").strip(), "# Existing notes")
        self.assertTrue(config.exists())
        self.assertTrue(neighbor.exists())
        self.assertFalse(skill.exists())
        self.assertFalse(Path(p["record"]).exists())
        self.assertFalse(Path(cli.data_home()).exists())             # no other client: runtime data goes (like Claude's uninstall)
        # with Claude Code still installed, the shared runtime data stays
        self.install(mode="ask")
        Path(cli.data_home()).mkdir(exist_ok=True)
        claude_record = Path(cli._paths()["record"]); claude_record.parent.mkdir(parents=True, exist_ok=True)
        claude_record.write_text("{}", encoding="utf-8")
        codex.uninstall(runner=self.runner)
        self.assertTrue(Path(cli.data_home()).exists())
        claude_record.unlink()

    def test_created_agents_removed(self):
        self.install()
        codex.uninstall(runner=self.runner)
        self.assertFalse(Path(codex.paths()["md"]).exists())

    def test_claude_removal_preserves_codex_data(self):
        self.install()
        cli.install(runner=self.runner, python="PY")
        data = Path(cli.data_home())
        data.mkdir(parents=True, exist_ok=True)
        cli.uninstall(runner=self.runner)
        self.assertTrue(data.exists())
        self.assertTrue(Path(codex.paths()["record"]).exists())

    def test_permission_accept_and_decline(self):
        from types import SimpleNamespace
        from pc_control import server
        class Answer:
            def __init__(self, action, allow):
                self.action, self.allow = action, allow
            async def elicit(self, *args):
                return SimpleNamespace(action=self.action, data=SimpleNamespace(allow=self.allow))
        with patch.dict(os.environ, {"PC_CONTROL_CLIENT": "codex"}):
            for action, allow, expected in (("accept", True, True), ("accept", False, False), ("decline", True, False)):
                self.assertEqual(asyncio.run(server._ask_user(Answer(action, allow),
                    policy.NeedsPermission("test"), "look", {})), expected)

    def test_foreign_skill_preserved(self):
        skill = Path(codex.paths()["skill"])
        skill.parent.mkdir(parents=True)
        skill.write_text("foreign", encoding="utf-8")
        self.install()
        self.assertEqual(skill.read_text(), "foreign")
        codex.uninstall(runner=self.runner)
        self.assertEqual(skill.read_text(), "foreign")

    def test_invalid_options_no_side_effect(self):
        for opts in ({"scope": "project"}, {"scope": "local"}, {"allow_reads": True}, {"mode": "bad"}):
            with self.assertRaises(ValueError):
                self.install(**opts)
        self.assertEqual(self.calls, [])
        self.assertFalse(Path(codex.paths()["record"]).exists())

    def test_failed_registration_no_artifacts(self):
        with self.assertRaises(RuntimeError):
            codex.install(runner=lambda _: (1, "missing codex"))
        self.assertFalse(Path(codex.paths()["md"]).exists())

    def test_failed_removal_keeps_artifacts(self):
        self.install()
        with self.assertRaises(RuntimeError):
            codex.uninstall(runner=lambda _: (1, "error"))
        self.assertTrue(Path(codex.paths()["record"]).exists())

    def test_reinstall_codex_only(self):
        self.install(mode="ask")
        with patch.object(codex, "run", self.runner), patch.object(cli, "install", side_effect=AssertionError("Claude touched")):
            cli.reinstall()
        self.assertEqual(cli._load(codex.paths()["record"], {})["mode"], "ask")

    def test_reinstall_both(self):
        self.install(mode="strict")
        cli._save(cli._paths()["record"], {"mode": "ask"})
        with patch.object(codex, "run", self.runner), patch.object(cli, "install", return_value=["claude ok"]) as install:
            lines = cli.reinstall()
        install.assert_called_once()
        self.assertIn("claude ok", lines)
        self.assertEqual(cli._load(codex.paths()["record"], {})["mode"], "strict")

    def test_cli_routes(self):
        for command, name in (("install", "install"), ("uninstall", "uninstall"), ("reinstall", "reinstall")):
            with patch.object(codex, name, return_value=["ok"]) as fn:
                self.assertEqual(cli.main([command, "--client", "codex"]), 0)
                fn.assert_called_once()
        with patch.object(codex, "status", return_value={"registered": True}) as fn:
            self.assertEqual(cli.main(["status", "--client", "codex"]), 0)
            fn.assert_called_once()
        with patch.object(cli, "install", return_value=["ok"]) as fn:
            self.assertEqual(cli.main(["install"]), 0)
            fn.assert_called_once()

    def test_codex_follow_ignores_claude_mode(self):
        with patch.dict(os.environ, {"PC_CONTROL_CLIENT": "codex", "PC_CONTROL_MODE": "follow"}):
            with patch("pc_control.perm.cc_mode", return_value="bypassPermissions"):
                self.assertEqual(policy.mode(), "auto")
        with patch.dict(os.environ, {"PC_CONTROL_CLIENT": "claude", "PC_CONTROL_MODE": "follow"}):
            with patch("pc_control.perm.cc_mode", return_value="bypassPermissions"):
                self.assertEqual(policy.mode(), "bypass")

    def test_permission_no_claude_fallback(self):
        from pc_control import server
        ToolError = server.ToolError
        class Unsupported:
            async def elicit(self, *args):
                raise RuntimeError("unsupported")
        with patch.dict(os.environ, {"PC_CONTROL_CLIENT": "codex"}), patch.object(server.perm, "take") as take:
            with self.assertRaisesRegex(ToolError, "Not executed: Codex"):
                asyncio.run(server._ask_user(Unsupported(), policy.NeedsPermission("test"), "look", {}))
            take.assert_not_called()

    def test_skill_has_no_claude_hook_promises(self):
        text = codex.skill_text()
        self.assertIn("MCP elicitation", text)
        self.assertNotIn("Repeat EXACTLY", text)
        self.assertNotIn("Codex will show its", text)


if __name__ == "__main__":
    result = unittest.main(exit=False)
    if result.result.wasSuccessful():
        print("Codex integration tests passed")
    sys.exit(not result.result.wasSuccessful())
