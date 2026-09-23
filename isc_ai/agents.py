"""Model assignment per SDD sub-agent, and the two clients that read it differently.

Claude Code reads a `model:` line in the agent frontmatter, and that line is data in the
packaged bundle. OpenCode reads `agent.<name>.model` in its own configuration, and it
wants a provider-qualified id (`amazon-bedrock/anthropic.claude-sonnet-5`), not a family
name. So the same intent needs two renders, and only one of them can be satisfied by a
family alias.

The rule that keeps this honest: a concrete model id is never invented. The role template
expresses a family preference; turning that into an id is a decision about which model
runs your work and on whose account, so it has to be configured. When it is not, the
agent is still declared, the model is left to the client default, and the available
candidates for that family are reported so the choice is one command away.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

from isc_ai import config as isc_config
from isc_ai.setup import (
    HARNESS_MANIFEST, ManagedWrite, _apply_transaction, _json_bytes, _managed_target,
    load_json_file, opencode_config_dir,
)

TEMPLATE = HARNESS_MANIFEST.parent / "skills" / "ai-work-policy" / "assets" / "model-role-template.json"
FRONTMATTER_MODEL = re.compile(r"(?m)^model:\s*(\S+)\s*$")


def role_preferences():
    """role -> intended family, from the packaged template."""
    template, _, _ = load_json_file(TEMPLATE)
    profiles = template.get("profiles") or {}
    out = {}
    for role, preference in (template.get("role_defaults") or {}).items():
        profile = profiles.get(preference) or {}
        out[role] = profile.get("intended_family") or preference
    return out


def available_models(binary="opencode"):
    """Model ids this workstation can actually reach, or None when unknown.

    Asking the client beats maintaining a list: an id that is not offered here would
    fail at runtime, and a list in the package would go stale.

    The resolved path is what gets executed, never the bare name: on Windows the client
    is a `.cmd` shim and CreateProcess does not apply PATHEXT, so a bare name raises
    FileNotFoundError even when the program is plainly on PATH.
    """
    resolved = shutil.which(binary)
    if not resolved:
        return None
    try:
        result = subprocess.run([resolved, "models"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return sorted({line.strip() for line in result.stdout.splitlines() if "/" in line.strip()})


def candidates_for(family, models):
    """Ids that plausibly belong to a family, newest-looking last."""
    if not models or not family:
        return []
    return sorted(model for model in models if family.lower() in model.lower())


def _agent_entry(role, previous, model):
    entry = dict(previous or {})
    entry["mode"] = "subagent"
    entry.setdefault("description", f"SDD {role.removeprefix('sdd-')} phase.")
    if model:
        entry["model"] = model
    else:
        entry.pop("model", None)
    return entry


def plan(project_dir=None, home=None, environ=None, verify_models=True):
    """What the sub-agent model assignment is, and what would change in OpenCode."""
    import os

    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    project = Path(project_dir).resolve() if project_dir else isc_config.find_project_root(Path.cwd())
    configured = {}
    if project is not None:
        runtime = isc_config.load_runtime_config(project, environ=environ)
        configured = dict(runtime.policy.agent_models or {})

    preferences = role_preferences()
    models = available_models() if verify_models else None

    report = {"mode": "check", "project_dir": str(project) if project else None,
              "models_verified": models is not None, "roles": [], "findings": [], "files": []}
    writes = []

    # What Claude Code will use comes from the packaged bundle, not from here.
    claude_models = {}
    agents_dir = HARNESS_MANIFEST.parent / "agents"
    for path in sorted(agents_dir.glob("sdd-*.md")):
        match = FRONTMATTER_MODEL.search(path.read_text(encoding="utf-8"))
        claude_models[path.stem] = match.group(1) if match else None

    resolved = {}
    for role, family in sorted(preferences.items()):
        if role == "default":
            continue
        model = configured.get(role) or configured.get("default")
        record = {"role": role, "family": family, "claude": claude_models.get(role),
                  "opencode": model}
        if model and models is not None and model not in models:
            record["opencode"] = None
            report["findings"].append({
                "code": "MODEL_UNAVAILABLE", "severity": "error", "role": role,
                "message": f"'{model}' is not offered by this workstation; run 'opencode models' "
                           f"to see what is",
            })
        elif not model:
            options = candidates_for(family, models)
            record["candidates"] = options[-3:] if options else []
            report["findings"].append({
                "code": "MODEL_UNRESOLVED", "severity": "warning", "role": role,
                "message": f"no model configured for the {family} preference; OpenCode will use its "
                           f"default. Pick one with: isc-ai config set agents.models.{role} <id>",
            })
        else:
            resolved[role] = model
        report["roles"].append(record)

    config_path = opencode_config_dir(home, environ) / "opencode.json"
    data, strict, _ = load_json_file(config_path)
    if not strict:
        report["files"].append({"path": str(config_path), "action": "skipped-jsonc"})
        return report, writes

    agents = dict(data.get("agent") or {})
    changed = False
    for role in sorted(preferences):
        if role == "default":
            continue
        entry = _agent_entry(role, agents.get(role), resolved.get(role))
        if agents.get(role) != entry:
            agents[role] = entry
            changed = True
    if changed:
        merged = dict(data)
        merged["agent"] = agents
        report["files"].append({"path": str(config_path),
                                "action": "update" if config_path.exists() else "create"})
        writes.append(ManagedWrite(config_path.parent, _managed_target(config_path.parent, config_path),
                                   _json_bytes(merged)))
    else:
        report["files"].append({"path": str(config_path), "action": "noop"})
    return report, writes


def apply(project_dir=None, home=None, environ=None, verify_models=True):
    report, writes = plan(project_dir=project_dir, home=home, environ=environ,
                          verify_models=verify_models)
    report["mode"] = "apply"
    if any(item["severity"] == "error" for item in report["findings"]):
        raise isc_config.ConfigError(
            "a configured model is not available on this workstation; nothing was written")
    if writes:
        _apply_transaction(writes, external_install=False)
    report["written"] = [str(write.target) for write in writes]
    return report
