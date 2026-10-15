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

    def test_extensions_are_probed_before_anything_is_installed(self):
        # An auth failure must stop before uv changes the installation.
        probing_call_index = self.text.index("\nreport_extensions\n")
        core_install_index = self.text.index('uv tool install "$@"')
        self.assertLess(probing_call_index, core_install_index)
        self.assertLess(core_install_index, self.text.index('setup --apply', core_install_index))

    def test_an_extension_rides_on_the_core_source_never_on_a_bare_index_name(self):
        # 'isc-ai' is not published on the public index: a bare name would resolve there.
        self.assertNotRegex(self.text, r"uv tool install --with \S+ isc-ai")
        self.assertIn('set -- "$@" --with "$extension"', self.text)


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

    def test_extensions_are_probed_before_anything_is_installed(self):
        probing_call_index = self.text.index("$With = Report-Extensions")
        core_install_index = self.text.index("& $Uv.Source @InstallArgs")
        self.assertLess(probing_call_index, core_install_index)
        self.assertLess(core_install_index, self.text.index("setup --apply", core_install_index))

    def test_an_extension_rides_on_the_core_source_never_on_a_bare_index_name(self):
        self.assertNotRegex(self.text, r"tool install --with \S+ isc-ai")
        self.assertIn('$InstallArgs = @("tool", "install") + $Source + $With', self.text)

    def test_probing_logic_mirrors_install_sh_not_just_flag_parity(self):
        sh_text = (ROOT / "install.sh").read_text(encoding="utf-8")
        for marker in ("not-found", "forbidden", "auth-failed", "no-ref", "Skipping extension"):
            self.assertIn(marker, sh_text)
            self.assertIn(marker, self.text)

    def test_a_reachable_extension_without_the_ref_is_skipped_not_installed(self):
        # An empty repository answers ls-remote with exit 0 and no refs; '--with' on it
        # would fail the whole uv call and take the core install down with it.
        self.assertIn("git ls-remote $Url $Ref", self.text)
        sh_text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('git ls-remote "$1" "$2"', sh_text)


class ExtensionRefPinningTests(unittest.TestCase):
    """Fix round: `@main` in the actual install command was a hardcoded ref, not a
    placeholder. Neither script has published a first tag yet (MANUAL-2), so the
    literal `main` fallback must live in one grep-able, commented variable instead
    of being baked directly into the install command string."""

    def test_install_sh_never_hardcodes_a_bare_main_ref_in_the_install_command(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertNotIn("@main", text)
        self.assertIn("MANUAL-2", text)
        self.assertIn("ISC_AI_TECHNICAL_REF", text)

    def test_install_ps1_never_hardcodes_a_bare_main_ref_in_the_install_command(self):
        text = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertNotIn("@main", text)
        self.assertIn("MANUAL-2", text)
        self.assertIn("ISC_AI_TECHNICAL_REF", text)


class GitTerminalPromptCleanupTests(unittest.TestCase):
    """Fix round: GIT_TERMINAL_PROMPT cleanup must run even if the git call itself
    throws a terminating error (install.ps1 sets $ErrorActionPreference = "Stop"),
    or the env var leaks into every subsequent command in the same process."""

    def test_install_ps1_wraps_the_probe_git_call_in_try_finally(self):
        text = (ROOT / "install.ps1").read_text(encoding="utf-8")
        start = text.index("function Probe-Extension")
        end = text.index("function Report-Extensions")
        body = text[start:end]
        try_index = body.index("try {")
        git_index = body.index("git ls-remote")
        finally_index = body.index("finally {")
        remove_index = body.index("Remove-Item Env:\\GIT_TERMINAL_PROMPT")
        self.assertLess(try_index, git_index)
        self.assertLess(git_index, finally_index)
        self.assertLess(finally_index, remove_index)


class AccessAmbiguityMessageTests(unittest.TestCase):
    """Fix round: GitLab's git wire protocol returns the same 404-shaped response
    for a repository that does not exist and one that exists but is forbidden (it
    deliberately does not leak which). The 'not-found' outcome's message must say
    so instead of asserting the repo definitely does not exist."""

    def test_install_sh_not_found_message_admits_the_ambiguity(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("not found or no access", text)

    def test_install_ps1_not_found_message_admits_the_ambiguity(self):
        text = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("not found or no access", text)


if __name__ == "__main__":
    unittest.main()
