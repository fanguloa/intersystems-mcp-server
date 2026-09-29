"""Vendoring an external Claude Code marketplace into the harness bundle must be safe:
refuse a moved pin, refuse to clobber a skill it did not bring in, write nothing without
--apply, and leave the manifest/provenance verifiably matching what landed on disk.

The fake marketplace repo built here is a real git checkout (no network: git init + git
commit against a throwaway identity), so resolve_source's pin verification is exercised
against real git plumbing, not a stub.
"""

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import sync_marketplace as sm  # noqa: E402

GIT_IDENTITY = ["-c", "user.name=isc-ai-tests", "-c", "user.email=isc-ai-tests@example.invalid"]


def _run_git(args, cwd):
    result = subprocess.run(["git", *GIT_IDENTITY, *args], cwd=cwd,
                             capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


class MarketplaceFixture(unittest.TestCase):
    """A throwaway git repo shaped like the real ai-enablement marketplace: a top-level
    marketplace.json plus two plugins (alpha, beta), each with one skill."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)

        self.repo = self.root / "marketplace"
        self.repo.mkdir()
        _run_git(["init"], cwd=self.repo)

        plugin_meta = self.repo / ".claude-plugin"
        plugin_meta.mkdir()
        (plugin_meta / "marketplace.json").write_text(json.dumps({
            "name": "fake-marketplace",
            "plugins": [
                {"name": "alpha", "source": "./plugins/alpha", "version": "1.0.0"},
                {"name": "beta", "source": "./plugins/beta", "version": "2.0.0"},
            ],
        }), encoding="utf-8")

        self._write_plugin("alpha", "1.0.0", "alpha-skill",
                            "# Alpha skill\n\nHarmless reviewed content.\n")
        self._write_plugin("beta", "2.0.0", "beta-skill",
                            "# Beta skill\n\nAlso harmless.\n")

        _run_git(["add", "."], cwd=self.repo)
        _run_git(["commit", "-m", "seed fake marketplace"], cwd=self.repo)
        self.commit = _run_git(["rev-parse", "HEAD"], cwd=self.repo)

        # A separate harness fixture, distinct from the real isc_ai/resources/harness,
        # following the same mock.patch.multiple(module, ...) pattern test_setup.py uses.
        self.harness = self.root / "harness"
        (self.harness / "skills").mkdir(parents=True)
        self.manifest_path = self.harness / "manifest.json"
        self.manifest_path.write_text(json.dumps({
            "schema": "isc-ai.harness-manifest.v1",
            "bundle_version": 1,
            "files": [],
        }), encoding="utf-8")
        self.provenance_path = self.harness / "vendor-provenance.json"

        self.patches = mock.patch.multiple(
            sm, HARNESS=self.harness, MANIFEST=self.manifest_path,
            PROVENANCE=self.provenance_path,
        )
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def _write_plugin(self, name, version, skill_name, skill_text):
        plugin_dir = self.repo / "plugins" / name
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": name, "version": version}), encoding="utf-8")
        skill_dir = plugin_dir / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")

    def manifest(self):
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))


class PinVerificationTests(MarketplaceFixture):
    def test_matching_pin_resolves_to_the_checkout(self):
        root = sm.resolve_source(str(self.repo), self.commit)
        self.assertEqual(root, self.repo)

    def test_pin_mismatch_is_refused_without_reading_the_repo_further(self):
        wrong = "0" * 40
        with self.assertRaisesRegex(sm.SyncError, "mismatch"):
            sm.resolve_source(str(self.repo), wrong)

    def test_malformed_commit_is_refused(self):
        with self.assertRaisesRegex(sm.SyncError, "40 hex"):
            sm.resolve_source(str(self.repo), "not-a-commit")


class PlanTests(MarketplaceFixture):
    def test_plan_mode_writes_nothing(self):
        root = sm.resolve_source(str(self.repo), self.commit)
        result = sm.plan(root, ["alpha", "beta"])

        self.assertTrue(result.ok)
        self.assertFalse(list((self.harness / "skills").iterdir()))
        self.assertFalse(self.provenance_path.exists())
        self.assertEqual(self.manifest()["files"], [])
        self.assertEqual(sorted(result.skills_by_plugin["alpha"]), ["alpha-skill"])
        self.assertEqual(sorted(result.skills_by_plugin["beta"]), ["beta-skill"])

    def test_allowlist_respected_only_requested_plugins_are_planned(self):
        root = sm.resolve_source(str(self.repo), self.commit)
        result = sm.plan(root, ["alpha"])

        self.assertTrue(result.ok)
        self.assertEqual(set(result.skills_by_plugin), {"alpha"})
        destinations = {destination for destination, *_ in result.candidates}
        self.assertEqual(destinations, {"skills/alpha-skill/SKILL.md"})

    def test_collision_with_an_existing_non_vendored_skill_is_refused(self):
        existing = self.harness / "skills" / "alpha-skill"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text("# Pre-existing isc-ai skill, not ours\n",
                                            encoding="utf-8")

        root = sm.resolve_source(str(self.repo), self.commit)
        result = sm.plan(root, ["alpha"])

        self.assertFalse(result.ok)
        self.assertIn("alpha-skill", result.collisions)
        # the pre-existing file must be provably untouched
        self.assertEqual((existing / "SKILL.md").read_text(encoding="utf-8"),
                          "# Pre-existing isc-ai skill, not ours\n")

    def test_a_credential_shaped_value_in_a_skill_file_is_blocked_by_the_gate(self):
        skill_dir = self.repo / "plugins" / "alpha" / "skills" / "alpha-skill"
        (skill_dir / "SKILL.md").write_text(
            "# Alpha skill\n\n| alias | host | someuser:hunter2x |\n", encoding="utf-8")
        _run_git(["commit", "-am", "inject a credential-shaped value"], cwd=self.repo)
        commit = _run_git(["rev-parse", "HEAD"], cwd=self.repo)

        root = sm.resolve_source(str(self.repo), commit)
        result = sm.plan(root, ["alpha"])

        self.assertFalse(result.ok)
        self.assertTrue(result.problems)


class ApplyTests(MarketplaceFixture):
    def _apply(self, plugins, audiences=None):
        root = sm.resolve_source(str(self.repo), self.commit)
        result = sm.plan(root, plugins)
        self.assertTrue(result.ok, f"unexpected problems/collisions: "
                                    f"{result.problems} {result.collisions}")
        sm.apply_plan(result, repo_url="git@example.invalid:fake/marketplace.git",
                       commit=self.commit, marketplace_label="fake-marketplace",
                       audiences=audiences or {})
        return result

    def test_apply_copies_files_and_the_manifest_hash_matches_what_landed_on_disk(self):
        self._apply(["alpha"])

        target = self.harness / "skills" / "alpha-skill" / "SKILL.md"
        self.assertTrue(target.is_file())

        manifest = self.manifest()
        entry = next(item for item in manifest["files"]
                     if item["destination"] == "skills/alpha-skill/SKILL.md")
        self.assertEqual(entry["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual(entry["category"], "skill")
        self.assertEqual(manifest["bundle_version"], 2)

    def test_apply_records_vendor_metadata_on_the_manifest_entry(self):
        self._apply(["alpha"], audiences={"alpha": "technical"})

        manifest = self.manifest()
        entry = next(item for item in manifest["files"]
                     if item["destination"] == "skills/alpha-skill/SKILL.md")
        self.assertEqual(entry["vendor"]["plugin"], "alpha")
        self.assertEqual(entry["vendor"]["plugin_version"], "1.0.0")
        self.assertEqual(entry["vendor"]["audience"], "technical")
        self.assertEqual(entry["vendor"]["commit"], self.commit)

    def test_provenance_file_records_source_commit_and_license_note(self):
        self._apply(["alpha", "beta"], audiences={"beta": "technical"})

        provenance = json.loads(self.provenance_path.read_text(encoding="utf-8"))
        source = provenance["sources"]["fake-marketplace"]
        self.assertEqual(source["repo_url"], "git@example.invalid:fake/marketplace.git")
        self.assertEqual(source["commit"], self.commit)
        self.assertIn("no license file", source["license_note"].lower())
        plugins = {plugin["name"]: plugin for plugin in source["plugins"]}
        self.assertEqual(plugins["alpha"]["audience"], "standard")
        self.assertEqual(plugins["beta"]["audience"], "technical")
        self.assertEqual(plugins["beta"]["skills"], ["beta-skill"])

    def test_allowlist_respected_the_unselected_plugin_never_reaches_disk(self):
        self._apply(["alpha"])

        self.assertFalse((self.harness / "skills" / "beta-skill").exists())
        manifest = self.manifest()
        self.assertFalse(any("beta-skill" in item["destination"] for item in manifest["files"]))

    def test_rerunning_the_same_plugin_is_idempotent_not_a_collision(self):
        self._apply(["alpha"])
        # A second sync of the same, already-vendored plugin must not be treated as
        # clobbering a foreign skill directory.
        second = self._apply(["alpha"])
        self.assertTrue(second.ok)
        manifest = self.manifest()
        self.assertEqual(manifest["bundle_version"], 3)


if __name__ == "__main__":
    unittest.main()
