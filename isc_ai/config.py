"""Site-agnostic configuration for the IRIS MCP server.

Two configuration layers, both plain JSON:

  project policy   <project>/.iris-mcp/config.json
                   Site vocabulary (CCR format, production markers), the canonical
                   BASE route, HA/VIP write routes, the allowed-package catalog, the
                   controlled-mutation facility settings and optional servers.
  user servers     ~/.iris-mcp/servers.json
                   Private connection entries only. The legacy
                   ~/.iris_mcp_servers.json is still honoured as a fallback.

Secrets are never stored: any string value may reference the process environment
as "{env:VAR_NAME}" and is resolved at load time.

This module performs no I/O at import time. Loading is explicit so hermetic tests
can inject a policy without touching the filesystem.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from isc_ai.ccr_discovery import LocalPolicyError, validate_namespace

CONFIG_VERSION = 1

# Directory names, newest first. The tool configures the agentic environment, of which
# IRIS/TrakCare is one server, so the state directory carries the tool's name rather than
# that server's. `.iris-mcp` stays readable forever: an installed deployment must keep
# working after an upgrade, and a rename is not a reason to break one.
STATE_DIR = ".isc-ai"
LEGACY_STATE_DIR = ".iris-mcp"
STATE_DIRS = (STATE_DIR, LEGACY_STATE_DIR)

# Environment overrides, newest first. Both are honoured; the new name wins when both are
# set, and the CLI reports which one was used instead of choosing in silence.
PROJECT_DIR_ENV = "ISC_AI_PROJECT_DIR"
PROJECT_CONFIG_ENV = "ISC_AI_CONFIG"
LEGACY_PROJECT_DIR_ENV = "IRIS_MCP_PROJECT_DIR"
LEGACY_PROJECT_CONFIG_ENV = "IRIS_MCP_CONFIG"
PROJECT_DIR_ENVS = (PROJECT_DIR_ENV, LEGACY_PROJECT_DIR_ENV)
PROJECT_CONFIG_ENVS = (PROJECT_CONFIG_ENV, LEGACY_PROJECT_CONFIG_ENV)

EXTRA_PACKAGES_ENV = "IRIS_EXTRA_PACKAGES"
PROJECT_CONFIG_RELATIVE = Path(STATE_DIR) / "config.json"
LEGACY_PROJECT_CONFIG_RELATIVE = Path(LEGACY_STATE_DIR) / "config.json"
USER_SERVERS_RELATIVE = Path(STATE_DIR) / "servers.json"
LEGACY_USER_SERVERS_RELATIVE = Path(LEGACY_STATE_DIR) / "servers.json"
LEGACY_USER_SERVERS_NAME = ".iris_mcp_servers.json"

TEMPLATE_PATH = Path(__file__).resolve().parent / "resources" / "config.template.json"
MUTATION_CLASS = "MCP.CCRMutation"
MUTATION_TEMPLATE_PATH = (Path(__file__).resolve().parent / "resources" / "MCP"
                          / "CCRMutation.cls.template")

_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_PACKAGE_PREFIX_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*")
_NAMESPACE_RE = re.compile(r"%?[A-Za-z0-9][A-Za-z0-9_-]*")
_HOST_RE = re.compile(r"[A-Za-z0-9.:\[\]-]+")
_PATH_PREFIX_RE = re.compile(r"(?:/[A-Za-z0-9_-]+)*")
_ROLE_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_SECRET_REF_RE = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
_SOURCE_ROOT_RE = re.compile(r"[A-Za-z0-9_.-]+(?:[\\/][A-Za-z0-9_.-]+)*")

# Package roots that may never be granted through any channel: IRIS core, Region
# (TrakCare core), the MCP facility package itself and TrakCare system roots.
FORBIDDEN_PACKAGE_ROOTS = ("%", "ens", "region", "mcp", "tc", "web", "websys",
                           "epr", "user", "model", "sqluser")

_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                 6: "six", 7: "seven", 8: "eight", 9: "nine"}

# Built-in production vocabulary. Projects may EXTEND it, never shrink it: a
# committed project file must not be able to open a production target.
BUILTIN_PRODUCTION_MARKERS = ("PROD", "PRODUCTION", "LIVE", "PRD")
BUILTIN_PRODUCTION_ROLES = frozenset({"LIVE", "PROD", "PRODUCTION", "PRD"})


class ConfigError(ValueError):
    """Configuration is missing, malformed or violates a policy invariant."""


# ---------------------------------------------------------------------------
# Policy model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CanonicalBase:
    server_id: str
    host: str
    port: int
    path_prefix: str
    namespace: str
    role: str = "BASE"

    def matches(self, host, port, path_prefix, namespace, role, is_production):
        normalized_role = (role or "").strip().upper() if isinstance(role, str) else ""
        return (host == self.host
                and type(port) is int and port == self.port
                and path_prefix == self.path_prefix
                and namespace == self.namespace
                and normalized_role == self.role
                and is_production is False)


@dataclass(frozen=True)
class WriteException:
    """A sanctioned UAT code-write route outside the BASE origin.

    hosts=None means "any configured direct HA node". When requires_active_node is
    true the caller must prove the node is executing the production (fresh
    evidence per call) before writing.
    """
    label: str
    namespace: str
    path_prefixes: frozenset
    hosts: frozenset = None
    requires_active_node: bool = True


@dataclass(frozen=True)
class PackageGrant:
    """Exactly one of prefix / class_name / document is set. roles=None means any role."""
    prefix: str = None
    class_name: str = None
    document: str = None
    roles: frozenset = None

    def allows(self, document_name, stem, role):
        if self.roles is not None and role not in self.roles:
            return False
        if self.prefix is not None:
            return re.fullmatch(re.escape(self.prefix) + r"\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", stem) is not None
        if self.class_name is not None:
            return stem == self.class_name
        return document_name == self.document


@dataclass(frozen=True)
class RunnerHost:
    """Explicit host namespace for the ephemeral runner of a locked namespace.

    The runner class is created in runner_namespace (same instance) and the payload
    switches to namespace itself. Applies only to the configured roles and, when
    hosts is set (lower-cased), only to targets whose host is listed.
    """
    namespace: str
    runner_namespace: str
    roles: frozenset
    hosts: frozenset = None


@dataclass(frozen=True)
class SitePolicy:
    # False for the built-in default policy (no project configuration loaded).
    # CODE WRITE routes require a configured policy; READ keeps working because
    # the production vocabulary below is built in.
    configured: bool = False
    site_name: str = "unconfigured"
    ccr_prefix: str = None
    ccr_digits: int = 0
    protocol_prefix: str = "MCP"
    production_markers: tuple = BUILTIN_PRODUCTION_MARKERS
    production_roles: frozenset = BUILTIN_PRODUCTION_ROLES
    non_production_roles: frozenset = frozenset({"BASE", "DEV", "UAT", "QA", "TEST", "SCRATCH", "TRAIN"})
    system_namespaces: frozenset = frozenset({"%SYS", "USER", "HSLIB", "HSSYS", "HSSYSLOCALTEMP",
                                             "DATAMIGRATION", "HSCUSTOM"})
    canonical_base: CanonicalBase = None
    direct_ha_nodes: frozenset = frozenset()
    alias_constraints: dict = field(default_factory=dict)
    uat_write_exceptions: tuple = ()
    # Explicit runner host namespaces (runtime.runner_hosts). Empty: the runner is
    # always created in the target namespace; there is never an implicit fallback.
    runner_hosts: tuple = ()
    packages: dict = field(default_factory=dict)
    mutation_class_prefix: str = None
    mutation_source_root: Path = None
    # Language the AI agents reply in. Not the language of this CLI (that is --lang):
    # this one is rendered into the instruction files the agents read.
    agent_reply_language: str = "es-CL"
    # role -> provider-qualified model id. Empty means "let the client decide": a
    # concrete id is a decision about which model runs the work, never a default.
    agent_models: dict = field(default_factory=dict)

    # -- CCR identifier -------------------------------------------------

    @property
    def ccr_configured(self):
        return bool(self.ccr_prefix) and self.ccr_digits > 0

    @property
    def ccr_regex(self):
        if not self.ccr_configured:
            return None
        return re.escape(self.ccr_prefix) + r"\d{" + str(self.ccr_digits) + "}"

    def ccr_matches(self, value):
        pattern = self.ccr_regex
        return pattern is not None and isinstance(value, str) and re.fullmatch(pattern, value) is not None

    @property
    def ccr_description(self):
        if not self.ccr_configured:
            return "a configured CCR identifier (site.ccr_id is not set)"
        digits = _NUMBER_WORDS.get(self.ccr_digits, str(self.ccr_digits))
        return f"{self.ccr_prefix} followed by {digits} digits"

    @property
    def ccr_cos_pattern(self):
        """ObjectScript pattern-match operand, e.g. 1"SITE"4N."""
        if not self.ccr_configured:
            return None
        return f'1"{self.ccr_prefix}"{self.ccr_digits}N'

    # -- Protocol names -------------------------------------------------

    @property
    def mutation_protocol(self):
        return f"{self.protocol_prefix}_CCR_MUTATION_V1"

    @property
    def initializer_protocol(self):
        return f"{self.protocol_prefix}_CCR_INITIALIZER_V1"

    # -- Routes ---------------------------------------------------------

    def is_direct_ha_node(self, host):
        return host in self.direct_ha_nodes

    def matching_write_exceptions(self, namespace, host, path_prefix):
        for exception in self.uat_write_exceptions:
            if exception.namespace != namespace or path_prefix not in exception.path_prefixes:
                continue
            hosts = exception.hosts if exception.hosts is not None else self.direct_ha_nodes
            if host in hosts:
                yield exception

    def runner_host_for(self, namespace, role, host=None):
        """The RunnerHost configured for namespace, role and host, or None.

        An entry with hosts matches only a listed host (case-insensitive); an entry
        without hosts matches any host.
        """
        if not isinstance(namespace, str) or not isinstance(role, str):
            return None
        target, normalized_role = namespace.upper(), role.strip().upper()
        normalized_host = host.lower() if isinstance(host, str) else None
        for entry in self.runner_hosts:
            if entry.namespace != target or normalized_role not in entry.roles:
                continue
            if entry.hosts is not None and normalized_host not in entry.hosts:
                continue
            return entry
        return None

    # -- Packages -------------------------------------------------------

    def package_allowed(self, namespace, role, document_name):
        if not isinstance(document_name, str) or "." not in document_name:
            return False
        stem = document_name.rsplit(".", 1)[0]
        if _is_mutation_facility_stem(stem):
            return False
        for grant in self.packages.get(namespace, ()):
            if grant.allows(document_name, stem, role):
                return True
        return False

    def with_extra_packages(self, raw):
        """Return a policy extended with IRIS_EXTRA_PACKAGES grants (BASE role only)."""
        grants = dict(self.packages)
        for namespace, prefix in parse_extra_packages(raw):
            grants[namespace] = tuple(grants.get(namespace, ())) + (
                PackageGrant(prefix=prefix, roles=frozenset({"BASE"})),
            )
        return _replace(self, packages=grants)

    # -- Mutation facility ----------------------------------------------

    @property
    def mutation_configured(self):
        return (self.canonical_base is not None and self.ccr_configured
                and bool(self.mutation_class_prefix) and self.mutation_source_root is not None)

    def mutation_class_allowed(self, class_name):
        if not self.mutation_class_prefix or not isinstance(class_name, str):
            return False
        if not re.fullmatch(re.escape(self.mutation_class_prefix) + r"\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", class_name):
            return False
        return not _is_mutation_facility_stem(class_name)

    def mutation_expected_path(self, class_name):
        """Local .cls path that must hold the approved source for class_name."""
        if not self.mutation_configured or not self.mutation_class_allowed(class_name):
            return None
        skip = self.mutation_class_prefix.count(".") + 1
        return (self.mutation_source_root / Path(*class_name.split(".")[skip:])).with_suffix(".cls")

    def render_mutation_source(self, template_text):
        """Render the packaged MCP.CCRMutation template for this site."""
        if not self.mutation_configured:
            raise ConfigError("Controlled mutation requires canonical_base, site.ccr_id and mutation settings.")
        prefix_with_dot = self.mutation_class_prefix + "."
        values = {
            "CANONICAL_NAMESPACE": self.canonical_base.namespace,
            "PROTOCOL_PREFIX": self.protocol_prefix,
            "MUTATION_CLASS_PREFIX": self.mutation_class_prefix,
            "MUTATION_CLASS_PREFIX_DOT": prefix_with_dot,
            "MUTATION_CLASS_PREFIX_DOT_LENGTH": str(len(prefix_with_dot)),
            "CCR_PREFIX": self.ccr_prefix,
            "CCR_PREFIX_LENGTH": str(len(self.ccr_prefix)),
            "CCR_DIGITS": str(self.ccr_digits),
            "CCR_DIGITS_START": str(len(self.ccr_prefix) + 1),
            "CCR_LENGTH": str(len(self.ccr_prefix) + self.ccr_digits),
            "CCR_DESCRIPTION": self.ccr_description,
        }
        return render_template(template_text, values)


def _replace(policy, **changes):
    import dataclasses
    return dataclasses.replace(policy, **changes)


def _is_mutation_facility_stem(stem):
    folded = stem.casefold()
    facility = MUTATION_CLASS.casefold()
    return folded == facility or folded.endswith("." + facility)


def render_template(template_text, values):
    def substitute(match):
        key = match.group(1)
        if key not in values:
            raise ConfigError(f"Template placeholder {{{{{key}}}}} has no value.")
        return values[key]
    rendered = re.sub(r"\{\{([A-Z_]+)\}\}", substitute, template_text)
    leftover = re.search(r"\{\{[A-Z_]+\}\}", rendered)
    if leftover:
        raise ConfigError(f"Template placeholder {leftover.group(0)} was not rendered.")
    return rendered


# ---------------------------------------------------------------------------
# IRIS_EXTRA_PACKAGES (process-environment override, kept for compatibility)
# ---------------------------------------------------------------------------

def parse_extra_packages(raw):
    """Parse "NS1:pkg1,pkg2;NS2:pkg3" into (namespace, prefix) pairs.

    Invalid names, forbidden roots and parents of the facility package are
    dropped silently: this channel is an operator convenience, never a policy
    escalation.
    """
    pairs = []
    if not isinstance(raw, str):
        return pairs
    for group in raw.split(";"):
        if ":" not in group:
            continue
        namespace, items = group.split(":", 1)
        namespace = namespace.strip()
        if not namespace:
            continue
        for item in items.split(","):
            prefix = item.strip()
            if not prefix or not _PACKAGE_PREFIX_RE.fullmatch(prefix):
                continue
            if prefix.split(".", 1)[0].casefold() in FORBIDDEN_PACKAGE_ROOTS:
                continue
            if MUTATION_CLASS.casefold().startswith(prefix.casefold() + "."):
                continue
            pairs.append((namespace, prefix))
    return pairs


# ---------------------------------------------------------------------------
# Parsing / validation
# ---------------------------------------------------------------------------

def _expect(condition, message):
    if not condition:
        raise ConfigError(message)


def _string(value, path, pattern=None, allow_empty=False):
    _expect(isinstance(value, str), f"{path} must be a string.")
    if not allow_empty:
        _expect(value != "", f"{path} must not be empty.")
    if pattern is not None and value != "":
        _expect(pattern.fullmatch(value) is not None, f"{path} has an invalid value.")
    return value


def _string_list(value, path, pattern=None, upper=False):
    _expect(isinstance(value, list), f"{path} must be a list of strings.")
    items = []
    for index, item in enumerate(value):
        text = _string(item, f"{path}[{index}]", pattern)
        items.append(text.upper() if upper else text)
    return items


def _port(value, path):
    _expect(type(value) is int and 1 <= value <= 65535, f"{path} must be an integer between 1 and 65535.")
    return value


def _validate_package_prefix(prefix, path):
    _string(prefix, path, _PACKAGE_PREFIX_RE)
    root = prefix.split(".", 1)[0].casefold()
    _expect(root not in FORBIDDEN_PACKAGE_ROOTS, f"{path}: package root '{prefix.split('.', 1)[0]}' can never be granted.")
    _expect(not MUTATION_CLASS.casefold().startswith(prefix.casefold() + "."),
            f"{path}: the facility package {MUTATION_CLASS} can never be granted.")
    return prefix


def _parse_site(data, policy_kwargs):
    site = data.get("site", {})
    _expect(isinstance(site, dict), "site must be an object.")
    if "name" in site:
        policy_kwargs["site_name"] = _string(site["name"], "site.name")
    if "ccr_id" in site:
        ccr = site["ccr_id"]
        _expect(isinstance(ccr, dict), "site.ccr_id must be an object with prefix and digits.")
        policy_kwargs["ccr_prefix"] = _string(ccr.get("prefix"), "site.ccr_id.prefix", _IDENTIFIER_RE)
        digits = ccr.get("digits")
        _expect(type(digits) is int and 1 <= digits <= 12, "site.ccr_id.digits must be an integer between 1 and 12.")
        policy_kwargs["ccr_digits"] = digits
    if "protocol_prefix" in site:
        policy_kwargs["protocol_prefix"] = _string(site["protocol_prefix"], "site.protocol_prefix", _IDENTIFIER_RE)
    if "production_markers" in site:
        extra = _string_list(site["production_markers"], "site.production_markers", upper=True)
        policy_kwargs["production_markers"] = BUILTIN_PRODUCTION_MARKERS + tuple(
            marker for marker in extra if marker not in BUILTIN_PRODUCTION_MARKERS)
    if "production_roles" in site:
        policy_kwargs["production_roles"] = BUILTIN_PRODUCTION_ROLES | frozenset(
            _string_list(site["production_roles"], "site.production_roles", upper=True))
    if "non_production_roles" in site:
        roles = frozenset(_string_list(site["non_production_roles"], "site.non_production_roles", upper=True))
        _expect(roles, "site.non_production_roles must not be empty.")
        policy_kwargs["non_production_roles"] = roles
    if "system_namespaces" in site:
        policy_kwargs["system_namespaces"] = frozenset(_string_list(site["system_namespaces"], "site.system_namespaces", upper=True))
    production = policy_kwargs.get("production_roles", SitePolicy.production_roles)
    non_production = policy_kwargs.get("non_production_roles", SitePolicy.non_production_roles)
    overlap = production & non_production
    _expect(not overlap, f"Roles cannot be both production and non-production: {sorted(overlap)}.")
    markers = policy_kwargs.get("production_markers", SitePolicy.production_markers)
    tainted = sorted(role for role in non_production if any(marker in role for marker in markers))
    _expect(not tainted, f"Non-production roles cannot contain a production marker: {tainted}.")


def _parse_canonical_base(data, policy_kwargs):
    base = data.get("canonical_base")
    if base is None:
        return
    _expect(isinstance(base, dict), "canonical_base must be an object.")
    policy_kwargs["canonical_base"] = CanonicalBase(
        server_id=_string(base.get("server_id"), "canonical_base.server_id"),
        host=_string(base.get("host"), "canonical_base.host", _HOST_RE),
        port=_port(base.get("port", 80), "canonical_base.port"),
        path_prefix=_string(base.get("path_prefix", ""), "canonical_base.path_prefix", _PATH_PREFIX_RE, allow_empty=True),
        namespace=_string(base.get("namespace"), "canonical_base.namespace", _NAMESPACE_RE),
    )


def _parse_routes(data, policy_kwargs):
    routes = data.get("routes", {})
    _expect(isinstance(routes, dict), "routes must be an object.")
    if "direct_ha_nodes" in routes:
        policy_kwargs["direct_ha_nodes"] = frozenset(_string_list(routes["direct_ha_nodes"], "routes.direct_ha_nodes", _HOST_RE))
    aliases = routes.get("alias_constraints", {})
    _expect(isinstance(aliases, dict), "routes.alias_constraints must be an object.")
    constraints = {}
    for alias, rule in aliases.items():
        _string(alias, "routes.alias_constraints key")
        _expect(isinstance(rule, dict), f"routes.alias_constraints.{alias} must be an object.")
        constraints[alias] = (
            _string(rule.get("host"), f"routes.alias_constraints.{alias}.host", _HOST_RE),
            _string(rule.get("path_prefix", ""), f"routes.alias_constraints.{alias}.path_prefix", _PATH_PREFIX_RE, allow_empty=True),
        )
    policy_kwargs["alias_constraints"] = constraints
    exceptions = routes.get("uat_write_exceptions", [])
    _expect(isinstance(exceptions, list), "routes.uat_write_exceptions must be a list.")
    parsed = []
    for index, item in enumerate(exceptions):
        path = f"routes.uat_write_exceptions[{index}]"
        _expect(isinstance(item, dict), f"{path} must be an object.")
        hosts = None
        if "hosts" in item:
            hosts = frozenset(_string_list(item["hosts"], f"{path}.hosts", _HOST_RE))
            _expect(hosts, f"{path}.hosts must not be empty.")
        prefixes = frozenset(_string_list(item.get("path_prefixes", []), f"{path}.path_prefixes", _PATH_PREFIX_RE)
                             if item.get("path_prefixes") else [])
        _expect(prefixes, f"{path}.path_prefixes must list at least one path prefix.")
        requires = item.get("requires_active_node", True)
        _expect(type(requires) is bool, f"{path}.requires_active_node must be boolean.")
        if not requires:
            # A write without active-node evidence is only admissible on a VIP that
            # is NOT a mirror member. Listing a direct HA node here (or omitting
            # hosts, which means "any direct HA node") would bypass the node gate.
            _expect(hosts is not None, f"{path}: requires_active_node=false needs an explicit hosts list (VIP).")
            direct = policy_kwargs.get("direct_ha_nodes", frozenset())
            leaking = sorted(hosts & direct)
            _expect(not leaking, f"{path}: hosts {leaking} are direct HA nodes; requires_active_node must be true.")
        parsed.append(WriteException(
            label=_string(item.get("label", f"exception {index}"), f"{path}.label"),
            namespace=_string(item.get("namespace"), f"{path}.namespace", _NAMESPACE_RE),
            path_prefixes=prefixes, hosts=hosts, requires_active_node=requires,
        ))
    policy_kwargs["uat_write_exceptions"] = tuple(parsed)


def _runtime_namespace(value, path, markers):
    try:
        namespace = validate_namespace(value)
    except LocalPolicyError:
        raise ConfigError(f"{path} must be a plain namespace identifier.") from None
    tainted = [marker for marker in markers if marker in namespace]
    _expect(not tainted, f"{path} '{namespace}' contains a production marker {tainted}.")
    return namespace


def _parse_runtime(data, policy_kwargs):
    """runtime.runner_hosts: explicit runner host namespaces, never inferred."""
    runtime = data.get("runtime")
    if runtime is None:
        return
    _expect(isinstance(runtime, dict), "runtime must be an object.")
    unknown = set(runtime) - {"runner_hosts"}
    _expect(not unknown, f"Unknown keys in runtime: {sorted(unknown)}.")
    hosts = runtime.get("runner_hosts", [])
    _expect(isinstance(hosts, list), "runtime.runner_hosts must be a list.")
    markers = policy_kwargs.get("production_markers", SitePolicy.production_markers)
    production = policy_kwargs.get("production_roles", SitePolicy.production_roles)
    non_production = policy_kwargs.get("non_production_roles", SitePolicy.non_production_roles)
    parsed, seen = [], set()
    for index, item in enumerate(hosts):
        path = f"runtime.runner_hosts[{index}]"
        _expect(isinstance(item, dict), f"{path} must be an object.")
        extra = set(item) - {"namespace", "runner_namespace", "roles", "hosts"}
        _expect(not extra, f"Unknown keys in {path}: {sorted(extra)}.")
        namespace = _runtime_namespace(item.get("namespace"), f"{path}.namespace", markers)
        runner_namespace = _runtime_namespace(item.get("runner_namespace"), f"{path}.runner_namespace", markers)
        _expect(runner_namespace != namespace, f"{path}.runner_namespace must differ from namespace.")
        _expect(namespace not in seen, f"{path}.namespace '{namespace}' is duplicated.")
        seen.add(namespace)
        # Roles are upper-cased BEFORE the pattern check here only (runner_hosts accepts
        # "uat"); packages[].roles keeps its stricter upper-case-only contract.
        raw_roles = item.get("roles")
        _expect(isinstance(raw_roles, list), f"{path}.roles must be a list of strings.")
        roles = frozenset(
            _string(role.upper() if isinstance(role, str) else role, f"{path}.roles[{position}]", _ROLE_RE)
            for position, role in enumerate(raw_roles))
        _expect(roles, f"{path}.roles must not be empty.")
        entry_hosts = None
        if "hosts" in item:
            entry_hosts = frozenset(host.lower() for host in _string_list(item["hosts"], f"{path}.hosts", _HOST_RE))
            _expect(entry_hosts, f"{path}.hosts must not be empty.")
        forbidden = sorted(roles & production)
        _expect(not forbidden, f"{path}.roles {forbidden} are production roles.")
        unlisted = sorted(roles - non_production)
        _expect(not unlisted, f"{path}.roles {unlisted} are not configured non-production roles.")
        parsed.append(RunnerHost(namespace=namespace, runner_namespace=runner_namespace,
                                 roles=roles, hosts=entry_hosts))
    policy_kwargs["runner_hosts"] = tuple(parsed)


def _parse_packages(data, policy_kwargs):
    packages = data.get("packages", {})
    _expect(isinstance(packages, dict), "packages must be an object keyed by namespace.")
    parsed = {}
    for namespace, grants in packages.items():
        _string(namespace, "packages key", _NAMESPACE_RE)
        _expect(isinstance(grants, list), f"packages.{namespace} must be a list of grants.")
        items = []
        for index, grant in enumerate(grants):
            path = f"packages.{namespace}[{index}]"
            _expect(isinstance(grant, dict), f"{path} must be an object.")
            kinds = [key for key in ("prefix", "class", "document") if key in grant]
            _expect(len(kinds) == 1, f"{path} must set exactly one of prefix, class or document.")
            roles = None
            if "roles" in grant:
                roles = frozenset(_string_list(grant["roles"], f"{path}.roles", _ROLE_RE, upper=True))
                _expect(roles, f"{path}.roles must not be empty.")
            if kinds[0] == "prefix":
                items.append(PackageGrant(prefix=_validate_package_prefix(grant["prefix"], f"{path}.prefix"), roles=roles))
            elif kinds[0] == "class":
                name = _string(grant["class"], f"{path}.class", _PACKAGE_PREFIX_RE)
                _expect(not _is_mutation_facility_stem(name), f"{path}.class: the facility class can never be granted.")
                items.append(PackageGrant(class_name=name, roles=roles))
            else:
                document = _string(grant["document"], f"{path}.document")
                _expect("." in document and _PACKAGE_PREFIX_RE.fullmatch(document.rsplit(".", 1)[0]) is not None,
                        f"{path}.document must be a document name with extension.")
                _expect(not _is_mutation_facility_stem(document.rsplit(".", 1)[0]),
                        f"{path}.document: the facility class can never be granted.")
                items.append(PackageGrant(document=document, roles=roles))
        parsed[namespace] = tuple(items)
    policy_kwargs["packages"] = parsed


def _parse_agents(data, policy_kwargs):
    """Agent-facing preferences. Today only the language the agents reply in.

    This is deliberately separate from the CLI's own language (--lang / ISC_AI_LANG):
    one governs what this program prints, the other is rendered into the instruction
    files the agents read, so it is a property of the project and of the team.
    """
    agents = data.get("agents")
    if agents is None:
        return
    _expect(isinstance(agents, dict), "agents must be an object.")
    unknown = set(agents) - {"reply_language", "models"}
    _expect(not unknown, f"Unknown keys in agents: {sorted(unknown)}.")
    models = agents.get("models")
    if models is not None:
        _expect(isinstance(models, dict), "agents.models must be an object keyed by role.")
        for role, model in models.items():
            _string(role, f"agents.models key {role!r}", re.compile(r"[a-z][a-z0-9-]*"))
            _expect(isinstance(model, str) and "/" in model,
                    f"agents.models.{role} must be a provider-qualified model id.")
        policy_kwargs["agent_models"] = dict(models)
    language = agents.get("reply_language")
    if language is None:
        return
    _expect(isinstance(language, str) and re.fullmatch(r"[a-z]{2}(-[A-Z]{2})?", language),
            "agents.reply_language must be a language tag such as 'es-CL', 'es' or 'en'.")
    policy_kwargs["agent_reply_language"] = language


def _parse_mutation(data, policy_kwargs, project_dir):
    mutation = data.get("mutation")
    if mutation is None:
        return
    _expect(isinstance(mutation, dict), "mutation must be an object.")
    prefix = _validate_package_prefix(mutation.get("class_prefix"), "mutation.class_prefix")
    root = _string(mutation.get("local_source_root"), "mutation.local_source_root", _SOURCE_ROOT_RE)
    policy_kwargs["mutation_class_prefix"] = prefix
    policy_kwargs["mutation_source_root"] = (Path(project_dir) / root).resolve()


def parse_policy(data, project_dir):
    """Build a SitePolicy from the project configuration mapping."""
    _expect(isinstance(data, dict), "Project configuration must be a JSON object.")
    version = data.get("version", CONFIG_VERSION)
    _expect(version == CONFIG_VERSION, f"Unsupported configuration version {version!r}; expected {CONFIG_VERSION}.")
    unknown = set(data) - {"version", "site", "canonical_base", "routes", "runtime", "packages", "mutation", "agents", "servers", "$schema", "_comment"}
    _expect(not unknown, f"Unknown top-level keys: {sorted(unknown)}.")
    kwargs = {"configured": True}
    _parse_site(data, kwargs)
    _parse_canonical_base(data, kwargs)
    _parse_routes(data, kwargs)
    _parse_runtime(data, kwargs)
    _parse_packages(data, kwargs)
    _parse_agents(data, kwargs)
    _parse_mutation(data, kwargs, project_dir)
    return SitePolicy(**kwargs)


# ---------------------------------------------------------------------------
# Servers and secrets
# ---------------------------------------------------------------------------

SERVER_KEYS = {"host", "port", "path_prefix", "username", "password", "namespace", "env_role",
               "is_production", "proxy", "ccr_id", "upload_token", "canonical_server_id", "description",
               "scheme", "verify_ssl"}


def resolve_secret_references(value, environ):
    """Replace {env:NAME} references inside string values. Raises on missing names.

    `environ` is already the merged view built by load_runtime_config: the process
    environment first, then the project secret store, then the global one. A name found
    nowhere still raises, because a missing credential must fail loudly instead of
    silently connecting as somebody else.
    """
    if isinstance(value, str):
        def substitute(match):
            name = match.group(1)
            if name not in environ:
                raise ConfigError(f"Environment variable {name} referenced by configuration is not set.")
            return environ[name]
        return _SECRET_REF_RE.sub(substitute, value)
    if isinstance(value, dict):
        return {key: resolve_secret_references(item, environ) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_secret_references(item, environ) for item in value]
    return value


def resolve_servers(servers, environ):
    """Resolve secrets per entry; entries with unresolved references are kept with an error."""
    _expect(isinstance(servers, dict), "servers must be an object keyed by server id.")
    resolved = {}
    for server_id, entry in servers.items():
        if not isinstance(entry, dict):
            resolved[server_id] = {"_config_error": "Malformed server entry."}
            continue
        try:
            resolved[server_id] = resolve_secret_references(entry, environ)
        except ConfigError as exc:
            redacted = {key: value for key, value in entry.items() if key not in SECRET_KEYS}
            redacted["_config_error"] = str(exc)
            resolved[server_id] = redacted
    return resolved


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

@dataclass
class RuntimeConfig:
    policy: SitePolicy
    servers: dict
    project_dir: Path
    project_config_path: Path
    user_servers_path: Path
    notes: tuple = ()


class AmbiguousStateError(ConfigError):
    """One directory holds both the new and the legacy state: refuse to guess which wins."""


def state_flavours(directory):
    """Which state directories exist in `directory`, newest first.

    Two of them means a half-finished migration, and picking one silently would make the
    other invisible: a stale policy that looks live is worse than an error.
    """
    base = Path(directory)
    return tuple(name for name in STATE_DIRS if (base / name / "config.json").is_file())


def find_project_root(start):
    """Nearest ancestor of `start` (inclusive) that holds a project configuration, else None.

    Mirrors how git finds .git: the server may be launched from any subdirectory of a
    deployment and still pick up that deployment's policy. Proximity decides, not flavour,
    so a nearby legacy deployment is never overruled by a distant migrated one.
    """
    current = Path(start).resolve()
    for candidate in (current, *current.parents):
        if state_flavours(candidate):
            return candidate
    return None


def _first_env(environ, names):
    """Value of the first name that is set, and the name it came from."""
    for name in names:
        value = environ.get(name)
        if value:
            return value, name
    return None, None


def project_directory(environ=None, start=None):
    """Directory whose project configuration governs this process.

    Precedence: an explicit override (no search) -> nearest ancestor of `start` (default
    cwd) with a project configuration -> `start` itself, which then yields an unconfigured
    policy.
    """
    environ = os.environ if environ is None else environ
    override, _ = _first_env(environ, PROJECT_DIR_ENVS)
    if override:
        return Path(override).resolve()
    start = Path.cwd() if start is None else Path(start)
    found = find_project_root(start)
    return found if found is not None else start.resolve()


def project_config_path(project_dir, environ=None):
    """Path of the policy that governs `project_dir`.

    Reads whichever flavour exists; writes the current one when neither does. Both present
    is an error, because the answer to "which policy is live" must not depend on which
    constant happens to be checked first.
    """
    environ = os.environ if environ is None else environ
    override, _ = _first_env(environ, PROJECT_CONFIG_ENVS)
    if override:
        return Path(override).resolve()
    base = Path(project_dir)
    flavours = state_flavours(base)
    if len(flavours) > 1:
        raise AmbiguousStateError(
            f"{base} holds both {STATE_DIR}/config.json and {LEGACY_STATE_DIR}/config.json; "
            f"keep one and remove the other (see 'isc-ai migrate')"
        )
    if flavours:
        return base / flavours[0] / "config.json"
    return base / PROJECT_CONFIG_RELATIVE


def user_servers_path(home=None):
    """User-level connections, preferring the current directory but reading the legacy one."""
    home = Path.home() if home is None else Path(home)
    current = home / USER_SERVERS_RELATIVE
    if current.is_file():
        return current
    legacy = home / LEGACY_USER_SERVERS_RELATIVE
    if legacy.is_file():
        return legacy
    return current


def legacy_user_servers_path(home=None):
    home = Path.home() if home is None else Path(home)
    return home / LEGACY_USER_SERVERS_NAME


def read_json(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc.strerror or 'I/O error'}.") from None
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}.") from None


def load_runtime_config(project_dir=None, environ=None, home=None):
    """Load project policy and merged servers. Missing files are not errors."""
    environ = os.environ if environ is None else environ
    explicit_config = environ.get(PROJECT_CONFIG_ENV)
    if project_dir is None and explicit_config and not environ.get(PROJECT_DIR_ENV):
        # An explicit config file governs its own project: relative paths inside it
        # (mutation.local_source_root) resolve against the directory that holds
        # .iris-mcp/, never against wherever the shell happens to be.
        project_dir = Path(explicit_config).resolve().parent.parent
    else:
        project_dir = project_directory(environ) if project_dir is None else Path(project_dir).resolve()
    config_path = project_config_path(project_dir, environ)
    notes = []

    project_data = {}
    if config_path.is_file():
        project_data = read_json(config_path)
        policy = parse_policy(project_data, project_dir)
    else:
        notes.append(f"No project configuration at {config_path}; site policy is unconfigured "
                     "and code-write routes are blocked (run 'isc-ai init').")
        policy = SitePolicy()
    extra = environ.get(EXTRA_PACKAGES_ENV)
    if extra:
        policy = policy.with_extra_packages(extra)

    servers = {}
    legacy_path = legacy_user_servers_path(home)
    user_path = user_servers_path(home)
    for path in (legacy_path, user_path):
        if path.is_file():
            data = read_json(path)
            _expect(isinstance(data, dict) and isinstance(data.get("servers", {}), dict),
                    f"{path} must contain a 'servers' object.")
            servers.update(data.get("servers", {}))
            if path == legacy_path:
                notes.append(f"Loaded legacy {path}; migrate with 'isc-ai servers migrate'.")
    project_servers = project_data.get("servers", {})
    _expect(isinstance(project_servers, dict), "servers must be an object keyed by server id.")
    servers.update(project_servers)

    # The secret stores extend the environment for reference resolution, never replace
    # it: a value exported in the shell or by CI must keep winning. Imported here to
    # avoid a cycle, since the store reads JSON through this module.
    from isc_ai import secrets as isc_secrets

    try:
        stored = isc_secrets.load(project_dir=project_dir, home=home)
    except ConfigError as exc:
        notes.append(f"Secret store ignored: {exc}")
        stored = {}
    if stored:
        resolution_environ = {**stored, **environ}
        notes.append(f"Loaded {len(stored)} value(s) from the secret store; the environment "
                     f"still takes precedence.")
    else:
        resolution_environ = environ

    return RuntimeConfig(
        policy=policy,
        servers=resolve_servers(servers, resolution_environ),
        project_dir=project_dir,
        project_config_path=config_path,
        user_servers_path=user_path,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Template / editing helpers used by the CLI
# ---------------------------------------------------------------------------

def load_template(path=None):
    path = TEMPLATE_PATH if path is None else Path(path)
    data = read_json(path)
    # The template must itself be a valid project configuration.
    parse_policy(data, Path("."))
    return data


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def coerce_value(raw, current=None):
    """Interpret a CLI value: JSON when parseable, otherwise a plain string.

    A current value guides scalar typing so 'config set canonical_base.port 80'
    yields an int while 'config set canonical_base.host 80' keeps a string.
    """
    if current is None or isinstance(current, str):
        if isinstance(current, str):
            return raw
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    if isinstance(current, bool):
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        raise ConfigError("Expected a boolean (true/false).")
    if isinstance(current, int):
        try:
            return int(raw)
        except ValueError:
            raise ConfigError("Expected an integer.") from None
    try:
        return json.loads(raw)
    except ValueError:
        raise ConfigError("Expected a JSON value for a list/object setting.") from None


def get_path(data, dotted):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_path(data, dotted, value):
    parts = dotted.split(".")
    _expect(all(parts), f"Invalid setting path {dotted!r}.")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def unset_path(data, dotted):
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return False
    if isinstance(node, dict) and parts[-1] in node:
        del node[parts[-1]]
        return True
    return False


SECRET_KEYS = ("password", "upload_token")

# Keys this schema does not enumerate but whose value is still a credential. The CLI
# prints configuration, findings and plans, and a value that leaks into any of them
# outlives its usefulness in shell history, logs and terminal recordings. Enumerating
# every possible name is a losing game, so the shape of the name is what decides.
SECRET_KEY_PATTERN = re.compile(r"(?i)(pass(word|wd)?|pwd|token|secret|api[_-]?key|credential|auth)")
REDACTED = "<redacted>"


def is_secret_key(name):
    return isinstance(name, str) and (name in SECRET_KEYS or bool(SECRET_KEY_PATTERN.search(name)))


def mask_argv(argv):
    """Mask the values of credential-looking flags inside a command line.

    Conservative on purpose: it masks what follows a flag whose name looks like a secret,
    and the right-hand side of `--flag=value`. It does not try to guess whether a bare
    argument is a credential, because over-masking a command line makes a drift report
    useless for the thing it exists to show.
    """
    masked, mask_next = [], False
    for item in argv:
        text = str(item)
        if mask_next:
            masked.append(REDACTED)
            mask_next = False
            continue
        if text.startswith("-") and "=" in text:
            flag, _, _value = text.partition("=")
            masked.append(f"{flag}={REDACTED}" if is_secret_key(flag) else text)
            continue
        masked.append(text)
        mask_next = text.startswith("-") and is_secret_key(text)
    return masked


def is_secret_reference(value):
    return isinstance(value, str) and _SECRET_REF_RE.fullmatch(value) is not None


def literal_secret_paths(servers, prefix="servers"):
    """Dotted paths of secret-bearing values that are literals, not {env:VAR} references."""
    found = []
    if not isinstance(servers, dict):
        return found
    for server_id, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        for key in SECRET_KEYS:
            if key in entry and not is_secret_reference(entry[key]):
                found.append(f"{prefix}.{server_id}.{key}")
    return found


def _redact_proxy(value):
    if not isinstance(value, str) or "@" not in value:
        return value
    scheme, sep, rest = value.partition("://")
    if not sep:
        return "<redacted>"
    userinfo, at, hostpart = rest.rpartition("@")
    return f"{scheme}://<redacted>@{hostpart}" if at else value


def mask_url(value):
    """Mask the two places a URL hides a credential: userinfo and the query string.

    The host and the path survive, because those are what a drift report is about. A URL
    is printed by the audit, so `https://user:pass@host` and `?api_key=...` cannot go
    through untouched.
    """
    if not isinstance(value, str) or "://" not in value:
        return value
    masked = _redact_proxy(value)
    head, sep, query = masked.partition("?")
    if not sep:
        return masked
    parts = []
    for pair in query.split("&"):
        name, equals, _ = pair.partition("=")
        parts.append(f"{name}={REDACTED}" if equals and is_secret_key(name) else pair)
    return head + "?" + "&".join(parts)


def redact(data):
    """Copy of a configuration mapping with secret-bearing values masked.

    A key is secret when it is one of the declared ones or when its name looks like a
    credential, so a value under a name this schema never anticipated is still masked.
    """
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if is_secret_key(key) and isinstance(value, str) and not is_secret_reference(value):
                out[key] = REDACTED
            elif key == "proxy":
                out[key] = _redact_proxy(value)
            elif key in ("args", "command") and isinstance(value, list):
                out[key] = mask_argv(value)
            else:
                out[key] = redact(value)
        return out
    if isinstance(data, list):
        return [redact(item) for item in data]
    return data
