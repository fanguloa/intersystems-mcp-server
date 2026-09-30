"""Interactive configuration of the agentic environment.

The other commands are verbs for someone who already knows what to change. This one asks.
It exists because knowing that `setup`, `secrets`, `suite`, `agents` and `docs` have to run,
in that order, with those flags, is knowledge the tool should hold instead of the person.

Design rules, all deliberate:

- Nothing is written until the whole plan is shown and confirmed. A wizard that writes as it
  goes cannot be cancelled halfway without leaving a mixed state.
- Every step can be declined. Someone configuring only a credential should not have to answer
  about IRIS topology.
- It never invents a value. If an answer is needed and absent, the step is declined and
  reported, never guessed.
- It refuses to run without a terminal. A prompt read from a pipe is a hang, and a wizard
  that silently accepts defaults in CI is worse than one that fails loudly.
- IRIS is one optional module, not the centre. The environment is clients, servers, harness,
  phases and credentials; IRIS/TrakCare is one of the servers this can configure.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import agents as agents_module
from . import audit as audit_module
from . import config as config_module
from . import docs as docs_module
from . import secrets as secrets_module
from . import suite as suite_module
from . import i18n
from .i18n import t

STEP_CLIENTS = "clients"
STEP_HARNESS = "harness"
STEP_SECRETS = "secrets"
STEP_SERVERS = "servers"
STEP_PHASES = "phases"
STEP_DOCS = "docs"
STEP_IRIS = "iris"

# Dependency order, and the reason for it: the harness ships the phase definitions that the
# phases step assigns models to; credentials must exist before the servers that reference
# them are written; the IRIS policy must exist before documents derive topology from it.
STEP_ORDER = (
    STEP_CLIENTS,
    STEP_IRIS,
    STEP_HARNESS,
    STEP_SECRETS,
    STEP_SERVERS,
    STEP_PHASES,
    STEP_DOCS,
)


class WizardAborted(Exception):
    """The person cancelled, or there is no terminal to ask on."""


def _report_of(result):
    """plan()/apply() return (report, writes) or the report alone, depending on the verb."""
    return result[0] if isinstance(result, tuple) else result


@dataclass
class Answer:
    step: str
    chosen: bool
    detail: str = ""
    actions: list = field(default_factory=list)
    values: dict = field(default_factory=dict)


@dataclass
class Session:
    project_dir: Path
    home: Path | None = None
    environ: dict | None = None
    answers: list = field(default_factory=list)

    def answer_for(self, step):
        for answer in self.answers:
            if answer.step == step:
                return answer
        return None

    def chose(self, step):
        answer = self.answer_for(step)
        return bool(answer and answer.chosen)

    @property
    def planned_actions(self):
        actions = []
        for answer in self.answers:
            if answer.chosen:
                actions.extend(answer.actions)
        return actions

    @property
    def declined(self):
        return [answer.step for answer in self.answers if not answer.chosen]


# --------------------------------------------------------------------------- asking

ESCAPE = "\x1b"
_CTRL_C = "\x03"
_CTRL_D = "\x04"

try:  # Windows only: lets a single ESC abort without waiting for Enter.
    import msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX
    msvcrt = None


class Console:
    """The only place that reads from a person, so a test can replace it wholesale."""

    def __init__(self, input_stream=None, output_stream=None, assume_tty=None,
                 allow_raw_keys=True):
        self.input_stream = input_stream if input_stream is not None else sys.stdin
        self.output_stream = output_stream if output_stream is not None else sys.stderr
        if assume_tty is None:
            assume_tty = bool(getattr(self.input_stream, "isatty", lambda: False)())
        self.is_tty = assume_tty
        # Raw single-key reads are only correct for one-key decisions on a real console.
        self.allow_raw_keys = bool(allow_raw_keys)

    def say(self, text=""):
        print(text, file=self.output_stream)

    def require_tty(self):
        if not self.is_tty:
            raise WizardAborted(t("error.wizard.no_tty"))

    def _abort_if_cancelled(self, text):
        """ESC anywhere in the answer means cancel, and cancelling writes nothing."""
        if ESCAPE in text:
            raise WizardAborted(t("msg.wizard.cancelled"))
        return text

    def _read_line(self):
        try:
            raw = self.input_stream.readline()
        except KeyboardInterrupt:
            raise WizardAborted(t("msg.wizard.cancelled")) from None
        if raw == "":
            raise WizardAborted(t("error.wizard.input_closed"))
        return self._abort_if_cancelled(raw.strip())

    def _can_read_single_keys(self):
        return bool(self.allow_raw_keys and self.is_tty and msvcrt is not None
                    and self.input_stream is sys.stdin)

    def _read_single_key(self):
        """One keypress, unbuffered. ESC and Ctrl+C abort; Enter takes the default.

        Only used for yes/no decisions. Text answers need line editing -- backspace, paste --
        so those stay line-buffered and detect ESC in the submitted line instead.
        """
        while True:
            char = msvcrt.getwch()
            if char in (ESCAPE, _CTRL_C):
                raise WizardAborted(t("msg.wizard.cancelled"))
            if char == _CTRL_D:
                raise WizardAborted(t("error.wizard.input_closed"))
            if char in ("\r", "\n"):
                return ""
            if char in ("\x00", "\xe0"):
                msvcrt.getwch()  # a function or arrow key: consume its second byte
                continue
            if char.isprintable():
                return char

    def ask_yes_no(self, question, default=True):
        self.require_tty()
        suffix = t("msg.wizard.yes_no_yes") if default else t("msg.wizard.yes_no_no")
        positive = {word.strip().lower()
                    for word in i18n.lines("msg.wizard.words_yes") if word.strip()}
        negative = {word.strip().lower()
                    for word in i18n.lines("msg.wizard.words_no") if word.strip()}
        while True:
            print(f"  {question} {suffix} ", end="", file=self.output_stream, flush=True)
            if self._can_read_single_keys():
                reply = self._read_single_key().lower()
                self.say(reply)  # raw mode does not echo, so echo it here
            else:
                reply = self._read_line().lower()
            if not reply:
                return default
            if reply in positive:
                return True
            if reply in negative:
                return False
            self.say(t("msg.wizard.answer_yes_no"))

    def ask_text(self, question, default=None):
        self.require_tty()
        hint = f" [{default}]" if default else ""
        while True:
            print(f"  {question}{hint}: ", end="", file=self.output_stream, flush=True)
            reply = self._read_line()
            if reply:
                return reply
            if default is not None:
                return default
            self.say(t("msg.wizard.answer_required"))

    def ask_secret(self, key):
        """Read a credential with no echo, through the same path `secrets set` uses."""
        self.require_tty()
        return secrets_module.read_value(t("msg.wizard.secret_prompt", name=key))


# --------------------------------------------------------------------------- steps

def _step_clients(console, session, report):
    clients = report.get("clients", {})
    installed = sorted(name for name, info in clients.items() if info.get("installed"))
    missing = sorted(name for name, info in clients.items() if not info.get("installed"))
    console.say()
    console.say(t("msg.wizard.step_clients"))
    if installed:
        console.say("    " + t("msg.wizard.clients_found", names=", ".join(installed)))
    if missing:
        console.say("    " + t("msg.wizard.clients_missing", names=", ".join(missing)))
    if not installed:
        # Nothing to configure, and installing a client is a decision of its own.
        console.say("    " + t("msg.wizard.clients_none"))
        return Answer(STEP_CLIENTS, chosen=False, detail="none installed")
    return Answer(STEP_CLIENTS, chosen=True, detail=", ".join(installed),
                  values={"clients": installed})


def _step_iris(console, session, report):
    console.say()
    console.say(t("msg.wizard.step_iris"))
    config_path = Path(config_module.project_config_path(session.project_dir, session.environ))
    if config_path.is_file():
        console.say("    " + t("msg.wizard.iris_present", path=config_path))
        return Answer(STEP_IRIS, chosen=False, detail=str(config_path))
    if not console.ask_yes_no(t("msg.wizard.iris_question"), default=False):
        console.say("    " + t("msg.wizard.iris_skipped"))
        return Answer(STEP_IRIS, chosen=False)
    site = console.ask_text(t("msg.wizard.iris_site"))
    values = {
        "site": site,
        "ccr_prefix": console.ask_text(t("msg.wizard.iris_ccr_prefix"),
                                       default=site.upper()[:4]).upper(),
        "host": console.ask_text(t("msg.wizard.iris_base_host")),
        "namespace": console.ask_text(t("msg.wizard.iris_base_namespace")),
        "package_prefix": console.ask_text(t("msg.wizard.iris_package_prefix")),
    }
    return Answer(STEP_IRIS, chosen=True, detail=f"{site} / {values['namespace']}",
                  actions=[t("msg.wizard.action_iris", path=config_path)],
                  values={"iris": values})


def _step_harness(console, session, report):
    harness = report.get("harness", {})
    counts = Counter(harness.get("actions") or {})
    pending = {action: count for action, count in counts.items() if action != "noop"}
    console.say()
    console.say(t("msg.wizard.step_harness"))
    console.say("    " + t("msg.wizard.harness_state",
                           files=harness.get("files", 0),
                           version=harness.get("bundle_version", "?")))
    if not pending:
        console.say("    " + t("msg.wizard.harness_current"))
        return Answer(STEP_HARNESS, chosen=False, detail="up to date")
    summary = ", ".join(f"{action}={count}" for action, count in sorted(pending.items()))
    console.say("    " + t("msg.wizard.harness_pending", summary=summary))
    if not console.ask_yes_no(t("msg.wizard.harness_question"), default=True):
        return Answer(STEP_HARNESS, chosen=False)
    return Answer(STEP_HARNESS, chosen=True, detail=summary,
                  actions=[t("msg.wizard.action_harness", summary=summary)])


def _declared_secrets(session):
    """Credential settings the suite declares, paired with whether they resolve today.

    Read from the suite baseline rather than from the store: a credential that nobody
    declares is not something to ask for, and one that is declared but absent is exactly
    what this step exists to catch.
    """
    suite = audit_module.load_suite()
    declared = []
    for name, spec in suite.get("servers", {}).items():
        for key, setting in (spec.get("settings") or {}).items():
            if setting.get("secret"):
                declared.append((key, name, bool(setting.get("required"))))

    inventory = secrets_module.inventory(
        project_dir=session.project_dir, home=session.home, environ=session.environ
    )
    resolved = {
        entry["key"] for entry in inventory.get("keys", [])
        if entry.get("effective")
    }
    return [(key, server, required) for key, server, required in declared
            if key not in resolved]


def _step_secrets(console, session, report):
    console.say()
    console.say(t("msg.wizard.step_secrets"))
    missing = _declared_secrets(session)
    if not missing:
        console.say("    " + t("msg.wizard.secrets_all_resolved"))
        return Answer(STEP_SECRETS, chosen=False, detail="all resolved")
    captured = {}
    for key, server, required in missing:
        console.say("    " + t("msg.wizard.secrets_needed", name=key, server=server))
        if not console.ask_yes_no(t("msg.wizard.secrets_question", name=key), default=required):
            continue
        value = console.ask_secret(key)
        if not value:
            console.say("    " + t("msg.wizard.secrets_empty", name=key))
            continue
        captured[key] = value
    if not captured:
        return Answer(STEP_SECRETS, chosen=False)
    names = ", ".join(sorted(captured))
    return Answer(STEP_SECRETS, chosen=True, detail=names,
                  actions=[t("msg.wizard.action_secrets", names=names)],
                  # Held in memory only until apply; never echoed, logged or serialized.
                  values={"secrets": captured})


def _step_servers(console, session, report):
    console.say()
    console.say(t("msg.wizard.step_servers"))
    plan = _report_of(suite_module.plan(
        project_dir=session.project_dir, home=session.home, environ=session.environ))
    changes = plan.get("changes", [])
    if not changes:
        console.say("    " + t("msg.wizard.servers_current"))
        return Answer(STEP_SERVERS, chosen=False, detail="up to date")
    console.say("    " + t("msg.wizard.servers_pending", count=len(changes)))
    for change in changes[:8]:
        console.say(f"      {change}")
    if len(changes) > 8:
        console.say("      " + t("msg.wizard.and_more", count=len(changes) - 8))
    if not console.ask_yes_no(t("msg.wizard.servers_question"), default=True):
        return Answer(STEP_SERVERS, chosen=False)
    return Answer(STEP_SERVERS, chosen=True, detail=f"{len(changes)} changes",
                  actions=[t("msg.wizard.action_servers", count=len(changes))])


def _step_phases(console, session, report):
    console.say()
    console.say(t("msg.wizard.step_phases"))
    try:
        plan = _report_of(agents_module.plan(
            project_dir=session.project_dir, home=session.home,
            environ=session.environ, verify_models=False))
    except Exception as error:
        # A broken client config must not take the whole wizard down with it.
        console.say("    " + t("msg.wizard.phases_unavailable", reason=str(error)[:110]))
        return Answer(STEP_PHASES, chosen=False, detail=str(error)[:110])
    pending = [entry for entry in plan.get("files", []) if entry.get("action") != "noop"]
    roles = plan.get("roles", [])
    console.say("    " + t("msg.wizard.phases_state", count=len(roles)))
    if not pending:
        console.say("    " + t("msg.wizard.phases_current"))
        return Answer(STEP_PHASES, chosen=False, detail="up to date")
    for entry in pending[:6]:
        console.say(f"      {entry.get('action')}  {entry.get('path')}")
    if not console.ask_yes_no(t("msg.wizard.phases_question"), default=True):
        return Answer(STEP_PHASES, chosen=False)
    return Answer(STEP_PHASES, chosen=True, detail=f"{len(pending)} files",
                  actions=[t("msg.wizard.action_phases", count=len(pending))])


def _step_docs(console, session, report):
    console.say()
    console.say(t("msg.wizard.step_docs"))
    plan = _report_of(docs_module.plan(
        project_dir=session.project_dir, home=session.home, environ=session.environ))
    pending = [block for block in plan.get("blocks", []) if block.get("action") != "noop"]
    if not pending:
        console.say("    " + t("msg.wizard.docs_current"))
        return Answer(STEP_DOCS, chosen=False, detail="up to date")
    console.say("    " + t("msg.wizard.docs_pending", count=len(pending)))
    for block in pending:
        console.say(f"      {block.get('block')}  ->  {block.get('document')}")
    if not console.ask_yes_no(t("msg.wizard.docs_question"), default=True):
        return Answer(STEP_DOCS, chosen=False)
    return Answer(STEP_DOCS, chosen=True, detail=f"{len(pending)} blocks",
                  actions=[t("msg.wizard.action_docs", count=len(pending))])


STEP_HANDLERS = {
    STEP_CLIENTS: _step_clients,
    STEP_IRIS: _step_iris,
    STEP_HARNESS: _step_harness,
    STEP_SECRETS: _step_secrets,
    STEP_SERVERS: _step_servers,
    STEP_PHASES: _step_phases,
    STEP_DOCS: _step_docs,
}


# --------------------------------------------------------------------------- driving

def interview(console, project_dir=None, home=None, environ=None, steps=None):
    """Ask every selected step and return the session. Writes nothing."""
    console.require_tty()
    resolved = Path(project_dir) if project_dir else Path.cwd()
    session = Session(project_dir=resolved, home=home, environ=environ)

    console.say(t("msg.wizard.intro", path=resolved))
    console.say(t("msg.wizard.cancel_hint"))
    report = audit_module.run_audit(
        project_dir=resolved, home=home, environ=environ)
    summary = report.get("summary", {})
    console.say(t("msg.wizard.discovered",
                  errors=summary.get("errors", 0),
                  warnings=summary.get("warnings", 0)))

    wanted = tuple(steps) if steps else STEP_ORDER
    for step in STEP_ORDER:
        if step in wanted:
            session.answers.append(STEP_HANDLERS[step](console, session, report))
    return session


def render_plan(session):
    """The whole plan as lines, before a single file is touched."""
    lines = [t("msg.wizard.plan_title")]
    actions = session.planned_actions
    if not actions:
        lines.append("  " + t("msg.wizard.plan_empty"))
    else:
        lines.extend(f"  {action}" for action in actions)
    if session.declined:
        lines.append("")
        lines.append("  " + t("msg.wizard.plan_declined", names=", ".join(session.declined)))
    return lines


def apply(session, home=None, environ=None):
    """Execute the confirmed plan in dependency order, reporting each result."""
    target = session.project_dir
    home = home if home is not None else session.home
    environ = environ if environ is not None else session.environ
    performed = []

    if session.chose(STEP_IRIS):
        values = session.answer_for(STEP_IRIS).values["iris"]
        performed.append(_write_iris_policy(target, values, environ))

    if session.chose(STEP_SECRETS):
        for key, value in sorted(session.answer_for(STEP_SECRETS).values["secrets"].items()):
            secrets_module.set_secret(key, value, scope="global", home=home)
            performed.append(t("msg.wizard.done_secret", name=key))

    if session.chose(STEP_HARNESS):
        from . import setup as setup_module
        # Harness only: the MCP entry and the logo belong to the servers step.
        result = setup_module.run_setup(
            apply=True, home=home, environ=environ,
            configure_opencode=False, install_logo=False, install_harness=True)
        counts = Counter(item["action"] for item in result.get("actions", []))
        summary = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
        performed.append(t("msg.wizard.done_harness", summary=summary or "-"))

    if session.chose(STEP_SERVERS):
        result = _report_of(suite_module.apply(
            project_dir=target, home=home, environ=environ))
        performed.append(t("msg.wizard.done_servers", count=len(result.get("changes", []))))

    if session.chose(STEP_PHASES):
        result = _report_of(agents_module.apply(
            project_dir=target, home=home, environ=environ, verify_models=False))
        written = [entry for entry in result.get("files", []) if entry.get("action") != "noop"]
        performed.append(t("msg.wizard.done_phases", count=len(written)))

    if session.chose(STEP_DOCS):
        result = _report_of(docs_module.apply(
            project_dir=target, home=home, environ=environ))
        written = [block for block in result.get("blocks", []) if block.get("action") != "noop"]
        performed.append(t("msg.wizard.done_docs", count=len(written)))

    return performed


def _write_iris_policy(project_dir, values, environ):
    """Render the packaged policy template with the answers, inventing nothing."""
    template = config_module.load_template()
    template["site"]["name"] = values["site"]
    template["site"]["ccr_id"]["prefix"] = values["ccr_prefix"]
    template["canonical_base"]["server_id"] = f"{values['site']}base"
    template["canonical_base"]["host"] = values["host"]
    template["canonical_base"]["namespace"] = values["namespace"]
    template["packages"] = {values["namespace"]: [{"prefix": values["package_prefix"]}]}
    template["mutation"]["class_prefix"] = values["package_prefix"]
    # Example routes belong to the template, not to a real deployment.
    template.pop("routes", None)
    # Server connections carry credentials: they live in the user file, never the project one.
    template.pop("servers", None)
    path = Path(config_module.project_config_path(project_dir, environ))
    config_module.write_json(path, template)
    return t("msg.wizard.done_iris", path=path)

