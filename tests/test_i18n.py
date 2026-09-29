"""Contract tests for the CLI language layer.

Two things are being protected here:
  * the catalogs cannot drift apart (keys, placeholders, dead entries)
  * translating the terminal must never translate a machine contract
"""

import ast
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from string import Formatter
from unittest.mock import patch

from isc_ai import i18n
from isc_ai.cli import main
from isc_ai.locales import en as en_catalog
from isc_ai.locales import es as es_catalog

_PACKAGE = Path(__file__).resolve().parent.parent / "isc_ai"

# Modules that render CLI-facing text. cli.py owns the argparse surface; wizard.py owns the
# interactive surface. A key used by neither is catalog rot; add a module here only when it
# genuinely prints to a person.
TEXT_MODULES = ("cli.py", "wizard.py")
CLI_SOURCE = "\n".join(
    (_PACKAGE / name).read_text(encoding="utf-8") for name in TEXT_MODULES
)

# The only values these modules ever interpolate. Anything else in a catalog entry is a
# literal brace that was not doubled -- typically a {env:VAR} reference, which
# str.format would silently eat as a field named "env".
#
# Note: "key" is deliberately absent. i18n.t()'s first parameter is named `key`, so a
# catalog entry using {key} raises TypeError at the call site. Those placeholders are
# named {name} instead.
ALLOWED_PARAMETERS = frozenset({
    "ancestor", "command", "count", "detail", "directory", "errors", "files", "language",
    "legacy", "name", "names", "path", "paths", "project_dir", "reason", "server",
    "server_id", "summary", "supported", "target", "valid", "value", "values", "version",
    "warnings",
})


def placeholders(template):
    return {name for _, name, _, _ in Formatter().parse(template) if name}


class LanguageResetTestCase(unittest.TestCase):
    """Language lives in module state, so every test restores it."""

    def setUp(self):
        super().setUp()
        previous = i18n.current()
        self.addCleanup(i18n.set_language, previous)
        i18n.set_language(i18n.LanguageChoice())


class CatalogIntegrityTests(LanguageResetTestCase):
    def test_every_language_declares_exactly_the_same_keys(self):
        english, spanish = set(en_catalog.MESSAGES), set(es_catalog.MESSAGES)
        self.assertEqual(english - spanish, set(), "keys missing from the Spanish catalog")
        self.assertEqual(spanish - english, set(), "keys missing from the English catalog")
        self.assertEqual(set(i18n.CATALOGS), set(i18n.LANGUAGES))

    def test_translations_keep_the_same_placeholders(self):
        for key, english in en_catalog.MESSAGES.items():
            with self.subTest(key=key):
                self.assertEqual(
                    placeholders(english), placeholders(es_catalog.MESSAGES[key]),
                    "a translation must interpolate exactly the same values as the original",
                )

    def test_every_entry_survives_formatting(self):
        for language, messages in i18n.CATALOGS.items():
            for key, template in messages.items():
                with self.subTest(language=language, key=key):
                    names = placeholders(template)
                    unknown = names - ALLOWED_PARAMETERS
                    self.assertEqual(
                        unknown, set(),
                        f"{language}/{key} interpolates {sorted(unknown)}: double those braces",
                    )
                    try:
                        template.format(**{name: "x" for name in names})
                    except (KeyError, IndexError, ValueError) as exc:
                        self.fail(f"{language}/{key} is not formattable: {exc!r}")

    def test_no_catalog_key_is_dead(self):
        # Keys are literals in the text modules, so an unused one is catalog rot.
        for key in en_catalog.MESSAGES:
            with self.subTest(key=key):
                self.assertTrue(f'"{key}"' in CLI_SOURCE or f"'{key}'" in CLI_SOURCE,
                                f"{key} is never used by {' or '.join(TEXT_MODULES)}")

    def test_no_catalog_entry_interpolates_key(self):
        """{key} collides with i18n.t()'s own first parameter and raises TypeError.

        Caught the hard way while adding the wizard: the failure happens at the call site,
        not in formatting, so a catalog review will not spot it.
        """
        for language, messages in i18n.CATALOGS.items():
            for name, template in messages.items():
                with self.subTest(language=language, key=name):
                    self.assertNotIn(
                        "key", placeholders(template),
                        f"{language}/{name} interpolates {{key}}: rename it to {{name}}",
                    )

    def test_spanish_catalog_is_written_in_accented_spanish(self):
        # Guards against dropping diacritics "to be safe with encodings": the CLI
        # already forces UTF-8 on Windows stdio.
        for key in ("app.description", "cmd.config.help", "error.command_failed", "msg.doctor.no_config"):
            with self.subTest(key=key):
                self.assertTrue(any(char in es_catalog.MESSAGES[key] for char in "áéíóúñ¿"),
                                "Spanish text must carry its diacritics")

    def test_no_user_facing_string_is_hardcoded_in_the_cli(self):
        """The mirror of the dead-key test.

        Dead keys are caught by reading the catalog; this reads the code, so a message
        written straight into a call would be found instead of shipping as a string
        that no language can ever translate.
        """
        # Anything that puts text in front of a person. `say` and `print` belong here
        # because wizard.py prints its prompts directly: a sink that is not listed is a
        # blind spot, and this test missed a hardcoded "[S/n]" until they were added.
        sinks = {"_emit", "_note", "_warn", "_hint", "_tell", "say", "print", "error",
                 "ArgumentTypeError", "ConfigError", "LocalPolicyError", "SetupError",
                 "WizardAborted"}
        described = {"help", "description", "epilog", "title"}
        # metavar values are usage-line placeholders, and _tell's first argument is a
        # catalog key by design.
        allowed = set(en_catalog.MESSAGES) | {"-h", "--help", "", " "}

        def name_of(call):
            return call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", "")

        def is_layout_only(node):
            """An f-string whose literal parts carry no letters is layout, not text.

            Indentation, arrows and separators around an already-translated value need no
            translation. Requiring t() for them would push whitespace into the catalog.
            Anything with a letter in it still has to come from the catalog.
            """
            if not isinstance(node, ast.JoinedStr):
                return False
            literals = [part.value for part in node.values
                        if isinstance(part, ast.Constant) and isinstance(part.value, str)]
            return not any(char.isalpha() for text in literals for char in text)

        def is_translated(node):
            if isinstance(node, ast.Call):
                return name_of(node) in {"t", "lines"}
            if isinstance(node, ast.JoinedStr):
                if is_layout_only(node):
                    return True
                return any(isinstance(part, ast.FormattedValue) and is_translated(part.value)
                           for part in node.values)
            if isinstance(node, ast.BinOp):
                return is_translated(node.left) or is_translated(node.right)
            return False

        offenders = []
        for node in ast.walk(ast.parse(CLI_SOURCE)):
            if not isinstance(node, ast.Call):
                continue
            if name_of(node) in sinks:
                for argument in node.args:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        if argument.value.strip() and argument.value not in allowed:
                            offenders.append((node.lineno, argument.value))
                    elif isinstance(argument, (ast.JoinedStr, ast.BinOp)) and not is_translated(argument):
                        offenders.append((node.lineno, "<f-string or concatenation without t()>"))
            for keyword in node.keywords:
                if keyword.arg in described and isinstance(keyword.value, ast.Constant):
                    value = keyword.value.value
                    if isinstance(value, str) and value.strip() and value not in allowed:
                        offenders.append((node.lineno, value))
        self.assertEqual(offenders, [],
                         f"{' / '.join(TEXT_MODULES)} write text that bypasses the catalog: "
                         f"{offenders}")


    def test_the_layout_exemption_does_not_swallow_real_text(self):
        """Guards the exemption itself: only letter-free literals may skip the catalog.

        Written because the exemption was added to let `f"  {line}"` through, and an
        exemption that is one character too wide would hide every untranslated sentence.
        """
        import ast as ast_module

        def literals_have_letters(source):
            tree = ast_module.parse(source)
            for node in ast_module.walk(tree):
                if isinstance(node, ast_module.JoinedStr):
                    parts = [p.value for p in node.values
                             if isinstance(p, ast_module.Constant) and isinstance(p.value, str)]
                    return any(char.isalpha() for text in parts for char in text)
            return False

        # Layout: must be exempt.
        for source in ('f"  {line}"', 'f"      {a}  ->  {b}"', 'f"{x}: {y}"'):
            with self.subTest(source=source):
                self.assertFalse(literals_have_letters(source))
        # Real text: must never be exempt.
        for source in ('f"  Hello {name}"', 'f"{count} files written"', 'f"done: {x}"'):
            with self.subTest(source=source):
                self.assertTrue(literals_have_letters(source))


class LanguageResolutionTests(LanguageResetTestCase):
    def test_precedence_is_flag_then_environment_then_english(self):
        env = {i18n.LANGUAGE_ENV: "es"}
        self.assertEqual(i18n.resolve_language(explicit="en", environ=env).language, "en")
        self.assertEqual(i18n.resolve_language(explicit="en", environ=env).source, i18n.SOURCE_FLAG)
        self.assertEqual(i18n.resolve_language(environ=env).language, "es")
        self.assertEqual(i18n.resolve_language(environ=env).source, i18n.SOURCE_ENV)
        default = i18n.resolve_language(environ={})
        self.assertEqual((default.language, default.source), ("en", i18n.SOURCE_DEFAULT))

    def test_locale_tags_are_normalized_and_the_os_locale_is_never_consulted(self):
        for raw in ("es", "ES", "es-CL", "es_CL.UTF-8", " es "):
            with self.subTest(raw=raw):
                self.assertEqual(i18n.normalize_language(raw), "es")
        for raw in ("fr", "", None, "esperanto", 7):
            with self.subTest(raw=raw):
                self.assertIsNone(i18n.normalize_language(raw))
        # No LANG/LC_ALL fallback: identical output in a shell, in CI and in docs.
        with patch.dict(os.environ, {"LANG": "es_CL.UTF-8", "LC_ALL": "es_CL.UTF-8"}, clear=True):
            self.assertEqual(i18n.resolve_language().language, "en")

    def test_an_unsupported_value_degrades_to_english_and_is_reported(self):
        choice = i18n.resolve_language(environ={i18n.LANGUAGE_ENV: "fr"})
        self.assertEqual(choice.language, "en")
        self.assertEqual(choice.source, i18n.SOURCE_DEFAULT)
        self.assertEqual(choice.rejected, (f"{i18n.LANGUAGE_ENV}=fr",))
        with self.assertRaises(ValueError):
            i18n.set_language("fr")


class TranslationFallbackTests(LanguageResetTestCase):
    def test_a_gap_or_a_broken_placeholder_falls_back_to_english(self):
        i18n.set_language("es")
        with patch.dict(i18n.CATALOGS, {"es": {}}, clear=False):
            self.assertEqual(i18n.t("msg.prefix.note"), en_catalog.MESSAGES["msg.prefix.note"])
        with patch.dict(i18n.CATALOGS, {"es": {"error.path.not_set": "{ruta} no existe."}}, clear=False):
            self.assertEqual(i18n.t("error.path.not_set", path="site.name"),
                             en_catalog.MESSAGES["error.path.not_set"].format(path="site.name"))

    def test_an_unknown_key_returns_the_key_instead_of_raising(self):
        self.assertEqual(i18n.t("nope.not.a.key"), "nope.not.a.key")

    def test_lines_splits_block_entries(self):
        self.assertEqual(i18n.lines("app.examples"), tuple(en_catalog.MESSAGES["app.examples"].splitlines()))


class TerminalOutputTests(LanguageResetTestCase):
    def help_text(self, *argv):
        stdout = io.StringIO()
        with patch("sys.stdout", stdout), patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit):
            main(list(argv))
        return stdout.getvalue()

    def test_spanish_help_localizes_argparse_furniture_too(self):
        text = self.help_text("--lang", "es")
        for expected in ("uso: ", "opciones:", "comandos:", "ejemplos:", "muestra esta ayuda y termina"):
            self.assertIn(expected, text)
        for leaked in ("usage: ", "options:", "examples:", "show this help message"):
            self.assertNotIn(leaked, text)

    def test_sub_command_help_is_localized_at_any_flag_position(self):
        before = self.help_text("--lang", "es", "init", "-h")
        after = self.help_text("init", "--lang", "es", "-h")
        self.assertEqual(before, after, "--lang must work before and after the command")
        self.assertIn("argumentos:", before)
        self.assertIn("plantilla", before)

    def test_the_banner_is_language_independent(self):
        self.assertIn("|___||___/ \\___|", self.help_text("--lang", "es"))
        self.assertIn("|___||___/ \\___|", self.help_text("--lang", "en"))


class MachineContractTests(LanguageResetTestCase):
    """Whatever the terminal language is, scripts and AI clients read the same bytes."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.home = self.root / "home"
        self.project.mkdir()
        self.home.mkdir()
        patches = (
            patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)}, clear=True),
            patch("pathlib.Path.home", return_value=self.home),
            patch("socket.create_connection", side_effect=AssertionError("Network forbidden")),
            patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Network forbidden")),
            patch("isc_ai.server.log_debug"),
        )
        for guard in patches:
            guard.start()
            self.addCleanup(guard.stop)

    def run_cli(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_json_reports_and_exit_codes_are_identical_in_every_language(self):
        self.run_cli("init", str(self.project), "--no-servers")
        for command in (["config", "--project-dir", str(self.project), "validate"],
                        ["facility", "--project-dir", str(self.project), "render"]):
            english = self.run_cli(*command)
            spanish = self.run_cli(*command, "--lang", "es")
            with self.subTest(command=command[0]):
                self.assertEqual(english[0], spanish[0], "exit codes are a contract")
                self.assertEqual(english[1], spanish[1], "JSON payloads must not be translated")

    def test_doctor_reports_the_language_without_translating_its_own_json(self):
        self.run_cli("init", str(self.project), "--no-servers")
        command = ["doctor", "--project-dir", str(self.project)]
        english_code, english_out, english_err = self.run_cli(*command)
        code, out, err = self.run_cli(*command, "--lang", "es")
        report, english_report = json.loads(out), json.loads(english_out)
        self.assertEqual(code, english_code, "the language must not change the exit code")
        self.assertEqual(report["language"], "es")
        self.assertEqual(report["language_source"], i18n.SOURCE_FLAG)
        self.assertEqual(report["project_source"], "--project-dir")  # a value, not prose
        self.assertNotIn("proyecto", out)
        # The language fields are the only difference allowed in the report.
        volatile = {"language", "language_source"}
        self.assertEqual({key: value for key, value in report.items() if key not in volatile},
                         {key: value for key, value in english_report.items() if key not in volatile})
        # Problems and notes come from the runtime, so they stay English in both.
        self.assertEqual(report["problems"], english_report["problems"])

    def test_confirmations_are_translated_but_the_data_is_not(self):
        self.run_cli("init", str(self.project), "--no-servers")
        code, out, _ = self.run_cli("config", "--project-dir", str(self.project),
                                    "set", "site.name", "ACME", "--lang", "es")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), 'site.name = "ACME"')  # dotted path and JSON value untouched
        code, _, err = self.run_cli("config", "--project-dir", str(self.project),
                                    "unset", "nothing.here", "--lang", "es")
        self.assertEqual(code, 1)
        self.assertIn("nothing.here no está definido", err)
        self.assertIn("config falló:", err)


if __name__ == "__main__":
    unittest.main()
