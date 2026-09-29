"""Contract tests for the interactive configuration of the agentic environment.

The invariants that matter for a wizard, and none of them are about the questions themselves:

- it refuses to run without a terminal, instead of hanging on a pipe or silently accepting
  defaults in CI
- the interview writes nothing; only apply() writes
- declining a step really declines it, and a declined step contributes no action
- a credential is never echoed, never logged, and never serialised into the plan
- the plan runs in dependency order, not in the order the questions were asked
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isc_ai import wizard
from isc_ai import i18n
from isc_ai.i18n import t
from isc_ai.config import STATE_DIR


class ScriptedConsole(wizard.Console):
    """Answers from queues keyed by question type.

    A flat list breaks the moment a step decides not to ask, which is exactly what a good
    wizard does, so the queues are separate and exhaustion is an error rather than a default.
    """

    def __init__(self, yes_no=(), text=(), secrets=(), tty=True):
        self.queue_yes_no = list(yes_no)
        self.queue_text = list(text)
        self.queue_secret = list(secrets)
        self.printed = []
        self.asked = []
        super().__init__(input_stream=io.StringIO(), output_stream=io.StringIO(),
                         assume_tty=tty)

    def say(self, text=""):
        self.printed.append(text)

    def ask_yes_no(self, question, default=True):
        self.require_tty()
        if not self.queue_yes_no:
            raise AssertionError(f"unscripted yes/no question: {question}")
        self.asked.append(("yes_no", question))
        return self.queue_yes_no.pop(0)

    def ask_text(self, question, default=None):
        self.require_tty()
        if not self.queue_text:
            raise AssertionError(f"unscripted text question: {question}")
        self.asked.append(("text", question))
        return self.queue_text.pop(0)

    def ask_secret(self, name):
        self.require_tty()
        if not self.queue_secret:
            raise AssertionError(f"unscripted secret question: {name}")
        self.asked.append(("secret", name))
        return self.queue_secret.pop(0)

    @property
    def output(self):
        return "\n".join(self.printed)


AUDIT_REPORT = {
    "clients": {
        "opencode": {"installed": True},
        "claude": {"installed": True},
        "vscode": {"installed": False},
    },
    "harness": {"files": 47, "bundle_version": 4, "actions": {"noop": 47}},
    "summary": {"errors": 0, "warnings": 2, "info": 0},
}

EMPTY_SUITE_PLAN = ({"mode": "check", "changes": [], "files": [], "findings": []}, [])
EMPTY_DOCS_PLAN = ({"mode": "check", "blocks": [{"block": "topology", "action": "noop"}]}, [])
EMPTY_AGENTS_PLAN = ({"mode": "check", "roles": [{"role": "r"}], "files": [
    {"path": "x", "action": "noop"}], "findings": []}, [])


def quiet_environment(project_dir):
    """Every plan reports 'nothing to do', so only the questions under test are asked."""
    return [
        patch.object(wizard.audit_module, "run_audit", return_value=AUDIT_REPORT),
        patch.object(wizard.suite_module, "plan", return_value=EMPTY_SUITE_PLAN),
        patch.object(wizard.docs_module, "plan", return_value=EMPTY_DOCS_PLAN),
        patch.object(wizard.agents_module, "plan", return_value=EMPTY_AGENTS_PLAN),
        patch.object(wizard, "_declared_secrets", return_value=[]),
        patch.object(wizard.config_module, "project_config_path",
                     return_value=str(Path(project_dir) / STATE_DIR / "config.json")),
    ]


class TerminalRequirementTests(unittest.TestCase):
    """A prompt read from a pipe is a hang; a default accepted in CI is worse."""

    def test_interview_refuses_without_a_terminal(self):
        console = ScriptedConsole(tty=False)
        with self.assertRaises(wizard.WizardAborted) as caught:
            wizard.interview(console, project_dir=".")
        self.assertIn("terminal", str(caught.exception).lower())

    def test_refusal_names_the_scriptable_alternative(self):
        """Refusing is only helpful if it says what to use instead."""
        console = ScriptedConsole(tty=False)
        with self.assertRaises(wizard.WizardAborted) as caught:
            console.ask_yes_no("anything?")
        message = str(caught.exception)
        for command in ("setup", "secrets", "suite"):
            self.assertIn(command, message)

    def test_closed_input_aborts_instead_of_defaulting(self):
        console = wizard.Console(input_stream=io.StringIO(""), output_stream=io.StringIO(),
                                 assume_tty=True)
        with self.assertRaises(wizard.WizardAborted):
            console.ask_yes_no("proceed?", default=True)


class InterviewWritesNothingTests(unittest.TestCase):
    """The plan is shown before anything happens, so the interview must be inert."""

    def test_interview_touches_no_file(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            (project / "existing.txt").write_text("before", encoding="utf-8")
            before = {p: p.stat().st_mtime_ns for p in project.rglob("*") if p.is_file()}

            console = ScriptedConsole(yes_no=[False])
            patches = quiet_environment(project)
            for item in patches:
                item.start()
            try:
                wizard.interview(console, project_dir=project)
            finally:
                for item in patches:
                    item.stop()

            after = {p: p.stat().st_mtime_ns for p in project.rglob("*") if p.is_file()}
            self.assertEqual(before, after, "the interview must not write")
            self.assertEqual((project / "existing.txt").read_text(encoding="utf-8"), "before")

    def test_declining_everything_yields_an_empty_plan(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            console = ScriptedConsole(yes_no=[False])
            patches = quiet_environment(project)
            for item in patches:
                item.start()
            try:
                session = wizard.interview(console, project_dir=project)
            finally:
                for item in patches:
                    item.stop()

            self.assertEqual(session.planned_actions, [])
            rendered = "\n".join(wizard.render_plan(session))
            self.assertIn("nothing to do", rendered.lower())

    def test_declined_steps_are_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            console = ScriptedConsole(yes_no=[False])
            patches = quiet_environment(project)
            for item in patches:
                item.start()
            try:
                session = wizard.interview(console, project_dir=project)
            finally:
                for item in patches:
                    item.stop()
            self.assertIn(wizard.STEP_IRIS, session.declined)
            self.assertIn("iris", "\n".join(wizard.render_plan(session)))


class CredentialHandlingTests(unittest.TestCase):
    """The wizard asks for credentials, which makes it the likeliest place to leak one."""

    SECRET = "s3cr3t-value-nobody-should-see"

    def _run(self, project):
        console = ScriptedConsole(yes_no=[False, True], secrets=[self.SECRET])
        patches = quiet_environment(project)
        patches[4] = patch.object(
            wizard, "_declared_secrets",
            return_value=[("DEMO_TOKEN", "demo-server", True)],
        )
        for item in patches:
            item.start()
        try:
            session = wizard.interview(console, project_dir=project)
        finally:
            for item in patches:
                item.stop()
        return console, session

    def test_value_never_reaches_the_output(self):
        with tempfile.TemporaryDirectory() as raw:
            console, session = self._run(Path(raw))
            self.assertNotIn(self.SECRET, console.output)

    def test_value_never_reaches_the_rendered_plan(self):
        with tempfile.TemporaryDirectory() as raw:
            console, session = self._run(Path(raw))
            rendered = "\n".join(wizard.render_plan(session))
            self.assertNotIn(self.SECRET, rendered)
            # The key name is useful; the value is not.
            self.assertIn("DEMO_TOKEN", rendered)

    def test_value_is_carried_to_apply_and_stored_once(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            console, session = self._run(project)
            recorded = []
            with patch.object(wizard.secrets_module, "set_secret",
                              side_effect=lambda *a, **k: recorded.append((a, k))):
                wizard.apply(session, home=project / "home")
            self.assertEqual(len(recorded), 1)
            args, kwargs = recorded[0]
            self.assertEqual(args[0], "DEMO_TOKEN")
            self.assertEqual(args[1], self.SECRET)
            self.assertEqual(kwargs.get("scope"), "global")

    def test_an_empty_answer_stores_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            console = ScriptedConsole(yes_no=[False, True], secrets=[""])
            patches = quiet_environment(project)
            patches[4] = patch.object(
                wizard, "_declared_secrets",
                return_value=[("DEMO_TOKEN", "demo-server", True)],
            )
            for item in patches:
                item.start()
            try:
                session = wizard.interview(console, project_dir=project)
            finally:
                for item in patches:
                    item.stop()
            self.assertFalse(session.chose(wizard.STEP_SECRETS))
            self.assertEqual(session.planned_actions, [])


class IrisPolicyStepTests(unittest.TestCase):
    """IRIS is one optional module of the environment, not its centre."""

    def test_step_is_skipped_when_the_deployment_says_no(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            console = ScriptedConsole(yes_no=[False])
            patches = quiet_environment(project)
            for item in patches:
                item.start()
            try:
                session = wizard.interview(console, project_dir=project)
            finally:
                for item in patches:
                    item.stop()
            self.assertFalse(session.chose(wizard.STEP_IRIS))
            # Asking about IRIS must not have asked for a host or a namespace.
            self.assertEqual([kind for kind, _ in console.asked if kind == "text"], [])

    def test_written_policy_carries_the_answers_and_no_example_data(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            console = ScriptedConsole(
                yes_no=[True],
                text=["demo", "DEMO", "base.demo.invalid", "BASE-DEMO", "Custom.Demo"],
            )
            patches = quiet_environment(project)
            for item in patches:
                item.start()
            try:
                session = wizard.interview(console, project_dir=project)
                wizard.apply(session, home=project / "home")
            finally:
                for item in patches:
                    item.stop()

            written = json.loads(
                (project / STATE_DIR / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(written["site"]["name"], "demo")
            self.assertEqual(written["site"]["ccr_id"]["prefix"], "DEMO")
            self.assertEqual(written["canonical_base"]["host"], "base.demo.invalid")
            self.assertEqual(written["canonical_base"]["namespace"], "BASE-DEMO")
            self.assertEqual(written["packages"], {"BASE-DEMO": [{"prefix": "Custom.Demo"}]})
            # Connections carry credentials: they belong to the user file, never here.
            self.assertNotIn("servers", written)
            # Example routes belong to the template, not to a real deployment.
            self.assertNotIn("routes", written)

    def test_the_template_example_host_never_survives(self):
        """Guards the rule that the wizard configures, it does not leave placeholders."""
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            console = ScriptedConsole(
                yes_no=[True],
                text=["demo", "DEMO", "real.host.invalid", "BASE-DEMO", "Custom.Demo"],
            )
            patches = quiet_environment(project)
            for item in patches:
                item.start()
            try:
                session = wizard.interview(console, project_dir=project)
                wizard.apply(session, home=project / "home")
            finally:
                for item in patches:
                    item.stop()
            raw_text = (project / STATE_DIR / "config.json").read_text(encoding="utf-8")
            self.assertNotIn("base.example.invalid", raw_text)
            self.assertNotIn("my-site", raw_text)


class StepSelectionTests(unittest.TestCase):

    def test_steps_are_asked_in_dependency_order_not_declaration_order(self):
        """Credentials before the servers that reference them; policy before documents."""
        order = list(wizard.STEP_ORDER)
        self.assertLess(order.index(wizard.STEP_SECRETS), order.index(wizard.STEP_SERVERS))
        self.assertLess(order.index(wizard.STEP_IRIS), order.index(wizard.STEP_DOCS))
        self.assertLess(order.index(wizard.STEP_HARNESS), order.index(wizard.STEP_PHASES))

    def test_only_runs_the_requested_step(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            console = ScriptedConsole(yes_no=[False])
            patches = quiet_environment(project)
            for item in patches:
                item.start()
            try:
                session = wizard.interview(console, project_dir=project,
                                           steps=[wizard.STEP_IRIS])
            finally:
                for item in patches:
                    item.stop()
            self.assertEqual([answer.step for answer in session.answers], [wizard.STEP_IRIS])

    def test_every_declared_step_has_a_handler(self):
        for step in wizard.STEP_ORDER:
            self.assertIn(step, wizard.STEP_HANDLERS, f"{step} has no handler")


class CancellingTests(unittest.TestCase):
    """ESC and Ctrl+C must cancel, and cancelling must write nothing."""

    def _console(self, text):
        return wizard.Console(input_stream=io.StringIO(text), output_stream=io.StringIO(),
                              assume_tty=True, allow_raw_keys=False)

    def test_escape_in_a_yes_no_answer_cancels(self):
        console = self._console("\x1b\n")
        with self.assertRaises(wizard.WizardAborted) as caught:
            console.ask_yes_no("proceed?")
        self.assertIn("cancel", str(caught.exception).lower())

    def test_escape_in_a_text_answer_cancels(self):
        console = self._console("\x1b\n")
        with self.assertRaises(wizard.WizardAborted):
            console.ask_text("site code")

    def test_escape_mixed_into_a_longer_answer_still_cancels(self):
        """A person who types, changes their mind and hits ESC must not have it ignored."""
        console = self._console("demo\x1b\n")
        with self.assertRaises(wizard.WizardAborted):
            console.ask_text("site code")

    def test_keyboard_interrupt_becomes_a_clean_cancel(self):
        class Interrupting(io.StringIO):
            def readline(self, *args, **kwargs):
                raise KeyboardInterrupt

        console = wizard.Console(input_stream=Interrupting(), output_stream=io.StringIO(),
                                 assume_tty=True, allow_raw_keys=False)
        with self.assertRaises(wizard.WizardAborted):
            console.ask_yes_no("proceed?")

    def test_cancelling_mid_interview_writes_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            before = {p: p.stat().st_mtime_ns for p in project.rglob("*") if p.is_file()}
            console = self._console("\x1b\n")
            patches = quiet_environment(project)
            for item in patches:
                item.start()
            try:
                with self.assertRaises(wizard.WizardAborted):
                    wizard.interview(console, project_dir=project)
            finally:
                for item in patches:
                    item.stop()
            after = {p: p.stat().st_mtime_ns for p in project.rglob("*") if p.is_file()}
            self.assertEqual(before, after)


class LanguageTests(unittest.TestCase):
    """The wizard prints through the catalog, so it follows --lang like everything else."""

    def setUp(self):
        self._previous = i18n.current_language()

    def tearDown(self):
        i18n.set_language(self._previous)

    def test_the_yes_no_suffix_follows_the_language(self):
        i18n.set_language("en")
        self.assertEqual(t("msg.wizard.yes_no_yes"), "[Y/n]")
        i18n.set_language("es")
        self.assertEqual(t("msg.wizard.yes_no_yes"), "[S/n]")

    def test_prompts_are_translated(self):
        i18n.set_language("es")
        console = ScriptedConsole(yes_no=[False])
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            patches = quiet_environment(project)
            for item in patches:
                item.start()
            try:
                wizard.interview(console, project_dir=project)
            finally:
                for item in patches:
                    item.stop()
        self.assertIn("Configurando el entorno", console.output)
        self.assertIn("Clientes de IA", console.output)

    def test_both_languages_words_are_accepted_either_way(self):
        """Typing 's' on an English prompt means yes, not a typo."""
        for language in ("en", "es"):
            i18n.set_language(language)
            for word, expected in (("y", True), ("s", True), ("si", True),
                                   ("n", False), ("no", False)):
                with self.subTest(language=language, word=word):
                    console = wizard.Console(
                        input_stream=io.StringIO(f"{word}\n"), output_stream=io.StringIO(),
                        assume_tty=True, allow_raw_keys=False)
                    self.assertIs(console.ask_yes_no("q", default=not expected), expected)

    def test_no_prompt_text_is_hardcoded_in_english_or_spanish(self):
        """The bug this replaces: a literal "[S/n]" shown regardless of --lang."""
        source = (Path(wizard.__file__)).read_text(encoding="utf-8")
        for literal in ('"[S/n]"', '"[s/N]"', '"[Y/n]"', '"[y/N]"'):
            self.assertNotIn(literal, source,
                             "the yes/no suffix must come from the catalog")


class CliSurfaceTests(unittest.TestCase):
    """The command list is the surface a person configures; `mcp` is not part of it."""

    def test_mcp_is_not_offered_in_the_command_list(self):
        from isc_ai.cli import main
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), self.assertRaises(SystemExit):
            main(["--help"])
        listed = buffer.getvalue()
        # It must not be advertised, and must not leak argparse's SUPPRESS sentinel either.
        self.assertNotIn("==SUPPRESS==", listed)
        self.assertNotRegex(listed, r"(?m)^\s+mcp\s{2,}\S")

    def test_wizard_is_offered_in_the_command_list(self):
        from isc_ai.cli import main
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), self.assertRaises(SystemExit):
            main(["--help"])
        self.assertRegex(buffer.getvalue(), r"(?m)^\s+wizard\s{2,}\S")

    def test_mcp_still_has_its_own_help(self):
        """Hidden from the list, still fully usable: the clients invoke it."""
        from isc_ai.cli import main
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), self.assertRaises(SystemExit):
            main(["mcp", "--help"])
        self.assertIn("stdio", buffer.getvalue())

    def test_unknown_step_is_rejected_before_anything_runs(self):
        from isc_ai.cli import main
        buffer = io.StringIO()
        with patch("sys.stdout", buffer), patch("sys.stderr", buffer):
            code = main(["wizard", "--only", "nonsense"])
        self.assertEqual(code, 1)
        self.assertIn("nonsense", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
