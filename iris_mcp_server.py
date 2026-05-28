import sys
import json
import os
import urllib.request
import urllib.error
import base64
import traceback

# Protocol Configuration
PROTOCOL_VERSION = "2024-11-05"

def log_debug(message):
    """Write debug logging to stderr so it doesn't corrupt the JSON-RPC stdout channel."""
    sys.stderr.write(f"[DEBUG] {message}\n")
    sys.stderr.flush()

class AtelierClient:
    def __init__(self, host, port, path_prefix, namespace, username, password):
        self.host = host
        self.port = port
        self.path_prefix = path_prefix
        self.namespace = namespace
        self.base_url = f"http://{host}:{port}{path_prefix}/api/atelier/v1/{namespace}"
        
        # Configure Basic Authentication
        auth_str = f"{username}:{password}"
        self.auth_header = f"Basic {base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')}"

    def is_write_allowed(self):
        """
        Verifies if writing is permitted on this connection.
        Protects Live/Production environments from accidental write or modify commands.
        """
        # 1. Check override environment variable
        allow_prod = os.environ.get("IRIS_ALLOW_PROD", "0") in ["1", "true", "TRUE"]
        if allow_prod:
            return True, "Permitido por la variable de entorno IRIS_ALLOW_PROD=1"

        # 2. Check namespace heuristics
        ns_upper = self.namespace.upper()
        if any(prod_word in ns_upper for prod_word in ["PROD", "PRODUCTION", "LIVE", "PRD"]):
            return False, f"Bloqueado: El namespace '{self.namespace}' parece ser un sistema de producción"

        # 3. Dynamic query to check SystemMode global state in %SYS
        try:
            # Query ^%SYS("SystemMode") to detect if instance is configured as "Live"
            query = "SELECT Value FROM %Library.Global_Get('%SYS', '^%SYS(\"SystemMode\")')"
            rows = self.run_query(query)
            if rows and len(rows) > 0:
                mode = str(rows[0].get("Value", "")).strip()
                if mode == "Live":
                    return False, "Bloqueado: El servidor está configurado en modo real ('Live') en ^%SYS(\"SystemMode\")"
        except Exception as e:
            log_debug(f"Could not check ^%SYS(\"SystemMode\") global (expected if non-SYS user): {e}")

        return True, "Permitido"


    def _send_request(self, endpoint, method='GET', payload=None, query_params=""):
        url = f"{self.base_url}{endpoint}"
        if query_params:
            url += f"?{query_params}"
            
        req = urllib.request.Request(url, method=method)
        req.add_header('Authorization', self.auth_header)
        req.add_header('Accept', 'application/json')
        
        if payload is not None:
            req.add_header('Content-Type', 'application/json')
            req.data = json.dumps(payload).encode('utf-8')
            
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                raw_data = response.read().decode('utf-8')
                return response.status, json.loads(raw_data) if raw_data else {}
        except urllib.error.HTTPError as e:
            err_content = e.read().decode('utf-8') if e else ""
            log_debug(f"HTTPError {e.code} on {method} {endpoint}: {err_content}")
            return e.code, {"error": e.reason, "details": err_content}
        except Exception as e:
            log_debug(f"Exception on {method} {endpoint}: {str(e)}")
            return 500, {"error": str(e)}

    def run_query(self, sql_query, params=None):
        payload = {
            "query": sql_query,
            "parameters": params or []
        }
        status, data = self._send_request("/action/query", method='POST', payload=payload)
        if status == 200:
            return data.get('result', {}).get('content', [])
        raise Exception(f"SQL execution failed with status {status}: {data}")

    def get_document(self, doc_name):
        status, data = self._send_request(f"/doc/{doc_name}", method='GET')
        if status == 200:
            return data.get('result', {}).get('content', [])
        raise Exception(f"Get document failed with status {status}: {data}")

    def save_document(self, doc_name, content_lines):
        payload = {
            "content": content_lines,
            "enc": False
        }
        status, data = self._send_request(f"/doc/{doc_name}", method='PUT', payload=payload, query_params="ignoreConflict=1")
        if status in [200, 201]:
            return True, "Saved successfully"
        return False, f"Save failed with status {status}: {data}"

    def compile_document(self, doc_name):
        status, data = self._send_request("/action/compile", method='POST', payload=[doc_name])
        if status == 200:
            return data
        raise Exception(f"Compile failed with status {status}: {data}")

    def delete_document(self, doc_name):
        status, data = self._send_request(f"/doc/{doc_name}", method='DELETE')
        if status == 200:
            return True, "Deleted successfully"
        return False, f"Delete failed with status {status}: {data}"

    def execute_code(self, code_str):
        """
        Executes arbitrary ObjectScript code over REST via Zero-Helper transient class generator.
        Creates a temporary class, compiles it, executes it via SQL, and deletes it.
        """
        import uuid
        uid = uuid.uuid4().hex[:12]
        class_name = f"User.McpTmpRun{uid}"
        doc_name = f"{class_name}.cls"
        sql_func = f"SQLUser.McpTmpRun{uid}_Execute"
        
        class_content = [
            f"Class {class_name} [ Final ]",
            "{",
            "ClassMethod Execute() As %String [ SqlProc ]",
            "{",
            "    Set savedIO = $IO",
            "    Set tempFile = ##class(%File).TempFilename(\"txt\")",
            "    Open tempFile:(\"WNS\"):5",
            "    If '$Test { Quit \"ERROR: Cannot open temp file\" }",
            "    Use tempFile",
            "    Try {",
        ]
        
        for line in code_str.splitlines():
            class_content.append(f"        {line}")
            
        class_content.extend([
            "    } Catch ex {",
            "        Write \"ERROR: \", ex.DisplayString(), !",
            "    }",
            "    Close tempFile",
            "    Use savedIO",
            "    Set stream = ##class(%Stream.FileCharacter).%New()",
            "    Set sc = stream.LinkToFile(tempFile)",
            "    Set output = \"\"",
            "    If $$$ISOK(sc) {",
            "        While 'stream.AtEnd {",
            "            Set output = output _ stream.ReadLine() _ $Char(10)",
            "        }",
            "    }",
            "    Do ##class(%File).Delete(tempFile)",
            "    Quit output",
            "}",
            "}"
        ])
        
        # 1. Save document (PUT)
        saved, save_msg = self.save_document(doc_name, class_content)
        if not saved:
            raise Exception(f"Failed to upload temporary executor class: {save_msg}")
            
        try:
            # 2. Compile document (POST)
            res = self.compile_document(doc_name)
            errors = res.get("status", {}).get("errors", [])
            if errors:
                raise Exception(f"Failed to compile temporary executor class: {json.dumps(errors)}")
                
            # 3. Query via SQL (POST)
            query = f"SELECT {sql_func}() AS result"
            results = self.run_query(query)
            if results and len(results) > 0:
                return results[0].get("result", "")
            return ""
        finally:
            # 4. Delete document (best-effort)
            try:
                self.delete_document(doc_name)
            except Exception as de:
                log_debug(f"Failed to delete temporary document {doc_name}: {de}")





class IRISMCPServer:
    def __init__(self):
        self.servers = {}
        self.load_configuration()

    def load_configuration(self):
        home_dir = os.path.expanduser("~")
        paths_to_try = [
            os.path.join(home_dir, ".iris_mcp_servers.json"),
            "mcp_config.json",
            os.path.join(os.path.dirname(__file__), "mcp_config.json"),
            os.path.join(os.path.dirname(__file__), "..", "mcp_config.json")
        ]
        
        config_loaded = False
        for path in paths_to_try:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                    servers_dict = config_data.get("servers", {})
                    for sid, sdata in servers_dict.items():
                        self.servers[sid] = sdata
                    log_debug(f"Loaded {len(self.servers)} server configurations from {path}")
                    config_loaded = True
                    break
                except Exception as e:
                    log_debug(f"Error loading configuration from {path}: {e}")
        
        if not config_loaded:
            log_debug("WARNING: No server configuration found. Please create ~/.iris_mcp_servers.json")

    def get_client(self, server_id, namespace_override=None):
        if server_id not in self.servers:
            raise ValueError(f"Server ID '{server_id}' not found in configuration.")
        s = self.servers[server_id]
        target_namespace = namespace_override or s.get("namespace") or "USER"
        return AtelierClient(
            host=s["host"],
            port=s.get("port", 80),
            path_prefix=s.get("path_prefix", ""),
            namespace=target_namespace,
            username=s["username"],
            password=s["password"]
        )

    def handle_tools_list(self):
        tools = [
            {
                "name": "iris_list_servers",
                "description": "Lista las conexiones de servidores de InterSystems IRIS disponibles y configuradas en el sistema.",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "iris_query_sql",
                "description": "Ejecuta una consulta SQL nativa en una instancia de InterSystems IRIS y retorna un listado de resultados estructurado.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "ID del servidor configurado (ej: 'my-dev-server')."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Namespace opcional. Si no se especifica, usa el configurado para el servidor, o 'USER' por defecto."
                        },
                        "query": {
                            "type": "string",
                            "description": "Consulta SQL a ejecutar (ej: 'SELECT Name FROM %Dictionary.ClassDefinition WHERE Name LIKE \\'Custom.%\\'')."
                        },
                        "parameters": {
                            "type": "array",
                            "description": "Parámetros opcionales para la consulta parametrizada.",
                            "items": {"type": "string"}
                        }
                    },
                    "required": ["server_id", "query"]
                }
            },
            {
                "name": "iris_get_class",
                "description": "Recupera el código fuente ObjectScript nativo completo de una clase .cls en el servidor.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "ID del servidor configurado."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Namespace opcional. Si no se especifica, usa el configurado para el servidor, o 'USER' por defecto."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Nombre completo de la clase, con o sin extensión '.cls' (ej: 'Custom.MyPackage.MyClass')."
                        }
                    },
                    "required": ["server_id", "class_name"]
                }
            },
            {
                "name": "iris_save_class",
                "description": "Guarda/Sube el código fuente de una clase al servidor de InterSystems (Equivalente al CSP PUT).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "ID del servidor."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Namespace opcional. Si no se especifica, usa el configurado para el servidor, o 'USER' por defecto."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Nombre completo de la clase (ej: 'Custom.MyPackage.MyClass')."
                        },
                        "content": {
                            "type": "string",
                            "description": "Código ObjectScript completo de la clase a subir."
                        }
                    },
                    "required": ["server_id", "class_name", "content"]
                }
            },
            {
                "name": "iris_compile_class",
                "description": "Compila una clase o documento en el servidor de InterSystems IRIS y retorna el estado y errores del compilador.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "ID del servidor."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Namespace opcional. Si no se especifica, usa el configurado para el servidor, o 'USER' por defecto."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Nombre completo de la clase con extensión (ej: 'Custom.MyPackage.MyClass.cls')."
                        }
                    },
                    "required": ["server_id", "class_name"]
                }
            },
            {
                "name": "iris_delete_class",
                "description": "Elimina de forma permanente un documento o clase .cls de un namespace en el servidor de InterSystems.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "ID del servidor."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Namespace opcional. Si no se especifica, usa el configurado para el servidor, o 'USER' por defecto."
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Nombre completo de la clase a eliminar (ej: 'Custom.Temp.MyClass.cls')."
                        }
                    },
                    "required": ["server_id", "class_name"]
                }
            },
            {
                "name": "iris_list_classes",
                "description": "Busca y lista de manera rápida clases personalizadas en el diccionario del servidor filtrando por paquete (ej: 'Custom'). Evita sobrecargar el servidor con listados pesados.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "ID del servidor."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Namespace opcional. Si no se especifica, usa el configurado para el servidor, o 'USER' por defecto."
                        },
                        "package_prefix": {
                            "type": "string",
                            "description": "Prefijo del paquete a buscar (ej: 'Custom.MyPackage', 'Logistica'). Por defecto busca clases que comiencen con 'Custom'."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_get_event_logs",
                "description": "Recupera los últimos registros de eventos del log del sistema de integración de Ensemble/IRIS (Ens_Util.Log), ideal para diagnosticar errores de ejecución.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "ID del servidor."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Namespace opcional. Si no se especifica, usa el configurado para el servidor, o 'USER' por defecto."
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Número máximo de registros a retornar (por defecto 20, máx 100)."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_get_recent_messages",
                "description": "Recupera las últimas transacciones de mensajes del bus de integración de Ensemble/IRIS (Ens.MessageHeader) para monitorear el tráfico y resolver bloqueos.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "ID del servidor."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Namespace opcional. Si no se especifica, usa el configurado para el servidor, o 'USER' por defecto."
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Número máximo de mensajes a retornar (por defecto 20)."
                        },
                        "only_errors": {
                            "type": "boolean",
                            "description": "Si es verdadero, retornará solo mensajes con error o en estado suspendido."
                        }
                    },
                    "required": ["server_id"]
                }
            },
            {
                "name": "iris_execute_objectscript",
                "description": "Ejecuta un bloque de código ObjectScript arbitrario de forma transitoria en el servidor y retorna la salida escrita estándar (Write/output).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server_id": {
                            "type": "string",
                            "description": "ID del servidor configurado."
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Namespace opcional. Si no se especifica, usa el configurado para el servidor, o 'USER' por defecto."
                        },
                        "code": {
                            "type": "string",
                            "description": "Código ObjectScript a ejecutar (ej: 'Write \"Hello World\", !')."
                        }
                    },
                    "required": ["server_id", "code"]
                }
            }
        ]
        return {"tools": tools}

    def handle_tool_call(self, name, arguments):
        log_debug(f"Executing tool: {name} with args: {arguments}")
        try:
            if name == "iris_list_servers":
                result_servers = []
                for sid, sdata in self.servers.items():
                    result_servers.append({
                        "id": sid,
                        "host": sdata["host"],
                        "port": sdata.get("port", 80),
                        "path_prefix": sdata.get("path_prefix", ""),
                        "namespace": sdata.get("namespace", "USER"),
                        "username": sdata["username"],
                        "vpn": sdata.get("vpn", "Local/Intranet"),
                        "description": sdata.get("description", "")
                    })
                
                output = "# Servidores InterSystems IRIS Configuraciones\n\n"
                if not result_servers:
                    output += "No hay servidores configurados en `~/.iris_mcp_servers.json` o local `mcp_config.json`"
                else:
                    output += "| ID | Host | Puerto | Path Prefix | Namespace | Usuario | VPN Requerida | Descripción |\n"
                    output += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                    for s in result_servers:
                        output += f"| **`{s['id']}`** | `{s['host']}` | `{s['port']}` | `{s['path_prefix']}` | `{s['namespace']}` | `{s['username']}` | *{s['vpn']}* | {s['description']} |\n"
                return {"content": [{"type": "text", "text": output}]}
                
            server_id = arguments.get("server_id")
            client = self.get_client(server_id, namespace_override=arguments.get("namespace"))
            
            # Enforce safety write protections for Live/Production environments
            if name in ["iris_save_class", "iris_delete_class", "iris_compile_class", "iris_execute_objectscript"]:
                allowed, reason = client.is_write_allowed()
                if not allowed:
                    return {
                        "isError": True,
                        "content": [{
                            "type": "text", 
                            "text": f"⚠️ [PROTECCIÓN DE PRODUCCIÓN] Operación denegada en el servidor '{server_id}' ({client.namespace}): {reason}.\n\nPara forzar la escritura/ejecución en ambientes protegidos, configura la variable de entorno `IRIS_ALLOW_PROD=1`."
                        }]
                    }
            
            if name == "iris_query_sql":
                query = arguments.get("query", "")
                query_upper = query.upper()
                import re
                is_write_op = bool(re.search(r'\b(UPDATE|INSERT|DELETE|DROP|ALTER|CREATE)\b', query_upper))
                if is_write_op:
                    allowed, reason = client.is_write_allowed()
                    if not allowed:
                        return {
                            "isError": True,
                            "content": [{
                                "type": "text", 
                                "text": f"⚠️ [PROTECCIÓN DE PRODUCCIÓN] Consulta SQL de modificación denegada en el servidor '{server_id}' ({client.namespace}): {reason}.\n\nPara forzar consultas de escritura en ambientes protegidos, configura la variable de entorno `IRIS_ALLOW_PROD=1`."
                            }]
                        }
                        
                params = arguments.get("parameters", [])
                results = client.run_query(query, params)
                
                output = f"### Resultados SQL en {server_id} ({client.namespace})\n"
                if not results:
                    output += "La consulta se ejecutó con éxito pero no devolvió filas.\n"
                else:
                    headers = list(results[0].keys())
                    output += "\n| " + " | ".join(headers) + " |\n"
                    output += "| " + " | ".join([":---" for _ in headers]) + " |\n"
                    for row in results[:100]:
                        cells = []
                        for h in headers:
                            val = row.get(h)
                            if val is None:
                                cells.append("*NULL*")
                            else:
                                cells.append(str(val).replace("|", "\\|").replace("\n", " "))
                        output += "| " + " | ".join(cells) + " |\n"
                    if len(results) > 100:
                        output += f"\n*(Mostrando las primeras 100 de {len(results)} filas devueltas)*\n"
                return {"content": [{"type": "text", "text": output}]}
                
            elif name == "iris_get_class":
                class_name = arguments.get("class_name")
                if not class_name.endswith(".cls"):
                    class_name += ".cls"
                
                content_lines = client.get_document(class_name)
                output = "\n".join(content_lines)
                formatted = f"```cos\n{output}\n```"
                return {"content": [{"type": "text", "text": formatted}]}
                
            elif name == "iris_save_class":
                class_name = arguments.get("class_name")
                if not class_name.endswith(".cls"):
                    class_name += ".cls"
                content = arguments.get("content")
                content_lines = content.splitlines()
                
                success, msg = client.save_document(class_name, content_lines)
                return {"content": [{"type": "text", "text": f"**Guardado**: {success}\n**Detalle**: {msg}"}]}
                
            elif name == "iris_compile_class":
                class_name = arguments.get("class_name")
                if not class_name.endswith(".cls"):
                    class_name += ".cls"
                
                res = client.compile_document(class_name)
                errors = res.get("status", {}).get("errors", [])
                summary = res.get("status", {}).get("summary", "Compilado")
                console = res.get("console", [])
                
                output = f"### Compilación de {class_name} en {server_id}\n"
                output += f"**Resumen**: {summary}\n"
                if errors:
                    output += "\n#### Errores de compilación:\n"
                    for err in errors:
                        output += f"- ❌ {json.dumps(err)}\n"
                if console:
                    output += "\n#### Consola de compilación:\n```text\n"
                    output += "\n".join(console)
                    output += "\n```"
                return {"content": [{"type": "text", "text": output}]}
                
            elif name == "iris_delete_class":
                class_name = arguments.get("class_name")
                if not class_name.endswith(".cls"):
                    class_name += ".cls"
                
                success, msg = client.delete_document(class_name)
                return {"content": [{"type": "text", "text": f"**Eliminado**: {success}\n**Detalle**: {msg}"}]}
                
            elif name == "iris_list_classes":
                package_prefix = arguments.get("package_prefix", "Custom")
                query = """
                    SELECT Name, Description, TimeChanged, Abstract, CompileAfter
                    FROM %Dictionary.ClassDefinition 
                    WHERE Name LIKE ? 
                    ORDER BY Name
                """
                results = client.run_query(query, [package_prefix + "%"])
                
                output = f"### Clases en {server_id} con prefijo `{package_prefix}`\n\n"
                if not results:
                    output += f"No se encontraron clases personalizadas con el prefijo `{package_prefix}`."
                else:
                    output += "| Nombre de Clase | Abstract | Compile After | Modificado en Servidor |\n"
                    output += "| :--- | :--- | :--- | :--- |\n"
                    for r in results:
                        output += f"| `{r.get('Name')}` | {'Sí' if r.get('Abstract') else 'No'} | `{r.get('CompileAfter') or ''}` | `{r.get('TimeChanged')}` |\n"
                return {"content": [{"type": "text", "text": output}]}
                
            elif name == "iris_get_event_logs":
                limit = min(int(arguments.get("limit", 20)), 100)
                query = """
                    SELECT TOP ? ID, TimeLogged, ConfigName, Type, Text 
                    FROM Ens_Util.Log 
                    WHERE Type = 'Error' OR Type = 'Warning' 
                    ORDER BY ID DESC
                """
                results = client.run_query(query, [limit])
                
                output = f"### Últimos {limit} Errores/Advertencias en Ens_Util.Log ({server_id})\n\n"
                if not results:
                    output += "No hay errores ni advertencias registradas en el log de Ensemble recientemente."
                else:
                    output += "| ID | Fecha Log | Config Name | Tipo | Mensaje |\n"
                    output += "| :--- | :--- | :--- | :--- | :--- |\n"
                    for r in results:
                        t_val = r.get("Type")
                        type_str = "Error" if t_val == 2 else "Warning" if t_val == 3 else str(t_val)
                        msg_text = r.get("Text", "")
                        if msg_text and len(msg_text) > 85:
                            msg_text = msg_text[:82] + "..."
                        output += f"| `{r.get('ID')}` | `{r.get('TimeLogged')}` | `{r.get('ConfigName')}` | **{type_str}** | {msg_text} |\n"
                return {"content": [{"type": "text", "text": output}]}
                
            elif name == "iris_get_recent_messages":
                limit = min(int(arguments.get("limit", 20)), 100)
                only_errors = arguments.get("only_errors", False)
                
                sql = "SELECT TOP ? ID, TimeCreated, SourceConfigName, TargetConfigName, Status, IsError, ErrorString FROM Ens.MessageHeader "
                params = [limit]
                if only_errors:
                    sql += "WHERE IsError = 1 OR Status = 'Suspended' "
                sql += "ORDER BY ID DESC"
                
                results = client.run_query(sql, params)
                
                output = f"### Últimos {limit} mensajes del bus en Ens.MessageHeader ({server_id})\n\n"
                if not results:
                    output += "No se registraron mensajes recientes en el bus."
                else:
                    output += "| ID | Fecha Creación | Origen (Source) | Destino (Target) | Estado | ¿Error? | Detalle Error |\n"
                    output += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                    for r in results:
                        err_str = r.get("ErrorString", "")
                        if err_str and len(err_str) > 55:
                            err_str = err_str[:52] + "..."
                        is_err = "❌ Sí" if r.get("IsError") == 1 else "✅ No"
                        output += f"| `{r.get('ID')}` | `{r.get('TimeCreated')}` | `{r.get('SourceConfigName')}` | `{r.get('TargetConfigName')}` | `{r.get('Status')}` | {is_err} | {err_str} |\n"
                return {"content": [{"type": "text", "text": output}]}

            elif name == "iris_execute_objectscript":
                code = arguments.get("code")
                res = client.execute_code(code)
                return {"content": [{"type": "text", "text": res}]}

            else:
                raise ValueError(f"Herramienta desconocida: {name}")
                
        except Exception as e:
            tb = traceback.format_exc()
            log_debug(f"Error handling tool call: {e}\n{tb}")
            
            # Extract VPN advice if server has one configured
            vpn_advice = ""
            try:
                server_id = arguments.get("server_id")
                if server_id and server_id in self.servers:
                    vpn = self.servers[server_id].get("vpn")
                    if vpn:
                        vpn_advice = f"\n\n👉 [Sugerencia de Conexión] Este servidor requiere la VPN: **{vpn}**. Asegúrate de tenerla activa antes de reintentar."
            except Exception:
                pass
                
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Error al ejecutar herramienta: {str(e)}{vpn_advice}\n\nConsola de traza:\n{tb}"}]
            }

    def run(self):
        log_debug("InterSystems IRIS MCP Server started running...")
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                
                request = json.loads(line)
                method = request.get("method")
                req_id = request.get("id")
                
                if method == "initialize":
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "protocolVersion": PROTOCOL_VERSION,
                            "capabilities": {
                                "tools": {}
                            },
                            "serverInfo": {
                                "name": "intersystems-iris-mcp",
                                "version": "1.0.0"
                            }
                        }
                    }
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
                    log_debug("Handshake completed successfully!")
                    
                elif method == "tools/list":
                    res = self.handle_tools_list()
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": res
                    }
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
                    
                elif method == "tools/call":
                    params = request.get("params", {})
                    t_name = params.get("name")
                    t_args = params.get("arguments", {})
                    
                    res = self.handle_tool_call(t_name, t_args)
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": res
                    }
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
                    
                else:
                    if req_id is not None:
                        response = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32601,
                                "message": f"Method not found: {method}"
                            }
                        }
                        sys.stdout.write(json.dumps(response) + "\n")
                        sys.stdout.flush()
                        
            except json.JSONDecodeError:
                log_debug("Error decoding incoming JSON-RPC request.")
            except Exception as e:
                log_debug(f"Main loop error: {e}")
                break


if __name__ == "__main__":
    server = IRISMCPServer()
    server.run()
