"""Offline-only fixtures. Never imported by the MCP server."""

from contextlib import redirect_stderr
import io
import os
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch


# Configuration loads on server construction and on each tool call (never at
# import), and startup only under __main__. Block optional third-party imports, file reads,
# ambient proxy/HOME and network while importing the application as an extra guard.
# These sentinels live for the offline test process. A temporary patch.dict on
# sys.modules would remove the newly imported application modules on exit.
sys.modules["sockshandler"] = None
sys.modules["socks"] = None
with (patch.dict(os.environ, {}, clear=True),
      patch("builtins.open", side_effect=AssertionError("Unexpected import-time file access")),
      patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Network forbidden")),
      redirect_stderr(io.StringIO())):
    from isc_ai.server import AtelierClient, IRISMCPServer
    from isc_ai import config as iris_mcp_config
    from isc_ai.config import CanonicalBase, PackageGrant, SitePolicy, WriteException

from isc_ai import i18n
from isc_ai.ccr_discovery import (
    ACTIVE_NODE_SQL, CLASS_SQL, ENGINE_SQL, IDENTITY_SQL, METHOD_SQL, PROJECTION_SQL,
)


# Reference site fixture. Exercises every policy feature (canonical BASE, HA nodes,
# VIP and active-node UAT exceptions, per-role package grants, controlled mutation).
# It mirrors the first real deployment so the rendered facility digest can be
# pinned; tests import these names instead of repeating literals.
REF = {
    "ccr_prefix": "ACHS",
    "ccr_digits": 4,
    "protocol_prefix": "ACHS",
    "base_server": "achsbase",
    "base_host": "10.244.10.4",
    "base_prefix": "/trakbase01",
    "base_namespace": "BASE-ACHS",
    "uat_namespace": "UAT-ACHS",
    "ha_nodes": ("10.244.20.4", "10.244.20.5", "achsuat01", "achsuat02"),
    "clinical_vip": "10.244.20.6",
    "int_vip_prefix": "/clacintuat",
    "int_node_prefixes": ("/intuat01", "/intuat02"),
    "clinical_node_prefixes": ("/trakuat01", "/trakuat02"),
    "gateway_namespace": "HMFACHSGATEWAY_INT",
    "mutation_prefix": "Custom.ACHS",
    "mutation_source_root": "BASE-ACHS/src/Custom/ACHS",
}

GATEWAY_BASE_DOCUMENTS = (
    "Custom.ACHS.HMF.Gateway.INT.HL2751OUT.Transform.DTL.SDA3ToHL7.HL7251.DFTP03.cls",
    "Custom.ACHS.HMF.Gateway.INT.Transform.SDA3ToHL7.HL7251.SubTransform.ProcedureToFT1.cls",
    "Custom.ACHS.HMF.Gateway.INT.HL7251OUT.Transform.DTL.SDA3ToHL7.HL7251.ORMO01.cls",
)
GATEWAY_UAT_DOCUMENTS = (GATEWAY_BASE_DOCUMENTS[2],)

# Project directory the fixture pretends to live in (never touched on disk).
FIXTURE_PROJECT_DIR = Path(__file__).resolve().parent.parent


def reference_policy_data():
    """Project configuration mapping equivalent to REFERENCE_POLICY (for parse tests)."""
    return {
        "version": 1,
        "site": {
            "name": "reference",
            "ccr_id": {"prefix": REF["ccr_prefix"], "digits": REF["ccr_digits"]},
            "protocol_prefix": REF["protocol_prefix"],
        },
        "canonical_base": {
            "server_id": REF["base_server"], "host": REF["base_host"], "port": 80,
            "path_prefix": REF["base_prefix"], "namespace": REF["base_namespace"],
        },
        "routes": {
            "direct_ha_nodes": list(REF["ha_nodes"]),
            "alias_constraints": {"achsuat": {"host": REF["clinical_vip"], "path_prefix": ""}},
            "uat_write_exceptions": [
                {"label": "INT UAT VIP", "namespace": "INT", "hosts": [REF["clinical_vip"]],
                 "path_prefixes": [REF["int_vip_prefix"]], "requires_active_node": False},
                {"label": "INT UAT active node", "namespace": "INT",
                 "path_prefixes": list(REF["int_node_prefixes"]), "requires_active_node": True},
                {"label": "Gateway UAT active node", "namespace": REF["gateway_namespace"],
                 "path_prefixes": list(REF["clinical_node_prefixes"]), "requires_active_node": True},
            ],
        },
        "packages": {
            REF["base_namespace"]: [{"prefix": REF["mutation_prefix"]}],
            "INT": [
                {"prefix": "SAP"},
                {"prefix": "MPI.REST"},
                {"prefix": "SALDI", "roles": ["BASE"]},
                {"class": "INTPKG.FoundationProduction"},
            ],
            REF["gateway_namespace"]: (
                [{"document": doc, "roles": ["BASE"]} for doc in GATEWAY_BASE_DOCUMENTS]
                + [{"document": doc, "roles": ["UAT"]} for doc in GATEWAY_UAT_DOCUMENTS]
            ),
        },
        "mutation": {
            "class_prefix": REF["mutation_prefix"],
            "local_source_root": REF["mutation_source_root"],
        },
    }


REFERENCE_POLICY = iris_mcp_config.parse_policy(reference_policy_data(), FIXTURE_PROJECT_DIR)
UNCONFIGURED_POLICY = SitePolicy()


def policy_with(**changes):
    import dataclasses
    return dataclasses.replace(REFERENCE_POLICY, **changes)


class HermeticTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Language is process-wide state: reset it so a test that runs --lang es
        # cannot change what the next test asserts.
        previous_language = i18n.current()
        self.addCleanup(i18n.set_language, previous_language)
        i18n.set_language(i18n.LanguageChoice())
        patches = (
            patch.dict(os.environ, {"HOME": "HERMETIC-NO-HOME", "USERPROFILE": "HERMETIC-NO-HOME"}, clear=True),
            patch("builtins.open", side_effect=AssertionError("Test file/config access forbidden")),
            patch("socket.create_connection", side_effect=AssertionError("Network forbidden")),
            patch("socket.socket", side_effect=AssertionError("Network forbidden")),
            patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Network forbidden")),
            patch.object(IRISMCPServer, "load_configuration", side_effect=AssertionError("Real config forbidden")),
            patch("isc_ai.config.load_runtime_config", side_effect=AssertionError("Real config forbidden")),
            patch("isc_ai.server.log_debug"),
        )
        for guard in patches:
            guard.start()
            self.addCleanup(guard.stop)


def synthetic_client(namespace=REF["base_namespace"], role="BASE", engine=0, configured=0,
                     host="example.invalid", path_prefix="", production=False, ens_jobs=None,
                     policy=None):
    opener = Mock()
    opener.open.side_effect = AssertionError("Unscripted HTTP request")
    with patch.object(AtelierClient, "_create_opener", return_value=opener):
        client = AtelierClient(host, 80, path_prefix, namespace, "synthetic", "synthetic",
                               is_production=production, env_role=role,
                               policy=REFERENCE_POLICY if policy is None else policy)
    client._send_request = Mock(side_effect=metadata_responder(client.namespace, engine, configured, ens_jobs))
    return client


def envelope(rows):
    return 200, {"status": {"errors": [], "summary": ""}, "result": {"content": rows}}


def metadata_responder(namespace, engine=0, configured=0, ens_jobs=None):
    def respond(endpoint, method="GET", payload=None, **kwargs):
        if endpoint != "/action/query" or method != "POST" or payload.get("parameters") != []:
            raise AssertionError("Only scripted metadata queries are permitted")
        sql = payload["query"]
        if sql == IDENTITY_SQL:
            return envelope([{"namespace": namespace}])
        if sql == ENGINE_SQL:
            return envelope([{"namespace": namespace, "engine_count": engine}])
        if sql == ACTIVE_NODE_SQL:
            if ens_jobs is None:
                raise AssertionError("Unscripted active-node query")
            return envelope([{"namespace": namespace, "ens_jobs": ens_jobs}])
        if sql == PROJECTION_SQL:
            return envelope([{
                "namespace": namespace, "ROUTINE_SCHEMA": "SyntheticMetadata",
                "ROUTINE_NAME": "Extension_ExtensionEnabled",
                "EXTERNAL_NAME": "%Atelier.v1.Utils.Extension.ExtensionEnabled",
            }])
        if sql == METHOD_SQL:
            return envelope([{
                "namespace": namespace, "parent": "%Atelier.v1.Utils.Extension",
                "Name": "ExtensionEnabled", "SqlName": "Extension_ExtensionEnabled",
                "SqlProc": 1, "ClassMethod": 1,
            }])
        if sql == CLASS_SQL:
            return envelope([{
                "namespace": namespace, "Name": "%Atelier.v1.Utils.Extension",
                "SqlSchemaName": "SyntheticMetadata",
            }])
        if sql == 'SELECT $NAMESPACE AS namespace, "SyntheticMetadata"."Extension_ExtensionEnabled"() AS configured':
            return envelope([{"namespace": namespace, "configured": configured}])
        raise AssertionError("Unscripted SQL")
    return respond


def synthetic_server(client=None, servers=None, policy=None):
    server = object.__new__(IRISMCPServer)
    server.servers = servers if servers is not None else {"fake": {"namespace": REF["base_namespace"]}}
    server.policy = REFERENCE_POLICY if policy is None else policy
    server.runtime_config = None
    server.load_configuration = Mock()
    if client is not None:
        server.get_client = Mock(return_value=client)
    return server


GUARD_STATES = ("CONTROLLED_ACTIVE", "SUSPENDED", "ABSENT")


def guard_record(state):
    """Synthetic same-process guard evidence exactly as the probe returns it."""
    if state == "CONTROLLED_ACTIVE":
        return {"state": state, "engine": True, "binding": "TC.ChangeControl.CCR",
                "source_binding": "%Studio.SourceControl.ISC", "suspended": False,
                "suspend_level": "0", "suspension_scope": "guard-runner-process"}
    if state == "SUSPENDED":
        return {"state": state, "engine": True, "binding": "TC.ChangeControl.CCR",
                "source_binding": "%Studio.SourceControl.ISC", "suspended": True,
                "suspend_level": "1", "suspension_scope": "guard-runner-process"}
    if state == "ABSENT":
        return {"state": state, "engine": False, "binding": "", "source_binding": "",
                "suspended": False, "suspend_level": "0", "suspension_scope": "guard-runner-process"}
    raise AssertionError("Unscripted guard state")


def guard_report_line(state, engine=None, binding=None, source_binding=None,
                      suspended=None, suspend_level=None):
    """MCPGUARD record as the guard runner writes it (URL-encoded bindings)."""
    import urllib.parse
    defaults = guard_record(state) if state in GUARD_STATES else {
        "engine": False, "binding": "", "source_binding": "", "suspended": False, "suspend_level": "0",
    }
    engine = defaults["engine"] if engine is None else engine
    binding = defaults["binding"] if binding is None else binding
    source_binding = defaults["source_binding"] if source_binding is None else source_binding
    suspended = defaults["suspended"] if suspended is None else suspended
    suspend_level = defaults["suspend_level"] if suspend_level is None else suspend_level
    return (
        f"MCPGUARD|state={state}|engine={int(bool(engine))}"
        f"|binding={urllib.parse.quote(binding, safe='')}"
        f"|source_binding={urllib.parse.quote(source_binding, safe='')}"
        f"|suspended={int(bool(suspended))}|suspend_level={suspend_level}\n"
    )


def script_guard(client, state):
    """Make the client's same-process guard probe return a fixed admissible state.

    Only the probe is synthetic; the REST write transport, package catalog and
    route decisions stay real. Returns the Mock so tests can assert usage.
    """
    probe = Mock(return_value=guard_record(state))
    client._probe_same_process_ccr_state = probe
    return probe


def script_guarded_runner(client, output=""):
    """Capture code sent through the runtime runner (used by guarded CCR operations)."""
    captured = []

    def run(code):
        captured.append(code)
        return output

    client.execute_runtime_code = run
    return captured
