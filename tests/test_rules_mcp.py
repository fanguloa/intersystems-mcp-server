"""WU7: the iris_rules MCP tool.

Reads (list/show/check/audit) never write. Narrowing (remove, a renew that
shortens the expiry) applies directly with a backup and an audit line.
Widening (add, a renew that extends the expiry) only ever returns a plan and
a fingerprint: the tool has no path that writes isc-ai.rules.json for it.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isc_ai import config as config_module
from isc_ai import rules as rules_module
from isc_ai import rules_write
from isc_ai.server import IRISMCPServer

from tests.hermetic_test_support import reference_policy_data


EXC = {"id": "uat-ha", "label": "UAT direct HA write", "ticket": "TCK-1", "expires": "2099-06-01",
       "capability": "code_write", "roles": ["UAT"], "namespaces": ["INT"]}


class RulesToolTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.project = root / "project"
        self.home = root / "home"
        self.project.mkdir()
        self.home.mkdir()
        env = {"HOME": str(self.home), "USERPROFILE": str(self.home)}
        for guard in (
            patch.dict(os.environ, env, clear=True),
            patch("pathlib.Path.home", return_value=self.home),
            patch("socket.create_connection", side_effect=AssertionError("Network forbidden")),
            patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Network forbidden")),
        ):
            guard.start()
            self.addCleanup(guard.stop)
        path = self.project / config_module.STATE_DIR / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        config_module.write_json(path, reference_policy_data())
        config_module.write_json(self.rules_path(), {"version": 1, "packages": {}, "exceptions": [EXC]})
        self.server = IRISMCPServer(project_dir=self.project)

    def rules_path(self):
        return self.project / rules_module.RULES_FILENAME

    def call(self, **arguments):
        return self.server.handle_tool_call("iris_rules", arguments)

    def text(self, result):
        return result["content"][0]["text"]

    def rules_on_disk(self):
        return config_module.read_json(self.rules_path())


class ReadActionTests(RulesToolTestCase):
    def test_list_describes_each_exception_with_its_expiry(self):
        before = self.rules_path().read_bytes()
        result = self.call(action="list")
        self.assertFalse(result.get("isError", False), result)
        self.assertIn("uat-ha", self.text(result))
        self.assertIn("2099-06-01", self.text(result))
        self.assertEqual([e["id"] for e in result["structuredContent"]["exceptions"]], ["uat-ha"])
        self.assertEqual(self.rules_path().read_bytes(), before)

    def test_show_returns_the_full_exception(self):
        result = self.call(action="show", id="uat-ha")
        self.assertEqual(result["structuredContent"]["exception"]["ticket"], "TCK-1")

    def test_show_unknown_id_is_an_error(self):
        self.assertTrue(self.call(action="show", id="nope").get("isError"))

    def test_check_reports_the_matching_exception(self):
        result = self.call(action="check", capability="code_write", namespace="INT", role="UAT")
        self.assertTrue(result["structuredContent"]["allowed"])
        self.assertIn("uat-ha", result["structuredContent"]["matches"])

    def test_check_denies_outside_the_scope(self):
        result = self.call(action="check", capability="code_write", namespace="BASE-X", role="UAT")
        self.assertFalse(result["structuredContent"]["allowed"])
        self.assertNotIn("uat-ha", result["structuredContent"]["matches"])

    def test_audit_lists_expiring_exceptions(self):
        import datetime
        soon = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        config_module.write_json(self.rules_path(), {"version": 1, "packages": {}, "exceptions": [
            dict(EXC, expires=soon)]})
        result = self.call(action="audit")
        self.assertIn("uat-ha", result["structuredContent"]["expiring_within_7_days"])


class NarrowingActionTests(RulesToolTestCase):
    def test_remove_applies_directly_with_backup_and_audit(self):
        result = self.call(action="remove", id="uat-ha")
        self.assertFalse(result.get("isError", False), result)
        self.assertTrue(result["structuredContent"]["applied"])
        self.assertEqual(self.rules_on_disk()["exceptions"], [])
        self.assertEqual(len(list(self.project.glob("isc-ai.rules.json.*.bak"))), 1)
        audit = self.project / ".backup" / "rules" / "audit.jsonl"
        self.assertIn('"action": "remove"', audit.read_text(encoding="utf-8"))

    def test_shortening_the_expiry_applies_directly(self):
        result = self.call(action="renew", id="uat-ha", expires="2099-01-01")
        self.assertTrue(result["structuredContent"]["applied"])
        self.assertEqual(self.rules_on_disk()["exceptions"][0]["expires"], "2099-01-01")


class WideningActionTests(RulesToolTestCase):
    def test_extending_the_expiry_only_returns_a_plan(self):
        before = self.rules_path().read_bytes()
        result = self.call(action="renew", id="uat-ha", expires="2100-01-01")
        self.assertFalse(result["structuredContent"]["applied"])
        fp = result["structuredContent"]["fingerprint"]
        self.assertIn(f"isc-ai rules apply {fp}", self.text(result))
        self.assertEqual(self.rules_path().read_bytes(), before)
        self.assertIsNotNone(rules_write.load_plan(self.project, fp))

    def test_add_only_returns_a_plan(self):
        before = self.rules_path().read_bytes()
        new = dict(EXC, id="live-ops", roles=["LIVE"], capability="ensemble_control")
        result = self.call(action="add", exception=new)
        self.assertFalse(result["structuredContent"]["applied"])
        self.assertEqual(self.rules_path().read_bytes(), before)

    def test_the_tool_never_applies_a_pending_plan(self):
        result = self.call(action="renew", id="uat-ha", expires="2100-01-01")
        fp = result["structuredContent"]["fingerprint"]
        applied = self.call(action="apply", fingerprint=fp)
        self.assertTrue(applied.get("isError"))
        self.assertEqual(self.rules_on_disk()["exceptions"][0]["expires"], "2099-06-01")

    def test_an_invalid_exception_is_rejected_before_any_plan(self):
        result = self.call(action="add", exception=dict(EXC, id="bad", capability="nope"))
        self.assertTrue(result.get("isError"))
        plans = rules_write.plans_dir(self.project)
        self.assertEqual(list(plans.glob("*.json")) if plans.is_dir() else [], [])


class CategoryTests(unittest.TestCase):
    def test_iris_rules_is_a_project_tool(self):
        self.assertIn("iris_rules", IRISMCPServer.PROJECT_TOOLS)


if __name__ == "__main__":
    unittest.main()
