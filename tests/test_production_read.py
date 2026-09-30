"""production_read: explicit per-entry READ opt-in on production targets.

The opt-in opens only the READ gate. Runners, RUNTIME, CCR and CODE WRITE gates
evaluate the production gate independently and must keep blocking production.
"""

from unittest.mock import Mock, patch

from tests.hermetic_test_support import (
    AtelierClient, IRISMCPServer, HermeticTestCase, REF, REFERENCE_POLICY,
    envelope, synthetic_client, synthetic_server,
)
from isc_ai.ccr_discovery import LocalPolicyError


CATALOGED = "Custom.ACHS.Tests.Sample.cls"
RUNNER = "User.McpTmpRun0123456789ab"


def production_client(production_read=True, namespace=REF["base_namespace"], role="BASE",
                      production=True, host="example.invalid", path_prefix=""):
    """Hermetic client built through the real constructor, with no transport."""
    opener = Mock()
    opener.open.side_effect = AssertionError("Unscripted HTTP request")
    with patch.object(AtelierClient, "_create_opener", return_value=opener):
        client = AtelierClient(host, 80, path_prefix, namespace, "synthetic", "synthetic",
                               is_production=production, env_role=role, policy=REFERENCE_POLICY,
                               production_read=production_read)
    client._send_request = Mock(side_effect=AssertionError("Unscripted HTTP request"))
    client._probe_same_process_ccr_state = Mock(side_effect=AssertionError("guard must not run on production"))
    return client


def production_targets():
    """Every production marker the policy recognises, each with production_read=True."""
    return (
        ("is_production", lambda: production_client()),
        ("LIVE role", lambda: production_client(production=False, role="LIVE")),
        ("PROD namespace", lambda: production_client(production=False, namespace="PROD-ACHS", role="BASE")),
        ("LIVE namespace + role", lambda: production_client(production=False, namespace="LIVE-ACHS", role="LIVE")),
    )


class ProductionReadGateTests(HermeticTestCase):
    def test_production_without_opt_in_denies_read(self):
        for client in (synthetic_client(production=True), production_client(production_read=False)):
            with self.subTest(client=client):
                allowed, reason = client.is_read_allowed()
                self.assertFalse(allowed)
                self.assertIn("production", reason)
                with self.assertRaisesRegex(LocalPolicyError, "production"):
                    client.run_query("SELECT 1")
                client._send_request.assert_not_called()

    def test_opt_in_defaults_to_false_and_only_identity_true_enables_it(self):
        self.assertFalse(synthetic_client(production=True).production_read)
        for value in ("true", "false", 1, "1", [True], None):
            with self.subTest(value=value):
                client = production_client(production_read=value)
                self.assertFalse(client.production_read)
                self.assertFalse(client.is_read_allowed()[0])

    def test_production_with_opt_in_allows_read(self):
        for label, factory in production_targets():
            with self.subTest(target=label):
                client = factory()
                allowed, reason = client.is_read_allowed()
                self.assertTrue(allowed)
                self.assertIn("production_read", reason)
                client._send_request.side_effect = None
                client._send_request.return_value = envelope([{"one": 1}])
                self.assertEqual(client.run_query("SELECT 1 AS one"), [{"one": 1}])
                client._probe_same_process_ccr_state.assert_not_called()

    def test_opt_in_keeps_the_sql_policy_and_target_validation(self):
        client = production_client()
        with self.assertRaisesRegex(LocalPolicyError, "SQL policy"):
            client.run_query("UPDATE X SET Y = 1")
        client.namespace = "OTHER"
        with self.assertRaisesRegex(LocalPolicyError, "Target changed"):
            client.is_read_allowed()
        client._send_request.assert_not_called()

    def test_opt_in_does_not_open_runtime_runner_ccr_or_code_write(self):
        for label, factory in production_targets():
            with self.subTest(target=label):
                client = factory()
                self.assertTrue(client.is_read_allowed()[0])
                self.assertFalse(client.is_runtime_operation_allowed()[0])
                self.assertFalse(client.is_code_write_route_allowed()[0])
                self.assertTrue(client.is_code_write_route_allowed(promotion=True)[0])
                self.assertFalse(client.is_code_write_allowed(CATALOGED, "ACHS0875")[0])
                blocked = (
                    lambda: client.execute_runtime_code("Write 1"),
                    lambda: client._execute_direct_objectscript("Write 1"),
                    lambda: client._require_ephemeral_runner(RUNNER),
                    lambda: client._run_guarded_ccr_runner("Write 1", "ACHS0875"),
                    lambda: client.save_document(CATALOGED, ["Class X {}"], ccr_id="ACHS0875"),
                    lambda: client.compile_document(CATALOGED, ccr_id="ACHS0875"),
                    lambda: client.delete_document(CATALOGED),
                    lambda: client.run_write_sql("UPDATE X SET Y = 1"),
                    lambda: client.dump_global("^X"),
                    lambda: client.get_ccr_status(class_name="Custom.ACHS.Tests.Sample"),
                    lambda: client.ccr_can_upload("ACHS0875"),
                    lambda: client.ccr_list_itemsets("ACHS0875"),
                )
                for operation in blocked:
                    with self.assertRaises((LocalPolicyError, RuntimeError)):
                        operation()
                client._send_request.assert_not_called()
                client._probe_same_process_ccr_state.assert_not_called()

    def test_opt_in_does_not_open_canonical_ccr_mutation(self):
        client = production_client(host=REF["base_host"], path_prefix=REF["base_prefix"])
        self.assertTrue(client.is_read_allowed()[0])
        with self.assertRaisesRegex(LocalPolicyError, "production"):
            client._require_canonical_base_ccr_mutation()
        with self.assertRaises(LocalPolicyError):
            client.ccr_mutate_class("Custom.ACHS.X", "ACHS0875", "ABSENT", "0" * 64, "")
        client._send_request.assert_not_called()


class ProductionReadConfigTests(HermeticTestCase):
    BASE = {"host": "h", "port": 80, "path_prefix": "", "username": "u", "password": "p",
            "namespace": "LIVE-ACHS", "env_role": "LIVE", "is_production": True}

    def test_validator_accepts_bool_and_rejects_non_bool(self):
        IRISMCPServer._validate_connection_config(dict(self.BASE))
        IRISMCPServer._validate_connection_config(dict(self.BASE, production_read=True))
        IRISMCPServer._validate_connection_config(dict(self.BASE, production_read=False))
        for bad in ("true", "false", 1, 0, None, "{env:IRIS_PRODUCTION_READ}", [True]):
            with self.subTest(bad=bad), self.assertRaises(LocalPolicyError):
                IRISMCPServer._validate_connection_config(dict(self.BASE, production_read=bad))

    def test_get_client_forwards_production_read_and_defaults_to_false(self):
        server = synthetic_server(synthetic_client())
        server.servers = {"live": dict(self.BASE, production_read=True), "live-default": dict(self.BASE)}
        with patch.object(AtelierClient, "_create_opener", return_value=Mock()):
            opted_in = server._get_client_direct("live")
            default = server._get_client_direct("live-default")
        self.assertIs(opted_in.production_read, True)
        self.assertIs(default.production_read, False)
        self.assertTrue(opted_in.is_read_allowed()[0])
        self.assertFalse(opted_in.is_runtime_operation_allowed()[0])
        self.assertFalse(default.is_read_allowed()[0])

    def test_no_environment_variable_enables_production_read(self):
        env = {"IRIS_PRODUCTION_READ": "true", "PRODUCTION_READ": "1", "IRIS_ALLOW_PROD": "1"}
        server = synthetic_server(synthetic_client())
        server.servers = {"live": dict(self.BASE)}
        with patch.dict("os.environ", env), \
                patch.object(AtelierClient, "_create_opener", return_value=Mock()):
            client = server._get_client_direct("live")
        self.assertIs(client.production_read, False)
        self.assertFalse(client.is_read_allowed()[0])

    def test_alias_sibling_production_read_is_ignored(self):
        server = synthetic_server(synthetic_client())
        server.servers = {"live": dict(self.BASE),
                          "live-alias": {"canonical_server_id": "live", "production_read": True}}
        with patch.object(AtelierClient, "_create_opener", return_value=Mock()):
            client = server._get_client_direct("live-alias")
        self.assertIs(client.production_read, False)
        self.assertFalse(client.is_read_allowed()[0])


class ProductionReadToolDispatchTests(HermeticTestCase):
    SERVERS = {"fake": {"namespace": "LIVE-ACHS", "env_role": "LIVE", "is_production": True,
                        "production_read": True, "ccr_id": "ACHS0875"}}

    def call(self, client, name, arguments):
        server = synthetic_server(client, servers=self.SERVERS)
        args = {"server_id": "fake", "namespace": client.namespace}
        args.update(arguments)
        return server.handle_tool_call(name, args)

    def test_rest_and_sql_read_tools_are_served(self):
        client = production_client(production=True, namespace="LIVE-ACHS", role="LIVE")
        client._send_request.side_effect = None
        client._send_request.return_value = (200, {"result": {"content": ["Class X {}"]}})
        result = self.call(client, "iris_get_class", {"class_name": "Custom.Test"})
        self.assertFalse(result.get("isError", False), result)
        self.assertIn("Class X", result["content"][0]["text"])

        client._send_request.return_value = envelope([{"Name": "Custom.Test", "Abstract": 0}])
        result = self.call(client, "iris_query_sql", {"query": "SELECT Name FROM %Dictionary.ClassDefinition"})
        self.assertFalse(result.get("isError", False), result)
        result = self.call(client, "iris_query_sql", {"query": "DELETE FROM X"})
        self.assertTrue(result.get("isError"))
        client._probe_same_process_ccr_state.assert_not_called()

    def test_runner_backed_read_tools_and_mutations_stay_blocked(self):
        cases = (
            ("iris_dump_global", {"global_name": "^X"}),
            ("iris_ccr_status", {"class_name": "Custom.ACHS.Tests.Sample"}),
            ("iris_ccr_can_upload", {"ccr_id": "ACHS0875"}),
            ("iris_ccr_list_itemsets", {"ccr_id": "ACHS0875"}),
            ("iris_ensemble_production_control", {"action": "status"}),
            ("iris_ensemble_production_control", {"action": "stop"}),
            ("iris_ensemble_component_control", {"action": "disable", "component_name": "X"}),
            ("iris_ensemble_default_settings", {"action": "set", "setting_name": "S", "setting_value": "V"}),
            ("iris_ensemble_queue_control", {"action": "list_queues"}),
            ("iris_ensemble_queue_control", {"action": "abort_message", "message_id": 1}),
            ("iris_execute_objectscript", {"code": "Write 1"}),
            ("iris_save_class", {"class_name": "Custom.ACHS.Tests.Sample", "content": "Class X {}"}),
            ("iris_compile_class", {"class_name": "Custom.ACHS.Tests.Sample"}),
            ("iris_ccr_checkout", {"class_name": "Custom.ACHS.Tests.Sample", "ccr_id": "ACHS0875"}),
            ("iris_unlock_questionnaire", {"win_code": "X"}),
        )
        for name, arguments in cases:
            with self.subTest(tool=name, arguments=arguments):
                client = production_client(production=True, namespace="LIVE-ACHS", role="LIVE")
                result = self.call(client, name, arguments)
                self.assertTrue(result.get("isError"), (name, result))
                client._send_request.assert_not_called()
                client._probe_same_process_ccr_state.assert_not_called()
