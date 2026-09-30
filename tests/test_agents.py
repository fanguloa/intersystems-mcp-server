"""Contract tests for the sub-agent model assignment.

The invariant: a concrete model id is never invented, and one that this workstation
cannot reach is an error before anything is written.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isc_ai import agents as isc_agents
from isc_ai import config as isc_config
from isc_ai.config import STATE_DIR

POLICY = {
    "version": 1,
    "site": {"name": "site", "ccr_id": {"prefix": "SITE", "digits": 4}},
    "canonical_base": {"server_id": "my-base", "host": "base.example.invalid",
                       "path_prefix": "/base01", "namespace": "BASE-SITE"},
    "packages": {"BASE-SITE": [{"prefix": "Custom.Site"}]},
}

AVAILABLE = ["amazon-bedrock/anthropic.claude-haiku-4-5", "amazon-bedrock/anthropic.claude-sonnet-5",
             "anthropic/claude-sonnet-5", "opencode/something-free"]


class AgentsTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "project"
        (self.home / ".config" / "opencode").mkdir(parents=True)
        (self.project / STATE_DIR).mkdir(parents=True)
        self.config = self.project / STATE_DIR / "config.json"
        self.write_policy({})
        self.opencode = self.home / ".config" / "opencode" / "opencode.json"
        self.environ = {}

    def write_policy(self, agents_section):
        data = dict(POLICY)
        if agents_section:
            data["agents"] = agents_section
        self.config.write_text(json.dumps(data), encoding="utf-8")

    def plan(self, models=AVAILABLE):
        with patch("isc_ai.agents.available_models", return_value=models):
            return isc_agents.plan(project_dir=self.project, home=self.home, environ=self.environ)

    def apply(self, models=AVAILABLE):
        with patch("isc_ai.agents.available_models", return_value=models):
            return isc_agents.apply(project_dir=self.project, home=self.home, environ=self.environ)

    def written(self):
        return json.loads(self.opencode.read_text(encoding="utf-8"))


class ResolutionTests(AgentsTestCase):
    def test_without_configuration_the_model_is_left_to_the_client(self):
        report, _ = self.plan()
        self.assertTrue(all(item["opencode"] is None for item in report["roles"]))
        self.assertTrue(all(finding["code"] == "MODEL_UNRESOLVED" for finding in report["findings"]))
        self.apply()
        entry = self.written()["agent"]["sdd-apply"]
        self.assertEqual(entry["mode"], "subagent")
        self.assertNotIn("model", entry, "no model is better than an invented one")

    def test_the_candidates_reported_match_the_family_preference(self):
        report, _ = self.plan()
        archive = next(item for item in report["roles"] if item["role"] == "sdd-archive")
        apply_role = next(item for item in report["roles"] if item["role"] == "sdd-apply")
        self.assertTrue(all("haiku" in model for model in archive["candidates"]))
        self.assertTrue(all("sonnet" in model for model in apply_role["candidates"]))

    def test_a_configured_model_is_used_and_a_default_covers_the_rest(self):
        self.write_policy({"models": {"default": "anthropic/claude-sonnet-5",
                                      "sdd-archive": "amazon-bedrock/anthropic.claude-haiku-4-5"}})
        self.apply()
        agent = self.written()["agent"]
        self.assertEqual(agent["sdd-apply"]["model"], "anthropic/claude-sonnet-5")
        self.assertEqual(agent["sdd-archive"]["model"], "amazon-bedrock/anthropic.claude-haiku-4-5")

    def test_an_unavailable_model_is_an_error_and_nothing_is_written(self):
        self.write_policy({"models": {"sdd-apply": "provider/model-that-does-not-exist"}})
        report, _ = self.plan()
        errors = [finding for finding in report["findings"] if finding["severity"] == "error"]
        self.assertEqual([finding["code"] for finding in errors], ["MODEL_UNAVAILABLE"])
        with self.assertRaisesRegex(isc_config.ConfigError, "not available"):
            self.apply()
        self.assertFalse(self.opencode.exists())

    def test_the_claude_side_is_read_from_the_packaged_bundle(self):
        report, _ = self.plan()
        by_role = {item["role"]: item for item in report["roles"]}
        self.assertEqual(by_role["sdd-apply"]["claude"], "sonnet")
        self.assertEqual(by_role["sdd-archive"]["claude"], "haiku")

    def test_a_model_id_without_a_provider_is_rejected_by_the_configuration(self):
        self.write_policy({"models": {"sdd-apply": "sonnet"}})
        with self.assertRaisesRegex(isc_config.ConfigError, "provider-qualified"):
            self.plan()

    def test_unknown_models_are_reported_as_unverified_rather_than_assumed_wrong(self):
        self.write_policy({"models": {"sdd-apply": "anything/at-all"}})
        report, _ = self.plan(models=None)
        self.assertFalse(report["models_verified"])
        self.assertEqual([finding for finding in report["findings"]
                          if finding["severity"] == "error"], [])


class WriteTests(AgentsTestCase):
    def test_existing_agent_entries_keep_their_other_fields(self):
        self.opencode.write_text(json.dumps(
            {"agent": {"sdd-apply": {"description": "mine", "temperature": 0.1}}}), encoding="utf-8")
        self.apply()
        entry = self.written()["agent"]["sdd-apply"]
        self.assertEqual(entry["description"], "mine")
        self.assertEqual(entry["temperature"], 0.1)
        self.assertEqual(entry["mode"], "subagent")

    def test_the_plan_writes_nothing_and_a_second_apply_changes_nothing(self):
        report, writes = self.plan()
        self.assertTrue(writes)
        self.assertFalse(self.opencode.exists())
        self.apply()
        first = self.opencode.read_bytes()
        self.apply()
        self.assertEqual(self.opencode.read_bytes(), first)

    def test_unrelated_configuration_survives(self):
        self.opencode.write_text(json.dumps({"mcp": {"keep": {"command": ["x"]}}}), encoding="utf-8")
        self.apply()
        self.assertEqual(self.written()["mcp"], {"keep": {"command": ["x"]}})


if __name__ == "__main__":
    unittest.main()
