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

Query Items(pRowId As %String) As %Query(ROWSPEC = "Item,Detail,SectionOrder:%Integer,Section") [ SqlProc ]
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

    def test_a_grouped_table_has_column_titles_once_and_a_heading_row_per_group(self):
        grouped = dict(COMPONENT, layouts=[{"type": "groupedTable", "groupBy": "SectionOrder", "groupTitle": "Section",
                                            "columns": [["Check", "Item", 0.5], ["Answer", "Detail", 0.5]]}])
        (self.root / "jreports" / "specs" / "Items.json").write_text(json.dumps(grouped), encoding="utf-8")
        self.assertTrue(generate(self.manifest).ok)
        root = ET.parse(self.output("Items.cls.xml")).getroot()
        header_texts = [f.findtext("Text") for f in root.find(".//BandedHeader").iter("TextField")]
        self.assertEqual(header_texts, ["Check", "Answer"])
        group = root.find(".//GroupPanel")
        self.assertEqual(group.findtext("GroupBy"), "SECTIONORDER")
        self.assertEqual(group.find("GroupHeaderPanel/DBField/ColumnName").text, "SECTION")
        self.assertEqual([f.findtext("ColumnName") for f in group.find("DetailPanel").iter("DBField")], ["ITEM", "DETAIL"])
        self.assertTrue(generate(self.manifest, check=True).ok)

    def test_an_unknown_grouped_table_column_is_a_contract_violation(self):
        grouped = dict(COMPONENT, layouts=[{"type": "groupedTable", "groupBy": "Nope", "groupTitle": "Item",
                                            "columns": [["Check", "Item", 1.0]]}])
        (self.root / "jreports" / "specs" / "Items.json").write_text(json.dumps(grouped), encoding="utf-8")
        result = generate(self.manifest)
        self.assertFalse(result.ok)
        self.assertIn("contract violation", result.problems[0])

    def test_record_per_page_starts_only_the_first_body_panel_on_a_new_page(self):
        paged = dict(REPORT, recordPerPage=True)
        (self.root / "jreports" / "specs" / "Demo.json").write_text(json.dumps(paged), encoding="utf-8")
        self.assertTrue(generate(self.manifest).ok)
        root = ET.parse(self.output("Demo.cls.xml")).getroot()
        page_panel = root.find(".//BandedPagePanel")
        body = page_panel.findall("DetailPanel")
        self.assertEqual(body[0].findtext("OnNewPage"), "true")
        self.assertEqual(sum(1 for p in root.iter() if p.tag == "OnNewPage"), 1)
        self.assertTrue(generate(self.manifest, check=True).ok)

    def test_reports_do_not_start_records_on_a_new_page_by_default(self):
        self.assertTrue(generate(self.manifest).ok)
        root = ET.parse(self.output("Demo.cls.xml")).getroot()
        self.assertFalse(any(p.tag == "OnNewPage" for p in root.iter()))

    def test_a_component_block_can_cross_pages_only_when_asked(self):
        crossing = dict(REPORT, blocks=[{"type": "title", "text": "LIST"},
                                        {"type": "component", "component": "Items", "links": {"RowId": "pRowId"}, "crossPage": True}])
        (self.root / "jreports" / "specs" / "Demo.json").write_text(json.dumps(crossing), encoding="utf-8")
        self.assertTrue(generate(self.manifest).ok)
        root = ET.parse(self.output("Demo.cls.xml")).getroot()
        panels = [p for p in root.iter("DetailPanel") if p.find("SubReport") is not None]
        self.assertEqual(len(panels), 1)
        self.assertEqual(panels[0].findtext("CanCrossPage"), "true")
        self.assertIsNone(panels[0].find("SubReport").find("CanCrossPage"))
        # The default stays unchanged: no other panel crosses pages
        self.assertEqual(sum(1 for p in root.iter() if p.tag == "CanCrossPage"), 1)

    def test_a_signature_block_draws_the_line_and_its_caption_in_one_panel(self):
        signed = dict(REPORT, blocks=REPORT["blocks"] + [
            {"type": "title", "text": "SIGNATURE"},
            {"type": "signature", "text": "Patient signature", "width": 4.0, "height": 0.5}])
        (self.root / "jreports" / "specs" / "Demo.json").write_text(json.dumps(signed), encoding="utf-8")
        self.assertTrue(generate(self.manifest).ok)
        root = ET.parse(self.output("Demo.cls.xml")).getroot()
        for panel in root.iter("DetailPanel"):
            fields = list(panel.iter("TextField"))
            texts = [f.findtext("Text") for f in fields]
            if "Patient signature" in texts:
                self.assertIn("SIGNATURE", texts, "the heading stays with the signature")
                line = next(f for f in fields if f.findtext("DisplayName") == "Rule" and f.findtext("Width") == "4.00000")
                caption = next(f for f in fields if f.findtext("Text") == "Patient signature")
                self.assertGreater(float(caption.findtext("Y")), float(line.findtext("Y")))
                break
        else:
            self.fail("signature caption not found")

    def test_a_signature_caption_can_come_from_a_column_and_sit_at_the_right(self):
        signed = dict(REPORT, blocks=REPORT["blocks"] + [
            {"type": "signature", "column": "Title", "align": "right", "width": 3.0}])
        (self.root / "jreports" / "specs" / "Demo.json").write_text(json.dumps(signed), encoding="utf-8")
        self.assertTrue(generate(self.manifest).ok)
        root = ET.parse(self.output("Demo.cls.xml")).getroot()
        caption = next(f for f in root.iter("DBField") if f.findtext("ColumnName") == "TITLE"
                       and f.findtext("HorizontalAlignment") == "Center")
        content_w = 8.26772 - 2 * 0.39
        self.assertAlmostEqual(float(caption.findtext("X")), content_w - 3.0, places=3)

    def test_an_unknown_signature_align_is_rejected(self):
        signed = dict(REPORT, blocks=[{"type": "signature", "text": "x", "align": "middle"}])
        (self.root / "jreports" / "specs" / "Demo.json").write_text(json.dumps(signed), encoding="utf-8")
        self.assertFalse(generate(self.manifest).ok)

    def test_a_table_note_is_a_second_line_inside_the_same_row_panel(self):
        noted = dict(COMPONENT, layouts=[{"type": "table", "columns": [["Item", "Item", 1.0]],
                                          "note": {"label": "Notes:", "column": "Detail", "indent": 0.2}}])
        (self.root / "jreports" / "specs" / "Items.json").write_text(json.dumps(noted), encoding="utf-8")
        self.assertTrue(generate(self.manifest).ok)
        root = ET.parse(self.output("Items.cls.xml")).getroot()
        detail = root.find(".//BandedPagePanel/DetailPanel")
        self.assertEqual([f.findtext("ColumnName") for f in detail.iter("DBField")], ["ITEM", "DETAIL"])
        note = next(f for f in detail.iter("DBField") if f.findtext("ColumnName") == "DETAIL")
        self.assertGreater(float(note.findtext("Y")), 0.0)
        self.assertEqual(float(detail.findtext("Height")), 0.48)
        self.assertIn("Notes:", [f.findtext("Text") for f in detail.iter("TextField")])

    def test_a_section_compresses_without_records_and_carries_its_band_in_the_header(self):
        section = dict(COMPONENT, layouts=[{"type": "section", "title": "HOSPITAL STAY", "columns": 2,
                                            "cells": [["Item", "Item"], ["Detail", "Detail"]]}])
        (self.root / "jreports" / "specs" / "Items.json").write_text(json.dumps(section), encoding="utf-8")
        self.assertTrue(generate(self.manifest).ok)
        root = ET.parse(self.output("Items.cls.xml")).getroot()
        banded = root.find(".//BandedObject")
        self.assertEqual(banded.findtext("CompressIfNoRecord"), "true")
        header = banded.find("BandedHeader")
        self.assertIsNone(header.find("Invisible"))
        self.assertIn("HOSPITAL STAY", [f.findtext("Text") for f in header.iter("TextField")])
        detail = banded.find("BandedPagePanel/DetailPanel")
        self.assertEqual([f.findtext("ColumnName") for f in detail.iter("DBField")], ["ITEM", "DETAIL"])
        self.assertTrue(generate(self.manifest, check=True).ok)


if __name__ == "__main__":
    unittest.main()
