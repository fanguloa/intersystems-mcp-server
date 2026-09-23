"""Write the recommended MCP suite into the AI clients.

The audit says what drifted; this applies the fix. Two rules shape everything here:

  * the suite normalizes shape, it never invents values. A hostname, a workspace name
    or a URL that already exists is preserved and, when one client has it and another
    does not, it is propagated instead of guessed.
  * a secret is never written as a literal. Each client gets a reference in its own
    syntax, and VS Code gets its native `inputs` entry so the value is prompted once
    and stored by the editor rather than sitting in a settings file.

Everything the suite does not manage is left exactly as it was: unknown fields,
out-of-suite servers and the formatting of files it does not need to touch.
"""

import json
import re
from pathlib import Path, PurePosixPath

from isc_ai import audit
from isc_ai.setup import (
    ManagedWrite, SetupError, _apply_transaction, _json_bytes, _managed_target,
    load_json_file,
)

SECRET_PLACEHOLDER = "<set with: isc-ai secrets set {key} --scope {scope}>"


def _reference(style, name):
    """Render a variable reference in one client's syntax."""
    return style.replace("NAME", name)


def _input_id(key):
    return key.lower().replace("_", "-")


def _known_values(entries, spec):
    """Collect the value each declared setting already has somewhere, plus its aliases.

    This is how a normalization keeps working configurations working: the value stays
    the user's, only its name and its place change.
    """
    declared = spec.get("settings") or {}
    alias_of = {}
    for key, rule in declared.items():
        for alias in rule.get("aliases", []):
            alias_of[alias.upper()] = key
    values = {}
    for entry in entries:
        for raw_key, value in entry.env.items():
            key = alias_of.get(raw_key.upper(), raw_key)
            if key not in declared or not isinstance(value, str) or not value.strip():
                continue
            if audit.REFERENCE.match(value):
                continue  # a reference carries no value to preserve
            values.setdefault(key, value)
    return values


def _environment(spec, client_config, values, findings, server_name, client):
    """Build the env block for one client, plus any VS Code input declarations."""
    style = client_config.get("reference_style") or "${NAME}"
    declared = spec.get("settings") or {}
    environment, inputs = {}, []
    for key, rule in declared.items():
        if rule.get("secret"):
            if style.startswith("${input:"):
                environment[key] = _reference(style, _input_id(key))
                inputs.append({
                    "id": _input_id(key),
                    "type": "promptString",
                    "description": f"{server_name}: {key}",
                    "password": True,
                })
            else:
                environment[key] = _reference("{env:NAME}" if style.startswith("{env:") else "${NAME}", key)
            continue
        value = values.get(key)
        if value is not None:
            environment[key] = value
            continue
        if rule.get("required"):
            findings.append(audit.Finding(
                "VALUE_UNKNOWN", audit.WARNING, client=client, server=server_name,
                message=f"{key} is required and no client has a value for it; the entry is written "
                        f"with a placeholder instead of a guess",
            ))
            environment[key] = SECRET_PLACEHOLDER.format(key=key, scope=rule.get("scope", "global"))
    return environment, inputs


def render_entry(spec, style, environment, previous=None):
    """One MCP registration in the shape a given client expects."""
    launch = spec.get("launch") or {}
    url = launch.get("url")
    result = dict(previous or {})
    # Drop the keys this function owns, so a stale command cannot survive a rewrite.
    for key in ("command", "args", "url", "env", "environment", "type"):
        result.pop(key, None)

    if style == "opencode":
        if url:
            result.update({"type": "remote", "url": url})
        else:
            result.update({"type": "local", "command": [launch.get("command", ""), *launch.get("args", [])]})
            if environment:
                result["environment"] = environment
        result.setdefault("enabled", True)
        return result

    if style == "vscode":
        if url:
            result.update({"type": "http", "url": url})
        else:
            result.update({"type": "stdio", "command": launch.get("command", "")})
            if launch.get("args"):
                result["args"] = list(launch["args"])
            if environment:
                result["env"] = environment
        return result

    if style == "claude":
        if url:
            result.update({"type": "http", "url": url})
        else:
            result["command"] = launch.get("command", "")
            if launch.get("args"):
                result["args"] = list(launch["args"])
            if environment:
                result["env"] = environment
        return result

    raise SetupError(f"unknown client entry style: {style}")


def _plan_client(name, spec_client, path, data, suite, entries, all_entries, findings, only_servers):
    """Return (new_data, changes) for one client configuration file."""
    config = spec_client.get("config") or {}
    key = config.get("mcp_key") or "mcpServers"
    style = config.get("entry_style") or name
    servers = dict(data.get(key) or {})
    inputs = list(data.get("inputs") or [])
    changes = []

    for server_name, spec in suite["servers"].items():
        if only_servers and server_name not in only_servers:
            continue
        canonical = audit._normalize_name(server_name)
        aliases = {audit._normalize_name(alias) for alias in spec.get("aliases", [])}
        wanted = aliases | {canonical}
        matching = [item for item in servers if audit._normalize_name(item) in wanted]
        # This client's own values win; another client's value is better than a guess.
        mine = [entry for entry in entries if audit._normalize_name(entry.name) in wanted]
        others = [entry for entry in all_entries
                  if entry.client != name and audit._normalize_name(entry.name) in wanted]
        values = _known_values(mine + others, spec)
        environment, declarations = _environment(spec, config, values, findings, server_name, name)

        previous = servers.get(server_name)
        for other in matching:
            if other != server_name and previous is None:
                previous = servers[other]  # carry over unknown fields from the alias
        rendered = render_entry(spec, style, environment, previous)

        renamed = [other for other in matching if other != server_name]
        existed = server_name in servers
        for other in renamed:
            del servers[other]
        if servers.get(server_name) != rendered:
            servers[server_name] = rendered
            if renamed:
                changes.append(f"{name}: renamed '{renamed[0]}' to '{server_name}'")
            else:
                changes.append(f"{name}: {'updated' if existed else 'added'} '{server_name}'")
        elif renamed:
            changes.append(f"{name}: removed duplicate '{renamed[0]}' of '{server_name}'")

        for declaration in declarations:
            if not any(item.get("id") == declaration["id"] for item in inputs if isinstance(item, dict)):
                inputs.append(declaration)
                changes.append(f"{name}: declared secret input '{declaration['id']}'")

    if not changes:
        return None, []
    result = dict(data)
    result[key] = servers
    if inputs:
        result["inputs"] = inputs
    return result, changes


def plan(project_dir=None, home=None, environ=None, suite_path=None, clients=None, servers=None):
    """Compute what would change in every client configuration. Writes nothing."""
    import os

    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    if project_dir is not None:
        project = Path(project_dir).resolve()
    else:
        # Same discovery as the rest of the CLI: nearest ancestor with .iris-mcp/.
        from isc_ai import config as isc_config
        project = isc_config.find_project_root(Path.cwd())
    suite = audit.load_suite(suite_path)
    client_records, entries, findings = audit.read_clients(suite, home, project, environ)

    only_clients = set(clients or ())
    only_servers = set(servers or ())
    report = {"mode": "check", "changes": [], "files": [], "findings": [], "skipped": []}
    writes = []

    for name, spec_client in suite["clients"].items():
        if name.startswith("$") or (only_clients and name not in only_clients):
            continue
        config = spec_client.get("config") or {}
        # One scope per client, declared in the baseline: writing two scopes would
        # register the same servers twice and the client would run both.
        scope = config.get("write_scope") or "user"
        template = config.get(scope)
        if not template:
            report["skipped"].append(f"{name}: no {scope} configuration path is declared")
            continue
        if scope == "project" and project is None:
            report["skipped"].append(f"{name}: writes the project file and no project was resolved")
            continue
        path = audit._resolve_placeholders(template, home, project, environ)
        data, strict, _ = load_json_file(path)
        if not strict:
            report["skipped"].append(f"{path} is JSONC; it is preserved byte for byte")
            continue
        scoped = [entry for entry in entries if entry.client == name]
        merged, changes = _plan_client(name, spec_client, path, data, suite, scoped,
                                       entries, findings, only_servers)
        if not changes:
            report["files"].append({"path": str(path), "action": "noop"})
            continue
        report["files"].append({"path": str(path), "action": "update" if path.exists() else "create"})
        report["changes"].extend(changes)
        writes.append(ManagedWrite(path.parent, _managed_target(path.parent, path), _json_bytes(merged)))

    report["findings"] = [finding.as_dict() for finding in findings
                          if finding.code in {"VALUE_UNKNOWN", "CONFIG_AMBIGUOUS"}]
    return report, writes


def prune_claude_global(home=None, environ=None, suite_path=None, apply_changes=False):
    """Clean the Claude global file without rewriting it as a suite registration.

    That file is owned and actively written by Claude Code, and it merges with the
    project `.mcp.json`, so registering the suite there would run some servers twice.
    Only three narrow repairs happen:

      * a per-directory override that points at a path which no longer exists is
        removed, because it can only fail
      * a global entry registered under an alias is renamed to the suite name, and its
        settings are renamed through the declared aliases with their values preserved
      * a literal secret in a global entry becomes a reference

    Every other key of the file is carried through untouched, and the write is atomic
    with rollback. It refuses to run while Claude Code is up, because the client writes
    this file too and the last writer would win.
    """
    import os
    import shutil
    import subprocess

    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    suite = audit.load_suite(suite_path)
    path = audit._resolve_placeholders(
        (suite["clients"]["claude"].get("config") or {})["user"], home, None, environ)
    report = {"path": str(path), "mode": "apply" if apply_changes else "check",
              "changes": [], "written": False}
    if not path.is_file():
        report["changes"].append("no global Claude file")
        return report

    data, strict, _ = load_json_file(path)
    if not strict:
        report["changes"].append("file is JSONC; left untouched")
        return report

    style = (suite["clients"]["claude"].get("config") or {}).get("reference_style") or "${NAME}"
    canonical = {}
    for name, spec in suite["servers"].items():
        canonical[audit._normalize_name(name)] = name
        for alias in spec.get("aliases", []):
            canonical[audit._normalize_name(alias)] = name

    result = dict(data)

    projects = {}
    for raw_path, config in (data.get("projects") or {}).items():
        entry = dict(config)
        servers = dict(entry.get("mcpServers") or {})
        for server_name in list(servers):
            if audit._normalize_name(server_name) not in canonical:
                continue
            probe = audit.Entry(client="claude", scope="user", path=path, name=server_name,
                                command=servers[server_name].get("command", ""),
                                args=tuple(servers[server_name].get("args") or []))
            if audit._missing_paths(probe):
                del servers[server_name]
                report["changes"].append(
                    f"removed dead override '{server_name}' under {raw_path}")
        if servers:
            entry["mcpServers"] = servers
        else:
            entry.pop("mcpServers", None)
        projects[raw_path] = entry
    if projects:
        result["projects"] = projects

    servers = dict(data.get("mcpServers") or {})
    for server_name in list(servers):
        target = canonical.get(audit._normalize_name(server_name))
        if target is None:
            continue
        spec = suite["servers"][target]
        entry = dict(servers[server_name])
        declared = spec.get("settings") or {}
        alias_of = {alias.upper(): key for key, rule in declared.items()
                    for alias in rule.get("aliases", [])}
        environment = {}
        for raw_key, value in (entry.get("env") or {}).items():
            key = alias_of.get(raw_key.upper(), raw_key)
            rule = declared.get(key) or {}
            if rule.get("secret") and not audit.REFERENCE.match(str(value)):
                environment[key] = _reference(style, key)
                report["changes"].append(f"replaced the literal {key} of '{server_name}' with a reference")
            else:
                environment[key] = value
            if key != raw_key:
                report["changes"].append(f"renamed setting {raw_key} to {key} in '{server_name}'")
        if environment:
            entry["env"] = environment
        if target != server_name:
            del servers[server_name]
            report["changes"].append(f"renamed global entry '{server_name}' to '{target}'")
        servers[target] = entry
    if servers:
        result["mcpServers"] = servers

    if not report["changes"]:
        return report
    if not apply_changes:
        return report

    binary = (suite["clients"]["claude"].get("binary") or "claude")
    if shutil.which(binary):
        try:
            running = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {binary}.exe", "/NH"],
                capture_output=True, text=True, timeout=20,
            ).stdout if os.name == "nt" else ""
        except (OSError, subprocess.SubprocessError):
            running = ""
        if f"{binary}.exe" in running.lower():
            raise SetupError(
                f"{binary} is running and owns this file; close it before pruning so the "
                f"client does not overwrite the repair")

    _apply_transaction([ManagedWrite(path.parent, _managed_target(path.parent, path),
                                     _json_bytes(result))], external_install=False)
    report["written"] = True
    return report


def apply(project_dir=None, home=None, environ=None, suite_path=None, clients=None, servers=None):
    """Apply the plan atomically: every file or none."""
    report, writes = plan(project_dir=project_dir, home=home, environ=environ,
                          suite_path=suite_path, clients=clients, servers=servers)
    report["mode"] = "apply"
    if writes:
        _apply_transaction(writes, external_install=False)
    report["written"] = [str(write.target) for write in writes]
    return report
