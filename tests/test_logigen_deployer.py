"""isc-ai logi: the deployer converts the generated XML to binary Logi files.

Logi renders the generated .cls.xml/.cat.xml but stamps every page with its evaluation
watermark; binaries made by the TrakCare converter render clean. These tests pin the
contract of the generated class; the conversion itself only runs inside IRIS.
"""

import json
import re
import tempfile
import unittest
from pathlib import Path

from isc_ai.logigen.build import generate
from isc_ai.logigen.deployer import deployer_source
from tests.test_logigen import COMPONENT, REPORT, SOURCE


def method(source, name):
    match = re.search(r"\nClassMethod " + name + r"\(.*?\n\}\n", source, re.S)
    if not match:
        raise AssertionError(f"method {name} not found")
    return match.group(0)


class LogiDeployerConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        source = self.root / "src" / "Demo" / "Reports" / "spDemo.cls"
        source.parent.mkdir(parents=True)
        source.write_text(SOURCE, encoding="utf-8")
        specs = self.root / "jreports" / "specs"
        specs.mkdir(parents=True)
        (specs / "Demo.json").write_text(json.dumps(REPORT), encoding="utf-8")
        (specs / "Items.json").write_text(json.dumps(COMPONENT), encoding="utf-8")
        self.manifest = self.root / "jreports" / "manifest.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifest(self, catalog):
        self.manifest.write_text(json.dumps({"sourceRoot": "../src", "outputRoot": "../web",
                                             "components": {"Items": "specs/Items.json"},
                                             "catalogs": [catalog]}), encoding="utf-8")

    def deployer(self, extra=None):
        catalog = {"name": "Demo", "folder": "demo", "deployerClass": "Demo.Reports.Deploy",
                   "database": "Data Source 1", "jdbcUrl": "jdbc:IRIS://localhost:1972/DEMO",
                   "reports": ["specs/Demo.json"]}
        catalog.update(extra or {})
        self.write_manifest(catalog)
        result = generate(self.manifest)
        self.assertTrue(result.ok, result.problems)
        return (self.root / "src" / "Demo" / "Reports" / "Deploy.cls").read_text(encoding="utf-8")

    def test_deploy_converts_after_the_hash_check_and_before_the_grants(self):
        source = self.deployer()
        self.assertIn("Parameter CONVERT = 1;", source)
        deploy = method(source, "Deploy")
        self.assertIn("pConvert As %Boolean = {..#CONVERT}", deploy)
        order = [deploy.index(step) for step in ("..CheckFiles(", "..Convert(", "..Grant()")]
        self.assertEqual(order, sorted(order), "hash check, then conversion, then grants")

    def test_the_converter_runs_from_the_temp_directory_one_file_at_a_time(self):
        convert = method(self.deployer(), "Convert")
        self.assertIn("Report.JReport.Configuration", convert)
        self.assertIn("PathToJavaConverterScript", convert)
        self.assertIn("%SYS.System).TempDirectory()", convert)
        self.assertIn("\"cd '\" _ work", convert, "the converter must not start in the IRIS data directory")
        self.assertIn("-files '\" _ name", convert)
        self.assertIn("LogiReport_RF*.log", convert, "the converter log is removed from the temp directory")

    def test_success_is_a_fresh_binary_with_the_logi_header_not_the_exit_code(self):
        source = self.deployer()
        self.assertIn("$Char(250, 206, 250, 222)", method(source, "IsBinary"))
        convert = method(source, "Convert")
        self.assertIn("'..IsBinary(binary) || (##class(%File).GetFileDateModified(binary) = before)", convert)

    def test_verify_checks_the_binaries_only_when_conversion_is_on(self):
        source = self.deployer()
        self.assertIn("If ..#CONVERT Set sc = $System.Status.AppendStatus(sc, ..CheckBinaries(pTargetDir))",
                      method(source, "Verify"))
        self.assertIn("Binary older than its XML", method(source, "CheckBinaries"))

    def test_conversion_can_be_turned_off_per_catalog(self):
        self.assertIn("Parameter CONVERT = 0;", self.deployer({"convertToBinary": False}))

    def test_a_non_boolean_convert_flag_is_a_contract_violation(self):
        with self.assertRaises(KeyError):
            deployer_source("Demo.Deploy", "demo", "Demo", {}, [], convert="yes")
        self.write_manifest({"name": "Demo", "folder": "demo", "deployerClass": "Demo.Reports.Deploy",
                             "database": "Data Source 1", "jdbcUrl": "jdbc:IRIS://localhost:1972/DEMO",
                             "reports": ["specs/Demo.json"], "convertToBinary": "yes"})
        result = generate(self.manifest)
        self.assertFalse(result.ok)
        self.assertIn("convertToBinary", result.problems[0])


if __name__ == "__main__":
    unittest.main()
