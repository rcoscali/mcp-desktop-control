from __future__ import annotations

import importlib.util
import inspect
import os
from pathlib import Path
import sys
import types
import unittest


def _load_bridge_server():
    """Load bridge/server.py with a stub FastMCP when mcp isn't installed."""
    class FakeFastMCP:
        """Minimal FastMCP stand-in used to import the bridge module in tests."""
        def __init__(self, _name: str):
            pass

        def tool(self):
            def decorator(func):
                return func

            return decorator

        def run(self, transport=None):
            return transport

    sys.modules.setdefault("mcp", types.ModuleType("mcp"))
    sys.modules.setdefault("mcp.server", types.ModuleType("mcp.server"))
    fastmcp = types.ModuleType("mcp.server.fastmcp")
    fastmcp.FastMCP = FakeFastMCP
    sys.modules["mcp.server.fastmcp"] = fastmcp

    path = Path(__file__).resolve().parents[1] / "bridge" / "server.py"
    spec = importlib.util.spec_from_file_location("bridge_server_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class BridgeServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _load_bridge_server()

    def setUp(self):
        self._env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_default_claude_argv_keeps_existing_flags(self):
        argv = self.server._build_windows_agent_argv(
            "hello world",
            allowed_tools=["mcp__desktop-control__*"],
            permission_mode="acceptEdits",
            resume="session-1",
            add_dir=["/mnt/c/work"],
            model="sonnet",
        )

        self.assertEqual(
            argv,
            [
                "claude.exe",
                "-p",
                "hello world",
                "--output-format",
                "json",
                "--allowedTools",
                "mcp__desktop-control__*",
                "--permission-mode",
                "acceptEdits",
                "--model",
                "sonnet",
                "--resume",
                "session-1",
                "--add-dir",
                "/mnt/c/work",
            ],
        )

    def test_custom_agent_template_expands_placeholders_without_splitting_prompt(self):
        os.environ["ASK_WIN_AGENT_BIN"] = "codex.exe"
        os.environ["ASK_WIN_AGENT_ARGS"] = "exec --json {model} {prompt}"

        argv = self.server._build_windows_agent_argv(
            "open notepad and say hello",
            model="gpt-5",
        )

        self.assertEqual(
            argv,
            ["codex.exe", "exec", "--json", "gpt-5", "open notepad and say hello"],
        )

    def test_normalize_generic_json_payload(self):
        result = self.server._normalize_agent_result(
            {"content": "done", "sessionId": "abc", "turns": 2}
        )

        self.assertEqual(result["result"], "done")
        self.assertEqual(result["session_id"], "abc")
        self.assertEqual(result["num_turns"], 2)
        self.assertFalse(result["is_error"])

    def test_ask_windows_agent_does_not_expose_command_or_api_overrides(self):
        parameters = inspect.signature(self.server.ask_windows_agent).parameters

        for name in (
            "cli_command",
            "cli_args_template",
            "api_url",
            "api_key",
            "api_headers",
            "api_body",
        ):
            self.assertNotIn(name, parameters)


if __name__ == "__main__":
    unittest.main()
