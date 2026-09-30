"""Contract tests for writing the suite into the clients.

The invariants that matter: a secret never lands as a literal, a value the user already
had is never lost or guessed, an entry outside the suite is never touched, and either
every file is written or none is.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isc_ai import suite as isc_suite
from isc_ai.cli import main

SUITE = {
    "version": 1,
    "clients": {
        "opencode": {
            "binary": "opencode-probe",
            "config": {"user": "{config_home}/opencode/opencode.json", "mcp_key": "mcp",
                       "entry_style": "opencode", "reference_style": "{env:NAME}",
                       "write_scope": "user"},
            "install": {"manager": "npm-probe", "argv": []},
        },
        "vscode": {
            "binary": "code-probe",
            "config": {"user": "{appdata}/Code/User/mcp.json", "project": "{project}/.vscode/mcp.json",
                       "mcp_key": "servers", "entry_style": "vscode",
                       "reference_style": "${input:NAME}", "write_scope": "user"},
            "install": {"manager": "winget-probe", "argv": []},
        },
        "claude": {
            "binary": "claude-probe",
            "config": {"user": "{home}/.claude.json", "project": "{project}/.mcp.json",
                       "mcp_key": "mcpServers", "entry_style": "claude",
                       "reference_style": "${NAME}", "write_scope": "project"},
            "install": {"manager": "npm-probe", "argv": []},
        },
    },
    "servers": {
        "alpha": {
            "transport": "stdio",
            "launch": {"command": "alpha-server", "args": ["--readonly"]},
            "aliases": ["alpha-legacy", "vendor/alpha"],
            "settings": {
                "ALPHA_TOKEN": {"scope": "global", "secret": True, "required": True,
                                "aliases": ["LEGACY_TOKEN"]},
                "ALPHA_HOST": {"scope": "global", "secret": False, "required": True,
                               "aliases": ["LEGACY_HOST"]},
            },
        },
        "beta": {"transport": "http", "launch": {"url": "https://example.invalid/mcp"},
                 "aliases": [], "settings": {}},
    },
    "out_of_suite": {},
}


class SuiteTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "project"
        (self.home / ".config" / "opencode").mkdir(parents=True)
        (self.home / "AppData" / "Roaming" / "Code" / "User").mkdir(parents=True)
        self.project.mkdir()
        self.suite_file = self.root / "suite.json"
        self.suite_file.write_text(json.dumps(SUITE), encoding="utf-8")
        self.environ = {"APPDATA": str(self.home / "AppData" / "Roaming")}
        self.opencode = self.home / ".config" / "opencode" / "opencode.json"
        self.vscode = self.home / "AppData" / "Roaming" / "Code" / "User" / "mcp.json"
        self.claude = self.project / ".mcp.json"

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def plan(self):
        report, writes = isc_suite.plan(project_dir=self.project, home=self.home,
                                       environ=self.environ, suite_path=self.suite_file)
        return report, writes

    def apply(self):
        return isc_suite.apply(project_dir=self.project, home=self.home,
                              environ=self.environ, suite_path=self.suite_file)

    def read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))


class RenderingTests(SuiteTestCase):
    def test_each_client_gets_its_own_entry_shape(self):
        self.apply()
        opencode = self.read(self.opencode)["mcp"]["alpha"]
        self.assertEqual(opencode["type"], "local")
        self.assertEqual(opencode["command"], ["alpha-server", "--readonly"])
        self.assertTrue(opencode["enabled"])

        vscode = self.read(self.vscode)["servers"]["alpha"]
        self.assertEqual(vscode["type"], "stdio")
        self.assertEqual((vscode["command"], vscode["args"]), ("alpha-server", ["--readonly"]))

        claude = self.read(self.claude)["mcpServers"]["alpha"]
        self.assertEqual((claude["command"], claude["args"]), ("alpha-server", ["--readonly"]))
        self.assertNotIn("type", claude)

    def test_http_servers_are_rendered_as_remote_or_http(self):
        self.apply()
        self.assertEqual(self.read(self.opencode)["mcp"]["beta"],
                         {"type": "remote", "url": "https://example.invalid/mcp", "enabled": True})
        self.assertEqual(self.read(self.vscode)["servers"]["beta"],
                         {"type": "http", "url": "https://example.invalid/mcp"})

    def test_one_scope_per_client_so_nothing_is_registered_twice(self):
        self.apply()
        # vscode writes the user file only: a workspace file would run the same servers again.
        self.assertFalse((self.project / ".vscode" / "mcp.json").exists())
        # claude writes the project file only: the global file holds unrelated state.
        self.assertFalse((self.home / ".claude.json").exists())


class SecretTests(SuiteTestCase):
    def test_a_secret_is_written_as_a_reference_in_each_client_syntax(self):
        self.apply()
        self.assertEqual(self.read(self.opencode)["mcp"]["alpha"]["environment"]["ALPHA_TOKEN"],
                         "{env:ALPHA_TOKEN}")
        self.assertEqual(self.read(self.claude)["mcpServers"]["alpha"]["env"]["ALPHA_TOKEN"],
                         "${ALPHA_TOKEN}")
        self.assertEqual(self.read(self.vscode)["servers"]["alpha"]["env"]["ALPHA_TOKEN"],
                         "${input:alpha-token}")

    def test_vscode_gets_a_native_password_input_declaration(self):
        self.apply()
        inputs = self.read(self.vscode)["inputs"]
        entry = next(item for item in inputs if item["id"] == "alpha-token")
        self.assertEqual(entry["type"], "promptString")
        self.assertTrue(entry["password"])

    def test_an_existing_literal_secret_is_replaced_by_a_reference(self):
        self.write(self.vscode, {"servers": {"alpha": {"command": "alpha-server",
                                                       "env": {"ALPHA_TOKEN": "real-token-value"}}}})
        report = self.apply()
        text = self.vscode.read_text(encoding="utf-8")
        self.assertNotIn("real-token-value", text, "the literal must be gone from the file")
        self.assertIn("${input:alpha-token}", text)
        self.assertIn("vscode: declared secret input 'alpha-token'", report["changes"])

    def test_an_input_declaration_is_not_duplicated_on_a_second_run(self):
        self.apply()
        first = self.read(self.vscode)["inputs"]
        self.apply()
        self.assertEqual(self.read(self.vscode)["inputs"], first)


class ValueTests(SuiteTestCase):
    def test_an_existing_value_is_preserved_not_overwritten(self):
        self.write(self.opencode, {"mcp": {"alpha": {"command": ["old"],
                                                     "environment": {"ALPHA_HOST": "kept.invalid"}}}})
        self.apply()
        self.assertEqual(self.read(self.opencode)["mcp"]["alpha"]["environment"]["ALPHA_HOST"],
                         "kept.invalid")

    def test_a_value_under_an_old_variable_name_is_carried_over(self):
        self.write(self.claude, {"mcpServers": {"alpha-legacy": {"command": "x",
                                                                "env": {"LEGACY_HOST": "from.alias"}}}})
        self.apply()
        entry = self.read(self.claude)["mcpServers"]["alpha"]
        self.assertEqual(entry["env"]["ALPHA_HOST"], "from.alias")
        self.assertNotIn("LEGACY_HOST", entry["env"])

    def test_a_value_present_in_one_client_is_propagated_to_another(self):
        self.write(self.opencode, {"mcp": {"alpha": {"command": ["alpha-server"],
                                                     "environment": {"ALPHA_HOST": "shared.invalid"}}}})
        self.apply()
        self.assertEqual(self.read(self.claude)["mcpServers"]["alpha"]["env"]["ALPHA_HOST"],
                         "shared.invalid")

    def test_a_missing_required_value_is_reported_and_never_invented(self):
        report = self.apply()
        codes = {item["code"] for item in report["findings"]}
        self.assertIn("VALUE_UNKNOWN", codes)
        written = self.read(self.claude)["mcpServers"]["alpha"]["env"]["ALPHA_HOST"]
        self.assertIn("isc-ai secrets set", written, "a placeholder, not a guess")


class MergeTests(SuiteTestCase):
    def test_an_alias_is_renamed_and_its_unknown_fields_survive(self):
        self.write(self.opencode, {"mcp": {"vendor/alpha": {"command": ["old"], "timeout": 99,
                                                            "environment": {"ALPHA_HOST": "h"}}}})
        report = self.apply()
        servers = self.read(self.opencode)["mcp"]
        self.assertNotIn("vendor/alpha", servers)
        self.assertEqual(servers["alpha"]["timeout"], 99, "a field the suite does not manage stays")
        self.assertEqual(servers["alpha"]["command"], ["alpha-server", "--readonly"])
        self.assertIn("opencode: renamed 'vendor/alpha' to 'alpha'", report["changes"])

    def test_entries_outside_the_suite_are_left_exactly_as_they_were(self):
        original = {"command": ["something"], "env": {"X": "y"}, "weird": True}
        self.write(self.opencode, {"mcp": {"unmanaged": dict(original)}})
        self.apply()
        self.assertEqual(self.read(self.opencode)["mcp"]["unmanaged"], original)

    def test_a_second_run_changes_nothing(self):
        self.apply()
        digests = {path: path.read_bytes() for path in (self.opencode, self.vscode, self.claude)}
        report = self.apply()
        self.assertEqual(report["changes"], [])
        for path, content in digests.items():
            self.assertEqual(path.read_bytes(), content, "apply must be idempotent")


class TransactionTests(SuiteTestCase):
    def test_the_plan_writes_nothing(self):
        self.write(self.opencode, {"mcp": {}})
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        report, writes = self.plan()
        self.assertTrue(report["changes"])
        self.assertTrue(writes)
        after = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_a_failure_halfway_leaves_every_file_as_it_was(self):
        self.write(self.opencode, {"mcp": {"keep": {"command": ["x"]}}})
        original = self.opencode.read_bytes()
        with patch("isc_ai.setup._replace_file", side_effect=OSError("disk full")):
            with self.assertRaises(Exception):
                self.apply()
        self.assertEqual(self.opencode.read_bytes(), original)
        self.assertFalse(self.claude.exists())

    def test_the_command_reports_the_plan_and_only_writes_with_apply(self):
        stdout = io.StringIO()
        argv = ["suite", "--project-dir", str(self.project), "--suite", str(self.suite_file)]
        with patch("sys.stdout", stdout), patch("sys.stderr", io.StringIO()), \
             patch("pathlib.Path.home", return_value=self.home), \
             patch.dict(os.environ, self.environ, clear=True):
            code = main(argv)
        self.assertEqual(code, 0)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["mode"], "check")
        self.assertFalse(self.opencode.exists(), "check must not create anything")


class AtlassianEntryTests(SuiteTestCase):
    """The packaged atlassian-cloud entry, written into synthetic client files.

    Jira Cloud and a separate Confluence (Server/Data Center here) is a real shape:
    writing the suite must keep it, not fold the Confluence values into the Jira ones.
    """

    NAME = "atlassian-cloud"

    def setUp(self):
        super().setUp()
        from isc_ai import audit
        spec = audit.load_suite()["servers"][self.NAME]
        suite = json.loads(json.dumps(SUITE))
        suite["servers"] = {self.NAME: spec}
        self.suite_file.write_text(json.dumps(suite), encoding="utf-8")

    def live_entry(self):
        return {"type": "local", "command": ["uvx", "mcp-atlassian==0.23.1"], "enabled": True,
                "environment": {
                    "JIRA_URL": "https://example.atlassian.net",
                    "JIRA_USERNAME": "someone@example.invalid",
                    "JIRA_API_TOKEN": "{env:JIRA_API_TOKEN}",
                    "CONFLUENCE_URL": "https://confluence.example.invalid",
                    "CONFLUENCE_PERSONAL_TOKEN": "{env:CONFLUENCE_PERSONAL_TOKEN}",
                }}

    def test_the_launch_is_written_pinned(self):
        self.apply()
        self.assertEqual(self.read(self.opencode)["mcp"][self.NAME]["command"],
                         ["uvx", "mcp-atlassian==0.23.1"])
        claude = self.read(self.claude)["mcpServers"][self.NAME]
        self.assertEqual((claude["command"], claude["args"]), ("uvx", ["mcp-atlassian==0.23.1"]))

    def test_jira_cloud_with_confluence_server_is_left_as_it_is(self):
        self.write(self.opencode, {"mcp": {self.NAME: self.live_entry()}})
        report = self.apply()
        self.assertFalse([change for change in report["changes"] if change.startswith("opencode:")],
                         "the suite must not revert a configuration that already matches it")
        self.assertEqual(self.read(self.opencode)["mcp"][self.NAME], self.live_entry())

    def test_the_confluence_url_is_its_own_value_not_the_jira_url(self):
        self.write(self.opencode, {"mcp": {self.NAME: self.live_entry()}})
        self.apply()
        env = self.read(self.claude)["mcpServers"][self.NAME]["env"]
        self.assertEqual(env["JIRA_URL"], "https://example.atlassian.net")
        self.assertEqual(env["CONFLUENCE_URL"], "https://confluence.example.invalid")
        self.assertEqual(env["CONFLUENCE_PERSONAL_TOKEN"], "${CONFLUENCE_PERSONAL_TOKEN}")

    def test_without_confluence_nothing_is_invented_or_asked_for(self):
        report = self.apply()
        env = self.read(self.opencode)["mcp"][self.NAME]["environment"]
        self.assertNotIn("CONFLUENCE_URL", env, "an optional value nobody has is omitted, not guessed")
        unknown = [item for item in report["findings"] if item["code"] == "VALUE_UNKNOWN"]
        self.assertFalse([item for item in unknown if "CONFLUENCE_" in item["message"]])
        # An optional secret nobody configured is not referenced in any client, and
        # VS Code gets no input for it, so nobody is prompted for a token they lack.
        self.assertNotIn("CONFLUENCE_PERSONAL_TOKEN", env)
        self.assertNotIn("CONFLUENCE_PERSONAL_TOKEN", self.read(self.claude)["mcpServers"][self.NAME]["env"])
        vscode = self.read(self.vscode)
        self.assertNotIn("CONFLUENCE_PERSONAL_TOKEN", vscode["servers"][self.NAME]["env"])
        self.assertNotIn("confluence-personal-token", [item["id"] for item in vscode.get("inputs", [])])

    def test_required_secrets_are_still_rendered_with_nothing_configured(self):
        self.apply()
        self.assertEqual(self.read(self.opencode)["mcp"][self.NAME]["environment"]["JIRA_API_TOKEN"],
                         "{env:JIRA_API_TOKEN}")
        self.assertEqual(self.read(self.claude)["mcpServers"][self.NAME]["env"]["JIRA_API_TOKEN"],
                         "${JIRA_API_TOKEN}")
        vscode = self.read(self.vscode)
        self.assertEqual(vscode["servers"][self.NAME]["env"]["JIRA_API_TOKEN"], "${input:jira-api-token}")
        self.assertIn("jira-api-token", [item["id"] for item in vscode["inputs"]])

    def test_an_optional_secret_one_client_has_is_kept_and_propagated(self):
        self.write(self.opencode, {"mcp": {self.NAME: self.live_entry()}})
        report = self.apply()
        self.assertEqual(self.read(self.opencode)["mcp"][self.NAME]["environment"]["CONFLUENCE_PERSONAL_TOKEN"],
                         "{env:CONFLUENCE_PERSONAL_TOKEN}")
        vscode = self.read(self.vscode)
        self.assertEqual(vscode["servers"][self.NAME]["env"]["CONFLUENCE_PERSONAL_TOKEN"],
                         "${input:confluence-personal-token}")
        self.assertIn("vscode: declared secret input 'confluence-personal-token'", report["changes"])

    def test_an_optional_literal_secret_counts_as_configured_and_becomes_a_reference(self):
        self.write(self.claude, {"mcpServers": {self.NAME: {"command": "uvx", "args": ["mcp-atlassian==0.23.1"],
                                                            "env": {"CONFLUENCE_PERSONAL_TOKEN": "literal-pat"}}}})
        self.apply()
        self.assertNotIn("literal-pat", self.claude.read_text(encoding="utf-8"))
        self.assertEqual(self.read(self.claude)["mcpServers"][self.NAME]["env"]["CONFLUENCE_PERSONAL_TOKEN"],
                         "${CONFLUENCE_PERSONAL_TOKEN}")

    def test_a_global_claude_entry_keeps_confluence_apart_from_jira(self):
        # The legacy global 'jira' entry: ATLASSIAN_* still renames to JIRA_*, but a
        # CONFLUENCE_URL next to it is no longer folded into (and overwriting) JIRA_URL.
        global_file = self.home / ".claude.json"
        self.write(global_file, {"mcpServers": {"jira": {"command": "uvx", "args": ["mcp-atlassian"], "env": {
            "ATLASSIAN_BASE_URL": "https://example.atlassian.net",
            "CONFLUENCE_URL": "https://confluence.example.invalid",
        }}}})
        before = global_file.read_bytes()
        report = isc_suite.prune_claude_global(home=self.home, environ=self.environ,
                                               suite_path=self.suite_file, apply_changes=False)
        self.assertIn("renamed setting ATLASSIAN_BASE_URL to JIRA_URL in 'jira'", report["changes"])
        self.assertFalse([change for change in report["changes"] if "CONFLUENCE_URL" in change])
        self.assertEqual(global_file.read_bytes(), before, "check mode writes nothing")


if __name__ == "__main__":
    unittest.main()
