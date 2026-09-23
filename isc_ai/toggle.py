"""Enable or disable a suite server, through each client's own mechanism.

The three clients express "off" in three different ways, and none of them is a shared
convention:

  opencode  `enabled: false` on the entry
  vscode    no such field exists: absence is the only off, so disabling removes the entry
            and enabling writes it back from the baseline
  claude    per-directory lists in the global file:
            projects.<path>.disabledMcpjsonServers / enabledMcpjsonServers

Hiding that behind one verb is the point of the command. Reporting which mechanism was
used is the honest part: removing an entry is not the same as flipping a flag, and the
caller deserves to know which one happened.
"""

import os
from pathlib import Path

from isc_ai import audit
from isc_ai import config as isc_config
from isc_ai import suite as isc_suite
from isc_ai.setup import (
    ManagedWrite, SetupError, _apply_transaction, _json_bytes, _managed_target, load_json_file,
)


def _canonical(suite, name):
    """Accept the suite name or any of its aliases; answer with the suite name."""
    wanted = audit._normalize_name(name)
    for server_name, spec in suite["servers"].items():
        names = {audit._normalize_name(server_name)}
        names |= {audit._normalize_name(alias) for alias in spec.get("aliases", [])}
        if wanted in names:
            return server_name, spec
    available = ", ".join(sorted(suite["servers"]))
    raise isc_config.ConfigError(f"'{name}' is not a suite server. Available: {available}.")


def _toggle_opencode(path, server_name, spec, enable, changes):
    data, strict, _ = load_json_file(path)
    if not strict:
        return None
    servers = dict(data.get("mcp") or {})
    entry = servers.get(server_name)
    if entry is None:
        changes.append(f"opencode: '{server_name}' is not registered; run 'isc-ai suite --apply' first")
        return None
    updated = dict(entry)
    updated["enabled"] = bool(enable)
    if updated == entry:
        return None
    servers[server_name] = updated
    changes.append(f"opencode: set enabled={str(bool(enable)).lower()} on '{server_name}'")
    return dict(data, mcp=servers)


def _toggle_vscode(path, server_name, spec, enable, changes, environment):
    data, strict, _ = load_json_file(path)
    if not strict:
        return None
    servers = dict(data.get("servers") or {})
    present = server_name in servers
    if enable and not present:
        style = "vscode"
        servers[server_name] = isc_suite.render_entry(spec, style, environment)
        changes.append(f"vscode: added '{server_name}' back (it has no enabled flag, so absence is off)")
    elif not enable and present:
        del servers[server_name]
        changes.append(f"vscode: removed '{server_name}' (it has no enabled flag, so removal is off)")
    else:
        return None
    return dict(data, servers=servers)


def _toggle_claude(path, project, server_name, enable, changes):
    """Claude keeps the per-directory on/off lists in its global file."""
    data, strict, _ = load_json_file(path)
    if not strict or project is None:
        return None
    projects = dict(data.get("projects") or {})
    key = next((raw for raw in projects if audit._same_path(raw, project)), str(project).replace("\\", "/"))
    entry = dict(projects.get(key) or {})
    enabled = [item for item in (entry.get("enabledMcpjsonServers") or []) if item != server_name]
    disabled = [item for item in (entry.get("disabledMcpjsonServers") or []) if item != server_name]
    if enable:
        enabled.append(server_name)
        changes.append(f"claude: listed '{server_name}' as enabled for {key}")
    else:
        disabled.append(server_name)
        changes.append(f"claude: listed '{server_name}' as disabled for {key}")
    entry["enabledMcpjsonServers"] = sorted(set(enabled))
    entry["disabledMcpjsonServers"] = sorted(set(disabled))
    projects[key] = entry
    if projects == (data.get("projects") or {}):
        return None
    return dict(data, projects=projects)


def toggle(server, enable, project_dir=None, home=None, environ=None, suite_path=None,
           clients=None, apply_changes=False):
    """Flip one server in every client that knows it, each through its own mechanism."""
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    project = Path(project_dir).resolve() if project_dir else isc_config.find_project_root(Path.cwd())
    suite = audit.load_suite(suite_path)
    server_name, spec = _canonical(suite, server)
    only = set(clients or ()) or {"opencode", "vscode", "claude"}

    report = {"mode": "apply" if apply_changes else "check", "server": server_name,
              "enabled": bool(enable), "changes": [], "files": []}
    writes = []
    changes = report["changes"]

    for client, spec_client in suite["clients"].items():
        if client.startswith("$") or client not in only:
            continue
        config = spec_client.get("config") or {}
        scope = config.get("write_scope") or "user"
        # Claude's switch lives in its global file, not in the project registration.
        template = config.get("user") if client == "claude" else config.get(scope)
        if not template or (scope == "project" and project is None and client != "claude"):
            continue
        path = audit._resolve_placeholders(template, home, project, environ)
        if not path.is_file():
            continue
        if client == "opencode":
            merged = _toggle_opencode(path, server_name, spec, enable, changes)
        elif client == "vscode":
            values = {}  # a re-added entry carries no settings it did not have
            merged = _toggle_vscode(path, server_name, spec, enable, changes, values)
        else:
            merged = _toggle_claude(path, project, server_name, enable, changes)
        if merged is None:
            report["files"].append({"path": str(path), "client": client, "action": "noop"})
            continue
        report["files"].append({"path": str(path), "client": client, "action": "update"})
        writes.append(ManagedWrite(path.parent, _managed_target(path.parent, path), _json_bytes(merged)))

    if apply_changes and writes:
        _apply_transaction(writes, external_install=False)
        report["written"] = [str(write.target) for write in writes]
    return report
