"""Catálogo en español de la superficie humana de la CLI isc-ai.

Debe declarar exactamente las mismas claves que `en.py` y los mismos
{placeholders} en cada entrada; el test de paridad falla si difieren.

Las llaves literales van siempre duplicadas ({{env:VAR}}) porque cada entrada pasa
por `str.format`. No se traducen: nombres de comandos, flags, claves de
configuración, rutas, ids de servidor, namespaces, ni los estados y veredictos del
runtime (INSTALLED, DIFFERENT, CONTROLLED_ACTIVE, ABSENT).
"""

MESSAGES = {
    # -- chrome de argparse ----------------------------------------------
    "chrome.usage_prefix": "uso: ",
    "chrome.group.options": "opciones",
    "chrome.group.arguments": "argumentos",
    "chrome.group.commands": "comandos",
    "chrome.group.subcommands": "subcomandos",
    "chrome.examples_label": "ejemplos:",
    "chrome.help": "muestra esta ayuda y termina",
    "chrome.version": "muestra la versión del programa y termina",
    "chrome.lang": "idioma de la ayuda y los mensajes de esta CLI: en, es "
                   "(por defecto: en, o la variable de entorno ISC_AI_LANG)",

    # -- nivel superior --------------------------------------------------
    "app.description": (
        "Configura y audita el entorno de desarrollo agéntico: los clientes de IA, los\n"
        "servidores MCP con los que hablan, el harness de instrucciones, las fases SDD con sus\n"
        "modelos, y las credenciales que todo eso necesita.\n"
        "\n"
        "Empieza por `wizard`: pregunta qué necesitas y lo configura. `audit` reporta lo que ya\n"
        "hay sin escribir nada. El resto son los verbos individuales que el asistente maneja,\n"
        "para cuando sabes exactamente qué quieres cambiar.\n"
        "\n"
        "Los servidores MCP los levantan los clientes, nunca este CLI, y este CLI nunca está en\n"
        "medio de esa conversación. Los comandos que resuelven un proyecto aceptan\n"
        "--project-dir; si no, gana el ancestro más cercano que tenga .iris-mcp/config.json,\n"
        "igual que git encuentra .git."
    ),
    "app.examples": (
        "isc-ai wizard                          configuración guiada: pregunta y configura\n"
        "isc-ai audit                           qué hay configurado ahora, sin escribir nada\n"
        "isc-ai suite --apply                   escribe los MCPs recomendados en cada cliente\n"
        "isc-ai secrets set JIRA_API_TOKEN      guarda una credencial sin mostrarla\n"
        "isc-ai setup --apply                   instala el harness de instrucciones y las fases SDD\n"
        "isc-ai agents                          qué modelo corre cada sub-agente SDD\n"
        "isc-ai doctor                          qué proyecto, archivos y servidores se resuelven aquí"
    ),
    "app.footer": "Ejecuta 'isc-ai <command> -h' para ver los flags y ejemplos de un solo comando.",

    # -- flags compartidos -----------------------------------------------
    "flag.project_dir": "directorio del proyecto (por defecto: el ancestro más cercano del cwd con "
                        ".iris-mcp/, o IRIS_MCP_PROJECT_DIR)",

    # -- mcp -------------------------------------------------------------
    "cmd.mcp.help": "ejecuta el servidor MCP sobre stdio",
    "cmd.mcp.examples": (
        "isc-ai mcp\n"
        "isc-ai mcp --project-dir C:\\work\\deployments\\site\n"
        "\n"
        "Lo lanza el cliente de IA y habla el protocolo MCP por stdout, así que ejecútalo\n"
        "a mano solo para comprobar que el servidor arranca y lista los servidores esperados.\n"
        "Sus descripciones de tools y sus respuestas están siempre en inglés: son un contrato\n"
        "que lee el cliente de IA, no salida de terminal, por lo que --lang no las afecta."
    ),

    # -- init ------------------------------------------------------------
    "cmd.init.help": "crea .iris-mcp/config.json desde la plantilla empaquetada (idempotente)",
    "cmd.init.examples": (
        "isc-ai init                            crea .iris-mcp/config.json en el cwd\n"
        "isc-ai init C:\\work\\deployments\\site\n"
        "isc-ai init --no-servers               solo la política, sin servidores de ejemplo\n"
        "isc-ai init --force                    regenera desde la plantilla\n"
        "isc-ai init --from ./my-policy.json"
    ),
    "flag.init.directory": "directorio del proyecto (por defecto: el cwd)",
    "flag.init.template": "archivo de plantilla alternativo",
    "flag.init.force": "sobrescribe una configuración de proyecto existente",
    "flag.init.no_servers": "omite la sección de servidores de ejemplo",

    # -- config ----------------------------------------------------------
    "cmd.config.help": "inspecciona y edita archivos de configuración",
    "cmd.config.examples": (
        "isc-ai config show\n"
        "isc-ai config show --servers\n"
        "isc-ai config set canonical_base.namespace BASE-SITE\n"
        "isc-ai config add-server my-base --host base.example.invalid --namespace BASE-SITE --role BASE\n"
        "isc-ai config validate"
    ),
    "cmd.config.show.help": "imprime la configuración efectiva (secretos redactados)",
    "cmd.config.show.examples": (
        "isc-ai config show                     la política del proyecto\n"
        "isc-ai config show --servers           servidores combinados de ambas capas\n"
        "isc-ai config show --servers --reveal  secretos literales, solo para depurar"
    ),
    "flag.show.reveal": "imprime los secretos literales en lugar de redactarlos",
    "flag.show.servers": "imprime los servidores combinados en lugar de la política del proyecto",
    "cmd.config.set.help": "define un valor del proyecto por ruta punteada, ej. canonical_base.host",
    "cmd.config.set.examples": (
        "isc-ai config set site.name SITE\n"
        "isc-ai config set canonical_base.host base.example.invalid\n"
        "isc-ai config set site.ccr_id.prefix SITE\n"
        "isc-ai config set packages.BASE-SITE '[{{\"prefix\": \"Custom.Site\"}}]'"
    ),
    "flag.set.value": "JSON cuando se puede parsear (números, booleanos, listas, objetos); "
                      "en otro caso, una cadena",
    "cmd.config.unset.help": "elimina un valor del proyecto por ruta punteada",
    "cmd.config.unset.examples": (
        "isc-ai config unset mutation\n"
        "isc-ai config unset packages.BASE-SITE"
    ),
    "cmd.config.add_server.help": "agrega o reemplaza una entrada de servidor",
    "cmd.config.add_server.examples": (
        "isc-ai config add-server my-base --host base.example.invalid --path-prefix /base01 "
        "--namespace BASE-SITE --role BASE\n"
        "isc-ai config add-server my-live --host live.example.invalid --namespace LIVE-SITE "
        "--role LIVE --production\n"
        "isc-ai config add-server my-base --user --host base.example.invalid --username me "
        "--password 'literal'\n"
        "\n"
        "El archivo del proyecto es commiteable, por eso solo acepta referencias {{env:VAR}}\n"
        "para credenciales; --user escribe secretos literales en ~/.iris-mcp/servers.json."
    ),
    "flag.add.role": "env_role, ej. BASE, UAT, LIVE",
    "flag.add.production": "marca la entrada como is_production=true",
    "flag.add.username": "valor literal o referencia {{env:VAR}}",
    "flag.add.password": "valor literal o referencia {{env:VAR}}",
    "flag.add.upload_token": "valor literal o referencia {{env:VAR}} para el token de subida de CCR",
    "flag.add.ccr_id": "CCR por defecto para este servidor",
    "flag.add.user": "escribe en ~/.iris-mcp/servers.json en lugar del archivo del proyecto",
    "cmd.config.remove_server.help": "elimina una entrada de servidor",
    "cmd.config.remove_server.examples": (
        "isc-ai config remove-server my-base\n"
        "isc-ai config remove-server my-base --user"
    ),
    "flag.remove.user": "elimina de ~/.iris-mcp/servers.json en lugar del archivo del proyecto",
    "cmd.config.validate.help": "carga y valida la configuración efectiva",
    "cmd.config.validate.examples": (
        "isc-ai config validate\n"
        "isc-ai config validate --project-dir C:\\work\\deployments\\site\n"
        "\n"
        "Imprime un resumen JSON y termina en 1 cuando algo está mal: secretos sin resolver,\n"
        "entradas mal formadas o un canonical_base.server_id sin entrada de servidor."
    ),

    # -- servers ---------------------------------------------------------
    "cmd.servers.help": "conexiones de servidor a nivel de usuario",
    "cmd.servers.examples": (
        "isc-ai servers migrate\n"
        "isc-ai servers migrate --force"
    ),
    "cmd.servers.migrate.help": "copia el archivo heredado ~/.iris_mcp_servers.json a ~/.iris-mcp/servers.json",
    "cmd.servers.migrate.examples": (
        "isc-ai servers migrate\n"
        "isc-ai servers migrate --force        sobrescribe un ~/.iris-mcp/servers.json existente"
    ),
    "flag.migrate.force": "sobrescribe el archivo de destino",

    # -- rules ---------------------------------------------------------
    "cmd.rules.help": "concesiones de paquetes y excepciones de capacidad explícitas (isc-ai.rules.json)",
    "cmd.rules.examples": (
        "isc-ai rules list\n"
        "isc-ai rules show base-direct-node\n"
        "isc-ai rules check code_write --namespace UAT-SITE --host 10.0.0.1 --role UAT\n"
        "isc-ai rules add --id ticket-123 --label \"parche UAT\" --ticket TCK-1 --expires 2026-12-31 "
        "--capability code_write\n"
        "isc-ai rules migrate --apply"
    ),
    "cmd.rules.list.help": "lista cada excepción con sus días para expirar",
    "cmd.rules.list.examples": "isc-ai rules list",
    "cmd.rules.show.help": "muestra una excepción completa",
    "cmd.rules.show.examples": "isc-ai rules show base-direct-node",
    "cmd.rules.check.help": "evalúa una capacidad contra un namespace/host/role/server/path-prefix",
    "cmd.rules.check.examples": "isc-ai rules check code_write --namespace UAT-SITE --host 10.0.0.1 --role UAT",
    "cmd.rules.validate.help": "reporta excepciones y concesiones de paquetes vencidas",
    "cmd.rules.validate.examples": "isc-ai rules validate",
    "cmd.rules.add.help": "agrega una excepción nueva (planifica por defecto, --apply para escribir)",
    "cmd.rules.add.examples": (
        "isc-ai rules add --id ticket-123 --label \"parche UAT\" --ticket TCK-1 --expires 2026-12-31 "
        "--capability code_write --roles UAT --namespaces INT --path-prefixes /uat01\n"
        "isc-ai rules add --id ticket-123 ... --apply"
    ),
    "cmd.rules.renew.help": "extiende o acorta el vencimiento de una excepción (planifica por defecto, --apply para escribir)",
    "cmd.rules.renew.examples": (
        "isc-ai rules renew base-direct-node --expires 2027-01-01 --apply\n"
        "isc-ai rules renew --expiring-within 7 --expires 2027-01-01 --apply"
    ),
    "cmd.rules.remove.help": "elimina una excepción (planifica por defecto, --apply para escribir)",
    "cmd.rules.remove.examples": "isc-ai rules remove base-direct-node --apply",
    "cmd.rules.apply.help": "aplica un plan pendiente por su huella (de una corrida solo-plan o de la herramienta MCP iris_rules)",
    "cmd.rules.apply.examples": "isc-ai rules apply 3f9a1c2b7e4d5601",
    "cmd.rules.migrate.help": "mueve claves heredadas de config.json/servers.json a isc-ai.rules.json",
    "cmd.rules.migrate.examples": (
        "isc-ai rules migrate\n"
        "isc-ai rules migrate --apply"
    ),
    "flag.rules.id": "id de la excepción",
    "flag.rules.label": "etiqueta legible",
    "flag.rules.ticket": "referencia del ticket",
    "flag.rules.expires": "fecha de vencimiento, YYYY-MM-DD",
    "flag.rules.capability": "code_write, ensemble_control, runner o read",
    "flag.rules.roles": "roles separados por coma",
    "flag.rules.hosts": "hosts separados por coma",
    "flag.rules.namespaces": "namespaces separados por coma",
    "flag.rules.servers": "ids de servidor separados por coma",
    "flag.rules.path_prefixes": "prefijos de ruta separados por coma",
    "flag.rules.active_node_evidence_namespace": "namespace usado para probar que el nodo está activo",
    "flag.rules.runner_namespace": "solo capacidad runner: namespace de destino del runner",
    "flag.rules.apply": "escribe el cambio (por defecto: solo lo planifica)",
    "flag.rules.expiring_within": "renueva cada excepción que vence dentro de estos días, en vez de una por id",
    "flag.rules.check_namespace": "namespace a evaluar",
    "flag.rules.check_host": "host a evaluar",
    "flag.rules.check_role": "role a evaluar",
    "flag.rules.check_server": "id de servidor a evaluar",
    "flag.rules.check_path_prefix": "prefijo de ruta a evaluar",
    "msg.rules.list.none": "no hay excepciones configuradas en isc-ai.rules.json",
    "msg.rules.list.entry": "{name}: {detail} -- expira {value} ({count} día(s) restante(s))",
    "msg.rules.list.entry_expired": "{name}: {detail} -- VENCIDA el {value}",
    "msg.rules.list.expiring_soon": "  ADVERTENCIA: expira dentro de 7 días",
    "msg.rules.check.allowed": "permitido por: {names}",
    "msg.rules.check.denied": "denegado: ninguna excepción coincide con la capacidad '{value}'",
    "msg.rules.validate.ok": "no se encontraron problemas",
    "msg.rules.plan": "plan: {detail} (huella {value}; ejecute 'isc-ai rules apply {value}' o repita con --apply)",
    "msg.rules.applied": "aplicado: {detail}",
    "msg.rules.apply.ok": "plan pendiente {value} aplicado",

    # -- facility --------------------------------------------------------
    "cmd.facility.help": "facilidad MCP.CCRMutation del sitio configurado",
    "cmd.facility.examples": (
        "isc-ai facility render\n"
        "isc-ai facility install --server my-base --namespace BASE-SITE --ccr SITE0757"
    ),
    "cmd.facility.render.help": "renderiza la facilidad de este sitio e imprime su SHA-256",
    "cmd.facility.render.examples": (
        "isc-ai facility render                                  clase, sha256 y tamaño, sin conectarse\n"
        "isc-ai facility render --output ./MCP.CCRMutation.cls"
    ),
    "flag.render.output": "escribe la clase renderizada en esta ruta",
    "cmd.facility.install.help": "instala o actualiza condicionalmente la facilidad en el BASE canónico",
    "cmd.facility.install.examples": (
        "isc-ai facility install --server my-base --namespace BASE-SITE --ccr SITE0757\n"
        "isc-ai facility install --server my-base --namespace BASE-SITE --ccr SITE0757 "
        "--upgrade --expected-current-sha256 <64-hex>\n"
        "\n"
        "Códigos de salida: 0 INSTALLED/UNCHANGED/UPGRADED, 2 DIFFERENT (el destino no\n"
        "coincide con el SHA-256 esperado), 1 cualquier otro caso. --upgrade y\n"
        "--expected-current-sha256 son obligatorios uno con el otro."
    ),
    "flag.install.ccr": "identificador de CCR en el formato configurado del sitio (site.ccr_id)",

    # -- audit -----------------------------------------------------------
    "cmd.audit.help": "audita los clientes de IA, la suite MCP recomendada, el harness y los modelos por rol",
    "cmd.audit.examples": (
        "isc-ai audit                           todo, como reporte JSON\n"
        "isc-ai audit --errors-only             solo lo que está roto\n"
        "isc-ai audit --project-dir C:\\work\\deployments\\site\n"
        "\n"
        "Solo lectura: nunca escribe, nunca levanta un servidor y nunca abre una conexión.\n"
        "Códigos de salida: 0 limpio, 1 al menos un error, 2 solo advertencias o notas.\n"
        "Los códigos de hallazgo son estables y están siempre en inglés, para poder\n"
        "grepearlos y afirmarlos: CLIENT_MISSING, CONFIG_AMBIGUOUS, SERVER_MISSING,\n"
        "SERVER_ALIAS, SERVER_DRIFT, SERVER_DISABLED, SERVER_DUPLICATE, SERVER_EXTRA,\n"
        "SECRET_LEAK, SETTING_MISSING, SETTING_UNKNOWN, COMMAND_NOT_FOUND, PATH_NOT_FOUND,\n"
        "HARNESS_FROZEN, HARNESS_INCOMPLETE, HARNESS_OBSOLETE, MODEL_ROLES_DECLARATIVE,\n"
        "MODEL_ROLE_UNSET, MODEL_ROLES_CLIENT_GAP, DOC_MISSING, DOC_UNMANAGED."
    ),
    "flag.audit.errors_only": "imprime solo los hallazgos con severidad error",
    "flag.audit.suite": "archivo alternativo de baseline de la suite",

    # -- suite -----------------------------------------------------------
    "cmd.suite.help": "escribe la suite MCP recomendada en los clientes de IA",
    "cmd.suite.examples": (
        "isc-ai suite                           qué cambiaría, sin escribir nada\n"
        "isc-ai suite --apply\n"
        "isc-ai suite --apply --client vscode   solo un cliente\n"
        "isc-ai suite --apply --server perforce-mcp-server\n"
        "\n"
        "Normaliza la forma, nunca inventa valores: un host o un nombre de workspace que ya\n"
        "existe se preserva, y un valor presente en un cliente se propaga a otro en vez de\n"
        "adivinarse. Los secretos nunca se escriben literales: cada cliente recibe una\n"
        "referencia en su propia sintaxis, y VS Code recibe una entrada nativa en `inputs`\n"
        "con password:true para que el editor lo guarde. Las entradas fuera de la suite no\n"
        "se tocan. Todos los archivos se escriben en una transacción, o ninguno."
    ),
    "flag.suite.apply": "escribe los cambios",
    "flag.suite.check": "imprime el plan sin escribir (por defecto)",
    "flag.suite.client": "limita a un cliente: opencode, vscode, claude",
    "flag.suite.server": "limita a un servidor de la suite",
    "flag.suite.prune_claude": "repara además el archivo global de Claude: borra overrides muertos por "
                              "directorio, renombra entradas con alias y reemplaza secretos literales",

    # -- docs ------------------------------------------------------------
    "cmd.docs.help": "refresca los bloques gestionados de la documentación del despliegue",
    "cmd.docs.examples": (
        "isc-ai docs                            qué cambiaría, sin escribir nada\n"
        "isc-ai docs --apply\n"
        "\n"
        "Dos bloques, delimitados por marcadores <!-- isc-ai:NOMBRE -->:\n"
        "  topology  en WORKFLOW.md, derivado de .iris-mcp/config.json para que los hosts,\n"
        "            nodos HA, alias de VIP, excepciones de escritura, catálogo de packages\n"
        "            y formato de CCR documentados no se separen de la política vigente\n"
        "  language  en AGENTS.md, el idioma en que deben responder los agentes\n"
        "\n"
        "Nada fuera de los marcadores se lee ni se reescribe: los anexos y los casos\n"
        "documentados son tuyos. A un documento sin marcadores se le agrega el bloque al\n"
        "final, y eso se reporta.\n"
        "\n"
        "El idioma de respuesta no es el de esta CLI (--lang): se cambia con\n"
        "`isc-ai config set agents.reply_language es-CL`."
    ),
    "flag.docs.apply": "escribe los cambios",
    "flag.docs.check": "imprime el plan sin escribir (por defecto)",

    # -- logi ------------------------------------------------------------
    "cmd.logi.help": "genera catálogos y reportes Logi, y la clase de despliegue, desde specs",
    "cmd.logi.examples": (
        "isc-ai logi --manifest jreports/manifest.json            escribe los archivos generados\n"
        "isc-ai logi --manifest jreports/manifest.json --check    sale con 1 si algo está desactualizado\n"
        "\n"
        "El manifest lista un catálogo por carpeta de área. Columnas y parámetros salen de la\n"
        "ROWSPEC de las fuentes ObjectScript que referencia; los layouts, de los specs JSON.\n"
        "Cada catálogo puede declarar una clase de despliegue: lleva los archivos como XData y\n"
        "su Deploy() los escribe, verifica su SHA-256 y otorga EXECUTE en cualquier ambiente."
    ),
    "flag.logi.manifest": "ruta del manifest.json que lista los catálogos",
    "flag.logi.check": "compara con los archivos en disco y no escribe nada",

    # -- agents ----------------------------------------------------------
    "cmd.agents.help": "muestra o escribe el modelo asignado a cada sub-agente SDD",
    "cmd.agents.examples": (
        "isc-ai agents                          la asignación por rol, sin escribir nada\n"
        "isc-ai agents --apply                  declara los sub-agentes en OpenCode\n"
        "isc-ai config set agents.models.sdd-apply amazon-bedrock/anthropic.claude-sonnet-5\n"
        "\n"
        "Los dos clientes leen la asignación distinto: Claude Code toma la línea `model:` del\n"
        "frontmatter empaquetado del agente, y OpenCode toma agent.<rol>.model de su propia\n"
        "configuración y quiere un id con proveedor, no un nombre de familia.\n"
        "\n"
        "Un id concreto nunca se inventa: la plantilla de roles declara una preferencia de\n"
        "familia, y convertirla en un id decide qué modelo corre tu trabajo y en qué cuenta.\n"
        "Sin configurar, el sub-agente igual se declara, el modelo queda al default del\n"
        "cliente y se reportan los candidatos disponibles acá. Los ids se validan contra\n"
        "`opencode models`, así que uno viejo es un error y no una falla en runtime."
    ),
    "flag.agents.apply": "escribe los cambios",
    "flag.agents.check": "imprime el plan sin escribir (por defecto)",
    "flag.agents.no_verify": "no le pregunta al cliente qué modelos hay disponibles",

    # -- secrets ---------------------------------------------------------
    "cmd.secrets.help": "guarda las credenciales que los archivos de configuración no deben contener",
    "cmd.secrets.examples": (
        "isc-ai secrets list                    qué claves existen y dónde, nunca un valor\n"
        "isc-ai secrets set JIRA_API_TOKEN      pregunta sin eco\n"
        "isc-ai secrets set P4CLIENT --scope project\n"
        "type token.txt | isc-ai secrets set JIRA_API_TOKEN\n"
        "isc-ai secrets unset JIRA_API_TOKEN\n"
        "\n"
        "Dos scopes: ~/.iris-mcp/secrets.json para lo que es tuyo (un Jira, una contraseña)\n"
        "y <proyecto>/.iris-mcp/secrets.json para lo que cambia por despliegue, que se agrega\n"
        "al .gitignore en la primera escritura.\n"
        "\n"
        "El valor nunca se toma de la línea de comandos: quedaría en el historial del shell,\n"
        "en la lista de procesos y en cualquier grabación de terminal. Se lee de un prompt o\n"
        "de stdin. Resolución al expandir una referencia {{env:VAR}}: primero el entorno del\n"
        "proceso, así un override del shell o de CI siempre gana, después el store del\n"
        "proyecto y al final el global."
    ),
    "flag.secrets.scope": "en qué store escribir: global (por defecto) o project",

    # -- mcp toggle ------------------------------------------------------
    "cmd.enable.help": "habilita un servidor de la suite en cada cliente que lo conoce",
    "cmd.disable.help": "deshabilita un servidor de la suite en cada cliente que lo conoce",
    "cmd.toggle.examples": (
        "isc-ai disable playwright              qué cambiaría, sin escribir nada\n"
        "isc-ai disable playwright --apply\n"
        "isc-ai enable playwright --apply --client opencode\n"
        "\n"
        "Los tres clientes expresan el \"apagado\" distinto y el comando usa el mecanismo\n"
        "nativo de cada uno: OpenCode pone enabled=false, Claude Code lista el nombre bajo\n"
        "projects.<ruta>.disabledMcpjsonServers, y VS Code no tiene ese campo, así que ahí\n"
        "deshabilitar quita la entrada y habilitar la vuelve a escribir desde el baseline.\n"
        "Se reporta qué mecanismo se usó, porque quitar una entrada no es lo mismo que\n"
        "cambiar una bandera."
    ),
    "flag.toggle.apply": "escribe los cambios",
    "flag.toggle.client": "limita a un cliente: opencode, vscode, claude",

    # -- clients ---------------------------------------------------------
    "cmd.clients.help": "reporta qué clientes de IA están instalados e instala el que falte",
    "cmd.clients.examples": (
        "isc-ai clients                         estado de cada cliente, sin ejecutar nada\n"
        "isc-ai clients --apply vscode          pregunta una vez y corre la receta\n"
        "isc-ai clients --apply --yes opencode  para un script, donde no hay dónde preguntar\n"
        "\n"
        "Es el único comando que cambia el sistema fuera de archivos de configuración, así que\n"
        "es el más explícito. La receta sale del baseline y su gestor de paquetes ya tiene que\n"
        "estar en el PATH. Un cliente instalado no se toca nunca, y el resultado se verifica\n"
        "preguntándole la versión al programa en vez de confiar en un código de salida.\n"
        "\n"
        "La confirmación es interactiva solo en una terminal. Este mismo binario sirve MCP por\n"
        "stdio, así que nunca pregunta en un pipe: sin terminal, pasá --yes."
    ),
    "flag.clients.apply": "corre la receta para los clientes que falten",
    "flag.clients.yes": "confirma sin preguntar, para uso no interactivo",

    # -- doctor ----------------------------------------------------------
    "cmd.doctor.help": "muestra el proyecto, los archivos y los servidores que resuelve este proceso",
    "cmd.doctor.examples": (
        "isc-ai doctor\n"
        "isc-ai doctor --project-dir C:\\work\\deployments\\site\n"
        "\n"
        "Empieza aquí cuando el cliente de IA ve servidores equivocados: project_source te\n"
        "dice si la política vino del cwd, de un ancestro, del entorno o de un flag.\n"
        "El JSON en sí está siempre en inglés, incluido el idioma que reporta."
    ),

    # -- setup -----------------------------------------------------------
    "cmd.setup.help": "planifica o aplica la instalación local del cliente de IA: entrada MCP, logo y harness SDD",
    "cmd.setup.examples": (
        "isc-ai setup                           plan de solo lectura (igual que --check)\n"
        "isc-ai setup --apply\n"
        "isc-ai setup --apply --claude-project C:\\work\\deployments\\site\n"
        "isc-ai setup --apply --no-logo --no-harness\n"
        "isc-ai setup --apply --with-opencode   instala OpenCode con npm si falta"
    ),
    "flag.setup.apply": "aplica el plan de configuración completo",
    "flag.setup.check": "imprime el plan de solo lectura (por defecto)",
    "flag.setup.claude_project": "proyecto existente donde se administra .mcp.json",
    "flag.setup.opencode_config": "ruta explícita a opencode.json u opencode.jsonc",
    "flag.setup.no_opencode": "no configura la entrada MCP de OpenCode",
    "flag.setup.no_logo": "no instala ni registra el logo de OpenCode",
    "flag.setup.no_harness": "no instala el harness genérico de Claude SDD",
    "flag.setup.adopt_harness": "sobrescribe los archivos del harness en conflicto y registra el bundle como línea base",
    "flag.setup.timeout": "timeout de MCP en segundos",
    "flag.setup.with_opencode": "instala OpenCode con npm si falta (requiere --apply)",

    # -- errores de forma de argumentos ----------------------------------
    "error.value.ccr": "debe ser un identificador de CCR (letras, dígitos, '_' o '-')",
    "error.value.sha256": "debe ser un SHA-256 de 64 caracteres en minúscula",
    "error.value.positive_int": "debe ser un entero positivo",

    # -- validación de comandos ------------------------------------------
    "error.subcommand.config": "se requiere un subcomando de config",
    "error.subcommand.servers": "se requiere un subcomando de servers",
    "error.subcommand.secrets": "se requiere un subcomando de secrets: list, set o unset",
    "error.subcommand.facility": "se requiere un subcomando de facility",
    "error.rules.id_or_expiring_within_required": "se requiere un id o --expiring-within",
    "error.rules.unknown_id": "no existe una excepción con id '{value}'",
    "error.rules.plan_not_found": "no hay un plan pendiente para la huella '{value}'",
    "error.rules.plan_drift": "isc-ai.rules.json cambió desde que se hizo este plan; replanifique antes de aplicar",
    "error.subcommand.rules": "se requiere un subcomando de rules",
    "error.upgrade_requires_sha": "--upgrade requiere --expected-current-sha256",
    "error.sha_requires_upgrade": "--expected-current-sha256 requiere --upgrade",
    "error.with_opencode_requires_apply": "--with-opencode requiere --apply",
    "error.unsupported.config": "Comando de config no soportado.",
    "error.unsupported.servers": "Comando de servers no soportado.",
    "error.unsupported.facility": "Comando de facility no soportado.",
    "error.unsupported.rules": "Comando de rules no soportado.",
    "error.unsupported.command": "Comando no soportado.",

    # -- prefijos --------------------------------------------------------
    "msg.prefix.note": "nota",
    "msg.prefix.warning": "advertencia",
    "msg.prefix.hint": "sugerencia",
    "msg.prefix.error": "error",
    "error.command_failed": "{command} falló: {detail}",

    # -- init ------------------------------------------------------------
    "msg.init.exists": "{path} ya existe; no se cambió nada (usa --force para regenerarlo desde la plantilla).",
    "msg.init.created": "Se creó {path} desde la plantilla. Edítalo con 'isc-ai config set' o con tu editor; "
                        "mantén los secretos como referencias {{env:VAR}}.",
    "msg.init.shadow": "existe una configuración de proyecto en el ancestro {ancestor}; este archivo nuevo "
                       "la tapa para todo lo que esté bajo {directory}.",
    "error.init.not_a_directory": "{directory} no es un directorio existente; crea primero la carpeta "
                                  "del despliegue.",

    # -- config ----------------------------------------------------------
    "error.project.missing": "No hay configuración de proyecto en {path}; ejecuta 'isc-ai init' primero.",
    "error.project.not_object": "{path} debe contener un objeto JSON.",
    "error.servers.not_object": "{path} debe contener un objeto 'servers'.",
    "error.servers.value_not_object": "servers debe ser un objeto indexado por id de servidor.",
    "error.secrets.literal": "No se permiten secretos literales en la configuración del proyecto ({paths}); "
                             "usa referencias {{env:VAR}} o 'add-server --user'.",
    "error.role.identifier": "--role debe ser un identificador simple (letras, dígitos, '_').",
    "error.path.not_set": "{path} no está definido.",
    "error.server.unknown": "el servidor '{server_id}' no está definido en {path}.",
    "msg.config.set": "{path} = {value}",
    "msg.config.set_secret": "{path} = <definido>",
    "msg.config.unset": "se eliminó {path}",
    "msg.server.written": "servidor '{server_id}' escrito en {path}",
    "msg.server.removed": "servidor '{server_id}' eliminado de {path}",

    # -- servers migrate -------------------------------------------------
    "error.legacy.missing": "No hay archivo heredado en {legacy}.",
    "error.legacy.target_exists": "{target} ya existe; usa --force para sobrescribirlo.",
    "error.legacy.shape": "{legacy} debe contener un objeto 'servers'.",
    "msg.servers.migrated": "Se copiaron {count} servidores a {target}. Borra {legacy} una vez verificado; "
                            "el archivo heredado se sigue leyendo como respaldo.",

    # -- doctor ----------------------------------------------------------
    "msg.doctor.no_config": "no se encontró configuración de proyecto; ejecuta 'isc-ai init' en el "
                            "directorio del despliegue.",
    "msg.doctor.home_policy": "la configuración de proyecto que gobierna está en tu directorio HOME; todo "
                              "despliegue sin su propio .iris-mcp/config.json la hereda. "
                              "Bórrala salvo que sea intencional.",
    "msg.doctor.ancestor": "política heredada del ancestro {project_dir}.",

    # -- facility install ------------------------------------------------
    "error.facility.failed": "Falló el inicializador: {detail}",
    "error.facility.unexpected": "Falló el inicializador: {name} inesperado; detalles omitidos. "
                                 "El resultado no está verificado; inspecciona el destino manualmente "
                                 "antes de cualquier reintento.",

    # -- idioma ----------------------------------------------------------
    "msg.language.rejected": "se ignoró el idioma no soportado {values}; se usa {language}. "
                             "Idiomas soportados: {supported}.",

    # -- asistente -------------------------------------------------------
    "cmd.wizard.help": "configuración guiada: pregunta qué necesitas y lo configura",
    "cmd.wizard.examples": (
        "isc-ai wizard\n"
        "isc-ai wizard --dry-run                   pregunta todo y para antes de escribir\n"
        "isc-ai wizard --only secrets              un solo paso\n"
        "isc-ai wizard --only harness --only docs  un par de pasos, en orden de dependencia\n"
        "\n"
        "Pregunta por todo el entorno agéntico: qué clientes hay instalados, el harness de\n"
        "instrucciones, las credenciales que declaran los servidores MCP, los servidores\n"
        "mismos, el modelo de cada fase SDD y los bloques de documentación gestionados.\n"
        "IRIS/TrakCare es un paso opcional, que solo se pregunta si este despliegue lo usa.\n"
        "\n"
        "No escribe nada hasta mostrar el plan completo y confirmarlo, y cada paso se puede\n"
        "rechazar. Necesita una terminal: no acepta valores por defecto desde un pipe."
    ),
    "flag.wizard.dry_run": "pregunta todo e imprime el plan, luego termina sin escribir",
    "flag.wizard.only": "ejecuta solo este paso (repetible): "
                        "clients, iris, harness, secrets, servers, phases, docs",
    "flag.wizard.yes": "omite la confirmación final (las preguntas de cada paso se siguen haciendo)",

    "error.wizard.no_tty": "el asistente necesita una terminal: stdin no es un tty. "
                           "Ejecútalo de forma interactiva, o usa los comandos individuales "
                           "(setup, secrets, suite, agents, docs) en un script.",
    "error.wizard.input_closed": "la entrada se cerró antes de terminar; no se escribió nada.",
    "error.wizard.unknown_step": "paso desconocido {name}; pasos válidos: {valid}",

    "msg.wizard.intro": "Configurando el entorno agéntico para {path}",
    "msg.wizard.discovered": "Estado actual: {errors} errores, {warnings} advertencias.",
    "msg.wizard.answer_yes_no": "    responde sí o no.",
    "msg.wizard.answer_required": "    hace falta una respuesta.",
    # El sufijo muestra qué respuesta elige Enter. Se traduce porque [Y/n] no es universal.
    "msg.wizard.yes_no_yes": "[S/n]",
    "msg.wizard.yes_no_no": "[s/N]",
    # Una palabra aceptada por línea. Se aceptan las de los dos idiomas en cualquiera,
    # porque alguien que escribe 'y' en un prompt en español quiere decir sí.
    "msg.wizard.words_yes": "s\nsi\nsí\ny\nyes",
    "msg.wizard.words_no": "n\nno",
    "msg.wizard.cancel_hint": "Presiona ESC o Ctrl+C en cualquier momento para cancelar sin escribir.",

    # -- migrate ---------------------------------------------------------
    "cmd.migrate.help": "mueve el directorio de estado de .iris-mcp a .isc-ai",
    "cmd.migrate.examples": (
        "isc-ai migrate                        muestra qué se movería\n"
        "isc-ai migrate --apply                lo mueve\n"
        "isc-ai migrate --apply --no-user      solo este proyecto, deja los archivos de usuario\n"
        "\n"
        "El directorio llevaba el nombre del servidor de IRIS; ahora lleva el de esta\n"
        "herramienta, porque lo que configura es todo el entorno agéntico. Los dos nombres se\n"
        "siguen leyendo, así que un despliegue sin migrar sigue funcionando: esto solo ordena.\n"
        "\n"
        "Nunca sobreescribe. Un destino que ya existe con contenido distinto se reporta como\n"
        "conflicto y no se mueve nada hasta que lo resuelvas. Los archivos se copian y se\n"
        "verifican antes de borrar el original."
    ),
    "flag.migrate.apply": "realiza la migración (por defecto: solo la reporta)",
    "flag.migrate.no_user": "migra solo el directorio del proyecto, no el de usuario",
    "msg.wizard.secret_prompt": "valor de {name} (no se muestra): ",
    "msg.wizard.and_more": "... y {count} más",

    "msg.wizard.step_clients": "1. Clientes de IA",
    "msg.wizard.clients_found": "instalados: {names}",
    "msg.wizard.clients_missing": "no instalados: {names}",
    "msg.wizard.clients_none": "no hay ningún cliente soportado; instala uno con 'isc-ai clients'.",

    "msg.wizard.step_iris": "2. Política de IRIS/TrakCare (opcional)",
    "msg.wizard.iris_present": "ya está configurada: {path}",
    "msg.wizard.iris_question": "¿Este despliegue trabaja con IRIS/TrakCare?",
    "msg.wizard.iris_skipped": "omitido; los demás servidores no la necesitan.",
    "msg.wizard.iris_site": "código del sitio (corto, por ejemplo la sigla del cliente)",
    "msg.wizard.iris_ccr_prefix": "prefijo de CCR",
    "msg.wizard.iris_base_host": "host de la BASE canónica",
    "msg.wizard.iris_base_namespace": "namespace de BASE",
    "msg.wizard.iris_package_prefix": "prefijo de package que tienes permitido escribir",

    "msg.wizard.step_harness": "3. Harness de instrucciones",
    "msg.wizard.harness_state": "{files} archivos, bundle versión {version}",
    "msg.wizard.harness_current": "al día; no hay nada que instalar.",
    "msg.wizard.harness_pending": "pendiente: {summary}",
    "msg.wizard.harness_question": "¿Instalar el harness y las fases SDD?",

    "msg.wizard.step_secrets": "4. Credenciales",
    "msg.wizard.secrets_all_resolved": "todas las credenciales declaradas ya resuelven.",
    "msg.wizard.secrets_needed": "{name} la declara {server} y no resuelve.",
    "msg.wizard.secrets_question": "¿Guardar {name} ahora?",
    "msg.wizard.secrets_empty": "{name} quedó vacía; no se guardó.",

    "msg.wizard.step_servers": "5. Servidores MCP",
    "msg.wizard.servers_current": "todos los clientes ya coinciden con la suite recomendada.",
    "msg.wizard.servers_pending": "{count} cambios pendientes:",
    "msg.wizard.servers_question": "¿Escribir los servidores recomendados en cada cliente?",

    "msg.wizard.step_phases": "6. Fases SDD y sus modelos",
    "msg.wizard.phases_state": "{count} roles declarados",
    "msg.wizard.phases_current": "la asignación de modelos ya coincide.",
    "msg.wizard.phases_unavailable": "no se puede planificar las fases: {reason}",
    "msg.wizard.phases_question": "¿Escribir la asignación de modelos?",

    "msg.wizard.step_docs": "7. Bloques de documentación gestionados",
    "msg.wizard.docs_current": "todos los bloques gestionados están al día.",
    "msg.wizard.docs_pending": "{count} bloques por refrescar:",
    "msg.wizard.docs_question": "¿Refrescarlos desde la política?",

    "msg.wizard.plan_title": "Plan (todavía no se escribió nada):",
    "msg.wizard.plan_empty": "nada que hacer; el entorno ya coincide.",
    "msg.wizard.plan_declined": "rechazado o no aplicable: {names}",
    "msg.wizard.confirm": "¿Aplicar este plan?",
    "msg.wizard.cancelled": "cancelado; no se escribió nada.",
    "msg.wizard.dry_run": "simulación: se detiene antes de escribir.",
    "msg.wizard.results_title": "Listo:",
    "msg.wizard.verify_hint": "Ejecuta 'isc-ai audit' para confirmar el resultado.",

    "msg.wizard.action_iris": "crear la política de IRIS en {path}",
    "msg.wizard.action_harness": "instalar el harness ({summary})",
    "msg.wizard.action_secrets": "guardar credenciales: {names}",
    "msg.wizard.action_servers": "escribir {count} cambios de servidores en los clientes",
    "msg.wizard.action_phases": "escribir la asignación de modelos ({count} archivos)",
    "msg.wizard.action_docs": "refrescar {count} bloques de documentación",

    "msg.wizard.done_iris": "política de IRIS escrita: {path}",
    "msg.wizard.done_harness": "harness instalado: {summary}",
    "msg.wizard.done_secret": "{name} guardada",
    "msg.wizard.done_servers": "{count} cambios de servidores escritos",
    "msg.wizard.done_phases": "asignación de modelos escrita ({count} archivos)",
    "msg.wizard.done_docs": "{count} bloques de documentación refrescados",
}
