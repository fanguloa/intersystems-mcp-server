"""isc-ai config from-ccr: IRIS connections derived from CCR portal System records."""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from isc_ai import ccr_topology, cli

TRAK = {
    "code": "T2026", "active": True, "deprecated": False,
    "environments": {
        "BASE": {"namespace": "BASE-SITE", "instance": "TRAKBASE01",
                 "management_portal": "https://10.0.0.4/trakbase01/csp/sys/UtilHome.csp"},
        "TEST": {"tier": "2"},
        "UAT": {"namespace": "UAT-SITE", "management_portal": "https://10.0.0.6/csp/sys/UtilHome.csp"},
        "LIVE": {"namespace": "LIVE-SITE", "management_portal": "https://live.example.invalid/csp/sys/UtilHome.csp"},
    },
}
INT = {
    "code": "INT", "active": True,
    "environments": {
        "BASE": {"namespace": "INT", "management_portal": "http://int.example.invalid:52773/intbase/csp/sys/%25CSP.Portal.Home.zen"},
        "LIVE": {},
    },
}
OLD = {"code": "T2019", "active": True, "environments": {"BASE": {"namespace": "OLD",
       "management_portal": "https://old.example.invalid/csp/sys/UtilHome.csp"}}}

FAKE_PORTAL = textwrap.dedent('''
    import json, sys
    systems = json.loads(sys.argv[1])
    login = {"done": False}
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        if msg["method"] == "initialize":
            result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "fake-ccr"}}
        else:
            name, args = msg["params"]["name"], msg["params"]["arguments"]
            if name == "ccr_lookup":
                code = args["system_code"].split("/")[1]
                if code == "AUTH" and not login["done"]:
                    body = {"error": "AUTH_REQUIRED", "message": "login"}
                elif code == "AUTH":
                    body = systems["T2026"]
                else:
                    body = systems.get(code, {"error": "POLICY", "message": "system code not found"})
            elif name == "ccr_login":
                login["done"] = True
                body = {"state": "PENDING"}
            else:
                body = {"authenticated": login["done"]}
            result = {"content": [{"type": "text", "text": json.dumps(body)}]}
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
''')


class DerivationTests(unittest.TestCase):
    def test_links_give_host_port_and_prefix(self):
        self.assertEqual(ccr_topology.parse_link("https://h.example/trakbase01/csp/sys/UtilHome.csp"),
                         ("https", "h.example", 443, "/trakbase01"))
        self.assertEqual(ccr_topology.parse_link("https://10.0.0.6/csp/sys/UtilHome.csp"), ("https", "10.0.0.6", 443, ""))
        self.assertEqual(ccr_topology.parse_link("http://h:52773/a/b/csp/sys/x"), ("http", "h", 52773, "/a/b"))
        with self.assertRaises(ccr_topology.TopologyError):
            ccr_topology.parse_link("/_Studio.SourceControl.UI.cls?Namespace=")

    def test_integration_systems_are_their_own_layer(self):
        for code in ("INT", "I2024", "INT2024"):
            self.assertEqual(ccr_topology.layer_of(code), "int")
        self.assertEqual(ccr_topology.layer_of("T2026"), "")

    def test_plan_builds_one_connection_per_environment(self):
        result = ccr_topology.plan("site", [TRAK, INT])
        self.assertEqual(sorted(result["servers"]), ["sitebase", "sitebaseint", "sitelive", "siteuat"])
        base = result["servers"]["sitebase"]
        self.assertEqual((base["host"], base["port"], base["path_prefix"], base["namespace"], base["env_role"]),
                         ("10.0.0.4", 443, "/trakbase01", "BASE-SITE", "BASE"))
        self.assertEqual(base["username"], "{env:SITE_BASE_USER}")
        self.assertTrue(result["servers"]["sitelive"]["is_production"])
        self.assertFalse(result["servers"]["siteuat"]["is_production"])
        self.assertEqual(result["servers"]["sitebaseint"]["port"], 52773)
        self.assertEqual((base["scheme"], result["servers"]["sitebaseint"]["scheme"]), ("https", "http"))
        self.assertEqual(result["canonical_base"]["server_id"], "sitebase")
        self.assertIn({"system": "INT", "environment": "LIVE",
                       "reason": "no namespace or Management Portal link in the CCR portal"}, result["skipped"])
        self.assertEqual(result["credentials"][:2], ["SITE_BASE_USER", "SITE_BASE_PASSWORD"])

    def test_an_endpoint_replaces_the_address_but_keeps_the_portal_namespace(self):
        endpoints = ccr_topology.parse_endpoints(["siteuat=http://10.0.0.5/uat02"])
        result = ccr_topology.plan("SITE", [TRAK], endpoints=endpoints)
        uat = result["servers"]["siteuat"]
        self.assertEqual((uat["scheme"], uat["host"], uat["port"], uat["path_prefix"], uat["namespace"]),
                         ("http", "10.0.0.5", 80, "/uat02", "UAT-SITE"))
        with self.assertRaises(ccr_topology.TopologyError):
            ccr_topology.plan("SITE", [TRAK], endpoints=ccr_topology.parse_endpoints(["sitetypo=http://h"]))
        with self.assertRaises(ccr_topology.TopologyError):
            ccr_topology.parse_endpoints(["siteuat=ftp://h"])

    def test_a_host_alias_replaces_the_portal_address(self):
        aliases = ccr_topology.parse_aliases(["10.0.0.4=base.example.invalid"])
        result = ccr_topology.plan("SITE", [TRAK], host_aliases=aliases)
        self.assertEqual(result["servers"]["sitebase"]["host"], "base.example.invalid")
        self.assertEqual(result["canonical_base"]["host"], "base.example.invalid")
        self.assertEqual(result["servers"]["siteuat"]["host"], "10.0.0.6")
        with self.assertRaises(ccr_topology.TopologyError):
            ccr_topology.parse_aliases(["no-equals"])

    def test_the_newest_trakcare_system_wins(self):
        result = ccr_topology.plan("SITE", [OLD, TRAK])
        self.assertEqual(result["systems"], ["T2026"])
        self.assertEqual(result["servers"]["sitebase"]["namespace"], "BASE-SITE")
        self.assertIn({"system": "T2019", "reason": "superseded by T2026"}, result["skipped"])

    def test_inactive_systems_and_empty_answers_are_refused(self):
        with self.assertRaises(ccr_topology.TopologyError):
            ccr_topology.plan("SITE", [dict(TRAK, active=False)])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.saved = self.root / "site-ccr.json"
        self.saved.write_text(json.dumps([TRAK, INT]), encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.root), "USERPROFILE": str(self.root),
                                               "XDG_CONFIG_HOME": str(self.root / ".config")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_plan_writes_nothing(self):
        code, out, _ = self.run_cli("config", "--project-dir", str(self.project), "from-ccr", "SITE",
                                    "--from-json", str(self.saved))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["mode"], "plan")
        self.assertFalse((self.project / ".isc-ai").exists())

    def test_apply_writes_a_valid_project_with_references_only(self):
        self.assertEqual(self.run_cli("init", str(self.project), "--no-servers")[0], 0)
        code, out, err = self.run_cli("config", "--project-dir", str(self.project), "from-ccr", "SITE",
                                      "--from-json", str(self.saved), "--apply")
        self.assertEqual(code, 0, err)
        data = json.loads((self.project / ".isc-ai" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(data["site"]["ccr_id"]["prefix"], "SITE")
        self.assertEqual(data["canonical_base"]["path_prefix"], "/trakbase01")
        self.assertEqual(data["servers"]["sitelive"]["password"], "{env:SITE_LIVE_PASSWORD}")
        self.assertIn("isc-ai secrets set SITE_BASE_USER", err)

    def test_apply_needs_an_initialized_project(self):
        code, _, err = self.run_cli("config", "--project-dir", str(self.project), "from-ccr", "SITE",
                                    "--from-json", str(self.saved), "--apply")
        self.assertNotEqual(code, 0)
        self.assertIn("isc-ai init", err)

    def test_without_a_site_and_without_a_terminal_it_asks_for_one(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            code, _, err = self.run_cli("config", "--project-dir", str(self.project), "from-ccr")
        self.assertNotEqual(code, 0)
        self.assertIn("from-ccr <SITE>", err)


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        script = Path(self.tmp.name) / "fake_portal.py"
        script.write_text(FAKE_PORTAL, encoding="utf-8")
        self.argv = [sys.executable, str(script), json.dumps({"T2026": TRAK, "INT": INT})]

    def test_systems_come_through_the_mcp_server_and_unknown_codes_are_reported(self):
        client = ccr_topology.PortalClient(self.argv)
        try:
            systems, missing = ccr_topology.fetch_systems(client, "SITE", ["T2026", "T2099", "INT"])
        finally:
            client.close()
        self.assertEqual([s["code"] for s in systems], ["T2026", "INT"])
        self.assertEqual(missing, ["T2099"])

    def test_an_expired_session_logs_in_once_and_retries(self):
        logins = []
        client = ccr_topology.PortalClient(self.argv)
        try:
            with self.assertRaises(ccr_topology.PortalAuthRequired):
                ccr_topology.fetch_systems(client, "SITE", ["AUTH"])

            def login(c):
                logins.append(1)
                c.tool("ccr_login", {})

            systems, _ = ccr_topology.fetch_systems(client, "SITE", ["AUTH"], login=login)
        finally:
            client.close()
        self.assertEqual(logins, [1])
        self.assertEqual(systems[0]["code"], "T2026")

    def test_the_command_comes_from_the_override_then_opencode(self):
        root = Path(self.tmp.name)
        self.assertEqual(ccr_topology.portal_command({"ISC_AI_CCR_PORTAL_COMMAND": '["a", "b"]'}), (["a", "b"], {}))
        (root / "opencode").mkdir()
        (root / "opencode" / "opencode.json").write_text(json.dumps({"mcp": {"ccr-portal": {
            "command": ["C:/tools/ccr-portal.exe"],
            "environment": {"CCR_PORTAL_USERNAME": "me@example.invalid", "TOKEN": "{env:X}"}}}}), encoding="utf-8")
        argv, extra = ccr_topology.portal_command({"XDG_CONFIG_HOME": str(root)})
        self.assertEqual(argv, ["C:/tools/ccr-portal.exe"])
        self.assertEqual(extra, {"CCR_PORTAL_USERNAME": "me@example.invalid"})


class CatalogTests(unittest.TestCase):
    def test_packaged_catalog_holds_codes_and_valid_fixes(self):
        data = json.loads(ccr_topology.CATALOG_RESOURCE.read_text(encoding="utf-8"))
        for site, entry in data["sites"].items():
            with self.subTest(site=site):
                self.assertLessEqual(set(entry), {"name", "systems", "host_aliases", "endpoints"})
                self.assertTrue(all(ccr_topology.SYSTEM_RE.match(code) for code in entry["systems"]))
                ccr_topology.parse_aliases([f"{k}={v}" for k, v in entry.get("host_aliases", {}).items()])
                for server_id in ccr_topology.parse_endpoints(
                        [f"{k}={v}" for k, v in entry.get("endpoints", {}).items()]):
                    self.assertTrue(server_id.startswith(site.lower()), server_id)

    def test_catalog_fixes_apply_and_flags_win(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / ".isc-ai").mkdir()
        (root / ".isc-ai" / "ccr-sites.json").write_text(json.dumps({"sites": {"SITE": {
            "name": "Mine", "systems": ["T2026"], "host_aliases": {"10.0.0.4": "base.example.invalid"},
            "endpoints": {"siteuat": "http://10.0.0.5/uat02"}}}}), encoding="utf-8")
        saved = root / "systems.json"
        saved.write_text(json.dumps([TRAK]), encoding="utf-8")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": str(root), "USERPROFILE": str(root)}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["config", "--project-dir", str(root), "from-ccr", "SITE", "--from-json", str(saved),
                             "--endpoint", "sitelive=https://live2.example.invalid"])
        self.assertEqual(code, 0)
        servers = json.loads(out.getvalue())["servers"]
        self.assertEqual(servers["sitebase"]["host"], "base.example.invalid")
        self.assertEqual((servers["siteuat"]["host"], servers["siteuat"]["path_prefix"]), ("10.0.0.5", "/uat02"))
        self.assertIn("via endpoint override", servers["siteuat"]["description"])
        self.assertEqual(servers["sitelive"]["host"], "live2.example.invalid")

    def test_a_user_catalog_replaces_a_packaged_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ccr-sites.json"
            path.write_text(json.dumps({"sites": {"ZZ": {"name": "Mine", "systems": ["T2030"]}}}), encoding="utf-8")
            catalog = ccr_topology.load_catalog(path)
        self.assertEqual(catalog["ZZ"]["systems"], ["T2030"])


if __name__ == "__main__":
    unittest.main()
