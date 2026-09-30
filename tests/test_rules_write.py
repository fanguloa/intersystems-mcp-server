"""isc_ai.rules_write: the shared plan-by-default / --apply gate for
isc-ai.rules.json writes (add/renew/remove), plus the deferred
plan+fingerprint path ('isc-ai rules apply <fingerprint>') the MCP tool uses
for widening actions.
"""

import datetime
import tempfile
import unittest
from pathlib import Path

from isc_ai import config as config_module
from isc_ai import rules as rules_module
from isc_ai import rules_write


ITEM = {"id": "e1", "label": "Test exception", "ticket": "TCK-1", "expires": "2099-01-01",
        "capability": "code_write", "roles": ["UAT"]}


class RulesWriteTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)

    def rules_path(self):
        return rules_write.rules_path_for(self.project)

    def write_rules(self, data):
        config_module.write_json(self.rules_path(), data)


class AddExceptionTests(RulesWriteTestCase):
    def test_plan_mode_writes_nothing_and_returns_a_fingerprint(self):
        executed, fp, plan = rules_write.add_exception(self.project, ITEM, apply=False)
        self.assertFalse(executed)
        self.assertIsNotNone(fp)
        self.assertFalse(self.rules_path().exists())
        self.assertEqual(plan["new"]["exceptions"], [ITEM])

    def test_apply_backs_up_an_existing_file_and_writes(self):
        self.write_rules({"version": 1, "packages": {}, "exceptions": []})
        original = self.rules_path().read_bytes()
        executed, fp, plan = rules_write.add_exception(self.project, ITEM, apply=True)
        self.assertTrue(executed)
        self.assertIsNone(fp)
        data = config_module.read_json(self.rules_path())
        self.assertEqual(data["exceptions"], [ITEM])
        backups = list(self.project.glob("isc-ai.rules.json.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_duplicate_id_is_rejected(self):
        self.write_rules({"version": 1, "packages": {}, "exceptions": [ITEM]})
        with self.assertRaises(ValueError):
            rules_write.add_exception(self.project, ITEM, apply=False)

    def test_an_invalid_exception_never_reaches_a_plan_or_the_file(self):
        bad = dict(ITEM, capability="not-a-real-capability")
        with self.assertRaises(config_module.ConfigError):
            rules_write.add_exception(self.project, bad, apply=False)
        self.assertFalse(self.rules_path().exists())
        plans = rules_write.plans_dir(self.project)
        self.assertEqual(list(plans.glob("*.json")) if plans.is_dir() else [], [])


class RenewExceptionTests(RulesWriteTestCase):
    def test_renew_updates_the_expiry_only(self):
        self.write_rules({"version": 1, "packages": {}, "exceptions": [ITEM]})
        executed, fp, plan = rules_write.renew_exception(self.project, "e1", "2030-01-01", apply=True)
        self.assertTrue(executed)
        data = config_module.read_json(self.rules_path())
        self.assertEqual(data["exceptions"][0]["expires"], "2030-01-01")
        self.assertEqual(data["exceptions"][0]["label"], "Test exception")

    def test_unknown_id_raises(self):
        self.write_rules({"version": 1, "packages": {}, "exceptions": [ITEM]})
        with self.assertRaises(ValueError):
            rules_write.renew_exception(self.project, "missing", "2030-01-01", apply=False)

    def test_renew_expiring_within_finds_and_renews_only_matching_entries(self):
        soon = dict(ITEM, id="soon", expires="2026-10-01")
        far = dict(ITEM, id="far", expires="2030-01-01")
        self.write_rules({"version": 1, "packages": {}, "exceptions": [soon, far]})
        today = datetime.date(2026, 9, 30)
        (executed, fp, plan), targets = rules_write.renew_expiring_within(
            self.project, days=7, expires="2027-01-01", apply=True, today=today)
        self.assertTrue(executed)
        self.assertEqual(targets, ["soon"])
        data = config_module.read_json(self.rules_path())
        by_id = {item["id"]: item for item in data["exceptions"]}
        self.assertEqual(by_id["soon"]["expires"], "2027-01-01")
        self.assertEqual(by_id["far"]["expires"], "2030-01-01")


class RemoveExceptionTests(RulesWriteTestCase):
    def test_plan_mode_leaves_the_file_untouched(self):
        self.write_rules({"version": 1, "packages": {}, "exceptions": [ITEM]})
        before = self.rules_path().read_bytes()
        executed, fp, plan = rules_write.remove_exception(self.project, "e1", apply=False)
        self.assertFalse(executed)
        self.assertEqual(self.rules_path().read_bytes(), before)
        self.assertEqual(plan["new"]["exceptions"], [])

    def test_apply_removes_the_entry_and_backs_up(self):
        self.write_rules({"version": 1, "packages": {}, "exceptions": [ITEM]})
        rules_write.remove_exception(self.project, "e1", apply=True)
        data = config_module.read_json(self.rules_path())
        self.assertEqual(data["exceptions"], [])
        self.assertEqual(len(list(self.project.glob("isc-ai.rules.json.*.bak"))), 1)

    def test_unknown_id_raises(self):
        self.write_rules({"version": 1, "packages": {}, "exceptions": []})
        with self.assertRaises(ValueError):
            rules_write.remove_exception(self.project, "missing", apply=False)


class PendingPlanTests(RulesWriteTestCase):
    def test_apply_pending_plan_writes_the_saved_new_content(self):
        self.write_rules({"version": 1, "packages": {}, "exceptions": []})
        executed, fp, plan = rules_write.add_exception(self.project, ITEM, apply=False)
        self.assertFalse(self.rules_path().read_text(encoding="utf-8").strip() == "")
        ok, reason = rules_write.apply_pending_plan(self.project, fp)
        self.assertTrue(ok, reason)
        data = config_module.read_json(self.rules_path())
        self.assertEqual(data["exceptions"], [ITEM])

    def test_drift_refuses_to_apply(self):
        self.write_rules({"version": 1, "packages": {}, "exceptions": []})
        executed, fp, plan = rules_write.add_exception(self.project, ITEM, apply=False)
        # The file changed after the plan was made.
        self.write_rules({"version": 1, "packages": {}, "exceptions": [dict(ITEM, id="other")]})
        ok, reason = rules_write.apply_pending_plan(self.project, fp)
        self.assertFalse(ok)
        self.assertEqual(reason, "drift")
        data = config_module.read_json(self.rules_path())
        self.assertEqual([item["id"] for item in data["exceptions"]], ["other"])

    def test_unknown_fingerprint_refuses(self):
        ok, reason = rules_write.apply_pending_plan(self.project, "0" * 16)
        self.assertFalse(ok)
        self.assertEqual(reason, "unknown_or_stale_fingerprint")

    def test_malformed_fingerprint_is_rejected_without_touching_the_filesystem(self):
        ok, reason = rules_write.apply_pending_plan(self.project, "../../etc/passwd")
        self.assertFalse(ok)
        self.assertEqual(reason, "unknown_or_stale_fingerprint")


class NarrowingDetectionTests(unittest.TestCase):
    def test_shortening_is_narrowing_lengthening_is_widening(self):
        base = {"exceptions": [dict(ITEM, expires="2027-01-01")]}
        self.assertTrue(rules_write.is_renewal_narrowing(base, "e1", "2026-01-01"))
        self.assertTrue(rules_write.is_renewal_narrowing(base, "e1", "2027-01-01"))
        self.assertFalse(rules_write.is_renewal_narrowing(base, "e1", "2028-01-01"))

    def test_unknown_target_or_bad_date_is_treated_as_widening(self):
        base = {"exceptions": [dict(ITEM, expires="2027-01-01")]}
        self.assertFalse(rules_write.is_renewal_narrowing(base, "missing", "2020-01-01"))
        self.assertFalse(rules_write.is_renewal_narrowing(base, "e1", "not-a-date"))


if __name__ == "__main__":
    unittest.main()
