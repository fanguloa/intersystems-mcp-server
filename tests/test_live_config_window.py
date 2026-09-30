"""routes.live_config_window: exceptional, expiring, ticket-bound Ensemble
configuration route on production-flagged targets.

Hermetic: a scripted fake IRIS answers every request; no network, no real config.
The window must never widen READ, RUNTIME, CODE WRITE or CCR gates.
"""

import base64
import datetime
import json
import os
import re
import shutil
import tempfile
import urllib.parse
from pathlib import Path
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape, quoteattr

from tests.hermetic_test_support import (
    AtelierClient, HermeticTestCase, envelope, iris_mcp_config, reference_policy_data,
    synthetic_server,
)
from isc_ai import server as server_module
from isc_ai.ccr_discovery import ACTIVE_NODE_SQL, LocalPolicyError
from isc_ai.config import ConfigError


# Resolved at import, before the hermetic environment clears TEMP/TMP.
_TMP_ROOT = tempfile.gettempdir()

TODAY = datetime.date(2026, 9, 28)
PRIMARY = "10.246.63.196"
BACKUP = "10.246.63.197"
NO_ATELIER = "10.246.63.206"
EVIDENCE_NS = "LIVE-ACHS"
ROUTER_NS = "HMFACHSROUTER"
GATEWAY_NS = "HMFACHSGATEWAY_INT"
TICKET = "ACHSLIVE-42"
LABEL = "ACHS LIVE pre-go-live HMF loopback"
PRODUCTION = "HMF.Router.Production"
ITEM = "HMF.Router.WS"
OTHER_ITEM = "HMF.Router.Other"
SETTING = "WebServiceURL"
OLD = "http://10.246.63.196:57772/old/ws"
NEW = "http://127.0.0.1:57772/hmf/ws"
PASSWORD = "s3cr3t-Pa55word"
RUNNER = "User.McpTmpRun0123456789ab"
REMOVE = object()


def window_data(**changes):
    window = {
        "label": LABEL, "ticket": TICKET, "expires": "2026-10-15",
        "namespaces": [EVIDENCE_NS, ROUTER_NS, GATEWAY_NS], "hosts": [PRIMARY, BACKUP],
        "active_node_evidence_namespace": EVIDENCE_NS,
    }
    window.update(changes)
    return {key: value for key, value in window.items() if value is not REMOVE}


def policy_data(window):
    data = reference_policy_data()
    data["routes"]["direct_ha_nodes"] = list(data["routes"]["direct_ha_nodes"]) + [PRIMARY, BACKUP, NO_ATELIER]
    if window is not None:
        data["routes"]["live_config_window"] = window
    return data


def b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class FakeIris:
    """Scripted IRIS: Atelier REST for the production class, the runner lifecycle,
    active-node evidence per namespace and a simulation of the fixed templates."""

    def __init__(self, settings=None, jobs=None, member=1, primary=True):
        self.settings = dict(settings if settings is not None else {(ITEM, "Host", SETTING): OLD})
        self.table = None              # runner-side view; None -> same as the class
        self.jobs = {EVIDENCE_NS: 7, ROUTER_NS: 0, GATEWAY_NS: 0} if jobs is None else jobs
        self.member, self.primary = member, primary
        self.pending = set()
        self.residue_after_write = False
        self.runner_updates_class = True
        self.change_other_item = False
        self.running = False
        self.runners = {}
        self.runner_codes = []
        self.evidence_namespaces = []
        self.backup_dir = None
        self.backups_at_runner_put = []
        self.client_namespace = None

    def source(self):
        lines = [f"Class {PRODUCTION} Extends Ens.Production", "{", "", "XData ProductionDefinition", "{",
                 f'<Production Name="{PRODUCTION}" LogGeneralTraceEvents="false">']
        for item in sorted({key[0] for key in self.settings} | {ITEM, OTHER_ITEM}):
            enabled = "false" if (self.change_other_item and item == OTHER_ITEM) else "true"
            lines.append(f'  <Item Name={quoteattr(item)} ClassName="X.Y" PoolSize="1" Enabled="{enabled}">')
            for (owner, target, name), value in sorted(self.settings.items()):
                if owner == item:
                    lines.append(f'    <Setting Target="{target}" Name="{name}">{escape(value)}</Setting>')
            lines.append("  </Item>")
        lines += ["</Production>", "}", "", "}"]
        return lines

    def respond(self, endpoint, method="GET", payload=None, **kwargs):
        if endpoint == "/action/query" and method == "POST":
            sql = payload["query"]
            if sql == ACTIVE_NODE_SQL:
                namespace = kwargs.get("namespace") or self.client_namespace
                self.evidence_namespaces.append(namespace)
                return envelope([{"namespace": namespace, "ens_jobs": self.jobs.get(namespace, 0)}])
            if sql == AtelierClient._EPHEMERAL_CHANGE_SQL:
                document = payload["parameters"][0]
                return envelope([{"InternalName": document}] if document in self.pending else [])
            match = re.fullmatch(r"SELECT SQLUser\.(McpTmpRun[0-9a-f]{12})_Execute\(\) AS result", sql)
            if match:
                return envelope([{"result": self.execute("User." + match.group(1))}])
            raise AssertionError("Unscripted SQL")
        if endpoint == "/action/compile":
            assert re.fullmatch(r"User\.McpTmpRun[0-9a-f]{12}\.cls", payload[0]), "only runners compile"
            return 200, {"status": {"errors": [], "summary": ""}, "result": {}}
        match = re.fullmatch(r"/doc/(.+)", endpoint)
        if not match:
            raise AssertionError("Unscripted endpoint")
        document = urllib.parse.unquote(match.group(1))
        if document == PRODUCTION + ".cls":
            assert method == "GET", "the production class is never written over REST"
            return 200, {"status": {"errors": []}, "result": {"content": self.source()}}
        if re.fullmatch(r"User\.McpTmpRun[0-9a-f]{12}\.cls", document):
            name = document[:-4]
            if method == "GET":
                if name in self.runners:
                    return 200, {"status": {"errors": []}, "result": {"content": self.runners[name]}}
                return 404, {"error": "not found"}
            if method == "PUT":
                if self.backup_dir is not None:
                    self.backups_at_runner_put.append(sorted(Path(self.backup_dir).glob("*.cls")))
                self.runners[name] = payload["content"]
                return 200, {"status": {"errors": []}, "result": {}}
            if method == "DELETE":
                self.runners.pop(name, None)
                return 200, {}
        raise AssertionError("Unscripted document request")

    def execute(self, runner):
        content = self.runners[runner]
        start = content.index("ClassMethod Payload()") + 2
        code = "\n".join(line[4:] for line in content[start:-2])
        self.runner_codes.append(code)
        output = self.simulate(code)
        return "MCPOK|" + b64(output)

    def simulate(self, code):
        assert code.splitlines()[0] == server_module._LIVE_WINDOW_PRIMARY_GUARD
        if self.member and not self.primary:
            return server_module._LIVE_WINDOW_NOT_PRIMARY + "\n"
        if "Ens.Config.Production" in code:
            values = [base64.b64decode(item).decode("utf-8")
                      for item in re.findall(r'Base64Decode\("([A-Za-z0-9+/=]*)"\)', code)]
            _, item, target, setting, value, expected = values
            key = (item, target, setting)
            store = self.table if self.table is not None else self.settings
            present, before = key in store, store.get(key, "")
            if before != expected:
                status, after, after_present = "DRIFT", before, present
            else:
                store[key] = value
                if self.runner_updates_class and store is not self.settings:
                    self.settings[key] = value
                elif not self.runner_updates_class and store is self.settings:
                    store[key] = before
                status, after, after_present = "OK", value, True
                if self.residue_after_write:
                    self.pending.add(PRODUCTION + ".cls")
            return (f"MCPLCW|status={status}|member={self.member}|primary={int(self.primary)}"
                    f"|before_present={int(present)}|before={b64(before)}|after_present={int(after_present)}"
                    f"|after={b64(after)}|running={int(self.running)}|message={b64('')}\n")
        if 'Write pName, "|", pState' in code:
            return f"{PRODUCTION}|{1 if self.running else 0}\n"
        return ""


def live_client(policy, fake, namespace=ROUTER_NS, host=PRIMARY, production=True, role="LIVE",
                production_read=True):
    opener = Mock()
    opener.open.side_effect = AssertionError("Unscripted HTTP request")
    with patch.object(AtelierClient, "_create_opener", return_value=opener):
        client = AtelierClient(host, 80, "", namespace, "svc-user", PASSWORD, is_production=production,
                               env_role=role, policy=policy, production_read=production_read)
    fake.client_namespace = client.namespace
    client._send_request = Mock(side_effect=fake.respond)
    client._probe_same_process_ccr_state = Mock(side_effect=AssertionError("CCR guard must not run"))
    return client


def mutating_calls(client):
    calls = []
    for call in client._send_request.call_args_list:
        endpoint = call.args[0]
        method = call.kwargs.get("method", call.args[1] if len(call.args) > 1 else "GET")
        if method in ("PUT", "DELETE") or endpoint == "/action/compile":
            calls.append((method, endpoint))
    return calls


class LiveWindowTestCase(HermeticTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="lcw-", dir=_TMP_ROOT)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        today = patch("isc_ai.server._live_window_today", return_value=TODAY)
        today.start()
        self.addCleanup(today.stop)
        self.audit = []
        self.audit_patcher = patch("isc_ai.server._append_live_window_audit",
                                   side_effect=lambda window, record: self.audit.append(record))
        self.audit_patcher.start()
        self.addCleanup(self.audit_patcher.stop)

    def policy(self, window=None, absent=False):
        data = policy_data(None if absent else (window if window is not None else window_data()))
        return iris_mcp_config.parse_policy(data, self.tmp)

    def backup_dir(self, namespace=ROUTER_NS):
        return Path(self.tmp).resolve() / ".backup" / "live-config-window" / namespace

    def call(self, client, name, arguments, production_read=True, is_production=True):
        servers = {"live": {"host": client.host, "port": 80, "namespace": client.namespace,
                            "env_role": client.env_role, "is_production": is_production,
                            "production_read": production_read, "username": "svc-user",
                            "password": PASSWORD}}
        server = synthetic_server(client, servers=servers, policy=client.policy)
        args = {"server_id": "live", "namespace": client.namespace}
        args.update(arguments)
        return server.handle_tool_call(name, args)

    def set_args(self, **changes):
        args = {"action": "set", "production_name": PRODUCTION, "item_name": ITEM, "target": "Host",
                "setting_name": SETTING, "value": NEW, "expected_current_value": OLD, "ticket": TICKET}
        args.update(changes)
        return {key: value for key, value in args.items() if value is not REMOVE}


class LiveWindowConfigTests(LiveWindowTestCase):
    def test_absent_window_is_none_and_valid_window_is_normalized(self):
        self.assertIsNone(self.policy(absent=True).live_config_window)
        window = self.policy(window_data(namespaces=["hmfachsrouter", "LIVE-ACHS"],
                                         hosts=[PRIMARY.upper()])).live_config_window
        self.assertEqual(window.namespaces, frozenset({ROUTER_NS, EVIDENCE_NS}))
        self.assertEqual(window.hosts, frozenset({PRIMARY}))
        self.assertEqual(window.active_node_evidence_namespace, EVIDENCE_NS)
        self.assertEqual(window.backup_root, Path(self.tmp).resolve() / ".backup" / "live-config-window")
        self.assertIsNone(self.policy(absent=True).live_config_window)

    def test_unknown_missing_and_mistyped_keys_are_rejected(self):
        cases = (
            window_data(extra=True),
            window_data(label=REMOVE),
            window_data(ticket=REMOVE),
            window_data(expires=REMOVE),
            window_data(namespaces=REMOVE),
            window_data(hosts=REMOVE),
            window_data(active_node_evidence_namespace=REMOVE),
            window_data(label=1),
            window_data(ticket=""),
            window_data(ticket=" T "),
            window_data(expires=20261015),
            window_data(namespaces=ROUTER_NS),
            window_data(namespaces=[]),
            window_data(namespaces=[1]),
            window_data(namespaces=["BAD/NS"]),
            window_data(hosts=[]),
            window_data(hosts=PRIMARY),
            window_data(active_node_evidence_namespace=["LIVE-ACHS"]),
        )
        for window in cases:
            with self.subTest(window=window), self.assertRaises(ConfigError):
                self.policy(window)
        for bad in (None, [], "window", 1):
            data = policy_data(None)
            data["routes"]["live_config_window"] = bad
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                iris_mcp_config.parse_policy(data, self.tmp)

    def test_window_host_must_be_a_direct_ha_node(self):
        with self.assertRaisesRegex(ConfigError, "direct_ha_nodes"):
            self.policy(window_data(hosts=[PRIMARY, "10.9.9.9"]))

    def test_no_environment_variable_opens_or_extends_the_window(self):
        env = {"ISC_AI_LIVE_CONFIG_WINDOW": json.dumps(window_data()), "IRIS_LIVE_CONFIG_WINDOW": "1",
               "IRIS_ALLOW_PROD": "1", "ISC_AI_LIVE_WINDOW_EXPIRES": "2099-01-01",
               "IRIS_EXTRA_PACKAGES": "HMFACHSROUTER:HMF"}
        with patch.dict(os.environ, env):
            absent = self.policy(absent=True)
            expired = self.policy(window_data(expires="2026-09-01"))
            fake = FakeIris()
            for policy in (absent, expired):
                with self.subTest(configured=policy.live_config_window is not None):
                    allowed, reason = live_client(policy, fake).is_live_config_allowed()
                    self.assertFalse(allowed, reason)
        self.assertIsNone(absent.live_config_window)
        self.assertEqual(fake.evidence_namespaces, [])


class LiveWindowGateTests(LiveWindowTestCase):
    def test_absent_expired_and_unparsable_windows_are_closed(self):
        fake = FakeIris()
        cases = (
            (self.policy(absent=True), "not configured"),
            (self.policy(window_data(expires="2026-09-27")), "expired"),
            (self.policy(window_data(expires="2026-13-01")), "not a valid"),
            (self.policy(window_data(expires="28-09-2026")), "not a valid"),
            (self.policy(window_data(expires="2026-9-30")), "not a valid"),
        )
        for policy, expected in cases:
            with self.subTest(expected=expected, expires=getattr(policy.live_config_window, "expires", None)):
                allowed, reason = live_client(policy, fake).is_live_config_allowed()
                self.assertFalse(allowed)
                self.assertIn(expected, reason)
        self.assertEqual(fake.evidence_namespaces, [])

    def test_expiry_day_is_inclusive_and_reasons_cite_label_and_ticket(self):
        fake = FakeIris()
        allowed, reason = live_client(self.policy(window_data(expires="2026-09-28")), fake).is_live_config_allowed()
        self.assertTrue(allowed, reason)
        self.assertIn(LABEL, reason)
        self.assertIn(TICKET, reason)
        denied, reason = live_client(self.policy(), fake, namespace="OTHER").is_live_config_allowed()
        self.assertFalse(denied)
        self.assertIn(LABEL, reason)
        self.assertIn(TICKET, reason)

    def test_namespace_and_host_outside_the_window_are_denied(self):
        policy = self.policy(window_data(hosts=[PRIMARY]))
        fake = FakeIris()
        allowed, reason = live_client(policy, fake, namespace="HSLIB").is_live_config_allowed()
        self.assertFalse(allowed)
        self.assertIn("namespace", reason)
        allowed, reason = live_client(policy, fake, host=BACKUP).is_live_config_allowed()
        self.assertFalse(allowed)
        self.assertIn("host", reason)
        self.assertEqual(fake.evidence_namespaces, [])

    def test_non_production_target_gets_nothing_from_the_window(self):
        fake = FakeIris()
        for role in ("BASE", "UAT"):
            with self.subTest(role=role):
                client = live_client(self.policy(), fake, production=False, role=role)
                allowed, reason = client.is_live_config_allowed()
                self.assertFalse(allowed)
                self.assertIn("production targets only", reason)
        self.assertEqual(fake.evidence_namespaces, [])

    def test_window_requires_production_read(self):
        fake = FakeIris()
        allowed, reason = live_client(self.policy(), fake, production_read=False).is_live_config_allowed()
        self.assertFalse(allowed)
        self.assertIn("production_read", reason)

    def test_backup_node_is_denied(self):
        fake = FakeIris(jobs={EVIDENCE_NS: 0, ROUTER_NS: 0})
        allowed, reason = live_client(self.policy(), fake, host=BACKUP).is_live_config_allowed()
        self.assertFalse(allowed)
        self.assertIn("active node", reason)
        self.assertIn(EVIDENCE_NS, reason)
        self.assertEqual(fake.evidence_namespaces, [EVIDENCE_NS])

    def test_evidence_namespace_is_used_even_when_target_production_is_stopped(self):
        for namespace in (ROUTER_NS, GATEWAY_NS):
            with self.subTest(namespace=namespace):
                fake = FakeIris(jobs={EVIDENCE_NS: 60, ROUTER_NS: 0, GATEWAY_NS: 0})
                allowed, reason = live_client(self.policy(), fake, namespace=namespace).is_live_config_allowed()
                self.assertTrue(allowed, reason)
                self.assertEqual(fake.evidence_namespaces, [EVIDENCE_NS])

    def test_active_node_evidence_failure_is_denied(self):
        fake = FakeIris()
        client = live_client(self.policy(), fake)
        client._send_request.side_effect = lambda *a, **k: (500, {"error": "Atelier transport error"})
        self.assertFalse(client.is_live_config_allowed()[0])


class LiveWindowKeepsOtherGatesClosedTests(LiveWindowTestCase):
    def test_client_gates_stay_closed_with_the_window_open(self):
        fake = FakeIris()
        client = live_client(self.policy(), fake, namespace=EVIDENCE_NS)
        self.assertTrue(client.is_live_config_allowed()[0])
        self.assertTrue(client.is_read_allowed()[0])
        self.assertFalse(client.is_runtime_operation_allowed()[0])
        self.assertFalse(client.is_code_write_route_allowed()[0])
        self.assertFalse(client.is_code_write_route_allowed(promotion=True)[0])
        blocked = (
            lambda: client.execute_runtime_code("Write 1"),
            lambda: client._execute_direct_objectscript("Write 1"),
            lambda: client._require_ephemeral_runner(RUNNER),
            lambda: client._run_guarded_ccr_runner("Write 1", "ACHS0875"),
            lambda: client.save_document("Custom.ACHS.X.cls", ["Class X {}"], ccr_id="ACHS0875"),
            lambda: client.compile_document("Custom.ACHS.X.cls", ccr_id="ACHS0875"),
            lambda: client.delete_document("Custom.ACHS.X.cls"),
            lambda: client.run_write_sql("UPDATE X SET Y = 1"),
            lambda: client.dump_global("^X"),
            lambda: client._require_canonical_base_ccr_mutation(),
        )
        for operation in blocked:
            with self.assertRaises((LocalPolicyError, RuntimeError)):
                operation()
        self.assertEqual(mutating_calls(client), [])
        self.assertEqual(fake.runners, {})

    def test_tools_stay_denied_on_production_with_the_window_open(self):
        cases = (
            ("iris_execute_objectscript", {"code": "Write 1"}),
            ("iris_save_class", {"class_name": "Custom.ACHS.Tests.Sample", "content": "Class X {}"}),
            ("iris_patch_class", {"class_name": "Custom.ACHS.Tests.Sample", "old_string": "a", "new_string": "b"}),
            ("iris_compile_class", {"class_name": "Custom.ACHS.Tests.Sample"}),
            ("iris_delete_class", {"class_name": "Custom.ACHS.Tests.Sample"}),
            ("iris_save_document", {"document_name": "Custom.ACHS.X.inc", "content": "x"}),
            ("iris_delete_document", {"document_name": "Custom.ACHS.X.inc"}),
            ("iris_ccr_checkout", {"class_name": "Custom.ACHS.Tests.Sample", "ccr_id": "ACHS0875"}),
            ("iris_ccr_commit", {"change_ids": [1], "ccr_id": "ACHS0875"}),
            ("iris_ccr_create", {"title": "t", "description": "d"}),
            ("iris_ccr_download_load", {"itemset_id": 1}),
            ("iris_unlock_questionnaire", {"win_code": "X"}),
            ("iris_dump_global", {"global_name": "^X"}),
            ("iris_ensemble_queue_control", {"action": "abort_message", "message_id": 1}),
            ("iris_ensemble_queue_control", {"action": "resend_message", "message_id": 1}),
            ("iris_ensemble_default_settings", {"action": "set", "setting_name": "S", "setting_value": "V"}),
            ("iris_ensemble_default_settings", {"action": "delete", "id": 1}),
        )
        for name, arguments in cases:
            with self.subTest(tool=name, arguments=arguments):
                fake = FakeIris()
                client = live_client(self.policy(), fake, namespace=ROUTER_NS)
                result = self.call(client, name, arguments)
                self.assertTrue(result.get("isError"), (name, result))
                self.assertIn("production", result["content"][0]["text"].lower())
                self.assertEqual(mutating_calls(client), [])
                self.assertEqual(fake.runner_codes, [])
                client._probe_same_process_ccr_state.assert_not_called()

    def test_ephemeral_runner_path_requires_an_issued_grant_for_this_target(self):
        policy = self.policy()
        fake = FakeIris()
        client = live_client(policy, fake)
        with self.assertRaises(LocalPolicyError):
            client._require_ephemeral_runner(RUNNER)
        forged = server_module._LiveWindowGrant(LABEL, TICKET, client.base_url)
        with self.assertRaisesRegex(LocalPolicyError, "fresh grant"):
            client._run_live_window_runner(forged, "Write 1")
        grant, _ = client._issue_live_window_grant()
        other = live_client(policy, FakeIris(), namespace=GATEWAY_NS)
        other._live_window_runner = (RUNNER, grant)
        with self.assertRaises(LocalPolicyError):
            other._require_ephemeral_runner(RUNNER)
        with self.assertRaisesRegex(LocalPolicyError, "fresh grant"):
            other._run_live_window_runner(grant, "Write 1")
        with patch("isc_ai.server._LIVE_WINDOW_GRANT_TTL_S", -1):
            with self.assertRaisesRegex(LocalPolicyError, "fresh grant"):
                client._run_live_window_runner(grant, "Write 1")
        self.assertEqual(mutating_calls(client), [])
        output = client._run_live_window_runner(grant, "Write 1")
        self.assertEqual(output, "")
        self.assertIsNone(client._live_window_runner)
        self.assertEqual(fake.runners, {})
        with self.assertRaises(LocalPolicyError):
            client._require_ephemeral_runner(RUNNER)

    def test_runner_is_stopped_in_process_on_a_non_primary_member(self):
        fake = FakeIris(primary=False)
        client = live_client(self.policy(), fake)
        grant, _ = client._issue_live_window_grant()
        with self.assertRaisesRegex(LocalPolicyError, "not on the mirror primary"):
            client._run_live_window_runner(grant, "Write 1")
        self.assertEqual(fake.runners, {})


class LiveWindowEscapingTests(LiveWindowTestCase):
    EVIL = (
        '"', '""', '" Write 1 "', '"_$C(10)_"', "$c(34)", "$C(34,95,34)", "line1\nline2", "a\r\nb",
        "_", '"_$ZF(-1,"calc")_"', "} Kill ^X {", '") Do ##class(%SYSTEM.OBJ).Delete("X") //',
        "\t|status=OK|", "ñandú 😀", "", "MCPLCW|status=OK", "' Quit", "&sql(DELETE FROM X)",
    )
    EXPR_RE = re.compile(r'\$ZConvert\(\$System\.Encryption\.Base64Decode\("([A-Za-z0-9+/]*={0,2})"\),"I","UTF8"\)')

    def test_string_expression_is_strict_and_round_trips(self):
        for value in self.EVIL:
            with self.subTest(value=value):
                expression = server_module._cos_string_expr(value)
                match = self.EXPR_RE.fullmatch(expression)
                self.assertIsNotNone(match, expression)
                self.assertEqual(base64.b64decode(match.group(1)).decode("utf-8"), value)
                self.assertNotIn("\n", expression)
                self.assertEqual(expression.count('"'), 6)
        for bad in (None, 1, b"x", "\ud800"):
            with self.subTest(bad=bad), self.assertRaises(LocalPolicyError):
                server_module._cos_string_expr(bad)

    def test_template_structure_never_depends_on_caller_values(self):
        def skeleton(code):
            return self.EXPR_RE.sub("VALUE", code)

        benign = server_module._live_window_item_setting_code(PRODUCTION, ITEM, "Host", SETTING, NEW, OLD)
        self.assertEqual(len(self.EXPR_RE.findall(benign)), 6)
        for value in self.EVIL:
            with self.subTest(value=value):
                code = server_module._live_window_item_setting_code(PRODUCTION, value, "Host", SETTING, value, value)
                self.assertEqual(skeleton(code), skeleton(benign))
                if len(value) > 3:
                    self.assertNotIn(value, code)

    def test_template_uses_only_the_documented_apis(self):
        code = server_module._live_window_item_setting_code(PRODUCTION, ITEM, "Host", SETTING, NEW, OLD)
        for api in ("$SYSTEM.Mirror.IsMember()", "$SYSTEM.Mirror.IsPrimary()",
                    "##class(Ens.Config.Production).%OpenId(", ".FindItemByConfigName(",
                    "mcpProduction.%Save()", "mcpProduction.SaveToClass(mcpItem)",
                    "##class(Ens.Config.Setting).%New()"):
            self.assertIn(api, code)
        self.assertNotRegex(code, r"\^Ens|\^\^|\$ZF|Kill \^|UpdateProduction|RestartProduction|StartProduction")

    def test_result_parser_rejects_malformed_records(self):
        good = (f"MCPLCW|status=OK|member=1|primary=1|before_present=1|before={b64(OLD)}"
                f"|after_present=1|after={b64(NEW)}|running=0|message=")
        parsed = server_module._parse_live_window_result(good + "\n")
        self.assertEqual((parsed["before"], parsed["after"], parsed["status"]), (OLD, NEW, "OK"))
        for bad in ("", good + "\n" + good, good.replace("|running=0", ""), good + "|extra=1",
                    good.replace("member=1", "member=x"), good.replace(b64(NEW), "%%%"), "garbage"):
            with self.subTest(bad=bad), self.assertRaises(LocalPolicyError):
                server_module._parse_live_window_result(bad)
        with self.assertRaises(server_module._LiveWindowNotPrimary):
            server_module._parse_live_window_result(server_module._LIVE_WINDOW_NOT_PRIMARY)


class LiveWindowItemSettingTests(LiveWindowTestCase):
    def test_get_uses_the_read_gate_without_runner_or_audit(self):
        fake = FakeIris()
        client = live_client(self.policy(), fake)
        result = self.call(client, "iris_ensemble_item_setting", {
            "action": "get", "production_name": PRODUCTION, "item_name": ITEM,
            "target": "Host", "setting_name": SETTING})
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"]["value"], OLD)
        self.assertTrue(result["structuredContent"]["present"])
        self.assertEqual(fake.runner_codes, [])
        self.assertEqual(fake.evidence_namespaces, [])
        self.assertEqual(self.audit, [])
        absent = self.call(client, "iris_ensemble_item_setting", {
            "action": "get", "production_name": PRODUCTION, "item_name": ITEM,
            "target": "Adapter", "setting_name": SETTING})
        self.assertFalse(absent["structuredContent"]["present"])

    def test_get_without_production_read_is_denied(self):
        fake = FakeIris()
        client = live_client(self.policy(), fake, production_read=False)
        result = self.call(client, "iris_ensemble_item_setting", {
            "action": "get", "production_name": PRODUCTION, "item_name": ITEM,
            "target": "Host", "setting_name": SETTING}, production_read=False)
        self.assertTrue(result.get("isError"))
        client._send_request.assert_not_called()

    def test_set_writes_backup_first_then_verifies_and_audits(self):
        fake = FakeIris()
        fake.backup_dir = self.backup_dir()
        fake.running = True
        client = live_client(self.policy(), fake)
        original = fake.source()
        result = self.call(client, "iris_ensemble_item_setting", self.set_args())
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(fake.settings[(ITEM, "Host", SETTING)], NEW)
        structured = result["structuredContent"]
        self.assertEqual(structured["result"], "OK")
        self.assertTrue(structured["verified_value_matches"])
        self.assertTrue(structured["other_settings_unchanged"])
        self.assertEqual(structured["source_control_change_row"], "NONE")
        self.assertIn("restart item", result["content"][0]["text"])
        # Backup existed before the runner was uploaded and holds the original class.
        self.assertEqual(len(fake.backups_at_runner_put), 1)
        self.assertEqual(len(fake.backups_at_runner_put[0]), 1)
        backup = fake.backups_at_runner_put[0][0]
        self.assertEqual(Path(structured["backup"]), backup)
        self.assertTrue(backup.name.startswith(PRODUCTION + "-"))
        data = os.read(os.open(str(backup), os.O_RDONLY | getattr(os, "O_BINARY", 0)), 1 << 20)
        self.assertEqual(data.decode("utf-8"), "\n".join(original) + "\n")
        self.assertIn(OLD, data.decode("utf-8"))
        # Evidence came from the evidence namespace; runner cleaned up.
        self.assertEqual(fake.evidence_namespaces, [EVIDENCE_NS])
        self.assertEqual(fake.runners, {})
        self.assertEqual(len(fake.runner_codes), 1)
        # Audit: exactly one line, all fields, no credentials.
        self.assertEqual(len(self.audit), 1)
        record = self.audit[0]
        for key in ("ts", "server_id", "host", "namespace", "tool", "action", "production", "item",
                    "target", "setting", "before", "after", "ticket", "result", "reason"):
            self.assertIn(key, record)
        self.assertEqual((record["before"], record["after"], record["ticket"], record["result"]),
                         (OLD, NEW, TICKET, "OK"))
        self.assertEqual((record["host"], record["namespace"]), (PRIMARY, ROUTER_NS))
        serialized = json.dumps(record)
        self.assertNotIn(PASSWORD, serialized)
        self.assertNotIn(client.auth_header.split()[1], serialized)
        client._probe_same_process_ccr_state.assert_not_called()

    def test_backup_is_never_overwritten(self):
        window = self.policy().live_config_window
        stamp = "20260928T101010123456Z"
        path, _ = server_module._live_window_write_backup(window.backup_root, ROUTER_NS, PRODUCTION, ["first"], stamp=stamp)
        with self.assertRaisesRegex(LocalPolicyError, "overwrite"):
            server_module._live_window_write_backup(window.backup_root, ROUTER_NS, PRODUCTION, ["second"], stamp=stamp)
        data = os.read(os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0)), 100)
        self.assertEqual(data, b"first\n")
        for production in ("..\\..\\evil", "%Evil", "a/b"):
            with self.subTest(production=production), self.assertRaises(LocalPolicyError):
                server_module._live_window_write_backup(window.backup_root, ROUTER_NS, production, ["x"])

    def test_set_rejections_happen_before_any_write_and_are_audited(self):
        cases = (
            (self.set_args(expected_current_value=REMOVE), "expected_current_value"),
            (self.set_args(value=REMOVE), "'value'"),
            (self.set_args(ticket=REMOVE), "ticket"),
            (self.set_args(ticket="ACHSLIVE-43"), "ticket"),
            (self.set_args(value="bad\x00value"), "cannot hold"),
        )
        for arguments, expected in cases:
            with self.subTest(expected=expected):
                self.audit.clear()
                fake = FakeIris()
                client = live_client(self.policy(), fake)
                result = self.call(client, "iris_ensemble_item_setting", arguments)
                self.assertTrue(result.get("isError"))
                self.assertIn(expected, result["content"][0]["text"])
                self.assertEqual(mutating_calls(client), [])
                self.assertEqual(fake.settings[(ITEM, "Host", SETTING)], OLD)
                self.assertEqual(len(self.audit), 1)
                self.assertEqual(self.audit[0]["result"], "DENIED")
                self.assertFalse(self.backup_dir().exists())

    def test_drift_in_class_xdata_aborts_without_backup_or_runner(self):
        fake = FakeIris(settings={(ITEM, "Host", SETTING): "http://someone-else/ws"})
        client = live_client(self.policy(), fake)
        result = self.call(client, "iris_ensemble_item_setting", self.set_args())
        self.assertTrue(result.get("isError"))
        self.assertEqual(result["structuredContent"]["result"], "DRIFT")
        self.assertEqual(mutating_calls(client), [])
        self.assertFalse(self.backup_dir().exists())
        self.assertEqual(self.audit[0]["result"], "DRIFT")
        self.assertEqual(self.audit[0]["before"], "http://someone-else/ws")

    def test_in_process_drift_aborts_without_writing(self):
        fake = FakeIris()
        fake.table = {(ITEM, "Host", SETTING): "http://table-differs/ws"}
        client = live_client(self.policy(), fake)
        result = self.call(client, "iris_ensemble_item_setting", self.set_args())
        self.assertTrue(result.get("isError"))
        self.assertEqual(result["structuredContent"]["result"], "DRIFT")
        self.assertEqual(fake.settings[(ITEM, "Host", SETTING)], OLD)
        self.assertEqual(fake.table[(ITEM, "Host", SETTING)], "http://table-differs/ws")
        self.assertEqual(self.audit[-1]["result"], "DRIFT")

    def test_absent_setting_is_added_only_when_expected_is_empty(self):
        fake = FakeIris(settings={})
        client = live_client(self.policy(), fake)
        result = self.call(client, "iris_ensemble_item_setting",
                           self.set_args(setting_name="StorageNamespace", value="HMFACHSROUTER",
                                         expected_current_value=""))
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(fake.settings[(ITEM, "Host", "StorageNamespace")], "HMFACHSROUTER")

    def test_backup_node_and_non_production_targets_are_denied_and_audited(self):
        fake = FakeIris(jobs={EVIDENCE_NS: 0})
        client = live_client(self.policy(), fake, host=BACKUP)
        result = self.call(client, "iris_ensemble_item_setting", self.set_args())
        self.assertTrue(result.get("isError"))
        self.assertIn("active node", result["content"][0]["text"])
        self.assertEqual(self.audit[-1]["result"], "DENIED")
        self.assertEqual(mutating_calls(client), [])

        fake = FakeIris()
        client = live_client(self.policy(), fake, production=False, role="UAT")
        result = self.call(client, "iris_ensemble_item_setting", self.set_args(), is_production=False)
        self.assertTrue(result.get("isError"))
        self.assertIn("production targets only", result["content"][0]["text"])
        self.assertEqual(mutating_calls(client), [])

    def test_set_without_window_is_denied(self):
        fake = FakeIris()
        client = live_client(self.policy(absent=True), fake)
        result = self.call(client, "iris_ensemble_item_setting", self.set_args())
        self.assertTrue(result.get("isError"))
        self.assertIn("not configured", result["content"][0]["text"])
        client._send_request.assert_not_called()

    def test_pending_source_control_change_blocks_before_backup(self):
        fake = FakeIris()
        fake.pending.add(PRODUCTION + ".cls")
        client = live_client(self.policy(), fake)
        result = self.call(client, "iris_ensemble_item_setting", self.set_args())
        self.assertTrue(result.get("isError"))
        self.assertIn("source-control", result["content"][0]["text"])
        self.assertEqual(mutating_calls(client), [])
        self.assertFalse(self.backup_dir().exists())

    def test_residue_created_by_the_write_is_reported_not_undone(self):
        fake = FakeIris()
        fake.residue_after_write = True
        client = live_client(self.policy(), fake)
        result = self.call(client, "iris_ensemble_item_setting", self.set_args())
        self.assertEqual(result["structuredContent"]["source_control_change_row"], "PRESENT")
        self.assertIn("PRESENT", result["content"][0]["text"])
        self.assertIn(PRODUCTION + ".cls", fake.pending)

    def test_verification_failures_are_loud(self):
        fake = FakeIris()
        fake.runner_updates_class = False
        client = live_client(self.policy(), fake)
        result = self.call(client, "iris_ensemble_item_setting", self.set_args())
        self.assertTrue(result.get("isError"))
        self.assertIn("FAILED", result["content"][0]["text"])
        self.assertEqual(self.audit[-1]["result"], "FAILED")

        fake = FakeIris()
        client = live_client(self.policy(), fake)
        original_simulate = fake.simulate

        def simulate_and_change_other_item(code):
            output = original_simulate(code)
            fake.change_other_item = True
            return output

        fake.simulate = simulate_and_change_other_item
        result = self.call(client, "iris_ensemble_item_setting", self.set_args())
        self.assertTrue(result.get("isError"))
        self.assertFalse(result["structuredContent"]["other_settings_unchanged"])

    def test_not_primary_in_process_is_denied_without_payload(self):
        fake = FakeIris(primary=False)
        client = live_client(self.policy(), fake)
        result = self.call(client, "iris_ensemble_item_setting", self.set_args())
        self.assertTrue(result.get("isError"))
        self.assertEqual(result["structuredContent"]["result"], "DENIED")
        self.assertEqual(fake.settings[(ITEM, "Host", SETTING)], OLD)
        self.assertEqual(fake.runners, {})

    def test_sensitive_setting_values_are_hashed_in_the_audit(self):
        fake = FakeIris(settings={(ITEM, "Host", "HTTPPassword"): "old-pw"})
        client = live_client(self.policy(), fake)
        self.call(client, "iris_ensemble_item_setting",
                  self.set_args(setting_name="HTTPPassword", value="new-pw", expected_current_value="old-pw"))
        serialized = json.dumps(self.audit[-1])
        self.assertNotIn("old-pw", serialized)
        self.assertNotIn("new-pw", serialized)
        self.assertIn("sha256:", serialized)

    def test_real_audit_writer_appends_json_lines_without_credentials(self):
        self.audit_patch_stop()
        fake = FakeIris()
        client = live_client(self.policy(), fake)
        self.call(client, "iris_ensemble_item_setting", self.set_args(ticket="WRONG"))
        self.call(client, "iris_ensemble_item_setting", self.set_args())
        path = Path(self.tmp).resolve() / ".backup" / "live-config-window" / "audit.jsonl"
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            data = os.read(fd, 1 << 20).decode("utf-8")
        finally:
            os.close(fd)
        records = [json.loads(line) for line in data.splitlines()]
        self.assertEqual([record["result"] for record in records], ["DENIED", "OK"])
        self.assertNotIn(PASSWORD, data)

    def audit_patch_stop(self):
        self.audit_patcher.stop()


class LiveWindowRuntimeActionTests(LiveWindowTestCase):
    def test_component_actions_and_status_run_under_the_window(self):
        for name, arguments in (
                ("iris_ensemble_component_control", {"action": "restart", "component_name": ITEM}),
                ("iris_ensemble_component_control", {"action": "disable", "component_name": ITEM}),
                ("iris_ensemble_production_control", {"action": "status"}),
                ("iris_ensemble_production_control", {"action": "stop"}),
        ):
            with self.subTest(tool=name, arguments=arguments):
                self.audit.clear()
                fake = FakeIris()
                client = live_client(self.policy(), fake)
                result = self.call(client, name, arguments)
                self.assertFalse(result.get("isError", False), result)
                self.assertEqual(len(fake.runner_codes), 1)
                self.assertEqual(fake.evidence_namespaces, [EVIDENCE_NS])
                self.assertEqual(fake.runners, {})
                self.assertEqual(len(self.audit), 1)
                self.assertEqual(self.audit[0]["result"], "OK")
                self.assertEqual(self.audit[0]["tool"], name)

    def test_component_name_is_escaped_in_runtime_actions(self):
        fake = FakeIris()
        client = live_client(self.policy(), fake)
        evil = '") Kill ^X Write ("'
        self.call(client, "iris_ensemble_component_control", {"action": "enable", "component_name": evil})
        self.assertNotIn(evil, fake.runner_codes[0])

    def test_runtime_actions_denied_outside_window_conditions(self):
        fake = FakeIris(jobs={EVIDENCE_NS: 0})
        client = live_client(self.policy(), fake, host=BACKUP)
        result = self.call(client, "iris_ensemble_component_control", {"action": "restart", "component_name": ITEM})
        self.assertTrue(result.get("isError"))
        self.assertEqual(self.audit[-1]["result"], "DENIED")
        self.assertEqual(mutating_calls(client), [])

        fake = FakeIris()
        client = live_client(self.policy(window_data(expires="2026-01-01")), fake)
        result = self.call(client, "iris_ensemble_production_control", {"action": "status"})
        self.assertTrue(result.get("isError"))
        self.assertIn("expired", result["content"][0]["text"])
        self.assertEqual(mutating_calls(client), [])

    def test_list_and_non_production_paths_are_unchanged(self):
        fake = FakeIris()
        client = live_client(self.policy(), fake)
        client._send_request.side_effect = None
        client._send_request.return_value = envelope([])
        result = self.call(client, "iris_ensemble_component_control", {"action": "list"})
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(self.audit, [])

        uat = live_client(self.policy(), FakeIris(), production=False, role="UAT")
        uat.execute_runtime_code = Mock(return_value="")
        result = self.call(uat, "iris_ensemble_component_control",
                           {"action": "restart", "component_name": ITEM}, is_production=False)
        self.assertFalse(result.get("isError", False), result)
        uat.execute_runtime_code.assert_called_once()
        self.assertEqual(self.audit, [])


class LiveWindowRulesFileParityTests(LiveWindowTestCase):
    """Gap fix: an ensemble_control exception defined purely in isc-ai.rules.json
    (no legacy routes.live_config_window at all) must audit and back up exactly
    like the legacy window. Required before 'rules migrate' can move ACHS's
    live_config_window into isc-ai.rules.json without losing this behavior."""

    def rules_only_policy(self):
        import dataclasses
        from isc_ai import rules as isc_rules
        base = self.policy(absent=True)
        ruleset = isc_rules.parse_rules({"version": 1, "exceptions": [{
            "id": "rules-live-window", "label": LABEL, "ticket": TICKET, "expires": "2026-10-15",
            "capability": "ensemble_control", "namespaces": [EVIDENCE_NS, ROUTER_NS, GATEWAY_NS],
            "hosts": [PRIMARY, BACKUP], "active_node_evidence_namespace": EVIDENCE_NS,
        }]})
        return dataclasses.replace(base, rules=ruleset)

    def _audit_and_backup(self, policy, name, arguments):
        fake = FakeIris()
        client = live_client(policy, fake)
        result = self.call(client, name, arguments)
        self.assertFalse(result.get("isError", False), result)
        return list(self.audit)

    def test_item_setting_set_audits_and_backs_up_identically_to_the_legacy_window(self):
        legacy_audit = self._audit_and_backup(self.policy(), "iris_ensemble_item_setting", self.set_args())
        self.assertTrue(self.backup_dir().exists())
        for entry in list(self.backup_dir().glob("*.cls")):
            entry.unlink()
        self.audit.clear()

        rules_audit = self._audit_and_backup(self.rules_only_policy(), "iris_ensemble_item_setting", self.set_args())
        self.assertTrue(self.backup_dir().exists())

        self.assertEqual(len(legacy_audit), 1)
        self.assertEqual(len(rules_audit), 1)
        ignored = {"ts", "backup"}
        legacy_record = {key: value for key, value in legacy_audit[0].items() if key not in ignored}
        rules_record = {key: value for key, value in rules_audit[0].items() if key not in ignored}
        self.assertEqual(legacy_record, rules_record)

    def test_ensemble_control_actions_audit_identically(self):
        legacy_audit = self._audit_and_backup(self.policy(), "iris_ensemble_production_control",
                                              {"action": "status"})
        self.audit.clear()
        rules_audit = self._audit_and_backup(self.rules_only_policy(), "iris_ensemble_production_control",
                                             {"action": "status"})

        self.assertEqual(len(legacy_audit), 1)
        self.assertEqual(len(rules_audit), 1)
        ignored = {"ts"}
        legacy_record = {key: value for key, value in legacy_audit[0].items() if key not in ignored}
        rules_record = {key: value for key, value in rules_audit[0].items() if key not in ignored}
        self.assertEqual(legacy_record, rules_record)

    def test_dispatch_routes_runtime_actions_for_a_rules_file_only_exception(self):
        # Regression for the dispatch precheck: without this fix the tool call would
        # fall through to the RUNTIME gate instead of the LIVE CONFIG WINDOW gate.
        fake = FakeIris(jobs={EVIDENCE_NS: 0})
        client = live_client(self.rules_only_policy(), fake, host=BACKUP)
        result = self.call(client, "iris_ensemble_component_control",
                           {"action": "restart", "component_name": ITEM})
        self.assertTrue(result.get("isError"))
        self.assertIn("active node", result["content"][0]["text"])
        self.assertEqual(self.audit[-1]["result"], "DENIED")
