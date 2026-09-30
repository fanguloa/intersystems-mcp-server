"""WU4: server.py gate integration with isc_ai.rules -- ACHS parity plus the
new BASE-role direct-HA-node code_write exception (previously impossible:
BASE on a direct HA node was unconditionally blocked).

ACHS parity for code_write/ensemble_control/runner/read is proven by the
existing suites staying green (test_config.py, test_live_config_window.py,
test_runtime_runner.py, test_production_read.py): this file adds only the
scenarios that are new in this change.
"""

import dataclasses

from tests.hermetic_test_support import HermeticTestCase, REF, REFERENCE_POLICY, synthetic_client
from isc_ai import rules as isc_rules

DIRECT_NODE = REF["ha_nodes"][0]  # "10.244.20.4", a configured direct HA node.
OTHER_DIRECT_NODE = REF["ha_nodes"][1]


def policy_with_code_write_rule(**exception_overrides):
    exception = {
        "id": "base-direct-node", "label": "BASE direct-node exception", "ticket": "TCK-1",
        "expires": "2099-01-01", "capability": "code_write", "roles": ["BASE"],
        "hosts": [DIRECT_NODE],
    }
    exception.update(exception_overrides)
    ruleset = isc_rules.parse_rules({"version": 1, "exceptions": [exception]})
    return dataclasses.replace(REFERENCE_POLICY, rules=ruleset)


class BaseDirectHaNodeCodeWriteTests(HermeticTestCase):
    """R4: a code_write exception on BASE + a direct HA node + active-node evidence."""

    def test_active_node_with_evidence_is_allowed(self):
        policy = policy_with_code_write_rule()
        client = synthetic_client(namespace=REF["base_namespace"], role="BASE",
                                  host=DIRECT_NODE, ens_jobs=5, policy=policy)
        allowed, reason = client.is_code_write_route_allowed()
        self.assertTrue(allowed, reason)
        self.assertIn("BASE direct-node exception", reason)

    def test_backup_node_without_evidence_is_blocked(self):
        policy = policy_with_code_write_rule()
        client = synthetic_client(namespace=REF["base_namespace"], role="BASE",
                                  host=DIRECT_NODE, ens_jobs=0, policy=policy)
        allowed, reason = client.is_code_write_route_allowed()
        self.assertFalse(allowed)
        self.assertIn("not executing the production", reason)

    def test_host_without_a_matching_exception_stays_unconditionally_blocked(self):
        # Regression: without the rules-file exception, BASE on a direct HA node is
        # blocked exactly as before this change (R4's baseline, still enforced).
        client = synthetic_client(namespace=REF["base_namespace"], role="BASE",
                                  host=DIRECT_NODE, policy=REFERENCE_POLICY)
        allowed, reason = client.is_code_write_route_allowed()
        self.assertFalse(allowed)
        self.assertIn("configured canonical VIP", reason)

    def test_exception_scoped_to_a_different_host_does_not_apply(self):
        policy = policy_with_code_write_rule()
        client = synthetic_client(namespace=REF["base_namespace"], role="BASE",
                                  host=OTHER_DIRECT_NODE, policy=policy)
        allowed, reason = client.is_code_write_route_allowed()
        self.assertFalse(allowed)
        self.assertIn("configured canonical VIP", reason)

    def test_requires_active_node_false_skips_the_evidence_check(self):
        policy = policy_with_code_write_rule(requires_active_node=False)
        client = synthetic_client(namespace=REF["base_namespace"], role="BASE",
                                  host=DIRECT_NODE, policy=policy)
        allowed, reason = client.is_code_write_route_allowed()
        self.assertTrue(allowed, reason)

    def test_exception_never_leaks_into_the_uat_role(self):
        # roles=["BASE"] must not also open the UAT route: capability_candidates'
        # role-scope filter has to be exact, not "any exception for this capability".
        policy = policy_with_code_write_rule()
        client = synthetic_client(namespace=REF["base_namespace"], role="UAT",
                                  host=DIRECT_NODE, policy=policy)
        allowed, reason = client.is_code_write_route_allowed()
        self.assertFalse(allowed)


class RulesFileOnlyRunnerAndReadGateTests(HermeticTestCase):
    """A runner/read exception defined purely in isc-ai.rules.json (no legacy
    runtime.runner_hosts / production_read at all) must reach the real gate."""

    def test_runner_exception_from_the_rules_file_selects_the_runner_namespace(self):
        ruleset = isc_rules.parse_rules({"version": 1, "exceptions": [{
            "id": "runner-1", "label": "Rules-file runner", "ticket": "TCK-2",
            "expires": "2099-01-01", "capability": "runner", "roles": ["UAT"],
            "namespaces": [REF["uat_namespace"]], "runner_namespace": "USER",
        }]})
        policy = dataclasses.replace(REFERENCE_POLICY, rules=ruleset, runner_hosts=())
        client = synthetic_client(namespace=REF["uat_namespace"], role="UAT",
                                  host=REF["clinical_vip"], policy=policy)
        sibling = client._runner_host_client()
        self.assertEqual(sibling.namespace, "USER")

    def test_read_exception_from_the_rules_file_opens_the_read_gate(self):
        from tests.hermetic_test_support import synthetic_server
        ruleset = isc_rules.parse_rules({"version": 1, "exceptions": [{
            "id": "read-1", "label": "Rules-file read", "ticket": "TCK-3",
            "expires": "2099-01-01", "capability": "read", "servers": ["prod1"],
        }]})
        policy = dataclasses.replace(REFERENCE_POLICY, rules=ruleset)
        servers = {"prod1": {"host": "prod1.invalid", "namespace": "PROD", "username": "u",
                             "password": "p", "is_production": True}}
        server = synthetic_server(policy=policy, servers=servers)
        client = server._get_client_direct("prod1")
        self.assertTrue(client.production_read)
        self.assertTrue(client.is_read_allowed()[0])


if __name__ == "__main__":
    import unittest
    unittest.main()
