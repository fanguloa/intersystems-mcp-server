"""Runner host namespaces, truthful runner diagnostics, SQL literal policy and the
local runtime audit trail. Hermetic: no network, no real HOME, no real config."""

import base64
import builtins
import io
import json
import os
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from tests.hermetic_test_support import (
    HermeticTestCase, REF, envelope, iris_mcp_config, policy_with, reference_policy_data,
    synthetic_client, synthetic_server,
)
from isc_ai import server as server_module
from isc_ai.ccr_discovery import LocalPolicyError
from isc_ai.config import ConfigError, RunnerHost
from isc_ai.server import AtelierClient, _read_sql_policy_violation

_REAL_OPEN = builtins.open
_REAL_APPEND = server_module._append_runtime_audit
RUN_ID = "McpTmpRun0123456789ab"
CLASS = "User." + RUN_ID
UUID = Mock(hex="0123456789abfedcba")
UAT = REF["uat_namespace"]
ROUTED_POLICY = policy_with(runner_hosts=(RunnerHost(UAT, "USER", frozenset({"UAT"})),))


def runner_ok(output):
    return "MCPOK|" + base64.b64encode(output.encode("utf-8")).decode("ascii")


def lifecycle_responder(calls, output="synthetic-output", put=(201, {}), compile_=None):
    """Class-level _send_request fake that records the namespace of every request."""
    def send(client, endpoint, method="GET", payload=None, **kwargs):
        calls.append((client.namespace, endpoint, method, payload))
        if endpoint == "/action/query" and payload["query"] == AtelierClient._EPHEMERAL_CHANGE_SQL:
            return envelope([])
        if method == "PUT":
            return put
        if endpoint == "/action/compile":
            return compile_ or (200, {"status": {"errors": [], "summary": ""}})
        if endpoint == "/action/query":
            return envelope([{"result": runner_ok(output)}])
        if method == "DELETE":
            return 200, {}
        if method == "GET":
            return 404, {}
        raise AssertionError("Unexpected runner request")
    return send


def routed_client(namespace=UAT, role="UAT", policy=ROUTED_POLICY, **kwargs):
    client = synthetic_client(namespace=namespace, role=role, host=REF["clinical_vip"],
                              policy=policy, **kwargs)
    del client._send_request  # use the class-level (patched) transport
    return client


class RunnerHostConfigTests(HermeticTestCase):
    def parse(self, runtime):
        data = reference_policy_data()
        data["runtime"] = runtime
        return iris_mcp_config.parse_policy(data, Path("/project"))

    def test_valid_runner_hosts_parse_and_match_explicitly(self):
        policy = self.parse({"runner_hosts": [
            {"namespace": "uat-achs", "runner_namespace": "user", "roles": ["uat"]}]})
        self.assertEqual(policy.runner_hosts, (RunnerHost("UAT-ACHS", "USER", frozenset({"UAT"})),))
        self.assertEqual(policy.runner_host_for("UAT-ACHS", "UAT").runner_namespace, "USER")
        self.assertEqual(policy.runner_host_for("uat-achs", " uat ").runner_namespace, "USER")
        self.assertEqual(policy.runner_host_for("UAT-ACHS", "UAT", "any.host").runner_namespace, "USER")
        self.assertIsNone(policy.runner_host_for("UAT-ACHS", "QA"))
        self.assertIsNone(policy.runner_host_for("BASE-ACHS", "UAT"))
        self.assertEqual(self.parse({}).runner_hosts, ())
        self.assertEqual(iris_mcp_config.parse_policy(reference_policy_data(), Path("/p")).runner_hosts, ())

    def test_lowercase_roles_are_runner_hosts_only(self):
        self.assertEqual(self.parse({"runner_hosts": [
            {"namespace": "UAT-ACHS", "runner_namespace": "USER", "roles": ["uat", "Qa"]}]}
        ).runner_hosts[0].roles, frozenset({"UAT", "QA"}))
        data = reference_policy_data()
        data["packages"] = {"NS": [{"prefix": "Custom.X", "roles": ["base"]}]}
        with self.assertRaisesRegex(ConfigError, "invalid value"):
            iris_mcp_config.parse_policy(data, Path("/project"))

    def test_hosts_scope_entries_case_insensitively(self):
        policy = self.parse({"runner_hosts": [
            {"namespace": "UAT-ACHS", "runner_namespace": "USER", "roles": ["UAT"],
             "hosts": ["ACHSUAT01", "10.244.20.6"]}]})
        self.assertEqual(policy.runner_hosts[0].hosts, frozenset({"achsuat01", "10.244.20.6"}))
        for host in ("achsuat01", "AchsUat01", "10.244.20.6"):
            with self.subTest(host=host):
                self.assertEqual(policy.runner_host_for("UAT-ACHS", "UAT", host).runner_namespace, "USER")
        for host in ("achsuat02", None, ""):
            with self.subTest(host=host):
                self.assertIsNone(policy.runner_host_for("UAT-ACHS", "UAT", host))

    def test_invalid_runner_hosts_are_rejected(self):
        good = {"namespace": "UAT-ACHS", "runner_namespace": "USER", "roles": ["UAT"]}
        cases = (
            ([], "runtime must be an object"),
            ({"runner_hosts": {}}, "must be a list"),
            ({"other": []}, "Unknown keys in runtime"),
            ({"runner_hosts": ["UAT-ACHS"]}, "must be an object"),
            ({"runner_hosts": [dict(good, extra=1)]}, "Unknown keys"),
            ({"runner_hosts": [dict(good, namespace="a/b")]}, "plain namespace"),
            ({"runner_hosts": [dict(good, namespace="UAT%2FX")]}, "plain namespace"),
            ({"runner_hosts": [dict(good, namespace=None)]}, "plain namespace"),
            ({"runner_hosts": [dict(good, runner_namespace="uat-achs")]}, "must differ"),
            ({"runner_hosts": [dict(good, namespace="TRAK-LIVE")]}, "production marker"),
            ({"runner_hosts": [dict(good, runner_namespace="PRODHOST")]}, "production marker"),
            ({"runner_hosts": [dict(good, roles=[])]}, "must not be empty"),
            ({"runner_hosts": [dict(good, roles="UAT")]}, "list of strings"),
            ({"runner_hosts": [dict(good, roles=["LIVE"])]}, "production roles"),
            ({"runner_hosts": [dict(good, roles=["STAGING"])]}, "not configured non-production"),
            ({"runner_hosts": [dict(good, roles=["u a t"])]}, "invalid value"),
            ({"runner_hosts": [dict(good, roles=[1])]}, "must be a string"),
            ({"runner_hosts": [dict(good, hosts=[])]}, "hosts must not be empty"),
            ({"runner_hosts": [dict(good, hosts="h1")]}, "list of strings"),
            ({"runner_hosts": [dict(good, hosts=["bad host"])]}, "invalid value"),
            ({"runner_hosts": [dict(good, hosts=[""])]}, "must not be empty"),
            ({"runner_hosts": [good, dict(good, namespace="uat-achs", runner_namespace="OTHER")]}, "duplicated"),
        )
        for runtime, message in cases:
            with self.subTest(runtime=runtime), self.assertRaisesRegex(ConfigError, message):
                self.parse(runtime)


class RunnerHostRoutingTests(HermeticTestCase):
    def run_routed(self, client, code='Write "x", !', **responder):
        calls = []
        with patch.object(AtelierClient, "_send_request", autospec=True,
                          side_effect=lifecycle_responder(calls, **responder)), \
                patch("uuid.uuid4", return_value=UUID):
            output = client._execute_direct_objectscript(code)
        return output, calls

    def test_entry_routes_whole_lifecycle_to_runner_namespace_with_prefix(self):
        client = routed_client()
        base_url = client.base_url
        output, calls = self.run_routed(client, 'Write "x", !')
        self.assertEqual(output, "synthetic-output")
        self.assertEqual({call[0] for call in calls}, {"USER"})
        methods = [(endpoint, method) for _, endpoint, method, _ in calls]
        self.assertIn(("/action/compile", "POST"), methods)
        self.assertEqual(methods[-3:][1:], [(f"/doc/{CLASS}.cls", "GET"), ("/action/query", "POST")])
        self.assertEqual(client.namespace, UAT)
        self.assertEqual(client.base_url, base_url)
        uploaded = next(payload["content"] for _, _, method, payload in calls if method == "PUT")
        start = uploaded.index("ClassMethod Payload()")
        body = uploaded[start:]
        self.assertEqual(body[2:5], ["    New $NAMESPACE", f'    Set $NAMESPACE = "{UAT}"', '    Write "x", !'])
        start_record, end_record = self.runtime_audit_records
        self.assertEqual(start_record["runner_namespace"], "USER")
        self.assertEqual(start_record["target_namespace"], UAT)
        self.assertEqual(start_record["code"], 'Write "x", !')
        self.assertEqual(end_record["outcome"], "ok")

    def test_no_entry_or_role_mismatch_keeps_target_namespace_and_payload(self):
        for namespace, role in ((REF["base_namespace"], "BASE"), (UAT, "QA"), (UAT, "BASE")):
            with self.subTest(namespace=namespace, role=role):
                client = routed_client(namespace=namespace, role=role)
                _, calls = self.run_routed(client)
                self.assertEqual({call[0] for call in calls}, {namespace})
                uploaded = next(payload["content"] for _, _, method, payload in calls if method == "PUT")
                self.assertNotIn("    New $NAMESPACE", uploaded)
        client = routed_client(policy=policy_with())
        _, calls = self.run_routed(client)
        self.assertEqual({call[0] for call in calls}, {UAT})

    def test_hosts_list_limits_routing_to_listed_target_hosts(self):
        vip = REF["clinical_vip"]
        for hosts, expected in ((frozenset({vip}), "USER"), (frozenset({"achsuat01"}), UAT)):
            with self.subTest(hosts=hosts):
                policy = policy_with(runner_hosts=(RunnerHost(UAT, "USER", frozenset({"UAT"}), hosts),))
                _, calls = self.run_routed(routed_client(policy=policy))
                self.assertEqual({call[0] for call in calls}, {expected})

    def test_entry_never_opens_production_or_production_looking_namespaces(self):
        live = policy_with(runner_hosts=(RunnerHost("APP-LIVE", "USER", frozenset({"UAT"})),))
        tainted = policy_with(runner_hosts=(RunnerHost(UAT, "PRODHOST", frozenset({"UAT"})),))
        cases = (
            dict(production=True),
            dict(namespace="APP-LIVE", policy=live),
            dict(role="LIVE", policy=policy_with(runner_hosts=(RunnerHost(UAT, "USER", frozenset({"LIVE"})),))),
            dict(policy=tainted),
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                client = routed_client(**kwargs)
                transport = Mock(side_effect=AssertionError("network forbidden"))
                with patch.object(AtelierClient, "_send_request", transport), \
                        patch("uuid.uuid4", return_value=UUID):
                    with self.assertRaises(LocalPolicyError):
                        client._execute_direct_objectscript("Write 1")
                    result = synthetic_server(client, policy=client.policy).handle_tool_call(
                        "iris_execute_objectscript", {"server_id": "fake", "code": "Write 1"})
                self.assertTrue(result.get("isError"))
                transport.assert_not_called()
                self.assertEqual(self.runtime_audit_records, [])

    def test_namespace_prefix_is_a_safe_literal(self):
        client = routed_client()
        self.assertEqual(client._namespace_switch_prefix(), f'New $NAMESPACE\nSet $NAMESPACE = "{UAT}"\n')
        client.namespace = 'X"Y'
        with self.assertRaises(LocalPolicyError):
            client._namespace_switch_prefix()


class RunnerDiagnosticTests(HermeticTestCase):
    SECRET_CODE = "Set ^SecretPayload=1"

    def test_put_200_with_status_error_is_a_locked_namespace_failure(self):
        client = routed_client(policy=policy_with())
        put = (200, {
            "status": {"errors": [], "summary": ""},
            "console": [f"NOTICE: {CLASS}.CLS is being treated as Readonly - Namespace is LOCKED"],
            "result": {"name": f"{CLASS}.cls", "status": f"ERROR #5845: Item '{CLASS}.CLS' is not editable"},
        })
        calls = []
        with patch.object(AtelierClient, "_send_request", autospec=True,
                          side_effect=lifecycle_responder(calls, put=put)), \
                patch("uuid.uuid4", return_value=UUID):
            with self.assertRaises(LocalPolicyError) as raised:
                client._execute_direct_objectscript(self.SECRET_CODE)
        message = str(raised.exception)
        self.assertIn("upload failed", message)
        self.assertIn("ERROR #5845", message)
        self.assertIn("runtime.runner_hosts", message)
        self.assertNotIn("SecretPayload", message)
        self.assertNotIn("Readonly", message)
        self.assertNotIn("/action/compile", [call[1] for call in calls])
        self.assertIn("DELETE", [call[2] for call in calls])
        self.assertEqual(self.runtime_audit_records[-1]["outcome"], message)

    def test_backup_node_read_only_hints_and_success_envelopes(self):
        client = synthetic_client()
        client._send_request.side_effect = None
        client._send_request.return_value = (400, {"result": {
            "status": f"ERROR #5883: Item '{CLASS}.CLS' is mapped from a database that you do not have write permission on."}})
        with self.assertRaisesRegex(LocalPolicyError, r"HTTP 400; IRIS ERROR #5883\. Hint: database is read-only"):
            client._ephemeral_runner_save(CLASS, ["Class x {}"])
        self.assertTrue(client._send_request.call_args.kwargs["capture_error_body"])
        for response in ((201, {}), (200, {"result": {"status": ""}}),
                         (200, {"status": {"errors": [], "summary": ""}, "result": {"status": ""}})):
            with self.subTest(response=response):
                client._send_request.return_value = response
                self.assertIsNone(client._ephemeral_runner_save(CLASS, []))
        client._send_request.return_value = (500, {"error": "Atelier HTTP request failed"})
        with self.assertRaisesRegex(LocalPolicyError, "^Temporary executor upload failed; server details withheld.$"):
            client._ephemeral_runner_save(CLASS, [])

    def test_compile_diagnostic_is_bounded_and_hinted(self):
        client = synthetic_client()
        client._send_request.side_effect = None
        client._send_request.return_value = (200, {"status": {
            "errors": [{"error": "ERROR #302: the database is read-only."}], "summary": "failed"},
            "console": ["console-detail-must-not-leak"]})
        with self.assertRaises(LocalPolicyError) as raised:
            client._ephemeral_runner_compile(CLASS)
        message = str(raised.exception)
        self.assertIn("compilation failed", message)
        self.assertIn("ERROR #302", message)
        self.assertIn("read-only", message)
        self.assertNotIn("console-detail", message)
        client._send_request.return_value = (200, {"status": {"errors": [{"error": "E" * 2000}], "summary": "x"}})
        with self.assertRaises(LocalPolicyError) as raised:
            client._ephemeral_runner_compile(CLASS)
        self.assertLess(len(str(raised.exception)), 400)

    def test_http_error_body_is_parsed_only_on_request(self):
        client = synthetic_client()
        del client._send_request
        body = json.dumps({"result": {"status": "ERROR #5883: read-only"}}).encode("utf-8")

        def fail(*args, **kwargs):
            raise urllib.error.HTTPError("http://x", 400, "Bad Request", {}, io.BytesIO(body))

        client.opener.open.side_effect = fail
        self.assertEqual(client._send_request("/doc/x", method="PUT", payload={}, capture_error_body=True),
                         (400, {"result": {"status": "ERROR #5883: read-only"}}))
        self.assertEqual(client._send_request("/doc/x", method="PUT", payload={}),
                         (400, {"error": "Atelier HTTP request failed"}))


class SqlReadPolicyLiteralTests(HermeticTestCase):
    def test_keywords_and_system_prefixes_inside_literals_are_accepted(self):
        for sql in (
            "SELECT Name FROM %Dictionary.ClassDefinition WHERE Name LIKE '%Create%'",
            "SELECT 'a'' CREATE' AS x",
            "SELECT '%SYSTEM.Version' AS x, '$SYSTEM.Util' AS y",
            "SELECT 'x' AS \"col\" FROM T WHERE Text = 'a DELETE inside'",
            "select 'CALL' as word",
        ):
            with self.subTest(sql=sql):
                self.assertIsNone(_read_sql_policy_violation(sql))

    def test_tokens_outside_literals_and_malformed_quotes_stay_denied(self):
        for sql in (
            "SELECT 1; CREATE TABLE x",
            "SELECT 'a''' CREATE",
            "SELECT 'unterminated",
            "SELECT 'a'' CREATE",
            'SELECT "unterminated',
            # Each of these hides a real token behind a quote inside an identifier or
            # comment; a naive literal stripper would accept them.
            "SELECT \"x'\" FROM t; CREATE TABLE y --'",
            "SELECT 1 -- it's\n; CREATE TABLE x --'",
            "SELECT 1 /* it's */ ; DROP TABLE t /*'*/",
            'SELECT "CREATE" FROM t',
            "SELECT %SYSTEM.Version.GetNumber()",
            "SELECT 'a' || $SYSTEM.Util.X()",
            "INSERT INTO t VALUES ('SELECT')",
        ):
            with self.subTest(sql=sql):
                self.assertIsNotNone(_read_sql_policy_violation(sql))


class RuntimeAuditTests(HermeticTestCase):
    def setUp(self):
        # Created before the hermetic environment clears TEMP/TMP.
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        super().setUp()

    def real_writer(self, directory):
        return (patch.dict(os.environ, {"ISC_AI_AUDIT_DIR": str(directory)}),
                patch("builtins.open", _REAL_OPEN),
                patch("isc_ai.server._append_runtime_audit", side_effect=_REAL_APPEND))

    def test_the_default_audit_directory_follows_the_state_directory(self):
        home = Path(self.tempdir.name)
        environ = {key: value for key, value in os.environ.items() if key != "ISC_AI_AUDIT_DIR"}
        with patch.dict(os.environ, environ, clear=True), patch("pathlib.Path.home", return_value=home):
            self.assertEqual(server_module._runtime_audit_path(), home / ".isc-ai" / "audit" / "runtime.jsonl")
            # An existing legacy trail keeps growing in place until 'isc-ai migrate'.
            (home / ".iris-mcp" / "audit").mkdir(parents=True)
            self.assertEqual(server_module._runtime_audit_path(), home / ".iris-mcp" / "audit" / "runtime.jsonl")

    def test_start_and_end_records_are_written_without_credentials_or_output(self):
        directory = Path(self.tempdir.name) / "nested" / "audit"
        client = routed_client()
        env, opener, writer = self.real_writer(directory)
        calls = []
        with env, opener, writer, patch.object(
                AtelierClient, "_send_request", autospec=True,
                side_effect=lifecycle_responder(calls, output="OUTPUT-MARKER-123")), \
                patch("uuid.uuid4", return_value=UUID):
            self.assertEqual(client._execute_direct_objectscript("Write 1"), "OUTPUT-MARKER-123")
            text = (directory / "runtime.jsonl").read_text(encoding="utf-8")
        start, end = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(start["event"], "start")
        self.assertEqual(end["event"], "end")
        for record in (start, end):
            self.assertEqual(record["run_id"], RUN_ID)
            self.assertEqual(record["host"], REF["clinical_vip"])
            self.assertEqual(record["target_namespace"], UAT)
            self.assertEqual(record["runner_namespace"], "USER")
            self.assertEqual(record["role"], "UAT")
            self.assertEqual(len(record["code_sha256"]), 64)
            self.assertTrue(record["ts"].endswith("Z"))
        self.assertEqual(start["code"], "Write 1")
        self.assertEqual(start["code_length"], 7)
        self.assertEqual(end["outcome"], "ok")
        self.assertIsInstance(end["duration_ms"], int)
        self.assertNotIn("OUTPUT-MARKER-123", text)
        self.assertNotIn("New $NAMESPACE", text)
        self.assertNotIn(base64.b64encode(b"synthetic:synthetic").decode("ascii"), text)
        self.assertNotIn("password", text)
        self.assertNotIn("Authorization", text)

    def test_unwritable_audit_directory_blocks_before_any_request(self):
        blocker = Path(self.tempdir.name) / "blocker"
        blocker.write_bytes(b"")
        for directory in (blocker / "sub", "relative-audit-dir"):
            with self.subTest(directory=directory):
                client = synthetic_client()
                env, opener, writer = self.real_writer(directory)
                with env, opener, writer, patch("uuid.uuid4", return_value=UUID):
                    with self.assertRaisesRegex(LocalPolicyError, "audit record could not be written"):
                        client._execute_direct_objectscript("Write 1")
                client._send_request.assert_not_called()
        self.assertFalse(Path("relative-audit-dir").exists())

    def test_end_record_failure_is_logged_not_raised(self):
        records = []

        def append(record):
            if record["event"] == "end":
                raise OSError("disk full")
            records.append(record)

        client = routed_client(policy=policy_with())
        with patch("isc_ai.server._append_runtime_audit", side_effect=append), \
                patch("isc_ai.server.log_debug") as log, \
                patch.object(AtelierClient, "_send_request", autospec=True,
                             side_effect=lifecycle_responder([])), \
                patch("uuid.uuid4", return_value=UUID):
            self.assertEqual(client._execute_direct_objectscript("Write 1"), "synthetic-output")
        self.assertEqual([record["event"] for record in records], ["start"])
        log.assert_called_once()

    def test_each_line_is_one_append_write_inside_the_lock(self):
        directory = Path(self.tempdir.name) / "locked"
        events = []
        real_write = os.write

        def write(fd, data):
            events.append(("write", data))
            return real_write(fd, data)

        env, opener, writer = self.real_writer(directory)
        with env, opener, writer, \
                patch("isc_ai.server._audit_try_lock", side_effect=lambda fd: events.append(("lock",))), \
                patch("isc_ai.server._audit_unlock", side_effect=lambda fd: events.append(("unlock",))), \
                patch("os.write", side_effect=write):
            server_module._append_runtime_audit({"event": "start", "code": "Write 1"})
            server_module._append_runtime_audit({"event": "end", "outcome": "ok"})
            lines = (directory / "runtime.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([event[0] for event in events], ["lock", "write", "unlock"] * 2)
        for _, data in (event for event in events if event[0] == "write"):
            self.assertTrue(data.endswith(b"\n"))
            self.assertEqual(data.count(b"\n"), 1)
        self.assertEqual([json.loads(line)["event"] for line in lines], ["start", "end"])

    def test_lock_is_retried_within_the_bound(self):
        directory = Path(self.tempdir.name) / "retry"
        env, opener, writer = self.real_writer(directory)
        with env, opener, writer, \
                patch("isc_ai.server._audit_try_lock", side_effect=[OSError("busy"), OSError("busy"), None]) as lock, \
                patch("isc_ai.server._audit_unlock"), patch("time.sleep") as sleep:
            server_module._append_runtime_audit({"event": "start"})
        self.assertEqual(lock.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_start_lock_failure_blocks_before_any_request(self):
        client = synthetic_client()
        env, opener, writer = self.real_writer(Path(self.tempdir.name) / "busy")
        with env, opener, writer, patch("uuid.uuid4", return_value=UUID), \
                patch("isc_ai.server._AUDIT_LOCK_TIMEOUT_S", 0.0), \
                patch("isc_ai.server._audit_try_lock", side_effect=OSError("held elsewhere")):
            with self.assertRaisesRegex(LocalPolicyError, "audit record could not be written"):
                client._execute_direct_objectscript("Write 1")
        client._send_request.assert_not_called()

    def test_real_platform_lock_contention_times_out(self):
        # A second descriptor holding the platform lock (msvcrt byte lock / flock)
        # makes the writer give up after the bound instead of interleaving.
        directory = Path(self.tempdir.name) / "contended"
        directory.mkdir()
        path = directory / "runtime.jsonl"
        env, opener, writer = self.real_writer(directory)
        holder = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        try:
            server_module._audit_try_lock(holder)
            with env, opener, writer, patch("isc_ai.server._AUDIT_LOCK_TIMEOUT_S", 0.2):
                with self.assertRaisesRegex(LocalPolicyError, "lock could not be obtained"):
                    server_module._append_runtime_audit({"event": "start"})
            server_module._audit_unlock(holder)
        finally:
            os.close(holder)
        self.assertEqual(path.stat().st_size, 0)

    def test_end_lock_failure_is_logged_not_raised(self):
        calls = {"n": 0}

        def try_lock(fd):
            calls["n"] += 1
            if calls["n"] > 1:
                raise OSError("held elsewhere")

        client = routed_client(policy=policy_with())
        env, opener, writer = self.real_writer(Path(self.tempdir.name) / "endlock")
        with env, opener, writer, patch("isc_ai.server._AUDIT_LOCK_TIMEOUT_S", 0.0), \
                patch("isc_ai.server._audit_try_lock", side_effect=try_lock), \
                patch("isc_ai.server._audit_unlock"), \
                patch("isc_ai.server.log_debug") as log, \
                patch.object(AtelierClient, "_send_request", autospec=True,
                             side_effect=lifecycle_responder([])), \
                patch("uuid.uuid4", return_value=UUID):
            self.assertEqual(client._execute_direct_objectscript("Write 1"), "synthetic-output")
        log.assert_called_once()

    def test_posix_permissions_are_best_effort_and_skipped_on_windows(self):
        for is_windows in (False, True):
            with self.subTest(is_windows=is_windows):
                directory = Path(self.tempdir.name) / f"perm-{is_windows}"
                env, opener, writer = self.real_writer(directory)
                with env, opener, writer, patch("isc_ai.server._IS_WINDOWS", is_windows), \
                        patch("isc_ai.server._audit_try_lock"), patch("isc_ai.server._audit_unlock"), \
                        patch("os.chmod", side_effect=OSError("unsupported")) as chmod:
                    server_module._append_runtime_audit({"event": "start"})
                if is_windows:
                    chmod.assert_not_called()
                else:
                    self.assertEqual([call.args for call in chmod.call_args_list], [
                        (directory, 0o700), (directory / "runtime.jsonl", 0o600)])


class RunnerTransportErrorTests(HermeticTestCase):
    UNREACHABLE = ("Temporary executor could not reach the server (transport timeout/unreachable); "
                   "payload was not executed.")

    def run_with(self, respond):
        client = synthetic_client()
        calls = []

        def send(endpoint, method="GET", payload=None, **kwargs):
            calls.append((endpoint, method, payload))
            return respond(endpoint, method, payload)

        client._send_request.side_effect = send
        with patch("uuid.uuid4", return_value=UUID):
            with self.assertRaises(LocalPolicyError) as raised:
                client._execute_direct_objectscript("Write 1")
        return str(raised.exception), calls

    def test_residue_precheck_timeout_is_unreachable_and_never_puts(self):
        for response in ((504, {"error": "Atelier client timeout"}), (500, {"error": "Atelier transport error"})):
            with self.subTest(response=response):
                message, calls = self.run_with(lambda endpoint, method, payload: response)
                self.assertEqual(message, self.UNREACHABLE)
                self.assertEqual([call[1] for call in calls], ["POST"])
                self.assertEqual(self.runtime_audit_records[-1]["outcome"], self.UNREACHABLE)

    def test_absence_precheck_timeout_is_unreachable_and_never_puts(self):
        def respond(endpoint, method, payload):
            if endpoint == "/action/query":
                return envelope([])
            return 504, {"error": "Atelier client timeout"}

        message, calls = self.run_with(respond)
        self.assertEqual(message, self.UNREACHABLE)
        self.assertNotIn("PUT", [call[1] for call in calls])

    def test_server_side_failures_keep_their_messages(self):
        message, _ = self.run_with(lambda e, m, p: (500, {"error": "Atelier HTTP request failed"}))
        self.assertIn("source-control check failed", message)

    def test_post_put_timeout_keeps_unknown_outcome_and_strict_cleanup(self):
        state = {"put": False}

        def respond(endpoint, method, payload):
            if method == "PUT":
                state["put"] = True
                return 201, {}
            if state["put"]:
                return 504, {"error": "Atelier client timeout"}
            if endpoint == "/action/query":
                return envelope([])
            return 404, {}

        message, calls = self.run_with(respond)
        self.assertNotIn("could not reach", message)
        self.assertIn("cleanup failed", message)
        self.assertIn("PUT", [call[1] for call in calls])
