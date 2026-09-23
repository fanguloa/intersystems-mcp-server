import base64
import hashlib
import json
from pathlib import Path
import re
import io
import unittest
from unittest.mock import Mock, patch

from isc_ai.ccr_discovery import LocalPolicyError
from tests.hermetic_test_support import (
    AtelierClient, HermeticTestCase, envelope, synthetic_client, synthetic_server,
    REFERENCE_POLICY, UNCONFIGURED_POLICY, policy_with,
)
from isc_ai.cli import _parse_cli_args, main
from isc_ai.server import (
    IRISMCPServer, _CCR_MUTATION_CLASS, _CCR_MUTATION_TEMPLATE,
    _canonical_crlf_bytes, _load_ccr_mutation_resource,
)


FIXED_SQL = "SELECT MCP.Mutate(?,?,?,?,?) AS result"
CLASS_NAME = "Custom.ACHS.Tests.Sample"
CCR_ID = "ACHS0875"
SOURCE = "Class Custom.ACHS.Tests.Sample Extends %RegisteredObject\r\n{\r\n/// Español 日本語\r\n}\r\n".encode("utf-8")
CANDIDATE_SHA = hashlib.sha256(SOURCE).hexdigest()
RESOURCE_SOURCE, RESOURCE_SHA = _load_ccr_mutation_resource(REFERENCE_POLICY)
# Digest of the facility as deployed before templating; rendering the reference
# policy must reproduce it byte-for-byte so installed sites report UNCHANGED.
DEPLOYED_FACILITY_SHA = "81eac1c6755ae870f009e316eec147059ba9a24d439855bff6f01b9dc4282618"


def canonical_client():
    return synthetic_client(
        namespace="BASE-ACHS", role="BASE", host="10.244.10.4",
        path_prefix="/trakbase01",
    )


def success_record(**overrides):
    record = {
        "protocol": "ACHS_CCR_MUTATION_V1",
        "ok": 1,
        "state": "VERIFIED",
        "code": "",
        "message": "",
        "namespace": "BASE-ACHS",
        "class": CLASS_NAME,
        "ccr": CCR_ID,
        "candidate_sha256": CANDIDATE_SHA,
        "binding": "TC.ChangeControl.CCR",
        "source_binding": "%Studio.SourceControl.ISC",
        "suspend_level": 0,
        "controlled_active": 1,
        "suspension_scope": "same-request-process",
        "round_trip": 1,
        "tracking": 1,
        "compile": 1,
        "change_id": 42,
        "checkout_created": 0,
        "created_tracking": 1,
        "manual_action_required": 0,
    }
    record.update(overrides)
    return record


def initializer_record(state="INSTALLED", **overrides):
    changed = state in {"INSTALLED", "UPGRADED"}
    upgrade = state == "UPGRADED"
    record = {
        "protocol": "ACHS_CCR_INITIALIZER_V1",
        "ok": 0 if state in {"DIFFERENT", "SOURCE_MATCH_NOT_COMPILED"} else 1,
        "state": state,
        "code": "",
        "message": "",
        "namespace": "BASE-ACHS",
        "class": _CCR_MUTATION_CLASS,
        "ccr": CCR_ID,
        "candidate_sha256": RESOURCE_SHA,
        "upgrade": int(upgrade),
        "expected_current_sha256": "a" * 64 if upgrade else "",
        "current_sha256": RESOURCE_SHA if state != "DIFFERENT" else "b" * 64,
        "binding": "TC.ChangeControl.CCR",
        "source_binding": "%Studio.SourceControl.ISC",
        "suspend_level": 0,
        "controlled_active": 1,
        "suspension_scope": "same-request-process",
        "round_trip": int(changed),
        "tracking": int(changed),
        "compile": int(changed),
        "change_id": 42 if changed else 0,
        "checkout_created": int(upgrade),
        "created_tracking": int(changed),
        "mutation_started": int(changed),
        "manual_action_required": 0,
        "checkout_may_exist": 0,
        "source_may_exist": 0,
        "tracking_may_exist": 0,
    }
    record.update(overrides)
    return record


class TransportTests(HermeticTestCase):
    def test_client_makes_exactly_one_fixed_parameterized_query(self):
        client = canonical_client()
        client._send_request.side_effect = None
        client._send_request.return_value = envelope([
            {"result": json.dumps(success_record(), separators=(",", ":"))}
        ])
        encoded = base64.b64encode(SOURCE).decode("ascii")

        result = client.ccr_mutate_class(CLASS_NAME, CCR_ID, "ABSENT", CANDIDATE_SHA, encoded)

        self.assertEqual(result, success_record())
        client._send_request.assert_called_once_with(
            "/action/query",
            method="POST",
            payload={
                "query": FIXED_SQL,
                "parameters": [CLASS_NAME, CCR_ID, "ABSENT", CANDIDATE_SHA, encoded],
            },
            timeout=300,
        )

    def test_client_rejects_any_missing_or_false_success_evidence(self):
        bad_records = (
            success_record(protocol="OTHER"),
            success_record(binding="TC.ChangeControl.Other"),
            success_record(source_binding="TC.ChangeControl.CCR"),
            success_record(suspend_level=1),
            success_record(suspension_scope="cached"),
            success_record(controlled_active=0),
            success_record(round_trip=0),
            success_record(tracking=0),
            success_record(compile=0),
            success_record(change_id=""),
            success_record(checkout_created=2),
            success_record(created_tracking=0),
            success_record(manual_action_required=1),
            {**success_record(), "unexpected": 1},
        )
        for record in bad_records:
            with self.subTest(record=record):
                client = canonical_client()
                client._send_request.side_effect = None
                client._send_request.return_value = envelope([{"result": json.dumps(record)}])
                with self.assertRaisesRegex(LocalPolicyError, "do not retry automatically"):
                    client.ccr_mutate_class(
                        CLASS_NAME, CCR_ID, "ABSENT", CANDIDATE_SHA,
                        base64.b64encode(SOURCE).decode("ascii"),
                    )
                self.assertEqual(client._send_request.call_count, 1)

    def test_noncanonical_targets_block_before_transport(self):
        clients = (
            synthetic_client(namespace="BASE-ACHS", role="BASE"),
            synthetic_client(namespace="UAT-ACHS", role="UAT", host="10.244.20.6"),
            synthetic_client(namespace="BASE-ACHS", role="BASE", host="10.244.10.4",
                             path_prefix="/trakbase01", production=True),
        )
        for client in clients:
            with self.subTest(target=client.base_url):
                with self.assertRaises(LocalPolicyError):
                    client.ccr_mutate_class(CLASS_NAME, CCR_ID, "ABSENT", CANDIDATE_SHA, "QQ==")
                client._send_request.assert_not_called()

    def test_tool_schema_is_explicit_and_external_unknown_is_not_an_authorization(self):
        server = object.__new__(type(synthetic_server()))
        tools = {tool["name"]: tool for tool in server.handle_tools_list()["tools"]}
        schema = tools["iris_ccr_mutate_class_from_file"]["inputSchema"]
        self.assertEqual(set(schema["required"]), {
            "server_id", "namespace", "class_name", "file_path", "ccr_id",
            "expected_before_sha256",
        })
        # External discovery is UNKNOWN here (engine+configured) but that is only
        # informational: READ is allowed and no legacy write gate exists.
        unknown = synthetic_client(namespace="BASE-ACHS", role="BASE", engine=1, configured=1)
        self.assertEqual(unknown.get_namespace_ccr_status()["applicability"], "UNKNOWN")
        self.assertTrue(unknown.is_read_allowed()[0])
        self.assertFalse(hasattr(unknown, "is_write_allowed"))

    def test_handler_preflight_and_success_use_no_discovery_or_extra_transport(self):
        client = canonical_client()
        client._send_request.side_effect = None
        client._send_request.return_value = envelope([
            {"result": json.dumps(success_record(), separators=(",", ":"))}
        ])
        servers = {
            "achsbase": {
                "host": "10.244.10.4", "port": 80, "path_prefix": "/trakbase01",
                "namespace": "BASE-ACHS", "env_role": "BASE", "is_production": False,
                "username": "synthetic", "password": "synthetic",
            }
        }
        server = synthetic_server(client, servers)
        expected = (Path(__file__).resolve().parent.parent / "BASE-ACHS" / "src" /
                    "Custom" / "ACHS" / "Tests" / "Sample.cls")
        with (patch.object(Path, "resolve", autospec=True,
                           side_effect=lambda value, strict=False: value.absolute()),
              patch.object(Path, "is_file", return_value=True),
              patch.object(Path, "read_bytes", return_value=SOURCE)):
            result = server.handle_tool_call("iris_ccr_mutate_class_from_file", {
                "server_id": "achsbase",
                "namespace": "BASE-ACHS",
                "class_name": CLASS_NAME,
                "file_path": str(expected),
                "ccr_id": CCR_ID,
                "expected_before_sha256": "ABSENT",
            })

        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["structuredContent"], success_record())
        self.assertEqual(client._send_request.call_count, 1)

    def test_handler_canonicalizes_lf_and_crlf_to_identical_transport_bytes(self):
        servers = {
            "achsbase": {
                "host": "10.244.10.4", "port": 80, "path_prefix": "/trakbase01",
                "namespace": "BASE-ACHS", "env_role": "BASE", "is_production": False,
                "username": "synthetic", "password": "synthetic",
            }
        }
        expected = (Path(__file__).resolve().parent.parent / "BASE-ACHS" / "src" /
                    "Custom" / "ACHS" / "Tests" / "Sample.cls")
        payloads = []
        for source in (SOURCE, SOURCE.replace(b"\r\n", b"\n")):
            client = canonical_client()
            client._send_request.side_effect = None
            client._send_request.return_value = envelope([
                {"result": json.dumps(success_record(), separators=(",", ":"))}
            ])
            server = synthetic_server(client, servers)
            with (patch.object(Path, "resolve", autospec=True,
                               side_effect=lambda value, strict=False: value.absolute()),
                  patch.object(Path, "is_file", return_value=True),
                  patch.object(Path, "read_bytes", return_value=source)):
                result = server.handle_tool_call("iris_ccr_mutate_class_from_file", {
                    "server_id": "achsbase", "namespace": "BASE-ACHS",
                    "class_name": CLASS_NAME, "file_path": str(expected),
                    "ccr_id": CCR_ID, "expected_before_sha256": "ABSENT",
                })
            self.assertFalse(result.get("isError", False))
            payloads.append(client._send_request.call_args.kwargs["payload"])

        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(payloads[0]["parameters"][3], CANDIDATE_SHA)
        self.assertEqual(base64.b64decode(payloads[0]["parameters"][4]), SOURCE)

    def test_client_rejects_preflight_and_retained_failures(self):
        failures = (
            {"protocol": "ACHS_CCR_MUTATION_V1", "ok": 0, "state": "REJECTED",
             "code": "PREEXISTING_PENDING", "message": "pending row exists",
             "manual_action_required": 0, "checkout_may_exist": 0,
             "source_may_exist": 0, "tracking_may_exist": 0},
            {"protocol": "ACHS_CCR_MUTATION_V1", "ok": 0,
             "state": "COMPILE_FAILED_RETAINED", "code": "COMPILE_FAILED_RETAINED",
             "message": "tracked source retained", "manual_action_required": 1,
             "checkout_may_exist": 1, "source_may_exist": 1,
             "tracking_may_exist": 1},
            {"protocol": "ACHS_CCR_MUTATION_V1", "ok": 0, "state": "UNKNOWN_PARTIAL",
             "code": "UNKNOWN_PARTIAL", "message": "manual inspection required",
             "manual_action_required": 1, "checkout_may_exist": 1,
             "source_may_exist": 1, "tracking_may_exist": 1},
        )
        for record in failures:
            with self.subTest(state=record["state"]):
                client = canonical_client()
                client._send_request.side_effect = None
                client._send_request.return_value = envelope([{"result": json.dumps(record)}])
                with self.assertRaisesRegex(LocalPolicyError, "do not retry automatically"):
                    client.ccr_mutate_class(
                        CLASS_NAME, CCR_ID, "ABSENT", CANDIDATE_SHA,
                        base64.b64encode(SOURCE).decode("ascii"),
                    )
                client._send_request.assert_called_once()

    def test_existing_class_success_requires_checkout_and_new_tracking(self):
        baseline = "a" * 64
        record = success_record(checkout_created=1)
        client = canonical_client()
        client._send_request.side_effect = None
        client._send_request.return_value = envelope([{"result": json.dumps(record)}])

        result = client.ccr_mutate_class(
            CLASS_NAME, CCR_ID, baseline, CANDIDATE_SHA,
            base64.b64encode(SOURCE).decode("ascii"),
        )

        self.assertEqual(result, record)
        client._send_request.assert_called_once()

    def test_client_rejects_booleans_for_exact_integer_evidence(self):
        fields = (
            "ok", "suspend_level", "controlled_active", "round_trip", "tracking",
            "compile", "checkout_created", "created_tracking", "manual_action_required",
        )
        for field in fields:
            for value in (False, True):
                with self.subTest(field=field, value=value):
                    client = canonical_client()
                    client._send_request.side_effect = None
                    client._send_request.return_value = envelope([
                        {"result": json.dumps(success_record(**{field: value}))}
                    ])
                    with self.assertRaisesRegex(LocalPolicyError, "do not retry automatically"):
                        client.ccr_mutate_class(
                            CLASS_NAME, CCR_ID, "ABSENT", CANDIDATE_SHA,
                            base64.b64encode(SOURCE).decode("ascii"),
                        )

    def test_client_requires_positive_integer_change_id(self):
        encoded = base64.b64encode(SOURCE).decode("ascii")
        for change_id in ("", "42", 0, -1, False, True):
            with self.subTest(change_id=change_id):
                client = canonical_client()
                client._send_request.side_effect = None
                client._send_request.return_value = envelope([
                    {"result": json.dumps(success_record(change_id=change_id))}
                ])
                with self.assertRaisesRegex(LocalPolicyError, "do not retry automatically"):
                    client.ccr_mutate_class(
                        CLASS_NAME, CCR_ID, "ABSENT", CANDIDATE_SHA, encoded,
                    )

        client = canonical_client()
        client._send_request.side_effect = None
        client._send_request.return_value = envelope([
            {"result": json.dumps(success_record(change_id=42))}
        ])
        self.assertEqual(
            client.ccr_mutate_class(
                CLASS_NAME, CCR_ID, "ABSENT", CANDIDATE_SHA, encoded,
            )["change_id"],
            42,
        )

    def test_invalid_local_input_blocks_before_client_or_file_read(self):
        servers = {
            "achsbase": {
                "host": "10.244.10.4", "port": 80, "path_prefix": "/trakbase01",
                "namespace": "BASE-ACHS", "env_role": "BASE", "is_production": False,
                "username": "synthetic", "password": "synthetic",
            }
        }
        server = synthetic_server(servers=servers)
        with patch.object(Path, "read_bytes") as read_bytes:
            result = server.handle_tool_call("iris_ccr_mutate_class_from_file", {
                "server_id": "achsbase", "namespace": "BASE-ACHS",
                "class_name": CLASS_NAME, "file_path": "relative.cls",
                "ccr_id": CCR_ID, "expected_before_sha256": "ABSENT",
            })
        self.assertTrue(result.get("isError"))
        read_bytes.assert_not_called()

    def test_general_direct_objectscript_uses_runtime_gate_not_ccr(self):
        client = canonical_client()
        client._require_runtime_operation = Mock(side_effect=LocalPolicyError("runtime gate called"))

        with self.assertRaisesRegex(LocalPolicyError, "runtime gate called"):
            client._execute_direct_objectscript("Write 1")

        client._require_runtime_operation.assert_called_once_with()
        client._send_request.assert_not_called()

    def _run_initializer(self, record, upgrade=False):
        client = canonical_client()
        client._require_write = Mock(side_effect=AssertionError("normal gate must not run"))
        client._ephemeral_runner_has_pending_change = Mock(side_effect=[False, False])
        client._ephemeral_runner_is_absent = Mock(side_effect=[True, True])
        client._ephemeral_runner_save = Mock()
        client._ephemeral_runner_compile = Mock()
        client._ephemeral_runner_query = Mock(return_value=json.dumps(record))
        client._delete_ephemeral_runner = Mock(return_value=True)
        expected = "a" * 64 if upgrade else ""
        result = client.initialize_ccr_mutation(
            RESOURCE_SOURCE, CCR_ID, RESOURCE_SHA, upgrade, expected,
        )
        client._require_write.assert_not_called()
        self.assertEqual(result["runner_cleanup"], 1)
        self.assertEqual(result["runner_residue"], 0)
        return client, result

    def test_initializer_accepts_install_noop_drift_and_upgrade_states(self):
        for state in ("INSTALLED", "UNCHANGED", "DIFFERENT", "UPGRADED", "SOURCE_MATCH_NOT_COMPILED"):
            with self.subTest(state=state):
                client, result = self._run_initializer(
                    initializer_record(state), upgrade=state == "UPGRADED",
                )
                self.assertEqual(result["state"], state)
                self.assertEqual(result["ok"], 1 if state in {"INSTALLED", "UNCHANGED", "UPGRADED"} else 0)
                self.assertEqual(result["change_id"], 42 if state in {"INSTALLED", "UPGRADED"} else 0)
                self.assertEqual(client._ephemeral_runner_has_pending_change.call_count, 2)

    def test_source_match_not_compiled_is_fail_closed_evidence(self):
        # Runner may only report it with ok=0 and the candidate hash as current hash.
        for bad in (
            initializer_record("SOURCE_MATCH_NOT_COMPILED", ok=1),
            initializer_record("SOURCE_MATCH_NOT_COMPILED", current_sha256="b" * 64),
            initializer_record("SOURCE_MATCH_NOT_COMPILED", compile=1),
            initializer_record("UNCHANGED", ok=0),
            initializer_record("DIFFERENT", current_sha256=RESOURCE_SHA),
        ):
            with self.subTest(state=bad["state"], ok=bad["ok"]):
                with self.assertRaisesRegex(LocalPolicyError, "do not retry automatically"):
                    self._run_initializer(bad)
        server = Mock(spec=IRISMCPServer)
        server.initialize_ccr_mutation.return_value = initializer_record("SOURCE_MATCH_NOT_COMPILED")
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("isc_ai.cli.IRISMCPServer", return_value=server), \
                patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = main(["facility", "install", "--server", "achsbase", "--namespace", "BASE-ACHS", "--ccr", CCR_ID])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout.getvalue())["state"], "SOURCE_MATCH_NOT_COMPILED")

    def test_client_initializer_rejects_noncanonical_routes_before_any_runner_step(self):
        clients = (
            synthetic_client(namespace="BASE-ACHS", role="BASE"),
            synthetic_client(namespace="BASE-ACHS", role="BASE", host="10.244.10.4"),
            synthetic_client(namespace="BASE-ACHS", role="BASE", host="10.244.10.4",
                             path_prefix="/intbase01"),
            synthetic_client(namespace="UAT-ACHS", role="UAT", host="10.244.20.6"),
            synthetic_client(namespace="BASE-ACHS", role="DEV", host="10.244.10.4",
                             path_prefix="/trakbase01"),
            synthetic_client(namespace="BASE-ACHS", role="BASE", host="10.244.10.4",
                             path_prefix="/trakbase01", production=True),
        )
        for client in clients:
            with self.subTest(target=client.base_url, role=client.env_role):
                client._ephemeral_runner_save = Mock()
                client._ephemeral_runner_has_pending_change = Mock()
                with self.assertRaisesRegex(LocalPolicyError, "canonical BASE|production"):
                    client.initialize_ccr_mutation(RESOURCE_SOURCE, CCR_ID, RESOURCE_SHA)
                client._ephemeral_runner_save.assert_not_called()
                client._ephemeral_runner_has_pending_change.assert_not_called()
                client._send_request.assert_not_called()
        canonical = canonical_client()
        canonical.port = 8080
        canonical.base_url = canonical.base_url.replace(":80/", ":8080/")
        with self.assertRaisesRegex(LocalPolicyError, "canonical BASE"):
            canonical.initialize_ccr_mutation(RESOURCE_SOURCE, CCR_ID, RESOURCE_SHA)
        canonical._send_request.assert_not_called()

    def test_ephemeral_runner_query_prefix_logic_is_python38_compatible(self):
        source = (Path(__file__).resolve().parent.parent / "isc_ai" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn(".removeprefix(", source)
        self.assertNotIn(".removesuffix(", source)
        self.assertEqual(
            AtelierClient._ephemeral_runner_sql_suffix("User.McpTmpRun0123456789ab"),
            "McpTmpRun0123456789ab",
        )
        self.assertEqual(AtelierClient._ephemeral_runner_sql_suffix("Other.Name"), "Other.Name")
        client = canonical_client()
        client._send_request.side_effect = None
        encoded = base64.b64encode(b"payload").decode("ascii")
        client._send_request.return_value = envelope([{"result": f"MCPOK|{encoded}"}])
        self.assertEqual(client._ephemeral_runner_query("User.McpTmpRun0123456789ab"), "payload")
        self.assertEqual(
            client._send_request.call_args.kwargs["payload"]["query"],
            "SELECT SQLUser.McpTmpRun0123456789ab_Execute() AS result",
        )

    def test_initializer_requires_upgrade_baseline_and_rejects_baseline_without_upgrade(self):
        client = canonical_client()
        for upgrade, expected in ((True, ""), (False, "a" * 64), (True, "A" * 64)):
            with self.subTest(upgrade=upgrade, expected=expected):
                with self.assertRaises(LocalPolicyError):
                    client.initialize_ccr_mutation(
                        RESOURCE_SOURCE, CCR_ID, RESOURCE_SHA, upgrade, expected,
                    )
        client._send_request.assert_not_called()

    def test_initializer_cleanup_ambiguity_fails_closed(self):
        client = canonical_client()
        client._ephemeral_runner_has_pending_change = Mock(side_effect=[False, True])
        client._ephemeral_runner_is_absent = Mock(side_effect=[True, False])
        client._ephemeral_runner_save = Mock()
        client._ephemeral_runner_compile = Mock()
        client._ephemeral_runner_query = Mock(return_value=json.dumps(initializer_record()))
        client._delete_ephemeral_runner = Mock(return_value=False)
        with self.assertRaisesRegex(LocalPolicyError, "cleanup is unverified"):
            client.initialize_ccr_mutation(RESOURCE_SOURCE, CCR_ID, RESOURCE_SHA)

    def test_initializer_always_cleans_after_ambiguous_execution_failure(self):
        client = canonical_client()
        client._ephemeral_runner_has_pending_change = Mock(side_effect=[False, False])
        client._ephemeral_runner_is_absent = Mock(side_effect=[True, True])
        client._ephemeral_runner_save = Mock()
        client._ephemeral_runner_compile = Mock()
        client._ephemeral_runner_query = Mock(side_effect=LocalPolicyError("unknown outcome"))
        client._delete_ephemeral_runner = Mock(return_value=True)
        with self.assertRaisesRegex(LocalPolicyError, "unknown outcome"):
            client.initialize_ccr_mutation(RESOURCE_SOURCE, CCR_ID, RESOURCE_SHA)
        client._delete_ephemeral_runner.assert_called_once()

    def test_public_tool_rejects_initializer_and_other_mcp_targets(self):
        servers = {
            "achsbase": {
                "host": "10.244.10.4", "port": 80, "path_prefix": "/trakbase01",
                "namespace": "BASE-ACHS", "env_role": "BASE", "is_production": False,
                "username": "synthetic", "password": "synthetic",
            }
        }
        for class_name in (_CCR_MUTATION_CLASS, "MCP.Other", "Custom.Other.Sample"):
            with self.subTest(class_name=class_name):
                server = synthetic_server(servers=servers)
                server.get_client = Mock(side_effect=AssertionError("client construction forbidden"))
                arguments = {
                    "server_id": "achsbase", "namespace": "BASE-ACHS",
                    "class_name": class_name,
                    "file_path": str(_CCR_MUTATION_TEMPLATE),
                    "ccr_id": CCR_ID, "expected_before_sha256": "ABSENT",
                }
                result = server.handle_tool_call("iris_ccr_mutate_class_from_file", arguments)
                self.assertTrue(result.get("isError"))
                server.get_client.assert_not_called()

    def test_server_initializer_uses_canonical_config_and_requested_namespace(self):
        client = canonical_client()
        client.initialize_ccr_mutation = Mock(return_value=initializer_record())
        servers = {
            "achsbase": {
                "host": "10.244.10.4", "port": 80, "path_prefix": "/trakbase01",
                "namespace": "BASE-ACHS", "env_role": "BASE", "is_production": False,
                "username": "synthetic", "password": "synthetic",
            },
            "alias": {"canonical_server_id": "achsbase"},
            "renamed": {
                "host": "10.244.10.4", "port": 80, "path_prefix": "/trakbase01",
                "namespace": "BASE-ACHS", "env_role": "BASE", "is_production": False,
                "username": "synthetic", "password": "synthetic",
            },
        }
        server = synthetic_server(client, servers)
        with patch("isc_ai.server._load_ccr_mutation_resource", return_value=(RESOURCE_SOURCE, RESOURCE_SHA)):
            record = server.initialize_ccr_mutation("achsbase", "BASE-ACHS", CCR_ID)
            self.assertEqual(record["state"], "INSTALLED")
            with self.assertRaisesRegex(LocalPolicyError, "canonical server ID"):
                server.initialize_ccr_mutation("alias", "BASE-ACHS", CCR_ID)
            # Same route under a different server id is not the configured canonical entry.
            with self.assertRaisesRegex(LocalPolicyError, "canonical server ID"):
                server.initialize_ccr_mutation("renamed", "BASE-ACHS", CCR_ID)
            with self.assertRaisesRegex(LocalPolicyError, "configured canonical namespace"):
                server.initialize_ccr_mutation("achsbase", "INT", CCR_ID)
        client.initialize_ccr_mutation.assert_called_once_with(
            RESOURCE_SOURCE, CCR_ID, RESOURCE_SHA, False, "",
        )

    def test_server_initializer_rejects_noncanonical_route_config_before_client(self):
        canonical = {
            "host": "10.244.10.4", "port": 80, "path_prefix": "/trakbase01",
            "namespace": "BASE-ACHS", "env_role": "BASE", "is_production": False,
            "username": "synthetic", "password": "synthetic",
        }
        variants = (
            {"host": "configured.invalid", "port": 52773, "path_prefix": "/configured"},
            {"host": "10.244.20.6"},
            {"port": 52773},
            {"path_prefix": "/intbase01"},
            {"path_prefix": ""},
        )
        for override in variants:
            with self.subTest(override=override):
                servers = {"achsbase": {**canonical, **override}}
                server = synthetic_server(servers=servers)
                server.get_client = Mock(side_effect=AssertionError("client construction forbidden"))
                with patch("isc_ai.server._load_ccr_mutation_resource",
                           side_effect=AssertionError("resource read forbidden")):
                    with self.assertRaisesRegex(LocalPolicyError, "exact canonical BASE route"):
                        server.initialize_ccr_mutation("achsbase", "BASE-ACHS", CCR_ID)
                server.get_client.assert_not_called()


class FacilityStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = RESOURCE_SOURCE.decode("utf-8")

    def test_facility_declares_expected_class_and_sql_projection(self):
        self.assertIn(
            "Class MCP.CCRMutation Extends %RegisteredObject [ Final ]",
            self.source,
        )
        self.assertIn(
            "ClassMethod Mutate(pClass As %String, pCCR As %String, "
            "pExpectedBeforeSHA256 As %String, pCandidateSHA256 As %String, "
            "pSourceBase64 As %String(MAXLEN=700000)) As %String(MAXLEN=16384) "
            "[ SqlName = Mutate, SqlProc ]",
            self.source,
        )

    def test_facility_contains_required_same_process_gates_and_lifecycle(self):
        required = (
            '$Namespace \'= "BASE-ACHS"',
            'ChangeControlClassGet()',
            'SourceControlClassGet()',
            'GetSuspendLevel()',
            'IsSuspended()',
            'ChangeControlCreate($Username, "", .tCreated)',
            '%ChangeControl.NormaliseKey(pCCR)',
            '%ChangeControl.IsKeyValid(tNormalizedCCR)',
            'SourceControlCreate("", "", .tSourceCreated)',
            '%SourceControl.CheckOut(tDocument, "MCP CCRMutation", 0)',
            '%SourceControl.OnBeforeSave(tDocument)',
            'SetTextFromString($Namespace, pClass, tCanonicalSource)',
            '%SourceControl.OnAfterSave(tDocument)',
            '%SourceControl.AddToSourceControl(tDocument, "MCP CCRMutation")',
            'GetTextAsString("", tDocument, .tRoundTripSource)',
            '$System.OBJ.Compile(pClass, "ck-d")',
            'ACHS_CCR_MUTATION_V1',
            'UNKNOWN_PARTIAL',
            'New %SourceControl, %ChangeControl, %ccrSession',
            'Set tResult = ..MutateWorker(',
            'ClassMethod MutateWorker(',
            'manual_action_required',
            'checkout_may_exist',
            'source_may_exist',
            'tracking_may_exist',
            'Lock -^MCP.CCRMutation(pClass)',
        )
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, self.source)

    def test_facility_separates_bindings_and_tracks_exact_pending_provenance(self):
        self.assertIn('Parameter APPROVEDSOURCECONTROL = "%Studio.SourceControl.ISC"', self.source)
        self.assertIn('$ClassName(%ChangeControl) \'= tBinding', self.source)
        self.assertIn('$ClassName(%SourceControl) \'= tSourceBinding', self.source)
        self.assertNotIn('$ClassName(%SourceControl) \'= tBinding', self.source)
        # %EXACT preserves case: MIN() over SQLUPPER columns otherwise returns
        # uppercased values and breaks exact provenance comparison.
        for field in ('MIN(ID)', 'MIN(%EXACT(Name))', 'MIN(%EXACT(Action))', 'MIN(%EXACT(CCR))',
                      'MIN(%EXACT(Source))', 'MIN(%EXACT(ItemFile))', 'MIN(%EXACT("User"))',
                      'MIN(%EXACT(InternalName))'):
            self.assertIn(field, self.source)
        self.assertIn('PREEXISTING_PENDING', self.source)
        self.assertIn('If tPendingCount Quit ..BeforeFail', self.source)
        self.assertIn('Pending row identity changed after save', self.source)
        self.assertIn('(InternalName = :pDocument OR ItemFile = :pExpectedFile)', self.source)
        self.assertNotIn('$System.OBJ.Delete', self.source)
        self.assertNotIn('UndoCheckout', self.source)
        self.assertNotIn('preexisting_tracking', self.source)
        self.assertNotIn('Kill %SourceControl', self.source)
        self.assertNotIn('Kill %ChangeControl', self.source)
        self.assertEqual(self.source.count('%Set("change_id", +pRowID)'), 2)

    def test_facility_canonicalizes_before_hash_import_and_round_trip(self):
        self.assertIn('Set tCanonicalSource = ..CanonicalCRLF(tSource)', self.source)
        self.assertIn('..CanonicalCRLF(tBeforeSource)', self.source)
        self.assertIn('SetTextFromString($Namespace, pClass, tCanonicalSource)', self.source)
        self.assertIn('..CanonicalCRLF(tRoundTripSource)', self.source)

    def test_facility_never_resumes_or_suspends_control(self):
        self.assertNotIn(".Resume(", self.source)
        self.assertNotIn(".Suspend(", self.source)

    def test_facility_revalidates_before_checkout_and_rechecks_baseline_before_import(self):
        worker_start = self.source.index("ClassMethod MutateWorker(")
        worker_end = self.source.index("ClassMethod CanonicalCRLF(")
        worker = self.source[worker_start:worker_end]
        helper = self.source[self.source.index("ClassMethod ControlIntact("):]
        for text in (
            '$Namespace \'= "BASE-ACHS"',
            'ChangeControlClassGet() \'= "TC.ChangeControl.CCR"',
            "SourceControlClassGet() '= ..#APPROVEDSOURCECONTROL",
            "IsSuspended() Quit 0",
            "+pSuspendLevel '= 0 Quit 0",
        ):
            with self.subTest(text=text):
                self.assertIn(text, helper)
        first_revalidation = worker.index("..ControlIntact(.tSuspendLevel)")
        checkout = worker.index('%SourceControl.CheckOut(tDocument, "MCP CCRMutation", 0)')
        before_save = worker.index("%SourceControl.OnBeforeSave(tDocument)")
        second_revalidation = worker.index("..ControlIntact(.tSuspendLevel)", before_save)
        presence_recheck = worker.index("%ExistsId(pClass) '= tWasPresent")
        source_recheck = worker.index("..CanonicalCRLF(tRecheckSource)")
        pending_recheck = worker.index("Unexpected pending rows before import")
        import_call = worker.index("SetTextFromString($Namespace, pClass, tCanonicalSource)")
        self.assertEqual(worker.count("..ControlIntact(.tSuspendLevel)"), 2)
        self.assertLess(first_revalidation, checkout)
        self.assertLess(checkout, before_save)
        self.assertLess(before_save, second_revalidation)
        self.assertLess(second_revalidation, presence_recheck)
        self.assertLess(presence_recheck, source_recheck)
        self.assertLess(source_recheck, pending_recheck)
        self.assertLess(pending_recheck, import_call)
        self.assertIn("'= pExpectedBeforeSHA256 Quit ..Retained(\"BASELINE_DRIFT_RETAINED\"", worker)
        self.assertIn('"CONTROL_CHANGED_RETAINED"', worker[before_save:import_call])
        self.assertIn('BeforeFail("CONTROL_CHANGED"', worker[:checkout])
        self.assertNotIn("Retained(", worker[:first_revalidation])

    def test_public_wrapper_has_one_exit_after_conditional_unlock(self):
        wrapper = self.source.split("ClassMethod MutateWorker(", 1)[0]
        self.assertEqual(wrapper.count("Lock +^MCP.CCRMutation(pClass)"), 1)
        self.assertEqual(wrapper.count("Lock -^MCP.CCRMutation(pClass)"), 1)
        self.assertEqual(wrapper.count("Quit tResult"), 1)
        self.assertLess(
            wrapper.index("Lock -^MCP.CCRMutation(pClass)"),
            wrapper.index("Quit tResult"),
        )


class InitializerTests(unittest.TestCase):
    def test_resource_is_repository_relative_and_lf_crlf_digest_is_equivalent(self):
        expected = Path(__file__).resolve().parent.parent / "isc_ai" / "resources" / "MCP" / "CCRMutation.cls.template"
        self.assertEqual(_CCR_MUTATION_TEMPLATE, expected)
        lf = RESOURCE_SOURCE.replace(b"\r\n", b"\n")
        self.assertEqual(_canonical_crlf_bytes(lf), RESOURCE_SOURCE)
        self.assertEqual(
            hashlib.sha256(_canonical_crlf_bytes(lf)).hexdigest(),
            hashlib.sha256(_canonical_crlf_bytes(RESOURCE_SOURCE)).hexdigest(),
        )

    def test_python_uses_runtime_resource_hash_without_digest_constant(self):
        source = (Path(__file__).resolve().parent.parent / "isc_ai" / "server.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("_CCR_MUTATION_TEMPLATE = iris_mcp_config.MUTATION_TEMPLATE_PATH", source)
        self.assertNotRegex(source, r"_CCR_[A-Z_]*SHA256\s*=")

    def test_reference_render_reproduces_deployed_facility_digest(self):
        self.assertEqual(RESOURCE_SHA, DEPLOYED_FACILITY_SHA)

    def test_template_has_no_site_literals_and_renders_per_site(self):
        template = _CCR_MUTATION_TEMPLATE.read_text(encoding="utf-8")
        for literal in ("BASE-ACHS", "Custom.ACHS", '"ACHS"', "ACHS_CCR"):
            self.assertNotIn(literal, template)
        other = policy_with(
            ccr_prefix="SITE", ccr_digits=5, protocol_prefix="MCP",
            canonical_base=REFERENCE_POLICY.canonical_base.__class__(
                "site-base", "base.example.invalid", 80, "/base", "BASE-SITE"),
            mutation_class_prefix="Custom.Site.Sub",
        )
        rendered, digest = _load_ccr_mutation_resource(other)
        text = rendered.decode("utf-8")
        self.assertNotEqual(digest, DEPLOYED_FACILITY_SHA)
        self.assertIn('$Namespace \'= "BASE-SITE"', text)
        self.assertIn('$Extract(pClass, 1, 16) \'= "Custom.Site.Sub."', text)
        self.assertIn('($Length(pCCR) = 9) && ($Extract(pCCR, 1, 4) = "SITE") && ($Extract(pCCR, 5, 9)?5N)', text)
        self.assertIn('"CCR must match SITE followed by five digits"', text)
        self.assertIn("MCP_CCR_MUTATION_V1", text)
        self.assertNotIn("{{", text)
        with self.assertRaisesRegex(LocalPolicyError, "canonical_base, site.ccr_id and mutation"):
            _load_ccr_mutation_resource(UNCONFIGURED_POLICY)

    def test_generated_payload_has_conditional_states_provenance_and_revalidation(self):
        payload = "\n".join(AtelierClient._ccr_initializer_runner_content(
            "User.McpTmpRun0123456789ab", RESOURCE_SOURCE, "BASE-ACHS", CCR_ID,
            RESOURCE_SHA, policy=REFERENCE_POLICY,
        ))
        required = (
            "Lock +^MCP.CCRMutationInitializer:30",
            '$Namespace \'= "BASE-ACHS"',
            "ChangeControlClassGet()",
            "SourceControlClassGet()",
            "IsSuspended()",
            "GetSuspendLevel()",
            'ChangeControlCreate($Username,"",.created)',
            "%ChangeControl.NormaliseKey(requestedCCR)",
            "%ChangeControl.IsKeyValid(normalizedCCR)",
            'SourceControlCreate("","",.sourceCreated)',
            "%Dictionary.ClassDefinition).%ExistsId(targetClass)",
            'NoMutation("UNCHANGED"',
            'NoMutation("DIFFERENT"',
            '"INSTALLED"',
            '"UPGRADED"',
            '%SourceControl.CheckOut(document,"MCP CCRMutation initializer",0)',
            "PREEXISTING_PENDING",
            "%SourceControl.OnBeforeSave(document)",
            "SetTextFromString($Namespace,targetClass,source)",
            '%SourceControl.AddToSourceControl(document,"MCP CCRMutation initializer")',
            "UpdateUncommitted(expectedFile, , , , , , , requestedCCR)",
            'GetTextAsString("",document,.roundTripSource)',
            '$System.OBJ.Compile(targetClass,"ck-d")',
            "ACHS_CCR_INITIALIZER_V1",
            RESOURCE_SHA,
            'result.%Set("mutation_started",+pMutationStarted)',
            'NoMutation("SOURCE_MATCH_NOT_COMPILED"',
            "$System.OBJ.IsUpToDate(pClass)",
            '%Dictionary.CompiledMethod).%OpenId(pClass_"||Mutate",0)',
            "compiled.SqlProc",
            'compiled.SqlName \'= "Mutate"',
            "ClassMethod ControlIntact(Output pSuspendLevel)",
            "ClassMethod CompiledArtifactsCurrent(pClass)",
        )
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, payload)
        initialize = payload[payload.index("ClassMethod Initialize()"):payload.index("ClassMethod ReadPending(")]
        helper = payload[payload.index("ClassMethod ControlIntact("):payload.index("ClassMethod CompiledArtifactsCurrent(")]
        for text in (
            '$Namespace \'= "BASE-ACHS" Quit 0',
            'ChangeControlClassGet() \'= "TC.ChangeControl.CCR" Quit 0',
            'SourceControlClassGet() \'= "%Studio.SourceControl.ISC" Quit 0',
            "IsSuspended() Quit 0",
            "+pSuspendLevel '= 0 Quit 0",
        ):
            with self.subTest(text=text):
                self.assertIn(text, helper)
        # UNCHANGED is only reachable after compiled-artifact verification.
        not_compiled = initialize.index('NoMutation("SOURCE_MATCH_NOT_COMPILED"')
        unchanged = initialize.index('NoMutation("UNCHANGED"')
        self.assertLess(initialize.index("..CompiledArtifactsCurrent(targetClass)"), unchanged)
        self.assertLess(not_compiled, unchanged)
        # Revalidation #1 precedes the first mutation-capable step (checkout, then OnBeforeSave).
        first_revalidation = initialize.index("..ControlIntact(.suspendLevel)")
        checkout = initialize.index('%SourceControl.CheckOut(document,"MCP CCRMutation initializer",0)')
        before_save = initialize.index("%SourceControl.OnBeforeSave(document)")
        target_save = initialize.index("SetTextFromString($Namespace,targetClass,source)")
        self.assertEqual(initialize.count("..ControlIntact(.suspendLevel)"), 2)
        self.assertLess(first_revalidation, checkout)
        self.assertLess(checkout, before_save)
        self.assertIn('BeforeFail("CONTROL_CHANGED"', initialize[first_revalidation:checkout])
        self.assertNotIn("Retained(", initialize[:first_revalidation])
        # Revalidation #2 plus the TOCTOU baseline recheck sit between OnBeforeSave and import.
        second_revalidation = initialize.index("..ControlIntact(.suspendLevel)", before_save)
        presence_recheck = initialize.index("%ExistsId(targetClass) '= present")
        source_recheck = initialize.index("..CanonicalCRLF(recheckSource)")
        pending_recheck = initialize.index("Unexpected pending rows before import")
        self.assertLess(before_save, second_revalidation)
        self.assertLess(second_revalidation, presence_recheck)
        self.assertLess(presence_recheck, source_recheck)
        self.assertLess(source_recheck, pending_recheck)
        self.assertLess(pending_recheck, target_save)
        self.assertIn('"CONTROL_CHANGED_RETAINED"', initialize[before_save:target_save])
        self.assertIn("'= expectedCurrentSHA Quit ..Retained(\"BASELINE_DRIFT_RETAINED\"", initialize)
        self.assertNotIn("_execute_direct_objectscript", payload)
        self.assertNotIn("$System.OBJ.Delete", payload)
        self.assertNotIn("UndoCheckout", payload)

    def test_cli_parsing_and_upgrade_contract(self):
        args = _parse_cli_args([
            "facility", "install", "--server", "achsbase", "--namespace", "BASE-ACHS",
            "--ccr", CCR_ID,
        ])
        self.assertEqual((args.command, args.facility_command), ("facility", "install"))
        self.assertFalse(args.upgrade)
        upgraded = _parse_cli_args([
            "facility", "install", "--server", "achsbase", "--namespace", "BASE-ACHS",
            "--ccr", CCR_ID, "--upgrade", "--expected-current-sha256", "a" * 64,
        ])
        self.assertTrue(upgraded.upgrade)
        for argv in (
            ["facility", "install", "--server", "x", "--namespace", "BASE-ACHS", "--ccr", "ACHS 875"],
            ["facility", "install", "--server", "x", "--namespace", "BASE-ACHS", "--ccr", CCR_ID,
             "--upgrade"],
            ["facility", "install", "--server", "x", "--namespace", "BASE-ACHS", "--ccr", CCR_ID,
             "--expected-current-sha256", "a" * 64],
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                _parse_cli_args(argv)

    def test_cli_exit_codes_and_no_argument_stdio_behavior(self):
        server = Mock(spec=IRISMCPServer)
        server.initialize_ccr_mutation.return_value = initializer_record("DIFFERENT")
        stdout = io.StringIO()
        with patch("isc_ai.cli.IRISMCPServer", return_value=server), patch("sys.stdout", stdout):
            self.assertEqual(main([
                "facility", "install", "--server", "achsbase", "--namespace", "BASE-ACHS",
                "--ccr", CCR_ID,
            ]), 2)
        server.initialize_ccr_mutation.assert_called_once()
        stdio_server = Mock(spec=IRISMCPServer)
        with patch("isc_ai.cli.IRISMCPServer", return_value=stdio_server):
            self.assertEqual(main(["mcp"]), 0)
        stdio_server.run.assert_called_once_with()
        help_out = io.StringIO()
        with patch("sys.stdout", help_out), self.assertRaises(SystemExit) as raised:
            main([])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("examples:", help_out.getvalue())

    def test_cli_exit_code_matrix(self):
        argv = ["facility", "install", "--server", "achsbase", "--namespace", "BASE-ACHS", "--ccr", CCR_ID]
        expected = {
            "INSTALLED": 0, "UNCHANGED": 0, "UPGRADED": 0,
            "DIFFERENT": 2, "SOURCE_MATCH_NOT_COMPILED": 1,
        }
        for state, code in expected.items():
            with self.subTest(state=state):
                server = Mock(spec=IRISMCPServer)
                server.initialize_ccr_mutation.return_value = initializer_record(state)
                with patch("isc_ai.cli.IRISMCPServer", return_value=server), \
                        patch("sys.stdout", io.StringIO()):
                    self.assertEqual(main(argv), code)
        # A record with ok=0 in a nominally successful state never exits zero.
        server = Mock(spec=IRISMCPServer)
        server.initialize_ccr_mutation.return_value = initializer_record("INSTALLED", ok=0)
        with patch("isc_ai.cli.IRISMCPServer", return_value=server), \
                patch("sys.stdout", io.StringIO()):
            self.assertEqual(main(argv), 1)
        server = Mock(spec=IRISMCPServer)
        server.initialize_ccr_mutation.side_effect = LocalPolicyError("policy " + "x" * 2000)
        stderr = io.StringIO()
        with patch("isc_ai.cli.IRISMCPServer", return_value=server), patch("sys.stderr", stderr):
            self.assertEqual(main(argv), 1)
        self.assertLess(len(stderr.getvalue()), 700)
        self.assertIn("Initializer failed: policy", stderr.getvalue())

    def test_cli_unexpected_exception_returns_one_without_traceback_or_details(self):
        argv = ["facility", "install", "--server", "achsbase", "--namespace", "BASE-ACHS", "--ccr", CCR_ID]
        secret = "C:/secret/path/credentials.json user=admin"
        for failure in (
            RuntimeError(secret),
            KeyError("state"),
            OSError(2, secret),
            ValueError(secret),
        ):
            with self.subTest(failure=type(failure).__name__):
                server = Mock(spec=IRISMCPServer)
                server.initialize_ccr_mutation.side_effect = failure
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch("isc_ai.cli.IRISMCPServer", return_value=server), \
                        patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                    code = main(argv)
                self.assertEqual(code, 1)
                self.assertEqual(stdout.getvalue(), "")
                message = stderr.getvalue()
                self.assertIn("Initializer failed: unexpected " + type(failure).__name__, message)
                self.assertIn("details withheld", message)
                self.assertNotIn("secret", message)
                self.assertNotIn("admin", message)
                self.assertNotIn("Traceback", message)
                self.assertEqual(message.count("\n"), 1)
                self.assertLess(len(message), 300)
        # Constructor failures on the init path are also bounded.
        stderr = io.StringIO()
        with patch("isc_ai.cli.IRISMCPServer", side_effect=RuntimeError(secret)), \
                patch("sys.stderr", stderr):
            self.assertEqual(main(argv), 1)
        self.assertNotIn("secret", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
