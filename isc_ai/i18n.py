"""Human-language selection for the isc-ai command line.

Scope is deliberate and narrow: this catalog covers the text the CLI writes for a
human — help, examples, hints, warnings and confirmations. Everything a machine
consumes stays in English on purpose and is never routed through here:

  * MCP tool and parameter descriptions, and every MCP response text
  * JSON payloads of `doctor`, `config validate`, `setup` and `facility`
  * gate verdicts (CONTROLLED_ACTIVE, SUSPENDED, ABSENT), states (INSTALLED,
    DIFFERENT, UNCHANGED, UPGRADED) and exit codes
  * configuration keys, dotted paths, server ids, namespaces and class names
  * error text raised by the runtime modules (config.py, server.py,
    ccr_discovery.py), which reaches AI clients as well as this terminal

Those are contracts. An AI client or a script must read the same bytes regardless
of who runs the command, so translating them would make behaviour depend on a
locale.

Resolution is explicit and never guesses from the operating system locale, so the
same command prints the same words in your shell, in CI and in documentation:

    --lang es  >  ISC_AI_LANG=es  >  English

Language is a property of the person reading, not of the deployment, which is why
it is not a key of .iris-mcp/config.json.
"""

import os
from dataclasses import dataclass, field

from isc_ai.locales import en as _en
from isc_ai.locales import es as _es

LANGUAGES = ("en", "es")
DEFAULT_LANGUAGE = "en"
LANGUAGE_ENV = "ISC_AI_LANG"

CATALOGS = {"en": _en.MESSAGES, "es": _es.MESSAGES}

SOURCE_FLAG = "--lang"
SOURCE_ENV = LANGUAGE_ENV
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class LanguageChoice:
    """Which language won, where it came from, and what was ignored on the way."""

    language: str = DEFAULT_LANGUAGE
    source: str = SOURCE_DEFAULT
    rejected: tuple = field(default=())


def normalize_language(raw):
    """'es', 'ES', 'es-CL', 'es_CL.UTF-8' -> 'es'. Anything unsupported -> None."""
    if not isinstance(raw, str):
        return None
    tag = raw.strip().replace("_", "-").split(".")[0].split("-")[0].lower()
    return tag if tag in LANGUAGES else None


def resolve_language(explicit=None, environ=None):
    """Apply the precedence above and report every value that had to be ignored.

    An unsupported value never aborts the process: a stale ISC_AI_LANG must not
    stop the MCP server from starting, so it degrades to English and is reported.
    """
    environ = os.environ if environ is None else environ
    rejected = []
    for raw, source in ((explicit, SOURCE_FLAG), (environ.get(LANGUAGE_ENV), SOURCE_ENV)):
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        language = normalize_language(raw)
        if language is None:
            rejected.append(f"{source}={raw}")
            continue
        return LanguageChoice(language=language, source=source, rejected=tuple(rejected))
    return LanguageChoice(language=DEFAULT_LANGUAGE, source=SOURCE_DEFAULT, rejected=tuple(rejected))


_current = LanguageChoice()


def set_language(choice):
    """Install the active choice. Accepts a LanguageChoice or a plain tag."""
    global _current
    if isinstance(choice, LanguageChoice):
        _current = choice
    else:
        language = normalize_language(choice)
        if language is None:
            raise ValueError(f"Unsupported language {choice!r}; expected one of {', '.join(LANGUAGES)}.")
        _current = LanguageChoice(language=language, source=SOURCE_FLAG)
    return _current


def current():
    return _current


def current_language():
    return _current.language


def catalog(language=None):
    return CATALOGS[normalize_language(language) or current_language()]


def t(key, **params):
    """Translate `key`, falling back to English and finally to the key itself.

    Every entry always goes through `str.format`, so a literal brace in a catalog
    is always doubled ({{env:VAR}}) whether or not that entry takes parameters.
    One rule for whoever writes a translation beats a rule that depends on the
    call site.

    Text is data, not code: a missing key or a mistyped placeholder in a
    translation must never raise from inside an error path. Gaps are impossible to
    ship silently because the catalogs are checked for key and placeholder parity
    by the test suite.
    """
    fallback = None
    for language in (current_language(), DEFAULT_LANGUAGE):
        template = CATALOGS[language].get(key)
        if template is None:
            continue
        if fallback is None:
            fallback = template
        try:
            return template.format(**params)
        except (KeyError, IndexError, ValueError):
            continue  # a broken translation must never hide the English one
    return fallback if fallback is not None else key


def lines(key, **params):
    """Same as `t`, split into a tuple of lines for block-shaped entries."""
    return tuple(t(key, **params).splitlines())
