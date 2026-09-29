"""Contract tests for the read-only audit.

Fixtures are synthetic client configurations in a temporary HOME, so the tests state
what each finding means instead of depending on the machine they run on. The last
test is the one that matters most: the audit must never write.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isc_ai import audit
from isc_ai.cli import main

SUITE = {
    "version": 1,
    "clients": {
        "opencode": {
            "binary": "opencode-probe",
            "config": {"user": "{config_home}/opencode/opencode.json",
                       "alternatives": ["{config_home}/opencode/opencode.jsonc"],
                       "mcp_key": "mcp"},
            "install": {"manager": "npm-probe", "argv": ["npm-probe", "i", "-g", "x"]},
        },
        "vscode": {
            "binary": "code-probe",
            "config": {"user": "{appdata}/Code/User/mcp.json",
                       "project": "{project}/.vscode/mcp.json", "mcp_key": "servers"},
            "install": {"manager": "winget-probe", "argv": ["winget-probe", "install", "x"]},
        },
        "claude": {
            "binary": "claude-probe",
            "config": {"user": "{home}/.claude.json", "project": "{project}/.mcp.json",
                       "mcp_key": "mcpServers"},
            "install": {"manager": "npm-probe", "argv": ["npm-probe", "i", "-g", "y"]},
        },
    },
    "servers": {
        "alpha": {
            "transport": "stdio",
            "launch": {"command": "python", "args": ["-c", "pass"]},
            "aliases": ["alpha-legacy", "io.github.vendor/alpha"],
            "settings": {
                "ALPHA_TOKEN": {"scope": "global", "secret": True, "required": True},
                "ALPHA_TIMEOUT": {"scope": "project", "secret": False, "required": False},
            },
        },
        "beta": {
            "transport": "http",
            "launch": {"url": "https://example.invalid/mcp"},
            "aliases": [],
            "settings": {},
        },
    },
    "out_of_suite": {"gamma": "Runs remote code on every start."},
}


class AuditTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "project"
        (self.home / ".config" / "opencode").mkdir(parents=True)
        (self.home / "AppData" / "Roaming" / "Code" / "User").mkdir(parents=True)
        (self.home / ".claude" / "agents").mkdir(parents=True)
        self.project.mkdir()
        self.suite_file = self.root / "suite.json"
        self.suite_file.write_text(json.dumps(SUITE), encoding="utf-8")
        self.environ = {"APPDATA": str(self.home / "AppData" / "Roaming")}

    def write_opencode(self, servers):
        path = self.home / ".config" / "opencode" / "opencode.json"
        path.write_text(json.dumps({"mcp": servers}), encoding="utf-8")
        return path

    def write_vscode(self, servers):
        path = self.home / "AppData" / "Roaming" / "Code" / "User" / "mcp.json"
        path.write_text(json.dumps({"servers": servers}), encoding="utf-8")
        return path

    def write_claude_project(self, servers):
        path = self.project / ".mcp.json"
        path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
        return path

    def run_audit(self):
        return audit.run_audit(project_dir=self.project, home=self.home,
                              environ=self.environ, suite_path=self.suite_file)

    def codes(self, report, server=None, client=None):
        return sorted(item["code"] for item in report["findings"]
                      if (server is None or item.get("server") == server)
                      and (client is None or item.get("client") == client))


class ClassificationTests(AuditTestCase):
    def test_a_recommended_entry_is_clean(self):
        self.write_opencode({"alpha": {"command": ["python", "-c", "pass"],
                                       "environment": {"ALPHA_TOKEN": "{env:ALPHA_TOKEN}"}}})
        report = self.run_audit()
        self.assertEqual(report["servers"]["alpha"]["opencode"]["state"], "OK")
        self.assertNotIn("SERVER_DRIFT", self.codes(report, "alpha"))
        self.assertNotIn("SECRET_LEAK", self.codes(report, "alpha"))

    def test_an_absent_server_is_missing_per_client(self):
        self.write_opencode({})
        report = self.run_audit()
        self.assertEqual(report["servers"]["alpha"]["opencode"]["state"], "MISSING")
        self.assertIn("SERVER_MISSING", self.codes(report, "alpha"))

    def test_another_name_for_the_same_server_is_an_alias_not_a_gap(self):
        self.write_opencode({"io.github.vendor/alpha": {"command": ["python", "-c", "pass"],
                                                       "environment": {"ALPHA_TOKEN": "${ALPHA_TOKEN}"}}})
        report = self.run_audit()
        state = report["servers"]["alpha"]["opencode"]
        self.assertIn("ALIAS", state["state"])
        self.assertEqual(state["registered_as"], ["io.github.vendor/alpha"])
        self.assertIn("SERVER_ALIAS", self.codes(report, "alpha", "opencode"))
        self.assertNotIn("SERVER_MISSING", self.codes(report, "alpha", "opencode"))
        # The other clients still lack it, and that is a separate finding.
        self.assertIn("SERVER_MISSING", self.codes(report, "alpha", "vscode"))

    def test_a_different_command_or_arguments_is_drift(self):
        self.write_opencode({"alpha": {"command": ["python", "-c", "other"],
                                       "environment": {"ALPHA_TOKEN": "{env:T}"}}})
        report = self.run_audit()
        self.assertIn("DRIFT", report["servers"]["alpha"]["opencode"]["state"])
        drift = [item for item in report["findings"] if item["code"] == "SERVER_DRIFT"]
        self.assertTrue(any("recommended ['-c', 'pass']" in item["message"] for item in drift))

    def test_a_shell_wrapper_is_reported_but_the_real_program_is_still_compared(self):
        self.write_claude_project({"alpha": {"command": "cmd", "args": ["/c", "python", "-c", "pass"],
                                            "env": {"ALPHA_TOKEN": "{env:T}"}}})
        report = self.run_audit()
        messages = [item["message"] for item in report["findings"] if item["code"] == "SERVER_DRIFT"]
        self.assertTrue(any("wrapped in a shell" in message for message in messages))
        # The switch of the wrapper must not be mistaken for a filesystem path.
        self.assertNotIn("PATH_NOT_FOUND", self.codes(report, "alpha"))

    def test_a_literal_secret_is_an_error_and_a_reference_is_not(self):
        self.write_opencode({"alpha": {"command": ["python", "-c", "pass"],
                                       "environment": {"ALPHA_TOKEN": "aaaa-real-token-bbbb"}}})
        report = self.run_audit()
        leaks = [item for item in report["findings"] if item["code"] == "SECRET_LEAK"]
        self.assertEqual(len(leaks), 1)
        self.assertEqual(leaks[0]["severity"], "error")
        self.assertNotIn("aaaa-real-token-bbbb", json.dumps(report), "the audit must not echo the secret")

    def test_a_required_setting_that_is_absent_is_an_error(self):
        self.write_opencode({"alpha": {"command": ["python", "-c", "pass"], "environment": {}}})
        report = self.run_audit()
        self.assertIn("SETTING_MISSING", self.codes(report, "alpha"))

    def test_an_argument_pointing_nowhere_is_broken(self):
        dead = str(self.root / "gone" / "server.py")
        self.write_vscode({"alpha": {"command": "python", "args": [dead],
                                     "env": {"ALPHA_TOKEN": "{env:T}"}}})
        report = self.run_audit()
        self.assertIn("BROKEN", report["servers"]["alpha"]["vscode"]["state"])
        self.assertIn("PATH_NOT_FOUND", self.codes(report, "alpha"))

    def test_a_command_that_is_not_installed_is_broken(self):
        self.write_opencode({"alpha": {"command": ["definitely-not-a-program-xyz"],
                                       "environment": {"ALPHA_TOKEN": "{env:T}"}}})
        report = self.run_audit()
        self.assertIn("COMMAND_NOT_FOUND", self.codes(report, "alpha"))

    def test_disabled_and_duplicate_registrations_are_reported(self):
        self.write_opencode({
            "alpha": {"command": ["python", "-c", "pass"], "enabled": False,
                      "environment": {"ALPHA_TOKEN": "{env:T}"}},
            "alpha-legacy": {"command": ["python", "-c", "pass"],
                             "environment": {"ALPHA_TOKEN": "{env:T}"}},
        })
        report = self.run_audit()
        codes = self.codes(report, "alpha")
        self.assertIn("SERVER_DISABLED", codes)
        self.assertIn("SERVER_DUPLICATE", codes)

    def test_an_entry_outside_the_suite_carries_its_documented_reason(self):
        self.write_opencode({"gamma": {"command": ["python"]}})
        report = self.run_audit()
        extra = [item for item in report["findings"] if item["code"] == "SERVER_EXTRA"]
        self.assertEqual(len(extra), 1)
        self.assertIn("Runs remote code", extra[0]["message"])
        self.assertEqual(extra[0]["severity"], "info")

    def test_http_servers_are_compared_by_url(self):
        self.write_opencode({"beta": {"type": "remote", "url": "https://wrong.invalid/mcp"}})
        report = self.run_audit()
        self.assertIn("DRIFT", report["servers"]["beta"]["opencode"]["state"])
        self.write_opencode({"beta": {"type": "remote", "url": "https://example.invalid/mcp"}})
        self.assertEqual(self.run_audit()["servers"]["beta"]["opencode"]["state"], "OK")


class InstallPinTests(AuditTestCase):
    """S3: per-server install/version-pin metadata surfaces drift instead of hiding it."""

    def suite_with_servers(self, extra_servers):
        suite = json.loads(json.dumps(SUITE))
        suite["servers"].update(extra_servers)
        return suite

    def run_audit_with_suite(self, suite):
        suite_file = self.root / "suite-install.json"
        suite_file.write_text(json.dumps(suite), encoding="utf-8")
        return audit.run_audit(project_dir=self.project, home=self.home,
                                environ=self.environ, suite_path=suite_file)

    def test_missing_install_block_with_unpinned_launch_is_a_warning(self):
        suite = self.suite_with_servers({
            "delta-pkg": {
                "transport": "stdio",
                "launch": {"command": "npx", "args": ["@vendor/delta-pkg@latest"]},
                "aliases": [], "settings": {},
            },
        })
        report = self.run_audit_with_suite(suite)
        self.assertIn("VERSION_UNPINNED", self.codes(report, "delta-pkg"))

    def test_pinned_server_matching_its_pin_has_no_finding(self):
        suite = self.suite_with_servers({
            "gamma-pkg": {
                "transport": "stdio",
                "launch": {"command": "uvx", "args": ["gamma-pkg@1.2.3"]},
                "aliases": [], "settings": {},
                "install": {"manager": "uvx", "pin": "1.2.3", "pinned": True},
            },
        })
        report = self.run_audit_with_suite(suite)
        self.assertNotIn("VERSION_UNPINNED", self.codes(report, "gamma-pkg"))
        self.assertNotIn("INSTALL_PIN_MISMATCH", self.codes(report, "gamma-pkg"))

    def test_declared_pin_disagreeing_with_launch_args_is_an_error(self):
        suite = self.suite_with_servers({
            "gamma-pkg": {
                "transport": "stdio",
                "launch": {"command": "uvx", "args": ["gamma-pkg@1.2.3"]},
                "aliases": [], "settings": {},
                "install": {"manager": "uvx", "pin": "9.9.9", "pinned": True},
            },
        })
        report = self.run_audit_with_suite(suite)
        mismatches = [item for item in report["findings"] if item["code"] == "INSTALL_PIN_MISMATCH"]
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["severity"], "error")

    def test_a_recorded_but_not_yet_adopted_pin_is_still_a_warning_not_an_error(self):
        # pinned:false + a candidate pin that does not match today's launch args yet
        # is the deliberate "identified but not applied" state, not an authoring error.
        suite = self.suite_with_servers({
            "epsilon-pkg": {
                "transport": "stdio",
                "launch": {"command": "npx", "args": ["epsilon-pkg@latest"]},
                "aliases": [], "settings": {},
                "install": {"manager": "npx", "pin": "2.0.0", "pinned": False,
                            "pin_reason": "no version has been evaluated yet"},
            },
        })
        report = self.run_audit_with_suite(suite)
        self.assertIn("VERSION_UNPINNED", self.codes(report, "epsilon-pkg"))
        self.assertNotIn("INSTALL_PIN_MISMATCH", self.codes(report, "epsilon-pkg"))

    def test_a_server_without_an_install_block_still_classifies_exactly_as_today(self):
        # 'alpha' (from SUITE) never declared an install block. Adding install-pin
        # detection must not change its per-client classification state at all.
        self.write_opencode({"alpha": {"command": ["python", "-c", "pass"],
                                        "environment": {"ALPHA_TOKEN": "{env:ALPHA_TOKEN}"}}})
        report = self.run_audit()
        self.assertEqual(report["servers"]["alpha"]["opencode"]["state"], "OK")
        self.assertNotIn("SERVER_DRIFT", self.codes(report, "alpha"))
        self.assertNotIn("INSTALL_PIN_MISMATCH", self.codes(report, "alpha"))

    def test_http_transport_servers_are_never_flagged_unpinned(self):
        # A hosted HTTP endpoint has no installable package version to pin at all.
        report = self.run_audit()
        self.assertNotIn("VERSION_UNPINNED", self.codes(report, "beta"))


class InstallIntegrityTests(AuditTestCase):
    """S3 fix round: git-sourced installs pin a tag AND an expected commit SHA.

    Per decision `distribution-channel`: for git-sourced installs the integrity
    check is (tag, commit), not a wheel sha256 - `uv tool install git+...@<tag>`
    has nothing else to verify against. Non-git managers (npm/uvx registry pins)
    are explicitly NOT asked for an invented checksum here; that stays deferred.
    """

    def suite_with_servers(self, extra_servers):
        suite = json.loads(json.dumps(SUITE))
        suite["servers"].update(extra_servers)
        return suite

    def run_audit_with_suite(self, suite):
        suite_file = self.root / "suite-install-integrity.json"
        suite_file.write_text(json.dumps(suite), encoding="utf-8")
        return audit.run_audit(project_dir=self.project, home=self.home,
                                environ=self.environ, suite_path=suite_file)

    def test_git_sourced_install_with_a_tag_but_no_commit_is_a_warning(self):
        suite = self.suite_with_servers({
            "zeta-ext": {
                "transport": "stdio",
                "launch": {"command": "zeta-ext", "args": []},
                "aliases": [], "settings": {},
                "install": {
                    "manager": "uv",
                    "argv": ["uv", "tool", "install",
                             "git+https://gitlab.iscinternal.com/isc-ai-squad/zeta-ext.git@v0.1.0"],
                    "pin": "v0.1.0", "pinned": True,
                },
            },
        })
        report = self.run_audit_with_suite(suite)
        self.assertIn("INSTALL_COMMIT_MISSING", self.codes(report, "zeta-ext"))

    def test_git_sourced_install_with_tag_and_a_valid_commit_has_no_finding(self):
        suite = self.suite_with_servers({
            "zeta-ext": {
                "transport": "stdio",
                "launch": {"command": "zeta-ext", "args": []},
                "aliases": [], "settings": {},
                "install": {
                    "manager": "uv",
                    "argv": ["uv", "tool", "install",
                             "git+https://gitlab.iscinternal.com/isc-ai-squad/zeta-ext.git@v0.1.0"],
                    "pin": "v0.1.0", "pinned": True,
                    "commit": "a" * 40,
                },
            },
        })
        report = self.run_audit_with_suite(suite)
        self.assertNotIn("INSTALL_COMMIT_MISSING", self.codes(report, "zeta-ext"))
        self.assertNotIn("INSTALL_COMMIT_INVALID", self.codes(report, "zeta-ext"))

    def test_git_sourced_install_with_a_malformed_commit_is_an_error(self):
        suite = self.suite_with_servers({
            "zeta-ext": {
                "transport": "stdio",
                "launch": {"command": "zeta-ext", "args": []},
                "aliases": [], "settings": {},
                "install": {
                    "manager": "uv",
                    "argv": ["uv", "tool", "install",
                             "git+https://gitlab.iscinternal.com/isc-ai-squad/zeta-ext.git@v0.1.0"],
                    "pin": "v0.1.0", "pinned": True,
                    "commit": "not-a-sha",
                },
            },
        })
        report = self.run_audit_with_suite(suite)
        invalid = [item for item in report["findings"] if item["code"] == "INSTALL_COMMIT_INVALID"]
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]["severity"], "error")

    def test_a_non_git_manager_is_never_asked_for_a_commit(self):
        # npm/uvx registry pins keep working exactly as before: no invented checksum,
        # no INSTALL_COMMIT_MISSING for a manager that isn't git-sourced at all.
        suite = self.suite_with_servers({
            "gamma-pkg": {
                "transport": "stdio",
                "launch": {"command": "uvx", "args": ["gamma-pkg@1.2.3"]},
                "aliases": [], "settings": {},
                "install": {"manager": "uvx", "pin": "1.2.3", "pinned": True},
            },
        })
        report = self.run_audit_with_suite(suite)
        self.assertNotIn("INSTALL_COMMIT_MISSING", self.codes(report, "gamma-pkg"))

    def test_git_source_is_detected_from_the_argv_even_with_manager_uv(self):
        # Detection is by the git+ URL actually run, not a fixed 'manager: git' spelling,
        # since the real recipe is 'uv tool install git+<url>@<tag>'.
        suite = self.suite_with_servers({
            "zeta-ext": {
                "transport": "stdio",
                "launch": {"command": "zeta-ext", "args": []},
                "aliases": [], "settings": {},
                "install": {
                    "manager": "uv",
                    "argv": ["uv", "tool", "install", "--force",
                             "git+https://gitlab.iscinternal.com/isc-ai-squad/zeta-ext.git@v0.1.0"],
                    "pin": "v0.1.0", "pinned": True,
                },
            },
        })
        report = self.run_audit_with_suite(suite)
        self.assertIn("INSTALL_COMMIT_MISSING", self.codes(report, "zeta-ext"))


class ClientAndAmbiguityTests(AuditTestCase):
    def test_an_absent_client_reports_whether_its_installer_is_available(self):
        report = self.run_audit()
        missing = [item for item in report["findings"] if item["code"] == "CLIENT_MISSING"]
        self.assertEqual({item["client"] for item in missing}, {"opencode", "vscode", "claude"})
        self.assertFalse(report["clients"]["opencode"]["installed"])
        self.assertFalse(report["clients"]["opencode"]["install_available"])

    def test_two_config_files_for_one_client_is_an_ambiguity(self):
        self.write_opencode({})
        (self.home / ".config" / "opencode" / "opencode.jsonc").write_text("{}", encoding="utf-8")
        report = self.run_audit()
        self.assertIn("CONFIG_AMBIGUOUS", [item["code"] for item in report["findings"]])

    def test_claude_per_project_toggles_drive_the_enabled_state(self):
        self.write_claude_project({"alpha": {"command": "python", "args": ["-c", "pass"],
                                            "env": {"ALPHA_TOKEN": "{env:T}"}}})
        (self.home / ".claude.json").write_text(json.dumps({
            "projects": {str(self.project): {"disabledMcpjsonServers": ["alpha"]}}
        }), encoding="utf-8")
        report = self.run_audit()
        self.assertIn("DISABLED", report["servers"]["alpha"]["claude"]["state"])


class ReadOnlyTests(AuditTestCase):
    def test_the_audit_writes_nothing_and_starts_nothing(self):
        self.write_opencode({"alpha": {"command": ["python", "-c", "pass"]}})
        self.write_vscode({"alpha": {"command": "python", "args": ["-c", "pass"]}})
        before = {path: path.stat().st_mtime_ns for path in sorted(self.root.rglob("*")) if path.is_file()}
        with patch("subprocess.run", side_effect=AssertionError("the audit must not start processes")), \
             patch("subprocess.Popen", side_effect=AssertionError("the audit must not start processes")), \
             patch("socket.create_connection", side_effect=AssertionError("the audit must not connect")):
            self.run_audit()
        after = {path: path.stat().st_mtime_ns for path in sorted(self.root.rglob("*")) if path.is_file()}
        self.assertEqual(before, after, "no file may be created, touched or rewritten")

    def test_exit_code_separates_errors_from_advisories(self):
        self.write_opencode({"alpha": {"command": ["python", "-c", "pass"],
                                       "environment": {"ALPHA_TOKEN": "literal-secret"}}})
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["audit", "--project-dir", str(self.project), "--suite", str(self.suite_file)]
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr), \
             patch("pathlib.Path.home", return_value=self.home), \
             patch.dict(os.environ, self.environ, clear=True):
            code = main(argv)
        report = json.loads(stdout.getvalue())
        self.assertEqual(code, 1, "a leaked secret is an error")
        self.assertGreaterEqual(report["summary"]["errors"], 1)

    def test_errors_only_filters_the_report_without_changing_the_summary(self):
        self.write_opencode({"alpha": {"command": ["python", "-c", "pass"],
                                       "environment": {"ALPHA_TOKEN": "literal-secret"}}})
        stdout = io.StringIO()
        argv = ["audit", "--project-dir", str(self.project), "--suite", str(self.suite_file), "--errors-only"]
        with patch("sys.stdout", stdout), patch("sys.stderr", io.StringIO()), \
             patch("pathlib.Path.home", return_value=self.home), \
             patch.dict(os.environ, self.environ, clear=True):
            main(argv)
        report = json.loads(stdout.getvalue())
        self.assertTrue(report["findings"])
        self.assertEqual({item["severity"] for item in report["findings"]}, {"error"})
        self.assertGreater(report["summary"]["warnings"], 0, "the summary still counts everything")


if __name__ == "__main__":
    unittest.main()
