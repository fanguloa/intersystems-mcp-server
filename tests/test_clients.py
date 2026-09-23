"""Contract tests for installing a client.

The invariants worth guarding: nothing runs without --apply, nothing that is already
installed is touched, and the confirmation never blocks on a pipe -- this binary also
serves MCP over stdio, so a prompt there would hang the client that launched it.
"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isc_ai import clients as isc_clients
from isc_ai import config as isc_config

SUITE = {
    "version": 1,
    "clients": {
        "alpha": {"binary": "alpha-client", "version_argv": ["--version"],
                  "config": {"user": "{home}/alpha.json", "mcp_key": "mcp",
                             "entry_style": "opencode", "write_scope": "user"},
                  "install": {"manager": "fake-manager", "argv": ["fake-manager", "install", "alpha"]}},
        "beta": {"binary": "beta-client", "version_argv": ["--version"],
                 "config": {"user": "{home}/beta.json", "mcp_key": "servers",
                            "entry_style": "vscode", "write_scope": "user"},
                 "install": {"manager": "absent-manager", "argv": ["absent-manager", "install", "beta"]}},
    },
    "servers": {},
    "out_of_suite": {},
}


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ClientsTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.suite_file = self.root / "suite.json"
        self.suite_file.write_text(json.dumps(SUITE), encoding="utf-8")

    def which(self, present):
        def resolver(name):
            return f"/usr/bin/{name}" if name in present else None
        return patch("isc_ai.clients.shutil.which", side_effect=resolver)

    def plan(self, present=(), names=None):
        with self.which(set(present)):
            return isc_clients.plan(names=names, suite_path=self.suite_file)

    def install(self, present=(), **kwargs):
        with self.which(set(present)):
            return isc_clients.install(suite_path=self.suite_file, **kwargs)


class PlanTests(ClientsTestCase):
    def test_the_plan_reports_the_recipe_and_whether_its_manager_exists(self):
        report = self.plan(present={"fake-manager"})
        alpha = next(item for item in report["clients"] if item["client"] == "alpha")
        beta = next(item for item in report["clients"] if item["client"] == "beta")
        self.assertFalse(alpha["installed"])
        self.assertTrue(alpha["manager_available"])
        self.assertEqual(alpha["command"], "fake-manager install alpha")
        self.assertFalse(beta["manager_available"])

    def test_an_unknown_client_is_refused_with_the_real_list(self):
        with self.assertRaisesRegex(isc_config.ConfigError, "Available: alpha, beta"):
            self.plan(names=["nope"])

    def test_a_single_client_can_be_singled_out(self):
        report = self.plan(present={"fake-manager"}, names=["alpha"])
        self.assertEqual([item["client"] for item in report["clients"]], ["alpha"])


class InstallTests(ClientsTestCase):
    def test_nothing_runs_without_apply(self):
        calls = []
        report = self.install(present={"fake-manager"}, runner=lambda *a, **k: calls.append(a) or Result())
        self.assertEqual(calls, [])
        alpha = next(item for item in report["results"] if item["client"] == "alpha")
        self.assertEqual(alpha["action"], "would-install")

    def test_an_installed_client_is_never_touched(self):
        calls = []
        with patch("isc_ai.clients._version_of", return_value="9.9"):
            report = self.install(present={"alpha-client", "fake-manager"}, apply_changes=True,
                                 assume_yes=True, runner=lambda *a, **k: calls.append(a) or Result())
        alpha = next(item for item in report["results"] if item["client"] == "alpha")
        self.assertEqual(alpha["action"], "noop")
        self.assertEqual(calls, [], "an installed client must not be reinstalled")

    def test_a_missing_package_manager_blocks_instead_of_trying(self):
        calls = []
        report = self.install(present=set(), apply_changes=True, assume_yes=True,
                             runner=lambda *a, **k: calls.append(a) or Result())
        beta = next(item for item in report["results"] if item["client"] == "beta")
        self.assertEqual(beta["action"], "blocked")
        self.assertIn("absent-manager", beta["detail"])
        self.assertEqual(calls, [])

    def test_success_is_verified_by_asking_for_a_version(self):
        # Call order: the plan asks for each client's version first, then the verification
        # after the recipe runs. Only alpha reaches that third call; beta is blocked.
        with patch("isc_ai.clients._version_of", side_effect=[None, None, "1.2.3"]):
            report = self.install(present={"fake-manager"}, apply_changes=True, assume_yes=True,
                                 runner=lambda *a, **k: Result())
        alpha = next(item for item in report["results"] if item["client"] == "alpha")
        self.assertEqual(alpha["action"], "installed")
        self.assertEqual(alpha["detail"], "1.2.3")
        self.assertEqual(report["installed"], ["alpha"])

    def test_a_recipe_that_succeeds_without_a_working_program_is_unverified_not_installed(self):
        with patch("isc_ai.clients._version_of", return_value=None):
            report = self.install(present={"fake-manager"}, apply_changes=True, assume_yes=True,
                                 runner=lambda *a, **k: Result())
        alpha = next(item for item in report["results"] if item["client"] == "alpha")
        self.assertEqual(alpha["action"], "unverified")
        self.assertEqual(report["installed"], [])

    def test_a_failing_recipe_reports_its_last_line_and_not_a_traceback(self):
        with patch("isc_ai.clients._version_of", return_value=None):
            report = self.install(present={"fake-manager"}, apply_changes=True, assume_yes=True,
                                 runner=lambda *a, **k: Result(returncode=1, stderr="boom: no network"))
        alpha = next(item for item in report["results"] if item["client"] == "alpha")
        self.assertEqual(alpha["action"], "failed")
        self.assertIn("boom: no network", alpha["detail"])


class ConfirmationTests(ClientsTestCase):
    def record(self):
        return {"client": "alpha", "command": "fake-manager install alpha"}

    def test_a_pipe_is_never_prompted(self):
        stream = io.StringIO("y\n")
        stream.isatty = lambda: False
        allowed, why = isc_clients._confirm(self.record(), False, stream, io.StringIO())
        self.assertFalse(allowed)
        self.assertIn("never prompts on a pipe", why)
        self.assertEqual(stream.read(), "y\n", "the input must not be consumed")

    def test_a_terminal_is_asked_and_the_command_is_shown_first(self):
        stream = io.StringIO("yes\n")
        stream.isatty = lambda: True
        output = io.StringIO()
        allowed, _ = isc_clients._confirm(self.record(), False, stream, output)
        self.assertTrue(allowed)
        self.assertIn("fake-manager install alpha", output.getvalue())

    def test_anything_other_than_yes_declines(self):
        for answer in ("n", "no", "", "later"):
            stream = io.StringIO(answer + "\n")
            stream.isatty = lambda: True
            allowed, _ = isc_clients._confirm(self.record(), False, stream, io.StringIO())
            self.assertFalse(allowed, f"{answer!r} must not be taken as consent")

    def test_yes_flag_skips_the_question_entirely(self):
        stream = io.StringIO("")
        stream.isatty = lambda: False
        allowed, why = isc_clients._confirm(self.record(), True, stream, io.StringIO())
        self.assertTrue(allowed)
        self.assertIn("--yes", why)


if __name__ == "__main__":
    unittest.main()
