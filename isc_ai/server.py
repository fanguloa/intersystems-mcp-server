import sys
import json
import os
import re
import socket
import ssl
import urllib.request
import urllib.error
import urllib.parse
import base64
import hashlib
from pathlib import Path
from isc_ai.ccr_discovery import (
    LocalPolicyError, active_node, discover_namespace, validate_namespace,
)
from isc_ai import __version__
from isc_ai import config as iris_mcp_config
from isc_ai import feature_check
from isc_ai.config import ConfigError, SitePolicy, STATE_DIR, LEGACY_STATE_DIR

try:
    import sockshandler
    import socks
    HAS_SOCKS = True
except ImportError:
    HAS_SOCKS = False

# Protocol Configuration
PROTOCOL_VERSION = "2024-11-05"

# Default HTTP read timeout (seconds) for fast operations (reads, metadata).
DEFAULT_HTTP_TIMEOUT = 15

_CCR_MUTATION_CLASS = iris_mcp_config.MUTATION_CLASS
_CCR_MUTATION_TEMPLATE = iris_mcp_config.MUTATION_TEMPLATE_PATH
_CCR_SOURCE_MAX_BYTES = 524288


def _canonical_base_route_matches(policy, host, port, path_prefix, namespace, role, is_production):
    """True only for the exact non-production canonical BASE route of the site."""
    base = policy.canonical_base if isinstance(policy, SitePolicy) else None
    return base is not None and base.matches(host, port, path_prefix, namespace, role, is_production)


def _canonical_crlf_bytes(source_bytes):
    if not isinstance(source_bytes, bytes) or not source_bytes:
        raise LocalPolicyError("Packaged initializer source is empty.")
    if len(source_bytes) > _CCR_SOURCE_MAX_BYTES:
        raise LocalPolicyError("Packaged initializer source exceeds 512 KiB.")
    if source_bytes.startswith(b"\xef\xbb\xbf") or b"\x00" in source_bytes:
        raise LocalPolicyError("Packaged initializer source must not contain BOM or NUL bytes.")
    try:
        text = source_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise LocalPolicyError("Packaged initializer source is not strict UTF-8.") from None
    canonical = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n ") + "\n"
    canonical = canonical.replace("\n", "\r\n")
    canonical_bytes = canonical.encode("utf-8")
    if len(canonical_bytes) > _CCR_SOURCE_MAX_BYTES:
        raise LocalPolicyError("Canonical packaged initializer source exceeds 512 KiB.")
    declarations = re.findall(
        r"(?im)^\s*Class\s+([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\b", canonical,
    )
    if declarations != [_CCR_MUTATION_CLASS]:
        raise LocalPolicyError("Packaged initializer source must declare exactly MCP.CCRMutation.")
    return canonical_bytes


def _load_ccr_mutation_resource(policy):
    """Render the packaged facility for the configured site and hash the result."""
    try:
        template_text = _CCR_MUTATION_TEMPLATE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise LocalPolicyError("Packaged MCP.CCRMutation template is unavailable.") from None
    try:
        rendered = policy.render_mutation_source(template_text)
    except ConfigError as exc:
        raise LocalPolicyError(str(exc)) from None
    canonical_bytes = _canonical_crlf_bytes(rendered.encode("utf-8"))
    return canonical_bytes, hashlib.sha256(canonical_bytes).hexdigest()

def _cli_failure_message(text, limit=512):
    """Single-line, bounded rendering of an error for logs and CLI stderr."""
    text = str(text).replace("\r", " ").replace("\n", " ")
    if len(text) > limit:
        text = text[:limit] + "..."
    return text


def get_exec_timeout():
    """Timeout (seconds) for potentially long-running operations (code execution, compiles).
    Configurable via the IRIS_MCP_TIMEOUT environment variable. Falls back to 300s."""
    try:
        return int(os.environ.get("IRIS_MCP_TIMEOUT", "300"))
    except (ValueError, TypeError):
        return 300

def log_debug(message):
    """Write debug logging to stderr so it doesn't corrupt the JSON-RPC stdout channel."""
    sys.stderr.write(f"[DEBUG] {message}\n")
    sys.stderr.flush()


# Local append-only audit of arbitrary runtime execution (iris_execute_objectscript
# transport). Tool arguments are redacted from logs, so this file is the only trail
# of what code ran where. Records never store connection credentials or execution
# output, but they DO store the caller's payload code verbatim: a secret embedded in
# the code is recorded. Several MCP processes append concurrently, so every line is
# one write under a cross-process exclusive lock.
RUNTIME_AUDIT_DIR_ENV = "ISC_AI_AUDIT_DIR"
RUNTIME_AUDIT_FILE = "runtime.jsonl"
_AUDIT_LOCK_TIMEOUT_S = 2.0
_AUDIT_LOCK_RETRY_S = 0.05
_IS_WINDOWS = os.name == "nt"


def _runtime_audit_path():
    override = os.environ.get(RUNTIME_AUDIT_DIR_ENV)
    if override:
        directory = Path(override)
    else:
        # Keep appending to a legacy trail until 'isc-ai migrate' moves the state directory:
        # an audit split across two directories is harder to read than one in the old place.
        directory = Path.home() / STATE_DIR / "audit"
        legacy = Path.home() / LEGACY_STATE_DIR / "audit"
        if not directory.is_dir() and legacy.is_dir():
            directory = legacy
    if not directory.is_absolute():
        raise LocalPolicyError("Runtime audit directory must be an absolute path.")
    return directory / RUNTIME_AUDIT_FILE


def _audit_try_lock(fd):
    """One non-blocking attempt at an exclusive lock; raises OSError when held elsewhere."""
    if _IS_WINDOWS:
        import msvcrt
        # Lock byte 0 (a fixed region shared by every process); O_APPEND still
        # writes at end of file regardless of the current position.
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _audit_unlock(fd):
    if _IS_WINDOWS:
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def _audit_lock(fd):
    """Exclusive lock with a bounded retry; raises LocalPolicyError on timeout."""
    import time
    deadline = time.monotonic() + _AUDIT_LOCK_TIMEOUT_S
    while True:
        try:
            _audit_try_lock(fd)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise LocalPolicyError("Runtime audit file lock could not be obtained.") from None
            time.sleep(_AUDIT_LOCK_RETRY_S)


def _chmod_best_effort(path, mode):
    if _IS_WINDOWS:
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _append_runtime_audit(record):
    """Append one JSON line to the runtime audit file (creates the directory).

    The line is written with a single os.write on an O_APPEND descriptor while an
    exclusive cross-process lock is held. On POSIX the directory is 0700 and the
    file 0600 (best effort). Any failure raises; callers decide fail-closed or not.
    """
    _append_jsonl_locked(_runtime_audit_path(), record)


def _append_jsonl_locked(path, record):
    """Shared locked JSON-line appender (runtime audit and LIVE config window audit)."""
    directory = path.parent
    existed = directory.is_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not existed:
        _chmod_best_effort(directory, 0o700)
    data = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        _chmod_best_effort(path, 0o600)
        _audit_lock(fd)
        try:
            written = os.write(fd, data)
            if written != len(data):
                raise OSError("Short write to the runtime audit file.")
        finally:
            try:
                _audit_unlock(fd)
            except OSError:
                pass
    finally:
        os.close(fd)


def _audit_timestamp():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# LIVE CONFIG WINDOW (routes.live_config_window)
#
# Exceptional, expiring, ticket-bound route for Ensemble configuration on
# production-flagged targets. It never opens CODE WRITE, CCR, arbitrary runtime
# execution or anything on a non-production target. Backups and the audit trail
# live under <project>/.backup/live-config-window/.
# ---------------------------------------------------------------------------

LIVE_WINDOW_AUDIT_FILE = "audit.jsonl"
# A grant is issued per tool call after the gate passed; it only needs to cover
# the runners of that single call.
_LIVE_WINDOW_GRANT_TTL_S = 600
_LIVE_WINDOW_VALUE_MAX = 8192
_LIVE_WINDOW_SENSITIVE_SETTING_RE = re.compile(r"pass|secret|token|credential|key", re.IGNORECASE)
_LIVE_WINDOW_SETTING_NAME_RE = re.compile(r"%?[A-Za-z][A-Za-z0-9_]{0,127}")
# Characters that XML 1.0 cannot carry (the value ends up inside the class XData).
_XML_INVALID_CHARS_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
_COS_BASE64_RE = re.compile(r"[A-Za-z0-9+/]*={0,2}")
_LIVE_WINDOW_NOT_PRIMARY = "MCPLCW_NOT_PRIMARY"
# Prepended to EVERY window runner: the payload never runs on a mirror member that
# is not the primary, evaluated in the same IRIS process that would execute it.
_LIVE_WINDOW_PRIMARY_GUARD = (
    "If $SYSTEM.Mirror.IsMember() && '$SYSTEM.Mirror.IsPrimary() { Write \""
    + _LIVE_WINDOW_NOT_PRIMARY + "\", ! Quit }"
)
_LIVE_WINDOW_RESULT_FIELDS = frozenset({
    "status", "member", "primary", "before_present", "before", "after_present", "after",
    "running", "message",
})


def _live_window_today():
    """Local calendar date of the MCP host: the documented clock of the window."""
    import datetime
    return datetime.date.today()


def _live_window_audit_path(backup_root):
    path = Path(backup_root) / LIVE_WINDOW_AUDIT_FILE
    if not path.is_absolute():
        raise LocalPolicyError("LIVE config window audit path must be absolute.")
    return path


def _append_live_window_audit(backup_root, record):
    """One JSON line per window operation, same locked appender as the runtime audit."""
    _append_jsonl_locked(_live_window_audit_path(backup_root), record)


def _live_window_audit_preflight(backup_root):
    """Prove the audit file can be created, locked and appended BEFORE any write."""
    path = _live_window_audit_path(backup_root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        _audit_lock(fd)
        _audit_unlock(fd)
    finally:
        os.close(fd)


def _cos_string_expr(value):
    """Strict ObjectScript expression that evaluates to `value`.

    The caller string never reaches the ObjectScript parser: it travels as the
    base64 of its UTF-8 bytes inside a quoted literal whose alphabet cannot close
    the literal, concatenate, call $C() or start a new command.
    """
    if not isinstance(value, str):
        raise LocalPolicyError("LIVE config window values must be strings.")
    try:
        raw = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise LocalPolicyError("LIVE config window values must be valid Unicode text.") from None
    encoded = base64.b64encode(raw).decode("ascii")
    if not _COS_BASE64_RE.fullmatch(encoded):
        raise LocalPolicyError("LIVE config window value encoding failed.")
    return f'$ZConvert($System.Encryption.Base64Decode("{encoded}"),"I","UTF8")'


def _live_window_item_setting_code(production, item, target, setting, value, expected):
    """FIXED runner template for one compare-and-set of a production item setting.

    Every caller value is embedded through _cos_string_expr; nothing else varies.
    In ONE process it: aborts unless the node is not a mirror member or is the
    primary; opens Ens.Config.Production; requires exactly one item with that
    name and FindItemByConfigName to return it; locates the setting by
    Target+Name (adds it when absent); aborts on drift; sets, %Save(), then
    SaveToClass() so the class XData is updated. It writes one MCPLCW record.
    """
    lines = [
        f"Set mcpProd = {_cos_string_expr(production)}",
        f"Set mcpItemName = {_cos_string_expr(item)}",
        f"Set mcpTarget = {_cos_string_expr(target)}",
        f"Set mcpSetting = {_cos_string_expr(setting)}",
        f"Set mcpValue = {_cos_string_expr(value)}",
        f"Set mcpExpected = {_cos_string_expr(expected)}",
        'Set mcpStatus = "EXCEPTION", mcpMsg = "", mcpMember = "", mcpPrimary = "", mcpRunning = 0',
        'Set mcpBeforePresent = 0, mcpBefore = "", mcpAfterPresent = 0, mcpAfter = ""',
        "Try {",
        "    Set mcpMember = +$SYSTEM.Mirror.IsMember(), mcpPrimary = +$SYSTEM.Mirror.IsPrimary()",
        "    If '(('mcpMember) || mcpPrimary) {",
        '        Set mcpStatus = "NOT_PRIMARY", mcpMsg = "Mirror member is not the primary; nothing was written"',
        "    } Else {",
        "        Set mcpSC = $$$OK",
        "        Set mcpProduction = ##class(Ens.Config.Production).%OpenId(mcpProd, , .mcpSC)",
        "        If '$IsObject(mcpProduction) {",
        '            Set mcpStatus = "NO_PRODUCTION", mcpMsg = "Production configuration not found; nothing was written"',
        "        } Else {",
        "            Set mcpCount = 0",
        "            For mcpI = 1:1:mcpProduction.Items.Count() {",
        "                If mcpProduction.Items.GetAt(mcpI).Name = mcpItemName { Set mcpCount = mcpCount + 1 }",
        "            }",
        "            If mcpCount '= 1 {",
        '                Set mcpStatus = "ITEM_NOT_UNIQUE", mcpMsg = "Item name must match exactly one item; found "_mcpCount_"; nothing was written"',
        "            } Else {",
        "                Set mcpItem = mcpProduction.FindItemByConfigName(mcpItemName, .mcpSC)",
        "                If ('$IsObject(mcpItem)) || (mcpItem.Name '= mcpItemName) {",
        '                    Set mcpStatus = "NO_ITEM", mcpMsg = "FindItemByConfigName did not return the item; nothing was written"',
        "                } Else {",
        "                    Set mcpIndex = 0, mcpDup = 0",
        "                    For mcpI = 1:1:mcpItem.Settings.Count() {",
        "                        Set mcpS = mcpItem.Settings.GetAt(mcpI)",
        "                        If (mcpS.Target = mcpTarget) && (mcpS.Name = mcpSetting) {",
        "                            If mcpIndex { Set mcpDup = 1 } Else { Set mcpIndex = mcpI }",
        "                        }",
        "                    }",
        "                    If mcpDup {",
        '                        Set mcpStatus = "DUPLICATE_SETTING", mcpMsg = "Setting appears more than once for this target; nothing was written"',
        "                    } Else {",
        "                        If mcpIndex { Set mcpBeforePresent = 1, mcpBefore = mcpItem.Settings.GetAt(mcpIndex).Value }",
        "                        If mcpBefore '= mcpExpected {",
        '                            Set mcpStatus = "DRIFT", mcpMsg = "Current value differs from expected_current_value; nothing was written"',
        "                        } Else {",
        "                            Set mcpNew = ##class(Ens.Config.Setting).%New()",
        "                            Set mcpNew.Target = mcpTarget, mcpNew.Name = mcpSetting, mcpNew.Value = mcpValue",
        "                            If mcpIndex { Set mcpSC = mcpItem.Settings.SetAt(mcpNew, mcpIndex) } Else { Set mcpSC = mcpItem.Settings.Insert(mcpNew) }",
        "                            If $$$ISOK(mcpSC) { Set mcpSC = mcpProduction.%Save() }",
        "                            If $$$ISERR(mcpSC) {",
        '                                Set mcpStatus = "SAVE_FAILED", mcpMsg = $System.Status.GetOneErrorText(mcpSC)',
        "                            } Else {",
        "                                Set mcpSC = mcpProduction.SaveToClass(mcpItem)",
        "                                If $$$ISERR(mcpSC) {",
        '                                    Set mcpStatus = "SAVETOCLASS_FAILED", mcpMsg = $System.Status.GetOneErrorText(mcpSC)',
        "                                    If mcpIndex {",
        "                                        Set mcpOld = ##class(Ens.Config.Setting).%New()",
        "                                        Set mcpOld.Target = mcpTarget, mcpOld.Name = mcpSetting, mcpOld.Value = mcpBefore",
        "                                        Set mcpRevert = mcpItem.Settings.SetAt(mcpOld, mcpIndex)",
        "                                    } Else {",
        "                                        Do mcpItem.Settings.RemoveAt(mcpItem.Settings.Count())",
        "                                        Set mcpRevert = $$$OK",
        "                                    }",
        "                                    If $$$ISOK(mcpRevert) { Set mcpRevert = mcpProduction.%Save() }",
        '                                    Set mcpMsg = mcpMsg_"; configuration table revert "_$Select($$$ISOK(mcpRevert):"OK",1:"FAILED")',
        "                                } Else {",
        '                                    Set mcpStatus = "OK"',
        "                                }",
        "                            }",
        "                            For mcpI = 1:1:mcpItem.Settings.Count() {",
        "                                Set mcpS = mcpItem.Settings.GetAt(mcpI)",
        "                                If (mcpS.Target = mcpTarget) && (mcpS.Name = mcpSetting) { Set mcpAfterPresent = 1, mcpAfter = mcpS.Value }",
        "                            }",
        "                        }",
        "                    }",
        "                }",
        "            }",
        '            Set mcpActive = "", mcpState = 0',
        "            Set mcpDirSC = ##class(Ens.Director).GetProductionStatus(.mcpActive, .mcpState)",
        "            If $$$ISOK(mcpDirSC) && (mcpActive = mcpProd) && (mcpState = 1) { Set mcpRunning = 1 }",
        "        }",
        "    }",
        "} Catch mcpEx {",
        '    Set mcpStatus = "EXCEPTION", mcpMsg = mcpEx.DisplayString()',
        "}",
        'Write "MCPLCW|status=", mcpStatus, "|member=", +mcpMember, "|primary=", +mcpPrimary, '
        '"|before_present=", mcpBeforePresent, '
        '"|before=", $System.Encryption.Base64Encode($ZConvert(mcpBefore,"O","UTF8"),1), '
        '"|after_present=", mcpAfterPresent, '
        '"|after=", $System.Encryption.Base64Encode($ZConvert(mcpAfter,"O","UTF8"),1), '
        '"|running=", mcpRunning, '
        '"|message=", $System.Encryption.Base64Encode($ZConvert(mcpMsg,"O","UTF8"),1), !',
    ]
    return "\n".join(lines)


def _decode_live_window_b64(value):
    try:
        return base64.b64decode(value, validate=True).decode("utf-8", errors="strict")
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid base64 field") from None


def _parse_live_window_result(output):
    """Exactly one MCPLCW record with the expected fields; anything else raises."""
    lines = [line.strip() for line in str(output).splitlines() if line.strip()]
    if any(line == _LIVE_WINDOW_NOT_PRIMARY for line in lines):
        raise _LiveWindowNotPrimary("LIVE config window: the runner process is not on the mirror primary; nothing was executed.")
    if len(lines) != 1:
        raise LocalPolicyError("LIVE config window runner returned no single result record; outcome UNKNOWN, inspect manually.")
    try:
        fields = _parse_pipe_record(lines[0], "MCPLCW")
        if set(fields) != _LIVE_WINDOW_RESULT_FIELDS:
            raise ValueError("fields")
        for flag in ("member", "primary", "before_present", "after_present", "running"):
            if not fields[flag].isdigit():
                raise ValueError("flag")
        return {
            "status": fields["status"],
            "mirror_member": int(fields["member"]),
            "mirror_primary": int(fields["primary"]),
            "before_present": fields["before_present"] == "1",
            "before": _decode_live_window_b64(fields["before"]),
            "after_present": fields["after_present"] == "1",
            "after": _decode_live_window_b64(fields["after"]),
            "production_running": fields["running"] == "1",
            "message": _decode_live_window_b64(fields["message"]),
        }
    except ValueError:
        raise LocalPolicyError("LIVE config window runner returned a malformed result record; outcome UNKNOWN, inspect manually.") from None


def _production_definition_root(lines, production):
    """Parse the XData ProductionDefinition of a production class source (UDL lines)."""
    import xml.etree.ElementTree as ET
    if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
        raise LocalPolicyError("Production class source is not an array of lines.")
    text = "\n".join(lines)
    headers = re.findall(r"(?m)^\s*XData\s+ProductionDefinition\b", text)
    if len(headers) != 1:
        raise LocalPolicyError("Production class must contain exactly one XData ProductionDefinition.")
    body = text[re.search(r"(?m)^\s*XData\s+ProductionDefinition\b", text).end():]
    start = body.find("<Production")
    end = body.find("</Production>")
    if start < 0 or end < start or body.count("<Production") != 1 or body.count("</Production>") != 1:
        raise LocalPolicyError("Production XData does not hold exactly one <Production> element.")
    xml = body[start:end + len("</Production>")]
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise LocalPolicyError("Production XData must not declare a DOCTYPE or entities.")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise LocalPolicyError("Production XData is not well-formed XML.") from None
    if root.tag != "Production" or root.get("Name") != production:
        raise LocalPolicyError("Production XData Name does not match production_name.")
    return root


def _production_item_setting(root, item_name, target, setting):
    """(present, value) of one Item setting; the item must be unique, the setting too."""
    items = [element for element in root.findall("Item") if element.get("Name") == item_name]
    if len(items) != 1:
        raise LocalPolicyError(f"Production XData must contain exactly one Item named '{item_name}'; found {len(items)}.")
    matches = [element for element in items[0].findall("Setting")
               if element.get("Target") == target and element.get("Name") == setting]
    if len(matches) > 1:
        raise LocalPolicyError("Production XData lists this setting more than once for the target.")
    if not matches:
        return False, ""
    return True, matches[0].text or ""


def _production_canonical_without(root, item_name, target, setting):
    """Order-insensitive canonical form of the production minus one target setting.

    Used to prove SaveToClass changed nothing but the requested setting.
    """
    def is_target(parent, child):
        return (parent.tag == "Item" and parent.get("Name") == item_name and child.tag == "Setting"
                and child.get("Target") == target and child.get("Name") == setting)

    def canonical(element):
        children = tuple(sorted((canonical(child) for child in element if not is_target(element, child)),
                                key=repr))
        text = element.text or ""
        if element.tag != "Setting":
            text = text.strip()
        return (element.tag, tuple(sorted(element.attrib.items())), text, children)

    return canonical(root)


def _live_window_write_backup(backup_root, namespace, production, lines, stamp=None):
    """Immutable, timestamped local copy of the production class; never overwrites."""
    from datetime import datetime, timezone
    namespace = validate_namespace(namespace)
    AtelierClient._validate_identifier(production)
    if production.startswith("%"):
        raise LocalPolicyError("Production names under % are not configurable through the window.")
    root = Path(backup_root)
    directory = root / namespace
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stamp is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if not re.fullmatch(r"\d{8}T\d{12}Z", stamp):
        raise LocalPolicyError("Invalid backup timestamp.")
    path = directory / f"{production}-{stamp}.cls"
    if path.resolve().parent != directory.resolve():
        raise LocalPolicyError("Backup path escaped the window backup directory.")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(path), flags, 0o600)
    except FileExistsError:
        raise LocalPolicyError("Backup file already exists; refusing to overwrite it.") from None
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return path, hashlib.sha256(data).hexdigest()


class _LiveWindowNotPrimary(LocalPolicyError):
    """The in-process mirror guard stopped the runner before its payload ran."""


class _LiveWindowGrant:
    """Per-call capability that lets ONE window-gated tool call run fixed runners on
    a production target. Issued only by AtelierClient._issue_live_window_grant after
    is_live_config_allowed() passed; never built from tool arguments."""
    __slots__ = ("label", "ticket", "base_url", "issued")

    def __init__(self, label, ticket, base_url):
        import time
        self.label = label
        self.ticket = ticket
        self.base_url = base_url
        self.issued = time.monotonic()

    def fresh(self):
        import time
        return time.monotonic() - self.issued <= _LIVE_WINDOW_GRANT_TTL_S

def clean_to_ascii(content_str):
    replacements = {
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
        'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U',
        'ñ': 'n', 'Ñ': 'N',
        'â': 'a', 'ê': 'e', 'î': 'i', 'ô': 'o', 'û': 'u',
        'Â': 'A', 'Ê': 'E', 'Î': 'I', 'Ô': 'O', 'Û': 'U',
        'ã': 'a', 'õ': 'o', 'Ã': 'A', 'Õ': 'O',
        'ä': 'a', 'ë': 'e', 'ï': 'i', 'ö': 'o', 'ü': 'u',
        'Ä': 'A', 'Ë': 'E', 'Ï': 'I', 'Ö': 'O', 'Ü': 'U',
        'à': 'a', 'è': 'e', 'ì': 'i', 'ò': 'o', 'ù': 'u',
        'À': 'A', 'È': 'E', 'Ì': 'I', 'Ò': 'O', 'Ù': 'U',
        '—': '-', '–': '-',
        'â€”': '-', 'â€“': '-', 'â—': '-', 'â': '-', '€”': '-', '€“': '-',
        'ã³': 'o', 'ã­': 'i', 'ã©': 'e', 'ã¡': 'a', 'ãº': 'u',
        'Ã³': 'o', 'Ã­': 'i', 'Ã©': 'e', 'Ã¡': 'a', 'Ãº': 'u', 'Ã±': 'n', 'Ã‘': 'N',
        'Ã': 'A', '­': '',
        'âš': '[ALERTA]', '⚠': '[ALERTA]',
        '°': 'o', 'ª': 'a',
        '¿': '', '¡': '',
        '─': '-'
    }
    for bad, good in replacements.items():
        content_str = content_str.replace(bad, good)
    
    cleaned_chars = []
    for char in content_str:
        o = ord(char)
        if o <= 127:
            cleaned_chars.append(char)
        else:
            if o in (9472, 8212, 8211, 150, 151, 9552):
                cleaned_chars.append("-")
            elif o == 160:
                cleaned_chars.append(" ")
            else:
                cleaned_chars.append("?")
    return "".join(cleaned_chars)


def _is_sql_read_only(sql_query):
    """
    Robustly determine whether an SQL query is purely a read (SELECT, WITH, etc.).
    Strips line comments (--) and block comments (/* ... */) and verifies that the statement
    carries no mutating clause (INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, TRUNCATE, etc.).
    """
    if not sql_query:
        return True
        
    # 1. Remover comentarios de bloque /* ... */
    sql_clean = re.sub(r'/\*.*?\*/', '', sql_query, flags=re.DOTALL)
    
    # 2. Strip line comments (-- ... and // ...)
    lines = []
    for line in sql_clean.splitlines():
        line_clean = re.sub(r'--.*$', '', line)
        line_clean = re.sub(r'//.*$', '', line_clean)
        lines.append(line_clean)
    
    sql_clean = "\n".join(lines).strip()
    
    if not sql_clean:
        return True
        
    # 2b. Remover literales de cadena ('...' con '' escapado): una palabra clave
    # dentro de un literal (ej. Name LIKE '%Update%') no es una mutacion.
    sql_no_literals = re.sub(r"'(?:[^']|'')*'", "''", sql_clean)
    sql_upper = sql_no_literals.upper()
    
    # 3. Check for mutating keywords
    write_patterns = [
        r'\bINSERT\b',
        r'\bUPDATE\b',
        r'\bDELETE\b',
        r'\bDROP\b',
        r'\bALTER\b',
        r'\bCREATE\b',
        r'\bTRUNCATE\b',
        r'\bGRANT\b',
        r'\bREVOKE\b',
        r'\bREPLACE\b'
    ]
    
    for pat in write_patterns:
        if re.search(pat, sql_upper):
            return False
            
    # 4. Validar palabras clave iniciales permitidas para lectura: SELECT, WITH, EXPLAIN, SHOW, DESCRIBE, VALUES
    words = re.findall(r'\b[A-Z]+\b', sql_upper)
    if not words:
        return True
        
    first_word = words[0]
    allowed_reads = {"SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE", "VALUES"}
    return first_word in allowed_reads


# Conservative READ-category SQL policy for the arbitrary query transport. It is
# deliberately lexical and does not attempt to prove side-effect freedom: a
# SELECT may still invoke a user procedure. Anything outside this shape is denied.
_READ_SQL_FORBIDDEN_TOKENS = (
    "CALL", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "DROP", "ALTER", "CREATE",
    "GRANT", "REVOKE", "BUILD", "TUNE",
)
_READ_SQL_FORBIDDEN_PREFIXES = ("%SYSTEM.", "$SYSTEM.")


def _mask_sql_string_literals(sql_query):
    """Replace single-quoted literals ('' escapes a quote) with ''; None if unterminated.

    Double-quoted delimited identifiers and comments are copied verbatim so they stay
    subject to the token scan, and a quote inside them never opens a literal.
    """
    out = []
    index, length = 0, len(sql_query)
    while index < length:
        char = sql_query[index]
        if char == "'":
            cursor = index + 1
            while True:
                close = sql_query.find("'", cursor)
                if close < 0:
                    return None
                if sql_query.startswith("''", close):
                    cursor = close + 2
                    continue
                break
            out.append("''")
            index = close + 1
            continue
        if char == '"':
            cursor = index + 1
            while True:
                close = sql_query.find('"', cursor)
                if close < 0:
                    return None
                if sql_query.startswith('""', close):
                    cursor = close + 2
                    continue
                break
            out.append(sql_query[index:close + 1])
            index = close + 1
            continue
        if sql_query.startswith("--", index) or sql_query.startswith("//", index):
            close = sql_query.find("\n", index)
            close = length if close < 0 else close
            out.append(sql_query[index:close])
            index = close
            continue
        if sql_query.startswith("/*", index):
            close = sql_query.find("*/", index + 2)
            close = length if close < 0 else close + 2
            out.append(sql_query[index:close])
            index = close
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _read_sql_policy_violation(sql_query):
    """Return a bounded denial reason, or None when the statement passes the policy."""
    if not isinstance(sql_query, str) or not sql_query.strip():
        return "SQL policy: a non-empty SQL statement is required."
    if not re.match(r"SELECT\b", sql_query.lstrip(), flags=re.IGNORECASE):
        return "SQL policy: only statements that start with SELECT are accepted on the read transport."
    # Keywords inside string literals (Name LIKE '%Create%') are data, not statements.
    masked = _mask_sql_string_literals(sql_query)
    if masked is None:
        return "SQL policy: unterminated string literal or delimited identifier."
    upper = masked.upper()
    for token in _READ_SQL_FORBIDDEN_TOKENS:
        if re.search(r"\b" + token + r"\b", upper):
            return f"SQL policy: the token {token} is not accepted on the read transport."
    for prefix in _READ_SQL_FORBIDDEN_PREFIXES:
        if prefix in upper:
            return f"SQL policy: references to {prefix}* are not accepted on the read transport."
    return None


def _parse_pipe_record(line, prefix):
    parts = line.split("|")
    if not parts or parts[0] != prefix:
        raise ValueError(f"Malformed {prefix} record")

    fields = {}
    for part in parts[1:]:
        if not part or "=" not in part:
            raise ValueError(f"Malformed segment in record {prefix}")
        key, value = part.split("=", 1)
        if not key or key in fields:
            raise ValueError(f"Duplicate or empty field in record {prefix}")
        fields[key] = value
    return fields


def _decode_record_value(value):
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ValueError("Invalid URL encoding")
    encoded = value.replace("+", " ")
    return urllib.parse.unquote_to_bytes(encoded).decode("utf-8", errors="strict")


def _classify_ccr_terminal_result(result, allowed_actions):
    lines = [line.strip() for line in str(result).splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("ERROR")]
    if errors:
        return "error", None, errors[-1]
    if not lines:
        return "error", None, "No terminal record was received"

    terminal = lines[-1]
    if terminal.startswith("PENDING_VERIFICATION|"):
        try:
            fields = _parse_pipe_record(terminal, "PENDING_VERIFICATION")
            if set(fields) != {"local_id", "local_name", "message"}:
                raise ValueError("Campos PENDING_VERIFICATION invalids")
            if not fields["local_id"].isdigit():
                raise ValueError("invalid local_id")
            fields["local_name"] = _decode_record_value(fields["local_name"])
            fields["message"] = _decode_record_value(fields["message"])
            if not fields["local_name"].strip() or not fields["message"].strip():
                raise ValueError("PENDING_VERIFICATION incompleto")
            if "DO NOT AUTO-RETRY" not in fields["message"]:
                raise ValueError("PENDING_VERIFICATION without a do-not-retry instruction")
        except (UnicodeDecodeError, ValueError) as exc:
            return "error", None, f"Malformed PENDING_VERIFICATION record: {exc}"
        return "pending", fields, ""

    if not terminal.startswith("VERIFIED|"):
        return "error", None, "No terminal VERIFIED confirmation was received from the central server"

    try:
        fields = _parse_pipe_record(terminal, "VERIFIED")
        required = {"action", "local_id", "local_name", "server_id", "server_name"}
        allowed = required | {"bytes_removed", "rollback_copies"}
        if not required.issubset(fields) or not set(fields).issubset(allowed):
            raise ValueError("Campos VERIFIED invalids")
        if fields["action"] not in allowed_actions:
            raise ValueError("VERIFIED action not allowed")
        if not fields["local_id"].isdigit():
            raise ValueError("invalid local_id")
        if not fields["server_id"] or not fields["server_id"].isdigit():
            raise ValueError("server_id must be numeric and non-empty")
        for optional_numeric in ("bytes_removed", "rollback_copies"):
            if optional_numeric in fields and not fields[optional_numeric].isdigit():
                raise ValueError(f"{optional_numeric} invalid")
        fields["local_name"] = _decode_record_value(fields["local_name"])
        fields["server_name"] = _decode_record_value(fields["server_name"])
        if not fields["local_name"].strip() or not fields["server_name"].strip():
            raise ValueError("VERIFIED names cannot be empty")
        if fields["local_name"] != fields["server_name"]:
            raise ValueError("The local and central names do not match exactly")
    except (UnicodeDecodeError, ValueError) as exc:
        return "error", None, f"Malformed VERIFIED record: {exc}"
    return "verified", fields, ""


def _classify_ccr_download_load_result(result, load, expected_itemset_id):
    lines = [line.strip() for line in str(result).splitlines() if line.strip()]
    stages = ["DOWNLOADED", "IMPORTED"]
    if load:
        stages.extend(["LOADED_OS", "LOADED_NS", "CONFIRMED"])

    def validate_stage(line, stage, imported_id):
        if stage == "DOWNLOADED":
            fields = _parse_pipe_record(line, stage)
            if (set(fields) != {"isid", "file"} or fields["isid"] != str(expected_itemset_id)
                    or not fields["file"]):
                raise ValueError("Malformed DOWNLOADED record or unexpected ItemSet")
        elif stage == "IMPORTED":
            fields = _parse_pipe_record(line, stage)
            if set(fields) != {"local_id", "ccr", "source", "target"} or not fields["local_id"].isdigit():
                raise ValueError("Malformed IMPORTED record")
            imported_id = fields["local_id"]
        elif stage == "LOADED_OS":
            if line != "LOADED_OS":
                raise ValueError("Malformed LOADED_OS record")
        elif stage == "CONFIRMED":
            fields = _parse_pipe_record(line, stage)
            if set(fields) != {"isid"} or fields["isid"] != str(expected_itemset_id):
                raise ValueError("Malformed CONFIRMED record")
        else:
            fields = _parse_pipe_record(line, stage)
            if set(fields) != {"itemset"} or fields["itemset"] != imported_id:
                raise ValueError("Inconsistent LOADED_NS record")
        return imported_id

    error_index = next((index for index, line in enumerate(lines) if line.startswith("ERROR")), None)
    if error_index is not None:
        valid_stages = []
        imported_id = None
        for index, line in enumerate(lines[:error_index]):
            if index >= len(stages):
                break
            try:
                imported_id = validate_stage(line, stages[index], imported_id)
            except ValueError:
                break
            valid_stages.append(stages[index])

        error_line = lines[error_index]
        phase_by_error = {
            "ERROR_DOWNLOAD": ("download", 0),
            "ERROR_IMPORT": ("import", 1),
            "ERROR_LOADTOOS": ("load_to_os", 2),
            "ERROR_LOADTONS": ("load_to_ns", 3),
            "ERROR_CONFIRM": ("confirm", 4),
            "ERROR_CONTROL": ("control", 0),
        }
        error_code = error_line.split(":", 1)[0].split("|", 1)[0]
        failed_phase, expected_prior = phase_by_error.get(error_code, ("unknown", None))
        prior_is_exact = len(valid_stages) == error_index
        if expected_prior is not None and (not prior_is_exact or len(valid_stages) != expected_prior):
            failed_phase = "unknown"
        load_attempted = "LOADED_OS" in valid_stages or error_code in {"ERROR_LOADTOOS", "ERROR_LOADTONS", "ERROR_CONFIRM"}
        load_may_have_started = load_attempted or (load and "IMPORTED" in valid_stages)
        failure_state = {
            "status": "DOWNLOAD_LOAD_FAILED",
            "load_attempted": load_attempted,
            "load_may_have_started": load_may_have_started,
            "phase_reached": valid_stages[-1] if valid_stages else None,
            "failed_phase": failed_phase,
            "possible_partial_load": load_may_have_started,
            "automatic_retry": False,
        }
        return False, lines, error_line, failure_state

    phase_by_stage = {
        "DOWNLOADED": "download",
        "IMPORTED": "import",
        "LOADED_OS": "load_to_os",
        "LOADED_NS": "load_to_ns",
        "CONFIRMED": "confirm",
    }
    valid_stages = []
    imported_id = None
    failure = None
    failed_phase = None
    for index, stage in enumerate(stages):
        if index >= len(lines):
            failure = f"Output truncated before {stage}"
            failed_phase = phase_by_stage[stage]
            break
        try:
            line = lines[index]
            imported_id = validate_stage(line, stage, imported_id)
        except ValueError as exc:
            failure = str(exc)
            failed_phase = phase_by_stage[stage]
            break
        valid_stages.append(stage)

    if failure is None and (len(lines) != len(stages) + 1 or lines[-1] != "SUCCESS"):
        failure = "SUCCESS must be a single terminal record"
        failed_phase = "success_terminal"
    if failure is not None:
        load_attempted = any(stage in valid_stages for stage in ("LOADED_OS", "LOADED_NS"))
        load_may_have_started = load_attempted or (load and "IMPORTED" in valid_stages)
        failure_state = {
            "status": "DOWNLOAD_LOAD_FAILED",
            "load_attempted": load_attempted,
            "load_may_have_started": load_may_have_started,
            "phase_reached": valid_stages[-1] if valid_stages else None,
            "failed_phase": failed_phase,
            "possible_partial_load": load_may_have_started,
            "automatic_retry": False,
        }
        return False, lines, failure, failure_state
    return True, lines, "", None


def _parse_ccr_list_result(result):
    lines = [line.strip() for line in str(result).splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("ERROR")]
    if errors:
        raise ValueError(errors[-1])
    if not lines or not lines[0].startswith("OK|"):
        raise ValueError("WSList did not return the required OK record")
    if sum(line.startswith("OK|") for line in lines) != 1:
        raise ValueError("WSList devolvio multiples registros OK")
    if any(not (line.startswith("OK|") or line.startswith("ITEM|")) for line in lines):
        raise ValueError("WSList returned an unknown record")

    header_fields = _parse_pipe_record(lines[0], "OK")
    if set(header_fields) != {"ccr_enc", "env_enc"}:
        raise ValueError("Malformed OK record")
    header = {
        "ccr": _decode_record_value(header_fields["ccr_enc"]),
        "env": _decode_record_value(header_fields["env_enc"]),
    }

    items = []
    seen_ids = set()
    for line in lines[1:]:
        fields = _parse_pipe_record(line, "ITEM")
        if set(fields) != {"id", "name_enc", "window_enc", "plan_enc"}:
            raise ValueError("Malformed ITEM record")
        if not fields["id"].isdigit() or fields["id"] in seen_ids:
            raise ValueError("Invalid or duplicate ServerItemSetID")
        seen_ids.add(fields["id"])
        items.append({
            "id": fields["id"],
            "name": _decode_record_value(fields["name_enc"]),
            "window": _decode_record_value(fields["window_enc"]),
            "plan": _decode_record_value(fields["plan_enc"]),
        })
    return header, items


def _markdown_table_cell(value):
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


class AtelierClient:
    # Identifiers, never filesystem/URL paths. Leading % is an IRIS name marker,
    # not URL encoding; encode it exactly once when constructing a doc endpoint.
    _CLASS_NAME_RE = re.compile(r'%?[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*')
    _DOCUMENT_EXTENSIONS = frozenset({"cls", "mac", "int", "inc", "bas", "obj"})
    _EPHEMERAL_RUNNER_RE = re.compile(r"User\.McpTmpRun[0-9a-f]{12}")
    _EPHEMERAL_RESULT_MAXLEN = 1048576
    _EPHEMERAL_OUTPUT_LIMIT = 180000
    _EPHEMERAL_CHANGE_SQL = (
        "SELECT TOP 1 InternalName FROM %Studio_SourceControl.Change "
        "WHERE Committed = 0 AND UPPER(InternalName) = UPPER(?)"
    )
    # Site vocabulary (production markers/roles, HA nodes, VIP exceptions, package
    # catalog, CCR format) comes from the injected SitePolicy. Nothing here names a
    # customer, host or namespace.
    # Same-process CCR guard. Evaluated inside the IRIS process that will execute
    # (runner-embedded operations) or immediately before a REST write (probe). It
    # yields exactly one of the admissible states below; any other outcome blocks.
    _GUARD_STATES = frozenset({"CONTROLLED_ACTIVE", "SUSPENDED", "ABSENT"})
    _GUARD_STATE_LINES = (
        'Set mcpGuardEngine = ##class(%Dictionary.CompiledClass).%ExistsId("TC.ChangeControl.Interface")',
        'Set mcpGuardBinding = "", mcpGuardSuspended = 0, mcpGuardLevel = 0',
        'If mcpGuardEngine {',
        '    Set mcpGuardBinding = $ClassMethod("TC.ChangeControl.Interface", "ChangeControlClassGet")',
        '    Set mcpGuardSuspended = +$ClassMethod("TC.ChangeControl.Interface", "IsSuspended")',
        '    Set mcpGuardLevel = +$ClassMethod("TC.ChangeControl.Interface", "GetSuspendLevel")',
        '}',
        'Set mcpGuardSource = ##class(%Studio.SourceControl.Interface).SourceControlClassGet()',
        'Set mcpGuardState = "MISMATCH"',
        'If mcpGuardEngine && (mcpGuardBinding = "TC.ChangeControl.CCR") && (mcpGuardSource = "%Studio.SourceControl.ISC") {',
        '    If mcpGuardSuspended || (mcpGuardLevel \'= 0) {',
        '        Set mcpGuardState = "SUSPENDED"',
        '    } Else {',
        '        Set mcpGuardState = "CONTROLLED_ACTIVE"',
        '    }',
        '} ElseIf (\'mcpGuardEngine) && (mcpGuardBinding = "") && (mcpGuardSource = "") {',
        '    Set mcpGuardState = "ABSENT"',
        '}',
    )
    _GUARD_REPORT_LINE = (
        'Write "MCPGUARD|state=", mcpGuardState, "|engine=", +mcpGuardEngine, '
        '"|binding=", $ZConvert(mcpGuardBinding, "O", "URL"), '
        '"|source_binding=", $ZConvert(mcpGuardSource, "O", "URL"), '
        '"|suspended=", mcpGuardSuspended, "|suspend_level=", mcpGuardLevel, !'
    )
    _GUARD_REQUIRE_CONTROLLED_LINES = (
        'If mcpGuardState \'= "CONTROLLED_ACTIVE" {',
        '    Write "ERROR_CONTROL|state=", mcpGuardState, "|message=Same-process CCR control is not CONTROLLED_ACTIVE; operation not executed", !',
        '    Quit',
        '}',
    )

    def __init__(self, host, port, path_prefix, namespace, username, password, is_production=False, env_role="", ccr_affected=None, proxy=None, policy=None, scheme="http", verify_ssl=True, production_read=False):
        namespace = validate_namespace(namespace)
        if policy is None:
            policy = SitePolicy()
        elif not isinstance(policy, SitePolicy):
            raise LocalPolicyError("Client policy must be a SitePolicy instance.")
        self.policy = policy
        self.host = host
        self.port = port
        self.path_prefix = path_prefix
        self.namespace = namespace
        self.is_production = is_production
        # Legacy argument retained for callers, NEVER used as discovery evidence.
        self.env_role = env_role
        self.ccr_affected = None
        self.proxy = proxy or os.environ.get("ALL_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        # Transport scheme is configuration ("scheme": "http"|"https"). When the front-end Apache
        # forces HTTPS with a certificate that does not match the configured host (e.g. an IP),
        # "verify_ssl": false disables certificate validation for THIS target only. Never a default.
        scheme = (scheme or "http").lower()
        if scheme not in ("http", "https"):
            raise LocalPolicyError("Unsupported transport scheme; use http or https.")
        self.scheme = scheme
        self.verify_ssl = bool(verify_ssl)
        # "production_read": true is an explicit per-entry opt-in that opens ONLY the READ
        # gate on a production target. Runtime, runners, CCR and code writes stay blocked.
        # Identity check (not truthiness) so a stray string such as "false" never enables it.
        self.production_read = production_read is True
        self.base_url = f"{scheme}://{host}:{port}{path_prefix}/api/atelier/v1/{namespace}"
        self.root_url = f"{scheme}://{host}:{port}{path_prefix}/api/atelier/"
        
        # Configure Basic Authentication
        auth_str = f"{username}:{password}"
        self.auth_header = f"Basic {base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')}"

        self.opener = self._create_opener()

    def _ssl_handlers(self):
        """HTTPS handler honouring verify_ssl; empty for plain HTTP or verified HTTPS (defaults)."""
        if getattr(self, "scheme", "http") != "https" or getattr(self, "verify_ssl", True):
            return []
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return [urllib.request.HTTPSHandler(context=context)]

    def _create_opener(self):
        if not self.proxy:
            return urllib.request.build_opener(*self._ssl_handlers())
            
        proxy_url = self.proxy.strip()
        parsed = urllib.parse.urlparse(proxy_url)
        scheme = parsed.scheme.lower()
        
        if scheme in ("socks5", "socks5h", "socks4"):
            if not HAS_SOCKS:
                raise LocalPolicyError("Configured SOCKS transport unavailable; target not contacted.")
            
            proxy_type = sockshandler.socks.PROXY_TYPE_SOCKS5 if scheme.startswith("socks5") else sockshandler.socks.PROXY_TYPE_SOCKS4
            remote_dns = True if scheme == "socks5h" else False
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 1080
            username = parsed.username
            password = parsed.password
            
            handler = sockshandler.SocksiPyHandler(
                proxy_type,
                host,
                port,
                remote_dns,
                username,
                password
            )
            return urllib.request.build_opener(handler, *self._ssl_handlers())
        elif scheme in ("http", "https"):
            return urllib.request.build_opener(
                urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url}), *self._ssl_handlers()
            )
        else:
            return urllib.request.build_opener(*self._ssl_handlers())

    @staticmethod
    def _escape_cos(value):
        """Escape a value so it can be embedded safely in an ObjectScript string literal.
        In COS a double quote is escaped by DOUBLING it ('' not \\'); a newline would break the literal."""
        return str(value).replace('\r', ' ').replace('\n', ' ').replace('"', '""')

    @staticmethod
    def _redact_secret(value, secret):
        text = str(value)
        if not secret:
            return text
        secret_text = str(secret)
        variants = {
            secret_text,
            secret_text.replace("\r", " ").replace("\n", " ").replace('"', '""'),
            urllib.parse.quote(secret_text, safe=""),
            urllib.parse.quote_plus(secret_text, safe=""),
        }
        variants |= {variant.lower() for variant in variants if "%" in variant}
        variants |= {variant.upper() for variant in variants if "%" in variant}
        for variant in sorted((item for item in variants if item), key=len, reverse=True):
            text = text.replace(variant, "<redacted>")
        return text

    @classmethod
    def _validate_class_name(cls, name):
        """Validate a class identifier with an optional exact .cls suffix."""
        if not isinstance(name, str):
            raise LocalPolicyError("Invalid class identifier.")
        base = name[:-4] if name.endswith(".cls") else name
        cls._validate_identifier(base)
        return name

    @classmethod
    def _validate_identifier(cls, name):
        if (not isinstance(name, str) or not cls._CLASS_NAME_RE.fullmatch(name)
                or re.search(r"%(?:2[eEfF]|5[cC]|25|3[fF]|23|00)", name)):
            raise LocalPolicyError("Invalid document identifier: paths and encoded separators are prohibited.")

    @classmethod
    def _validate_document_name(cls, name):
        if not isinstance(name, str) or "." not in name:
            raise LocalPolicyError("A document identifier with a supported extension is required.")
        stem, extension = name.rsplit(".", 1)
        cls._validate_identifier(stem)
        if extension not in cls._DOCUMENT_EXTENSIONS:
            raise LocalPolicyError("Unsupported document extension.")
        return name

    @classmethod
    def _class_document_name(cls, name):
        """Class tools never interpret a class component as a routine suffix."""
        cls._validate_class_name(name)
        return name if name.endswith(".cls") else name + ".cls"

    @classmethod
    def _compile_document_name(cls, name):
        """Compile accepts exact lowercase document suffixes; otherwise a class.

        Append .cls explicitly for a class whose final component is a lowercase
        document suffix (for example Custom.obj.cls). No case normalization.
        """
        cls._validate_identifier(name)
        if "." in name and name.rsplit(".", 1)[-1] in cls._DOCUMENT_EXTENSIONS:
            return cls._validate_document_name(name)
        return cls._class_document_name(name)

    @classmethod
    def _tracked_document_name(cls, class_name=None, document_name=None):
        """Resolve the exact document for CCR tools: a class (suffix .cls implied,
        never inferred from components) or an explicit document with its extension
        (Custom.Site.Macros.inc). Exactly one of the two must be given."""
        if (class_name is None) == (document_name is None):
            raise LocalPolicyError("Provide exactly one of class_name or document_name.")
        if document_name is not None:
            return cls._validate_document_name(document_name)
        return cls._class_document_name(class_name)

    # ------------------------------------------------------------------
    # Gate categories. READ and RUNTIME never consult CCR state. CODE WRITE uses
    # the same-process CCR guard; the external namespace discovery
    # (ccr_discovery.discover_namespace) is informational only.
    # ------------------------------------------------------------------

    def _production_gate_reason(self):
        """Shared production block. Returns None when the target is non-production."""
        self._validate_target()
        if getattr(self, "is_production", False):
            return "Blocked: the server is marked as production (is_production=true) in the configuration"
        ns_upper = self.namespace.upper()
        if any(marker in ns_upper for marker in self.policy.production_markers):
            return f"Blocked: namespace '{self.namespace}' looks like a production system"
        role = (getattr(self, "env_role", "") or "").strip().upper()
        if role in self.policy.production_roles:
            return f"Blocked: environment '{role}' is production"
        return None

    def _configured_role(self):
        return (getattr(self, "env_role", "") or "").strip().upper()

    def _is_direct_ha_node(self):
        return self.policy.is_direct_ha_node(self.host)

    def _runtime_node_route_allowed(self):
        """RUNTIME never blocks on direct HA nodes.

        Runtime operations (item restart, diagnostics, queues) target a specific
        node on purpose: the clinical UAT VIP publishes no Atelier endpoint and
        an operator may need to act on a given node. Production/LIVE remain
        blocked by _production_gate_reason; CODE WRITE keeps its own route rules.
        """
        return True, ""

    def is_read_allowed(self):
        """READ gate: production block only. No CCR consultation, no runner.

        An entry with production_read=true may read a production target. This opens
        the READ gate only: every runner, RUNTIME, CCR and CODE WRITE gate evaluates
        _production_gate_reason independently and keeps blocking production.
        """
        reason = self._production_gate_reason()
        if reason and getattr(self, "production_read", False) is True:
            return True, "Read allowed on production target by explicit production_read opt-in."
        if reason:
            return False, reason
        return True, "Read allowed on non-production target."

    def _require_read(self):
        allowed, reason = self.is_read_allowed()
        if not allowed:
            raise LocalPolicyError(reason)

    # Package catalog: the project SitePolicy grants packages per namespace and
    # role. IRIS_EXTRA_PACKAGES (process environment) may extend it for the BASE
    # role only; core, Region, MCP.* and the facility package can never be granted.

    def _cataloged_package_allowed(self, document_name):
        """Project the site package catalog onto this namespace/role."""
        return self.policy.package_allowed(self.namespace, self._configured_role(), document_name)

    def _require_cataloged_package(self, document_name):
        self._validate_document_name(document_name)
        if not self._cataloged_package_allowed(document_name):
            raise LocalPolicyError(
                "Package not cataloged for this namespace/role; code write blocked "
                "in every CCR state."
            )

    def _code_write_route(self, promotion=False):
        """Static environment route for code writes; None blocks before any runner.

        Returns "BASE" (origin of changes), "UAT_EXCEPTION" (a configured UAT write
        exception: VIP or demonstrably active mirror node) or "UAT_PROMOTION"
        (sanctioned ItemSet promotion into the configured UAT VIP).

        Fail-closed: without a loaded project configuration the HA-node list and
        the write exceptions are unknown, so no route exists at all.
        """
        if not self.policy.configured:
            return None, ("Code write blocked: no project configuration loaded (.isc-ai/config.json); "
                          "HA-node and route policy are unknown. Run 'isc-ai init' in the deployment directory.")
        role = self._configured_role()
        if promotion:
            # Sanctioned ItemSet promotion is admitted in every configured environment
            # (BASE, UAT, TEST, LIVE...) and on direct HA nodes: the ItemSet comes only
            # from the central CCR server by ISID, the runner embeds the same-process
            # CONTROLLED_ACTIVE guard, and a mirror backup fails at its read-only database.
            if role in ("BASE", "UAT", "TEST", "QA", "LIVE"):
                return "PROMOTION", f"Promotion route: configured {role} target."
            return None, "CCR promotion requires a configured BASE, UAT, TEST, QA or LIVE role."
        if role == "BASE":
            if self._is_direct_ha_node():
                exceptions = list(self.policy.effective_capability_exceptions(
                    "code_write", namespace=self.namespace, host=self.host,
                    path_prefix=self.path_prefix, role=role))
                for exception in exceptions:
                    if not exception.requires_active_node:
                        return "BASE", f"Configured BASE write exception '{exception.label}'."
                for exception in exceptions:
                    if active_node(self.namespace, self._read_metadata_query):
                        return "BASE", (f"Configured BASE write exception '{exception.label}' on the "
                                        "ACTIVE mirror node (production jobs observed).")
                    return None, (f"Direct HA node mutation blocked: node is not executing the production "
                                 f"for '{exception.label}' (backup or stopped); use the active node or the configured VIP.")
                return None, "Direct HA node mutation blocked; use the configured canonical VIP."
            return "BASE", "BASE environment route."
        if role == "UAT":
            exceptions = list(self.policy.effective_capability_exceptions(
                "code_write", namespace=self.namespace, host=self.host,
                path_prefix=self.path_prefix, role=role))
            for exception in exceptions:
                if not exception.requires_active_node:
                    return "UAT_EXCEPTION", f"Configured UAT write exception '{exception.label}'."
            for exception in exceptions:
                if active_node(self.namespace, self._read_metadata_query):
                    return "UAT_EXCEPTION", (f"Configured UAT write exception '{exception.label}' on the "
                                             "ACTIVE mirror node (production jobs observed).")
                return None, (f"Direct HA node mutation blocked: node is not executing the production "
                              f"for '{exception.label}' (backup or stopped); use the active node or the configured VIP.")
        if self._is_direct_ha_node():
            return None, "Direct HA node mutation blocked; use the configured canonical VIP."
        return None, "Environment has no verified direct-write route."

    def is_code_write_route_allowed(self, promotion=False):
        """Static CODE WRITE preflight: production, configured role and route. No network
        except fresh active-node evidence for configured UAT exceptions; never a runner.
        CCR promotion is admitted in every configured environment, production included."""
        if not promotion:
            reason = self._production_gate_reason()
            if reason:
                return False, reason
            if self._configured_role() not in self.policy.non_production_roles:
                return False, "Code write requires an explicit configured non-production role."
        route, detail = self._code_write_route(promotion=promotion)
        if route is None:
            return False, detail
        return True, detail

    def _require_code_write_route(self, promotion=False):
        allowed, reason = self.is_code_write_route_allowed(promotion=promotion)
        if not allowed:
            raise LocalPolicyError(reason)

    def _probe_same_process_ccr_state(self):
        """Fresh same-process CCR guard through the ephemeral runner.

        Returns a dict with the admissible state and its evidence. Any transport,
        protocol or consistency failure raises; callers treat that as blocked.
        """
        failure = "Same-process CCR guard did not return an admissible state; operation blocked."
        code = "\n".join(self._GUARD_STATE_LINES + (self._GUARD_REPORT_LINE,))
        output = self.execute_runtime_code(code)
        lines = [line.strip() for line in str(output).splitlines() if line.strip()]
        if len(lines) != 1:
            raise LocalPolicyError(failure)
        try:
            fields = _parse_pipe_record(lines[0], "MCPGUARD")
            expected = {"state", "engine", "binding", "source_binding", "suspended", "suspend_level"}
            if set(fields) != expected:
                raise ValueError("guard fields")
            binding = _decode_record_value(fields["binding"])
            source_binding = _decode_record_value(fields["source_binding"])
        except (UnicodeDecodeError, ValueError):
            raise LocalPolicyError(failure) from None
        state = fields["state"]
        if state not in self._GUARD_STATES:
            raise LocalPolicyError(failure)
        engine, suspended, level = fields["engine"], fields["suspended"], fields["suspend_level"]
        if state == "CONTROLLED_ACTIVE" and not (
                engine == "1" and binding == "TC.ChangeControl.CCR"
                and source_binding == "%Studio.SourceControl.ISC"
                and suspended == "0" and level == "0"):
            raise LocalPolicyError(failure)
        if state == "SUSPENDED" and not (
                engine == "1" and binding == "TC.ChangeControl.CCR"
                and source_binding == "%Studio.SourceControl.ISC"
                and (suspended != "0" or level != "0")):
            raise LocalPolicyError(failure)
        if state == "ABSENT" and not (engine == "0" and binding == "" and source_binding == ""):
            raise LocalPolicyError(failure)
        return {
            "state": state, "engine": engine == "1", "binding": binding,
            "source_binding": source_binding, "suspended": suspended != "0",
            "suspend_level": level, "suspension_scope": "guard-runner-process",
        }

    def is_code_write_allowed(self, document_name=None, ccr_tracking=None,
                              require_controlled=False, promotion=False):
        """CODE WRITE gate for REST-transport writes (PUT/compile/DELETE/write SQL).

        Order: production gate, configured role, cataloged package (when a document
        is named), environment route, then the same-process CCR guard probe.
        Routing per state:
        - SUSPENDED: blocked.
        - CONTROLLED_ACTIVE: allowed only on the BASE route (or UAT promotion) and
          only when the operation tracks its change under an explicit CCR.
        - ABSENT: allowed only when the operation does not require control, the
          route is BASE or a configured UAT exception, and the document is cataloged.
        - anything else, or guard failure: blocked. UNKNOWN is never an outcome.
        """
        reason = None if promotion else self._production_gate_reason()
        if reason:
            return False, reason
        if not promotion and self._configured_role() not in self.policy.non_production_roles:
            return False, "Code write requires an explicit configured non-production role."
        if document_name is not None:
            self._validate_document_name(document_name)
            if not self._cataloged_package_allowed(document_name):
                return False, ("Package not cataloged for this namespace/role; code write blocked "
                               "in every CCR state.")
        route, detail = self._code_write_route(promotion=promotion)
        if route is None:
            return False, detail
        try:
            guard = self._probe_same_process_ccr_state()
        except LocalPolicyError as exc:
            return False, f"Same-process CCR guard unavailable; code write blocked. {exc}"
        state = guard.get("state") if isinstance(guard, dict) else None
        if state == "SUSPENDED":
            return False, "CCR SUSPENDED in the guard process; code write blocked."
        if state == "CONTROLLED_ACTIVE":
            if route not in ("BASE", "PROMOTION"):
                return False, "CCR CONTROLLED_ACTIVE: only BASE tracked changes or CCR promotion are admitted."
            if not (isinstance(ccr_tracking, str) and ccr_tracking.strip()):
                return False, ("CCR CONTROLLED_ACTIVE: this operation does not track the change under "
                               "an explicit CCR; blocked. Use the CCR-tracked tool variant.")
            return True, f"CCR CONTROLLED_ACTIVE; tracked change under {ccr_tracking.strip()}. {detail}"
        if state == "ABSENT":
            if require_controlled:
                return False, "Operation requires CCR CONTROLLED_ACTIVE; ABSENT namespace blocked."
            if route == "PROMOTION":
                return False, "CCR promotion requires CONTROLLED_ACTIVE; ABSENT namespace blocked."
            if document_name is None:
                return False, ("CCR ABSENT: only cataloged package documents may be written on this "
                               "route; operation without a cataloged document blocked.")
            return True, f"CCR ABSENT; cataloged package on permitted route. {detail} Explicit approval and preservation workflow remain external."
        return False, "Same-process CCR guard returned no admissible state; code write blocked."

    def _require_code_write_same_process(self, document_name=None, ccr_tracking=None,
                                         require_controlled=False, promotion=False):
        allowed, reason = self.is_code_write_allowed(
            document_name, ccr_tracking, require_controlled, promotion,
        )
        if not allowed:
            raise LocalPolicyError(reason)

    def _run_guarded_ccr_runner(self, code_str, ccr_tracking, promotion=False):
        """Run a CCR operation with the same-process guard embedded in its runner.

        The guard executes in the same IRIS process as the operation body and
        requires CONTROLLED_ACTIVE; the body never runs otherwise. Static route
        checks happen first so no runner is created on an inadmissible target.
        """
        if not (isinstance(ccr_tracking, str) and ccr_tracking.strip()):
            raise LocalPolicyError("CCR-tracked operations require an explicit CCR reference.")
        self._require_code_write_route(promotion=promotion)
        code = "\n".join(self._GUARD_STATE_LINES + self._GUARD_REQUIRE_CONTROLLED_LINES) + "\n" + code_str
        if promotion:
            return self.execute_runtime_code(code, promotion=True)
        return self.execute_runtime_code(code)

    def is_runtime_operation_allowed(self):
        """RUNTIME gate: Ensemble runtime operations, diagnostics that need a runner,
        the same-process guard probe and iris_execute_objectscript.

        These operations do not go through Perforce or TC change control tracking,
        so CCR state is never consulted. Only the production gate (is_production,
        PROD/LIVE-looking namespace, production role) and an explicit configured
        non-production role apply. Direct HA nodes are NOT blocked here (see
        _runtime_node_route_allowed): a runner on a mirror backup fails at the
        read-only database, not at this gate.
        """
        reason = self._production_gate_reason()
        if reason:
            return False, reason
        if self._configured_role() not in self.policy.non_production_roles:
            return False, "Runtime operation requires an explicit configured non-production role."
        node_ok, node_reason = self._runtime_node_route_allowed()
        if not node_ok:
            return False, node_reason
        return True, "Runtime operation allowed on configured non-production target."

    def _require_runtime_operation(self):
        allowed, reason = self.is_runtime_operation_allowed()
        if not allowed:
            raise LocalPolicyError(reason)

    # ------------------------------------------------------------------
    # LIVE CONFIG WINDOW gate (routes.live_config_window). Separate from every
    # other gate: it never changes READ, RUNTIME, CODE WRITE or CCR decisions.
    # ------------------------------------------------------------------

    def _evaluate_live_config_window(self, window):
        """Per-candidate LIVE CONFIG WINDOW checks (one ExceptionRule, legacy-
        translated or from isc-ai.rules.json). Same checks and wording as
        before this became a loop over candidates instead of one field."""
        from isc_ai import rules as _rules
        prefix = f"LIVE CONFIG WINDOW '{window.label}' (ticket {window.ticket})"
        expiry_date = _rules._parse_expiry_date(window.expires)
        if expiry_date is None:
            return False, f"{prefix} blocked: expires '{window.expires}' is not a valid YYYY-MM-DD date; window closed."
        if _live_window_today() > expiry_date:
            return False, f"{prefix} blocked: window expired on {window.expires} (local MCP host date)."
        if self._production_gate_reason() is None:
            return False, f"{prefix} blocked: the window applies to production targets only; it grants nothing here."
        if self.namespace.upper() not in (window.namespaces or frozenset()):
            return False, f"{prefix} blocked: namespace '{self.namespace}' is not listed in the window."
        if self.host.lower() not in (window.hosts or frozenset()):
            return False, f"{prefix} blocked: host '{self.host}' is not listed in the window."
        if self.host.lower() not in {host.lower() for host in self.policy.direct_ha_nodes}:
            return False, f"{prefix} blocked: host '{self.host}' is not a configured direct HA node."
        read_allowed, _ = self.is_read_allowed()
        if not read_allowed:
            return False, (f"{prefix} blocked: the server entry needs production_read=true (backups and "
                           "verification read the production over REST).")
        evidence = window.active_node_evidence_namespace
        if not active_node(evidence, lambda sql: self._read_metadata_query(sql, namespace=evidence)):
            return False, (f"{prefix} blocked: no Interoperability jobs observed in '{evidence}' on "
                           f"'{self.host}' (mirror backup, stopped or evidence unavailable); use the active node.")
        return True, (f"{prefix}: allowed on the ACTIVE node '{self.host}' (jobs observed in '{evidence}') "
                      f"for namespace '{self.namespace}', expires {window.expires}.")

    def _resolve_open_live_config_window(self):
        """The first ensemble_control candidate whose checks all pass, and the
        reason; (None, reason) when none does (the last-checked reason, or the
        "not configured" reason when there are no candidates at all)."""
        if not self.policy.configured:
            return None, "LIVE CONFIG WINDOW blocked: no project configuration loaded."
        windows = self.policy.capability_candidates("ensemble_control")
        if not windows:
            return None, "LIVE CONFIG WINDOW blocked: routes.live_config_window is not configured."
        reason = None
        for window in windows:
            allowed, reason = self._evaluate_live_config_window(window)
            if allowed:
                return window, reason
        return None, reason

    def is_live_config_allowed(self):
        """LIVE CONFIG WINDOW gate for Ensemble configuration on production targets.

        Allowed only if ALL hold: a project policy is loaded; an ensemble_control
        exception (routes.live_config_window, or an isc-ai.rules.json exception)
        is configured and its expiry date (local MCP host date, inclusive) has not
        passed; the target is production-flagged; namespace and host are listed in
        the window; the host is a configured direct HA node; the READ gate is open
        (production_read), because backups and verification read over REST; and
        fresh evidence obtained NOW shows Interoperability jobs in the window's
        active_node_evidence_namespace on this node (mirror primary). No environment
        variable is consulted. Never a runner.
        """
        window, reason = self._resolve_open_live_config_window()
        return window is not None, reason

    def _issue_live_window_grant(self):
        """Evaluate the gate NOW and return (per-call grant, reason), or raise the denial."""
        window, reason = self._resolve_open_live_config_window()
        if window is None:
            raise LocalPolicyError(reason)
        grant = _LiveWindowGrant(window.label, window.ticket, self.base_url)
        self.__dict__.setdefault("_live_window_grants", []).append(grant)
        return grant, reason

    def _run_live_window_runner(self, grant, code_str):
        """Run a FIXED window runner on the production target under a fresh grant.

        The mirror-primary guard is prepended and evaluated in the runner process.
        Residue, absence, cleanup and residue re-check follow the direct runner
        lifecycle. Not reachable from iris_execute_objectscript or any tool argument.
        """
        import uuid
        issued = self.__dict__.get("_live_window_grants", ())
        if (not isinstance(grant, _LiveWindowGrant) or not any(item is grant for item in issued)
                or grant.base_url != self.base_url or not grant.fresh()):
            raise LocalPolicyError("LIVE CONFIG WINDOW runner requires a fresh grant from the window gate.")
        if self.__dict__.get("_live_window_runner") is not None:
            raise LocalPolicyError("LIVE CONFIG WINDOW runners do not nest.")
        class_name = f"User.McpTmpRun{uuid.uuid4().hex[:12]}"
        content = self._runner_class_content(class_name, _LIVE_WINDOW_PRIMARY_GUARD + "\n" + code_str)
        self._live_window_runner = (class_name, grant)
        try:
            output = self._run_direct_runner_lifecycle(class_name, content)
        finally:
            self._live_window_runner = None
        if any(line.strip() == _LIVE_WINDOW_NOT_PRIMARY for line in str(output).splitlines()):
            raise _LiveWindowNotPrimary("LIVE CONFIG WINDOW: the runner process is not on the mirror primary; "
                                        "nothing was executed.")
        return output

    def _live_window_pending_change(self, document_name):
        """True when %Studio.SourceControl holds an uncommitted change row for the document."""
        self._validate_document_name(document_name)
        failure = "Source-control change check failed; server details withheld."
        status, data = self._send_request(
            "/action/query", method="POST",
            payload={"query": self._EPHEMERAL_CHANGE_SQL, "parameters": [document_name]},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if status != 200 or not isinstance(data, dict):
            raise LocalPolicyError(failure)
        status_info = data.get("status")
        result = data.get("result")
        if (not isinstance(status_info, dict) or status_info.get("errors") != []
                or status_info.get("summary") not in (None, "")
                or not isinstance(result, dict) or not isinstance(result.get("content"), list)):
            raise LocalPolicyError(failure)
        rows = result["content"]
        if not rows:
            return False
        row = rows[0]
        if (len(rows) != 1 or not isinstance(row, dict) or set(row) != {"InternalName"}
                or not isinstance(row["InternalName"], str)
                or row["InternalName"].casefold() != document_name.casefold()):
            raise LocalPolicyError(failure)
        return True

    def execute_runtime_code(self, code_str, promotion=False):
        """Run an Ensemble runtime operation through the ephemeral runner.

        Gated by is_runtime_operation_allowed only (production gate), never by
        CCR discovery. Cleanup and residue verification are mandatory.
        promotion=True is set only internally by _run_guarded_ccr_runner for CCR
        ItemSet promotion, already admitted by the promotion route and carrying the
        embedded CONTROLLED_ACTIVE guard; it skips the production/role gates.
        """
        import uuid

        if not promotion:
            self._require_runtime_operation()
        runner_class = f"User.McpTmpRun{uuid.uuid4().hex[:12]}"
        self._require_ephemeral_runner(runner_class, promotion=promotion)
        if promotion:
            # Only this exact runner, for this call, passes the promotion runner check.
            self.__dict__["_promotion_runner"] = runner_class
        try:
            return self._execute_runtime_runner(runner_class, code_str)
        finally:
            if promotion:
                self.__dict__.pop("_promotion_runner", None)

    def _execute_runtime_runner(self, runner_class, code_str):
        content = self._runner_class_content(runner_class, code_str)
        if self._ephemeral_runner_has_pending_change(runner_class):
            raise LocalPolicyError("Runner blocked by pre-existing source-control residue.")
        if not self._ephemeral_runner_is_absent(runner_class):
            raise LocalPolicyError("Runner absence could not be proven.")
        output = None
        failure = None
        try:
            self._ephemeral_runner_save(runner_class, content)
            self._ephemeral_runner_compile(runner_class)
            output = self._ephemeral_runner_query(runner_class)
        except Exception as exc:
            failure = exc
        finally:
            removed = self._delete_ephemeral_runner(runner_class)
            try:
                residue = self._ephemeral_runner_has_pending_change(runner_class)
            except Exception:
                residue = True
        if not removed or residue:
            raise LocalPolicyError("Runner cleanup failed; manual verification is required.")
        if failure is not None:
            raise failure
        return output

    def _require_ephemeral_runner(self, class_name, promotion=False):
        """Authorize only the fixed direct-tool runner in configured non-production
        (or, for CCR ItemSet promotion, any configured environment)."""
        self._validate_target()
        if (not isinstance(class_name, str)
                or not self._EPHEMERAL_RUNNER_RE.fullmatch(class_name)):
            raise LocalPolicyError("Invalid ephemeral runner identity.")
        if promotion or (class_name == self.__dict__.get("_promotion_runner")):
            if not self._configured_role():
                raise LocalPolicyError("CCR promotion runner requires an explicit configured environment role.")
            return
        # Separate, explicit path: ONLY the runner that _run_live_window_runner is
        # executing right now, for a grant issued by the LIVE CONFIG WINDOW gate on
        # this exact target. No tool argument can set it; every other runner
        # (iris_execute_objectscript included) falls through to the checks below.
        active = self.__dict__.get("_live_window_runner")
        if active is not None:
            runner_class, grant = active
            if (class_name == runner_class and isinstance(grant, _LiveWindowGrant)
                    and grant.base_url == self.base_url):
                return
        if getattr(self, "is_production", False):
            raise LocalPolicyError("Ephemeral runner blocked on production targets.")
        namespace = self.namespace.upper()
        if any(marker in namespace for marker in self.policy.production_markers):
            raise LocalPolicyError("Ephemeral runner blocked on production-looking namespaces.")
        role = (getattr(self, "env_role", "") or "").strip().upper()
        if role not in self.policy.non_production_roles:
            raise LocalPolicyError("Ephemeral runner requires an explicit configured non-production role.")

    def _require_canonical_base_ccr_mutation(self):
        """Authorize only the permanent fixed SQLProc on the configured canonical BASE."""
        self._validate_target()
        if getattr(self, "is_production", False):
            raise LocalPolicyError("Controlled CCR mutation is blocked on production targets.")
        if self.policy.canonical_base is None:
            raise LocalPolicyError("Controlled CCR mutation requires canonical_base in the project configuration.")
        if not _canonical_base_route_matches(
                self.policy, self.host, self.port, self.path_prefix, self.namespace,
                getattr(self, "env_role", ""), bool(getattr(self, "is_production", False))):
            raise LocalPolicyError("Controlled CCR mutation requires the canonical BASE route.")

    def _require_ccr_id(self, value):
        """Validate a CCR identifier against the configured site format."""
        if not self.policy.ccr_configured:
            raise LocalPolicyError("CCR identifier format is not configured (site.ccr_id).")
        if not self.policy.ccr_matches(value):
            raise LocalPolicyError(f"ccr_id must match {self.policy.ccr_description}.")
        return value

    def _validate_target(self):
        namespace = validate_namespace(self.namespace)
        expected = f"{getattr(self, 'scheme', 'http')}://{self.host}:{self.port}{self.path_prefix}/api/atelier/v1/{namespace}"
        if self.namespace != namespace or self.base_url != expected:
            raise LocalPolicyError("Target changed; create a new canonical client before continuing.")


    def _send_request(self, endpoint, method='GET', payload=None, query_params="", timeout=None,
                      capture_error_body=False, namespace=None):
        """capture_error_body=True returns the parsed JSON body of an HTTP error so a
        private caller can extract bounded IRIS error codes; others get a generic dict.

        namespace (private callers only) addresses a sibling namespace on the SAME
        host/port/prefix/credentials, e.g. active-node evidence for the LIVE config
        window; None keeps the canonical target namespace."""
        self._validate_target()
        base_url = self.base_url
        if namespace is not None:
            sibling = validate_namespace(namespace)
            base_url = (f"{getattr(self, 'scheme', 'http')}://{self.host}:{self.port}"
                        f"{self.path_prefix}/api/atelier/v1/{sibling}")
        url = f"{base_url}{endpoint}"
        if query_params:
            url += f"?{query_params}"
            
        req = urllib.request.Request(url, method=method)
        req.add_header('Authorization', self.auth_header)
        req.add_header('Accept', 'application/json')
        
        if payload is not None:
            req.add_header('Content-Type', 'application/json')
            req.data = json.dumps(payload).encode('utf-8')
            
        try:
            effective_timeout = timeout if timeout is not None else DEFAULT_HTTP_TIMEOUT
            with self.opener.open(req, timeout=effective_timeout) as response:
                raw_data = response.read().decode('utf-8')
                return response.status, json.loads(raw_data) if raw_data else {}
        except urllib.error.HTTPError as e:
            log_debug(f"Atelier request failed: HTTP {e.code}")
            body = None
            if capture_error_body:
                try:
                    raw_error = e.read(65536).decode('utf-8', errors='replace')
                    body = json.loads(raw_error) if raw_error else None
                except Exception:
                    body = None
            e.close()
            if isinstance(body, dict):
                return e.code, body
            return e.code, {"error": "Atelier HTTP request failed"}
        except (socket.timeout, TimeoutError):
            log_debug("Atelier request failed: client-side timeout")
            return 504, {"error": "Atelier client timeout"}
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
                log_debug("Atelier request failed: client-side timeout")
                return 504, {"error": "Atelier client timeout"}
            log_debug("Atelier request failed: transport error")
            return 500, {"error": "Atelier transport error"}
        except Exception:
            log_debug("Atelier request failed: transport or response error")
            return 500, {"error": "Atelier transport or response error"}

    def _read_metadata_query(self, sql, namespace=None):
        """Separate discovery transport: no run_query policy recursion or runner.

        namespace=None queries the target namespace (unchanged behaviour); a value
        queries that sibling namespace on the same instance route."""
        extra = {} if namespace is None else {"namespace": namespace}
        status, data = self._send_request(
            "/action/query", method="POST", payload={"query": sql, "parameters": []},
            timeout=DEFAULT_HTTP_TIMEOUT, **extra,
        )
        if status != 200 or not isinstance(data, dict):
            raise ValueError("Metadata unavailable")
        status_info = data.get("status")
        if (not isinstance(status_info, dict) or status_info.get("errors") != []
                or status_info.get("summary") not in (None, "")):
            raise ValueError("Metadata status unavailable")
        result = data.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("content"), list):
            raise ValueError("Metadata rows unavailable")
        return result["content"]

    def run_query(self, sql_query, params=None, timeout=None):
        """READ transport. Production gate plus the conservative SQL policy; the
        policy is lexical and does not prove side-effect freedom."""
        self._require_read()
        violation = _read_sql_policy_violation(sql_query)
        if violation:
            raise LocalPolicyError(violation)
        return self._send_sql(sql_query, params, timeout)

    def run_write_sql(self, sql_query, params=None, timeout=None):
        """Data-mutating SQL (CT tables). CODE WRITE gate without a cataloged
        document: blocked under CONTROLLED_ACTIVE (untracked) and under ABSENT
        (no cataloged document), so it only succeeds if the catalog grows."""
        self._require_code_write_same_process(document_name=None, ccr_tracking=None)
        return self._send_sql(sql_query, params, timeout)

    # Bounded diagnostic for SQL failures. IRIS SQLCODE/%msg are operator-facing
    # diagnostics (unknown table/column, syntax, reserved word), not secrets; without
    # them a typo is indistinguishable from a network failure. The query text itself
    # is never echoed and the message is collapsed to one short line.
    _SQL_DIAG_MAX_CHARS = 240
    _SQL_DIAG_SQLCODE_RE = re.compile(r"SQLCODE\s*[:=]?\s*(-?\d+)", re.IGNORECASE)

    @classmethod
    def _sql_error_diagnostic(cls, errors):
        parts = []
        for item in errors if isinstance(errors, list) else []:
            text = ""
            if isinstance(item, dict):
                text = str(item.get("error") or item.get("message") or item.get("code") or "")
            elif item is not None:
                text = str(item)
            text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
            if text:
                parts.append(text)
        joined = " | ".join(parts)
        if not joined:
            return ""
        if len(joined) > cls._SQL_DIAG_MAX_CHARS:
            joined = joined[:cls._SQL_DIAG_MAX_CHARS] + "..."
        return joined

    def _send_sql(self, sql_query, params, timeout):
        if params is not None and not isinstance(params, list):
            params = [params]

        payload = {
            "query": sql_query,
            "parameters": params or []
        }
        status, data = self._send_request("/action/query", method='POST', payload=payload, timeout=timeout)
        if status == 200:
            # Detect SQL syntax or execution errors reported by the IRIS wrapper
            status_info = data.get("status", {}) if isinstance(data, dict) else {}
            errors = status_info.get("errors", []) if isinstance(status_info, dict) else []
            summary = status_info.get("summary", "") if isinstance(status_info, dict) else ""
            if errors or summary:
                diagnostic = self._sql_error_diagnostic(errors) or self._sql_error_diagnostic([summary])
                match = self._SQL_DIAG_SQLCODE_RE.search(diagnostic)
                sqlcode = f" SQLCODE={match.group(1)}." if match else ""
                raise LocalPolicyError(
                    "IRIS SQL execution failed (server accepted the request; the statement itself was rejected)."
                    f"{sqlcode} IRIS: {diagnostic or 'no detail returned'}. Query text withheld."
                )

            result_node = data.get('result', {})
            if isinstance(result_node, dict):
                return result_node.get('content', [])
            return []
        hint = {
            401: "authentication rejected",
            403: "forbidden",
            404: "Atelier endpoint or namespace not found",
            500: "server error or transport failure (timeout, connection reset, non-JSON body)",
            502: "gateway error",
            503: "service unavailable",
            504: "gateway timeout",
        }.get(status, "unexpected HTTP status")
        raise LocalPolicyError(
            f"IRIS SQL request failed before execution: HTTP {status} ({hint}). "
            "Query text and server details withheld."
        )

    def get_document(self, doc_name, strict=False):
        self._validate_document_name(doc_name)
        self._require_read()
        import urllib.parse
        quoted_doc = urllib.parse.quote(doc_name, safe="")
        status, data = self._send_request(f"/doc/{quoted_doc}", method='GET')
        if status == 200:
            if not strict:
                return data.get('result', {}).get('content', [])
            if not isinstance(data, dict):
                raise Exception("Get document returned an invalid response envelope")
            status_node = data.get("status")
            if not isinstance(status_node, dict) or not isinstance(status_node.get("errors"), list):
                raise Exception("Get document returned an invalid status envelope")
            if status_node["errors"]:
                raise Exception("Get document returned application errors")
            result_node = data.get("result")
            if not isinstance(result_node, dict) or "content" not in result_node:
                raise Exception("Get document response is missing result.content")
            content = result_node["content"]
            if not isinstance(content, list) or any(not isinstance(line, str) for line in content):
                raise Exception("Get document result.content must be an array of strings")
            return content
        raise Exception(f"Get document failed with status {status}: {data}")

    def save_document(self, doc_name, content_lines, verify=False, ccr_id=None):
        """CODE WRITE. ccr_id is the explicit CCR the change was checked out under;
        without it the save is admissible only on an ABSENT cataloged route."""
        self._validate_document_name(doc_name)
        self._require_code_write_same_process(doc_name, ccr_id)
        import urllib.parse
        quoted_doc = urllib.parse.quote(doc_name, safe="")
        payload = {
            "content": content_lines,
            "enc": False
        }
        status, data = self._send_request(f"/doc/{quoted_doc}", method='PUT', payload=payload, query_params="ignoreConflict=1")
        if status not in [200, 201]:
            return False, f"Save failed with status {status}; server details withheld"
        if verify:
            ok, vmsg = self.verify_document(doc_name, content_lines)
            if not ok:
                return False, (
                    f"Written over HTTP but the round-trip VERIFICATION FAILED (do NOT compile): {vmsg}. "
                    f"If this is a false positive caused by server normalization, retry with verify=false."
                )
            return True, f"Saved & verified ({vmsg})"
        return True, "Saved successfully"

    @staticmethod
    def _normalize_for_verify(lines):
        # Round-trip tolerance for the IRIS UDL formatter: it re-indents lines, drops
        # or inserts blank lines and trims trailing whitespace when a class is stored.
        # Whitespace never carries meaning in a UDL class (strings with significant
        # whitespace keep it INSIDE the line, which is preserved), so the comparison
        # ignores leading/trailing whitespace and blank lines. Duplication or
        # truncation of code still changes the token stream and is detected.
        normalized = [ln.strip() for ln in lines]
        return "\n".join(ln for ln in normalized if ln != "")

    @classmethod
    def _document_change_snapshot(cls, doc_name, lines):
        if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
            raise ValueError("Document content must be an array of strings")
        if doc_name.endswith(".cls"):
            return cls._normalize_for_verify(lines)
        content = "\n".join(lines)
        return content[:-1] if content.endswith("\n") else content

    def verify_document(self, doc_name, expected_lines):
        """
        Reread the document just written (GET) and compare it against what was sent.
        Closes the duplication/truncation hole: if a block was duplicated or lines were
        swallowed, the server content does not match and the operation aborts BEFORE
        compiling. Returns (ok, detail). Tolerates the server's own format normalization
        (indentation, blank lines).
        """
        import hashlib
        try:
            server_lines = self.get_document(doc_name)
        except Exception as e:
            return False, f"could not reread the document to verify it: {e}"
        expected = self._normalize_for_verify(expected_lines)
        got = self._normalize_for_verify(server_lines)
        if expected == got:
            digest = hashlib.sha256(expected.encode("utf-8")).hexdigest()[:12]
            n = len([ln for ln in server_lines if ln.strip() != ""]) if server_lines else 0
            reformatted = len(server_lines) != len(expected_lines) or any(
                a != b for a, b in zip(server_lines, expected_lines))
            note = "; format normalized by the server" if reformatted else ""
            return True, f"{n} lines of code, sha256:{digest}{note}"
        exp_l = expected.split("\n")
        got_l = got.split("\n")
        diff_line = next(
            (i + 1 for i in range(min(len(exp_l), len(got_l))) if exp_l[i] != got_l[i]),
            min(len(exp_l), len(got_l)) + 1,
        )
        return False, (
            f"Content MISMATCH: sent {len(exp_l)} lines of code, the server has {len(got_l)}. "
            f"First difference (ignoring indentation and blank lines) at code line {diff_line}."
        )

    def compile_document(self, doc_name, ccr_id=None):
        """CODE WRITE. Compilation is a mutation; under CONTROLLED_ACTIVE it is
        admissible only as part of a change tracked under an explicit CCR."""
        self._validate_document_name(doc_name)
        self._require_code_write_same_process(doc_name, ccr_id)
        status, data = self._send_request("/action/compile", method='POST', payload=[doc_name], timeout=get_exec_timeout())
        if status == 200:
            return data
        raise LocalPolicyError("Compilation request failed; server details withheld.")

    def delete_document(self, doc_name):
        """CODE WRITE without CCR tracking: admissible only on an ABSENT cataloged route."""
        self._validate_document_name(doc_name)
        self._require_code_write_same_process(doc_name, None)
        import urllib.parse
        quoted_doc = urllib.parse.quote(doc_name, safe="")
        status, data = self._send_request(f"/doc/{quoted_doc}", method='DELETE')
        if status == 200:
            return True, "Deleted successfully"
        return False, f"Delete failed with status {status}; server details withheld"

    # Bounded runner diagnostics: IRIS error codes plus an operator hint. The payload
    # code, console output and raw server bodies are never echoed.
    _RUNNER_ERROR_CODE_RE = re.compile(r"#\s?(\d{2,5})\b")
    _RUNNER_LOCKED_HINT = ("namespace is locked by source control; configure "
                           "runtime.runner_hosts for this namespace")
    _RUNNER_READONLY_HINT = "database is read-only (likely a mirror backup node); target the primary"

    @classmethod
    def _runner_hint(cls, codes, locked=False):
        if locked or "5845" in codes:
            return cls._RUNNER_LOCKED_HINT
        if "5883" in codes or "302" in codes:
            return cls._RUNNER_READONLY_HINT
        return ""

    @classmethod
    def _bounded_runner_text(cls, text):
        text = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())
        if len(text) > cls._SQL_DIAG_MAX_CHARS:
            text = text[:cls._SQL_DIAG_MAX_CHARS] + "..."
        return text

    @classmethod
    def _runner_upload_diagnostic(cls, status, data):
        """Return None when the PUT succeeded, else a bounded failure message."""
        texts = []
        locked = False
        reported = False
        if isinstance(data, dict):
            result = data.get("result")
            result_status = result.get("status") if isinstance(result, dict) else None
            if isinstance(result_status, str) and result_status.strip():
                texts.append(result_status)
                reported = True
            status_info = data.get("status")
            errors = status_info.get("errors") if isinstance(status_info, dict) else None
            if isinstance(errors, list) and errors:
                reported = True
                for item in errors:
                    text = item.get("error", "") if isinstance(item, dict) else item
                    if isinstance(text, str) and text.strip():
                        texts.append(text)
            console = data.get("console")
            if isinstance(console, list):
                locked = any(isinstance(line, str) and "namespace is locked" in line.lower()
                             for line in console)
        if status in (200, 201) and not reported and not locked:
            return None
        codes = []
        for text in texts:
            for code in cls._RUNNER_ERROR_CODE_RE.findall(text):
                if code not in codes:
                    codes.append(code)
        if not codes and not locked and status not in (200, 201):
            return "Temporary executor upload failed; server details withheld."
        detail = f"HTTP {status}"
        if codes:
            detail += "; IRIS " + ", ".join(f"ERROR #{code}" for code in codes)
        hint = cls._runner_hint(codes, locked)
        if hint:
            detail += f". Hint: {hint}"
        return (f"Temporary executor upload failed ({cls._bounded_runner_text(detail)}); "
                "server details withheld.")

    def _ephemeral_runner_save(self, class_name, content_lines):
        self._require_ephemeral_runner(class_name)
        quoted_doc = urllib.parse.quote(class_name + ".cls", safe="")
        status, data = self._send_request(
            f"/doc/{quoted_doc}", method="PUT",
            payload={"content": content_lines, "enc": False},
            query_params="ignoreConflict=1", capture_error_body=True,
        )
        failure = self._runner_upload_diagnostic(status, data)
        if failure:
            raise LocalPolicyError(failure)

    @classmethod
    def _runner_compile_failure(cls, data):
        generic = "Temporary executor compilation failed; code/details withheld."
        status_info = data.get("status") if isinstance(data, dict) else None
        errors = status_info.get("errors") if isinstance(status_info, dict) else None
        parts = []
        for item in errors if isinstance(errors, list) else []:
            text = item.get("error") if isinstance(item, dict) else None
            if isinstance(text, str) and text.strip():
                parts.append(" ".join(text.split()))
        if not parts:
            return generic
        joined = " | ".join(parts)
        codes = cls._RUNNER_ERROR_CODE_RE.findall(joined)
        hint = cls._runner_hint(codes)
        message = f"Temporary executor compilation failed; IRIS: {cls._bounded_runner_text(joined)}."
        if hint:
            message += f" Hint: {hint}."
        return message + " Console withheld."

    def _ephemeral_runner_compile(self, class_name):
        self._require_ephemeral_runner(class_name)
        status, data = self._send_request(
            "/action/compile", method="POST", payload=[class_name + ".cls"],
            timeout=get_exec_timeout(), capture_error_body=True,
        )
        if status != 200 or not isinstance(data, dict):
            raise LocalPolicyError(self._runner_compile_failure(data))
        status_info = data.get("status")
        if (not isinstance(status_info, dict)
                or "errors" not in status_info
                or "summary" not in status_info
                or status_info.get("errors") != []
                or status_info.get("summary") not in (None, "")):
            raise LocalPolicyError(self._runner_compile_failure(data))

    @staticmethod
    def _ephemeral_runner_sql_suffix(class_name):
        # Python 3.8 compatible: str.removeprefix is 3.9+.
        prefix = "User."
        if class_name.startswith(prefix):
            return class_name[len(prefix):]
        return class_name

    def _ephemeral_runner_query(self, class_name):
        self._require_ephemeral_runner(class_name)
        suffix = self._ephemeral_runner_sql_suffix(class_name)
        query = f"SELECT SQLUser.{suffix}_Execute() AS result"
        status, data = self._send_request(
            "/action/query", method="POST", payload={"query": query, "parameters": []},
            timeout=get_exec_timeout(),
        )
        failure = (
            "Payload execution outcome is unknown; payload may have partially persisted data."
        )
        if status != 200 or not isinstance(data, dict):
            raise LocalPolicyError(failure)
        status_info = data.get("status")
        result = data.get("result")
        if (not isinstance(status_info, dict) or status_info.get("errors") != []
                or status_info.get("summary") != ""
                or not isinstance(result, dict)):
            raise LocalPolicyError(failure)
        rows = result.get("content")
        if (not isinstance(rows, list) or len(rows) != 1
                or not isinstance(rows[0], dict) or set(rows[0]) != {"result"}
                or not isinstance(rows[0]["result"], str)):
            raise LocalPolicyError(failure)
        protocol = rows[0]["result"]
        prefix, separator, value = protocol.partition("|")
        if not separator:
            raise LocalPolicyError(failure)
        if prefix in ("MCPOK", "MCPPAYLOAD"):
            try:
                import base64
                output = base64.b64decode(value, validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                raise LocalPolicyError(failure) from None
            if prefix == "MCPOK":
                return output
            raise LocalPolicyError(
                "Payload execution failed; payload may have partially persisted data."
            )
        if prefix == "MCPSETUP" and value in ("OPEN", "SETUP"):
            raise LocalPolicyError(
                "Temporary executor setup failed; payload was not executed."
            )
        if prefix == "MCPUNKNOWN":
            if value == "OUTPUT":
                raise LocalPolicyError(
                    f"Payload executed and may have persisted data, but its output exceeded the "
                    f"{self._EPHEMERAL_OUTPUT_LIMIT}-character capture limit."
                )
            if value == "DELETE":
                raise LocalPolicyError(
                    "Temporary executor cleanup failed; manual verification is required."
                )
            if value == "PAYLOAD_DELETE":
                raise LocalPolicyError(
                    "Payload execution failed; payload may have partially persisted data. "
                    "Temporary executor cleanup failed; manual verification is required."
                )
            if value == "SETUP_DELETE":
                raise LocalPolicyError(
                    "Temporary executor setup failed; payload was not executed. "
                    "Temporary executor cleanup failed; manual verification is required."
                )
            if value == "OUTCOME_DELETE":
                raise LocalPolicyError(
                    f"{failure} Temporary executor cleanup failed; manual verification is required."
                )
            if value in ("CLOSE", "IO", "LINK", "READ", "WRAPPER"):
                raise LocalPolicyError(failure)
        raise LocalPolicyError(failure)

    # Exact bodies _send_request synthesizes for client-side timeout / unreachable
    # transport (never a server-sent body). A pre-check failing this way means the
    # server was not reached, which is distinct from a server-side refusal.
    _RUNNER_TRANSPORT_FAILURES = frozenset({
        (504, "Atelier client timeout"),
        (500, "Atelier transport error"),
    })
    _RUNNER_UNREACHABLE_MESSAGE = (
        "Temporary executor could not reach the server (transport timeout/unreachable); "
        "payload was not executed."
    )

    @classmethod
    def _raise_if_runner_unreachable(cls, status, data):
        """Pre-check helpers raise the distinct unreachable message. Every post-PUT
        caller catches Exception and treats it as cleanup failure, so cleanup
        strictness is unchanged."""
        if (isinstance(data, dict) and set(data) == {"error"}
                and (status, data["error"]) in cls._RUNNER_TRANSPORT_FAILURES):
            raise LocalPolicyError(cls._RUNNER_UNREACHABLE_MESSAGE)

    def _ephemeral_runner_has_pending_change(self, class_name):
        """Check only the generated document through the private metadata transport."""
        self._require_ephemeral_runner(class_name)
        document_name = class_name + ".cls"
        status, data = self._send_request(
            "/action/query", method="POST",
            payload={"query": self._EPHEMERAL_CHANGE_SQL, "parameters": [document_name]},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        self._raise_if_runner_unreachable(status, data)
        if status != 200 or not isinstance(data, dict):
            raise LocalPolicyError("Temporary executor source-control check failed; server details withheld.")
        status_info = data.get("status")
        result = data.get("result")
        if (not isinstance(status_info, dict) or status_info.get("errors") != []
                or status_info.get("summary") not in (None, "")
                or not isinstance(result, dict) or not isinstance(result.get("content"), list)):
            raise LocalPolicyError("Temporary executor source-control check failed; server details withheld.")
        rows = result["content"]
        if not rows:
            return False
        row = rows[0]
        if (len(rows) != 1 or not isinstance(row, dict) or set(row) != {"InternalName"}
                or not isinstance(row["InternalName"], str)
                or row["InternalName"].casefold() != document_name.casefold()):
            raise LocalPolicyError("Temporary executor source-control check failed; server details withheld.")
        return True

    def _ephemeral_runner_delete(self, class_name):
        self._require_ephemeral_runner(class_name)
        quoted_doc = urllib.parse.quote(class_name + ".cls", safe="")
        status, _ = self._send_request(f"/doc/{quoted_doc}", method="DELETE")
        return status == 200

    def _ephemeral_runner_is_absent(self, class_name):
        self._require_ephemeral_runner(class_name)
        quoted_doc = urllib.parse.quote(class_name + ".cls", safe="")
        status, data = self._send_request(f"/doc/{quoted_doc}", method="GET")
        self._raise_if_runner_unreachable(status, data)
        return status == 404

    def _delete_ephemeral_runner(self, class_name):
        for _ in range(3):
            try:
                self._ephemeral_runner_delete(class_name)
                if self._ephemeral_runner_is_absent(class_name):
                    return True
            except Exception:
                pass
        return False

    def _runner_host_client(self):
        """Client whose namespace hosts the ephemeral runner for this target.

        Only an explicit runner exception (legacy runtime.runner_hosts, or the
        rules-file "runner" capability) for this namespace AND the configured
        role selects another namespace; otherwise the target itself hosts the
        runner. The sibling shares host, port, prefix, scheme, credentials,
        production flags and role; only namespace/base_url differ and stay canonical.
        """
        import copy
        candidates = self.policy.effective_capability_exceptions(
            "runner", namespace=self.namespace, host=self.host, role=self._configured_role())
        entry = next((exc for exc in candidates if exc.runner_namespace), None)
        if entry is None:
            return self
        runner_namespace = validate_namespace(entry.runner_namespace)
        if runner_namespace == self.namespace:
            raise LocalPolicyError("Runner host namespace must differ from the target namespace.")
        sibling = copy.copy(self)
        sibling.namespace = runner_namespace
        sibling.base_url = (f"{getattr(self, 'scheme', 'http')}://{self.host}:{self.port}"
                            f"{self.path_prefix}/api/atelier/v1/{runner_namespace}")
        sibling._validate_target()
        return sibling

    def _namespace_switch_prefix(self):
        """Payload prologue that moves the runner process into the target namespace."""
        target = validate_namespace(self.namespace)
        return f'New $NAMESPACE\nSet $NAMESPACE = "{self._escape_cos(target)}"\n'

    def _runtime_audit_base(self, class_name, runner, code_str):
        return {
            "run_id": self._ephemeral_runner_sql_suffix(class_name),
            "host": self.host,
            "path_prefix": self.path_prefix,
            "target_namespace": self.namespace,
            "runner_namespace": runner.namespace,
            "role": self._configured_role(),
            "code_sha256": hashlib.sha256(code_str.encode("utf-8")).hexdigest(),
            "code_length": len(code_str),
        }

    def _execute_direct_objectscript(self, code_str):
        """Run the temporary executor under the RUNTIME gate (production gate only).

        Arbitrary ObjectScript may persist data but is not a source-code change
        tracked by Perforce/TC, so CCR state does not authorize or deny it. When
        runtime.runner_hosts names a host namespace for this target and role, the
        runner lives there and the payload switches to the target namespace; both
        namespaces must pass the runtime and runner gates. Every call is recorded in
        the local runtime audit file before any server request (fail closed).
        """
        import time
        import uuid
        class_name = f"User.McpTmpRun{uuid.uuid4().hex[:12]}"
        self._require_ephemeral_runner(class_name)
        self._require_runtime_operation()
        runner = self._runner_host_client()
        payload_code = code_str
        if runner is not self:
            runner._require_ephemeral_runner(class_name)
            runner._require_runtime_operation()
            payload_code = self._namespace_switch_prefix() + code_str
        class_content = self._runner_class_content(class_name, payload_code)
        audit = self._runtime_audit_base(class_name, runner, code_str)
        try:
            _append_runtime_audit(dict(audit, ts=_audit_timestamp(), event="start", code=code_str))
        except Exception:
            raise LocalPolicyError(
                "Runtime audit record could not be written; execution blocked (set "
                f"{RUNTIME_AUDIT_DIR_ENV} to a writable absolute directory)."
            ) from None
        started = time.monotonic()
        outcome = "Temporary executor operation failed unexpectedly; details withheld."
        try:
            output = runner._run_direct_runner_lifecycle(class_name, class_content)
            outcome = "ok"
            return output
        except LocalPolicyError as exc:
            outcome = str(exc)
            raise
        finally:
            try:
                _append_runtime_audit(dict(
                    audit, ts=_audit_timestamp(), event="end", outcome=outcome,
                    duration_ms=int((time.monotonic() - started) * 1000),
                ))
            except Exception:
                log_debug("Runtime audit end record could not be written.")

    def _run_direct_runner_lifecycle(self, class_name, class_content):
        """Residue check, absence check, PUT, compile, query, delete and residue re-check
        of the generated runner in THIS client's namespace."""
        self._require_ephemeral_runner(class_name)
        if self._ephemeral_runner_has_pending_change(class_name):
            raise LocalPolicyError("Temporary executor blocked by pre-existing source-control residue.")
        if not self._ephemeral_runner_is_absent(class_name):
            raise LocalPolicyError(
                "Temporary executor blocked because generated document absence could not be proven."
            )
        save_attempted = False
        operation_failed = False
        operation_message = "Temporary executor operation failed; details withheld."
        output = None
        cleanup_failed = False
        try:
            save_attempted = True
            operation_message = "Temporary executor upload failed; server details withheld."
            self._ephemeral_runner_save(class_name, class_content)
            operation_message = "Temporary executor compilation failed; code/details withheld."
            self._ephemeral_runner_compile(class_name)
            operation_message = (
                "Payload execution outcome is unknown; payload may have partially persisted data."
            )
            output = self._ephemeral_runner_query(class_name)
        except LocalPolicyError as exc:
            operation_failed = True
            operation_message = str(exc)
        except Exception:
            operation_failed = True
        finally:
            if save_attempted:
                removed = self._delete_ephemeral_runner(class_name)
                try:
                    residue = self._ephemeral_runner_has_pending_change(class_name)
                except Exception:
                    residue = True
                if not removed or residue:
                    cleanup_failed = True
        if cleanup_failed:
            cleanup_message = (
                "Temporary executor cleanup failed; manual verification is required."
            )
            if operation_failed:
                raise LocalPolicyError(f"{operation_message} {cleanup_message}")
            raise LocalPolicyError(cleanup_message)
        if operation_failed:
            raise LocalPolicyError(operation_message)
        return output

    def ccr_mutate_class(self, class_name, ccr_id, expected_before_sha256,
                         candidate_sha256, source_base64):
        self._require_canonical_base_ccr_mutation()
        sql = "SELECT MCP.Mutate(?,?,?,?,?) AS result"
        status, data = self._send_request(
            "/action/query", method="POST",
            payload={
                "query": sql,
                "parameters": [class_name, ccr_id, expected_before_sha256,
                               candidate_sha256, source_base64],
            },
            timeout=get_exec_timeout(),
        )
        failure = "Controlled CCR mutation outcome is unverified; do not retry automatically."
        if status != 200 or not isinstance(data, dict):
            raise LocalPolicyError(failure)
        status_info = data.get("status")
        result = data.get("result")
        if (not isinstance(status_info, dict) or status_info.get("errors") != []
                or status_info.get("summary") not in (None, "")
                or not isinstance(result, dict)):
            raise LocalPolicyError(failure)
        rows = result.get("content")
        if (not isinstance(rows, list) or len(rows) != 1
                or not isinstance(rows[0], dict) or set(rows[0]) != {"result"}):
            raise LocalPolicyError(failure)
        payload = rows[0]["result"]
        # Atelier /action/query may return the SqlProc's JSON string already
        # deserialized as an object; accept both forms with the same bound.
        if isinstance(payload, dict):
            record = payload
        elif isinstance(payload, str) and len(payload) <= 16384:
            try:
                record = json.loads(payload)
            except (TypeError, ValueError):
                raise LocalPolicyError(failure) from None
        else:
            raise LocalPolicyError(failure)
        if (isinstance(record, dict) and record.get("protocol") == self.policy.mutation_protocol
                and type(record.get("ok")) is int and record["ok"] == 0
                and isinstance(record.get("state"), str) and record["state"] != "VERIFIED"
                and isinstance(record.get("code"), str)):
            # Facility rejected or retained fail-closed; surface its own verdict.
            raise LocalPolicyError(
                f"Controlled CCR mutation {record['state']} ({record['code']}): "
                f"{record.get('message', '')}; do not retry automatically."
                + (" Manual inspection is required." if record.get("manual_action_required") else "")
            )
        expected_keys = {
            "protocol", "ok", "state", "code", "message", "namespace", "class",
            "ccr", "candidate_sha256", "binding", "source_binding", "suspend_level",
            "controlled_active", "suspension_scope", "round_trip", "tracking", "compile",
            "change_id", "checkout_created", "created_tracking", "manual_action_required",
        }
        exact_integer_evidence = {
            "ok": 1,
            "suspend_level": 0,
            "controlled_active": 1,
            "round_trip": 1,
            "tracking": 1,
            "compile": 1,
            "created_tracking": 1,
            "manual_action_required": 0,
        }
        if (not isinstance(record, dict) or set(record) != expected_keys
                or record["protocol"] != self.policy.mutation_protocol
                or any(type(record[key]) is not int or record[key] != value
                       for key, value in exact_integer_evidence.items())
                or record["state"] != "VERIFIED"
                or record["code"] != "" or record["message"] != ""
                or record["namespace"] != self.policy.canonical_base.namespace
                or record["class"] != class_name or record["ccr"] != ccr_id
                or record["candidate_sha256"] != candidate_sha256
                or record["binding"] != "TC.ChangeControl.CCR"
                or record["source_binding"] != "%Studio.SourceControl.ISC"
                or record["suspension_scope"] != "same-request-process"
                or type(record["change_id"]) is not int or record["change_id"] <= 0
                or type(record["checkout_created"]) is not int
                or record["checkout_created"] not in (0, 1)
                or record["checkout_created"] != (expected_before_sha256 != "ABSENT")):
            raise LocalPolicyError(failure)
        return record

    @classmethod
    def _ccr_initializer_runner_content(cls, runner_class, source_bytes, namespace,
                                        ccr_id, candidate_sha256, upgrade=False,
                                        expected_current_sha256="", policy=None):
        """Build the fixed conditional initializer for the packaged facility."""
        if policy is None or not policy.ccr_configured:
            raise LocalPolicyError("CCR identifier format is not configured (site.ccr_id).")
        encoded = base64.b64encode(source_bytes).decode("ascii")
        chunks = [encoded[pos:pos + 24000] for pos in range(0, len(encoded), 24000)]
        lines = [
            f"Class {runner_class} [ Final ]",
            "{",
            "ClassMethod Execute() As %String(MAXLEN=16384) [ SqlProc ]",
            "{",
            "    New %SourceControl, %ChangeControl, %ccrSession",
            '    Set locked = 0, json = ""',
            "    Try {",
            "        Lock +^MCP.CCRMutationInitializer:30",
            '        If \'$Test { Set json = ..Result(0,"REJECTED","LOCK_TIMEOUT","Initializer lock timed out","","",0,"",0,0,0,0,0,0,0,0,0,0,0) } Else {',
            "            Set locked = 1",
            "            Try { Set json = ..Initialize() } Catch ex {",
            '                Set json = ..Result(0,"UNKNOWN_PARTIAL","EXCEPTION","Initializer outcome is unknown; inspect target and pending queue manually","","",0,"",0,0,0,0,0,0,1,1,1,1,1)',
            "            }",
            "        }",
            "    } Catch wrapper {",
            '        Set json = ..Result(0,"UNKNOWN_PARTIAL","WRAPPER_EXCEPTION","Initializer wrapper failed; inspect target and pending queue manually","","",0,"",0,0,0,0,0,0,1,1,1,1,1)',
            "    }",
            "    If locked Lock -^MCP.CCRMutationInitializer",
            '    Quit "MCPOK|"_$System.Encryption.Base64Encode($ZConvert(json,"O","UTF8"),1)',
            "}",
            "",
            "ClassMethod Initialize() As %String [ Private ]",
            "{",
            f'    Set targetClass = "{_CCR_MUTATION_CLASS}", document = targetClass_".cls", requestedCCR = "{ccr_id}"',
            f'    Set candidateSHA = "{candidate_sha256}", upgrade = {1 if upgrade else 0}, expectedCurrentSHA = "{expected_current_sha256}"',
            '    Set binding = "", sourceBinding = "", suspendLevel = "", sourceBytes = "", mutationStarted = 0',
        ]
        for chunk in chunks:
            lines.append(f'    Set sourceBytes = sourceBytes_$System.Encryption.Base64Decode("{chunk}")')
        lines.extend([
            f'    If $Namespace \'= "{cls._escape_cos(namespace)}" Quit ..BeforeFail("INVALID_NAMESPACE","Initializer namespace changed",binding,sourceBinding,suspendLevel,"",mutationStarted)',
            f'    If requestedCCR\'?{policy.ccr_cos_pattern} Quit ..BeforeFail("INVALID_CCR","CCR must match {cls._escape_cos(policy.ccr_description)}",binding,sourceBinding,suspendLevel)',
            '    If (upgrade=1) && (\'..ValidSHA(expectedCurrentSHA)) Quit ..BeforeFail("INVALID_BASELINE_HASH","Upgrade baseline hash is invalid",binding,sourceBinding,suspendLevel,"",mutationStarted)',
            '    If ..SHA256(sourceBytes) \'= candidateSHA Quit ..BeforeFail("SOURCE_HASH","Packaged source hash mismatch",binding,sourceBinding,suspendLevel,"",mutationStarted)',
            '    If $Extract(sourceBytes,1,3) = $Char(239,187,191) Quit ..BeforeFail("SOURCE_BOM","UTF-8 BOM is not allowed",binding,sourceBinding,suspendLevel)',
            '    If $Find(sourceBytes,$Char(0)) Quit ..BeforeFail("SOURCE_NUL","NUL is not allowed",binding,sourceBinding,suspendLevel)',
            '    Set source = $ZConvert(sourceBytes,"I","UTF8")',
            '    If $ZConvert(source,"O","UTF8") \'= sourceBytes Quit ..BeforeFail("SOURCE_UTF8","Source is not strict UTF-8",binding,sourceBinding,suspendLevel)',
            '    Set source = ..CanonicalCRLF(source)',
            '    If ..SHA256($ZConvert(source,"O","UTF8")) \'= candidateSHA Quit ..BeforeFail("CANONICAL_HASH","Canonical packaged source hash mismatch",binding,sourceBinding,suspendLevel,"",mutationStarted)',
            "",
            "    Set binding = ##class(TC.ChangeControl.Interface).ChangeControlClassGet()",
            "    Set sourceBinding = ##class(%Studio.SourceControl.Interface).SourceControlClassGet()",
            "    Set suspendLevel = ##class(TC.ChangeControl.Interface).GetSuspendLevel()",
            '    If binding \'= "TC.ChangeControl.CCR" Quit ..BeforeFail("BINDING_MISMATCH","Exact TC.ChangeControl.CCR binding is required",binding,sourceBinding,suspendLevel)',
            '    If sourceBinding \'= "%Studio.SourceControl.ISC" Quit ..BeforeFail("SOURCE_BINDING_MISMATCH","Exact %Studio.SourceControl.ISC binding is required",binding,sourceBinding,suspendLevel)',
            '    If ##class(TC.ChangeControl.Interface).IsSuspended() || (+suspendLevel \'= 0) Quit ..BeforeFail("CONTROL_SUSPENDED","Change control must be active at level zero",binding,sourceBinding,suspendLevel)',
            "",
            '    Set sc = ##class(TC.ChangeControl.Interface).ChangeControlCreate($Username,"",.created)',
            '    If $$$ISERR(sc) || (\'created) || (\'$IsObject($Get(%ChangeControl))) || ($ClassName(%ChangeControl) \'= binding) Quit ..BeforeFail("CHANGE_CONTROL_CREATE","Unable to create exact change-control object",binding,sourceBinding,suspendLevel)',
            "    Set normalizedCCR = %ChangeControl.NormaliseKey(requestedCCR)",
            '    If (normalizedCCR \'= requestedCCR) || (\'%ChangeControl.IsKeyValid(normalizedCCR)) Quit ..BeforeFail("CCR_REJECTED","CCR normalization or validation rejected the requested value",binding,sourceBinding,suspendLevel)',
            "    Set %ccrSession = requestedCCR",
            '    Set sc = ##class(%Studio.SourceControl.Interface).SourceControlCreate("","",.sourceCreated)',
            '    If $$$ISERR(sc) || (\'sourceCreated) || (\'$IsObject($Get(%SourceControl))) || ($ClassName(%SourceControl) \'= sourceBinding) Quit ..BeforeFail("SOURCE_CONTROL_CREATE","Unable to create exact source-control object",binding,sourceBinding,suspendLevel)',
            "",
            "    Set expectedFile = %SourceControl.ExternalName(document)",
            "    Set expectedName = document, expectedUser = $Username, expectedSource = \"studio\"",
            '    If (expectedFile = "") || (expectedName = "") || (expectedUser = "") Quit ..BeforeFail("SOURCE_IDENTITY","Source-control identity is incomplete",binding,sourceBinding,suspendLevel)',
            "    Set sc = ..ReadPending(document,expectedFile,.pendingCount,.pendingID,.pendingName,.pendingAction,.pendingCCR,.pendingSource,.pendingFile,.pendingUser,.pendingInternal)",
            '    If $$$ISERR(sc) Quit ..BeforeFail("PENDING_QUERY","Unable to inspect pending source-control rows",binding,sourceBinding,suspendLevel)',
            '    If pendingCount Quit ..BeforeFail("PREEXISTING_PENDING","Target document or file already has pending work",binding,sourceBinding,suspendLevel)',
            "",
            "    Set present = ##class(%Dictionary.ClassDefinition).%ExistsId(targetClass), currentSHA = \"\"",
            "    If present {",
            '        Set sc = ##class(%Compiler.UDL.TextServices).GetTextAsString("",document,.currentSource)',
            '        If $$$ISERR(sc) Quit ..BeforeFail("CURRENT_READ","Unable to read current class source",binding,sourceBinding,suspendLevel,"",mutationStarted)',
            '        Set currentSHA = ..SHA256($ZConvert(..CanonicalCRLF(currentSource),"O","UTF8"))',
            "        If currentSHA = candidateSHA {",
            '            If \'..CompiledArtifactsCurrent(targetClass) Quit ..NoMutation("SOURCE_MATCH_NOT_COMPILED",currentSHA,binding,sourceBinding,suspendLevel)',
            '            Quit ..NoMutation("UNCHANGED",currentSHA,binding,sourceBinding,suspendLevel)',
            "        }",
            '        If \'upgrade Quit ..NoMutation("DIFFERENT",currentSHA,binding,sourceBinding,suspendLevel)',
            '        If currentSHA \'= expectedCurrentSHA Quit ..BeforeFail("BASELINE_MISMATCH","Current source does not match the required upgrade baseline",binding,sourceBinding,suspendLevel,currentSHA,mutationStarted)',
            '    } ElseIf upgrade {',
            '        Quit ..BeforeFail("BASELINE_ABSENT","Target is absent but upgrade requires an existing baseline",binding,sourceBinding,suspendLevel,"",mutationStarted)',
            "    }",
            "",
            "    // Revalidate namespace, exact bindings and process-local suspension immediately",
            "    // before the first mutation-capable step (checkout when present, else OnBeforeSave).",
            '    If \'..ControlIntact(.suspendLevel) Quit ..BeforeFail("CONTROL_CHANGED","Namespace, bindings, or suspension changed before the first mutation-capable step",binding,sourceBinding,suspendLevel,currentSHA,mutationStarted)',
            "",
            "    Set mutationStarted = 1",
            "    If present {",
            '        Set sc = %SourceControl.CheckOut(document,"MCP CCRMutation initializer",0)',
            '        If $$$ISERR(sc) Quit ..Retained("CHECKOUT_FAILED_RETAINED","Checkout failed; inspect manually",binding,sourceBinding,suspendLevel,1,0,1,"",currentSHA,mutationStarted)',
            "        Set sc = ..ReadPending(document,expectedFile,.pendingCount,.pendingID,.pendingName,.pendingAction,.pendingCCR,.pendingSource,.pendingFile,.pendingUser,.pendingInternal)",
            '        If $$$ISERR(sc) || (pendingCount\'=1) || (\'..PendingOwned(pendingID,pendingName,pendingAction,pendingCCR,pendingSource,pendingFile,pendingUser,pendingInternal,expectedName,"edit",requestedCCR,expectedSource,expectedFile,expectedUser,document)) Quit ..Retained("UNKNOWN_PARTIAL","Checkout provenance is unknown",binding,sourceBinding,suspendLevel,1,0,1,pendingID,currentSHA,mutationStarted)',
            "        Set rowID = pendingID",
            '        If pendingCCR = "" { Set sc = ##class(%Studio.SourceControl.Change).UpdateUncommitted(expectedFile, , , , , , , requestedCCR) If $$$ISERR(sc) Quit ..Retained("CHECKOUT_CCR_RETAINED","Checkout CCR assignment failed",binding,sourceBinding,suspendLevel,1,0,1,rowID,currentSHA,mutationStarted) }',
            "        Set sc = ..ReadPending(document,expectedFile,.pendingCount,.pendingID,.pendingName,.pendingAction,.pendingCCR,.pendingSource,.pendingFile,.pendingUser,.pendingInternal)",
            '        If $$$ISERR(sc) || (pendingCount\'=1) || (pendingID\'=rowID) || (\'..PendingMatches(pendingID,pendingName,pendingAction,pendingCCR,pendingSource,pendingFile,pendingUser,pendingInternal,expectedName,"edit",requestedCCR,expectedSource,expectedFile,expectedUser,document)) Quit ..Retained("UNKNOWN_PARTIAL","Assigned checkout provenance is unknown",binding,sourceBinding,suspendLevel,1,0,1,rowID,currentSHA,mutationStarted)',
            "    }",
            "    Set sc = %SourceControl.OnBeforeSave(document)",
            '    If $$$ISERR(sc) Quit ..Retained("BEFORE_SAVE_FAILED_RETAINED","Before-save hook failed; inspect manually",binding,sourceBinding,suspendLevel,+present,0,+present,$Get(rowID),currentSHA,mutationStarted)',
            "    // Revalidate again immediately before import.",
            '    If \'..ControlIntact(.suspendLevel) Quit ..Retained("CONTROL_CHANGED_RETAINED","Namespace, bindings, or suspension changed immediately before save",binding,sourceBinding,suspendLevel,+present,0,+present,$Get(rowID),currentSHA,mutationStarted)',
            "    // Close the TOCTOU window: presence, current source hash and pending rows must",
            "    // still equal the baseline observed earlier in this same process.",
            '    If ##class(%Dictionary.ClassDefinition).%ExistsId(targetClass) \'= present Quit ..Retained("BASELINE_DRIFT_RETAINED","Target presence changed before import",binding,sourceBinding,suspendLevel,+present,0,+present,$Get(rowID),currentSHA,mutationStarted)',
            "    If present {",
            '        Set sc = ##class(%Compiler.UDL.TextServices).GetTextAsString("",document,.recheckSource)',
            '        If $$$ISERR(sc) Quit ..Retained("BASELINE_DRIFT_RETAINED","Current source cannot be reread before import",binding,sourceBinding,suspendLevel,1,0,1,rowID,currentSHA,mutationStarted)',
            '        If ..SHA256($ZConvert(..CanonicalCRLF(recheckSource),"O","UTF8")) \'= expectedCurrentSHA Quit ..Retained("BASELINE_DRIFT_RETAINED","Current source changed before import",binding,sourceBinding,suspendLevel,1,0,1,rowID,currentSHA,mutationStarted)',
            "    }",
            "    Set sc = ..ReadPending(document,expectedFile,.pendingCount,.pendingID,.pendingName,.pendingAction,.pendingCCR,.pendingSource,.pendingFile,.pendingUser,.pendingInternal)",
            '    If $$$ISERR(sc) Quit ..Retained("BASELINE_DRIFT_RETAINED","Pending rows cannot be reread before import",binding,sourceBinding,suspendLevel,+present,0,+present,$Get(rowID),currentSHA,mutationStarted)',
            "    If present {",
            '        If (pendingCount\'=1) || (pendingID\'=rowID) || (\'..PendingMatches(pendingID,pendingName,pendingAction,pendingCCR,pendingSource,pendingFile,pendingUser,pendingInternal,expectedName,"edit",requestedCCR,expectedSource,expectedFile,expectedUser,document)) Quit ..Retained("BASELINE_DRIFT_RETAINED","Unexpected pending rows before import",binding,sourceBinding,suspendLevel,1,0,1,rowID,currentSHA,mutationStarted)',
            '    } ElseIf pendingCount \'= 0 {',
            '        Quit ..Retained("BASELINE_DRIFT_RETAINED","Unexpected pending rows before import",binding,sourceBinding,suspendLevel,0,0,1,pendingID,currentSHA,mutationStarted)',
            "    }",
            "    Set sc = ##class(%Compiler.UDL.TextServices).SetTextFromString($Namespace,targetClass,source)",
            '    If $$$ISERR(sc) Quit ..Retained("IMPORT_FAILED_RETAINED","Source import failed or is partial; inspect manually",binding,sourceBinding,suspendLevel,+present,1,+present,$Get(rowID),currentSHA,mutationStarted)',
            '    If present { Set sc = %SourceControl.OnAfterSave(document) } Else { Set sc = %SourceControl.AddToSourceControl(document,"MCP CCRMutation initializer") }',
            '    If $$$ISERR(sc) Quit ..Retained("TRACKING_FAILED_RETAINED","Source changed but tracking failed; inspect manually",binding,sourceBinding,suspendLevel,+present,1,1,$Get(rowID),currentSHA,mutationStarted)',
            "",
            "    Set sc = ..ReadPending(document,expectedFile,.pendingCount,.pendingID,.pendingName,.pendingAction,.pendingCCR,.pendingSource,.pendingFile,.pendingUser,.pendingInternal)",
            '    Set expectedAction = $Select(present:"edit",1:"add")',
            '    If $$$ISERR(sc) || (pendingCount \'= 1) || (\'..PendingOwned(pendingID,pendingName,pendingAction,pendingCCR,pendingSource,pendingFile,pendingUser,pendingInternal,expectedName,expectedAction,requestedCCR,expectedSource,expectedFile,expectedUser,document)) Quit ..Retained("UNKNOWN_PARTIAL","Pending-row provenance is unknown",binding,sourceBinding,suspendLevel,+present,1,1,pendingID,currentSHA,mutationStarted)',
            '    If present && (pendingID\'=rowID) Quit ..Retained("UNKNOWN_PARTIAL","Pending row identity changed after save",binding,sourceBinding,suspendLevel,1,1,1,pendingID,currentSHA,mutationStarted)',
            '    If pendingCCR = "" {',
            "        Set sc = ##class(%Studio.SourceControl.Change).UpdateUncommitted(expectedFile, , , , , , , requestedCCR)",
            '        If $$$ISERR(sc) Quit ..Retained("CCR_ASSIGNMENT_RETAINED","Tracking exists but CCR assignment failed",binding,sourceBinding,suspendLevel,+present,1,1,pendingID,currentSHA,mutationStarted)',
            "    }",
            "    Set rowID = pendingID",
            "    Set sc = ..ReadPending(document,expectedFile,.pendingCount,.pendingID,.pendingName,.pendingAction,.pendingCCR,.pendingSource,.pendingFile,.pendingUser,.pendingInternal)",
            '    If $$$ISERR(sc) || (pendingCount \'= 1) || (pendingID \'= rowID) || (\'..PendingMatches(pendingID,pendingName,pendingAction,pendingCCR,pendingSource,pendingFile,pendingUser,pendingInternal,expectedName,expectedAction,requestedCCR,expectedSource,expectedFile,expectedUser,document)) Quit ..Retained("UNKNOWN_PARTIAL","Assigned pending-row provenance is unknown",binding,sourceBinding,suspendLevel,+present,1,1,pendingID,currentSHA,mutationStarted)',
            '    If (rowID \'?1.N) || (+rowID < 1) Quit ..Retained("CHANGE_ID_RETAINED","Pending change ID is not a positive integer",binding,sourceBinding,suspendLevel,+present,1,1,rowID,currentSHA,mutationStarted)',
            "",
            '    Set sc = ##class(%Compiler.UDL.TextServices).GetTextAsString("",document,.roundTripSource)',
            '    If $$$ISERR(sc) Quit ..Retained("ROUND_TRIP_READ_RETAINED","Tracked source cannot be reread",binding,sourceBinding,suspendLevel,+present,1,1,rowID,currentSHA,mutationStarted)',
            '    If ..SHA256($ZConvert(..CanonicalCRLF(roundTripSource),"O","UTF8")) \'= candidateSHA Quit ..Retained("ROUND_TRIP_MISMATCH_RETAINED","Round-trip source hash differs",binding,sourceBinding,suspendLevel,+present,1,1,rowID,currentSHA,mutationStarted)',
            '    Set compileSC = $System.OBJ.Compile(targetClass,"ck-d")',
            '    If $$$ISERR(compileSC) Quit ..Retained("COMPILE_FAILED_RETAINED","Tracked source failed compilation; inspect manually",binding,sourceBinding,suspendLevel,+present,1,1,rowID,currentSHA,mutationStarted)',
            '    Quit ..Success($Select(present:"UPGRADED",1:"INSTALLED"),candidateSHA,binding,sourceBinding,suspendLevel,+rowID,+present)',
            "}",
            "",
            "ClassMethod ReadPending(pDocument,pExpectedFile,Output pCount,Output pID,Output pName,Output pAction,Output pCCR,Output pSource,Output pFile,Output pUser,Output pInternal) As %Status [ Private ]",
            "{",
            '    Set (pCount,pID,pName,pAction,pCCR,pSource,pFile,pUser,pInternal) = ""',
            '    &SQL(SELECT COUNT(*),MIN(ID),MIN(%EXACT(Name)),MIN(%EXACT(Action)),MIN(%EXACT(CCR)),MIN(%EXACT(Source)),MIN(%EXACT(ItemFile)),MIN(%EXACT("User")),MIN(%EXACT(InternalName)) INTO :pCount,:pID,:pName,:pAction,:pCCR,:pSource,:pFile,:pUser,:pInternal FROM %Studio_SourceControl.Change WHERE (InternalName = :pDocument OR ItemFile = :pExpectedFile) AND Committed = 0)',
            '    If SQLCODE Quit $$$ERROR($$$GeneralError,"Pending source-control query failed")',
            "    Quit $$$OK",
            "}",
            "",
            "ClassMethod PendingMatches(pID,pName,pAction,pCCR,pSource,pFile,pUser,pInternal,pExpectedName,pExpectedAction,pExpectedCCR,pExpectedSource,pExpectedFile,pExpectedUser,pExpectedInternal) As %Boolean [ Private ]",
            "{",
            '    Quit (pID \'= "") && (pName = pExpectedName) && (pAction = pExpectedAction) && (pCCR = pExpectedCCR) && (pSource = pExpectedSource) && (pFile = pExpectedFile) && (pUser = pExpectedUser) && (pInternal = pExpectedInternal)',
            "}",
            "",
            "ClassMethod PendingOwned(pID,pName,pAction,pCCR,pSource,pFile,pUser,pInternal,pExpectedName,pExpectedAction,pExpectedCCR,pExpectedSource,pExpectedFile,pExpectedUser,pExpectedInternal) As %Boolean [ Private ]",
            "{",
            '    Quit ((pCCR = "") || (pCCR = pExpectedCCR)) && ..PendingMatches(pID,pName,pAction,pCCR,pSource,pFile,pUser,pInternal,pExpectedName,pExpectedAction,pCCR,pExpectedSource,pExpectedFile,pExpectedUser,pExpectedInternal)',
            "}",
            "",
            "ClassMethod SHA256(pBytes) As %String [ Private ]",
            "{",
            '    Set hash = $System.Encryption.SHAHash(256,pBytes), hex = "", digits = "0123456789abcdef"',
            '    For index = 1:1:$Length(hash) { Set byte = $Ascii(hash,index), hex = hex_$Extract(digits,(byte\\16)+1)_$Extract(digits,(byte#16)+1) }',
            "    Quit hex",
            "}",
            "",
            "ClassMethod ValidSHA(pValue) As %Boolean [ Private ]",
            "{",
            "    If $Length(pValue) '= 64 Quit 0",
            '    For index=1:1:64 { If \'$Find("0123456789abcdef",$Extract(pValue,index)) Quit 0 }',
            "    Quit 1",
            "}",
            "",
            "ClassMethod CanonicalCRLF(pText) As %String [ Private ]",
            "{",
            '    Set text = $Replace($Replace(pText,$Char(13,10),$Char(10)),$Char(13),$Char(10))',
            '    While ($Length(text) > 0) && (($Extract(text,*) = $Char(10)) || ($Extract(text,*) = " ")) { Set text = $Extract(text,1,*-1) }',
            '    Quit $Replace(text_$Char(10),$Char(10),$Char(13,10))',
            "}",
            "",
            "ClassMethod ControlIntact(Output pSuspendLevel) As %Boolean [ Private ]",
            "{",
            "    Set pSuspendLevel = ##class(TC.ChangeControl.Interface).GetSuspendLevel()",
            f'    If $Namespace \'= "{cls._escape_cos(namespace)}" Quit 0',
            '    If ##class(TC.ChangeControl.Interface).ChangeControlClassGet() \'= "TC.ChangeControl.CCR" Quit 0',
            '    If ##class(%Studio.SourceControl.Interface).SourceControlClassGet() \'= "%Studio.SourceControl.ISC" Quit 0',
            "    If ##class(TC.ChangeControl.Interface).IsSuspended() Quit 0",
            "    If +pSuspendLevel '= 0 Quit 0",
            "    Quit 1",
            "}",
            "",
            "ClassMethod CompiledArtifactsCurrent(pClass) As %Boolean [ Private ]",
            "{",
            "    If '$System.OBJ.IsUpToDate(pClass) Quit 0",
            '    Set compiled = ##class(%Dictionary.CompiledMethod).%OpenId(pClass_"||Mutate",0)',
            "    If '$IsObject(compiled) Quit 0",
            '    If (\'compiled.SqlProc) || (compiled.SqlName \'= "Mutate") Quit 0',
            "    Quit 1",
            "}",
            "",
            "ClassMethod BeforeFail(pCode,pMessage,pBinding,pSourceBinding,pSuspendLevel,pCurrentSHA=\"\",pMutationStarted=0) As %String [ Private ]",
            "{",
            '    Quit ..Result(0,"REJECTED",pCode,pMessage,pBinding,pSourceBinding,pSuspendLevel,pCurrentSHA,0,0,0,0,0,0,0,0,0,0,+pMutationStarted)',
            "}",
            "",
            "ClassMethod NoMutation(pState,pCurrentSHA,pBinding,pSourceBinding,pSuspendLevel) As %String [ Private ]",
            "{",
            '    Quit ..Result($Select(pState="UNCHANGED":1,1:0),pState,"","",pBinding,pSourceBinding,pSuspendLevel,pCurrentSHA,0,0,0,0,0,0,0,0,0,0,0)',
            "}",
            "",
            "ClassMethod Success(pState,pCurrentSHA,pBinding,pSourceBinding,pSuspendLevel,pChangeID,pCheckout) As %String [ Private ]",
            "{",
            '    Quit ..Result(1,pState,"","",pBinding,pSourceBinding,pSuspendLevel,pCurrentSHA,1,1,1,+pChangeID,+pCheckout,1,0,0,0,0,1)',
            "}",
            "",
            "ClassMethod Retained(pState,pMessage,pBinding,pSourceBinding,pSuspendLevel,pCheckout,pSource,pTracking,pRowID,pCurrentSHA,pMutationStarted) As %String [ Private ]",
            "{",
            '    Quit ..Result(0,pState,pState,pMessage,pBinding,pSourceBinding,pSuspendLevel,pCurrentSHA,0,+pTracking,0,+pRowID,+pCheckout,+pTracking,1,+pCheckout,+pSource,+pTracking,+pMutationStarted)',
            "}",
            "",
            "ClassMethod Result(pOK,pState,pCode,pMessage,pBinding,pSourceBinding,pSuspendLevel,pCurrentSHA,pRoundTrip,pTracking,pCompile,pChangeID,pCheckout,pCreatedTracking,pManual,pCheckoutMay,pSourceMay,pTrackingMay,pMutationStarted) As %String [ Private ]",
            "{",
            "    Set result = ##class(%DynamicObject).%New()",
            f'    Do result.%Set("protocol","{policy.initializer_protocol}"),result.%Set("ok",+pOK),result.%Set("state",pState),result.%Set("code",pCode),result.%Set("message",pMessage),result.%Set("namespace",$Namespace),result.%Set("class","{_CCR_MUTATION_CLASS}"),result.%Set("ccr","{ccr_id}"),result.%Set("candidate_sha256","{candidate_sha256}")',
            f'    Do result.%Set("upgrade",{1 if upgrade else 0}),result.%Set("expected_current_sha256","{expected_current_sha256}"),result.%Set("current_sha256",pCurrentSHA)',
            '    Do result.%Set("binding",pBinding),result.%Set("source_binding",pSourceBinding),result.%Set("suspend_level",+pSuspendLevel),result.%Set("controlled_active",$Select((pBinding="TC.ChangeControl.CCR")&&(pSourceBinding="%Studio.SourceControl.ISC")&&(+pSuspendLevel=0):1,1:0)),result.%Set("suspension_scope","same-request-process")',
            '    Do result.%Set("round_trip",+pRoundTrip),result.%Set("tracking",+pTracking),result.%Set("compile",+pCompile),result.%Set("change_id",+pChangeID),result.%Set("checkout_created",+pCheckout),result.%Set("created_tracking",+pCreatedTracking),result.%Set("mutation_started",+pMutationStarted),result.%Set("manual_action_required",+pManual)',
            '    Do result.%Set("checkout_may_exist",+pCheckoutMay),result.%Set("source_may_exist",+pSourceMay),result.%Set("tracking_may_exist",+pTrackingMay)',
            "    Quit result.%ToJSON()",
            "}",
            "}",
        ])
        return lines

    def initialize_ccr_mutation(self, source_bytes, ccr_id, candidate_sha256,
                                upgrade=False, expected_current_sha256=""):
        """Conditionally install or upgrade only the packaged MCP.CCRMutation."""
        import uuid

        # Same exact canonical BASE route gate as the fixed SQLProc transport.
        self._require_canonical_base_ccr_mutation()
        if any(marker in self.namespace.upper() for marker in self.policy.production_markers):
            raise LocalPolicyError("CCR initializer requires the configured non-production canonical BASE namespace.")
        if (not isinstance(source_bytes, bytes) or not source_bytes
                or _canonical_crlf_bytes(source_bytes) != source_bytes
                or hashlib.sha256(source_bytes).hexdigest() != candidate_sha256):
            raise LocalPolicyError("Packaged MCP.CCRMutation source hash mismatch.")
        self._require_ccr_id(ccr_id)
        if type(upgrade) is not bool:
            raise LocalPolicyError("upgrade must be boolean.")
        if upgrade:
            if not re.fullmatch(r"[0-9a-f]{64}", expected_current_sha256 or ""):
                raise LocalPolicyError("Upgrade requires a lowercase 64-character expected current SHA-256.")
        elif expected_current_sha256:
            raise LocalPolicyError("expected_current_sha256 is valid only with upgrade.")

        runner_class = f"User.McpTmpRun{uuid.uuid4().hex[:12]}"
        self._require_ephemeral_runner(runner_class)
        content = self._ccr_initializer_runner_content(
            runner_class, source_bytes, self.namespace, ccr_id, candidate_sha256, upgrade,
            expected_current_sha256, policy=self.policy,
        )
        if self._ephemeral_runner_has_pending_change(runner_class):
            raise LocalPolicyError("Initializer runner blocked by pre-existing source-control residue.")
        if not self._ephemeral_runner_is_absent(runner_class):
            raise LocalPolicyError("Initializer runner absence could not be proven.")

        operation_error = None
        record = None
        try:
            self._ephemeral_runner_save(runner_class, content)
            self._ephemeral_runner_compile(runner_class)
            output = self._ephemeral_runner_query(runner_class)
            try:
                record = json.loads(output)
            except (TypeError, ValueError):
                raise LocalPolicyError("Initializer outcome is unverified; do not retry automatically. Manual inspection is required.") from None
            required = {
                "protocol", "ok", "state", "code", "message", "namespace", "class", "ccr",
                "candidate_sha256", "upgrade", "expected_current_sha256", "current_sha256",
                "binding", "source_binding", "suspend_level",
                "controlled_active", "suspension_scope", "round_trip", "tracking", "compile",
                "change_id", "checkout_created", "created_tracking", "mutation_started",
                "manual_action_required",
                "checkout_may_exist", "source_may_exist", "tracking_may_exist",
            }
            if not isinstance(record, dict) or set(record) != required:
                raise LocalPolicyError("Initializer outcome is unverified; do not retry automatically. Manual inspection is required.")
            request_bound = {
                "protocol": self.policy.initializer_protocol, "namespace": self.namespace,
                "class": _CCR_MUTATION_CLASS, "ccr": ccr_id,
                "candidate_sha256": candidate_sha256, "upgrade": int(upgrade),
                "expected_current_sha256": expected_current_sha256,
                "binding": "TC.ChangeControl.CCR", "source_binding": "%Studio.SourceControl.ISC",
                "suspend_level": 0, "controlled_active": 1,
                "suspension_scope": "same-request-process",
            }
            integer_fields = {
                "ok", "upgrade", "suspend_level", "controlled_active", "round_trip",
                "tracking", "compile", "change_id", "checkout_created", "created_tracking",
                "mutation_started", "manual_action_required", "checkout_may_exist",
                "source_may_exist", "tracking_may_exist",
            }
            if (any(record.get(key) != value for key, value in request_bound.items())
                    or any(type(record.get(key)) is not int for key in integer_fields)
                    or not isinstance(record.get("current_sha256"), str)):
                raise LocalPolicyError("Initializer outcome is unverified; do not retry automatically. Manual inspection is required.")
            state = record["state"]
            if state in {"UNCHANGED", "DIFFERENT", "SOURCE_MATCH_NOT_COMPILED"}:
                # Only UNCHANGED is a verified success: source matches AND the compiled
                # artifact plus the MCP.Mutate projection were verified current.
                # SOURCE_MATCH_NOT_COMPILED is fail-closed evidence; compile is a mutation.
                if (record["ok"] != (1 if state == "UNCHANGED" else 0)
                        or record["code"] != "" or record["message"] != ""
                        or not re.fullmatch(r"[0-9a-f]{64}", record["current_sha256"])
                        or (state == "DIFFERENT" and record["current_sha256"] == candidate_sha256)
                        or (state != "DIFFERENT" and record["current_sha256"] != candidate_sha256)
                        or any(record[key] != 0 for key in (
                            "round_trip", "tracking", "compile", "change_id", "checkout_created",
                            "created_tracking", "mutation_started", "manual_action_required",
                            "checkout_may_exist", "source_may_exist", "tracking_may_exist",
                        ))):
                    raise LocalPolicyError("Initializer no-mutation outcome is unverified; do not retry automatically.")
            elif state in {"INSTALLED", "UPGRADED"}:
                expected_state = "UPGRADED" if upgrade else "INSTALLED"
                if (state != expected_state or record["ok"] != 1 or record["code"] != ""
                        or record["message"] != "" or record["current_sha256"] != candidate_sha256
                        or any(record[key] != 1 for key in (
                            "round_trip", "tracking", "compile", "created_tracking", "mutation_started",
                        ))
                        or record["checkout_created"] != int(upgrade)
                        or record["manual_action_required"] != 0
                        or any(record[key] != 0 for key in (
                            "checkout_may_exist", "source_may_exist", "tracking_may_exist",
                        ))
                        or record["change_id"] <= 0):
                    raise LocalPolicyError("Initializer mutation outcome is unverified; do not retry automatically.")
            else:
                raise LocalPolicyError(
                    f"Initializer {state or 'UNKNOWN'} ({record.get('code', 'UNKNOWN')}); "
                    "do not retry automatically. Manual inspection may be required."
                )
        except Exception as exc:
            operation_error = exc
        finally:
            removed = self._delete_ephemeral_runner(runner_class)
            try:
                residue = self._ephemeral_runner_has_pending_change(runner_class)
            except Exception:
                residue = True
            try:
                absent = self._ephemeral_runner_is_absent(runner_class)
            except Exception:
                absent = False
            if not removed or not absent or residue:
                raise LocalPolicyError(
                    "Initializer runner cleanup is unverified; inspect the runner and pending queue manually. "
                    "Do not retry the target initializer automatically."
                )
        if operation_error is not None:
            if isinstance(operation_error, LocalPolicyError):
                raise operation_error
            raise LocalPolicyError("Initializer outcome is unverified; do not retry automatically. Manual inspection is required.") from None
        record["runner_cleanup"] = 1
        record["runner_residue"] = 0
        return record

    def save_class_from_file_active_ccr(self, document_name, source_bytes, ccr_id,
                                        compile_after=False):
        """Overwrite one class through a same-process TrakCare CCR lifecycle."""
        raise LocalPolicyError(
            "Runner-based active-CCR overwrite was removed; use iris_ccr_mutate_class_from_file."
        )
        import base64

        self._require_active_ccr_class_overwrite()
        self._validate_document_name(document_name)
        if not document_name.endswith(".cls"):
            raise LocalPolicyError("Active-CCR overwrite accepts class documents only.")
        requested_ccr = str(ccr_id)
        if not requested_ccr.strip() or not requested_ccr.isascii():
            raise LocalPolicyError("ccr_id must be an explicit non-empty ASCII value.")
        if not isinstance(source_bytes, bytes) or not source_bytes:
            raise LocalPolicyError("The local class source must be non-empty UTF-8 bytes.")
        try:
            source_text = source_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise LocalPolicyError("The local class source is not valid UTF-8.") from None

        expected_class = document_name[:-4]
        declarations = re.findall(
            r"(?im)^\s*Class\s+(%?[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\b",
            source_text,
        )
        if declarations != [expected_class]:
            raise LocalPolicyError("The local source must declare exactly the requested class.")

        encoded = base64.b64encode(source_bytes).decode("ascii")
        # Bound each ObjectScript literal and preserve Base64 quantum boundaries.
        chunks = [encoded[pos:pos + 24000] for pos in range(0, len(encoded), 24000)]
        code_lines = [
            f'Set documentName = "{self._escape_cos(document_name)}"',
            f'Set expectedClass = "{self._escape_cos(expected_class)}"',
            f'Set requestedCCR = "{self._escape_cos(requested_ccr)}"',
            f'Set compileAfter = {1 if compile_after else 0}',
            'Set flowOK = 1, terminal = "", externalName = "", sessionReady = 0',
            'Try {',
            "    If '$IsObject($Get(%session)) {",
            '        Set terminal = "ERROR_SESSION|message=CCR change session is unavailable", flowOK = 0',
            '    } Else {',
            '        Set priorSessionKeyDefined = $Data(%session.Data("CHANGECONTROL.KEY"))',
            '        Set priorSessionKey = $Get(%session.Data("CHANGECONTROL.KEY"))',
            '        Set %session.Data("CHANGECONTROL.KEY") = requestedCCR',
            '        Set sessionReady = 1',
            '    }',
            '    If flowOK {',
            '    Set binding = ##class(TC.ChangeControl.Interface).ChangeControlClassGet()',
            "    If binding '= \"TC.ChangeControl.CCR\" {",
            '        Set terminal = "ERROR_BINDING|message=Exact TC.ChangeControl.CCR binding is required", flowOK = 0',
            '    }',
            '    }',
            '    If flowOK {',
            '        Set isSuspended = ##class(TC.ChangeControl.Interface).IsSuspended()',
            '        Set suspendLevel = ##class(TC.ChangeControl.Interface).GetSuspendLevel()',
            "        If isSuspended || ((suspendLevel '= \"\") && ('$IsValidNum(suspendLevel) || (+suspendLevel '= 0))) {",
            '            Set terminal = "ERROR_SUSPENDED|message=Change control is suspended or its level is unknown", flowOK = 0',
            '        }',
            '    }',
            '    If flowOK {',
            '        Set scInst = $ClassMethod(binding, "%New", "")',
            "        If '$IsObject(scInst) {",
            '            Set terminal = "ERROR_SOURCE_CONTROL|message=Unable to instantiate the exact CCR binding", flowOK = 0',
            '        } Else {',
            '            Set %SourceControl = scInst',
            '        }',
            '    }',
            '    If flowOK {',
            '        Set externalName = scInst.ExternalName(documentName)',
            '        If externalName = "" {',
            '            Set terminal = "ERROR_EXTERNAL_NAME|message=CCR returned an empty external filename", flowOK = 0',
            '        }',
            '    }',
            '    If flowOK {',
            '        Set inSC = 0, editable = 0, isCheckedOut = 0, checkoutUser = ""',
            '        Set sc = scInst.GetStatus(documentName, .inSC, .editable, .isCheckedOut, .checkoutUser)',
            '        If $$$ISERR(sc) { Set terminal = "ERROR_STATUS|message=CCR status check failed", flowOK = 0 }',
            '    }',
            '    If flowOK {',
            '        Set pendingBefore = ##class(%Studio.SourceControl.Change).IsUncommitted(externalName)',
            '        If pendingBefore {',
            '            Set change = ##class(%Studio.SourceControl.Change).OpenUncommitted(externalName)',
            "            If '$IsObject(change) {",
            '                Set terminal = "ERROR_CHANGE_LOOKUP|message=Existing pending change is unreadable", flowOK = 0',
            "            } ElseIf change.InternalName '= documentName {",
            '                Set terminal = "ERROR_DOCUMENT_MISMATCH|message=Existing pending change targets another document", flowOK = 0',
            "            } ElseIf change.CCR '= requestedCCR {",
            '                Set terminal = "ERROR_CCR_MISMATCH|message=Existing pending change belongs to another CCR", flowOK = 0',
            '            }',
            '        } ElseIf inSC && (isCheckedOut || editable) {',
            '            Set terminal = "ERROR_STALE_CHECKOUT|message=Writable source has no pending change row", flowOK = 0',
            "        } ElseIf 'inSC {",
            '            Set terminal = "ERROR_NOT_IN_SOURCE_CONTROL|message=Active-CCR overwrite requires an existing source-controlled class", flowOK = 0',
            '        } Else {',
            '            Set sc = scInst.CheckOut(documentName, 0, 0)',
            '            If $$$ISERR(sc) { Set terminal = "ERROR_CHECKOUT|message=CCR checkout failed", flowOK = 0 }',
            '        }',
            '    }',
            '    If flowOK {',
            '        Set sc = ##class(%Studio.SourceControl.Change).UpdateUncommitted(externalName, , , , , , , requestedCCR)',
            '        If $$$ISERR(sc) { Set terminal = "ERROR_CCR_ASSIGNMENT|message=Unable to assign checkout to requested CCR", flowOK = 0 }',
            '    }',
            '    If flowOK {',
            '        Set changeCount = 0, changeID = ""',
            '        &SQL(SELECT COUNT(*), MIN(ID) INTO :changeCount, :changeID FROM %Studio_SourceControl.Change WHERE Committed = 0 AND InternalName = :documentName)',
            "        If SQLCODE || (changeCount '= 1) {",
            '            Set terminal = "ERROR_CHANGE_COUNT|message=Checkout did not produce exactly one pending document row", flowOK = 0',
            '        }',
            '    }',
            '    If flowOK {',
            '        Set change = ##class(%Studio.SourceControl.Change).%OpenId(changeID)',
            "        If '$IsObject(change) || (change.InternalName '= documentName) || (change.CCR '= requestedCCR) || (change.ItemFile '= externalName) {",
            '            Set terminal = "ERROR_CHECKOUT_MISMATCH|message=Checkout row does not exactly match document and CCR", flowOK = 0',
            '        }',
            '    }',
            '    If flowOK {',
            '        Set sc = scInst.OnBeforeSave(documentName)',
            '        If $$$ISERR(sc) { Set terminal = "ERROR_BEFORE_SAVE|message=CCR rejected the save", flowOK = 0 }',
            '    }',
            '    If flowOK {',
            '        Set sourceStream = ##class(%Stream.TmpBinary).%New()',
        ]
        for chunk in chunks:
            code_lines.extend([
                '        If flowOK {',
                f'            Set streamSC = sourceStream.Write($System.Encryption.Base64Decode("{chunk}"))',
                '            If $$$ISERR(streamSC) { Set terminal = "ERROR_STREAM|message=Unable to stage UTF-8 source", flowOK = 0 }',
                '        }',
            ])
        code_lines.extend([
            '        If flowOK {',
            '            Do sourceStream.Rewind()',
            '            Set loadSC = $System.OBJ.LoadStream(sourceStream, "", .loadErrors, .loadedList, 0, "", documentName, "UTF8")',
            '            If $$$ISERR(loadSC) { Set terminal = "ERROR_LOAD|message=Class import failed", flowOK = 0 }',
            '        }',
            '    }',
            '    If flowOK {',
            '        Set sc = scInst.OnAfterSave(documentName, 0)',
            '        If $$$ISERR(sc) { Set terminal = "ERROR_AFTER_SAVE|message=CCR export/update failed", flowOK = 0 }',
            '    }',
            '    If flowOK {',
            '        Set sc = ##class(%Studio.SourceControl.Change).UpdateUncommitted(externalName, , , , , , , requestedCCR)',
            '        If $$$ISERR(sc) { Set terminal = "ERROR_CCR_ASSIGNMENT|message=Unable to preserve requested CCR after export", flowOK = 0 }',
            '    }',
            '    If flowOK && compileAfter {',
            '        Set compileSC = $System.OBJ.Compile(expectedClass, "ck", .compileErrors)',
            '        If $$$ISERR(compileSC) { Set terminal = "ERROR_COMPILE|message=Class compilation failed", flowOK = 0 }',
            '    }',
            '    If flowOK {',
            '        Set changeCount = 0, changeID = ""',
            '        &SQL(SELECT COUNT(*), MIN(ID) INTO :changeCount, :changeID FROM %Studio_SourceControl.Change WHERE Committed = 0 AND InternalName = :documentName)',
            "        If SQLCODE || (changeCount '= 1) {",
            '            Set terminal = "ERROR_CHANGE_COUNT|message=Expected exactly one pending change for the document", flowOK = 0',
            '        }',
            '    }',
            '    If flowOK {',
            '        Set change = ##class(%Studio.SourceControl.Change).%OpenId(changeID)',
            "        If '$IsObject(change) {",
            '            Set terminal = "ERROR_CHANGE_LOOKUP|message=Verified pending change is unreadable", flowOK = 0',
            "        } ElseIf change.InternalName '= documentName {",
            '            Set terminal = "ERROR_DOCUMENT_MISMATCH|message=Pending change document mismatch", flowOK = 0',
            "        } ElseIf change.CCR '= requestedCCR {",
            '            Set terminal = "ERROR_CCR_MISMATCH|message=Pending change CCR mismatch", flowOK = 0',
            "        } ElseIf change.ItemFile '= externalName {",
            '            Set terminal = "ERROR_ITEM_FILE_MISMATCH|message=Pending change external file mismatch", flowOK = 0',
            '        }',
            '    }',
            '} Catch ex {',
            '    Set terminal = "ERROR_EXCEPTION|message=Active CCR overwrite failed", flowOK = 0',
            '}',
            'If sessionReady {',
            '    Try {',
            '        If priorSessionKeyDefined {',
            '            Set %session.Data("CHANGECONTROL.KEY") = priorSessionKey',
            '        } Else {',
            '            Kill %session.Data("CHANGECONTROL.KEY")',
            '        }',
            '    } Catch restoreEx {',
            '        Set terminal = "ERROR_SESSION_CLEANUP|message=Unable to restore prior CCR change session", flowOK = 0',
            '    }',
            '}',
            'If flowOK {',
            '    Write "VERIFIED_ACTIVE_CCR_SAVE|document_enc=", $ZConvert(documentName, "O", "URL"), "|ccr_enc=", $ZConvert(requestedCCR, "O", "URL"), "|compiled=", compileAfter, !',
            '} Else {',
            '    Write terminal, !',
            '}',
        ])

        output = self._execute_direct_objectscript("\n".join(code_lines))
        lines = [line.strip() for line in str(output).splitlines() if line.strip()]
        if len(lines) != 1 or not lines[0].startswith("VERIFIED_ACTIVE_CCR_SAVE|"):
            detail = (lines[-1] if lines and lines[-1].startswith("ERROR_")
                      else "ERROR_RESPONSE|message=No verified terminal result")
            return False, detail
        try:
            fields = _parse_pipe_record(lines[0], "VERIFIED_ACTIVE_CCR_SAVE")
            if set(fields) != {"document_enc", "ccr_enc", "compiled"}:
                raise ValueError("Invalid active-CCR result fields")
            verified_document = _decode_record_value(fields["document_enc"])
            verified_ccr = _decode_record_value(fields["ccr_enc"])
            if fields["compiled"] not in {"0", "1"}:
                raise ValueError("Invalid compilation result")
        except (UnicodeDecodeError, ValueError) as exc:
            return False, f"ERROR_RESPONSE|message={exc}"
        if (verified_document != document_name or verified_ccr != requested_ccr
                or fields["compiled"] != ("1" if compile_after else "0")):
            return False, "ERROR_RESPONSE_MISMATCH|message=Verified result does not match the request"
        return True, "Active CCR overwrite and pending change were verified in one process."

    @classmethod
    def _runner_class_content(cls, class_name, code_str):
        class_content = [
            f"Class {class_name} [ Final ]",
            "{",
            f"ClassMethod Execute() As %String(MAXLEN={cls._EPHEMERAL_RESULT_MAXLEN}) [ SqlProc ]",
            "{",
            "    Set savedIO = $IO, tempFile = \"\", fileOpen = 0",
            "    Set state = \"SETUP\", output = \"\"",
            "    Try {",
            "        Set tempFile = ##class(%File).TempFilename(\"txt\")",
            "        Open tempFile:(\"WNS\"):5",
            "        If '$Test {",
            "            Set state = \"OPEN\"",
            "        } Else {",
            "            Set fileOpen = 1",
            "            Use tempFile",
            "            Set state = \"EXECUTING\"",
            "            Try {",
            "                Do ..Payload()",
            "                Set state = \"OK\"",
            "            } Catch ex {",
            "                Set state = \"PAYLOAD\"",
            "            }",
            "            Use savedIO",
            "            Try {",
            "                Close tempFile",
            "                Set fileOpen = 0",
            "            } Catch ex {",
            "                If state = \"OK\" { Set state = \"CLOSE\" }",
            "            }",
            "            If state = \"OK\" {",
            "                Set stream = ##class(%Stream.FileCharacter).%New()",
            "                Set sc = stream.LinkToFile(tempFile)",
            "                If $$$ISERR(sc) {",
            "                    Set state = \"LINK\"",
            "                } Else {",
            "                    Try {",
            "                        While 'stream.AtEnd {",
            "                            Set chunk = stream.Read(32000)",
            f"                            If ($Length(output) + $Length(chunk)) > {cls._EPHEMERAL_OUTPUT_LIMIT} {{",
            "                                Set state = \"OUTPUT\"",
            "                                Quit",
            "                            }",
            "                            Set output = output _ chunk",
            "                        }",
            "                    } Catch ex {",
            "                        Set state = \"READ\"",
            "                    }",
            "                }",
            "            }",
            "        }",
            "    } Catch ex {",
            "        If (state '= \"SETUP\") && (state '= \"OPEN\") && (state '= \"PAYLOAD\") {",
            "            Set state = \"WRAPPER\"",
            "        }",
            "    }",
            "    Try { Use savedIO } Catch ex {",
            "        If (state '= \"SETUP\") && (state '= \"OPEN\") && (state '= \"PAYLOAD\") {",
            "            Set state = \"IO\"",
            "        }",
            "    }",
            "    If fileOpen {",
            "        Try { Close tempFile Set fileOpen = 0 } Catch ex {",
            "            If state = \"OK\" { Set state = \"CLOSE\" }",
            "        }",
            "    }",
            "    Set deleteFailed = 0",
            "    If tempFile '= \"\" {",
            "        Try {",
            "            If ##class(%File).Exists(tempFile) {",
            "                If '##class(%File).Delete(tempFile) { Set deleteFailed = 1 }",
            "            }",
            "        } Catch ex {",
            "            Set deleteFailed = 1",
            "        }",
            "    }",
            "    If deleteFailed {",
            "        If state = \"PAYLOAD\" { Quit \"MCPUNKNOWN|PAYLOAD_DELETE\" }",
            "        If (state = \"OPEN\") || (state = \"SETUP\") { Quit \"MCPUNKNOWN|SETUP_DELETE\" }",
            "        If state '= \"OK\" { Quit \"MCPUNKNOWN|OUTCOME_DELETE\" }",
            "        Quit \"MCPUNKNOWN|DELETE\"",
            "    }",
            "    If state = \"OK\" { Quit \"MCPOK|\" _ $System.Encryption.Base64Encode($ZConvert(output,\"O\",\"UTF8\"),1) }",
            "    If state = \"PAYLOAD\" { Quit \"MCPPAYLOAD|\" _ $System.Encryption.Base64Encode($ZConvert(output,\"O\",\"UTF8\"),1) }",
            "    If state = \"OPEN\" { Quit \"MCPSETUP|OPEN\" }",
            "    If state = \"SETUP\" { Quit \"MCPSETUP|SETUP\" }",
            "    Quit \"MCPUNKNOWN|\" _ state",
            "}",
            "",
            "ClassMethod Payload()",
            "{",
        ]
        for line in code_str.splitlines():
            class_content.append(f"    {line}")
        class_content.extend([
            "}",
            "}",
        ])
        return class_content

    def execute_code(self, code_str):
        """Compatibility alias: every runner-based diagnostic uses the RUNTIME gate.

        The legacy write-gated executor (orphan sweep, User package override) was
        removed; CCR state never authorizes or denies runner execution.
        """
        return self.execute_runtime_code(code_str)

    def dump_global(self, global_name, subscript=None, start_from=None, max_nodes=200):
        """
        Read or dump the content of a global (or a subtree of a global) in InterSystems IRIS.
        Supports pagination through start_from, node limits through max_nodes and formatting
        of $ListBuild structures and scalar/numeric values.
        """
        gname = (global_name or "").strip()
        if not gname:
            raise ValueError("The 'global_name' parameter is required.")

        if not gname.startswith("^"):
            gname = "^" + gname

        if "(" in gname and gname.endswith(")"):
            start_ref = gname
            base_gname = gname.split("(", 1)[0]
        else:
            base_gname = gname
            if subscript is not None and str(subscript).strip() != "":
                sub_str = str(subscript).strip()
                if not sub_str.startswith("("):
                    sub_str = "(" + sub_str + ")"
                start_ref = base_gname + sub_str
            else:
                start_ref = base_gname

        if start_from and str(start_from).strip():
            iter_ref = str(start_from).strip()
            if not iter_ref.startswith("^"):
                iter_ref = "^" + iter_ref
        else:
            iter_ref = start_ref

        try:
            max_n = int(max_nodes) if max_nodes else 200
        except (ValueError, TypeError):
            max_n = 200

        if max_n < 1:
            max_n = 200
        if max_n > 5000:
            max_n = 5000

        esc_start_ref = self._escape_cos(start_ref)
        esc_iter_ref = self._escape_cos(iter_ref)

        code_lines = [
            f'Set targetRef = "{esc_start_ref}"',
            f'Set iterRef = "{esc_iter_ref}"',
            f'Set maxNodes = {max_n}',
            'Set prefix = $Select(targetRef[")":$Extract(targetRef, 1, $Length(targetRef)-1) _ ",", 1:targetRef _ "(")',
            'Write "=== Dump de Global: ", targetRef, " (Max nodos: ", maxNodes, ") ===", !',
            'Set dTarget = $Data(@targetRef)',
            'Set dIter = $Data(@iterRef)',
            'If (dTarget = 0) && (dIter = 0) {',
            '    Set testQ = $Query(@iterRef)',
            '    If (testQ = "") || ($Extract(testQ, 1, $Length(prefix)) \'= prefix) {',
            '        Write "La global o nodo ", targetRef, " no existe (Data=0).", !',
            '        Quit',
            '    }',
            '}',
            'Set count = 0',
            'Set isFirst = 1',
            'Set curr = iterRef',
            'For {',
            '    If isFirst {',
            '        Set isFirst = 0',
            '        If (dIter = 1) || (dIter = 11) {',
            '            Set currNode = iterRef',
            '        } Else {',
            '            Set curr = $Query(@iterRef)',
            '            If curr = "" Quit',
            '            If $Extract(curr, 1, $Length(prefix)) \'= prefix Quit',
            '            Set currNode = curr',
            '        }',
            '    } Else {',
            '        Set curr = $Query(@curr)',
            '        If curr = "" Quit',
            '        If $Extract(curr, 1, $Length(prefix)) \'= prefix Quit',
            '        Set currNode = curr',
            '    }',
            '    If count >= maxNodes {',
            '        Write "=== Limit reached (", maxNodes, " nodes). Use start_from=\'", currNode, "\' to continue ===", !',
            '        Quit',
            '    }',
            '    Set val = $Get(@currNode)',
            '    Write currNode, " = "',
            '    If $ListValid(val) && ($ListLength(val) > 0) {',
            '        Set lStr = ""',
            '        Try {',
            '            Set ptr = 0',
            '            While $ListNext(val, ptr, item) {',
            '                Set iStr = $Select(\'$Data(item):"<null>", $ListValid(item) && ($ListLength(item)>0):"$lb(...)", item="":"""""", $IsValidNum(item):item, 1:"""" _ $ZCVT(item, "O", "JS") _ """")',
            '                Set lStr = lStr _ $Select(lStr="":"", 1:", ") _ iStr',
            '            }',
            '            Write "$lb(" _ lStr _ ")", !',
            '        } Catch { Write $Select(val="":"""""", $IsValidNum(val):val, 1:"""" _ $ZCVT(val, "O", "JS") _ """"), ! }',
            '    } Else {',
            '        Write $Select(val="":"""""", $IsValidNum(val):val, 1:"""" _ $ZCVT(val, "O", "JS") _ """"), !',
            '    }',
            '    Set count = count + 1',
            '}',
            'Write "=== Fin del Dump (Total nodos mostrados: ", count, ") ===", !',
        ]

        cos_code = "\n".join(code_lines)
        return self.execute_runtime_code(cos_code)

    def get_ccr_status(self, class_name=None, document_name=None):
        class_name = self._tracked_document_name(class_name, document_name)
        code = (
            'Set scClass = ##class(%Studio.SourceControl.Interface).SourceControlClassGet()\n'
            'If scClass = "" {\n'
            '    Write "{""has_sc"": false}", !\n'
            '    Quit\n'
            '}\n'
            'Set scInst = $ClassMethod(scClass, "%New", "")\n'
            'If \'$IsObject(scInst) {\n'
            '    Write "{""has_sc"": true, ""instantiated"": false}", !\n'
            '    Quit\n'
            '}\n'
            'Set inSC = 0, editable = 0, isCheckedOut = 0, user = ""\n'
            f'Set sc = scInst.GetStatus("{class_name}", .inSC, .editable, .isCheckedOut, .user)\n'
            'Write "{""has_sc"": true, ""instantiated"": true, ""in_sc"": ", inSC, ", ""editable"": ", editable, ", ""checked_out"": ", isCheckedOut, ", ""user"": """, user, """}", !\n'
        )
        output = self.execute_runtime_code(code)
        if isinstance(output, dict):
            return output
        try:
            return json.loads(output.strip())
        except Exception:
            raise Exception(f"Failed to parse CCR status output: {output}")

    def get_namespace_ccr_status(self):
        self._validate_target()
        return discover_namespace(self.namespace, self.base_url, self._read_metadata_query)

    def ccr_checkout(self, class_name, ccr_desc, document_name=None):
        class_name = self._tracked_document_name(class_name, document_name)
        self._require_cataloged_package(class_name)
        requested_ccr = str(ccr_desc)
        if not requested_ccr.strip() or not requested_ccr.isascii():
            raise ValueError("ccr_id must be a non-empty ASCII value")
        ccr_desc_escaped = self._escape_cos(requested_ccr)
        # New classes not yet in source control cannot be checked out (CheckOut tries to
        # back up a non-existent .xml and fails). For those the correct action is
        # AddToSourceControl, which exports the item and registers it under the CCR.
        code = f"""
        Set className = "{class_name}"
        Set requestedCCR = "{ccr_desc_escaped}"
        Set %ccrSession = requestedCCR
        Set flowOK = 1, verifiedAction = ""
        Set scClass = ##class(%Studio.SourceControl.Interface).SourceControlClassGet()
        If scClass = "" {{
            Write "ERROR_SOURCE_CONTROL|message=No active source control class is configured in this namespace", !
            Set flowOK = 0
        }}
        If flowOK {{
            Set scInst = $ClassMethod(scClass, "%New", "")
            If '$IsObject(scInst) {{
                Write "ERROR_SOURCE_CONTROL|message=Failed to instantiate source control class", !
                Set flowOK = 0
            }}
        }}
        If flowOK {{
            Set externalName = scInst.ExternalName(className)
            If externalName = "" {{
                Write "ERROR_EXTERNAL_NAME|message=Source control returned an empty external filename", !
                Set flowOK = 0
            }}
        }}
        If flowOK {{
            Set inSC = 0, editable = 0, isCheckedOut = 0, user = ""
            Set statusSC = scInst.GetStatus(className, .inSC, .editable, .isCheckedOut, .user)
            // GetStatus can print a NOTICE (e.g. "checked out by user ...") without a trailing
            // newline; end that line so the next record starts on its own line.
            Write:$X>0 !
            If $$$ISERR(statusSC) {{
                Write "ERROR_STATUS|message=", $ZConvert($System.Status.GetErrorText(statusSC), "O", "URL"), !
                Set flowOK = 0
            }}
        }}
        If flowOK {{
            Set pendingBefore = ##class(%Studio.SourceControl.Change).IsUncommitted(externalName)
            If pendingBefore {{
                Set change = ##class(%Studio.SourceControl.Change).OpenUncommitted(externalName)
                If '$IsObject(change) {{
                    Write "ERROR_CHANGE_LOOKUP|message=IsUncommitted is true but OpenUncommitted returned no object", !
                    Set flowOK = 0
                }} Else {{
                    Set actualCCR = change.CCR
                    If actualCCR'=requestedCCR {{
                        Write "ERROR_CCR_MISMATCH|expected_ccr_enc=", $ZConvert(requestedCCR, "O", "URL"), "|actual_ccr_enc=", $ZConvert(actualCCR, "O", "URL"), "|message=Existing uncommitted change belongs to a blank or different CCR", !
                        Set flowOK = 0
                    }} Else {{
                        Write "VERIFIED_CHECKOUT|action=idempotent|ccr_enc=", $ZConvert(actualCCR, "O", "URL"), !
                        Set flowOK = 0
                    }}
                }}
            }} ElseIf inSC && (isCheckedOut || editable) {{
                Write "ERROR_STALE_CHECKOUT|external_name_enc=", $ZConvert(externalName, "O", "URL"), "|message=Source file is writable or checked out but no uncommitted Change row exists; manual repair is required", !
                Set flowOK = 0
            }}
        }}
        If flowOK {{
            Set %ccrSession = requestedCCR
            If 'inSC {{
                Set verifiedAction = "added"
                Set sc = scInst.AddToSourceControl(className, requestedCCR)
            }} Else {{
                Set verifiedAction = "checkout"
                Set sc = scInst.CheckOut(className, requestedCCR)
            }}
            Write:$X>0 !
            If $$$ISERR(sc) {{
                Write "ERROR_CHECKOUT|message=", $ZConvert($System.Status.GetErrorText(sc), "O", "URL"), !
                Set flowOK = 0
            }}
        }}
        If flowOK {{
            Set pendingAfter = ##class(%Studio.SourceControl.Change).IsUncommitted(externalName)
            If 'pendingAfter {{
                Write "ERROR_MISSING_CHANGE|external_name_enc=", $ZConvert(externalName, "O", "URL"), "|message=Checkout returned OK but no uncommitted Change row exists", !
                Set flowOK = 0
            }}
        }}
        If flowOK {{
            Set change = ##class(%Studio.SourceControl.Change).OpenUncommitted(externalName)
            If '$IsObject(change) {{
                Write "ERROR_CHANGE_LOOKUP|message=Checkout created an unreadable uncommitted Change row", !
                Set flowOK = 0
            }} Else {{
                Set actualCCR = change.CCR
                If actualCCR'=requestedCCR {{
                    Write "ERROR_CCR_MISMATCH|expected_ccr_enc=", $ZConvert(requestedCCR, "O", "URL"), "|actual_ccr_enc=", $ZConvert(actualCCR, "O", "URL"), "|message=New uncommitted change is not associated with the requested CCR", !
                    Set flowOK = 0
                }} Else {{
                    Write "VERIFIED_CHECKOUT|action=", verifiedAction, "|ccr_enc=", $ZConvert(actualCCR, "O", "URL"), !
                }}
            }}
        }}
        """
        output = self._run_guarded_ccr_runner(code, requested_ccr)
        lines = [line.strip() for line in str(output).splitlines() if line.strip()]
        errors = [line for line in lines if line.startswith("ERROR")]
        if errors:
            return False, errors[-1]
        # AddToSourceControl / CheckOut may echo core output (e.g. "Assigned GUID: ...")
        # before or after the terminal record; locate the VERIFIED_CHECKOUT record itself
        # instead of assuming it is the last line. Exactly one record is expected.
        records = [line for line in lines if line.startswith("VERIFIED_CHECKOUT|")]
        if not records:
            return False, "ERROR_CHECKOUT_RESPONSE|message=No terminal checkout result was returned"
        if len(records) > 1:
            return False, "ERROR_CHECKOUT_RESPONSE|message=Multiple terminal checkout results were returned"

        terminal = records[0]
        try:
            fields = _parse_pipe_record(terminal, "VERIFIED_CHECKOUT")
            if set(fields) != {"action", "ccr_enc"}:
                raise ValueError("Campos VERIFIED_CHECKOUT invalids")
            if fields["action"] not in {"checkout", "added", "idempotent"}:
                raise ValueError("VERIFIED_CHECKOUT action not allowed")
            verified_ccr = _decode_record_value(fields["ccr_enc"])
        except (UnicodeDecodeError, ValueError) as exc:
            return False, f"ERROR_CHECKOUT_RESPONSE|message={exc}"

        if verified_ccr != requested_ccr:
            return False, (
                "ERROR_CHECKOUT_CCR_MISMATCH|"
                f"expected={requested_ccr}|actual={verified_ccr}"
            )
        messages = {
            "checkout": "Checkout completed and CCR association verified.",
            "added": "New document added and CCR association verified.",
            "idempotent": "The pending row was already associated with exactly the requested CCR.",
        }
        return True, messages[fields["action"]]

    def ccr_undocheckout(self, class_name, ccr_id, document_name=None):
        """Revert a checkout only when the pending row belongs to the explicit CCR.

        UndoCheckout is a CCR-tracked operation: it requires CONTROLLED_ACTIVE in
        the same runner process and never touches a row of another or blank CCR.
        """
        class_name = self._tracked_document_name(class_name, document_name)
        self._require_cataloged_package(class_name)
        requested_ccr = str(ccr_id)
        if not requested_ccr.strip() or not requested_ccr.isascii():
            raise ValueError("ccr_id must be a non-empty ASCII value")
        ccr_esc = self._escape_cos(requested_ccr)
        code = f"""
        Set className = "{class_name}"
        Set requestedCCR = "{ccr_esc}"
        Set flowOK = 1
        Set scClass = ##class(%Studio.SourceControl.Interface).SourceControlClassGet()
        If scClass = "" {{
            Write "ERROR_SOURCE_CONTROL|message=No active source control class is configured in this namespace", !
            Set flowOK = 0
        }}
        If flowOK {{
            Set scInst = $ClassMethod(scClass, "%New", "")
            If '$IsObject(scInst) {{
                Write "ERROR_SOURCE_CONTROL|message=Failed to instantiate source control class", !
                Set flowOK = 0
            }}
        }}
        If flowOK {{
            Set externalName = scInst.ExternalName(className)
            If externalName = "" {{
                Write "ERROR_EXTERNAL_NAME|message=Source control returned an empty external filename", !
                Set flowOK = 0
            }}
        }}
        If flowOK {{
            If '##class(%Studio.SourceControl.Change).IsUncommitted(externalName) {{
                Write "ERROR_NO_PENDING_CHANGE|message=No uncommitted Change row exists for this document", !
                Set flowOK = 0
            }}
        }}
        If flowOK {{
            Set change = ##class(%Studio.SourceControl.Change).OpenUncommitted(externalName)
            If '$IsObject(change) {{
                Write "ERROR_CHANGE_LOOKUP|message=IsUncommitted is true but OpenUncommitted returned no object", !
                Set flowOK = 0
            }} ElseIf change.CCR'=requestedCCR {{
                Write "ERROR_CCR_MISMATCH|expected_ccr_enc=", $ZConvert(requestedCCR, "O", "URL"), "|actual_ccr_enc=", $ZConvert(change.CCR, "O", "URL"), "|message=Pending change belongs to a blank or different CCR", !
                Set flowOK = 0
            }}
        }}
        If flowOK {{
            Set %ccrSession = requestedCCR
            Set sc = scInst.UndoCheckout(className)
            If $$$ISERR(sc) {{
                Write "ERROR_UNDOCHECKOUT|message=", $ZConvert($System.Status.GetErrorText(sc), "O", "URL"), !
                Set flowOK = 0
            }}
        }}
        If flowOK {{
            Write "VERIFIED_UNDOCHECKOUT|ccr_enc=", $ZConvert(requestedCCR, "O", "URL"), !
        }}
        """
        output = self._run_guarded_ccr_runner(code, requested_ccr)
        lines = [line.strip() for line in str(output).splitlines() if line.strip()]
        errors = [line for line in lines if line.startswith("ERROR")]
        if errors:
            return False, errors[-1]
        if not lines:
            return False, "ERROR_UNDOCHECKOUT_RESPONSE|message=No terminal result was returned"
        try:
            fields = _parse_pipe_record(lines[-1], "VERIFIED_UNDOCHECKOUT")
            if set(fields) != {"ccr_enc"}:
                raise ValueError("Campos VERIFIED_UNDOCHECKOUT invalids")
            verified_ccr = _decode_record_value(fields["ccr_enc"])
        except (UnicodeDecodeError, ValueError) as exc:
            return False, f"ERROR_UNDOCHECKOUT_RESPONSE|message={exc}"
        if verified_ccr != requested_ccr:
            return False, f"ERROR_UNDOCHECKOUT_CCR_MISMATCH|expected={requested_ccr}|actual={verified_ccr}"
        return True, "UndoCheckout completed and CCR association verified."

    def ccr_commit(self, ccr_id, change_ids, upload_token):
        safe_ids = []
        for cid in change_ids:
            if isinstance(cid, bool) or not isinstance(cid, int):
                try:
                    cid = int(str(cid).strip())
                except Exception:
                    raise ValueError(f"invalid change_id (not an integer): {cid}")
            safe_ids.append(cid)
        if not safe_ids:
            raise ValueError("change_ids must contain at least one ID")
        if len(set(safe_ids)) != len(safe_ids):
            raise ValueError("change_ids must not contain duplicates")
        list_items = ",".join(str(cid) for cid in safe_ids)
        ccr_id_esc = self._escape_cos(ccr_id)
        upload_token_esc = self._escape_cos(upload_token)
        code = f"""
        Set ccr = "{ccr_id_esc}"
        Set token = "{upload_token_esc}"
        Set env = $$Env^%buildccr
        Set beforeID = 0, flowOK = 1, lockHeld = 0
        Try {{
            Lock +^IRISMCPCCRCommit(ccr):30
            If '$Test {{
                Write "ERROR_LOCK|message=Timed out waiting for the per-CCR commit lock", !
                Set flowOK = 0
            }} Else {{
                Set lockHeld = 1
            }}

            If flowOK {{
                Set beforeStmt = ##class(%SQL.Statement).%New()
                Set querySC = beforeStmt.%Prepare("SELECT TOP 1 ID FROM %Studio_SourceControl.ItemSet WHERE CCR = ? ORDER BY ID DESC")
                If $$$ISERR(querySC) {{
                    Write "ERROR_LOCAL_LOOKUP|message=", $ZConvert($System.Status.GetErrorText(querySC), "O", "URL"), !
                    Set flowOK = 0
                }}
            }}
            If flowOK {{
                Set beforeRS = beforeStmt.%Execute(ccr)
                If '$IsObject(beforeRS) {{
                    Write "ERROR_LOCAL_LOOKUP|message=Initial ItemSet query returned no result object", !
                    Set flowOK = 0
                }} ElseIf (beforeRS.%SQLCODE<0) {{
                    Write "ERROR_LOCAL_LOOKUP|message=", $ZConvert(beforeRS.%Message, "O", "URL"), !
                    Set flowOK = 0
                }} ElseIf beforeRS.%Next() {{
                    Set beforeID = +beforeRS.%Get("ID")
                }}
            }}

            // Change-control engine discovery, in the same process that commits.
            // %pkg.isc.changecontrol.CCR: Commit(ccr, list, .success, .absent, .errors)
            // TC.ChangeControl.CCR (native TrakCare): Commit(Description, Changes, Key); it reads the
            // token from %request and the user from %session, writes its log to the current device
            // and always returns $$$OK, so its outcome is proven by the ItemSet capture below.
            Set engine = "", tcLog = "", tcError = ""
            If flowOK {{
                If ##class(%Dictionary.CompiledMethod).%ExistsId("%pkg.isc.changecontrol.CCR||Commit") {{
                    Set engine = "%pkg.isc.changecontrol.CCR"
                }}
                If (engine = "") && ##class(%Dictionary.CompiledMethod).%ExistsId("TC.ChangeControl.CCR||Commit") {{
                    Set engineSpec = ##class(%Dictionary.CompiledMethod).%OpenId("TC.ChangeControl.CCR||Commit").FormalSpec
                    If ($Length(engineSpec, ",") = 3) && ($Piece($Piece(engineSpec, ",", 3), ":", 1) = "Key") {{
                        Set engine = "TC.ChangeControl.CCR"
                    }}
                }}
                If engine = "" {{
                    Write "ERROR_ENGINE|message=No supported change-control Commit was found (pkg.isc.changecontrol.CCR or TC.ChangeControl.CCR)", !
                    Set flowOK = 0
                }} Else {{
                    Write "COMMIT_ENGINE|name_enc=", $ZConvert(engine, "O", "URL"), !
                }}
            }}

            // The native TC Commit does not check the rows it bundles: prove every change is a
            // pending row of exactly this CCR before handing it over.
            If flowOK && (engine = "TC.ChangeControl.CCR") {{
                Set changeList = $ListBuild({list_items})
                For changeIdx=1:1:$ListLength(changeList) {{
                    Set changeId = $List(changeList, changeIdx)
                    Set change = ##class(%Studio.SourceControl.Change).%OpenId(changeId)
                    If '$IsObject(change) {{
                        Write "ERROR_CHANGE_PRECHECK|change_id=", changeId, "|message=Change does not exist", !
                        Set flowOK = 0
                    }} ElseIf change.Committed {{
                        Write "ERROR_CHANGE_PRECHECK|change_id=", changeId, "|message=Change is already committed", !
                        Set flowOK = 0
                    }} ElseIf change.CCR '= ccr {{
                        Write "ERROR_CHANGE_PRECHECK|change_id=", changeId, "|actual_ccr_enc=", $ZConvert(change.CCR, "O", "URL"), "|message=Change belongs to a blank or different CCR", !
                        Set flowOK = 0
                    }} ElseIf change.ItemFile = "" {{
                        Write "ERROR_CHANGE_PRECHECK|change_id=", changeId, "|message=Change has no ItemFile", !
                        Set flowOK = 0
                    }}
                    If 'flowOK {{
                        Quit
                    }}
                }}
                If flowOK && '$IsObject($Get(%session)) {{
                    Write "ERROR_SESSION|message=TC.ChangeControl.CCR Commit needs a CSP session and none is available", !
                    Set flowOK = 0
                }}
            }}

            If flowOK {{
                Set priorRequestDefined = $Data(%request)#2
                Set priorRequest = $Get(%request)
                Set %request = ##class(%CSP.Request).%New()
                Set %request.Data("UploadToken", 1) = token
                Set list = $ListBuild({list_items})
                Kill success, absent, errors
                If engine = "%pkg.isc.changecontrol.CCR" {{
                    Set commitSC = $ClassMethod(engine, "Commit", ccr, list, .success, .absent, .errors)
                }} Else {{
                    // TC records the committing user from %session; set it only when absent and restore it.
                    Set priorUserDefined = $Data(%session.Data("LOGON.USERCODE"))
                    Set priorUser = $Get(%session.Data("LOGON.USERCODE"))
                    If priorUser = "" {{
                        Set %session.Data("LOGON.USERCODE") = $Username
                    }}
                    Set commitIO = $IO, tcLogFile = ##class(%File).TempFilename("log"), tcLogOpen = 0
                    Set commitSC = $$$OK
                    Try {{
                        Open tcLogFile:("WNS"):5
                        If $Test {{
                            Set tcLogOpen = 1
                            Use tcLogFile
                            // Only with the log captured: TC writes its raw log to the current device.
                            Set commitSC = $ClassMethod(engine, "Commit", ccr, list, ccr)
                        }} Else {{
                            Set commitSC = $$$ERROR($$$GeneralError, "Could not open a temporary file to capture the TC commit log; commit not attempted")
                        }}
                    }} Catch tcEx {{
                        Set commitSC = tcEx.AsStatus()
                    }}
                    Use commitIO
                    If tcLogOpen {{
                        Close tcLogFile
                        Set tcStream = ##class(%Stream.FileCharacter).%New()
                        If $$$ISOK(tcStream.LinkToFile(tcLogFile)) {{
                            While 'tcStream.AtEnd && ($Length(tcLog) < 20000) {{
                                Set tcLog = tcLog _ tcStream.Read(4000)
                            }}
                        }}
                        Kill tcStream
                        Do ##class(%File).Delete(tcLogFile)
                    }}
                    If token'="" {{
                        Set tcLog = $Replace(tcLog, token, "<redacted>")
                    }}
                    If priorUserDefined {{
                        Set %session.Data("LOGON.USERCODE") = priorUser
                    }} Else {{
                        Kill %session.Data("LOGON.USERCODE")
                    }}
                    Set tcUpload = $ZStrip($Piece($Piece(tcLog, "Upload Status:", 2), $Char(10), 1), "<>WC")
                    Set tcError = ""
                    For tcLine=1:1:$Length(tcLog, $Char(10)) {{
                        Set tcText = $ZStrip($Piece(tcLog, $Char(10), tcLine), "<>WC")
                        If (tcText [ "ERROR") || (tcText [ "No ItemSet was created") {{
                            Set tcError = $Extract(tcText, 1, 300)
                            Quit
                        }}
                    }}
                    Write "COMMIT_UPLOAD_STATUS|message_enc=", $ZConvert(tcUpload, "O", "URL"), !
                }}
                If priorRequestDefined {{
                    Set %request = priorRequest
                }} Else {{
                    Kill %request
                }}
                If $$$ISERR(commitSC) {{
                    Set errorText = $System.Status.GetErrorText(commitSC)
                    If token'="" {{
                        Set errorText = $Replace(errorText, token, "<redacted>")
                    }}
                    Write "ERROR_COMMIT_STATUS|message=", $ZConvert(errorText, "O", "URL"), !
                    Set flowOK = 0
                }} Else {{
                    Set absentCount = $ListLength($Get(absent))
                    Set errorCount = $ListLength($Get(errors))
                    If (absentCount>0)||(errorCount>0) {{
                        Write "ERROR_COMMIT_RESULTS|absent_count=", absentCount, "|error_count=", errorCount, "|message=Commit reported absent or failed changes", !
                        Set flowOK = 0
                    }}
                }}
            }}

            If flowOK {{
                Set afterStmt = ##class(%SQL.Statement).%New()
                Set querySC = afterStmt.%Prepare("SELECT ID, Name, ItemSetFile FROM %Studio_SourceControl.ItemSet WHERE CCR = ? AND ID > ? ORDER BY ID")
                If $$$ISERR(querySC) {{
                    Write "ERROR_LOCAL_CAPTURE|message=", $ZConvert($System.Status.GetErrorText(querySC), "O", "URL"), !
                    Set flowOK = 0
                }}
            }}
            If flowOK {{
                Set afterRS = afterStmt.%Execute(ccr, beforeID)
                If '$IsObject(afterRS) {{
                    Write "ERROR_LOCAL_CAPTURE|message=Post-commit ItemSet query returned no result object", !
                    Set flowOK = 0
                }} ElseIf (afterRS.%SQLCODE<0) {{
                    Write "ERROR_LOCAL_CAPTURE|message=", $ZConvert(afterRS.%Message, "O", "URL"), !
                    Set flowOK = 0
                }} Else {{
                    Set newCount = 0, localID = "", localName = "", localFile = ""
                    While afterRS.%Next() {{
                        Set newCount = newCount + 1
                        Set localID = afterRS.%Get("ID")
                        Set localName = afterRS.%Get("Name")
                        Set localFile = afterRS.%Get("ItemSetFile")
                    }}
                    If newCount'=1 {{
                        Write "ERROR_ITEMSET_COUNT|after_id=", beforeID, "|new_count=", newCount, "|engine_enc=", $ZConvert(engine, "O", "URL"), "|engine_log_enc=", $ZConvert($Get(tcError), "O", "URL"), "|message=Commit must create exactly one local ItemSet for this CCR", !
                        Set flowOK = 0
                    }} ElseIf localName="" {{
                        Write "ERROR_LOCAL_CAPTURE|local_id=", localID, "|local_name=|message=New local ItemSet has no Name", !
                        Set flowOK = 0
                    }} ElseIf (engine = "TC.ChangeControl.CCR") && ($Get(tcUpload) '= "") && ($Get(tcUpload) '= "Success!") && ($Get(tcUpload) '[ "Check-in in progress") {{
                        // TC created the local ItemSet and removed the rows, but its upload failed:
                        // report it with the local id so iris_ccr_reupload_itemset can recover it.
                        Write "ERROR_COMMIT_UPLOAD|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=", $ZConvert("Local ItemSet created but TC upload failed: "_tcUpload_". Recover with iris_ccr_reupload_itemset; DO NOT re-commit.", "O", "URL"), !
                        Set flowOK = 0
                    }}
                }}
            }}
        }} Catch captureEx {{
            Set errorText = captureEx.DisplayString()
            If token'="" {{
                Set errorText = $Replace(errorText, token, "<redacted>")
            }}
            Write "ERROR_COMMIT_CAPTURE|message=", $ZConvert(errorText, "O", "URL"), !
            Set flowOK = 0
        }}
        If lockHeld {{
            Lock -^IRISMCPCCRCommit(ccr)
            Set lockHeld = 0
        }}

        If flowOK {{
            Set centralID = "", centralName = "", pollOK = 1
            For listAttempt=1:1:6 {{
                If 'pollOK {{
                    Quit
                }}
                Set centralRS = "", verifyException = ""
                Try {{
                    Set verifySC = ##class(%Studio.SourceControl.ItemSet).WSList(ccr, env, token, .centralRS)
                }} Catch listEx {{
                    Set verifyException = listEx.DisplayString()
                }}
                If verifyException'="" {{
                    Set errorText = verifyException
                    If token'="" {{
                        Set errorText = $Replace(errorText, token, "<redacted>")
                    }}
                    Write "ERROR_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=", $ZConvert(errorText, "O", "URL"), !
                    Set pollOK = 0
                }} ElseIf $$$ISERR(verifySC) {{
                    Set errorText = $System.Status.GetErrorText(verifySC)
                    If token'="" {{
                        Set errorText = $Replace(errorText, token, "<redacted>")
                    }}
                    Write "ERROR_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=", $ZConvert(errorText, "O", "URL"), !
                    Set pollOK = 0
                }} ElseIf '$IsObject(centralRS) {{
                    Write "ERROR_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=WSList returned no DataSet", !
                    Set pollOK = 0
                }} Else {{
                    Set scanException = ""
                    Try {{
                        While centralRS.Next() {{
                            If centralRS.Get("Name") = localName {{
                                Set centralID = centralRS.Get("ID")
                                Set centralName = centralRS.Get("Name")
                            }}
                        }}
                    }} Catch scanEx {{
                        Set scanException = scanEx.DisplayString()
                    }}
                    If scanException'="" {{
                        Set errorText = scanException
                        If token'="" {{
                            Set errorText = $Replace(errorText, token, "<redacted>")
                        }}
                        Write "ERROR_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=", $ZConvert(errorText, "O", "URL"), !
                        Set pollOK = 0
                    }}
                }}
                If centralID'="" {{
                    Quit
                }}
                If pollOK&&(listAttempt<6) {{
                    Hang 1
                }}
            }}

            If pollOK {{
                If centralID'="" {{
                    Write "VERIFIED|action=commit|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|server_id=", centralID, "|server_name=", $ZConvert(centralName, "O", "URL"), !
                }} Else {{
                    // The native Commit already uploaded the ItemSet (UploadToken in %request).
                    // WSList on this central is known to lag behind fresh uploads, so a second
                    // WSUpload here would create a duplicate commit in Perforce (observed as
                    // add+edit of the same revision). Never auto-retry; report pending instead.
                    Write "PENDING_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=Commit returned OK but exact ItemSet is not visible after bounded polling; DO NOT AUTO-RETRY because a duplicate upload may be created. Verify in the CCR portal; use iris_ccr_reupload_itemset only if the ItemSet is truly absent", !
                }}
            }}
        }}
        """
        try:
            output = self._execute_same_process_ccr_operation(code, ccr_id)
        except Exception as exc:
            raise RuntimeError(self._redact_secret(exc, upload_token)) from None
        return self._redact_secret(output, upload_token).strip()

    def _execute_same_process_ccr_operation(self, code_str, ccr_id):
        """Run a CCR bundle/upload/create operation on the canonical BASE only.

        The same-process guard is embedded in the runner: exact TC binding,
        source-control binding and suspension level zero are verified inside the
        executing IRIS process immediately before the operation body. ccr_id may
        be None only for WSCreate, which produces the CCR itself.
        """
        self._require_canonical_base_ccr_mutation()
        if ccr_id is None:
            tracking = "WSCreate"
        else:
            tracking = self._require_ccr_id(str(ccr_id))
        return self._run_guarded_ccr_runner(code_str, tracking)

    def list_uncommitted_changes(self, ccr=None, limit=200):
        """List the queue of uncommitted local changes (%Studio.SourceControl.Change).
        The returned 'ID' values are the integer change_ids that iris_ccr_commit consumes.
        Filters by CCR/Job when given. Uses the IDKEY (ORDER BY ID DESC) to avoid full scans."""
        try:
            limit = int(limit)
        except Exception:
            limit = 200
        where = "Committed = 0"
        params = []
        if ccr:
            where += " AND CCR = ?"
            params.append(ccr)
        query = (
            f"SELECT TOP {limit} ID, Name, Action, CCR, Source, ItemFile, "
            f'"User" AS ChangedUser, UpdatedTime '
            f"FROM %Studio_SourceControl.Change WHERE {where} ORDER BY ID DESC"
        )
        return self.run_query(query, params)

    def ccr_reupload_itemset(self, itemset_id, ccr_id, upload_token, sanitize=True):
        """Recovery: re-upload an already generated local ItemSet to the CCR server, optionally
        sanitizing its XML byte by byte (drops the non-ASCII bytes that break the central CCR
        SAX parser, #6301). Useful when an upload failed and left the ItemSet dangling and
        uncommitted, blocking retries through dependency. Cleans the main file and its
        'p.xml' payload."""
        itemset_id = int(itemset_id)
        ccr_esc = self._escape_cos(ccr_id)
        token_esc = self._escape_cos(upload_token)
        do_sanitize = "1" if sanitize else "0"
        code = f"""
        Set ccr = "{ccr_esc}"
        Set token = "{token_esc}"
        Set env = $$Env^%buildccr
        Set localID = {itemset_id}
        Set item = ##class(%Studio.SourceControl.ItemSet).%OpenId(localID)
        If '$IsObject(item) {{
            Write "ERROR_LOCAL_ITEMSET|local_id=", localID, "|message=Local ItemSet does not exist", !
        }} Else {{
            Set localName = item.Name
            Set mainFile = item.ItemSetFile
            If localName="" {{
                Write "ERROR_LOCAL_ITEMSET|local_id=", localID, "|local_name=|message=Local ItemSet has no Name", !
            }} Else {{
                Set centralID = "", centralName = "", pollOK = 1
                For listAttempt=1:1:6 {{
                    If 'pollOK {{
                        Quit
                    }}
                    Set rs = "", listException = ""
                    Try {{
                        Set listSC = ##class(%Studio.SourceControl.ItemSet).WSList(ccr, env, token, .rs)
                    }} Catch listEx {{
                        Set listException = listEx.DisplayString()
                    }}
                    If listException'="" {{
                        Set errorText = listException
                        If token'="" {{
                            Set errorText = $Replace(errorText, token, "<redacted>")
                        }}
                        Write "ERROR_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=", $ZConvert(errorText, "O", "URL"), !
                        Set pollOK = 0
                    }} ElseIf $$$ISERR(listSC) {{
                        Set errorText = $System.Status.GetErrorText(listSC)
                        If token'="" {{
                            Set errorText = $Replace(errorText, token, "<redacted>")
                        }}
                        Write "ERROR_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=", $ZConvert(errorText, "O", "URL"), !
                        Set pollOK = 0
                    }} ElseIf '$IsObject(rs) {{
                        Write "ERROR_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=WSList returned no DataSet", !
                        Set pollOK = 0
                    }} Else {{
                        Set scanException = ""
                        Try {{
                            While rs.Next() {{
                                If rs.Get("Name") = localName {{
                                    Set centralID = rs.Get("ID")
                                    Set centralName = rs.Get("Name")
                                }}
                            }}
                        }} Catch scanEx {{
                            Set scanException = scanEx.DisplayString()
                        }}
                        If scanException'="" {{
                            Set errorText = scanException
                            If token'="" {{
                                Set errorText = $Replace(errorText, token, "<redacted>")
                            }}
                            Write "ERROR_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=", $ZConvert(errorText, "O", "URL"), !
                            Set pollOK = 0
                        }}
                    }}
                    If centralID'="" {{
                        Quit
                    }}
                    If pollOK&&(listAttempt<6) {{
                        Hang 1
                    }}
                }}

                If pollOK {{
                    If centralID'="" {{
                        Write "VERIFIED|action=already_present|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|server_id=", centralID, "|server_name=", $ZConvert(centralName, "O", "URL"), "|bytes_removed=0|rollback_copies=0", !
                    }} ElseIf mainFile="" {{
                        Write "ERROR_DIRECT_UPLOAD|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=Local ItemSet has no ItemSetFile", !
                    }} Else {{
                        Set sanitizeOK = 1, removed = 0, rollbackCount = 0
                        If {do_sanitize} {{
                            Set payloadFile = $Piece(mainFile, ".xml", 1) _ "p.xml"
                            Set filePath(1) = mainFile, filePath(2) = payloadFile
                            Set fileRequired(1) = 1, fileRequired(2) = 0
                            Set suffix = ".mcp-" _ $Job _ "-" _ $Piece($Horolog, ",", 2)
                            For fIdx=1:1:2 {{
                                Set path = filePath(fIdx)
                                Set fileExists(fIdx) = ##class(%File).Exists(path)
                                Set rollbackReady(fIdx) = 0
                                If 'fileExists(fIdx) {{
                                    If fileRequired(fIdx) {{
                                        Write "ERROR_SANITIZE|file_index=", fIdx, "|operation=exists|message=Required source file does not exist", !
                                        Set sanitizeOK = 0
                                    }}
                                }} Else {{
                                    Set tmpPath(fIdx) = path _ suffix _ ".tmp"
                                    Set backupPath(fIdx) = path _ suffix _ ".rollback"
                                    If ##class(%File).Exists(tmpPath(fIdx))||##class(%File).Exists(backupPath(fIdx)) {{
                                        Write "ERROR_SANITIZE|file_index=", fIdx, "|operation=prepare|message=Temporary or rollback path already exists", !
                                        Set sanitizeOK = 0
                                    }} Else {{
                                        Set fileCopied = ##class(%File).CopyFile(path, backupPath(fIdx), 0)
                                        If 'fileCopied {{
                                            Write "ERROR_SANITIZE|file_index=", fIdx, "|operation=backup|message=Could not preserve rollback copy", !
                                            Set sanitizeOK = 0
                                        }} Else {{
                                            Set rollbackReady(fIdx) = 1
                                            Set rollbackCount = rollbackCount + 1
                                            Set fileReady = 1
                                            Try {{
                                                Set inS = ##class(%Stream.FileBinary).%New()
                                                Set fileSC = inS.LinkToFile(path)
                                                If $$$ISERR(fileSC) {{
                                                    Write "ERROR_SANITIZE|file_index=", fIdx, "|operation=link_input|message=", $ZConvert($System.Status.GetErrorText(fileSC), "O", "URL"), !
                                                    Set fileReady = 0, sanitizeOK = 0
                                                }}
                                                If fileReady {{
                                                    Set outS = ##class(%Stream.FileBinary).%New()
                                                    Set fileSC = outS.LinkToFile(tmpPath(fIdx))
                                                    If $$$ISERR(fileSC) {{
                                                        Write "ERROR_SANITIZE|file_index=", fIdx, "|operation=link_output|message=", $ZConvert($System.Status.GetErrorText(fileSC), "O", "URL"), !
                                                        Set fileReady = 0, sanitizeOK = 0
                                                    }}
                                                }}
                                                While fileReady&&('inS.AtEnd) {{
                                                    Set chunk = inS.Read(32000)
                                                    Set clean = ""
                                                    For i=1:1:$Length(chunk) {{
                                                        Set b = $Ascii(chunk, i)
                                                        If (b=9)||(b=10)||(b=13)||((b>=32)&&(b<=126)) {{
                                                            Set clean = clean _ $Char(b)
                                                        }} Else {{
                                                            Set removed = removed + 1
                                                        }}
                                                    }}
                                                    Set fileSC = outS.Write(clean)
                                                    If $$$ISERR(fileSC) {{
                                                        Write "ERROR_SANITIZE|file_index=", fIdx, "|operation=write|message=", $ZConvert($System.Status.GetErrorText(fileSC), "O", "URL"), !
                                                        Set fileReady = 0, sanitizeOK = 0
                                                    }}
                                                }}
                                                If fileReady {{
                                                    Set fileSC = outS.%Save()
                                                    If $$$ISERR(fileSC) {{
                                                        Write "ERROR_SANITIZE|file_index=", fIdx, "|operation=save|message=", $ZConvert($System.Status.GetErrorText(fileSC), "O", "URL"), !
                                                        Set sanitizeOK = 0
                                                    }}
                                                }}
                                            }} Catch fileEx {{
                                                Set errorText = fileEx.DisplayString()
                                                If token'="" {{
                                                    Set errorText = $Replace(errorText, token, "<redacted>")
                                                }}
                                                Write "ERROR_SANITIZE|file_index=", fIdx, "|operation=exception|message=", $ZConvert(errorText, "O", "URL"), !
                                                Set sanitizeOK = 0
                                            }}
                                            Kill inS, outS
                                        }}
                                    }}
                                }}
                            }}

                            If sanitizeOK {{
                                For fIdx=1:1:2 {{
                                    If $Get(fileExists(fIdx)) {{
                                        Set fileCopied = ##class(%File).CopyFile(tmpPath(fIdx), filePath(fIdx), 1)
                                        If 'fileCopied {{
                                            Write "ERROR_SANITIZE|file_index=", fIdx, "|operation=replace|message=Could not install sanitized file", !
                                            Set sanitizeOK = 0
                                        }}
                                    }}
                                }}
                            }}

                            For fIdx=1:1:2 {{
                                If $Get(tmpPath(fIdx))'="" {{
                                    If ##class(%File).Exists(tmpPath(fIdx)) {{
                                        Set fileDeleted = ##class(%File).Delete(tmpPath(fIdx))
                                        If 'fileDeleted {{
                                            Write "ERROR_SANITIZE|file_index=", fIdx, "|operation=cleanup|message=Could not delete temporary sanitized file", !
                                            Set sanitizeOK = 0
                                        }}
                                    }}
                                }}
                            }}

                            If 'sanitizeOK {{
                                Set rollbackOK = 1
                                For fIdx=1:1:2 {{
                                    If $Get(rollbackReady(fIdx)) {{
                                        Set fileCopied = ##class(%File).CopyFile(backupPath(fIdx), filePath(fIdx), 1)
                                        If 'fileCopied {{
                                            Write "ERROR_SANITIZE_ROLLBACK|file_index=", fIdx, "|operation=restore|message=Could not restore original file", !
                                            Set rollbackOK = 0
                                        }}
                                    }}
                                }}
                                Write "ERROR_SANITIZE_ABORTED|rollback_ok=", rollbackOK, "|message=Sanitization failed; upload was not attempted and rollback copies were preserved", !
                            }}
                        }}

                        If sanitizeOK {{
                            Set uploadException = ""
                            Try {{
                                Set uploadSC = ##class(%Studio.SourceControl.ItemSet).WSUpload(mainFile, ccr, token)
                            }} Catch uploadEx {{
                                Set uploadException = uploadEx.DisplayString()
                            }}
                            If uploadException'="" {{
                                Set errorText = uploadException
                                If token'="" {{
                                    Set errorText = $Replace(errorText, token, "<redacted>")
                                }}
                                Write "ERROR_DIRECT_UPLOAD|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=", $ZConvert(errorText, "O", "URL"), !
                            }} ElseIf $$$ISERR(uploadSC) {{
                                Set errorText = $System.Status.GetErrorText(uploadSC)
                                If token'="" {{
                                    Set errorText = $Replace(errorText, token, "<redacted>")
                                }}
                                Write "ERROR_DIRECT_UPLOAD|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=", $ZConvert(errorText, "O", "URL"), !
                            }} Else {{
                                Set verifiedID = "", verifiedName = "", postPollOK = 1
                                For verifyAttempt=1:1:6 {{
                                    If 'postPollOK {{
                                        Quit
                                    }}
                                    Set verifyRS = "", verifyException = ""
                                    Try {{
                                        Set verifySC = ##class(%Studio.SourceControl.ItemSet).WSList(ccr, env, token, .verifyRS)
                                    }} Catch verifyEx {{
                                        Set verifyException = verifyEx.DisplayString()
                                    }}
                                    If verifyException'="" {{
                                        Set errorText = verifyException
                                        If token'="" {{
                                            Set errorText = $Replace(errorText, token, "<redacted>")
                                        }}
                                        Write "ERROR_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=", $ZConvert(errorText, "O", "URL"), !
                                        Set postPollOK = 0
                                    }} ElseIf $$$ISERR(verifySC) {{
                                        Set errorText = $System.Status.GetErrorText(verifySC)
                                        If token'="" {{
                                            Set errorText = $Replace(errorText, token, "<redacted>")
                                        }}
                                        Write "ERROR_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=", $ZConvert(errorText, "O", "URL"), !
                                        Set postPollOK = 0
                                    }} ElseIf '$IsObject(verifyRS) {{
                                        Write "ERROR_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=WSList returned no DataSet after direct upload", !
                                        Set postPollOK = 0
                                    }} Else {{
                                        Set scanException = ""
                                        Try {{
                                            While verifyRS.Next() {{
                                                If verifyRS.Get("Name") = localName {{
                                                    Set verifiedID = verifyRS.Get("ID")
                                                    Set verifiedName = verifyRS.Get("Name")
                                                }}
                                            }}
                                        }} Catch scanEx {{
                                            Set scanException = scanEx.DisplayString()
                                        }}
                                        If scanException'="" {{
                                            Set errorText = scanException
                                            If token'="" {{
                                                Set errorText = $Replace(errorText, token, "<redacted>")
                                            }}
                                            Write "ERROR_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=", $ZConvert(errorText, "O", "URL"), !
                                            Set postPollOK = 0
                                        }}
                                    }}
                                    If verifiedID'="" {{
                                        Quit
                                    }}
                                    If postPollOK&&(verifyAttempt<6) {{
                                        Hang 1
                                    }}
                                }}
                                If postPollOK {{
                                    If verifiedID'="" {{
                                        Write "VERIFIED|action=uploaded|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|server_id=", verifiedID, "|server_name=", $ZConvert(verifiedName, "O", "URL"), "|bytes_removed=", removed, "|rollback_copies=", rollbackCount, !
                                    }} Else {{
                                        Write "PENDING_VERIFICATION|local_id=", localID, "|local_name=", $ZConvert(localName, "O", "URL"), "|message=WSUpload returned OK but exact ItemSet is not visible after bounded polling; DO NOT AUTO-RETRY because a duplicate upload may be created", !
                                    }}
                                }}
                            }}
                        }}
                    }}
                }}
            }}
        }}
        """
        try:
            output = self._execute_same_process_ccr_operation(code, ccr_id)
        except Exception as exc:
            raise RuntimeError(self._redact_secret(exc, upload_token)) from None
        return self._redact_secret(output, upload_token).strip()

    def ccr_create(self, title, description, ccr_type="", ccr_info="", site_code=None, sys_code=None):
        """Create a new CCR on the central server through WSCreate (SOAP verb 'CREATE').
        Codes = SiteCode@SysCode and Info = Title|Description|Type|TypeInfo are assembled inside the wrapper.
        SiteCode/SysCode default to the environment's %buildccr routine (Org/Sys).
        PERSISTENT operation: the golden rule limits it to BASE (where the change originates)."""
        title_esc = self._escape_cos(title)
        desc_esc = self._escape_cos(description)
        type_esc = self._escape_cos(ccr_type)
        info_esc = self._escape_cos(ccr_info)
        site_line = f'Set site = "{self._escape_cos(site_code)}"' if site_code else "Set site = $$Org^%buildccr"
        sys_line = f'Set sys = "{self._escape_cos(sys_code)}"' if sys_code else "Set sys = $$Sys^%buildccr"
        code = f"""
        {site_line}
        {sys_line}
        Set ccrid = ""
        Set sc = ##class(%Studio.SourceControl.ItemSet).WSCreate(site, sys, "{title_esc}", "{desc_esc}", .ccrid, "{type_esc}", "{info_esc}")
        If $$$ISERR(sc) {{
            Write "ERROR: ", $System.Status.GetErrorText(sc), !
        }} Else {{
            Write "SUCCESS|ccr_id=", ccrid, "|site=", site, "|sys=", sys, !
        }}
        """
        return self._execute_same_process_ccr_operation(code, None).strip()

    def ccr_list_itemsets(self, ccr_id, env=None, token=""):
        """List the ItemSets available on the central CCR server for this environment (verb 'List').
        Read-only: mutates nothing. env defaults to $$Env^%buildccr.
        REQUIRES a CCR access token (without one WSList returns #5001 unless the instance has API keys)."""
        ccr_esc = self._escape_cos(ccr_id)
        env_line = f'Set env = "{self._escape_cos(env)}"' if env else "Set env = $$Env^%buildccr"
        token_esc = self._escape_cos(token)
        code = f"""
        {env_line}
        Set ccr = "{ccr_esc}"
        Set token = "{token_esc}"
        Set rs = ""
        Set listException = ""
        Try {{
            Set sc = ##class(%Studio.SourceControl.ItemSet).WSList(ccr, env, token, .rs)
        }} Catch ex {{
            Set listException = ex.DisplayString()
        }}
        If listException'="" {{
            Set errorText = listException
            If token'="" {{
                Set errorText = $Replace(errorText, token, "<redacted>")
            }}
            Write "ERROR: ", errorText, !
        }} ElseIf $$$ISERR(sc) {{
            Set errorText = $System.Status.GetErrorText(sc)
            If token'="" {{
                Set errorText = $Replace(errorText, token, "<redacted>")
            }}
            Write "ERROR: ", errorText, !
        }} ElseIf '$IsObject(rs) {{
            Write "ERROR: WSList no devolvio un DataSet", !
        }} Else {{
            Write "OK|ccr_enc=", $ZConvert(ccr, "O", "URL"), "|env_enc=", $ZConvert(env, "O", "URL"), !
            While rs.Next() {{
                Write "ITEM|id=", rs.Get("ID"), "|name_enc=", $ZConvert(rs.Get("Name"), "O", "URL"), "|window_enc=", $ZConvert(rs.Get("WindowRequiredForMove"), "O", "URL"), "|plan_enc=", $ZConvert(rs.Get("ImplementationPlan"), "O", "URL"), !
            }}
        }}
        """
        try:
            output = self.execute_runtime_code(code)
        except Exception as exc:
            raise RuntimeError(self._redact_secret(exc, token)) from None
        return self._redact_secret(output, token).strip()

    def ccr_can_upload(self, ccr_id, org=None, system=None):
        """Pre-check of the upload gate on the central server (verb 'CanUpload'). Read-only.
        Returns whether the CCR accepts an upload, and why. Org/System default to %buildccr."""
        ccr_esc = self._escape_cos(ccr_id)
        org_line = f'Set org = "{self._escape_cos(org)}"' if org else "Set org = $$Org^%buildccr"
        sys_line = f'Set system = "{self._escape_cos(system)}"' if system else "Set system = $$Sys^%buildccr"
        code = f"""
        {org_line}
        {sys_line}
        Set reason = ""
        Set can = ##class(%Studio.SourceControl.ItemSet).WSCanUpload("{ccr_esc}", org, system, .reason)
        Write "CANUPLOAD|value=", +can, "|reason=", reason, !
        """
        return self.execute_runtime_code(code).strip()

    def ccr_download_load(self, itemset_id, load=True, token="", ccr_id=""):
        """CCR PROMOTION: download an APPROVED ItemSet from the central server by its
        ServerItemSetID and, when load=1, load it (LoadToOS + LoadToNS) and confirm the load
        to the central server (WSConfirmLoad). Mirrors the CCRItemSetTask.OnTask flow:
        WSDownload -> Import -> LoadToOS -> LoadToNS -> WSConfirmLoad. This is the
        sanctioned promotion channel: it only downloads by ISID from the central server and
        never loads an arbitrary local file. WSDownload/WSConfirmLoad need the CCR job
        (ccr_id, e.g. ACHS1046) and a CCR access token (ccr-portal ccr_upload_token).

        Its own gate: any configured environment role (BASE, UAT, TEST, LIVE) and the
        same-process CONTROLLED_ACTIVE guard embedded in the very runner that loads."""
        itemset_id = int(itemset_id)
        token_esc = self._escape_cos(token)
        # WSDownload pJob is the CCR / Perforce job (e.g. ACHS1046); Org^Sys makes the central
        # web service fail with <SUBSCRIPT> (subscript 2 is "").
        ccr_id = (ccr_id or "").strip()
        if ccr_id:
            ccr_id = self._require_ccr_id(ccr_id)
        ccr_esc = self._escape_cos(ccr_id)
        do_load = "1" if load else "0"
        code = f"""
        Set env = $$Env^%buildccr
        Set job = "{ccr_esc}"
        If job = "" Set job = $$Org^%buildccr _ "^" _ $$Sys^%buildccr
        Set isid = {itemset_id}
        Set isFile = ""
        Set sc = ##class(%Studio.SourceControl.ItemSet).WSDownload(job, env, "{token_esc}", "", .isid, .isFile)
        If $$$ISERR(sc) {{
            Write "ERROR_DOWNLOAD: ", $System.Status.GetErrorText(sc), !
            Quit
        }}
        Write "DOWNLOADED|isid=", isid, "|file=", isFile, !
        Do ##class(%Studio.SourceControl.ItemSetWS).%New().GetAPICredentials(.apikey)
        Set IS = ""
        Set sc = ##class(%Studio.SourceControl.ItemSet).Import(isFile, .IS, isid, $Get(apikey))
        If $$$ISERR(sc) {{
            Write "ERROR_IMPORT: ", $System.Status.GetErrorText(sc), !
            Quit
        }}
        If '$IsObject(IS) {{
            Write "ERROR_IMPORT: Import no devolvio objeto ItemSet", !
            Quit
        }}
        Write "IMPORTED|local_id=", IS.%Id(), "|ccr=", IS.CCR, "|source=", IS.Source, "|target=", IS.Target, !
        If {do_load} {{
            Set sc = IS.LoadToOS(,,.itemList,,,1)
            If $$$ISERR(sc) {{
                Write "ERROR_LOADTOOS: ", $System.Status.GetErrorText(sc), !
                Quit
            }}
            Write "LOADED_OS", !
            Set sc = IS.LoadToNS(,.itemList,0,2,.incPercent)
            If $$$ISERR(sc) {{
                Write "ERROR_LOADTONS: ", $System.Status.GetErrorText(sc), !
                Quit
            }}
            Write "LOADED_NS|itemset=", IS.%Id(), !
            // Report the load to the central CCR server (like CCRItemSetTask): without it the
            // portal keeps the client ItemSet undeployed and blocks markMoveTo<Env>Complete.
            Set confirmJob = $Select(IS.CCR '= "": IS.CCR, 1: job)
            Set confirmLog = IS.Log
            Set sc = ##class(%Studio.SourceControl.ItemSet).WSConfirmLoad(isid, confirmJob, env, "{token_esc}", $$$OK, .confirmLog)
            If $$$ISERR(sc) {{
                Write "ERROR_CONFIRM: ", $System.Status.GetErrorText(sc), !
                Quit
            }}
            Write "CONFIRMED|isid=", isid, !
        }}
        Write "SUCCESS", !
        """
        try:
            output = self._run_guarded_ccr_runner(code, f"ItemSet {itemset_id}", promotion=True)
        except Exception as exc:
            raise RuntimeError(self._redact_secret(exc, token)) from None
        return self._redact_secret(output, token).strip()

    def list_namespaces(self):
        """REST metadata only (READ gate). Unsupported/denied metadata never starts a runner."""
        self._require_read()
        try:
            req = urllib.request.Request(self.root_url, method='GET')
            req.add_header('Authorization', self.auth_header)
            req.add_header('Accept', 'application/json')
            with self.opener.open(req, timeout=15) as response:
                raw = response.read().decode('utf-8')
            data = json.loads(raw) if raw else {}
            if response.status != 200 or data.get("status", {}).get("errors"):
                raise ValueError("Metadata request failed")
            nss = data.get('result', {}).get('content', {}).get('namespaces')
            if isinstance(nss, list) and nss:
                return [validate_namespace(ns) for ns in nss]
        except Exception:
            pass
        raise LocalPolicyError("Namespace REST metadata unavailable or malformed; no executor fallback is permitted.")




class IRISMCPServer:
    # Gate categories by tool. Every tool except iris_list_servers (no client) must
    # belong to exactly one category; the hermetic suite asserts the partition.
    READ_TOOLS = frozenset({
        "iris_query_sql", "iris_get_class", "iris_get_document", "iris_list_classes", "iris_get_event_logs",
        "iris_describe_class", "iris_scan_namespace", "iris_trakcare_feature_check",
        "iris_get_recent_messages", "iris_get_ensemble_trace", "iris_dump_global",
        "iris_ccr_status", "iris_ccr_namespace_status", "iris_list_namespaces",
        "iris_ccr_list_uncommitted", "iris_ccr_list_itemsets", "iris_ccr_can_upload",
    })
    RUNTIME_TOOLS = frozenset({
        "iris_ensemble_production_control", "iris_ensemble_component_control",
        "iris_ensemble_default_settings", "iris_ensemble_queue_control",
        "iris_execute_objectscript",
    })
    CODE_WRITE_TOOLS = frozenset({
        "iris_save_class", "iris_save_class_from_file", "iris_patch_class",
        "iris_compile_class", "iris_delete_class", "iris_ccr_checkout",
        "iris_ccr_undocheckout", "iris_ccr_checkout_ct", "iris_unlock_questionnaire",
        "iris_ccr_commit", "iris_ccr_reupload_itemset", "iris_ccr_create",
        "iris_ccr_download_load", "iris_ccr_mutate_class_from_file",
        "iris_save_document", "iris_save_document_from_file", "iris_patch_document", "iris_delete_document",
    })
    # Ensemble tools expose read sub-actions; their mutating actions apply the
    # runtime gate per action inside the handler.
    _ENSEMBLE_TOOLS = frozenset({
        "iris_ensemble_production_control", "iris_ensemble_component_control",
        "iris_ensemble_default_settings", "iris_ensemble_queue_control",
    })
    # LIVE CONFIG WINDOW category: gated per action inside the handler ('get' ->
    # READ gate; 'set' -> LIVE CONFIG WINDOW gate, audited).
    LIVE_CONFIG_WINDOW_TOOLS = frozenset({"iris_ensemble_item_setting"})
    # PROJECT category: no IRIS client at all; reads/edits the project's
    # isc-ai.rules.json. Widening edits are plan-only (a human applies them
    # with 'isc-ai rules apply <fingerprint>').
    PROJECT_TOOLS = frozenset({"iris_rules"})
    # Runtime actions that, on a PRODUCTION target and only while a window is
    # configured, route through the LIVE CONFIG WINDOW gate instead of RUNTIME.
    _LIVE_WINDOW_RUNTIME_ACTIONS = {
        "iris_ensemble_production_control": frozenset({"status", "start", "stop", "restart"}),
        "iris_ensemble_component_control": frozenset({"enable", "disable", "restart"}),
    }

    def __init__(self, project_dir=None):
        """project_dir pins the project explicitly (CLI --project-dir); None resolves
        it from ISC_AI_PROJECT_DIR (legacy IRIS_MCP_PROJECT_DIR) or the nearest ancestor of cwd with .isc-ai/."""
        self.servers = {}
        self.policy = SitePolicy()
        self.runtime_config = None
        self.project_dir = Path(project_dir).resolve() if project_dir is not None else None
        self.load_configuration()

    def load_configuration(self):
        """Load project policy (<project>/.isc-ai/config.json) and merged servers.

        Called on construction and on every tools/call so CLI edits apply live.
        Failures never crash the server: on error the snapshot is cleared
        (fail closed) and the reason is logged without details.
        """
        try:
            runtime = iris_mcp_config.load_runtime_config(project_dir=self.project_dir)
        except ConfigError as exc:
            # Fail closed: a broken file must not leave a previous, possibly more
            # permissive snapshot in force. No servers, unconfigured policy.
            self.runtime_config = None
            self.policy = SitePolicy()
            self.servers = {}
            log_debug(f"Configuration load failed; servers cleared: {_cli_failure_message(exc, 200)}")
            return
        except Exception:
            self.runtime_config = None
            self.policy = SitePolicy()
            self.servers = {}
            log_debug("Configuration load failed; servers cleared; details withheld")
            return
        self.runtime_config = runtime
        self.policy = runtime.policy
        self.servers = runtime.servers
        for note in runtime.notes:
            log_debug(note)
        log_debug(f"Loaded {len(self.servers)} server configurations for site '{self.policy.site_name}'")

    def resolve_ccr_affected(self, server_id, namespace, client):
        """Compatibility name, now returns structured evidence, never a bool/cache."""
        if validate_namespace(namespace) != client.namespace:
            raise LocalPolicyError("Namespace does not match the selected client.")
        return client.get_namespace_ccr_status()

    def _resolve_server_config(self, server_id):
        """Resolve against the loaded snapshot, without I/O or alias overrides.

        canonical_server_id makes an entry a reference: ALL sibling settings are
        ignored. Only the final concrete entry supplies connection/policy defaults.
        """
        constrained = server_id in self.policy.alias_constraints
        prefix = f"Unresolved {server_id} target: " if constrained else "Unresolved server target: "
        current = server_id
        visited = set()
        while True:
            if not isinstance(current, str) or not current:
                raise LocalPolicyError(prefix + "Malformed alias target.")
            if current in visited:
                raise LocalPolicyError(prefix + "Alias cycle.")
            if len(visited) >= 16:
                raise LocalPolicyError(prefix + "Alias chain exceeds 16 entries.")
            visited.add(current)
            if current not in self.servers:
                raise LocalPolicyError(prefix + "Target is missing.")
            config = self.servers[current]
            if not isinstance(config, dict):
                raise LocalPolicyError(prefix + "Malformed target configuration.")
            if "_config_error" in config:
                raise LocalPolicyError(prefix + str(config["_config_error"]))
            if "canonical_server_id" in config:
                current = config["canonical_server_id"]
                continue
            break

        for alias in visited:
            constraint = self.policy.alias_constraints.get(alias)
            if constraint is None:
                continue
            required_host, required_prefix = constraint
            if config.get("host") != required_host or config.get("path_prefix", "") != required_prefix:
                raise LocalPolicyError(
                    prefix + f"The {alias} route requires the configured VIP; direct-node fallback is prohibited."
                )
        resolved = dict(config)
        resolved["namespace"] = validate_namespace(config.get("namespace", "USER"))
        return current, resolved

    @staticmethod
    def _validate_connection_config(config):
        """Shared local validation for catalog and client construction."""
        host = config.get("host")
        port = config.get("port", 80)
        prefix = config.get("path_prefix", "")
        if (not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.:\[\]-]+", host)
                or type(port) is not int or not 1 <= port <= 65535
                or not isinstance(prefix, str) or not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*", prefix)
                or not isinstance(config.get("username"), str) or not config["username"]
                or not isinstance(config.get("password"), str)
                or type(config.get("is_production", False)) is not bool
                or not isinstance(config.get("env_role", ""), str)
                or config.get("scheme", "http") not in ("http", "https")
                or type(config.get("verify_ssl", True)) is not bool
                or type(config.get("production_read", False)) is not bool):
            raise LocalPolicyError("Unresolved server target: malformed connection or policy settings.")

    def _server_catalog_record(self, server_id):
        """Display the same resolved target used by execution, without clients."""
        record = {"id": server_id, "canonical_server_id": None, "status": "UNRESOLVED"}
        try:
            current, config = self._resolve_server_config(server_id)
            self._validate_connection_config(config)
        except LocalPolicyError as exc:
            record["reason"] = str(exc)
            return record
        record.update(canonical_server_id=current, status="ALIAS" if current != server_id else "CONFIGURED",
                      host=config["host"], port=config.get("port", 80),
                      path_prefix=config.get("path_prefix", ""), namespace=config["namespace"],
                      reason="Configuration only; connectivity and activation not checked.")
        return record

    def _get_client_direct(self, server_id, namespace_override=None):
        # Historical method name retained; aliases cannot bypass resolution here.
        current, s = self._resolve_server_config(server_id)
        self._validate_connection_config(s)
        target_namespace = namespace_override if namespace_override is not None else s["namespace"]
        # production_read is the merged legacy-flag-or-rules-file "read" exception for
        # this resolved server id; effective_capability_exceptions already covers the
        # legacy per-server flag, so the raw config value is not read separately here.
        production_read = bool(self.policy.effective_capability_exceptions(
            "read", server=current, servers=self.servers))
        return AtelierClient(
            host=s["host"],
            port=s.get("port", 80),
            path_prefix=s.get("path_prefix", ""),
            namespace=target_namespace,
            username=s["username"],
            password=s["password"],
            is_production=s.get("is_production", False),
            env_role=s.get("env_role", ""),
            proxy=s.get("proxy"),
            policy=self.policy,
            scheme=s.get("scheme", "http"),
            verify_ssl=s.get("verify_ssl", True),
            production_read=production_read,
        )

    def get_client(self, server_id, namespace_override=None):
        # __init__ and tool dispatch load configuration. Do not reload between
        # default namespace selection and client construction within one call.
        if namespace_override is not None:
            validate_namespace(namespace_override)
        return self._get_client_direct(server_id, namespace_override)

    def _require_canonical_base_target(self, server_id, canonical_server_id, config, requested_namespace, failure):
        """Server-level canonical BASE gate shared by the initializer and controlled mutation."""
        base = self.policy.canonical_base
        if base is None:
            raise LocalPolicyError(f"{failure}: canonical_base is not configured for this project.")
        role = (config.get("env_role") or "").strip().upper()
        configured_namespace = config.get("namespace")
        if server_id != canonical_server_id or server_id != base.server_id:
            raise LocalPolicyError(f"{failure}: the configured canonical server ID is required, not an alias.")
        if requested_namespace != configured_namespace or configured_namespace != base.namespace:
            raise LocalPolicyError(f"{failure}: namespace must match the configured canonical namespace.")
        if (config.get("is_production", False) or role != base.role
                or any(marker in requested_namespace.upper() for marker in self.policy.production_markers)):
            raise LocalPolicyError(f"{failure}: the configured non-production canonical BASE is required.")
        if not _canonical_base_route_matches(
                self.policy, config.get("host"), config.get("port", 80), config.get("path_prefix", ""),
                configured_namespace, role, bool(config.get("is_production", False))):
            raise LocalPolicyError(f"{failure}: the exact canonical BASE route is required.")

    def initialize_ccr_mutation(self, server_id, namespace, ccr_id, upgrade=False,
                                expected_current_sha256=""):
        """Run the packaged conditional initializer against one canonical target."""
        requested_namespace = validate_namespace(namespace)
        canonical_server_id, config = self._resolve_server_config(server_id)
        self._validate_connection_config(config)
        self._require_canonical_base_target(server_id, canonical_server_id, config, requested_namespace,
                                            "CCR initializer blocked")
        source_bytes, candidate_sha256 = _load_ccr_mutation_resource(self.policy)
        client = self.get_client(server_id, namespace_override=requested_namespace)
        return client.initialize_ccr_mutation(
            source_bytes, ccr_id, candidate_sha256, upgrade, expected_current_sha256,
        )

    def get_server_namespaces(self, server_id):
        return self.get_client(server_id).list_namespaces()

    def check_namespace_ambiguity(self, server_id, ns_arg):
        _, s = self._resolve_server_config(server_id)
        # If the tool call explicitly passed a namespace, there's no ambiguity
        if ns_arg is not None:
            return None, validate_namespace(ns_arg)
            
        # If the server configuration has an explicit non-USER namespace, use it without prompting
        config_ns = s["namespace"]
        if config_ns != "USER":
            return None, config_ns
            
        # Otherwise, fetch all namespaces on the server
        namespaces = self.get_server_namespaces(server_id)
        if not namespaces:
            raise LocalPolicyError("Namespace metadata unavailable; specify a verified namespace explicitly.")
            
        # Filter working/custom namespaces
        system_namespaces = self.policy.system_namespaces
        working_namespaces = [ns for ns in namespaces if ns.upper() not in system_namespaces and not ns.startswith("^^")]
        
        # If there is only one working namespace, auto-route to it
        if len(working_namespaces) == 1:
            log_debug(f"Auto-routing to single working namespace: {working_namespaces[0]}")
            return None, working_namespaces[0]
            
        if len(working_namespaces) > 1:
            # We have ambiguity! Return the list of working namespaces
            return working_namespaces, None
            
        return None, "USER"

    # ------------------------------------------------------------------
    # LIVE CONFIG WINDOW handlers (routes.live_config_window)
    # ------------------------------------------------------------------

    def _live_window_audit(self, client, server_id, tool, action, result, reason, production=None,
                           item=None, target=None, setting=None, before=None, after=None,
                           ticket=None, extra=None, window_label=None):
        """Append one audit line; returns a warning string when it could not be written.

        Never stores connection credentials; values of sensitive-looking settings are
        recorded as SHA-256 digests only. Works identically whether the ensemble_control
        exception in force comes from routes.live_config_window or isc-ai.rules.json:
        the backup/audit root is a per-project constant (self.policy.live_config_window_backup_root),
        not a per-window one.
        """
        if not self.policy.capability_candidates("ensemble_control"):
            return None
        backup_root = self.policy.live_config_window_backup_root
        sensitive = isinstance(setting, str) and _LIVE_WINDOW_SENSITIVE_SETTING_RE.search(setting)

        def shown(value):
            if value is None or not sensitive:
                return value
            return "sha256:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()

        try:
            password = self._resolve_server_config(server_id)[1].get("password")
        except Exception:
            password = None
        record = {
            "ts": _audit_timestamp(), "server_id": server_id, "host": client.host,
            "namespace": client.namespace, "tool": tool, "action": action,
            "production": production, "item": item, "target": target, "setting": setting,
            "before": shown(before), "after": shown(after), "ticket": ticket,
            "window": window_label, "result": result, "reason": reason,
        }
        if extra:
            record.update(extra)
        if isinstance(password, str) and len(password) >= 4:
            record = {key: (AtelierClient._redact_secret(value, password) if isinstance(value, str) else value)
                      for key, value in record.items()}
        try:
            _append_live_window_audit(backup_root, record)
        except Exception:
            log_debug("LIVE config window audit line could not be written.")
            return "WARNING: the LIVE config window audit line could not be written; record this operation manually."
        return None

    @staticmethod
    def _live_window_text_value(value, name):
        if not isinstance(value, str):
            raise LocalPolicyError(f"'{name}' must be a string.")
        if len(value) > _LIVE_WINDOW_VALUE_MAX:
            raise LocalPolicyError(f"'{name}' exceeds {_LIVE_WINDOW_VALUE_MAX} characters.")
        if _XML_INVALID_CHARS_RE.search(value):
            raise LocalPolicyError(f"'{name}' contains characters that the production XData cannot hold.")
        return value

    @classmethod
    def _live_window_setting_spec(cls, arguments):
        """Validate the item/setting coordinates locally, before any gate or network."""
        production = arguments.get("production_name")
        if not isinstance(production, str) or production.endswith(".cls") or production.startswith("%"):
            raise LocalPolicyError("production_name must be a production class name without .cls.")
        AtelierClient._validate_identifier(production)
        item = arguments.get("item_name")
        if (not isinstance(item, str) or not item or len(item) > 256
                or re.search(r"[\x00-\x1f\x7f]", item)):
            raise LocalPolicyError("item_name must be a non-empty item name without control characters.")
        target = arguments.get("target")
        if target not in ("Host", "Adapter"):
            raise LocalPolicyError("target must be 'Host' or 'Adapter'.")
        setting = arguments.get("setting_name")
        if not isinstance(setting, str) or not _LIVE_WINDOW_SETTING_NAME_RE.fullmatch(setting):
            raise LocalPolicyError("setting_name must be a plain setting identifier.")
        return {"production": production, "item": item, "target": target, "setting": setting,
                "document": production + ".cls"}

    def _live_window_item_setting(self, client, server_id, arguments):
        tool = "iris_ensemble_item_setting"
        action = arguments.get("action")
        if action not in ("get", "set"):
            raise LocalPolicyError("action must be 'get' or 'set'.")
        spec = self._live_window_setting_spec(arguments)
        production, item, target, setting, document = (
            spec["production"], spec["item"], spec["target"], spec["setting"], spec["document"])

        if action == "get":
            allowed, reason = client.is_read_allowed()
            if not allowed:
                return {"isError": True, "content": [{"type": "text", "text": f"Operation denied: {reason}"}]}
            lines = client.get_document(document, strict=True)
            root = _production_definition_root(lines, production)
            present, value = _production_item_setting(root, item, target, setting)
            structured = {"production": production, "item": item, "target": target,
                          "setting": setting, "present": present, "value": value}
            text = (f"### Item setting on {server_id} ({client.namespace})\n\n"
                    f"- **Production**: `{production}`\n- **Item**: `{item}`\n"
                    f"- **Setting**: `{target}.{setting}`\n"
                    f"- **Present in XData**: {'yes' if present else 'no (class default applies)'}\n"
                    f"- **Value**: `{value}`\n")
            return {"structuredContent": structured, "content": [{"type": "text", "text": text}]}

        ticket = arguments.get("ticket")
        fields = {"production": production, "item": item, "target": target, "setting": setting,
                  "ticket": ticket}
        state = {"window_label": None}

        def finish(result, reason, is_error=True, before=None, after=None, extra=None, text=None):
            warning = self._live_window_audit(client, server_id, tool, "set", result, reason,
                                              window_label=state["window_label"], before=before,
                                              after=after, extra=extra, **fields)
            body = text or f"LIVE CONFIG WINDOW set {result}: {reason}"
            if warning:
                body += f"\n\n{warning}"
            structured = {"result": result, "reason": reason}
            structured.update(extra or {})
            return {"isError": is_error or bool(warning), "structuredContent": structured,
                    "content": [{"type": "text", "text": body}]}

        candidates = self.policy.capability_candidates("ensemble_control")
        if not candidates:
            return finish("DENIED", "No ensemble_control exception is configured (routes.live_config_window "
                                    "is not configured and isc-ai.rules.json has none either); 'set' is never "
                                    "available outside the LIVE CONFIG WINDOW.")
        value = arguments.get("value")
        expected = arguments.get("expected_current_value")
        try:
            if value is None:
                raise LocalPolicyError("'value' is required for set.")
            if expected is None:
                raise LocalPolicyError("'expected_current_value' is required for set (compare-and-set).")
            self._live_window_text_value(value, "value")
            self._live_window_text_value(expected, "expected_current_value")
            valid_tickets = {candidate.ticket for candidate in candidates}
            if not isinstance(ticket, str) or ticket not in valid_tickets:
                raise LocalPolicyError("'ticket' is required and must equal the window ticket.")
        except LocalPolicyError as exc:
            return finish("DENIED", str(exc))
        try:
            grant, gate_reason = client._issue_live_window_grant()
        except LocalPolicyError as exc:
            return finish("DENIED", str(exc))
        state["window_label"] = grant.label
        backup_root = self.policy.live_config_window_backup_root

        try:
            before_lines = client.get_document(document, strict=True)
            before_root = _production_definition_root(before_lines, production)
            present, current = _production_item_setting(before_root, item, target, setting)
        except LocalPolicyError as exc:
            return finish("FAILED", f"Production class could not be read or parsed: {exc} Nothing was written.")
        except Exception:
            return finish("FAILED", "Production class could not be read; server details withheld. Nothing was written.")
        if current != expected:
            return finish("DRIFT", "The class XData value differs from expected_current_value; nothing was written.",
                          before=current)
        try:
            if client._live_window_pending_change(document):
                return finish("DENIED", f"{document} has an uncommitted source-control change row; resolve it "
                                        "first. Nothing was written.", before=current)
        except LocalPolicyError as exc:
            return finish("FAILED", f"{exc} Nothing was written.", before=current)
        try:
            _live_window_audit_preflight(backup_root)
        except Exception:
            return finish("DENIED", "The LIVE config window audit file is not writable; nothing was written.",
                          before=current)
        try:
            backup_path, backup_sha = _live_window_write_backup(backup_root, client.namespace, production, before_lines)
        except Exception as exc:
            detail = str(exc) if isinstance(exc, LocalPolicyError) else "local I/O error"
            return finish("FAILED", f"Backup could not be written ({detail}); nothing was written.", before=current)
        extra = {"backup": str(backup_path), "backup_sha256": backup_sha}

        code = _live_window_item_setting_code(production, item, target, setting, value, expected)
        try:
            output = client._run_live_window_runner(grant, code)
            outcome = _parse_live_window_result(output)
        except _LiveWindowNotPrimary as exc:
            return finish("DENIED", str(exc), before=current, extra=extra)
        except LocalPolicyError as exc:
            return finish("UNKNOWN", f"{exc} Compare the class with the backup {backup_path}.",
                          before=current, extra=extra)
        extra.update(mirror_member=outcome["mirror_member"], mirror_primary=outcome["mirror_primary"],
                     production_running=outcome["production_running"])
        if outcome["status"] != "OK":
            result = "DRIFT" if outcome["status"] == "DRIFT" else "FAILED"
            return finish(result, f"Runner status {outcome['status']}: {outcome['message']}",
                          before=outcome["before"], after=outcome["after"], extra=extra)

        verified_value = None
        others_unchanged = False
        verify_error = ""
        try:
            after_lines = client.get_document(document, strict=True)
            after_root = _production_definition_root(after_lines, production)
            after_present, verified_value = _production_item_setting(after_root, item, target, setting)
            if not after_present:
                verified_value = None
            others_unchanged = (_production_canonical_without(before_root, item, target, setting)
                                == _production_canonical_without(after_root, item, target, setting))
        except Exception as exc:
            verify_error = str(exc) if isinstance(exc, LocalPolicyError) else "re-read failed; details withheld"
        try:
            residue = client._live_window_pending_change(document)
        except LocalPolicyError:
            residue = None
        value_ok = verified_value == value
        extra.update(verified_value_matches=value_ok, other_settings_unchanged=others_unchanged,
                     source_control_change_row={True: "PRESENT", False: "NONE", None: "UNKNOWN"}[residue])
        lines = [
            f"### LIVE CONFIG WINDOW item setting on {server_id} ({client.namespace})",
            "",
            f"- **Gate**: {gate_reason}",
            f"- **Production / item**: `{production}` / `{item}`",
            f"- **Setting**: `{target}.{setting}`",
            f"- **Before**: `{outcome['before']}` (present: {'yes' if outcome['before_present'] else 'no'})",
            f"- **After (runner)**: `{outcome['after']}`",
            f"- **Backup**: `{backup_path}` (sha256 {backup_sha})",
            f"- **Class XData re-read**: value {'MATCHES' if value_ok else 'DOES NOT MATCH'}; "
            f"rest of the production {'unchanged' if others_unchanged else 'CHANGED OR UNVERIFIED'}"
            + (f" ({verify_error})" if verify_error else ""),
            f"- **Source-control change row for `{document}`**: "
            + {True: "PRESENT (created or pre-existing; not undone automatically, review it)",
               False: "none", None: "UNKNOWN (check failed)"}[residue],
        ]
        if outcome["production_running"]:
            lines.append(f"- **Hint**: production is running; restart item `{item}` for the change to take effect "
                         "(not done automatically).")
        if value_ok and others_unchanged:
            return finish("OK", "Setting written and verified in the class XData.", is_error=False,
                          before=outcome["before"], after=verified_value, extra=extra,
                          text="\n".join(lines) + "\n")
        lines.insert(0, "## FAILED: post-write verification did not confirm the change. Restore from the "
                        "backup if needed.\n")
        return finish("FAILED", "Post-write verification failed: "
                      + ("value mismatch" if not value_ok else "other parts of the production changed or could not be compared"),
                      before=outcome["before"], after=verified_value, extra=extra, text="\n".join(lines) + "\n")

    def _live_window_ensemble_control(self, name, client, server_id, arguments):
        """Production/component runtime actions on a production target, window-gated."""
        action = arguments.get("action")
        prod_name = arguments.get("production_name")
        comp_name = arguments.get("component_name")
        fields = {"production": prod_name, "item": comp_name, "ticket": None}
        state = {"window_label": None}

        def finish(result, reason, text=None):
            warning = self._live_window_audit(client, server_id, name, action, result, reason,
                                              window_label=state["window_label"], **fields)
            body = text or f"LIVE CONFIG WINDOW {name} '{action}' {result}: {reason}"
            if warning:
                body += f"\n\n{warning}"
            return {"isError": result != "OK" or bool(warning),
                    "content": [{"type": "text", "text": body}]}

        if name == "iris_ensemble_component_control" and not comp_name:
            return finish("DENIED", f"'component_name' is required for action '{action}'.")
        try:
            timeout_val = int(arguments.get("timeout", 30))
        except (TypeError, ValueError):
            return finish("DENIED", "'timeout' must be an integer.")
        if not 0 <= timeout_val <= 3600:
            return finish("DENIED", "'timeout' must be between 0 and 3600 seconds.")
        force_val = 1 if arguments.get("force", False) is True else 0
        try:
            grant, gate_reason = client._issue_live_window_grant()
        except LocalPolicyError as exc:
            return finish("DENIED", str(exc))
        fields["ticket"] = grant.ticket
        state["window_label"] = grant.label
        backup_root = self.policy.live_config_window_backup_root
        try:
            _live_window_audit_preflight(backup_root)
        except Exception:
            return finish("DENIED", "The LIVE config window audit file is not writable; nothing was executed.")

        def run(code):
            return client._run_live_window_runner(grant, code).strip()

        where = f"{server_id} ({client.namespace})"
        try:
            if name == "iris_ensemble_production_control":
                if action == "status":
                    raw = run('Set sc = ##class(Ens.Director).GetProductionStatus(.pName, .pState) Write pName, "|", pState, !')
                    parts = raw.split("|")
                    state_num = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else -1
                    state_map = {0: "Stopped", 1: "Running", 2: "Suspended", 3: "Troubled (errors/partial)"}
                    text = (f"### Ensemble production status on {where}\n\n"
                            f"- **Gate**: {gate_reason}\n"
                            f"- **Active production**: `{(parts[0] if parts else '') or 'none'}`\n"
                            f"- **State**: **{state_map.get(state_num, f'Unknown ({state_num})')}**\n")
                    return finish("OK", "status read", text=text)
                if action == "start":
                    if not prod_name:
                        prod_name = run('Set sc = ##class(Ens.Director).GetProductionStatus(.pName, .pState) Write pName, !')
                        fields["production"] = prod_name
                        if not prod_name:
                            return finish("FAILED", "No 'production_name' was given and no production was previously recorded.")
                    code = (f'Set sc = ##class(Ens.Director).StartProduction({_cos_string_expr(prod_name)}) '
                            'If \'$$$ISOK(sc) { Write $System.Status.GetOneErrorText(sc), ! }')
                elif action == "stop":
                    code = (f'Set sc = ##class(Ens.Director).StopProduction({timeout_val}, {force_val}) '
                            'If \'$$$ISOK(sc) { Write $System.Status.GetOneErrorText(sc), ! }')
                else:
                    code = (f'Set sc = ##class(Ens.Director).RestartProduction({timeout_val}, {force_val}) '
                            'If \'$$$ISOK(sc) { Write $System.Status.GetOneErrorText(sc), ! }')
            else:
                item = _cos_string_expr(comp_name)
                if action == "enable":
                    code = f'Set sc = ##class(Ens.Director).EnableConfigItem({item}, 1) If \'$$$ISOK(sc) {{ Write $System.Status.GetOneErrorText(sc), ! }}'
                elif action == "disable":
                    code = f'Set sc = ##class(Ens.Director).EnableConfigItem({item}, 0) If \'$$$ISOK(sc) {{ Write $System.Status.GetOneErrorText(sc), ! }}'
                else:
                    code = (f'Set sc = ##class(Ens.Director).EnableConfigItem({item}, 0) If \'$$$ISOK(sc) {{ Write "disable: ", $System.Status.GetOneErrorText(sc), ! }} '
                            f'Hang 0.5 Set sc2 = ##class(Ens.Director).EnableConfigItem({item}, 1) If \'$$$ISOK(sc2) {{ Write "enable: ", $System.Status.GetOneErrorText(sc2), ! }}')
            error_text = run(code)
        except _LiveWindowNotPrimary as exc:
            return finish("DENIED", str(exc))
        except LocalPolicyError as exc:
            return finish("FAILED", str(exc))
        if error_text:
            return finish("FAILED", f"IRIS reported: {AtelierClient._bounded_runner_text(error_text)}")
        subject = f"component '{comp_name}'" if comp_name else f"production '{prod_name or ''}'".strip()
        return finish("OK", f"{action} completed", text=(
            f"LIVE CONFIG WINDOW: {action} of {subject} completed on {where}.\n\n- **Gate**: {gate_reason}\n"))


    def handle_tools_list(self):
        tools = [
            {
                "name": "iris_list_servers",
                "description": "Lists the available InterSystems IRIS server connections configured on the system.",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "iris_rules",
                "description": "Reads and edits the project's isc-ai.rules.json (package grants and expiring gate exceptions). No IRIS connection. 'list', 'show', 'check' and 'audit' only read. NARROWING edits apply directly with a backup and an audit line: 'remove', and 'renew' to an EARLIER or equal date. WIDENING edits never write: 'add' and 'renew' to a later date only save a plan and return a fingerprint that a HUMAN applies with `isc-ai rules apply <fingerprint>`; this tool cannot apply plans.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "show", "check", "audit", "remove", "renew", "add"]},
                        "id": {"type": "string", "description": "Exception id (show, remove, renew)."},
                        "expires": {"type": "string", "description": "New expiry YYYY-MM-DD (renew)."},
                        "exception": {"type": "object", "description": "Full exception object (add): id, label, ticket, expires, capability and optional scope."},
                        "capability": {"type": "string", "description": "check: code_write, ensemble_control, runner or read."},
                        "namespace": {"type": "string"},
                        "host": {"type": "string"},
                        "role": {"type": "string"},
                        "server": {"type": "string", "description": "Configured server id (check)."},
                        "path_prefix": {"type": "string"},
                    },
                    "required": ["action"],
                }
            },
            {
                "name": "iris_query_sql",
                "description": "Runs a read-only SQL query on an InterSystems IRIS instance and returns a structured result listing. READ gate (blocks production unless the entry sets production_read) plus a conservative SQL policy: only statements that start with SELECT, with no CALL/INSERT/UPDATE/DELETE/TRUNCATE/DROP/ALTER/CREATE/GRANT/REVOKE/BUILD/TUNE tokens and no %SYSTEM./$SYSTEM. references outside single-quoted string literals; unterminated literals and anything else are denied.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID (e.g. 'my-dev-server')."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "query": {
                            "type": "string",
                            "description": "SQL query to run (e.g. 'SELECT Name FROM %Dictionary.ClassDefinition WHERE Name LIKE \\'Custom.%\\'')."
                        },
                        "parameters": {
                            "type": "array",
                            "description": "Optional parameters for the parameterized query.",
                            "items": {"type": "string"}
                        },
                        "timeout": {
                            "type": "number",
                            "description": "Optional timeout in seconds for query execution."
                        }
                    },
                    "required": ["server_id", "query"]
                }
            },
            {
                "name": "iris_get_class",
                "description": "Retrieves the complete native ObjectScript source of a .cls class on the server.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Class name with an optional exact '.cls' suffix; preserves case and components (e.g. '%SYSTEM.OBJ' -> '%SYSTEM.OBJ.cls'). Does not interpret routine suffixes."
                        }
                    },
                    "required": ["server_id", "class_name"]
                }
            },
            {
                "name": "iris_save_class",
                "description": "Saves/uploads the source of a class to the InterSystems server (equivalent to the CSP PUT) with support for CCR auto-checkout and ASCII normalization of special characters.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Full class name (e.g. 'Custom.MyPackage.MyClass')."
                        },
                        "content": {
                            "type": "string",
                            "description": "Complete ObjectScript source of the class to upload."
                        },
                        "auto_checkout": {
                            "type": "boolean",
                            "description": "If true (the default), automatically performs a CCR checkout for the class on the server before saving it.",
                            "default": True
                        },
                        "ccr_id": {
                            "type": "string",
                            "description": "Optional CCR ID (e.g. 'SITE0757'). If provided together with auto_checkout=true, the checkout is made under this CCR (runner with the same-process CONTROLLED_ACTIVE guard) and the save is tracked. Without ccr_id there is no checkout: the save is admissible only in a namespace with CCR ABSENT and a cataloged package.",
                            "default": None
                        },
                        "clean_ascii": {
                            "type": "boolean",
                            "description": "If true, normalizes special characters (accents, enyes, em dashes) to pure ASCII before sending. WARNING: this is a LOSSY transformation that rewrites the source (Spanish comments and literals lose their accents). That is why it is DISABLED by default; enable it only if the Perforce/CCR SAX/XML validator fails on invalid characters.",
                            "default": False
                        },
                        "verify": {
                            "type": "boolean",
                            "description": "If true (the default), after the PUT it rereads the class from the server and verifies that it matches what was sent (tolerating trailing whitespace). If it does not match, it aborts WITHOUT compiling. Detects duplication/truncation of the inline content. Set to false only if there are false positives caused by server normalization.",
                            "default": True
                        },
                        "compile": {
                            "type": "boolean",
                            "description": "If true, automatically compiles the class on the server immediately after saving it.",
                            "default": False
                        }
                    },
                    "required": ["server_id", "class_name", "content"]
                }
            },
            {
                "name": "iris_patch_class",
                "description": "Applies a surgical patch to an existing class on the server: downloads the source, replaces old_string with new_string EXACTLY (a single occurrence by default) and saves with CCR auto-checkout and optional compilation. Use it instead of iris_save_class when you are only changing a few lines: it avoids uploading the whole file inline and removes the risk of double encoding because the fetch/replace/put happen server-side over the real UTF-8. If old_string does not appear the expected number of times, it aborts WITHOUT writing anything.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Full class name (e.g. 'Custom.MyPackage.MyClass')."
                        },
                        "old_string": {
                            "type": "string",
                            "description": "Exact literal text to replace. It must appear exactly expected_count times in the current server source (include enough context to make it unique)."
                        },
                        "new_string": {
                            "type": "string",
                            "description": "Text that replaces old_string."
                        },
                        "expected_count": {
                            "type": "integer",
                            "description": "Expected number of occurrences of old_string. Defaults to 1. The operation fails without saving if the real count does not match.",
                            "default": 1
                        },
                        "auto_checkout": {
                            "type": "boolean",
                            "description": "If true (the default), automatically performs a CCR checkout for the class before saving it.",
                            "default": True
                        },
                        "ccr_id": {
                            "type": "string",
                            "description": "Optional CCR ID (e.g. 'SITE0757'). If provided together with auto_checkout=true, the checkout is made under this CCR (runner with the same-process CONTROLLED_ACTIVE guard) and the save is tracked. Without ccr_id there is no checkout: the save is admissible only in a namespace with CCR ABSENT and a cataloged package.",
                            "default": None
                        },
                        "clean_ascii": {
                            "type": "boolean",
                            "description": "If true, normalizes special characters (accents, em dashes) to pure ASCII before sending. WARNING: lossy transformation that rewrites the WHOLE file (not only the patched lines). Disabled by default.",
                            "default": False
                        },
                        "verify": {
                            "type": "boolean",
                            "description": "If true (the default), after the PUT it rereads the class from the server and verifies that the patch result matches (tolerating trailing whitespace). If it does not match, it aborts WITHOUT compiling. Set to false only if there are false positives caused by server normalization.",
                            "default": True
                        },
                        "compile": {
                            "type": "boolean",
                            "description": "If true, compiles the class immediately after saving it.",
                            "default": False
                        }
                    },
                    "required": ["server_id", "class_name", "old_string", "new_string"]
                }
            },
            {
                "name": "iris_compile_class",
                "description": "Compiles a class or document on the InterSystems IRIS server and returns the compiler status and errors.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Class or document. Only the exact lowercase suffixes .cls/.mac/.int/.inc/.bas/.obj indicate a document; otherwise .cls is appended without changing case. For the class Custom.obj use Custom.obj.cls."
                        }
                    },
                    "required": ["server_id", "class_name"]
                }
            },
            {
                "name": "iris_delete_class",
                "description": "Permanently deletes a class from the namespace. Takes a class name with an optional exact .cls suffix; it does not interpret components as routine suffixes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Full name of the class to delete (e.g. 'Custom.Temp.MyClass.cls')."
                        }
                    },
                    "required": ["server_id", "class_name"]
                }
            },
            {
                "name": "iris_list_classes",
                "description": "Quickly searches and lists custom classes in the server dictionary, filtering by package (e.g. 'Custom'). Avoids overloading the server with heavy listings.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "package_prefix": {
                            "type": "string",
                            "description": "Package prefix to search for (e.g. 'Custom.MyPackage', 'Logistica'). By default it searches for classes starting with 'Custom'."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_get_event_logs",
                "description": "Retrieves the latest event records from the Ensemble/IRIS integration system log (Ens_Util.Log), ideal for diagnosing execution errors.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of records to return (default 20, max 100)."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_get_recent_messages",
                "description": "Retrieves the latest message transactions from the Ensemble/IRIS integration bus (Ens.MessageHeader) to monitor traffic and resolve blockages.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of messages to return (default 20)."
                        },
                        "only_errors": {
                            "type": "boolean",
                            "description": "If true, returns only messages with an error or in a suspended state."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_get_ensemble_trace",
                "description": "Gets the complete trace (Visual Trace) of an Ensemble/IRIS Interoperability execution by its SessionId. Retrieves, in chronological order, the message sequence (Ens.MessageHeader), the flow between components (Services, Processes, Operations), the associated logs (Ens_Util.Log) and optionally the content of the message bodies.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "session_id": {
                            "type": ["integer", "string"],
                            "description": "SessionId identifier of the Ensemble trace to inspect (e.g. 14789 or '14789')."
                        },
                        "include_logs": {
                            "type": "boolean",
                            "description": "If true (default true), includes the Ens_Util.Log records associated with the session."
                        },
                        "include_bodies": {
                            "type": "boolean",
                            "description": "If true (default false), inspects and retrieves the content/payload of the MessageBody objects in the trace."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace."
                        }
                    },
                    "required": ["server_id", "session_id"]
                }
            },
            {
                "name": "iris_ensemble_production_control",
                "description": "Queries the status of, or controls, the Ensemble production (status, start, stop, restart). Non-production targets: RUNTIME gate (ephemeral runner). Production targets: blocked, except while the project configures routes.live_config_window, in which case every action (status included) passes only the LIVE CONFIG WINDOW gate (window not expired, namespace and host listed, production_read, fresh Interoperability jobs in the window evidence namespace on this node, mirror-primary check inside the runner process) and is appended to <project>/.backup/live-config-window/audit.jsonl.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "action": {
                            "type": "string",
                            "enum": ["status", "start", "stop", "restart"],
                            "description": "Action to perform: 'status' (query state), 'start' (start), 'stop' (stop), 'restart' (restart)."
                        },
                        "production_name": {
                            "type": "string",
                            "description": "Name of the Ensemble production (optional; if omitted, auto-detects the active production)."
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in seconds for stopping or restarting (default 30)."
                        },
                        "force": {
                            "type": "boolean",
                            "description": "If true (default false), force immediate stop/restart."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace."
                        }
                    },
                    "required": ["server_id", "action"]
                }
            },
            {
                "name": "iris_ensemble_component_control",
                "description": "Manages individual production components (Business Services, Processes, Operations): list, enable, disable or hot-restart. 'list' uses the READ gate. enable/disable/restart: RUNTIME gate on non-production targets; on production targets blocked, except while routes.live_config_window is configured, in which case they pass only the LIVE CONFIG WINDOW gate (window not expired, namespace and host listed, production_read, fresh Interoperability jobs in the window evidence namespace on this node, mirror-primary check inside the runner process) and are audited to <project>/.backup/live-config-window/audit.jsonl.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "action": {
                            "type": "string",
                            "enum": ["list", "enable", "disable", "restart"],
                            "description": "Action to perform: 'list' (list components and their states), 'enable' (enable), 'disable' (disable), 'restart' (restart component)."
                        },
                        "component_name": {
                            "type": "string",
                            "description": "Component name (Business Service/Process/Operation). Required for enable/disable/restart."
                        },
                        "production_name": {
                            "type": "string",
                            "description": "Production name to filter on in 'list' (optional)."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace."
                        }
                    },
                    "required": ["server_id", "action"]
                }
            },
            {
                "name": "iris_ensemble_default_settings",
                "description": "Queries, creates, updates or deletes Ensemble system default settings (Ens.Config.DefaultSettings).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "action": {
                            "type": "string",
                            "enum": ["list", "set", "delete"],
                            "description": "Action to perform: 'list' (query settings), 'set' (create or update a setting), 'delete' (delete a setting)."
                        },
                        "id": {
                            "type": ["integer", "string"],
                            "description": "ID of the setting record (required for 'delete')."
                        },
                        "production_name": {
                            "type": "string",
                            "description": "Production name (or '*' for all)."
                        },
                        "item_name": {
                            "type": "string",
                            "description": "Component/item name (or '*' for all)."
                        },
                        "host_class_name": {
                            "type": "string",
                            "description": "Host class name (or '*' for all)."
                        },
                        "setting_name": {
                            "type": "string",
                            "description": "Name of the parameter/setting (e.g. TargetConfigNames, HTTPPort, SSLConfig)."
                        },
                        "setting_value": {
                            "type": "string",
                            "description": "Value of the parameter/setting (required for 'set')."
                        },
                        "deployable": {
                            "type": "boolean",
                            "description": "Whether the setting is deployable (default true)."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace."
                        }
                    },
                    "required": ["server_id", "action"]
                }
            },
            {
                "name": "iris_ensemble_queue_control",
                "description": "Monitors Ensemble message queues, lists pending messages, resends suspended messages or aborts/dequeues stuck messages.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "action": {
                            "type": "string",
                            "enum": ["list_queues", "list_messages", "resend_message", "abort_message"],
                            "description": "Action to perform: 'list_queues' (list active queues and message count), 'list_messages' (list messages in a queue), 'resend_message' (resend/retry a message), 'abort_message' (abort/dequeue a stuck message)."
                        },
                        "queue_name": {
                            "type": "string",
                            "description": "Queue name (required for 'list_messages')."
                        },
                        "message_id": {
                            "type": ["integer", "string"],
                            "description": "Message ID (MessageHeader ID) required for 'resend_message' and 'abort_message'."
                        },
                        "new_target": {
                            "type": "string",
                            "description": "Optional new target component when resending a message ('resend_message'). E.g. 'HL7 Rule' to reroute a message that went to another target. If omitted, it is resent to the original target."
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Limit of messages to list in 'list_messages' (default 20)."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace."
                        }
                    },
                    "required": ["server_id", "action"]
                }
            },
            {
                "name": "iris_ensemble_item_setting",
                "description": "Reads or compare-and-sets ONE production Item setting stored in the production class XData (e.g. WebServiceURL, StorageNamespace, AllowedIPAddresses). 'get': READ gate only (production_read on production targets); parses the class XData fetched over REST, no runner. 'set': LIVE CONFIG WINDOW gate only (routes.live_config_window): production-flagged target, window not expired (local MCP host date), namespace and host listed, host is a configured direct HA node, production_read, and fresh Interoperability jobs in the window evidence namespace on this node; 'set' is never allowed on non-production targets. 'set' requires expected_current_value (compare-and-set: drift aborts without writing) and the window ticket; it stores an immutable timestamped backup of the class under <project>/.backup/live-config-window/<namespace>/, blocks if the class has a pending source-control change, then runs a FIXED runner (no caller ObjectScript; values travel base64-encoded) that checks the mirror primary in-process, updates Ens.Config.Production, %Save() and SaveToClass(). It re-reads the class to verify the new value and that nothing else changed, reports any source-control change row created for the class, and never applies or restarts the production (restart the item if it is running). Every 'set' (allowed or denied) is appended to <project>/.backup/live-config-window/audit.jsonl.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Target namespace (must be listed in the window for 'set')."
                        },
                        "action": {
                            "type": "string",
                            "enum": ["get", "set"],
                            "description": "'get' (read the setting) or 'set' (compare-and-set through the LIVE CONFIG WINDOW gate)."
                        },
                        "production_name": {
                            "type": "string",
                            "description": "Production class name (e.g. 'HMF.Router.Production')."
                        },
                        "item_name": {
                            "type": "string",
                            "description": "Exact Item Name (config name) inside the production."
                        },
                        "target": {
                            "type": "string",
                            "enum": ["Host", "Adapter"],
                            "description": "Setting target: 'Host' or 'Adapter'."
                        },
                        "setting_name": {
                            "type": "string",
                            "description": "Setting name (e.g. 'WebServiceURL')."
                        },
                        "value": {
                            "type": "string",
                            "description": "New value (required for 'set')."
                        },
                        "expected_current_value": {
                            "type": "string",
                            "description": "Required for 'set': the value you expect now. An absent setting counts as ''. If the current value differs, nothing is written."
                        },
                        "ticket": {
                            "type": "string",
                            "description": "Required for 'set': must equal the ticket of routes.live_config_window."
                        }
                    },
                    "required": ["server_id", "action", "production_name", "item_name", "target", "setting_name"]
                }
            },
            {
                "name": "iris_execute_objectscript",
                "description": "Runs ObjectScript through a dedicated temporary runner and returns Write/output. The payload may persist data. RUNTIME gate: only the production gate (is_production, PROD/LIVE/PRD namespace/role) and a configured non-production role; CCR state is not consulted and neither authorizes nor denies this operation because it is not a tracked code change. LIVE/PROD always blocks. Direct HA nodes are not blocked by this gate; on a mirror backup the runner upload fails because the database is read-only. If the project policy runtime.runner_hosts maps the target namespace (for the configured role and, when the entry lists hosts, the target host) to a runner namespace, the runner is created there on the same instance and the payload first runs New $NAMESPACE and Set $NAMESPACE to the target; both namespaces must pass the gate. Requires verified deletion of the runner and the absence of source-control residue. Every call is appended under a cross-process file lock to the local audit file ~/.isc-ai/audit/runtime.jsonl (ISC_AI_AUDIT_DIR overrides the directory) with target, runner namespace, outcome and the payload code verbatim: it never stores connection credentials or output, but secrets embedded in the code are recorded. If the start record cannot be written the code is not executed. The LIVE CONFIG WINDOW (routes.live_config_window) never opens this tool: it stays blocked on production targets.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "code": {
                            "type": "string",
                            "description": "ObjectScript code to run (e.g. 'Write \"Hello World\", !')."
                        }
                    },
                    "required": ["server_id", "code"]
                }
            },
            {
                "name": "iris_dump_global",
                "description": "Reads or dumps the content of a global (or a subtree of a global) in InterSystems IRIS, with support for pagination, node limits and automatic formatting of $ListBuild structures and scalar values.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "global_name": {
                            "type": "string",
                            "description": "Name of the global to read (e.g. '^PAADM', 'PAADM', '^websys.MenuD', '^||tmp', '^CT(\"SEX\")'). It may include subscripts directly (e.g. '^PAADM(100)')."
                        },
                        "subscript": {
                            "type": "string",
                            "description": "Optional starting subscript or node if it was not included in global_name (e.g. '100', '\"57400\"', '10,1')."
                        },
                        "start_from": {
                            "type": "string",
                            "description": "Node reference from which to start or continue the iteration for pagination (e.g. '^PAADM(100,5)')."
                        },
                        "max_nodes": {
                            "type": "number",
                            "description": "Maximum number of nodes to return (default 200, maximum 5000)."
                        }
                    },
                    "required": ["server_id", "global_name"]
                }
            },
            {
                "name": "iris_ccr_status",
                "description": "Gets the change-control (CCR/Perforce) status of a class on the server (e.g. whether it is in Checkout, Editable, or who is holding the lock).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace (e.g. 'BASE-SITE')."
                        },
                        "document_name": {
                            "type": "string",
                            "description": "Alternative to class_name: exact document WITH extension (e.g. 'Custom.Site.Macros.inc', 'Custom.Site.Util.mac'). Exactly one of class_name or document_name."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Full class name with or without extension (e.g. 'Custom.Site.Integration.REST.BO.PAPerson')."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_ccr_checkout",
                "description": "Performs a checkout (lock/enable editing) of a class under a CCR ID or description on the server with active change control.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace."
                        },
                        "document_name": {
                            "type": "string",
                            "description": "Alternative to class_name: exact document WITH extension (e.g. 'Custom.Site.Macros.inc', 'Custom.Site.Util.mac'). Exactly one of class_name or document_name."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Full class name with or without extension."
                        },
                        "ccr_id": {
                            "type": "string",
                            "description": "CCR identifier or description (e.g. 'CCR-12345')."
                        }
                    },
                    "required": ["server_id", "ccr_id"]
                }
            },
            {
                "name": "iris_ccr_undocheckout",
                "description": "Undoes a checkout of a class on the server, releasing the lock and reverting any uncommitted change. CCR-tracked operation: it requires an explicit ccr_id and reverts only the pending row that belongs to exactly that CCR; it runs in a runner with the same-process CONTROLLED_ACTIVE guard.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace."
                        },
                        "document_name": {
                            "type": "string",
                            "description": "Alternative to class_name: exact document WITH extension (e.g. 'Custom.Site.Macros.inc', 'Custom.Site.Util.mac'). Exactly one of class_name or document_name."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Full class name with or without extension."
                        },
                        "ccr_id": {
                            "type": "string",
                            "description": "CCR the pending row belongs to (e.g. 'SITE0757'). If omitted, it is read from the server config or IRIS_CCR_ID."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_ccr_namespace_status",
                "description": "Partial read-only CCR discovery per namespace (INFORMATIONAL): installed engine, generic source control and structured evidence. UNKNOWN here is only an external discovery result; it is NOT an authorization verdict. Authorization of code writes uses the same-process guard (CONTROLLED_ACTIVE / SUSPENDED / ABSENT) inside the runner or probe. It does not create classes or change configuration.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace to evaluate (e.g. 'BASE-SITE'). If not specified, uses the one configured for the server."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_ccr_create",
                "description": "Creates a new CCR on the central CCR server (SOAP verb 'CREATE' via %Studio.SourceControl.ItemSet.WSCreate). Records title, description and type to keep the documentation tidy in the CCR layer. PERSISTENT operation: the golden rule restricts it to BASE environments (where the change originates). SiteCode/SysCode default to the environment's %buildccr routine (Org/Sys).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace (e.g. 'BASE-SITE')."
                        },
                        "title": {
                            "type": "string",
                            "description": "CCR title."
                        },
                        "description": {
                            "type": "string",
                            "description": "CCR description (context of the change)."
                        },
                        "ccr_type": {
                            "type": "string",
                            "description": "Optional CCR type (according to the central server catalog)."
                        },
                        "ccr_info": {
                            "type": "string",
                            "description": "Optional additional type information (TypeInfo)."
                        },
                        "site_code": {
                            "type": "string",
                            "description": "Optional SiteCode. Defaults to the Org from %buildccr (e.g. 'SITE')."
                        },
                        "sys_code": {
                            "type": "string",
                            "description": "Optional SysCode. Defaults to the Sys from %buildccr (e.g. 'T2026')."
                        }
                    },
                    "required": ["server_id", "title", "description"]
                }
            },
            {
                "name": "iris_ccr_list_itemsets",
                "description": "Queries the central CCR ItemSets via WSList: id, name, window and implementation plan. READ gate (blocks production); the transport creates a temporary class under the RUNTIME gate (configured non-production role), without consulting CCR state. Requires a CCR access token unless API keys are configured.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace (e.g. 'BASE-SITE')."
                        },
                        "ccr_id": {
                            "type": "string",
                            "description": "Identifier of the CCR / Job to query (e.g. 'SITE0875'). If omitted, it is read from the server config or IRIS_CCR_ID."
                        },
                        "env": {
                            "type": "string",
                            "description": "Optional CCR environment to query (e.g. 'BASE', 'UAT'). Defaults to the namespace's Env from %buildccr."
                        },
                        "token": {
                            "type": "string",
                            "description": "CCR access token (obtained from CCR code). If omitted, it is read from the server config ('upload_token') or from IRIS_CCR_TOKEN."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_ccr_can_upload",
                "description": "Queries CanUpload on the central CCR before the commit; returns the verdict and the reason. READ gate (blocks production); the transport creates a temporary class under the RUNTIME gate (configured non-production role), without consulting CCR state.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace (e.g. 'BASE-SITE')."
                        },
                        "ccr_id": {
                            "type": "string",
                            "description": "Identifier of the CCR / Job to validate (e.g. 'SITE0530'). If omitted, it is read from the server config or IRIS_CCR_ID."
                        },
                        "org": {
                            "type": "string",
                            "description": "Optional Org. Defaults to the Org from %buildccr."
                        },
                        "system": {
                            "type": "string",
                            "description": "Optional System. Defaults to the Sys from %buildccr."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_ccr_download_load",
                "description": "CCR PROMOTION (its own gate): downloads an approved ItemSet from the central server by ServerItemSetID and, if load=true, loads it (LoadToOS + LoadToNS). verify_documents requires documents that already exist in UAT and makes it possible to observe that they changed relative to a previous snapshot, without proving equality against BASE or against the payload. Without verify_documents, success is explicitly unverified at the document level. Admitted in every configured environment (BASE, UAT, TEST, LIVE), on the VIP or a direct HA node (a mirror backup fails at its read-only database), always with the same-process CONTROLLED_ACTIVE guard embedded in the same runner that loads; SUSPENDED, ABSENT, an unconfigured role or any error block, even with load=false. It never loads arbitrary local files. After a successful load it reports, under feature_check, websys.Feature codes referenced by the loaded classes that are missing in the target.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace (e.g. 'UAT-SITE')."
                        },
                        "itemset_id": {
                            "type": "integer",
                            "description": "ServerItemSetID of the ItemSet to download from the central server (obtained from iris_ccr_list_itemsets)."
                        },
                        "load": {
                            "type": "boolean",
                            "description": "If true (the default), loads the ItemSet (LoadToOS + LoadToNS) after downloading it. If false, it only downloads and imports the local object without loading."
                        },
                        "verify_documents": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Non-empty list, without case-insensitive duplicates, of exact documents with a valid extension that must already exist in UAT. It takes snapshots before loading and checks afterwards that each document changed; it is accepted only with load=true."
                        },
                        "ccr_id": {"type": "string", "description": "CCR / Perforce job the ItemSet belongs to (e.g. 'ACHS1046'). WSDownload requires it together with a CCR access token (ccr-portal ccr_upload_token)."},
                        "feature_check": {
                            "type": "boolean",
                            "description": "If true (the default) and the load succeeded, runs a fail-soft READ check: the classes of the loaded ItemSet are scanned for websys.Feature codes and any code missing in the target is reported as FEATURE_MISSING (websys.Feature does not travel in ItemSets). It never changes the load outcome and never registers anything."
                        },
                        "token": {
                            "type": "string",
                            "description": "Optional session token for the download. Empty by default (download scoped to the environment)."
                        }
                    },
                    "required": ["server_id", "itemset_id"]
                }
            },
            {
                "name": "iris_list_namespaces",
                "description": "Gets the list of all namespaces available on the selected InterSystems IRIS instance.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_unlock_questionnaire",
                "description": "Unlocks a questionnaire (UserDefWindow) on the InterSystems IRIS server by running the SQL updates needed for the controls, responses and generation state.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "win_code": {
                            "type": "string",
                            "description": "Code of the questionnaire/window to unlock (WIN_Code, e.g. 'SITEPREANE')."
                        }
                    },
                    "required": ["server_id", "win_code"]
                }
            },
            {
                "name": "iris_ccr_commit",
                "description": "Triggers a change-control commit (CCR/Perforce), captures the new local ItemSet and verifies by exact name that it exists on the central server. The change-control engine is discovered in the committing process (%pkg.isc.changecontrol.CCR first, else the native TrakCare TC.ChangeControl.CCR, whose rows are pre-checked to be pending changes of exactly this CCR). The native Commit already uploads the ItemSet; it NEVER re-uploads automatically (this avoids duplicate commits in Perforce). If the central server does not list it yet, it returns PENDING_VERIFICATION. It reports success only with a VERIFIED result.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace (e.g. 'BASE-SITE')."
                        },
                        "ccr_id": {
                            "type": "string",
                            "description": "Optional CCR / Job identifier (e.g. 'SITE0530'). If omitted, it will be read from the server configuration or the IRIS_CCR_ID environment variable."
                        },
                        "upload_token": {
                            "type": "string",
                            "description": "Optional access token for uploading the ItemSet to the central CCR server (e.g. 'sgBhFXAfoz'). If omitted, it will be read from the server configuration or the IRIS_CCR_TOKEN environment variable."
                        },
                        "change_ids": {
                            "type": "array",
                            "description": "List of integer numbers of the local change IDs to include in the bundle (e.g. [17427, 17429]).",
                            "items": {
                                "type": "integer"
                            }
                        }
                    },
                    "required": ["server_id", "change_ids"]
                }
            },
            {
                "name": "iris_ccr_list_uncommitted",
                "description": "Lists the queue of uncommitted local changes (%Studio.SourceControl.Change) on the server. Returns the integer change_ids, the document, the action and the associated CCR/Job. These IDs are the ones iris_ccr_commit consumes to package the ItemSet.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace (e.g. 'BASE-SITE')."
                        },
                        "ccr_id": {
                            "type": "string",
                            "description": "Optional CCR / Job to filter the queue (e.g. 'SITE0530'). If omitted, it lists all uncommitted changes."
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of rows to return (default 200)."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_ccr_reupload_itemset",
                "description": "CCR recovery: verifies a local ItemSet by exact name and, only if it is missing on the central server, sanitizes it and re-uploads it through a direct WSUpload. It avoids duplicates with a verified no-op and reports success only with a VERIFIED result.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace (e.g. 'BASE-SITE')."
                        },
                        "itemset_id": {
                            "type": "integer",
                            "description": "Local ID of the ItemSet to re-upload (e.g. 734). It is obtained from the iris_ccr_commit log or from %Studio.SourceControl.ItemSet."
                        },
                        "ccr_id": {
                            "type": "string",
                            "description": "CCR / Job identifier (e.g. 'SITE0611'). If omitted, it is read from the server config or IRIS_CCR_ID."
                        },
                        "upload_token": {
                            "type": "string",
                            "description": "Access token for uploading the ItemSet to the central CCR server. If omitted, it is read from the server config or IRIS_CCR_TOKEN."
                        },
                        "sanitize": {
                            "type": "boolean",
                            "description": "If true (the default), cleans the ItemSet XML and its payload byte by byte before uploading. Disable only if the ItemSet is already pure ASCII."
                        }
                    },
                    "required": ["server_id", "itemset_id"]
                }
            },
            {
                "name": "iris_ccr_mutate_class_from_file",
                "description": "Mutates one approved class (mutation.class_prefix) on the configured canonical BASE through the installed MCP.Mutate same-process CCR SQL facility. Requires an explicit CCR and expected baseline SHA-256 or ABSENT.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {"type": "string"},
                        "namespace": {"type": "string"},
                        "class_name": {"type": "string"},
                        "file_path": {"type": "string"},
                        "ccr_id": {"type": "string"},
                        "expected_before_sha256": {"type": "string"}
                    },
                    "required": ["server_id", "namespace", "class_name", "file_path", "ccr_id", "expected_before_sha256"]
                }
            },
            {
                "name": "iris_save_class_from_file",
                "description": "Saves a class by reading its source DIRECTLY from a local file (byte by byte) on the machine where the MCP runs.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Full class name (e.g. 'Custom.MyPackage.MyClass')."
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the local .cls file on the machine where the MCP runs. It is read byte by byte as UTF-8."
                        },
                        "auto_checkout": {
                            "type": "boolean",
                            "description": "If true (the default), automatically performs a CCR checkout for the class before saving it.",
                            "default": True
                        },
                        "ccr_id": {
                            "type": "string",
                            "description": "Optional CCR ID (e.g. 'SITE0757'). If provided, it activates the specialized CCR route and must be non-empty ASCII; auto_checkout and verify must stay true.",
                            "default": None
                        },
                        "clean_ascii": {
                            "type": "boolean",
                            "description": "If true, normalizes special characters (accents, em dashes) to pure ASCII before sending. WARNING: LOSSY transformation. Disabled by default.",
                            "default": False
                        },
                        "verify": {
                            "type": "boolean",
                            "description": "If true (the default), after the PUT it rereads the class from the server and verifies that it matches the local file byte for byte (tolerating trailing whitespace). If it does not match, it aborts WITHOUT compiling.",
                            "default": True
                        },
                        "compile": {
                            "type": "boolean",
                            "description": "If true, compiles the class immediately after saving (only if verification passed).",
                            "default": False
                        }
                    },
                    "required": ["server_id", "class_name", "file_path"]
                }
            },
            {
                "name": "iris_ccr_checkout_ct",
                "description": "Registers one or more TrakCare CT (Configuration Tables) objects in the CCR change queue under an explicit CCR, generating consistent GUIDs through the native TrakCare mechanism (TC.ChangeControl.CCR). Use it when the records were created by writing directly to globals and need to enter the CCR queue for commit. It applies to any class with GUIDENABLED=1 that extends XML.Adaptor (epr.CTIconAssociation, epr.CTIconProfile, User.CTOverride, websys.Preferences, etc.). It runs in a runner with the same-process CONTROLLED_ACTIVE guard.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "Configured server ID."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace (e.g. 'BASE-SITE')."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Full name of the CT class (e.g. 'epr.CTIconAssociation', 'epr.CTIconProfile', 'User.CTOverride')."
                        },
                        "ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of record IDs to register in the CCR queue (e.g. ['806','807','808'] or ['6'] for a single record). Each ID will be processed with TC.ChangeControl.CCR.CheckOut($lb(id, class))."
                        },
                        "ccr_id": {
                            "type": "string",
                            "description": "Explicit CCR under which the objects are registered (e.g. 'SITE0757'). If omitted, it is read from the server config or IRIS_CCR_ID."
                        }
                    },
                    "required": ["server_id", "class_name", "ids"]
                }
            }
        ]
        tools.extend(self._document_tools())
        tools.extend(self._dictionary_tools())
        tools.extend(self._feature_tools())
        return {"tools": tools}

    @staticmethod
    def _feature_tools():
        """READ-only TrakCare websys.Feature presence checks. No runner, no writes."""
        server = {"type": "string", "description": "Server ID."}
        namespace = {"type": "string", "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."}
        return [
            {
                "name": "iris_trakcare_feature_check",
                "description": (
                    "READ check of TrakCare websys.Feature records. websys.Feature does not travel in CCR "
                    "ItemSets, so code that checks a Feature can load and stay silently off. Scans the source "
                    "of the given classes for Feature codes (IsFeatureEnabled, GetFeatureParam*, RegisterFeature "
                    "and *FEATURE* class parameters), adds any explicit codes, and reports per code PRESENT or "
                    "MISSING in the namespace plus whether DateFrom/DateTo cover today (a date hint, not "
                    "IsFeatureEnabled). With reference_server_id it also reports presence in the reference and "
                    "per parameter MATCH, DIFFERENT, MISSING_IN_TARGET or EXTRA_IN_TARGET. Parameter values are "
                    f"never returned. Bounded to {feature_check.MAX_CLASSES} classes and "
                    f"{feature_check.MAX_CODES} codes."),
                "inputSchema": {"type": "object", "properties": {
                    "server_id": server, "namespace": namespace,
                    "classes": {"type": "array", "items": {"type": "string"},
                                "description": "Class names without .cls (e.g. 'Custom.Site.Viewer.Lab') whose source is scanned."},
                    "codes": {"type": "array", "items": {"type": "string"},
                              "description": "Feature codes to check explicitly (e.g. 'SiteResultPdfLink')."},
                    "reference_server_id": {"type": "string",
                                            "description": "Optional configured server to compare against (typically BASE)."},
                    "reference_namespace": {"type": "string",
                                            "description": "Optional namespace of the reference server."},
                }, "required": ["server_id"]},
            },
        ]

    @staticmethod
    def _validate_feature_check_arguments(arguments):
        classes = arguments.get("classes", [])
        codes = arguments.get("codes", [])
        if not isinstance(classes, list) or not isinstance(codes, list):
            raise LocalPolicyError("classes and codes must be arrays.")
        if not classes and not codes:
            raise LocalPolicyError("Provide at least one class or one Feature code.")
        if len(classes) > feature_check.MAX_CLASSES or len(codes) > feature_check.MAX_CODES:
            raise LocalPolicyError(
                f"At most {feature_check.MAX_CLASSES} classes and {feature_check.MAX_CODES} codes per call.")
        for name in classes:
            if not feature_check.valid_class_name(name):
                raise LocalPolicyError("classes must be plain non-core class names without .cls.")
        for code in codes:
            if not feature_check.valid_feature_code(code):
                raise LocalPolicyError("codes must be plain Feature codes (letters, digits, '_' or '.').")
        reference = arguments.get("reference_server_id")
        if reference is not None and (not isinstance(reference, str) or not reference.strip()):
            raise LocalPolicyError("reference_server_id must be a non-empty string when provided.")

    def _feature_check_tool(self, client, server_id, arguments):
        classes = list(dict.fromkeys(arguments.get("classes", [])))
        references, unreadable, truncated = feature_check.scan_classes(client, classes)
        for code in arguments.get("codes", []):
            references.setdefault(code, [])
        reference_client = None
        reference_id = arguments.get("reference_server_id")
        if reference_id:
            _, reference_config = self._resolve_server_config(reference_id)
            reference_ns = arguments.get("reference_namespace") or reference_config.get("namespace")
            reference_client = self.get_client(reference_id, namespace_override=reference_ns)
        record = feature_check.check_features(client, references, reference_client=reference_client)
        record.update({"classes_scanned": classes[:feature_check.MAX_CLASSES],
                       "classes_truncated": truncated, "unreadable": unreadable})
        if reference_client is not None:
            record["reference"] = {"server_id": reference_id, "namespace": reference_client.namespace}
        output = (f"### websys.Feature check on {server_id} ({client.namespace})\n\n"
                  + feature_check.render_markdown(record))
        return {"structuredContent": record, "content": [{"type": "text", "text": output}]}

    @staticmethod
    def _post_load_feature_check(client, lines):
        """Fail-soft READ check after a successful ItemSet load: which loaded classes
        reference websys.Feature codes absent from the target. Never raises; never
        changes the load outcome."""
        try:
            imported = next((line for line in lines if line.startswith("IMPORTED|")), None)
            if imported is None:
                raise ValueError("no IMPORTED record")
            local_id = _parse_pipe_record(imported, "IMPORTED").get("local_id", "")
            if not local_id.isdigit():
                raise ValueError("malformed local ItemSet id")
            rows = client.run_query(feature_check.ITEMSET_ITEMS_SQL, [int(local_id)])
            paths = [next((v for k, v in row.items() if str(k).lower() == "itempath"), "") for row in rows or []]
            classes = feature_check.itemset_paths_to_classes(paths)
            references, unreadable, truncated = feature_check.scan_classes(client, classes)
            record = feature_check.check_features(client, references)
            record.update({"classes_scanned": classes[:feature_check.MAX_CLASSES],
                           "classes_truncated": truncated, "unreadable": unreadable})
            return record
        except LocalPolicyError as exc:
            return {"status": "FEATURE_CHECK_UNAVAILABLE", "reason": str(exc)}
        except Exception:
            return {"status": "FEATURE_CHECK_UNAVAILABLE",
                    "reason": "the read-only check failed; details withheld"}

    @staticmethod
    def _dictionary_tools():
        """READ-only class dictionary introspection (%Dictionary.*). No runner, no writes."""
        server = {"type": "string", "description": "Server ID."}
        namespace = {"type": "string", "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."}
        return [
            {
                "name": "iris_describe_class",
                "description": (
                    "Describes a class from the dictionary (%Dictionary): superclasses, parameters, properties "
                    "(type, collection, required, MAXLEN), indices, methods and declared queries. READ gate; "
                    "use it to understand a model before modifying code."),
                "inputSchema": {"type": "object", "properties": {
                    "server_id": server, "namespace": namespace,
                    "class_name": {"type": "string", "description": "Full class name (without .cls), e.g. 'Custom.Site.Data.Patient'."},
                    "include_inherited": {"type": "boolean", "default": False,
                                          "description": "If true, includes inherited properties (%Dictionary.CompiledProperty). By default only those declared in the class."},
                }, "required": ["server_id", "class_name"]},
            },
            {
                "name": "iris_scan_namespace",
                "description": (
                    "READ inventory of the dictionary for a package prefix: declared class_type and a "
                    "direct_role hint based only on known direct superclasses (it does not classify indirect inheritance), "
                    "superclass, property count and SQL table. "
                    "Excludes core (%, Ens, HS, CSP, INFORMATION.SCHEMA). Default limit 200 classes."),
                "inputSchema": {"type": "object", "properties": {
                    "server_id": server, "namespace": namespace,
                    "package_prefix": {"type": "string", "description": "Package prefix (e.g. 'Custom.Site'). Defaults to 'Custom'."},
                    "persistent_only": {"type": "boolean", "default": False, "description": "Only %Persistent classes (SQL tables)."},
                    "limit": {"type": "integer", "default": 200, "description": "Maximum number of classes to return (1-1000)."},
                }, "required": ["server_id"]},
            },
        ]

    @staticmethod
    def _dictionary_row(row):
        """Normalize Atelier SQL column casing into a stable public shape."""
        return {str(key).lower(): value for key, value in row.items()}

    @staticmethod
    def _dictionary_bool(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() in {"1", "true", "yes"}

    @classmethod
    def _describe_class(cls, client, class_name, include_inherited=False):
        class_rows = client.run_query("""
            SELECT Name AS name, Description AS description, Super AS superclasses,
                   Abstract AS abstract, ClassType AS class_type,
                   SqlTableName AS sql_table, TimeChanged AS time_changed
            FROM %Dictionary.ClassDefinition
            WHERE Name = ?
        """, [class_name])
        if not class_rows:
            raise LocalPolicyError(f"Class not found: {class_name}")
        definition = cls._dictionary_row(class_rows[0])

        parameters = [cls._dictionary_row(row) for row in client.run_query("""
            SELECT Name AS name, Type AS type, Default AS default_value,
                   Description AS description
            FROM %Dictionary.ParameterDefinition
            WHERE parent = ?
            ORDER BY Name
        """, [class_name])]

        property_table = "%Dictionary.CompiledProperty" if include_inherited else "%Dictionary.PropertyDefinition"
        origin_column = "Origin" if include_inherited else "parent"
        properties = [cls._dictionary_row(row) for row in client.run_query(f"""
            SELECT Name AS name, {origin_column} AS origin, Type AS type,
                   Collection AS collection, Required AS required,
                   %Dictionary.GetParameterValue(Parameters, 'MAXLEN') AS maxlen
            FROM {property_table}
            WHERE parent = ?
            ORDER BY Name
        """, [class_name])]
        for prop in properties:
            prop["required"] = cls._dictionary_bool(prop.get("required"))

        indices = [cls._dictionary_row(row) for row in client.run_query("""
            SELECT Name AS name, Properties AS properties, Type AS type,
                   _Unique AS unique_value, PrimaryKey AS primary_key
            FROM %Dictionary.IndexDefinition
            WHERE parent = ?
            ORDER BY Name
        """, [class_name])]
        for index in indices:
            index["unique"] = cls._dictionary_bool(index.pop("unique_value", False))
            index["primary_key"] = cls._dictionary_bool(index.get("primary_key"))

        methods = [cls._dictionary_row(row) for row in client.run_query("""
            SELECT Name AS name, ClassMethod AS class_method, ReturnType AS return_type,
                   FormalSpec AS formal_spec, Abstract AS abstract, SqlProc AS sql_proc
            FROM %Dictionary.MethodDefinition
            WHERE parent = ?
            ORDER BY Name
        """, [class_name])]
        for method in methods:
            for field in ("class_method", "abstract", "sql_proc"):
                method[field] = cls._dictionary_bool(method.get(field))

        queries = [cls._dictionary_row(row) for row in client.run_query("""
            SELECT Name AS name, Type AS type, FormalSpec AS formal_spec,
                   SqlProc AS sql_proc, SqlName AS sql_name
            FROM %Dictionary.QueryDefinition
            WHERE parent = ?
            ORDER BY Name
        """, [class_name])]
        for query in queries:
            query["sql_proc"] = cls._dictionary_bool(query.get("sql_proc"))

        superclasses = [item.strip() for item in str(definition.get("superclasses") or "").split(",") if item.strip()]
        return {
            "name": definition.get("name") or class_name,
            "description": definition.get("description") or "",
            "superclasses": superclasses,
            "abstract": cls._dictionary_bool(definition.get("abstract")),
            "class_type": definition.get("class_type") or "",
            "sql_table": definition.get("sql_table") or None,
            "time_changed": definition.get("time_changed"),
            "include_inherited": include_inherited,
            "parameters": parameters,
            "properties": properties,
            "indices": indices,
            "methods": methods,
            "queries": queries,
        }

    @staticmethod
    def _dictionary_direct_role(row):
        """Return a useful direct-superclass hint, never an effective inheritance claim."""
        supers = {item.strip() for item in str(row.get("super") or "").split(",")}
        for superclass, role in (
            ("Ens.Production", "production"),
            ("Ens.DataTransformDTL", "DTL"),
            ("Ens.BusinessService", "business service"),
            ("Ens.BusinessProcess", "business process"),
            ("Ens.BusinessOperation", "business operation"),
            ("%CSP.REST", "REST"),
        ):
            if superclass in supers:
                return role
        return None

    @classmethod
    def _scan_namespace(cls, client, package_prefix, persistent_only=False, limit=200):
        persistent_clause = " AND c.ClassType = 'persistent'" if persistent_only else ""
        rows = client.run_query(f"""
            SELECT TOP {limit} c.Name AS name, c.Super AS super,
                   c.ClassType AS class_type, c.SqlTableName AS sql_table,
                   c.TimeChanged AS time_changed,
                   (SELECT COUNT(*) FROM %Dictionary.PropertyDefinition p
                    WHERE p.parent = c.Name) AS property_count
            FROM %Dictionary.ClassDefinition c
            WHERE c.Name LIKE ?
              AND LEFT(c.Name, 1) <> '%'
              AND c.Name NOT LIKE 'Ens.%'
              AND c.Name NOT LIKE 'HS.%'
              AND c.Name NOT LIKE 'CSP.%'
              AND c.Name NOT LIKE 'INFORMATION.SCHEMA.%'{persistent_clause}
            ORDER BY c.Name
        """, [package_prefix + ".%"])
        result = []
        for raw in rows:
            row = cls._dictionary_row(raw)
            result.append({
                "name": row.get("name") or "",
                "class_type": str(row.get("class_type") or "registered").lower(),
                "direct_role": cls._dictionary_direct_role(row),
                "super": row.get("super") or "",
                "property_count": int(row.get("property_count") or 0),
                "sql_table": row.get("sql_table") or None,
                "time_changed": row.get("time_changed"),
            })
        return result

    @staticmethod
    def _render_class_description(record):
        output = f"### Class `{record['name']}`\n\n"
        output += f"- Tipo: `{record['class_type'] or 'registered'}`\n"
        output += f"- Superclases: `{', '.join(record['superclasses']) or 'ninguna'}`\n"
        output += f"- Abstracta: {'si' if record['abstract'] else 'no'}\n"
        output += f"- Tabla SQL: `{record['sql_table'] or 'ninguna'}`\n"
        if record["description"]:
            output += f"- Description: {_markdown_table_cell(record['description'])}\n"
        sections = (
            ("Propiedades", "properties", ("name", "origin", "type", "collection", "required", "maxlen")),
            ("Indices", "indices", ("name", "properties", "type", "unique", "primary_key")),
            ("Metodos", "methods", ("name", "class_method", "return_type", "formal_spec", "abstract", "sql_proc")),
            ("Queries", "queries", ("name", "type", "formal_spec", "sql_proc", "sql_name")),
        )
        for title, key, fields in sections:
            output += f"\n#### {title} ({len(record[key])})\n\n"
            if not record[key]:
                output += "Ninguno.\n"
                continue
            output += "| " + " | ".join(fields) + " |\n"
            output += "| " + " | ".join(":---" for _ in fields) + " |\n"
            for row in record[key]:
                output += "| " + " | ".join(_markdown_table_cell(row.get(field, "")) for field in fields) + " |\n"
        return output

    @staticmethod
    def _document_tools():
        """Document tools: same gates as the class tools, explicit extension required.

        `document_name` carries its extension (.inc/.mac/.int/.cls/.bas/.obj); nothing
        is inferred or appended. This is how include files and routines are handled.
        """
        server = {"type": "string", "description": "Server ID."}
        namespace = {"type": "string", "description": "Optional namespace. If not specified, uses the one configured for the server, or 'USER' by default."}
        document = {"type": "string", "description": (
            "Exact document name WITH a lowercase extension: .inc (include), .mac (routine), .int, .cls, .bas, .obj "
            "(e.g. 'Custom.Site.Macros.inc', 'Custom.Site.Util.mac'). No extension is inferred or appended; "
            "for classes use the iris_*_class tools as well.")}
        write_props = {
            "auto_checkout": {"type": "boolean", "default": True, "description": "If true (the default), performs a CCR checkout of the document before saving it (only with an explicit ccr_id)."},
            "ccr_id": {"type": "string", "default": None, "description": "Optional CCR ID (e.g. 'SITE0757'). With auto_checkout=true the checkout is made under this CCR (same-process CONTROLLED_ACTIVE guard) and the save is tracked. Without ccr_id the save is admissible only in a namespace with CCR ABSENT and a cataloged package."},
            "clean_ascii": {"type": "boolean", "default": False, "description": "If true, normalizes special characters to pure ASCII before sending. WARNING: LOSSY transformation. Disabled by default."},
            "verify": {"type": "boolean", "default": True, "description": "If true (the default), after the PUT it rereads the document and verifies that it matches what was sent (tolerating trailing whitespace). If it does not match, it aborts WITHOUT compiling."},
            "compile": {"type": "boolean", "default": False, "description": "If true, compiles the document immediately after saving it (for .inc it recompiles dependents according to IRIS)."},
        }

        def tool(name, description, extra_props=None, extra_required=()):
            props = {"server_id": server, "namespace": namespace, "document_name": document}
            props.update(extra_props or {})
            return {"name": name, "description": description,
                    "inputSchema": {"type": "object", "properties": props,
                                    "required": ["server_id", "document_name", *extra_required]}}

        return [
            tool("iris_get_document",
                 "Retrieves the source of a document by exact name with extension: include files (.inc), routines (.mac/.int), classes (.cls). READ gate."),
            tool("iris_save_document",
                 "Saves the complete content of a document (.inc/.mac/.int/.cls) with optional CCR auto-checkout, round-trip verification and optional compilation. Same gates as iris_save_class; the package is evaluated on the name without the extension.",
                 {"content": {"type": "string", "description": "Complete content of the document."}, **write_props},
                 ("content",)),
            tool("iris_save_document_from_file",
                 "Saves a document (.inc/.mac/.int/.cls) by reading its source byte by byte from a local file on the MCP machine.",
                 {"file_path": {"type": "string", "description": "Absolute path to the local file (UTF-8)."}, **write_props},
                 ("file_path",)),
            tool("iris_patch_document",
                 "Surgical patch to an existing document (.inc/.mac/.int/.cls): downloads the source, replaces old_string with new_string EXACTLY (expected_count occurrences) and saves. If the count does not match it aborts WITHOUT writing.",
                 {"old_string": {"type": "string", "description": "Exact literal text to replace."},
                  "new_string": {"type": "string", "description": "Text that replaces old_string."},
                  "expected_count": {"type": "integer", "default": 1, "description": "Expected occurrences of old_string (default 1)."},
                  **write_props},
                 ("old_string", "new_string")),
            tool("iris_delete_document",
                 "Permanently deletes a document (.inc/.mac/.int/.cls) from the namespace. CODE WRITE without CCR: admissible only on a cataloged ABSENT route."),
        ]

    # ------------------------------------------------------------------
    # Shared CODE WRITE flow for class and document tools: optional CCR checkout
    # (explicit ccr_id only), REST save with round-trip verification, optional
    # compile. doc_name is already an exact, validated document (Foo.cls, Foo.inc).
    # ------------------------------------------------------------------

    @staticmethod
    def _checkout_before_write(client, doc_name, arguments, failure_verb):
        """Returns (tracked_ccr, checkout_msg) or an error result dict."""
        if not arguments.get("auto_checkout", True):
            return None, "Not attempted"
        ccr_id_param = arguments.get("ccr_id")
        if not ccr_id_param:
            return None, "Not performed: without an explicit ccr_id there is no CCR checkout (cataloged ABSENT route only)"
        log_debug(f"Attempting CCR checkout for {doc_name} under CCR {ccr_id_param}...")
        try:
            success_co, msg_co = client.ccr_checkout(None, ccr_id_param, document_name=doc_name)
        except Exception as ce:
            log_debug(f"CCR checkout for {doc_name} under {ccr_id_param} failed: {ce}")
            return {"isError": True, "content": [{"type": "text", "text": f"CCR checkout failed; did not {failure_verb} `{doc_name}`: {ce}"}]}
        if not success_co:
            return {"isError": True, "content": [{"type": "text", "text": f"CCR checkout rejected; did not {failure_verb} `{doc_name}`: {msg_co}"}]}
        log_debug(f"CCR checkout for {doc_name} under {ccr_id_param} succeeded.")
        return ccr_id_param, f"Exito bajo CCR {ccr_id_param}: {msg_co}"

    @staticmethod
    def _compile_after_write(client, doc_name, tracked_ccr):
        try:
            res = client.compile_document(doc_name, ccr_id=tracked_ccr)
        except Exception as coe:
            return f"\n\n\u274c **Error al compilar**: {str(coe)}"
        errors = res.get("status", {}).get("errors", [])
        summary = res.get("status", {}).get("summary", "Compiled")
        if errors:
            text = f"\n\n\u26a0\ufe0f **Compilation failed**: {summary}\n"
            for err in errors:
                text += f"- \u274c {json.dumps(err)}\n"
            return text
        return f"\n\n\u2705 **Compiled successfully**: {summary}"

    def _write_document(self, client, doc_name, content, arguments, failure_verb="save",
                        header_lines=()):
        """Save `content` (str) into doc_name through the CODE WRITE gates.

        Package catalog is enforced first; then optional CCR checkout, ASCII
        normalization (opt-in, lossy), save + round-trip verification, compile.
        """
        client._require_cataloged_package(doc_name)
        clean_ascii = arguments.get("clean_ascii", False)
        if clean_ascii:
            log_debug(f"Normalizing {doc_name} content to clean ASCII...")
            content = clean_to_ascii(content)
        content_lines = content.splitlines()

        checkout = self._checkout_before_write(client, doc_name, arguments, failure_verb)
        if isinstance(checkout, dict):
            return checkout
        tracked_ccr, checkout_msg = checkout

        verify = arguments.get("verify", True)
        success, msg = client.save_document(doc_name, content_lines, verify=verify, ccr_id=tracked_ccr)

        detail_msg = "".join(line + "\n" for line in header_lines)
        detail_msg += (
            f"**Saved**: {success}\n**Detail**: {msg}\n"
            f"**Round-trip verification**: {'enabled' if verify else 'disabled'}\n"
            f"**CCR checkout**: {checkout_msg}\n"
            f"**ASCII cleanup**: {'applied' if clean_ascii else 'disabled'}"
        )
        compile_after = arguments.get("compile", False)
        if success and compile_after:
            log_debug(f"Compiling {doc_name} immediately after write...")
            detail_msg += self._compile_after_write(client, doc_name, tracked_ccr)
        elif compile_after and not success:
            detail_msg += "\n\n\u23f9\ufe0f **Compilation SKIPPED**: the save/verification did not succeed."
        return {"isError": not success, "content": [{"type": "text", "text": detail_msg}]}

    def _read_local_source(self, file_path):
        """Returns file text or an error result dict."""
        if not file_path:
            return {"isError": True, "content": [{"type": "text", "text": "\u274c Se requiere `file_path`."}]}
        if not os.path.exists(file_path):
            return {"isError": True, "content": [{"type": "text", "text": f"\u274c El archivo local no existe: {file_path}"}]}
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as fe:
            return {"isError": True, "content": [{"type": "text", "text": f"\u274c Could not read {file_path}: {fe}"}]}

    def _patch_document(self, client, doc_name, arguments):
        old_string = arguments.get("old_string")
        new_string = arguments.get("new_string")
        if old_string is None or new_string is None:
            return {"isError": True, "content": [{"type": "text", "text": "\u274c Se requieren `old_string` y `new_string`."}]}
        expected_count = arguments.get("expected_count", 1)
        client._require_cataloged_package(doc_name)
        # Fetch the current source server-side (correct UTF-8, no mojibake).
        try:
            content_lines = client.get_document(doc_name)
        except Exception as ge:
            return {"isError": True, "content": [{"type": "text", "text": f"\u274c Could not download {doc_name}: {ge}"}]}
        content = "\n".join(content_lines)
        actual = content.count(old_string)
        if actual != expected_count:
            return {"isError": True, "content": [{"type": "text", "text": (
                f"\u26a0\ufe0f Patch aborted (nothing was saved). `old_string` appears {actual} time(s) in {doc_name}, "
                f"{expected_count} expected. Add more context to make it unique or adjust `expected_count`.")}]}
        content = content.replace(old_string, new_string)
        return self._write_document(client, doc_name, content, arguments, failure_verb="patch",
                                    header_lines=(f"**Patch applied**: {actual} replacement(s)",))

    def _handle_rules_tool(self, arguments):
        """iris_rules: read the rules; apply only NARROWING edits; plan widening ones."""
        import datetime as _datetime
        from isc_ai import rules as isc_rules
        from isc_ai import rules_write as isc_rules_write

        if self.runtime_config is None or self.runtime_config.project_dir is None:
            raise LocalPolicyError("iris_rules requires a loaded project configuration.")
        project_dir = Path(self.runtime_config.project_dir)
        ruleset = self.policy.rules or isc_rules.RuleSet(packages={}, exceptions=())
        action = arguments.get("action")
        today = _datetime.date.today()

        def reply(data, text):
            return {"structuredContent": data, "content": [{"type": "text", "text": text}]}

        def find(exc_id):
            match = next((exc for exc in ruleset.exceptions if exc.id == exc_id), None)
            if match is None:
                raise LocalPolicyError(f"No exception with id '{exc_id}'.")
            return match

        def audit(record):
            record = dict(record, ts=_datetime.datetime.now(_datetime.timezone.utc).isoformat(), via="mcp")
            _append_jsonl_locked(project_dir / ".backup" / "rules" / "audit.jsonl", record)

        def written(executed, fp, plan):
            if executed:
                return reply({"applied": True, "fingerprint": None, "description": plan["description"]},
                             f"Applied (narrowing): {plan['description']}. A backup of isc-ai.rules.json was kept.")
            return reply({"applied": False, "fingerprint": fp, "description": plan["description"]},
                         f"Not applied (widening): {plan['description']}.\n"
                         f"A human must review and run: `isc-ai rules apply {fp}`")

        try:
            if action == "list":
                rows, lines = [], []
                for exc in ruleset.exceptions:
                    expiry = isc_rules._parse_expiry_date(exc.expires)
                    days = None if expiry is None else (expiry - today).days
                    rows.append(dict(isc_rules.exception_to_dict(exc), days_left=days))
                    state = "EXPIRED" if days is None or days < 0 else f"{days} day(s) left"
                    lines.append(f"- `{exc.id}` [{exc.capability}] {exc.label} (ticket {exc.ticket}) "
                                 f"until {exc.expires}: {state}")
                return reply({"exceptions": rows}, "\n".join(lines) or "No exceptions configured.")
            if action == "show":
                match = find(arguments.get("id"))
                return reply({"exception": isc_rules.exception_to_dict(match)}, json.dumps(
                    isc_rules.exception_to_dict(match), indent=2, ensure_ascii=False))
            if action == "check":
                capability = arguments.get("capability")
                if capability not in isc_rules.EXCEPTION_CAPABILITIES:
                    raise LocalPolicyError(f"capability must be one of {sorted(isc_rules.EXCEPTION_CAPABILITIES)}.")
                matches = self.policy.effective_capability_exceptions(
                    capability, namespace=arguments.get("namespace"), host=arguments.get("host"),
                    role=arguments.get("role"), server=arguments.get("server"),
                    path_prefix=arguments.get("path_prefix"), servers=self.servers)
                ids = [exc.id for exc in matches]
                text = (f"Allowed for {capability} by: {', '.join(ids)}" if ids
                        else f"No active exception allows {capability} for that scope.")
                return reply({"allowed": bool(ids), "matches": ids}, text)
            if action == "audit":
                issues = isc_rules.validate(ruleset, today=today)
                soon = [exc.id for exc in isc_rules.expiring_within(ruleset, 7, today=today)]
                text = "\n".join(issues + [f"Expires within 7 days: {', '.join(soon)}" if soon else ""]).strip()
                return reply({"issues": issues, "expiring_within_7_days": soon}, text or "No issues.")
            if action == "remove":
                exc_id = arguments.get("id")
                find(exc_id)
                audit({"action": "remove", "id": exc_id})
                return written(*isc_rules_write.remove_exception(project_dir, exc_id, apply=True))
            if action == "renew":
                exc_id, expires = arguments.get("id"), arguments.get("expires")
                find(exc_id)
                base = isc_rules_write.read_rules_data(project_dir)
                narrowing = isc_rules_write.is_renewal_narrowing(base, exc_id, expires)
                if narrowing:
                    audit({"action": "renew", "id": exc_id, "expires": expires})
                return written(*isc_rules_write.renew_exception(project_dir, exc_id, expires, apply=narrowing))
            if action == "add":
                item = arguments.get("exception")
                if not isinstance(item, dict):
                    raise LocalPolicyError("add requires an 'exception' object.")
                return written(*isc_rules_write.add_exception(project_dir, item, apply=False))
        except (ConfigError, ValueError) as exc:
            raise LocalPolicyError(str(exc)) from None
        raise LocalPolicyError("Unknown iris_rules action; use list, show, check, audit, remove, renew or add "
                               "(plans are applied only by a human with 'isc-ai rules apply').")

    def handle_tool_call(self, name, arguments):
        try:
            # Validate locally before client construction or any metadata request.
            tools = {tool["name"]: tool for tool in self.handle_tools_list()["tools"]}
            if not isinstance(name, str) or name not in tools or not isinstance(arguments, dict):
                raise LocalPolicyError("Invalid tool request.")
            schema = tools[name]["inputSchema"]
            if any(key not in arguments for key in schema.get("required", [])):
                raise LocalPolicyError("Missing required tool arguments.")
            types = {"string": str, "boolean": bool, "integer": int, "array": list, "object": dict}
            for key, prop in schema.get("properties", {}).items():
                if key in arguments and arguments[key] is not None:
                    declared = prop.get("type")
                    declared = declared if isinstance(declared, list) else [declared]
                    expected = tuple(types[t] for t in declared if t in types)
                    if expected and type(arguments[key]) not in expected:
                        raise LocalPolicyError("Invalid tool argument type.")
                elif key in schema.get("required", []) and arguments.get(key) is None:
                    raise LocalPolicyError("Missing required tool arguments.")
            if arguments.get("namespace") is not None:
                validate_namespace(arguments["namespace"])
            properties = schema.get("properties", {})
            ccr_target_tools = {"iris_ccr_status", "iris_ccr_checkout", "iris_ccr_undocheckout"}
            if name in ccr_target_tools:
                # Exactly one target: class_name (implied .cls) or document_name (explicit
                # extension, e.g. Custom.Site.Macros.inc). Resolved once, here.
                arguments = dict(arguments)
                arguments["_document"] = AtelierClient._tracked_document_name(
                    arguments.get("class_name"), arguments.get("document_name"))
            elif "document_name" in properties:
                # Document tools never infer: the extension is part of the identifier.
                arguments = dict(arguments)
                arguments["document_name"] = AtelierClient._validate_document_name(arguments.get("document_name"))
            elif "class_name" in properties:
                # Run before config reload, client creation, discovery, local file
                # reads and auto-checkout, including auto_checkout=False paths.
                document_tools = {"iris_get_class", "iris_save_class", "iris_save_class_from_file",
                                  "iris_patch_class", "iris_compile_class", "iris_delete_class"}
                if name in document_tools:
                    arguments = dict(arguments)
                    if name == "iris_compile_class":
                        arguments["class_name"] = AtelierClient._compile_document_name(arguments.get("class_name"))
                    else:
                        arguments["class_name"] = AtelierClient._class_document_name(arguments.get("class_name"))
                else:
                    AtelierClient._validate_class_name(arguments.get("class_name"))
                    if name == "iris_describe_class" and arguments["class_name"].endswith(".cls"):
                        raise LocalPolicyError("iris_describe_class requires a class name without .cls.")
            if name == "iris_scan_namespace":
                package_prefix = arguments.get("package_prefix") or "Custom"
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*", package_prefix):
                    raise LocalPolicyError("package_prefix must be a plain non-core package name.")
                limit = arguments.get("limit", 200)
                if type(limit) is not int or not 1 <= limit <= 1000:
                    raise LocalPolicyError("limit must be an integer between 1 and 1000.")
            if name == "iris_trakcare_feature_check":
                self._validate_feature_check_arguments(arguments)
            if name == "iris_ccr_download_load" and "feature_check" in arguments \
                    and not isinstance(arguments.get("feature_check"), bool):
                raise LocalPolicyError("feature_check must be a boolean.")
            if name == "iris_ccr_download_load" and "verify_documents" in arguments:
                verify_documents = arguments.get("verify_documents")
                if not verify_documents:
                    raise LocalPolicyError("verify_documents must be a non-empty array when provided.")
                if arguments.get("load", True) is False:
                    raise LocalPolicyError("verify_documents requires load=true.")
                validated_documents = [
                    AtelierClient._validate_document_name(document)
                    for document in verify_documents
                ]
                if len({document.casefold() for document in validated_documents}) != len(validated_documents):
                    raise LocalPolicyError("verify_documents must not contain case-insensitive duplicates.")
            log_debug(f"Executing tool: {name}; arguments <redacted>")
            self.load_configuration()
            if name == "iris_list_servers":
                result_servers = [self._server_catalog_record(sid) for sid in self.servers]
                
                output = "# Servidores InterSystems IRIS Configuraciones\n\n"
                if not result_servers:
                    output += "No servers configured: add entries to `~/.isc-ai/servers.json` or to the project's `.isc-ai/config.json` (`isc-ai config add-server`)"
                else:
                    output += "| ID | Target | State | Host | Port | Path prefix | Namespace | Detail |\n"
                    output += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                    for s in result_servers:
                        fields = (s.get(key, "—") for key in (
                            "id", "canonical_server_id", "status", "host", "port", "path_prefix", "namespace", "reason"))
                        output += "| " + " | ".join(_markdown_table_cell(value) for value in fields) + " |\n"
                return {"structuredContent": {"servers": result_servers}, "content": [{"type": "text", "text": output}]}

            if name == "iris_rules":
                return self._handle_rules_tool(arguments)
                
            server_id = arguments.get("server_id")
            canonical_server_id, server_config = self._resolve_server_config(server_id)

            controlled_mutation = name == "iris_ccr_mutate_class_from_file"
            mutation_input = None
            if controlled_mutation:
                if not self.policy.mutation_configured:
                    raise LocalPolicyError("Controlled CCR mutation requires canonical_base, site.ccr_id and mutation settings in the project configuration.")
                requested_namespace = arguments.get("namespace")
                if not isinstance(requested_namespace, str):
                    raise LocalPolicyError("Controlled CCR mutation blocked: namespace must match the configured canonical BASE namespace.")
                self._require_canonical_base_target(server_id, canonical_server_id, server_config,
                                                    requested_namespace, "Controlled CCR mutation blocked")
                class_name = arguments["class_name"]
                ccr_id = arguments["ccr_id"]
                if not self.policy.ccr_matches(ccr_id):
                    raise LocalPolicyError(f"ccr_id must match {self.policy.ccr_description}.")
                expected_before = arguments["expected_before_sha256"]
                if not self.policy.mutation_class_allowed(class_name):
                    raise LocalPolicyError(
                        f"Controlled CCR mutation accepts approved {self.policy.mutation_class_prefix} classes only."
                    )
                if expected_before != "ABSENT" and not re.fullmatch(r"[0-9a-f]{64}", expected_before):
                    raise LocalPolicyError("expected_before_sha256 must be ABSENT or lowercase SHA-256.")
                requested_path = Path(arguments["file_path"])
                if not requested_path.is_absolute():
                    raise LocalPolicyError("file_path must be absolute.")
                expected_path = self.policy.mutation_expected_path(class_name)
                if expected_path is None:
                    raise LocalPolicyError("Controlled CCR mutation has no approved local source path for this class.")
                try:
                    resolved_path = requested_path.resolve(strict=True)
                except (OSError, RuntimeError):
                    raise LocalPolicyError("The local class file is unavailable.") from None
                if resolved_path != expected_path.resolve() or not resolved_path.is_file():
                    raise LocalPolicyError("class_name and file_path do not match the exact approved local source.")
                source_bytes = resolved_path.read_bytes()
                if (not source_bytes or source_bytes.startswith(b"\xef\xbb\xbf")
                        or b"\x00" in source_bytes):
                    raise LocalPolicyError("Source must be non-empty UTF-8 without BOM/NUL and at most 512 KiB.")
                try:
                    source_text = source_bytes.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    raise LocalPolicyError("Source is not strict UTF-8.") from None
                transport_text = source_text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
                transport_bytes = transport_text.encode("utf-8")
                if len(transport_bytes) > 524288:
                    raise LocalPolicyError("Canonical source must be at most 512 KiB.")
                declarations = re.findall(r"(?im)^\s*Class\s+([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\b", transport_text)
                if declarations != [class_name]:
                    raise LocalPolicyError("Source must declare exactly the requested class.")
                candidate_sha256 = hashlib.sha256(transport_bytes).hexdigest()
                mutation_input = {
                    "class_name": class_name,
                    "ccr_id": ccr_id,
                    "expected_before": expected_before,
                    "candidate_sha256": candidate_sha256,
                    "source_bytes": transport_bytes,
                    "source_base64": base64.b64encode(transport_bytes).decode("ascii"),
                }
            if name == "iris_execute_objectscript" and arguments.get("namespace") is not None:
                requested_namespace = validate_namespace(arguments["namespace"])
                if requested_namespace != server_config["namespace"]:
                    raise LocalPolicyError(
                        "Temporary runner namespace must match the configured canonical namespace."
                    )

            # Validate before client creation so a malformed read-only request cannot trigger network discovery.
            list_itemsets_ccr_id = None
            if name == "iris_ccr_list_itemsets":
                list_itemsets_ccr_id = arguments.get("ccr_id") or server_config.get("ccr_id") or os.environ.get("IRIS_CCR_ID")
                if not list_itemsets_ccr_id:
                    raise LocalPolicyError(
                        "The 'ccr_id' parameter is required. Pass it, set it in the server "
                        "configuration ('config add-server') under 'ccr_id', or define IRIS_CCR_ID."
                    )
            
            # Dynamic namespace check (skip for list tools)
            selected_ns = arguments.get("namespace")
            if name in {"iris_execute_objectscript", "iris_ccr_mutate_class_from_file"}:
                selected_ns = server_config["namespace"]
            elif name not in ["iris_list_servers", "iris_list_namespaces"]:
                working_ns, selected_ns = self.check_namespace_ambiguity(server_id, arguments.get("namespace"))
                if working_ns:
                    ns_list_str = "\n".join([f"- **`{ns}`**" for ns in working_ns])
                    return {
                        "isError": True,
                        "content": [{
                            "type": "text",
                            "text": (
                                f"⚠️ **Multiple working namespaces detected**\n\n"
                                f"The tool call for server `{server_id}` did not specify the `namespace` parameter, "
                                f"and this server holds more than one active working namespace.\n\n"
                                f"Pick one of these namespaces to proceed:\n{ns_list_str}\n\n"
                                f"Run the tool again with the namespace you want in the `namespace` parameter."
                            )
                        }]
                    }
                    
            client = self.get_client(server_id, namespace_override=selected_ns)
            
            # Category routing. Dispatch applies the static part of each gate and the
            # low-level client methods gate again with full context; no cached
            # evidence or caller flag authorizes anything. External CCR discovery
            # (iris_ccr_namespace_status) is informational and never routes here.
            if name in self.READ_TOOLS or name in self._ENSEMBLE_TOOLS:
                allowed, reason = client.is_read_allowed()
            elif name == "iris_execute_objectscript":
                allowed, reason = client.is_runtime_operation_allowed()
            elif name in self.CODE_WRITE_TOOLS:
                allowed, reason = client.is_code_write_route_allowed(
                    promotion=(name == "iris_ccr_download_load"))
            elif name in self.LIVE_CONFIG_WINDOW_TOOLS:
                # Gated per action in the handler: get -> READ, set -> LIVE CONFIG WINDOW.
                allowed, reason = True, ""
            else:
                allowed, reason = False, "Tool has no gate category."
            if not allowed:
                return {
                    "isError": True,
                    "content": [{
                        "type": "text",
                        "text": f"Operation denied: {reason}"
                    }]
                }

            if name in self.LIVE_CONFIG_WINDOW_TOOLS:
                return self._live_window_item_setting(client, server_id, arguments)
            if (self.policy.capability_candidates("ensemble_control")
                    and arguments.get("action") in self._LIVE_WINDOW_RUNTIME_ACTIONS.get(name, ())
                    and client._production_gate_reason() is not None):
                return self._live_window_ensemble_control(name, client, server_id, arguments)

            if name == "iris_query_sql":
                query = arguments.get("query", "")
                violation = _read_sql_policy_violation(query)
                if violation:
                    return {
                        "isError": True,
                        "content": [{
                            "type": "text",
                            "text": f"SQL execution denied: {violation}"
                        }]
                    }
                        
                params = arguments.get("parameters", [])
                timeout = arguments.get("timeout")
                if timeout is not None:
                    try:
                        timeout = float(timeout)
                    except (ValueError, TypeError):
                        timeout = None
                results = client.run_query(query, params, timeout=timeout)
                
                output = f"### Resultados SQL en {server_id} ({client.namespace})\n"
                if not results:
                    output += "The query ran successfully but returned no rows.\n"
                else:
                    headers = list(results[0].keys())
                    output += "\n| " + " | ".join(headers) + " |\n"
                    output += "| " + " | ".join([":---" for _ in headers]) + " |\n"
                    for row in results[:100]:
                        cells = []
                        for h in headers:
                            val = row.get(h)
                            if val is None:
                                cells.append("*NULL*")
                            else:
                                cells.append(str(val).replace("|", "\\|").replace("\n", " "))
                        output += "| " + " | ".join(cells) + " |\n"
                    if len(results) > 100:
                        output += f"\n*(Showing the first 100 of {len(results)} filas devueltas)*\n"
                return {"content": [{"type": "text", "text": output}]}
                
            elif name == "iris_get_class":
                class_name = arguments.get("class_name")
                
                content_lines = client.get_document(class_name)
                output = "\n".join(content_lines)
                formatted = f"```cos\n{output}\n```"
                return {"content": [{"type": "text", "text": formatted}]}
                
            elif name == "iris_save_class":
                class_name = arguments.get("class_name")
                return self._write_document(client, class_name, arguments.get("content"), arguments)

            elif name == "iris_ccr_mutate_class_from_file":
                client._require_canonical_base_ccr_mutation()
                record = client.ccr_mutate_class(
                    mutation_input["class_name"],
                    mutation_input["ccr_id"],
                    mutation_input["expected_before"],
                    mutation_input["candidate_sha256"],
                    mutation_input["source_base64"],
                )
                return {
                    "structuredContent": record,
                    "content": [{"type": "text", "text": (
                        f"CCR mutation VERIFIED for `{mutation_input['class_name']}` under "
                        f"`{mutation_input['ccr_id']}`; SHA-256 "
                        f"`{mutation_input['candidate_sha256']}`; tracked, round-trip verified, and compiled."
                    )}],
                }

            elif name == "iris_save_class_from_file":
                class_name = arguments.get("class_name")
                client._require_cataloged_package(class_name)
                file_path = arguments.get("file_path")
                content = self._read_local_source(file_path)
                if isinstance(content, dict):
                    return content
                header = (f"**Source**: `{file_path}` ({len(content.splitlines())} lineas)",)
                return self._write_document(client, class_name, content, arguments, header_lines=header)

            elif name == "iris_patch_class":
                return self._patch_document(client, arguments.get("class_name"), arguments)

            elif name == "iris_compile_class":
                class_name = arguments.get("class_name")
                
                res = client.compile_document(class_name)
                errors = res.get("status", {}).get("errors", [])
                summary = res.get("status", {}).get("summary", "Compiled")
                console = res.get("console", [])
                
                output = f"### Compilation of {class_name} on {server_id}\n"
                output += f"**Resumen**: {summary}\n"
                if errors:
                    output += "\n#### Compilation errors:\n"
                    for err in errors:
                        output += f"- ❌ {json.dumps(err)}\n"
                if console:
                    output += "\n#### Compilation console:\n```text\n"
                    output += "\n".join(console)
                    output += "\n```"
                return {"content": [{"type": "text", "text": output}]}
                
            elif name == "iris_delete_class":
                success, msg = client.delete_document(arguments.get("class_name"))
                return {"isError": not success, "content": [{"type": "text", "text": f"**Deleted**: {success}\n**Detalle**: {msg}"}]}

            elif name == "iris_get_document":
                doc_name = arguments.get("document_name")
                content_lines = client.get_document(doc_name)
                return {"content": [{"type": "text", "text": f"```cos\n" + "\n".join(content_lines) + "\n```"}]}

            elif name == "iris_save_document":
                return self._write_document(client, arguments.get("document_name"), arguments.get("content"), arguments)

            elif name == "iris_save_document_from_file":
                doc_name = arguments.get("document_name")
                client._require_cataloged_package(doc_name)
                file_path = arguments.get("file_path")
                content = self._read_local_source(file_path)
                if isinstance(content, dict):
                    return content
                header = (f"**Source**: `{file_path}` ({len(content.splitlines())} lineas)",)
                return self._write_document(client, doc_name, content, arguments, header_lines=header)

            elif name == "iris_patch_document":
                return self._patch_document(client, arguments.get("document_name"), arguments)

            elif name == "iris_delete_document":
                success, msg = client.delete_document(arguments.get("document_name"))
                return {"isError": not success, "content": [{"type": "text", "text": f"**Deleted**: {success}\n**Detalle**: {msg}"}]}
                
            elif name == "iris_list_classes":
                package_prefix = arguments.get("package_prefix", "Custom")
                query = """
                    SELECT Name, Description, TimeChanged, Abstract, CompileAfter
                    FROM %Dictionary.ClassDefinition 
                    WHERE Name LIKE ? 
                    ORDER BY Name
                """
                results = client.run_query(query, [package_prefix + "%"])
                
                output = f"### Clases en {server_id} with prefix `{package_prefix}`\n\n"
                if not results:
                    output += f"No custom classes found with prefix `{package_prefix}`."
                else:
                    output += "| Class name | Abstract | Compile After | Changed on server |\n"
                    output += "| :--- | :--- | :--- | :--- |\n"
                    for r in results:
                        output += f"| `{r.get('Name')}` | {'Sí' if r.get('Abstract') else 'No'} | `{r.get('CompileAfter') or ''}` | `{r.get('TimeChanged')}` |\n"
                return {"content": [{"type": "text", "text": output}]}

            elif name == "iris_describe_class":
                class_name = arguments.get("class_name")
                record = self._describe_class(client, class_name, bool(arguments.get("include_inherited", False)))
                return {"structuredContent": record, "content": [{"type": "text", "text": self._render_class_description(record)}]}

            elif name == "iris_scan_namespace":
                package_prefix = arguments.get("package_prefix") or "Custom"
                limit = arguments.get("limit", 200)
                rows = self._scan_namespace(client, package_prefix, bool(arguments.get("persistent_only", False)), limit)
                output = f"### Inventory of `{package_prefix}` on {server_id} ({client.namespace}): {len(rows)} class(es)\n\n"
                if not rows:
                    output += "No classes found with that prefix (core excluded)."
                else:
                    output += "| Class | Class type | Direct role | Superclass | Props | SQL table | Changed |\n| :--- | :--- | :--- | :--- | ---: | :--- | :--- |\n"
                    for r in rows:
                        output += "| " + " | ".join(_markdown_table_cell(r.get(k, "")) for k in (
                            "name", "class_type", "direct_role", "super", "property_count", "sql_table", "time_changed")) + " |\n"
                    if len(rows) >= limit:
                        output += f"\n_Result truncated at {limit}; ajusta `package_prefix` o `limit`._"
                return {"structuredContent": {"package_prefix": package_prefix, "classes": rows},
                        "content": [{"type": "text", "text": output}]}

            elif name == "iris_trakcare_feature_check":
                return self._feature_check_tool(client, server_id, arguments)
                
            elif name == "iris_get_event_logs":
                limit = min(int(arguments.get("limit", 20)), 100)
                query = """
                    SELECT TOP ? ID, TimeLogged, ConfigName, Type, Text 
                    FROM Ens_Util.Log 
                    WHERE Type = 'Error' OR Type = 'Warning' 
                    ORDER BY ID DESC
                """
                results = client.run_query(query, [limit])
                
                output = f"### Latest {limit} errors/warnings in Ens_Util.Log ({server_id})\n\n"
                if not results:
                    output += "No errors or warnings have been recorded in the Ensemble log recently."
                else:
                    output += "| ID | Log date | Config name | Type | Message |\n"
                    output += "| :--- | :--- | :--- | :--- | :--- |\n"
                    for r in results:
                        t_val = r.get("Type")
                        type_str = "Error" if t_val == 2 else "Warning" if t_val == 3 else str(t_val)
                        msg_text = r.get("Text", "")
                        if msg_text and len(msg_text) > 85:
                            msg_text = msg_text[:82] + "..."
                        output += f"| `{r.get('ID')}` | `{r.get('TimeLogged')}` | `{r.get('ConfigName')}` | **{type_str}** | {msg_text} |\n"
                return {"content": [{"type": "text", "text": output}]}
                
            elif name == "iris_get_recent_messages":
                limit = min(int(arguments.get("limit", 20)), 100)
                only_errors = arguments.get("only_errors", False)
                
                status_map = {1: "Created", 2: "Queued", 3: "Delivered", 4: "Discarded", 5: "Deferred", 6: "Suspended", 7: "Aborted", 9: "Completed"}
                sql = "SELECT TOP ? ID, SessionId, TimeCreated, SourceConfigName, TargetConfigName, Status, IsError, Description FROM Ens.MessageHeader "
                params = [limit]
                if only_errors:
                    sql += "WHERE IsError = 1 OR Status = 6 OR Status = 7 "
                sql += "ORDER BY ID DESC"
                
                results = client.run_query(sql, params)
                
                output = f"### Latest {limit} bus messages in Ens.MessageHeader ({server_id})\n\n"
                if not results:
                    output += "No recent messages were recorded on the bus."
                else:
                    output += "| ID | SessionId | Created | Source | Target | State | Error? | Detail / description |\n"
                    output += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                    for r in results:
                        err_str = r.get("Description", "")
                        if err_str and len(err_str) > 55:
                            err_str = err_str[:52] + "..."
                        is_err = "❌ Yes" if r.get("IsError") == 1 else "✅ No"
                        st_code = r.get("Status")
                        st_label = status_map.get(st_code, str(st_code))
                        output += f"| `{r.get('ID')}` | `{r.get('SessionId')}` | `{r.get('TimeCreated')}` | `{r.get('SourceConfigName')}` | `{r.get('TargetConfigName')}` | `{st_label}` | {is_err} | {err_str} |\n"
                return {"content": [{"type": "text", "text": output}]}

            elif name == "iris_get_ensemble_trace":
                session_id = arguments.get("session_id")
                try:
                    session_id_num = int(str(session_id).strip())
                except (ValueError, TypeError):
                    return {
                        "isError": True,
                        "content": [{"type": "text", "text": f"Invalid SessionId: '{session_id}'. It must be an integer."}]
                    }
                
                include_logs = arguments.get("include_logs", True)
                include_bodies = arguments.get("include_bodies", False)
                
                status_map = {1: "Created", 2: "Queued", 3: "Delivered", 4: "Discarded", 5: "Deferred", 6: "Suspended", 7: "Aborted", 9: "Completed"}
                
                # 1. Obtener mensajes del bus para la sesion
                sql_msg = "SELECT ID, TimeCreated, TimeProcessed, SourceConfigName, TargetConfigName, MessageBodyClassName, MessageBodyId, Status, IsError, Description, SuperSession FROM Ens.MessageHeader WHERE SessionId = ? ORDER BY ID ASC"
                messages = client.run_query(sql_msg, [session_id_num])
                
                # 2. Obtener logs si aplica
                logs = []
                if include_logs:
                    sql_log = "SELECT ID, TimeLogged, ConfigName, Type, Text, SourceClass, SourceMethod FROM Ens_Util.Log WHERE SessionId = ? ORDER BY ID ASC"
                    try:
                        logs = client.run_query(sql_log, [session_id_num])
                    except Exception as e:
                        log_debug(f"Error querying Ens_Util.Log for SessionId {session_id_num}: {e}")
                
                if not messages and not logs:
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"No Ensemble messages or event records were found for `SessionId` = `{session_id_num}` on server `{server_id}` ({client.namespace})."
                        }]
                    }
                
                # Construccion del Reporte
                has_errors = any(m.get("IsError") for m in messages) or any(l.get("Type") in [1, 2] for l in logs)
                status_icon = "❌ CON ERRORES" if has_errors else "✅ COMPLETED"
                
                output = f"## 🔍 Traza Visual Ensemble (SessionId: `{session_id_num}`) - {status_icon}\n\n"
                output += f"- **Servidor**: `{server_id}`\n"
                output += f"- **Namespace**: `{client.namespace}`\n"
                output += f"- **Total Mensajes**: `{len(messages)}` | **Total Event Logs**: `{len(logs)}` \n\n"
                
                if messages:
                    output += "### ✉️ Flujo de Mensajes (`Ens.MessageHeader`)\n\n"
                    output += "| Msg ID | Created | Source | Target | Body class | Body ID | State | Error? | Detail / exception |\n"
                    output += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                    
                    for m in messages:
                        m_id = m.get("ID")
                        t_created = m.get("TimeCreated") or ""
                        source = m.get("SourceConfigName") or ""
                        target = m.get("TargetConfigName") or ""
                        b_class = m.get("MessageBodyClassName") or ""
                        b_id = m.get("MessageBodyId") or ""
                        st_code = m.get("Status")
                        st_label = status_map.get(st_code, str(st_code))
                        is_err = "❌ Yes" if m.get("IsError") else "✅ No"
                        desc = m.get("Description") or ""
                        if desc and len(desc) > 60:
                            desc = desc[:57] + "..."
                        
                        b_class_short = b_class.split(".")[-1] if b_class else ""
                        
                        output += f"| `{m_id}` | `{t_created}` | `{source}` | `{target}` | `{b_class_short}` | `{b_id}` | `{st_label}` | {is_err} | {desc} |\n"
                    output += "\n"
                
                if logs:
                    output += "### 📋 Registros del Event Log (`Ens_Util.Log`)\n\n"
                    output += "| Log ID | Log time | Component config | Type | Message |\n"
                    output += "| :--- | :--- | :--- | :--- | :--- |\n"
                    log_types = {1: "Assert ❌", 2: "Error ❌", 3: "Warning ⚠️", 4: "Info ℹ️", 5: "Trace 🔍"}
                    for l in logs:
                        l_id = l.get("ID")
                        l_time = l.get("TimeLogged") or ""
                        l_cfg = l.get("ConfigName") or ""
                        l_type = log_types.get(l.get("Type"), str(l.get("Type")))
                        l_text = l.get("Text") or ""
                        if l_text and len(l_text) > 120:
                            l_text = l_text[:117] + "..."
                        output += f"| `{l_id}` | `{l_time}` | `{l_cfg}` | **{l_type}** | {l_text} |\n"
                    output += "\n"
                
                # 3. Opcional: Extraer Cuerpos de Mensaje
                if include_bodies and messages:
                    output += "### 📦 Message body inspection (`MessageBody`)\n\n"
                    fetched_bodies = 0
                    for m in messages:
                        b_class = m.get("MessageBodyClassName")
                        b_id = m.get("MessageBodyId")
                        m_id = m.get("ID")
                        if not b_class or not b_id:
                            continue
                        
                        # Mapear nombre de clase a tabla SQL de IRIS (ej: Custom.Pkg.Msg -> Custom_Pkg.Msg)
                        parts = b_class.rsplit(".", 1)
                        if len(parts) == 2:
                            sql_table = f"{parts[0].replace('.', '_')}.{parts[1]}"
                        else:
                            sql_table = b_class.replace(".", "_")
                        
                        body_rows = []
                        try:
                            q_body = f"SELECT TOP 1 * FROM {sql_table} WHERE ID = ?"
                            body_rows = client.run_query(q_body, [b_id])
                        except Exception:
                            try:
                                fallback_table = b_class.replace(".", "_")
                                q_body = f"SELECT TOP 1 * FROM {fallback_table} WHERE ID = ?"
                                body_rows = client.run_query(q_body, [b_id])
                            except Exception as e_body:
                                log_debug(f"Could not query body {b_class} ID {b_id}: {e_body}")
                        
                        if body_rows:
                            fetched_bodies += 1
                            b_data = body_rows[0]
                            output += f"#### Message `{m_id}` -> Body `{b_class}` (ID `{b_id}`)\n```json\n"
                            output += json.dumps(b_data, indent=2, ensure_ascii=False, default=str)
                            output += "\n```\n\n"
                    
                    if fetched_bodies == 0:
                        output += "*Could not extract the message bodies, or the table is not directly queryable over SQL.*\n\n"
                
                return {"content": [{"type": "text", "text": output}]}

            elif name == "iris_ensemble_production_control":
                action = arguments.get("action")
                prod_name = arguments.get("production_name")
                timeout_val = int(arguments.get("timeout", 30))
                force_val = 1 if arguments.get("force", False) else 0

                if action == "status":
                    code = 'Set sc = ##class(Ens.Director).GetProductionStatus(.pName, .pState) Write pName, "|", pState, !'
                    raw = client.execute_runtime_code(code).strip()
                    parts = raw.split("|")
                    active_name = parts[0] if parts else ""
                    state_num = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else -1
                    state_map = {0: "Stopped", 1: "Running", 2: "Suspended", 3: "Troubled (errors/partial)"}
                    state_label = state_map.get(state_num, f"Unknown ({state_num})")
                    
                    output = f"### Ensemble production status on {server_id} ({client.namespace})\n\n"
                    output += f"- **Active production**: `{active_name or 'none'}`\n"
                    output += f"- **State**: **{state_label}**\n"
                    return {"content": [{"type": "text", "text": output}]}

                allowed, reason = client.is_runtime_operation_allowed()
                if not allowed:
                    return {
                        "isError": True,
                        "content": [{
                            "type": "text",
                            "text": f"⚠️ [PRODUCTION PROTECTION] Action '{action}' denied on the production of '{server_id}' ({client.namespace}): {reason}."
                        }]
                    }

                if action == "start":
                    if not prod_name:
                        code_get = 'Set sc = ##class(Ens.Director).GetProductionStatus(.pName, .pState) Write pName, !'
                        prod_name = client.execute_runtime_code(code_get).strip()
                        if not prod_name:
                            return {"isError": True, "content": [{"type": "text", "text": "No 'production_name' was given and no production was previously recorded."}]}
                    
                    code_start = f'Set sc = ##class(Ens.Director).StartProduction("{AtelierClient._escape_cos(prod_name)}") If \'$$$ISOK(sc) {{ Write $System.Status.GetOneErrorText(sc), ! }}'
                    res_err = client.execute_runtime_code(code_start).strip()
                    if res_err:
                        return {"isError": True, "content": [{"type": "text", "text": f"Error starting production '{prod_name}': {res_err}"}]}
                    return {"content": [{"type": "text", "text": f"✅ Production '{prod_name}' started successfully on {server_id} ({client.namespace})."}]}

                elif action == "stop":
                    code_stop = f'Set sc = ##class(Ens.Director).StopProduction({timeout_val}, {force_val}) If \'$$$ISOK(sc) {{ Write $System.Status.GetOneErrorText(sc), ! }}'
                    res_err = client.execute_runtime_code(code_stop).strip()
                    if res_err:
                        return {"isError": True, "content": [{"type": "text", "text": f"Error stopping production: {res_err}"}]}
                    return {"content": [{"type": "text", "text": f"🛑 Production stopped successfully on {server_id} ({client.namespace}) (Timeout: {timeout_val}s, Force: {force_val})."}]}

                elif action == "restart":
                    code_restart = f'Set sc = ##class(Ens.Director).RestartProduction({timeout_val}, {force_val}) If \'$$$ISOK(sc) {{ Write $System.Status.GetOneErrorText(sc), ! }}'
                    res_err = client.execute_runtime_code(code_restart).strip()
                    if res_err:
                        return {"isError": True, "content": [{"type": "text", "text": f"Error restarting production: {res_err}"}]}
                    return {"content": [{"type": "text", "text": f"🔄 Production restarted successfully on {server_id} ({client.namespace})."}]}

            elif name == "iris_ensemble_component_control":
                action = arguments.get("action")
                comp_name = arguments.get("component_name")
                prod_name = arguments.get("production_name")

                if action == "list":
                    sql_items = "SELECT ID, Name, Enabled, ClassName, Category, PoolSize, Production FROM Ens_Config.Item "
                    params = []
                    if prod_name:
                        sql_items += "WHERE Production = ? "
                        params.append(prod_name)
                    sql_items += "ORDER BY Name"
                    items = client.run_query(sql_items, params)

                    output = f"### Production components on {server_id} ({client.namespace})\n\n"
                    if not items:
                        output += "No registered production components were found."
                    else:
                        output += "| Component | Enabled | Host class | Pool size | Category | Production |\n"
                        output += "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
                        for it in items:
                            en_str = "✅ Yes" if it.get("Enabled") else "❌ No"
                            output += f"| `{it.get('Name')}` | {en_str} | `{it.get('ClassName')}` | `{it.get('PoolSize')}` | `{it.get('Category') or ''}` | `{it.get('Production')}` |\n"
                    return {"content": [{"type": "text", "text": output}]}

                if not comp_name:
                    return {"isError": True, "content": [{"type": "text", "text": f"'component_name' is required for action '{action}'."}]}

                allowed, reason = client.is_runtime_operation_allowed()
                if not allowed:
                    return {
                        "isError": True,
                        "content": [{
                            "type": "text",
                            "text": f"⚠️ [PRODUCTION PROTECTION] Action '{action}' en componente '{comp_name}' denied on '{server_id}' ({client.namespace}): {reason}."
                        }]
                    }

                escaped_comp = AtelierClient._escape_cos(comp_name)
                if action == "enable":
                    code_en = f'Set sc = ##class(Ens.Director).EnableConfigItem("{escaped_comp}", 1) If \'$$$ISOK(sc) {{ Write $System.Status.GetOneErrorText(sc), ! }}'
                    res_err = client.execute_runtime_code(code_en).strip()
                    if res_err:
                        return {"isError": True, "content": [{"type": "text", "text": f"Error enabling component '{comp_name}': {res_err}"}]}
                    return {"content": [{"type": "text", "text": f"✅ Component '{comp_name}' enabled successfully on {server_id} ({client.namespace})."}]}

                elif action == "disable":
                    code_dis = f'Set sc = ##class(Ens.Director).EnableConfigItem("{escaped_comp}", 0) If \'$$$ISOK(sc) {{ Write $System.Status.GetOneErrorText(sc), ! }}'
                    res_err = client.execute_runtime_code(code_dis).strip()
                    if res_err:
                        return {"isError": True, "content": [{"type": "text", "text": f"Error disabling component '{comp_name}': {res_err}"}]}
                    return {"content": [{"type": "text", "text": f"🛑 Component '{comp_name}' disabled successfully on {server_id} ({client.namespace})."}]}

                elif action == "restart":
                    code_rst = f'Set sc = ##class(Ens.Director).EnableConfigItem("{escaped_comp}", 0) If \'$$$ISOK(sc) {{ Write "disable: ", $System.Status.GetOneErrorText(sc), ! }} Hang 0.5 Set sc2 = ##class(Ens.Director).EnableConfigItem("{escaped_comp}", 1) If \'$$$ISOK(sc2) {{ Write "enable: ", $System.Status.GetOneErrorText(sc2), ! }}'
                    res_err = client.execute_runtime_code(code_rst).strip()
                    if res_err:
                        return {"isError": True, "content": [{"type": "text", "text": f"Error restarting component '{comp_name}': {res_err}"}]}
                    return {"content": [{"type": "text", "text": f"🔄 Component '{comp_name}' restarted successfully on {server_id} ({client.namespace})."}]}

            elif name == "iris_ensemble_default_settings":
                action = arguments.get("action")
                setting_id = arguments.get("id")
                prod_name = arguments.get("production_name", "*")
                item_name = arguments.get("item_name", "*")
                host_class_name = arguments.get("host_class_name", "*")
                setting_name = arguments.get("setting_name")
                setting_val = arguments.get("setting_value")
                deployable_val = 1 if arguments.get("deployable", True) else 0

                if action == "list":
                    sql_ds = "SELECT ID, ProductionName, ItemName, HostClassName, SettingName, SettingValue, Deployable FROM Ens_Config.DefaultSettings "
                    conditions = []
                    params = []
                    if prod_name and prod_name != "*":
                        conditions.append("ProductionName = ?")
                        params.append(prod_name)
                    if item_name and item_name != "*":
                        conditions.append("ItemName = ?")
                        params.append(item_name)
                    if setting_name:
                        conditions.append("SettingName = ?")
                        params.append(setting_name)
                    
                    if conditions:
                        sql_ds += "WHERE " + " AND ".join(conditions) + " "
                    sql_ds += "ORDER BY ProductionName, ItemName, SettingName"
                    rows = client.run_query(sql_ds, params)

                    output = f"### System Default Settings en {server_id} ({client.namespace})\n\n"
                    if not rows:
                        output += "No default settings matching the filter are registered."
                    else:
                        output += "| ID | Production | Component / item | Host class | Setting | Value | Deployable |\n"
                        output += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                        for r in rows:
                            dep_str = "Sí" if r.get("Deployable") else "No"
                            output += f"| `{r.get('ID')}` | `{r.get('ProductionName')}` | `{r.get('ItemName')}` | `{r.get('HostClassName')}` | `{r.get('SettingName')}` | `{r.get('SettingValue')}` | {dep_str} |\n"
                    return {"content": [{"type": "text", "text": output}]}

                allowed, reason = client.is_runtime_operation_allowed()
                if not allowed:
                    return {
                        "isError": True,
                        "content": [{
                            "type": "text",
                            "text": f"⚠️ [PRODUCTION PROTECTION] Action '{action}' on default settings denied on '{server_id}' ({client.namespace}): {reason}."
                        }]
                    }

                if action == "set":
                    if not setting_name or setting_val is None:
                        return {"isError": True, "content": [{"type": "text", "text": "'setting_name' and 'setting_value' are required to save a default setting."}]}
                    
                    esc_p = AtelierClient._escape_cos(prod_name or "*")
                    esc_i = AtelierClient._escape_cos(item_name or "*")
                    esc_h = AtelierClient._escape_cos(host_class_name or "*")
                    esc_sn = AtelierClient._escape_cos(setting_name)
                    esc_sv = AtelierClient._escape_cos(setting_val)
                    
                    code_set = (
                        f'Set id = "{esc_p}"_"||"_"{esc_i}"_"||"_"{esc_h}"_"||"_"{esc_sn}"\n'
                        f'Set obj = ##class(Ens.Config.DefaultSettings).%OpenId(id)\n'
                        f'If \'$IsObject(obj) {{ Set obj = ##class(Ens.Config.DefaultSettings).%New() }}\n'
                        f'Set obj.ProductionName = "{esc_p}", obj.ItemName = "{esc_i}", obj.HostClassName = "{esc_h}", obj.SettingName = "{esc_sn}", obj.SettingValue = "{esc_sv}", obj.Deployable = {deployable_val}\n'
                        f'Set sc = obj.%Save()\n'
                        f'If \'$$$ISOK(sc) {{ Write $System.Status.GetOneErrorText(sc), ! }}'
                    )
                    res_err = client.execute_runtime_code(code_set).strip()
                    if res_err:
                        return {"isError": True, "content": [{"type": "text", "text": f"Error saving default setting: {res_err}"}]}
                    return {"content": [{"type": "text", "text": f"✅ Default Setting `{setting_name}` = `{setting_val}` saved successfully for item `{item_name}` on {server_id} ({client.namespace})."}]}

                elif action == "delete":
                    if not setting_id:
                        return {"isError": True, "content": [{"type": "text", "text": "'id' is required to delete a default setting."}]}
                    
                    code_del = f'Set sc = ##class(Ens.Config.DefaultSettings).%DeleteId("{AtelierClient._escape_cos(str(setting_id))}") If \'$$$ISOK(sc) {{ Write $System.Status.GetOneErrorText(sc), ! }}'
                    res_err = client.execute_runtime_code(code_del).strip()
                    if res_err:
                        return {"isError": True, "content": [{"type": "text", "text": f"Error deleting default setting ID {setting_id}: {res_err}"}]}
                    return {"content": [{"type": "text", "text": f"🗑️ Default Setting ID `{setting_id}` deleted successfully on {server_id} ({client.namespace})."}]}

            elif name == "iris_ensemble_queue_control":
                action = arguments.get("action")
                queue_name = arguments.get("queue_name")
                msg_id = arguments.get("message_id")
                new_target = arguments.get("new_target")
                limit_val = min(int(arguments.get("limit", 20)), 100)

                if action == "list_queues":
                    code_q = (
                        'Set rs = ##class(%ResultSet).%New("Ens.Queue:Enumerate")\n'
                        'Do rs.Execute()\n'
                        'While rs.Next() {\n'
                        ' Write rs.Get("Name"), "|", rs.Get("Count"), !\n'
                        '}\n'
                    )
                    raw_q = client.execute_runtime_code(code_q).strip()
                    lines = [l.strip() for l in raw_q.splitlines() if l.strip()]

                    output = f"### Ensemble queue status ({server_id} - {client.namespace})\n\n"
                    if not lines:
                        output += "No active or registered queues have pending messages."
                    else:
                        output += "| Queue name | Pending messages |\n"
                        output += "| :--- | :--- |\n"
                        for line in lines:
                            parts = line.split("|")
                            q_n = parts[0] if parts else ""
                            q_c = parts[1] if len(parts) > 1 else "0"
                            output += f"| `{q_n}` | **{q_c}** |\n"
                    return {"content": [{"type": "text", "text": output}]}

                elif action == "list_messages":
                    status_map = {1: "Created", 2: "Queued", 3: "Delivered", 4: "Discarded", 5: "Deferred", 6: "Suspended", 7: "Aborted", 9: "Completed"}
                    sql_qm = "SELECT TOP ? ID, SessionId, TimeCreated, SourceConfigName, TargetConfigName, Status, IsError, Description FROM Ens.MessageHeader "
                    params = [limit_val]
                    if queue_name:
                        sql_qm += "WHERE Status = 2 AND TargetQueueName = ? "
                        params.append(queue_name)
                    else:
                        sql_qm += "WHERE Status = 2 "
                    sql_qm += "ORDER BY ID ASC"

                    rows = client.run_query(sql_qm, params)
                    q_header = f" in queue `{queue_name}`" if queue_name else " in the system"
                    output = f"### Mensajes Encolados{q_header} ({server_id} - {client.namespace})\n\n"
                    if not rows:
                        output += "There are no queued messages right now."
                    else:
                        output += "| Msg ID | SessionId | Created | Source | Target | State | Description / error |\n"
                        output += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                        for r in rows:
                            st_code = r.get("Status")
                            st_label = status_map.get(st_code, str(st_code))
                            desc = r.get("Description") or ""
                            if len(desc) > 55: desc = desc[:52] + "..."
                            output += f"| `{r.get('ID')}` | `{r.get('SessionId')}` | `{r.get('TimeCreated')}` | `{r.get('SourceConfigName')}` | `{r.get('TargetConfigName')}` | `{st_label}` | {desc} |\n"
                    return {"content": [{"type": "text", "text": output}]}

                if not msg_id:
                    return {"isError": True, "content": [{"type": "text", "text": f"'message_id' is required for action '{action}'."}]}

                allowed, reason = client.is_runtime_operation_allowed()
                if not allowed:
                    return {
                        "isError": True,
                        "content": [{
                            "type": "text",
                            "text": f"⚠️ [PRODUCTION PROTECTION] Action '{action}' en mensaje ID '{msg_id}' denied on '{server_id}' ({client.namespace}): {reason}."
                        }]
                    }

                msg_id_num = int(str(msg_id).strip())

                if action == "resend_message":
                    # Ens.MessageHeader.Resend does not exist. The Management Portal uses
                    # ResendDuplicatedMessage(id, .newId, newTarget): it clones the header
                    # (body reused, same SessionId) and enqueues it to the original or new target.
                    esc_target = f'"{AtelierClient._escape_cos(new_target)}"' if new_target else '""'
                    code_resend = (
                        f'Set newId = "" '
                        f'Set sc = ##class(Ens.MessageHeader).ResendDuplicatedMessage({msg_id_num}, .newId, {esc_target}) '
                        f'If $System.Status.IsOK(sc) {{ Write "OK|newId=", newId, ! }} '
                        f'Else {{ Write "ERR|", $System.Status.GetOneErrorText(sc), ! }}'
                    )
                    res = client.execute_runtime_code(code_resend).strip()
                    if not res.startswith("OK|newId="):
                        return {"isError": True, "content": [{"type": "text", "text": f"Error al re-enviar mensaje ID {msg_id_num}: {res}"}]}
                    new_id = res.split("=", 1)[1].strip()
                    target_txt = f" hacia `{new_target}`" if new_target else " (destino original)"
                    return {"content": [{"type": "text", "text": f"✅ Message ID `{msg_id_num}` resent{target_txt} on {server_id} ({client.namespace}). New header: `{new_id}` (same SessionId; use iris_get_ensemble_trace to verify)."}]}

                elif action == "abort_message":
                    code_abort = (
                        f'Set header = ##class(Ens.MessageHeader).%OpenId({msg_id_num})\n'
                        f'If $IsObject(header) {{\n'
                        f'  Set header.Status = 7\n'
                        f'  Set sc = header.%Save()\n'
                        f'  Write $System.Status.GetOneErrorText(sc), !\n'
                        f'}} Else {{\n'
                        f'  Write "Mensaje ID {msg_id_num} no existe", !\n'
                        f'}}\n'
                    )
                    res_err = client.execute_runtime_code(code_abort).strip()
                    if res_err:
                        return {"isError": True, "content": [{"type": "text", "text": f"Error al abortar/desencolar mensaje ID {msg_id_num}: {res_err}"}]}
                    return {"content": [{"type": "text", "text": f"🛑 Message ID `{msg_id_num}` dequeued/aborted successfully on {server_id} ({client.namespace})."}]}

            elif name == "iris_execute_objectscript":
                code = arguments.get("code")
                res = client._execute_direct_objectscript(code)
                return {"content": [{"type": "text", "text": res}]}

            elif name == "iris_dump_global":
                global_name = arguments.get("global_name")
                subscript = arguments.get("subscript")
                start_from = arguments.get("start_from")
                max_nodes = arguments.get("max_nodes", 200)
                
                res = client.dump_global(
                    global_name=global_name,
                    subscript=subscript,
                    start_from=start_from,
                    max_nodes=max_nodes
                )
                return {"content": [{"type": "text", "text": res}]}

            elif name == "iris_ccr_status":
                class_name = arguments["_document"]
                status = client.get_ccr_status(document_name=class_name)
                
                output = f"### Change-control (CCR) status of `{class_name}` en {server_id}\n\n"
                if not status.get("has_sc"):
                    output += "❌ No source-code control is configured in this namespace."
                elif not status.get("instantiated"):
                    output += "❌ Source-code control is active but instantiating the extension failed."
                else:
                    output += f"- **Pertenece a SC / CCR**: {'Sí' if status.get('in_sc') else 'No'}\n"
                    output += f"- **Editable**: {'Sí' if status.get('editable') else 'No'}\n"
                    output += f"- **In checkout**: {'Sí' if status.get('checked_out') else 'No'}\n"
                    if status.get("user"):
                        output += f"- **Checkout held by**: `{status.get('user')}`\n"
                return {"content": [{"type": "text", "text": output}]}
                
            elif name == "iris_ccr_checkout":
                class_name = arguments["_document"]
                ccr_id = arguments.get("ccr_id") or server_config.get("ccr_id") or os.environ.get("IRIS_CCR_ID")
                if not ccr_id:
                    raise ValueError(
                        "The 'ccr_id' parameter (Perforce Job) is required to perform the checkout. "
                        "Pass it in the tool call, set it in the server "
                        "configuration ('config add-server') under the 'ccr_id' key for this server, "
                        "or define the `IRIS_CCR_ID` environment variable."
                    )
                success, msg = client.ccr_checkout(None, ccr_id, document_name=class_name)
                
                output = f"### Solicitud de Checkout en {server_id}\n"
                output += f"- **Documento**: `{class_name}`\n"
                output += f"- **CCR / description**: `{ccr_id}`\n"
                if success:
                    output += f"\n✅ **Success**: {msg}"
                else:
                    output += f"\n❌ **Fallo**: {msg}"
                return {"isError": not success, "content": [{"type": "text", "text": output}]}
                
            elif name == "iris_ccr_undocheckout":
                class_name = arguments["_document"]
                ccr_id = arguments.get("ccr_id") or server_config.get("ccr_id") or os.environ.get("IRIS_CCR_ID")
                if not ccr_id:
                    raise ValueError(
                        "The 'ccr_id' parameter is required for UndoCheckout: only the pending row that "
                        "belongs to exactly that CCR is reverted. Pass it, set it in the server "
                        "configuration ('config add-server') under 'ccr_id', or define IRIS_CCR_ID."
                    )
                success, msg = client.ccr_undocheckout(None, ccr_id, document_name=class_name)
                
                output = f"### Solicitud de UndoCheckout en {server_id}\n"
                output += f"- **Documento**: `{class_name}`\n"
                output += f"- **CCR**: `{ccr_id}`\n"
                if success:
                    output += f"\n✅ **Success**: {msg}"
                else:
                    output += f"\n❌ **Fallo**: {msg}"
                return {"isError": not success, "content": [{"type": "text", "text": output}]}

            elif name == "iris_ccr_checkout_ct":
                ct_class = arguments.get("class_name")
                ids = arguments.get("ids", [])
                if not ids:
                    return {"isError": True, "content": [{"type": "text", "text": "At least one ID is required in 'ids'."}]}
                ccr_id = arguments.get("ccr_id") or server_config.get("ccr_id") or os.environ.get("IRIS_CCR_ID")
                if not ccr_id or not str(ccr_id).isascii():
                    raise ValueError(
                        "The 'ccr_id' parameter is required to register CT objects under an explicit CCR. "
                        "Pass it, set it in the server configuration ('config add-server') under 'ccr_id', or define IRIS_CCR_ID."
                    )
                
                # TC.ChangeControl.CCR.CheckOut for each record, tracked under the explicit
                # CCR session key. Runs inside the same-process guarded runner.
                lines = [
                    f'Set requestedCCR = "{AtelierClient._escape_cos(ccr_id)}"',
                    'Set %ccrSession = requestedCCR',
                    'Set cc = $ClassMethod("TC.ChangeControl.CCR", "%New", $lb($username))',
                    'Set okCnt = 0, errCnt = 0, errMsgs = ""',
                    'If \'$IsObject(cc) {',
                    '    Write "ERROR_CT_CHECKOUT|message=Failed to instantiate TC.ChangeControl.CCR", !',
                    '    Quit',
                    '}',
                ]
                for rid in ids:
                    rid_safe = AtelierClient._escape_cos(rid)
                    lines.append(f'Set sc = cc.CheckOut($lb("{rid_safe}","{ct_class}"))')
                    lines.append('If $$$ISERR(sc) { Set errCnt=errCnt+1, errMsgs=errMsgs_$System.Status.GetErrorText(sc)_$c(10) } Else { Set okCnt=okCnt+1 }')
                lines.append('Write "OK:"_okCnt_"|ERR:"_errCnt_"|MSGS:"_errMsgs')
                
                code = "\n".join(lines)
                log_debug(f"Running CT checkout for {len(ids)} items of {ct_class}...")
                result_text = client._run_guarded_ccr_runner(code, str(ccr_id))
                stripped = str(result_text).strip()
                if not stripped.startswith("OK:"):
                    return {"isError": True, "content": [{"type": "text", "text": (
                        f"### CheckOut CT en {server_id} ({client.namespace})\n\n❌ **Fallo**: {stripped or 'no result'}"
                    )}]}
                
                # Parse result
                parts = stripped.split("|", 2)
                ok_count = parts[0].replace("OK:", "") if len(parts) > 0 else "?"
                err_count = parts[1].replace("ERR:", "") if len(parts) > 1 else "?"
                err_msgs = parts[2].replace("MSGS:", "") if len(parts) > 2 else ""
                
                output = f"### CheckOut CT en {server_id} ({client.namespace})\n\n"
                output += f"- **Class**: `{ct_class}`\n"
                output += f"- **CCR**: `{ccr_id}`\n"
                output += f"- **Registros procesados**: {len(ids)}\n"
                output += f"- **Exitosos**: {ok_count}\n"
                output += f"- **Errors**: {err_count}\n"
                if err_msgs.strip():
                    output += f"\n**Errores:**\n```\n{err_msgs.strip()}\n```\n"
                
                return {"content": [{"type": "text", "text": output}]}

            elif name == "iris_ccr_namespace_status":
                status = self.resolve_ccr_affected(server_id, client.namespace, client)
                output = (
                    f"CCR {status['applicability']} ({status['discovery_status']}) — {status['namespace']}\n"
                    f"{status['reason']}\n"
                    "Suspension is process-local and was not observed.\n"
                    "Informational only: code-write authorization uses the same-process CCR guard "
                    "(CONTROLLED_ACTIVE / SUSPENDED / ABSENT), not this discovery record.\n"
                )
                # JSON text retains compatibility with older MCP consumers; the
                # structured record preserves bool/null semantics for newer ones.
                return {"structuredContent": status, "content": [
                    {"type": "text", "text": output + json.dumps(status, ensure_ascii=False)}
                ]}

            elif name == "iris_list_namespaces":
                namespaces = client.list_namespaces()
                output = f"### Namespaces available on `{server_id}`:\n\n"
                if not namespaces:
                    output += "Could not retrieve the namespaces, or the list is empty."
                else:
                    for ns in namespaces:
                        output += f"- `{ns}`\n"
                return {"content": [{"type": "text", "text": output}]}

            elif name == "iris_unlock_questionnaire":
                win_code = arguments.get("win_code")
                if not win_code:
                    raise ValueError("The 'win_code' parameter is required.")
                
                queries = [
                    ("UPDATE %notrigger SQLUser.SS_UserDefWindowControls SET CON_Generated='N' WHERE CON_ParREf->WIN_Code=?", [win_code]),
                    ("UPDATE %notrigger SQLUser.SS_UserDefWindowControls SET CON_AdminAccessRequired='N' WHERE CON_ParREf->WIN_Code=?", [win_code]),
                    ("UPDATE %notrigger SQLUser.SS_UserDefWinContResp SET RESP_Generated='N' WHERE RESP_ParRef->CON_ParRef->WIN_Code=?", [win_code]),
                    ("UPDATE %notrigger SS_UserDefWindow SET WIN_GenerateError = null WHERE WIN_Code=?", [win_code])
                ]
                
                results_info = []
                for idx, (q, params) in enumerate(queries, 1):
                    try:
                        client.run_write_sql(q, params)
                        results_info.append(f"Query {idx}: OK")
                    except LocalPolicyError as qe:
                        results_info.append(f"Query {idx}: Denied ({qe})")
                    except Exception:
                        results_info.append(f"Query {idx}: Error (details withheld)")
                        
                output = f"### Questionnaire unlock `{win_code}` on {server_id} ({client.namespace})\n\n"
                output += "\n".join([f"- {res}" for res in results_info])
                is_error = not all(res.endswith(": OK") for res in results_info)
                return {"isError": is_error, "content": [{"type": "text", "text": output}]}

            elif name == "iris_ccr_commit":
                ccr_id = arguments.get("ccr_id") or server_config.get("ccr_id") or os.environ.get("IRIS_CCR_ID")
                upload_token = arguments.get("upload_token") or server_config.get("upload_token") or os.environ.get("IRIS_CCR_TOKEN")
                
                if not ccr_id:
                    raise ValueError(
                        "The 'ccr_id' parameter (Perforce Job) is required to perform the commit. "
                        "Pass it in the tool call, set it in the server "
                        "configuration ('config add-server') under the 'ccr_id' key for this server, "
                        "or define the `IRIS_CCR_ID` environment variable."
                    )
                if not upload_token:
                    raise ValueError(
                        "The 'upload_token' parameter is required to perform the commit. "
                        "Pass it in the tool call, set it in the server "
                        "configuration ('config add-server') under the 'upload_token' key for this server, "
                        "or define the `IRIS_CCR_TOKEN` environment variable."
                    )
                
                change_ids = arguments.get("change_ids")
                if not isinstance(change_ids, list) or not change_ids:
                    raise ValueError("The 'change_ids' parameter must be a non-empty list of integers.")
                
                log_debug(f"Triggering automated CCR Commit for Job {ccr_id} with {len(change_ids)} change IDs...")
                try:
                    result = client.ccr_commit(ccr_id, change_ids, upload_token)
                except Exception as exc:
                    raise RuntimeError(AtelierClient._redact_secret(exc, upload_token)) from None
                result = AtelierClient._redact_secret(result, upload_token)
                status, fields, detail = _classify_ccr_terminal_result(
                    result,
                    {"commit"},
                )
                output = f"### CCR commit `{ccr_id}` on {server_id} ({client.namespace})\n\n"
                if status == "verified":
                    output += (
                        "✅ **VERIFIED**: the local ItemSet appears by exact name on the central server.\n\n"
                        f"- **Local ItemSet**: `{fields['local_id']}` (`{fields['local_name']}`)\n"
                        f"- **ItemSet central**: `{fields['server_id']}` (`{fields['server_name']}`)\n"
                        f"- **Action**: `{fields['action']}`"
                    )
                    return {"content": [{"type": "text", "text": output}]}

                if status == "pending":
                    output += (
                        "⚠️ **PENDING_VERIFICATION**: WSUpload replied OK, but the ItemSet is "
                        "not visible on the central server yet.\n\n"
                        f"- **Local ItemSet**: `{fields['local_id']}` (`{fields['local_name']}`)\n"
                        "- **DO NOT RETRY AUTOMATICALLY**: another upload could create a duplicate. "
                        "Verify visibility manually before any retry."
                    )
                    return {"isError": True, "content": [{"type": "text", "text": output}]}

                output += f"❌ **ERROR**: {detail}"
                return {"isError": True, "content": [{"type": "text", "text": output}]}

            elif name == "iris_ccr_list_uncommitted":
                ccr_filter = arguments.get("ccr_id") or server_config.get("ccr_id") or os.environ.get("IRIS_CCR_ID")
                limit = arguments.get("limit", 200)
                rows = client.list_uncommitted_changes(ccr=ccr_filter, limit=limit)

                header = f"### Uncommitted changes on {server_id} ({client.namespace})"
                if ccr_filter:
                    header += f" — filtered by CCR `{ccr_filter}`"
                output = header + "\n\n"
                if not rows:
                    output += "There are no uncommitted changes"
                    if ccr_filter:
                        output += f" for CCR `{ccr_filter}`"
                    output += "."
                    return {"content": [{"type": "text", "text": output}]}

                id_list = [r.get("ID") for r in rows if r.get("ID") not in (None, "")]
                output += "| change_id | Document | Action | CCR | Source |\n"
                output += "| :--- | :--- | :--- | :--- | :--- |\n"
                for r in rows:
                    output += (
                        f"| {r.get('ID','')} | {r.get('Name','')} | {r.get('Action','')} "
                        f"| {r.get('CCR','')} | {r.get('Source','')} |\n"
                    )
                output += f"\n**change_ids** (for `iris_ccr_commit`): `{id_list}`"
                return {"content": [{"type": "text", "text": output}]}

            elif name == "iris_ccr_reupload_itemset":
                ccr_id = arguments.get("ccr_id") or server_config.get("ccr_id") or os.environ.get("IRIS_CCR_ID")
                upload_token = arguments.get("upload_token") or server_config.get("upload_token") or os.environ.get("IRIS_CCR_TOKEN")
                itemset_id = arguments.get("itemset_id")

                if itemset_id in (None, ""):
                    raise ValueError("The 'itemset_id' parameter (local ItemSet ID) is required.")
                if not ccr_id:
                    raise ValueError(
                        "The 'ccr_id' parameter (Perforce Job) is required. Pass it, set it "
                        "in the server configuration ('config add-server') under 'ccr_id', or define IRIS_CCR_ID."
                    )
                if not upload_token:
                    raise ValueError(
                        "The 'upload_token' parameter is required. Pass it, set it "
                        "in the server configuration ('config add-server') under 'upload_token', or define IRIS_CCR_TOKEN."
                    )

                sanitize = arguments.get("sanitize", True)
                log_debug(f"Re-uploading ItemSet {itemset_id} for CCR {ccr_id} (sanitize={sanitize})...")
                try:
                    result = client.ccr_reupload_itemset(itemset_id, ccr_id, upload_token, sanitize=sanitize)
                except Exception as exc:
                    raise RuntimeError(AtelierClient._redact_secret(exc, upload_token)) from None
                result = AtelierClient._redact_secret(result, upload_token)

                output = f"### ItemSet re-upload `{itemset_id}` to CCR `{ccr_id}` on {server_id}\n\n"
                status, fields, detail = _classify_ccr_terminal_result(
                    result,
                    {"already_present", "uploaded"},
                )
                if status == "verified":
                    output += (
                        "✅ **VERIFIED**: the local ItemSet appears by exact name on the central server.\n\n"
                        f"- **Local ItemSet**: `{fields['local_id']}` (`{fields['local_name']}`)\n"
                        f"- **ItemSet central**: `{fields['server_id']}` (`{fields['server_name']}`)\n"
                        f"- **Action**: `{fields['action']}`\n"
                        f"- **Non-ASCII bytes removed**: `{fields.get('bytes_removed', '0')}`\n"
                        f"- **Copias rollback preservadas**: `{fields.get('rollback_copies', '0')}`"
                    )
                    return {"content": [{"type": "text", "text": output}]}

                if status == "pending":
                    output += (
                        "⚠️ **PENDING_VERIFICATION**: WSUpload replied OK, but the ItemSet is "
                        "not visible on the central server yet.\n\n"
                        f"- **Local ItemSet**: `{fields['local_id']}` (`{fields['local_name']}`)\n"
                        "- **DO NOT RETRY AUTOMATICALLY**: another upload could create a duplicate. "
                        "Verify visibility manually before any retry."
                    )
                    return {"isError": True, "content": [{"type": "text", "text": output}]}

                output += f"❌ **ERROR**: {detail}"
                return {"isError": True, "content": [{"type": "text", "text": output}]}

            elif name == "iris_ccr_create":
                title = arguments.get("title")
                description = arguments.get("description")
                if not title:
                    raise ValueError("The 'title' parameter is required to create a CCR.")
                if not description:
                    raise ValueError("The 'description' parameter is required to create a CCR.")
                ccr_type = arguments.get("ccr_type", "")
                ccr_info = arguments.get("ccr_info", "")
                site_code = arguments.get("site_code")
                sys_code = arguments.get("sys_code")
                log_debug(f"Creating new CCR '{title}' on central server via WSCreate...")
                result = client.ccr_create(title, description, ccr_type, ccr_info, site_code, sys_code)

                output = f"### CCR creation on {server_id} ({client.namespace})\n\n"
                if result.startswith("SUCCESS"):
                    fields = dict(p.split("=", 1) for p in result.split("|")[1:] if "=" in p)
                    output += (
                        f"✅ **CCR created**: `{fields.get('ccr_id','?')}`\n\n"
                        f"- **Title**: {title}\n"
                        f"- **Site/Sys**: {fields.get('site','?')}@{fields.get('sys','?')}"
                    )
                else:
                    output += f"❌ **Fallo**: {result}"
                return {"content": [{"type": "text", "text": output}]}

            elif name == "iris_ccr_list_itemsets":
                env = arguments.get("env")
                token = arguments.get("token") or server_config.get("upload_token") or os.environ.get("IRIS_CCR_TOKEN", "")
                try:
                    result = client.ccr_list_itemsets(list_itemsets_ccr_id, env=env, token=token)
                except Exception as exc:
                    raise RuntimeError(AtelierClient._redact_secret(exc, token)) from None
                result = AtelierClient._redact_secret(result, token)

                output = f"### ItemSets available on the central server for CCR `{list_itemsets_ccr_id}` on {server_id} ({client.namespace})\n\n"
                try:
                    header, items = _parse_ccr_list_result(result)
                    if header["ccr"] != str(list_itemsets_ccr_id):
                        raise ValueError("The CCR of the OK record does not match the requested one")
                except (UnicodeDecodeError, ValueError) as exc:
                    output += f"❌ **ERROR**: invalid WSList output: {exc}"
                    return {"isError": True, "content": [{"type": "text", "text": output}]}

                if not items:
                    output += "No ItemSets are available on the central server for this environment."
                else:
                    output += "| ServerItemSetID | Name | Required window | Implementation Plan |\n"
                    output += "| :--- | :--- | :--- | :--- |\n"
                    for item in items:
                        output += (
                            f"| {_markdown_table_cell(item['id'])} | {_markdown_table_cell(item['name'])} "
                            f"| {_markdown_table_cell(item['window'])} | {_markdown_table_cell(item['plan'])} |\n"
                        )
                return {"content": [{"type": "text", "text": output}]}

            elif name == "iris_ccr_can_upload":
                ccr_id = arguments.get("ccr_id") or server_config.get("ccr_id") or os.environ.get("IRIS_CCR_ID")
                if not ccr_id:
                    raise ValueError(
                        "The 'ccr_id' parameter is required. Pass it, set it in the server "
                        "configuration ('config add-server') under 'ccr_id', or define IRIS_CCR_ID."
                    )
                org = arguments.get("org")
                system = arguments.get("system")
                result = client.ccr_can_upload(ccr_id, org=org, system=system)

                output = f"### Upload pre-check (CanUpload) for CCR `{ccr_id}` on {server_id}\n\n"
                if result.startswith("CANUPLOAD|"):
                    f = dict(p.split("=", 1) for p in result.split("|")[1:] if "=" in p)
                    can = f.get("value", "0") == "1"
                    icon = "✅" if can else "🚫"
                    output += f"{icon} **{'Upload allowed' if can else 'Upload not allowed'}**\n\n- **Reason**: {f.get('reason','')}"
                else:
                    output += f"❌ **Fallo**: {result}"
                return {"content": [{"type": "text", "text": output}]}

            elif name == "iris_ccr_download_load":
                itemset_id = arguments.get("itemset_id")
                if itemset_id in (None, ""):
                    raise ValueError("The 'itemset_id' parameter (ServerItemSetID) is required.")
                load = arguments.get("load", True)
                verify_documents = arguments.get("verify_documents")
                token = arguments.get("token") or server_config.get("upload_token") or os.environ.get("IRIS_CCR_TOKEN", "")
                before = {}
                if verify_documents:
                    unreadable = []
                    for document in verify_documents:
                        try:
                            before[document] = AtelierClient._document_change_snapshot(
                                document, client.get_document(document, strict=True),
                            )
                        except Exception:
                            unreadable.append(document)
                    if unreadable:
                        verification = {
                            "status": "PRE_LOAD_VERIFICATION_FAILED",
                            "itemset_id": itemset_id,
                            "changed": [],
                            "unchanged": [],
                            "unreadable": unreadable,
                            "load_attempted": False,
                            "automatic_retry": False,
                        }
                        output = (
                            f"### CCR promotion: ItemSet `{itemset_id}` on {server_id} ({client.namespace})\n\n"
                            "❌ **PRE_LOAD_VERIFICATION_FAILED**: could not take every prior snapshot"
                            "; the download/load never started.\n\n"
                            f"- **Unreadable**: {', '.join(f'`{name}`' for name in unreadable)}\n"
                            "- **NO automatic RETRY**."
                        )
                        return {"isError": True, "structuredContent": verification,
                                "content": [{"type": "text", "text": output}]}
                log_debug(f"CCR promotion: download+load ItemSet {itemset_id} (load={load}) in {client.namespace}...")
                output = f"### CCR promotion: ItemSet `{itemset_id}` on {server_id} ({client.namespace})\n\n"
                try:
                    result = client.ccr_download_load(itemset_id, load=load, token=token, ccr_id=arguments.get("ccr_id") or "")
                except Exception:
                    failure_state = {
                        "status": "DOWNLOAD_LOAD_FAILED",
                        "itemset_id": itemset_id,
                        "load_attempted": False,
                        "load_may_have_started": bool(load),
                        "phase_reached": None,
                        "failed_phase": "unknown",
                        "possible_partial_load": bool(load),
                        "automatic_retry": False,
                    }
                    output += "❌ **DOWNLOAD_LOAD_FAILED**: transport or protocol failure; phase unknown."
                    if load:
                        output += "\n\n⚠️ **Possible partial load**. **NO automatic RETRY**."
                    return {"isError": True, "structuredContent": failure_state,
                            "content": [{"type": "text", "text": output}]}

                success, lines, failure, failure_state = _classify_ccr_download_load_result(
                    result, load, itemset_id,
                )
                detail = "\n".join(f"- {ln}" for ln in lines if ln != "SUCCESS")
                if not success:
                    output += f"❌ **DOWNLOAD_LOAD_FAILED**: {failure}\n\n{detail}"
                    response = {"isError": True, "content": [{"type": "text", "text": output}]}
                    if failure_state is not None:
                        failure_state["itemset_id"] = itemset_id
                        if failure_state["possible_partial_load"]:
                            output += (
                                "\n\n⚠️ **Possible partial load**: there is evidence that LoadToOS "
                                "was attempted or completed. **NO automatic RETRY**."
                            )
                            response["content"][0]["text"] = output
                        response["structuredContent"] = failure_state
                    return response

                feature_record = None
                if load and arguments.get("feature_check", True):
                    feature_record = self._post_load_feature_check(client, lines)

                def attach_feature_check(response):
                    if feature_record is None:
                        return response
                    response["structuredContent"]["feature_check"] = feature_record
                    response["content"][0]["text"] += "\n\n" + feature_check.render_markdown(feature_record)
                    return response

                if not verify_documents:
                    verification = {
                        "status": "LOAD_REPORTED_SUCCESS_UNVERIFIED",
                        "itemset_id": itemset_id,
                        "load": load,
                        "load_attempted": load,
                        "verification_requested": False,
                    }
                    if load:
                        explanation = "Only the %Status values reported by LoadToOS/LoadToNS were validated; no documents were verified."
                    else:
                        explanation = "Only the reported download/import result was validated; the load did not run and no documents were verified."
                    output += (
                        "✅ **LOAD_REPORTED_SUCCESS_UNVERIFIED**\n\n"
                        f"{explanation}\n\n{detail}"
                    )
                    return attach_feature_check({"structuredContent": verification,
                                                 "content": [{"type": "text", "text": output}]})

                changed, unchanged, unreadable = [], [], []
                for document in verify_documents:
                    try:
                        after = AtelierClient._document_change_snapshot(
                            document, client.get_document(document, strict=True),
                        )
                    except Exception:
                        unreadable.append(document)
                        continue
                    (changed if before[document] != after else unchanged).append(document)

                if unchanged or unreadable:
                    verification = {
                        "status": "POST_LOAD_VERIFICATION_FAILED",
                        "itemset_id": itemset_id,
                        "changed": changed,
                        "unchanged": unchanged,
                        "unreadable": unreadable,
                        "load_attempted": True,
                        "automatic_retry": False,
                    }
                    output += (
                        "❌ **POST_LOAD_VERIFICATION_FAILED**: possible partial load; at least one document "
                        "was left unchanged or could not be reread.\n\n"
                        f"- **Changed**: {', '.join(f'`{name}`' for name in changed) or 'ninguno'}\n"
                        f"- **Unchanged**: {', '.join(f'`{name}`' for name in unchanged) or 'ninguno'}\n"
                        f"- **Unreadable**: {', '.join(f'`{name}`' for name in unreadable) or 'ninguno'}\n"
                        "- **NO automatic RETRY**."
                    )
                    return attach_feature_check({"isError": True, "structuredContent": verification,
                                                 "content": [{"type": "text", "text": output}]})

                verification = {
                    "status": "POST_LOAD_CHANGE_OBSERVED",
                    "itemset_id": itemset_id,
                    "changed": changed,
                    "unchanged": [],
                    "unreadable": [],
                    "load_attempted": True,
                    "proves_payload_equality": False,
                }
                output += (
                    "✅ **POST_LOAD_CHANGE_OBSERVED**: every requested document changed relative to "
                    "the previous UAT snapshot. This observes change, but does NOT prove equality against "
                    "BASE or against the ItemSet payload.\n\n"
                    f"- **Changed**: {', '.join(f'`{name}`' for name in changed)}"
                )
                return attach_feature_check({"structuredContent": verification,
                                             "content": [{"type": "text", "text": output}]})

            else:
                raise ValueError(f"Unknown tool: {name}")
                
        except Exception as e:
            error_text = str(e) if isinstance(e, LocalPolicyError) else "Tool execution failed; details <redacted>."
            log_debug("Tool call failed; arguments, server details and traceback withheld")
            return {
                "isError": True,
                "content": [{"type": "text", "text": error_text}]
            }

    def run(self):
        log_debug("InterSystems IRIS MCP Server started running...")
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                
                request = json.loads(line)
                method = request.get("method")
                req_id = request.get("id")
                
                if method == "initialize":
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "protocolVersion": PROTOCOL_VERSION,
                            "capabilities": {
                                "tools": {}
                            },
                            "serverInfo": {
                                "name": "intersystems-iris-mcp",
                                "version": __version__
                            }
                        }
                    }
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
                    log_debug("Handshake completed successfully!")
                    
                elif method == "tools/list":
                    res = self.handle_tools_list()
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": res
                    }
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
                    
                elif method == "tools/call":
                    params = request.get("params", {})
                    t_name = params.get("name")
                    t_args = params.get("arguments", {})
                    
                    res = self.handle_tool_call(t_name, t_args)
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": res
                    }
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
                    
                elif method == "ping":
                    if req_id is not None:
                        response = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {}
                        }
                        sys.stdout.write(json.dumps(response) + "\n")
                        sys.stdout.flush()

                else:
                    if req_id is not None:
                        response = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32601,
                                "message": f"Method not found: {method}"
                            }
                        }
                        sys.stdout.write(json.dumps(response) + "\n")
                        sys.stdout.flush()
                        
            except json.JSONDecodeError:
                log_debug("Error decoding incoming JSON-RPC request.")
            except Exception as e:
                log_debug(f"Main loop error (continuing): {e}")
                continue
