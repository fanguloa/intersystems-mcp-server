"""Move legacy config.json / servers.json keys into isc-ai.rules.json.

Migrates routes.uat_write_exceptions, routes.live_config_window,
runtime.runner_hosts, packages, and per-server production_read flags (from the
project config.json's own "servers" section and the user-level servers.json)
into <project>/isc-ai.rules.json, plus the explicit Custom.{SITE}.* baseline
package grant (never implicit).

Same discipline as isc_ai.migrate: plan() never writes; apply() refuses ALL
writes if the plan would conflict with an existing hand-written rules-file
entry (all-or-nothing), and backs up (copy2 + filecmp verify) every file it is
about to change before writing.

Legacy exceptions carry no expiry; a migrated exception that has none gets
`expires` = today + 365 days so nothing silently becomes permanent, and the
plan lists which entries got this default.
"""

from __future__ import annotations

import datetime
import filecmp
import shutil
from pathlib import Path

from isc_ai import config as config_module
from isc_ai import rules as rules_module

DEFAULT_EXPIRY_DAYS = 365


def _default_expiry(today):
    return (today + datetime.timedelta(days=DEFAULT_EXPIRY_DAYS)).isoformat()


def _signature(item):
    """Same shape as config._exception_signature, applied to a plain dict."""
    return (
        item.get("capability"),
        tuple(sorted(item.get("roles") or ())),
        tuple(sorted(item.get("servers") or ())),
        tuple(sorted(item.get("hosts") or ())),
        tuple(sorted(item.get("namespaces") or ())),
        tuple(sorted(item.get("path_prefixes") or ())),
    )


def _existing_signature(exc):
    return (
        exc.capability,
        tuple(sorted(exc.roles or ())),
        tuple(sorted(exc.servers or ())),
        tuple(sorted(exc.hosts or ())),
        tuple(sorted(exc.namespaces or ())),
        tuple(sorted(exc.path_prefixes or ())),
    )


def _code_write_items(policy, today, defaulted):
    items = []
    for index, exc in enumerate(policy.uat_write_exceptions):
        item = {
            "id": f"migrated-code-write-{index}", "label": exc.label, "ticket": "TICKET-PENDING",
            "expires": _default_expiry(today), "capability": "code_write",
            "roles": ["UAT"], "namespaces": [exc.namespace],
            "path_prefixes": sorted(exc.path_prefixes),
            "requires_active_node": exc.requires_active_node,
        }
        if exc.hosts is not None:
            item["hosts"] = sorted(exc.hosts)
        defaulted.append(item["id"])
        items.append(item)
    return items


def _live_window_items(policy):
    window = policy.live_config_window
    if window is None:
        return []
    return [{
        "id": "migrated-live-config-window", "label": window.label, "ticket": window.ticket,
        "expires": window.expires, "capability": "ensemble_control",
        "namespaces": sorted(window.namespaces), "hosts": sorted(window.hosts),
        "active_node_evidence_namespace": window.active_node_evidence_namespace,
    }]


def _runner_items(policy, today, defaulted):
    items = []
    for entry in policy.runner_hosts:
        item = {
            "id": f"migrated-runner-{entry.namespace}", "label": f"runner_hosts:{entry.namespace}",
            "ticket": "TICKET-PENDING", "expires": _default_expiry(today), "capability": "runner",
            "roles": sorted(entry.roles), "namespaces": [entry.namespace],
            "runner_namespace": entry.runner_namespace,
        }
        if entry.hosts is not None:
            item["hosts"] = sorted(entry.hosts)
        defaulted.append(item["id"])
        items.append(item)
    return items


def _find_production_read(project_data, user_data):
    """[(server_id, source)] for every production_read: true flag, source in
    ('project', 'user'). Both arguments are the RAW loaded JSON, not the merged
    runtime view, so the caller knows exactly which file to edit and back up."""
    found = []
    for source, data in (("project", project_data), ("user", user_data)):
        servers = data.get("servers", {}) if isinstance(data, dict) else {}
        if not isinstance(servers, dict):
            continue
        for server_id, entry in sorted(servers.items()):
            if isinstance(entry, dict) and entry.get("production_read") is True:
                found.append((server_id, source))
    return found


def _read_items(found, today, defaulted):
    items = []
    for server_id, _source in found:
        item = {
            "id": f"migrated-production-read-{server_id}", "label": f"production_read:{server_id}",
            "ticket": "TICKET-PENDING", "expires": _default_expiry(today), "capability": "read",
            "servers": [server_id],
        }
        defaulted.append(item["id"])
        items.append(item)
    return items


def _package_items(policy):
    packages = {}
    for namespace, grants in policy.packages.items():
        rendered = []
        for grant in grants:
            item = {}
            if grant.prefix is not None:
                item["prefix"] = grant.prefix
            elif grant.class_name is not None:
                item["class"] = grant.class_name
            else:
                item["document"] = grant.document
            if grant.roles is not None:
                item["roles"] = sorted(grant.roles)
            rendered.append(item)
        if rendered:
            packages[namespace] = rendered
    return packages


def _backup(path):
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = path.with_name(f"{path.name}.{stamp}.bak")
    shutil.copy2(path, backup_path)
    if not filecmp.cmp(path, backup_path, shallow=False):
        raise RuntimeError(f"Backup verification failed for {path}.")
    return backup_path


def plan(project_dir, home=None, today=None):
    """What a migration would do, without touching anything."""
    project_dir = Path(project_dir).resolve()
    config_path = config_module.project_config_path(project_dir, environ={})
    rules_path = project_dir / rules_module.RULES_FILENAME
    report = {
        "mode": "check", "project_dir": str(project_dir), "config_path": str(config_path),
        "rules_path": str(rules_path), "rules_file_exists": rules_path.is_file(),
        "exceptions": [], "packages": {}, "conflicts": [], "notes": [],
        "expiry_defaulted": [], "files_to_backup": [],
    }
    if not config_path.is_file():
        report["notes"].append(f"No project configuration at {config_path}; nothing to migrate.")
        return report

    project_data = config_module.read_json(config_path)
    policy = config_module.parse_policy(project_data, project_dir)
    today = today or datetime.date.today()

    defaulted = []
    exceptions = []
    exceptions += _code_write_items(policy, today, defaulted)
    exceptions += _live_window_items(policy)
    exceptions += _runner_items(policy, today, defaulted)

    user_servers_path = config_module.user_servers_path(home) if home is not None else None
    user_data = {}
    if user_servers_path is not None and user_servers_path.is_file():
        user_data = config_module.read_json(user_servers_path)
    found_reads = _find_production_read(project_data, user_data)
    exceptions += _read_items(found_reads, today, defaulted)

    packages = _package_items(policy)
    if policy.ccr_configured:
        baseline_namespace = policy.canonical_base.namespace if policy.canonical_base is not None else None
        if baseline_namespace is not None:
            baseline = {"prefix": f"Custom.{policy.ccr_prefix}"}
            existing = packages.setdefault(baseline_namespace, [])
            if baseline not in existing:
                existing.insert(0, baseline)

    existing_ruleset = rules_module.load_rules(project_dir, site_prefix=policy.ccr_prefix)
    existing_signatures = {_existing_signature(exc) for exc in (existing_ruleset.exceptions if existing_ruleset else ())}
    conflicts = [item for item in exceptions if _signature(item) in existing_signatures]

    files_to_backup = []
    if exceptions or packages:
        files_to_backup.append(str(config_path))
    if any(source == "user" for _sid, source in found_reads) and user_servers_path is not None:
        files_to_backup.append(str(user_servers_path))

    report.update({
        "exceptions": exceptions, "packages": packages, "conflicts": conflicts,
        "expiry_defaulted": defaulted, "files_to_backup": files_to_backup,
    })
    if defaulted:
        report["notes"].append(
            f"{len(defaulted)} migrated exception(s) had no expiry in the legacy configuration; "
            f"they will expire {_default_expiry(today)} (today + {DEFAULT_EXPIRY_DAYS} days).")
    if conflicts:
        report["notes"].append(
            "Conflicts with existing isc-ai.rules.json entries; apply will refuse all writes.")
    return report


def apply(project_dir, home=None, today=None):
    """Perform the migration. Refuses entirely if any conflict is present."""
    report = plan(project_dir, home=home, today=today)
    report["mode"] = "apply"
    if report["conflicts"] or not (report["exceptions"] or report["packages"]):
        report["performed"] = []
        return report

    project_dir = Path(report["project_dir"])
    config_path = Path(report["config_path"])
    rules_path = Path(report["rules_path"])
    performed = []

    existing_data = config_module.read_json(rules_path) if rules_path.is_file() else {}
    if rules_path.is_file():
        backup_path = _backup(rules_path)
        performed.append({"action": "backed-up", "file": str(rules_path), "backup": str(backup_path)})
    merged_packages = dict(existing_data.get("packages", {}))
    for namespace, grants in report["packages"].items():
        current = list(merged_packages.get(namespace, []))
        merged_packages[namespace] = current + [grant for grant in grants if grant not in current]
    new_rules_data = dict(existing_data)
    new_rules_data["version"] = rules_module.RULES_VERSION
    new_rules_data["exceptions"] = list(existing_data.get("exceptions", [])) + report["exceptions"]
    new_rules_data["packages"] = merged_packages
    config_module.write_json(rules_path, new_rules_data)
    performed.append({"action": "wrote", "file": str(rules_path)})

    project_data = config_module.read_json(config_path)
    config_backup = _backup(config_path)
    performed.append({"action": "backed-up", "file": str(config_path), "backup": str(config_backup)})
    routes = dict(project_data.get("routes", {}))
    routes.pop("uat_write_exceptions", None)
    routes.pop("live_config_window", None)
    if routes:
        project_data["routes"] = routes
    else:
        project_data.pop("routes", None)
    runtime = dict(project_data.get("runtime", {}))
    runtime.pop("runner_hosts", None)
    if runtime:
        project_data["runtime"] = runtime
    else:
        project_data.pop("runtime", None)
    project_data.pop("packages", None)

    user_servers_path = config_module.user_servers_path(home) if home is not None else None
    user_data = {}
    if user_servers_path is not None and user_servers_path.is_file():
        user_data = config_module.read_json(user_servers_path)
    found_reads = _find_production_read(project_data, user_data)
    project_servers = dict(project_data.get("servers", {}))
    for server_id, source in found_reads:
        if source == "project" and isinstance(project_servers.get(server_id), dict):
            project_servers[server_id] = {key: value for key, value in project_servers[server_id].items()
                                          if key != "production_read"}
    if project_servers:
        project_data["servers"] = project_servers
    else:
        project_data.pop("servers", None)
    config_module.write_json(config_path, project_data)
    performed.append({"action": "wrote", "file": str(config_path)})

    if user_servers_path is not None and any(source == "user" for _sid, source in found_reads):
        user_backup = _backup(user_servers_path)
        performed.append({"action": "backed-up", "file": str(user_servers_path), "backup": str(user_backup)})
        user_servers = dict(user_data.get("servers", {}))
        for server_id, source in found_reads:
            if source == "user" and isinstance(user_servers.get(server_id), dict):
                user_servers[server_id] = {key: value for key, value in user_servers[server_id].items()
                                           if key != "production_read"}
        user_data = dict(user_data)
        user_data["servers"] = user_servers
        config_module.write_json(user_servers_path, user_data)
        performed.append({"action": "wrote", "file": str(user_servers_path)})

    report["performed"] = performed
    return report
