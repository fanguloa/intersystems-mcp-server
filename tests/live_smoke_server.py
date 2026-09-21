import sys
import os
import json
import traceback

# Live smoke script (needs real servers + VPN). Run from the repo root:
#   python tests/live_smoke_server.py
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
    print("   Suite de Pruebas: InterSystems IRIS MCP Server ")
    print("==================================================\n")

    # 0. Unit tests for SQL Read-Only Detection
    print("Paso 0: Verificando lógica de detección de consultas SQL de lectura (_is_sql_read_only)...")
    test_cases = [
        ("SELECT * FROM Table", True, "Simple SELECT"),
        ("WITH MyCTE AS (SELECT * FROM Table) SELECT * FROM MyCTE", True, "SELECT with WITH (CTE)"),
        ("-- Esto es un comentario\nSELECT Name FROM Class", True, "SELECT with line comments"),
        ("/* Comentario de bloque */ SELECT ID FROM Table", True, "SELECT with block comments"),
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
            print(f"  [FAILED] {desc}: esperado {expected}, obtenido {got}")
            all_passed = False
            
    if all_passed:
        print("  -> Todas las pruebas unitarias de detección SQL PASARON con éxito!\n")
    else:
        print("  -> [ERROR] Fallaron pruebas unitarias de detección SQL!\n")
        return

    # 1. Initialize server
    print("Paso 1: Inicializando Servidor MCP...")
    try:
        server = IRISMCPServer()
        print("  [SUCCESS] Servidor MCP inicializado con éxito.")
        print(f"  [INFO] Servidores detectados: {list(server.servers.keys())}\n")
    except Exception as e:
        print(f"  [ERROR] ERROR al inicializar servidor: {e}")
        traceback.print_exc()
        return

    # 2. Check tools list
    print("Paso 2: Solicitando lista de herramientas expuestas (tools/list)...")
    try:
        tools_list = server.handle_tools_list()
        tools = tools_list.get("tools", [])
        print(f"  [SUCCESS] {len(tools)} herramientas expuestas.")
        for idx, t in enumerate(tools, 1):
            print(f"    {idx}. {t['name']}: {t['description'][:65]}...")
        print()
    except Exception as e:
        print(f"  [ERROR] ERROR al listar herramientas: {e}\n")

    # 3. Test list servers
    print("Paso 3: Verificando herramienta 'iris_list_servers'...")
    try:
        res = server.handle_tool_call("iris_list_servers", {})
        content = res.get("content", [{}])[0].get("text", "")
        print("  [SUCCESS] Respuesta de iris_list_servers:")
        print(content)
        print()
    except Exception as e:
        print(f"  [ERROR] ERROR al listar servidores: {e}\n")

    # If no connections exist, stop
    if not server.servers:
        print("ALERTA: No se detectaron servidores configurados. Deteniendo pruebas en vivo.")
        print("Crea un archivo 'mcp_config.json' local para correr pruebas en vivo.")
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
    print(f"Paso 4: Iniciando pruebas de conexión en vivo con el servidor '{test_sid}' (Namespace: '{test_ns}')...")
    print("*(Nota: Si esto falla por timeout, verifica tu conexión VPN asociada)*")

    # 4. Run SQL Query
    print(f"\n  -> [Live Test] Ejecutando SQL de prueba (iris_query_sql) en {test_sid}...")
    try:
        sql = "SELECT TOP 3 Name, TimeChanged FROM %Dictionary.ClassDefinition WHERE Name LIKE '%System%' ORDER BY Name"
        args = {"server_id": test_sid, "query": sql}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_query_sql", args)
        if res.get("isError"):
            print("     [FAILED] Falló la consulta SQL:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] SQL ejecutado con éxito. Filas recuperadas:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] Excepción en SQL: {e}")

    # 4b. Test Bad SQL Query Error Handling
    print(f"\n  -> [Live Test] Verificando detección de error en SQL incorrecto en {test_sid}...")
    try:
        sql_bad = "SELECT * FROM NonExistentTable_12345"
        args_bad = {"server_id": test_sid, "query": sql_bad}
        if test_ns: args_bad["namespace"] = test_ns
        res_bad = server.handle_tool_call("iris_query_sql", args_bad)
        if res_bad.get("isError") or "Error de ejecución SQL" in res_bad.get("content", [{}])[0].get("text", ""):
            print("     [SUCCESS] Error de SQL detectado y capturado correctamente:")
            print("     " + res_bad.get("content", [{}])[0].get("text", "").splitlines()[0])
        else:
            print("     [FAILED] No se capturó el error en la consulta SQL inválida!")
    except Exception as e:
        print(f"     [SUCCESS] Excepción capturada correctamente: {e}")

    # 4c. Test Recent Messages
    print(f"\n  -> [Live Test] Probando mensajes recientes del bus (iris_get_recent_messages) en {test_sid}...")
    try:
        args = {"server_id": test_sid, "limit": 5}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_get_recent_messages", args)
        if res.get("isError"):
            print("     [FAILED] Falló la consulta de mensajes recientes:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] Mensajes recientes recuperados:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] Excepción en mensajes recientes: {e}")

    # 4d. Test Ensemble Trace Tool
    print(f"\n  -> [Live Test] Probando la herramienta de Traza Ensemble (iris_get_ensemble_trace) en {test_sid}...")
    try:
        args = {"server_id": test_sid, "session_id": 81980, "include_logs": True, "include_bodies": True}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_get_ensemble_trace", args)
        if res.get("isError"):
            print("     [FAILED] Falló la traza de Ensemble:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] Traza de Ensemble recuperada con éxito:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] Excepción en traza Ensemble: {e}")

    # 4e. Test Production Control (status)
    print(f"\n  -> [Live Test] Probando estado de producción (iris_ensemble_production_control) en {test_sid}...")
    try:
        args = {"server_id": test_sid, "action": "status"}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_ensemble_production_control", args)
        if res.get("isError"):
            print("     [FAILED] Falló la consulta de estado de producción:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] Estado de producción recuperado:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] Excepción en control de producción: {e}")

    # 4f. Test Component Control (list)
    print(f"\n  -> [Live Test] Probando listado de componentes (iris_ensemble_component_control) en {test_sid}...")
    try:
        args = {"server_id": test_sid, "action": "list"}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_ensemble_component_control", args)
        if res.get("isError"):
            print("     [FAILED] Falló el listado de componentes:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] Componentes recuperados con éxito:")
            text = res.get("content", [{}])[0].get("text", "")
            print("\n".join(text.splitlines()[:12]))
            print("     ...")
    except Exception as e:
        print(f"     [ERROR] Excepción en control de componentes: {e}")

    # 4g. Test Default Settings (list)
    print(f"\n  -> [Live Test] Probando Default Settings (iris_ensemble_default_settings) en {test_sid}...")
    try:
        args = {"server_id": test_sid, "action": "list"}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_ensemble_default_settings", args)
        if res.get("isError"):
            print("     [FAILED] Falló la consulta de Default Settings:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] Default Settings consultados con éxito:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] Excepción en Default Settings: {e}")

    # 4h. Test Queue Control (list_queues)
    print(f"\n  -> [Live Test] Probando estado de colas (iris_ensemble_queue_control) en {test_sid}...")
    try:
        args = {"server_id": test_sid, "action": "list_queues"}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_ensemble_queue_control", args)
        if res.get("isError"):
            print("     [FAILED] Falló la consulta de colas:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] Estado de colas recuperado con éxito:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] Excepción en control de colas: {e}")

    # 5. List Custom Classes
    print(f"\n  -> [Live Test] Listando clases del paquete '%SYS' (iris_list_classes) en {test_sid}...")
    try:
        args = {"server_id": test_sid, "package_prefix": "%SYS"}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_list_classes", args)
        text = res.get("content", [{}])[0].get("text", "")
        lines = text.splitlines()
        print("     [SUCCESS] Clases recuperadas con éxito:")
        print("\n".join(lines[:12]))
        print("     ...")
    except Exception as e:
        print(f"     [ERROR] Excepción al listar clases: {e}")

    # 6. Test ObjectScript Execution
    print(f"\n  -> [Live Test] Ejecutando código ObjectScript transitorio (iris_execute_objectscript) en {test_sid}...")
    try:
        code = 'Write "¡Hola desde el Servidor MCP de Antigravity!", !, "La fecha es: ", $ZDatetime($Horolog, 3), !'
        args = {"server_id": test_sid, "code": code}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_execute_objectscript", args)
        if res.get("isError"):
            print("     [FAILED] Falló la ejecución del código:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] Código ejecutado con éxito. Salida recibida:")
            output_content = res.get("content", [{}])[0].get("text", "")
            print("--------------------------------------------------")
            print(output_content.rstrip())
            print("--------------------------------------------------")
    except Exception as e:
        print(f"     [ERROR] Excepción al ejecutar código: {e}")

    # 6b. Test Global Dump
    print(f"\n  -> [Live Test] Probando dump de global (iris_dump_global) en {test_sid}...")
    try:
        args = {"server_id": test_sid, "global_name": "^websys.MenuD", "max_nodes": 5}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_dump_global", args)
        if res.get("isError"):
            print("     [FAILED] Falló el dump de global:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] Dump de global ejecutado con éxito:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] Excepción al hacer dump de global: {e}")

    # 7. Test CCR Status Query
    print(f"\n  -> [Live Test] Consultando estado CCR (iris_ccr_status) en {test_sid}...")
    try:
        args = {"server_id": test_sid, "class_name": "Custom.ACHS.Integration.REST.BO.PAPerson"}
        if test_ns: args["namespace"] = test_ns
        res = server.handle_tool_call("iris_ccr_status", args)
        if res.get("isError"):
            print("     [FAILED] Falló la consulta CCR:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] Consulta CCR realizada con éxito:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] Excepción al consultar CCR: {e}")

    # 8. Test Namespaces List Query
    print(f"\n  -> [Live Test] Listando Namespaces (iris_list_namespaces) en {test_sid}...")
    try:
        args = {"server_id": test_sid}
        res = server.handle_tool_call("iris_list_namespaces", args)
        if res.get("isError"):
            print("     [FAILED] Falló el listado de namespaces:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] Listado de namespaces realizado con éxito:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] Excepción al listar namespaces: {e}")

    print("\n==================================================")
    print("   Fin de la Suite de Pruebas de Servidor MCP     ")
    print("==================================================")

if __name__ == "__main__":
    run_tests()
