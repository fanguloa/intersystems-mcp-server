"""IRIS connections derived from the CCR portal's System records.

The CCR portal already knows every environment of a site's System: namespace and the
Management Portal link, whose host, port and web path prefix are exactly what an isc-ai
server entry needs. This module turns those records into a project plan; the CLI asks
for the site and writes it.

The portal is reached through the ccr-portal MCP server (the same one the AI clients
use), so the corporate SSO session, its browser and its guards stay in one place. A
saved record set (--from-json) gives the same plan offline.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from . import config as isc_config

CATALOG_RESOURCE = Path(__file__).resolve().parent / "resources" / "ccr-sites.json"
USER_CATALOG = Path.home() / ".isc-ai" / "ccr-sites.json"
ENVIRONMENTS = ("BASE", "TEST", "UAT", "LIVE")
PRODUCTION_ENVIRONMENTS = {"LIVE"}
SITE_RE = re.compile(r"^[A-Za-z0-9_-]{2,20}$")
SYSTEM_RE = re.compile(r"^[A-Za-z0-9_-]{2,20}$")
DEFAULT_PORTS = {"https": 443, "http": 80}


class TopologyError(Exception):
    """The portal data cannot be turned into IRIS connections."""


class PortalAuthRequired(TopologyError):
    """The ccr-portal session needs the corporate SSO login."""


# --------------------------------------------------------------------- catalog
def load_catalog(user_catalog=None):
    """Known sites and their CCR System codes: the packaged list, then the user's own file."""
    catalog = {}
    for path in (CATALOG_RESOURCE, Path(user_catalog) if user_catalog else USER_CATALOG):
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            for site, entry in (data.get("sites") or {}).items():
                catalog[site.upper()] = {"name": entry.get("name", ""), "systems": list(entry.get("systems", []))}
    return catalog


# --------------------------------------------------------------------- derivation
def layer_of(system_code):
    """'int' for an integration System (INT, I2024, INT2024), '' for TrakCare."""
    return "int" if re.match(r"^I(NT)?\d*$", system_code.upper()) else ""


def parse_link(url):
    """(scheme, host, port, path_prefix) of a portal link such as https://h/trakbase01/csp/sys/UtilHome.csp."""
    parts = urlsplit(url or "")
    if parts.scheme not in DEFAULT_PORTS or not parts.hostname:
        raise TopologyError(f"not an http(s) link: {url!r}")
    path = parts.path or "/"
    index = path.lower().find("/csp/")
    prefix = path[:index] if index >= 0 else ""
    return parts.scheme, parts.hostname, parts.port or DEFAULT_PORTS[parts.scheme], prefix.rstrip("/")


def plan(site, systems, credential_prefix=None, host_aliases=None, endpoints=None):
    """Plan the project entries for one site from its System summaries (ccr_lookup kind=system).

    Returns {site, systems, servers, canonical_base, credentials, skipped}. Server ids are
    <site><env>[int]; every environment of a layer shares <PREFIX>_<ENV>_USER/_PASSWORD.
    host_aliases ({portal host: name}) replaces a host the portal gives as an IP address
    when the certificate only names the DNS host. endpoints ({server_id: (scheme, host,
    port, prefix)}) replaces the whole address of one environment, e.g. when the portal
    link is a load balancer that does not route the IRIS REST API; namespace and role
    still come from the portal.
    """
    host_aliases = {k.lower(): v for k, v in (host_aliases or {}).items()}
    endpoints = dict(endpoints or {})
    site = site.upper()
    prefix = (credential_prefix or site).upper()
    servers, skipped, credentials, used = {}, [], [], []
    canonical = None
    # One System per layer: an older TrakCare (or INT) System of the same site would
    # produce the same server ids, so the highest version wins.
    newest = {}
    for system in systems:
        if system.get("deprecated") or system.get("active") is False:
            continue
        code = system.get("code") or ""
        version = int(re.sub(r"\D", "", code) or 0)
        layer = layer_of(code)
        if layer not in newest or version > newest[layer][0]:
            newest[layer] = (version, code)
    for system in systems:
        code = system.get("code") or ""
        if system.get("deprecated") or system.get("active") is False:
            skipped.append({"system": code, "reason": "inactive or deprecated in the CCR portal"})
            continue
        if newest.get(layer_of(code), (0, code))[1] != code:
            skipped.append({"system": code, "reason": f"superseded by {newest[layer_of(code)][1]}"})
            continue
        used.append(code)
        layer = layer_of(code)
        for env in ENVIRONMENTS:
            info = (system.get("environments") or {}).get(env)
            if info is None:
                continue
            link = info.get("management_portal")
            namespace = info.get("namespace")
            if not link or not namespace:
                skipped.append({"system": code, "environment": env,
                                "reason": "no namespace or Management Portal link in the CCR portal"})
                continue
            scheme, host, port, path_prefix = parse_link(link)
            host = host_aliases.get(host.lower(), host)
            server_id = f"{site}{env}{layer}".lower()
            if server_id in endpoints:
                scheme, host, port, path_prefix = endpoints[server_id]
            user_var, password_var = f"{prefix}_{env}_USER", f"{prefix}_{env}_PASSWORD"
            servers[server_id] = {
                "host": host, "port": port, "path_prefix": path_prefix, "scheme": scheme, "namespace": namespace,
                "env_role": env, "is_production": env in PRODUCTION_ENVIRONMENTS,
                "username": "{env:" + user_var + "}", "password": "{env:" + password_var + "}",
                "description": f"CCR {site}/{code} {env}" + (f" ({info['instance']})" if info.get("instance") else ""),
            }
            for var in (user_var, password_var):
                if var not in credentials:
                    credentials.append(var)
            if env == "BASE" and not layer and canonical is None:
                canonical = {"server_id": server_id, "host": host, "port": port, "path_prefix": path_prefix,
                             "namespace": namespace}
    if not servers:
        raise TopologyError(f"the CCR portal has no environment with a namespace and link for {site}")
    unknown = sorted(set(endpoints) - set(servers))
    if unknown:
        raise TopologyError(f"--endpoint names no planned server: {', '.join(unknown)} (planned: {', '.join(servers)})")
    return {"site": site, "systems": used, "servers": servers, "canonical_base": canonical,
            "credentials": credentials, "skipped": skipped}


def apply(data, result):
    """Merge a plan into the project policy dict (in place) and return it."""
    site = result["site"]
    data.setdefault("site", {})
    data["site"]["name"] = site.lower()
    data["site"].setdefault("ccr_id", {})
    data["site"]["ccr_id"]["prefix"] = site
    data["site"]["ccr_id"].setdefault("digits", 4)
    data["site"]["protocol_prefix"] = site
    if result.get("canonical_base"):
        data["canonical_base"] = dict(result["canonical_base"])
    data.setdefault("servers", {})
    for server_id, entry in result["servers"].items():
        data["servers"][server_id] = dict(entry)
    return data


# --------------------------------------------------------------------- portal client
def portal_command(environ=None):
    """(argv, extra_env) that launches the ccr-portal MCP server.

    ISC_AI_CCR_PORTAL_COMMAND (a path or a JSON argv) wins; then the 'ccr-portal' entry of
    the OpenCode configuration, which also carries the variables it needs; then PATH.
    """
    environ = os.environ if environ is None else environ
    override = environ.get("ISC_AI_CCR_PORTAL_COMMAND")
    if override:
        try:
            argv = json.loads(override)
        except ValueError:
            argv = [override]
        return ([str(a) for a in argv] if isinstance(argv, list) else [str(argv)]), {}
    config_home = Path(environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    for name in ("opencode.json", "opencode.jsonc"):
        path = config_home / "opencode" / name
        if not path.is_file():
            continue
        try:
            entry = (json.loads(path.read_text(encoding="utf-8")).get("mcp") or {}).get("ccr-portal") or {}
        except ValueError:
            continue
        command = entry.get("command")
        if isinstance(command, list) and command:
            extra = {k: str(v) for k, v in (entry.get("environment") or {}).items()
                     if isinstance(v, (str, int)) and not str(v).startswith("{env:")}
            return [str(c) for c in command], extra
    found = shutil.which("ccr-portal")
    if found:
        return [found], {}
    raise TopologyError("ccr-portal MCP server not found: install it, register it in OpenCode, or set "
                        "ISC_AI_CCR_PORTAL_COMMAND")


class PortalClient:
    """Minimal MCP stdio client for the ccr-portal server."""

    def __init__(self, argv, extra_env=None, timeout=180):
        env = dict(os.environ)
        env.update(extra_env or {})
        self._timeout = timeout
        self._id = 0
        self._proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env)
        self._request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                     "clientInfo": {"name": "isc-ai", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, message):
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def _request(self, method, params):
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise TopologyError("the ccr-portal MCP server closed the channel")
            reply = json.loads(line)
            if reply.get("id") == self._id:
                if "error" in reply:
                    raise TopologyError(f"ccr-portal: {reply['error'].get('message')}")
                return reply.get("result") or {}

    def tool(self, name, arguments):
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        text = "".join(part.get("text", "") for part in result.get("content") or [])
        try:
            data = json.loads(text)
        except ValueError:
            raise TopologyError(f"ccr-portal {name}: unexpected answer") from None
        if isinstance(data, dict) and data.get("error"):
            if data["error"] in ("AUTH_REQUIRED", "AUTH"):
                raise PortalAuthRequired(data.get("message") or "login required")
            raise TopologyError(f"ccr-portal {name}: {data.get('message') or data['error']}")
        return data

    def close(self):
        try:
            self._proc.stdin.close()
            self._proc.wait(timeout=30)
        except Exception:
            self._proc.kill()
        finally:
            self._proc.stdout.close()


def fetch_systems(client, site, codes, login=None):
    """System summaries for SITE/<code>; a code the portal does not know is reported, not fatal."""
    systems, missing = [], []
    for code in codes:
        arguments = {"kind": "system", "system_code": f"{site}/{code}"}
        try:
            systems.append(client.tool("ccr_lookup", arguments))
        except PortalAuthRequired:
            if login is None:
                raise
            login(client)
            systems.append(client.tool("ccr_lookup", arguments))
        except TopologyError as exc:
            if "not found" not in str(exc):
                raise
            missing.append(code)
    return systems, missing


def parse_aliases(values):
    """{host: name} from HOST=NAME pairs."""
    aliases = {}
    for value in values or []:
        host, sep, name = value.partition("=")
        if not sep or not host.strip() or not re.match(r"^[A-Za-z0-9.-]+$", name.strip()):
            raise TopologyError(f"not a HOST=NAME alias: {value!r}")
        aliases[host.strip().lower()] = name.strip()
    return aliases


def parse_endpoints(values):
    """{server_id: (scheme, host, port, path_prefix)} from SERVER_ID=URL pairs (URL up to the web prefix)."""
    endpoints = {}
    for value in values or []:
        server_id, sep, url = value.partition("=")
        parts = urlsplit(url.strip())
        if not sep or not server_id.strip() or parts.scheme not in DEFAULT_PORTS or not parts.hostname:
            raise TopologyError(f"not a SERVER_ID=http(s)://host[:port][/prefix] endpoint: {value!r}")
        endpoints[server_id.strip().lower()] = (parts.scheme, parts.hostname, parts.port or DEFAULT_PORTS[parts.scheme],
                                                parts.path.rstrip("/"))
    return endpoints


def validate_site(site):
    if not SITE_RE.match(site or ""):
        raise TopologyError(f"not a site code: {site!r}")
    return site.upper()


def validate_system(code):
    if not SYSTEM_RE.match(code or ""):
        raise TopologyError(f"not a CCR System code: {code!r}")
    return code.upper()


def project_policy(project_dir):
    """Current project policy dict; the project must exist (isc-ai init)."""
    path = Path(project_dir) / isc_config.PROJECT_CONFIG_RELATIVE
    if not path.is_file():
        raise TopologyError(f"no project configuration at {path}: run 'isc-ai init' first")
    return path, json.loads(path.read_text(encoding="utf-8"))
