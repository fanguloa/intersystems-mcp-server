"""Managed documentation blocks: keep the derivable parts of a document derived.

WORKFLOW.md mixes three things with three different owners. The doctrine is generic and
belongs in the packaged skills. The site-specific tables -- canonical BASE, HA nodes,
VIP aliases, UAT write exceptions, package catalog, CCR format -- are a prose view of
data this tool already owns in `.isc-ai/config.json`, and that is exactly where drift
comes from: change the VIP in the policy and the table keeps saying the old one. The
annexes and documented cases are the author's and nobody else's.

So the site-specific part becomes a rendered block, delimited the same way the harness
delimits its own sections:

    <!-- isc-ai:topology -->   ... generated, replaced on every run ...
    <!-- /isc-ai:topology -->

Nothing outside the markers is ever read for meaning or rewritten. A document without
markers is reported, never patched blindly: where a block belongs in someone's document
is their call, so the tool appends it at the end and says so.
"""

import re
from pathlib import Path

from isc_ai import config as isc_config
from isc_ai.setup import ManagedWrite, _apply_transaction, _managed_target

MARKER = "isc-ai"
GENERATED_NOTE = ("Bloque generado por `isc-ai docs` desde `.isc-ai/config.json`. "
                  "No editar a mano: se reescribe en cada corrida. "
                  "Lo que esté fuera de los marcadores es tuyo y no se toca.")


def _open(name):
    return f"<!-- {MARKER}:{name} -->"


def _close(name):
    return f"<!-- /{MARKER}:{name} -->"


def _block_pattern(name):
    return re.compile(
        re.escape(_open(name)) + r".*?" + re.escape(_close(name)),
        re.S,
    )


def _table(headers, rows):
    if not rows:
        return "_(vacío)_\n"
    widths = [max(len(str(headers[index])), *(len(str(row[index])) for row in rows))
              for index in range(len(headers))]
    lines = ["| " + " | ".join(str(headers[i]).ljust(widths[i]) for i in range(len(headers))) + " |",
             "|" + "|".join("-" * (widths[i] + 2) for i in range(len(headers))) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))) + " |")
    return "\n".join(lines) + "\n"


def render_topology(runtime):
    """The site-specific sections of WORKFLOW.md, derived from the effective policy."""
    policy = runtime.policy
    servers = isc_config.redact(runtime.servers)
    out = [f"### Topología efectiva de `{policy.site_name}`", "",
           f"> {GENERATED_NOTE}", ""]

    base = policy.canonical_base
    out.append("**BASE canónica** (único origen de cambios y única ruta de mutación controlada)")
    out.append("")
    if base is None:
        out.append("_No hay `canonical_base` configurada: las rutas de escritura de código están bloqueadas._\n")
    else:
        out.append(_table(
            ["server_id", "host", "puerto", "path_prefix", "namespace"],
            [[base.server_id, base.host, base.port, base.path_prefix or "(ninguno)", base.namespace]],
        ))

    rows = []
    for server_id, entry in sorted(servers.items()):
        if not isinstance(entry, dict) or entry.get("_config_error"):
            continue
        host = entry.get("host", "")
        role = entry.get("env_role") or ""
        rows.append([
            server_id, host, entry.get("path_prefix") or "", entry.get("namespace", ""),
            role or "(sin rol)",
            "sí" if entry.get("is_production") else "no",
            "sí" if policy.is_direct_ha_node(host) else "no",
        ])
    out.append("**Targets configurados**")
    out.append("")
    out.append(_table(["server_id", "host", "path_prefix", "namespace", "rol",
                       "producción", "nodo HA directo"], rows))

    out.append("**Nodos HA directos** (nunca son canal de escritura: se escribe por el VIP)")
    out.append("")
    out.append(_table(["host"], [[host] for host in sorted(policy.direct_ha_nodes)]))

    if policy.alias_constraints:
        out.append("**Alias con ruta forzada** (el alias solo resuelve a este host/prefijo)")
        out.append("")
        # The policy stores each constraint as (host, path_prefix).
        out.append(_table(
            ["alias", "host", "path_prefix"],
            [[alias, host, path_prefix or "(cualquiera)"]
             for alias, (host, path_prefix) in sorted(policy.alias_constraints.items())],
        ))

    if policy.uat_write_exceptions:
        out.append("**Excepciones de escritura en UAT** (exigen ausencia de CCR confirmada en el proceso real)")
        out.append("")
        out.append(_table(
            ["namespace", "path_prefix", "hosts"],
            [[exception.namespace, ", ".join(exception.path_prefixes) or "(cualquiera)",
              ", ".join(sorted(exception.hosts)) if exception.hosts is not None else "(nodos HA directos)"]
             for exception in policy.uat_write_exceptions],
        ))

    out.append("**Catálogo de packages por namespace** (fuera de esto, la escritura se deniega)")
    out.append("")
    grants = []
    for namespace, entries in sorted(policy.packages.items()):
        for grant in entries:
            roles = ", ".join(sorted(grant.roles)) if getattr(grant, "roles", None) else "(todos)"
            grants.append([namespace, grant.prefix, roles])
    out.append(_table(["namespace", "prefijo", "roles"], grants))

    out.append("**Identidad del sitio**")
    out.append("")
    identity = [["formato de CCR", policy.ccr_description],
                ["prefijo de protocolo", policy.protocol_prefix]]
    if policy.mutation_configured:
        identity.append(["mutación controlada", policy.mutation_class_prefix])
        identity.append(["fuente local de mutación", str(policy.mutation_source_root)])
    out.append(_table(["clave", "valor"], identity))

    return "\n".join(out).rstrip() + "\n"


LANGUAGE_RESOURCE = Path(__file__).resolve().parent / "resources" / "reply-languages.json"


def render_language(runtime):
    """The reply-language instruction, rendered from the configured tag.

    It has to live in a file the agents always load, not in a skill: skills are pulled
    in by trigger, and how to talk to you is not a triggered decision. The rule is
    generic and packaged; the value is the project's.
    """
    import json

    catalog = json.loads(LANGUAGE_RESOURCE.read_text(encoding="utf-8"))
    tag = runtime.policy.agent_reply_language or catalog["default"]
    profile = catalog["profiles"].get(tag)
    if profile is None:
        available = ", ".join(sorted(catalog["profiles"]))
        raise isc_config.ConfigError(
            f"agents.reply_language is '{tag}', which has no profile. Available: {available}.")

    lines = [f"## Idioma de respuesta: {profile['name']} (`{tag}`)", "",
             f"> {GENERATED_NOTE}", ""]
    lines.extend(f"- {instruction}" for instruction in profile["instructions"])
    lines += [
        "",
        "Esta regla gobierna **solo la respuesta al usuario**. No gobierna los artefactos:",
        "código, identificadores, comentarios, strings de UI, mensajes de commit, "
        "documentación de producto y respuestas de servidores MCP van en inglés, porque son",
        "artefactos y contratos, no conversación. La documentación de este despliegue "
        "(WORKFLOW.md, AGENTS.md) va en español.",
        "",
        f"Para cambiarlo: `isc-ai config set agents.reply_language <tag>` "
        f"(disponibles: {', '.join(sorted(catalog['profiles']))}).",
    ]
    return "\n".join(lines).rstrip() + "\n"


BLOCKS = {
    "topology": {
        "document": "WORKFLOW.md",
        "render": render_topology,
        "summary": "site topology derived from .isc-ai/config.json",
    },
    "language": {
        "document": "AGENTS.md",
        "render": render_language,
        "summary": "reply language the agents must use",
    },
}


def _replace_or_append(text, name, body):
    """Replace the block if it exists, append it if it does not. Never touch the rest."""
    block = f"{_open(name)}\n{body}{_close(name)}\n"
    pattern = _block_pattern(name)
    if pattern.search(text):
        return pattern.sub(lambda _: block.rstrip("\n"), text, count=1), "updated"
    separator = "" if text.endswith("\n\n") or not text else ("\n" if text.endswith("\n") else "\n\n")
    return text + separator + block, "appended"


def plan(project_dir=None, home=None, environ=None):
    """What would change in the managed documentation blocks. Writes nothing."""
    project = Path(project_dir).resolve() if project_dir else isc_config.find_project_root(Path.cwd())
    report = {"mode": "check", "project_dir": str(project) if project else None,
              "blocks": [], "skipped": []}
    writes = []
    if project is None:
        report["skipped"].append("no project resolved; the topology block needs .isc-ai/config.json")
        return report, writes

    runtime = isc_config.load_runtime_config(project, environ=environ)
    for name, spec in BLOCKS.items():
        path = project / spec["document"]
        if not path.is_file():
            report["skipped"].append(f"{spec['document']} does not exist in {project}")
            continue
        current = path.read_text(encoding="utf-8")
        body = spec["render"](runtime)
        merged, action = _replace_or_append(current, name, body)
        if merged == current:
            report["blocks"].append({"block": name, "document": str(path), "action": "noop"})
            continue
        report["blocks"].append({"block": name, "document": str(path), "action": action,
                                 "summary": spec["summary"]})
        writes.append(ManagedWrite(path.parent, _managed_target(path.parent, path),
                                   merged.encode("utf-8")))
    return report, writes


def apply(project_dir=None, home=None, environ=None):
    report, writes = plan(project_dir=project_dir, home=home, environ=environ)
    report["mode"] = "apply"
    if writes:
        _apply_transaction(writes, external_install=False)
    report["written"] = [str(write.target) for write in writes]
    return report
