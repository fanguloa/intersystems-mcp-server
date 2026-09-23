"""Contract tests for the credential store and the per-client toggles.

The invariants: a value never reaches argv or a report, the project store never stays
outside .gitignore, the environment always wins over the store, and disabling uses each
client's real mechanism rather than a convention we invented.
"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isc_ai import config as isc_config
from isc_ai import secrets as isc_secrets
from isc_ai import toggle as isc_toggle

SUITE = {
    "version": 1,
    "clients": {
        "opencode": {"binary": "opencode-probe",
                     "config": {"user": "{config_home}/opencode/opencode.json", "mcp_key": "mcp",
                                "entry_style": "opencode", "reference_style": "{env:NAME}",
                                "write_scope": "user"},
                     "install": {"manager": "npm", "argv": []}},
        "vscode": {"binary": "code-probe",
                   "config": {"user": "{appdata}/Code/User/mcp.json", "mcp_key": "servers",
                              "entry_style": "vscode", "reference_style": "${input:NAME}",
                              "write_scope": "user"},
                   "install": {"manager": "winget", "argv": []}},
        "claude": {"binary": "claude-probe",
                   "config": {"user": "{home}/.claude.json", "project": "{project}/.mcp.json",
                              "mcp_key": "mcpServers", "entry_style": "claude",
                              "reference_style": "${NAME}", "write_scope": "project"},
                   "install": {"manager": "npm", "argv": []}},
    },
    "servers": {
        "alpha": {"transport": "stdio", "launch": {"command": "alpha-server", "args": ["--x"]},
                  "aliases": ["alpha-legacy"], "settings": {}},
    },
    "out_of_suite": {},
}


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "project"
        self.home.mkdir()
        (self.project / ".git").mkdir(parents=True)
        self.patcher = patch("pathlib.Path.home", return_value=self.home)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)


class SecretStoreTests(StoreTestCase):
    def test_a_value_is_stored_and_never_reported(self):
        isc_secrets.set_secret("JIRA_API_TOKEN", "super-secret-1", home=self.home)
        report = isc_secrets.inventory(project_dir=self.project, home=self.home, environ={})
        self.assertNotIn("super-secret-1", json.dumps(report))
        entry = next(item for item in report["keys"] if item["key"] == "JIRA_API_TOKEN")
        self.assertEqual(entry["sources"], ["global"])

    def test_the_environment_wins_over_the_store(self):
        isc_secrets.set_secret("IRIS_PASSWORD", "from-store", home=self.home)
        report = isc_secrets.inventory(project_dir=self.project, home=self.home,
                                      environ={"IRIS_PASSWORD": "from-shell"})
        entry = next(item for item in report["keys"] if item["key"] == "IRIS_PASSWORD")
        self.assertEqual(entry["effective"], "environment")
        self.assertEqual(entry["sources"], ["environment", "global"])

    def test_the_project_store_overrides_the_global_one(self):
        isc_secrets.set_secret("P4CLIENT", "global-value", home=self.home)
        isc_secrets.set_secret("P4CLIENT", "project-value", scope="project",
                               project_dir=self.project, home=self.home)
        merged = isc_secrets.load(project_dir=self.project, home=self.home)
        self.assertEqual(merged["P4CLIENT"], "project-value")

    def test_writing_the_project_store_adds_it_to_gitignore(self):
        result = isc_secrets.set_secret("P4CLIENT", "x", scope="project",
                                       project_dir=self.project, home=self.home)
        ignored = (self.project / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(isc_secrets.GITIGNORE_LINE, ignored.splitlines())
        self.assertTrue(result["notes"])
        # Idempotent: a second write does not duplicate the line.
        isc_secrets.set_secret("P4USER", "y", scope="project", project_dir=self.project, home=self.home)
        again = (self.project / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(again.count(isc_secrets.GITIGNORE_LINE), 1)

    def test_a_value_is_read_from_stdin_when_there_is_no_terminal(self):
        stream = io.StringIO("piped-secret\n")
        stream.isatty = lambda: False
        self.assertEqual(isc_secrets.read_value("prompt: ", stream), "piped-secret")

    def test_an_empty_or_malformed_request_is_refused(self):
        with self.assertRaisesRegex(isc_config.ConfigError, "not a secret"):
            isc_secrets.set_secret("KEY", "", home=self.home)
        with self.assertRaisesRegex(isc_config.ConfigError, "not a usable environment variable"):
            isc_secrets.set_secret("2BAD", "x", home=self.home)
        with self.assertRaisesRegex(isc_config.ConfigError, "needs a project"):
            isc_secrets.set_secret("KEY", "x", scope="project", home=self.home)
        with self.assertRaisesRegex(isc_config.ConfigError, "is not set"):
            isc_secrets.unset_secret("NEVER_SET", home=self.home)

    def test_unset_removes_only_that_key(self):
        isc_secrets.set_secret("A", "1", home=self.home)
        isc_secrets.set_secret("B", "2", home=self.home)
        isc_secrets.unset_secret("A", home=self.home)
        self.assertEqual(sorted(isc_secrets.load(home=self.home)), ["B"])


class ToggleTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        (self.home / ".config" / "opencode").mkdir(parents=True)
        (self.home / "AppData" / "Roaming" / "Code" / "User").mkdir(parents=True)
        self.suite_file = self.root / "suite.json"
        self.suite_file.write_text(json.dumps(SUITE), encoding="utf-8")
        self.environ = {"APPDATA": str(self.home / "AppData" / "Roaming")}
        self.opencode = self.home / ".config" / "opencode" / "opencode.json"
        self.vscode = self.home / "AppData" / "Roaming" / "Code" / "User" / "mcp.json"
        self.claude = self.home / ".claude.json"
        self.opencode.write_text(json.dumps(
            {"mcp": {"alpha": {"type": "local", "command": ["alpha-server", "--x"], "enabled": True}}}),
            encoding="utf-8")
        self.vscode.write_text(json.dumps(
            {"servers": {"alpha": {"type": "stdio", "command": "alpha-server", "args": ["--x"]}}}),
            encoding="utf-8")
        self.claude.write_text(json.dumps({"projects": {str(self.project).replace("\\", "/"): {}}}),
                               encoding="utf-8")

    def toggle(self, enable, **kwargs):
        return isc_toggle.toggle("alpha", enable, project_dir=self.project, home=self.home,
                                 environ=self.environ, suite_path=self.suite_file, **kwargs)

    def read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_each_client_is_switched_through_its_own_mechanism(self):
        report = self.toggle(False, apply_changes=True)
        self.assertFalse(self.read(self.opencode)["mcp"]["alpha"]["enabled"])
        self.assertNotIn("alpha", self.read(self.vscode)["servers"])
        project_key = next(iter(self.read(self.claude)["projects"]))
        self.assertIn("alpha", self.read(self.claude)["projects"][project_key]["disabledMcpjsonServers"])
        joined = " | ".join(report["changes"])
        self.assertIn("enabled=false", joined)
        self.assertIn("removal is off", joined, "VS Code has no flag and the report must say so")

    def test_enabling_restores_the_vscode_entry_from_the_baseline(self):
        self.toggle(False, apply_changes=True)
        self.toggle(True, apply_changes=True)
        entry = self.read(self.vscode)["servers"]["alpha"]
        self.assertEqual((entry["command"], entry["args"]), ("alpha-server", ["--x"]))
        self.assertTrue(self.read(self.opencode)["mcp"]["alpha"]["enabled"])

    def test_enabling_does_not_turn_the_allowlist_into_a_trap(self):
        """`enabledMcpjsonServers` is an allowlist: a name in it disables every other one.

        Enabling one server by adding it there silently switched off the rest, which is
        exactly what happened on a real workstation before this was fixed.
        """
        self.toggle(False, apply_changes=True)   # alpha lands in the disabled list
        self.toggle(True, apply_changes=True)    # and must simply leave it
        project_key = next(iter(self.read(self.claude)["projects"]))
        entry = self.read(self.claude)["projects"][project_key]
        self.assertEqual(entry["disabledMcpjsonServers"], [])
        self.assertEqual(entry["enabledMcpjsonServers"], [],
                         "enabling must not create an allowlist that excludes everything else")

    def test_an_allowlist_already_in_force_is_respected(self):
        project_key = str(self.project).replace("\\", "/")
        self.claude.write_text(json.dumps(
            {"projects": {project_key: {"enabledMcpjsonServers": ["other-server"]}}}), encoding="utf-8")
        self.toggle(True, apply_changes=True)
        entry = self.read(self.claude)["projects"][project_key]
        self.assertEqual(entry["enabledMcpjsonServers"], ["alpha", "other-server"],
                         "where an allowlist exists, the name has to join it to be reachable")

    def test_the_plan_writes_nothing(self):
        before = {path: path.read_bytes() for path in (self.opencode, self.vscode, self.claude)}
        report = self.toggle(False)
        self.assertTrue(report["changes"])
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_one_client_can_be_singled_out(self):
        self.toggle(False, apply_changes=True, clients=["opencode"])
        self.assertFalse(self.read(self.opencode)["mcp"]["alpha"]["enabled"])
        self.assertIn("alpha", self.read(self.vscode)["servers"], "the other clients are untouched")

    def test_an_alias_is_accepted_and_answered_with_the_suite_name(self):
        report = isc_toggle.toggle("alpha-legacy", False, project_dir=self.project, home=self.home,
                                   environ=self.environ, suite_path=self.suite_file)
        self.assertEqual(report["server"], "alpha")

    def test_an_unknown_server_is_refused_with_the_list_of_real_ones(self):
        with self.assertRaisesRegex(isc_config.ConfigError, "Available: alpha"):
            isc_toggle.toggle("nope", False, project_dir=self.project, home=self.home,
                              environ=self.environ, suite_path=self.suite_file)


if __name__ == "__main__":
    unittest.main()
