# InterSystems IRIS - Model Context Protocol (MCP) Server 🚀

[![MCP Version](https://img.shields.io/badge/mcp-2024--11--05-blue.svg)](https://modelcontextprotocol.io/)
[![Python Version](https://img.shields.io/badge/python-3.8%2B-brightgreen.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Un servidor nativo, robusto y ultraseguro de **Model Context Protocol (MCP)** para conectar asistentes de Inteligencia Artificial (como Claude, GPTs y agentes de desarrollo) directamente con instancias de **InterSystems IRIS** (incluyendo TrakCare y Ensemble/HealthShare). 

Este servidor sigue la filosofía **Zero-Helper**: realiza consultas SQL, recupera código fuente, compila clases y revisa logs de eventos de forma pasiva a través de la API REST nativa de Atelier, **sin necesidad de instalar ninguna clase auxiliar o wrapper en las bases de datos de producción**.

---

## ✨ Características Destacadas

* 📡 **Conexión stdio robusta**: Implementa el protocolo estándar JSON-RPC 2.0 nativo sobre canales de entrada y salida estándar, compatible con múltiples entornos e IDEs.
* 📦 **Multiservidor Centralizado**: Soporta infinitos servidores y proyectos organizados en un único repositorio de credenciales seguro en tu directorio home (`~/.iris_mcp_servers.json`).
* 🔀 **Namespace Dinámico**: Todas las herramientas permiten especificar un namespace en caliente. El servidor reconfigura dinámicamente la conexión REST para apuntar a la base de datos solicitada de forma transparente, con fallbacks inteligentes a `"USER"`.
* 🛡️ **VPN-Aware (Consistente con VPNs)**: Almacena información de túneles VPN (Cisco, GlobalProtect, FortiClient, etc.). Si una conexión falla por timeout de red, el servidor intercepta el error e inyecta una sugerencia activa indicándote qué VPN debes activar antes de reintentar.
* ❌ **Cero Huella (Zero-Footprint)**: Diseñado bajo altos estándares de seguridad para ambientes clínicos y de misión crítica, evitando alterar la integridad de los ambientes protegidos.

---

## 🛠️ Herramientas Expuestas (Tools)

El servidor MCP registra las siguientes **9 herramientas nativas** para tu asistente de IA:

| Nombre de la Herramienta | Descripción | Parámetros de Entrada |
| :--- | :--- | :--- |
| `iris_list_servers` | Muestra la tabla de todas tus instancias, credenciales, namespaces y VPNs asociadas. | Ninguno |
| `iris_query_sql` | Corre consultas SQL parametrizadas nativas (ej. búsquedas en diccionarios u operaciones). | `server_id`, `query`, `parameters` (array), `namespace` (opcional) |
| `iris_list_classes` | Lista de forma rápida clases personalizadas del sistema filtrando por paquete (evita sobrecargas). | `server_id`, `package_prefix`, `namespace` (opcional) |
| `iris_get_class` | Recupera el código fuente ObjectScript completo de una clase `.cls` en el servidor. | `server_id`, `class_name`, `namespace` (opcional) |
| `iris_save_class` | Sube o guarda código fuente de una clase ObjectScript directamente al namespace. | `server_id`, `class_name`, `content`, `namespace` (opcional) |
| `iris_compile_class` | Compila una clase en caliente en el servidor y retorna el resumen y logs del compilador. | `server_id`, `class_name`, `namespace` (opcional) |
| `iris_delete_class` | Elimina una clase de forma segura y permanente del namespace. | `server_id`, `class_name`, `namespace` (opcional) |
| `iris_get_event_logs` | Extrae las últimas trazas de error y warnings del log del bus de integración (`Ens_Util.Log`). | `server_id`, `limit` (max 100), `namespace` (opcional) |
| `iris_get_recent_messages` | Revisa el tráfico de mensajes recientes en el bus de Ensemble (`Ens.MessageHeader`). | `server_id`, `limit`, `only_errors` (bool), `namespace` (opcional) |

---

## 📋 Requisitos Previos

* **Python 3.8 o superior** instalado en el sistema.
* **Acceso de red** a la API de Atelier en tus instancias de IRIS (por defecto expuesta en el puerto CSP, ej. `80`, `52773` o `42773`).
* **Credenciales** (Usuario / Contraseña) con permisos de lectura para las consultas y operaciones REST deseadas.

---

## 🚀 Guía de Instalación Rápida

### Paso 1: Clonar el Repositorio
Clona este repositorio en un directorio local de tu máquina:
```bash
git clone https://github.com/tu-usuario/intersystems-iris-mcp.git
cd intersystems-iris-mcp
```

### Paso 2: Crear el Archivo de Configuración de Servidores
Copia la plantilla provista a tu directorio personal de usuario (home) con el nombre `.iris_mcp_servers.json`:

* En **Windows** (PowerShell):
  ```powershell
  Copy-Item mcp_config.json.template -Destination "$HOME\.iris_mcp_servers.json"
  ```
* En **Linux / macOS**:
  ```bash
  cp mcp_config.json.template ~/.iris_mcp_servers.json
  ```

Edita el nuevo archivo `.iris_mcp_servers.json` en tu carpeta home con tus servidores y credenciales de confianza. Ejemplo de estructura:

```json
{
  "servers": {
    "mi-servidor-local": {
      "host": "localhost",
      "port": 52773,
      "path_prefix": "",
      "scheme": "http",
      "username": "SuperUser",
      "password": "SYS",
      "namespace": "USER",
      "vpn": "Ninguna",
      "description": "Desarrollo Local"
    },
    "mi-trakcare-uat": {
      "host": "10.0.10.4",
      "port": 80,
      "path_prefix": "/csp/trakcare",
      "scheme": "http",
      "username": "api_user",
      "password": "SecretPassword123",
      "namespace": "UAT-APP",
      "vpn": "GlobalProtect (MyCompany VPN)",
      "description": "TrakCare Clínico UAT"
    }
  }
}
```

### Paso 3: Ejecutar Pruebas Locales (Opcional)
Para validar que tus credenciales carguen de forma perfecta y que la API REST responda rápido, ejecuta la suite de pruebas unitaria:
```bash
python test_mcp_server.py
```

---

## ⚙️ Registro en Clientes de IA

Registra el script `iris_mcp_server.py` en tu entorno preferido usando la configuración de tipo `stdio`:

### 1. Claude Desktop
Abre tu archivo de configuración de Claude Desktop en `%APPDATA%\Claude\mcp_config.json` (Windows) o `~/Library/Application Support/Claude/mcp_config.json` (macOS) y añade el nodo:

```json
{
  "mcpServers": {
    "intersystems-iris": {
      "command": "python",
      "args": [
        "/ruta/absoluta/a/intersystems-iris-mcp/iris_mcp_server.py"
      ]
    }
  }
}
```

### 2. VS Code (Cline / Roo Code)
En los ajustes de la extensión Cline o Roo Code, selecciona **MCP**, presiona **Edit MCP Settings** y pega el bloque del servidor correspondiente.

### 3. Cursor IDE
* Abre **Cursor Settings** -> **Features** -> **MCP**.
* Añade un nuevo servidor:
  * **Name**: `intersystems-iris`
  * **Type**: `stdio`
  * **Command**: `python -u "/ruta/absoluta/a/intersystems-iris-mcp/iris_mcp_server.py"`

---

## 💬 Ejemplos de Uso en el Chat

Una vez configurado, puedes interactuar directamente con tu IA en lenguaje natural:

* 🗺️ *"Lista mis servidores configurados para verificar qué VPN necesito conectar."*
* 🔍 *"Busca qué clases existen en el paquete `Custom.SAP` en el servidor `mi-trakcare-uat`."*
* 📄 *"Muéstrame el código de la clase `Custom.SAP.Api.RestHandler` en `mi-trakcare-uat`."*
* 🔨 *"Modifica el método `OnProcess` para añadir manejo de excepciones y compila la clase de nuevo."*
* 📊 *"Ejecuta la consulta SQL 'SELECT TOP 10 ID, PAPER_Name FROM SQLUser.PA_Person' en el servidor de desarrollo."*
* 🚨 *"¿Hay algún error o warning reciente en el log de Ensemble de integración?"*

---

## 📄 Licencia

Este proyecto está bajo la Licencia **MIT**. Consulta el archivo `LICENSE` para más detalles.
