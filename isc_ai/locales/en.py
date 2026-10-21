"""English catalog for the isc-ai CLI surface (the reference language).

Keys are grouped by role:
  chrome.*  argparse furniture (labels and section titles)
  app.*     top-level description, examples and footer
  cmd.*     per-command summary (.help) and verbatim example block (.examples)
  flag.*    individual option help
  error.*   messages the CLI itself rejects or raises
  msg.*     confirmations, notes, warnings and hints
"""

MESSAGES = {
    # -- argparse chrome -------------------------------------------------
    "chrome.usage_prefix": "usage: ",
    "chrome.group.options": "options",
    "chrome.group.arguments": "arguments",
    "chrome.group.commands": "commands",
    "chrome.group.subcommands": "sub-commands",
    "chrome.examples_label": "examples:",
    "chrome.help": "show this help message and exit",
    "chrome.version": "show the program version and exit",
    "chrome.lang": "language of this CLI's help and messages: en, es "
                   "(default: en, or the ISC_AI_LANG environment variable)",

    # -- top level -------------------------------------------------------
    "app.description": (
        "Configure and audit the agentic development environment: the AI clients, the MCP\n"
        "servers they talk to, the instruction harness, the SDD phases and their models, and\n"
        "the credentials all of that needs.\n"
        "\n"
        "Start with `wizard`: it asks what you need and configures it. `audit` reports what is\n"
        "already there without writing anything. The rest are the individual verbs the wizard\n"
        "drives, for when you know exactly what you want to change.\n"
        "\n"
        "The MCP servers are launched by the clients, never by this CLI, and this CLI is never\n"
        "in the middle of that conversation. Commands that resolve a project accept\n"
        "--project-dir; otherwise the nearest ancestor holding .isc-ai/config.json wins, the\n"
        "same way git finds .git."
    ),
    "app.examples": (
        "isc-ai wizard                          guided setup: it asks, then configures\n"
        "isc-ai audit                           what is configured right now, writes nothing\n"
        "isc-ai suite --apply                   write the recommended MCP servers to every client\n"
        "isc-ai secrets set JIRA_API_TOKEN      store a credential without echoing it\n"
        "isc-ai setup --apply                   install the instruction harness and the SDD phases\n"
        "isc-ai agents                          which model runs each SDD sub-agent\n"
        "isc-ai doctor                          which project, files and servers resolve here"
    ),
    "app.footer": "Run 'isc-ai <command> -h' for the flags and examples of a single command.",

    # -- shared flags ----------------------------------------------------
    "flag.project_dir": "project directory (default: nearest ancestor of cwd with .isc-ai/, "
                        "or ISC_AI_PROJECT_DIR)",

    # -- mcp -------------------------------------------------------------
    "cmd.mcp.help": "run the MCP server over stdio",
    "cmd.mcp.examples": (
        "isc-ai mcp\n"
        "isc-ai mcp --project-dir C:\\work\\deployments\\site\n"
        "\n"
        "The AI client launches this and speaks the MCP protocol over stdout, so run it\n"
        "by hand only to check that the server starts and lists the expected servers.\n"
        "Its tool descriptions and responses are always English: they are a contract read\n"
        "by the AI client, not terminal output, so --lang does not affect them."
    ),

    # -- init ------------------------------------------------------------
    "cmd.init.help": "create .isc-ai/config.json from the packaged template (idempotent)",
    "cmd.init.examples": (
        "isc-ai init                            create .isc-ai/config.json in the cwd\n"
        "isc-ai init C:\\work\\deployments\\site\n"
        "isc-ai init --no-servers               policy only, no example servers\n"
        "isc-ai init --force                    regenerate from the template\n"
        "isc-ai init --from ./my-policy.json"
    ),
    "flag.init.directory": "project directory (default: cwd)",
    "flag.init.template": "alternative template file",
    "flag.init.force": "overwrite an existing project configuration",
    "flag.init.no_servers": "omit the example servers section",

    # -- config ----------------------------------------------------------
    "cmd.config.help": "inspect and edit configuration files",
    "cmd.config.examples": (
        "isc-ai config show\n"
        "isc-ai config show --servers\n"
        "isc-ai config set canonical_base.namespace BASE-SITE\n"
        "isc-ai config add-server my-base --host base.example.invalid --namespace BASE-SITE --role BASE\n"
        "isc-ai config validate"
    ),
    "cmd.config.show.help": "print the effective configuration (secrets redacted)",
    "cmd.config.show.examples": (
        "isc-ai config show                     the project policy\n"
        "isc-ai config show --servers           servers merged from both layers\n"
        "isc-ai config show --servers --reveal  literal secrets, for debugging only"
    ),
    "flag.show.reveal": "print literal secrets instead of redacting",
    "flag.show.servers": "print merged servers instead of the project policy",
    "cmd.config.set.help": "set a project setting by dotted path, e.g. canonical_base.host",
    "cmd.config.set.examples": (
        "isc-ai config set site.name SITE\n"
        "isc-ai config set canonical_base.host base.example.invalid\n"
        "isc-ai config set site.ccr_id.prefix SITE\n"
        "isc-ai config set packages.BASE-SITE '[{{\"prefix\": \"Custom.Site\"}}]'"
    ),
    "flag.set.value": "JSON when parseable (numbers, booleans, lists, objects), otherwise a string",
    "cmd.config.unset.help": "remove a project setting by dotted path",
    "cmd.config.unset.examples": (
        "isc-ai config unset mutation\n"
        "isc-ai config unset packages.BASE-SITE"
    ),
    "cmd.config.add_server.help": "add or replace a server entry",
    "cmd.config.add_server.examples": (
        "isc-ai config add-server my-base --host base.example.invalid --path-prefix /base01 "
        "--namespace BASE-SITE --role BASE\n"
        "isc-ai config add-server my-live --host live.example.invalid --namespace LIVE-SITE "
        "--role LIVE --production\n"
        "isc-ai config add-server my-base --user --host base.example.invalid --username me "
        "--password 'literal'\n"
        "\n"
        "The project file is committable, so it only accepts {{env:VAR}} references for\n"
        "credentials; --user writes literal secrets to ~/.isc-ai/servers.json instead."
    ),
    "flag.add.role": "env_role, e.g. BASE, UAT, LIVE",
    "flag.add.production": "mark as is_production=true",
    "flag.add.username": "literal or {{env:VAR}} reference",
    "flag.add.password": "literal or {{env:VAR}} reference",
    "flag.add.upload_token": "literal or {{env:VAR}} reference for the CCR upload token",
    "flag.add.ccr_id": "default CCR identifier for this server",
    "flag.add.user": "write to ~/.isc-ai/servers.json instead of the project file",
    "cmd.config.remove_server.help": "remove a server entry",
    "cmd.config.remove_server.examples": (
        "isc-ai config remove-server my-base\n"
        "isc-ai config remove-server my-base --user"
    ),
    "flag.remove.user": "remove from ~/.isc-ai/servers.json instead of the project file",
    "cmd.config.validate.help": "load and validate the effective configuration",
    "cmd.config.from_ccr.help": "derive the IRIS connections of a site from its CCR portal Systems",
    "cmd.config.from_ccr.examples": (
        "isc-ai config from-ccr                 asks for the site (known sites are listed)\n"
        "isc-ai config from-ccr SITE            plan only: what would be written\n"
        "isc-ai config from-ccr SITE --apply    write the servers and canonical_base\n"
        "isc-ai config from-ccr SITE --system T2026 --save-json site-ccr.json\n"
        "isc-ai config from-ccr SITE --from-json site-ccr.json --apply   offline\n"
        "\n"
        "  The CCR portal already holds every environment of a site: namespace and the\n"
        "  Management Portal link, which gives host, port and web path prefix. The data is\n"
        "  read through the ccr-portal MCP server, so its SSO session is reused. Credentials\n"
        "  are written as {{env:SITE_ENV_USER}} / {{env:SITE_ENV_PASSWORD}} references."
    ),
    "flag.ccr.site": "site code, e.g. the CCR prefix; asked when omitted",
    "flag.ccr.system": "CCR System code of the site (repeatable); default: the known ones",
    "flag.ccr.credential_prefix": "prefix of the credential variables (default: the site code)",
    "flag.ccr.host_alias": "replace a host the portal gives (often an IP) by the DNS name its certificate covers (repeatable)",
    "flag.ccr.endpoint": "use this address for one planned server, e.g. siteuat=http://10.0.0.5/uat02 when the portal link is a load balancer without the IRIS REST API (repeatable)",
    "flag.ccr.from_json": "use System records saved with --save-json instead of the portal",
    "flag.ccr.save_json": "also save the System records read from the portal",
    "flag.ccr.apply": "write the plan into the project configuration",
    "cmd.config.validate.examples": (
        "isc-ai config validate\n"
        "isc-ai config validate --project-dir C:\\work\\deployments\\site\n"
        "\n"
        "Prints a JSON summary and exits 1 when something is wrong: unresolved secrets,\n"
        "malformed entries or a canonical_base.server_id without a server entry."
    ),

    # -- servers ---------------------------------------------------------
    "cmd.servers.help": "user-level server connections",
    "cmd.servers.examples": (
        "isc-ai servers migrate\n"
        "isc-ai servers migrate --force"
    ),
    "cmd.servers.migrate.help": "copy legacy ~/.iris_mcp_servers.json into ~/.isc-ai/servers.json",
    "cmd.servers.migrate.examples": (
        "isc-ai servers migrate\n"
        "isc-ai servers migrate --force        overwrite an existing ~/.isc-ai/servers.json"
    ),
    "flag.migrate.force": "overwrite the destination file",

    # -- rules -------------------------------------------------------------
    "cmd.rules.help": "explicit package grants and capability exceptions (isc-ai.rules.json)",
    "cmd.rules.examples": (
        "isc-ai rules list\n"
        "isc-ai rules show base-direct-node\n"
        "isc-ai rules check code_write --namespace UAT-SITE --host 10.0.0.1 --role UAT\n"
        "isc-ai rules add --id ticket-123 --label \"UAT hotfix\" --ticket TCK-1 --expires 2026-12-31 "
        "--capability code_write\n"
        "isc-ai rules migrate --apply"
    ),
    "cmd.rules.list.help": "list every exception with its days-to-expiry",
    "cmd.rules.list.examples": "isc-ai rules list",
    "cmd.rules.show.help": "print one exception in full",
    "cmd.rules.show.examples": "isc-ai rules show base-direct-node",
    "cmd.rules.check.help": "evaluate one capability against a namespace/host/role/server/path-prefix",
    "cmd.rules.check.examples": "isc-ai rules check code_write --namespace UAT-SITE --host 10.0.0.1 --role UAT",
    "cmd.rules.validate.help": "report expired exceptions and package grants",
    "cmd.rules.validate.examples": "isc-ai rules validate",
    "cmd.rules.add.help": "add a new exception (plan by default, --apply to write)",
    "cmd.rules.add.examples": (
        "isc-ai rules add --id ticket-123 --label \"UAT hotfix\" --ticket TCK-1 --expires 2026-12-31 "
        "--capability code_write --roles UAT --namespaces INT --path-prefixes /uat01\n"
        "isc-ai rules add --id ticket-123 ... --apply"
    ),
    "cmd.rules.renew.help": "extend or shorten an exception's expiry (plan by default, --apply to write)",
    "cmd.rules.renew.examples": (
        "isc-ai rules renew base-direct-node --expires 2027-01-01 --apply\n"
        "isc-ai rules renew --expiring-within 7 --expires 2027-01-01 --apply"
    ),
    "cmd.rules.remove.help": "remove an exception (plan by default, --apply to write)",
    "cmd.rules.remove.examples": "isc-ai rules remove base-direct-node --apply",
    "cmd.rules.apply.help": "apply a pending plan by its fingerprint (from a plan-only run or the iris_rules MCP tool)",
    "cmd.rules.apply.examples": "isc-ai rules apply 3f9a1c2b7e4d5601",
    "cmd.rules.migrate.help": "move legacy config.json/servers.json keys into isc-ai.rules.json",
    "cmd.rules.migrate.examples": (
        "isc-ai rules migrate\n"
        "isc-ai rules migrate --apply"
    ),
    "flag.rules.id": "exception id",
    "flag.rules.label": "human-readable label",
    "flag.rules.ticket": "ticket reference",
    "flag.rules.expires": "expiry date, YYYY-MM-DD",
    "flag.rules.capability": "code_write, ensemble_control, runner, read or pre_go_live",
    "flag.rules.roles": "comma-separated roles",
    "flag.rules.hosts": "comma-separated hosts",
    "flag.rules.namespaces": "comma-separated namespaces",
    "flag.rules.servers": "comma-separated server ids",
    "flag.rules.path_prefixes": "comma-separated path prefixes",
    "flag.rules.active_node_evidence_namespace": "namespace used to prove the node is active",
    "flag.rules.runner_namespace": "runner capability only: the runner's destination namespace",
    "flag.rules.apply": "write the change (default: only plan it)",
    "flag.rules.expiring_within": "renew every exception expiring within this many days, instead of one by id",
    "flag.rules.check_namespace": "namespace to check",
    "flag.rules.check_host": "host to check",
    "flag.rules.check_role": "role to check",
    "flag.rules.check_server": "server id to check",
    "flag.rules.check_path_prefix": "path prefix to check",
    "msg.rules.list.none": "no exceptions are configured in isc-ai.rules.json",
    "msg.rules.list.entry": "{name}: {detail} -- expires {value} ({count} day(s) left)",
    "msg.rules.list.entry_expired": "{name}: {detail} -- EXPIRED on {value}",
    "msg.rules.list.expiring_soon": "  WARNING: expires within 7 days",
    "msg.rules.check.allowed": "allowed by: {names}",
    "msg.rules.check.denied": "denied: no exception matches capability '{value}'",
    "msg.rules.validate.ok": "no issues found",
    "msg.rules.plan": "plan: {detail} (fingerprint {value}; run 'isc-ai rules apply {value}' or repeat with --apply)",
    "msg.rules.applied": "applied: {detail}",
    "msg.rules.apply.ok": "applied pending plan {value}",

    # -- facility --------------------------------------------------------
    "cmd.facility.help": "MCP.CCRMutation facility for the configured site",
    "cmd.facility.examples": (
        "isc-ai facility render\n"
        "isc-ai facility install --server my-base --namespace BASE-SITE --ccr SITE0757"
    ),
    "cmd.facility.render.help": "render the facility for this site and print its SHA-256",
    "cmd.facility.render.examples": (
        "isc-ai facility render                                  class, sha256 and size, no connection\n"
        "isc-ai facility render --output ./MCP.CCRMutation.cls"
    ),
    "flag.render.output": "write the rendered class to this path",
    "cmd.facility.install.help": "conditionally install/upgrade the facility on the canonical BASE",
    "cmd.facility.install.examples": (
        "isc-ai facility install --server my-base --namespace BASE-SITE --ccr SITE0757\n"
        "isc-ai facility install --server my-base --namespace BASE-SITE --ccr SITE0757 "
        "--upgrade --expected-current-sha256 <64-hex>\n"
        "\n"
        "Exit codes: 0 INSTALLED/UNCHANGED/UPGRADED, 2 DIFFERENT (the target does not\n"
        "match the expected SHA-256), 1 anything else. --upgrade and\n"
        "--expected-current-sha256 are mandatory together."
    ),
    "flag.install.ccr": "CCR identifier in the configured site format (site.ccr_id)",

    # -- audit -----------------------------------------------------------
    "cmd.audit.help": "audit the AI clients, the recommended MCP suite, the harness and the model roles",
    "cmd.audit.examples": (
        "isc-ai audit                           everything, as a JSON report\n"
        "isc-ai audit --errors-only             only what is broken\n"
        "isc-ai audit --project-dir C:\\work\\deployments\\site\n"
        "\n"
        "Read-only: it never writes, never starts a server and never opens a connection.\n"
        "Exit codes: 0 clean, 1 at least one error, 2 only warnings or notes.\n"
        "Finding codes are stable and always English, so they can be grepped and asserted:\n"
        "CLIENT_MISSING, CONFIG_AMBIGUOUS, SERVER_MISSING, SERVER_ALIAS, SERVER_DRIFT,\n"
        "SERVER_DISABLED, SERVER_DUPLICATE, SERVER_EXTRA, SECRET_LEAK, SETTING_MISSING,\n"
        "SETTING_UNKNOWN, COMMAND_NOT_FOUND, PATH_NOT_FOUND, HARNESS_FROZEN,\n"
        "HARNESS_INCOMPLETE, HARNESS_OBSOLETE, MODEL_ROLES_DECLARATIVE, MODEL_ROLE_UNSET,\n"
        "MODEL_ROLES_CLIENT_GAP, DOC_MISSING, DOC_UNMANAGED."
    ),
    "flag.audit.errors_only": "print only findings with severity error",
    "flag.audit.suite": "alternative suite baseline file",

    # -- suite -----------------------------------------------------------
    "cmd.suite.help": "write the recommended MCP suite into the AI clients",
    "cmd.suite.examples": (
        "isc-ai suite                           what would change, nothing written\n"
        "isc-ai suite --apply\n"
        "isc-ai suite --apply --client vscode   one client only\n"
        "isc-ai suite --apply --server perforce-mcp-server\n"
        "\n"
        "Normalizes shape, never invents values: a hostname or workspace name that already\n"
        "exists is preserved, and a value present in one client is propagated to another\n"
        "instead of guessed. Secrets are never written as literals: each client gets a\n"
        "reference in its own syntax, and VS Code gets a native `inputs` entry with\n"
        "password:true so the editor stores it. Entries outside the suite are left alone.\n"
        "All files are written in one transaction, or none is."
    ),
    "flag.suite.apply": "write the changes",
    "flag.suite.check": "print the plan without writing (default)",
    "flag.suite.client": "limit to one client: opencode, vscode, claude",
    "flag.suite.server": "limit to one server of the suite",
    "flag.suite.prune_claude": "also repair the Claude global file: drop dead per-directory overrides, "
                              "rename aliased entries and replace literal secrets",

    # -- docs ------------------------------------------------------------
    "cmd.docs.help": "refresh the managed blocks of the deployment documentation",
    "cmd.docs.examples": (
        "isc-ai docs                            what would change, nothing written\n"
        "isc-ai docs --apply\n"
        "\n"
        "Two blocks, delimited by <!-- isc-ai:NAME --> markers:\n"
        "  topology  in WORKFLOW.md, derived from .isc-ai/config.json so the documented\n"
        "            hosts, HA nodes, VIP aliases, write exceptions, package catalog and\n"
        "            CCR format cannot drift away from the policy that is enforced\n"
        "  language  in AGENTS.md, the language the agents must reply in\n"
        "\n"
        "Nothing outside the markers is read for meaning or rewritten: annexes and\n"
        "documented cases are yours. A document without markers gets the block appended at\n"
        "the end, and that is reported.\n"
        "\n"
        "The reply language is not this CLI's language (--lang): change it with\n"
        "`isc-ai config set agents.reply_language es-CL`."
    ),
    "flag.docs.apply": "write the changes",
    "flag.docs.check": "print the plan without writing (default)",

    # -- logi ------------------------------------------------------------
    "cmd.logi.help": "generate Logi report catalogs, reports and the deployer class from specs",
    "cmd.logi.examples": (
        "isc-ai logi --manifest jreports/manifest.json            write the generated files\n"
        "isc-ai logi --manifest jreports/manifest.json --check    exit 1 if anything is out of date\n"
        "\n"
        "The manifest lists one catalog per area folder. Columns and parameters come from the\n"
        "ROWSPEC of the ObjectScript sources it points to; layouts come from the JSON specs.\n"
        "Each catalog may name a deployer class: it carries the files as XData, and its\n"
        "Deploy() writes them, checks their SHA-256, converts them to binary .cls/.cat (Logi\n"
        "stamps the XML with its evaluation watermark) and grants EXECUTE in any environment."
    ),
    "flag.logi.manifest": "path of the manifest.json that lists the catalogs",
    "flag.logi.check": "compare with the files on disk and write nothing",

    # -- agents ----------------------------------------------------------
    "cmd.agents.help": "show or write the model assigned to each SDD sub-agent",
    "cmd.agents.examples": (
        "isc-ai agents                          the assignment per role, nothing written\n"
        "isc-ai agents --apply                  declare the sub-agents in OpenCode\n"
        "isc-ai config set agents.models.sdd-apply amazon-bedrock/anthropic.claude-sonnet-5\n"
        "\n"
        "The two clients read the assignment differently: Claude Code takes the `model:`\n"
        "line from the packaged agent frontmatter, OpenCode takes agent.<role>.model from\n"
        "its own configuration and wants a provider-qualified id, not a family name.\n"
        "\n"
        "A concrete id is never invented: the role template states a family preference, and\n"
        "turning that into an id decides which model runs your work and on whose account.\n"
        "Without configuration the sub-agent is still declared, the model is left to the\n"
        "client default, and the candidates available here are reported. Ids are validated\n"
        "against `opencode models`, so a stale one is an error instead of a runtime failure."
    ),
    "flag.agents.apply": "write the changes",
    "flag.agents.check": "print the plan without writing (default)",
    "flag.agents.no_verify": "do not ask the client which models are available",

    # -- secrets ---------------------------------------------------------
    "cmd.secrets.help": "store the credentials that configuration files must not contain",
    "cmd.secrets.examples": (
        "isc-ai secrets list                    which keys exist and where, never a value\n"
        "isc-ai secrets set JIRA_API_TOKEN      prompts without echo\n"
        "isc-ai secrets set P4CLIENT --scope project\n"
        "type token.txt | isc-ai secrets set JIRA_API_TOKEN\n"
        "isc-ai secrets unset JIRA_API_TOKEN\n"
        "\n"
        "Two scopes: ~/.isc-ai/secrets.json for what is yours (one Jira, one password)\n"
        "and <project>/.isc-ai/secrets.json for what differs per deployment, which is\n"
        "added to .gitignore on first write.\n"
        "\n"
        "A value is never taken from the command line: it would land in shell history, in\n"
        "the process list and in any terminal recording. It is read from a prompt or stdin.\n"
        "Resolution when a {{env:VAR}} reference is expanded: process environment first, so a\n"
        "shell or CI override always wins, then the project store, then the global one."
    ),
    "flag.secrets.scope": "which store to write: global (default) or project",

    # -- mcp toggle ------------------------------------------------------
    "cmd.enable.help": "enable a suite server in every client that knows it",
    "cmd.disable.help": "disable a suite server in every client that knows it",
    "cmd.toggle.examples": (
        "isc-ai disable playwright              what would change, nothing written\n"
        "isc-ai disable playwright --apply\n"
        "isc-ai enable playwright --apply --client opencode\n"
        "\n"
        "The three clients express 'off' differently and the command uses each one's own\n"
        "mechanism: OpenCode sets enabled=false, Claude Code lists the name under\n"
        "projects.<path>.disabledMcpjsonServers, and VS Code has no such field at all, so\n"
        "there disabling removes the entry and enabling writes it back from the baseline.\n"
        "Which mechanism was used is reported, because removing an entry is not the same as\n"
        "flipping a flag."
    ),
    "flag.toggle.apply": "write the changes",
    "flag.toggle.client": "limit to one client: opencode, vscode, claude",

    # -- clients ---------------------------------------------------------
    "cmd.clients.help": "report which AI clients are installed and install a missing one",
    "cmd.clients.examples": (
        "isc-ai clients                         state of every client, nothing executed\n"
        "isc-ai clients --apply vscode          asks once, then runs the recipe\n"
        "isc-ai clients --apply --yes opencode  for a script, where no prompt is possible\n"
        "\n"
        "This is the only command that changes the system outside configuration files, so it\n"
        "is the most explicit one. The recipe comes from the baseline and its package manager\n"
        "must already be on PATH. A client that is installed is never touched, and the result\n"
        "is verified by asking the program for its version instead of trusting an exit code.\n"
        "\n"
        "The confirmation is interactive only on a terminal. This same binary serves MCP over\n"
        "stdio, so it never prompts on a pipe: without a terminal, pass --yes."
    ),
    "flag.clients.apply": "run the recipe for the clients that are missing",
    "flag.clients.yes": "confirm without asking, for non-interactive use",

    # -- doctor ----------------------------------------------------------
    "cmd.doctor.help": "show the project, files and servers this process resolves",
    "cmd.doctor.examples": (
        "isc-ai doctor\n"
        "isc-ai doctor --project-dir C:\\work\\deployments\\site\n"
        "\n"
        "Start here when the AI client sees the wrong servers: project_source tells you\n"
        "whether the policy came from the cwd, an ancestor, the environment or a flag.\n"
        "The JSON itself is always English, including the language it reports."
    ),

    # -- setup -----------------------------------------------------------
    "cmd.setup.help": "plan or apply the local AI-client setup: MCP entry, logo and SDD harness",
    "cmd.setup.examples": (
        "isc-ai setup                           read-only plan (same as --check)\n"
        "isc-ai setup --apply\n"
        "isc-ai setup --apply --claude-project C:\\work\\deployments\\site\n"
        "isc-ai setup --apply --no-logo --no-harness\n"
        "isc-ai setup --apply --with-opencode   installs OpenCode with npm when missing"
    ),
    "flag.setup.apply": "apply the complete setup plan",
    "flag.setup.check": "print the read-only plan (default)",
    "flag.setup.claude_project": "existing project where .mcp.json is managed",
    "flag.setup.opencode_config": "explicit opencode.json or opencode.jsonc",
    "flag.setup.no_opencode": "do not configure the OpenCode MCP entry",
    "flag.setup.no_logo": "do not install/register the OpenCode logo",
    "flag.setup.no_harness": "do not install the generic Claude SDD harness",
    "flag.setup.adopt_harness": "overwrite harness files that conflict and record the bundle as the baseline",
    "flag.setup.timeout": "MCP timeout in seconds",
    "flag.setup.with_opencode": "install OpenCode with npm if missing (requires --apply)",

    # -- argument shape errors -------------------------------------------
    "error.value.ccr": "must be a CCR identifier (letters, digits, '_' or '-')",
    "error.value.sha256": "must be a lowercase 64-character SHA-256",
    "error.value.positive_int": "must be a positive integer",

    # -- command validation ----------------------------------------------
    "error.subcommand.config": "a config sub-command is required",
    "error.subcommand.servers": "a servers sub-command is required",
    "error.subcommand.secrets": "a secrets sub-command is required: list, set or unset",
    "error.subcommand.facility": "a facility sub-command is required",
    "error.rules.id_or_expiring_within_required": "either an id or --expiring-within is required",
    "error.rules.unknown_id": "no exception with id '{value}'",
    "error.rules.plan_not_found": "no pending plan for fingerprint '{value}'",
    "error.rules.plan_drift": "isc-ai.rules.json changed since this plan was made; re-plan before applying",
    "error.subcommand.rules": "a rules sub-command is required",
    "error.upgrade_requires_sha": "--upgrade requires --expected-current-sha256",
    "error.sha_requires_upgrade": "--expected-current-sha256 requires --upgrade",
    "error.with_opencode_requires_apply": "--with-opencode requires --apply",
    "error.unsupported.config": "Unsupported config command.",
    "error.unsupported.servers": "Unsupported servers command.",
    "error.unsupported.facility": "Unsupported facility command.",
    "error.unsupported.rules": "Unsupported rules command.",
    "error.unsupported.command": "Unsupported command.",

    # -- prefixes --------------------------------------------------------
    "msg.prefix.note": "note",
    "msg.prefix.warning": "warning",
    "msg.prefix.hint": "hint",
    "msg.prefix.error": "error",
    "error.command_failed": "{command} failed: {detail}",

    # -- init ------------------------------------------------------------
    "msg.init.exists": "{path} already exists; nothing changed (use --force to regenerate from the template).",
    "msg.init.created": "Created {path} from template. Edit it with 'isc-ai config set' or your editor; "
                        "keep secrets as {{env:VAR}} references.",
    "msg.init.shadow": "an ancestor project configuration exists at {ancestor}; this new file shadows it "
                       "for everything under {directory}.",
    "error.init.not_a_directory": "{directory} is not an existing directory; create the deployment folder first.",

    # -- config ----------------------------------------------------------
    "error.project.missing": "No project configuration at {path}; run 'isc-ai init' first.",
    "error.project.not_object": "{path} must contain a JSON object.",
    "error.servers.not_object": "{path} must contain a 'servers' object.",
    "error.servers.value_not_object": "servers must be an object keyed by server id.",
    "error.secrets.literal": "Literal secrets are not allowed in the project configuration ({paths}); "
                             "use {{env:VAR}} references or 'add-server --user'.",
    "error.role.identifier": "--role must be a plain identifier (letters, digits, '_').",
    "error.path.not_set": "{path} is not set.",
    "error.server.unknown": "server '{server_id}' is not defined in {path}.",
    "msg.config.set": "{path} = {value}",
    "msg.config.set_secret": "{path} = <set>",
    "msg.config.unset": "removed {path}",
    "msg.server.written": "server '{server_id}' written to {path}",
    "msg.server.removed": "server '{server_id}' removed from {path}",
    "error.ccr.site_required": "say which site: isc-ai config from-ccr <SITE> (the list is only offered in a terminal).",
    "error.ccr.no_systems": "no CCR System code known for {name}: pass --system <CODE>.",
    "error.ccr.no_project": "no project configuration at {path}: run 'isc-ai init' first.",
    "error.ccr.login": "the CCR portal needs the corporate SSO login: finish it in the browser window and run this again.",
    "msg.ccr.menu": "Known sites (CCR Systems):",
    "msg.ccr.prompt": "Number or site code: ",
    "msg.ccr.login": "The CCR portal asks for the SSO login: a browser window opens.",
    "msg.ccr.login_wait": "Press Enter when the login is finished... ",
    "msg.ccr.applied": "{count} servers and canonical_base written to {path}",
    "msg.ccr.next": "next, store the credentials: {command}",
    "msg.ccr.plan": "plan only: run again with --apply to write it",

    # -- servers migrate -------------------------------------------------
    "error.legacy.missing": "No legacy file at {legacy}.",
    "error.legacy.target_exists": "{target} already exists; use --force to overwrite.",
    "error.legacy.shape": "{legacy} must contain a 'servers' object.",
    "msg.servers.migrated": "Copied {count} servers to {target}. Remove {legacy} once verified; "
                            "the legacy file is still read as a fallback.",

    # -- doctor ----------------------------------------------------------
    "msg.doctor.no_config": "no project configuration found; run 'isc-ai init' in the deployment directory.",
    "msg.doctor.home_policy": "the governing project configuration is in your HOME directory; every "
                              "deployment without its own .isc-ai/config.json inherits it. "
                              "Remove it unless intended.",
    "msg.doctor.ancestor": "policy inherited from ancestor {project_dir}.",

    # -- facility install ------------------------------------------------
    "error.facility.failed": "Initializer failed: {detail}",
    "error.facility.unexpected": "Initializer failed: unexpected {name}; details withheld. "
                                 "Outcome is unverified; inspect the target manually before any retry.",

    # -- language --------------------------------------------------------
    "msg.language.rejected": "ignored unsupported language {values}; using {language}. "
                             "Supported languages: {supported}.",

    # -- wizard ----------------------------------------------------------
    "cmd.wizard.help": "guided setup: it asks what you need, then configures it",
    "cmd.wizard.examples": (
        "isc-ai wizard\n"
        "isc-ai wizard --dry-run                   ask everything, then stop before writing\n"
        "isc-ai wizard --only secrets              a single step\n"
        "isc-ai wizard --only harness --only docs  a couple of steps, in dependency order\n"
        "\n"
        "Asks about the whole agentic environment: which clients are installed, the\n"
        "instruction harness, the credentials the MCP servers declare, the servers\n"
        "themselves, the model of each SDD phase, and the managed documentation blocks.\n"
        "IRIS/TrakCare is one optional step, asked only if this deployment needs it.\n"
        "\n"
        "Nothing is written until the full plan is shown and confirmed, and every step can\n"
        "be declined. It requires a terminal: it will not accept defaults from a pipe."
    ),
    "flag.wizard.dry_run": "ask everything and print the plan, then exit without writing",
    "flag.wizard.only": "run only this step (repeatable): "
                        "clients, iris, harness, secrets, servers, phases, docs",
    "flag.wizard.yes": "skip the final confirmation (the per-step questions are still asked)",

    "error.wizard.no_tty": "wizard needs a terminal: stdin is not a tty. "
                           "Run it interactively, or use the individual commands "
                           "(setup, secrets, suite, agents, docs) in a script.",
    "error.wizard.input_closed": "input closed before the wizard finished; nothing was written.",
    "error.wizard.unknown_step": "unknown step {name}; valid steps: {valid}",

    "msg.wizard.intro": "Configuring the agentic environment for {path}",
    "msg.wizard.discovered": "Current state: {errors} errors, {warnings} warnings.",
    "msg.wizard.answer_yes_no": "    please answer yes or no.",
    "msg.wizard.answer_required": "    an answer is required.",
    # The suffix shows which answer Enter picks. Localised because [Y/n] is not universal.
    "msg.wizard.yes_no_yes": "[Y/n]",
    "msg.wizard.yes_no_no": "[y/N]",
    # One accepted word per line. Both languages' words are accepted in either language,
    # because a person typing 's' on an English prompt means yes, not a typo.
    "msg.wizard.words_yes": "y\nyes\ns\nsi\nsí",
    "msg.wizard.words_no": "n\nno",
    "msg.wizard.cancel_hint": "Press ESC or Ctrl+C at any point to cancel without writing.",

    # -- migrate ---------------------------------------------------------
    "cmd.migrate.help": "move the state directory from .iris-mcp to .isc-ai",
    "cmd.migrate.examples": (
        "isc-ai migrate                        show what would move\n"
        "isc-ai migrate --apply                move it\n"
        "isc-ai migrate --apply --no-user      this project only, leave the user files\n"
        "\n"
        "The directory was named after the IRIS server; it now carries this tool's name,\n"
        "because what it configures is the whole agentic environment. Both names keep being\n"
        "read, so an un-migrated deployment goes on working: this only tidies it up.\n"
        "\n"
        "It never overwrites. A destination that already exists with different content is\n"
        "reported as a conflict and nothing is moved until you resolve it. Files are copied\n"
        "and verified before the original is removed."
    ),
    "flag.migrate.apply": "perform the migration (default: only report it)",
    "flag.migrate.no_user": "migrate the project directory only, not the user-level one",
    "msg.wizard.secret_prompt": "value for {name} (not echoed): ",
    "msg.wizard.and_more": "... and {count} more",

    "msg.wizard.step_clients": "1. AI clients",
    "msg.wizard.clients_found": "installed: {names}",
    "msg.wizard.clients_missing": "not installed: {names}",
    "msg.wizard.clients_none": "no supported client found; install one with 'isc-ai clients'.",

    "msg.wizard.step_iris": "2. IRIS/TrakCare policy (optional)",
    "msg.wizard.iris_present": "already configured: {path}",
    "msg.wizard.iris_question": "Does this deployment work with IRIS/TrakCare?",
    "msg.wizard.iris_skipped": "skipped; the other servers do not need it.",
    "msg.wizard.iris_site": "site code (short, e.g. the client abbreviation)",
    "msg.wizard.iris_ccr_prefix": "CCR prefix",
    "msg.wizard.iris_base_host": "canonical BASE host",
    "msg.wizard.iris_base_namespace": "BASE namespace",
    "msg.wizard.iris_package_prefix": "package prefix you are allowed to write",

    "msg.wizard.step_harness": "3. Instruction harness",
    "msg.wizard.harness_state": "{files} files, bundle version {version}",
    "msg.wizard.harness_current": "up to date; nothing to install.",
    "msg.wizard.harness_pending": "pending: {summary}",
    "msg.wizard.harness_question": "Install the harness and the SDD phases?",

    "msg.wizard.step_secrets": "4. Credentials",
    "msg.wizard.secrets_all_resolved": "every declared credential already resolves.",
    "msg.wizard.secrets_needed": "{name} is declared by {server} and does not resolve.",
    "msg.wizard.secrets_question": "Store {name} now?",
    "msg.wizard.secrets_empty": "{name} left empty; not stored.",

    "msg.wizard.step_servers": "5. MCP servers",
    "msg.wizard.servers_current": "every client already matches the recommended suite.",
    "msg.wizard.servers_pending": "{count} changes pending:",
    "msg.wizard.servers_question": "Write the recommended servers to every client?",

    "msg.wizard.step_phases": "6. SDD phases and their models",
    "msg.wizard.phases_state": "{count} roles declared",
    "msg.wizard.phases_current": "model assignment already matches.",
    "msg.wizard.phases_unavailable": "cannot plan the phases: {reason}",
    "msg.wizard.phases_question": "Write the model assignment?",

    "msg.wizard.step_docs": "7. Managed documentation blocks",
    "msg.wizard.docs_current": "every managed block is current.",
    "msg.wizard.docs_pending": "{count} blocks to refresh:",
    "msg.wizard.docs_question": "Refresh them from the policy?",

    "msg.wizard.plan_title": "Plan (nothing has been written yet):",
    "msg.wizard.plan_empty": "nothing to do; the environment already matches.",
    "msg.wizard.plan_declined": "declined or not applicable: {names}",
    "msg.wizard.confirm": "Apply this plan?",
    "msg.wizard.cancelled": "cancelled; nothing was written.",
    "msg.wizard.dry_run": "dry run: stopping before writing.",
    "msg.wizard.results_title": "Done:",
    "msg.wizard.verify_hint": "Run 'isc-ai audit' to confirm the result.",

    "msg.wizard.action_iris": "create the IRIS policy at {path}",
    "msg.wizard.action_harness": "install the harness ({summary})",
    "msg.wizard.action_secrets": "store credentials: {names}",
    "msg.wizard.action_servers": "write {count} server changes across the clients",
    "msg.wizard.action_phases": "write the model assignment ({count} files)",
    "msg.wizard.action_docs": "refresh {count} documentation blocks",

    "msg.wizard.done_iris": "IRIS policy written: {path}",
    "msg.wizard.done_harness": "harness installed: {summary}",
    "msg.wizard.done_secret": "stored {name}",
    "msg.wizard.done_servers": "{count} server changes written",
    "msg.wizard.done_phases": "model assignment written ({count} files)",
    "msg.wizard.done_docs": "{count} documentation blocks refreshed",
}
