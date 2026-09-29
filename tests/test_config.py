"""Configuration model, loader and CLI tests.

Parsing/validation tests are hermetic. Loader and CLI tests use a temporary
directory as the project and an isolated HOME; they never touch the real user
configuration or the network.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isc_ai.ccr_discovery import LocalPolicyError
from tests.hermetic_test_support import (
    HermeticTestCase, REF, REFERENCE_POLICY, UNCONFIGURED_POLICY, iris_mcp_config,
    reference_policy_data, synthetic_client, synthetic_server,
)
from isc_ai.config import CanonicalBase, ConfigError, PackageGrant, SitePolicy, WriteException
from isc_ai.cli import _parse_cli_args, main
from isc_ai.server import _load_ccr_mutation_resource
from isc_ai.config import STATE_DIR


class PolicyModelTests(HermeticTestCase):
    def test_unconfigured_policy_blocks_every_site_specific_operation(self):
        policy = UNCONFIGURED_POLICY
        self.assertFalse(policy.ccr_configured)
        self.assertFalse(policy.ccr_matches("ANY0001"))
        self.assertIsNone(policy.ccr_regex)
        self.assertIsNone(policy.ccr_cos_pattern)
        self.assertIsNone(policy.canonical_base)
        self.assertFalse(policy.mutation_configured)
        self.assertFalse(policy.package_allowed("BASE-X", "BASE", "Custom.Anything.cls"))
        self.assertEqual(list(policy.matching_write_exceptions("INT", "h", "/p")), [])
        client = synthetic_client(policy=policy)
        # Production gate and READ keep working with built-in vocabulary.
        self.assertTrue(client.is_read_allowed()[0])
        self.assertFalse(synthetic_client("APP-LIVE", "BASE", policy=policy).is_read_allowed()[0])
        allowed, reason = client.is_code_write_route_allowed()
        self.assertFalse(allowed)  # fail closed: HA-node/route policy unknown
        self.assertIn("no project configuration", reason)
        with self.assertRaisesRegex(LocalPolicyError, "site.ccr_id"):
            client._require_ccr_id("ANY0001")
        with self.assertRaisesRegex(LocalPolicyError, "canonical_base"):
            client._require_canonical_base_ccr_mutation()

    def test_ccr_format_derivations(self):
        policy = REFERENCE_POLICY
        self.assertEqual(policy.ccr_regex, r"ACHS\d{4}")
        self.assertTrue(policy.ccr_matches("ACHS0875"))
        self.assertFalse(policy.ccr_matches("ACHS875"))
        self.assertFalse(policy.ccr_matches("achs0875"))
        self.assertFalse(policy.ccr_matches(None))
        self.assertEqual(policy.ccr_cos_pattern, '1"ACHS"4N')
        self.assertEqual(policy.ccr_description, "ACHS followed by four digits")
        self.assertEqual(policy.mutation_protocol, "ACHS_CCR_MUTATION_V1")
        self.assertEqual(policy.initializer_protocol, "ACHS_CCR_INITIALIZER_V1")
        twelve = SitePolicy(ccr_prefix="Z", ccr_digits=12)
        self.assertEqual(twelve.ccr_description, "Z followed by 12 digits")

    def test_package_grants_match_prefix_class_and_document_with_roles(self):
        prefix = PackageGrant(prefix="Custom.Site")
        self.assertTrue(prefix.allows("Custom.Site.A.cls", "Custom.Site.A", "BASE"))
        self.assertFalse(prefix.allows("Custom.Site.cls", "Custom.Site", "BASE"))
        self.assertFalse(prefix.allows("Custom.Sites.A.cls", "Custom.Sites.A", "BASE"))
        exact = PackageGrant(class_name="Pkg.Prod", roles=frozenset({"BASE"}))
        self.assertTrue(exact.allows("Pkg.Prod.mac", "Pkg.Prod", "BASE"))
        self.assertFalse(exact.allows("Pkg.Prod.mac", "Pkg.Prod", "UAT"))
        document = PackageGrant(document="Pkg.Doc.cls")
        self.assertTrue(document.allows("Pkg.Doc.cls", "Pkg.Doc", "UAT"))
        self.assertFalse(document.allows("Pkg.Doc.mac", "Pkg.Doc", "UAT"))
        # The facility class is never grantable, whatever the catalog says.
        policy = SitePolicy(packages={"NS": (PackageGrant(prefix="MCP"), PackageGrant(prefix="Custom"))})
        self.assertFalse(policy.package_allowed("NS", "BASE", "MCP.CCRMutation.cls"))
        self.assertFalse(policy.package_allowed("NS", "BASE", "Custom.MCP.CCRMutation.cls"))
        self.assertTrue(policy.package_allowed("NS", "BASE", "Custom.Thing.cls"))

    def test_mutation_expected_path_strips_configured_prefix(self):
        expected = (REFERENCE_POLICY.mutation_source_root / "Tests" / "Sample.cls")
        self.assertEqual(REFERENCE_POLICY.mutation_expected_path("Custom.ACHS.Tests.Sample"), expected)
        self.assertIsNone(REFERENCE_POLICY.mutation_expected_path("Custom.Other.Sample"))
        self.assertIsNone(REFERENCE_POLICY.mutation_expected_path("Custom.ACHS.MCP.CCRMutation"))
        self.assertIsNone(UNCONFIGURED_POLICY.mutation_expected_path("Custom.ACHS.Tests.Sample"))

    def test_write_exceptions_resolve_hosts_against_direct_nodes_or_explicit_list(self):
        policy = REFERENCE_POLICY
        vip = list(policy.matching_write_exceptions("INT", REF["clinical_vip"], REF["int_vip_prefix"]))
        self.assertEqual([item.label for item in vip], ["INT UAT VIP"])
        self.assertFalse(vip[0].requires_active_node)
        node = list(policy.matching_write_exceptions("INT", REF["ha_nodes"][0], "/intuat01"))
        self.assertEqual([item.label for item in node], ["INT UAT active node"])
        self.assertEqual(list(policy.matching_write_exceptions("INT", "other.invalid", "/intuat01")), [])
        self.assertEqual(list(policy.matching_write_exceptions("UAT-ACHS", REF["ha_nodes"][0], "/intuat01")), [])


class PolicyParsingTests(HermeticTestCase):
    def parse(self, **overrides):
        data = reference_policy_data()
        data.update(overrides)
        return iris_mcp_config.parse_policy(data, Path("/project"))

    def test_reference_data_round_trips(self):
        policy = self.parse()
        self.assertEqual(policy.canonical_base, CanonicalBase("achsbase", "10.244.10.4", 80, "/trakbase01", "BASE-ACHS"))
        self.assertEqual(policy.direct_ha_nodes, frozenset(REF["ha_nodes"]))
        self.assertEqual(policy.alias_constraints, {"achsuat": (REF["clinical_vip"], "")})
        self.assertEqual(len(policy.uat_write_exceptions), 3)
        self.assertEqual(policy.mutation_source_root, (Path("/project") / REF["mutation_source_root"]).resolve())

    def test_template_is_a_valid_project_configuration_without_customer_identity(self):
        with patch("builtins.open", side_effect=None):
            pass
        text = iris_mcp_config.TEMPLATE_PATH.read_text(encoding="utf-8")
        for literal in ("ACHS", "10.244.", "trakuat", "clacintuat", "HMF"):
            self.assertNotIn(literal, text)
        data = json.loads(text)
        policy = iris_mcp_config.parse_policy(data, Path("/project"))
        self.assertTrue(policy.ccr_configured)
        self.assertTrue(policy.mutation_configured)
        self.assertIn(policy.canonical_base.server_id, data["servers"])
        for entry in data["servers"].values():
            self.assertRegex(entry["password"], r"^\{env:[A-Z_]+\}$")
        rendered, _ = _load_ccr_mutation_resource(policy)
        self.assertIn(b"BASE-SITE", rendered)

    def test_rejects_unknown_top_level_keys_and_wrong_version(self):
        with self.assertRaisesRegex(ConfigError, "Unknown top-level keys"):
            self.parse(extra={})
        with self.assertRaisesRegex(ConfigError, "Unsupported configuration version"):
            self.parse(version=2)
        with self.assertRaisesRegex(ConfigError, "JSON object"):
            iris_mcp_config.parse_policy([], Path("."))

    def test_rejects_forbidden_package_roots_and_facility_grants(self):
        for grant in ({"prefix": "Region.X"}, {"prefix": "%SYS"}, {"prefix": "MCP"},
                      {"class": "MCP.CCRMutation"}, {"document": "Custom.MCP.CCRMutation.cls"},
                      {"prefix": "A", "class": "B"}, {}, {"prefix": "bad name"}):
            with self.subTest(grant=grant), self.assertRaises(ConfigError):
                self.parse(packages={"NS": [grant]})
        with self.assertRaisesRegex(ConfigError, "never be granted"):
            self.parse(mutation={"class_prefix": "Ens.Custom", "local_source_root": "src"})

    def test_production_vocabulary_is_a_floor_never_shrinkable(self):
        policy = self.parse(site={"production_markers": [], "production_roles": [],
                                  "non_production_roles": ["BASE", "UAT"]})
        self.assertEqual(policy.production_markers, iris_mcp_config.BUILTIN_PRODUCTION_MARKERS)
        self.assertEqual(policy.production_roles, iris_mcp_config.BUILTIN_PRODUCTION_ROLES)
        extended = self.parse(site={"production_markers": ["PRD", "GOLD"], "production_roles": ["GOLD"]})
        self.assertEqual(extended.production_markers, iris_mcp_config.BUILTIN_PRODUCTION_MARKERS + ("GOLD",))
        self.assertIn("GOLD", extended.production_roles)
        self.assertIn("LIVE", extended.production_roles)
        with self.assertRaisesRegex(ConfigError, "both production and non-production"):
            self.parse(site={"non_production_roles": ["LIVE", "BASE"]})
        with self.assertRaisesRegex(ConfigError, "production marker"):
            self.parse(site={"non_production_roles": ["PREPROD", "BASE"]})
        with self.assertRaisesRegex(ConfigError, "must not be empty"):
            self.parse(site={"non_production_roles": []})
        live = synthetic_client("TRAK-LIVE", "LIVE", policy=policy)
        self.assertFalse(live.is_read_allowed()[0])

    def test_write_exception_without_active_node_cannot_target_direct_ha_nodes(self):
        routes = {"direct_ha_nodes": ["n1", "n2"], "uat_write_exceptions": [
            {"namespace": "INT", "hosts": ["n1"], "path_prefixes": ["/x"], "requires_active_node": False}]}
        with self.assertRaisesRegex(ConfigError, "direct HA nodes"):
            self.parse(routes=routes)
        routes["uat_write_exceptions"][0].pop("hosts")
        with self.assertRaisesRegex(ConfigError, "explicit hosts"):
            self.parse(routes=routes)
        routes["uat_write_exceptions"][0]["hosts"] = ["vip"]
        self.assertEqual(len(self.parse(routes=routes).uat_write_exceptions), 1)

    def test_rejects_role_overlap_and_malformed_routes(self):
        with self.assertRaisesRegex(ConfigError, "both production and non-production"):
            self.parse(site={"production_roles": ["UAT"]})
        with self.assertRaisesRegex(ConfigError, "path_prefixes"):
            self.parse(routes={"uat_write_exceptions": [{"namespace": "INT"}]})
        with self.assertRaisesRegex(ConfigError, "requires_active_node"):
            self.parse(routes={"uat_write_exceptions": [{"namespace": "INT", "path_prefixes": ["/x"], "requires_active_node": "yes"}]})
        with self.assertRaisesRegex(ConfigError, "canonical_base.port"):
            self.parse(canonical_base={"server_id": "b", "host": "h", "port": "80", "namespace": "N"})
        with self.assertRaisesRegex(ConfigError, "ccr_id.digits"):
            self.parse(site={"ccr_id": {"prefix": "X", "digits": 0}})

    def test_extra_packages_parser_and_policy_extension(self):
        raw = "BASE-ACHS:Custom.MotorSDA,Region.X,MCP,%SYS,bad name;OTHER:Own.Pkg;nocolon"
        self.assertEqual(iris_mcp_config.parse_extra_packages(raw),
                         [("BASE-ACHS", "Custom.MotorSDA"), ("OTHER", "Own.Pkg")])
        extended = REFERENCE_POLICY.with_extra_packages(raw)
        self.assertTrue(extended.package_allowed("BASE-ACHS", "BASE", "Custom.MotorSDA.X.cls"))
        self.assertFalse(extended.package_allowed("BASE-ACHS", "UAT", "Custom.MotorSDA.X.cls"))
        self.assertTrue(extended.package_allowed("OTHER", "BASE", "Own.Pkg.Thing.cls"))
        self.assertFalse(REFERENCE_POLICY.package_allowed("BASE-ACHS", "BASE", "Custom.MotorSDA.X.cls"))


class SecretResolutionTests(HermeticTestCase):
    def test_env_references_resolve_and_missing_names_mark_entry(self):
        servers = {
            "ok": {"host": "h", "password": "{env:PW}", "username": "{env:USER_A}"},
            "missing": {"host": "h", "password": "{env:NOPE}", "username": "u"},
            "broken": "not-a-dict",
        }
        resolved = iris_mcp_config.resolve_servers(servers, {"PW": "s3cret", "USER_A": "alice"})
        self.assertEqual(resolved["ok"], {"host": "h", "password": "s3cret", "username": "alice"})
        self.assertIn("NOPE", resolved["missing"]["_config_error"])
        self.assertNotIn("password", resolved["missing"])
        self.assertIn("_config_error", resolved["broken"])
        server = synthetic_server(servers=resolved)
        with self.assertRaisesRegex(LocalPolicyError, "NOPE"):
            server._resolve_server_config("missing")

    def test_redaction_masks_literal_secrets_but_keeps_env_references(self):
        data = {"servers": {"a": {"password": "literal", "upload_token": "{env:TOK}", "host": "h"}}}
        data["servers"]["a"]["proxy"] = "socks5h://user:pw@proxy.invalid:1080"
        redacted = iris_mcp_config.redact(data)
        self.assertEqual(redacted["servers"]["a"]["password"], "<redacted>")
        self.assertEqual(redacted["servers"]["a"]["upload_token"], "{env:TOK}")
        self.assertEqual(redacted["servers"]["a"]["host"], "h")
        self.assertEqual(redacted["servers"]["a"]["proxy"], "socks5h://<redacted>@proxy.invalid:1080")
        self.assertEqual(iris_mcp_config.redact({"proxy": "http://proxy.invalid:8080"})["proxy"], "http://proxy.invalid:8080")


class AliasConstraintTests(HermeticTestCase):
    def test_constrained_alias_requires_configured_vip(self):
        servers = {
            "achsuat": {"canonical_server_id": "node"},
            "node": {"host": REF["ha_nodes"][0], "path_prefix": "/trakuat01", "namespace": "UAT-ACHS",
                     "username": "u", "password": "p", "env_role": "UAT"},
            "vip": {"host": REF["clinical_vip"], "path_prefix": "", "namespace": "UAT-ACHS",
                    "username": "u", "password": "p", "env_role": "UAT"},
            "free": {"canonical_server_id": "node"},
        }
        server = synthetic_server(servers=servers)
        with self.assertRaisesRegex(LocalPolicyError, "Unresolved achsuat target: The achsuat route requires the configured VIP"):
            server._resolve_server_config("achsuat")
        # Unconstrained aliases may point at a node.
        self.assertEqual(server._resolve_server_config("free")[0], "node")
        servers["achsuat"] = {"canonical_server_id": "vip"}
        self.assertEqual(server._resolve_server_config("achsuat")[0], "vip")


class FilesystemTestCase(unittest.TestCase):
    """Temporary project + HOME; the real user files are never read."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.home = self.root / "home"
        self.project.mkdir()
        self.home.mkdir()
        env = {"HOME": str(self.home), "USERPROFILE": str(self.home)}
        patches = (
            patch.dict(os.environ, env, clear=True),
            patch("pathlib.Path.home", return_value=self.home),
            patch("socket.create_connection", side_effect=AssertionError("Network forbidden")),
            patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Network forbidden")),
            patch("isc_ai.server.log_debug"),
        )
        for guard in patches:
            guard.start()
            self.addCleanup(guard.stop)

    def run_cli(self, *argv):
        """Run an isc-ai command scoped to the temporary project.

        `init` takes the directory positionally; `servers migrate` is user-level;
        every other command receives --project-dir after its top-level verb.
        """
        argv = list(argv)
        if argv[0] == "init":
            full = ["init", str(self.project), *argv[1:]]
        elif argv[0] == "migrate-servers":
            full = ["servers", "migrate", *argv[1:]]
        elif argv[0] == "render-facility":
            full = ["facility", "--project-dir", str(self.project), "render", *argv[1:]]
        elif argv[0] == "doctor":
            full = ["doctor", "--project-dir", str(self.project), *argv[1:]]
        else:
            full = ["config", "--project-dir", str(self.project), *argv]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = main(full)
        return code, stdout.getvalue(), stderr.getvalue()

    def project_config(self):
        return json.loads((self.project / STATE_DIR / "config.json").read_text(encoding="utf-8"))


class LoaderTests(FilesystemTestCase):
    def test_missing_files_yield_unconfigured_policy_and_note(self):
        runtime = iris_mcp_config.load_runtime_config(self.project)
        self.assertEqual(runtime.policy, SitePolicy())
        self.assertEqual(runtime.servers, {})
        self.assertTrue(any("No project configuration" in note for note in runtime.notes))
        self.assertFalse(runtime.policy.configured)

    def test_project_servers_override_user_and_legacy_files(self):
        (self.home / ".iris_mcp_servers.json").write_text(json.dumps({"servers": {
            "legacy": {"host": "legacy.invalid"}, "shared": {"host": "legacy-shared.invalid"},
        }}), encoding="utf-8")
        (self.home / STATE_DIR).mkdir()
        (self.home / STATE_DIR / "servers.json").write_text(json.dumps({"servers": {
            "user": {"host": "user.invalid", "password": "{env:PW}"}, "shared": {"host": "user-shared.invalid"},
        }}), encoding="utf-8")
        data = reference_policy_data()
        data["servers"] = {"shared": {"host": "project-shared.invalid"}}
        iris_mcp_config.write_json(self.project / STATE_DIR / "config.json", data)
        with patch.dict(os.environ, {"PW": "resolved", "IRIS_EXTRA_PACKAGES": "BASE-ACHS:Own.Extra"}):
            runtime = iris_mcp_config.load_runtime_config(self.project)
        self.assertEqual(runtime.servers["legacy"]["host"], "legacy.invalid")
        self.assertEqual(runtime.servers["user"]["password"], "resolved")
        self.assertEqual(runtime.servers["shared"]["host"], "project-shared.invalid")
        self.assertEqual(runtime.policy.canonical_base.server_id, "achsbase")
        self.assertTrue(runtime.policy.package_allowed("BASE-ACHS", "BASE", "Own.Extra.Thing.cls"))
        self.assertTrue(any("legacy" in note for note in runtime.notes))

    def test_server_reload_fails_closed_on_invalid_project_file(self):
        from isc_ai.server import IRISMCPServer
        data = reference_policy_data()
        data["servers"] = {"achsbase": {"host": "10.244.10.4", "port": 80, "path_prefix": "/trakbase01",
                                        "namespace": "BASE-ACHS", "env_role": "BASE", "username": "u",
                                        "password": "{env:PW}"}}
        iris_mcp_config.write_json(self.project / STATE_DIR / "config.json", data)
        with patch.dict(os.environ, {iris_mcp_config.PROJECT_DIR_ENV: str(self.project), "PW": "x"}):
            server = IRISMCPServer()
            self.assertEqual(server.policy.site_name, "reference")
            self.assertIn("achsbase", server.servers)
            (self.project / STATE_DIR / "config.json").write_text("{not json", encoding="utf-8")
            server.load_configuration()
            # A broken file never leaves the previous snapshot in force.
            self.assertFalse(server.policy.configured)
            self.assertEqual(server.servers, {})
            with self.assertRaisesRegex(LocalPolicyError, "missing"):
                server._resolve_server_config("achsbase")

    def test_missing_project_config_blocks_code_write_routes_but_not_read(self):
        (self.home / STATE_DIR).mkdir()
        (self.home / STATE_DIR / "servers.json").write_text(json.dumps({"servers": {
            "node": {"host": "10.244.20.4", "port": 80, "path_prefix": "/trakuat01", "namespace": "UAT-X",
                     "env_role": "UAT", "username": "u", "password": "p"},
            "base": {"host": "b.invalid", "port": 80, "path_prefix": "", "namespace": "BASE-X",
                     "env_role": "BASE", "username": "u", "password": "p"},
        }}), encoding="utf-8")
        runtime = iris_mcp_config.load_runtime_config(self.project)
        self.assertFalse(runtime.policy.configured)
        self.assertTrue(any("code-write routes are blocked" in note for note in runtime.notes))
        for server_id in ("node", "base"):
            entry = runtime.servers[server_id]
            client = synthetic_client(entry["namespace"], entry["env_role"], host=entry["host"],
                                      path_prefix=entry["path_prefix"], policy=runtime.policy)
            self.assertTrue(client.is_read_allowed()[0])
            self.assertTrue(client.is_runtime_operation_allowed()[0])
            for promotion in (False, True):
                allowed, reason = client.is_code_write_route_allowed(promotion=promotion)
                self.assertFalse(allowed, (server_id, promotion))
                self.assertIn("no project configuration", reason)

    def test_env_overrides_for_project_dir_and_config_path(self):
        alt = self.root / "alt.json"
        iris_mcp_config.write_json(alt, {"version": 1, "site": {"name": "alt"}})
        with patch.dict(os.environ, {iris_mcp_config.PROJECT_CONFIG_ENV: str(alt)}):
            runtime = iris_mcp_config.load_runtime_config(self.project)
        self.assertEqual(runtime.policy.site_name, "alt")
        self.assertEqual(runtime.project_config_path, alt.resolve())

    def test_explicit_config_env_resolves_relative_paths_against_its_own_project(self):
        other = self.root / "other-site"
        (other / STATE_DIR).mkdir(parents=True)
        data = reference_policy_data()
        data["mutation"]["local_source_root"] = "SRC/Custom/ACHS"
        iris_mcp_config.write_json(other / STATE_DIR / "config.json", data)
        env = {iris_mcp_config.PROJECT_CONFIG_ENV: str(other / STATE_DIR / "config.json")}
        # Shell sits inside an unrelated deployment; the explicit file must still win
        # and its relative mutation root must resolve under other-site, not under cwd.
        with patch.dict(os.environ, env), patch("pathlib.Path.cwd", return_value=self.project):
            runtime = iris_mcp_config.load_runtime_config()
        self.assertEqual(runtime.project_dir, other.resolve())
        self.assertEqual(runtime.policy.mutation_source_root, (other / "SRC" / "Custom" / "ACHS").resolve())
        # An explicit project dir still takes precedence over the file override.
        with patch.dict(os.environ, {**env, iris_mcp_config.PROJECT_DIR_ENV: str(self.project)}):
            runtime = iris_mcp_config.load_runtime_config()
        self.assertEqual(runtime.project_dir, self.project.resolve())

    def test_server_pins_project_dir_when_given(self):
        from isc_ai.server import IRISMCPServer
        other = self.root / "other-site"
        (other / STATE_DIR).mkdir(parents=True)
        data = reference_policy_data()
        data["site"]["name"] = "other"
        iris_mcp_config.write_json(other / STATE_DIR / "config.json", data)
        iris_mcp_config.write_json(self.project / STATE_DIR / "config.json", reference_policy_data())
        with patch("pathlib.Path.cwd", return_value=self.project):
            self.assertEqual(IRISMCPServer().policy.site_name, "reference")
            pinned = IRISMCPServer(project_dir=other)
            self.assertEqual(pinned.policy.site_name, "other")
            pinned.load_configuration()  # reload keeps the pin
            self.assertEqual(pinned.policy.site_name, "other")
            self.assertEqual(pinned.runtime_config.project_dir, other.resolve())


class ConfigCliTests(FilesystemTestCase):
    def test_init_copies_template_and_is_idempotent(self):
        code, out, _ = self.run_cli("init")
        self.assertEqual(code, 0)
        self.assertIn("Created", out)
        data = self.project_config()
        self.assertEqual(data["version"], 1)
        self.assertIn("servers", data)
        before = (self.project / STATE_DIR / "config.json").read_bytes()
        # Second run: success, nothing rewritten, explicit message.
        code, out, err = self.run_cli("init")
        self.assertEqual(code, 0)
        self.assertIn("already exists; nothing changed", out)
        self.assertEqual(err, "")
        self.assertEqual((self.project / STATE_DIR / "config.json").read_bytes(), before)
        code, _, _ = self.run_cli("init", "--force", "--no-servers")
        self.assertEqual(code, 0)
        self.assertNotIn("servers", self.project_config())

    def test_init_defaults_to_cwd_and_doctor_finds_project_upward(self):
        nested = self.project / "BASE-X" / "src" / "Custom"
        nested.mkdir(parents=True)
        stdout = io.StringIO()
        with patch("pathlib.Path.cwd", return_value=self.project), patch("sys.stdout", stdout):
            self.assertEqual(main(["init"]), 0)
        self.assertTrue((self.project / STATE_DIR / "config.json").is_file())
        # A process launched deep inside the deployment resolves the same project.
        self.assertEqual(iris_mcp_config.find_project_root(nested), self.project)
        self.assertEqual(iris_mcp_config.project_directory(environ={}, start=nested), self.project)
        self.assertIsNone(iris_mcp_config.find_project_root(self.root))
        self.assertEqual(iris_mcp_config.project_directory(environ={}, start=self.root), self.root.resolve())
        # Explicit env wins and disables the search.
        env = {iris_mcp_config.PROJECT_DIR_ENV: str(self.root)}
        self.assertEqual(iris_mcp_config.project_directory(environ=env, start=nested), self.root.resolve())
        stdout = io.StringIO()
        with patch("pathlib.Path.cwd", return_value=nested), patch("sys.stdout", stdout), patch("sys.stderr", io.StringIO()):
            main(["doctor"])
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["project_dir"], str(self.project.resolve()))
        self.assertTrue(report["configured"])
        self.assertEqual(report["cwd"], str(nested))

    def test_init_from_custom_template_validates_it(self):
        custom = self.root / "custom.json"
        custom.write_text(json.dumps(reference_policy_data()), encoding="utf-8")
        code, _, _ = self.run_cli("init", "--from", str(custom))
        self.assertEqual(code, 0)
        self.assertEqual(self.project_config()["site"]["name"], "reference")
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"version": 1, "packages": {"NS": [{"prefix": "Region"}]}}), encoding="utf-8")
        code, _, err = self.run_cli("init", "--force", "--from", str(bad))
        self.assertEqual(code, 1)
        self.assertIn("never be granted", err)

    def test_set_types_values_from_current_schema_and_validates(self):
        self.run_cli("init")
        code, out, _ = self.run_cli("set", "canonical_base.host", "10.9.9.9")
        self.assertEqual(code, 0)
        self.assertIn('canonical_base.host = "10.9.9.9"', out)
        self.run_cli("set", "canonical_base.port", "8080")
        self.run_cli("set", "site.ccr_id.digits", "5")
        self.run_cli("set", "routes.direct_ha_nodes", '["n1.invalid", "n2.invalid"]')
        self.run_cli("set", "servers.my-base.is_production", "true")
        data = self.project_config()
        self.assertEqual(data["canonical_base"]["host"], "10.9.9.9")
        self.assertEqual(data["canonical_base"]["port"], 8080)
        self.assertEqual(data["site"]["ccr_id"]["digits"], 5)
        self.assertEqual(data["routes"]["direct_ha_nodes"], ["n1.invalid", "n2.invalid"])
        self.assertIs(data["servers"]["my-base"]["is_production"], True)
        # Invalid values are rejected before writing.
        code, _, err = self.run_cli("set", "canonical_base.port", "not-a-port")
        self.assertEqual(code, 1)
        self.assertIn("integer", err)
        code, _, err = self.run_cli("set", "mutation.class_prefix", "Region.X")
        self.assertEqual(code, 1)
        self.assertIn("never be granted", err)
        self.assertEqual(self.project_config()["mutation"]["class_prefix"], "Custom.Site")
        code, out, _ = self.run_cli("unset", "routes.alias_constraints")
        self.assertEqual(code, 0)
        self.assertNotIn("alias_constraints", self.project_config()["routes"])

    def test_add_and_remove_server_in_project_and_user_files(self):
        self.run_cli("init", "--no-servers")
        code, out, _ = self.run_cli("add-server", "dev", "--host", "dev.invalid", "--port", "52773",
                                    "--namespace", "DEV", "--role", "dev", "--upload-token", "{env:TOK}")
        self.assertEqual(code, 0)
        entry = self.project_config()["servers"]["dev"]
        self.assertEqual(entry["env_role"], "DEV")
        self.assertEqual(entry["port"], 52773)
        self.assertEqual(entry["password"], "{env:IRIS_PASSWORD}")
        self.assertEqual(entry["upload_token"], "{env:TOK}")
        # Literal secrets never reach the committable project file.
        code, _, err = self.run_cli("add-server", "leak", "--host", "l.invalid", "--password", "literal")
        self.assertEqual(code, 1)
        self.assertIn("Literal secrets", err)
        self.assertNotIn("leak", self.project_config()["servers"])
        code, out, err = self.run_cli("set", "servers.dev.password", "literal")
        self.assertEqual(code, 1)
        self.assertNotIn("literal", out + err.replace("Literal secrets", ""))
        code, out, _ = self.run_cli("set", "servers.dev.password", "{env:OTHER_PW}")
        self.assertEqual(code, 0)
        self.assertIn("<set>", out)
        self.assertNotIn("OTHER_PW", out)
        code, _, _ = self.run_cli("add-server", "private", "--host", "p.invalid", "--user",
                                  "--username", "me", "--password", "literal")
        self.assertEqual(code, 0)
        user_file = json.loads((self.home / STATE_DIR / "servers.json").read_text(encoding="utf-8"))
        self.assertEqual(user_file["servers"]["private"]["password"], "literal")
        self.assertNotIn("private", self.project_config()["servers"])
        code, out, _ = self.run_cli("show", "--servers")
        self.assertEqual(code, 0)
        shown = json.loads(out)
        self.assertEqual(shown["private"]["password"], "<redacted>")
        # Effective view: unresolved {env:...} references surface as an error, never as a value.
        self.assertRegex(shown["dev"]["_config_error"], "IRIS_(USER|PASSWORD)")
        self.assertNotIn("password", shown["dev"])
        code, _, err = self.run_cli("add-server", "bad", "--host", "bad host")
        self.assertEqual(code, 1)
        self.assertIn("malformed", err)
        code, _, _ = self.run_cli("remove-server", "dev")
        self.assertEqual(code, 0)
        self.assertNotIn("dev", self.project_config()["servers"])
        code, _, err = self.run_cli("remove-server", "dev")
        self.assertEqual(code, 1)

    def test_validate_reports_problems_and_exit_code(self):
        self.run_cli("init")
        code, out, _ = self.run_cli("validate")
        summary = json.loads(out)
        self.assertEqual(code, 1)  # template servers reference unset env vars
        self.assertTrue(any("IRIS_MY_BASE_USER" in problem for problem in summary["problems"]))
        env = {name: "x" for name in ("IRIS_MY_BASE_USER", "IRIS_MY_BASE_PASSWORD", "IRIS_MY_UAT_USER",
                                      "IRIS_MY_UAT_PASSWORD", "IRIS_CCR_TOKEN", "IRIS_MY_LIVE_USER",
                                      "IRIS_MY_LIVE_PASSWORD")}
        with patch.dict(os.environ, env):
            code, out, _ = self.run_cli("validate")
        summary = json.loads(out)
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary["problems"], [])
        self.assertTrue(summary["mutation_configured"])
        self.assertEqual(summary["canonical_base"], "my-base")
        self.run_cli("set", "canonical_base.server_id", "ghost")
        with patch.dict(os.environ, env):
            code, out, _ = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertTrue(any("ghost" in problem for problem in json.loads(out)["problems"]))

    def test_migrate_servers_copies_legacy_file(self):
        (self.home / ".iris_mcp_servers.json").write_text(json.dumps({"servers": {"a": {"host": "a.invalid"}}}), encoding="utf-8")
        code, out, _ = self.run_cli("migrate-servers")
        self.assertEqual(code, 0)
        self.assertIn("Copied 1 servers", out)
        migrated = json.loads((self.home / STATE_DIR / "servers.json").read_text(encoding="utf-8"))
        self.assertEqual(migrated, {"servers": {"a": {"host": "a.invalid"}}})
        code, _, err = self.run_cli("migrate-servers")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def test_render_facility_uses_project_policy(self):
        custom = self.root / "ref.json"
        custom.write_text(json.dumps(reference_policy_data()), encoding="utf-8")
        self.run_cli("init", "--from", str(custom))
        output = self.root / "rendered.cls"
        code, out, _ = self.run_cli("render-facility", "--output", str(output))
        self.assertEqual(code, 0)
        record = json.loads(out)
        self.assertEqual(record["sha256"], "81eac1c6755ae870f009e316eec147059ba9a24d439855bff6f01b9dc4282618")
        self.assertIn(b'$Namespace \'= "BASE-ACHS"', output.read_bytes())
        self.run_cli("unset", "mutation")
        code, _, err = self.run_cli("render-facility")
        self.assertEqual(code, 1)
        self.assertIn("mutation", err)

    def test_config_without_subcommand_and_unknown_paths_fail_cleanly(self):
        stderr = io.StringIO()
        with patch("sys.stderr", stderr), self.assertRaises(SystemExit) as raised:
            _parse_cli_args(["config"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("a config sub-command is required", stderr.getvalue())
        self.assertIn("isc-ai config show --servers", stderr.getvalue())  # examples, not just usage
        code, _, err = self.run_cli("show")
        self.assertEqual(code, 0)  # missing project file shows {} with a note
        self.assertIn("No project configuration", err)
        code, _, err = self.run_cli("set", "site.name", "x")
        self.assertEqual(code, 1)
        self.assertIn("isc-ai init", err)

    def test_bare_invocation_prints_full_help_and_exits_zero(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr), self.assertRaises(SystemExit) as raised:
            _parse_cli_args([])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(stderr.getvalue(), "")
        help_text = stdout.getvalue()
        for expected in ("mcp", "init", "config", "servers", "facility", "doctor", "setup",
                         "examples:", "isc-ai doctor", "isc-ai <command> -h"):
            self.assertIn(expected, help_text)

    def test_help_banner_is_plain_ascii_unless_the_stream_is_a_terminal(self):
        from isc_ai import __version__, cli
        stdout = io.StringIO()  # not a TTY: a pipe or a captured test stream
        with patch.dict(os.environ, {}, clear=False), patch("sys.stdout", stdout), self.assertRaises(SystemExit):
            os.environ.pop("FORCE_COLOR", None)
            _parse_cli_args([])
        help_text = stdout.getvalue()
        self.assertIn("|___||___/ \\___|", help_text)  # the ISC-AI logo leads the help
        self.assertIn(f"v{__version__}", help_text)
        self.assertNotIn("\033[", help_text)  # never leak ANSI into pipes, files or docs
        self.assertTrue(help_text.startswith(" ___"), "the banner must precede argparse's usage line")
        with patch.dict(os.environ, {"FORCE_COLOR": "1"}, clear=False):
            self.assertIn("\033[38;5;33m", cli._banner(io.StringIO()))
        with patch.dict(os.environ, {"NO_COLOR": "1", "FORCE_COLOR": "1"}, clear=False):
            self.assertNotIn("\033[", cli._banner(io.StringIO()))

    def test_every_command_exposes_its_own_examples(self):
        for argv in (["mcp"], ["init"], ["config"], ["config", "show"], ["config", "set"],
                     ["config", "add-server"], ["config", "validate"], ["servers"],
                     ["servers", "migrate"], ["facility"], ["facility", "render"],
                     ["facility", "install"], ["doctor"], ["setup"]):
            stdout = io.StringIO()
            with self.subTest(argv=argv), patch("sys.stdout", stdout), self.assertRaises(SystemExit):
                _parse_cli_args(argv + ["-h"])
            text = stdout.getvalue()
            self.assertIn("examples:", text)
            self.assertIn(f"isc-ai {' '.join(argv)}", text)

    def test_facility_install_honours_project_dir_flag(self):
        # The installer must load the policy of --project-dir, not of the shell's cwd.
        from unittest.mock import Mock
        constructed = {}

        def fake_server(project_dir=None):
            constructed["project_dir"] = project_dir
            server = Mock()
            server.initialize_ccr_mutation.return_value = {"ok": 1, "state": "UNCHANGED"}
            return server

        with patch("isc_ai.cli.IRISMCPServer", side_effect=fake_server), patch("sys.stdout", io.StringIO()):
            code = main(["facility", "--project-dir", str(self.project), "install",
                         "--server", "s", "--namespace", "N", "--ccr", "SITE0001"])
            self.assertEqual(code, 0)
            self.assertEqual(constructed["project_dir"], self.project.resolve())
            code = main(["facility", "install", "--server", "s", "--namespace", "N", "--ccr", "SITE0001"])
            self.assertEqual(code, 0)
            self.assertIsNone(constructed["project_dir"])  # no flag: server resolves cwd/env itself

    def test_add_server_rejects_malformed_role(self):
        self.run_cli("init", "--no-servers")
        code, _, err = self.run_cli("add-server", "x", "--host", "h", "--role", "b@se")
        self.assertEqual(code, 1)
        self.assertIn("--role", err)
        self.assertNotIn("servers", self.project_config())

    def test_doctor_reports_project_source_and_init_warns_about_shadowing(self):
        self.run_cli("init", "--no-servers")
        nested = self.project / "sub" / "deeper"
        nested.mkdir(parents=True)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("pathlib.Path.cwd", return_value=nested), patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            main(["doctor"])
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["project_source"], "ancestor (2 levels above cwd)")
        self.assertIn("inherited from ancestor", stderr.getvalue())
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            self.assertEqual(main(["init", str(nested)]), 0)
        self.assertIn("shadows it", stderr.getvalue())
        self.assertTrue((nested / STATE_DIR / "config.json").is_file())
        # A configuration living in HOME governs every deployment below it: warn loudly.
        (self.home / STATE_DIR).mkdir(exist_ok=True)
        iris_mcp_config.write_json(self.home / STATE_DIR / "config.json", {"version": 1, "site": {"name": "home"}})
        orphan = self.home / "deployments" / "x"
        orphan.mkdir(parents=True)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("pathlib.Path.cwd", return_value=orphan), patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            main(["doctor"])
        self.assertEqual(json.loads(stdout.getvalue())["site"], "home")
        self.assertIn("HOME directory", stderr.getvalue())
        # init on a missing directory does not mkdir -p a typo.
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            self.assertEqual(main(["init", str(self.root / "typo" / "dir")]), 1)
        self.assertIn("not an existing directory", stderr.getvalue())
        self.assertFalse((self.root / "typo").exists())


if __name__ == "__main__":
    unittest.main()
