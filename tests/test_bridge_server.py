from __future__ import annotations

import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


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

    def test_normalize_preserves_empty_result_string(self):
        result = self.server._normalize_agent_result({"result": "", "content": "fallback"})

        self.assertEqual(result["result"], "")

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

    def test_run_api_agent_parses_json_headers_body_and_formats_body_tokens(self):
        captured: dict[str, object] = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"done"}}]}'

        def _fake_urlopen(req, timeout):
            captured["request"] = req
            captured["timeout"] = timeout
            return _FakeResponse()

        with mock.patch.object(self.server.urllib.request, "urlopen", side_effect=_fake_urlopen):
            result = self.server._run_api_agent(
                "openai",
                "hello from prompt",
                model="gpt-5",
                timeout=12,
                api_url="https://example.test/v1/chat/completions",
                api_key="token-123",
                api_headers='{"X-Trace":"1"}',
                api_body='{"model":"{model}","messages":[{"role":"user","content":"{prompt}"}]}',
            )

        request = captured["request"]
        self.assertEqual(result["result"], "done")
        self.assertEqual(captured["timeout"], 12)
        self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
        self.assertEqual(request.headers["X-trace"], "1")
        self.assertTrue(request.headers["Authorization"].startswith("Bearer "))
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"model": "gpt-5", "messages": [{"role": "user", "content": "hello from prompt"}]},
        )

    def test_normalize_agent_result_supports_choices_and_output_shapes(self):
        choices_result = self.server._normalize_agent_result(
            {"choices": [{"message": {"content": "from choices"}}]}
        )
        output_result = self.server._normalize_agent_result(
            {"output": [{"content": [{"text": "first"}, {"text": "second"}]}]}
        )

        self.assertEqual(choices_result["result"], "from choices")
        self.assertEqual(output_result["result"], "first\nsecond")


if __name__ == "__main__":
    unittest.main()
