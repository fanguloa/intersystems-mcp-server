"""isc-ai.rules.json: schema, parsing, validation and query for explicit,
versioned package grants and capability exceptions (isc_ai/rules.py).

Hermetic: no network, no real filesystem outside tempfile.
"""

import datetime
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.hermetic_test_support import HermeticTestCase
from isc_ai import rules as isc_rules
from isc_ai.config import ConfigError


def write_rules_file(project_dir, data):
    import json
    path = Path(project_dir) / isc_rules.RULES_FILENAME
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class RulesFileDiscoveryTests(HermeticTestCase):
    """R1: discovery and fail-closed parsing."""

    def test_missing_file_yields_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("builtins.open", side_effect=None):
                self.assertIsNone(isc_rules.load_rules(tmp))

    def test_invalid_json_raises_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("builtins.open", side_effect=None):
                (Path(tmp) / isc_rules.RULES_FILENAME).write_text("{not json", encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, "not valid JSON"):
                    isc_rules.load_rules(tmp)

    def test_non_object_raises_config_error(self):
        with self.assertRaisesRegex(ConfigError, "JSON object"):
            isc_rules.parse_rules([])

    def test_unsupported_version_raises_config_error(self):
        with self.assertRaisesRegex(ConfigError, "Unsupported rules schema version"):
            isc_rules.parse_rules({"version": 2})

    def test_unknown_top_level_key_raises_config_error(self):
        with self.assertRaisesRegex(ConfigError, "Unknown top-level keys"):
            isc_rules.parse_rules({"version": 1, "bogus": {}})

    def test_absent_data_yields_empty_ruleset(self):
        ruleset = isc_rules.parse_rules(None)
        self.assertEqual(ruleset.packages, {})
        self.assertEqual(ruleset.exceptions, ())

    def test_load_rules_reads_and_parses_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("builtins.open", side_effect=None):
                write_rules_file(tmp, {"version": 1, "packages": {"USER": [{"prefix": "Custom.Foo"}]}})
                ruleset = isc_rules.load_rules(tmp)
            self.assertEqual(len(ruleset.packages["USER"]), 1)


class PackageRuleParsingTests(HermeticTestCase):
    """R2: package grants (exact kind, forbidden roots, {SITE}, expiry)."""

    def test_prefix_class_and_document_grants_parse(self):
        ruleset = isc_rules.parse_rules({
            "version": 1,
            "packages": {
                "USER": [
                    {"prefix": "Custom.Foo"},
                    {"class": "Custom.Bar.Baz"},
                    {"document": "Custom.Qux.mac"},
                ],
            },
        })
        grants = ruleset.packages["USER"]
        self.assertEqual(len(grants), 3)
        self.assertTrue(grants[0].allows("Custom.Foo.A.cls", "Custom.Foo.A", "BASE"))
        self.assertTrue(grants[1].allows("Custom.Bar.Baz.cls", "Custom.Bar.Baz", "BASE"))
        self.assertTrue(grants[2].allows("Custom.Qux.mac", "Custom.Qux", "BASE"))

    def test_site_placeholder_is_resolved_at_parse_time(self):
        ruleset = isc_rules.parse_rules(
            {"version": 1, "packages": {"USER": [{"prefix": "Custom.{SITE}"}]}},
            site_prefix="ACHS",
        )
        grant = ruleset.packages["USER"][0]
        self.assertEqual(grant.prefix, "Custom.ACHS")
        self.assertTrue(grant.allows("Custom.ACHS.Thing.cls", "Custom.ACHS.Thing", "BASE"))

    def test_site_placeholder_without_site_prefix_raises(self):
        with self.assertRaisesRegex(ConfigError, "SITE"):
            isc_rules.parse_rules({"version": 1, "packages": {"USER": [{"prefix": "Custom.{SITE}"}]}})

    def test_forbidden_roots_and_wildcards_rejected(self):
        for grant in ({"prefix": "*"}, {"prefix": "%*"}, {"prefix": "Ens.Custom"},
                      {"prefix": "MCP"}, {"class": "MCP.CCRMutation"},
                      {"document": "Custom.MCP.CCRMutation.cls"}, {"prefix": "A", "class": "B"}, {}):
            with self.subTest(grant=grant), self.assertRaises(ConfigError):
                isc_rules.parse_rules({"version": 1, "packages": {"USER": [grant]}})

    def test_grant_with_expired_expiry_is_inert(self):
        ruleset = isc_rules.parse_rules({
            "version": 1,
            "packages": {"USER": [{"prefix": "Custom.Foo", "expires": "2020-01-01"}]},
        })
        grant = ruleset.packages["USER"][0]
        self.assertFalse(grant.allows("Custom.Foo.A.cls", "Custom.Foo.A", "BASE",
                                      today=datetime.date(2026, 1, 1)))
        self.assertTrue(grant.allows("Custom.Foo.A.cls", "Custom.Foo.A", "BASE",
                                     today=datetime.date(2019, 1, 1)))

    def test_grant_scoped_by_roles_and_servers(self):
        ruleset = isc_rules.parse_rules({
            "version": 1,
            "packages": {"USER": [{"prefix": "Custom.Foo", "roles": ["BASE"], "servers": ["achsbase"]}]},
        })
        grant = ruleset.packages["USER"][0]
        self.assertTrue(grant.allows("Custom.Foo.A.cls", "Custom.Foo.A", "BASE", server="achsbase"))
        self.assertFalse(grant.allows("Custom.Foo.A.cls", "Custom.Foo.A", "UAT", server="achsbase"))
        self.assertFalse(grant.allows("Custom.Foo.A.cls", "Custom.Foo.A", "BASE", server="other"))


class ExceptionRuleParsingTests(HermeticTestCase):
    """R3: exception required fields and capability enum."""

    def _exception(self, **overrides):
        item = {
            "id": "exc-1", "label": "Test exception", "ticket": "TCK-1",
            "expires": "2099-01-01", "capability": "code_write",
        }
        item.update(overrides)
        return isc_rules.parse_rules({"version": 1, "exceptions": [item]}).exceptions[0]

    def test_required_fields_enforced(self):
        for missing in ("id", "label", "ticket", "expires", "capability"):
            item = {"id": "e", "label": "L", "ticket": "T", "expires": "2099-01-01",
                    "capability": "code_write"}
            del item[missing]
            with self.subTest(missing=missing), self.assertRaisesRegex(ConfigError, "missing required keys"):
                isc_rules.parse_rules({"version": 1, "exceptions": [item]})

    def test_capability_must_be_a_known_value(self):
        with self.assertRaisesRegex(ConfigError, "capability"):
            self._exception(capability="delete_everything")

    def test_capability_accepts_every_documented_value(self):
        for capability in ("code_write", "ensemble_control", "runner", "read"):
            exc = self._exception(capability=capability, id=f"e-{capability}")
            self.assertEqual(exc.capability, capability)

    def test_requires_active_node_defaults_true(self):
        exc = self._exception(hosts=["10.1.1.1"])
        self.assertTrue(exc.requires_active_node)

    def test_requires_active_node_can_be_explicitly_false(self):
        exc = self._exception(hosts=["10.1.1.1"], requires_active_node=False)
        self.assertFalse(exc.requires_active_node)

    def test_expired_exception_does_not_match(self):
        ruleset = isc_rules.parse_rules({"version": 1, "exceptions": [{
            "id": "e", "label": "L", "ticket": "T", "expires": "2020-01-01", "capability": "code_write",
        }]})
        self.assertTrue(ruleset.exceptions[0].is_expired(today=datetime.date(2026, 1, 1)))
        self.assertFalse(ruleset.exceptions[0].is_expired(today=datetime.date(2019, 1, 1)))

    def test_unknown_key_rejected(self):
        with self.assertRaisesRegex(ConfigError, "Unknown keys"):
            self._exception(bogus="x")

    def test_runner_namespace_only_allowed_for_runner_capability(self):
        exc = self._exception(capability="runner", runner_namespace="USER")
        self.assertEqual(exc.runner_namespace, "USER")
        with self.assertRaisesRegex(ConfigError, "runner_namespace"):
            self._exception(capability="code_write", runner_namespace="USER")


class MatchingExceptionsTests(HermeticTestCase):
    """R2/R3 query helpers: matching_exceptions, expiring_within, validate."""

    def _ruleset(self):
        return isc_rules.parse_rules({"version": 1, "exceptions": [
            {"id": "cw-1", "label": "BASE direct-node", "ticket": "T1", "expires": "2099-01-01",
             "capability": "code_write", "roles": ["BASE"], "hosts": ["10.1.1.1"]},
            {"id": "en-1", "label": "LIVE window", "ticket": "T2", "expires": "2020-01-01",
             "capability": "ensemble_control"},
            {"id": "rd-1", "label": "server read", "ticket": "T3", "expires": "2099-06-15",
             "capability": "read", "servers": ["achslive"]},
        ]})

    def test_filters_by_capability_and_excludes_expired(self):
        ruleset = self._ruleset()
        code_write = isc_rules.matching_exceptions(ruleset, "code_write", today=datetime.date(2026, 1, 1))
        self.assertEqual([exc.id for exc in code_write], ["cw-1"])
        ensemble = isc_rules.matching_exceptions(ruleset, "ensemble_control", today=datetime.date(2026, 1, 1))
        self.assertEqual(ensemble, ())

    def test_filters_by_scope_when_dimension_is_supplied(self):
        ruleset = self._ruleset()
        matched = isc_rules.matching_exceptions(ruleset, "code_write", role="BASE", host="10.1.1.1",
                                                today=datetime.date(2026, 1, 1))
        self.assertEqual([exc.id for exc in matched], ["cw-1"])
        unmatched = isc_rules.matching_exceptions(ruleset, "code_write", role="UAT",
                                                   today=datetime.date(2026, 1, 1))
        self.assertEqual(unmatched, ())
        by_server = isc_rules.matching_exceptions(ruleset, "read", server="achslive",
                                                   today=datetime.date(2026, 1, 1))
        self.assertEqual([exc.id for exc in by_server], ["rd-1"])
        self.assertEqual(isc_rules.matching_exceptions(ruleset, "read", server="other",
                                                        today=datetime.date(2026, 1, 1)), ())

    def test_expiring_within_finds_entries_in_the_window(self):
        ruleset = self._ruleset()
        soon = isc_rules.expiring_within(ruleset, 7, today=datetime.date(2099, 6, 10))
        self.assertEqual([exc.id for exc in soon], ["rd-1"])
        none_yet = isc_rules.expiring_within(ruleset, 7, today=datetime.date(2026, 1, 1))
        self.assertEqual(none_yet, ())

    def test_validate_reports_expired_exceptions_and_grants(self):
        ruleset = isc_rules.parse_rules({
            "version": 1,
            "packages": {"USER": [{"prefix": "Custom.Foo", "expires": "2020-01-01"}]},
            "exceptions": [{"id": "e1", "label": "Stale", "ticket": "T", "expires": "2020-06-01",
                            "capability": "code_write"}],
        })
        issues = isc_rules.validate(ruleset, today=datetime.date(2026, 1, 1))
        self.assertEqual(len(issues), 2)
        self.assertTrue(any("e1" in issue for issue in issues))
        self.assertTrue(any("Custom.Foo" in issue for issue in issues))

    def test_validate_reports_nothing_when_healthy(self):
        ruleset = isc_rules.parse_rules({"version": 1, "exceptions": [
            {"id": "e1", "label": "Fresh", "ticket": "T", "expires": "2099-01-01", "capability": "code_write"},
        ]})
        self.assertEqual(isc_rules.validate(ruleset, today=datetime.date(2026, 1, 1)), [])


class RenderTemplateTests(HermeticTestCase):
    def test_produces_the_baseline_prefix_grant(self):
        self.assertEqual(isc_rules.render_template("ACHS"), {"prefix": "Custom.ACHS.*"})


if __name__ == "__main__":
    import unittest
    unittest.main()
