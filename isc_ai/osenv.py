"""Windows user-environment writer: the missing half of "a secret is never a literal".

A client config that writes `{env:NAME}`/`${NAME}`/`${input:NAME}` only works if the
OS environment already holds that name when the client spawns its MCP server. Nothing
in this repository wrote that half before this module: every reference suite.py renders
is unresolved unless something else already exported the variable.

Windows only, by design (v1): there is no winreg equivalent on macOS/Linux, and this
module refuses to guess one. `is_supported()` gates every call; a non-Windows caller
gets a clear, explicit error instead of a silent no-op.

Plan-first, like every other capability in this project: `plan()` never writes and
never reports a value, only a key name and an action ("would set"/"unchanged"),
mirroring secrets.py's `inventory()` convention exactly. `apply()` is the only
function that touches the registry, and it is never implicit.

Refuses, loudly, to write a project-discovery or config-path environment variable:
writing one of those to the *user's persistent* environment would silently override
the per-invocation resolution every other isc-ai command depends on, machine-wide,
until the user notices and unsets it by hand. DISALLOWED_KEYS is read from config.py's
own constants rather than duplicated here, so it stays correct if those names change.
"""

import ctypes
import sys

from isc_ai import config as isc_config
from isc_ai import setup as isc_setup
from isc_ai.setup import SetupError

try:
    import winreg
except ImportError:  # pragma: no cover - exercised only on non-Windows
    winreg = None

HKCU_ENVIRONMENT = r"Environment"

_HWND_BROADCAST = 0xFFFF
_WM_SETTINGCHANGE = 0x1A
_SMTO_ABORTIFHUNG = 0x0002
_BROADCAST_TIMEOUT_MS = 5000


def _discovery_key_names():
    """Every env var name today's project-discovery machinery treats as reserved.

    Reads config.py's own constants by name instead of duplicating them. Tolerates
    both the current single-name shape (`PROJECT_DIR_ENV`) and the plural
    current+legacy shape (`PROJECT_DIR_ENVS`) that a pending state-directory rename
    may introduce, so this guard keeps working without an edit here either way.
    """
    names = set()
    for plural, singular in (
        ("PROJECT_DIR_ENVS", "PROJECT_DIR_ENV"),
        ("PROJECT_CONFIG_ENVS", "PROJECT_CONFIG_ENV"),
    ):
        value = getattr(isc_config, plural, None)
        if value:
            names.update(value)
            continue
        single = getattr(isc_config, singular, None)
        if single:
            names.add(single)
    extra_packages = getattr(isc_config, "EXTRA_PACKAGES_ENV", None)
    if extra_packages:
        names.add(extra_packages)
    names.update(isc_setup.STALE_ENV_KEYS)
    return frozenset(names)


DISALLOWED_KEYS = _discovery_key_names()


def is_supported(platform_name=None):
    """True only on Windows: the only platform this module knows how to write."""
    return (platform_name if platform_name is not None else sys.platform) == "win32"


def _reject_disallowed(names):
    for name in names:
        if name in DISALLOWED_KEYS:
            raise SetupError(
                f"refusing to write '{name}' to the OS user environment: it is a "
                f"project-discovery/config key isc-ai resolves per invocation, and "
                f"a persistent machine-wide value would silently override that "
                f"resolution for every project until removed by hand"
            )


def _require_supported():
    if not is_supported():
        raise SetupError(
            f"the Windows user-environment writer is not supported on "
            f"'{sys.platform}' yet; set the variable through your shell's own "
            f"profile instead"
        )


def get(name):
    """Current value of `name` in HKCU\\Environment, or None. Read-only, no broadcast."""
    if not is_supported() or winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, HKCU_ENVIRONMENT, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except FileNotFoundError:
        return None


def plan(changes):
    """Dry-run report of what `apply()` would do, without touching anything.

    `changes` maps name -> new value. Returns
    `{"mode": "check", "sets": [{"name": ..., "action": "would set"|"unchanged"}],
    "skipped": []}` — the value itself never appears in the report, only the key
    name and the action, matching `secrets.py.inventory()`'s "never a value"
    convention. Raises `SetupError` for any disallowed name; it never silently
    drops a request.
    """
    _reject_disallowed(changes)
    sets = []
    for name, value in changes.items():
        current = get(name)
        action = "unchanged" if current == value else "would set"
        sets.append({"name": name, "action": action})
    return {"mode": "check", "sets": sets, "skipped": []}


def _set_registry_value(name, value):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, HKCU_ENVIRONMENT, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _broadcast_environment_change():
    """Tell already-running Explorer/shells to re-read HKCU\\Environment.

    A process already launched before this call (including isc-ai's own current
    process) does not see the new value without a restart — an OS limitation, not
    a bug; callers must say so in their own apply output.
    """
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(
        _HWND_BROADCAST, _WM_SETTINGCHANGE, 0, "Environment",
        _SMTO_ABORTIFHUNG, _BROADCAST_TIMEOUT_MS, ctypes.byref(result),
    )


def apply(changes):
    """Writes each name to HKCU\\Environment, then broadcasts once.

    Raises `SetupError` for a disallowed name (checked before anything is written)
    and for a non-Windows platform. Returns `{"mode": "apply", "sets": [{"name":
    ..., "action": "set"}]}` — again, never a value.
    """
    _reject_disallowed(changes)
    _require_supported()
    sets = []
    for name, value in changes.items():
        _set_registry_value(name, value)
        sets.append({"name": name, "action": "set"})
    if sets:
        _broadcast_environment_change()
    return {"mode": "apply", "sets": sets}


def unset(names):
    """Deletes registry values — the rollback path for this module's own writes.

    Raises `SetupError` for a disallowed name and for a non-Windows platform, same
    as `apply()`. Deleting a name that is already absent is not an error.
    """
    _reject_disallowed(names)
    _require_supported()
    results = []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, HKCU_ENVIRONMENT, 0, winreg.KEY_SET_VALUE) as key:
        for name in names:
            try:
                winreg.DeleteValue(key, name)
                results.append({"name": name, "action": "unset"})
            except FileNotFoundError:
                results.append({"name": name, "action": "already unset"})
    if results:
        _broadcast_environment_change()
    return {"mode": "apply", "sets": results}
