"""config.py integration with isc-ai.rules.json: SitePolicy.rules, legacy
translation into the unified exception view, dual-definition fail-closed
check, and IRIS_EXTRA_PACKAGES gating once a rules file exists.
"""

import json

from tests.test_config import FilesystemTestCase
from tests.hermetic_test_support import reference_policy_data
from isc_ai import config as iris_mcp_config
from isc_ai import rules as isc_rules
from isc_ai.config import ConfigError, STATE_DIR


class RulesConfigTestCase(FilesystemTestCase):
    def write_config(self, data):
        iris_mcp_config.write_json(self.project / STATE_DIR / "config.json", data)

    def write_rules(self, data):
        (self.project / isc_rules.RULES_FILENAME).write_text(json.dumps(data), encoding="utf-8")

    def load(self, environ=None):
        return iris_mcp_config.load_runtime_config(self.project, environ=environ or {})


class RulesFieldWiringTests(RulesConfigTestCase):
    def test_rules_file_loads_and_appears_on_policy(self):
        self.write_config(reference_policy_data())
        self.write_rules({"version": 1, "packages": {"USER": [{"prefix": "Custom.Extra"}]}})
        runtime = self.load()
        self.assertIsNotNone(runtime.policy.rules)
        self.assertIn("USER", runtime.policy.rules.packages)

    def test_no_rules_file_leaves_policy_rules_none(self):
        self.write_config(reference_policy_data())
        runtime = self.load()
        self.assertIsNone(runtime.policy.rules)


class DualDefinitionTests(RulesConfigTestCase):
    def test_duplicate_code_write_signature_raises_naming_both(self):
        self.write_config(reference_policy_data())
        # Same (namespace, hosts, path_prefixes) signature as the "INT UAT active node" legacy entry.
        self.write_rules({"version": 1, "exceptions": [{
            "id": "dup-1", "label": "Duplicate INT UAT active node", "ticket": "TCK-9",
            "expires": "2099-01-01", "capability": "code_write", "roles": ["UAT"],
            "hosts": ["10.244.20.4", "10.244.20.5", "achsuat01", "achsuat02"],
            "namespaces": ["INT"], "path_prefixes": ["/intuat01", "/intuat02"],
        }]})
        with self.assertRaisesRegex(ConfigError, "duplicates the legacy exception"):
            self.load()

    def test_non_overlapping_exceptions_from_both_sources_load_fine(self):
        self.write_config(reference_policy_data())
        self.write_rules({"version": 1, "exceptions": [{
            "id": "new-1", "label": "New BASE direct-node exception", "ticket": "TCK-10",
            "expires": "2099-01-01", "capability": "code_write", "roles": ["BASE"],
            "hosts": ["10.244.20.4"],
        }]})
        runtime = self.load()
        self.assertEqual(len(runtime.policy.rules.exceptions), 1)


class ExtraPackagesEnvGatingTests(RulesConfigTestCase):
    def test_extra_packages_env_ignored_with_note_when_rules_file_present(self):
        self.write_config(reference_policy_data())
        self.write_rules({"version": 1})
        runtime = self.load(environ={iris_mcp_config.EXTRA_PACKAGES_ENV: "USER:Foo"})
        self.assertFalse(runtime.policy.package_allowed("USER", "BASE", "Foo.Bar.cls"))
        self.assertTrue(any("ignored" in note for note in runtime.notes))

    def test_extra_packages_env_still_applies_without_a_rules_file(self):
        self.write_config(reference_policy_data())
        runtime = self.load(environ={iris_mcp_config.EXTRA_PACKAGES_ENV: "USER:Foo"})
        self.assertTrue(runtime.policy.package_allowed("USER", "BASE", "Foo.Bar.cls"))


class EffectiveCapabilityExceptionsTests(RulesConfigTestCase):
    def test_merges_legacy_and_rules_file_per_capability(self):
        self.write_config(reference_policy_data())
        self.write_rules({"version": 1, "exceptions": [{
            "id": "new-1", "label": "New BASE direct-node exception", "ticket": "TCK-10",
            "expires": "2099-01-01", "capability": "code_write", "roles": ["BASE"],
            "hosts": ["10.244.20.4"],
        }]})
        policy = self.load().policy
        # Legacy UAT exceptions still resolve through the unified accessor.
        legacy_hit = policy.effective_capability_exceptions(
            "code_write", namespace="INT", host="10.244.20.4", path_prefix="/intuat01", role="UAT")
        self.assertEqual([exc.label for exc in legacy_hit], ["INT UAT active node"])
        # The new rules-file BASE exception is reachable too, and only for BASE.
        base_hit = policy.effective_capability_exceptions(
            "code_write", namespace="INT", host="10.244.20.4", role="BASE")
        self.assertEqual([exc.label for exc in base_hit], ["New BASE direct-node exception"])
        self.assertEqual(policy.effective_capability_exceptions(
            "code_write", namespace="INT", host="10.244.20.4", role="UAT",
        ), legacy_hit)  # BASE-scoped exception must not leak into a UAT lookup.

    def test_read_capability_merges_legacy_production_read_flag(self):
        policy = iris_mcp_config.SitePolicy()
        servers = {"achslive": {"production_read": True}, "other": {"production_read": False}}
        hits = policy.effective_capability_exceptions("read", server="achslive", servers=servers)
        self.assertEqual(len(hits), 1)
        self.assertEqual(policy.effective_capability_exceptions("read", server="other", servers=servers), ())


class ProjectDiscoveryUnaffectedTests(RulesConfigTestCase):
    def test_rules_file_presence_does_not_change_project_root_resolution(self):
        self.write_config(reference_policy_data())
        nested = self.project / "BASE-ACHS" / "src" / "Custom"
        nested.mkdir(parents=True)
        without_rules = iris_mcp_config.find_project_root(nested)
        self.write_rules({"version": 1})
        with_rules = iris_mcp_config.find_project_root(nested)
        self.assertEqual(without_rules, self.project)
        self.assertEqual(with_rules, self.project)


if __name__ == "__main__":
    import unittest
    unittest.main()
