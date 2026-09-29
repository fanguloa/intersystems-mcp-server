"""Named, hermetic regression suite; never run the live test_mcp_server module."""

import base64
import copy
import inspect
import io
import json
import os
import urllib.error
import urllib.parse
from unittest.mock import Mock, patch, mock_open

from tests.hermetic_test_support import (
    AtelierClient, IRISMCPServer, HermeticTestCase, envelope, metadata_responder,
    synthetic_client, synthetic_server, GUARD_STATES, guard_record, guard_report_line,
    script_guard, script_guarded_runner, REFERENCE_POLICY, GATEWAY_BASE_DOCUMENTS, iris_mcp_config,
)
from isc_ai.ccr_discovery import (
    CLASS_SQL, ENGINE_SQL, IDENTITY_SQL, METHOD_SQL, PROJECTION_SQL, LocalPolicyError,
    unknown_record,
)


def runner_ok(output):
    encoded = base64.b64encode(output.encode("utf-8")).decode("ascii")
    return f"MCPOK|{encoded}"


CATALOGED = "Custom.ACHS.Tests.Sample.cls"


class DiscoveryTests(HermeticTestCase):
    def test_sufficient_absence_is_explicit_and_process_state_is_not_invented(self):
        status = synthetic_client().get_namespace_ccr_status()
        self.assertEqual(status["applicability"], "ABSENT")
        self.assertIs(status["subject_to_ccr"], False)
        self.assertIs(status["ccr_engine_present"], False)
        self.assertIs(status["sourcecontrol_configured"], False)
        self.assertTrue(status["identity_verified"])
        self.assertIsNone(status["sc_class"])
        self.assertIsNone(status["tc_binding"])
        self.assertIsNone(status["suspended"])
        self.assertIn("execution-process", status["suspension_scope"])
        self.assertTrue(status["observed_at"])

    def test_engine_and_sourcecontrol_combinations_cannot_prove_binding(self):
        for engine, configured in ((0, 1), (1, 0), (1, 1)):
            with self.subTest(engine=engine, configured=configured):
                status = synthetic_client(engine=engine, configured=configured).get_namespace_ccr_status()
                self.assertEqual(status["applicability"], "UNKNOWN")
                self.assertIsNone(status["subject_to_ccr"])
                self.assertIsNone(status["sc_class"])
                self.assertIn("binding probe", status["reason"])

    def test_legacy_false_override_and_clinical_labels_are_ignored_without_writeback(self):
        client = synthetic_client(engine=1, configured=1)
        client.ccr_affected = False
        server = synthetic_server(client, {"fake": {
            "namespace": "BASE-ACHS", "ccr_affected": False, "layer": "INT",
        }})
        server.config_path = "HERMETIC-NO-CONFIG"
        before = copy.deepcopy(server.servers)
        script_guard(client, "SUSPENDED")
        with patch("builtins.open", side_effect=AssertionError("No writeback")) as files:
            result = server.resolve_ccr_affected("fake", "BASE-ACHS", client)
            # The legacy flag never reaches the code-write gate; the guard decides.
            self.assertFalse(client.is_code_write_allowed(CATALOGED, "ACHS0875")[0])
            files.assert_not_called()
        self.assertIsNone(result["subject_to_ccr"])
        self.assertEqual(before, server.servers)

    def test_same_server_different_namespaces_and_repeat_reads_have_no_cache(self):
        first = synthetic_client("APPONE", engine=0, configured=0)
        second = synthetic_client("APPTWO", engine=1, configured=1)
        server = synthetic_server()
        a = server.resolve_ccr_affected("fake", "APPONE", first)
        b = server.resolve_ccr_affected("fake", "APPTWO", second)
        first._send_request.side_effect = metadata_responder("APPONE", 1, 0)
        c = server.resolve_ccr_affected("fake", "APPONE", first)
        self.assertEqual([a["applicability"], b["applicability"], c["applicability"]],
                         ["ABSENT", "UNKNOWN", "UNKNOWN"])
        self.assertEqual(first._send_request.call_count, 8)

    def test_each_query_requires_expected_namespace_identity(self):
        for bad_step in range(4):
            with self.subTest(step=bad_step):
                client = synthetic_client()
                normal = client._send_request.side_effect
                calls = []

                def wrong_identity(*args, **kwargs):
                    status, data = normal(*args, **kwargs)
                    if len(calls) == bad_step:
                        data["result"]["content"][0]["namespace"] = "OTHER"
                    calls.append(1)
                    return status, data

                client._send_request.side_effect = wrong_identity
                record = client.get_namespace_ccr_status()
                self.assertEqual(record["applicability"], "UNKNOWN")
                self.assertIsNone(record["subject_to_ccr"])
                self.assertEqual(len(calls), bad_step + 1)

    def test_malformed_counts_are_unknown_not_zero(self):
        # JSON booleans are valid: IRIS projects %Boolean SqlProc results as true/false.
        for field in ("engine_count", "configured"):
            for value in (None, "", "no", "0.0", -1, 2, 0.0, [], {}):
                with self.subTest(field=field, value=value):
                    kwargs = {"engine": value} if field == "engine_count" else {"configured": value}
                    result = synthetic_client(**kwargs).get_namespace_ccr_status()
                    self.assertIsNone(result["subject_to_ccr"])
                    self.assertEqual(result["applicability"], "UNKNOWN")

    def test_json_boolean_flags_are_accepted(self):
        absent = synthetic_client(engine=False, configured=False).get_namespace_ccr_status()
        self.assertEqual(absent["applicability"], "ABSENT")
        configured = synthetic_client(engine=False, configured=True).get_namespace_ccr_status()
        self.assertEqual(configured["applicability"], "UNKNOWN")
        self.assertIs(configured["sourcecontrol_configured"], True)

    def test_empty_missing_duplicate_and_malformed_rows_are_unknown_at_every_step(self):
        for bad_step in range(4):
            for bad_rows in ([], [{}], [None], [{}, {}], {"engine_count": 0}, None):
                with self.subTest(step=bad_step, rows=bad_rows):
                    client = synthetic_client()
                    normal = client._send_request.side_effect
                    calls = []

                    def malformed(*args, **kwargs):
                        result = normal(*args, **kwargs)
                        if len(calls) == bad_step:
                            result = envelope(bad_rows)
                        calls.append(1)
                        return result

                    client._send_request.side_effect = malformed
                    self.assertIsNone(client.get_namespace_ccr_status()["subject_to_ccr"])

    def test_denied_timeout_wrong_envelope_and_sql_errors_are_unknown(self):
        responses = (
            (401, {"error": "secret"}), (403, {}), (500, {}), (200, {}),
            (200, []), (200, {"result": {"content": []}}),
            (200, {"status": {"errors": [{"text": "secret"}]}, "result": {"content": []}}),
            (200, {"status": {"errors": [], "summary": "secret"}, "result": {"content": []}}),
        )
        for response in responses:
            with self.subTest(response=response):
                client = synthetic_client()
                client._send_request.side_effect = None
                client._send_request.return_value = response
                result = client.get_namespace_ccr_status()
                self.assertIsNone(result["subject_to_ccr"])
                self.assertNotIn("secret", json.dumps(result))
        for error in (TimeoutError("secret"), PermissionError("secret")):
            client = synthetic_client()
            client._send_request.side_effect = error
            self.assertIsNone(client.get_namespace_ccr_status()["subject_to_ccr"])

    def test_catalog_projection_requires_exact_external_identity_and_safe_schema(self):
        bad_fields = (
            {"EXTERNAL_NAME": "Other.Provider.ExtensionEnabled"},
            {"ROUTINE_NAME": "Other"},
            {"ROUTINE_SCHEMA": 'Injected";DELETE'}, {"ROUTINE_SCHEMA": ""},
        )
        for fields in bad_fields:
            with self.subTest(fields=fields):
                client = synthetic_client()
                normal = client._send_request.side_effect

                def bad_projection(*args, **kwargs):
                    status, data = normal(*args, **kwargs)
                    if kwargs["payload"]["query"] == PROJECTION_SQL:
                        data["result"]["content"][0].update(fields)
                    return status, data

                client._send_request.side_effect = bad_projection
                self.assertIsNone(client.get_namespace_ccr_status()["subject_to_ccr"])
                self.assertEqual(client._send_request.call_count, 3)

    def test_blank_external_name_uses_exact_compiled_method_metadata(self):
        for external_name in ("", None):
            with self.subTest(external_name=external_name):
                client = synthetic_client()
                normal = client._send_request.side_effect

                def blank_external_name(*args, **kwargs):
                    status, data = normal(*args, **kwargs)
                    if kwargs["payload"]["query"] == PROJECTION_SQL:
                        data["result"]["content"][0]["EXTERNAL_NAME"] = external_name
                    return status, data

                client._send_request.side_effect = blank_external_name
                result = client.get_namespace_ccr_status()
                queries = [call.kwargs["payload"]["query"]
                           for call in client._send_request.call_args_list]
                self.assertEqual(result["applicability"], "ABSENT")
                self.assertIn(METHOD_SQL, queries)
                self.assertIn(CLASS_SQL, queries)
                self.assertIn('SELECT $NAMESPACE AS namespace, "SyntheticMetadata".'
                              '"Extension_ExtensionEnabled"() AS configured', queries)

    def test_blank_external_name_with_invalid_method_metadata_is_unknown(self):
        invalid_rows = (
            [{"namespace": "BASE-ACHS", "parent": "Other.Parent", "Name": "ExtensionEnabled",
              "SqlName": "Extension_ExtensionEnabled", "SqlProc": 1, "ClassMethod": 1}],
            [{"namespace": "BASE-ACHS", "parent": "%Atelier.v1.Utils.Extension", "Name": "ExtensionEnabled",
              "SqlName": "Other", "SqlProc": 1, "ClassMethod": 1}],
            [{"namespace": "BASE-ACHS", "parent": "%Atelier.v1.Utils.Extension", "Name": "ExtensionEnabled",
              "SqlName": "Extension_ExtensionEnabled", "SqlProc": 0, "ClassMethod": 1}],
            [{"namespace": "BASE-ACHS", "parent": "%Atelier.v1.Utils.Extension", "Name": "ExtensionEnabled",
              "SqlName": "Extension_ExtensionEnabled", "SqlProc": 1, "ClassMethod": 0}],
            [],
            [{"namespace": "BASE-ACHS"}],
            [{"namespace": "BASE-ACHS"}, {"namespace": "BASE-ACHS"}],
        )
        for rows in invalid_rows:
            with self.subTest(rows=rows):
                client = synthetic_client()
                normal = client._send_request.side_effect

                def invalid_method(*args, **kwargs):
                    sql = kwargs["payload"]["query"]
                    status, data = normal(*args, **kwargs)
                    if sql == PROJECTION_SQL:
                        data["result"]["content"][0]["EXTERNAL_NAME"] = None
                    elif sql == METHOD_SQL:
                        return envelope(rows)
                    return status, data

                client._send_request.side_effect = invalid_method
                result = client.get_namespace_ccr_status()
                self.assertEqual(result["applicability"], "UNKNOWN")
                self.assertIsNone(result["subject_to_ccr"])
                self.assertEqual(client._send_request.call_count, 4)

    def test_blank_external_name_requires_correlated_compiled_class_schema(self):
        invalid_rows = (
            [{"namespace": "BASE-ACHS", "Name": "%Atelier.v1.Utils.Extension",
              "SqlSchemaName": "UnrelatedSchema"}],
            [{"namespace": "BASE-ACHS", "Name": "Other.Class",
              "SqlSchemaName": "SyntheticMetadata"}],
            [{"namespace": "OTHER", "Name": "%Atelier.v1.Utils.Extension",
              "SqlSchemaName": "SyntheticMetadata"}],
            [{"namespace": "BASE-ACHS", "Name": "%Atelier.v1.Utils.Extension",
              "SqlSchemaName": "SyntheticMetadata", "unexpected": True}],
            [],
            [{"namespace": "BASE-ACHS"}],
            [{"namespace": "BASE-ACHS", "Name": "%Atelier.v1.Utils.Extension",
              "SqlSchemaName": "SyntheticMetadata"}] * 2,
        )
        for rows in invalid_rows:
            with self.subTest(rows=rows):
                client = synthetic_client()
                normal = client._send_request.side_effect

                def invalid_class(*args, **kwargs):
                    sql = kwargs["payload"]["query"]
                    status, data = normal(*args, **kwargs)
                    if sql == PROJECTION_SQL:
                        data["result"]["content"][0]["EXTERNAL_NAME"] = ""
                    elif sql == CLASS_SQL:
                        return envelope(rows)
                    return status, data

                client._send_request.side_effect = invalid_class
                result = client.get_namespace_ccr_status()
                self.assertEqual(result["applicability"], "UNKNOWN")
                self.assertIsNone(result["subject_to_ccr"])
                self.assertEqual(client._send_request.call_count, 5)

    def test_projection_qualified_name_comes_from_verified_catalog_row(self):
        client = synthetic_client()
        result = client.get_namespace_ccr_status()
        query = client._send_request.call_args.kwargs["payload"]["query"]
        self.assertIn('"SyntheticMetadata"."Extension_ExtensionEnabled"()', query)
        self.assertEqual(result["sourcecontrol_projection"], "SyntheticMetadata.Extension_ExtensionEnabled")

    def test_namespace_tool_returns_structured_unknown_as_informational_only(self):
        client = synthetic_client(engine=1, configured=1)
        result = synthetic_server(client).handle_tool_call(
            "iris_ccr_namespace_status", {"server_id": "fake", "namespace": "BASE-ACHS"})
        self.assertIsNone(result["structuredContent"]["subject_to_ccr"])
        text = result["content"][0]["text"]
        # UNKNOWN is the external discovery applicability, never an authorization verdict.
        self.assertEqual(result["structuredContent"]["applicability"], "UNKNOWN")
        self.assertIn("Informational only", text)
        self.assertIn("same-process CCR guard", text)
        self.assertIn('"subject_to_ccr": null', text)
        self.assertNotIn("Afecto a CCR**: No", text)
        self.assertNotIn("denied", text.lower())
        self.assertLess(len(text), 2500)

    def test_code_write_gate_never_consults_external_discovery(self):
        client = synthetic_client(engine=1)
        script_guard(client, "ABSENT")
        with patch.object(client, "get_namespace_ccr_status",
                          side_effect=AssertionError("external discovery must not gate writes")):
            synthetic_server(client)  # discovery tool is the only consumer of that method
            self.assertTrue(client.is_code_write_allowed(CATALOGED)[0])
            self.assertTrue(client.is_read_allowed()[0])
            self.assertTrue(client.is_runtime_operation_allowed()[0])


class MutationTests(HermeticTestCase):
    """CODE WRITE authorization is decided by the same-process CCR guard.

    The external namespace discovery (engine=1/configured=1 -> UNKNOWN) is
    informational and must neither authorize nor deny any write.
    """

    def assert_only_metadata(self, client):
        for call in client._send_request.call_args_list:
            self.assertEqual(call.args[0], "/action/query")
            self.assertEqual(call.kwargs["method"], "POST")
            self.assertEqual(call.kwargs["payload"]["parameters"], [])
            query = call.kwargs["payload"]["query"]
            self.assertTrue(query in (IDENTITY_SQL, ENGINE_SQL, PROJECTION_SQL, METHOD_SQL, CLASS_SQL)
                            or query == 'SELECT $NAMESPACE AS namespace, "SyntheticMetadata"."Extension_ExtensionEnabled"() AS configured')

    def assert_no_document_mutation(self, client):
        for call in client._send_request.call_args_list:
            self.assertNotIn(call.kwargs.get("method"), ("PUT", "DELETE"))
            self.assertNotEqual(call.args[0], "/action/compile")

    def test_suspended_guard_blocks_every_role_and_promotion(self):
        for role in ("BASE", "UAT", "TEST", ""):
            for promotion in (False, True):
                for tracking in (None, "ACHS0875"):
                    with self.subTest(role=role, promotion=promotion, tracking=tracking):
                        client = synthetic_client(role=role, engine=1)
                        script_guard(client, "SUSPENDED")
                        allowed, reason = client.is_code_write_allowed(
                            CATALOGED, tracking, promotion=promotion)
                        self.assertFalse(allowed)
                        self.assertNotIn("UNKNOWN", reason)
                        self.assert_only_metadata(client)

    def test_external_discovery_records_never_authorize(self):
        client = synthetic_client()
        script_guard(client, "SUSPENDED")
        for record in ({}, {"subject_to_ccr": False}, {"applicability": "ACTIVE", "suspended": False},
                       {"applicability": "ABSENT", "discovery_status": "SUFFICIENT_ABSENCE"},
                       unknown_record(client.namespace, client.base_url)):
            with patch.object(client, "get_namespace_ccr_status", return_value=record):
                self.assertFalse(client.is_code_write_allowed(CATALOGED, "ACHS0875")[0])

    def test_guard_probe_rejects_inconsistent_or_unknown_records(self):
        malformed = (
            "",
            "synthetic",
            "MCPGUARD|state=UNKNOWN|engine=1|binding=|source_binding=|suspended=0|suspend_level=0\n",
            "MCPGUARD|state=MISMATCH|engine=1|binding=|source_binding=|suspended=0|suspend_level=0\n",
            guard_report_line("CONTROLLED_ACTIVE", suspended=True),
            guard_report_line("CONTROLLED_ACTIVE", suspend_level="1"),
            guard_report_line("CONTROLLED_ACTIVE", binding="TC.ChangeControl.Other"),
            guard_report_line("CONTROLLED_ACTIVE", source_binding="TC.SourceControl.Edition"),
            guard_report_line("CONTROLLED_ACTIVE", engine=False),
            guard_report_line("ABSENT", engine=True),
            guard_report_line("ABSENT", binding="TC.ChangeControl.CCR"),
            guard_report_line("ABSENT", source_binding="%Studio.SourceControl.ISC"),
            guard_report_line("SUSPENDED", suspended=False, suspend_level="0"),
            guard_report_line("SUSPENDED", binding=""),
            guard_report_line("ABSENT") + guard_report_line("ABSENT"),
            "MCPGUARD|state=ABSENT|engine=0|binding=|source_binding=|suspended=0\n",
            "MCPGUARD|state=ABSENT|engine=0|binding=|source_binding=|suspended=0|suspend_level=0|extra=1\n",
        )
        for output in malformed:
            with self.subTest(output=output):
                client = synthetic_client()
                client.execute_runtime_code = Mock(return_value=output)
                with self.assertRaisesRegex(LocalPolicyError, "admissible state"):
                    client._probe_same_process_ccr_state()
                allowed, reason = client.is_code_write_allowed(CATALOGED, "ACHS0875")
                self.assertFalse(allowed)
                self.assertIn("blocked", reason)

    def test_guard_probe_parses_exactly_the_three_admissible_states(self):
        for state in GUARD_STATES:
            with self.subTest(state=state):
                client = synthetic_client()
                client.execute_runtime_code = Mock(return_value=guard_report_line(state))
                record = client._probe_same_process_ccr_state()
                self.assertEqual(record, guard_record(state))
                code = client.execute_runtime_code.call_args.args[0]
                self.assertIn('%ExistsId("TC.ChangeControl.Interface")', code)
                self.assertIn('$ClassMethod("TC.ChangeControl.Interface", "ChangeControlClassGet")', code)
                self.assertIn('+$ClassMethod("TC.ChangeControl.Interface", "GetSuspendLevel")', code)
                self.assertIn('+$ClassMethod("TC.ChangeControl.Interface", "IsSuspended")', code)
                self.assertIn("SourceControlClassGet()", code)
                self.assertIn('Write "MCPGUARD|state="', code)
                self.assertNotIn("##class(TC.", code)
                self.assertNotRegex(code, r"(?m)^\s*New\b")
                self.assertEqual(code.count("{"), code.count("}"))

    def test_production_env_override_is_rejected_before_any_request(self):
        for client in (synthetic_client(role="LIVE"), synthetic_client(production=True),
                       synthetic_client(namespace="APP-PROD")):
            client._probe_same_process_ccr_state = Mock(side_effect=AssertionError("guard must not run"))
            with patch.dict(os.environ, {"IRIS_ALLOW_PROD": "1"}):
                self.assertFalse(client.is_read_allowed()[0])
                self.assertFalse(client.is_runtime_operation_allowed()[0])
                self.assertFalse(client.is_code_write_allowed(CATALOGED, "ACHS0875")[0])
                self.assertFalse(client.is_code_write_allowed(CATALOGED, "ACHS0875", promotion=True)[0])
                self.assertFalse(client.is_code_write_route_allowed()[0])
                client._send_request.assert_not_called()

    def test_uncataloged_package_blocks_before_the_guard_runs(self):
        for name in ("Custom.Test.cls", "Custom.SITE.Shared.cls", "%Library.String.cls", "Ens.Director.cls",
                     "web.MRAdm.cls", "Region.Thing.cls", "MCP.CCRMutation.cls", "Custom.ACHS.MCP.CCRMutation.cls",
                     "User.McpTmpRun0123456789ab.cls", "INTPKG.FoundationProduction.cls", "SAP.Api.cls"):
            for state in GUARD_STATES:
                with self.subTest(name=name, state=state):
                    client = synthetic_client()
                    probe = script_guard(client, state)
                    allowed, reason = client.is_code_write_allowed(name, "ACHS0875")
                    self.assertFalse(allowed)
                    self.assertIn("not cataloged", reason)
                    probe.assert_not_called()
                    client._send_request.assert_not_called()

    def test_catalog_projection_follows_namespace_and_role(self):
        cases = (
            ("BASE-ACHS", "BASE", "Custom.ACHS.Tests.Sample.cls", True),
            ("BASE-ACHS", "BASE", "Custom.ACHS.web.Handler.mac", True),
            ("BASE-ACHS", "BASE", "Custom.ACHS.MCP.CCRMutation.cls", False),
            ("BASE-ACHS", "BASE", "Custom.MotorSDA.Integration.HS.Facade.CoreEntity.cls", False),
            ("BASE-ACHS", "BASE", "SAP.Api.cls", False),
            ("INT", "BASE", "SAP.Api.Rest.cls", True),
            ("INT", "UAT", "SAP.Api.Rest.cls", True),
            ("INT", "BASE", "MPI.REST.Lookup.cls", True),
            ("INT", "UAT", "MPI.REST.Lookup.cls", True),
            ("INT", "BASE", "SALDI.Data.CuentaEstado.cls", True),
            ("INT", "BASE", "SALDI.Tests.OutboundDFTGateTest.cls", True),
            ("INT", "UAT", "SALDI.Data.CuentaEstado.cls", False),
            ("INT", "BASE", "SALDIX.Other.cls", False),
            ("BASE-ACHS", "BASE", "SALDI.Data.CuentaEstado.cls", False),
            ("INT", "BASE", "INTPKG.FoundationProduction.cls", True),
            ("INT", "UAT", "INTPKG.FoundationProduction.cls", True),
            ("INT", "BASE", "INTPKG.Rule.HMFRouter.cls", False),
            ("INT", "BASE", "TC.hmf.System.Production.cls", False),
            ("INT", "BASE", "Custom.ACHS.Tests.Sample.cls", False),
            ("UAT-ACHS", "UAT", "Custom.ACHS.Tests.Sample.cls", False),
            ("OTHER", "BASE", "Custom.ACHS.Tests.Sample.cls", False),
        )
        for namespace, role, name, expected in cases:
            with self.subTest(namespace=namespace, role=role, name=name):
                # Hermetic: the built-in catalog must not be widened by the process env
                # or by the real project .env file.
                with patch.dict(os.environ, {"IRIS_EXTRA_PACKAGES": ""}, clear=False):
                    client = synthetic_client(namespace=namespace, role=role)
                    self.assertIs(client._cataloged_package_allowed(name), expected)

    def test_extra_packages_from_environment_are_namespace_scoped_and_base_only(self):
        raw = "BASE-ACHS:Custom.MotorSDA, Region.CLXX ,MCP.Mutate,%SYS,bad name,MCP;OTHER-NS:Custom.Other"
        target = "Custom.MotorSDA.Integration.HS.Facade.CoreEntity.cls"
        # The override is applied once at configuration load, never read by clients.
        policy = REFERENCE_POLICY.with_extra_packages(raw)
        base = synthetic_client(namespace="BASE-ACHS", role="BASE", policy=policy)
        self.assertTrue(base._cataloged_package_allowed(target))
        self.assertFalse(base._cataloged_package_allowed("Custom.MotorSDA.cls"))
        self.assertFalse(base._cataloged_package_allowed("Region.CLXX.Integration.HS.Facade.UDF.Core.cls"))
        self.assertFalse(base._cataloged_package_allowed("MCP.Mutate.Runner.cls"))
        self.assertFalse(base._cataloged_package_allowed("MCP.CCRMutation.cls"))
        self.assertFalse(base._cataloged_package_allowed("Custom.Other.Thing.cls"))
        other = synthetic_client(namespace="OTHER-NS", role="BASE", policy=policy)
        self.assertTrue(other._cataloged_package_allowed("Custom.Other.Thing.cls"))
        self.assertFalse(other._cataloged_package_allowed(target))
        for namespace, role in (("UAT-ACHS", "UAT"), ("INT", "BASE"), ("INT", "UAT"), ("BASE-ACHS", "UAT")):
            client = synthetic_client(namespace=namespace, role=role, policy=policy)
            self.assertFalse(client._cataloged_package_allowed(target))
        with patch.dict(os.environ, {"IRIS_EXTRA_PACKAGES": raw}, clear=False):
            # A client built with the base policy is not widened by the process env.
            base = synthetic_client(namespace="BASE-ACHS", role="BASE")
            self.assertFalse(base._cataloged_package_allowed(target))

    def test_extra_packages_parser_drops_invalid_and_forbidden_entries(self):
        raw = "# comment;BASE-ACHS:Custom.MotorSDA,Region.CLXX,MCP.Mutate,%SYS,bad name,MCP;:Nope;OTHER-NS:Custom.Other"
        self.assertEqual(
            iris_mcp_config.parse_extra_packages(raw),
            [("BASE-ACHS", "Custom.MotorSDA"), ("OTHER-NS", "Custom.Other")],
        )
        self.assertEqual(iris_mcp_config.parse_extra_packages(None), [])
        self.assertEqual(iris_mcp_config.parse_extra_packages(""), [])

    def test_hmf_achsgateway_int_catalog_is_exact_and_base_only(self):
        allowed_documents = GATEWAY_BASE_DOCUMENTS
        client = synthetic_client(namespace="HMFACHSGATEWAY_INT", role="BASE")
        base_grants = {grant.document for grant in REFERENCE_POLICY.packages["HMFACHSGATEWAY_INT"]
                       if grant.roles == frozenset({"BASE"})}
        self.assertEqual(base_grants, frozenset(allowed_documents))
        for name in allowed_documents:
            with self.subTest(name=name):
                self.assertTrue(client._cataloged_package_allowed(name))
        # UAT grant is a separate, narrower list (one document only).
        uat_grants = {grant.document for grant in REFERENCE_POLICY.packages["HMFACHSGATEWAY_INT"]
                      if grant.roles == frozenset({"UAT"})}
        self.assertEqual(uat_grants, frozenset({allowed_documents[2]}))
        uat = synthetic_client(namespace="HMFACHSGATEWAY_INT", role="UAT")
        self.assertTrue(uat._cataloged_package_allowed(allowed_documents[2]))

        blocked = (
            ("HMFACHSGATEWAY_INT", "UAT", allowed_documents[0]),
            ("HMFACHSGATEWAY_INT", "UAT", allowed_documents[1]),
            ("HMFACHSGATEWAY_INT", "BASE", allowed_documents[0][:-4] + ".mac"),
            ("HMFACHSGATEWAY_INT", "BASE", "Custom.ACHS.HMF.Gateway.INT.Sibling.cls"),
            ("HMFACHSGATEWAY_INT", "BASE", "Custom.ACHS.HMF.Gateway.INT.*.cls"),
            ("INT", "BASE", allowed_documents[1]),
        )
        for namespace, role, name in blocked:
            with self.subTest(namespace=namespace, role=role, name=name):
                client = synthetic_client(namespace=namespace, role=role)
                self.assertFalse(client._cataloged_package_allowed(name))

    def test_uncataloged_hmf_sibling_public_mutation_paths_have_zero_effects(self):
        sibling = "Custom.ACHS.HMF.Gateway.INT.Sibling"
        cases = {
            "iris_save_class": {"content": "Class Custom.ACHS.HMF.Gateway.INT.Sibling {}"},
            "iris_save_class_from_file": {"file_path": "MUST-NOT-READ"},
            "iris_patch_class": {"old_string": "a", "new_string": "b"},
            "iris_ccr_checkout": {"ccr_id": "ACHS0875"},
            "iris_ccr_undocheckout": {"ccr_id": "ACHS0875"},
        }
        for tool, extra in cases.items():
            with self.subTest(tool=tool):
                client = synthetic_client(namespace="HMFACHSGATEWAY_INT", role="BASE")
                client.execute_runtime_code = Mock(side_effect=AssertionError("runner must not start"))
                server = synthetic_server(client)
                with patch("builtins.open") as files, patch("os.path.exists") as exists:
                    result = server.handle_tool_call(tool, {
                        "server_id": "fake", "namespace": "HMFACHSGATEWAY_INT",
                        "class_name": sibling, **extra,
                    })
                    files.assert_not_called()
                    exists.assert_not_called()
                self.assertTrue(result.get("isError"), result)
                self.assertIn("not cataloged", result["content"][0]["text"])
                client.execute_runtime_code.assert_not_called()
                client._send_request.assert_not_called()
                client.opener.open.assert_not_called()

    def test_real_lowlevel_entrypoints_block_before_any_mutating_request(self):
        operations = (
            lambda c: c.save_document(CATALOGED, ["synthetic"], ccr_id="ACHS0875"),
            lambda c: c.compile_document(CATALOGED, ccr_id="ACHS0875"),
            lambda c: c.delete_document(CATALOGED),
            lambda c: c.run_write_sql("UPDATE %notrigger SS_UserDefWindow SET WIN_GenerateError = null WHERE WIN_Code=?", ["X"]),
            lambda c: c.ccr_commit("ACHS0875", [1], "synthetic-token"),
            lambda c: c.ccr_download_load(1, load=False),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                client = synthetic_client(engine=1, configured=1)
                script_guard(client, "SUSPENDED")
                client.execute_runtime_code = Mock(side_effect=AssertionError("runner must not start"))
                with self.assertRaises((LocalPolicyError, RuntimeError)):
                    operation(client)
                self.assert_only_metadata(client)

    def test_runner_embedded_operations_place_guard_before_body(self):
        client = synthetic_client()
        captured = script_guarded_runner(client, "ERROR_CONTROL|state=SUSPENDED|message=blocked")
        self.assertFalse(client.ccr_checkout("Custom.ACHS.Tests.Sample", "ACHS0875")[0])
        self.assertFalse(client.ccr_undocheckout("Custom.ACHS.Tests.Sample", "ACHS0875")[0])
        self.assertEqual(len(captured), 2)
        for code in captured:
            guard_end = code.index("operation not executed")
            self.assertIn('If mcpGuardState \'= "CONTROLLED_ACTIVE" {', code[:guard_end])
            self.assertGreater(code.index("SourceControlClassGet()\n"), 0)
            self.assertLess(code.index('Set mcpGuardState = "MISMATCH"'), code.index("Set className ="))
            self.assertIn("ERROR_CONTROL|state=", code[:code.index("Set className =")])

    def test_actual_tool_dispatch_rejects_all_code_write_routes_when_suspended(self):
        cases = {
            "iris_save_class": {"class_name": "Custom.ACHS.Tests.Sample", "content": "synthetic", "ccr_id": "ACHS0875"},
            "iris_save_class_from_file": {"class_name": "Custom.ACHS.Tests.Sample", "file_path": "MUST-NOT-READ", "ccr_id": "ACHS0875"},
            "iris_patch_class": {"class_name": "Custom.ACHS.Tests.Sample", "old_string": "a", "new_string": "b", "ccr_id": "ACHS0875"},
            "iris_compile_class": {"class_name": "Custom.ACHS.Tests.Sample"},
            "iris_delete_class": {"class_name": "Custom.ACHS.Tests.Sample"},
            "iris_ccr_checkout": {"class_name": "Custom.ACHS.Tests.Sample", "ccr_id": "ACHS0875"},
            "iris_ccr_undocheckout": {"class_name": "Custom.ACHS.Tests.Sample", "ccr_id": "ACHS0875"},
            "iris_ccr_checkout_ct": {"class_name": "Synthetic.Table", "ids": ["1"], "ccr_id": "ACHS0875"},
            "iris_ccr_commit": {"ccr_id": "ACHS0875", "change_ids": [1]},
            "iris_ccr_reupload_itemset": {"itemset_id": 1, "ccr_id": "ACHS0875", "upload_token": "t"},
            "iris_ccr_create": {"title": "synthetic", "description": "synthetic"},
            "iris_ccr_download_load": {"itemset_id": 1},
            "iris_unlock_questionnaire": {"win_code": "SYNTHETIC"},
        }
        for role in ("BASE", "UAT"):
            for name, args in cases.items():
                with self.subTest(role=role, name=name):
                    client = synthetic_client(role=role, engine=1, configured=1)
                    script_guard(client, "SUSPENDED")
                    client.execute_runtime_code = Mock(
                        return_value="ERROR_CONTROL|state=SUSPENDED|message=blocked")
                    with patch("builtins.open", side_effect=AssertionError("MUST-NOT-READ")), \
                            patch("os.path.exists", return_value=True):
                        result = synthetic_server(client).handle_tool_call(name, {
                            "server_id": "fake", "namespace": "BASE-ACHS", **args,
                            "ccr_affected": False, "discovery": {"applicability": "ACTIVE", "suspended": False},
                        })
                    self.assertTrue(result.get("isError"), (name, result))
                    text = result["content"][0]["text"]
                    self.assertNotIn("UNKNOWN", text)
                    self.assert_no_document_mutation(client)

    def test_direct_runner_is_not_gated_by_external_discovery(self):
        # engine=1/configured=1 makes external discovery UNKNOWN; the RUNTIME gate ignores it.
        client = synthetic_client(role="BASE", engine=1, configured=1)
        client._probe_same_process_ccr_state = Mock(side_effect=AssertionError("guard is not a runtime gate"))
        client.get_namespace_ccr_status = Mock(side_effect=AssertionError("discovery is not a runtime gate"))

        def send(endpoint, method="GET", payload=None, **kwargs):
            if endpoint == "/action/query" and payload["query"] == AtelierClient._EPHEMERAL_CHANGE_SQL:
                return envelope([])
            if method == "PUT":
                return 201, {}
            if endpoint == "/action/compile":
                return 200, {"status": {"errors": [], "summary": ""}}
            if endpoint == "/action/query":
                return envelope([{"result": runner_ok("synthetic-output")}])
            if method == "DELETE":
                return 200, {}
            if method == "GET":
                return 404, {}
            raise AssertionError("Unexpected runner request")

        client._send_request.side_effect = send
        with patch("uuid.uuid4", return_value=Mock(hex="0123456789abfedcba")):
            result = synthetic_server(client).handle_tool_call("iris_execute_objectscript", {
                "server_id": "fake", "code": "Write \"synthetic\", !",
            })
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["content"][0]["text"], "synthetic-output")

    def test_direct_runner_blocks_production_and_unknown_roles_before_network(self):
        clients = (
            synthetic_client(role="LIVE", engine=1, configured=1),
            synthetic_client(role="PROD", engine=1, configured=1),
            synthetic_client(role="BASE", production=True, engine=1, configured=1),
            synthetic_client(namespace="APP-PROD", role="BASE", engine=1, configured=1),
            synthetic_client(role="", engine=1, configured=1),
            synthetic_client(role="STAGING", engine=1, configured=1),
        )
        for client in clients:
            with self.subTest(role=client.env_role, namespace=client.namespace):
                result = synthetic_server(client).handle_tool_call("iris_execute_objectscript", {
                    "server_id": "fake", "code": "Set ^Synthetic=1",
                })
                self.assertTrue(result.get("isError"))
                client._send_request.assert_not_called()

    def test_direct_runner_accepts_only_catalog_nonproduction_roles(self):
        class_name = "User.McpTmpRun0123456789ab"
        for role in ("BASE", "DEV", "UAT", "QA", "TEST", "SCRATCH", "TRAIN"):
            with self.subTest(role=role):
                client = synthetic_client(role=role, engine=1, configured=1)
                client._require_ephemeral_runner(class_name)
                client._send_request.assert_not_called()

        client = synthetic_client(role="BASE", engine=1, configured=1)
        client.base_url = "http://changed.invalid/api/atelier/v1/BASE-ACHS"
        with self.assertRaisesRegex(LocalPolicyError, "Target changed"):
            client._require_ephemeral_runner(class_name)
        client._send_request.assert_not_called()

    def test_direct_runner_private_lifecycle_rejects_non_generated_names_before_network(self):
        operations = (
            lambda client, name: client._ephemeral_runner_save(name, []),
            lambda client, name: client._ephemeral_runner_compile(name),
            lambda client, name: client._ephemeral_runner_query(name),
            lambda client, name: client._ephemeral_runner_delete(name),
        )
        invalid = (
            "User.McpTmpRun0123456789AB", "User.McpTmpRun0123456789ab.cls",
            "Other.McpTmpRun0123456789ab", "User.McpTmpRun0123456789abc",
            "User.McpTmpRun../../", "User.McpTmpRun", None,
        )
        for operation in operations:
            for name in invalid:
                with self.subTest(operation=operation, name=name):
                    client = synthetic_client(role="BASE", engine=1, configured=1)
                    with self.assertRaises(LocalPolicyError):
                        operation(client, name)
                    client._send_request.assert_not_called()

    def test_direct_runner_compile_requires_strict_success_envelope(self):
        malformed = (
            (500, {"status": {"errors": [], "summary": ""}}),
            (200, []),
            (200, {}),
            (200, {"status": []}),
            (200, {"status": {}}),
            (200, {"status": {"summary": ""}}),
            (200, {"status": {"errors": []}}),
            (200, {"status": {"errors": None, "summary": ""}}),
            (200, {"status": {"errors": {}, "summary": ""}}),
            (200, {"status": {"errors": ["compile failed"], "summary": ""}}),
            (200, {"status": {"errors": [], "summary": "warning"}}),
        )
        for response in malformed:
            with self.subTest(response=response):
                client = synthetic_client(role="BASE", engine=1, configured=1)
                client._send_request.side_effect = None
                client._send_request.return_value = response
                with self.assertRaisesRegex(LocalPolicyError, "compilation failed"):
                    client._ephemeral_runner_compile("User.McpTmpRun0123456789ab")

        for summary in (None, ""):
            with self.subTest(summary=summary):
                client = synthetic_client(role="BASE", engine=1, configured=1)
                client._send_request.side_effect = None
                client._send_request.return_value = (
                    200, {"status": {"errors": [], "summary": summary}}
                )
                self.assertIsNone(
                    client._ephemeral_runner_compile("User.McpTmpRun0123456789ab")
                )

    def test_direct_runner_malformed_compile_never_invokes_execute_sql(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        calls = []

        def send(endpoint, method="GET", payload=None, **kwargs):
            calls.append((endpoint, method, payload))
            if endpoint == "/action/query" and payload["query"] == AtelierClient._EPHEMERAL_CHANGE_SQL:
                return envelope([])
            if method == "GET":
                return 404, {}
            if method == "PUT":
                return 201, {}
            if endpoint == "/action/compile":
                return 200, {"status": {"errors": [], "summary": "not blank"}}
            if method == "DELETE":
                return 200, {}
            raise AssertionError("Execution SQL must not run after malformed compile response")

        client._send_request.side_effect = send
        with patch("uuid.uuid4", return_value=Mock(hex="0123456789abfedcba")):
            with self.assertRaisesRegex(LocalPolicyError, "compilation failed"):
                client._execute_direct_objectscript("Set ^Synthetic=1")
        self.assertFalse(any(
            endpoint == "/action/query" and "_Execute()" in payload["query"]
            for endpoint, _, payload in calls if isinstance(payload, dict) and "query" in payload
        ))

    def test_direct_runner_source_separates_payload_and_covers_capture_lifecycle(self):
        caller = 'Write "before", !\nQuit\nWrite "after", !'
        source = "\n".join(AtelierClient._runner_class_content(
            "User.McpTmpRun0123456789ab", caller
        ))
        execute_source, payload_source = source.split("ClassMethod Payload()", 1)

        self.assertEqual(source.count("{"), source.count("}"))
        self.assertIn("Do ..Payload()", execute_source)
        self.assertNotIn('Write "before", !', execute_source)
        self.assertIn('    Write "before", !\n    Quit\n    Write "after", !', payload_source)
        self.assertIn('Open tempFile:("WNS"):5', execute_source)
        self.assertIn("If '$Test {", execute_source)
        self.assertIn("stream.LinkToFile(tempFile)", execute_source)
        self.assertIn("stream.Read(32000)", execute_source)
        self.assertNotIn("stream.ReadLine()", execute_source)
        self.assertIn("ClassMethod Execute() As %String(MAXLEN=1048576) [ SqlProc ]", execute_source)
        self.assertIn("If ($Length(output) + $Length(chunk)) > 180000 {", execute_source)
        self.assertIn('Set state = "OUTPUT"', execute_source)
        worst_case_bytes = AtelierClient._EPHEMERAL_OUTPUT_LIMIT * 4
        worst_case_protocol = len("MCPOK|") + 4 * ((worst_case_bytes + 2) // 3)
        self.assertLessEqual(worst_case_protocol, AtelierClient._EPHEMERAL_RESULT_MAXLEN)
        self.assertIn("##class(%File).Delete(tempFile)", execute_source)
        self.assertIn('Quit "MCPSETUP|OPEN"', execute_source)
        self.assertIn('Quit "MCPUNKNOWN|PAYLOAD_DELETE"', execute_source)
        self.assertIn('Quit "MCPUNKNOWN|OUTCOME_DELETE"', execute_source)
        encoding = '$System.Encryption.Base64Encode($ZConvert(output,"O","UTF8"),1)'
        self.assertEqual(execute_source.count(encoding), 2)
        self.assertNotIn("Base64Encode(output", execute_source)
        self.assertLess(execute_source.index('Set state = "OUTPUT"'), execute_source.index(encoding))
        self.assertNotIn("DisplayString", source)

    def test_execute_code_is_a_thin_runtime_alias(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        client.execute_runtime_code = Mock(return_value="aliased")
        self.assertEqual(client.execute_code('Write "x"'), "aliased")
        client.execute_runtime_code.assert_called_once_with('Write "x"')
        source = inspect.getsource(AtelierClient.execute_code)
        self.assertNotIn("_require_write", source)
        self.assertNotIn("IRIS_TMP_PACKAGE", source)
        self.assertNotIn("save_document", source)

    def test_direct_runner_cleanup_runs_after_compile_and_query_failures(self):
        for failed_stage in ("compile", "query"):
            with self.subTest(failed_stage=failed_stage):
                client = synthetic_client(role="BASE", engine=1, configured=1)
                calls = []

                def send(endpoint, method="GET", payload=None, **kwargs):
                    calls.append((endpoint, method, payload))
                    if endpoint == "/action/query" and payload["query"] == AtelierClient._EPHEMERAL_CHANGE_SQL:
                        return envelope([])
                    if method == "PUT":
                        return 201, {}
                    if endpoint == "/action/compile":
                        if failed_stage == "compile":
                            return 500, {}
                        return 200, {"status": {"errors": [], "summary": ""}}
                    if endpoint == "/action/query":
                        return 500, {}
                    if method == "DELETE":
                        return 200, {}
                    if method == "GET":
                        return 404, {}
                    raise AssertionError("Unexpected runner request")

                client._send_request.side_effect = send
                with patch("uuid.uuid4", return_value=Mock(hex="0123456789abfedcba")):
                    with self.assertRaises(LocalPolicyError):
                        client._execute_direct_objectscript("Set ^Synthetic=1")
                self.assertEqual([(call[0], call[1]) for call in calls[-3:]], [
                    (calls[-3][0], "DELETE"), (calls[-2][0], "GET"), ("/action/query", "POST")
                ])

    def test_direct_runner_requires_canonical_namespace_before_transport(self):
        configs = {
            "alias": {"canonical_server_id": "canonical"},
            "canonical": {
                "host": "canonical.example.invalid", "port": 80, "path_prefix": "",
                "namespace": "BASE-ACHS", "env_role": "BASE",
                "username": "synthetic", "password": "synthetic",
            },
        }
        server = synthetic_server(servers=configs)
        with patch.object(AtelierClient, "_create_opener") as opener:
            result = server.handle_tool_call("iris_execute_objectscript", {
                "server_id": "alias", "namespace": "OTHER", "code": "Write 1",
            })
        self.assertTrue(result.get("isError"))
        self.assertIn("canonical namespace", result["content"][0]["text"])
        opener.assert_not_called()

    def test_direct_runner_alias_accepts_canonical_namespace_through_real_client_path(self):
        configs = {
            "alias": {"canonical_server_id": "canonical"},
            "canonical": {
                "host": "canonical.example.invalid", "port": 80, "path_prefix": "",
                "namespace": "BASE-ACHS", "env_role": "BASE",
                "username": "synthetic", "password": "synthetic",
            },
        }
        server = synthetic_server(servers=configs)
        calls = []

        def send(client, endpoint, method="GET", payload=None, **kwargs):
            calls.append((client.namespace, endpoint, method, payload))
            if endpoint == "/action/query" and payload["query"] == AtelierClient._EPHEMERAL_CHANGE_SQL:
                return envelope([])
            if method == "PUT":
                return 201, {}
            if endpoint == "/action/compile":
                return 200, {"status": {"errors": [], "summary": ""}}
            if endpoint == "/action/query":
                return envelope([{"result": runner_ok("synthetic-output")}])
            if method == "DELETE":
                return 200, {}
            if method == "GET":
                return 404, {}
            raise AssertionError("Unexpected runner request")

        with (patch.object(AtelierClient, "_create_opener", return_value=Mock()),
              patch.object(AtelierClient, "_send_request", autospec=True, side_effect=send),
              patch("uuid.uuid4", return_value=Mock(hex="0123456789abfedcba"))):
            result = server.handle_tool_call("iris_execute_objectscript", {
                "server_id": "alias", "namespace": "BASE-ACHS", "code": "Write 1",
            })
        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["content"][0]["text"], "synthetic-output")
        self.assertTrue(calls)
        self.assertEqual({call[0] for call in calls}, {"BASE-ACHS"})

    def test_direct_runner_persistent_delete_failure_is_an_error(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        uploaded = False

        def send(endpoint, method="GET", payload=None, **kwargs):
            nonlocal uploaded
            if endpoint == "/action/query" and payload["query"] == AtelierClient._EPHEMERAL_CHANGE_SQL:
                return envelope([])
            if method == "PUT":
                uploaded = True
                return 201, {}
            if endpoint == "/action/compile":
                return 200, {"status": {"errors": [], "summary": ""}}
            if endpoint == "/action/query":
                return envelope([{"result": runner_ok("synthetic-output")}])
            if method == "DELETE":
                return 500, {}
            if method == "GET":
                if uploaded:
                    return 200, {"result": {"content": ["still present"]}}
                return 404, {}
            raise AssertionError("Unexpected runner request")

        client._send_request.side_effect = send
        with patch("uuid.uuid4", return_value=Mock(hex="0123456789abfedcba")):
            result = synthetic_server(client).handle_tool_call(
                "iris_execute_objectscript", {"server_id": "fake", "code": "Write 1"})
        self.assertTrue(result.get("isError"))
        self.assertEqual(result["content"][0]["text"],
                         "Temporary executor cleanup failed; manual verification is required.")

    def test_direct_runner_delete_200_but_document_present_is_an_error(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        uploaded = False

        def send(endpoint, method="GET", payload=None, **kwargs):
            nonlocal uploaded
            if endpoint == "/action/query" and payload["query"] == AtelierClient._EPHEMERAL_CHANGE_SQL:
                return envelope([])
            if method == "PUT":
                uploaded = True
                return 201, {}
            if endpoint == "/action/compile":
                return 200, {"status": {"errors": [], "summary": ""}}
            if endpoint == "/action/query":
                return envelope([{"result": runner_ok("synthetic-output")}])
            if method == "DELETE":
                return 200, {}
            if method == "GET":
                if uploaded:
                    return 200, {"result": {"content": ["still present"]}}
                return 404, {}
            raise AssertionError("Unexpected runner request")

        client._send_request.side_effect = send
        with patch("uuid.uuid4", return_value=Mock(hex="0123456789abfedcba")):
            result = synthetic_server(client).handle_tool_call(
                "iris_execute_objectscript", {"server_id": "fake", "code": "Write 1"})
        self.assertTrue(result.get("isError"))
        self.assertIn("cleanup failed", result["content"][0]["text"])

    def test_direct_runner_payload_marker_like_output_is_never_control_state(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        marker = "__MCP_PAYLOAD_FAILURE_0123456789ab__ MCPPAYLOAD|not-control"
        uploaded_content = []

        def send(endpoint, method="GET", payload=None, **kwargs):
            if endpoint == "/action/query" and payload["query"] == AtelierClient._EPHEMERAL_CHANGE_SQL:
                return envelope([])
            if method == "PUT":
                uploaded_content.extend(payload["content"])
                return 201, {}
            if endpoint == "/action/compile":
                return 200, {"status": {"errors": [], "summary": ""}}
            if endpoint == "/action/query":
                return envelope([{"result": runner_ok(marker)}])
            if method == "DELETE":
                return 200, {}
            if method == "GET":
                return 404, {}
            raise AssertionError("Unexpected runner request")

        client._send_request.side_effect = send
        with patch("uuid.uuid4", return_value=Mock(hex="0123456789abfedcba")):
            result = synthetic_server(client).handle_tool_call(
                "iris_execute_objectscript", {"server_id": "fake", "code": f'Write "{marker}"'})
        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["content"][0]["text"], marker)
        self.assertIn(f'    Write "{marker}"', uploaded_content)

    def test_direct_runner_pending_change_query_is_fixed_and_residue_blocks(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        document_name = "User.McpTmpRun0123456789ab.cls"
        client._send_request.side_effect = lambda endpoint, method="GET", payload=None, **kwargs: envelope([
            {"InternalName": document_name}
        ])
        with patch("uuid.uuid4", return_value=Mock(hex="0123456789abfedcba")):
            result = synthetic_server(client).handle_tool_call(
                "iris_execute_objectscript", {"server_id": "fake", "code": "Write 1"})
        self.assertTrue(result.get("isError"))
        self.assertIn("pre-existing source-control residue", result["content"][0]["text"])
        self.assertEqual(client._send_request.call_count, 1)
        call = client._send_request.call_args
        self.assertEqual(call.args, ("/action/query",))
        self.assertEqual(call.kwargs["payload"], {
            "query": AtelierClient._EPHEMERAL_CHANGE_SQL,
            "parameters": [document_name],
        })

    def test_direct_runner_requires_exact_document_absence_before_put(self):
        for get_status in (200, 500):
            with self.subTest(get_status=get_status):
                client = synthetic_client(role="BASE", engine=1, configured=1)
                calls = []

                def send(endpoint, method="GET", payload=None, **kwargs):
                    calls.append((endpoint, method, payload))
                    if endpoint == "/action/query":
                        return envelope([])
                    if method == "GET":
                        return get_status, {"secret": "document-server-detail"}
                    raise AssertionError("Mutation must not occur before absence is proven")

                client._send_request.side_effect = send
                with patch("uuid.uuid4", return_value=Mock(hex="0123456789abfedcba")):
                    result = synthetic_server(client).handle_tool_call(
                        "iris_execute_objectscript", {"server_id": "fake", "code": "Write 1"})

                self.assertTrue(result.get("isError"))
                self.assertEqual(
                    result["content"][0]["text"],
                    "Temporary executor blocked because generated document absence could not be proven.",
                )
                self.assertEqual([method for _, method, _ in calls], ["POST", "GET"])
                self.assertNotIn("document-server-detail", result["content"][0]["text"])

    def test_direct_runner_post_cleanup_source_control_residue_is_an_error(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        document_name = "User.McpTmpRun0123456789ab.cls"
        metadata_checks = 0

        def send(endpoint, method="GET", payload=None, **kwargs):
            nonlocal metadata_checks
            if endpoint == "/action/query" and payload["query"] == AtelierClient._EPHEMERAL_CHANGE_SQL:
                metadata_checks += 1
                return envelope([] if metadata_checks == 1 else [{"InternalName": document_name}])
            if method == "PUT":
                return 201, {}
            if endpoint == "/action/compile":
                return 200, {"status": {"errors": [], "summary": ""}}
            if endpoint == "/action/query":
                return envelope([{"result": runner_ok("synthetic-output")}])
            if method == "DELETE":
                return 200, {}
            if method == "GET":
                return 404, {}
            raise AssertionError("Unexpected runner request")

        client._send_request.side_effect = send
        with patch("uuid.uuid4", return_value=Mock(hex="0123456789abfedcba")):
            result = synthetic_server(client).handle_tool_call(
                "iris_execute_objectscript", {"server_id": "fake", "code": "Write 1"})
        self.assertTrue(result.get("isError"))
        self.assertEqual(metadata_checks, 2)
        self.assertEqual(result["content"][0]["text"],
                         "Temporary executor cleanup failed; manual verification is required.")

    def test_direct_runner_execution_and_cleanup_failure_preserves_both_failures(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        uploaded = False

        def send(endpoint, method="GET", payload=None, **kwargs):
            nonlocal uploaded
            if endpoint == "/action/query" and payload["query"] == AtelierClient._EPHEMERAL_CHANGE_SQL:
                return envelope([])
            if method == "PUT":
                uploaded = True
                return 201, {}
            if endpoint == "/action/compile":
                return 500, {"secret": "payload-server-detail"}
            if method == "DELETE":
                return 500, {"secret": "cleanup-server-detail"}
            if method == "GET":
                if uploaded:
                    return 200, {"secret": "document-server-detail"}
                return 404, {}
            raise AssertionError("Unexpected runner request")

        client._send_request.side_effect = send
        with patch("uuid.uuid4", return_value=Mock(hex="0123456789abfedcba")):
            result = synthetic_server(client).handle_tool_call(
                "iris_execute_objectscript", {"server_id": "fake", "code": "Set ^Secret=1"})
        self.assertTrue(result.get("isError"))
        text = result["content"][0]["text"]
        self.assertEqual(
            text,
            "Temporary executor compilation failed; code/details withheld. "
            "Temporary executor cleanup failed; manual verification is required.",
        )
        self.assertNotIn("Secret", text)
        self.assertNotIn("server-detail", text)

    def test_direct_runner_query_requires_exact_single_string_result(self):
        malformed = (
            (504, {"secret": "timeout-detail"}),
            (200, {}),
            (200, {"status": {"errors": []}, "result": {"content": [{"result": "ok"}]}}),
            (200, {"status": {"errors": [], "summary": None}, "result": {"content": [{"result": "ok"}]}}),
            envelope([]),
            envelope([{"result": "one"}, {"result": "two"}]),
            envelope([None]),
            envelope([{}]),
            envelope([{"result": "ok", "unexpected": "secret"}]),
            envelope([{"result": 1}]),
            (200, {"status": {"errors": [], "summary": ""}, "result": []}),
            (200, {"status": {"errors": [], "summary": ""}, "result": {"content": {}}}),
        )
        expected = "Payload execution outcome is unknown; payload may have partially persisted data."
        for response in malformed:
            with self.subTest(response=response):
                client = synthetic_client(role="BASE", engine=1, configured=1)
                client._send_request.side_effect = None
                client._send_request.return_value = response
                with self.assertRaisesRegex(LocalPolicyError, expected.replace(".", r"\.")):
                    client._ephemeral_runner_query("User.McpTmpRun0123456789ab")
                request = client._send_request.call_args
                self.assertEqual(request.args, ("/action/query",))
                self.assertEqual(request.kwargs["payload"], {
                    "query": "SELECT SQLUser.McpTmpRun0123456789ab_Execute() AS result",
                    "parameters": [],
                })

    def test_direct_runner_query_parses_only_exact_protocol_records(self):
        marker_like_output = (
            "__MCP_PAYLOAD_FAILURE_0123456789ab__\n"
            "MCPPAYLOAD|still ordinary output\nMCPUNKNOWN|DELETE"
        )
        client = synthetic_client(role="BASE", engine=1, configured=1)
        client._send_request.side_effect = None
        client._send_request.return_value = envelope([{"result": runner_ok(marker_like_output)}])
        self.assertEqual(
            client._ephemeral_runner_query("User.McpTmpRun0123456789ab"),
            marker_like_output,
        )

        malformed = (
            "synthetic-output",
            "MCPOK",
            "MCPOK-ish|",
            "MCPOK|not base64!",
            "MCPOK|YWJj|",
            "MCPOK|/w==",
            "MCPPAYLOAD|not base64!",
            "MCPSETUP|OTHER",
            "MCPUNKNOWN|OTHER",
            "MCPUNKNOWN|OUTPUT|extra",
        )
        expected = "Payload execution outcome is unknown; payload may have partially persisted data."
        for record in malformed:
            with self.subTest(record=record):
                client._send_request.return_value = envelope([{"result": record}])
                with self.assertRaisesRegex(LocalPolicyError, expected.replace(".", r"\.")):
                    client._ephemeral_runner_query("User.McpTmpRun0123456789ab")

    def test_direct_runner_protocol_preserves_exact_output_and_long_strict_base64(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        client._send_request.side_effect = None
        outputs = (
            "single physical line without final newline",
            "first line\nsecond line",
            "first line\nsecond line\n",
            "Español: áéíóú, 日本語, 😀",
            "x" * 80,
        )

        for output in outputs:
            with self.subTest(output=repr(output)):
                protocol = runner_ok(output)
                encoded = protocol.partition("|")[2]
                if len(output) == 80:
                    self.assertGreater(len(encoded), 76)
                self.assertNotIn("\n", encoded)
                self.assertEqual(
                    base64.b64decode(encoded, validate=True).decode("utf-8"),
                    output,
                )
                client._send_request.return_value = envelope([{"result": protocol}])
                self.assertEqual(
                    client._ephemeral_runner_query("User.McpTmpRun0123456789ab"),
                    output,
                )

    def test_direct_runner_protocol_maps_lifecycle_failures_without_details(self):
        cases = (
            ("MCPPAYLOAD|", "Payload execution failed; payload may have partially persisted data."),
            ("MCPSETUP|OPEN", "Temporary executor setup failed; payload was not executed."),
            ("MCPUNKNOWN|LINK", "Payload execution outcome is unknown; payload may have partially persisted data."),
            ("MCPUNKNOWN|READ", "Payload execution outcome is unknown; payload may have partially persisted data."),
            ("MCPUNKNOWN|CLOSE", "Payload execution outcome is unknown; payload may have partially persisted data."),
            ("MCPUNKNOWN|IO", "Payload execution outcome is unknown; payload may have partially persisted data."),
            ("MCPUNKNOWN|WRAPPER", "Payload execution outcome is unknown; payload may have partially persisted data."),
            (
                "MCPUNKNOWN|OUTPUT",
                "Payload executed and may have persisted data, but its output exceeded the "
                "180000-character capture limit.",
            ),
            ("MCPUNKNOWN|DELETE", "Temporary executor cleanup failed; manual verification is required."),
            (
                "MCPUNKNOWN|PAYLOAD_DELETE",
                "Payload execution failed; payload may have partially persisted data. "
                "Temporary executor cleanup failed; manual verification is required.",
            ),
            (
                "MCPUNKNOWN|SETUP_DELETE",
                "Temporary executor setup failed; payload was not executed. "
                "Temporary executor cleanup failed; manual verification is required.",
            ),
            (
                "MCPUNKNOWN|OUTCOME_DELETE",
                "Payload execution outcome is unknown; payload may have partially persisted data. "
                "Temporary executor cleanup failed; manual verification is required.",
            ),
        )
        for record, expected in cases:
            with self.subTest(record=record):
                client = synthetic_client(role="BASE", engine=1, configured=1)
                client._send_request.side_effect = None
                client._send_request.return_value = envelope([{"result": record}])
                with self.assertRaises(LocalPolicyError) as caught:
                    client._ephemeral_runner_query("User.McpTmpRun0123456789ab")
                self.assertEqual(str(caught.exception), expected)

    def test_direct_runner_unknown_query_outcome_and_cleanup_failure_are_both_bounded(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        uploaded = False

        def send(endpoint, method="GET", payload=None, **kwargs):
            nonlocal uploaded
            if endpoint == "/action/query" and payload["query"] == AtelierClient._EPHEMERAL_CHANGE_SQL:
                return envelope([])
            if method == "PUT":
                uploaded = True
                return 201, {}
            if endpoint == "/action/compile":
                return 200, {"status": {"errors": [], "summary": ""}}
            if endpoint == "/action/query":
                return 200, {"secret": "malformed-query-detail"}
            if method == "DELETE":
                return 500, {"secret": "cleanup-detail"}
            if method == "GET":
                return (200, {"secret": "document-detail"}) if uploaded else (404, {})
            raise AssertionError("Unexpected runner request")

        client._send_request.side_effect = send
        with patch("uuid.uuid4", return_value=Mock(hex="0123456789abfedcba")):
            result = synthetic_server(client).handle_tool_call(
                "iris_execute_objectscript", {"server_id": "fake", "code": "Set ^Secret=1"})

        self.assertTrue(result.get("isError"))
        self.assertEqual(
            result["content"][0]["text"],
            "Payload execution outcome is unknown; payload may have partially persisted data. "
            "Temporary executor cleanup failed; manual verification is required.",
        )
        self.assertNotIn("detail", result["content"][0]["text"])

    def test_direct_runner_query_cannot_accept_caller_controlled_sql(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        with self.assertRaises(TypeError):
            client._ephemeral_runner_query(
                "User.McpTmpRun0123456789ab", "SELECT SideEffectingFunction()"
            )
        client._send_request.assert_not_called()

    def test_fresh_guard_on_every_save_blocks_the_second_put(self):
        client = synthetic_client()
        writes = []
        states = iter(("ABSENT", "SUSPENDED"))
        client._probe_same_process_ccr_state = Mock(side_effect=lambda: guard_record(next(states)))

        def send(endpoint, **kwargs):
            if kwargs.get("method") == "PUT":
                writes.append(endpoint)
                return 201, {}
            raise AssertionError("Only the document PUT is expected")

        client._send_request.side_effect = send
        self.assertTrue(client.save_document(CATALOGED, ["synthetic"])[0])
        with self.assertRaisesRegex(LocalPolicyError, "SUSPENDED"):
            client.save_document(CATALOGED, ["synthetic"])
        self.assertEqual(len(writes), 1)
        self.assertEqual(client._probe_same_process_ccr_state.call_count, 2)

    def test_mutating_client_target_fields_invalidates_route_before_requests(self):
        for field, value in (("namespace", "OTHER"), ("host", "other.invalid"),
                             ("path_prefix", "/other"), ("port", 1234)):
            for gate in (lambda c: c.is_read_allowed(), lambda c: c.is_runtime_operation_allowed(),
                         lambda c: c.is_code_write_allowed(CATALOGED), lambda c: c.is_code_write_route_allowed()):
                client = synthetic_client()
                script_guard(client, "ABSENT")
                setattr(client, field, value)
                with self.assertRaisesRegex(LocalPolicyError, "Target changed"):
                    gate(client)
                client._send_request.assert_not_called()

    def test_absent_allows_base_cataloged_but_not_generic_uat_test_or_promotion(self):
        base = synthetic_client()
        script_guard(base, "ABSENT")
        allowed, reason = base.is_code_write_allowed(CATALOGED)
        self.assertTrue(allowed)
        self.assertIn("ABSENT", reason)
        for role in ("UAT", "TEST", "QA", ""):
            with self.subTest(role=role):
                client = synthetic_client(role=role)
                script_guard(client, "ABSENT")
                self.assertFalse(client.is_code_write_allowed(CATALOGED)[0])
        promotion = synthetic_client(namespace="UAT-ACHS", role="UAT", host="10.244.20.6")
        script_guard(promotion, "ABSENT")
        allowed, reason = promotion.is_code_write_allowed(
            None, "ItemSet 1", require_controlled=True, promotion=True)
        self.assertFalse(allowed)
        self.assertIn("CONTROLLED_ACTIVE", reason)
        absent_no_document = synthetic_client()
        script_guard(absent_no_document, "ABSENT")
        self.assertFalse(absent_no_document.is_code_write_allowed(None)[0])

    def test_controlled_active_requires_base_route_and_explicit_ccr(self):
        base = synthetic_client()
        script_guard(base, "CONTROLLED_ACTIVE")
        self.assertTrue(base.is_code_write_allowed(CATALOGED, "ACHS0875")[0])
        allowed, reason = base.is_code_write_allowed(CATALOGED)
        self.assertFalse(allowed)
        self.assertIn("explicit CCR", reason)
        for tracking in ("", "   ", None, 42):
            with self.subTest(tracking=tracking):
                self.assertFalse(base.is_code_write_allowed(CATALOGED, tracking)[0])
        int_uat = synthetic_client("INT", "UAT", host="10.244.20.6", path_prefix="/clacintuat")
        script_guard(int_uat, "CONTROLLED_ACTIVE")
        allowed, reason = int_uat.is_code_write_allowed("SAP.Api.cls", "ACHS0875")
        self.assertFalse(allowed)
        self.assertIn("BASE tracked changes or UAT promotion", reason)
        promotion = synthetic_client(namespace="UAT-ACHS", role="UAT", host="10.244.20.6")
        script_guard(promotion, "CONTROLLED_ACTIVE")
        self.assertTrue(promotion.is_code_write_allowed(
            None, "ItemSet 1", require_controlled=True, promotion=True)[0])

    def test_arbitrary_sql_read_gate_and_policy_ignore_external_discovery(self):
        for role in ("BASE", "UAT"):
            with self.subTest(role=role):
                unknown = synthetic_client(role=role, engine=1, configured=1)
                unknown._send_request.side_effect = None
                unknown._send_request.return_value = envelope([{"value": 1}])
                result = synthetic_server(unknown).handle_tool_call("iris_query_sql", {
                    "server_id": "fake", "query": "SELECT 1 AS value",
                })
                self.assertFalse(result.get("isError", False), result)
                self.assertEqual(unknown._send_request.call_args.kwargs["payload"]["query"], "SELECT 1 AS value")

        denied = (
            "SELECT SideEffectingFunction() FROM %SYSTEM.Version",
            "SELECT $SYSTEM.Version.GetNumber()",
            "CALL Some_Proc('x')",
            "UPDATE Ens_Config.Item SET Enabled = 0",
            "SELECT 1; DROP TABLE t",
            # Tokens inside string literals are data (see SqlReadPolicyLiteralTests);
            # the same token outside a literal is still denied.
            "SELECT Name FROM T WHERE Text = 'a' OR DELETE = 1",
            "WITH x AS (SELECT 1) SELECT * FROM x",
            "  select 1 union all select 2 from Foo where BUILD = 1",
            "",
        )
        for query in denied:
            with self.subTest(query=query):
                base = synthetic_client()
                base.run_query = Mock(return_value=[{"value": 1}])
                result = synthetic_server(base).handle_tool_call("iris_query_sql", {
                    "server_id": "fake", "query": query,
                })
                self.assertTrue(result.get("isError"), query)
                self.assertIn("SQL execution denied", result["content"][0]["text"])
                self.assertNotIn("UNKNOWN", result["content"][0]["text"])
                base.run_query.assert_not_called()
                with self.assertRaisesRegex(LocalPolicyError, "SQL policy"):
                    AtelierClient.run_query(base, query)

        production = synthetic_client(production=True)
        production.run_query = Mock(return_value=[{"value": 1}])
        result = synthetic_server(production).handle_tool_call("iris_query_sql", {
            "server_id": "fake", "query": "SELECT 1 AS value",
        })
        self.assertTrue(result.get("isError"))
        production.run_query.assert_not_called()

    def test_promotion_requires_uat_vip_and_same_process_controlled_active(self):
        server = synthetic_server(synthetic_client())
        tools = {tool["name"]: tool for tool in server.handle_tools_list()["tools"]}
        description = tools["iris_ccr_download_load"]["description"]
        self.assertNotIn("NO DISPONIBLE", description)
        self.assertNotIn("UNKNOWN", description)
        self.assertIn("CONTROLLED_ACTIVE", description)
        self.assertIn("LIVE", description)

        base = synthetic_client()
        base.execute_runtime_code = Mock(side_effect=AssertionError("runner must not start"))
        result = synthetic_server(base).handle_tool_call("iris_ccr_download_load", {
            "server_id": "fake", "itemset_id": 1, "load": False,
        })
        self.assertTrue(result.get("isError"))
        self.assertIn("UAT role", result["content"][0]["text"])
        self.assert_only_metadata(base)

        node = synthetic_client(namespace="UAT-ACHS", role="UAT", host="10.244.20.4", path_prefix="/trakuat01")
        node.execute_runtime_code = Mock(side_effect=AssertionError("runner must not start"))
        result = synthetic_server(node).handle_tool_call("iris_ccr_download_load", {
            "server_id": "fake", "namespace": "UAT-ACHS", "itemset_id": 1, "load": False,
        })
        self.assertTrue(result.get("isError"))
        self.assertIn("Direct HA node", result["content"][0]["text"])

        for state, expected_error in (("ABSENT", True), ("SUSPENDED", True), ("CONTROLLED_ACTIVE", False)):
            with self.subTest(state=state):
                vip = synthetic_client(namespace="UAT-ACHS", role="UAT", host="10.244.20.6")
                output = (
                    "DOWNLOADED|isid=7|file=item.xml\n"
                    "IMPORTED|local_id=9|ccr=ACHS0875|source=BASE|target=UAT\n"
                    "SUCCESS"
                    if state == "CONTROLLED_ACTIVE"
                    else f"ERROR_CONTROL|state={state}|message=blocked"
                )
                captured = script_guarded_runner(vip, output)
                result = synthetic_server(vip).handle_tool_call("iris_ccr_download_load", {
                    "server_id": "fake", "namespace": "UAT-ACHS", "itemset_id": 7, "load": False, "token": "secret-token",
                })
                self.assertEqual(bool(result.get("isError", False)), expected_error, result)
                self.assertEqual(len(captured), 1)
                code = captured[0]
                self.assertLess(code.index('If mcpGuardState \'= "CONTROLLED_ACTIVE" {'), code.index("WSDownload("))
                self.assertIn("Set isid = 7", code)
                self.assertNotIn("secret-token", result["content"][0]["text"])


class RoutingAndTransportTests(HermeticTestCase):
    def config(self, host="10.244.20.6", path_prefix=""):
        return {"host": host, "port": 80, "path_prefix": path_prefix, "namespace": "UAT-ACHS",
                "env_role": "UAT", "username": "synthetic", "password": "synthetic"}

    def read_opener(self, requests, namespaces=None):
        opener = Mock()

        def respond(request, **kwargs):
            self.assertEqual(request.method, "GET")
            requests.append(request)
            if request.full_url.endswith("/api/atelier/"):
                self.assertIsNotNone(namespaces)
                content = {"namespaces": namespaces}
            else:
                content = ["synthetic"]
            response = Mock(status=200)
            response.read.return_value = json.dumps({"result": {"content": content}}).encode()
            context = Mock()
            context.__enter__ = Mock(return_value=response)
            context.__exit__ = Mock(return_value=False)
            return context

        opener.open.side_effect = respond
        return opener

    def test_malicious_namespaces_rejected_before_client_or_requests(self):
        for namespace in ("../BASE", "A/B", "A\\B", "%2fSYS", "A%2FB", "A%252fB", "A?x", "A#x",
                          "A\n", "", "A'", "A\x00", 123):
            with self.subTest(namespace=namespace):
                server = synthetic_server(servers={"achsuat": self.config()})
                with patch.object(AtelierClient, "_create_opener") as opener:
                    result = server.handle_tool_call("iris_ccr_namespace_status", {
                        "server_id": "achsuat", "namespace": namespace})
                    self.assertTrue(result.get("isError"))
                    opener.assert_not_called()
                    server.load_configuration.assert_not_called()

    def test_valid_percent_sys_and_case_normalization(self):
        client = synthetic_client("%sys")
        self.assertEqual(client.namespace, "%SYS")
        self.assertTrue(client.base_url.endswith("/%SYS"))

    def test_ha_alias_resolves_configured_vip_without_requests(self):
        for servers in ({"achsuat": self.config()},
                        {"achsuat": {"canonical_server_id": "clinical-vip"}, "clinical-vip": self.config()}):
            server = synthetic_server(servers=servers)
            with patch.object(AtelierClient, "_create_opener", return_value=Mock()) as factory:
                client = server.get_client("achsuat")
                self.assertEqual(client.host, "10.244.20.6")
                factory.return_value.open.assert_not_called()

    def test_unmapped_ha_alias_never_falls_back_to_direct_node(self):
        for servers in ({"achsuat02": self.config("10.244.20.5")},
                        {"achsuat": self.config("10.244.20.4")},
                        {"achsuat": {"canonical_server_id": "missing"}}):
            server = synthetic_server(servers=servers)
            with patch.object(AtelierClient, "_create_opener") as factory:
                with self.assertRaisesRegex(LocalPolicyError, "Unresolved achsuat"):
                    server.get_client("achsuat")
                factory.assert_not_called()

    def test_generic_configured_vip_remains_usable(self):
        server = synthetic_server(servers={"other-vip": self.config("vip.example.invalid", "/app")})
        with patch.object(AtelierClient, "_create_opener", return_value=Mock()):
            client = server.get_client("other-vip")
        self.assertEqual(client.host, "vip.example.invalid")
        client.opener.open.assert_not_called()

    def test_server_listing_resolves_alias_and_preserves_unresolved_entries_without_clients(self):
        configs = {
            "clinical-vip": {**self.config(), "password": "synthetic-secret", "upload_token": "synthetic-secret"},
            "achsuat": {"canonical_server_id": "clinical-vip"},
            "missing": {"canonical_server_id": "absent"},
            "cycle-a": {"canonical_server_id": "cycle-b"},
            "cycle-b": {"canonical_server_id": "cycle-a"},
            "malformed-alias": {"canonical_server_id": {"secret": "synthetic-secret"}},
            "malformed-target": ["synthetic-secret"],
            "incomplete-target": {"username": "synthetic-secret"},
            "bad-target-reference": {"canonical_server_id": "malformed-target"},
        }
        server = synthetic_server(servers=configs)
        before = copy.deepcopy(configs)
        with patch.object(server, "get_client") as clients, patch.object(AtelierClient, "_create_opener") as openers:
            result = server.handle_tool_call("iris_list_servers", {})
            clients.assert_not_called()
            openers.assert_not_called()
        self.assertFalse(result.get("isError", False))
        rows = {row["id"]: row for row in result["structuredContent"]["servers"]}
        self.assertEqual(set(rows), set(configs))
        self.assertEqual(rows["clinical-vip"]["status"], "CONFIGURED")
        self.assertEqual(rows["achsuat"]["status"], "ALIAS")
        self.assertEqual(rows["achsuat"]["canonical_server_id"], "clinical-vip")
        self.assertEqual(rows["achsuat"]["host"], "10.244.20.6")
        for name in set(configs) - {"clinical-vip", "achsuat"}:
            self.assertEqual(rows[name]["status"], "UNRESOLVED")
        self.assertIn("cycle", rows["cycle-a"]["reason"])
        self.assertIn("missing", rows["missing"]["reason"])
        self.assertNotIn("synthetic-secret", json.dumps(result))
        self.assertNotIn("password", json.dumps(result))
        self.assertEqual(configs, before)

    def test_generic_alias_dispatch_and_catalog_use_canonical_target_without_merging(self):
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                alias = {"canonical_server_id": "canonical"}
                if mixed:
                    alias.update(self.config("original.example.invalid", "/original"))
                    alias.update(namespace="APPONE", username="original-user", password="original-password",
                                 env_role="BASE", is_production=False)
                configs = {"alias": alias, "canonical": {
                    **self.config("canonical.example.invalid", "/canonical"), "namespace": "APPTWO",
                    "username": "canonical-user", "password": "canonical-password",
                }}
                before = copy.deepcopy(configs)
                server = synthetic_server(servers=configs)
                requests = []
                opener = self.read_opener(requests)
                with patch.object(AtelierClient, "_create_opener", return_value=opener) as factory:
                    listing = server.handle_tool_call("iris_list_servers", {})
                    factory.assert_not_called()
                    row = next(row for row in listing["structuredContent"]["servers"] if row["id"] == "alias")
                    server.load_configuration.reset_mock()
                    result = server.handle_tool_call("iris_get_class", {"server_id": "alias", "class_name": "Custom.Test"})
                self.assertFalse(result.get("isError", False))
                server.load_configuration.assert_called_once_with()
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].full_url,
                                 "http://canonical.example.invalid:80/canonical/api/atelier/v1/APPTWO/doc/Custom.Test.cls")
                self.assertEqual((row["host"], row["path_prefix"], row["namespace"]),
                                 ("canonical.example.invalid", "/canonical", "APPTWO"))
                auth = "Basic " + base64.b64encode(b"canonical-user:canonical-password").decode("ascii")
                self.assertEqual(requests[0].get_header("Authorization"), auth)
                self.assertEqual(configs, before)

    def test_generic_alias_explicit_namespace_keeps_canonical_connection(self):
        server = synthetic_server(servers={
            "alias": {**self.config("original.example.invalid"), "canonical_server_id": "target", "namespace": "APPONE"},
            "target": {**self.config("canonical.example.invalid"), "namespace": "APPTWO"},
        })
        requests = []
        with patch.object(AtelierClient, "_create_opener", return_value=self.read_opener(requests)):
            result = server.handle_tool_call("iris_get_class", {
                "server_id": "alias", "namespace": "APPTHREE", "class_name": "Custom.Test",
            })
        self.assertFalse(result.get("isError", False))
        self.assertEqual([r.full_url for r in requests],
                         ["http://canonical.example.invalid:80/api/atelier/v1/APPTHREE/doc/Custom.Test.cls"])

    def test_canonical_production_guards_cannot_be_weakened_by_alias_settings(self):
        for canonical_guard in ({"is_production": True, "env_role": "BASE"},
                                {"is_production": False, "env_role": "LIVE"}):
            with self.subTest(guard=canonical_guard):
                server = synthetic_server(servers={
                    "alias": {**self.config("original.example.invalid"), "canonical_server_id": "target",
                              "namespace": "APPONE", "is_production": False, "env_role": "BASE"},
                    "target": {**self.config("canonical.example.invalid"), "namespace": "APPTWO", **canonical_guard},
                })
                opener = Mock()
                with patch.object(AtelierClient, "_create_opener", return_value=opener):
                    result = server.handle_tool_call("iris_save_class", {
                        "server_id": "alias", "namespace": "APPONE", "class_name": "Custom.Test",
                        "content": "synthetic", "auto_checkout": False,
                    })
                    client = server.get_client("alias")
                self.assertTrue(result.get("isError"))
                opener.open.assert_not_called()
                self.assertEqual(client.host, "canonical.example.invalid")
                self.assertEqual(client.namespace, "APPTWO")
                self.assertEqual(client.is_production, canonical_guard["is_production"])
                self.assertEqual(client.env_role, canonical_guard["env_role"])

    def test_invalid_aliases_have_same_unresolved_catalog_and_runtime_without_transport(self):
        valid = {**self.config("canonical.example.invalid"), "namespace": "APPTWO"}
        cases = [
            {"alias": {"canonical_server_id": "missing"}},
            {"alias": {"canonical_server_id": "other"}, "other": {"canonical_server_id": "alias"}},
            {"alias": {"canonical_server_id": None}},
            {"alias": {"canonical_server_id": {"secret": "synthetic"}}},
            {"alias": {"canonical_server_id": "target"}, "target": []},
            {"alias": {"canonical_server_id": "target"}, "target": {"namespace": "APPTWO"}},
            {"alias": {"canonical_server_id": "target"}, "target": {**valid, "is_production": "false"}},
            {"alias": {"canonical_server_id": "achsuat"}, "achsuat": self.config("10.244.20.4")},
        ]
        chain = {"alias": {"canonical_server_id": "link0"}, "target": valid}
        chain.update({"link" + str(i): {"canonical_server_id": "link" + str(i + 1) if i < 15 else "target"}
                      for i in range(16)})
        cases.append(chain)
        for configs in cases:
            for tool in ("iris_get_class", "iris_list_namespaces"):
                with self.subTest(configs=configs, tool=tool):
                    server = synthetic_server(servers=configs)
                    with patch.object(AtelierClient, "_create_opener") as factory:
                        catalog = server.handle_tool_call("iris_list_servers", {})
                        row = next(row for row in catalog["structuredContent"]["servers"] if row["id"] == "alias")
                        result = server.handle_tool_call(tool, {"server_id": "alias", "class_name": "Custom.Test"})
                        factory.assert_not_called()
                    self.assertEqual(row["status"], "UNRESOLVED")
                    self.assertTrue(result.get("isError"))
                    self.assertEqual(result["content"][0]["text"], row["reason"])

    def test_alias_chain_defaults_and_namespace_metadata_use_canonical_route(self):
        server = synthetic_server(servers={
            "alias": {"canonical_server_id": "middle", "namespace": "APPONE"},
            "middle": {"canonical_server_id": "target", "host": "ignored.example.invalid"},
            "target": {**self.config("canonical.example.invalid", "/canonical"), "namespace": "USER"},
        })
        requests = []
        opener = self.read_opener(requests, namespaces=["%SYS", "USER", "APPTWO"])
        with patch.object(AtelierClient, "_create_opener", return_value=opener):
            result = server.handle_tool_call("iris_get_class", {"server_id": "alias", "class_name": "Custom.Test"})
        self.assertFalse(result.get("isError", False))
        self.assertEqual([r.full_url for r in requests], [
            "http://canonical.example.invalid:80/canonical/api/atelier/",
            "http://canonical.example.invalid:80/canonical/api/atelier/v1/APPTWO/doc/Custom.Test.cls",
        ])
        server.load_configuration.assert_called_once_with()

    def test_ccr_defaults_are_taken_from_canonical_entry_only(self):
        client = synthetic_client()
        client.ccr_list_itemsets = Mock(return_value="OK|ccr_enc=CANONICAL|env_enc=BASE")
        server = synthetic_server(client, {
            "alias": {"canonical_server_id": "target", "namespace": "OTHER", "ccr_id": "ORIGINAL", "upload_token": "original-token"},
            "target": {"namespace": "BASE-ACHS", "ccr_id": "CANONICAL", "upload_token": "canonical-token"},
        })
        result = server.handle_tool_call("iris_ccr_list_itemsets", {"server_id": "alias", "env": "BASE"})
        self.assertFalse(result.get("isError", False))
        client.ccr_list_itemsets.assert_called_once_with("CANONICAL", env="BASE", token="canonical-token")

    def test_ha_read_and_malformed_tool_requests_have_zero_writes(self):
        server = synthetic_server(servers={"achsuat": self.config()})
        response = Mock(status=200)
        response.read.return_value = json.dumps({"result": {"content": ["synthetic"]}}).encode()
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(AtelierClient, "_create_opener", return_value=opener):
            result = server.handle_tool_call("iris_get_class", {"server_id": "achsuat", "class_name": "Custom.Test"})
            self.assertFalse(result.get("isError", False))
            self.assertEqual([c.args[0].method for c in opener.open.call_args_list], ["GET"])
            opener.reset_mock()
            for args in ({"server_id": "achsuat"}, {"server_id": "achsuat", "class_name": 5}):
                result = server.handle_tool_call("iris_get_class", args)
                self.assertTrue(result.get("isError"))
            opener.open.assert_not_called()

    def test_namespace_rest_failure_never_calls_runner_or_mutates(self):
        for data in ({}, {"result": {"content": {"namespaces": "BAD"}}},
                     {"result": {"content": {"namespaces": ["../BAD"]}}}):
            client = synthetic_client()
            response = Mock(status=200)
            response.read.return_value = json.dumps(data).encode()
            client.opener.open.return_value = Mock()
            client.opener.open.side_effect = None
            client.opener.open.return_value.__enter__ = Mock(return_value=response)
            client.opener.open.return_value.__exit__ = Mock(return_value=False)
            with patch.object(client, "execute_runtime_code") as runner:
                with self.assertRaisesRegex(LocalPolicyError, "no executor fallback"):
                    client.list_namespaces()
                runner.assert_not_called()
                client._send_request.assert_not_called()
        client.opener.open.side_effect = TimeoutError("secret")
        with self.assertRaises(LocalPolicyError):
            client.list_namespaces()

    def test_namespace_ambiguity_failure_does_not_silently_route_user(self):
        client = synthetic_client("USER")
        server = synthetic_server(client, {"fake": {"namespace": "USER"}})
        with patch.object(client, "list_namespaces", side_effect=LocalPolicyError("Metadata unavailable")):
            result = server.handle_tool_call("iris_get_class", {"server_id": "fake", "class_name": "Custom.Test"})
        self.assertTrue(result.get("isError"))
        client._send_request.assert_not_called()

    def test_http_errors_do_not_read_or_echo_response_code_query_or_config(self):
        client = synthetic_client()
        error_body = io.BytesIO(b"private-query private-code private-config")
        error_body.read = Mock(wraps=error_body.read)
        client.opener.open.side_effect = urllib.error.HTTPError(
            "http://example.invalid/?private-query", 403, "private-config", {}, error_body)
        with patch("isc_ai.server.log_debug") as log:
            status, data = AtelierClient._send_request(client, "/action/query", method="POST",
                                                      payload={"query": "private-query", "parameters": ["private-code"]})
        self.assertEqual(status, 403)
        error_body.read.assert_not_called()
        self.assertTrue(error_body.closed)
        self.assertNotIn("private-", json.dumps(data) + str(log.call_args_list))

    def test_dispatch_exceptions_and_logs_do_not_echo_arguments_or_tracebacks(self):
        client = synthetic_client()
        client.run_query = Mock(side_effect=RuntimeError("private-query private-code private-config"))
        with patch("isc_ai.server.log_debug") as log:
            result = synthetic_server(client).handle_tool_call("iris_query_sql", {
                "server_id": "fake", "query": "SELECT 'private-query'", "parameters": ["private-code"],
            })
        self.assertTrue(result.get("isError"))
        self.assertNotIn("private-", json.dumps(result) + str(log.call_args_list))
        self.assertNotIn("Traceback", json.dumps(result))


class DocumentBoundaryTests(HermeticTestCase):
    INVALID = (
        "../../OTHER/doc/Custom.Example.cls", "Custom/Example.cls", "Custom\\Example.cls",
        "/Custom.Example.cls", "C:\\Custom.Example.cls", "Custom..Example.cls", ".cls",
        "..", "Custom.Example.cls/..", "Custom%2fExample.cls", "%2F.cls", "%5c.cls",
        "%252f.cls", "%2e%2e.cls", "Custom.Example.cls?namespace=OTHER", "Custom.Example.cls#x",
        "Custom.Example\x00.cls", "Custom.Example\n.cls", "Custom.Example.cls\n", "", None, 7, [], b"Test.cls",
    )

    def wire_document_http(self, client):
        """Exercise real Request construction with only synthetic HTTP responses."""
        calls = []
        metadata = metadata_responder(client.namespace)

        def respond(request, **kwargs):
            calls.append(request)
            payload = json.loads(request.data) if request.data else None
            if not request.full_url.startswith(client.base_url + "/"):
                raise AssertionError("Request escaped the expected base URL")
            endpoint = request.full_url[len(client.base_url):].split("?", 1)[0]
            if endpoint == "/action/query":
                status, data = metadata(endpoint, method=request.method, payload=payload)
            elif endpoint == "/action/compile":
                status, data = 200, {"status": {"errors": [], "summary": ""}}
            elif endpoint.startswith("/doc/"):
                status = 201 if request.method == "PUT" else 200
                data = {"result": {"content": ["Class Custom.Test {}"]}}
            else:
                raise AssertionError("Unexpected request")
            response = Mock(status=status)
            response.read.return_value = json.dumps(data).encode()
            context = Mock()
            context.__enter__ = Mock(return_value=response)
            context.__exit__ = Mock(return_value=False)
            return context

        client._send_request = AtelierClient._send_request.__get__(client)
        client.opener.open.side_effect = respond
        return calls

    def test_invalid_lowlevel_documents_fail_before_discovery_guard_or_http(self):
        operations = (
            lambda c, n: c.get_document(n), lambda c, n: c.save_document(n, ["synthetic"]),
            lambda c, n: c.delete_document(n), lambda c, n: c.compile_document(n),
        )
        for name in (*self.INVALID, "Custom.Example.txt", "NoExtension"):
            for operation in operations:
                with self.subTest(name=name, operation=operation):
                    client = synthetic_client()
                    guard = Mock(side_effect=AssertionError("guard must not run"))
                    client._probe_same_process_ccr_state = guard
                    with patch.object(client, "get_namespace_ccr_status") as discovery:
                        with self.assertRaises(LocalPolicyError):
                            operation(client, name)
                        discovery.assert_not_called()
                    guard.assert_not_called()
                    client._send_request.assert_not_called()
                    client.opener.open.assert_not_called()

    def test_invalid_dispatch_identifiers_have_zero_effects_even_without_auto_checkout(self):
        tools = {
            "iris_get_class": {}, "iris_compile_class": {}, "iris_delete_class": {},
            "iris_save_class": {"content": "synthetic"},
            "iris_save_class_from_file": {"file_path": "MUST-NOT-READ"},
            "iris_patch_class": {"old_string": "a", "new_string": "b"},
            "iris_ccr_status": {}, "iris_ccr_checkout": {"ccr_id": "SYNTHETIC"},
            "iris_ccr_undocheckout": {"ccr_id": "SYNTHETIC"},
            "iris_ccr_checkout_ct": {"ids": ["1"], "ccr_id": "SYNTHETIC"},
        }
        for identifier in self.INVALID:
            for tool, extra in tools.items():
                for auto_checkout in (False, True):
                    with self.subTest(identifier=identifier, tool=tool, auto_checkout=auto_checkout):
                        client = synthetic_client()
                        server = synthetic_server(client)
                        with patch("builtins.open") as files, patch("os.path.exists") as exists:
                            result = server.handle_tool_call(tool, {
                                "server_id": "fake", "class_name": identifier,
                                "auto_checkout": auto_checkout, **extra,
                            })
                            files.assert_not_called()
                            exists.assert_not_called()
                        self.assertTrue(result.get("isError"))
                        server.load_configuration.assert_not_called()
                        server.get_client.assert_not_called()
                        client._send_request.assert_not_called()
                        client.opener.open.assert_not_called()

    def test_valid_names_reach_exact_http_boundary_only_when_the_gate_admits_them(self):
        read_names = ("%Library.String.cls", "%SYSTEM.OBJ.cls", "%ABCD.Test.cls", "Custom.Test.cls",
                      "%Routine.mac", "Custom.Generated.1.int", "Common.inc", "Routine.bas", "Routine.obj",
                      "Custom.ACHS.Tests.Sample.cls")
        for name in read_names:
            with self.subTest(name=name, operation="get_document"):
                client = synthetic_client()
                calls = self.wire_document_http(client)
                client.get_document(name)
                request = calls[-1]
                self.assertEqual(request.method, "GET")
                endpoint = request.full_url.split("?", 1)[0]
                self.assertEqual(endpoint, client.base_url + "/doc/" + urllib.parse.quote(name, safe=""))
                self.assertEqual(urllib.parse.unquote(endpoint.rsplit("/", 1)[-1]), name)

        write_ops = (("save_document", "PUT"), ("delete_document", "DELETE"), ("compile_document", "POST"))
        cataloged = ("Custom.ACHS.Tests.Sample.cls", "Custom.ACHS.Routine.mac", "Custom.ACHS.Common.inc",
                     "Custom.ACHS.Generated.1.int")
        for name in cataloged:
            for operation, method in write_ops:
                with self.subTest(name=name, operation=operation):
                    client = synthetic_client()
                    script_guard(client, "ABSENT")
                    calls = self.wire_document_http(client)
                    if operation == "save_document":
                        self.assertTrue(client.save_document(name, ["synthetic"])[0])
                    else:
                        getattr(client, operation)(name)
                    request = calls[-1]
                    self.assertEqual(request.method, method)
                    if operation == "compile_document":
                        self.assertEqual(json.loads(request.data), [name])
                        self.assertEqual(request.full_url, client.base_url + "/action/compile")
                    else:
                        endpoint = request.full_url.split("?", 1)[0]
                        self.assertEqual(endpoint, client.base_url + "/doc/" + urllib.parse.quote(name, safe=""))
                        self.assertEqual(urllib.parse.unquote(endpoint.rsplit("/", 1)[-1]), name)

        for name in read_names[:-1]:
            for operation, _ in write_ops:
                with self.subTest(name=name, operation=operation, blocked=True):
                    client = synthetic_client()
                    guard = script_guard(client, "ABSENT")
                    calls = self.wire_document_http(client)
                    with self.assertRaisesRegex(LocalPolicyError, "not cataloged"):
                        if operation == "save_document":
                            client.save_document(name, ["synthetic"])
                        else:
                            getattr(client, operation)(name)
                    guard.assert_not_called()
                    self.assertEqual(calls, [])

    def test_get_class_preserves_case_and_extension_named_class_components(self):
        cases = (
            ("%SYSTEM.OBJ", "%SYSTEM.OBJ.cls"), ("%SYSTEM.OBJ.cls", "%SYSTEM.OBJ.cls"),
            ("Custom.ACHS.Obj", "Custom.ACHS.Obj.cls"), ("Custom.ACHS.Obj.cls", "Custom.ACHS.Obj.cls"),
            ("Custom.ACHS.obj", "Custom.ACHS.obj.cls"), ("Custom.ACHS.CLS", "Custom.ACHS.CLS.cls"),
            ("%Routine.mac", "%Routine.mac.cls"), ("%Library.String", "%Library.String.cls"),
        )
        for name, expected in cases:
            with self.subTest(name=name):
                client = synthetic_client()
                calls = self.wire_document_http(client)
                with patch.object(client, "get_document", wraps=client.get_document) as get_document:
                    result = synthetic_server(client).handle_tool_call("iris_get_class", {"server_id": "fake", "class_name": name})
                    get_document.assert_called_once_with(expected)
                self.assertFalse(result.get("isError", False))
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].full_url, client.base_url + "/doc/" + urllib.parse.quote(expected, safe=""))

    def test_compile_uses_exact_explicit_suffixes_without_case_normalization(self):
        cases = (
            ("Custom.ACHS.Obj", "Custom.ACHS.Obj.cls"), ("Custom.ACHS.Obj.cls", "Custom.ACHS.Obj.cls"),
            ("Custom.ACHS.obj.cls", "Custom.ACHS.obj.cls"), ("Custom.ACHS.Routine.mac", "Custom.ACHS.Routine.mac"),
            ("Custom.ACHS.Routine.MAC", "Custom.ACHS.Routine.MAC.cls"), ("Custom.ACHS.Common.inc", "Custom.ACHS.Common.inc"),
            ("Custom.ACHS.Sub.obj", "Custom.ACHS.Sub.obj"),
        )
        for name, expected in cases:
            with self.subTest(name=name):
                client = synthetic_client()
                script_guard(client, "ABSENT")
                calls = self.wire_document_http(client)
                result = synthetic_server(client).handle_tool_call("iris_compile_class", {"server_id": "fake", "class_name": name})
                self.assertFalse(result.get("isError", False), result)
                self.assertEqual(json.loads(calls[-1].data), [expected])
                self.assertEqual(calls[-1].full_url, client.base_url + "/action/compile")
        for name in ("%SYSTEM.OBJ", "Routine.mac", "Common.inc", "obj"):
            with self.subTest(name=name, blocked=True):
                client = synthetic_client()
                guard = script_guard(client, "ABSENT")
                calls = self.wire_document_http(client)
                result = synthetic_server(client).handle_tool_call("iris_compile_class", {"server_id": "fake", "class_name": name})
                self.assertTrue(result.get("isError"))
                self.assertIn("not cataloged", result["content"][0]["text"])
                guard.assert_not_called()
                self.assertEqual(calls, [])

    def test_ccr_status_and_checkout_accept_cataloged_extension_named_class_components(self):
        for name in ("Custom.ACHS.Obj", "Custom.ACHS.Obj.cls"):
            for tool in ("iris_ccr_status", "iris_ccr_checkout", "iris_ccr_undocheckout"):
                with self.subTest(name=name, tool=tool):
                    client = synthetic_client()
                    expected = name if name.endswith(".cls") else name + ".cls"
                    output = {
                        "iris_ccr_status": '{"has_sc": false}',
                        "iris_ccr_checkout": "VERIFIED_CHECKOUT|action=checkout|ccr_enc=SYNTHETIC",
                        "iris_ccr_undocheckout": "VERIFIED_UNDOCHECKOUT|ccr_enc=SYNTHETIC",
                    }[tool]
                    # Only the runner is synthetic: dispatch, validation, guard embedding and
                    # CCR code generation are real; no server execution occurs.
                    client.execute_runtime_code = Mock(return_value=output)
                    result = synthetic_server(client).handle_tool_call(tool, {
                        "server_id": "fake", "class_name": name, "ccr_id": "SYNTHETIC",
                    })
                    self.assertFalse(result.get("isError", False), result)
                    client.execute_runtime_code.assert_called_once()
                    code = client.execute_runtime_code.call_args.args[0]
                    self.assertIn('"' + expected + '"', code)
                    if tool == "iris_ccr_status":
                        self.assertNotIn("mcpGuardState", code)
                    else:
                        self.assertLess(code.index('If mcpGuardState \'= "CONTROLLED_ACTIVE" {'), code.index(expected))

        for name in ("%SYSTEM.OBJ", "%SYSTEM.OBJ.cls"):
            with self.subTest(name=name, tool="iris_ccr_status"):
                client = synthetic_client()
                client.execute_runtime_code = Mock(return_value='{"has_sc": false}')
                result = synthetic_server(client).handle_tool_call("iris_ccr_status", {
                    "server_id": "fake", "class_name": name,
                })
                self.assertFalse(result.get("isError", False), result)
                client.execute_runtime_code.assert_called_once()

            for tool in ("iris_ccr_checkout", "iris_ccr_undocheckout"):
                with self.subTest(name=name, tool=tool):
                    client = synthetic_client()
                    client.execute_runtime_code = Mock(side_effect=AssertionError("runner must not start"))
                    result = synthetic_server(client).handle_tool_call(tool, {
                        "server_id": "fake", "class_name": name, "ccr_id": "SYNTHETIC",
                    })
                    self.assertTrue(result.get("isError"), result)
                    self.assertIn("not cataloged", result["content"][0]["text"])
                    client.execute_runtime_code.assert_not_called()

    def test_valid_save_file_and_patch_dispatch_keep_behavior(self):
        cases = {
            "iris_save_class": {"content": "Class Custom.ACHS.Tests.Sample {}"},
            "iris_save_class_from_file": {"file_path": "synthetic.cls"},
            "iris_patch_class": {"old_string": "{}", "new_string": "{ }"},
        }
        for tool, extra in cases.items():
            with self.subTest(tool=tool):
                client = synthetic_client()
                script_guard(client, "ABSENT")
                calls = self.wire_document_http(client)
                with patch("builtins.open", mock_open(read_data="Class Custom.ACHS.Tests.Sample {}")), patch("os.path.exists", return_value=True):
                    result = synthetic_server(client).handle_tool_call(tool, {
                        "server_id": "fake", "class_name": "Custom.ACHS.Tests.Sample", "auto_checkout": False,
                        "verify": False, **extra,
                    })
                self.assertFalse(result.get("isError", False), result)
                writes = [request for request in calls if request.method == "PUT"]
                self.assertEqual(len(writes), 1)
                self.assertEqual(writes[0].full_url, client.base_url + "/doc/Custom.ACHS.Tests.Sample.cls?ignoreConflict=1")
                # No Atelier UserAction checkout is issued through the read transport.
                self.assertFalse(any(request.data and b"Extension_UserAction" in request.data for request in calls))

    def test_class_tools_preserve_components_that_match_document_extensions(self):
        for tool, extra in (("iris_save_class", {"content": "synthetic"}),
                            ("iris_save_class_from_file", {"file_path": "synthetic"}),
                            ("iris_patch_class", {"old_string": "{}", "new_string": "{ }"}),
                            ("iris_delete_class", {})):
            for name in ("Custom.ACHS.Obj", "Custom.ACHS.Obj.cls", "Custom.ACHS.mac", "Custom.ACHS.OBJ"):
                with self.subTest(tool=tool, name=name):
                    client = synthetic_client()
                    script_guard(client, "ABSENT")
                    calls = self.wire_document_http(client)
                    with patch("builtins.open", mock_open(read_data="synthetic")), patch("os.path.exists", return_value=True):
                        result = synthetic_server(client).handle_tool_call(tool, {
                            "server_id": "fake", "class_name": name, "auto_checkout": False, "verify": False, **extra,
                        })
                    self.assertFalse(result.get("isError", False), result)
                    expected = name if name.endswith(".cls") else name + ".cls"
                    mutations = [request for request in calls if request.method in {"PUT", "DELETE"}]
                    self.assertEqual(len(mutations), 1)
                    self.assertEqual(mutations[0].full_url.split("?", 1)[0],
                                     client.base_url + "/doc/" + urllib.parse.quote(expected, safe=""))
            for name in ("%SYSTEM.OBJ", "Test.mac", "Custom.Test"):
                with self.subTest(tool=tool, name=name, blocked=True):
                    client = synthetic_client()
                    guard = script_guard(client, "ABSENT")
                    calls = self.wire_document_http(client)
                    with patch("builtins.open", mock_open(read_data="synthetic")), patch("os.path.exists", return_value=True):
                        result = synthetic_server(client).handle_tool_call(tool, {
                            "server_id": "fake", "class_name": name, "auto_checkout": False, "verify": False, **extra,
                        })
                    self.assertTrue(result.get("isError"))
                    guard.assert_not_called()
                    self.assertEqual([r for r in calls if r.method in {"PUT", "DELETE"}], [])

    # ------------------------------------------------------------------
    # Document tools: include files and routines with an explicit extension.
    # ------------------------------------------------------------------

    DOC_WRITE_TOOLS = (
        ("iris_save_document", {"content": "#define X 1"}),
        ("iris_save_document_from_file", {"file_path": "synthetic"}),
        ("iris_patch_document", {"old_string": "1", "new_string": "2", "expected_count": 0}),
        ("iris_delete_document", {}),
    )

    def call_document_tool(self, client, tool, doc_name, **extra):
        with patch("builtins.open", mock_open(read_data="#define X 1")), patch("os.path.exists", return_value=True):
            return synthetic_server(client).handle_tool_call(tool, {
                "server_id": "fake", "document_name": doc_name, "auto_checkout": False, "verify": False, **extra,
            })

    def test_document_tools_reach_exact_http_boundary_for_inc_mac_int_and_cls(self):
        for doc_name in ("Custom.ACHS.Macros.inc", "Custom.ACHS.Util.mac", "Custom.ACHS.Generated.1.int",
                         "Custom.ACHS.Tests.Sample.cls"):
            with self.subTest(document=doc_name, tool="iris_get_document"):
                client = synthetic_client()
                calls = self.wire_document_http(client)
                result = self.call_document_tool(client, "iris_get_document", doc_name)
                self.assertFalse(result.get("isError", False), result)
                self.assertEqual(calls[-1].method, "GET")
                self.assertEqual(calls[-1].full_url, client.base_url + "/doc/" + urllib.parse.quote(doc_name, safe=""))
            for tool, extra in self.DOC_WRITE_TOOLS:
                with self.subTest(document=doc_name, tool=tool):
                    client = synthetic_client()
                    script_guard(client, "ABSENT")
                    calls = self.wire_document_http(client)
                    result = self.call_document_tool(client, tool, doc_name, **extra)
                    self.assertFalse(result.get("isError", False), result)
                    mutations = [r for r in calls if r.method in {"PUT", "DELETE"}]
                    self.assertEqual(len(mutations), 1)
                    self.assertEqual(mutations[0].full_url.split("?", 1)[0],
                                     client.base_url + "/doc/" + urllib.parse.quote(doc_name, safe=""))
                    if tool == "iris_save_document":
                        self.assertEqual(json.loads(mutations[0].data)["content"], ["#define X 1"])

    def test_document_tools_never_infer_or_append_an_extension(self):
        # Missing, uppercase or unsupported extensions are rejected before any
        # config reload, gate or HTTP request.
        for doc_name in ("Custom.ACHS.Macros", "Custom.ACHS.Macros.INC", "Custom.ACHS.Macros.txt",
                         "Custom.ACHS.Macros.xml", "", None):
            for tool in ("iris_get_document",) + tuple(t for t, _ in self.DOC_WRITE_TOOLS):
                with self.subTest(document=doc_name, tool=tool):
                    client = synthetic_client()
                    guard = script_guard(client, "ABSENT")
                    calls = self.wire_document_http(client)
                    extra = dict(self.DOC_WRITE_TOOLS).get(tool, {})
                    result = self.call_document_tool(client, tool, doc_name, **extra)
                    self.assertTrue(result.get("isError"), result)
                    guard.assert_not_called()
                    self.assertEqual(calls, [])

    def test_document_writes_honor_package_catalog_by_stem(self):
        # The stem (name without extension) must be cataloged, exactly as for classes.
        for doc_name in ("Region.Core.inc", "%SYS.Macros.inc", "Custom.Other.Util.mac", "MCP.CCRMutation.inc"):
            for tool, extra in self.DOC_WRITE_TOOLS:
                with self.subTest(document=doc_name, tool=tool):
                    client = synthetic_client()
                    guard = script_guard(client, "ABSENT")
                    calls = self.wire_document_http(client)
                    result = self.call_document_tool(client, tool, doc_name, **extra)
                    self.assertTrue(result.get("isError"), result)
                    self.assertIn("not cataloged", result["content"][0]["text"])
                    guard.assert_not_called()
                    self.assertEqual([r for r in calls if r.method in {"PUT", "DELETE"}], [])
            # Reading uncataloged documents stays allowed (READ gate only).
            client = synthetic_client()
            calls = self.wire_document_http(client)
            self.assertFalse(self.call_document_tool(client, "iris_get_document", doc_name).get("isError", False))

    def test_document_write_with_ccr_checks_out_the_exact_document(self):
        client = synthetic_client()
        script_guard(client, "CONTROLLED_ACTIVE")
        calls = self.wire_document_http(client)
        checkout = Mock(return_value=(True, "ok"))
        client.ccr_checkout = checkout
        result = synthetic_server(client).handle_tool_call("iris_save_document", {
            "server_id": "fake", "document_name": "Custom.ACHS.Macros.inc", "content": "#define X 1",
            "ccr_id": "ACHS0875", "verify": False, "compile": True,
        })
        self.assertFalse(result.get("isError", False), result)
        checkout.assert_called_once_with(None, "ACHS0875", document_name="Custom.ACHS.Macros.inc")
        self.assertIn("Exito bajo CCR ACHS0875", result["content"][0]["text"])
        self.assertEqual([r.method for r in calls if r.method in {"PUT", "POST"} and "/doc/" in r.full_url], ["PUT"])
        compile_calls = [r for r in calls if r.full_url.endswith("/action/compile")]
        self.assertEqual(json.loads(compile_calls[-1].data), ["Custom.ACHS.Macros.inc"])

    def test_ccr_tools_accept_document_name_or_class_name_but_not_both(self):
        for tool in ("iris_ccr_status", "iris_ccr_checkout", "iris_ccr_undocheckout"):
            for arguments, expected in (
                ({"class_name": "Custom.ACHS.Obj"}, "Custom.ACHS.Obj.cls"),
                ({"class_name": "Custom.ACHS.inc"}, "Custom.ACHS.inc.cls"),
                ({"document_name": "Custom.ACHS.Macros.inc"}, "Custom.ACHS.Macros.inc"),
                ({"document_name": "Custom.ACHS.Util.mac"}, "Custom.ACHS.Util.mac"),
            ):
                with self.subTest(tool=tool, arguments=arguments):
                    client = synthetic_client()
                    seen = {}
                    client.get_ccr_status = lambda class_name=None, document_name=None: seen.setdefault("doc", document_name) and {"has_sc": False}
                    client.ccr_checkout = lambda class_name, ccr, document_name=None: (seen.setdefault("doc", document_name), (True, "ok"))[1]
                    client.ccr_undocheckout = lambda class_name, ccr, document_name=None: (seen.setdefault("doc", document_name), (True, "ok"))[1]
                    result = synthetic_server(client).handle_tool_call(tool, {"server_id": "fake", "ccr_id": "ACHS0875", **arguments})
                    self.assertFalse(result.get("isError", False), result)
                    self.assertEqual(seen["doc"], expected)
            for arguments in ({}, {"class_name": "Custom.ACHS.Obj", "document_name": "Custom.ACHS.Macros.inc"},
                              {"document_name": "Custom.ACHS.Macros"}, {"document_name": "Custom.ACHS.Macros.INC"}):
                with self.subTest(tool=tool, arguments=arguments, blocked=True):
                    client = synthetic_client()
                    client.get_ccr_status = client.ccr_checkout = client.ccr_undocheckout = Mock(side_effect=AssertionError("must not reach client"))
                    result = synthetic_server(client).handle_tool_call(tool, {"server_id": "fake", "ccr_id": "ACHS0875", **arguments})
                    self.assertTrue(result.get("isError"), result)

    def test_client_ccr_methods_accept_documents_and_reject_uncataloged_or_ambiguous(self):
        client = synthetic_client()
        captured = script_guarded_runner(client, "VERIFIED_CHECKOUT|action=added|ccr_enc=ACHS0875\n")
        success, message = client.ccr_checkout(None, "ACHS0875", document_name="Custom.ACHS.Macros.inc")
        self.assertTrue(success, message)
        self.assertIn('Set className = "Custom.ACHS.Macros.inc"', captured[-1])
        self.assertIn("New document added", message)
        with self.assertRaisesRegex(LocalPolicyError, "exactly one"):
            client.ccr_checkout("Custom.ACHS.A", "ACHS0875", document_name="Custom.ACHS.Macros.inc")
        with self.assertRaisesRegex(LocalPolicyError, "exactly one"):
            client.get_ccr_status()
        with self.assertRaisesRegex(LocalPolicyError, "not cataloged"):
            client.ccr_undocheckout(None, "ACHS0875", document_name="Region.Core.inc")
        with self.assertRaisesRegex(LocalPolicyError, "extension"):
            client.ccr_checkout(None, "ACHS0875", document_name="Custom.ACHS.Macros")


class TransportSchemeTests(HermeticTestCase):
    """scheme/verify_ssl are per-server configuration; defaults keep plain HTTP with verified TLS."""

    def make(self, **kw):
        with patch.object(AtelierClient, "_create_opener", return_value=Mock()):
            return AtelierClient("10.0.0.1", 443, "/p", "BASE-ACHS", "u", "p", env_role="BASE", **kw)

    def test_default_scheme_is_http_and_validate_target_accepts_it(self):
        client = self.make()
        self.assertTrue(client.base_url.startswith("http://10.0.0.1:443/p/api/atelier/v1/BASE-ACHS"))
        self.assertEqual(client.root_url, "http://10.0.0.1:443/p/api/atelier/")
        client._validate_target()  # must not raise

    def test_https_scheme_builds_https_urls(self):
        client = self.make(scheme="https")
        self.assertEqual(client.base_url, "https://10.0.0.1:443/p/api/atelier/v1/BASE-ACHS")
        self.assertEqual(client.root_url, "https://10.0.0.1:443/p/api/atelier/")
        client._validate_target()

    def test_invalid_scheme_is_rejected(self):
        with self.assertRaises(LocalPolicyError):
            self.make(scheme="ftp")

    def test_ssl_handlers_only_when_https_and_verification_disabled(self):
        self.assertEqual(self.make()._ssl_handlers(), [])
        self.assertEqual(self.make(scheme="https")._ssl_handlers(), [])
        self.assertEqual(self.make(scheme="http", verify_ssl=False)._ssl_handlers(), [])
        handlers = self.make(scheme="https", verify_ssl=False)._ssl_handlers()
        self.assertEqual(len(handlers), 1)
        self.assertIsInstance(handlers[0], urllib.request.HTTPSHandler)

    def test_connection_config_validation_accepts_and_rejects_scheme_fields(self):
        base = {"host": "h", "port": 443, "path_prefix": "", "username": "u", "password": "p", "namespace": "N"}
        IRISMCPServer._validate_connection_config(dict(base))
        IRISMCPServer._validate_connection_config(dict(base, scheme="https", verify_ssl=False))
        for bad in ({"scheme": "ftp"}, {"verify_ssl": "no"}, {"scheme": "HTTPS"}):
            with self.subTest(bad=bad), self.assertRaises(LocalPolicyError):
                IRISMCPServer._validate_connection_config(dict(base, **bad))

    def test_get_client_forwards_scheme_and_verify_ssl(self):
        server = synthetic_server(synthetic_client())
        server.servers = {"tls": {"host": "h", "port": 443, "path_prefix": "/x", "namespace": "BASE-ACHS",
                                  "username": "u", "password": "p", "env_role": "BASE",
                                  "scheme": "https", "verify_ssl": False}}
        with patch.object(AtelierClient, "_create_opener", return_value=Mock()):
            client = server._get_client_direct("tls")
        self.assertEqual(client.scheme, "https")
        self.assertFalse(client.verify_ssl)
        self.assertEqual(client.base_url, "https://h:443/x/api/atelier/v1/BASE-ACHS")


class RoundTripVerifyTests(HermeticTestCase):
    """verify_document must tolerate the IRIS UDL formatter (indentation, blank lines) and still
    detect duplicated or truncated code."""

    SENT = ["Class X", "{", "", "ClassMethod A()", "{", "\t\tIf x {", "\t\t\tSet y = 1", "\t\t}", "}", "", "}"]

    def verify(self, server_lines):
        client = synthetic_client()
        client.get_document = Mock(return_value=server_lines)
        return client.verify_document("Custom.ACHS.X.cls", self.SENT)

    def test_identical_content_verifies(self):
        ok, msg = self.verify(list(self.SENT))
        self.assertTrue(ok, msg)
        self.assertNotIn("format normalized", msg)

    def test_reindented_and_reblanked_content_verifies_with_note(self):
        server = ["Class X", "{", "ClassMethod A()", "{", "If x {", "    Set y = 1", "}", "}", "", "", "}", ""]
        ok, msg = self.verify(server)
        self.assertTrue(ok, msg)
        self.assertIn("format normalized", msg)

    def test_duplicated_block_is_rejected(self):
        server = list(self.SENT[:7]) + ["\t\t\tSet y = 1"] + list(self.SENT[7:])
        ok, msg = self.verify(server)
        self.assertFalse(ok)
        self.assertIn("Content MISMATCH", msg)

    def test_truncated_content_is_rejected(self):
        ok, msg = self.verify(list(self.SENT[:-2]))
        self.assertFalse(ok)
        self.assertIn("Content MISMATCH", msg)

    def test_changed_token_inside_line_is_rejected(self):
        server = list(self.SENT)
        server[6] = "\t\t\tSet y = 2"
        ok, msg = self.verify(server)
        self.assertFalse(ok)
        self.assertIn("code line 6", msg)


class SqlDiagnosticTests(HermeticTestCase):
    """SQL failures must tell the operator WHICH layer failed and expose the IRIS
    SQLCODE/%msg (operator diagnostics), while never echoing the query text."""

    QUERY = "SELECT SecretColumn FROM Secret_Schema.SecretTable WHERE X = 'sensitive-literal'"

    def test_execution_failure_exposes_sqlcode_and_message_but_not_query(self):
        client = synthetic_client()
        client._send_request = Mock(return_value=(200, {
            "status": {"errors": [{"error": "ERROR #5540: SQLCODE: -30 Message: Table 'SECRET_SCHEMA.SECRETTABLE' not found",
                                   "code": 5540}], "summary": "x"},
            "result": {"content": []},
        }))
        with self.assertRaises(LocalPolicyError) as ctx:
            client.run_query(self.QUERY)
        message = str(ctx.exception)
        self.assertIn("execution failed", message)
        self.assertIn("SQLCODE=-30", message)
        self.assertIn("not found", message)
        self.assertNotIn("SecretColumn", message)
        self.assertNotIn("sensitive-literal", message)
        self.assertNotIn("SELECT", message)

    def test_execution_failure_diagnostic_is_bounded_and_single_line(self):
        client = synthetic_client()
        long_text = "Line one\r\nLine two " + ("z" * 1000)
        client._send_request = Mock(return_value=(200, {
            "status": {"errors": [{"error": long_text}], "summary": ""},
            "result": {"content": []},
        }))
        with self.assertRaises(LocalPolicyError) as ctx:
            client.run_query(self.QUERY)
        message = str(ctx.exception)
        self.assertNotIn("\n", message)
        self.assertNotIn("\r", message)
        self.assertLess(len(message), 500)
        self.assertIn("...", message)

    def test_request_failure_reports_http_status_and_hint(self):
        for status, hint in ((504, "timeout"), (401, "authentication"), (404, "not found"), (500, "transport")):
            with self.subTest(status=status):
                client = synthetic_client()
                client._send_request = Mock(return_value=(status, {"error": "x"}))
                with self.assertRaises(LocalPolicyError) as ctx:
                    client.run_query(self.QUERY)
                message = str(ctx.exception)
                self.assertIn("request failed", message)
                self.assertIn(f"HTTP {status}", message)
                self.assertIn(hint, message)
                self.assertNotIn("SecretColumn", message)

    def test_client_timeout_maps_to_504_not_500(self):
        import socket as _socket
        client = synthetic_client()
        opener = Mock()
        opener.open.side_effect = _socket.timeout("timed out")
        client.opener = opener
        del client._send_request  # use the real transport
        status, data = client._send_request("/action/query", method="POST", payload={"query": "SELECT 1", "parameters": []})
        self.assertEqual(status, 504)
        url_err = urllib.error.URLError(_socket.timeout("timed out"))
        opener.open.side_effect = url_err
        status, data = client._send_request("/action/query", method="POST", payload={"query": "SELECT 1", "parameters": []})
        self.assertEqual(status, 504)
        opener.open.side_effect = urllib.error.URLError("connection refused")
        status, data = client._send_request("/action/query", method="POST", payload={"query": "SELECT 1", "parameters": []})
        self.assertEqual(status, 500)


class RuntimeGateTests(HermeticTestCase):
    """Ensemble runtime operations are gated by the production gate only, never by CCR.

    Restarting a production item does not touch source code, Perforce or TC change
    control, so CCR discovery UNKNOWN must not block it. Production targets stay blocked.
    """

    RUNTIME_CALLS = {
        "iris_ensemble_component_control": {"action": "restart", "component_name": "REST.BO.Synthetic"},
        "iris_ensemble_production_control": {"action": "restart"},
        "iris_ensemble_queue_control": {"action": "abort_message", "message_id": 1},
        "iris_ensemble_default_settings": {"action": "set", "setting_name": "Synthetic", "setting_value": "1"},
    }

    def test_runtime_ops_allowed_under_ccr_unknown_on_nonproduction(self):
        for role in ("BASE", "UAT"):
            for name, args in self.RUNTIME_CALLS.items():
                with self.subTest(role=role, name=name):
                    # engine=1, configured=1 -> external discovery is UNKNOWN (PARTIAL)
                    client = synthetic_client(role=role, engine=1, configured=1)
                    self.assertEqual(client.get_namespace_ccr_status()["applicability"], "UNKNOWN")
                    client.execute_runtime_code = Mock(return_value="")
                    result = synthetic_server(client).handle_tool_call(name, {
                        "server_id": "fake", "namespace": "BASE-ACHS", **args,
                    })
                    self.assertFalse(result.get("isError"), result)
                    client.execute_runtime_code.assert_called()

    def test_runtime_ops_blocked_on_production_targets(self):
        cases = (
            dict(production=True),
            dict(role="LIVE"),
            dict(namespace="LIVE-ACHS"),
        )
        for kwargs in cases:
            for name, args in self.RUNTIME_CALLS.items():
                with self.subTest(kwargs=kwargs, name=name):
                    client = synthetic_client(**kwargs)
                    client.execute_runtime_code = Mock(side_effect=AssertionError("must not run"))
                    result = synthetic_server(client).handle_tool_call(name, {
                        "server_id": "fake", "namespace": kwargs.get("namespace", "BASE-ACHS"), **args,
                    })
                    self.assertTrue(result.get("isError"), result)
                    client.execute_runtime_code.assert_not_called()

    def test_runtime_runner_never_consults_ccr_state(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        client._probe_same_process_ccr_state = Mock(side_effect=AssertionError("CCR guard must not be consulted"))
        client.get_namespace_ccr_status = Mock(side_effect=AssertionError("discovery must not be consulted"))
        client.is_code_write_allowed = Mock(side_effect=AssertionError("code-write gate must not be consulted"))
        client._ephemeral_runner_has_pending_change = Mock(return_value=False)
        client._ephemeral_runner_is_absent = Mock(return_value=True)
        client._ephemeral_runner_save = Mock()
        client._ephemeral_runner_compile = Mock()
        client._ephemeral_runner_query = Mock(return_value="ok")
        client._delete_ephemeral_runner = Mock(return_value=True)
        self.assertEqual(client.execute_runtime_code('Write "x"'), "ok")
        client._delete_ephemeral_runner.assert_called_once()

    def test_runtime_runner_fails_closed_on_cleanup_ambiguity(self):
        client = synthetic_client(role="BASE", engine=1, configured=1)
        client._ephemeral_runner_has_pending_change = Mock(return_value=False)
        client._ephemeral_runner_is_absent = Mock(return_value=True)
        client._ephemeral_runner_save = Mock()
        client._ephemeral_runner_compile = Mock()
        client._ephemeral_runner_query = Mock(return_value="ok")
        client._delete_ephemeral_runner = Mock(return_value=False)
        with self.assertRaisesRegex(LocalPolicyError, "cleanup failed"):
            client.execute_runtime_code('Write "x"')

    def test_runtime_never_blocks_direct_ha_nodes_on_nonproduction(self):
        # RUNTIME targets a node on purpose (clinical UAT VIP has no Atelier).
        # Active or backup, known or unknown prefix: allowed. Production stays blocked.
        for prefix, jobs in (("/trakuat01", 3), ("/trakuat02", 0), ("/other", 0), ("/intuat01", 0)):
            with self.subTest(prefix=prefix, jobs=jobs):
                client = synthetic_client(namespace="HMFSYS", role="UAT", host="10.244.20.4",
                                          path_prefix=prefix, ens_jobs=jobs)
                self.assertTrue(client.is_runtime_operation_allowed()[0])
        client = synthetic_client(namespace="HMFSYS", role="UAT", host="10.244.20.4",
                                  path_prefix="/trakuat01", ens_jobs=3, production=True)
        self.assertFalse(client.is_runtime_operation_allowed()[0])
        # CODE WRITE never takes the clinical node route.
        client = synthetic_client(namespace="UAT-ACHS", role="UAT", host="10.244.20.4",
                                  path_prefix="/trakuat01", ens_jobs=3)
        self.assertFalse(client.is_code_write_route_allowed()[0])


class GateMatrixTests(HermeticTestCase):
    """Three gate categories x five targets, plus same-process guard state routing.

    Every denial is proven to produce no network mutation; the guard runner is
    synthetic (CONTROLLED_ACTIVE / SUSPENDED / ABSENT) and its routing is asserted
    per state. UNKNOWN never appears as an authorization outcome.
    """

    def targets(self):
        return (
            ("BASE non-prod", lambda: synthetic_client(), True),
            ("UAT non-prod VIP", lambda: synthetic_client(namespace="UAT-ACHS", role="UAT", host="10.244.20.6"), True),
            ("is_production", lambda: synthetic_client(production=True), False),
            ("LIVE role", lambda: synthetic_client(role="LIVE"), False),
            ("PROD namespace", lambda: synthetic_client(namespace="APP-PROD"), False),
        )

    def assert_no_mutation(self, client):
        for call in client._send_request.call_args_list:
            self.assertNotIn(call.kwargs.get("method"), ("PUT", "DELETE"))
            self.assertNotEqual(call.args[0], "/action/compile")

    def test_tool_categories_partition_every_tool(self):
        server = object.__new__(IRISMCPServer)
        names = {tool["name"] for tool in server.handle_tools_list()["tools"]}
        categories = (IRISMCPServer.READ_TOOLS, IRISMCPServer.RUNTIME_TOOLS, IRISMCPServer.CODE_WRITE_TOOLS)
        self.assertEqual(set().union(*categories) | {"iris_list_servers"}, names)
        for first in categories:
            for second in categories:
                if first is not second:
                    self.assertEqual(first & second, frozenset())
        self.assertTrue(IRISMCPServer._ENSEMBLE_TOOLS <= IRISMCPServer.RUNTIME_TOOLS)

    def test_read_category_matrix(self):
        for label, factory, expected in self.targets():
            with self.subTest(target=label):
                client = factory()
                client._probe_same_process_ccr_state = Mock(side_effect=AssertionError("guard is not a read gate"))
                client.get_namespace_ccr_status = Mock(side_effect=AssertionError("discovery is not a read gate"))
                client._send_request.side_effect = None
                client._send_request.return_value = (200, {"result": {"content": ["Class X {}"]}})
                result = synthetic_server(client).handle_tool_call("iris_get_class", {
                    "server_id": "fake", "namespace": client.namespace, "class_name": "Custom.Test",
                })
                self.assertEqual(not result.get("isError", False), expected, (label, result))
                self.assertEqual(client.is_read_allowed()[0], expected)
                if not expected:
                    client._send_request.assert_not_called()
                    self.assertIn("Operation denied", result["content"][0]["text"])
                    self.assertNotIn("UNKNOWN", result["content"][0]["text"])

    def test_runtime_category_matrix(self):
        for label, factory, expected in self.targets():
            with self.subTest(target=label):
                client = factory()
                client._probe_same_process_ccr_state = Mock(side_effect=AssertionError("guard is not a runtime gate"))
                client.get_namespace_ccr_status = Mock(side_effect=AssertionError("discovery is not a runtime gate"))
                client._execute_direct_objectscript = Mock(return_value="ok")
                result = synthetic_server(client).handle_tool_call("iris_execute_objectscript", {
                    "server_id": "fake", "code": "Write 1",
                })
                self.assertEqual(not result.get("isError", False), expected, (label, result))
                self.assertEqual(client.is_runtime_operation_allowed()[0], expected)
                self.assertEqual(client._execute_direct_objectscript.called, expected)
                if not expected:
                    client._send_request.assert_not_called()
                    self.assertNotIn("UNKNOWN", result["content"][0]["text"])

    def test_code_write_category_matrix(self):
        # UAT-ACHS has no direct-write route even when the guard reports ABSENT.
        expectations = {"BASE non-prod": True, "UAT non-prod VIP": False, "is_production": False,
                        "LIVE role": False, "PROD namespace": False}
        for label, factory, _ in self.targets():
            with self.subTest(target=label):
                client = factory()
                guard = script_guard(client, "ABSENT")
                client._send_request.side_effect = None
                client._send_request.return_value = (200, {})
                result = synthetic_server(client).handle_tool_call("iris_delete_class", {
                    "server_id": "fake", "namespace": client.namespace, "class_name": "Custom.ACHS.Tests.Sample",
                })
                expected = expectations[label]
                self.assertEqual(not result.get("isError", False), expected, (label, result))
                if expected:
                    self.assertEqual(client._send_request.call_args.kwargs["method"], "DELETE")
                else:
                    client._send_request.assert_not_called()
                    self.assertNotIn("UNKNOWN", result["content"][0]["text"])
                if label in ("is_production", "LIVE role", "PROD namespace"):
                    guard.assert_not_called()

    def test_guard_state_routing_per_operation_kind(self):
        # (document, ccr_tracking, require_controlled) -> expected per state on BASE.
        matrix = {
            ("tracked save", CATALOGED, "ACHS0875", False): {"CONTROLLED_ACTIVE": True, "SUSPENDED": False, "ABSENT": True},
            ("untracked save/delete", CATALOGED, None, False): {"CONTROLLED_ACTIVE": False, "SUSPENDED": False, "ABSENT": True},
            ("write SQL without document", None, None, False): {"CONTROLLED_ACTIVE": False, "SUSPENDED": False, "ABSENT": False},
            ("controlled-only op", None, "ACHS0875", True): {"CONTROLLED_ACTIVE": True, "SUSPENDED": False, "ABSENT": False},
        }
        for (label, document, tracking, controlled), expected in matrix.items():
            for state in GUARD_STATES:
                with self.subTest(op=label, state=state):
                    client = synthetic_client()
                    probe = script_guard(client, state)
                    allowed, reason = client.is_code_write_allowed(document, tracking, controlled)
                    self.assertEqual(allowed, expected[state], (label, state, reason))
                    probe.assert_called_once_with()
                    self.assertNotIn("UNKNOWN", reason)
                    if state == "SUSPENDED":
                        self.assertIn("SUSPENDED", reason)

    def test_guard_denial_produces_no_mutation_on_real_transport(self):
        for state in ("SUSPENDED", "CONTROLLED_ACTIVE"):
            with self.subTest(state=state):
                client = synthetic_client()
                script_guard(client, state)
                client._send_request.side_effect = None
                client._send_request.return_value = (201, {})
                for operation in (lambda: client.save_document(CATALOGED, ["x"]),
                                  lambda: client.compile_document(CATALOGED),
                                  lambda: client.delete_document(CATALOGED)):
                    with self.assertRaises(LocalPolicyError):
                        operation()
                client._send_request.assert_not_called()

    def test_int_uat_exception_keeps_active_node_rule_under_absent_guard(self):
        vip = synthetic_client("INT", "UAT", host="10.244.20.6", path_prefix="/clacintuat")
        script_guard(vip, "ABSENT")
        self.assertTrue(vip.is_code_write_allowed("SAP.Api.cls")[0])
        # MPI.REST.* was extended to INT UAT on 2026-09-16 (CCR ACHS0965) and now
        # follows the same route as SAP.*; unrelated packages remain blocked.
        self.assertTrue(vip.is_code_write_allowed("MPI.REST.Lookup.cls")[0])
        self.assertFalse(vip.is_code_write_allowed("Custom.ACHS.Other.cls")[0])

        active = synthetic_client("INT", "UAT", host="10.244.20.4", path_prefix="/intuat01", ens_jobs=73)
        script_guard(active, "ABSENT")
        allowed, reason = active.is_code_write_allowed("SAP.Api.cls")
        self.assertTrue(allowed)
        self.assertIn("ACTIVE mirror node", reason)
        before = active._send_request.call_count
        active.is_code_write_allowed("SAP.Api.cls")
        self.assertGreater(active._send_request.call_count, before)

        for ens_jobs in (0, "0", "many", -1, 1.5):
            with self.subTest(ens_jobs=ens_jobs):
                backup = synthetic_client("INT", "UAT", host="10.244.20.5", path_prefix="/intuat02", ens_jobs=ens_jobs)
                guard = script_guard(backup, "ABSENT")
                allowed, reason = backup.is_code_write_allowed("SAP.Api.cls")
                self.assertFalse(allowed)
                self.assertIn("not executing", reason)
                guard.assert_not_called()

        for ns, host, prefix in (("UAT-ACHS", "10.244.20.4", "/trakuat01"),
                                 ("INT", "10.244.20.4", "/trakuat01"),
                                 ("INT", "10.244.20.9", "/intuat01")):
            with self.subTest(ns=ns, host=host, prefix=prefix):
                other = synthetic_client(ns, "UAT", host=host, path_prefix=prefix, ens_jobs=99)
                guard = script_guard(other, "ABSENT")
                self.assertFalse(other.is_code_write_allowed("SAP.Api.cls")[0])
                guard.assert_not_called()

    def test_guard_probe_runs_through_the_runtime_runner_only(self):
        client = synthetic_client()
        client._ephemeral_runner_has_pending_change = Mock(return_value=False)
        client._ephemeral_runner_is_absent = Mock(return_value=True)
        client._ephemeral_runner_save = Mock()
        client._ephemeral_runner_compile = Mock()
        client._ephemeral_runner_query = Mock(return_value=guard_report_line("ABSENT"))
        client._delete_ephemeral_runner = Mock(return_value=True)
        self.assertEqual(client._probe_same_process_ccr_state()["state"], "ABSENT")
        client._ephemeral_runner_save.assert_called_once()
        client._delete_ephemeral_runner.assert_called_once()
        uploaded = "\n".join(client._ephemeral_runner_save.call_args.args[1])
        self.assertIn('Write "MCPGUARD|state="', uploaded)
        # The probe itself is denied where the runtime gate is denied (production only;
        # direct HA nodes are admitted for RUNTIME).
        for factory in (lambda: synthetic_client(production=True), lambda: synthetic_client(role="LIVE")):
            blocked = factory()
            blocked._ephemeral_runner_save = Mock(side_effect=AssertionError("must not upload"))
            with self.assertRaises(LocalPolicyError):
                blocked._probe_same_process_ccr_state()
            blocked._ephemeral_runner_save.assert_not_called()

    def test_unlock_questionnaire_is_denied_in_every_guard_state_without_side_effects(self):
        for state in GUARD_STATES:
            with self.subTest(state=state):
                client = synthetic_client()
                script_guard(client, state)
                client._send_request.side_effect = None
                client._send_request.return_value = envelope([])
                result = synthetic_server(client).handle_tool_call("iris_unlock_questionnaire", {
                    "server_id": "fake", "win_code": "ACHSPREANE",
                })
                self.assertTrue(result.get("isError"), result)
                self.assertIn("Denied", result["content"][0]["text"])
                client._send_request.assert_not_called()

    def test_checkout_ct_requires_explicit_ccr_and_guarded_runner(self):
        client = synthetic_client()
        captured = script_guarded_runner(client, "OK:2|ERR:0|MSGS:")
        result = synthetic_server(client).handle_tool_call("iris_ccr_checkout_ct", {
            "server_id": "fake", "class_name": "epr.CTIconAssociation", "ids": ["806", "807"], "ccr_id": "ACHS0875",
        })
        self.assertFalse(result.get("isError", False), result)
        code = captured[0]
        self.assertLess(code.index('If mcpGuardState \'= "CONTROLLED_ACTIVE" {'), code.index("Set %ccrSession = requestedCCR"))
        self.assertIn('Set requestedCCR = "ACHS0875"', code)
        self.assertIn('$ClassMethod("TC.ChangeControl.CCR", "%New", $lb($username))', code)
        self.assertIn('cc.CheckOut($lb("806","epr.CTIconAssociation"))', code)
        self.assertNotIn("##class(TC.", code)

        missing = synthetic_client()
        missing.execute_runtime_code = Mock(side_effect=AssertionError("runner must not start"))
        with patch.dict(os.environ, {"IRIS_CCR_ID": ""}):
            result = synthetic_server(missing).handle_tool_call("iris_ccr_checkout_ct", {
                "server_id": "fake", "class_name": "epr.CTIconAssociation", "ids": ["806"],
            })
        self.assertTrue(result.get("isError"))

        denied = synthetic_client()
        script_guarded_runner(denied, "ERROR_CONTROL|state=ABSENT|message=blocked")
        result = synthetic_server(denied).handle_tool_call("iris_ccr_checkout_ct", {
            "server_id": "fake", "class_name": "epr.CTIconAssociation", "ids": ["806"], "ccr_id": "ACHS0875",
        })
        self.assertTrue(result.get("isError"))
        self.assertIn("ERROR_CONTROL", result["content"][0]["text"])


if __name__ == "__main__":
    import unittest
    unittest.main()
