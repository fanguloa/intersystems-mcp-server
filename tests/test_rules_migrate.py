"""WU5: 'isc-ai rules migrate' backend (R9).

isc_ai.rules_migrate.plan()/apply() move routes.uat_write_exceptions,
routes.live_config_window, runtime.runner_hosts, packages and per-server
production_read flags into isc-ai.rules.json, plus the explicit Custom.{SITE}
baseline package grant. plan() never writes; apply() backs up every file it
changes and refuses all writes on any conflict with a hand-written rule.
"""

import datetime
import json
import tempfile
import unittest
from pathlib import Path

from isc_ai import config as config_module
from isc_ai import rules as rules_module
from isc_ai import rules_migrate

from tests.hermetic_test_support import REF, reference_policy_data

TODAY = datetime.date(2026, 9, 30)


class MigrationTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.home = self.root / "home"
        self.project.mkdir()
        self.home.mkdir()
        self.config_dir = self.project / config_module.STATE_DIR
        self.config_dir.mkdir()
        self.config_path = self.config_dir / "config.json"

    def full_fixture_data(self):
        """reference_policy_data() plus a LIVE window, a runner host and a
        production_read server -- every legacy key WU5 must migrate."""
        data = reference_policy_data()
        ha_nodes = list(data["routes"]["direct_ha_nodes"])
        data["routes"]["live_config_window"] = {
            "label": "ACHS LIVE pre-go-live", "ticket": "ACHSLIVE-1", "expires": "2026-11-01",
            "namespaces": ["LIVE-ACHS"], "hosts": ha_nodes[:1],
            "active_node_evidence_namespace": "LIVE-ACHS",
        }
        data["runtime"] = {"runner_hosts": [
            {"namespace": REF["uat_namespace"], "runner_namespace": "USER", "roles": ["UAT"]},
        ]}
        data["servers"] = {"prod1": {"host": "prod1.invalid", "namespace": "LIVE-ACHS",
                                     "username": "svc", "password": "p", "is_production": True,
                                     "production_read": True}}
        return data

    def write_config(self, data):
        config_module.write_json(self.config_path, data)

    def read_config(self):
        return config_module.read_json(self.config_path)

    def read_rules(self):
        return config_module.read_json(self.project / rules_module.RULES_FILENAME)


class PlanTests(MigrationTestCase):
    def test_plan_never_writes_anything(self):
        data = self.full_fixture_data()
        self.write_config(data)
        before = self.config_path.read_bytes()
        report = rules_migrate.plan(self.project, home=self.home, today=TODAY)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertFalse((self.project / rules_module.RULES_FILENAME).exists())
        self.assertEqual(report["mode"], "check")
        # 3 uat_write_exceptions + 1 live window + 1 runner + 1 production_read = 6
        self.assertEqual(len(report["exceptions"]), 6)

    def test_missing_expiry_entries_get_the_default_and_are_noted(self):
        data = self.full_fixture_data()
        self.write_config(data)
        report = rules_migrate.plan(self.project, home=self.home, today=TODAY)
        expected_expiry = "2027-09-30"  # TODAY + 365 days
        code_write = [item for item in report["exceptions"] if item["capability"] == "code_write"]
        runner = [item for item in report["exceptions"] if item["capability"] == "runner"]
        read = [item for item in report["exceptions"] if item["capability"] == "read"]
        live_window = [item for item in report["exceptions"] if item["capability"] == "ensemble_control"]
        self.assertTrue(all(item["expires"] == expected_expiry for item in code_write))
        self.assertTrue(all(item["expires"] == expected_expiry for item in runner))
        self.assertTrue(all(item["expires"] == expected_expiry for item in read))
        # The LIVE window already had an explicit expiry: it must be preserved, not defaulted.
        self.assertEqual(live_window[0]["expires"], "2026-11-01")
        self.assertEqual(len(report["expiry_defaulted"]), 5)
        self.assertTrue(any("365 days" in note for note in report["notes"]))

    def test_baseline_package_rule_is_written_explicitly(self):
        data = self.full_fixture_data()
        self.write_config(data)
        report = rules_migrate.plan(self.project, home=self.home, today=TODAY)
        baseline = report["packages"][REF["base_namespace"]]
        self.assertIn({"prefix": f"Custom.{REF['ccr_prefix']}"}, baseline)

    def test_production_read_from_the_user_servers_file_is_found_too(self):
        data = reference_policy_data()
        self.write_config(data)
        (self.home / config_module.STATE_DIR).mkdir()
        config_module.write_json(self.home / config_module.STATE_DIR / "servers.json",
                                 {"servers": {"userprod": {"host": "u.invalid", "production_read": True}}})
        report = rules_migrate.plan(self.project, home=self.home, today=TODAY)
        read_items = [item for item in report["exceptions"] if item["capability"] == "read"]
        self.assertEqual([item["servers"] for item in read_items], [["userprod"]])
        self.assertIn(str(self.home / config_module.STATE_DIR / "servers.json"), report["files_to_backup"])


class ApplyTests(MigrationTestCase):
    def test_apply_backs_up_config_json_before_writing(self):
        data = self.full_fixture_data()
        self.write_config(data)
        original = self.config_path.read_bytes()
        report = rules_migrate.apply(self.project, home=self.home, today=TODAY)
        self.assertEqual(report["mode"], "apply")
        backups = [entry for entry in report["performed"]
                  if entry["action"] == "backed-up" and entry["file"] == str(self.config_path)]
        self.assertEqual(len(backups), 1)
        backup_path = Path(backups[0]["backup"])
        self.assertTrue(backup_path.is_file())
        self.assertEqual(backup_path.read_bytes(), original)

    def test_apply_writes_rules_file_and_strips_migrated_keys(self):
        data = self.full_fixture_data()
        self.write_config(data)
        rules_migrate.apply(self.project, home=self.home, today=TODAY)
        new_config = self.read_config()
        self.assertNotIn("uat_write_exceptions", new_config.get("routes", {}))
        self.assertNotIn("live_config_window", new_config.get("routes", {}))
        self.assertNotIn("runtime", new_config)
        self.assertNotIn("packages", new_config)
        self.assertNotIn("production_read", new_config["servers"]["prod1"])
        self.assertEqual(new_config["servers"]["prod1"]["host"], "prod1.invalid")

        rules_data = self.read_rules()
        self.assertEqual(rules_data["version"], 1)
        self.assertEqual(len(rules_data["exceptions"]), 6)
        self.assertIn({"prefix": f"Custom.{REF['ccr_prefix']}"}, rules_data["packages"][REF["base_namespace"]])

    def test_production_read_server_entry_becomes_a_read_exception_for_that_server_id(self):
        data = self.full_fixture_data()
        self.write_config(data)
        rules_migrate.apply(self.project, home=self.home, today=TODAY)
        rules_data = self.read_rules()
        read_items = [item for item in rules_data["exceptions"] if item["capability"] == "read"]
        self.assertEqual(len(read_items), 1)
        self.assertEqual(read_items[0]["servers"], ["prod1"])

    def test_conflict_with_a_hand_written_rule_refuses_all_writes(self):
        data = self.full_fixture_data()
        self.write_config(data)
        # A hand-written code_write exception with the exact same signature as one
        # of the migrated INT UAT exceptions (roles=UAT, namespaces=[INT], the same
        # path_prefixes) -- the migration must refuse entirely, not skip just this one.
        config_module.write_json(self.project / rules_module.RULES_FILENAME, {
            "version": 1, "exceptions": [{
                "id": "hand-written", "label": "Hand-written INT UAT", "ticket": "TCK-9",
                "expires": "2099-01-01", "capability": "code_write", "roles": ["UAT"],
                "namespaces": ["INT"], "path_prefixes": list(REF["int_node_prefixes"]),
            }],
        })
        before_config = self.config_path.read_bytes()
        before_rules = (self.project / rules_module.RULES_FILENAME).read_bytes()
        report = rules_migrate.apply(self.project, home=self.home, today=TODAY)
        self.assertTrue(report["conflicts"])
        self.assertEqual(report["performed"], [])
        self.assertEqual(self.config_path.read_bytes(), before_config)
        self.assertEqual((self.project / rules_module.RULES_FILENAME).read_bytes(), before_rules)

    def test_gates_are_behaviorally_equivalent_before_and_after_migration(self):
        data = self.full_fixture_data()
        self.write_config(data)
        old_policy = config_module.parse_policy(data, self.project)

        rules_migrate.apply(self.project, home=self.home, today=TODAY)
        new_config = self.read_config()
        new_rules = self.read_rules()
        new_policy = config_module.parse_policy(new_config, self.project, rules_data=new_rules)

        # code_write: the three INT/Gateway UAT exceptions must resolve identically.
        for namespace, host, path_prefix in (
            ("INT", REF["clinical_vip"], REF["int_vip_prefix"]),
            ("INT", REF["ha_nodes"][0], REF["int_node_prefixes"][0]),
            (REF["gateway_namespace"], REF["ha_nodes"][0], REF["clinical_node_prefixes"][0]),
            ("INT", "unrelated-host", REF["int_vip_prefix"]),
        ):
            old = old_policy.effective_capability_exceptions(
                "code_write", namespace=namespace, host=host, role="UAT", path_prefix=path_prefix)
            new = new_policy.effective_capability_exceptions(
                "code_write", namespace=namespace, host=host, role="UAT", path_prefix=path_prefix)
            self.assertEqual(bool(old), bool(new), (namespace, host, path_prefix))

        # ensemble_control: same namespace/host must still resolve to an open window.
        for namespace, host in (("LIVE-ACHS", REF["ha_nodes"][0]), ("LIVE-ACHS", "other-host")):
            old = old_policy.effective_capability_exceptions(
                "ensemble_control", namespace=namespace, host=host, today=TODAY)
            new = new_policy.effective_capability_exceptions(
                "ensemble_control", namespace=namespace, host=host, today=TODAY)
            self.assertEqual(bool(old), bool(new), (namespace, host))

        # runner: same namespace/role must resolve to the same runner_namespace.
        old_runner = old_policy.runner_host_for(REF["uat_namespace"], "UAT")
        new = new_policy.effective_capability_exceptions(
            "runner", namespace=REF["uat_namespace"], role="UAT")
        self.assertTrue(new)
        self.assertEqual(new[0].runner_namespace, old_runner.runner_namespace)

        # read: the migrated server's production_read flag must open the read gate.
        old_read = old_policy.effective_capability_exceptions("read", server="prod1", servers=data["servers"])
        new_read = new_policy.effective_capability_exceptions("read", server="prod1", servers=new_config.get("servers", {}))
        self.assertTrue(old_read)
        self.assertTrue(new_read)

        # package_allowed: the explicit baseline grant covers what the mutation
        # prefix already covered implicitly through 'packages' before migration.
        self.assertTrue(new_policy.package_allowed(REF["base_namespace"], "BASE", "Custom.ACHS.Foo.cls"))
        self.assertEqual(
            old_policy.package_allowed(REF["base_namespace"], "BASE", "Custom.ACHS.Foo.cls"),
            new_policy.package_allowed(REF["base_namespace"], "BASE", "Custom.ACHS.Foo.cls"))


if __name__ == "__main__":
    unittest.main()
