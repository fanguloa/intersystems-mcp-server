"""Build a Logi catalog (.cat.xml) from stored procedure contracts.

Skeleton: product DummyCatalog (IRIS JDBC connection metadata) without procedures, parameters
or credentials. TrakCare overrides the JDBC URL and credentials at run time (Report.JReport.Server
sends jrs.jdbc_url / jrs.db_user / jrs.db_pswd from Report_JReport.Configuration), so the catalog
never stores secrets.
"""
from __future__ import annotations

import pathlib
import uuid
import xml.etree.ElementTree as ET

from .rowspec import StoredProcedure
from .xmlkit import AUTOF, props, sub

TEMPLATE = pathlib.Path(__file__).resolve().parent / "templates" / "catalog-skeleton.cat.xml"

# Seed of the catalog JavaFormulaName (stable per catalog name). A manifest may set its own
# "formulaSeed" to keep the bytes of catalogs that were generated with another seed.
DEFAULT_FORMULA_SEED = "isc-ai-logi"


class FieldNames:
    """Catalog-wide unique mapping names (Logi appends 1, 2... on collisions, like Designer)."""

    def __init__(self, reserved: set[str]) -> None:
        self._used = {name.upper() for name in reserved}

    def claim(self, name: str) -> str:
        candidate, index = name, 0
        while candidate.upper() in self._used:
            index += 1
            candidate = f"{name}{index}"
        self._used.add(candidate.upper())
        return candidate


class CatalogModel:
    """Mapping names of every procedure parameter and field in the catalog."""

    def __init__(self, procedures: list[StoredProcedure], extra_parameters: list[str], data_source: str = "Data Source 1") -> None:
        self.data_source = data_source
        self.procedures = procedures
        self.parameters: list[str] = []
        for proc in procedures:
            for param in proc.params:
                if param.name not in self.parameters:
                    self.parameters.append(param.name)
        for name in extra_parameters:
            if name not in self.parameters:
                self.parameters.append(name)
        names = FieldNames(set(self.parameters) | {p.sql_name for p in procedures})
        self.fields: dict[str, dict[str, str]] = {}
        for proc in procedures:
            self.fields[proc.sql_name] = {c.name: names.claim(c.name) for c in proc.columns}

    def procedure(self, sql_name: str) -> StoredProcedure:
        for proc in self.procedures:
            if proc.sql_name == sql_name:
                return proc
        raise KeyError(sql_name)

    def field(self, sql_name: str, column: str) -> str:
        try:
            return self.fields[sql_name][column]
        except KeyError as exc:
            raise KeyError(f"{sql_name} has no column {column}") from exc

    def dataset_mapping_names(self, sql_name: str) -> list[str]:
        proc = self.procedure(sql_name)
        return [p.name for p in proc.params] + [self.fields[sql_name][c.name] for c in proc.columns]


def _parameter(database: ET.Element, name: str, sql_type: int) -> None:
    element = sub(database, "Parameter")
    props(element, {
        "Type": "Integer" if sql_type == 4 else "String",
        "Format": "XXXXXXXXX...",
        "IsCascading": False,
        "DataMappingFile": AUTOF,
        "IsDynamicParam": False,
        "IsAllSelected": False,
        "IsAllowAll": False,
        "IsStringNull": False,
        "IsMultipleValue": False,
        "Limit": "'2''",
        "ChainID": "",
        "GetValueFromAPI": False,
        "BorderWidth": 0.01389,
        "IsSetAsParameter": False,
        "Name": name,
        "HorizontalAlignment": "Left",
        "SortType": 0,
        "FontFace": "Arial",
        "AutoScaleInNumber": AUTOF,
        "Value_isExpression": "F",
        "Description": "Data type is INTEGER." if sql_type == 4 else "Data type is VARCHAR.",
    })


def _procedure(database: ET.Element, model: CatalogModel, proc: StoredProcedure, connection: str) -> None:
    element = sub(database, "Procedure")
    props(element, {
        "Name": proc.sql_name,
        "ConnectionName": connection,
        "ProcedureName": proc.sql_name,
        "Owner": proc.schema,
    })
    for index, param in enumerate(proc.params, start=1):
        p = sub(element, "ProcdureParameter")  # sic: Logi spelling
        # A string parameter linked from a main dataset column (e.g. a list of row ids) is cut to
        # its Precision when Logi binds the subreport: keep it as wide as a string column.
        props(p, {
            "Precision": 32000 if param.sql_type == 12 else 10,
            "ParameterName": param.name,
            "Nullable": 2,
            "Length": 32000 if param.sql_type == 12 else 4,
            "Type": "IN",
            "Name": param.name,
            "SQLTypeName": param.type_name,
            "AutoScaleInNumber": AUTOF,
            "Radix": 10,
            "Index": index,
            "SQLType": param.sql_type,
        })
    for index, column in enumerate(proc.columns, start=1):
        f = sub(element, "ProcedureField")
        props(f, {
            "Name": model.field(proc.sql_name, column.name),
            "HorizontalAlignment": "Left",
            "Nullable": 2,
            "Description": f"Data type is {column.type_name}.",
            "ColumnName": column.name,
            "ColumnIndex": index,
            "BorderWidth": 0.01389,
            "Format": "XXXXXXXXX..." if column.sql_type == 12 else "#####",
            "Precision": 32000 if column.sql_type == 12 else 10,
            "FontFace": "Arial",
            "AutoScaleInNumber": AUTOF,
            "SQLType": column.sql_type,
        })


def build_catalog(name: str, database_name: str, jdbc_url: str, model: CatalogModel,
                  formula_seed: str = DEFAULT_FORMULA_SEED) -> ET.ElementTree:
    tree = ET.parse(TEMPLATE)
    root = tree.getroot()
    root.find("Name").text = name
    database = root.find("Database")
    database.find("Name").text = database_name
    database.find("JavaFormulaName").text = "jet.formula.javaformula.Formula" + uuid.uuid5(uuid.NAMESPACE_URL, f"{formula_seed}:{name}").hex.upper()
    connection = database.find("JDBCConnection")
    connection.find("Name").text = jdbc_url
    connection.find("URL").text = jdbc_url
    param_types = {}
    for proc in model.procedures:
        for param in proc.params:
            param_types.setdefault(param.name, param.sql_type)
    for parameter in model.parameters:
        _parameter(database, parameter, param_types.get(parameter, 12))
    for proc in model.procedures:
        _procedure(database, model, proc, jdbc_url)
    return tree
