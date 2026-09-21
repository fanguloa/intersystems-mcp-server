import base64
import os
import re
import unittest
import urllib.parse
import unittest.mock
from unittest.mock import Mock, mock_open, patch

from tests.hermetic_test_support import (
    AtelierClient, IRISMCPServer, HermeticTestCase, synthetic_client, script_guard,
    script_guarded_runner, GUARD_STATES, REFERENCE_POLICY,
)
from isc_ai.server import LocalPolicyError


def encode_record_value(value):
    return urllib.parse.quote(str(value), safe="")


def list_result(ccr="ACHS0875", env="BASE", items=None):
    lines = [
        f"OK|ccr_enc={encode_record_value(ccr)}|env_enc={encode_record_value(env)}"
    ]
    for item in items or []:
        lines.append(
            "ITEM|id={id}|name_enc={name}|window_enc={window}|plan_enc={plan}".format(
                id=item["id"],
                name=encode_record_value(item.get("name", "")),
                window=encode_record_value(item.get("window", "")),
                plan=encode_record_value(item.get("plan", "")),
            )
        )
    return "\n".join(lines)


class FakeToolClient(AtelierClient):
    def __init__(self, response="", exception=None):
        # Explicit, synthetic ABSENT evidence through the real discovery transport.
        self.__dict__.update(synthetic_client().__dict__)
        self.response = response
        self.exception = exception
        self.calls = []

    def _result(self):
        if self.exception:
            raise RuntimeError(self.exception)
        return self.response

    def ccr_commit(self, ccr_id, change_ids, upload_token):
        self.calls.append(("commit", ccr_id, change_ids, upload_token))
        return self._result()

    def ccr_reupload_itemset(self, itemset_id, ccr_id, upload_token, sanitize=True):
        self.calls.append(("reupload", itemset_id, ccr_id, upload_token, sanitize))
        return self._result()

    def ccr_list_itemsets(self, ccr_id, env=None, token=""):
        self.calls.append(("list", ccr_id, env, token))
        return self._result()

    def ccr_checkout(self, class_name, ccr_id, document_name=None):
        self.calls.append(("checkout", class_name or document_name, ccr_id))
        return self._result()

    def save_document(self, class_name, content_lines, verify=False, ccr_id=None):
        self.calls.append(("save", class_name, content_lines, verify, ccr_id))
        return True, "saved"

    def verify_document(self, class_name, content_lines):
        self.calls.append(("verify", class_name, content_lines))
        return True, "verified"


def recording_client(response=""):
    """Capture the ObjectScript body of every CCR operation without any gate or runner.

    Runner-embedded operations (checkout, undocheckout, commit, reupload, create,
    list, can_upload) are intercepted at the guarded-runner / runtime boundary so
    the generated body is asserted verbatim; the guard prefix is not included.
    """
    client = object.__new__(AtelierClient)
    client.namespace = "BASE-ACHS"
    client.env_role = "BASE"
    client.policy = REFERENCE_POLICY
    captured = []

    def execute_code(code):
        captured.append(code)
        return response

    client.execute_runtime_code = execute_code
    client._run_guarded_ccr_runner = lambda code, ccr_tracking, promotion=False: execute_code(code)
    client._execute_same_process_ccr_operation = lambda code, ccr_id: execute_code(code)
    return client, captured


def fake_server(client, include_ccr=True):
    server = object.__new__(IRISMCPServer)
    config = {
        "namespace": "BASE-ACHS",
        "upload_token": "configured-token",
    }
    if include_ccr:
        config["ccr_id"] = "ACHS0875"
    server.servers = {"fake": config}
    server.policy = REFERENCE_POLICY
    server.runtime_config = None
    server.load_configuration = lambda: None
    server.get_client = lambda server_id, namespace_override=None: client
    return server


class WriteGuardTests(HermeticTestCase):
    """Code-write authorization: production gate, route, catalog and same-process guard."""

    DOCUMENT = "Custom.ACHS.Tests.Sample.cls"

    def test_live_environment_is_blocked_before_the_guard(self):
        client = synthetic_client("APP", "LIVE")
        guard = script_guard(client, "CONTROLLED_ACTIVE")
        with patch.dict(os.environ, {"IRIS_ALLOW_PROD": "1"}, clear=False):
            allowed, reason = client.is_code_write_allowed(self.DOCUMENT, "ACHS0875")
        self.assertFalse(allowed)
        self.assertIn("LIVE", reason)
        guard.assert_not_called()

    def test_clinical_uat_direct_write_is_blocked_in_every_guard_state(self):
        for state in GUARD_STATES:
            with self.subTest(state=state):
                client = synthetic_client("UAT-ACHS", "UAT", host="10.244.20.6")
                guard = script_guard(client, state)
                allowed, reason = client.is_code_write_allowed(self.DOCUMENT, "ACHS0875")
                self.assertFalse(allowed)
                self.assertNotIn("UNKNOWN", reason)
                # UAT-ACHS has no cataloged package and no direct-write route: blocked statically.
                guard.assert_not_called()

    def test_base_requires_controlled_active_with_ccr_or_absent_cataloged(self):
        for state, tracking, expected in (
            ("CONTROLLED_ACTIVE", "ACHS0875", True),
            ("CONTROLLED_ACTIVE", None, False),
            ("SUSPENDED", "ACHS0875", False),
            ("ABSENT", None, True),
            ("ABSENT", "ACHS0875", True),
        ):
            with self.subTest(state=state, tracking=tracking):
                client = synthetic_client("BASE-ACHS", "BASE")
                script_guard(client, state)
                self.assertEqual(client.is_code_write_allowed(self.DOCUMENT, tracking)[0], expected)

    def test_direct_runner_description_documents_runtime_gate(self):
        server = object.__new__(IRISMCPServer)
        tools = {tool["name"]: tool for tool in server.handle_tools_list()["tools"]}
        description = tools["iris_execute_objectscript"]["description"]
        self.assertIn("puede persistir datos", description)
        self.assertIn("Gate RUNTIME", description)
        self.assertIn("LIVE/PROD", description)
        self.assertNotIn("UNKNOWN", description)
        for name in ("iris_ccr_list_itemsets", "iris_ccr_can_upload", "iris_ccr_download_load"):
            self.assertNotIn("UNKNOWN", tools[name]["description"])
        self.assertIn("INFORMATIVO", tools["iris_ccr_namespace_status"]["description"])
        self.assertIn("SQL", tools["iris_query_sql"]["description"])
        self.assertIn("SELECT", tools["iris_query_sql"]["description"])

    def test_uat_integration_exception_requires_absent_guard_and_catalog(self):
        vip = synthetic_client("INT", "UAT", host="10.244.20.6", path_prefix="/clacintuat")
        script_guard(vip, "ABSENT")
        allowed, reason = vip.is_code_write_allowed("SAP.Api.cls")
        self.assertTrue(allowed)
        self.assertIn("INT UAT VIP", reason)
        for state in ("CONTROLLED_ACTIVE", "SUSPENDED"):
            with self.subTest(state=state):
                client = synthetic_client("INT", "UAT", host="10.244.20.6", path_prefix="/clacintuat")
                script_guard(client, state)
                self.assertFalse(client.is_code_write_allowed("SAP.Api.cls", "ACHS0875")[0])
        uncataloged = synthetic_client("INT", "UAT", host="10.244.20.6", path_prefix="/clacintuat")
        guard = script_guard(uncataloged, "ABSENT")
        self.assertFalse(uncataloged.is_code_write_allowed("Custom.ACHS.Tests.Sample.cls")[0])
        guard.assert_not_called()

    def test_uat_integration_active_mirror_node_is_allowed_with_fresh_evidence(self):
        client = synthetic_client("INT", "UAT", host="10.244.20.4", path_prefix="/intuat01", ens_jobs=73)
        script_guard(client, "ABSENT")
        allowed, reason = client.is_code_write_allowed("SAP.Api.cls")
        self.assertTrue(allowed)
        self.assertIn("ACTIVE mirror node", reason)
        # Evidence is fresh on every call: a second call re-queries the node.
        calls_before = client._send_request.call_count
        client.is_code_write_allowed("SAP.Api.cls")
        self.assertGreater(client._send_request.call_count, calls_before)

    def test_uat_integration_inactive_or_unknown_node_is_blocked(self):
        for ens_jobs in (0, "0", None, "many", -1, 1.5):
            with self.subTest(ens_jobs=ens_jobs):
                client = synthetic_client("INT", "UAT", host="10.244.20.5", path_prefix="/intuat02", ens_jobs=ens_jobs)
                guard = script_guard(client, "ABSENT")
                if ens_jobs is None:
                    client._send_request.side_effect = self._deny_active_node(client)
                allowed, reason = client.is_code_write_allowed("SAP.Api.cls")
                self.assertFalse(allowed)
                self.assertIn("not executing", reason)
                guard.assert_not_called()

    @staticmethod
    def _deny_active_node(client):
        from isc_ai.ccr_discovery import ACTIVE_NODE_SQL
        normal = client._send_request.side_effect

        def respond(endpoint, method="GET", payload=None, **kwargs):
            if payload and payload.get("query") == ACTIVE_NODE_SQL:
                return 403, {"error": "denied"}
            return normal(endpoint, method=method, payload=payload, **kwargs)
        return respond

    def test_clinical_uat_node_and_other_prefixes_never_use_active_node_route(self):
        for ns, host, prefix in (("UAT-ACHS", "10.244.20.4", "/trakuat01"),
                                 ("INT", "10.244.20.4", "/trakuat01"),
                                 ("INT", "10.244.20.9", "/intuat01")):
            with self.subTest(ns=ns, host=host, prefix=prefix):
                client = synthetic_client(ns, "UAT", host=host, path_prefix=prefix, ens_jobs=99)
                guard = script_guard(client, "ABSENT")
                self.assertFalse(client.is_code_write_allowed("SAP.Api.cls")[0])
                guard.assert_not_called()

    HMF_GATEWAY_ORMO01 = "Custom.ACHS.HMF.Gateway.INT.HL7251OUT.Transform.DTL.SDA3ToHL7.HL7251.ORMO01.cls"

    def test_hmf_gateway_uat_active_node_admits_cataloged_document_under_absent(self):
        client = synthetic_client("HMFACHSGATEWAY_INT", "UAT", host="10.244.20.4",
                                  path_prefix="/trakuat01", ens_jobs=14)
        script_guard(client, "ABSENT")
        allowed, reason = client.is_code_write_allowed(self.HMF_GATEWAY_ORMO01)
        self.assertTrue(allowed, reason)
        self.assertIn("Gateway UAT active node", reason)
        self.assertIn("ACTIVE mirror node", reason)
        # Evidence is fresh on every call.
        calls_before = client._send_request.call_count
        client.is_code_write_allowed(self.HMF_GATEWAY_ORMO01)
        self.assertGreater(client._send_request.call_count, calls_before)

    def test_hmf_gateway_uat_backup_node_and_controlled_states_are_blocked(self):
        backup = synthetic_client("HMFACHSGATEWAY_INT", "UAT", host="10.244.20.5",
                                  path_prefix="/trakuat02", ens_jobs=0)
        guard = script_guard(backup, "ABSENT")
        allowed, reason = backup.is_code_write_allowed(self.HMF_GATEWAY_ORMO01)
        self.assertFalse(allowed)
        self.assertIn("not executing the production for 'Gateway UAT active node'", reason)
        guard.assert_not_called()
        for state in ("CONTROLLED_ACTIVE", "SUSPENDED"):
            with self.subTest(state=state):
                client = synthetic_client("HMFACHSGATEWAY_INT", "UAT", host="10.244.20.4",
                                          path_prefix="/trakuat01", ens_jobs=14)
                script_guard(client, state)
                self.assertFalse(client.is_code_write_allowed(self.HMF_GATEWAY_ORMO01, "ACHS0967")[0])

    def test_hmf_gateway_uat_catalog_is_document_exact(self):
        for document in ("Custom.ACHS.HMF.Gateway.INT.HL7251OUT.Transform.DTL.SDA3ToHL7.HL7251.RDEO11.cls",
                         "Custom.ACHS.HMF.Gateway.INT.HL2751OUT.Transform.DTL.SDA3ToHL7.HL7251.DFTP03.cls",
                         "SAP.Api.cls"):
            with self.subTest(document=document):
                client = synthetic_client("HMFACHSGATEWAY_INT", "UAT", host="10.244.20.4",
                                          path_prefix="/trakuat01", ens_jobs=14)
                guard = script_guard(client, "ABSENT")
                self.assertFalse(client.is_code_write_allowed(document)[0])
                guard.assert_not_called()
        # The INT web application prefix never routes the clinical gateway namespace.
        client = synthetic_client("HMFACHSGATEWAY_INT", "UAT", host="10.244.20.4",
                                  path_prefix="/intuat01", ens_jobs=14)
        guard = script_guard(client, "ABSENT")
        self.assertFalse(client.is_code_write_allowed(self.HMF_GATEWAY_ORMO01)[0])
        guard.assert_not_called()


class GeneratedObjectScriptTests(HermeticTestCase):
    def assert_token_is_not_written(self, code, token):
        write_lines = "\n".join(line for line in code.splitlines() if "Write " in line)
        self.assertNotIn(token, write_lines)

    def assert_safe_objectscript(self, code):
        self.assertTrue(code.isascii())
        self.assertEqual(code.count("{"), code.count("}"))
        self.assertNotRegex(code, r"(?im)^[ \t]*Quit[ \t]+\S")

    def test_checkout_sets_escaped_ccr_session_before_source_control_calls(self):
        requested_ccr = 'ACHS"0875'
        client, captured = recording_client(
            "VERIFIED_CHECKOUT|action=checkout|ccr_enc=ACHS%220875"
        )

        success, message = client.ccr_checkout("Custom.ACHS.Test", requested_ccr)

        code = captured[0]
        self.assertTrue(success)
        self.assertIn("asociacion CCR verificada", message)
        self.assertIn('Set requestedCCR = "ACHS""0875"', code)
        self.assertIn("Set %ccrSession = requestedCCR", code)
        self.assertIn("Set externalName = scInst.ExternalName(className)", code)
        self.assertLess(
            code.index("Set %ccrSession = requestedCCR"),
            code.index("scInst.AddToSourceControl"),
        )
        self.assertLess(
            code.index("Set %ccrSession = requestedCCR"),
            code.index("scInst.CheckOut"),
        )
        self.assertEqual(code.count("IsUncommitted(externalName)"), 2)
        self.assertIn("OpenUncommitted(externalName)", code)
        self.assertIn("Set actualCCR = change.CCR", code)
        self.assert_safe_objectscript(code)

    def test_checkout_rejects_non_ascii_ccr_before_generating_code(self):
        client, captured = recording_client()

        with self.assertRaisesRegex(ValueError, "non-empty ASCII"):
            client.ccr_checkout("Custom.ACHS.Test", "ACHS\u00f10875")
        with self.assertRaisesRegex(ValueError, "non-empty ASCII"):
            client.ccr_undocheckout("Custom.ACHS.Test", "ACHS\u00f10875")

        self.assertEqual(captured, [])

    def test_undocheckout_verifies_pending_row_ccr_before_reverting(self):
        client, captured = recording_client("VERIFIED_UNDOCHECKOUT|ccr_enc=ACHS0875")

        success, message = client.ccr_undocheckout("Custom.ACHS.Test", "ACHS0875")

        code = captured[0]
        self.assertTrue(success)
        self.assertIn("asociacion CCR verificada", message)
        self.assertIn('Set requestedCCR = "ACHS0875"', code)
        self.assertIn("IsUncommitted(externalName)", code)
        self.assertIn("OpenUncommitted(externalName)", code)
        self.assertIn("ERROR_NO_PENDING_CHANGE", code)
        self.assertIn("ERROR_CCR_MISMATCH", code)
        self.assertLess(code.index("ERROR_CCR_MISMATCH"), code.index("scInst.UndoCheckout(className)"))
        self.assertLess(code.index("Set %ccrSession = requestedCCR"), code.index("scInst.UndoCheckout(className)"))
        self.assert_safe_objectscript(code)

        for response, expected_error in (
            ("SUCCESS", "ERROR_UNDOCHECKOUT_RESPONSE"),
            ("VERIFIED_UNDOCHECKOUT|ccr_enc=ACHS9999", "ERROR_UNDOCHECKOUT_CCR_MISMATCH"),
            ("ERROR_CCR_MISMATCH|expected_ccr_enc=ACHS0875|actual_ccr_enc=|message=blank", "ERROR_CCR_MISMATCH"),
            ("ERROR_NO_PENDING_CHANGE|message=none", "ERROR_NO_PENDING_CHANGE"),
            ("", "ERROR_UNDOCHECKOUT_RESPONSE"),
        ):
            with self.subTest(response=response):
                client, _ = recording_client(response)
                success, message = client.ccr_undocheckout("Custom.ACHS.Test", "ACHS0875")
                self.assertFalse(success)
                self.assertIn(expected_error, message)

    def test_checkout_rejects_stale_writable_status_without_pending_row(self):
        stale = (
            "ERROR_STALE_CHECKOUT|external_name_enc=test.xml|"
            "message=Source file is writable but no uncommitted Change row exists"
        )
        client, captured = recording_client(stale)

        success, message = client.ccr_checkout("Custom.ACHS.Test", "ACHS0875")

        code = captured[0]
        self.assertFalse(success)
        self.assertEqual(message, stale)
        self.assertIn("ElseIf inSC && (isCheckedOut || editable) {", code)
        self.assertIn("ERROR_STALE_CHECKOUT", code)
        self.assertLess(
            code.index("ERROR_STALE_CHECKOUT"),
            code.index("Set sc = scInst.CheckOut"),
        )

    def test_checkout_rejects_blank_or_different_existing_ccr(self):
        for actual_ccr in ("", "ACHS9999"):
            with self.subTest(actual_ccr=actual_ccr):
                mismatch = (
                    "ERROR_CCR_MISMATCH|expected_ccr_enc=ACHS0875|"
                    f"actual_ccr_enc={actual_ccr}|message=CCR mismatch"
                )
                client, captured = recording_client(mismatch)

                success, message = client.ccr_checkout("Custom.ACHS.Test", "ACHS0875")

                self.assertFalse(success)
                self.assertEqual(message, mismatch)
                self.assertIn("If actualCCR'=requestedCCR {", captured[0])
                self.assertGreaterEqual(captured[0].count("ERROR_CCR_MISMATCH"), 2)

    def test_checkout_requires_exact_ccr_in_verified_success_record(self):
        responses = (
            ("SUCCESS", "ERROR_CHECKOUT_RESPONSE"),
            (
                "VERIFIED_CHECKOUT|action=checkout|ccr_enc=ACHS9999",
                "ERROR_CHECKOUT_CCR_MISMATCH",
            ),
            (
                "VERIFIED_CHECKOUT|action=unknown|ccr_enc=ACHS0875",
                "ERROR_CHECKOUT_RESPONSE",
            ),
        )
        for response, expected_error in responses:
            with self.subTest(response=response):
                client, _ = recording_client(response)

                success, message = client.ccr_checkout("Custom.ACHS.Test", "ACHS0875")

                self.assertFalse(success)
                self.assertIn(expected_error, message)

        client, _ = recording_client(
            "VERIFIED_CHECKOUT|action=idempotent|ccr_enc=ACHS0875"
        )
        success, message = client.ccr_checkout("Custom.ACHS.Test", "ACHS0875")
        self.assertTrue(success)
        self.assertIn("exactamente", message)

    def test_checkout_tolerates_core_echo_around_the_terminal_record(self):
        """AddToSourceControl echoes TrakCare noise (e.g. 'Assigned GUID: ...') that used to be
        mistaken for the terminal record, failing the FIRST save of any class outside SC."""
        noisy = (
            "\nAssigned GUID: Custom.ACHS.Test\t42\tD3570202-B5C4-11F1-B5F2-6045BDD2E731"
            "VERIFIED_CHECKOUT|action=added|ccr_enc=ACHS0875\n"
            "Some trailing core output\n"
        )
        # The noise may even be glued to the record on the same physical line in real captures;
        # the record must start a line to be recognised, so split it here as the runner does.
        noisy = noisy.replace("6045BDD2E731VERIFIED", "6045BDD2E731\nVERIFIED")
        client, _ = recording_client(noisy)
        success, message = client.ccr_checkout("Custom.ACHS.Test", "ACHS0875")
        self.assertTrue(success, message)

        client, _ = recording_client(
            "VERIFIED_CHECKOUT|action=added|ccr_enc=ACHS0875\nVERIFIED_CHECKOUT|action=checkout|ccr_enc=ACHS0875\n"
        )
        success, message = client.ccr_checkout("Custom.ACHS.Test", "ACHS0875")
        self.assertFalse(success)
        self.assertIn("Multiple terminal", message)

        client, _ = recording_client("Assigned GUID: only noise\n")
        success, message = client.ccr_checkout("Custom.ACHS.Test", "ACHS0875")
        self.assertFalse(success)
        self.assertIn("No terminal checkout result", message)

    def test_list_itemsets_encodes_free_text_records(self):
        client, captured = recording_client(list_result())

        client.ccr_list_itemsets("ACHS0875", env="BASE", token="test-token")

        code = captured[0]
        self.assertIn('Set ccr = "ACHS0875"', code)
        self.assertIn("WSList(ccr, env, token, .rs)", code)
        self.assertIn('"OK|ccr_enc="', code)
        self.assertIn('$ZConvert(rs.Get("Name"), "O", "URL")', code)
        self.assertIn('$ZConvert(rs.Get("ImplementationPlan"), "O", "URL")', code)
        self.assertNotIn(".Upload(", code)
        self.assertIn("<redacted>", code)
        self.assert_safe_objectscript(code)
        self.assert_token_is_not_written(code, "test-token")

    def test_commit_is_fail_closed_locked_and_bounded(self):
        client, captured = recording_client(
            "VERIFIED|action=commit|local_id=1080|local_name=item.xml|"
            "server_id=42|server_name=item.xml"
        )

        client.ccr_commit("ACHS0875", [100, 101], "test-token")

        code = captured[0]
        self.assertIn("Lock +^IRISMCPCCRCommit(ccr):30", code)
        self.assertIn("Lock -^IRISMCPCCRCommit(ccr)", code)
        self.assertGreater(code.index("Lock -^IRISMCPCCRCommit"), code.index("Catch captureEx"))
        self.assertGreater(code.index("WSList(ccr, env, token"), code.index("Lock -^IRISMCPCCRCommit"))
        self.assertIn("If $$$ISERR(commitSC)", code)
        self.assertIn("ERROR_COMMIT_STATUS", code)
        self.assertIn("Set absentCount = $ListLength($Get(absent))", code)
        self.assertIn("Set errorCount = $ListLength($Get(errors))", code)
        self.assertIn("ERROR_COMMIT_RESULTS", code)
        self.assertIn(
            "WHERE CCR = ? AND ID > ? ORDER BY ID",
            code,
        )
        self.assertNotIn("SELECT TOP 1 ID, Name, ItemSetFile", code)
        self.assertIn("If newCount'=1", code)
        self.assertIn("ERROR_ITEMSET_COUNT", code)
        self.assertIn("For listAttempt=1:1:3", code)
        # The native Commit already uploads the ItemSet: the commit runner must never
        # issue a second WSUpload (it produced duplicate add+edit commits in Perforce).
        self.assertEqual(code.count("WSUpload("), 0)
        self.assertNotIn("direct_retry", code)
        self.assertEqual(code.count("WSList(ccr, env, token"), 1)
        self.assertIn("PENDING_VERIFICATION|local_id=", code)
        self.assertIn("DO NOT AUTO-RETRY", code)
        self.assertNotIn(".Upload(", code)
        self.assert_safe_objectscript(code)
        self.assert_token_is_not_written(code, "test-token")

    def test_reupload_sanitization_preserves_and_rolls_back_originals(self):
        client, captured = recording_client(
            "VERIFIED|action=uploaded|local_id=1080|local_name=item.xml|"
            "server_id=42|server_name=item.xml|bytes_removed=2|rollback_copies=2"
        )

        client.ccr_reupload_itemset(1080, "ACHS0875", "test-token", sanitize=True)

        code = captured[0]
        self.assertIn("For listAttempt=1:1:3", code)
        self.assertIn("For verifyAttempt=1:1:3", code)
        self.assertEqual(code.count("WSUpload("), 1)
        self.assertEqual(code.count("WSList(ccr, env, token"), 2)
        self.assertIn("CopyFile(path, backupPath(fIdx), 0)", code)
        self.assertIn("CopyFile(tmpPath(fIdx), filePath(fIdx), 1)", code)
        self.assertIn("CopyFile(backupPath(fIdx), filePath(fIdx), 1)", code)
        self.assertNotIn("Delete(backupPath", code)
        self.assertNotIn("Rename(", code)
        self.assertIn("Set fileSC = inS.LinkToFile(path)", code)
        self.assertIn("Set fileSC = outS.LinkToFile(tmpPath(fIdx))", code)
        self.assertIn("Set fileSC = outS.Write(clean)", code)
        self.assertIn("Set fileSC = outS.%Save()", code)
        self.assertGreaterEqual(code.count("If $$$ISERR(fileSC)"), 4)
        self.assertIn("ERROR_SANITIZE_ROLLBACK", code)
        self.assertIn("ERROR_SANITIZE_ABORTED", code)
        self.assertIn("rollback_copies=", code)
        self.assertIn("PENDING_VERIFICATION|local_id=", code)
        self.assertIn("DO NOT AUTO-RETRY", code)
        self.assert_safe_objectscript(code)
        self.assert_token_is_not_written(code, "test-token")

    def test_client_methods_redact_tokens_from_normal_output(self):
        calls = (
            lambda client: client.ccr_commit("ACHS0875", [100], "test-token"),
            lambda client: client.ccr_reupload_itemset(
                1080, "ACHS0875", "test-token", sanitize=False
            ),
            lambda client: client.ccr_list_itemsets(
                "ACHS0875", env="BASE", token="test-token"
            ),
        )
        for call in calls:
            with self.subTest(call=call):
                client, _ = recording_client("ERROR: server echoed test-token")
                output = call(client)
                self.assertNotIn("test-token", output)
                self.assertIn("<redacted>", output)

    def test_client_methods_redact_url_encoded_tokens(self):
        token = "test|token"
        encoded_token = encode_record_value(token)
        calls = (
            lambda client: client.ccr_commit("ACHS0875", [100], token),
            lambda client: client.ccr_reupload_itemset(
                1080, "ACHS0875", token, sanitize=False
            ),
            lambda client: client.ccr_list_itemsets("ACHS0875", env="BASE", token=token),
        )
        for call in calls:
            with self.subTest(call=call):
                client, _ = recording_client(f"ERROR: server echoed {encoded_token}")
                output = call(client)
                self.assertNotIn(token, output)
                self.assertNotIn(encoded_token, output)
                self.assertIn("<redacted>", output)

    def test_client_methods_redact_tokens_from_exceptions(self):
        calls = (
            lambda client: client.ccr_commit("ACHS0875", [100], "test-token"),
            lambda client: client.ccr_reupload_itemset(
                1080, "ACHS0875", "test-token", sanitize=False
            ),
            lambda client: client.ccr_list_itemsets(
                "ACHS0875", env="BASE", token="test-token"
            ),
        )
        for call in calls:
            with self.subTest(call=call):
                client, _ = recording_client()
                def _raise(*_args, **_kwargs):
                    raise RuntimeError("compiler echoed test-token")
                client.execute_runtime_code = _raise
                client._run_guarded_ccr_runner = _raise
                client._execute_same_process_ccr_operation = _raise
                with self.assertRaises(RuntimeError) as raised:
                    call(client)
                self.assertNotIn("test-token", str(raised.exception))
                self.assertIn("<redacted>", str(raised.exception))


class ToolContractTests(HermeticTestCase):
    def call(self, server, name, **arguments):
        arguments.setdefault("server_id", "fake")
        arguments.setdefault("namespace", "BASE-ACHS")
        return server.handle_tool_call(name, arguments)

    def test_list_itemsets_schema_exposes_ccr_id(self):
        server = object.__new__(IRISMCPServer)

        tools = server.handle_tools_list()["tools"]

        tool = next(tool for tool in tools if tool["name"] == "iris_ccr_list_itemsets")
        self.assertIn("ccr_id", tool["inputSchema"]["properties"])

    def test_checkout_handler_marks_stale_result_as_error(self):
        stale = (
            "ERROR_STALE_CHECKOUT|external_name_enc=test.xml|"
            "message=Source file is writable but no uncommitted Change row exists"
        )
        client = FakeToolClient((False, stale))

        result = self.call(
            fake_server(client),
            "iris_ccr_checkout",
            class_name="Custom.Test",
            ccr_id="ACHS0875",
        )

        self.assertTrue(result.get("isError"))
        self.assertIn("ERROR_STALE_CHECKOUT", result["content"][0]["text"])
        self.assertNotIn("**\u00c9xito**", result["content"][0]["text"])
        self.assertEqual(client.calls, [("checkout", "Custom.Test.cls", "ACHS0875")])

    def test_save_handler_stops_when_explicit_ccr_checkout_is_rejected(self):
        mismatch = "ERROR_CCR_MISMATCH|message=Existing row belongs to ACHS9999"
        client = FakeToolClient((False, mismatch))

        result = self.call(
            fake_server(client),
            "iris_save_class",
            class_name="Custom.ACHS.Test",
            content="Class Custom.ACHS.Test {}",
            ccr_id="ACHS0875",
            auto_checkout=True,
        )

        self.assertTrue(result.get("isError"))
        self.assertIn("no se guardo", result["content"][0]["text"])
        self.assertIn("ERROR_CCR_MISMATCH", result["content"][0]["text"])
        self.assertEqual(client.calls, [("checkout", "Custom.ACHS.Test.cls", "ACHS0875")])

    def test_list_itemsets_rejects_missing_ccr_before_client_creation(self):
        server = fake_server(FakeToolClient(), include_ccr=False)
        client_requested = []
        server.get_client = lambda *args, **kwargs: client_requested.append(True)

        with patch.dict(os.environ, {"IRIS_CCR_ID": ""}, clear=False):
            result = self.call(server, "iris_ccr_list_itemsets")

        self.assertTrue(result.get("isError"))
        self.assertIn("ccr_id", result["content"][0]["text"])
        self.assertEqual(client_requested, [])

    def test_list_itemsets_handler_uses_configured_ccr(self):
        client = FakeToolClient(list_result())
        server = fake_server(client)

        result = self.call(server, "iris_ccr_list_itemsets", env="BASE")

        self.assertFalse(result.get("isError", False))
        self.assertEqual(client.calls, [("list", "ACHS0875", "BASE", "configured-token")])

    def test_handler_redacts_explicit_tokens_from_debug_log(self):
        client = FakeToolClient(list_result())
        server = fake_server(client)

        with patch("isc_ai.server.log_debug") as debug_log:
            self.call(
                server,
                "iris_ccr_list_itemsets",
                ccr_id="ACHS0875",
                token="secret-token",
            )

        messages = "\n".join(call.args[0] for call in debug_log.call_args_list)
        self.assertNotIn("secret-token", messages)
        self.assertIn("<redacted>", messages)

    def test_commit_handler_renders_only_valid_verified_record(self):
        client = FakeToolClient(
            "VERIFIED|action=direct_retry|local_id=1080|local_name=item.xml|"
            "server_id=42|server_name=item.xml"
        )
        result = self.call(
            fake_server(client),
            "iris_ccr_commit",
            change_ids=[100],
        )

        self.assertFalse(result.get("isError", False))
        self.assertIn("**VERIFIED**", result["content"][0]["text"])
        self.assertIn("`42` (`item.xml`)", result["content"][0]["text"])

    def test_commit_handler_rejects_unverified_legacy_success(self):
        result = self.call(
            fake_server(FakeToolClient("SUCCESS")),
            "iris_ccr_commit",
            change_ids=[100],
        )

        self.assertTrue(result.get("isError"))
        self.assertNotIn("**VERIFIED**", result["content"][0]["text"])

    def test_verified_record_validation_rejects_malformed_records(self):
        malformed = (
            "VERIFIED|action=uploaded|local_id=1080|local_name=item.xml|server_id=42|server_name=item.xml",
            "VERIFIED|action=commit|local_id=1080|local_name=item.xml|server_id=abc|server_name=item.xml",
            "VERIFIED|action=commit|local_id=1080|local_name=item.xml|server_id=|server_name=item.xml",
            "VERIFIED|action=commit|local_id=1080|local_name=|server_id=42|server_name=",
            "VERIFIED|action=commit|local_id=1080|local_name=item-a.xml|server_id=42|server_name=item-b.xml",
            "VERIFIED|action=commit|local_id=1080|local_name=item.xml|server_id=42|server_name=item.xml|junk",
            "VERIFIED|action=commit|action=direct_retry|local_id=1080|local_name=item.xml|server_id=42|server_name=item.xml",
            "VERIFIED|action=commit|local_id=not-numeric|local_name=item.xml|server_id=42|server_name=item.xml",
        )
        for response in malformed:
            with self.subTest(response=response):
                result = self.call(
                    fake_server(FakeToolClient(response)),
                    "iris_ccr_commit",
                    change_ids=[100],
                )
                self.assertTrue(result.get("isError"))
                self.assertNotIn("**VERIFIED**", result["content"][0]["text"])

    def test_error_anywhere_rejects_later_verified_record(self):
        response = (
            "ERROR_COMMIT_STATUS|message=partial failure\n"
            "VERIFIED|action=commit|local_id=1080|local_name=item.xml|"
            "server_id=42|server_name=item.xml"
        )

        result = self.call(
            fake_server(FakeToolClient(response)),
            "iris_ccr_commit",
            change_ids=[100],
        )

        self.assertTrue(result.get("isError"))
        self.assertIn("ERROR_COMMIT_STATUS", result["content"][0]["text"])
        self.assertNotIn("**VERIFIED**", result["content"][0]["text"])

    def test_pending_verification_is_not_success_and_forbids_auto_retry(self):
        response = (
            "PENDING_VERIFICATION|local_id=1080|local_name=item.xml|"
            "message=WSUpload returned OK; DO NOT AUTO-RETRY because duplicate upload is possible"
        )

        result = self.call(
            fake_server(FakeToolClient(response)),
            "iris_ccr_commit",
            change_ids=[100],
        )

        self.assertTrue(result.get("isError"))
        self.assertIn("PENDING_VERIFICATION", result["content"][0]["text"])
        self.assertIn("NO REINTENTAR", result["content"][0]["text"])
        self.assertIn("`1080` (`item.xml`)", result["content"][0]["text"])
        self.assertNotIn("**VERIFIED**", result["content"][0]["text"])

    def test_reupload_handler_accepts_allowed_verified_action(self):
        response = (
            "SUCCESS\n"
            "VERIFIED|action=already_present|local_id=1080|local_name=item.xml|"
            "server_id=42|server_name=item.xml|bytes_removed=0|rollback_copies=0"
        )

        result = self.call(
            fake_server(FakeToolClient(response)),
            "iris_ccr_reupload_itemset",
            itemset_id=1080,
        )

        self.assertFalse(result.get("isError", False))
        self.assertIn("**VERIFIED**", result["content"][0]["text"])

    def test_reupload_handler_rejects_error_after_verified_marker(self):
        response = (
            "VERIFIED|action=uploaded|local_id=1080|local_name=item.xml|"
            "server_id=42|server_name=item.xml|bytes_removed=0|rollback_copies=1\n"
            "ERROR_VERIFICATION|local_id=1080|message=late failure"
        )

        result = self.call(
            fake_server(FakeToolClient(response)),
            "iris_ccr_reupload_itemset",
            itemset_id=1080,
        )

        self.assertTrue(result.get("isError"))
        self.assertIn("ERROR_VERIFICATION", result["content"][0]["text"])
        self.assertNotIn("**VERIFIED**", result["content"][0]["text"])

    def test_list_itemsets_returns_error_for_error_missing_ok_or_malformed_output(self):
        valid_header = list_result()
        malformed = (
            "ERROR: denied",
            "ITEM|id=42|name_enc=item.xml|window_enc=Y|plan_enc=plan",
            "OK|ccr=ACHS0875|env=BASE",
            valid_header + "\nITEM|id=abc|name_enc=item.xml|window_enc=Y|plan_enc=plan",
            valid_header + "\nITEM|id=42|name_enc=%ZZ|window_enc=Y|plan_enc=plan",
            valid_header + "\nUNKNOWN|value=x",
        )
        for response in malformed:
            with self.subTest(response=response):
                result = self.call(
                    fake_server(FakeToolClient(response)),
                    "iris_ccr_list_itemsets",
                    env="BASE",
                )
                self.assertTrue(result.get("isError"))

    def test_list_itemsets_decodes_fields_without_parser_or_markdown_injection(self):
        response = list_result(
            items=[
                {
                    "id": "42",
                    "name": "item|name.xml",
                    "window": "Y",
                    "plan": "first line\nITEM|id=999|name=evil",
                }
            ]
        )

        result = self.call(
            fake_server(FakeToolClient(response)),
            "iris_ccr_list_itemsets",
            env="BASE",
        )

        text = result["content"][0]["text"]
        self.assertFalse(result.get("isError", False))
        self.assertIn("item\\|name.xml", text)
        self.assertIn("ITEM\\|id=999\\|name=evil", text)
        self.assertNotIn("\n| 999 |", text)

    def test_handlers_redact_configured_tokens_from_output_and_exceptions(self):
        cases = (
            (
                "iris_ccr_commit",
                {"change_ids": [100]},
                "ERROR_COMMIT_STATUS|message=configured-token",
            ),
            (
                "iris_ccr_reupload_itemset",
                {"itemset_id": 1080},
                "ERROR_DIRECT_UPLOAD|message=configured-token",
            ),
            (
                "iris_ccr_list_itemsets",
                {"env": "BASE"},
                "ERROR: configured-token",
            ),
        )
        for name, arguments, response in cases:
            with self.subTest(name=name, source="output"):
                result = self.call(fake_server(FakeToolClient(response)), name, **arguments)
                self.assertNotIn("configured-token", result["content"][0]["text"])
                self.assertIn("<redacted>", result["content"][0]["text"])
            with self.subTest(name=name, source="exception"):
                result = self.call(
                    fake_server(FakeToolClient(exception="configured-token failed")),
                    name,
                    **arguments,
                )
                self.assertTrue(result.get("isError"))
                self.assertNotIn("configured-token", result["content"][0]["text"])
                self.assertIn("<redacted>", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()


class SqlReadOnlyClassifierTests(unittest.TestCase):
    def test_keywords_inside_string_literals_are_not_mutations(self):
        from isc_ai.server import _is_sql_read_only
        for sql in (
            "SELECT Name FROM %Dictionary.CompiledMethod WHERE Name LIKE '%Update%'",
            "SELECT * FROM Ens_Util.Log WHERE Text = 'it''s a DELETE inside'",
            "SELECT 'CREATE' AS word",
        ):
            with self.subTest(sql=sql):
                self.assertTrue(_is_sql_read_only(sql))

    def test_real_mutations_are_still_detected(self):
        from isc_ai.server import _is_sql_read_only
        for sql in (
            "UPDATE Ens_Config.Item SET Enabled = 0 WHERE Name = 'x'",
            "DELETE FROM Ens_Util.Log WHERE ID = 1",
            "SELECT 1; DROP TABLE t",
            "CALL Some_Proc('x')",
        ):
            with self.subTest(sql=sql):
                self.assertFalse(_is_sql_read_only(sql))


class QueueResendTests(HermeticTestCase):
    """resend_message must use the real Ens.MessageHeader API and surface the new header id."""

    def _server_and_client(self, output):
        client = synthetic_client("INT", "UAT", engine=0, configured=0,
                                  host="10.244.20.6", path_prefix="/clacintuat")
        client.execute_runtime_code = unittest.mock.Mock(return_value=output)
        return fake_server(client, include_ccr=False), client

    def test_resend_uses_resend_duplicated_message_and_reports_new_id(self):
        server, client = self._server_and_client("OK|newId=380569\n")
        with patch.dict(os.environ, {"IRIS_ALLOW_PROD": ""}, clear=False):
            result = server.handle_tool_call("iris_ensemble_queue_control", {
                "server_id": "fake", "action": "resend_message",
                "message_id": "380124", "new_target": "HL7 Rule",
            })
        self.assertFalse(result.get("isError"))
        text = result["content"][0]["text"]
        self.assertIn("380569", text)
        self.assertIn("HL7 Rule", text)
        code = client.execute_runtime_code.call_args.args[0]
        self.assertIn("ResendDuplicatedMessage(380124, .newId, \"HL7 Rule\")", code)
        self.assertNotIn(").Resend(", code)
        self.assertNotIn("$$$", code)

    def test_resend_without_target_passes_empty_string(self):
        server, client = self._server_and_client("OK|newId=1\n")
        with patch.dict(os.environ, {"IRIS_ALLOW_PROD": ""}, clear=False):
            server.handle_tool_call("iris_ensemble_queue_control", {
                "server_id": "fake", "action": "resend_message", "message_id": 5,
            })
        self.assertIn('ResendDuplicatedMessage(5, .newId, "")', client.execute_runtime_code.call_args.args[0])

    def test_resend_error_or_unexpected_output_is_reported_as_error(self):
        for output in ("ERR|ERROR #5001: header not found", "", "garbage", "ERROR: <METHOD DOES NOT EXIST>"):
            with self.subTest(output=output):
                server, _ = self._server_and_client(output)
                with patch.dict(os.environ, {"IRIS_ALLOW_PROD": ""}, clear=False):
                    result = server.handle_tool_call("iris_ensemble_queue_control", {
                        "server_id": "fake", "action": "resend_message", "message_id": "7",
                    })
                self.assertTrue(result.get("isError"))
