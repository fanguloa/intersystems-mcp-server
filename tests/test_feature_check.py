"""Hermetic tests for the TrakCare websys.Feature presence check."""

from unittest.mock import Mock

from isc_ai import feature_check
from tests.hermetic_test_support import HermeticTestCase, synthetic_client, synthetic_server
from tests.test_ccr_verification import FakeToolClient, download_result, fake_server


VIEWER_SOURCE = [
    "Class Custom.Site.Viewer.Lab Extends %RegisteredObject",
    "{",
    'Parameter FEATURENAME = "SiteResultPdfLink";',
    "ClassMethod Render() As %String",
    "{",
    "\tIf '##class(websys.Feature).IsFeatureEnabled(..#FEATURENAME) Quit \"\"",
    '\tSet url = ##class(websys.Feature).GetFeatureParam("SiteLabAccess", "URL")',
    "\tQuit url",
    "}",
    "}",
]


def feature_rows(present):
    def rows(sql, params=None, timeout=None):
        del timeout
        if "FROM websys.FeatureParameter" in sql:
            return []
        if "FROM websys.Feature" in sql:
            return [{"ID": 10 + index, "Code": code, "DatesCoverToday": present[code]}
                    for index, code in enumerate(params) if code in present]
        raise AssertionError(f"Unexpected query: {sql}")
    return rows


class ExtractionTests(HermeticTestCase):
    def test_extracts_literal_calls_and_feature_parameters(self):
        self.assertEqual(feature_check.extract_feature_codes(VIEWER_SOURCE), ["SiteLabAccess", "SiteResultPdfLink"])

    def test_parameter_with_type_and_register_call_are_detected(self):
        source = [
            'Parameter LABFEATURE As STRING = "SiteTyped";',
            'Set sc = ##class(websys.Feature).RegisterFeature("SiteRegistered", "Desc")',
            'Set flag = ##class(websys.Feature).GetFeatureParamAsBoolean("SiteBool", "On")',
        ]
        self.assertEqual(feature_check.extract_feature_codes(source), ["SiteBool", "SiteRegistered", "SiteTyped"])

    def test_plain_mentions_and_dynamic_codes_are_not_references(self):
        source = [
            "/// Controlled by the Feature <b>SiteDocOnly</b>.",
            "Set code = \"SiteNotACall\"",
            "If ##class(websys.Feature).IsFeatureEnabled(code) Quit 1",
        ]
        self.assertEqual(feature_check.extract_feature_codes(source), [])

    def test_itemset_paths_map_only_class_items(self):
        paths = [
            "/itemsetsourcelink/custom_ccrs/cl/SITE/T2026/UAT/cls/Custom/Site/Viewer/Lab.xml#head",
            "/itemsetsourcelink/custom_ccrs/cl/SITE/T2026/UAT/cls/Custom/Site/Viewer/Lab.xml#head",
            "/itemsetsourcelink/custom_ccrs/cl/SITE/T2026/UAT/misc/User/CTOverride/12.xml#head",
            "/itemsetsourcelink/custom_ccrs/cl/SITE/T2026/UAT/cls/Custom/Site/Model/List.xml",
            "",
        ]
        self.assertEqual(feature_check.itemset_paths_to_classes(paths),
                         ["Custom.Site.Viewer.Lab", "Custom.Site.Model.List"])


class FeatureCheckToolTests(HermeticTestCase):
    def call(self, client, servers=None, **arguments):
        server = synthetic_server(client, servers=servers)
        return server.handle_tool_call("iris_trakcare_feature_check", {"server_id": "fake", **arguments})

    def test_reports_missing_and_present_codes_with_owning_classes(self):
        client = synthetic_client()
        client.get_document = Mock(return_value=VIEWER_SOURCE)
        client.run_query = Mock(side_effect=feature_rows({"SiteLabAccess": 0}))

        result = self.call(client, classes=["Custom.Site.Viewer.Lab"], codes=["SiteExplicit"])

        self.assertNotIn("isError", result)
        record = result["structuredContent"]
        self.assertEqual(record["status"], "FEATURE_MISSING")
        self.assertEqual(record["missing"], ["SiteExplicit", "SiteResultPdfLink"])
        by_code = {entry["code"]: entry for entry in record["features"]}
        self.assertEqual(by_code["SiteLabAccess"]["status"], "PRESENT")
        self.assertIs(by_code["SiteLabAccess"]["dates_cover_today"], False)
        self.assertEqual(by_code["SiteResultPdfLink"]["referenced_by"], ["Custom.Site.Viewer.Lab"])
        self.assertEqual(by_code["SiteExplicit"]["referenced_by"], [])
        client.get_document.assert_called_once_with("Custom.Site.Viewer.Lab.cls", strict=True)
        sql, params = client.run_query.call_args.args[:2]
        self.assertIn("FROM websys.Feature WHERE Code IN (?, ?, ?)", sql)
        self.assertEqual(params, ["SiteExplicit", "SiteLabAccess", "SiteResultPdfLink"])
        self.assertIn("FEATURE_MISSING", result["content"][0]["text"])
        self.assertIn("do not cover today", result["content"][0]["text"])

    def test_all_present_is_ok(self):
        client = synthetic_client()
        client.run_query = Mock(side_effect=feature_rows({"SiteA": 1}))
        result = self.call(client, codes=["SiteA"])
        self.assertEqual(result["structuredContent"]["status"], "FEATURE_CHECK_OK")

    def test_reference_comparison_reports_parameter_status_without_values(self):
        target = synthetic_client()
        reference = synthetic_client()

        def rows_for(params_by_code, present):
            def rows(sql, params=None, timeout=None):
                if "FROM websys.FeatureParameter" in sql:
                    return [{"Code": code, "Name": name, "Value": value}
                            for code, values in params_by_code.items() for name, value in values.items()]
                return feature_rows(present)(sql, params, timeout)
            return rows

        target.run_query = Mock(side_effect=rows_for(
            {"SiteA": {"URL": "https://target-secret.invalid", "Extra": "x"}}, {"SiteA": 1}))
        reference.run_query = Mock(side_effect=rows_for(
            {"SiteA": {"URL": "https://reference-secret.invalid", "Path": "p"},
             "SiteB": {"Path": "q"}}, {"SiteA": 1, "SiteB": 1}))
        server = synthetic_server(servers={"fake": {"namespace": "UAT-SITE"}, "ref": {"namespace": "BASE-SITE"}})
        server.get_client = Mock(side_effect=lambda sid, namespace_override=None: reference if sid == "ref" else target)

        result = server.handle_tool_call("iris_trakcare_feature_check", {
            "server_id": "fake", "codes": ["SiteA", "SiteB"], "reference_server_id": "ref",
        })

        self.assertNotIn("isError", result)
        by_code = {entry["code"]: entry for entry in result["structuredContent"]["features"]}
        self.assertEqual(by_code["SiteB"]["status"], "MISSING")
        self.assertEqual(by_code["SiteB"]["reference_status"], "PRESENT")
        self.assertEqual(by_code["SiteB"]["parameters"], [{"name": "Path", "status": "MISSING_IN_TARGET"}])
        self.assertEqual(by_code["SiteA"]["parameters"], [
            {"name": "Extra", "status": "EXTRA_IN_TARGET"},
            {"name": "Path", "status": "MISSING_IN_TARGET"},
            {"name": "URL", "status": "DIFFERENT"},
        ])
        text = result["content"][0]["text"] + repr(result["structuredContent"])
        self.assertNotIn("secret", text)
        server.get_client.assert_any_call("ref", namespace_override="BASE-SITE")

    def test_invalid_inputs_fail_before_configuration_or_queries(self):
        cases = (
            {},
            {"classes": "Custom.Site.Viewer"},
            {"classes": ["Custom.Site.Viewer.cls"]},
            {"classes": ["%SYS.Task"]},
            {"codes": ["bad code"]},
            {"codes": ["x"] * (feature_check.MAX_CODES + 1)},
            {"codes": ["SiteA"], "reference_server_id": ""},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                client = synthetic_client()
                client.run_query = Mock(side_effect=AssertionError("Query must not run"))
                server = synthetic_server(client)
                result = server.handle_tool_call("iris_trakcare_feature_check", {"server_id": "fake", **arguments})
                self.assertTrue(result["isError"])
                server.load_configuration.assert_not_called()
                client.run_query.assert_not_called()


class PostLoadFeatureCheckTests(HermeticTestCase):
    ITEM = "/itemsetsourcelink/custom_ccrs/cl/SITE/T2026/UAT/cls/Custom/Site/Viewer/Lab.xml#head"

    def client(self, present=None, query_error=None):
        client = FakeToolClient(download_result(), promotion=True,
                                document_reads={"Custom.Site.Viewer.Lab.cls": [VIEWER_SOURCE]})

        def rows(sql, params=None, timeout=None):
            if query_error is not None:
                raise query_error
            if sql == feature_check.ITEMSET_ITEMS_SQL:
                self.assertEqual(params, [7])
                return [{"ItemPath": self.ITEM}]
            return feature_rows(present or {})(sql, params, timeout)

        client.run_query = Mock(side_effect=rows)
        return client

    def load(self, client, **extra):
        return fake_server(client).handle_tool_call("iris_ccr_download_load", {
            "server_id": "fake", "itemset_id": 42, "namespace": "UAT-ACHS", **extra,
        })

    def test_successful_load_reports_missing_features_without_changing_outcome(self):
        client = self.client(present={"SiteLabAccess": 1})
        result = self.load(client)

        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["structuredContent"]["status"], "LOAD_REPORTED_SUCCESS_UNVERIFIED")
        check = result["structuredContent"]["feature_check"]
        self.assertEqual(check["status"], "FEATURE_MISSING")
        self.assertEqual(check["missing"], ["SiteResultPdfLink"])
        self.assertEqual(check["classes_scanned"], ["Custom.Site.Viewer.Lab"])
        self.assertIn("FEATURE_MISSING", result["content"][0]["text"])
        self.assertIn("does not travel in CCR ItemSets", result["content"][0]["text"])

    def test_check_failure_is_fail_soft(self):
        client = self.client(query_error=RuntimeError("transport down"))
        result = self.load(client)

        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["structuredContent"]["status"], "LOAD_REPORTED_SUCCESS_UNVERIFIED")
        self.assertEqual(result["structuredContent"]["feature_check"]["status"], "FEATURE_CHECK_UNAVAILABLE")
        self.assertNotIn("transport down", result["content"][0]["text"])

    def test_opt_out_and_download_only_skip_the_check(self):
        for extra in ({"feature_check": False}, {"load": False}):
            with self.subTest(extra=extra):
                response = download_result(load=extra.get("load", True))
                client = self.client()
                client.response = response
                result = self.load(client, **extra)
                self.assertFalse(result.get("isError", False))
                self.assertNotIn("feature_check", result["structuredContent"])
                client.run_query.assert_not_called()

    def test_non_boolean_opt_out_is_rejected_before_remote_access(self):
        client = self.client()
        server = fake_server(client)
        requested = []
        server.get_client = lambda *args, **kwargs: requested.append(True)
        result = server.handle_tool_call("iris_ccr_download_load", {
            "server_id": "fake", "itemset_id": 42, "feature_check": "no",
        })
        self.assertTrue(result["isError"])
        self.assertEqual(requested, [])
