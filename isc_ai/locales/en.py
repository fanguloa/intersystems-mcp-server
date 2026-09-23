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
        "InterSystems IRIS/TrakCare MCP server, the per-project configuration it reads, and\n"
        "the local setup of the AI clients that consume it.\n"
        "\n"
        "Three jobs. `mcp` serves the IRIS tools over stdio. `init`, `config`, `servers`,\n"
        "`doctor` and `facility` manage the site policy of one deployment. `setup` registers\n"
        "the MCP in OpenCode and installs the packaged Claude SDD harness, its agents,\n"
        "commands and skills, plus the OpenCode logo.\n"
        "\n"
        "Only `mcp` and `facility install` talk to IRIS. Commands that resolve a project\n"
        "accept --project-dir; otherwise the nearest ancestor directory holding\n"
        ".iris-mcp/config.json wins, the same way git finds .git."
    ),
    "app.examples": (
        "isc-ai setup --check                   preview the local AI-client configuration\n"
        "isc-ai setup --apply                   register the MCP server, logo and Claude harness\n"
        "isc-ai init                            create .iris-mcp/config.json in this directory\n"
        "isc-ai doctor                          which project, files and servers resolve here\n"
        "isc-ai config show --servers           merged server list, secrets redacted\n"
        "isc-ai config validate                 exit 1 when the effective configuration is broken\n"
        "isc-ai facility render                 render MCP.CCRMutation and print its SHA-256"
    ),
    "app.footer": "Run 'isc-ai <command> -h' for the flags and examples of a single command.",

    # -- shared flags ----------------------------------------------------
    "flag.project_dir": "project directory (default: nearest ancestor of cwd with .iris-mcp/, "
                        "or IRIS_MCP_PROJECT_DIR)",

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
    "cmd.init.help": "create .iris-mcp/config.json from the packaged template (idempotent)",
    "cmd.init.examples": (
        "isc-ai init                            create .iris-mcp/config.json in the cwd\n"
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
        "credentials; --user writes literal secrets to ~/.iris-mcp/servers.json instead."
    ),
    "flag.add.role": "env_role, e.g. BASE, UAT, LIVE",
    "flag.add.production": "mark as is_production=true",
    "flag.add.username": "literal or {{env:VAR}} reference",
    "flag.add.password": "literal or {{env:VAR}} reference",
    "flag.add.upload_token": "literal or {{env:VAR}} reference for the CCR upload token",
    "flag.add.ccr_id": "default CCR identifier for this server",
    "flag.add.user": "write to ~/.iris-mcp/servers.json instead of the project file",
    "cmd.config.remove_server.help": "remove a server entry",
    "cmd.config.remove_server.examples": (
        "isc-ai config remove-server my-base\n"
        "isc-ai config remove-server my-base --user"
    ),
    "flag.remove.user": "remove from ~/.iris-mcp/servers.json instead of the project file",
    "cmd.config.validate.help": "load and validate the effective configuration",
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
    "cmd.servers.migrate.help": "copy legacy ~/.iris_mcp_servers.json into ~/.iris-mcp/servers.json",
    "cmd.servers.migrate.examples": (
        "isc-ai servers migrate\n"
        "isc-ai servers migrate --force        overwrite an existing ~/.iris-mcp/servers.json"
    ),
    "flag.migrate.force": "overwrite the destination file",

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
    "error.subcommand.facility": "a facility sub-command is required",
    "error.upgrade_requires_sha": "--upgrade requires --expected-current-sha256",
    "error.sha_requires_upgrade": "--expected-current-sha256 requires --upgrade",
    "error.with_opencode_requires_apply": "--with-opencode requires --apply",
    "error.unsupported.config": "Unsupported config command.",
    "error.unsupported.servers": "Unsupported servers command.",
    "error.unsupported.facility": "Unsupported facility command.",
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

    # -- servers migrate -------------------------------------------------
    "error.legacy.missing": "No legacy file at {legacy}.",
    "error.legacy.target_exists": "{target} already exists; use --force to overwrite.",
    "error.legacy.shape": "{legacy} must contain a 'servers' object.",
    "msg.servers.migrated": "Copied {count} servers to {target}. Remove {legacy} once verified; "
                            "the legacy file is still read as a fallback.",

    # -- doctor ----------------------------------------------------------
    "msg.doctor.no_config": "no project configuration found; run 'isc-ai init' in the deployment directory.",
    "msg.doctor.home_policy": "the governing project configuration is in your HOME directory; every "
                              "deployment without its own .iris-mcp/config.json inherits it. "
                              "Remove it unless intended.",
    "msg.doctor.ancestor": "policy inherited from ancestor {project_dir}.",

    # -- facility install ------------------------------------------------
    "error.facility.failed": "Initializer failed: {detail}",
    "error.facility.unexpected": "Initializer failed: unexpected {name}; details withheld. "
                                 "Outcome is unverified; inspect the target manually before any retry.",

    # -- language --------------------------------------------------------
    "msg.language.rejected": "ignored unsupported language {values}; using {language}. "
                             "Supported languages: {supported}.",
}
