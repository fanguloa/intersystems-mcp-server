import sys
import os
import json
import traceback

# Live smoke script (needs real servers + VPN). Run from the repo root:
#   python tests/live_smoke_server.py
# Output is English like the rest of the runtime; only the ObjectScript payload in
# step 6 stays non-ASCII on purpose, to exercise the UTF-8 path end to end.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from isc_ai.server import IRISMCPServer, _is_sql_read_only

def run_tests():
    # Force output encoding to UTF-8 on Windows
    if sys.platform == "win32":
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
        except Exception:
            pass

    print("==================================================")
    print("   Live smoke: InterSystems IRIS MCP server        ")
    print("==================================================\n")

    # 0. Unit tests for SQL Read-Only Detection
    print("Step 0: checking the read-only SQL detection (_is_sql_read_only)...")
    test_cases = [
        ("SELECT * FROM Table", True, "Simple SELECT"),
        ("WITH MyCTE AS (SELECT * FROM Table) SELECT * FROM MyCTE", True, "SELECT with WITH (CTE)"),
        ("-- This is a comment\nSELECT Name FROM Class", True, "SELECT with line comments"),
        ("/* Block comment */ SELECT ID FROM Table", True, "SELECT with block comments"),
        ("UPDATE MyTable SET Status='A'", False, "UPDATE command"),
        ("INSERT INTO Log VALUES (1)", False, "INSERT command"),
        ("SELECT * FROM Table; DROP TABLE Table", False, "SELECT with dangerous DROP statement"),
        ("EXPLAIN SELECT * FROM Table", True, "EXPLAIN utility query"),
        ("VALUES (1, 2, 3)", True, "VALUES query"),
    ]
    
    all_passed = True
    for query, expected, desc in test_cases:
        got = _is_sql_read_only(query)
        if got == expected:
            print(f"  [SUCCESS] {desc}: {got}")
        else:
            print(f"  [FAILED] {desc}: expected {expected}, got {got}")
            all_passed = False
            
    if all_passed:
        print("  -> every SQL detection unit case PASSED\n")
    else:
        print("  -> [ERROR] SQL detection unit cases FAILED\n")
        return

    # 1. Initialize server
    print("Step 1: initializing the MCP server...")
    try:
        server = IRISMCPServer()
        print("  [SUCCESS] MCP server initialized.")
        print(f"  [INFO] servers detected: {list(server.servers.keys())}\n")
    except Exception as e:
        print(f"  [ERROR] failed to initialize the server: {e}")
        traceback.print_exc()
        return

    # 2. Check tools list
    print("Step 2: requesting the exposed tool list (tools/list)...")
    try:
        tools_list = server.handle_tools_list()
        tools = tools_list.get("tools", [])
        print(f"  [SUCCESS] {len(tools)} tools exposed.")
        for idx, t in enumerate(tools, 1):
            print(f"    {idx}. {t['name']}: {t['description'][:65]}...")
        print()
    except Exception as e:
        print(f"  [ERROR] failed to list the tools: {e}\n")

    # 3. Test list servers
    print("Step 3: checking the 'iris_list_servers' tool...")
    try:
        res = server.handle_tool_call("iris_list_servers", {})
        content = res.get("content", [{}])[0].get("text", "")
        print("  [SUCCESS] iris_list_servers replied:")
        print(content)
        print()
    except Exception as e:
        print(f"  [ERROR] failed to list the servers: {e}\n")

    # If no connections exist, stop
    if not server.servers:
        print("WARNING: no configured servers were detected. Stopping the live tests.")
        print("Run 'isc-ai init' in the deployment directory, or add entries to ~/.iris-mcp/servers.json.")
        return

    # Choose a server to test (prefer achsuat if available, otherwise achsbase, otherwise first)
    if "achsuat02" in server.servers or "achsuat01" in server.servers:
        test_sid = "achsuat"
        test_ns = "UAT-ACHS"
    elif "achsbase" in server.servers:
        test_sid = "achsbase"
        test_ns = "BASE-ACHS"
    elif "clmebase" in server.servers:
        test_sid = "clmebase"
        test_ns = "BASE-CLME"
    else:
        test_sid = list(server.servers.keys())[0]
        test_ns = server.servers[test_sid].get("namespace")
    print(f"Step 4: starting live connection tests against '{test_sid}' (namespace: '{test_ns}')...")
    print("*(Note: a timeout here usually means the matching VPN connection is down)*")

    # 4. Run SQL Query
    print(f"\n  -> [Live Test] running a probe SQL statement (iris_query_sql) on {test_sid}...")
    try:
        sql = "SELECT TOP 3 Name, TimeChanged FROM %Dictionary.ClassDefinition WHERE Name LIKE '%System%' ORDER BY Name"
        args = {"server_id": test_sid, "query": sql}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_query_sql", args)
        if res.get("isError"):
            print("     [FAILED] the SQL query failed:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] SQL executed. Rows retrieved:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] exception during SQL: {e}")

    # 4b. Test Bad SQL Query Error Handling
    print(f"\n  -> [Live Test] checking error detection on a bad SQL statement on {test_sid}...")
    try:
        sql_bad = "SELECT * FROM NonExistentTable_12345"
        args_bad = {"server_id": test_sid, "query": sql_bad}
        if test_ns: args_bad["namespace"] = test_ns
        res_bad = server.handle_tool_call("iris_query_sql", args_bad)
        if res_bad.get("isError") or "IRIS SQL execution failed" in res_bad.get("content", [{}])[0].get("text", ""):
            print("     [SUCCESS] the SQL error was detected and captured:")
            print("     " + res_bad.get("content", [{}])[0].get("text", "").splitlines()[0])
        else:
            print("     [FAILED] the invalid SQL query was not reported as an error!")
    except Exception as e:
        print(f"     [SUCCESS] exception captured as expected: {e}")

    # 4c. Test Recent Messages
    print(f"\n  -> [Live Test] recent bus messages (iris_get_recent_messages) on {test_sid}...")
    try:
        args = {"server_id": test_sid, "limit": 5}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_get_recent_messages", args)
        if res.get("isError"):
            print("     [FAILED] the recent messages query failed:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] recent messages retrieved:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] exception during recent messages: {e}")

    # 4d. Test Ensemble Trace Tool
    print(f"\n  -> [Live Test] Ensemble trace tool (iris_get_ensemble_trace) on {test_sid}...")
    try:
        args = {"server_id": test_sid, "session_id": 81980, "include_logs": True, "include_bodies": True}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_get_ensemble_trace", args)
        if res.get("isError"):
            print("     [FAILED] the Ensemble trace failed:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] Ensemble trace retrieved:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] exception during the Ensemble trace: {e}")

    # 4e. Test Production Control (status)
    print(f"\n  -> [Live Test] production status (iris_ensemble_production_control) on {test_sid}...")
    try:
        args = {"server_id": test_sid, "action": "status"}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_ensemble_production_control", args)
        if res.get("isError"):
            print("     [FAILED] the production status query failed:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] production status retrieved:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] exception during production control: {e}")

    # 4f. Test Component Control (list)
    print(f"\n  -> [Live Test] component listing (iris_ensemble_component_control) on {test_sid}...")
    try:
        args = {"server_id": test_sid, "action": "list"}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_ensemble_component_control", args)
        if res.get("isError"):
            print("     [FAILED] the component listing failed:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] components retrieved:")
            text = res.get("content", [{}])[0].get("text", "")
            print("\n".join(text.splitlines()[:12]))
            print("     ...")
    except Exception as e:
        print(f"     [ERROR] exception during component control: {e}")

    # 4g. Test Default Settings (list)
    print(f"\n  -> [Live Test] default settings (iris_ensemble_default_settings) on {test_sid}...")
    try:
        args = {"server_id": test_sid, "action": "list"}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_ensemble_default_settings", args)
        if res.get("isError"):
            print("     [FAILED] the default settings query failed:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] default settings retrieved:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] exception during default settings: {e}")

    # 4h. Test Queue Control (list_queues)
    print(f"\n  -> [Live Test] queue status (iris_ensemble_queue_control) on {test_sid}...")
    try:
        args = {"server_id": test_sid, "action": "list_queues"}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_ensemble_queue_control", args)
        if res.get("isError"):
            print("     [FAILED] the queue query failed:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] queue status retrieved:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] exception during queue control: {e}")

    # 5. List Custom Classes
    print(f"\n  -> [Live Test] listing classes of package '%SYS' (iris_list_classes) on {test_sid}...")
    try:
        args = {"server_id": test_sid, "package_prefix": "%SYS"}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_list_classes", args)
        text = res.get("content", [{}])[0].get("text", "")
        lines = text.splitlines()
        print("     [SUCCESS] classes retrieved:")
        print("\n".join(lines[:12]))
        print("     ...")
    except Exception as e:
        print(f"     [ERROR] exception while listing classes: {e}")

    # 6. Test ObjectScript Execution
    print(f"\n  -> [Live Test] transient ObjectScript execution (iris_execute_objectscript) on {test_sid}...")
    try:
        # Deliberately non-ASCII: exercises the UTF-8 round trip through the runner.
        code = 'Write "¡Hola! Acentos y eñe: áéíóú ñ", !, "Server time: ", $ZDatetime($Horolog, 3), !'
        args = {"server_id": test_sid, "code": code}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_execute_objectscript", args)
        if res.get("isError"):
            print("     [FAILED] the code execution failed:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] code executed. Output received:")
            output_content = res.get("content", [{}])[0].get("text", "")
            print("--------------------------------------------------")
            print(output_content.rstrip())
            print("--------------------------------------------------")
    except Exception as e:
        print(f"     [ERROR] exception while executing code: {e}")

    # 6b. Test Global Dump
    print(f"\n  -> [Live Test] global dump (iris_dump_global) on {test_sid}...")
    try:
        args = {"server_id": test_sid, "global_name": "^websys.MenuD", "max_nodes": 5}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_dump_global", args)
        if res.get("isError"):
            print("     [FAILED] the global dump failed:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] global dump executed:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] exception during the global dump: {e}")

    # 7. Test CCR Status Query
    print(f"\n  -> [Live Test] CCR status (iris_ccr_status) on {test_sid}...")
    try:
        args = {"server_id": test_sid, "class_name": "Custom.ACHS.Integration.REST.BO.PAPerson"}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_ccr_status", args)
        if res.get("isError"):
            print("     [FAILED] the CCR query failed:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] CCR query completed:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] exception while querying CCR: {e}")

    # 8. Test Namespaces List Query
    print(f"\n  -> [Live Test] listing namespaces (iris_list_namespaces) on {test_sid}...")
    try:
        args = {"server_id": test_sid}
        res = server.handle_tool_call("iris_list_namespaces", args)
        if res.get("isError"):
            print("     [FAILED] the namespace listing failed:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] namespace listing completed:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] exception while listing namespaces: {e}")

    print("\n==================================================")
    print("   End of the MCP server live smoke               ")
    print("==================================================")

if __name__ == "__main__":
    run_tests()
