"""The CLI must never print a credential, including ones it was not told about.

Configuration, findings and plans all end up on a terminal, in a log, or in a screen
recording. Enumerating every possible key name is a losing game, so the shape of the name
decides, and the places a value can hide -- an argv, a URL, an unanticipated key -- are
covered one by one.
"""

import json
import unittest

from isc_ai import audit
from isc_ai import config as isc_config

SECRET = "ATATT3xFfGF0-not-a-real-token-99118822"


class SecretKeyTests(unittest.TestCase):
    def test_names_that_mean_credential_are_recognized(self):
        for name in ("password", "passwd", "pwd", "upload_token", "JIRA_API_TOKEN",
                     "CONTEXT7_API_KEY", "api-key", "clientSecret", "AUTH_HEADER",
                     "P4PASSWD", "credential"):
            with self.subTest(name=name):
                self.assertTrue(isc_config.is_secret_key(name))

    def test_ordinary_names_are_not_over_masked(self):
        # Over-masking makes a drift report useless for the thing it exists to show.
        for name in ("host", "port", "namespace", "env_role", "path_prefix", "P4PORT",
                     "P4CLIENT", "JIRA_URL", "JIRA_USERNAME", "command", "args", "url"):
            with self.subTest(name=name):
                self.assertFalse(isc_config.is_secret_key(name))


class RedactTests(unittest.TestCase):
    def test_a_key_the_schema_never_anticipated_is_still_masked(self):
        data = {"servers": {"one": {"host": "h.invalid", "GITHUB_TOKEN": SECRET,
                                   "password": SECRET, "namespace": "BASE-SITE"}}}
        masked = isc_config.redact(data)
        text = json.dumps(masked)
        self.assertNotIn(SECRET, text)
        self.assertIn("h.invalid", text, "the non-secret fields must survive")
        self.assertEqual(masked["servers"]["one"]["namespace"], "BASE-SITE")

    def test_a_reference_is_left_readable(self):
        # `{env:VAR}` is the shape we want people to use: masking it hides good news.
        data = {"password": "{env:IRIS_PASSWORD}"}
        self.assertEqual(isc_config.redact(data)["password"], "{env:IRIS_PASSWORD}")

    def test_a_command_line_inside_a_configuration_is_masked_too(self):
        data = {"command": ["server", "--api-key", SECRET, "--readonly"]}
        masked = isc_config.redact(data)["command"]
        self.assertNotIn(SECRET, json.dumps(masked))
        self.assertEqual(masked, ["server", "--api-key", "<redacted>", "--readonly"])


class ArgvMaskingTests(unittest.TestCase):
    def test_the_value_after_a_secret_flag_is_masked(self):
        self.assertEqual(isc_config.mask_argv(["run", "--token", SECRET, "--verbose"]),
                         ["run", "--token", "<redacted>", "--verbose"])

    def test_an_inline_assignment_is_masked(self):
        self.assertEqual(isc_config.mask_argv([f"--api-key={SECRET}", "--port=8080"]),
                         ["--api-key=<redacted>", "--port=8080"])

    def test_ordinary_arguments_are_untouched(self):
        argv = ["mcp-atlassian", "--readonly", "--port", "1666", "C:/tools/server.exe"]
        self.assertEqual(isc_config.mask_argv(argv), argv)

    def test_a_flag_that_only_looks_like_a_secret_does_not_swallow_the_next_value(self):
        # `--keyfile` names a path, not a secret; masking it would hide the drift.
        self.assertEqual(isc_config.mask_argv(["--port", "1666"]), ["--port", "1666"])


class UrlMaskingTests(unittest.TestCase):
    def test_userinfo_is_masked_and_the_host_survives(self):
        masked = isc_config.mask_url("https://user:hunter2@mcp.example.invalid/mcp")
        self.assertNotIn("hunter2", masked)
        self.assertIn("mcp.example.invalid/mcp", masked)

    def test_a_credential_in_the_query_string_is_masked(self):
        masked = isc_config.mask_url(f"https://mcp.example.invalid/mcp?api_key={SECRET}&mode=fast")
        self.assertNotIn(SECRET, masked)
        self.assertIn("mode=fast", masked, "the rest of the query must stay readable")

    def test_a_plain_url_is_untouched(self):
        url = "https://mcp.context7.com/mcp"
        self.assertEqual(isc_config.mask_url(url), url)


class AuditOutputTests(unittest.TestCase):
    """The end-to-end promise: a secret in a client configuration never reaches the report."""

    def entry(self, **kwargs):
        defaults = {"client": "vscode", "scope": "user", "path": audit.Path("x.json"),
                    "name": "alpha"}
        return audit.Entry(**{**defaults, **kwargs})

    def test_a_token_on_the_command_line_is_not_reprinted_by_the_drift_finding(self):
        spec = {"launch": {"command": "alpha-server", "args": ["--readonly"]}}
        entry = self.entry(command="alpha-server", args=("--api-key", SECRET))
        differences = audit._compare_launch(entry, spec)
        self.assertTrue(differences)
        self.assertNotIn(SECRET, " ".join(differences))
        self.assertIn("<redacted>", " ".join(differences))

    def test_a_token_in_a_url_is_not_reprinted_either(self):
        spec = {"launch": {"url": "https://mcp.example.invalid/mcp"}}
        entry = self.entry(transport="http", url=f"https://mcp.example.invalid/mcp?token={SECRET}")
        differences = audit._compare_launch(entry, spec)
        self.assertTrue(differences)
        self.assertNotIn(SECRET, " ".join(differences))


if __name__ == "__main__":
    unittest.main()
