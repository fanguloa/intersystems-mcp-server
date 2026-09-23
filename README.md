# InterSystems IRIS - Model Context Protocol (MCP) Server 🚀

[![MCP Version](https://img.shields.io/badge/mcp-2024--11--05-blue.svg)](https://modelcontextprotocol.io/)
[![Python Version](https://img.shields.io/badge/python-3.11%2B-brightgreen.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Un servidor de **Model Context Protocol (MCP)** para conectar asistentes de desarrollo con instancias de **InterSystems IRIS**, mediante la API de Atelier.

Se distribuye como el paquete Python instalable `isc-ai` (versión 0.3.0, solo
biblioteca estándar, Python 3.11 o superior). El paquete expone un único binario,
`isc-ai`, que agrupa el servidor MCP (`isc-ai mcp`), la CLI que administra la
configuración por proyecto (`init`, `config`, `servers`, `facility`, `doctor`) y la
instalación local de los clientes de IA (`setup`: entrada MCP en OpenCode, logo y el
harness SDD de Claude con sus agentes, comandos y skills empaquetados).
Reemplaza al script suelto `intersystems-mcp-server/iris_mcp_server.py`.

**Activación: HOLD.** El candidato local tiene discovery CCR parcial y controles
fail-closed; la detección completa en destinos reales no está verificada. La nueva
revisión independiente sigue pendiente. Consultar el
[informe de remediación, evidencia y limitaciones](../docs/agent-environment-remediation.md)
antes de cualquier registro, reinicio o prueba contra servidores.

Guardar, compilar y borrar son escrituras. La ejecución ObjectScript y algunas
herramientas de diagnóstico crean, compilan, ejecutan y eliminan clases temporales;
pueden persistir datos y no son lecturas puras ni operaciones de “cero huella”.

---

## ✨ Características Destacadas

* 📡 **Conexión stdio robusta**: Implementa el protocolo estándar JSON-RPC 2.0 nativo sobre canales de entrada y salida estándar, compatible con múltiples entornos e IDEs.
* 📦 **Configuración en dos capas, sin datos de sitio en el código**: la política del proyecto vive en `<proyecto>/.iris-mcp/config.json` (formato de CCR, ruta BASE canónica, rutas HA/VIP, catálogo de packages, facilidad de mutación) y las conexiones privadas en `~/.iris-mcp/servers.json`. Los secretos se referencian como `{env:VAR}` y se resuelven desde el entorno del proceso, extendido por el store de credenciales (`isc-ai secrets`). El listado expone metadatos seleccionados, nunca credenciales, y conserva los aliases no resueltos con su diagnóstico. Ver la sección **Configuración**.
* 🔀 **Manejo Inteligente de Namespaces (Pregunta Dinámica)**: Todas las herramientas permiten especificar un namespace en caliente. Si se omite y el servidor cuenta con múltiples entornos (ej: `BASE-CLME` e `INT`), el servidor detecta la ambigüedad y retorna una pregunta interactiva con las alternativas para que el asistente consulte cuál utilizar. Si solo hay un entorno de trabajo, se rutea de forma automática y transparente.
* 🛡️ **Diagnóstico acotado**: Los errores de transporte omiten cuerpos, argumentos y trazas sensibles. La conexión de red y la VPN requieren alcance aprobado por separado.
* 🔒 **Discovery parcial**: Identidad de namespace, presencia del motor y source control genérico se consultan sin runners. UNKNOWN bloquea toda mutación, incluida la ejecución ObjectScript arbitraria, incluso en BASE; no se consulta la suspensión de otro proceso para autorizar una escritura.
* 🧭 **Administra la suite de IA completa, no solo su propio MCP**: `isc-ai audit` compara los tres clientes (OpenCode, Claude Code, VS Code) contra un baseline declarativo de 7 servidores recomendados y reporta con códigos estables qué falta, qué está registrado con otro nombre, qué apunta a una ruta muerta y qué secreto quedó literal. `isc-ai suite` lo corrige. Ninguno de los dos pone a `isc-ai` en el camino crítico: los clientes siguen levantando y reseteando sus MCPs con sus propias herramientas.
* 🎓 **Harness SDD y doctrina InterSystems distribuibles**: el paquete lleva 47 archivos verificados por SHA-256 —agentes, comandos y 17 skills— incluida la doctrina de TrakCare que antes vivía en una sola máquina. Un gate de empaquetado impide que una credencial, una ruta personal o el identificador de un cliente entren al bundle, y un test lo mantiene cierto.
* 🗣️ **Idioma configurable en dos niveles distintos**: `--lang` / `ISC_AI_LANG` cambia lo que imprime esta CLI; `agents.reply_language` (por defecto `es-CL`) cambia el idioma en que **responden los agentes**, renderizado en el archivo de instrucciones que siempre se carga. Los artefactos —código, identificadores, JSON, respuestas MCP— quedan siempre en inglés porque son contratos.

---

## 🛠️ Herramientas Expuestas (Tools)

El servidor MCP registra las siguientes **herramientas nativas** para tu asistente de IA:

| Nombre de la Herramienta | Descripción | Parámetros de Entrada |
| :--- | :--- | :--- |
| `iris_list_servers` | Muestra metadatos de configuración y resolución de aliases, sin credenciales ni pruebas de conectividad. | Ninguno |
| `iris_list_namespaces` | Obtiene la lista de todos los namespaces disponibles en la instancia de InterSystems IRIS seleccionada. | `server_id` |
| `iris_query_sql` | Corre consultas SQL parametrizadas nativas (ej. búsquedas en diccionarios u operaciones). | `server_id`, `query`, `parameters` (array), `namespace` (opcional) |
| `iris_list_classes` | Lista de forma rápida clases personalizadas del sistema filtrando por paquete (evita sobrecargas). | `server_id`, `package_prefix`, `namespace` (opcional) |
| `iris_describe_class` | Describe metadatos declarados de una clase desde `%Dictionary`, con propiedades heredadas opcionales. | `server_id`, `class_name`, `namespace`, `include_inherited` |
| `iris_scan_namespace` | Inventaría clases por package. Devuelve `class_type` declarado y `direct_role` solo como pista basada en superclases directas conocidas; no afirma clasificación por herencia indirecta. | `server_id`, `package_prefix`, `persistent_only`, `limit`, `namespace` |
| `iris_get_class` | Recupera el código fuente ObjectScript completo de una clase `.cls` en el servidor. | `server_id`, `class_name`, `namespace` (opcional) |
| `iris_save_class` | Sube o guarda código fuente de una clase ObjectScript directamente al namespace con soporte para auto-checkout de CCR, limpieza y normalizacion de caracteres especiales a ASCII puro (evita SAX XML errors), verificacion round-trip post-escritura (detecta duplicacion/truncacion del contenido inline antes de compilar) y compilacion automatica opcional. | `server_id`, `class_name`, `content`, `namespace` (opcional), `auto_checkout` (bool, opcional, por defecto true), `clean_ascii` (bool, opcional, por defecto false), `verify` (bool, opcional, por defecto true), `compile` (bool, opcional, por defecto false) |
| `iris_save_class_from_file` | **(Preferido para overwrites grandes)** Lee una clase desde un archivo `.cls` local y conserva la ruta directa existente, sujeta a los gates legacy. | `server_id`, `class_name`, `file_path`, `namespace` (opcional), `auto_checkout`, `ccr_id`, `clean_ascii`, `verify`, `compile` |
| `iris_ccr_mutate_class_from_file` | Ruta permanente y acotada para clases bajo `mutation.class_prefix` en el `canonical_base` configurado mediante la facilidad instalada `MCP.Mutate`; no instala ni actualiza clases `MCP.*`. El archivo local debe estar bajo `mutation.local_source_root`. | `server_id`, `namespace` (el `canonical_base.namespace` explícito, ej. `BASE-SITE`), `class_name`, `file_path`, `ccr_id`, `expected_before_sha256` |
| `iris_compile_class` | Compila una clase o un documento con sufijo explícito; respeta el gate de mutación y las convenciones de nombres indicadas abajo. | `server_id`, `class_name`, `namespace` (opcional) |
| `iris_delete_class` | Elimina una clase de forma segura y permanente del namespace. | `server_id`, `class_name`, `namespace` (opcional) |
| `iris_get_document` | Recupera el fuente de un documento por nombre exacto con extensión: include files (`.inc`), rutinas (`.mac`/`.int`), clases (`.cls`). Gate READ. | `server_id`, `document_name`, `namespace` (opcional) |
| `iris_save_document` | Guarda un documento (`.inc`/`.mac`/`.int`/`.cls`) con los mismos gates y opciones que `iris_save_class`; el package se evalúa sobre el nombre sin extensión. | `server_id`, `document_name`, `content`, `namespace`, `auto_checkout`, `ccr_id`, `clean_ascii`, `verify`, `compile` |
| `iris_save_document_from_file` | Guarda un documento leyendo su fuente de un archivo local. | `server_id`, `document_name`, `file_path`, + opciones de escritura |
| `iris_patch_document` | Parche quirúrgico a un documento existente (`old_string` → `new_string`, `expected_count`); aborta sin escribir si el conteo no coincide. | `server_id`, `document_name`, `old_string`, `new_string`, `expected_count`, + opciones de escritura |
| `iris_delete_document` | Elimina un documento del namespace (CODE WRITE sin CCR: solo ruta ABSENT catalogada). | `server_id`, `document_name`, `namespace` (opcional) |
| `iris_get_event_logs` | Extrae las últimas trazas de error y warnings del log del bus de integración (`Ens_Util.Log`). | `server_id`, `limit` (max 100), `namespace` (opcional) |
| `iris_get_recent_messages` | Revisa el tráfico de mensajes recientes en el bus de Ensemble (`Ens.MessageHeader`). | `server_id`, `limit`, `only_errors` (bool), `namespace` (opcional) |
| `iris_get_ensemble_trace` | Obtiene la traza completa (Visual Trace) de una ejecución de Ensemble/IRIS Interoperability por su `SessionId`, incluyendo mensajes, logs y payloads de cuerpos de mensaje. | `server_id`, `session_id` (int/string), `include_logs` (bool, opcional, por defecto true), `include_bodies` (bool, opcional, por defecto false), `namespace` (opcional) |
| `iris_ensemble_production_control` | Consulta el estado o controla la Producción de Ensemble (`status`, `start`, `stop`, `restart`). | `server_id`, `action` (status/start/stop/restart), `production_name` (opcional), `timeout` (int, opcional), `force` (bool, opcional), `namespace` (opcional) |
| `iris_ensemble_component_control` | Gestiona componentes individuales de la producción (Business Services/Processes/Operations) en caliente (`list`, `enable`, `disable`, `restart`). | `server_id`, `action` (list/enable/disable/restart), `component_name` (string), `production_name` (opcional), `namespace` (opcional) |
| `iris_ensemble_default_settings` | Consulta, crea, actualiza o elimina ajustes por defecto del sistema de Ensemble (`Ens.Config.DefaultSettings`). | `server_id`, `action` (list/set/delete), `id` (int/string), `production_name`, `item_name`, `host_class_name`, `setting_name`, `setting_value`, `deployable` (bool), `namespace` (opcional) |
| `iris_ensemble_queue_control` | Monitorea colas de mensajes de Ensemble (`list_queues`, `list_messages`), re-envía mensajes (`resend_message`, via `Ens.MessageHeader.ResendDuplicatedMessage`: clona el header, misma SessionId, destino original o `new_target`; devuelve el nuevo header id) o desencola/aborta mensajes atascados (`abort_message`). | `server_id`, `action` (list_queues/list_messages/resend_message/abort_message), `queue_name` (string), `message_id` (int/string), `new_target` (string, ej. `HL7 Rule`), `limit` (int), `namespace` (opcional) |
| `iris_execute_objectscript` | Ejecuta código mediante una clase temporal fija `User.McpTmpRun*`; el payload puede persistir datos y usa el gate fail-closed normal. Solo una ausencia CCR suficientemente verificada y una ruta de ambiente permitida autorizan; UNKNOWN, control activo/suspendido y producción bloquean. | `server_id`, `code`, `namespace` (opcional) |
| `iris_ccr_status` | Consulta el estado de control de cambios (CCR/Perforce) de una clase o documento (bloqueado, editable, checkout). | `server_id`, `class_name` **o** `document_name`, `namespace` (opcional) |
| `iris_ccr_checkout` | Realiza un Checkout (bloqueo/edición) de una clase o documento bajo un identificador de CCR. | `server_id`, `class_name` **o** `document_name`, `ccr_id`, `namespace` (opcional) |
| `iris_ccr_undocheckout` | Revierte un Checkout en el servidor, removiendo bloqueos y deshaciendo cambios no confirmados. | `server_id`, `class_name` **o** `document_name`, `ccr_id`, `namespace` (opcional) |
| `iris_ccr_download_load` | Promueve un ItemSet aprobado a UAT. `verify_documents` requiere documentos ya existentes en UAT, toma snapshots antes de cargar y confirma después que cada uno cambió; no prueba igualdad contra BASE ni contra el payload. Sin esa lista, el resultado queda marcado `LOAD_REPORTED_SUCCESS_UNVERIFIED`. | `server_id`, `itemset_id`, `namespace`, `load`, `token`, `verify_documents` (array no vacío, sin duplicados case-insensitive; solo con `load=true`) |

**Convención de nombres:** las herramientas `iris_*_class` reciben nombres de clase con
el sufijo exacto `.cls` opcional. Conservan los componentes y su capitalización:
`%SYSTEM.OBJ` apunta a `%SYSTEM.OBJ.cls`, y `Custom.Site.Obj` a `Custom.Site.Obj.cls`.
No interpretan `.mac`, `.inc`, `.obj` u otros componentes como extensiones de rutina.
Para include files y rutinas usar las herramientas `iris_*_document`, cuyo
`document_name` lleva SIEMPRE la extensión exacta en minúsculas (`Custom.Site.Macros.inc`,
`Custom.Site.Util.mac`); no se infiere ni se agrega nada. Las herramientas CCR
aceptan exactamente uno de `class_name` (implica `.cls`) o `document_name`.
Solo compile admite ambos estilos en `class_name`: los sufijos documentales exactos en
minúsculas `.cls`, `.mac`, `.int`, `.inc`, `.bas`, `.obj` indican documento; de otro modo
agrega `.cls` sin normalizar mayúsculas. Para compilar una clase cuyo último componente
sea `obj`, usar `Custom.obj.cls`.
El catálogo de packages se evalúa sobre el nombre sin extensión, así que
`Custom.Site.Macros.inc` está cubierto por un grant `{"prefix": "Custom.Site"}`.
Ningún nombre puede ser una ruta o contener separadores codificados.

**Contrato de aliases:** cualquier entrada con `canonical_server_id` es una
referencia, incluso si conserva campos de conexión antiguos. Sus demás campos se
ignoran por completo: host, puerto, prefijo, namespace predeterminado, credenciales,
defaults CCR y controles `is_production`/`env_role` proceden solo de la entrada
concreta final. No se mezclan ni heredan overrides del alias. Catálogo, cliente y
selección de namespace usan el mismo resolver, limitado a 16 entradas por cadena.
Targets ausentes, ciclos o configuraciones inválidas producen UNRESOLVED en el
catálogo y bloqueo antes del transporte al ejecutar. Un alias listado en
`routes.alias_constraints` debe terminar en el host y prefijo declarados (el VIP
documentado); no hay fallback a nodos.

El parámetro explícito `namespace` conserva su contrato dentro de la instancia
canónica y no modifica los controles de producción. Si el default canónico es
`USER`, la selección automática consulta únicamente metadatos REST de esa misma
instancia. Cada llamada de herramienta recarga la configuración una sola vez; los
helpers de cliente usan ese snapshot y no lo recargan entre selección y ejecución.

---

## 🛡️ Seguridad y Protección de Producción

Las escrituras y promociones están prohibidas en **Producción / Live**. El control
usa la marca `is_production`, el rol configurado y los indicadores de producción
del namespace. La variable `IRIS_ALLOW_PROD` no concede autorización: su antiguo
override fue eliminado.

La ausencia CCR requiere evidencia suficiente; un motor instalado o source control
genérico configurado no prueba el binding TC. UNKNOWN bloquea guardado, compilación,
borrado, checkout, promoción y las herramientas que ejecutan ObjectScript mediante
el `execute_code` regular, incluso en BASE. El binding exacto y la suspensión del
proceso real requieren una facilidad backend autorizada antes de habilitar esas
mutaciones. El clasificador SQL no es una frontera de autorización: un SELECT puede
invocar procedimientos con efectos secundarios.

Las dos rutas MCP explícitas son `iris_execute_objectscript` y
`iris_ccr_mutate_class_from_file`. La primera ejecuta un payload que puede persistir
datos únicamente cuando el gate normal prueba ausencia CCR suficiente, permite la
ruta de ambiente configurada y el rol es explícitamente no productivo. El transporte solo crea
clases `User.McpTmpRun*`, rechaza `LIVE`/`PROD`, namespaces con apariencia productiva
y roles desconocidos, y exige borrado verificado y ausencia de residuos de control
de cambios. Esta excepción no habilita mutaciones permanentes ni herramientas que
usen internamente el `execute_code` regular. La segunda llama exclusivamente al
SQL fijo `MCP.Mutate(?,?,?,?,?)` sobre el `canonical_base` configurado; no usa
runner, PUT, endpoint de compilación, discovery previo ni evidencia cacheada.
Normaliza LF/CRLF a CRLF antes del hash y Base64; el SQLProc repite esa
canonicalización para baseline, import y round-trip. Solo acepta la combinación
independiente `TC.ChangeControl.CCR` + `%Studio.SourceControl.ISC`, valida identidad,
acción, CCR, source, archivo y usuario de la fila pendiente. Cada invocación exige
ausencia total de trabajo pendiente previo y verifica que la fila final haya nacido
en esa invocación. No intenta rollback automático de documentos `.cls`: antes de
checkout/save rechaza sin cambiar source; después de iniciar ese ciclo conserva el
estado y devuelve `UNKNOWN_PARTIAL` o un estado `*_RETAINED`, con
`manual_action_required=true` y evidencia separada sobre checkout, source y tracking.

La inicialización de la facilidad es un comando de distribución separado
(`isc-ai facility install`). Lee
internamente la plantilla empaquetada `isc_ai/resources/MCP/CCRMutation.cls.template`, la renderiza con
los valores del sitio (`{{CANONICAL_NAMESPACE}}`, `{{CCR_PREFIX}}`,
`{{MUTATION_CLASS_PREFIX}}`, `{{PROTOCOL_PREFIX}}`, entre otros), normaliza LF/CRLF a
CRLF, valida UTF-8 estricto sin BOM/NUL, la declaración exacta y el límite de tamaño,
y calcula el SHA-256 canónico **sobre la salida renderizada** en cada ejecución. Por
eso un sitio cuya facilidad se instaló antes de la plantilla sigue informando
`UNCHANGED` cuando los valores coinciden. `isc-ai facility render` produce la misma
salida y su hash sin conectarse. No acepta una ruta de fuente ni ObjectScript.
Resuelve aliases y configuración con el mismo resolver del servidor, pero exige el
ID canónico, el namespace configurado solicitado y la **ruta canónica exacta** del
`canonical_base`: la misma verificación que usa el transporte `MCP.Mutate` (host,
puerto, prefijo y namespace declarados en `canonical_base`, rol `BASE`, no
productivo). Esa verificación se aplica dos veces, sobre la configuración
resuelta y sobre el cliente ya construido; cualquier otra ruta se rechaza antes de
leer el recurso o crear el runner.

El runner privado `User.McpTmpRun*` toma un lock fijo y decide en el mismo proceso:
`INSTALLED` agrega, compila y verifica una clase ausente; `UNCHANGED` no muta y solo
se informa cuando la fuente coincide **y** el artefacto compilado está al día
(`$System.OBJ.IsUpToDate`) **y** existe el método compilado `MCP.CCRMutation||Mutate`
con `SqlProc` y `SqlName = Mutate`; `SOURCE_MATCH_NOT_COMPILED` es el estado
fail-closed cuando la fuente coincide pero esos artefactos faltan o están
desactualizados (no compila automáticamente, porque compilar bajo CCR es una
mutación); `DIFFERENT` informa el hash actual; `UPGRADED` requiere `--upgrade` y
coincidencia exacta de `--expected-current-sha256`, luego hace checkout editado,
compila y verifica. Add/edit conservan procedencia exacta y devuelven un `change_id`
positivo. No-op, drift y `SOURCE_MATCH_NOT_COMPILED` no hacen checkout, save,
compile ni crean una fila.

Namespace, binding exacto `TC.ChangeControl.CCR`, `%Studio.SourceControl.ISC`,
`IsSuspended()=0` y `GetSuspendLevel()=0` se revalidan en el mismo proceso
**inmediatamente antes del primer paso capaz de mutar** (checkout si la clase existe,
`OnBeforeSave` si no) y **otra vez inmediatamente antes de `SetTextFromString`**.
Antes del import se cierra además la ventana TOCTOU: se vuelve a comprobar la
presencia de la clase, se relee la fuente actual y su SHA canónico debe seguir
siendo el baseline observado (`--expected-current-sha256` en upgrade; ausente debe
seguir ausente en install) y se releen las filas pendientes exigiendo exactamente la
fila propia del checkout (o ninguna en install). Cualquier deriva devuelve
`CONTROL_CHANGED`/`CONTROL_CHANGED_RETAINED` o `BASELINE_DRIFT_RETAINED` con
`manual_action_required=1` y evidencia retenida; nunca continúa. El SQLProc
empaquetado `MCP.CCRMutation.MutateWorker` aplica el mismo orden: revalidación antes
del checkout y, tras `OnBeforeSave`, revalidación + recomprobación de baseline antes
del import.

El runner debe quedar ausente y sin fila pendiente; ambigüedad, baseline distinto o
resultado parcial exige inspección manual y no se reintenta automáticamente. Nunca
llama Suspend/Resume y no habilita `MCP.*`, otro target, otra fuente, ejecución
arbitraria ni un bypass UNKNOWN reutilizable.

Remediación manual de `SOURCE_MATCH_NOT_COMPILED`: un administrador compila
`MCP.CCRMutation` por el flujo CCR soportado (checkout tracked y compilación con la
herramienta sancionada), verifica `IsUpToDate` y la proyección exacta `MCP.Mutate`
en `INFORMATION_SCHEMA.ROUTINES`, y recién entonces repite `isc-ai facility install`,
que debe devolver `UNCHANGED`. El comando no compila en su lugar.

El contrato de salida del runner directo limita la captura a **180.000 caracteres**.
Ese límite mantiene el resultado SQLProc dentro de `%String(MAXLEN=1048576)` aun
en el peor caso de UTF-8 de cuatro bytes y su expansión Base64. La captura se corta
antes de convertir o codificar; el runner devuelve `MCPUNKNOWN|OUTPUT` y el cliente
informa, sin incluir el payload ni su salida, que la ejecución ocurrió, pudo
persistir datos y excedió el límite. Dentro del límite, la salida se convierte
explícitamente a bytes UTF-8 y se transporta como Base64 sin saltos de línea; el
cliente exige Base64 y UTF-8 válidos. Este límite no reduce los riesgos de efectos
persistentes ni modifica el estado **HOLD**.

---

## 📋 Requisitos Previos

* **Python 3.11 o superior** (`requires-python = ">=3.11"` en `pyproject.toml`). El
  paquete no tiene dependencias fuera de la biblioteca estándar; el extra opcional
  `socks` agrega `PySocks` para proxies SOCKS.
* **[uv](https://docs.astral.sh/uv/)** para instalar el binario `isc-ai` como
  herramienta de usuario (alternativamente `pip install` en un entorno propio).
* **Acceso de red** a la API de Atelier en tus instancias de IRIS (por defecto expuesta en el puerto CSP, ej. `80`, `52773` o `42773`).
* **Credenciales** (Usuario / Contraseña) con permisos de lectura para las consultas y operaciones REST deseadas.

---

## 📦 Instalación

El repositorio vive en `Documents/work/tools/isc-ai`. Instalación editable como
herramienta de usuario (idempotente: volver a ejecutarla actualiza la instalación):

```powershell
uv tool install --editable C:\ruta\a\isc-ai
```

El binario `isc-ai` queda en `~/.local/bin` (agregar ese directorio al `PATH` si
`uv` lo indica). Alternativa desde la raíz del repositorio, sin modo editable:

```powershell
uv tool install .
```

Mantenimiento:

```powershell
uv tool upgrade isc-ai
uv tool uninstall isc-ai
isc-ai --version
```

### Setup local de clientes

`isc-ai setup` es un plan de solo lectura. `--check` es su alias explícito y
únicamente `--apply` escribe. Por defecto prepara la entrada MCP global de
OpenCode, el logo y el harness SDD genérico de Claude:

```powershell
isc-ai setup
isc-ai setup --check
isc-ai setup --apply
isc-ai setup --apply --claude-project C:\ruta\a\proyecto
```

OpenCode se administra en `~/.config/opencode/opencode.json` (o bajo
`XDG_CONFIG_HOME`). Si existen a la vez `opencode.json` y `opencode.jsonc`, se debe
elegir uno con `--opencode-config`. Un archivo JSON estricto se mezcla de forma
atómica, preservando campos desconocidos. Un JSONC o JSON comentado queda
**byte-for-byte intacto** y el comando entrega el snippet exacto para aplicar a
mano. Reiniciar OpenCode después de cambiar configuración o plugins.

El harness se instala bajo `~/.claude/` desde un manifest versionado con SHA-256.
Archivos ausentes se instalan; hashes iguales no cambian; versiones anteriores
administradas se actualizan; una edición del usuario produce conflicto y nunca se
sobrescribe. Archivos retirados del bundle se informan como obsoletos, sin borrado
automático. El estado de propiedad vive en
`~/.config/isc-ai/setup-state.json`. Para rollback, restaurar los archivos desde
backup/control de versiones y eliminar solo los destinos que el informe y ese
estado identifiquen como administrados; `setup` nunca borra obsoletos.
Los destinos administrados deben permanecer dentro de sus raíces resueltas y no
pueden ser symlinks. En `--apply`, las escrituras se aplican como una transacción
local: ante un fallo se restauran los bytes previos o se eliminan los archivos que
antes no existían, y el estado se escribe al final.
Cada apply usa un lock exclusivo de `isc-ai`, vuelve a comprobar bytes y modo antes
de reemplazar, conserva el modo existente cuando la plataforma lo permite y hace
`fsync` del archivo y, en POSIX compatible, del directorio. Estas comprobaciones
reducen carreras accidentales; la biblioteca estándar portable no puede eliminar
un intercambio malicioso de rutas a nivel del sistema operativo entre la última
validación y la syscall de reemplazo. No se preservan ownership ni xattrs.

Opciones de exclusión: `--no-opencode`, `--no-logo`, `--no-harness`. La integración
Claude por proyecto solo se habilita con `--claude-project DIR` y exige un
directorio existente. `--with-opencode` requiere `--apply` y puede ejecutar el
package manager; no usarlo en validaciones offline.
Ese package manager se ejecuta después del preflight pero antes de las escrituras
administradas. Si luego falla una escritura, los archivos se revierten, pero una
instalación externa que ya terminó no es rollbackable automáticamente.

Los bootstrappers `install.ps1` e `install.sh` requieren un `uv` existente y son
dry-run por defecto. `install.sh` acepta solo cero argumentos, `--apply` o
`--help`, y rechaza cualquier otro argumento. `-Apply` / `--apply` instala el repositorio local y luego
delega toda configuración a `isc-ai setup --apply`; no instala Node, no edita PATH
o perfiles y no contacta IRIS.

Con la instalación editable, los cambios en `isc_ai/` se reflejan al siguiente
arranque del proceso; el servidor MCP en ejecución debe reiniciarse (ver más abajo).

---

## 🗂️ Estructura del repositorio

```text
isc-ai/
├── pyproject.toml                   # nombre isc-ai, versión 0.3.0, entry point isc-ai = isc_ai.cli:main
├── README.md
├── LICENSE
├── .gitattributes                   # los recursos empaquetados NO pasan por traducción de fin de línea
├── install.ps1 / install.sh         # dry-run por defecto; delegan en uv tool install + isc-ai setup
├── isc_ai/
│   ├── cli.py                       # CLI: mcp | init | config | servers | facility | audit | suite
│   │                                #      docs | agents | secrets | enable | disable | clients | doctor | setup
│   ├── config.py                    # dos capas, búsqueda ascendente, {env:VAR}, agents.reply_language/models
│   ├── i18n.py                      # idioma de la superficie humana de la CLI: --lang, ISC_AI_LANG, fallback
│   ├── locales/
│   │   ├── en.py                    # catálogo de referencia
│   │   └── es.py                    # traducción; mismas claves y mismos {placeholders}
│   ├── audit.py                     # auditoría read-only de clientes, suite, harness, modelos y docs
│   ├── suite.py                     # escribe la suite en los 3 clientes; secretos siempre por referencia
│   ├── toggle.py                    # enable/disable por el mecanismo nativo de cada cliente
│   ├── secrets.py                   # store de credenciales global y por proyecto, gitignoreado
│   ├── agents.py                    # modelo por sub-agente SDD, validado contra el cliente
│   ├── clients.py                   # instalación de clientes desde la receta del baseline
│   ├── docs.py                      # bloques gestionados <!-- isc-ai:NOMBRE --> en la documentación
│   ├── setup.py                     # escritura atómica con rollback, lock y harness
│   ├── server.py                    # servidor MCP stdio, herramientas iris_* y gates
│   ├── ccr_discovery.py             # discovery CCR parcial y política local
│   └── resources/
│       ├── config.template.json     # plantilla que usa `isc-ai init`
│       ├── mcp-suite.json           # baseline: 3 clientes y 7 servidores, con scope de cada setting
│       ├── reply-languages.json     # perfiles de idioma de respuesta de los agentes
│       ├── harness/                 # bundle SDD: 47 archivos con manifest verificado por SHA-256
│       ├── opencode/isc-logo.tsx    # plugin TUI
│       └── MCP/
│           └── CCRMutation.cls.template   # facilidad MCP.CCRMutation renderizada por sitio
├── tools/                           # utilidades de mantenimiento, no se distribuyen
│   ├── bundle_gate.py               # qué nunca entra al bundle: credencial, ruta personal, cliente
│   ├── pack_harness.py              # renderiza los modelos por rol en el bundle
│   └── add_doctrine_skills.py       # agrega skills al bundle, con el gate por delante
└── tests/
    ├── hermetic_test_support.py
    ├── test_config.py
    ├── test_i18n.py                 # paridad de catálogos e invariantes de contrato máquina
    ├── test_audit.py                # clasificación por cliente y garantía de solo lectura
    ├── test_suite.py                # forma por cliente, secretos por referencia, transacción
    ├── test_docs.py                 # bloques gestionados y perfiles de idioma
    ├── test_agents.py               # nunca inventa un id de modelo
    ├── test_secrets_and_toggle.py   # el valor nunca sale en un reporte; tres mecanismos
    ├── test_clients.py              # nunca pregunta en un pipe; verifica por versión
    ├── test_bundle_safety.py        # el bundle es seguro de distribuir
    ├── test_setup.py
    ├── test_dictionary_tools.py
    ├── test_ccr_discovery.py
    ├── test_ccr_verification.py
    ├── test_ccr_controlled_mutation.py
    └── live_smoke_server.py         # script en vivo; requiere servidores reales, no es parte de la suite
```

---

## ⚙️ Configuración

El código no contiene identificadores de cliente ni de sitio. Todo lo específico
(formato de CCR, ruta BASE canónica, nodos HA, packages permitidos, prefijo de
mutación) proviene de dos archivos JSON. Ambos se releen en **cada llamada de
herramienta**, por lo que las ediciones por CLI aplican en vivo; un archivo mal
formado conserva el snapshot anterior y registra el fallo sin detalles.

### Capa 1: política del proyecto — `<proyecto>/.iris-mcp/config.json`

**Resolución del proyecto.** Tanto el servidor MCP como todo comando de la CLI que
necesita un proyecto buscan **hacia arriba** desde el `cwd` el `.iris-mcp/config.json`
más cercano, del mismo modo en que git localiza `.git`. Por eso el servidor puede
lanzarse desde cualquier subdirectorio de un despliegue y toma la política de ese
despliegue. Precedencia:

1. `IRIS_MCP_PROJECT_DIR`: directorio de proyecto explícito; **desactiva la búsqueda**.
2. Ancestro más cercano del `cwd` (incluido el propio `cwd`) con `.iris-mcp/config.json`.
3. El `cwd` mismo, que produce una política **no configurada**.

`IRIS_MCP_CONFIG` fuerza la ruta del archivo de configuración con independencia del
directorio resuelto. Los comandos `config`, `facility` y `doctor` aceptan además
`--project-dir DIR`. `isc-ai doctor` muestra qué proyecto y qué archivos resolvió
el proceso.

Sin configuración de proyecto, las herramientas de lectura (gate READ) y de
ejecución acotada (gate RUNTIME) siguen operando sobre los servidores del usuario,
pero toda escritura de código (CODE WRITE) queda **bloqueada**: no hay catálogo de
packages, `canonical_base` ni formato de CCR que la autorice.

Secciones del archivo:

| Sección | Contenido |
| :--- | :--- |
| `site` | `name`; `ccr_id {prefix, digits}` define el formato del identificador CCR (prefijo `SITE` y 4 dígitos → `SITE0001`); `protocol_prefix` nombra los protocolos `<prefix>_CCR_MUTATION_V1` / `<prefix>_CCR_INITIALIZER_V1`; `production_markers`, `production_roles`, `non_production_roles`, `system_namespaces`. |
| `canonical_base` | `{server_id, host, port, path_prefix, namespace}`. Única ruta donde se admiten bundle/upload/create de CCR, el SQLProc de mutación controlada y el inicializador de la facilidad. El rol debe ser `BASE` e `is_production` falso. |
| `routes` | `direct_ha_nodes`: hosts donde las escrituras de código se bloquean salvo excepción. `alias_constraints {alias_id: {host, path_prefix}}`: un alias debe resolver a ese VIP. `uat_write_exceptions`: lista de `{label, namespace, hosts?, path_prefixes, requires_active_node}`; sin `hosts` aplica a cualquier nodo HA directo; `requires_active_node: true` exige evidencia fresca de que el nodo ejecuta la producción. |
| `packages` | `{namespace: [grants]}`. Cada grant fija exactamente uno de `prefix` (coincide con `<prefix>.<clase>`, nunca el prefijo solo), `class` (stem exacto) o `document` (nombre con extensión), más `roles` opcional. Nunca se otorgan las raíces `%`, `Ens`, `Region`, `MCP`, `TC`, `web`, `websys`, `EPR`, `User`, `Model`, `SQLUser` ni la facilidad `MCP.CCRMutation`. |
| `mutation` | `{class_prefix, local_source_root}`: clases admitidas por `iris_ccr_mutate_class_from_file` y el directorio local (relativo al proyecto) que debe contener el `.cls` aprobado; la ruta es la raíz más los componentes de la clase posteriores al prefijo. |
| `servers` (opcional) | Entradas de servidor con el mismo formato que la capa 2. Las del proyecto prevalecen sobre las del usuario. |

Este archivo es **commitable** cuando sus secretos son referencias `{env:VAR}`.

### Capa 2: servidores del usuario — `~/.iris-mcp/servers.json`

```json
{ "servers": { "my-base": { "host": "base.example.invalid", "port": 80, "path_prefix": "/base01",
  "namespace": "BASE-SITE", "env_role": "BASE", "is_production": false,
  "username": "{env:IRIS_USER}", "password": "{env:IRIS_PASSWORD}" } } }
```

Claves admitidas por entrada: `host`, `port`, `path_prefix`, `username`, `password`,
`namespace`, `env_role`, `is_production`, `proxy`, `ccr_id`, `upload_token`,
`canonical_server_id` (alias), `description`. El archivo heredado
`~/.iris_mcp_servers.json` sigue leyéndose como fallback (se registra una nota);
`isc-ai servers migrate` lo copia al nuevo destino. Este archivo es **privado**:
agregarlo al `.gitignore` global y no copiarlo a repositorios.

### Secretos

Cualquier valor de cadena puede ser `{env:VAR_NAME}`; se resuelve desde el entorno
del proceso al cargar. Una referencia sin resolver marca la entrada de servidor con
un error (no puede usarse) en lugar de detener el servidor. `isc-ai config show` redacta
`password` y `upload_token` literales salvo `--reveal`. El archivo del proyecto
rechaza secretos literales al escribir con `config set` o `config add-server`
(usar referencias `{env:VAR}` o `add-server --user`).

### `IRIS_EXTRA_PACKAGES` (compatibilidad)

La variable `IRIS_EXTRA_PACKAGES` (`NAMESPACE:pkg1,pkg2;NAMESPACE2:pkg3`) sigue
aplicándose como grant aditivo **solo para el rol `BASE`** al cargar la
configuración. El archivo `.env` del directorio padre **ya no se lee**: solo cuenta
el entorno del proceso MCP. Se aplican las mismas raíces prohibidas que en
`packages`. Preferir la sección `packages` del `config.json` del proyecto.

### CLI

```powershell
isc-ai [--version] [--lang en|es] <comando> [opciones]
```

La CLI no contacta IRIS salvo en `isc-ai mcp` e `isc-ai facility install`.
`isc-ai setup --with-opencode` es la única excepción capaz de invocar un package
manager y requiere `--apply`; el resto de `setup` solo administra archivos locales.
Invocar `isc-ai` sin comando imprime la ayuda completa con ejemplos y termina en `0`
(no arranca el servidor); cada comando trae sus propios ejemplos en
`isc-ai <comando> -h`. Los comandos `config`, `facility` y `doctor` aceptan
`--project-dir DIR`; sin él aplican la búsqueda ascendente descrita en la Capa 1.

#### Idioma de la CLI

`--lang es` o `ISC_AI_LANG=es` traducen **solo la superficie humana**: ayuda,
ejemplos, confirmaciones, notas, advertencias y errores que nacen en la CLI. El flag
vale antes o después del comando (`isc-ai --lang es doctor` o `isc-ai doctor --lang es`).
Precedencia: `--lang` > `ISC_AI_LANG` > inglés. No hay autodetección del locale del
sistema, a propósito: el mismo comando debe imprimir lo mismo en tu shell, en CI y en
la documentación. Un valor no soportado no aborta nada: degrada a inglés y lo avisa.

Queda **siempre en inglés**, en cualquier idioma:

| Superficie | Por qué |
| :--- | :--- |
| Descripciones de tools y respuestas del MCP | Las lee el cliente de IA; son contrato, y codifican los gates (`READ`, `RUNTIME`, `CODE WRITE`, `is_production`). Dos redacciones del mismo gate duplican la superficie donde una diferencia de matiz cambia el comportamiento del agente. |
| JSON de `doctor`, `config validate`, `setup`, `facility` | Lo parsean scripts. `doctor` **reporta** el idioma activo (`language`, `language_source`) pero su JSON no se traduce. |
| Veredictos y estados | `CONTROLLED_ACTIVE`, `SUSPENDED`, `ABSENT`, `INSTALLED`, `DIFFERENT`, `UNCHANGED`, `UPGRADED`, `VERIFIED`, `PENDING_VERIFICATION`. |
| Códigos de salida, claves de configuración, rutas punteadas, ids de servidor, namespaces, nombres de clase | Son identificadores. |
| Errores nacidos en `config.py`, `server.py` y `ccr_discovery.py` | Son runtime: viajan al cliente de IA además de a tu terminal. En modo `es` vas a ver el marco en español con el detalle técnico en inglés. |

Los catálogos viven en `isc_ai/locales/en.py` y `es.py`, con `en.py` como referencia.
`tests/test_i18n.py` exige paridad de claves y de `{placeholders}`, que ninguna clave
quede muerta, que el español lleve tildes y que el JSON y los exit codes sean idénticos
en ambos idiomas. Los mensajes de argparse que no son nuestros (`unrecognized
arguments`, `invalid choice`) pertenecen a su dominio gettext y quedan en inglés.

| Comando | Efecto |
| :--- | :--- |
| `isc-ai mcp` | Ejecuta el servidor MCP sobre stdio. Es lo que lanza el cliente de IA. |
| `isc-ai init [DIR] [--from PLANTILLA] [--force] [--no-servers]` | Crea `<DIR o cwd>/.iris-mcp/config.json` desde la plantilla empaquetada (`isc_ai/resources/config.template.json`) o una plantilla propia (validada). **Idempotente**: si el archivo ya existe no hace nada, lo informa y termina en `0`; `--force` lo regenera. `--no-servers` omite la sección de servidores de ejemplo. |
| `isc-ai config show [--servers] [--reveal]` | Imprime la política del proyecto o, con `--servers`, los servidores mezclados de ambas capas; secretos redactados salvo `--reveal`. Las notas de carga van a stderr. |
| `isc-ai config set <ruta.punteada> <valor>` | Tipa el valor según el esquema actual (int/bool/lista/objeto vía JSON; cadena en otro caso), valida la política y rechaza secretos literales antes de escribir. Ej.: `canonical_base.host`. |
| `isc-ai config unset <ruta.punteada>` | Elimina una clave de la política. |
| `isc-ai config add-server <id> --host H [--port] [--path-prefix] [--namespace] [--role] [--production] [--username] [--password] [--upload-token] [--ccr-id] [--proxy] [--description] [--user]` | Agrega o reemplaza una entrada. `--user` escribe en `~/.iris-mcp/servers.json` en lugar del archivo del proyecto; usuario y contraseña valen `{env:IRIS_USER}` / `{env:IRIS_PASSWORD}` por defecto. |
| `isc-ai config remove-server <id> [--user]` | Elimina una entrada del proyecto o, con `--user`, del archivo del usuario. |
| `isc-ai config validate` | Resumen JSON de la configuración efectiva; sale con `1` si hay problemas (secretos sin resolver, entradas mal formadas, `canonical_base.server_id` sin entrada de servidor). |
| `isc-ai servers migrate [--force]` | Copia el archivo heredado `~/.iris_mcp_servers.json` a `~/.iris-mcp/servers.json`. Falla si el destino existe, salvo `--force`. |
| `isc-ai facility render [--output RUTA]` | Renderiza `MCP.CCRMutation` para el sitio configurado e imprime clase, SHA-256 y tamaño, sin conectarse. `--output` escribe la clase renderizada. |
| `isc-ai facility install --server S --namespace NS --ccr CCR [--upgrade --expected-current-sha256 H]` | Instala o actualiza condicionalmente la facilidad en el `canonical_base` (ver **Guía rápida**, paso 3). `--upgrade` y `--expected-current-sha256` son obligatorios uno con otro. |
| `isc-ai doctor` | Informe JSON de lo que resolvería este proceso: `project_dir`, rutas y presencia de `project_config` y `user_servers`, `site`, `configured`, `ccr_format`, `canonical_base`, `mutation_configured`, `servers`, `notes`, `problems`, `cwd`, variables de entorno relevantes (`IRIS_MCP_PROJECT_DIR`, `IRIS_MCP_CONFIG`, `IRIS_EXTRA_PACKAGES`, `IRIS_MCP_TIMEOUT`, `ISC_AI_LANG`), `language`, `language_source` y `version`. Sale con `1` si hay problemas; si no encuentra configuración de proyecto sugiere `isc-ai init` en stderr. |
| `isc-ai setup [--check|--apply] [--adopt-harness]` | Planifica la configuración local de OpenCode, logo y harness Claude; solo `--apply` escribe. `--adopt-harness` sobrescribe los archivos del harness en conflicto y registra el bundle como línea base: es la salida de un conflicto permanente cuando un archivo lo instaló otra herramienta. Admite `--claude-project`, `--opencode-config`, exclusiones, timeout y `--with-opencode`. |
| `isc-ai audit [--errors-only]` | Auditoría **de solo lectura** de la suite: presencia y versión de los tres clientes, los 7 servidores recomendados × 3 clientes, estado del harness, modelos por sub-agente y bloques gestionados de documentación. Nunca escribe, nunca levanta un servidor, nunca abre una conexión. Códigos de hallazgo estables y en inglés (`SECRET_LEAK`, `PATH_NOT_FOUND`, `COMMAND_NOT_FOUND`, `SERVER_DRIFT`, `SERVER_ALIAS`, `SERVER_DUPLICATE`, `HARNESS_FROZEN`, …). Sale con `1` si hay errores, `2` si solo hay advertencias, `0` si está limpio. |
| `isc-ai suite [--apply] [--client C] [--server S] [--prune-claude-global]` | Escribe la suite recomendada en los tres clientes. Normaliza la forma pero **nunca inventa valores**: preserva lo que ya existe, mapea nombres de variable viejos conservando su valor y propaga un valor de un cliente a otro antes que adivinar. Los secretos nunca quedan literales: referencia en la sintaxis de cada cliente, y en VS Code su mecanismo nativo `inputs` con `password: true`. Un scope de escritura por cliente. Todo en una transacción o ninguna. |
| `isc-ai docs [--apply]` | Refresca los bloques gestionados `<!-- isc-ai:NOMBRE -->`: `topology` en `WORKFLOW.md` (derivado de `.iris-mcp/config.json`, así la topología documentada no puede separarse de la vigente) y `language` en `AGENTS.md`. Nada fuera de los marcadores se lee ni se reescribe. |
| `isc-ai agents [--apply] [--no-verify-models]` | Modelo por sub-agente SDD. Claude Code lo lee del frontmatter empaquetado; OpenCode de `agent.<rol>.model`, que exige id con proveedor. **Un id concreto nunca se inventa**: sale de `agents.models` y se valida contra `opencode models`, así un id viejo es error antes de escribir. Sin configurar, el sub-agente se declara sin modelo y se reportan los candidatos de su familia. |
| `isc-ai secrets list\|set\|unset <KEY> [--scope global\|project]` | Store de credenciales. Dos scopes: `~/.iris-mcp/secrets.json` y `<proyecto>/.iris-mcp/secrets.json`, este último agregado al `.gitignore` en la primera escritura. **El valor nunca se toma de la línea de comandos** (historial, lista de procesos, grabaciones): se lee de un prompt sin eco o de stdin. `list` nunca imprime un valor. Los stores extienden el entorno al resolver `{env:VAR}`; gana el proceso, después el proyecto, al final el global. |
| `isc-ai enable\|disable <server> [--apply] [--client C]` | Un verbo, tres mecanismos nativos: OpenCode `enabled: false`, Claude Code las listas `projects.<ruta>.disabledMcpjsonServers`, y VS Code —que no tiene ese campo— quitando o reescribiendo la entrada. El reporte dice cuál se usó. |
| `isc-ai clients [<client>…] [--apply] [--yes]` | Reporta qué clientes están instalados con su versión real e instala el que falte con la receta del baseline. Es lo único que cambia el sistema fuera de archivos de configuración: plan por defecto, confirmación por cliente, y **la confirmación es interactiva solo en una terminal** porque este binario también sirve MCP por stdio. El éxito se verifica preguntando la versión, no por exit code. |

Ejemplo mínimo, ejecutado desde el directorio del despliegue:

```powershell
isc-ai init --no-servers
isc-ai config set site.ccr_id.prefix SITE
isc-ai config set canonical_base.namespace BASE-SITE
isc-ai config set packages.BASE-SITE '[{"prefix": "Custom.Site"}]'
isc-ai config add-server my-base --host base.example.invalid --path-prefix /base01 --namespace BASE-SITE --role BASE --user
isc-ai config validate
isc-ai doctor
```

Puesta a punto de una estación completa, en orden. Todo lo que escribe está en modo
plan hasta que se pasa `--apply`, así que la secuencia se puede leer entera antes de
ejecutar nada:

```powershell
isc-ai clients                 # que clientes hay y con que version
isc-ai audit                   # que esta mal hoy, sin tocar nada
isc-ai setup --apply           # entrada MCP, logo y harness SDD
isc-ai suite --apply           # los 7 servidores en los 3 clientes
isc-ai secrets set JIRA_API_TOKEN
isc-ai agents --apply          # modelo por sub-agente en OpenCode
isc-ai docs --apply            # topologia e idioma en la documentacion
isc-ai audit                   # deberia quedar en 0 errores
```

---

## 🚀 Guía rápida local — activación HOLD

### 1. Revisar el candidato y sus backups
El informe enlazado arriba identifica archivos, manifests SHA-256, cambios de
compatibilidad y pasos de rollback. La aprobación de trabajo local no autoriza
conexiones, lectura de credenciales, instalación ni activación.

### 2. Ejecutar únicamente la suite offline explícita
Desde la raíz del repositorio, con Python 3.11+ y después de verificar la ruta:
```powershell
python -m unittest discover -s tests -t . -p "test_*.py"
```
La suite usa datos sintéticos y bloquea red/configuración real. El patrón
`test_*.py` incluye los módulos unittest herméticos; `tests/live_smoke_server.py`
no coincide con ese patrón y sigue siendo un script en vivo que requiere alcance
independiente aprobado. No ejecutarlo durante validación offline.

### 3. Inicializar la facilidad empaquetada cuando exista autorización remota

Instalación condicional:

```powershell
isc-ai facility install --server <id> --namespace <ns> --ccr <SITE0001>
```

Upgrade explícito y ligado al baseline observado:

```powershell
isc-ai facility install --server <id> --namespace <ns> --ccr <SITE0001> --upgrade --expected-current-sha256 <sha256-actual>
```

`--ccr` debe cumplir el formato configurado en `site.ccr_id`; argparse solo valida
la forma general y el servidor aplica el formato del sitio. El comando no usa upload
token. Códigos de salida: `INSTALLED`, `UNCHANGED` y
`UPGRADED` verificados terminan en `0`; `DIFFERENT` imprime el registro con el hash
actual y termina en `2`; `SOURCE_MATCH_NOT_COMPILED`, cualquier estado retenido o
rechazado, errores de política y excepciones inesperadas terminan en `1`. Una
excepción inesperada imprime en stderr solo el tipo de error, acotado y sin
traceback ni detalles (rutas, credenciales o respuestas del servidor). Errores o
cleanup ambiguo requieren inspección manual antes de cualquier nuevo intento.

### 4. Mantener pendiente la configuración/activación
Los aliases declarados en `routes.alias_constraints` deben resolver al VIP
configurado; no hay elección de nodo mediante escrituras. No crear/copiar
configuración con credenciales literales ni iniciar el servidor como parte de esta
validación offline; usar referencias `{env:VAR}`. La compatibilidad real del
catálogo SQL, el binding TC y el control operativo del proceso siguen pendientes.

Después de instalar o actualizar deben verificarse la proyección exacta
`MCP.Mutate`, la fila pendiente y los permisos mínimos. La activación del tool
permanece en **HOLD** hasta revisión humana. Modificar el código del paquete
`isc_ai/` o la plantilla de su recurso requiere cerrar y reiniciar el cliente
(OpenCode); el proceso stdio y los recursos no se recargan en vivo, aun con
instalación editable. Los archivos de configuración (`config.json` y
`servers.json`) sí se releen en cada llamada. El servidor MCP se inicia solo con
`isc-ai mcp`; `isc-ai` sin comando imprime la ayuda completa con ejemplos y
termina en `0`, y cada comando trae sus propios ejemplos en `isc-ai <comando> -h`.

---

## ⚙️ Registro en Clientes de IA

**Pendiente mientras continúe HOLD.** El transporte es stdio, pero el registro y
reinicio requieren revisión completada y autorización explícita. No se ha verificado
la activación de este candidato en ningún cliente de IA o servidor real.

Cuando exista autorización, el registro es **una sola entrada para todos los
proyectos**: no hay variables de entorno por proyecto ni rutas al repositorio. El
servidor resuelve el proyecto desde su `cwd` con la búsqueda ascendente de
`.iris-mcp/config.json`. Ejemplo para `opencode.json`:

```json
{
  "mcp": {
    "isc-ai": {
      "type": "local",
      "command": ["isc-ai", "mcp"],
      "enabled": true,
      "environment": { "IRIS_MCP_TIMEOUT": "1800" }
    }
  }
}
```

OpenCode lanza el proceso en el contexto de trabajo de la sesión; si ese directorio
(o alguno de sus ancestros) contiene `.iris-mcp/config.json`, esa política gobierna
la sesión. Sin configuración de proyecto, las herramientas READ/RUNTIME funcionan y
las escrituras de código quedan bloqueadas. Para confirmar qué proyecto verá el
servidor, ejecutar `isc-ai doctor` desde el mismo directorio.

---

## 💬 Ejemplos de Uso en el Chat

Ejemplos para una futura sesión con alcance remoto autorizado; no ejecutarlos como
parte del quickstart offline:

* 🗺️ *"Lista los metadatos de servidores y los aliases no resueltos."*
* 🔍 *"Busca qué clases existen en el paquete `Custom.SAP` en el servidor `mi-servidor-uat`."*
* 📄 *"Muéstrame el código de la clase `Custom.SAP.Api.RestHandler` en `mi-servidor-uat`."*
* 🔨 *"Modifica el método `OnProcess` para añadir manejo de excepciones y compila la clase de nuevo."*
* 📊 *"Ejecuta la consulta SQL 'SELECT TOP 10 ID, Name FROM Sample.Person' en el servidor de desarrollo."*
* 🚨 *"¿Hay algún error o warning reciente en el log de Ensemble de integración?"*

---

## 📄 Licencia

Este proyecto está bajo la Licencia **MIT**. Consulta el archivo `LICENSE` para más detalles.
