"""isc-ai command line.

    isc-ai mcp                      run the MCP server over stdio (what the client launches)
    isc-ai init [DIR]               create <project>/.iris-mcp/config.json from the packaged template
    isc-ai config <sub>             show | set | unset | add-server | remove-server | validate
    isc-ai servers migrate          copy legacy ~/.iris_mcp_servers.json into ~/.iris-mcp/servers.json
    isc-ai facility render|install  render / conditionally install MCP.CCRMutation for this site
    isc-ai doctor                   what project, files and servers this process would use
    isc-ai setup                    plan/apply local OpenCode and Claude harness configuration

The CLI only edits configuration files; only `mcp` and `facility install` talk to IRIS.
Every command that resolves a project accepts `--project-dir` and otherwise searches
upward from the current directory for `.iris-mcp/config.json`.

`isc-ai` with no command prints the full help (exit 0); every sub-command carries its
own examples, reachable with `isc-ai <command> -h`.

Every human-facing string here comes from `isc_ai.i18n` (English or Spanish, selected
with `--lang` or ISC_AI_LANG). Machine-facing output does not: JSON payloads, gate
verdicts, facility states and configuration keys stay English in every language.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from isc_ai import __version__
from isc_ai import config as isc_config
from isc_ai import i18n
from isc_ai import agents as isc_agents
from isc_ai import docs as isc_docs
from isc_ai import suite as isc_suite
from isc_ai.audit import run_audit
from isc_ai.ccr_discovery import LocalPolicyError
from isc_ai.config import ConfigError
from isc_ai.i18n import t
from isc_ai.server import IRISMCPServer, _CCR_MUTATION_CLASS, _cli_failure_message, _load_ccr_mutation_resource
from isc_ai.setup import DEFAULT_TIMEOUT, SetupError, run_setup

PROG = "isc-ai"

_BANNER_ART = r"""
 ___  ___   ___      _    ___
|_ _|/ __| / __|    /_\  |_ _|
 | | \__ \| (__    / _ \  | |
|___||___/ \___|  /_/ \_\|___|"""

_ISC_BLUE = "\033[38;5;33m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _use_color(stream):
    """Same contract as argparse 3.14: honour NO_COLOR / FORCE_COLOR, otherwise require a TTY."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001 - a wrapped or fake stream is simply not a terminal
        return False


def _banner(stream=None):
    art = _BANNER_ART.strip("\n")
    version = f"  v{__version__}"
    if _use_color(stream if stream is not None else sys.stdout):
        art = "\n".join(f"{_ISC_BLUE}{line}{_RESET}" for line in art.splitlines())
        version = f"{_DIM}{version}{_RESET}"
    return f"{art}{version}\n"


class _BannerParser(argparse.ArgumentParser):
    """argparse prints 'usage:' first, so the banner is prepended to the whole help block."""

    def format_help(self):
        return _banner() + "\n" + super().format_help()


class _Formatter(argparse.RawDescriptionHelpFormatter):
    """Keeps descriptions and examples verbatim and localizes the 'usage:' prefix."""

    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, t("chrome.usage_prefix") if prefix is None else prefix)


def _localize_groups(parser):
    """Translate argparse's two built-in section titles.

    argparse exposes no public hook for them, so the private attributes are
    confined to this function: they have been stable since argparse entered the
    standard library, and the test suite asserts the localized titles, so a future
    rename fails loudly here instead of leaking English headings into the Spanish
    help. argparse's own parse errors ('unrecognized arguments', 'invalid choice')
    belong to its gettext domain and stay English.
    """
    parser._positionals.title = t("chrome.group.arguments")
    parser._optionals.title = t("chrome.group.options")
    return parser


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def _add_language_option(parser):
    """Accept --lang at every level.

    The value is already applied by `_prescan_language`, so this declaration exists
    to keep `isc-ai doctor --lang es` working as naturally as `isc-ai --lang es
    doctor`. SUPPRESS keeps it out of the namespace: nothing reads args.lang.
    """
    parser.add_argument("--lang", choices=i18n.LANGUAGES, default=argparse.SUPPRESS, help=t("chrome.lang"))


def _add_parser(sub, name, help_key, examples_key=None, **kwargs):
    """Sub-parser whose own help repeats the summary and shows verbatim examples."""
    help_text = t(help_key)
    epilog = None
    if examples_key:
        block = i18n.lines(examples_key)
        epilog = t("chrome.examples_label") + "\n" + "".join(f"  {line}\n" if line else "\n" for line in block)
    parser = sub.add_parser(
        name,
        help=help_text,
        description=help_text,
        epilog=epilog,
        formatter_class=_Formatter,
        add_help=False,
        **kwargs,
    )
    _localize_groups(parser)
    parser.add_argument("-h", "--help", action="help", help=t("chrome.help"))
    _add_language_option(parser)
    return parser


def _require_subcommand(parser, message):
    """Missing sub-command: show that command's own help, then fail like argparse does."""
    parser.print_help(sys.stderr)
    sys.stderr.write(f"\n{PROG}: {t('msg.prefix.error')}: {message}\n")
    sys.stderr.flush()
    raise SystemExit(2)


def _ccr_cli_value(value):
    """Shape check only; the site format (site.ccr_id) is enforced by the server."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value or ""):
        raise argparse.ArgumentTypeError(t("error.value.ccr"))
    return value


def _sha256_cli_value(value):
    if not re.fullmatch(r"[0-9a-f]{64}", value or ""):
        raise argparse.ArgumentTypeError(t("error.value.sha256"))
    return value


def _positive_int(value):
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(t("error.value.positive_int")) from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError(t("error.value.positive_int"))
    return parsed


def _add_project_dir(parser):
    parser.add_argument("--project-dir", help=t("flag.project_dir"))


def _prescan_language(argv):
    """Read --lang before argparse exists, because the help text itself depends on it.

    Accepts `--lang es` and `--lang=es`. An unsupported value is not rejected here:
    resolve_language() decides, reports it and falls back to English.
    """
    for index, token in enumerate(argv):
        if token == "--lang":
            return argv[index + 1] if index + 1 < len(argv) else ""
        if token.startswith("--lang="):
            return token.split("=", 1)[1]
    return None


def _parse_cli_args(argv):
    # The help text is built from the catalog, so the language must be settled first.
    choice = i18n.resolve_language(explicit=_prescan_language(argv))
    i18n.set_language(choice)

    parser = _BannerParser(
        prog=PROG,
        description=t("app.description"),
        epilog=t("chrome.examples_label") + "\n"
               + "".join(f"  {line}\n" if line else "\n" for line in i18n.lines("app.examples"))
               + "\n" + t("app.footer"),
        formatter_class=_Formatter,
        add_help=False,
    )
    _localize_groups(parser)
    parser.add_argument("-h", "--help", action="help", help=t("chrome.help"))
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}", help=t("chrome.version"))
    _add_language_option(parser)
    sub = parser.add_subparsers(dest="command", title=t("chrome.group.commands"), metavar="<command>")

    p_mcp = _add_parser(sub, "mcp", "cmd.mcp.help", "cmd.mcp.examples")
    _add_project_dir(p_mcp)

    p_init = _add_parser(sub, "init", "cmd.init.help", "cmd.init.examples")
    p_init.add_argument("directory", nargs="?", help=t("flag.init.directory"))
    p_init.add_argument("--from", dest="template", help=t("flag.init.template"))
    p_init.add_argument("--force", action="store_true", help=t("flag.init.force"))
    p_init.add_argument("--no-servers", action="store_true", help=t("flag.init.no_servers"))

    p_config = _add_parser(sub, "config", "cmd.config.help", "cmd.config.examples")
    _add_project_dir(p_config)
    config_sub = p_config.add_subparsers(dest="config_command", title=t("chrome.group.subcommands"),
                                         metavar="<sub-command>")

    p_show = _add_parser(config_sub, "show", "cmd.config.show.help", "cmd.config.show.examples")
    p_show.add_argument("--reveal", action="store_true", help=t("flag.show.reveal"))
    p_show.add_argument("--servers", action="store_true", help=t("flag.show.servers"))

    p_set = _add_parser(config_sub, "set", "cmd.config.set.help", "cmd.config.set.examples")
    p_set.add_argument("path")
    p_set.add_argument("value", help=t("flag.set.value"))

    p_unset = _add_parser(config_sub, "unset", "cmd.config.unset.help", "cmd.config.unset.examples")
    p_unset.add_argument("path")

    p_add = _add_parser(config_sub, "add-server", "cmd.config.add_server.help", "cmd.config.add_server.examples")
    p_add.add_argument("server_id")
    p_add.add_argument("--host", required=True)
    p_add.add_argument("--port", type=int, default=80)
    p_add.add_argument("--path-prefix", default="")
    p_add.add_argument("--namespace", default="USER")
    p_add.add_argument("--role", default="", help=t("flag.add.role"))
    p_add.add_argument("--production", action="store_true", help=t("flag.add.production"))
    p_add.add_argument("--username", default="{env:IRIS_USER}", help=t("flag.add.username"))
    p_add.add_argument("--password", default="{env:IRIS_PASSWORD}", help=t("flag.add.password"))
    p_add.add_argument("--upload-token", help=t("flag.add.upload_token"))
    p_add.add_argument("--ccr-id", help=t("flag.add.ccr_id"))
    p_add.add_argument("--proxy")
    p_add.add_argument("--description")
    p_add.add_argument("--user", action="store_true", help=t("flag.add.user"))

    p_remove = _add_parser(config_sub, "remove-server", "cmd.config.remove_server.help",
                           "cmd.config.remove_server.examples")
    p_remove.add_argument("server_id")
    p_remove.add_argument("--user", action="store_true", help=t("flag.remove.user"))

    _add_parser(config_sub, "validate", "cmd.config.validate.help", "cmd.config.validate.examples")

    p_servers = _add_parser(sub, "servers", "cmd.servers.help", "cmd.servers.examples")
    servers_sub = p_servers.add_subparsers(dest="servers_command", title=t("chrome.group.subcommands"),
                                           metavar="<sub-command>")
    p_migrate = _add_parser(servers_sub, "migrate", "cmd.servers.migrate.help", "cmd.servers.migrate.examples")
    p_migrate.add_argument("--force", action="store_true", help=t("flag.migrate.force"))

    p_facility = _add_parser(sub, "facility", "cmd.facility.help", "cmd.facility.examples")
    _add_project_dir(p_facility)
    facility_sub = p_facility.add_subparsers(dest="facility_command", title=t("chrome.group.subcommands"),
                                             metavar="<sub-command>")
    p_render = _add_parser(facility_sub, "render", "cmd.facility.render.help", "cmd.facility.render.examples")
    p_render.add_argument("--output", help=t("flag.render.output"))
    p_install = _add_parser(facility_sub, "install", "cmd.facility.install.help", "cmd.facility.install.examples")
    p_install.add_argument("--server", required=True)
    p_install.add_argument("--namespace", required=True)
    p_install.add_argument("--ccr", required=True, type=_ccr_cli_value, help=t("flag.install.ccr"))
    p_install.add_argument("--upgrade", action="store_true")
    p_install.add_argument("--expected-current-sha256", type=_sha256_cli_value)

    p_audit = _add_parser(sub, "audit", "cmd.audit.help", "cmd.audit.examples")
    _add_project_dir(p_audit)
    p_audit.add_argument("--errors-only", action="store_true", help=t("flag.audit.errors_only"))
    p_audit.add_argument("--suite", help=t("flag.audit.suite"))

    p_suite = _add_parser(sub, "suite", "cmd.suite.help", "cmd.suite.examples")
    _add_project_dir(p_suite)
    suite_mode = p_suite.add_mutually_exclusive_group()
    suite_mode.add_argument("--apply", action="store_true", help=t("flag.suite.apply"))
    suite_mode.add_argument("--check", action="store_true", help=t("flag.suite.check"))
    p_suite.add_argument("--client", action="append", choices=("opencode", "vscode", "claude"),
                         help=t("flag.suite.client"))
    p_suite.add_argument("--server", action="append", help=t("flag.suite.server"))
    p_suite.add_argument("--prune-claude-global", action="store_true", help=t("flag.suite.prune_claude"))
    p_suite.add_argument("--suite", help=t("flag.audit.suite"))

    p_docs = _add_parser(sub, "docs", "cmd.docs.help", "cmd.docs.examples")
    _add_project_dir(p_docs)
    docs_mode = p_docs.add_mutually_exclusive_group()
    docs_mode.add_argument("--apply", action="store_true", help=t("flag.docs.apply"))
    docs_mode.add_argument("--check", action="store_true", help=t("flag.docs.check"))

    p_agents = _add_parser(sub, "agents", "cmd.agents.help", "cmd.agents.examples")
    _add_project_dir(p_agents)
    agents_mode = p_agents.add_mutually_exclusive_group()
    agents_mode.add_argument("--apply", action="store_true", help=t("flag.agents.apply"))
    agents_mode.add_argument("--check", action="store_true", help=t("flag.agents.check"))
    p_agents.add_argument("--no-verify-models", action="store_true", help=t("flag.agents.no_verify"))

    p_doctor = _add_parser(sub, "doctor", "cmd.doctor.help", "cmd.doctor.examples")
    _add_project_dir(p_doctor)

    p_setup = _add_parser(sub, "setup", "cmd.setup.help", "cmd.setup.examples")
    mode = p_setup.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help=t("flag.setup.apply"))
    mode.add_argument("--check", action="store_true", help=t("flag.setup.check"))
    p_setup.add_argument("--claude-project", metavar="DIR", help=t("flag.setup.claude_project"))
    p_setup.add_argument("--opencode-config", metavar="PATH", help=t("flag.setup.opencode_config"))
    p_setup.add_argument("--no-opencode", action="store_true", help=t("flag.setup.no_opencode"))
    p_setup.add_argument("--no-logo", action="store_true", help=t("flag.setup.no_logo"))
    p_setup.add_argument("--no-harness", action="store_true", help=t("flag.setup.no_harness"))
    p_setup.add_argument("--adopt-harness", action="store_true", help=t("flag.setup.adopt_harness"))
    p_setup.add_argument("--timeout", type=_positive_int, default=DEFAULT_TIMEOUT, help=t("flag.setup.timeout"))
    p_setup.add_argument("--with-opencode", action="store_true", help=t("flag.setup.with_opencode"))

    args = parser.parse_args(argv)
    if choice.rejected:
        _warn(t("msg.language.rejected", values=", ".join(choice.rejected),
                language=choice.language, supported=", ".join(i18n.LANGUAGES)))
    if args.command is None:
        # No command is a discovery attempt, not a failure: show everything and exit clean.
        parser.print_help()
        raise SystemExit(0)
    if args.command == "config" and not args.config_command:
        _require_subcommand(p_config, t("error.subcommand.config"))
    if args.command == "servers" and not args.servers_command:
        _require_subcommand(p_servers, t("error.subcommand.servers"))
    if args.command == "facility":
        if not args.facility_command:
            _require_subcommand(p_facility, t("error.subcommand.facility"))
        if args.facility_command == "install":
            if args.upgrade and not args.expected_current_sha256:
                parser.error(t("error.upgrade_requires_sha"))
            if not args.upgrade and args.expected_current_sha256:
                parser.error(t("error.sha_requires_upgrade"))
    if args.command == "setup" and args.with_opencode and not args.apply:
        p_setup.error(t("error.with_opencode_requires_apply"))
    return args


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _configure_windows_stdio():
    # Force UTF-8 on Windows for stdin, stdout, and stderr to prevent encoding crashes with Spanish accents
    if sys.platform == "win32":
        try:
            import io
            sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass


def _cli_exit_code(record):
    """DIFFERENT -> 2; verified INSTALLED/UNCHANGED/UPGRADED -> 0; anything else -> 1."""
    state = record.get("state") if isinstance(record, dict) else None
    if state == "DIFFERENT":
        return 2
    if record.get("ok") == 1 and state in {"INSTALLED", "UNCHANGED", "UPGRADED"}:
        return 0
    return 1


def _emit(text):
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    sys.stdout.flush()


def _tell(prefix_key, message):
    """Prefixed line on stderr: note / warning / hint, with a localized label."""
    sys.stderr.write(f"{t(prefix_key)}: {message}\n")
    sys.stderr.flush()


def _note(message):
    _tell("msg.prefix.note", message)


def _warn(message):
    _tell("msg.prefix.warning", message)


def _hint(message):
    _tell("msg.prefix.hint", message)


def _project_paths(args):
    explicit = getattr(args, "project_dir", None)
    project_dir = Path(explicit).resolve() if explicit else isc_config.project_directory()
    return project_dir, isc_config.project_config_path(project_dir)


def _load_project_data(config_path):
    if not config_path.is_file():
        raise ConfigError(t("error.project.missing", path=config_path))
    data = isc_config.read_json(config_path)
    if not isinstance(data, dict):
        raise ConfigError(t("error.project.not_object", path=config_path))
    return data


def _servers_file_for(args, config_path):
    if getattr(args, "user", False):
        path = isc_config.user_servers_path()
        data = isc_config.read_json(path) if path.is_file() else {"servers": {}}
        if not isinstance(data, dict) or not isinstance(data.get("servers", {}), dict):
            raise ConfigError(t("error.servers.not_object", path=path))
        data.setdefault("servers", {})
        return path, data
    data = _load_project_data(config_path)
    servers = data.setdefault("servers", {})
    if not isinstance(servers, dict):
        raise ConfigError(t("error.servers.value_not_object"))
    return config_path, data


def _reject_literal_project_secrets(data):
    """The project file is committable: secrets must be {env:VAR} references."""
    literals = isc_config.literal_secret_paths(data.get("servers", {}))
    if literals:
        raise ConfigError(t("error.secrets.literal", paths=", ".join(literals)))


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def _run_init(args):
    directory = Path(args.directory).resolve() if args.directory else Path.cwd().resolve()
    config_path = directory / isc_config.PROJECT_CONFIG_RELATIVE
    if config_path.exists() and not args.force:
        _emit(t("msg.init.exists", path=config_path))
        return 0
    if not directory.is_dir():
        raise ConfigError(t("error.init.not_a_directory", directory=directory))
    ancestor = isc_config.find_project_root(directory.parent) if directory.parent != directory else None
    template = isc_config.load_template(args.template)
    if args.no_servers:
        template.pop("servers", None)
    isc_config.write_json(config_path, template)
    _emit(t("msg.init.created", path=config_path))
    if ancestor is not None:
        _warn(t("msg.init.shadow", ancestor=ancestor, directory=directory))
    return 0


def _run_config(args):
    project_dir, config_path = _project_paths(args)
    command = args.config_command

    if command == "show":
        runtime = isc_config.load_runtime_config(project_dir)
        if args.servers:
            data = runtime.servers
        else:
            data = _load_project_data(config_path) if config_path.is_file() else {}
        _emit(json.dumps(data if args.reveal else isc_config.redact(data), indent=2, ensure_ascii=False, sort_keys=True))
        for note in runtime.notes:
            _note(note)
        return 0

    if command == "set":
        data = _load_project_data(config_path)
        current = isc_config.get_path(data, args.path)
        value = isc_config.coerce_value(args.value, current)
        isc_config.set_path(data, args.path, value)
        isc_config.parse_policy(data, project_dir)
        _reject_literal_project_secrets(data)
        isc_config.write_json(config_path, data)
        if args.path.split(".")[-1] in isc_config.SECRET_KEYS:
            _emit(t("msg.config.set_secret", path=args.path))
        else:
            _emit(t("msg.config.set", path=args.path, value=json.dumps(value, ensure_ascii=False)))
        return 0

    if command == "unset":
        data = _load_project_data(config_path)
        if not isc_config.unset_path(data, args.path):
            raise ConfigError(t("error.path.not_set", path=args.path))
        isc_config.parse_policy(data, project_dir)
        isc_config.write_json(config_path, data)
        _emit(t("msg.config.unset", path=args.path))
        return 0

    if command == "add-server":
        path, data = _servers_file_for(args, config_path)
        role = args.role.strip().upper()
        if role and not re.fullmatch(r"[A-Z][A-Z0-9_]*", role):
            raise ConfigError(t("error.role.identifier"))
        entry = {
            "host": args.host, "port": args.port, "path_prefix": args.path_prefix,
            "namespace": args.namespace, "env_role": role,
            "is_production": bool(args.production),
            "username": args.username, "password": args.password,
        }
        for key, value in (("upload_token", args.upload_token), ("ccr_id", args.ccr_id),
                           ("proxy", args.proxy), ("description", args.description)):
            if value:
                entry[key] = value
        IRISMCPServer._validate_connection_config(dict(entry, username="x", password="x"))
        data["servers"][args.server_id] = entry
        if not getattr(args, "user", False):
            _reject_literal_project_secrets(data)
        isc_config.write_json(path, data)
        _emit(t("msg.server.written", server_id=args.server_id, path=path))
        return 0

    if command == "remove-server":
        path, data = _servers_file_for(args, config_path)
        if args.server_id not in data["servers"]:
            raise ConfigError(t("error.server.unknown", server_id=args.server_id, path=path))
        del data["servers"][args.server_id]
        isc_config.write_json(path, data)
        _emit(t("msg.server.removed", server_id=args.server_id, path=path))
        return 0

    if command == "validate":
        summary, problems = _validate(project_dir)
        _emit(json.dumps(summary, indent=2, ensure_ascii=False))
        return 1 if problems else 0

    raise ConfigError(t("error.unsupported.config"))


def _validate(project_dir):
    runtime = isc_config.load_runtime_config(project_dir)
    problems = []
    for server_id, entry in runtime.servers.items():
        if "_config_error" in entry:
            problems.append(f"servers.{server_id}: {entry['_config_error']}")
            continue
        try:
            IRISMCPServer._validate_connection_config(entry)
        except LocalPolicyError as exc:
            problems.append(f"servers.{server_id}: {exc}")
    base = runtime.policy.canonical_base
    if base is not None and base.server_id not in runtime.servers:
        problems.append(f"canonical_base.server_id '{base.server_id}' has no server entry.")
    summary = {
        "project_dir": str(runtime.project_dir),
        "project_config": str(runtime.project_config_path),
        "project_config_present": runtime.project_config_path.is_file(),
        "user_servers": str(runtime.user_servers_path),
        "user_servers_present": runtime.user_servers_path.is_file(),
        "site": runtime.policy.site_name,
        "configured": runtime.policy.configured,
        "ccr_format": runtime.policy.ccr_description if runtime.policy.ccr_configured else None,
        "canonical_base": base.server_id if base else None,
        "mutation_configured": runtime.policy.mutation_configured,
        "servers": sorted(runtime.servers),
        "notes": list(runtime.notes),
        "problems": problems,
    }
    return summary, problems


def _run_doctor(args):
    project_dir, _ = _project_paths(args)
    summary, problems = _validate(project_dir)
    cwd = Path.cwd().resolve()
    summary["cwd"] = str(cwd)
    resolved = Path(summary["project_dir"])
    if getattr(args, "project_dir", None):
        summary["project_source"] = "--project-dir"
    elif os.environ.get(isc_config.PROJECT_DIR_ENV):
        summary["project_source"] = isc_config.PROJECT_DIR_ENV
    elif resolved == cwd:
        summary["project_source"] = "cwd"
    elif resolved in cwd.parents:
        summary["project_source"] = f"ancestor ({len(cwd.relative_to(resolved).parts)} levels above cwd)"
    else:
        summary["project_source"] = "cwd (no configuration found)"
    summary["env"] = {
        name: os.environ.get(name)
        for name in (isc_config.PROJECT_DIR_ENV, isc_config.PROJECT_CONFIG_ENV, isc_config.EXTRA_PACKAGES_ENV,
                     "IRIS_MCP_TIMEOUT", i18n.LANGUAGE_ENV)
        if os.environ.get(name) is not None
    }
    # Machine-facing values: the report states which language the terminal uses but is
    # itself always English, so a script can parse it regardless of who ran the command.
    summary["language"] = i18n.current_language()
    summary["language_source"] = i18n.current().source
    summary["version"] = __version__
    _emit(json.dumps(summary, indent=2, ensure_ascii=False))
    if not summary["project_config_present"]:
        _hint(t("msg.doctor.no_config"))
    elif resolved == Path.home().resolve():
        _warn(t("msg.doctor.home_policy"))
    elif summary["project_source"].startswith("ancestor"):
        _note(t("msg.doctor.ancestor", project_dir=resolved))
    return 1 if problems else 0


def _run_audit(args):
    """Read-only report. Exit 1 on errors, 2 when only warnings or notes were found."""
    report = run_audit(project_dir=getattr(args, "project_dir", None), suite_path=args.suite)
    if args.errors_only:
        report = dict(report, findings=[item for item in report["findings"] if item["severity"] == "error"])
    _emit(json.dumps(report, indent=2, ensure_ascii=False))
    summary = report["summary"]
    if summary["errors"]:
        return 1
    return 2 if summary["warnings"] or summary["info"] else 0


def _run_agents(args):
    """Sub-agent model assignment. Plan by default; --apply writes the OpenCode entries."""
    runner = isc_agents.apply if args.apply else isc_agents.plan
    result = runner(project_dir=getattr(args, "project_dir", None),
                    verify_models=not args.no_verify_models)
    report = result[0] if isinstance(result, tuple) else result
    _emit(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if any(item["severity"] == "error" for item in report["findings"]) else 0


def _run_docs(args):
    """Refresh the managed blocks. Plan by default; --apply writes."""
    runner = isc_docs.apply if args.apply else isc_docs.plan
    result = runner(project_dir=getattr(args, "project_dir", None))
    report = result[0] if isinstance(result, tuple) else result
    _emit(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _run_suite(args):
    """Plan by default; --apply writes every file in one transaction or none."""
    runner = isc_suite.apply if args.apply else isc_suite.plan
    result = runner(project_dir=getattr(args, "project_dir", None), suite_path=args.suite,
                    clients=args.client, servers=args.server)
    # plan() returns (report, writes) so callers can inspect the writes; apply() returns
    # the report alone.
    report = result[0] if isinstance(result, tuple) else result
    if args.prune_claude_global:
        report["claude_global"] = isc_suite.prune_claude_global(
            suite_path=args.suite, apply_changes=args.apply)
    _emit(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _run_servers(args):
    if args.servers_command == "migrate":
        legacy = isc_config.legacy_user_servers_path()
        target = isc_config.user_servers_path()
        if not legacy.is_file():
            raise ConfigError(t("error.legacy.missing", legacy=legacy))
        if target.exists() and not args.force:
            raise ConfigError(t("error.legacy.target_exists", target=target))
        data = isc_config.read_json(legacy)
        if not isinstance(data, dict) or not isinstance(data.get("servers"), dict):
            raise ConfigError(t("error.legacy.shape", legacy=legacy))
        isc_config.write_json(target, {"servers": data["servers"]})
        _emit(t("msg.servers.migrated", count=len(data["servers"]), target=target, legacy=legacy))
        return 0
    raise ConfigError(t("error.unsupported.servers"))


def _run_setup(args):
    report = run_setup(
        apply=args.apply,
        check=args.check,
        configure_opencode=not args.no_opencode,
        install_logo=not args.no_logo,
        install_harness=not args.no_harness,
        adopt_harness=args.adopt_harness,
        claude_project=args.claude_project,
        opencode_config=args.opencode_config,
        with_opencode=args.with_opencode,
        timeout=args.timeout,
    )
    _emit(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report["conflicts"] else 0


def _run_facility(args):
    project_dir, _ = _project_paths(args)
    if args.facility_command == "render":
        runtime = isc_config.load_runtime_config(project_dir)
        source_bytes, digest = _load_ccr_mutation_resource(runtime.policy)
        if args.output:
            Path(args.output).write_bytes(source_bytes)
        _emit(json.dumps({"class": _CCR_MUTATION_CLASS, "sha256": digest, "bytes": len(source_bytes),
                          "output": args.output}, indent=2))
        return 0
    if args.facility_command == "install":
        try:
            server = IRISMCPServer(project_dir=project_dir if getattr(args, "project_dir", None) else None)
            record = server.initialize_ccr_mutation(
                args.server, args.namespace, args.ccr, args.upgrade,
                args.expected_current_sha256 or "",
            )
            sys.stdout.write(json.dumps(record, sort_keys=True) + "\n")
            sys.stdout.flush()
            return _cli_exit_code(record)
        except LocalPolicyError as exc:
            sys.stderr.write(t("error.facility.failed", detail=_cli_failure_message(exc)) + "\n")
            sys.stderr.flush()
            return 1
        except Exception as exc:  # noqa: BLE001 - bounded, redacted CLI boundary
            # Unexpected failure: never print a traceback or exception text, which may
            # carry paths, credentials or server details. Outcome is unverified.
            sys.stderr.write(t("error.facility.unexpected",
                               name=_cli_failure_message(type(exc).__name__, 64)) + "\n")
            sys.stderr.flush()
            return 1
    raise ConfigError(t("error.unsupported.facility"))


def main(argv=None):
    _configure_windows_stdio()
    argv = sys.argv[1:] if argv is None else list(argv)
    args = _parse_cli_args(argv)
    if args.command == "mcp":
        IRISMCPServer(project_dir=args.project_dir).run()
        return 0
    if args.command == "facility" and args.facility_command == "install":
        return _run_facility(args)
    try:
        if args.command == "init":
            return _run_init(args)
        if args.command == "config":
            return _run_config(args)
        if args.command == "servers":
            return _run_servers(args)
        if args.command == "facility":
            return _run_facility(args)
        if args.command == "audit":
            return _run_audit(args)
        if args.command == "agents":
            return _run_agents(args)
        if args.command == "docs":
            return _run_docs(args)
        if args.command == "suite":
            return _run_suite(args)
        if args.command == "doctor":
            return _run_doctor(args)
        if args.command == "setup":
            return _run_setup(args)
    except (ConfigError, LocalPolicyError, SetupError) as exc:
        sys.stderr.write(t("error.command_failed", command=args.command,
                           detail=_cli_failure_message(exc)) + "\n")
        sys.stderr.flush()
        return 1
    except OSError as exc:
        # Filesystem failures (permissions, read-only share, disk full): bounded, no traceback.
        sys.stderr.write(t("error.command_failed", command=args.command,
                           detail=_cli_failure_message(exc.strerror or type(exc).__name__, 200)) + "\n")
        sys.stderr.flush()
        return 1
    raise LocalPolicyError(t("error.unsupported.command"))


if __name__ == "__main__":
    raise SystemExit(main())
