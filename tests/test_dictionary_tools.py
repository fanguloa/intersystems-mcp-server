"""Hermetic tests for read-only %Dictionary introspection tools."""

from unittest.mock import Mock

from tests.hermetic_test_support import HermeticTestCase, synthetic_client, synthetic_server


class DictionaryToolTests(HermeticTestCase):
    @staticmethod
    def _describe_rows(sql, params=None, timeout=None):
        del timeout
        if params != ["Custom.Site.Patient"]:
            raise AssertionError(f"Unexpected parameters: {params!r}")
        if "FROM %Dictionary.ClassDefinition" in sql:
            return [{
                "NAME": "Custom.Site.Patient", "DESCRIPTION": "Patient | model",
                "SUPERCLASSES": "%Persistent, %JSON.Adaptor", "ABSTRACT": 0,
                "CLASS_TYPE": "persistent", "SQL_TABLE": "Custom_Site.Patient",
                "TIME_CHANGED": "2026-09-22 10:00:00",
            }]
        if "FROM %Dictionary.ParameterDefinition" in sql:
            return [{"NAME": "DEFAULTGLOBAL", "TYPE": "%String", "DEFAULT_VALUE": "^Patient", "DESCRIPTION": "Storage"}]
        if "FROM %Dictionary.PropertyDefinition" in sql:
            return [{"NAME": "Name", "ORIGIN": "Custom.Site.Patient", "TYPE": "%String",
                     "COLLECTION": "", "REQUIRED": 1, "MAXLEN": 120}]
        if "FROM %Dictionary.IndexDefinition" in sql:
            return [{"NAME": "NameIdx", "PROPERTIES": "Name", "TYPE": "index",
                     "UNIQUE_VALUE": 1, "PRIMARY_KEY": 0}]
        if "FROM %Dictionary.MethodDefinition" in sql:
            return [{"NAME": "Normalize", "CLASS_METHOD": 1, "RETURN_TYPE": "%Status",
                     "FORMAL_SPEC": "value:%String", "ABSTRACT": 0, "SQL_PROC": 0}]
        if "FROM %Dictionary.QueryDefinition" in sql:
            return [{"NAME": "ByName", "TYPE": "%SQLQuery", "FORMAL_SPEC": "name:%String",
                     "SQL_PROC": 1, "SQL_NAME": "ByName"}]
        raise AssertionError(f"Unexpected query: {sql}")

    def test_describe_class_returns_stable_structured_content(self):
        client = synthetic_client()
        client.run_query = Mock(side_effect=self._describe_rows)
        client._probe_same_process_ccr_state = Mock(side_effect=AssertionError("CCR guard is not a read gate"))
        client.get_namespace_ccr_status = Mock(side_effect=AssertionError("CCR discovery is not a read gate"))

        result = synthetic_server(client).handle_tool_call("iris_describe_class", {
            "server_id": "fake", "class_name": "Custom.Site.Patient",
        })

        self.assertNotIn("isError", result)
        record = result["structuredContent"]
        self.assertEqual(record["superclasses"], ["%Persistent", "%JSON.Adaptor"])
        self.assertEqual(record["properties"][0]["maxlen"], 120)
        self.assertIs(record["properties"][0]["required"], True)
        self.assertIs(record["indices"][0]["unique"], True)
        self.assertIn("Patient \\| model", result["content"][0]["text"])
        self.assertEqual(client.run_query.call_count, 6)

    def test_describe_class_uses_compiled_properties_when_requested(self):
        client = synthetic_client()

        def rows(sql, params=None, timeout=None):
            if "FROM %Dictionary.CompiledProperty" in sql:
                return [{"name": "Inherited", "origin": "Parent.Model", "type": "%String",
                         "collection": "", "required": 0, "maxlen": 50}]
            return self._describe_rows(sql, params, timeout)

        client.run_query = Mock(side_effect=rows)
        result = synthetic_server(client).handle_tool_call("iris_describe_class", {
            "server_id": "fake", "class_name": "Custom.Site.Patient", "include_inherited": True,
        })

        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["properties"][0]["origin"], "Parent.Model")
        self.assertTrue(any("%Dictionary.CompiledProperty" in call.args[0]
                            for call in client.run_query.call_args_list))

    def test_scan_namespace_is_bounded_parameterized_and_classifies(self):
        client = synthetic_client()
        client.run_query = Mock(return_value=[
            {"NAME": "Custom.Site.Api", "SUPER": "%CSP.REST", "CLASS_TYPE": "registered",
             "SQL_TABLE": None, "TIME_CHANGED": "now", "PROPERTY_COUNT": 0},
            {"NAME": "Custom.Site.Patient", "SUPER": "%Persistent", "CLASS_TYPE": "persistent",
             "SQL_TABLE": "Custom_Site.Patient", "TIME_CHANGED": "now", "PROPERTY_COUNT": 3},
        ])

        result = synthetic_server(client).handle_tool_call("iris_scan_namespace", {
            "server_id": "fake", "package_prefix": "Custom.Site", "limit": 25,
        })

        self.assertNotIn("isError", result)
        classes = result["structuredContent"]["classes"]
        self.assertEqual([row["class_type"] for row in classes], ["registered", "persistent"])
        self.assertEqual([row["direct_role"] for row in classes], ["REST", None])
        self.assertTrue(all("kind" not in row for row in classes))
        sql, params = client.run_query.call_args.args[:2]
        self.assertIn("SELECT TOP 25", sql)
        self.assertIn("c.Name NOT LIKE 'Ens.%'", sql)
        self.assertEqual(params, ["Custom.Site.%"])

    def test_scan_persistent_filter_is_fixed_sql(self):
        client = synthetic_client()
        client.run_query = Mock(return_value=[])
        result = synthetic_server(client).handle_tool_call("iris_scan_namespace", {
            "server_id": "fake", "persistent_only": True,
        })
        self.assertNotIn("isError", result)
        self.assertIn("c.ClassType = 'persistent'", client.run_query.call_args.args[0])
        self.assertEqual(client.run_query.call_args.args[1], ["Custom.%"])

    def test_invalid_inputs_fail_before_configuration_or_queries(self):
        cases = (
            ("iris_describe_class", {"server_id": "fake", "class_name": "Custom.Patient.cls"}),
            ("iris_scan_namespace", {"server_id": "fake", "package_prefix": "%SYSTEM"}),
            ("iris_scan_namespace", {"server_id": "fake", "package_prefix": "Custom.%"}),
            ("iris_scan_namespace", {"server_id": "fake", "limit": 0}),
            ("iris_scan_namespace", {"server_id": "fake", "limit": 1001}),
        )
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                client = synthetic_client()
                client.run_query = Mock(side_effect=AssertionError("Query must not run"))
                server = synthetic_server(client)
                result = server.handle_tool_call(name, arguments)
                self.assertTrue(result["isError"])
                server.load_configuration.assert_not_called()
                client.run_query.assert_not_called()

    def test_missing_class_is_a_bounded_error(self):
        client = synthetic_client()
        client.run_query = Mock(return_value=[])
        result = synthetic_server(client).handle_tool_call("iris_describe_class", {
            "server_id": "fake", "class_name": "Custom.Site.Missing",
        })
        self.assertTrue(result["isError"])
        self.assertEqual(result["content"][0]["text"], "Class not found: Custom.Site.Missing")
