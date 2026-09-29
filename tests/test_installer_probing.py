"""Contract tests for the bootstrap installers' extension pre-flight probing.

install.sh and install.ps1 are outside the Python hermetic-unittest suite's reach
for their actual runtime behavior (a real `git ls-remote` against a real GitLab
endpoint cannot run inside this test process without a network call). What CAN be
asserted hermetically, and what these tests assert, is the SCRIPT CONTENT and its
ordering: the probing logic exists, is baked-in (no runtime URL discovery), reports
the three documented outcomes distinctly, and never fails the whole install for an
unreachable extension. This mirrors the existing convention in test_setup.py
(`test_install_sh_documents_flags_and_rejects_unknown_arguments_before_uv`).

The actual probe-against-a-real-repository behavior (readable vs 403/404 vs an
auth failure) is verified by a documented MANUAL check, not by these tests --
see the apply-progress note for WU-S12's manual verification log.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallShProbingTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "install.sh").read_text(encoding="utf-8")

    def test_probes_with_git_ls_remote_using_the_installers_own_credentials(self):
        self.assertIn("git ls-remote", self.text)
        self.assertNotIn("--token", self.text)
        self.assertNotIn("GITLAB_TOKEN", self.text)

    def test_extension_repo_urls_are_baked_in_not_discovered_at_runtime(self):
        self.assertIn("gitlab.iscinternal.com/isc-ai-squad/isc-ai-technical.git", self.text)

    def test_reports_not_found_forbidden_and_auth_failed_as_distinct_outcomes(self):
        for marker in ("not-found", "forbidden", "auth-failed"):
            self.assertIn(marker, self.text)

    def test_an_unreachable_extension_is_skipped_and_reported_not_a_hard_failure(self):
        self.assertIn("Skipping extension", self.text)

    def test_an_auth_failure_stops_with_a_login_instruction(self):
        self.assertIn("log in to GitLab", self.text)
        # The stop must be a real script exit, not just a printed line.
        auth_failed_index = self.text.index('"auth-failed")')
        exit_after = self.text.index("exit 1", auth_failed_index)
        self.assertGreater(exit_after, auth_failed_index)

    def test_extension_probing_runs_after_core_install_in_the_apply_path(self):
        core_install_index = self.text.index('isc-ai setup --apply')
        probing_call_index = self.text.rindex("report_extensions")
        self.assertGreater(probing_call_index, core_install_index)


class InstallPs1ProbingParityTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "install.ps1").read_text(encoding="utf-8")

    def test_probes_with_git_ls_remote_using_the_installers_own_credentials(self):
        self.assertIn("git ls-remote", self.text)
        self.assertNotIn("--token", self.text)
        self.assertNotIn("GITLAB_TOKEN", self.text)

    def test_extension_repo_urls_are_baked_in_not_discovered_at_runtime(self):
        self.assertIn("gitlab.iscinternal.com/isc-ai-squad/isc-ai-technical.git", self.text)

    def test_reports_not_found_forbidden_and_auth_failed_as_distinct_outcomes(self):
        for marker in ("not-found", "forbidden", "auth-failed"):
            self.assertIn(marker, self.text)

    def test_an_unreachable_extension_is_skipped_and_reported_not_a_hard_failure(self):
        self.assertIn("Skipping extension", self.text)

    def test_an_auth_failure_stops_with_a_login_instruction(self):
        self.assertIn("log in to GitLab", self.text)
        auth_failed_index = self.text.index('"auth-failed"')
        exit_after = self.text.index("exit 1", auth_failed_index)
        self.assertGreater(exit_after, auth_failed_index)

    def test_extension_probing_runs_after_core_install_in_the_apply_path(self):
        core_install_index = self.text.index('isc-ai setup --apply')
        probing_call_index = self.text.rindex("Report-Extensions")
        self.assertGreater(probing_call_index, core_install_index)

    def test_probing_logic_mirrors_install_sh_not_just_flag_parity(self):
        sh_text = (ROOT / "install.sh").read_text(encoding="utf-8")
        for marker in ("not-found", "forbidden", "auth-failed", "Skipping extension"):
            self.assertIn(marker, sh_text)
            self.assertIn(marker, self.text)


if __name__ == "__main__":
    unittest.main()
