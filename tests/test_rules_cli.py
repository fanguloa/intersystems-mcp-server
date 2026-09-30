"""WU6a/WU6b: 'isc-ai rules' CLI subcommands.

Read commands (list/show/check/validate) never write. Write commands
(add/renew/remove/apply/migrate) plan by default and require --apply to
write, mirroring the existing servers-migrate/config convention.

Uses an isolated HOME and project directory (never the real user files),
the same discipline as test_config.py's FilesystemTestCase.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isc_ai import config as config_module
from isc_ai import rules as rules_module
from isc_ai import rules_write
from isc_ai.cli import main

from tests.hermetic_test_support import REF, reference_policy_data


class RulesCliTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.home = self.root / "home"
        self.project.mkdir()
        self.home.mkdir()
        env = {"HOME": str(self.home), "USERPROFILE": str(self.home)}
        patches = (
            patch.dict(os.environ, env, clear=True),
            patch("pathlib.Path.home", return_value=self.home),
            patch("socket.create_connection", side_effect=AssertionError("Network forbidden")),
            patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Network forbidden")),
        )
        for guard in patches:
            guard.start()
            self.addCleanup(guard.stop)

    def run_cli(self, *argv):
        argv = list(argv)
        full = [argv[0], "--project-dir", str(self.project), *argv[1:]]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            try:
                code = main(full)
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def write_config(self, data):
        path = self.project / config_module.STATE_DIR / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        config_module.write_json(path, data)

    def write_rules(self, data):
        config_module.write_json(self.project / rules_module.RULES_FILENAME, data)

    def read_rules(self):
        return config_module.read_json(self.project / rules_module.RULES_FILENAME)


class ReadCommandTests(RulesCliTestCase):
    def test_list_prints_days_to_expiry_and_warns_when_close(self):
        self.write_config(reference_policy_data())
        self.write_rules({"version": 1, "packages": {}, "exceptions": [
            {"id": "soon", "label": "Expiring soon", "ticket": "TCK-1", "expires": "2099-01-01",
             "capability": "code_write"},
        ]})
        with patch("isc_ai.cli.datetime") as mocked:
            import datetime as real_datetime
            mocked.date.today.return_value = real_datetime.date(2099, 1, 1)
            code, out, _ = self.run_cli("rules", "list")
        self.assertEqual(code, 0)
        self.assertIn("soon", out)
        self.assertIn("0 day(s) left", out)
        self.assertIn("WARNING", out)

    def test_list_reports_expired_entries(self):
        self.write_config(reference_policy_data())
        self.write_rules({"version": 1, "packages": {}, "exceptions": [
            {"id": "old", "label": "Old", "ticket": "TCK-1", "expires": "2000-01-01",
             "capability": "code_write"},
        ]})
        code, out, _ = self.run_cli("rules", "list")
        self.assertEqual(code, 0)
        self.assertIn("EXPIRED", out)

    def test_list_with_no_exceptions_says_so(self):
        self.write_config(reference_policy_data())
        code, out, _ = self.run_cli("rules", "list")
        self.assertEqual(code, 0)
        self.assertIn("no exceptions", out)

    def test_list_in_spanish_is_localized(self):
        self.write_config(reference_policy_data())
        code, out, _ = self.run_cli("rules", "--lang", "es", "list")
        self.assertEqual(code, 0)
        self.assertIn("no hay excepciones", out)

    def test_show_prints_the_full_exception(self):
        self.write_config(reference_policy_data())
        self.write_rules({"version": 1, "packages": {}, "exceptions": [
            {"id": "e1", "label": "One", "ticket": "TCK-1", "expires": "2099-01-01",
             "capability": "code_write", "roles": ["UAT"]},
        ]})
        code, out, _ = self.run_cli("rules", "show", "e1")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["id"], "e1")
        self.assertEqual(data["roles"], ["UAT"])

    def test_show_unknown_id_fails(self):
        self.write_config(reference_policy_data())
        code, _, err = self.run_cli("rules", "show", "missing")
        self.assertEqual(code, 1)
        self.assertIn("missing", err)

    def test_check_reports_allowed_and_denied(self):
        self.write_config(reference_policy_data())
        code, out, _ = self.run_cli(
            "rules", "check", "code_write", "--namespace", "INT",
            "--host", REF["clinical_vip"], "--role", "UAT", "--path-prefix", REF["int_vip_prefix"])
        self.assertEqual(code, 0)
        self.assertIn("allowed", out)

        code, out, _ = self.run_cli(
            "rules", "check", "code_write", "--namespace", "SOMEWHERE-ELSE", "--role", "UAT")
        self.assertEqual(code, 1)
        self.assertIn("denied", out)

    def test_validate_reports_expired_entries_and_exits_nonzero(self):
        self.write_config(reference_policy_data())
        self.write_rules({"version": 1, "packages": {}, "exceptions": [
            {"id": "old", "label": "Old", "ticket": "TCK-1", "expires": "2000-01-01",
             "capability": "code_write"},
        ]})
        code, out, _ = self.run_cli("rules", "validate")
        self.assertEqual(code, 1)
        self.assertIn("old", out)

    def test_validate_clean_reports_ok(self):
        self.write_config(reference_policy_data())
        code, out, _ = self.run_cli("rules", "validate")
        self.assertEqual(code, 0)
        self.assertIn("no issues", out)


class WriteCommandTests(RulesCliTestCase):
    def test_add_without_apply_writes_nothing_and_prints_a_fingerprint(self):
        self.write_config(reference_policy_data())
        code, out, _ = self.run_cli(
            "rules", "add", "--id", "e1", "--label", "Test", "--ticket", "TCK-1",
            "--expires", "2099-01-01", "--capability", "code_write", "--roles", "UAT,BASE")
        self.assertEqual(code, 0)
        self.assertIn("plan", out)
        self.assertFalse((self.project / rules_module.RULES_FILENAME).exists())

    def test_add_with_apply_writes_the_exception(self):
        self.write_config(reference_policy_data())
        code, out, _ = self.run_cli(
            "rules", "add", "--id", "e1", "--label", "Test", "--ticket", "TCK-1",
            "--expires", "2099-01-01", "--capability", "code_write", "--roles", "UAT,BASE",
            "--apply")
        self.assertEqual(code, 0)
        self.assertIn("applied", out)
        data = self.read_rules()
        self.assertEqual(data["exceptions"][0]["id"], "e1")
        self.assertEqual(data["exceptions"][0]["roles"], ["UAT", "BASE"])

    def test_add_duplicate_id_fails(self):
        self.write_config(reference_policy_data())
        self.write_rules({"version": 1, "packages": {}, "exceptions": [
            {"id": "e1", "label": "One", "ticket": "TCK-1", "expires": "2099-01-01", "capability": "code_write"},
        ]})
        code, _, err = self.run_cli(
            "rules", "add", "--id", "e1", "--label", "Two", "--ticket", "TCK-2",
            "--expires", "2099-01-01", "--capability", "code_write", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("e1", err)

    def test_renew_requires_id_or_expiring_within(self):
        self.write_config(reference_policy_data())
        code, _, err = self.run_cli("rules", "renew", "--expires", "2099-01-01")
        self.assertEqual(code, 2)
        self.assertIn("expiring-within", err)

    def test_renew_by_id_with_apply(self):
        self.write_config(reference_policy_data())
        self.write_rules({"version": 1, "packages": {}, "exceptions": [
            {"id": "e1", "label": "One", "ticket": "TCK-1", "expires": "2020-01-01", "capability": "code_write"},
        ]})
        code, out, _ = self.run_cli("rules", "renew", "e1", "--expires", "2099-01-01", "--apply")
        self.assertEqual(code, 0)
        self.assertEqual(self.read_rules()["exceptions"][0]["expires"], "2099-01-01")

    def test_remove_without_apply_plans_only(self):
        self.write_config(reference_policy_data())
        self.write_rules({"version": 1, "packages": {}, "exceptions": [
            {"id": "e1", "label": "One", "ticket": "TCK-1", "expires": "2099-01-01", "capability": "code_write"},
        ]})
        code, out, _ = self.run_cli("rules", "remove", "e1")
        self.assertEqual(code, 0)
        self.assertIn("plan", out)
        self.assertEqual(len(self.read_rules()["exceptions"]), 1)

    def test_remove_with_apply_removes_it(self):
        self.write_config(reference_policy_data())
        self.write_rules({"version": 1, "packages": {}, "exceptions": [
            {"id": "e1", "label": "One", "ticket": "TCK-1", "expires": "2099-01-01", "capability": "code_write"},
        ]})
        code, out, _ = self.run_cli("rules", "remove", "e1", "--apply")
        self.assertEqual(code, 0)
        self.assertEqual(self.read_rules()["exceptions"], [])

    def test_apply_fingerprint_from_a_plan_only_add(self):
        self.write_config(reference_policy_data())
        # Plan (no --apply): capture the fingerprint from stdout.
        code, out, _ = self.run_cli(
            "rules", "add", "--id", "e1", "--label", "Test", "--ticket", "TCK-1",
            "--expires", "2099-01-01", "--capability", "code_write")
        self.assertEqual(code, 0)
        import re
        match = re.search(r"fingerprint ([0-9a-f]{16})", out)
        self.assertIsNotNone(match, out)
        fp = match.group(1)
        code, out, _ = self.run_cli("rules", "apply", fp)
        self.assertEqual(code, 0)
        self.assertEqual(self.read_rules()["exceptions"][0]["id"], "e1")

    def test_apply_unknown_fingerprint_fails(self):
        self.write_config(reference_policy_data())
        code, _, err = self.run_cli("rules", "apply", "0" * 16)
        self.assertEqual(code, 1)

    def test_migrate_without_apply_writes_nothing(self):
        self.write_config(reference_policy_data())
        before = (self.project / config_module.STATE_DIR / "config.json").read_bytes()
        code, out, _ = self.run_cli("rules", "migrate")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["mode"], "check")
        self.assertEqual((self.project / config_module.STATE_DIR / "config.json").read_bytes(), before)

    def test_migrate_with_apply_writes_the_rules_file(self):
        self.write_config(reference_policy_data())
        code, out, _ = self.run_cli("rules", "migrate", "--apply")
        self.assertEqual(code, 0)
        self.assertTrue((self.project / rules_module.RULES_FILENAME).is_file())

    def test_missing_subcommand_fails_cleanly(self):
        code, _, err = self.run_cli("rules")
        self.assertEqual(code, 2)
        self.assertIn("rules sub-command", err)


if __name__ == "__main__":
    unittest.main()
