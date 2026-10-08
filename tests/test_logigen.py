"""isc-ai logi: the Logi report generator is deterministic and enforces its data contract.

The fixture is a minimal site: one ObjectScript class with two SqlProc queries (a one-row
main dataset and a component dataset), a component spec, a report spec and a manifest.
"""

import hashlib
import io
import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path

from isc_ai.cli import main
from isc_ai.logigen.build import generate
from isc_ai.logigen.deployer import grant_call

SOURCE = """/// Demo data of a printable.
Class Demo.Reports.spDemo Extends %RegisteredObject
{

Query Header(pRowId As %String) As %Query(ROWSPEC = "RowId,Title,Unit,Notes") [ SqlProc ]
{
}

Query Items(pRowId As %String) As %Query(ROWSPEC = "Item,Detail") [ SqlProc ]
{
}

}
"""

COMPONENT = {
    "name": "Items", "source": {"class": "Demo.Reports.spDemo", "query": "Items"},
    "tabs": ["Items"],
    "layouts": [{"type": "table", "columns": [["Item", "Item", 0.4], ["Detail", "Detail", 0.6]]}],
}

REPORT = {
    "name": "Demo", "source": {"class": "Demo.Reports.spDemo", "query": "Header"},
    "page": {"size": "A4", "orientation": "Portrait", "margin": 0.39},
    "pageHeader": {"component": "Items", "height": 0.5, "links": {"RowId": "pRowId"}},
    "pageFooter": {"component": "Items", "height": 0.3, "links": {"RowId": "pRowId"}},
    "blocks": [
        {"type": "band", "text": "GENERAL"},
        {"type": "grid", "columns": 2, "cells": [["Title", "Title"], ["Unit", "Unit"]]},
        {"type": "subband", "text": "NOTES"},
        {"type": "textbox", "label": "Notes", "column": "Notes", "height": 0.3},
        {"type": "component", "component": "Items", "links": {"RowId": "pRowId"}, "height": 0.4},
    ],
}


class LogiGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        source = self.root / "src" / "Demo" / "Reports" / "spDemo.cls"
        source.parent.mkdir(parents=True)
        source.write_text(SOURCE, encoding="utf-8")
        specs = self.root / "jreports" / "specs"
        specs.mkdir(parents=True)
        (specs / "Items.json").write_text(json.dumps(COMPONENT), encoding="utf-8")
        (specs / "Demo.json").write_text(json.dumps(REPORT), encoding="utf-8")
        self.manifest = self.root / "jreports" / "manifest.json"
        self.write_manifest({})

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifest(self, extra):
        catalog = {"name": "Demo", "folder": "demo", "deployerClass": "Demo.Reports.Deploy",
                   "database": "Data Source 1", "jdbcUrl": "jdbc:IRIS://localhost:1972/DEMO",
                   "reports": ["specs/Demo.json"]}
        catalog.update(extra)
        self.manifest.write_text(json.dumps({
            "sourceRoot": "../src", "outputRoot": "../web",
            "components": {"Items": "specs/Items.json"}, "catalogs": [catalog]}), encoding="utf-8")

    def output(self, name):
        return self.root / "web" / "demo" / name

    def test_generation_writes_the_catalog_reports_and_deployer_and_check_is_clean(self):
        result = generate(self.manifest)
        self.assertTrue(result.ok, result.problems)
        for name in ("Demo.cat.xml", "Demo.cls.xml", "Items.cls.xml"):
            ET.parse(self.output(name))  # well-formed XML
        self.assertTrue((self.root / "src" / "Demo" / "Reports" / "Deploy.cls").is_file())
        self.assertTrue(generate(self.manifest, check=True).ok)

    def test_generation_is_deterministic(self):
        generate(self.manifest)
        first = {p.name: p.read_bytes() for p in (self.root / "web" / "demo").iterdir()}
        generate(self.manifest)
        second = {p.name: p.read_bytes() for p in (self.root / "web" / "demo").iterdir()}
        self.assertEqual(first, second)

    def test_check_reports_a_hand_edited_file(self):
        generate(self.manifest)
        report = self.output("Demo.cls.xml")
        report.write_text(report.read_text(encoding="utf-8") + "<!-- edit -->\n", encoding="utf-8")
        result = generate(self.manifest, check=True)
        self.assertFalse(result.ok)
        self.assertTrue(any("Demo.cls.xml" in problem for problem in result.problems))

    def test_an_unknown_column_is_a_contract_violation_and_nothing_is_written(self):
        broken = dict(REPORT, blocks=[{"type": "grid", "columns": 1, "cells": [["X", "NoSuchColumn"]]}])
        (self.root / "jreports" / "specs" / "Demo.json").write_text(json.dumps(broken), encoding="utf-8")
        result = generate(self.manifest)
        self.assertFalse(result.ok)
        self.assertIn("contract violation", result.problems[0])
        self.assertFalse(self.output("Demo.cls.xml").exists())

    def test_the_deployer_embeds_the_hash_of_every_file_and_the_procedures(self):
        generate(self.manifest)
        deployer = (self.root / "src" / "Demo" / "Reports" / "Deploy.cls").read_text(encoding="utf-8")
        for name in ("Demo.cat.xml", "Demo.cls.xml", "Items.cls.xml"):
            digest = hashlib.sha256(self.output(name).read_bytes()).hexdigest()
            self.assertIn(f"{name}:", deployer)
            self.assertIn(digest, deployer)
        procedures = re.search(r'Parameter PROCEDURES = "([^"]*)";', deployer).group(1).split(",")
        self.assertEqual(sorted(procedures), ["Demo_Reports.spDemo_Header", "Demo_Reports.spDemo_Items"])
        self.assertIn("GRANT EXECUTE ON", deployer, "plain SQL grant by default")

    def test_a_site_grant_helper_replaces_the_sql_grant(self):
        self.write_manifest({"grantMethod": "Site.Utils.GrantStoredProc", "sqlRole": "SiteRole"})
        generate(self.manifest)
        deployer = (self.root / "src" / "Demo" / "Reports" / "Deploy.cls").read_text(encoding="utf-8")
        self.assertIn("Do ##class(Site.Utils).GrantStoredProc($List(procedures, i), ..#SQLROLE)", deployer)
        self.assertIn('Parameter SQLROLE = "SiteRole";', deployer)
        self.assertNotIn("GRANT EXECUTE ON", deployer)
        with self.assertRaises(KeyError):
            grant_call("NotAMethod")

    def test_headings_share_the_panel_of_the_next_block(self):
        # A detail panel is never split across pages, so this keeps a heading with its content.
        generate(self.manifest)
        root = ET.parse(self.output("Demo.cls.xml")).getroot()
        for panel in root.iter("DetailPanel"):
            texts = [label.findtext("Text") for label in panel.iter("TextField")]
            if "GENERAL" in texts:
                self.assertIn("Title", texts, "the band must sit in the first grid row panel")
                break
        else:
            self.fail("band GENERAL not found")

    def test_the_last_page_footer_sits_at_the_bottom(self):
        generate(self.manifest)
        root = ET.parse(self.output("Demo.cls.xml")).getroot()
        self.assertEqual(root.find(".//BandedPageFooter/SuppressInLastPage").text, "true")
        self.assertEqual(root.find(".//BandedFooter/ToBottom").text, "true")

    def test_theme_overrides_apply_and_unknown_keys_are_rejected(self):
        self.write_manifest({"theme": {"primary": "#123456"}})
        self.assertTrue(generate(self.manifest).ok)
        self.assertIn("ff123456", self.output("Demo.cls.xml").read_text(encoding="utf-8").lower())
        self.write_manifest({"theme": {"nope": "#000000"}})
        self.assertFalse(generate(self.manifest).ok)

    def test_the_cli_reports_json_and_exit_codes(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["logi", "--manifest", str(self.manifest)]), 0)
        self.assertTrue(json.loads(out.getvalue())["written"])
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["logi", "--manifest", str(self.manifest), "--check"]), 0)
        self.output("Demo.cat.xml").write_text("<x/>\n", encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["logi", "--manifest", str(self.manifest), "--check"]), 1)
        self.assertTrue(json.loads(out.getvalue())["problems"])

    def test_generated_files_carry_no_credentials(self):
        generate(self.manifest)
        catalog = self.output("Demo.cat.xml").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?i)<(password|pswd|user(name)?)>[^<]+<", catalog))

    def test_the_catalog_template_is_packaged_with_the_generator(self):
        from isc_ai.logigen.catalog import TEMPLATE
        self.assertTrue(TEMPLATE.is_file())


if __name__ == "__main__":
    unittest.main()
