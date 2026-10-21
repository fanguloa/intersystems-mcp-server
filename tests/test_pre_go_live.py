"""isc-ai.rules.json capability "pre_go_live": lifts the production gate on a
production-flagged environment that is not yet in clinical use.

Hermetic: synthetic clients answer only scripted metadata queries. The exception
must stay closed unless every condition holds (scope, expiry, direct HA node,
production_read and fresh active-node evidence), and it never grants core
packages, SUSPENDED CCR or non-production targets anything new.
"""

import dataclasses
import datetime
from unittest.mock import patch

from tests.hermetic_test_support import HermeticTestCase, REFERENCE_POLICY, guard_record, synthetic_client
from isc_ai import rules as isc_rules
from isc_ai import server as server_module
from isc_ai.ccr_discovery import LocalPolicyError
from isc_ai.config import ConfigError

TODAY = datetime.date(2026, 10, 16)
PRIMARY = "10.246.63.196"
BACKUP = "10.246.63.197"
NOT_A_NODE = "sistemaclinico.invalid"
LIVE_NS = "LIVE-ACHS"
ROUTER_NS = "HMFACHSROUTER"


def exception_data(**changes):
    data = {
        "id": "live-pre-go-live", "label": "ACHS LIVE pre-go-live full access", "ticket": "TCK-LIVE",
        "expires": "2026-11-15", "capability": "pre_go_live", "roles": ["LIVE"],
        "hosts": [PRIMARY, BACKUP], "namespaces": [LIVE_NS, ROUTER_NS],
        "active_node_evidence_namespace": LIVE_NS,
    }
    data.update(changes)
    return {key: value for key, value in data.items() if value is not None}


def backup_exception_data(**changes):
    data = {
        "id": "live-backup-config", "label": "ACHS LIVE backup node configuration", "ticket": "TCK-BKP",
        "expires": (datetime.date.today() + datetime.timedelta(days=3)).isoformat(),
        "capability": "pre_go_live", "roles": ["LIVE"], "hosts": [BACKUP], "namespaces": [LIVE_NS],
        "requires_active_node": False, "active_node_evidence_namespace": None,
    }
    return exception_data(**dict(data, **changes))


def live_policy(exceptions=None, packages=None):
    ruleset = isc_rules.parse_rules({
        "version": 1,
        "packages": packages if packages is not None else {
            LIVE_NS: [{"prefix": "Custom.ACHS", "roles": ["LIVE"], "expires": "2026-11-15"}],
        },
        "exceptions": [exception_data()] if exceptions is None else exceptions,
    })
    return dataclasses.replace(
        REFERENCE_POLICY, rules=ruleset,
        direct_ha_nodes=frozenset(REFERENCE_POLICY.direct_ha_nodes) | {PRIMARY, BACKUP})


def live_client(host=PRIMARY, namespace=LIVE_NS, ens_jobs=12, policy=None, production_read=True, role="LIVE"):
    client = synthetic_client(namespace=namespace, role=role, host=host, production=True,
                              ens_jobs=ens_jobs, policy=live_policy() if policy is None else policy)
    client.production_read = production_read
    return client


class PreGoLiveTestCase(HermeticTestCase):
    def setUp(self):
        super().setUp()
        today = patch("isc_ai.server._live_window_today", return_value=TODAY)
        today.start()
        self.addCleanup(today.stop)


class ParseTests(PreGoLiveTestCase):
    def test_valid_exception_parses(self):
        ruleset = isc_rules.parse_rules({"version": 1, "exceptions": [exception_data()]})
        self.assertEqual(ruleset.exceptions[0].capability, "pre_go_live")

    def test_every_scope_key_is_mandatory(self):
        for key in ("hosts", "namespaces", "roles", "active_node_evidence_namespace"):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                isc_rules.parse_rules({"version": 1, "exceptions": [exception_data(**{key: None})]})

    def test_active_node_evidence_cannot_be_disabled_for_long(self):
        with self.assertRaises(ConfigError):
            isc_rules.parse_rules({"version": 1, "exceptions": [exception_data(requires_active_node=False)]})

    def test_short_backup_node_exception_parses_without_evidence(self):
        ruleset = isc_rules.parse_rules({"version": 1, "exceptions": [backup_exception_data()]})
        self.assertFalse(ruleset.exceptions[0].requires_active_node)
        self.assertIsNone(ruleset.exceptions[0].active_node_evidence_namespace)

    def test_invalid_expiry_is_rejected(self):
        with self.assertRaises(ConfigError):
            isc_rules.parse_rules({"version": 1, "exceptions": [exception_data(expires="someday")]})


class OpenGateTests(PreGoLiveTestCase):
    def test_active_node_opens_runtime_and_code_write(self):
        client = live_client()
        self.assertIsNone(client._production_gate_reason())
        allowed, reason = client.is_runtime_operation_allowed()
        self.assertTrue(allowed, reason)
        allowed, reason = client.is_code_write_route_allowed()
        self.assertTrue(allowed, reason)
        self.assertIn("PRE-GO-LIVE", reason)
        self.assertIn("TCK-LIVE", reason)

    def test_ephemeral_runner_is_admitted(self):
        live_client()._require_ephemeral_runner("User.McpTmpRun0123456789ab")

    def test_listed_sibling_namespace_uses_the_evidence_namespace(self):
        from tests.hermetic_test_support import envelope
        client = live_client(namespace=ROUTER_NS)
        seen = []

        def respond(endpoint, method="GET", payload=None, **kwargs):
            seen.append(kwargs.get("namespace"))
            return envelope([{"namespace": kwargs.get("namespace"), "ens_jobs": 9}])

        client._send_request.side_effect = respond
        allowed, reason = client.is_runtime_operation_allowed()
        self.assertTrue(allowed, reason)
        self.assertEqual(seen, [LIVE_NS])

    def test_evidence_is_reused_within_one_call_window(self):
        client = live_client()
        client.is_runtime_operation_allowed()
        client.is_code_write_route_allowed()
        self.assertEqual(client._send_request.call_count, 1)

    def test_untracked_write_under_controlled_active_is_admitted_and_audited(self):
        client = live_client()
        with patch.object(server_module.AtelierClient, "_probe_same_process_ccr_state",
                          return_value=guard_record("CONTROLLED_ACTIVE")):
            allowed, reason = client.is_code_write_allowed("Custom.ACHS.Foo.cls")
        self.assertTrue(allowed, reason)
        self.assertEqual(self.runtime_audit_records[-1]["event"], "pre_go_live_code_write")
        self.assertEqual(self.runtime_audit_records[-1]["document"], "Custom.ACHS.Foo.cls")
        self.assertEqual(self.runtime_audit_records[-1]["ticket"], "TCK-LIVE")


class ClosedGateTests(PreGoLiveTestCase):
    def assert_blocked(self, client, fragment=None):
        self.assertIsNotNone(client._production_gate_reason())
        allowed, reason = client.is_runtime_operation_allowed()
        self.assertFalse(allowed)
        allowed, route_reason = client.is_code_write_route_allowed()
        self.assertFalse(allowed)
        with self.assertRaises(LocalPolicyError):
            client._require_ephemeral_runner("User.McpTmpRun0123456789ab")
        if fragment is not None:
            self.assertIn(fragment, client._resolve_pre_go_live()[1])

    def test_without_an_exception_live_stays_blocked(self):
        self.assert_blocked(live_client(policy=live_policy(exceptions=[])), "no open pre_go_live exception")

    def test_backup_node_without_evidence_is_blocked(self):
        self.assert_blocked(live_client(host=BACKUP, ens_jobs=0), "use the active node")

    def test_expired_exception_is_blocked(self):
        policy = live_policy(exceptions=[exception_data(expires="2026-10-15")])
        self.assert_blocked(live_client(policy=policy), "no open pre_go_live exception")

    def test_production_read_is_required(self):
        self.assert_blocked(live_client(production_read=False), "production_read")

    def test_host_must_be_a_direct_ha_node(self):
        policy = live_policy(exceptions=[exception_data(hosts=[NOT_A_NODE])])
        self.assert_blocked(live_client(host=NOT_A_NODE, policy=policy), "direct HA node")

    def test_unlisted_namespace_is_blocked(self):
        self.assert_blocked(live_client(namespace="HMFACHSGATEWAY_INT"), "no open pre_go_live exception")

    def test_unlisted_role_is_blocked(self):
        self.assert_blocked(live_client(role="PROD"), "no open pre_go_live exception")

    def test_suspended_ccr_stays_blocked(self):
        client = live_client()
        with patch.object(server_module.AtelierClient, "_probe_same_process_ccr_state",
                          return_value=guard_record("SUSPENDED")):
            allowed, reason = client.is_code_write_allowed("Custom.ACHS.Foo.cls")
        self.assertFalse(allowed)
        self.assertIn("SUSPENDED", reason)

    def test_uncataloged_and_core_documents_stay_blocked(self):
        client = live_client()
        for document in ("web.Foo.cls", "Custom.Other.Foo.cls"):
            with self.subTest(document=document):
                allowed, reason = client.is_code_write_allowed(document)
                self.assertFalse(allowed)
                self.assertIn("not cataloged", reason)

    def test_non_production_targets_gain_nothing(self):
        client = synthetic_client(namespace="UAT-ACHS", role="UAT", host=PRIMARY, policy=live_policy())
        self.assertIn("production targets only", client._resolve_pre_go_live()[1])
        baseline = synthetic_client(namespace="UAT-ACHS", role="UAT", host=PRIMARY,
                                    policy=live_policy(exceptions=[], packages={}))
        self.assertEqual(client.is_runtime_operation_allowed(), baseline.is_runtime_operation_allowed())
        self.assertEqual(client.is_code_write_route_allowed(), baseline.is_code_write_route_allowed())

    def test_ccr_tracked_write_is_blocked(self):
        client = live_client()
        with patch.object(server_module.AtelierClient, "_probe_same_process_ccr_state",
                          return_value=guard_record("CONTROLLED_ACTIVE")):
            allowed, reason = client.is_code_write_allowed("Custom.ACHS.Foo.cls", ccr_tracking="ACHS1234")
        self.assertFalse(allowed)
        self.assertIn("untracked", reason)
        self.assertEqual(self.runtime_audit_records, [])

    def test_guarded_ccr_runner_is_blocked(self):
        client = live_client()
        with patch.object(server_module.AtelierClient, "execute_runtime_code") as runner:
            with self.assertRaises(LocalPolicyError):
                client._run_guarded_ccr_runner("Write 1", "ACHS1234")
        runner.assert_not_called()

    def test_canonical_base_ccr_mutation_stays_blocked(self):
        with self.assertRaises(LocalPolicyError):
            live_client()._require_canonical_base_ccr_mutation()

    def test_failed_audit_blocks_the_write(self):
        client = live_client()
        with patch.object(server_module.AtelierClient, "_probe_same_process_ccr_state",
                          return_value=guard_record("CONTROLLED_ACTIVE")), \
                patch("isc_ai.server._append_runtime_audit", side_effect=OSError("disk full")):
            with self.assertRaises(LocalPolicyError):
                client.is_code_write_allowed("Custom.ACHS.Foo.cls")


class BackupNodeTests(PreGoLiveTestCase):
    def backup_client(self, host=BACKUP, exceptions=None):
        policy = live_policy(exceptions=[backup_exception_data()] if exceptions is None else exceptions)
        return live_client(host=host, ens_jobs=0, policy=policy)

    def test_backup_exception_opens_runtime_without_evidence(self):
        client = self.backup_client()
        allowed, reason = client.is_runtime_operation_allowed()
        self.assertTrue(allowed, reason)
        client._require_ephemeral_runner("User.McpTmpRun0123456789ab")
        self.assertEqual(client._send_request.call_count, 0)

    def test_backup_exception_never_admits_code_write(self):
        client = self.backup_client()
        allowed, reason = client.is_code_write_route_allowed()
        self.assertFalse(allowed)
        self.assertIn("never CODE WRITE", reason)
        with patch.object(server_module.AtelierClient, "_probe_same_process_ccr_state",
                          return_value=guard_record("CONTROLLED_ACTIVE")):
            allowed, _ = client.is_code_write_allowed("Custom.ACHS.Foo.cls")
        self.assertFalse(allowed)
        self.assertEqual(self.runtime_audit_records, [])

    def test_backup_exception_is_scoped_to_its_hosts(self):
        client = self.backup_client(host=PRIMARY)
        self.assertFalse(client.is_runtime_operation_allowed()[0])

    def test_backup_exception_still_honours_expiry(self):
        expired = backup_exception_data(expires="2026-10-15")
        self.assertFalse(self.backup_client(exceptions=[expired]).is_runtime_operation_allowed()[0])


class ScopeAndCacheTests(PreGoLiveTestCase):
    def test_servers_and_path_prefixes_are_rejected(self):
        for key, value in (("servers", ["achslive01"]), ("path_prefixes", ["/x"])):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                isc_rules.parse_rules({"version": 1, "exceptions": [exception_data(**{key: value})]})

    def test_untracked_write_sql_is_admitted_and_audited(self):
        client = live_client()
        with patch.object(server_module.AtelierClient, "_probe_same_process_ccr_state",
                          return_value=guard_record("ABSENT")):
            allowed, reason = client.is_code_write_allowed(None)
        self.assertTrue(allowed, reason)
        self.assertIsNone(self.runtime_audit_records[-1]["document"])

    def test_runtime_operation_is_audited(self):
        client = live_client()
        with patch.object(server_module.AtelierClient, "_execute_runtime_runner", return_value="ok"):
            self.assertEqual(client.execute_runtime_code("Write 1"), "ok")
        self.assertEqual(self.runtime_audit_records[-1]["event"], "pre_go_live_runtime")

    def test_evidence_expires_after_the_ttl(self):
        client = live_client()
        with patch("isc_ai.server.time.monotonic", return_value=1000.0):
            client.is_runtime_operation_allowed()
        with patch("isc_ai.server.time.monotonic", return_value=1000.0 + server_module._PRE_GO_LIVE_EVIDENCE_TTL_S + 1):
            client.is_runtime_operation_allowed()
        self.assertEqual(client._send_request.call_count, 2)

    def test_uppercase_host_entry_matches(self):
        policy = live_policy(exceptions=[exception_data(hosts=["ACHS-LIVE-NODE"])])
        policy = dataclasses.replace(policy, direct_ha_nodes=policy.direct_ha_nodes | {"achs-live-node"})
        client = live_client(host="achs-live-node", policy=policy)
        self.assertTrue(client.is_runtime_operation_allowed()[0])


if __name__ == "__main__":
    import unittest
    unittest.main()
