"""Logi report generator (report-as-code for TrakCare).

Usage:
    isc-ai logi --manifest <project>/jreports/manifest.json [--check]

The manifest lists the catalogs (one per area folder, like product), their components and
reports. Data contracts are read from the ObjectScript sources; layouts from JSON specs.
Components are canonical: they are written into every area folder that uses them, because a
Logi subreport must belong to the same catalog as its main report.

Optional per-catalog manifest keys: "theme" (palette overrides), "sqlRole", "grantMethod"
(site grant helper Package.Class.Method; default plain SQL GRANT), "formulaSeed" and
"convertToBinary" (default true: Deploy() converts the files to binary .cls/.cat, because Logi
stamps the XML with its evaluation watermark).
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import xml.etree.ElementTree as ET

from .catalog import DEFAULT_FORMULA_SEED, CatalogModel, build_catalog
from .deployer import DEFAULT_SQL_ROLE, deployer_path, render_deployer, write_deployer
from .report import build_component, build_report, use_theme
from .rowspec import read_procedure


@dataclasses.dataclass
class Result:
    """written: files generated; problems: contract violations or out-of-date files."""
    written: list[pathlib.Path] = dataclasses.field(default_factory=list)
    problems: list[str] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _serialize(tree: ET.ElementTree) -> str:
    ET.indent(tree, space="  ")
    return ET.tostring(tree.getroot(), encoding="unicode") + "\n"


def _load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _component_blocks(spec: dict):
    for key in ("pageHeader", "pageFooter"):
        if key in spec:
            yield spec[key]
    for block in spec.get("blocks", []):
        if block["type"] == "component":
            yield block


def generate(manifest_path: pathlib.Path, check: bool = False) -> Result:
    """Generate every catalog of the manifest; with check=True only compare and write nothing."""
    manifest_path = pathlib.Path(manifest_path).resolve()
    manifest = _load(manifest_path)
    base = manifest_path.parent
    source_root = (base / manifest["sourceRoot"]).resolve()
    output_root = (base / manifest["outputRoot"]).resolve()
    component_specs = {name: _load(base / path) for name, path in manifest.get("components", {}).items()}
    result = Result()

    for catalog in manifest["catalogs"]:
        try:
            use_theme(catalog.get("theme"))
            report_specs = [_load(base / path) for path in catalog["reports"]]
            used = sorted({b["component"] for s in report_specs for b in _component_blocks(s)})
            specs = [component_specs[name] for name in used] + report_specs
            procedures, seen = [], set()
            for spec in specs:
                proc = read_procedure(source_root, spec["source"]["class"], spec["source"]["query"])
                if proc.sql_name not in seen:
                    procedures.append(proc)
                    seen.add(proc.sql_name)
                spec["dataset"] = proc.sql_name
            model = CatalogModel(procedures, catalog.get("parameters", []), catalog["database"])
            components = {name: {"name": name, "dataset": component_specs[name]["dataset"],
                                 "tabs": [{"id": "Report" if i == 0 else f"Report{i}", "display": t}
                                          for i, t in enumerate(component_specs[name]["tabs"])]}
                          for name in used}
            folder = output_root / catalog["folder"]
            outputs = {folder / f"{catalog['name']}.cat.xml": build_catalog(
                catalog["name"], catalog["database"], catalog["jdbcUrl"], model,
                catalog.get("formulaSeed", DEFAULT_FORMULA_SEED))}
            for name in used:
                outputs[folder / f"{name}.cls.xml"] = build_component(component_specs[name], model)
            for spec in report_specs:
                outputs[folder / f"{spec['name']}.cls.xml"] = build_report(spec, model, components)
        except (KeyError, ValueError, FileNotFoundError) as exc:
            result.problems.append(f"{catalog.get('name', '?')}: contract violation: {exc}")
            continue

        for path, tree in outputs.items():
            text = _serialize(tree)
            if check:
                if not path.exists() or path.read_text(encoding="utf-8") != text:
                    result.problems.append(f"out of date: {path}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
                result.written.append(path)

        if catalog.get("deployerClass"):
            args = (catalog["deployerClass"], catalog["folder"], catalog["name"], folder,
                    [f"{p.schema}.{p.sql_name}" for p in procedures],
                    catalog.get("sqlRole", DEFAULT_SQL_ROLE), catalog.get("grantMethod", ""),
                    catalog.get("convertToBinary", True))
            try:
                if check:
                    target = deployer_path(source_root, catalog["deployerClass"])
                    if not target.exists() or target.read_text(encoding="utf-8") != render_deployer(*args):
                        result.problems.append(f"out of date: {target}")
                else:
                    result.written.append(write_deployer(source_root, *args))
            except KeyError as exc:
                result.problems.append(f"{catalog['name']}: contract violation: {exc}")
    return result
