"""Move the state directory from the legacy name to the current one.

The tool configures the agentic environment, of which IRIS/TrakCare is one server, so the
state directory carries the tool's name. Renaming it is only acceptable with a migration
that an installed deployment can run without losing anything, which is what this is.

Rules, in order of importance:

- Never overwrite. If the destination already exists, that pair is reported as a conflict
  and left untouched: a half-migrated deployment is recoverable, an overwritten policy is not.
- Copy, then verify, then remove. A move that fails halfway must not lose the only copy.
- Report every file, including the ones already migrated, so the outcome is checkable instead
  of assumed.
"""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

from . import config as config_module

# Files worth carrying across, with why each one exists.
KNOWN_FILES = {
    "config.json": "project policy",
    "servers.json": "user-level connections",
    "secrets.json": "credential store",
}


def _pair(root):
    return Path(root) / config_module.LEGACY_STATE_DIR, Path(root) / config_module.STATE_DIR


def plan(project_dir=None, home=None):
    """What a migration would do, without touching anything."""
    roots = []
    if project_dir is not None:
        roots.append(("project", Path(project_dir)))
    if home is not None:
        roots.append(("user", Path(home)))

    moves, conflicts, absent = [], [], []
    for scope, root in roots:
        legacy, current = _pair(root)
        if not legacy.is_dir():
            absent.append({"scope": scope, "path": str(legacy)})
            continue
        for item in sorted(legacy.iterdir()):
            if not item.is_file():
                continue
            destination = current / item.name
            record = {
                "scope": scope,
                "name": item.name,
                "purpose": KNOWN_FILES.get(item.name, "unrecognised file, carried as-is"),
                "from": str(item),
                "to": str(destination),
            }
            if not destination.exists():
                moves.append(record)
            elif filecmp.cmp(item, destination, shallow=False):
                record["action"] = "already-identical"
                moves.append(record)
            else:
                record["reason"] = "destination exists with different content"
                conflicts.append(record)

    return {
        "mode": "check",
        "legacy_dir": config_module.LEGACY_STATE_DIR,
        "current_dir": config_module.STATE_DIR,
        "moves": moves,
        "conflicts": conflicts,
        "absent": absent,
    }


def apply(project_dir=None, home=None):
    """Perform the migration. Refuses entirely if any conflict is present."""
    report = plan(project_dir=project_dir, home=home)
    report["mode"] = "apply"
    if report["conflicts"]:
        # All or nothing: migrating half of a policy pair is worse than migrating none.
        report["performed"] = []
        return report

    performed = []
    for record in report["moves"]:
        source = Path(record["from"])
        destination = Path(record["to"])
        if record.get("action") == "already-identical":
            source.unlink()
            performed.append({**record, "action": "removed-duplicate"})
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        # Verify before removing the only other copy.
        if not filecmp.cmp(source, destination, shallow=False):
            performed.append({**record, "action": "copy-unverified"})
            continue
        source.unlink()
        performed.append({**record, "action": "moved"})

    # Remove the legacy directory only when it is empty: anything left is someone else's.
    for scope, root in (("project", project_dir), ("user", home)):
        if root is None:
            continue
        legacy, _ = _pair(root)
        if legacy.is_dir() and not any(legacy.iterdir()):
            legacy.rmdir()
            performed.append({"scope": scope, "action": "removed-empty-dir",
                              "from": str(legacy)})

    report["performed"] = performed
    return report
