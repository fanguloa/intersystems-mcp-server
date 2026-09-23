"""Plan-first local client setup for ``isc-ai``.

The default operation is read-only. Files are changed only when ``apply=True``.
No setup operation contacts IRIS.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

# The registration name is the canonical suite name, so setup and suite agree
# and a workstation never ends up with the same server under two names.
MCP_NAME = "intersystems-iris"
SCHEMA_URL = "https://opencode.ai/config.json"
TUI_SCHEMA_URL = "https://opencode.ai/tui.json"
DEFAULT_TIMEOUT = 1800
STALE_ENV_KEYS = ("IRIS_MCP_PROJECT_DIR", "IRIS_EXTRA_PACKAGES", "IRIS_MCP_CONFIG")
RESOURCE_ROOT = Path(__file__).resolve().parent / "resources"
LOGO_RESOURCE = RESOURCE_ROOT / "opencode" / "isc-logo.tsx"
HARNESS_ROOT = RESOURCE_ROOT / "harness"
HARNESS_MANIFEST = HARNESS_ROOT / "manifest.json"
STATE_FILENAME = "setup-state.json"


class SetupError(RuntimeError):
    """A setup plan cannot be made safely."""


@dataclass(frozen=True)
class FilePlan:
    category: str
    source: Path
    target: Path
    digest: str
    action: str


@dataclass(frozen=True)
class ManagedWrite:
    root: Path
    target: Path
    content: bytes


@dataclass(frozen=True)
class FileState:
    content: bytes | None
    mode: int | None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _positive_timeout(value: int | str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise SetupError("timeout must be a positive integer") from None
    if result <= 0:
        raise SetupError("timeout must be a positive integer")
    return result


def _config_home(home: Path, environ: dict[str, str]) -> Path:
    xdg = environ.get("XDG_CONFIG_HOME")
    if xdg is not None:
        path = Path(xdg).expanduser()
        if not path.is_absolute():
            raise SetupError("XDG_CONFIG_HOME must be an absolute path")
        return path
    return home / ".config"


def opencode_config_dir(home=None, environ=None) -> Path:
    environ = dict(os.environ if environ is None else environ)
    home = Path.home() if home is None else Path(home)
    return _config_home(home, environ) / "opencode"


def state_path(home=None, environ=None) -> Path:
    environ = dict(os.environ if environ is None else environ)
    home = Path.home() if home is None else Path(home)
    return _config_home(home, environ) / "isc-ai" / STATE_FILENAME


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _managed_target(root: Path, target: Path) -> Path:
    """Validate containment/components; stdlib checks cannot prevent an OS-level swap after validation."""
    root = root.expanduser().absolute()
    target = target.expanduser().absolute()
    root_resolved = root.resolve(strict=False)
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise SetupError(f"managed file target escapes its root {root_resolved}: {target}") from None
    try:
        relative = target.relative_to(root)
    except ValueError:
        relative = resolved.relative_to(root_resolved)
        root = root_resolved
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if current.exists() and _is_link_or_reparse(current):
            raise SetupError(f"managed file parent must not be a symlink or reparse point: {current}")
    if _is_link_or_reparse(target):
        raise SetupError(f"managed file target must not be a symlink or reparse point: {target}")
    return target


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = None
    try:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.fsync(descriptor)
    except OSError:
        # Directory fsync is unavailable on some otherwise supported filesystems.
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _replace_with_retry(tmp: Path, path: Path, attempts: int = 6, delay: float = 0.05) -> None:
    """Commit the replacement, retrying the Windows sharing races.

    `os.replace` is atomic but not immune to another process holding a handle: on Windows
    an antivirus or the search indexer routinely opens a file microseconds after it is
    created, and the call fails with access denied (5) or a sharing violation (32). It is
    transient, so a bounded retry is the difference between a command that works and one
    that fails once every few runs. Anything else, and the last attempt of these, is
    raised for the caller to roll back.
    """
    transient = {5, 32, 33}
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in transient or attempt == attempts - 1:
                raise
            time.sleep(delay * (2 ** attempt))


def _replace_file(
    path: Path,
    content: bytes,
    *,
    root: Path | None = None,
    mode: int | None = None,
    on_replaced: Callable[[FileState], None] | None = None,
) -> FileState:
    root = path.parent if root is None else root
    path = _managed_target(root, path)
    if path.exists():
        if not path.is_file():
            raise SetupError(f"managed file target is not a regular file: {path}")
        existing_mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
        if os.name == "nt" and not existing_mode & stat.S_IWRITE:
            raise SetupError(f"managed file target is read-only: {path}")
        if mode is None:
            mode = existing_mode
    path.parent.mkdir(parents=True, exist_ok=True)
    _managed_target(root, path)
    descriptor = None
    tmp = None
    try:
        descriptor, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.isc-ai.",
            suffix=".tmp",
            dir=path.parent,
        )
        tmp = Path(tmp_name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        committed = FileState(content, stat.S_IMODE(tmp.stat(follow_symlinks=False).st_mode))
        _managed_target(root, path)
        _replace_with_retry(tmp, path)
        if on_replaced is not None:
            on_replaced(committed)
        _fsync_directory(path.parent)
        return committed
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if tmp is not None:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass


def _atomic_write(
    path: Path,
    content: bytes,
    *,
    root: Path | None = None,
    mode: int | None = None,
    on_replaced: Callable[[FileState], None] | None = None,
) -> FileState:
    return _replace_file(path, content, root=root, mode=mode, on_replaced=on_replaced)


def _capture_file_state(write: ManagedWrite) -> FileState:
    target = _managed_target(write.root, write.target)
    if not target.exists():
        return FileState(None, None)
    if not target.is_file():
        raise SetupError(f"managed file target is not a regular file: {target}")
    mode = stat.S_IMODE(target.stat(follow_symlinks=False).st_mode)
    if os.name == "nt" and not mode & stat.S_IWRITE:
        raise SetupError(f"managed file target is read-only: {target}")
    return FileState(target.read_bytes(), mode)


def _assert_expected_state(write: ManagedWrite, expected: FileState) -> None:
    current = _capture_file_state(write)
    if current != expected:
        raise SetupError(f"managed file changed after preflight: {write.target}")


def _preflight_writes(writes: list[ManagedWrite]) -> list[FileState]:
    snapshots: list[FileState] = []
    seen: set[str] = set()
    for write in writes:
        key = os.path.normcase(str(write.target.resolve(strict=False)))
        if key in seen:
            raise SetupError(f"duplicate managed write target: {write.target}")
        seen.add(key)
        snapshots.append(_capture_file_state(write))
    return snapshots


def _rollback_writes(applied: list[tuple[ManagedWrite, FileState, FileState]]) -> list[str]:
    failures: list[str] = []
    for write, original, committed in reversed(applied):
        try:
            current = _capture_file_state(write)
            if current != committed:
                raise SetupError(f"managed file changed before rollback: {write.target}")
            if original.content is None:
                write.target.unlink(missing_ok=True)
                _fsync_directory(write.target.parent)
            else:
                _replace_file(write.target, original.content, root=write.root, mode=original.mode)
        except Exception as exc:  # rollback is best-effort and all failures must be reported
            failures.append(f"{write.target}: {getattr(exc, 'strerror', None) or str(exc) or type(exc).__name__}")
    return failures


def _apply_transaction(
    writes: list[ManagedWrite],
    *,
    external_install: bool,
    snapshots: list[FileState] | None = None,
) -> None:
    snapshots = _preflight_writes(writes) if snapshots is None else snapshots
    applied: list[tuple[ManagedWrite, FileState, FileState]] = []
    try:
        for write, expected in zip(writes, snapshots):
            _assert_expected_state(write, expected)
            _atomic_write(
                write.target,
                write.content,
                root=write.root,
                mode=expected.mode,
                on_replaced=lambda committed, write=write, expected=expected: applied.append(
                    (write, expected, committed)
                ),
            )
    except Exception as exc:
        rollback_failures = _rollback_writes(applied)
        message = f"managed write failed at {write.target}: {exc}; prior managed files were rolled back"
        if rollback_failures:
            message += "; rollback failures: " + "; ".join(rollback_failures)
        if external_install:
            message += "; the earlier OpenCode package-manager install is not rollbackable"
        raise SetupError(message) from exc


def _json_bytes(data: dict) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _strip_jsonc(text: str) -> str:
    out: list[str] = []
    i = 0
    in_string = False
    while i < len(text):
        if in_string:
            out.append(text[i])
            if text[i] == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if text[i] == '"':
                in_string = False
            i += 1
            continue
        if text[i] == '"':
            in_string = True
            out.append(text[i])
            i += 1
            continue
        if text.startswith("//", i):
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise SetupError("unterminated JSONC block comment")
            i = end + 2
            continue
        out.append(text[i])
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def load_json_file(path: Path) -> tuple[dict, bool, bytes | None]:
    """Return object, strict-JSON flag, and original bytes."""
    if not path.exists():
        return {}, True, None
    original = path.read_bytes()
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SetupError(f"{path} is not UTF-8: {exc}") from None
    if not text.strip():
        return {}, True, original
    try:
        data = json.loads(text)
        strict = True
    except json.JSONDecodeError:
        try:
            data = json.loads(_strip_jsonc(text))
            strict = False
        except (json.JSONDecodeError, SetupError) as exc:
            raise SetupError(f"{path} is not valid JSON/JSONC: {exc}") from None
    if not isinstance(data, dict):
        raise SetupError(f"{path} must contain a JSON object")
    return data, strict, original


def _require_object(container: dict, key: str, path: Path) -> dict:
    value = container.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SetupError(f"{path}: '{key}' must be an object")
    return dict(value)


def _require_array(container: dict, key: str, path: Path) -> list:
    value = container.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise SetupError(f"{path}: '{key}' must be an array")
    return list(value)


def opencode_mcp_entry(existing=None, timeout=DEFAULT_TIMEOUT) -> dict:
    timeout = _positive_timeout(timeout)
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        raise SetupError("mcp.isc-ai must be an object")
    result = dict(existing)
    result.pop("cwd", None)  # Removed from the partial setup design; not in the OpenCode MCP schema.
    environment = result.get("environment", {})
    if not isinstance(environment, dict):
        raise SetupError("mcp.isc-ai.environment must be an object")
    environment = {key: value for key, value in environment.items() if key not in STALE_ENV_KEYS}
    environment["IRIS_MCP_TIMEOUT"] = str(timeout)
    result.update({
        "type": "local",
        "command": ["isc-ai", "mcp"],
        "enabled": True,
        "environment": environment,
    })
    return result


def claude_mcp_entry(existing=None, timeout=DEFAULT_TIMEOUT) -> dict:
    timeout = _positive_timeout(timeout)
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        raise SetupError("mcpServers.isc-ai must be an object")
    result = dict(existing)
    environment = result.get("env", {})
    if not isinstance(environment, dict):
        raise SetupError("mcpServers.isc-ai.env must be an object")
    environment = {key: value for key, value in environment.items() if key not in STALE_ENV_KEYS}
    environment["IRIS_MCP_TIMEOUT"] = str(timeout)
    result.update({"command": "isc-ai", "args": ["mcp"], "env": environment})
    return result


def merge_opencode_config(
    data: dict,
    timeout=DEFAULT_TIMEOUT,
    configure_mcp: bool = True,
) -> tuple[dict, bool]:
    if not isinstance(data, dict):
        raise SetupError("OpenCode config must be an object")
    result = dict(data)
    if configure_mcp:
        mcp = _require_object(result, "mcp", Path("opencode.json"))
        mcp[MCP_NAME] = opencode_mcp_entry(mcp.get(MCP_NAME), timeout)
        result["mcp"] = mcp
    result["$schema"] = SCHEMA_URL
    # The logo is a TUI plugin: it is registered in tui.json, never in opencode.json.
    # An entry left here by an earlier version would make OpenCode try to load a TUI
    # component as a runtime plugin, so it is removed on sight.
    stale = [item for item in _require_array(result, "plugin", Path("opencode.json"))
             if not (isinstance(item, str)
                     and PurePosixPath(item.replace("\\", "/")).name == LOGO_RESOURCE.name)]
    if "plugin" in result:
        if stale:
            result["plugin"] = stale
        else:
            result.pop("plugin")
    return result, result != data


def merge_tui_config(data: dict, logo_path: str) -> tuple[dict, bool]:
    """Register the logo where OpenCode actually reads TUI plugins from."""
    if not isinstance(data, dict):
        raise SetupError("OpenCode TUI config must be an object")
    result = dict(data)
    result["$schema"] = TUI_SCHEMA_URL
    plugins = [item for item in _require_array(result, "plugin", Path("tui.json"))
               if not (isinstance(item, str)
                       and PurePosixPath(item.replace("\\", "/")).name == LOGO_RESOURCE.name)]
    plugins.append(logo_path)
    result["plugin"] = plugins
    return result, result != data


def merge_claude_project_config(data: dict, timeout=DEFAULT_TIMEOUT) -> tuple[dict, bool]:
    if not isinstance(data, dict):
        raise SetupError("Claude project config must be an object")
    result = dict(data)
    servers = _require_object(result, "mcpServers", Path(".mcp.json"))
    servers[MCP_NAME] = claude_mcp_entry(servers.get(MCP_NAME), timeout)
    result["mcpServers"] = servers
    return result, result != data


def _resolve_opencode_config(config_dir: Path, explicit: str | Path | None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().absolute()
    json_path = config_dir / "opencode.json"
    jsonc_path = config_dir / "opencode.jsonc"
    if json_path.exists() and jsonc_path.exists():
        raise SetupError(
            f"both {json_path} and {jsonc_path} exist; use --opencode-config to choose one"
        )
    return jsonc_path if jsonc_path.exists() else json_path


def _safe_relative(value: str, field: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise SetupError(f"manifest {field} must be a safe relative path: {value!r}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or "\\" in value
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SetupError(f"manifest {field} must be a safe relative path: {value!r}")
    return path


def load_harness_manifest(path: Path | None = None) -> dict:
    path = HARNESS_MANIFEST if path is None else Path(path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupError(f"cannot load harness manifest {path}: {exc}") from None
    if not isinstance(manifest, dict) or manifest.get("schema") != "isc-ai.harness-manifest.v1":
        raise SetupError("unsupported harness manifest schema")
    if not isinstance(manifest.get("bundle_version"), int) or manifest["bundle_version"] < 1:
        raise SetupError("harness bundle_version must be a positive integer")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise SetupError("harness manifest files must be an array")
    destinations: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise SetupError("harness manifest file entries must be objects")
        source = _safe_relative(item.get("source", ""), "source")
        destination = _safe_relative(item.get("destination", ""), "destination")
        digest = item.get("sha256")
        category = item.get("category")
        if not isinstance(category, str) or not category:
            raise SetupError("harness manifest category must be a non-empty string")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SetupError(f"invalid SHA-256 for {source}")
        if str(destination) in destinations:
            raise SetupError(f"duplicate harness destination: {destination}")
        destinations.add(str(destination))
        source_path = path.parent.joinpath(*source.parts)
        if not source_path.is_file() or _sha256(source_path.read_bytes()) != digest:
            raise SetupError(f"harness resource hash mismatch: {source}")
    return manifest


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"schema": "isc-ai.setup-state.v1", "managed": {}}
    data, strict, _ = load_json_file(path)
    if not strict:
        raise SetupError(f"{path} must be strict JSON")
    if data.get("schema") != "isc-ai.setup-state.v1" or not isinstance(data.get("managed"), dict):
        raise SetupError(f"{path} is not a supported isc-ai setup state file")
    return data


def _plan_file(category: str, source: Path, target: Path, digest: str, previous: str | None) -> FilePlan:
    if not target.exists():
        action = "install"
    elif not target.is_file():
        action = "conflict"
    else:
        current = _sha256(target.read_bytes())
        if current == digest:
            action = "noop"
        elif previous is not None and current == previous:
            action = "upgrade"
        else:
            action = "conflict"
    return FilePlan(category, source, target, digest, action)


def plan_harness(home: Path, state: dict, manifest: dict) -> tuple[list[FilePlan], list[str]]:
    plans: list[FilePlan] = []
    current_destinations: set[str] = set()
    managed = {
        key: value for key, value in state.get("managed", {}).items()
        if not key.startswith("opencode/")
    }
    for item in manifest["files"]:
        source_rel = _safe_relative(item["source"], "source")
        destination_rel = _safe_relative(item["destination"], "destination")
        destination = str(destination_rel)
        current_destinations.add(destination)
        target = _managed_target(
            home / ".claude",
            home / ".claude" / Path(*destination_rel.parts),
        )
        plans.append(_plan_file(
            item["category"],
            HARNESS_ROOT.joinpath(*source_rel.parts),
            target,
            item["sha256"],
            managed.get(destination),
        ))
    obsolete = sorted(set(managed) - current_destinations)
    return plans, obsolete


def _manual_action(path: Path, reason: str, snippet: dict) -> dict:
    return {
        "file": str(path),
        "reason": reason,
        "snippet": json.dumps(snippet, indent=2, ensure_ascii=False),
    }


def _install_opencode() -> bool:
    if shutil.which("opencode"):
        return False
    npm = shutil.which("npm")
    if not npm:
        raise SetupError("--with-opencode requires npm when opencode is not installed")
    result = subprocess.run([npm, "install", "-g", "opencode-ai"], check=False)
    if result.returncode != 0:
        raise SetupError(f"OpenCode package-manager install failed with exit code {result.returncode}")
    return True


@contextmanager
def _exclusive_setup_lock(config_root: Path):
    lock_path = _managed_target(config_root, config_root / ".setup.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _managed_target(config_root, lock_path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except FileExistsError:
        raise SetupError(f"another isc-ai setup apply is active: {lock_path}") from None
    identity = None
    try:
        os.write(descriptor, b"isc-ai setup\n")
        os.fsync(descriptor)
        identity = os.fstat(descriptor)
        yield lock_path
    finally:
        os.close(descriptor)
        try:
            current = lock_path.stat(follow_symlinks=False)
            if identity is not None and (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                lock_path.unlink()
                _fsync_directory(lock_path.parent)
        except FileNotFoundError:
            pass


def _run_setup_unlocked(
    *,
    apply: bool = False,
    check: bool = False,
    home=None,
    environ=None,
    configure_opencode: bool = True,
    install_logo: bool = True,
    install_harness: bool = True,
    adopt_harness: bool = False,
    claude_project=None,
    opencode_config=None,
    with_opencode: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Plan every requested change, then optionally apply conflict-free writes."""
    if apply and check:
        raise SetupError("--apply and --check are mutually exclusive")
    if with_opencode and not apply:
        raise SetupError("--with-opencode requires --apply")
    timeout = _positive_timeout(timeout)
    environ = dict(os.environ if environ is None else environ)
    home = (Path.home() if home is None else Path(home)).expanduser().resolve(strict=False)
    report = {"mode": "apply" if apply else "check", "actions": [], "conflicts": [], "manual": [], "obsolete": [], "adopted": []}
    writes: list[ManagedWrite] = []
    config_home = _config_home(home, environ)
    state_file = _managed_target(config_home / "isc-ai", state_path(home, environ))
    state = _load_state(state_file)
    next_managed = dict(state["managed"])

    config_path = None
    logo_plan = None
    config_dir = None
    if configure_opencode or install_logo:
        config_dir = opencode_config_dir(home, environ)
        config_path = _resolve_opencode_config(config_dir, opencode_config)
        config_root = config_path.parent if opencode_config is not None else config_dir
        config_path = _managed_target(config_root, config_path)

    if install_logo:
        # tui-plugins/ is where OpenCode loads TUI components from; plugins/ is for
        # runtime plugins and is not read by the TUI at all.
        logo_target = _managed_target(
            config_dir,
            config_dir / "tui-plugins" / LOGO_RESOURCE.name,
        )
        digest = _sha256(LOGO_RESOURCE.read_bytes())
        state_key = "opencode/tui-plugins/isc-logo.tsx"
        logo_plan = _plan_file("logo", LOGO_RESOURCE, logo_target, digest, next_managed.get(state_key))
        report["actions"].append({"target": str(logo_plan.target), "action": logo_plan.action, "category": logo_plan.category})
        if logo_plan.action == "conflict":
            report["conflicts"].append(str(logo_plan.target))
        else:
            next_managed[state_key] = digest
            if logo_plan.action in {"install", "upgrade"}:
                writes.append(ManagedWrite(config_dir, logo_plan.target, logo_plan.source.read_bytes()))
            # Registration lives in tui.json, next to the plugin directory.
            tui_path = _managed_target(config_dir, config_dir / "tui.json")
            tui_config, tui_strict, _ = load_json_file(tui_path)
            merged_tui, tui_changed = merge_tui_config(tui_config, str(logo_target))
            if tui_changed and not tui_strict:
                report["manual"].append(_manual_action(
                    tui_path,
                    "JSONC/commented JSON is preserved byte-for-byte; merge this snippet manually",
                    {"plugin": merged_tui["plugin"]},
                ))
            elif tui_changed:
                writes.append(ManagedWrite(config_dir, tui_path, _json_bytes(merged_tui)))
                report["actions"].append({
                    "target": str(tui_path),
                    "action": "update" if tui_path.exists() else "install",
                    "category": "logo",
                })
            else:
                report["actions"].append({"target": str(tui_path), "action": "noop", "category": "logo"})

    if configure_opencode or install_logo:
        config, strict, _ = load_json_file(config_path)
        merged, changed = merge_opencode_config(
            config,
            timeout=timeout,
            configure_mcp=configure_opencode,
        )
        if changed and not strict:
            snippet = {"$schema": SCHEMA_URL}
            if configure_opencode:
                snippet["mcp"] = {MCP_NAME: merged["mcp"][MCP_NAME]}
            report["manual"].append(_manual_action(
                config_path,
                "JSONC/commented JSON is preserved byte-for-byte; merge this snippet manually",
                snippet,
            ))
        elif changed:
            writes.append(ManagedWrite(config_root, config_path, _json_bytes(merged)))
            report["actions"].append({"target": str(config_path), "action": "update" if config_path.exists() else "install"})
        else:
            report["actions"].append({"target": str(config_path), "action": "noop"})

    if claude_project is not None:
        project = Path(claude_project).expanduser().resolve()
        if not project.is_dir():
            raise SetupError(f"--claude-project must be an existing directory: {project}")
        claude_path = _managed_target(project, project / ".mcp.json")
        config, strict, _ = load_json_file(claude_path)
        merged, changed = merge_claude_project_config(config, timeout)
        if changed and not strict:
            report["manual"].append(_manual_action(
                claude_path,
                "JSONC/commented JSON is preserved byte-for-byte; merge this snippet manually",
                {"mcpServers": {MCP_NAME: merged["mcpServers"][MCP_NAME]}},
            ))
        elif changed:
            writes.append(ManagedWrite(project, claude_path, _json_bytes(merged)))
            report["actions"].append({"target": str(claude_path), "action": "update" if claude_path.exists() else "install"})
        else:
            report["actions"].append({"target": str(claude_path), "action": "noop"})

    if install_harness:
        manifest = load_harness_manifest()
        plans, obsolete = plan_harness(home, state, manifest)
        report["bundle_version"] = manifest["bundle_version"]
        report["obsolete"].extend(str(home / ".claude" / Path(*PurePosixPath(item).parts)) for item in obsolete)
        for plan in plans:
            relative = plan.target.relative_to(home / ".claude").as_posix()
            action = plan.action
            if action == "conflict" and adopt_harness:
                # The installed copy was not written by this tool and differs from the
                # bundle. Adoption is the explicit way out: the bundle wins and its
                # digest is recorded, so later upgrades stop being conflicts. Without
                # this flag a conflict is never overwritten.
                action = "adopt"
                report["adopted"].append(str(plan.target))
            report["actions"].append({"target": str(plan.target), "action": action, "category": plan.category})
            if action == "conflict":
                report["conflicts"].append(str(plan.target))
                continue
            next_managed[relative] = plan.digest
            if action in {"install", "upgrade", "adopt"}:
                writes.append(ManagedWrite(home / ".claude", plan.target, plan.source.read_bytes()))

    if apply:
        state_out = {
            "schema": "isc-ai.setup-state.v1",
            "managed": next_managed,
        }
        state_content = _json_bytes(state_out)
        if not state_file.is_file() or state_file.read_bytes() != state_content:
            writes.append(ManagedWrite(config_home / "isc-ai", state_file, state_content))
        snapshots = _preflight_writes(writes)
        external_installed = False
        if with_opencode:
            external_installed = bool(_install_opencode())
            if external_installed:
                report["notes"] = ["OpenCode package-manager installation occurred before managed writes and is not rollbackable."]
        _apply_transaction(writes, external_install=external_installed, snapshots=snapshots)
    report["state_file"] = str(state_file)
    report["restart_required"] = bool(
        (config_path and any(item.target == config_path for item in writes))
        or (logo_plan and logo_plan.action in {"install", "upgrade"})
    )
    return report


def run_setup(
    *,
    apply: bool = False,
    check: bool = False,
    home=None,
    environ=None,
    configure_opencode: bool = True,
    install_logo: bool = True,
    install_harness: bool = True,
    adopt_harness: bool = False,
    claude_project=None,
    opencode_config=None,
    with_opencode: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Plan every requested change, locking only the explicitly mutating apply mode."""
    environ = dict(os.environ if environ is None else environ)
    resolved_home = (Path.home() if home is None else Path(home)).expanduser().resolve(strict=False)
    if not apply:
        return _run_setup_unlocked(
            apply=apply, check=check, home=resolved_home, environ=environ,
            configure_opencode=configure_opencode, install_logo=install_logo,
            install_harness=install_harness, adopt_harness=adopt_harness, claude_project=claude_project,
            opencode_config=opencode_config, with_opencode=with_opencode, timeout=timeout,
        )
    if check:
        raise SetupError("--apply and --check are mutually exclusive")
    config_root = _config_home(resolved_home, environ) / "isc-ai"
    with _exclusive_setup_lock(config_root):
        return _run_setup_unlocked(
            apply=True, check=False, home=resolved_home, environ=environ,
            configure_opencode=configure_opencode, install_logo=install_logo,
            install_harness=install_harness, adopt_harness=adopt_harness, claude_project=claude_project,
            opencode_config=opencode_config, with_opencode=with_opencode, timeout=timeout,
        )
