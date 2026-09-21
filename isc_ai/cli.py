"""isc-ai command line.

    isc-ai mcp                      run the MCP server over stdio (what the client launches)
    isc-ai init [DIR]               create <project>/.iris-mcp/config.json from the packaged template
    isc-ai config <sub>             show | set | unset | add-server | remove-server | validate
    isc-ai servers migrate          copy legacy ~/.iris_mcp_servers.json into ~/.iris-mcp/servers.json
    isc-ai facility render|install  render / conditionally install MCP.CCRMutation for this site
    isc-ai doctor                   what project, files and servers this process would use

The CLI only edits configuration files; only `mcp` and `facility install` talk to IRIS.
Every command that resolves a project accepts `--project-dir` and otherwise searches
upward from the current directory for `.iris-mcp/config.json`.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from isc_ai import __version__
from isc_ai import config as isc_config
from isc_ai.ccr_discovery import LocalPolicyError
from isc_ai.config import ConfigError
from isc_ai.server import IRISMCPServer, _CCR_MUTATION_CLASS, _cli_failure_message, _load_ccr_mutation_resource

PROG = "isc-ai"


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def _ccr_cli_value(value):
    """Shape check only; the site format (site.ccr_id) is enforced by the server."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value or ""):
        raise argparse.ArgumentTypeError("must be a CCR identifier (letters, digits, '_' or '-')")
    return value


def _sha256_cli_value(value):
    if not re.fullmatch(r"[0-9a-f]{64}", value or ""):
        raise argparse.ArgumentTypeError("must be a lowercase 64-character SHA-256")
    return value


def _add_project_dir(parser):
    parser.add_argument("--project-dir", help="project directory (default: nearest ancestor of cwd with .iris-mcp/, or IRIS_MCP_PROJECT_DIR)")


def _parse_cli_args(argv):
    parser = argparse.ArgumentParser(prog=PROG, description="InterSystems IRIS/TrakCare MCP server and project configuration.")
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_mcp = sub.add_parser("mcp", help="run the MCP server over stdio")
    _add_project_dir(p_mcp)

    p_init = sub.add_parser("init", help="create .iris-mcp/config.json from the packaged template (idempotent)")
    p_init.add_argument("directory", nargs="?", help="project directory (default: cwd)")
    p_init.add_argument("--from", dest="template", help="alternative template file")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing project configuration")
    p_init.add_argument("--no-servers", action="store_true", help="omit the example servers section")

    p_config = sub.add_parser("config", help="inspect and edit configuration files")
    _add_project_dir(p_config)
    config_sub = p_config.add_subparsers(dest="config_command")

    p_show = config_sub.add_parser("show", help="print the effective configuration (secrets redacted)")
    p_show.add_argument("--reveal", action="store_true", help="print literal secrets instead of redacting")
    p_show.add_argument("--servers", action="store_true", help="print merged servers instead of the project policy")

    p_set = config_sub.add_parser("set", help="set a project setting by dotted path, e.g. canonical_base.host")
    p_set.add_argument("path")
    p_set.add_argument("value", help="JSON when parseable (numbers, booleans, lists, objects), otherwise a string")

    p_unset = config_sub.add_parser("unset", help="remove a project setting by dotted path")
    p_unset.add_argument("path")

    p_add = config_sub.add_parser("add-server", help="add or replace a server entry")
    p_add.add_argument("server_id")
    p_add.add_argument("--host", required=True)
    p_add.add_argument("--port", type=int, default=80)
    p_add.add_argument("--path-prefix", default="")
    p_add.add_argument("--namespace", default="USER")
    p_add.add_argument("--role", default="", help="env_role, e.g. BASE, UAT, LIVE")
    p_add.add_argument("--production", action="store_true", help="mark as is_production=true")
    p_add.add_argument("--username", default="{env:IRIS_USER}", help="literal or {env:VAR} reference")
    p_add.add_argument("--password", default="{env:IRIS_PASSWORD}", help="literal or {env:VAR} reference")
    p_add.add_argument("--upload-token", help="literal or {env:VAR} reference for the CCR upload token")
    p_add.add_argument("--ccr-id", help="default CCR identifier for this server")
    p_add.add_argument("--proxy")
    p_add.add_argument("--description")
    p_add.add_argument("--user", action="store_true", help="write to ~/.iris-mcp/servers.json instead of the project file")

    p_remove = config_sub.add_parser("remove-server", help="remove a server entry")
    p_remove.add_argument("server_id")
    p_remove.add_argument("--user", action="store_true", help="remove from ~/.iris-mcp/servers.json instead of the project file")

    config_sub.add_parser("validate", help="load and validate the effective configuration")

    p_servers = sub.add_parser("servers", help="user-level server connections")
    servers_sub = p_servers.add_subparsers(dest="servers_command")
    p_migrate = servers_sub.add_parser("migrate", help="copy legacy ~/.iris_mcp_servers.json into ~/.iris-mcp/servers.json")
    p_migrate.add_argument("--force", action="store_true")

    p_facility = sub.add_parser("facility", help="MCP.CCRMutation facility for the configured site")
    _add_project_dir(p_facility)
    facility_sub = p_facility.add_subparsers(dest="facility_command")
    p_render = facility_sub.add_parser("render", help="render the facility for this site and print its SHA-256")
    p_render.add_argument("--output", help="write the rendered class to this path")
    p_install = facility_sub.add_parser("install", help="conditionally install/upgrade the facility on the canonical BASE")
    p_install.add_argument("--server", required=True)
    p_install.add_argument("--namespace", required=True)
    p_install.add_argument("--ccr", required=True, type=_ccr_cli_value, help="CCR identifier in the configured site format (site.ccr_id)")
    p_install.add_argument("--upgrade", action="store_true")
    p_install.add_argument("--expected-current-sha256", type=_sha256_cli_value)

    p_doctor = sub.add_parser("doctor", help="show the project, files and servers this process resolves")
    _add_project_dir(p_doctor)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("a command is required (try: isc-ai doctor)")
    if args.command == "config" and not args.config_command:
        p_config.error("a config sub-command is required")
    if args.command == "servers" and not args.servers_command:
        p_servers.error("a servers sub-command is required")
    if args.command == "facility":
        if not args.facility_command:
            p_facility.error("a facility sub-command is required")
        if args.facility_command == "install":
            if args.upgrade and not args.expected_current_sha256:
                parser.error("--upgrade requires --expected-current-sha256")
            if not args.upgrade and args.expected_current_sha256:
                parser.error("--expected-current-sha256 requires --upgrade")
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


def _project_paths(args):
    explicit = getattr(args, "project_dir", None)
    project_dir = Path(explicit).resolve() if explicit else isc_config.project_directory()
    return project_dir, isc_config.project_config_path(project_dir)


def _load_project_data(config_path):
    if not config_path.is_file():
        raise ConfigError(f"No project configuration at {config_path}; run 'isc-ai init' first.")
    data = isc_config.read_json(config_path)
    if not isinstance(data, dict):
        raise ConfigError(f"{config_path} must contain a JSON object.")
    return data


def _servers_file_for(args, config_path):
    if getattr(args, "user", False):
        path = isc_config.user_servers_path()
        data = isc_config.read_json(path) if path.is_file() else {"servers": {}}
        if not isinstance(data, dict) or not isinstance(data.get("servers", {}), dict):
            raise ConfigError(f"{path} must contain a 'servers' object.")
        data.setdefault("servers", {})
        return path, data
    data = _load_project_data(config_path)
    servers = data.setdefault("servers", {})
    if not isinstance(servers, dict):
        raise ConfigError("servers must be an object keyed by server id.")
    return config_path, data


def _reject_literal_project_secrets(data):
    """The project file is committable: secrets must be {env:VAR} references."""
    literals = isc_config.literal_secret_paths(data.get("servers", {}))
    if literals:
        raise ConfigError(
            "Literal secrets are not allowed in the project configuration "
            f"({', '.join(literals)}); use {{env:VAR}} references or 'add-server --user'."
        )


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def _run_init(args):
    directory = Path(args.directory).resolve() if args.directory else Path.cwd().resolve()
    config_path = directory / isc_config.PROJECT_CONFIG_RELATIVE
    if config_path.exists() and not args.force:
        _emit(f"{config_path} already exists; nothing changed (use --force to regenerate from the template).")
        return 0
    if not directory.is_dir():
        raise ConfigError(f"{directory} is not an existing directory; create the deployment folder first.")
    ancestor = isc_config.find_project_root(directory.parent) if directory.parent != directory else None
    template = isc_config.load_template(args.template)
    if args.no_servers:
        template.pop("servers", None)
    isc_config.write_json(config_path, template)
    _emit(f"Created {config_path} from template. Edit it with 'isc-ai config set' or your editor; "
          "keep secrets as {env:VAR} references.")
    if ancestor is not None:
        sys.stderr.write(f"warning: an ancestor project configuration exists at {ancestor}; "
                         f"this new file shadows it for everything under {directory}.\n")
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
            sys.stderr.write(f"note: {note}\n")
        return 0

    if command == "set":
        data = _load_project_data(config_path)
        current = isc_config.get_path(data, args.path)
        value = isc_config.coerce_value(args.value, current)
        isc_config.set_path(data, args.path, value)
        isc_config.parse_policy(data, project_dir)
        _reject_literal_project_secrets(data)
        isc_config.write_json(config_path, data)
        secret = args.path.split(".")[-1] in isc_config.SECRET_KEYS
        _emit(f"{args.path} = {'<set>' if secret else json.dumps(value, ensure_ascii=False)}")
        return 0

    if command == "unset":
        data = _load_project_data(config_path)
        if not isc_config.unset_path(data, args.path):
            raise ConfigError(f"{args.path} is not set.")
        isc_config.parse_policy(data, project_dir)
        isc_config.write_json(config_path, data)
        _emit(f"removed {args.path}")
        return 0

    if command == "add-server":
        path, data = _servers_file_for(args, config_path)
        role = args.role.strip().upper()
        if role and not re.fullmatch(r"[A-Z][A-Z0-9_]*", role):
            raise ConfigError("--role must be a plain identifier (letters, digits, '_').")
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
        _emit(f"server '{args.server_id}' written to {path}")
        return 0

    if command == "remove-server":
        path, data = _servers_file_for(args, config_path)
        if args.server_id not in data["servers"]:
            raise ConfigError(f"server '{args.server_id}' is not defined in {path}.")
        del data["servers"][args.server_id]
        isc_config.write_json(path, data)
        _emit(f"server '{args.server_id}' removed from {path}")
        return 0

    if command == "validate":
        summary, problems = _validate(project_dir)
        _emit(json.dumps(summary, indent=2, ensure_ascii=False))
        return 1 if problems else 0

    raise ConfigError("Unsupported config command.")


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
        for name in (isc_config.PROJECT_DIR_ENV, isc_config.PROJECT_CONFIG_ENV, isc_config.EXTRA_PACKAGES_ENV, "IRIS_MCP_TIMEOUT")
        if os.environ.get(name) is not None
    }
    summary["version"] = __version__
    _emit(json.dumps(summary, indent=2, ensure_ascii=False))
    if not summary["project_config_present"]:
        sys.stderr.write("hint: no project configuration found; run 'isc-ai init' in the deployment directory.\n")
    elif resolved == Path.home().resolve():
        sys.stderr.write("warning: the governing project configuration is in your HOME directory; every "
                         "deployment without its own .iris-mcp/config.json inherits it. Remove it unless intended.\n")
    elif summary["project_source"].startswith("ancestor"):
        sys.stderr.write(f"note: policy inherited from ancestor {resolved}.\n")
    return 1 if problems else 0


def _run_servers(args):
    if args.servers_command == "migrate":
        legacy = isc_config.legacy_user_servers_path()
        target = isc_config.user_servers_path()
        if not legacy.is_file():
            raise ConfigError(f"No legacy file at {legacy}.")
        if target.exists() and not args.force:
            raise ConfigError(f"{target} already exists; use --force to overwrite.")
        data = isc_config.read_json(legacy)
        if not isinstance(data, dict) or not isinstance(data.get("servers"), dict):
            raise ConfigError(f"{legacy} must contain a 'servers' object.")
        isc_config.write_json(target, {"servers": data["servers"]})
        _emit(f"Copied {len(data['servers'])} servers to {target}. Remove {legacy} once verified; "
              "the legacy file is still read as a fallback.")
        return 0
    raise ConfigError("Unsupported servers command.")


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
            sys.stderr.write(f"Initializer failed: {_cli_failure_message(exc)}\n")
            sys.stderr.flush()
            return 1
        except Exception as exc:  # noqa: BLE001 - bounded, redacted CLI boundary
            # Unexpected failure: never print a traceback or exception text, which may
            # carry paths, credentials or server details. Outcome is unverified.
            sys.stderr.write(
                "Initializer failed: unexpected "
                f"{_cli_failure_message(type(exc).__name__, 64)}; details withheld. "
                "Outcome is unverified; inspect the target manually before any retry.\n"
            )
            sys.stderr.flush()
            return 1
    raise ConfigError("Unsupported facility command.")


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
        if args.command == "doctor":
            return _run_doctor(args)
    except (ConfigError, LocalPolicyError) as exc:
        sys.stderr.write(f"{args.command} failed: {_cli_failure_message(exc)}\n")
        sys.stderr.flush()
        return 1
    except OSError as exc:
        # Filesystem failures (permissions, read-only share, disk full): bounded, no traceback.
        sys.stderr.write(f"{args.command} failed: {_cli_failure_message(exc.strerror or type(exc).__name__, 200)}\n")
        sys.stderr.flush()
        return 1
    raise LocalPolicyError("Unsupported command.")


if __name__ == "__main__":
    raise SystemExit(main())
