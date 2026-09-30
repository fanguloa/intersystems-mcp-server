"""Read-only audit of the AI-client configuration this suite recommends.

Three questions, one report:

  * are the clients installed, and where do they keep their MCP configuration?
  * does every server of the suite appear, in the recommended shape, enabled, with
    its settings in the right place and no secret written as a literal?
  * is the packaged harness in sync, are the sub-agent model roles applied, and do
    the managed documentation blocks exist?

Nothing here writes, spawns or connects. The report is machine-facing English with
stable finding codes, so it can be diffed, grepped and asserted regardless of the
language the CLI prints its help in.
"""

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from isc_ai import config as isc_config
from isc_ai.setup import HARNESS_MANIFEST, load_harness_manifest, load_json_file, opencode_config_dir, state_path

SUITE_RESOURCE = Path(__file__).resolve().parent / "resources" / "mcp-suite.json"

# A value that resolves at launch time instead of sitting in the file.
REFERENCE = re.compile(r"^(\{env:[A-Za-z_][A-Za-z0-9_]*\}|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\$\{input:[^}]+\})$")
# Something that only makes sense as a filesystem path, so its existence is checkable.
# A separator or a drive prefix is required: a bare switch like `/c` or `-y` is not a path.
LOOKS_LIKE_PATH = re.compile(r"^([A-Za-z]:[\\/]|[\\/]{2}[^\\/]|[\\/][^\\/]+[\\/])")

GIT_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")

ERROR = "error"
WARNING = "warning"
INFO = "info"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str
    client: str = ""
    scope: str = ""
    server: str = ""

    def as_dict(self):
        return {key: value for key, value in
                (("code", self.code), ("severity", self.severity), ("client", self.client),
                 ("scope", self.scope), ("server", self.server), ("message", self.message))
                if value}


@dataclass
class Entry:
    """One MCP registration, normalized across the three client formats."""

    client: str
    scope: str
    path: Path
    name: str
    transport: str = "stdio"
    command: str = ""
    args: tuple = ()
    url: str = ""
    env: dict = field(default_factory=dict)
    enabled: bool = None  # None when the client has no per-entry switch


def _normalize_name(name):
    """Compare names the way humans mean them: case, separators and registry prefixes."""
    tail = str(name).split("/")[-1]
    return re.sub(r"[^a-z0-9]", "", tail.lower())


def load_suite(path=None):
    """Read the baseline and check its shape, not its size.

    An empty `servers` or `clients` section is a valid baseline: a deployment may declare
    only the clients it manages. What is not valid is a missing or non-object section, so
    the check is structural.
    """
    resolved = Path(path) if path else SUITE_RESOURCE
    data, _, _ = load_json_file(resolved)
    for section in ("servers", "clients"):
        if not isinstance(data.get(section), dict):
            raise isc_config.ConfigError(
                f"{resolved} is not a suite baseline: '{section}' must be an object.")
    return data


# ---------------------------------------------------------------------------
# client readers
# ---------------------------------------------------------------------------

def _resolve_placeholders(template, home, project, environ):
    config_home = environ.get("XDG_CONFIG_HOME")
    config_home = Path(config_home) if config_home else home / ".config"
    appdata = environ.get("APPDATA")
    appdata = Path(appdata) if appdata else home / "AppData" / "Roaming"
    values = {"home": home, "config_home": config_home, "appdata": appdata,
              "project": project if project else home}
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return Path(text)


def _entries_opencode(data, path):
    for name, raw in (data.get("mcp") or {}).items():
        if not isinstance(raw, dict):
            continue
        command = raw.get("command")
        args = ()
        if isinstance(command, list) and command:
            command, args = command[0], tuple(command[1:])
        yield Entry(
            client="opencode", scope="user", path=path, name=name,
            transport="http" if raw.get("type") == "remote" or raw.get("url") else "stdio",
            command=command or "", args=args, url=raw.get("url") or "",
            env=dict(raw.get("environment") or raw.get("env") or {}),
            enabled=raw.get("enabled") if isinstance(raw.get("enabled"), bool) else None,
        )


def _entries_vscode(data, path, scope):
    for name, raw in (data.get("servers") or {}).items():
        if not isinstance(raw, dict):
            continue
        yield Entry(
            client="vscode", scope=scope, path=path, name=name,
            transport="http" if raw.get("type") in {"http", "sse"} or raw.get("url") else "stdio",
            command=raw.get("command") or "", args=tuple(raw.get("args") or []),
            url=raw.get("url") or "", env=dict(raw.get("env") or {}),
            enabled=None,  # VS Code has no per-entry switch: absence is the only "off"
        )


def _entries_claude(data, path, scope, toggles=None):
    disabled = set()
    enabled_only = set()
    if toggles:
        disabled = {_normalize_name(item) for item in toggles.get("disabled", [])}
        enabled_only = {_normalize_name(item) for item in toggles.get("enabled", [])}
    for name, raw in (data.get("mcpServers") or {}).items():
        if not isinstance(raw, dict):
            continue
        state = None
        if _normalize_name(name) in disabled:
            state = False
        elif enabled_only:
            state = _normalize_name(name) in enabled_only
        yield Entry(
            client="claude", scope=scope, path=path, name=name,
            transport="http" if raw.get("type") in {"http", "sse"} or raw.get("url") else "stdio",
            command=raw.get("command") or "", args=tuple(raw.get("args") or []),
            url=raw.get("url") or "", env=dict(raw.get("env") or {}), enabled=state,
        )


def read_clients(suite, home, project, environ):
    """Return per-client presence plus every normalized entry found."""
    clients, entries, findings = {}, [], []
    for name, spec in suite["clients"].items():
        if name.startswith("$"):
            continue
        binary = spec.get("binary", "")
        found = shutil.which(binary) if binary else None
        config = spec.get("config") or {}
        paths = {}
        for scope in ("user", "project"):
            template = config.get(scope)
            if not template:
                continue
            if scope == "project" and project is None:
                continue
            paths[scope] = _resolve_placeholders(template, home, project, environ)
        alternatives = [_resolve_placeholders(item, home, project, environ)
                        for item in config.get("alternatives", [])]

        record = {
            "installed": bool(found),
            "binary": binary,
            "resolved_binary": found or None,
            "config": {scope: str(path) for scope, path in paths.items()},
            "config_present": {scope: path.is_file() for scope, path in paths.items()},
            "install": spec.get("install") or {},
        }
        if not found:
            manager = (spec.get("install") or {}).get("manager", "")
            available = bool(shutil.which(manager)) if manager else False
            record["install_available"] = available
            findings.append(Finding(
                "CLIENT_MISSING", WARNING, client=name,
                message=f"client '{name}' is not on PATH; install recipe needs '{manager}'"
                        f" ({'available' if available else 'also missing'})",
            ))
        for extra in alternatives:
            if extra.is_file() and paths.get("user") and paths["user"].is_file():
                findings.append(Finding(
                    "CONFIG_AMBIGUOUS", WARNING, client=name,
                    message=f"both {paths['user'].name} and {extra.name} exist; commands that write "
                            f"this client need an explicit path",
                ))
        clients[name] = record

        for scope, path in paths.items():
            if not path.is_file():
                continue
            data, _, _ = load_json_file(path)
            if name == "opencode":
                entries.extend(_entries_opencode(data, path))
            elif name == "vscode":
                entries.extend(_entries_vscode(data, path, scope))
            elif name == "claude" and scope == "project":
                entries.extend(_entries_claude(data, path, "project",
                                               _claude_toggles(home, project, environ)))
            elif name == "claude":
                entries.extend(_entries_claude(data, path, "user"))
                entries.extend(_claude_project_overrides(data, path, project))
    return clients, entries, findings


def _claude_toggles(home, project, environ):
    """Claude keeps the per-project on/off lists in the global file, not in .mcp.json."""
    global_file = home / ".claude.json"
    if not global_file.is_file() or project is None:
        return None
    data, _, _ = load_json_file(global_file)
    for raw_path, config in (data.get("projects") or {}).items():
        if _same_path(raw_path, project):
            return {"enabled": config.get("enabledMcpjsonServers") or [],
                    "disabled": (config.get("disabledMcpjsonServers") or [])
                    + (config.get("disabledMcpServers") or [])}
    return None


def _claude_project_overrides(data, path, project):
    """Per-directory server definitions that live inside the global Claude file."""
    for raw_path, config in (data.get("projects") or {}).items():
        servers = config.get("mcpServers") or {}
        if not servers:
            continue
        scope = "project-override" if project and _same_path(raw_path, project) else "other-project"
        yield from _entries_claude({"mcpServers": servers}, path, scope)


def _same_path(left, right):
    try:
        return Path(str(left)).resolve() == Path(str(right)).resolve()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# classification against the baseline
# ---------------------------------------------------------------------------

def _launch_of(spec):
    launch = spec.get("launch") or {}
    return (launch.get("command") or "", tuple(launch.get("args") or []), launch.get("url") or "")


def _version_from_arg(arg):
    """The version pinned onto one launch argument, if any.

    Handles bare package refs (`markitdown-mcp@0.0.1a4`) and npm-scoped ones
    (`@playwright/mcp@latest`), where a leading `@` names the scope, not a
    version boundary: only an `@` found after the first character counts.
    A PEP 508 exact pin (`mcp-atlassian==0.23.1`, the form uvx also accepts, or the
    arbitrary-equality `===`) counts too; a wildcard (`==1.*`) or a range such as `>=`
    is not a pin and stays unpinned.
    """
    if not isinstance(arg, str):
        return None
    if "==" in arg:
        version = arg.rsplit("==", 1)[1]
        return version if version and "*" not in version else None
    tail = arg[1:] if arg.startswith("@") else arg
    if "@" not in tail:
        return None
    return tail.rsplit("@", 1)[1]


def _launch_reference_version(spec):
    """The version suffix baked into this server's launch args, if any."""
    _, args, _ = _launch_of(spec)
    for arg in args:
        version = _version_from_arg(arg)
        if version:
            return version
    return None


def _is_git_sourced_install(install):
    """True when this install block actually runs `... git+<url>[@ref]`.

    Detected from the real recipe (`install.argv`), not a fixed `manager: "git"`
    spelling: the decided distribution recipe for isc-ai's own packages is
    `uv tool install git+https://...@<tag>`, so `manager` stays `"uv"` and the
    git source only shows up in argv. `manager == "git"` is accepted too, in
    case a future recipe spells it that way directly.
    """
    if not install:
        return False
    if str(install.get("manager", "")).lower() == "git":
        return True
    for arg in install.get("argv") or []:
        if isinstance(arg, str) and "git+" in arg:
            return True
    return False


def _audit_install(name, spec, findings):
    """Surface an unpinned or inconsistently-pinned package reference.

    Detection-only (isc_ai.resources.mcp-suite.json §Suite Manifest Install Metadata):
    an absent install block or `pinned: false` never installs, upgrades, or writes
    anything by itself; it only changes what the audit report says. HTTP-transport
    servers have no installable package version at all, so they are never checked.

    Git-sourced installs (`uv tool install git+<url>@<tag>`) get a distinct
    integrity check instead of the launch-args-vs-pin comparison below: their
    launch command is a plain installed console-script entry point (e.g.
    `perforce-mcp-server`), which never carries a version suffix, so comparing
    it against `install.pin` would always mismatch and is not a meaningful
    check for this source kind. Per decision `distribution-channel`, integrity
    for a git source is the pinned tag PLUS an expected commit SHA (there is no
    wheel/package checksum to verify against for a git checkout) - a tag with no
    recorded commit is reported (a moved/re-tagged ref would install silently),
    and a malformed `commit` value is an authoring error.
    """
    _, _, url = _launch_of(spec)
    if url:
        return
    install = spec.get("install")
    pinned = install.get("pinned") if install else None
    launch_version = _launch_reference_version(spec)
    looks_unpinned = launch_version is None or launch_version == "latest"
    git_sourced = _is_git_sourced_install(install)
    if (install is None or pinned is False) and looks_unpinned:
        reason = install.get("pin_reason") if install else None
        findings.append(Finding(
            "VERSION_UNPINNED", WARNING, server=name,
            message=f"'{name}' launches an unpinned package reference"
                    + (f" ({reason})" if reason else ""),
        ))
    if install and pinned is True and not git_sourced:
        pin = install.get("pin")
        if pin and launch_version != pin:
            findings.append(Finding(
                "INSTALL_PIN_MISMATCH", ERROR, server=name,
                message=f"'{name}' declares install.pin={pin} but the launch command "
                        f"actually runs {launch_version or '<unpinned>'}",
            ))
    if git_sourced and install:
        pin = install.get("pin")
        if pin:
            commit = install.get("commit")
            if not commit:
                findings.append(Finding(
                    "INSTALL_COMMIT_MISSING", WARNING, server=name,
                    message=f"'{name}' pins git tag {pin} but declares no expected commit "
                            f"SHA (install.commit); a re-tagged or moved ref would be "
                            f"installed silently. Not invented here: leave absent and tag "
                            f"the real release first (see MANUAL-2)",
                ))
            elif not GIT_COMMIT_SHA.match(str(commit)):
                findings.append(Finding(
                    "INSTALL_COMMIT_INVALID", ERROR, server=name,
                    message=f"'{name}' install.commit={commit!r} is not a 40-character "
                            f"hex commit SHA",
                ))


def _unwrap(entry):
    """`cmd /c <real command>` states the same intent as the bare command.

    Returns (command, args, wrapped) so every check reasons about the real program
    instead of the shell that fronts it.
    """
    command, args = entry.command, list(entry.args)
    if PurePosixPath(command.replace("\\", "/")).stem.lower() in {"cmd", "sh", "bash"} \
            and args[:1] in (["/c"], ["-c"]):
        args = args[1:]
        if args:
            return args[0], args[1:], True
        return "", [], True
    return command, args, False


def _missing_paths(entry):
    """Absolute paths in the real argv that do not exist: a registration pointing nowhere."""
    _, args, _ = _unwrap(entry)
    return [str(arg) for arg in args
            if isinstance(arg, str) and LOOKS_LIKE_PATH.match(arg) and not Path(arg).exists()]


def _compare_launch(entry, spec):
    expected_command, expected_args, expected_url = _launch_of(spec)
    differences = []
    if expected_url:
        if entry.url != expected_url:
            differences.append(f"url={isc_config.mask_url(entry.url) or '<none>'} (recommended {expected_url})")
        return differences
    command, args, wrapped = _unwrap(entry)
    if wrapped:
        differences.append("wrapped in a shell instead of calling the program directly")
    if PurePosixPath(command.replace("\\", "/")).stem.lower() != PurePosixPath(
            expected_command.replace("\\", "/")).stem.lower():
        differences.append(f"command={command or '<none>'} (recommended {expected_command})")
    stripped = [arg for arg in args if arg != "-y"]
    if tuple(stripped) != expected_args:
        # Some servers take a credential on the command line. The report exists to show
        # the drift, not to reprint the secret into a terminal recording.
        differences.append(f"args={isc_config.mask_argv(stripped)} "
                           f"(recommended {list(expected_args)})")
    return differences


def _audit_settings(entry, spec, findings):
    declared = spec.get("settings") or {}
    for key, rule in declared.items():
        value = entry.env.get(key)
        if value is None:
            if rule.get("required"):
                findings.append(Finding(
                    "SETTING_MISSING", ERROR, client=entry.client, scope=entry.scope, server=entry.name,
                    message=f"required setting {key} is not set ({rule.get('scope', 'global')} scope)",
                ))
            continue
        if rule.get("secret") and not REFERENCE.match(str(value)):
            findings.append(Finding(
                "SECRET_LEAK", ERROR, client=entry.client, scope=entry.scope, server=entry.name,
                message=f"{key} is written as a literal value; use a reference so the secret is not "
                        f"stored in {entry.path.name}",
            ))
    for key in entry.env:
        if key not in declared:
            findings.append(Finding(
                "SETTING_UNKNOWN", INFO, client=entry.client, scope=entry.scope, server=entry.name,
                message=f"{key} is not declared by the baseline for this server",
            ))


def classify(suite, entries, findings, clients=None):
    """Match every suite server against what each client has registered.

    `clients` lists the clients whose configuration was readable, so a client with an
    empty config still gets a MISSING verdict instead of disappearing from the report.
    """
    servers = {}
    matched = set()
    by_client = {name: [] for name in (clients or [])}
    for entry in entries:
        by_client.setdefault(entry.client, []).append(entry)

    for name, spec in suite["servers"].items():
        _audit_install(name, spec, findings)
        canonical = _normalize_name(name)
        aliases = {_normalize_name(alias) for alias in spec.get("aliases", [])}
        per_client = {}
        for client, client_entries in by_client.items():
            # Matching is tolerant (case, separators, registry prefixes); the ALIAS
            # verdict is literal, because a different name is what breaks a runbook.
            same = [item for item in client_entries if _normalize_name(item.name) == canonical]
            alias = [item for item in client_entries if _normalize_name(item.name) in aliases]
            chosen = same + [item for item in alias if item not in same]
            if not chosen:
                per_client[client] = {"state": "MISSING"}
                findings.append(Finding(
                    "SERVER_MISSING", WARNING, client=client, server=name,
                    message=f"'{name}' is not registered",
                ))
                continue
            for item in chosen:
                matched.add(id(item))
            states = []
            for item in chosen:
                item_states = []
                if item.name != name:
                    item_states.append("ALIAS")
                    findings.append(Finding(
                        "SERVER_ALIAS", WARNING, client=client, scope=item.scope, server=name,
                        message=f"registered as '{item.name}'; the suite name is '{name}'",
                    ))
                differences = _compare_launch(item, spec)
                if differences:
                    item_states.append("DRIFT")
                    findings.append(Finding(
                        "SERVER_DRIFT", WARNING, client=client, scope=item.scope, server=name,
                        message="; ".join(differences),
                    ))
                gone = _missing_paths(item)
                if gone:
                    item_states.append("BROKEN")
                    findings.append(Finding(
                        "PATH_NOT_FOUND", ERROR, client=client, scope=item.scope, server=name,
                        message=f"argument points at a path that does not exist: {gone[0]}",
                    ))
                real_command, _, _ = _unwrap(item)
                if item.transport == "stdio" and real_command and not shutil.which(real_command) \
                        and not Path(real_command).exists():
                    item_states.append("BROKEN")
                    findings.append(Finding(
                        "COMMAND_NOT_FOUND", ERROR, client=client, scope=item.scope, server=name,
                        message=f"command '{real_command}' is not on PATH",
                    ))
                if item.enabled is False:
                    item_states.append("DISABLED")
                    findings.append(Finding(
                        "SERVER_DISABLED", INFO, client=client, scope=item.scope, server=name,
                        message="registered but disabled",
                    ))
                _audit_settings(item, spec, findings)
                states.append(item_states or ["OK"])
            if len(chosen) > 1:
                where = ", ".join(f"{item.name} ({item.scope})" for item in chosen)
                findings.append(Finding(
                    "SERVER_DUPLICATE", WARNING, client=client, server=name,
                    message=f"registered {len(chosen)} times: {where}",
                ))
            flat = sorted({state for group in states for state in group})
            per_client[client] = {"state": "+".join(flat), "registered_as": [item.name for item in chosen]}
        servers[name] = per_client

    known = {_normalize_name(name) for name in suite["servers"]}
    for name, spec in suite["servers"].items():
        known |= {_normalize_name(alias) for alias in spec.get("aliases", [])}
    out_of_suite = suite.get("out_of_suite") or {}
    extras = []
    for entry in entries:
        if id(entry) in matched or _normalize_name(entry.name) in known:
            continue
        note = next((text for key, text in out_of_suite.items()
                     if not key.startswith("$") and _normalize_name(key) == _normalize_name(entry.name)), "")
        extras.append({"client": entry.client, "scope": entry.scope, "name": entry.name})
        findings.append(Finding(
            "SERVER_EXTRA", INFO, client=entry.client, scope=entry.scope, server=entry.name,
            message=note or "registered but not part of the suite",
        ))
    return servers, extras


# ---------------------------------------------------------------------------
# harness, sub-agent model roles and managed documentation blocks
# ---------------------------------------------------------------------------

MODEL_TEMPLATE_RELATIVE = PurePosixPath("skills/ai-work-policy/assets/model-role-template.json")
FRONTMATTER_MODEL = re.compile(r"^model:\s*(\S+)\s*$", re.M)
# Managed instruction blocks, whoever owns them: the harness writes `gentle-ai:` and
# `isc-ai docs` writes `isc-ai:`. Both count as managed.
MANAGED_BLOCK = re.compile(r"<!--\s*(?:gentle-ai|isc-ai):([a-z0-9-]+)\s*-->")


def audit_harness(home, findings, manifest_path=None):
    """Compare the packaged bundle with what is installed under ~/.claude."""
    from isc_ai.setup import _load_state, plan_harness  # local import: audit stays read-only

    manifest = load_harness_manifest(manifest_path or HARNESS_MANIFEST)
    state = _load_state(state_path(home=home))
    plans, obsolete = plan_harness(home, state, manifest)
    counts = {}
    for plan in plans:
        counts[plan.action] = counts.get(plan.action, 0) + 1
    report = {
        "bundle_version": manifest.get("bundle_version"),
        "files": len(plans),
        "actions": counts,
        "obsolete": list(obsolete),
        "state_file": str(state_path(home=home)),
        "state_recorded": len(state.get("managed", {})),
    }
    conflicts = counts.get("conflict", 0)
    if conflicts:
        findings.append(Finding(
            "HARNESS_FROZEN", ERROR,
            message=f"{conflicts} of {len(plans)} harness files differ from the bundle with no recorded "
                    f"baseline, so every one of them is a conflict and the harness cannot be updated",
        ))
    if counts.get("install"):
        findings.append(Finding(
            "HARNESS_INCOMPLETE", WARNING,
            message=f"{counts['install']} harness files are not installed yet",
        ))
    for name in obsolete:
        findings.append(Finding(
            "HARNESS_OBSOLETE", INFO,
            message=f"{name} was installed by a previous bundle and is no longer part of it",
        ))
    return report


def audit_model_roles(home, findings, suite_root=None):
    """Are the sub-agent model roles applied where each client actually reads them?"""
    template_path = (Path(suite_root) if suite_root else HARNESS_MANIFEST.parent) / MODEL_TEMPLATE_RELATIVE
    template, _, _ = load_json_file(template_path)
    roles = dict(template.get("role_defaults") or {})
    applied = {}
    agents_dir = home / ".claude" / "agents"
    for path in sorted(agents_dir.glob("*.md")) if agents_dir.is_dir() else []:
        match = FRONTMATTER_MODEL.search(path.read_text(encoding="utf-8"))
        applied[path.stem] = match.group(1) if match else None

    opencode_models = {}
    opencode_file = opencode_config_dir(home=home) / "opencode.json"
    if opencode_file.is_file():
        data, _, _ = load_json_file(opencode_file)
        for name, entry in (data.get("agent") or {}).items():
            if isinstance(entry, dict):
                opencode_models[name] = entry.get("model")

    if template.get("runtime_applied") is False:
        findings.append(Finding(
            "MODEL_ROLES_DECLARATIVE", WARNING,
            message="the model-role template is marked runtime_applied=false: it records intent but "
                    "nothing renders it into the clients",
        ))
    for role, preference in roles.items():
        if role == "default" or role.startswith("fresh"):
            continue
        claude_value = applied.get(role, "<absent>")
        if claude_value in (None, "<absent>"):
            findings.append(Finding(
                "MODEL_ROLE_UNSET", WARNING, client="claude", server=role,
                message=f"agent '{role}' declares no model; the template asks for {preference}",
            ))
    if applied and not opencode_models:
        findings.append(Finding(
            "MODEL_ROLES_CLIENT_GAP", WARNING, client="opencode",
            message="Claude Code agents carry model assignments but opencode declares no agents, "
                    "so the same roles run on the client default there",
        ))
    return {"template": str(template_path), "runtime_applied": template.get("runtime_applied"),
            "role_defaults": roles, "claude_agents": applied, "opencode_agents": opencode_models}


def audit_documents(home, project, findings):
    """Managed instruction blocks: present, and which ones."""
    targets = {"claude-global": home / ".claude" / "CLAUDE.md"}
    if project is not None:
        targets["workflow"] = project / "WORKFLOW.md"
        targets["agents"] = project / "AGENTS.md"
    report = {}
    for label, path in targets.items():
        if not path.is_file():
            report[label] = {"path": str(path), "present": False, "blocks": []}
            findings.append(Finding(
                "DOC_MISSING", INFO, message=f"{label} document not found at {path}",
            ))
            continue
        blocks = sorted(set(MANAGED_BLOCK.findall(path.read_text(encoding="utf-8"))))
        report[label] = {"path": str(path), "present": True, "blocks": blocks}
        if not blocks:
            findings.append(Finding(
                "DOC_UNMANAGED", WARNING,
                message=f"{path.name} has no managed block, so the suite cannot refresh its "
                        f"recommended sections without rewriting the whole file",
            ))
    return report


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def run_audit(project_dir=None, home=None, environ=None, suite_path=None):
    """Build the whole report. Read-only: no writes, no processes, no network."""
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    project = None
    if project_dir is not None:
        project = Path(project_dir).resolve()
    else:
        found = isc_config.find_project_root(Path.cwd())
        project = found if found is not None else None

    suite = load_suite(suite_path)
    findings = []
    clients, entries, client_findings = read_clients(suite, home, project, environ)
    findings.extend(client_findings)
    servers, extras = classify(suite, entries, findings, clients=list(clients))

    report = {
        "suite_version": suite.get("version"),
        "project_dir": str(project) if project else None,
        "clients": clients,
        "servers": servers,
        "extras": extras,
        "harness": audit_harness(home, findings),
        "model_roles": audit_model_roles(home, findings),
        "documents": audit_documents(home, project, findings),
    }
    ordered = {ERROR: 0, WARNING: 1, INFO: 2}
    report["findings"] = [finding.as_dict() for finding in
                          sorted(findings, key=lambda item: (ordered[item.severity], item.code, item.client))]
    report["summary"] = {
        "errors": sum(1 for item in findings if item.severity == ERROR),
        "warnings": sum(1 for item in findings if item.severity == WARNING),
        "info": sum(1 for item in findings if item.severity == INFO),
    }
    return report
