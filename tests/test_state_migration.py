"""Contract tests for renaming the state directory.

The rename is only acceptable if an installed deployment survives it. These pin the four
behaviours that make that true: both names are read, proximity beats flavour, two flavours in
one directory is an error rather than a guess, and migrating never overwrites.
"""

import json
import tempfile
import unittest
from pathlib import Path

from isc_ai import config as isc_config
from isc_ai import migrate as isc_migrate

POLICY = {"version": 1, "site": {"name": "x", "ccr_id": {"prefix": "X", "digits": 4}}}


def write_policy(directory, flavour, marker):
    path = Path(directory) / flavour / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(POLICY)
    payload["_marker"] = marker
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class DiscoveryTests(unittest.TestCase):
    """An upgrade must not make a configured deployment look unconfigured."""

    def test_the_current_directory_is_found(self):
        with tempfile.TemporaryDirectory() as raw:
            write_policy(raw, isc_config.STATE_DIR, "current")
            self.assertEqual(isc_config.find_project_root(raw), Path(raw).resolve())

    def test_the_legacy_directory_is_still_found(self):
        with tempfile.TemporaryDirectory() as raw:
            write_policy(raw, isc_config.LEGACY_STATE_DIR, "legacy")
            self.assertEqual(isc_config.find_project_root(raw), Path(raw).resolve())

    def test_a_legacy_project_still_resolves_its_policy_path(self):
        with tempfile.TemporaryDirectory() as raw:
            expected = write_policy(raw, isc_config.LEGACY_STATE_DIR, "legacy")
            self.assertEqual(isc_config.project_config_path(raw, environ={}), expected)

    def test_the_current_flavour_is_written_when_neither_exists(self):
        with tempfile.TemporaryDirectory() as raw:
            resolved = isc_config.project_config_path(raw, environ={})
            self.assertEqual(resolved.parent.name, isc_config.STATE_DIR)

    def test_proximity_beats_flavour(self):
        """A nearby legacy deployment must not be overruled by a distant migrated one."""
        with tempfile.TemporaryDirectory() as raw:
            outer = Path(raw)
            inner = outer / "deployments" / "site"
            inner.mkdir(parents=True)
            write_policy(outer, isc_config.STATE_DIR, "outer-current")
            write_policy(inner, isc_config.LEGACY_STATE_DIR, "inner-legacy")
            self.assertEqual(isc_config.find_project_root(inner), inner.resolve())

    def test_both_flavours_in_one_directory_is_an_error(self):
        """A stale policy that looks live is worse than a refusal."""
        with tempfile.TemporaryDirectory() as raw:
            write_policy(raw, isc_config.STATE_DIR, "current")
            write_policy(raw, isc_config.LEGACY_STATE_DIR, "legacy")
            with self.assertRaises(isc_config.AmbiguousStateError) as caught:
                isc_config.project_config_path(raw, environ={})
            message = str(caught.exception)
            self.assertIn(isc_config.STATE_DIR, message)
            self.assertIn(isc_config.LEGACY_STATE_DIR, message)
            self.assertIn("migrate", message)


class EnvironmentOverrideTests(unittest.TestCase):

    def test_the_legacy_variable_is_still_honoured(self):
        with tempfile.TemporaryDirectory() as raw:
            resolved = isc_config.project_directory(
                environ={isc_config.LEGACY_PROJECT_DIR_ENV: raw})
            self.assertEqual(resolved, Path(raw).resolve())

    def test_the_current_variable_wins_when_both_are_set(self):
        with tempfile.TemporaryDirectory() as new, tempfile.TemporaryDirectory() as old:
            resolved = isc_config.project_directory(environ={
                isc_config.PROJECT_DIR_ENV: new,
                isc_config.LEGACY_PROJECT_DIR_ENV: old,
            })
            self.assertEqual(resolved, Path(new).resolve())


class UserServersTests(unittest.TestCase):

    def test_the_legacy_user_file_is_read_when_it_is_the_only_one(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            legacy = home / isc_config.LEGACY_STATE_DIR / "servers.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("{}", encoding="utf-8")
            self.assertEqual(isc_config.user_servers_path(home), legacy)

    def test_the_current_user_file_wins_when_both_exist(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            for flavour in (isc_config.STATE_DIR, isc_config.LEGACY_STATE_DIR):
                path = home / flavour / "servers.json"
                path.parent.mkdir(parents=True)
                path.write_text("{}", encoding="utf-8")
            self.assertEqual(isc_config.user_servers_path(home).parent.name,
                             isc_config.STATE_DIR)


class MigrationTests(unittest.TestCase):

    def test_plan_reports_without_touching_anything(self):
        with tempfile.TemporaryDirectory() as raw:
            source = write_policy(raw, isc_config.LEGACY_STATE_DIR, "legacy")
            before = source.stat().st_mtime_ns
            report = isc_migrate.plan(project_dir=raw)
            self.assertEqual(len(report["moves"]), 1)
            self.assertEqual(report["conflicts"], [])
            self.assertTrue(source.is_file(), "plan must not move the file")
            self.assertEqual(source.stat().st_mtime_ns, before)

    def test_apply_moves_the_file_and_removes_the_empty_directory(self):
        with tempfile.TemporaryDirectory() as raw:
            source = write_policy(raw, isc_config.LEGACY_STATE_DIR, "legacy")
            report = isc_migrate.apply(project_dir=raw)
            destination = Path(raw) / isc_config.STATE_DIR / "config.json"
            self.assertTrue(destination.is_file())
            self.assertFalse(source.exists())
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8"))["_marker"],
                             "legacy")
            self.assertFalse((Path(raw) / isc_config.LEGACY_STATE_DIR).exists())
            self.assertIn("moved", [item.get("action") for item in report["performed"]])

    def test_a_differing_destination_is_a_conflict_and_nothing_moves(self):
        with tempfile.TemporaryDirectory() as raw:
            legacy = write_policy(raw, isc_config.LEGACY_STATE_DIR, "legacy")
            current = write_policy(raw, isc_config.STATE_DIR, "current")
            report = isc_migrate.apply(project_dir=raw)
            self.assertEqual(len(report["conflicts"]), 1)
            self.assertEqual(report["performed"], [])
            # Both survive untouched: a half-migrated deployment is recoverable.
            self.assertEqual(json.loads(legacy.read_text(encoding="utf-8"))["_marker"], "legacy")
            self.assertEqual(json.loads(current.read_text(encoding="utf-8"))["_marker"], "current")

    def test_an_identical_duplicate_is_removed_not_reported_as_a_conflict(self):
        with tempfile.TemporaryDirectory() as raw:
            legacy = write_policy(raw, isc_config.LEGACY_STATE_DIR, "same")
            current = write_policy(raw, isc_config.STATE_DIR, "same")
            report = isc_migrate.apply(project_dir=raw)
            self.assertEqual(report["conflicts"], [])
            self.assertFalse(legacy.exists())
            self.assertTrue(current.is_file())

    def test_unrecognised_files_are_carried_and_labelled(self):
        """Someone else's file in that directory must not be silently dropped."""
        with tempfile.TemporaryDirectory() as raw:
            extra = Path(raw) / isc_config.LEGACY_STATE_DIR / "notes.txt"
            extra.parent.mkdir(parents=True)
            extra.write_text("keep me", encoding="utf-8")
            report = isc_migrate.apply(project_dir=raw)
            moved = Path(raw) / isc_config.STATE_DIR / "notes.txt"
            self.assertTrue(moved.is_file())
            self.assertEqual(moved.read_text(encoding="utf-8"), "keep me")
            self.assertIn("unrecognised", report["moves"][0]["purpose"])

    def test_a_missing_legacy_directory_is_reported_not_an_error(self):
        with tempfile.TemporaryDirectory() as raw:
            report = isc_migrate.plan(project_dir=raw)
            self.assertEqual(report["moves"], [])
            self.assertEqual(len(report["absent"]), 1)

    def test_after_migrating_the_policy_resolves_from_the_new_location(self):
        """The end-to-end point: migrate, and discovery keeps working."""
        with tempfile.TemporaryDirectory() as raw:
            write_policy(raw, isc_config.LEGACY_STATE_DIR, "legacy")
            isc_migrate.apply(project_dir=raw)
            resolved = isc_config.project_config_path(raw, environ={})
            self.assertEqual(resolved.parent.name, isc_config.STATE_DIR)
            self.assertTrue(resolved.is_file())
            self.assertEqual(isc_config.find_project_root(raw), Path(raw).resolve())


if __name__ == "__main__":
    unittest.main()
