"""Write orchestration for isc-ai.rules.json: add/renew/remove exceptions,
all going through one plan-by-default / --apply gate (mirrors the
migrate.py/rules_migrate.py convention already used elsewhere in this
toolchain), plus deferred ("widen") plans that a human applies later with
'isc-ai rules apply <fingerprint>'.

That deferred path is the ONLY route the iris_rules MCP tool has for a
widening action (add, an expiry extension, or anything touching a
production-flagged scope): it saves a plan here and returns the fingerprint;
it never writes isc-ai.rules.json itself for those actions. 'Narrowing'
(remove, or a renew that SHORTENS an expiry) is exactly the opposite of what
a plan-then-apply gate is for -- both the CLI and the MCP tool may apply it
directly (still backed up).

Every function here validates the resulting content with
isc_ai.rules.parse_rules before it is ever persisted (as a plan or as the
live file), so a bad edit fails loudly before it reaches disk.
"""

from __future__ import annotations

import filecmp
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from isc_ai import config as config_module
from isc_ai import rules as rules_module

PLANS_DIRNAME = "rules-plans"
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{16}")


def rules_path_for(project_dir):
    return Path(project_dir) / rules_module.RULES_FILENAME


def plans_dir(project_dir):
    return Path(project_dir) / config_module.STATE_DIR / PLANS_DIRNAME


def _read_rules_data(project_dir):
    path = rules_path_for(project_dir)
    if not path.is_file():
        return {"version": rules_module.RULES_VERSION, "packages": {}, "exceptions": []}
    return config_module.read_json(path)


def _backup(path):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = path.with_name(f"{path.name}.{stamp}.bak")
    shutil.copy2(path, backup_path)
    if not filecmp.cmp(path, backup_path, shallow=False):
        raise RuntimeError(f"Backup verification failed for {path}.")
    return backup_path


def fingerprint(plan):
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def save_plan(project_dir, plan):
    fp = fingerprint(plan)
    directory = plans_dir(project_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_module.write_json(directory / f"{fp}.json", plan)
    return fp


def load_plan(project_dir, fp):
    if not isinstance(fp, str) or not _FINGERPRINT_RE.fullmatch(fp):
        return None
    path = plans_dir(project_dir) / f"{fp}.json"
    if not path.is_file():
        return None
    return config_module.read_json(path)


def _write_rules_data(project_dir, new_data):
    path = rules_path_for(project_dir)
    if path.is_file():
        _backup(path)
    config_module.write_json(path, new_data)


def execute_or_plan(project_dir, action, description, new_data, apply):
    """The shared plan-by-default / --apply gate every write goes through.

    Returns (executed, fingerprint_or_none, plan).
    """
    base_data = _read_rules_data(project_dir)
    plan = {"action": action, "description": description, "base": base_data, "new": new_data}
    if not apply:
        fp = save_plan(project_dir, plan)
        return False, fp, plan
    _write_rules_data(project_dir, new_data)
    return True, None, plan


def apply_pending_plan(project_dir, fp):
    """isc-ai rules apply <fingerprint>: refuses on drift or an unknown plan."""
    plan = load_plan(project_dir, fp)
    if plan is None:
        return False, "unknown_or_stale_fingerprint"
    current = _read_rules_data(project_dir)
    if current != plan["base"]:
        return False, "drift"
    _write_rules_data(project_dir, plan["new"])
    return True, None


# ---------------------------------------------------------------------------
# Dict-level mutations (operate on the raw isc-ai.rules.json content)
# ---------------------------------------------------------------------------

def add_exception(project_dir, item, apply):
    base_data = _read_rules_data(project_dir)
    existing_ids = {existing.get("id") for existing in base_data.get("exceptions", [])}
    if item.get("id") in existing_ids:
        raise ValueError(f"An exception with id '{item['id']}' already exists.")
    new_data = dict(base_data)
    new_data["exceptions"] = list(base_data.get("exceptions", [])) + [item]
    new_data["version"] = rules_module.RULES_VERSION
    rules_module.parse_rules(new_data)
    return execute_or_plan(project_dir, "add", f"add exception '{item.get('id')}'", new_data, apply)


def _with_renewed_expiry(base_data, target_id, expires):
    new_exceptions = []
    found = False
    for item in base_data.get("exceptions", []):
        if item.get("id") == target_id:
            item = dict(item)
            item["expires"] = expires
            found = True
        new_exceptions.append(item)
    if not found:
        raise ValueError(f"No exception with id '{target_id}'.")
    new_data = dict(base_data)
    new_data["exceptions"] = new_exceptions
    new_data["version"] = rules_module.RULES_VERSION
    return new_data


def renew_exception(project_dir, target_id, expires, apply):
    base_data = _read_rules_data(project_dir)
    new_data = _with_renewed_expiry(base_data, target_id, expires)
    rules_module.parse_rules(new_data)
    return execute_or_plan(project_dir, "renew", f"renew exception '{target_id}' to {expires}", new_data, apply)


def renew_expiring_within(project_dir, days, expires, apply, today=None):
    base_data = _read_rules_data(project_dir)
    ruleset = rules_module.parse_rules(base_data)
    targets = [exc.id for exc in rules_module.expiring_within(ruleset, days, today=today)]
    new_data = base_data
    for target_id in targets:
        new_data = _with_renewed_expiry(new_data, target_id, expires)
    if targets:
        rules_module.parse_rules(new_data)
    result = execute_or_plan(
        project_dir, "renew",
        f"renew {len(targets)} exception(s) expiring within {days} day(s) to {expires}", new_data, apply)
    return result, targets


def remove_exception(project_dir, target_id, apply):
    base_data = _read_rules_data(project_dir)
    new_exceptions = [item for item in base_data.get("exceptions", []) if item.get("id") != target_id]
    if len(new_exceptions) == len(base_data.get("exceptions", [])):
        raise ValueError(f"No exception with id '{target_id}'.")
    new_data = dict(base_data)
    new_data["exceptions"] = new_exceptions
    new_data["version"] = rules_module.RULES_VERSION
    return execute_or_plan(project_dir, "remove", f"remove exception '{target_id}'", new_data, apply)


def is_renewal_narrowing(base_data, target_id, new_expires):
    """True when the new expiry is EARLIER than (or equal to) the current one:
    narrowing, not widening. A missing target or an unparsable date is treated
    as widening (fail closed toward the deferral path, never the direct one)."""
    for item in base_data.get("exceptions", []):
        if item.get("id") == target_id:
            current = rules_module._parse_expiry_date(item.get("expires"))
            new = rules_module._parse_expiry_date(new_expires)
            if current is None or new is None:
                return False
            return new <= current
    return False
