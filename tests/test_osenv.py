"""Contract tests for the Windows OS-environment writer.

winreg and the ctypes broadcast call are always mocked: no test may touch the real
registry. Fixtures never assert on argument order, only on the fact that both calls
happened, so the tests describe intent instead of implementation shape.
"""

import unittest
from unittest.mock import patch

from isc_ai import osenv
from isc_ai import config as isc_config
from isc_ai.setup import SetupError


class SupportTests(unittest.TestCase):
    def test_supported_only_on_windows(self):
        self.assertTrue(osenv.is_supported(platform_name="win32"))
        self.assertFalse(osenv.is_supported(platform_name="linux"))
        self.assertFalse(osenv.is_supported(platform_name="darwin"))


class DiscoveryKeyGuardTests(unittest.TestCase):
    def test_plan_refuses_a_project_discovery_key(self):
        with self.assertRaises(SetupError) as ctx:
            osenv.plan({isc_config.PROJECT_DIR_ENV: "C:\\somewhere"})
        self.assertIn(isc_config.PROJECT_DIR_ENV, str(ctx.exception))

    def test_apply_refuses_a_project_config_key_before_writing_anything(self):
        with patch("isc_ai.osenv.winreg.SetValueEx") as set_value:
            with self.assertRaises(SetupError):
                osenv.apply({isc_config.PROJECT_CONFIG_ENV: "C:\\somewhere\\config.json"})
            set_value.assert_not_called()

    def test_unset_refuses_a_stale_legacy_key(self):
        from isc_ai.setup import STALE_ENV_KEYS
        with self.assertRaises(SetupError):
            osenv.unset(list(STALE_ENV_KEYS))

    def test_guard_names_the_discovery_mechanism_it_would_break(self):
        with self.assertRaises(SetupError) as ctx:
            osenv.plan({isc_config.EXTRA_PACKAGES_ENV: "1"})
        message = str(ctx.exception).lower()
        self.assertIn("discovery", message)


class ApplyWritesTheRegistryTests(unittest.TestCase):
    def test_apply_calls_set_value_and_broadcasts_for_an_allowed_key(self):
        with patch("isc_ai.osenv.winreg.OpenKey"), \
             patch("isc_ai.osenv.winreg.SetValueEx") as set_value, \
             patch("isc_ai.osenv.is_supported", return_value=True), \
             patch("isc_ai.osenv.ctypes.windll.user32.SendMessageTimeoutW") as broadcast:
            report = osenv.apply({"JIRA_API_TOKEN": "super-secret-value"})
        set_value.assert_called_once()
        broadcast.assert_called_once()
        self.assertEqual(report["sets"], [{"name": "JIRA_API_TOKEN", "action": "set"}])

    def test_apply_never_broadcasts_when_there_is_nothing_to_set(self):
        with patch("isc_ai.osenv.is_supported", return_value=True), \
             patch("isc_ai.osenv.ctypes.windll.user32.SendMessageTimeoutW") as broadcast:
            osenv.apply({})
        broadcast.assert_not_called()

    def test_apply_on_an_unsupported_platform_raises_instead_of_silently_skipping(self):
        with patch("isc_ai.osenv.is_supported", return_value=False):
            with self.assertRaises(SetupError):
                osenv.apply({"JIRA_API_TOKEN": "value"})


class PlanNeverLeaksAValueTests(unittest.TestCase):
    def test_plan_reports_only_the_key_name_and_action_never_the_value(self):
        with patch("isc_ai.osenv.get", return_value=None):
            report = osenv.plan({"JIRA_API_TOKEN": "a-real-secret-value"})
        self.assertEqual(report["sets"], [{"name": "JIRA_API_TOKEN", "action": "would set"}])
        self.assertNotIn("a-real-secret-value", str(report))

    def test_plan_reports_unchanged_when_the_current_value_already_matches(self):
        with patch("isc_ai.osenv.get", return_value="same-value"):
            report = osenv.plan({"JIRA_API_TOKEN": "same-value"})
        self.assertEqual(report["sets"], [{"name": "JIRA_API_TOKEN", "action": "unchanged"}])

    def test_plan_never_writes_anything(self):
        with patch("isc_ai.osenv.winreg.SetValueEx") as set_value, \
             patch("isc_ai.osenv.ctypes.windll.user32.SendMessageTimeoutW") as broadcast:
            osenv.plan({"JIRA_API_TOKEN": "value"})
        set_value.assert_not_called()
        broadcast.assert_not_called()


class UnsetTests(unittest.TestCase):
    def test_unset_deletes_a_present_value_and_broadcasts(self):
        with patch("isc_ai.osenv.winreg.OpenKey"), \
             patch("isc_ai.osenv.winreg.DeleteValue") as delete_value, \
             patch("isc_ai.osenv.is_supported", return_value=True), \
             patch("isc_ai.osenv.ctypes.windll.user32.SendMessageTimeoutW") as broadcast:
            report = osenv.unset(["JIRA_API_TOKEN"])
        delete_value.assert_called_once()
        broadcast.assert_called_once()
        self.assertEqual(report["sets"], [{"name": "JIRA_API_TOKEN", "action": "unset"}])

    def test_unset_tolerates_a_name_that_is_already_absent(self):
        with patch("isc_ai.osenv.winreg.OpenKey"), \
             patch("isc_ai.osenv.winreg.DeleteValue", side_effect=FileNotFoundError), \
             patch("isc_ai.osenv.is_supported", return_value=True), \
             patch("isc_ai.osenv.ctypes.windll.user32.SendMessageTimeoutW"):
            report = osenv.unset(["NEVER_SET"])
        self.assertEqual(report["sets"], [{"name": "NEVER_SET", "action": "already unset"}])


class ProjectDiscoveryRegressionTests(unittest.TestCase):
    """Mandatory per the proposal: this module must never perturb discovery."""

    def test_importing_and_using_osenv_does_not_change_project_directory_resolution(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            start = Path(tmp)
            before = isc_config.project_directory(environ={}, start=start)
            before_root = isc_config.find_project_root(start)

            with patch("isc_ai.osenv.winreg.OpenKey"), \
                 patch("isc_ai.osenv.winreg.QueryValueEx", side_effect=FileNotFoundError), \
                 patch("isc_ai.osenv.winreg.SetValueEx"), \
                 patch("isc_ai.osenv.is_supported", return_value=True), \
                 patch("isc_ai.osenv.ctypes.windll.user32.SendMessageTimeoutW"):
                osenv.plan({"SOME_ALLOWED_KEY": "value"})
                osenv.apply({"SOME_ALLOWED_KEY": "value"})

            after = isc_config.project_directory(environ={}, start=start)
            after_root = isc_config.find_project_root(start)

        self.assertEqual(before, after)
        self.assertEqual(before_root, after_root)


if __name__ == "__main__":
    unittest.main()
