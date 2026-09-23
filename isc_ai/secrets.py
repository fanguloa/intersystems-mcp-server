"""Credential store for the values configuration files must never contain.

Two scopes, because credentials have two natures:

  ~/.iris-mcp/secrets.json          global: one per person (Jira token, IRIS password)
  <project>/.iris-mcp/secrets.json  project: differs per deployment, and gitignored

Resolution order when a `{env:VAR}` reference is expanded: the process environment first,
so CI and a shell override always win, then the project store, then the global one. A
value found nowhere is still an error, exactly as before: a missing credential must fail
loudly rather than silently connect as somebody else.

A value is never accepted as a command-line argument. It would land in shell history, in
the process list and in any terminal recording, which are three places a secret outlives
its usefulness. It is read from a prompt or from stdin.
"""

import json
import os
import stat
import sys
from pathlib import Path

from isc_ai import config as isc_config

SECRETS_RELATIVE = Path(".iris-mcp") / "secrets.json"
SCHEMA = "isc-ai.secrets.v1"
GITIGNORE_LINE = ".iris-mcp/secrets.json"


def global_path(home=None):
    return (Path.home() if home is None else Path(home)) / SECRETS_RELATIVE


def project_path(project_dir):
    return Path(project_dir) / SECRETS_RELATIVE


def _read(path):
    if not path.is_file():
        return {}
    data = isc_config.read_json(path)
    if not isinstance(data, dict):
        raise isc_config.ConfigError(f"{path} must contain a JSON object.")
    secrets = data.get("secrets", {})
    if not isinstance(secrets, dict):
        raise isc_config.ConfigError(f"{path} must hold a 'secrets' object.")
    for key, value in secrets.items():
        if not isinstance(value, str):
            raise isc_config.ConfigError(f"{path}: secrets.{key} must be a string.")
    return secrets


def _write(path, secrets):
    """Write the store and restrict it to its owner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"$schema": SCHEMA, "secrets": dict(sorted(secrets.items()))}
    isc_config.write_json(path, payload)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows ignores POSIX modes; the file stays inside the user profile.
        pass


def load(project_dir=None, home=None):
    """Merged view: project overrides global. Values, for the resolver only."""
    merged = dict(_read(global_path(home)))
    if project_dir is not None:
        merged.update(_read(project_path(project_dir)))
    return merged


def inventory(project_dir=None, home=None, environ=None):
    """Where each known key comes from, without ever reporting a value."""
    environ = os.environ if environ is None else environ
    rows = []
    stores = [("global", global_path(home))]
    if project_dir is not None:
        stores.append(("project", project_path(project_dir)))
    known = {}
    for scope, path in stores:
        for key in _read(path):
            known.setdefault(key, []).append(scope)
    for key in sorted(set(known) | {name for name in environ if name.startswith(("IRIS_", "JIRA_", "P4"))}):
        sources = list(known.get(key, []))
        if key in environ:
            sources.insert(0, "environment")
        rows.append({"key": key, "sources": sources,
                     "effective": sources[0] if sources else None})
    return {"stores": [{"scope": scope, "path": str(path), "present": path.is_file()}
                       for scope, path in stores],
            "keys": rows}


def read_value(prompt, stream=None):
    """Read a secret without echoing it and without ever touching argv."""
    stream = sys.stdin if stream is None else stream
    if stream.isatty():
        import getpass
        return getpass.getpass(prompt)
    value = stream.readline()
    if not value:
        raise isc_config.ConfigError("no value on stdin; pipe it or run in a terminal.")
    return value.rstrip("\r\n")


def set_secret(key, value, scope="global", project_dir=None, home=None):
    if not key or not key.replace("_", "").isalnum() or not key[0].isalpha():
        raise isc_config.ConfigError(f"'{key}' is not a usable environment variable name.")
    if not value:
        raise isc_config.ConfigError("an empty value is not a secret; use 'secrets unset' instead.")
    path = _target(scope, project_dir, home)
    secrets = _read(path)
    existed = key in secrets
    secrets[key] = value
    _write(path, secrets)
    notes = []
    if scope == "project":
        notes.extend(ensure_ignored(Path(project_dir)))
    return {"key": key, "scope": scope, "path": str(path),
            "action": "replaced" if existed else "added", "notes": notes}


def unset_secret(key, scope="global", project_dir=None, home=None):
    path = _target(scope, project_dir, home)
    secrets = _read(path)
    if key not in secrets:
        raise isc_config.ConfigError(f"{key} is not set in the {scope} store.")
    del secrets[key]
    _write(path, secrets)
    return {"key": key, "scope": scope, "path": str(path), "action": "removed"}


def _target(scope, project_dir, home):
    if scope == "global":
        return global_path(home)
    if scope == "project":
        if project_dir is None:
            raise isc_config.ConfigError("the project scope needs a project; run inside one "
                                        "or pass --project-dir.")
        return project_path(project_dir)
    raise isc_config.ConfigError(f"unknown scope '{scope}'; use global or project.")


def ensure_ignored(project_dir):
    """Keep the project store out of version control, and say what was done."""
    gitignore = Path(project_dir) / ".gitignore"
    if not (Path(project_dir) / ".git").exists() and not gitignore.exists():
        return [f"{project_dir} is not a git repository; nothing to ignore"]
    existing = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    if GITIGNORE_LINE in existing.splitlines():
        return []
    separator = "" if not existing or existing.endswith("\n") else "\n"
    comment = "# Credentials for the MCP suite: never committed.\n"
    gitignore.write_text(existing + separator + comment + GITIGNORE_LINE + "\n", encoding="utf-8")
    return [f"added {GITIGNORE_LINE} to {gitignore}"]


def tracked_by_git(project_dir):
    """True when the project store is already committed: a leak that needs a human."""
    import subprocess

    path = project_path(project_dir)
    if not path.is_file():
        return False
    try:
        result = subprocess.run(["git", "-C", str(project_dir), "ls-files", "--error-unmatch",
                                 str(path.relative_to(project_dir)).replace("\\", "/")],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
