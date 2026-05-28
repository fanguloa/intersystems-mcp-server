import sys
import os
import json
import traceback

# Import the class definitions from our MCP server file
try:
    sys.path.append(os.path.dirname(__file__))
    from iris_mcp_server import IRISMCPServer
except ImportError:
    sys.path.append(os.path.join(os.path.dirname(__file__), "..", "scratch"))
    from iris_mcp_server import IRISMCPServer

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

    # Choose a server to test
    test_sid = list(server.servers.keys())[0]
    print(f"Paso 4: Iniciando pruebas de conexión en vivo con el primer servidor '{test_sid}'...")
    print("*(Nota: Si esto falla por timeout, verifica tu conexión VPN asociada)*")

    # 4. Run SQL Query
    print(f"\n  -> [Live Test] Ejecutando SQL de prueba (iris_query_sql) en {test_sid}...")
    try:
        sql = "SELECT TOP 3 Name, TimeChanged FROM %Dictionary.ClassDefinition WHERE Name LIKE '%System%' ORDER BY Name"
        args = {"server_id": test_sid, "query": sql}
        res = server.handle_tool_call("iris_query_sql", args)
        if res.get("isError"):
            print("     [FAILED] Falló la consulta SQL:")
            print(res.get("content", [{}])[0].get("text"))
        else:
            print("     [SUCCESS] SQL ejecutado con éxito. Filas recuperadas:")
            print(res.get("content", [{}])[0].get("text"))
    except Exception as e:
        print(f"     [ERROR] Excepción en SQL: {e}")

    # 5. List Custom Classes
    print(f"\n  -> [Live Test] Listando clases del paquete '%SYS' (iris_list_classes) en {test_sid}...")
    try:
        args = {"server_id": test_sid, "package_prefix": "%SYS"}
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

    print("\n==================================================")
    print("   Fin de la Suite de Pruebas de Servidor MCP     ")
    print("==================================================")

if __name__ == "__main__":
    run_tests()
