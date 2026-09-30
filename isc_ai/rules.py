"""isc-ai.rules.json: explicit, versioned package grants and capability
exceptions (project root, sibling to the state directory).

One new module: the schema, its parsing/validation and its query helpers.
Reuses isc_ai.config's private validation helpers instead of duplicating
them; forbidden package roots and the mutation-facility guard come from
there so this file and config.py can never silently diverge.

This module performs no I/O at import time; `load_rules`/`load_rules_data`
are the only functions that touch the filesystem, and only when called.
"""

import datetime
import re
from dataclasses import dataclass
from pathlib import Path

from isc_ai import config as isc_config
from isc_ai.ccr_discovery import LocalPolicyError, validate_namespace
from isc_ai.config import ConfigError

RULES_FILENAME = "isc-ai.rules.json"
RULES_VERSION = 1

EXCEPTION_CAPABILITIES = frozenset({"code_write", "ensemble_control", "runner", "read"})

_RULES_TOP_LEVEL_KEYS = frozenset({"version", "packages", "exceptions", "$schema", "_comment"})
_PACKAGE_GRANT_KEYS = frozenset({"prefix", "class", "document", "roles", "servers", "expires"})
_EXCEPTION_REQUIRED_KEYS = frozenset({"id", "label", "ticket", "expires", "capability"})
_EXCEPTION_OPTIONAL_KEYS = frozenset({"roles", "servers", "hosts", "namespaces", "path_prefixes",
                                      "requires_active_node", "active_node_evidence_namespace"})
_EXCEPTION_ALLOWED_KEYS = _EXCEPTION_REQUIRED_KEYS | _EXCEPTION_OPTIONAL_KEYS

_expect = isc_config._expect


# ---------------------------------------------------------------------------
# Expiry (shared shape between package grants and exceptions)
# ---------------------------------------------------------------------------

def _parse_expiry_date(expires):
    if not isinstance(expires, str) or not isc_config._EXPIRES_RE.fullmatch(expires):
        return None
    try:
        return datetime.date.fromisoformat(expires)
    except ValueError:
        return None


def _is_expired(expires, today=None):
    """None means permanent (never expires). An unparsable date is fail-closed:
    treated as already expired/inert rather than crashing the caller."""
    if expires is None:
        return False
    date = _parse_expiry_date(expires)
    if date is None:
        return True
    return (today or datetime.date.today()) > date


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackageRule:
    """Exactly one of prefix / class_name / document is set. roles/servers
    None means "any"; expires None means permanent (matches PackageGrant's
    contract in config.py, plus optional servers scoping and expiry)."""
    prefix: str = None
    class_name: str = None
    document: str = None
    roles: frozenset = None
    servers: frozenset = None
    expires: str = None

    def allows(self, document_name, stem, role, server=None, today=None):
        if _is_expired(self.expires, today):
            return False
        if self.roles is not None and role not in self.roles:
            return False
        if self.servers is not None and server not in self.servers:
            return False
        if self.prefix is not None:
            return re.fullmatch(re.escape(self.prefix) + r"\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", stem) is not None
        if self.class_name is not None:
            return stem == self.class_name
        return document_name == self.document


@dataclass(frozen=True)
class ExceptionRule:
    """A capability exception: mandatory ticket/expiry, optional scope."""
    id: str
    label: str
    ticket: str
    expires: str
    capability: str
    roles: frozenset = None
    servers: frozenset = None
    hosts: frozenset = None
    namespaces: frozenset = None
    path_prefixes: frozenset = None
    requires_active_node: bool = True
    active_node_evidence_namespace: str = None

    def is_expired(self, today=None):
        return _is_expired(self.expires, today)


@dataclass(frozen=True)
class RuleSet:
    packages: dict          # namespace -> tuple[PackageRule, ...]
    exceptions: tuple       # tuple[ExceptionRule, ...]


# ---------------------------------------------------------------------------
# Parsing / validation
# ---------------------------------------------------------------------------

def _substitute_site(prefix, site_prefix, path):
    if "{SITE}" not in prefix:
        return prefix
    _expect(isinstance(site_prefix, str) and site_prefix,
            f"{path}: uses {{SITE}} but no site.ccr_id.prefix is configured.")
    return prefix.replace("{SITE}", site_prefix)


def _namespace_value(value, where):
    try:
        return validate_namespace(value)
    except LocalPolicyError:
        raise ConfigError(f"{where} must be a plain namespace identifier.") from None


def _parse_package_grant(grant, path, site_prefix):
    _expect(isinstance(grant, dict), f"{path} must be an object.")
    unknown = set(grant) - _PACKAGE_GRANT_KEYS
    _expect(not unknown, f"Unknown keys in {path}: {sorted(unknown)}.")
    kinds = [key for key in ("prefix", "class", "document") if key in grant]
    _expect(len(kinds) == 1, f"{path} must set exactly one of prefix, class or document.")
    roles = None
    if "roles" in grant:
        roles = frozenset(isc_config._string_list(grant["roles"], f"{path}.roles", isc_config._ROLE_RE, upper=True))
        _expect(roles, f"{path}.roles must not be empty.")
    servers = None
    if "servers" in grant:
        servers = frozenset(isc_config._string_list(grant["servers"], f"{path}.servers"))
        _expect(servers, f"{path}.servers must not be empty.")
    expires = None
    if "expires" in grant:
        expires = isc_config._string(grant["expires"], f"{path}.expires")
    if kinds[0] == "prefix":
        prefix = isc_config._string(grant["prefix"], f"{path}.prefix", allow_empty=True)
        prefix = _substitute_site(prefix, site_prefix, f"{path}.prefix")
        prefix = isc_config._validate_package_prefix(prefix, f"{path}.prefix")
        return PackageRule(prefix=prefix, roles=roles, servers=servers, expires=expires)
    if kinds[0] == "class":
        name = isc_config._string(grant["class"], f"{path}.class", isc_config._PACKAGE_PREFIX_RE)
        _expect(not isc_config._is_mutation_facility_stem(name),
                f"{path}.class: the facility class can never be granted.")
        return PackageRule(class_name=name, roles=roles, servers=servers, expires=expires)
    document = isc_config._string(grant["document"], f"{path}.document")
    _expect("." in document and isc_config._PACKAGE_PREFIX_RE.fullmatch(document.rsplit(".", 1)[0]) is not None,
            f"{path}.document must be a document name with extension.")
    _expect(not isc_config._is_mutation_facility_stem(document.rsplit(".", 1)[0]),
            f"{path}.document: the facility class can never be granted.")
    return PackageRule(document=document, roles=roles, servers=servers, expires=expires)


def _parse_packages(data, site_prefix):
    _expect(isinstance(data, dict), "packages must be an object keyed by namespace.")
    parsed = {}
    for namespace, grants in data.items():
        isc_config._string(namespace, "packages key", isc_config._NAMESPACE_RE)
        _expect(isinstance(grants, list), f"packages.{namespace} must be a list of grants.")
        items = tuple(
            _parse_package_grant(grant, f"packages.{namespace}[{index}]", site_prefix)
            for index, grant in enumerate(grants)
        )
        parsed[namespace] = items
    return parsed


def _parse_exception(item, index):
    path = f"exceptions[{index}]"
    _expect(isinstance(item, dict), f"{path} must be an object.")
    unknown = set(item) - _EXCEPTION_ALLOWED_KEYS
    _expect(not unknown, f"Unknown keys in {path}: {sorted(unknown)}.")
    missing = _EXCEPTION_REQUIRED_KEYS - set(item)
    _expect(not missing, f"{path} is missing required keys: {sorted(missing)}.")
    id_ = isc_config._string(item["id"], f"{path}.id")
    label = isc_config._string(item["label"], f"{path}.label")
    ticket = isc_config._string(item["ticket"], f"{path}.ticket")
    expires = isc_config._string(item["expires"], f"{path}.expires")
    capability = isc_config._string(item["capability"], f"{path}.capability")
    _expect(capability in EXCEPTION_CAPABILITIES,
            f"{path}.capability must be one of {sorted(EXCEPTION_CAPABILITIES)}; got {capability!r}.")
    roles = None
    if "roles" in item:
        roles = frozenset(isc_config._string_list(item["roles"], f"{path}.roles", isc_config._ROLE_RE, upper=True))
        _expect(roles, f"{path}.roles must not be empty.")
    servers = None
    if "servers" in item:
        servers = frozenset(isc_config._string_list(item["servers"], f"{path}.servers"))
        _expect(servers, f"{path}.servers must not be empty.")
    hosts = None
    if "hosts" in item:
        hosts = frozenset(host.lower() for host in isc_config._string_list(item["hosts"], f"{path}.hosts", isc_config._HOST_RE))
        _expect(hosts, f"{path}.hosts must not be empty.")
    namespaces = None
    if "namespaces" in item:
        raw = item["namespaces"]
        _expect(isinstance(raw, list) and raw, f"{path}.namespaces must be a non-empty list of strings.")
        namespaces = frozenset(
            _namespace_value(isc_config._string(value, f"{path}.namespaces[{i}]"), f"{path}.namespaces[{i}]")
            for i, value in enumerate(raw)
        )
    path_prefixes = None
    if "path_prefixes" in item:
        path_prefixes = frozenset(isc_config._string_list(item["path_prefixes"], f"{path}.path_prefixes", isc_config._PATH_PREFIX_RE))
        _expect(path_prefixes, f"{path}.path_prefixes must not be empty.")
    requires_active_node = item.get("requires_active_node", True)
    _expect(type(requires_active_node) is bool, f"{path}.requires_active_node must be boolean.")
    evidence = None
    if "active_node_evidence_namespace" in item:
        evidence = _namespace_value(
            isc_config._string(item["active_node_evidence_namespace"], f"{path}.active_node_evidence_namespace"),
            f"{path}.active_node_evidence_namespace",
        )
    return ExceptionRule(id=id_, label=label, ticket=ticket, expires=expires, capability=capability,
                         roles=roles, servers=servers, hosts=hosts, namespaces=namespaces,
                         path_prefixes=path_prefixes, requires_active_node=requires_active_node,
                         active_node_evidence_namespace=evidence)


def _parse_exceptions(data):
    _expect(isinstance(data, list), "exceptions must be a list.")
    return tuple(_parse_exception(item, index) for index, item in enumerate(data))


def parse_rules(data, site_prefix=None):
    """Build a RuleSet from already-loaded JSON data (or None: empty RuleSet).

    Fail closed on any schema violation, mirroring config.py's parse_policy.
    """
    if data is None:
        return RuleSet(packages={}, exceptions=())
    _expect(isinstance(data, dict), "isc-ai.rules.json must be a JSON object.")
    version = data.get("version", RULES_VERSION)
    _expect(version == RULES_VERSION,
            f"Unsupported rules schema version {version!r}; expected {RULES_VERSION}.")
    unknown = set(data) - _RULES_TOP_LEVEL_KEYS
    _expect(not unknown, f"Unknown top-level keys in {RULES_FILENAME}: {sorted(unknown)}.")
    packages = _parse_packages(data.get("packages", {}), site_prefix)
    exceptions = _parse_exceptions(data.get("exceptions", []))
    return RuleSet(packages=packages, exceptions=exceptions)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_rules_data(project_dir):
    """Read and JSON-parse the rules file; None if it does not exist."""
    path = Path(project_dir) / RULES_FILENAME
    if not path.is_file():
        return None
    return isc_config.read_json(path)


def load_rules(project_dir, site_prefix=None):
    """Read isc-ai.rules.json under project_dir and parse it. None (not an
    empty RuleSet) when the file is absent: callers that must distinguish
    "no rules file" from "an empty rules file" (dual-definition wiring,
    IRIS_EXTRA_PACKAGES gating) rely on this."""
    data = load_rules_data(project_dir)
    if data is None:
        return None
    return parse_rules(data, site_prefix=site_prefix)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _scope_matches(scope_set, value, normalize=None):
    """A criterion narrows the result only when the caller supplies a value
    for that dimension; an unscoped rule (scope_set is None) always matches."""
    if scope_set is None or value is None:
        return True
    if normalize is not None:
        value = normalize(value)
    return value in scope_set


def matching_exceptions(ruleset, capability, *, namespace=None, host=None, role=None,
                        server=None, path_prefix=None, today=None):
    """Effective, non-expired exceptions for one capability, optionally
    narrowed by namespace/host/role/server/path_prefix."""
    matches = []
    for exc in ruleset.exceptions:
        if exc.capability != capability or exc.is_expired(today):
            continue
        if not _scope_matches(exc.roles, role, str.upper):
            continue
        if not _scope_matches(exc.servers, server):
            continue
        if not _scope_matches(exc.hosts, host, str.lower):
            continue
        if not _scope_matches(exc.namespaces, namespace, str.upper):
            continue
        if not _scope_matches(exc.path_prefixes, path_prefix):
            continue
        matches.append(exc)
    return tuple(matches)


def expiring_within(ruleset, days, today=None):
    """Exceptions whose expiry falls within [today, today + days], inclusive."""
    today = today or datetime.date.today()
    horizon = today + datetime.timedelta(days=days)
    result = []
    for exc in ruleset.exceptions:
        date = _parse_expiry_date(exc.expires)
        if date is not None and today <= date <= horizon:
            result.append(exc)
    return tuple(result)


def validate(ruleset, today=None):
    """Human-readable issue list: expired exceptions and expired package
    grants. Empty list means the rule set has no known problems."""
    today = today or datetime.date.today()
    issues = []
    for exc in ruleset.exceptions:
        if exc.is_expired(today):
            issues.append(f"Exception '{exc.id}' ({exc.label}) expired on {exc.expires}.")
    for namespace, grants in ruleset.packages.items():
        for grant in grants:
            if grant.expires is not None and _is_expired(grant.expires, today):
                label = grant.prefix or grant.class_name or grant.document
                issues.append(f"Package grant '{label}' in namespace '{namespace}' expired on {grant.expires}.")
    return issues


def render_template(site_prefix):
    """The baseline package grant written by 'isc-ai init'/'rules migrate'
    into a fresh isc-ai.rules.json: Custom.{SITE}.* with SITE resolved."""
    return {"prefix": f"Custom.{site_prefix}.*"}
