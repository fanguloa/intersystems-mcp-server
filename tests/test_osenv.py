"""Contract tests for the Windows OS-environment writer.

winreg and the ctypes broadcast call are always mocked: no test may touch the real
registry. Fixtures never assert on argument order, only on the fact that both calls
happened, so the tests describe intent instead of implementation shape.

Module-wide hermeticity guard: `setUpModule`/`tearDownModule` replace every winreg
call osenv.py can reach (`OpenKey`, `QueryValueEx`, `SetValueEx`, `DeleteValue`) with
a sentinel that raises `AssertionError` if actually invoked. Individual tests that
need specific winreg behavior layer their own `patch(...)` on top (an inner `with`
patch always wins over this outer guard for the duration of its block, and the guard
is restored the moment that block exits) — so this guard only ever fires when a test
forgot to mock a call path that reaches winreg, which is exactly the bug this guard
exists to catch.
"""

import pathlib
import subprocess
import sys
import unittest
from unittest.mock import patch

try:
    import winreg
except ImportError:  # pragma: no cover - exercised only off Windows
    winreg = None

from isc_ai import osenv
from isc_ai import config as isc_config
from isc_ai.setup import SetupError

REPO = pathlib.Path(__file__).resolve().parent.parent


def _real_winreg_call_attempted(*_args, **_kwargs):
    raise AssertionError(
        "a test attempted a real winreg call; mock isc_ai.osenv.winreg.* "
        "explicitly for this test instead of relying on the real registry"
    )


_GUARDED_WINREG_CALLS = ("OpenKey", "QueryValueEx", "SetValueEx", "DeleteValue")
_guard_patches = []


def setUpModule():
    if winreg is None:
        raise unittest.SkipTest("Windows registry tests require winreg (not available here)")
    for name in _GUARDED_WINREG_CALLS:
        p = patch(f"isc_ai.osenv.winreg.{name}", side_effect=_real_winreg_call_attempted)
        p.start()
        _guard_patches.append(p)


def tearDownModule():
    for p in _guard_patches:
        p.stop()
    _guard_patches.clear()


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
             patch("isc_ai.osenv.winreg.QueryValueEx", side_effect=FileNotFoundError), \
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
        with patch("isc_ai.osenv.get", return_value=None), \
             patch("isc_ai.osenv.winreg.SetValueEx") as set_value, \
             patch("isc_ai.osenv.ctypes.windll.user32.SendMessageTimeoutW") as broadcast:
            osenv.plan({"JIRA_API_TOKEN": "value"})
        set_value.assert_not_called()
        broadcast.assert_not_called()


class RegistryValueTypeTests(unittest.TestCase):
    """`_set_registry_value` must preserve the existing REG_* type, not force REG_SZ."""

    def test_overwriting_an_existing_reg_expand_sz_value_keeps_its_type(self):
        with patch("isc_ai.osenv.winreg.OpenKey"), \
             patch("isc_ai.osenv.winreg.QueryValueEx", return_value=("old", winreg.REG_EXPAND_SZ)), \
             patch("isc_ai.osenv.winreg.SetValueEx") as set_value:
            osenv._set_registry_value("PATH_LIKE", "%SystemRoot%\\value")
        args = set_value.call_args[0]
        self.assertEqual(args[3], winreg.REG_EXPAND_SZ)
        self.assertEqual(args[4], "%SystemRoot%\\value")

    def test_writing_a_brand_new_value_defaults_to_reg_sz(self):
        with patch("isc_ai.osenv.winreg.OpenKey"), \
             patch("isc_ai.osenv.winreg.QueryValueEx", side_effect=FileNotFoundError), \
             patch("isc_ai.osenv.winreg.SetValueEx") as set_value:
            osenv._set_registry_value("BRAND_NEW", "value")
        args = set_value.call_args[0]
        self.assertEqual(args[3], winreg.REG_SZ)
        self.assertEqual(args[4], "value")


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


class ModuleImportPortabilityTests(unittest.TestCase):
    """This module does an unconditional `import winreg` at module scope. On a platform
    where winreg does not exist, discovery must skip the module cleanly instead of
    crashing the whole `unittest discover` run with a ModuleNotFoundError.

    Simulated here (still on Windows) by forcing `import winreg` to fail in a fresh
    subprocess via `sys.modules["winreg"] = None` before discovery runs -- this is the
    documented way to make `import winreg` raise ImportError without needing a real
    non-Windows interpreter.
    """

    def test_discovery_skips_cleanly_when_winreg_is_unavailable(self):
        probe = (
            "import sys\n"
            "sys.modules['winreg'] = None\n"
            "import unittest\n"
            "suite = unittest.TestLoader().discover('tests', pattern='test_osenv.py')\n"
            "result = unittest.TextTestRunner(verbosity=0).run(suite)\n"
            "sys.exit(0 if not result.errors and not result.failures else 1)\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe], cwd=str(REPO), capture_output=True, text=True,
        )
        self.assertEqual(
            completed.returncode, 0,
            f"module import must not error when winreg is unavailable; "
            f"stderr:\n{completed.stderr}",
        )
        self.assertNotIn("ModuleNotFoundError", completed.stderr)


if __name__ == "__main__":
    unittest.main()
